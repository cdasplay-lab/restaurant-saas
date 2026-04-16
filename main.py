import os
import uuid
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv()

from jose import JWTError, jwt
import bcrypt as _bcrypt
import database
from services import webhooks
from services import menu_parser as _menu_parser
from services.integrations import get_adapter, get_all_adapters, PLATFORM_CATALOG
import secrets as _secrets
import tempfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("restaurant-saas")

SECRET_KEY = os.getenv("JWT_SECRET", "supersecretkey_change_in_production_123456789")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("SESSION_HOURS", "24"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL        = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
META_APP_ID        = os.getenv("META_APP_ID", "")
META_APP_SECRET    = os.getenv("META_APP_SECRET", "")
META_VERIFY_TOKEN  = os.getenv("META_VERIFY_TOKEN", "")
# WhatsApp Embedded Signup Configuration ID (different from APP_ID)
# Get from: Meta Business Manager → Apps → Your App → Facebook Login for Business → Create Configuration
META_WA_CONFIG_ID  = os.getenv("META_WA_CONFIG_ID", "")

# ── Simple in-process rate limiter (no external dependency) ──────────────────
import time as _time
import collections as _collections

_rate_store: dict = _collections.defaultdict(list)  # ip → [timestamps]

def _check_rate(ip: str, limit: int = 10, window: int = 60) -> bool:
    """Return True if request is allowed. limit=requests per window (seconds)."""
    now = _time.time()
    timestamps = _rate_store[ip]
    # Remove old timestamps outside the window
    _rate_store[ip] = [t for t in timestamps if now - t < window]
    if len(_rate_store[ip]) >= limit:
        return False
    _rate_store[ip].append(now)
    return True

# ── Plan limits ───────────────────────────────────────────────────────────────
PLAN_LIMITS: dict = {
    "trial":        {"products": 10,   "staff": 2,   "channels": 1},
    "starter":      {"products": 50,   "staff": 5,   "channels": 2},
    "professional": {"products": 200,  "staff": 15,  "channels": 4},
    "enterprise":   {"products": 9999, "staff": 9999, "channels": 10},
}


def _plan_limit(plan: str, resource: str) -> int:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["trial"]).get(resource, 0)


def _check_plan_limit(conn, restaurant_id: str, plan: str, resource: str, table: str) -> None:
    """Raise 402 if the restaurant has reached its plan limit for the resource."""
    limit = _plan_limit(plan, resource)
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE restaurant_id=?", (restaurant_id,)
    ).fetchone()[0]
    if count >= limit:
        raise HTTPException(
            402,
            f"وصلت إلى الحد الأقصى للخطة ({limit} {resource}). رقّ خطتك للمزيد."
        )


openai_client = None
if OPENAI_API_KEY:
    import openai as _openai
    openai_client = _openai.OpenAI(api_key=OPENAI_API_KEY)

def _hash_password(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()

def _verify_password(pw: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False
bearer = HTTPBearer()


from contextlib import asynccontextmanager


async def _subscription_cleanup_job():
    """Every hour: expire overdue subscriptions and sync restaurant status."""
    while True:
        try:
            conn = database.get_db()
            today = datetime.utcnow().strftime("%Y-%m-%d")
            # 1. Mark active subscriptions as expired when end_date has passed
            conn.execute("""
                UPDATE subscriptions
                SET status='expired', updated_at=CURRENT_TIMESTAMP
                WHERE status='active'
                  AND end_date != ''
                  AND end_date < ?
            """, (today,))
            # 2. Mark trial subscriptions as expired when trial_ends_at has passed
            conn.execute("""
                UPDATE subscriptions
                SET status='expired', updated_at=CURRENT_TIMESTAMP
                WHERE status='trial'
                  AND trial_ends_at != ''
                  AND trial_ends_at < ?
            """, (today,))
            # 3. Sync restaurant.status for newly expired restaurants
            conn.execute("""
                UPDATE restaurants
                SET status='expired'
                WHERE id IN (
                    SELECT restaurant_id FROM subscriptions WHERE status='expired'
                ) AND status IN ('active', 'trial')
            """)
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.error(f"subscription_cleanup error: {exc}")
        await asyncio.sleep(3600)  # run every hour


async def _send_report_to_telegram(bot_token: str, chat_id: str, text: str) -> None:
    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        )


async def _report_job():
    """Send daily/weekly stats reports to restaurant owners via Telegram."""
    await asyncio.sleep(60)  # wait 1 min after startup
    while True:
        try:
            now = datetime.utcnow()
            conn = database.get_db()
            try:
                rows = conn.execute(
                    """SELECT s.restaurant_id, s.report_frequency, s.report_last_sent,
                              s.notify_chat_id, s.restaurant_name,
                              ch.token as tg_token
                       FROM settings s
                       LEFT JOIN channels ch ON ch.restaurant_id=s.restaurant_id AND ch.type='telegram'
                       WHERE s.report_frequency IN ('daily','weekly')
                         AND s.notify_chat_id != ''
                         AND (ch.token IS NOT NULL AND ch.token != '')"""
                ).fetchall()
            finally:
                conn.close()

            for row in rows:
                freq = row["report_frequency"]
                last_sent_str = row["report_last_sent"] or ""
                chat_id = row["notify_chat_id"]
                bot_token = row["tg_token"] or ""
                rid = row["restaurant_id"]
                rest_name = row["restaurant_name"] or "المطعم"

                # Check if it's time
                hours_since = 9999.0
                if last_sent_str:
                    try:
                        last_dt = datetime.fromisoformat(last_sent_str)
                        hours_since = (now - last_dt).total_seconds() / 3600
                    except Exception:
                        pass

                if freq == "daily" and hours_since < 23:
                    continue
                if freq == "weekly" and hours_since < 167:
                    continue

                # Build stats
                try:
                    conn2 = database.get_db()
                    try:
                        if freq == "daily":
                            period_label = "اليوم"
                            since = (now - timedelta(hours=24)).isoformat(sep=' ', timespec='seconds')
                        else:
                            period_label = "هذا الأسبوع"
                            since = (now - timedelta(days=7)).isoformat(sep=' ', timespec='seconds')

                        orders_row = conn2.execute(
                            "SELECT COUNT(*) as cnt, COALESCE(SUM(total),0) as rev FROM orders WHERE restaurant_id=? AND created_at >= ? AND status != 'cancelled'",
                            (rid, since)
                        ).fetchone()
                        new_custs = conn2.execute(
                            "SELECT COUNT(*) as cnt FROM customers WHERE restaurant_id=? AND created_at >= ?",
                            (rid, since)
                        ).fetchone()
                        top_prod = conn2.execute(
                            """SELECT oi.name, SUM(oi.quantity) as qty FROM order_items oi
                               JOIN orders o ON oi.order_id=o.id
                               WHERE o.restaurant_id=? AND o.created_at >= ? AND o.status != 'cancelled'
                               GROUP BY oi.name ORDER BY qty DESC LIMIT 1""",
                            (rid, since)
                        ).fetchone()
                    finally:
                        conn2.close()

                    orders_count = orders_row["cnt"] if orders_row else 0
                    revenue = int(orders_row["rev"]) if orders_row else 0
                    new_c = new_custs["cnt"] if new_custs else 0
                    top_name = top_prod["name"] if top_prod else "—"
                    top_qty = top_prod["qty"] if top_prod else 0

                    text = (
                        f"📊 <b>تقرير {rest_name} — {period_label}</b>\n\n"
                        f"🛍️ الطلبات: {orders_count}\n"
                        f"💰 الإيرادات: {revenue:,} د.ع\n"
                        f"👤 عملاء جدد: {new_c}\n"
                        f"⭐ أكثر طلباً: {top_name} ({top_qty} مرة)\n\n"
                        f"<i>تقرير تلقائي من منصة إدارة {rest_name}</i>"
                    )
                    await _send_report_to_telegram(bot_token, chat_id, text)

                    # Update last_sent
                    conn3 = database.get_db()
                    try:
                        conn3.execute(
                            "UPDATE settings SET report_last_sent=? WHERE restaurant_id=?",
                            (now.isoformat(sep=' ', timespec='seconds'), rid)
                        )
                        conn3.commit()
                    finally:
                        conn3.close()

                    logger.info(f"[report] sent {freq} report to {rest_name} ({rid})")
                except Exception as e:
                    logger.warning(f"[report] failed for {rid}: {e}")

        except Exception as exc:
            logger.error(f"report_job error: {exc}")
        await asyncio.sleep(3600)  # check every hour


async def _token_refresh_job():
    """Every 6 hours: silently refresh Meta tokens expiring within 7 days."""
    await asyncio.sleep(120)  # wait 2 min after startup before first pass
    while True:
        try:
            warn_threshold = (datetime.utcnow() + timedelta(days=7)).isoformat()
            conn = database.get_db()
            try:
                rows = conn.execute("""
                    SELECT * FROM channels
                    WHERE connection_status='connected'
                      AND token_expires_at != ''
                      AND token_expires_at < ?
                      AND reconnect_needed=0
                """, (warn_threshold,)).fetchall()
            finally:
                conn.close()

            for row in rows:
                ch       = dict(row)
                platform = ch["type"]
                adapter  = get_adapter(platform)
                if not adapter:
                    continue
                try:
                    updates = adapter.refresh_token(ch)
                    _allowed = {"token", "token_expires_at", "reconnect_needed"}
                    filtered = {k: v for k, v in updates.items() if k in _allowed}
                    if filtered:
                        set_sql = ", ".join(f"{k}=?" for k in filtered)
                        conn2 = database.get_db()
                        try:
                            conn2.execute(
                                f"UPDATE channels SET {set_sql} WHERE id=?",
                                list(filtered.values()) + [ch["id"]]
                            )
                            conn2.commit()
                        finally:
                            conn2.close()
                    logger.info(f"[token_refresh] refreshed {platform} rid={ch['restaurant_id']}")
                except NotImplementedError:
                    pass  # platform doesn't support silent refresh
                except Exception as exc:
                    logger.warning(f"[token_refresh] failed {platform} rid={ch['restaurant_id']}: {exc}")
                    conn3 = database.get_db()
                    try:
                        conn3.execute(
                            "UPDATE channels SET reconnect_needed=1, last_error=? WHERE id=?",
                            (str(exc)[:500], ch["id"])
                        )
                        conn3.commit()
                    finally:
                        conn3.close()

        except Exception as exc:
            logger.error(f"token_refresh_job error: {exc}")
        await asyncio.sleep(21600)  # 6 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    asyncio.create_task(_subscription_cleanup_job())
    asyncio.create_task(_report_job())
    asyncio.create_task(_token_refresh_job())
    yield


app = FastAPI(title="Restaurant SaaS API", version="3.0.0", lifespan=lifespan)

_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
if _raw_origins.strip() in ("", "*"):
    # dev fallback — في الإنتاج اضبط ALLOWED_ORIGINS بشكل صريح
    ALLOWED_ORIGINS = ["*"]
    if os.getenv("NODE_ENV") == "production" or os.getenv("RAILWAY_ENVIRONMENT"):
        logger.warning(
            "⚠️  ALLOWED_ORIGINS=* في بيئة إنتاج — اضبط المتغير في Railway/Render:\n"
            "    ALLOWED_ORIGINS=https://yourapp.netlify.app,https://yourdomain.com"
        )
else:
    ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.middleware("http")
async def subscription_guard(request: Request, call_next):
    """Block suspended/expired restaurants from accessing protected API routes."""
    path = request.url.path
    # Only guard authenticated restaurant API endpoints
    if not path.startswith("/api/"):
        return await call_next(request)
    # Exclude: auth (login/me/logout), super admin, webhooks, health
    skip_prefixes = ("/api/auth/", "/api/super/")
    if any(path.startswith(p) for p in skip_prefixes):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return await call_next(request)

    try:
        from jose import jwt as _jwt, JWTError
        payload = _jwt.decode(
            auth_header.split(" ", 1)[1], SECRET_KEY, algorithms=[ALGORITHM]
        )
        rid = payload.get("restaurant_id")
        if rid and not payload.get("is_super"):
            conn = database.get_db()
            sub = conn.execute(
                "SELECT status FROM subscriptions WHERE restaurant_id=?", (rid,)
            ).fetchone()
            rest = conn.execute(
                "SELECT status FROM restaurants WHERE id=?", (rid,)
            ).fetchone()
            conn.close()
            rest_status = (rest["status"] if rest else "") or ""
            sub_status  = (sub["status"]  if sub  else "") or ""
            if rest_status == "suspended" or sub_status == "suspended":
                return JSONResponse(
                    {"detail": {"code": "SUSPENDED", "message": "الحساب موقوف — تواصل مع الدعم"}},
                    status_code=402,
                )
            if rest_status == "expired" or sub_status == "expired":
                return JSONResponse(
                    {"detail": {"code": "EXPIRED", "message": "الاشتراك منتهي — جدد اشتراكك للاستمرار"}},
                    status_code=402,
                )
    except Exception:
        pass  # Let the route handler return 401

    return await call_next(request)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def create_token(data: dict, hours: int = None) -> str:
    payload = data.copy()
    h = hours if hours is not None else ACCESS_TOKEN_EXPIRE_HOURS
    payload["exp"] = datetime.utcnow() + timedelta(hours=h)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        if not payload.get("sub"):
            raise HTTPException(status_code=401, detail="Invalid token")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def current_user(payload: dict = Depends(verify_token)):
    conn = database.get_db()
    row = conn.execute("""
        SELECT u.*, r.name AS restaurant_name, r.plan
        FROM users u JOIN restaurants r ON u.restaurant_id = r.id
        WHERE u.id = ?
    """, (payload["sub"],)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(row)


def require_role(*roles):
    def checker(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "غير مصرح — الدور غير كافٍ")
        return user
    return checker


def current_super_admin(payload: dict = Depends(verify_token)):
    """Dependency that ensures the caller is an authenticated super admin."""
    if not payload.get("is_super"):
        raise HTTPException(403, "غير مصرح — يلزم صلاحية super_admin")
    conn = database.get_db()
    row = conn.execute("SELECT * FROM super_admins WHERE id=?", (payload["sub"],)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "حساب super_admin غير موجود")
    return dict(row)


# ── Activity & Notification helpers ──────────────────────────────────────────

def log_activity(conn, restaurant_id, action, entity_type="", entity_id="", description="",
                 user_id=None, user_name="System"):
    try:
        conn.execute(
            "INSERT INTO activity_log (id, restaurant_id, user_id, user_name, action, entity_type, entity_id, description) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), restaurant_id, user_id, user_name, action, entity_type, entity_id, description)
        )
    except Exception:
        pass


def create_notification(conn, restaurant_id, ntype, title, message, entity_type="", entity_id=""):
    try:
        conn.execute(
            "INSERT INTO notifications (id, restaurant_id, type, title, message, entity_type, entity_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), restaurant_id, ntype, title, message, entity_type, entity_id)
        )
    except Exception:
        pass


def record_channel_error(conn, channel_id: str, restaurant_id: str, platform: str,
                         error_message: str, error_code: str = "",
                         error_type: str = "webhook", request_payload: str = ""):
    """Insert a connection_errors row and update channel last_error."""
    try:
        conn.execute(
            "INSERT INTO connection_errors "
            "(id, channel_id, restaurant_id, platform, error_code, error_message, error_type, request_payload) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), channel_id, restaurant_id, platform,
             error_code, error_message[:500], error_type, request_payload[:2000])
        )
        conn.execute(
            "UPDATE channels SET last_error=?, connection_status='error', "
            "last_tested_at=CURRENT_TIMESTAMP WHERE id=?",
            (error_message[:500], channel_id)
        )
    except Exception as _e:
        logger.warning(f"record_channel_error failed: {_e}")


# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginReq(BaseModel):
    email: str
    password: str


class ProductCreate(BaseModel):
    name: str
    price: float
    category: str = "Main"
    description: str = ""
    icon: str = "🍽️"
    variants: list = []
    available: bool = True
    image: str = ""
    image_url: str = ""
    gallery_images: list = []


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    variants: Optional[list] = None
    available: Optional[bool] = None
    image: Optional[str] = None
    image_url: Optional[str] = None
    gallery_images: Optional[list] = None


class OrderCreate(BaseModel):
    customer_id: str
    channel: str = "telegram"
    type: str = "delivery"
    address: str = ""
    notes: str = ""
    items: list = []


class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    address: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    vip: Optional[bool] = None
    preferences: Optional[str] = None
    favorite_item: Optional[str] = None


class MsgCreate(BaseModel):
    content: str


class SettingsUpdate(BaseModel):
    restaurant_name: Optional[str] = None
    restaurant_description: Optional[str] = None
    restaurant_phone: Optional[str] = None
    restaurant_address: Optional[str] = None
    working_hours: Optional[dict] = None
    bot_name: Optional[str] = None
    bot_personality: Optional[str] = None
    bot_language: Optional[str] = None
    bot_welcome: Optional[str] = None
    bot_enabled: Optional[bool] = None
    security_2fa: Optional[bool] = None
    security_session_timeout: Optional[int] = None
    payment_methods: Optional[str] = None
    business_type: Optional[str] = None
    delivery_time: Optional[str] = None
    notify_chat_id: Optional[str] = None
    delivery_fee: Optional[int] = None
    min_order: Optional[int] = None
    report_frequency: Optional[str] = None  # none / daily / weekly
    menu_url: Optional[str] = None


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    token: Optional[str] = None
    webhook_url: Optional[str] = None
    username: Optional[str] = None
    enabled: Optional[bool] = None
    webhook_secret: Optional[str] = None
    admin_chat_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    business_account_id: Optional[str] = None
    verify_token: Optional[str] = None
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    page_id: Optional[str] = None
    page_name: Optional[str] = None
    bot_username: Optional[str] = None


class StaffCreate(BaseModel):
    email: str
    name: str
    password: str
    role: str = "staff"


class StaffUpdate(BaseModel):
    role: str


class BotConfigUpdate(BaseModel):
    system_prompt: Optional[str] = None
    sales_prompt: Optional[str] = None
    escalation_keywords: Optional[List[str]] = None
    fallback_message: Optional[str] = None
    max_bot_turns: Optional[int] = None
    auto_handoff_enabled: Optional[bool] = None
    order_extraction_enabled: Optional[bool] = None
    memory_enabled: Optional[bool] = None
    escalation_threshold: Optional[int] = None


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    try:
        conn = database.get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception as e:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "db_backend": "postgresql" if database.IS_POSTGRES else "sqlite",
        "version": "3.0.0",
        "base_url": BASE_URL,
    }


@app.post("/api/super/init-db")
async def reinit_db(admin=Depends(current_super_admin)):
    """Re-run init_db (seeds super admin + demo restaurant if missing). Super admin only."""
    try:
        database.init_db()
        return {"ok": True, "message": "تم تهيئة قاعدة البيانات بنجاح"}
    except Exception as e:
        logger.error(f"[super] init-db failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(request: Request, data: LoginReq):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate(ip, limit=10, window=60):
        raise HTTPException(429, "طلبات كثيرة جداً — حاول بعد دقيقة")
    conn = database.get_db()
    user = conn.execute("""
        SELECT u.*, r.name AS restaurant_name, r.plan
        FROM users u JOIN restaurants r ON u.restaurant_id = r.id
        WHERE u.email = ?
    """, (data.email,)).fetchone()

    if not user or not _verify_password(data.password, user["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="البريد الإلكتروني أو كلمة المرور غير صحيحة")

    # Update last_login
    try:
        conn.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE id=?", (user["id"],))
        conn.commit()
    except Exception:
        pass

    conn.close()
    token = create_token({"sub": user["id"], "restaurant_id": user["restaurant_id"]})
    return {
        "token": token,
        "user": {
            "id": user["id"], "name": user["name"], "email": user["email"],
            "role": user["role"], "restaurant_id": user["restaurant_id"],
            "restaurant_name": user["restaurant_name"], "plan": user["plan"],
        },
    }


@app.post("/api/auth/logout")
async def logout():
    return {"message": "تم تسجيل الخروج"}


class RegisterReq(BaseModel):
    restaurant_name: str
    owner_name: str
    email: str
    password: str
    plan: Optional[str] = "trial"
    business_type: Optional[str] = "restaurant"


@app.post("/api/auth/register")
async def register(request: Request, data: RegisterReq):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate(ip, limit=5, window=60):
        raise HTTPException(429, "طلبات كثيرة جداً — حاول بعد دقيقة")

    if len(data.password) < 6:
        raise HTTPException(400, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")

    plan = data.plan if data.plan in ("trial", "starter", "professional", "enterprise") else "trial"

    conn = database.get_db()
    try:
        if conn.execute("SELECT id FROM users WHERE email=?", (data.email.lower().strip(),)).fetchone():
            raise HTTPException(400, "البريد الإلكتروني مستخدم بالفعل")

        rid = str(uuid.uuid4())
        uid = str(uuid.uuid4())

        _btype = data.business_type if data.business_type in ("restaurant", "cafe") else "restaurant"
        conn.execute(
            "INSERT INTO restaurants (id, name, phone, address, plan, status, business_type) VALUES (?,?,?,?,?,'active',?)",
            (rid, data.restaurant_name.strip(), "", "", plan, _btype),
        )
        pw_hash = _bcrypt.hashpw(data.password.encode(), _bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (id, restaurant_id, email, password_hash, name, role) VALUES (?,?,?,?,?,?)",
            (uid, rid, data.email.lower().strip(), pw_hash, data.owner_name.strip(), "owner"),
        )
        for ch_type in ["telegram", "whatsapp", "instagram", "facebook"]:
            conn.execute(
                "INSERT INTO channels (id, restaurant_id, type, name, enabled, verified) VALUES (?,?,?,?,0,0)",
                (str(uuid.uuid4()), rid, ch_type, f"قناة {ch_type}"),
            )
        conn.execute(
            "INSERT INTO settings (id, restaurant_id, restaurant_name, bot_enabled, business_type) VALUES (?,?,?,1,?)",
            (str(uuid.uuid4()), rid, data.restaurant_name.strip(), _btype),
        )
        conn.execute(
            "INSERT INTO bot_config (id, restaurant_id, system_prompt, sales_prompt) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), rid,
             f"أنت مساعد ذكاء اصطناعي لـ {data.restaurant_name}. ساعد العملاء بكل ود واحترافية.", ""),
        )
        from datetime import date as _date, timedelta as _td
        today = _date.today().isoformat()
        trial_end = (_date.today() + _td(days=14)).isoformat()
        conn.execute(
            "INSERT INTO subscriptions (id, restaurant_id, plan, status, price, start_date, end_date, trial_ends_at, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), rid, plan,
             "trial" if plan == "trial" else "active",
             0.0, today,
             (_date.today() + _td(days=365)).isoformat() if plan != "trial" else trial_end,
             trial_end, "Self-registered"),
        )
        conn.commit()
        logger.info(f"[register] new restaurant — name={data.restaurant_name} email={data.email} plan={plan}")
    finally:
        conn.close()

    token = create_token({"sub": uid, "restaurant_id": rid})
    return {
        "token": token,
        "user": {
            "id": uid, "name": data.owner_name.strip(), "email": data.email.lower().strip(),
            "role": "owner", "restaurant_id": rid,
            "restaurant_name": data.restaurant_name.strip(), "plan": plan,
        },
    }


@app.get("/api/auth/me")
async def me(user=Depends(current_user)):
    return {k: user[k] for k in ("id", "name", "email", "role",
                                  "restaurant_id", "restaurant_name", "plan")}


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/api/analytics/summary")
async def summary(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    today = datetime.now().strftime("%Y-%m-%d")

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    result = {
        "total_orders":    q("SELECT COUNT(*) FROM orders WHERE restaurant_id=?", rid),
        "today_revenue":   round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'", rid, today), 2),
        "open_chats":      q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND status='open'", rid),
        "total_customers": q("SELECT COUNT(*) FROM customers WHERE restaurant_id=?", rid),
        "pending_orders":  q("SELECT COUNT(*) FROM orders WHERE restaurant_id=? AND status IN ('pending','confirmed','preparing','on_way')", rid),
        "total_revenue":   round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND status!='cancelled'", rid), 2),
        "total_products":  q("SELECT COUNT(*) FROM products WHERE restaurant_id=?", rid),
        "urgent_chats":    q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND urgent=1 AND status='open'", rid),
    }

    recent = conn.execute("""
        SELECT o.id, o.total, o.status, o.channel, o.created_at, c.name AS customer_name
        FROM orders o JOIN customers c ON o.customer_id = c.id
        WHERE o.restaurant_id = ? ORDER BY o.created_at DESC LIMIT 5
    """, (rid,)).fetchall()
    result["recent_orders"] = [dict(r) for r in recent]

    conn.close()
    return result


@app.get("/api/analytics/weekly-revenue")
async def weekly_revenue(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    data = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        rev = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'",
            (rid, day)
        ).fetchone()[0]
        data.append({"date": day, "revenue": round(rev, 2)})
    conn.close()
    return data


@app.get("/api/analytics/channel-breakdown")
async def channel_breakdown(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute("""
        SELECT channel, COUNT(*) AS count, COALESCE(SUM(total),0) AS revenue
        FROM orders WHERE restaurant_id=? AND status!='cancelled' GROUP BY channel
    """, (user["restaurant_id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/top-products")
async def top_products_analytics(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute("""
        SELECT id, name, price, category, icon, order_count
        FROM products WHERE restaurant_id=? ORDER BY order_count DESC LIMIT 5
    """, (user["restaurant_id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/top-customers")
async def top_customers_analytics(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute("""
        SELECT id, name, platform, vip, total_orders, total_spent
        FROM customers WHERE restaurant_id=? ORDER BY total_spent DESC LIMIT 5
    """, (user["restaurant_id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/bot-stats")
async def bot_stats(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    total_bot = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND bot_turn_count > 0",
        (rid,)
    ).fetchone()[0]

    escalated = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND handoff_reason != '' AND handoff_reason IS NOT NULL",
        (rid,)
    ).fetchone()[0]

    avg_turns_row = conn.execute(
        "SELECT COALESCE(AVG(bot_turn_count), 0) FROM conversations WHERE restaurant_id=? AND bot_turn_count > 0",
        (rid,)
    ).fetchone()[0]

    success_rate = round(((total_bot - escalated) / total_bot * 100) if total_bot > 0 else 0, 1)
    handoff_rate = round((escalated / total_bot * 100) if total_bot > 0 else 0, 1)

    conn.close()
    return {
        "total_bot_convs": total_bot,
        "escalated": escalated,
        "success_rate": success_rate,
        "avg_turns": round(avg_turns_row, 1),
        "handoff_rate": handoff_rate,
    }


@app.get("/api/analytics/channel-performance")
async def channel_performance(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    rows = conn.execute("""
        SELECT
            o.channel,
            COUNT(o.id) AS orders,
            COALESCE(SUM(o.total), 0) AS revenue,
            CASE WHEN COUNT(o.id) > 0 THEN COALESCE(SUM(o.total),0) / COUNT(o.id) ELSE 0 END AS avg_order_value
        FROM orders o
        WHERE o.restaurant_id=? AND o.status != 'cancelled'
        GROUP BY o.channel
    """, (rid,)).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        # Count conversations per channel
        conv_count = conn.execute(
            "SELECT COUNT(*) FROM conversations cv JOIN customers c ON cv.customer_id=c.id "
            "WHERE cv.restaurant_id=? AND c.platform=?",
            (rid, d["channel"])
        ).fetchone()[0]
        d["conversations"] = conv_count
        d["revenue"] = round(d["revenue"], 2)
        d["avg_order_value"] = round(d["avg_order_value"], 2)
        result.append(d)

    conn.close()
    return result


@app.get("/api/analytics/revenue-range")
async def revenue_range(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]

    if not from_date:
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")

    # Build date range
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "تنسيق التاريخ غير صحيح — استخدم YYYY-MM-DD")

    data = []
    current = start
    while current <= end:
        day = current.strftime("%Y-%m-%d")
        rev = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'",
            (rid, day)
        ).fetchone()[0]
        data.append({"date": day, "revenue": round(rev, 2)})
        current += timedelta(days=1)

    conn.close()
    return data


@app.get("/api/analytics/order-funnel")
async def order_funnel(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    total_conversations = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", (rid,)
    ).fetchone()[0]

    with_orders = conn.execute(
        "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE restaurant_id=?", (rid,)
    ).fetchone()[0]

    conversion_rate = round((with_orders / total_conversations * 100) if total_conversations > 0 else 0, 1)

    status_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM orders WHERE restaurant_id=? GROUP BY status", (rid,)
    ).fetchall()
    by_status = {r["status"]: r["cnt"] for r in status_rows}

    conn.close()
    return {
        "total_conversations": total_conversations,
        "with_orders": with_orders,
        "conversion_rate": conversion_rate,
        "by_status_counts": by_status,
    }


# ── Products ──────────────────────────────────────────────────────────────────

@app.get("/api/products")
async def list_products(category: Optional[str] = None, user=Depends(current_user)):
    conn = database.get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM products WHERE restaurant_id=? AND category=? ORDER BY name",
            (user["restaurant_id"], category)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM products WHERE restaurant_id=? ORDER BY name",
            (user["restaurant_id"],)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["variants"] = json.loads(d.get("variants") or "[]")
        d["gallery_images"] = json.loads(d.get("gallery_images") or "[]")
        result.append(d)
    return result


@app.get("/api/products/{pid}")
async def get_product(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM products WHERE id=? AND restaurant_id=?",
        (pid, user["restaurant_id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Product not found")
    d = dict(row)
    d["variants"] = json.loads(d.get("variants") or "[]")
    d["gallery_images"] = json.loads(d.get("gallery_images") or "[]")
    return d


@app.post("/api/products", status_code=201)
async def create_product(data: ProductCreate, user=Depends(current_user)):
    conn = database.get_db()
    _check_plan_limit(conn, user["restaurant_id"], user.get("plan", "trial"), "products", "products")
    pid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO products (id, restaurant_id, name, price, category, description, icon, variants, available, image, image_url, gallery_images)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (pid, user["restaurant_id"], data.name, data.price, data.category,
          data.description, data.icon, json.dumps(data.variants), int(data.available),
          data.image, data.image_url, json.dumps(data.gallery_images)))
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    d = dict(row)
    d["variants"] = json.loads(d.get("variants") or "[]")
    d["gallery_images"] = json.loads(d.get("gallery_images") or "[]")
    return d


@app.patch("/api/products/{pid}")
async def update_product(pid: str, data: ProductUpdate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM products WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Product not found")
    upd = {}
    if data.name is not None: upd["name"] = data.name
    if data.price is not None: upd["price"] = data.price
    if data.category is not None: upd["category"] = data.category
    if data.description is not None: upd["description"] = data.description
    if data.icon is not None: upd["icon"] = data.icon
    if data.variants is not None: upd["variants"] = json.dumps(data.variants)
    if data.available is not None: upd["available"] = int(data.available)
    if data.image is not None: upd["image"] = data.image
    if data.image_url is not None: upd["image_url"] = data.image_url
    if data.gallery_images is not None: upd["gallery_images"] = json.dumps(data.gallery_images)
    if upd:
        sets = ", ".join(f"{k}=?" for k in upd) + ", updated_at=datetime('now')"
        conn.execute(f"UPDATE products SET {sets} WHERE id=?", list(upd.values()) + [pid])
        log_activity(conn, user["restaurant_id"], "product_updated", "product", pid,
                     f"تعديل المنتج: {', '.join(upd.keys())}", user["id"], user.get("name", ""))
        conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    d = dict(row)
    d["variants"] = json.loads(d.get("variants") or "[]")
    d["gallery_images"] = json.loads(d.get("gallery_images") or "[]")
    return d


@app.patch("/api/products/{pid}/availability")
async def toggle_availability(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("SELECT available FROM products WHERE id=? AND restaurant_id=?",
                       (pid, user["restaurant_id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Product not found")
    new_val = 1 - row["available"]
    conn.execute("UPDATE products SET available=? WHERE id=?", (new_val, pid))
    conn.commit()
    conn.close()
    return {"available": bool(new_val)}


@app.patch("/api/products/{product_id}/sold-out-today")
async def toggle_sold_out_today(product_id: str, user=Depends(require_role("owner","manager","staff"))):
    from datetime import date as _date
    conn = database.get_db()
    try:
        p = conn.execute("SELECT * FROM products WHERE id=? AND restaurant_id=?",
                         (product_id, user["restaurant_id"])).fetchone()
        if not p:
            raise HTTPException(404, "المنتج غير موجود")
        today = _date.today().isoformat()
        current = p["sold_out_date"] if "sold_out_date" in p.keys() else ""
        new_val = today if current != today else ""
        conn.execute("UPDATE products SET sold_out_date=? WHERE id=?", (new_val, product_id))
        conn.commit()
        return {"sold_out_today": new_val == today}
    finally:
        conn.close()


@app.delete("/api/products/{pid}")
async def delete_product(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM products WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Product not found")
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}


@app.patch("/api/categories/rename")
async def rename_category(data: dict, user=Depends(current_user)):
    old_name = (data.get("old_name") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    if not old_name or not new_name:
        raise HTTPException(400, "اسم الفئة مطلوب")
    conn = database.get_db()
    try:
        conn.execute(
            "UPDATE products SET category=? WHERE category=? AND restaurant_id=?",
            (new_name, old_name, user["restaurant_id"]))
        conn.commit()
        return {"message": "تم التعديل"}
    finally:
        conn.close()


@app.delete("/api/categories/{name}")
async def delete_category(name: str, user=Depends(current_user)):
    conn = database.get_db()
    try:
        conn.execute(
            "UPDATE products SET category='Main' WHERE category=? AND restaurant_id=?",
            (name, user["restaurant_id"]))
        conn.commit()
        return {"message": "تم حذف الفئة ونقل المنتجات إلى Main"}
    finally:
        conn.close()


@app.post("/api/upload/product-image", status_code=201)
async def upload_product_image(
    file: UploadFile = File(...),
    product_id: str = "",
    user=Depends(require_role("owner", "manager")),
):
    """Upload a product image to Supabase Storage and return the URL."""
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"نوع الملف غير مدعوم: {ext}")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > 10:
        raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 10 MB")

    pid = product_id or str(uuid.uuid4())
    fname = f"{uuid.uuid4()}{ext}"
    storage_path = _storage.product_storage_path(user["restaurant_id"], pid, fname)

    public_url = _storage.upload_bytes(
        content,
        _storage.BUCKET_PRODUCTS,
        storage_path,
        content_type=file.content_type or "image/jpeg",
    )

    if not public_url:
        # Supabase not configured — return placeholder
        return {"url": "", "message": "Supabase not configured — configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"}

    # Save URL to products table if product_id refers to an existing product
    if product_id:
        conn = database.get_db()
        row = conn.execute("SELECT id FROM products WHERE id=? AND restaurant_id=?",
                           (product_id, user["restaurant_id"])).fetchone()
        if row:
            conn.execute("UPDATE products SET image_url=? WHERE id=?", (public_url, product_id))
            conn.commit()
        conn.close()

    return {"url": public_url, "product_id": pid}


@app.post("/api/upload/bulk-product-images", status_code=200)
async def bulk_upload_product_images(
    files: List[UploadFile] = File(...),
    user=Depends(require_role("owner", "manager")),
):
    """Upload multiple product images at once.

    Each file's stem (filename without extension) is matched against product names
    (case-insensitive, trimmed). On match the image is uploaded via the same
    Supabase Storage path used by the single-upload route, and the product's
    image_url is updated automatically.

    Returns a report:
      matched   — list of {file, product_id, product_name, image_url}
      unmatched — list of {file, reason}
      matched_count, unmatched_count, total
    """
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    # Load all products for this restaurant once (avoids N+1 queries)
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT id, name FROM products WHERE restaurant_id=?",
            (user["restaurant_id"],),
        ).fetchall()
    finally:
        conn.close()

    # Build case-insensitive name → product lookup
    product_map: dict = {dict(r)["name"].strip().lower(): dict(r) for r in rows}

    matched: list = []
    unmatched: list = []

    for f in files:
        filename = f.filename or ""
        ext = Path(filename).suffix.lower()
        stem = Path(filename).stem.strip()

        if ext not in ALLOWED:
            unmatched.append({"file": filename, "reason": f"نوع الملف غير مدعوم: {ext}"})
            continue

        content = await f.read()
        if len(content) > 10 * 1024 * 1024:
            unmatched.append({"file": filename, "reason": "الملف أكبر من 10 MB"})
            continue
        if not content:
            unmatched.append({"file": filename, "reason": "الملف فارغ"})
            continue

        product = product_map.get(stem.lower())
        if not product:
            unmatched.append({"file": filename, "reason": "لم يُعثر على منتج بهذا الاسم"})
            continue

        fname = f"{uuid.uuid4()}{ext}"
        storage_path = _storage.product_storage_path(
            user["restaurant_id"], product["id"], fname
        )
        public_url = _storage.upload_bytes(
            content,
            _storage.BUCKET_PRODUCTS,
            storage_path,
            content_type=f.content_type or "image/jpeg",
        )

        if not public_url:
            unmatched.append({"file": filename, "reason": "Supabase غير مُهيأ — تحقق من SUPABASE_URL"})
            continue

        conn = database.get_db()
        conn.execute("UPDATE products SET image_url=? WHERE id=?", (public_url, product["id"]))
        conn.commit()
        conn.close()

        matched.append({
            "file":         filename,
            "product_id":   product["id"],
            "product_name": product["name"],
            "image_url":    public_url,
        })

    return {
        "matched":         matched,
        "unmatched":       unmatched,
        "total":           len(files),
        "matched_count":   len(matched),
        "unmatched_count": len(unmatched),
    }


@app.post("/api/upload/gallery-image", status_code=201)
async def upload_gallery_image(
    file: UploadFile = File(...),
    product_id: str = "",
    user=Depends(require_role("owner", "manager")),
):
    """Upload a gallery image to Supabase Storage and append the URL to products.gallery_images."""
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"نوع الملف غير مدعوم: {ext}")

    content = await file.read()
    if len(content) / (1024 * 1024) > 10:
        raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 10 MB")

    pid = product_id or str(uuid.uuid4())
    fname = f"{uuid.uuid4()}{ext}"
    storage_path = _storage.gallery_image_path(user["restaurant_id"], pid, fname)

    public_url = _storage.upload_bytes(
        content,
        _storage.BUCKET_PRODUCTS,
        storage_path,
        content_type=file.content_type or "image/jpeg",
    )

    if not public_url:
        return {"url": "", "message": "Supabase not configured"}

    # Append URL to gallery_images JSON array for the product
    if product_id:
        conn = database.get_db()
        row = conn.execute("SELECT gallery_images FROM products WHERE id=? AND restaurant_id=?",
                           (product_id, user["restaurant_id"])).fetchone()
        if row:
            gallery = json.loads(row["gallery_images"] or "[]")
            gallery.append(public_url)
            conn.execute("UPDATE products SET gallery_images=? WHERE id=?",
                         (json.dumps(gallery), product_id))
            conn.commit()
        conn.close()

    return {"url": public_url, "product_id": pid}


# ── Customers ─────────────────────────────────────────────────────────────────

@app.get("/api/customers")
async def list_customers(search: Optional[str] = None, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    if search:
        rows = conn.execute(
            "SELECT * FROM customers WHERE restaurant_id=? AND (name LIKE ? OR phone LIKE ?) ORDER BY total_spent DESC",
            (rid, f"%{search}%", f"%{search}%")).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM customers WHERE restaurant_id=? ORDER BY total_spent DESC", (rid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/customers/{cid}")
async def get_customer(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM customers WHERE id=? AND restaurant_id=?",
                       (cid, user["restaurant_id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Customer not found")
    orders = conn.execute(
        "SELECT * FROM orders WHERE customer_id=? ORDER BY created_at DESC LIMIT 10", (cid,)).fetchall()
    conn.close()
    result = dict(row)
    result["orders"] = [dict(o) for o in orders]
    return result


@app.patch("/api/customers/{cid}")
async def update_customer(cid: str, data: CustomerUpdate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM customers WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Customer not found")
    upd = {}
    if data.name is not None: upd["name"] = data.name
    if data.phone is not None: upd["phone"] = data.phone
    if data.vip is not None: upd["vip"] = int(data.vip)
    if data.preferences is not None: upd["preferences"] = data.preferences
    if data.favorite_item is not None: upd["favorite_item"] = data.favorite_item
    if upd:
        conn.execute(f"UPDATE customers SET {','.join(k+'=?' for k in upd)} WHERE id=?",
                     list(upd.values()) + [cid])
        conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/customers/{cid}")
async def delete_customer(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM customers WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Customer not found")
    conn.execute("DELETE FROM customers WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}


@app.get("/api/export/orders")
async def export_orders(user=Depends(current_user)):
    """Export all orders as CSV."""
    import csv, io
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        rows = conn.execute(
            """SELECT o.id, o.status, o.total, o.address, o.notes, o.created_at,
                      c.name as customer_name, c.phone as customer_phone
               FROM orders o
               LEFT JOIN customers c ON o.customer_id = c.id
               WHERE o.restaurant_id=?
               ORDER BY o.created_at DESC""",
            (rid,)
        ).fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["رقم الطلب", "الحالة", "الإجمالي", "العنوان", "الملاحظات", "التاريخ", "اسم العميل", "جوال العميل"])
    for r in rows:
        writer.writerow([r["id"], r["status"], r["total"], r["address"] or "", r["notes"] or "",
                         r["created_at"], r["customer_name"] or "", r["customer_phone"] or ""])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=orders.csv"}
    )


@app.get("/api/export/customers")
async def export_customers(user=Depends(current_user)):
    """Export all customers as CSV."""
    import csv, io
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT name, phone, platform, vip, orders_count, total_spent, last_seen, preferences FROM customers WHERE restaurant_id=? ORDER BY total_spent DESC",
            (rid,)
        ).fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["الاسم", "الجوال", "المنصة", "VIP", "عدد الطلبات", "إجمالي الإنفاق", "آخر ظهور", "التفضيلات"])
    for r in rows:
        writer.writerow([r["name"] or "", r["phone"] or "", r["platform"] or "",
                         "نعم" if r["vip"] else "لا", r["orders_count"] or 0,
                         r["total_spent"] or 0, r["last_seen"] or "", r["preferences"] or ""])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=customers.csv"}
    )


@app.post("/api/broadcast")
async def broadcast_message(req: Request, background_tasks: BackgroundTasks, user=Depends(current_user)):
    """Send a broadcast message to all customers who have had a conversation."""
    body = await req.json()
    message_text = (body.get("message") or "").strip()
    platform_filter = body.get("platform")  # optional: "telegram","whatsapp","instagram","facebook"
    if not message_text:
        raise HTTPException(400, "الرسالة فارغة")

    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        # Fetch distinct customers with conversations for this restaurant
        query = "SELECT DISTINCT c.id, c.platform, c.phone FROM customers c WHERE c.restaurant_id=? AND c.platform IS NOT NULL"
        params = [rid]
        if platform_filter:
            query += " AND c.platform=?"
            params.append(platform_filter)
        customers = conn.execute(query, params).fetchall()

        # Fetch channel credentials once per platform
        channels = {}
        for ch in conn.execute("SELECT type, token, phone_number_id FROM channels WHERE restaurant_id=?", (rid,)).fetchall():
            channels[ch["type"]] = dict(ch)

        # Collect (platform, external_id) pairs
        targets = []
        for cust in customers:
            cid = cust["id"]
            platform = cust["platform"] or ""
            mem = conn.execute(
                "SELECT memory_value FROM conversation_memory WHERE restaurant_id=? AND customer_id=? AND memory_key='external_id'",
                (rid, cid)
            ).fetchone()
            external_id = mem["memory_value"] if mem else (cust["phone"] or "")
            if external_id and platform:
                targets.append((platform, external_id))
    finally:
        conn.close()

    async def _do_broadcast():
        import httpx as _httpx
        sent = 0
        failed = 0
        async with _httpx.AsyncClient(timeout=10) as client:
            for platform, external_id in targets:
                try:
                    if platform == "telegram":
                        ch = channels.get("telegram", {})
                        bot_token = ch.get("token", "")
                        if not bot_token:
                            continue
                        await client.post(
                            f"https://api.telegram.org/bot{bot_token}/sendMessage",
                            json={"chat_id": external_id, "text": message_text}
                        )
                        sent += 1
                    elif platform == "whatsapp":
                        ch = channels.get("whatsapp", {})
                        access_token = ch.get("token", "")
                        phone_number_id = ch.get("phone_number_id", "")
                        if not access_token or not phone_number_id:
                            continue
                        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                        payload = {
                            "messaging_product": "whatsapp",
                            "to": external_id,
                            "type": "text",
                            "text": {"body": message_text}
                        }
                        await client.post(
                            f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
                            headers=headers, json=payload
                        )
                        sent += 1
                    elif platform in ("instagram", "facebook"):
                        ch = channels.get(platform, {})
                        page_token = ch.get("token", "")
                        if not page_token:
                            continue
                        payload = {"recipient": {"id": external_id}, "message": {"text": message_text}}
                        await client.post(
                            "https://graph.facebook.com/v19.0/me/messages",
                            params={"access_token": page_token},
                            json=payload
                        )
                        sent += 1
                except Exception as _e:
                    failed += 1
                    logger.warning(f"[broadcast] failed to send to {platform}:{external_id}: {_e}")
        logger.info(f"[broadcast] restaurant={rid} sent={sent} failed={failed}")

    background_tasks.add_task(_do_broadcast)
    return {"queued": len(targets), "message": f"تم إرسال الرسالة لـ {len(targets)} زبون"}


# ── Reply Templates ───────────────────────────────────────────────────────────

@app.get("/api/reply-templates")
async def list_reply_templates(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM reply_templates WHERE restaurant_id=? ORDER BY created_at ASC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/reply-templates", status_code=201)
async def create_reply_template(req: Request, user=Depends(current_user)):
    body = await req.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "العنوان والمحتوى مطلوبان")
    tid = str(uuid.uuid4())
    conn = database.get_db()
    conn.execute(
        "INSERT INTO reply_templates (id, restaurant_id, title, content) VALUES (?,?,?,?)",
        (tid, user["restaurant_id"], title, content)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM reply_templates WHERE id=?", (tid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/reply-templates/{tid}")
async def delete_reply_template(tid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM reply_templates WHERE id=? AND restaurant_id=?",
                        (tid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404)
    conn.execute("DELETE FROM reply_templates WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Orders ────────────────────────────────────────────────────────────────────

STATUS_FLOW = {
    "pending": "confirmed",
    "confirmed": "preparing",
    "preparing": "on_way",
    "on_way": "delivered",
}

# Explicit allowed transitions for direct-set (action != advance/cancel)
ALLOWED_TRANSITIONS: dict = {
    "pending":    {"confirmed", "cancelled"},
    "confirmed":  {"preparing", "cancelled"},
    "preparing":  {"on_way", "cancelled"},
    "on_way":     {"delivered", "cancelled"},
    "delivered":  set(),
    "cancelled":  set(),
}


@app.get("/api/orders")
async def list_orders(
    status: Optional[str] = None,
    channel: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = """
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone
        FROM orders o JOIN customers c ON o.customer_id = c.id
        WHERE o.restaurant_id = ?
    """
    params = [rid]
    if status:
        q += " AND o.status=?"; params.append(status)
    if channel:
        q += " AND o.channel=?"; params.append(channel)
    if search:
        q += " AND (c.name LIKE ? OR o.id LIKE ?)"; params += [f"%{search}%", f"%{search}%"]
    if date_from:
        q += " AND DATE(o.created_at) >= ?"; params.append(date_from)
    if date_to:
        q += " AND DATE(o.created_at) <= ?"; params.append(date_to)
    q += " ORDER BY o.created_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/orders/{oid}")
async def get_order(oid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("""
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, c.platform
        FROM orders o JOIN customers c ON o.customer_id = c.id
        WHERE o.id=? AND o.restaurant_id=?
    """, (oid, user["restaurant_id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Order not found")
    items = conn.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
    conn.close()
    result = dict(row)
    result["items"] = [dict(i) for i in items]
    return result


@app.post("/api/orders", status_code=201)
async def create_order(data: OrderCreate, user=Depends(current_user)):
    conn = database.get_db()
    oid = str(uuid.uuid4())
    total = sum(i.get("price", 0) * i.get("quantity", 1) for i in data.items)
    conn.execute("""
        INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, address, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (oid, user["restaurant_id"], data.customer_id, data.channel,
          data.type, total, data.address, data.notes))
    for item in data.items:
        conn.execute("""
            INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), oid, item.get("product_id"),
              item.get("name"), item.get("price", 0), item.get("quantity", 1)))

    log_activity(conn, user["restaurant_id"], "order_created", "order", oid,
                 f"طلب جديد بقيمة {total} د.ع", user["id"], user["name"])
    create_notification(conn, user["restaurant_id"], "new_order", "طلب جديد",
                        f"طلب جديد بقيمة {total} د.ع من {data.channel}", "order", oid)
    conn.commit()

    row = conn.execute("""
        SELECT o.*, c.name AS customer_name FROM orders o
        JOIN customers c ON o.customer_id = c.id WHERE o.id=?
    """, (oid,)).fetchone()
    conn.close()
    return dict(row)


async def _notify_customer_confirmed(order: dict, restaurant_id: str) -> None:
    """Send a confirmation message to the customer on their platform (non-fatal)."""
    try:
        import httpx as _httpx
        conn = database.get_db()
        try:
            customer_id = order.get("customer_id", "")
            # Get the platform/channel from customers table
            cust_row = conn.execute(
                "SELECT platform, phone FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            if not cust_row:
                return
            platform = cust_row["platform"] or ""

            # Get the customer's external_id from conversation_memory
            mem_row = conn.execute(
                "SELECT memory_value FROM conversation_memory WHERE restaurant_id=? AND customer_id=? AND memory_key='external_id'",
                (restaurant_id, customer_id)
            ).fetchone()
            external_id = mem_row["memory_value"] if mem_row else cust_row["phone"] or ""
            if not external_id:
                return

            # Get delivery_time from settings
            settings_row = conn.execute(
                "SELECT delivery_time FROM settings WHERE restaurant_id=?", (restaurant_id,)
            ).fetchone()
            delivery_time = settings_row["delivery_time"] if settings_row and "delivery_time" in settings_row.keys() else ""

            # Build message
            msg_lines = ["✅ طلبك وصلنا وصار بالتجهيز!"]
            if delivery_time:
                msg_lines.append(f"⏱️ الوقت التقريبي للتوصيل: {delivery_time}")
            msg_lines.append("شكراً لك، نشوفك قريب 😊")
            message_text = "\n".join(msg_lines)

            if platform == "telegram":
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'",
                    (restaurant_id,)
                ).fetchone()
                bot_token = ch["token"] if ch else ""
                if not bot_token or not external_id:
                    return
                async with _httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": external_id, "text": message_text}
                    )

            elif platform == "whatsapp":
                ch = conn.execute(
                    "SELECT token, phone_number_id FROM channels WHERE restaurant_id=? AND type='whatsapp'",
                    (restaurant_id,)
                ).fetchone()
                if not ch:
                    return
                access_token = ch["token"] if ch else ""
                phone_number_id = ch["phone_number_id"] if "phone_number_id" in ch.keys() else ""
                if not access_token or not phone_number_id or not external_id:
                    return
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                payload = {
                    "messaging_product": "whatsapp",
                    "to": external_id,
                    "type": "text",
                    "text": {"body": message_text}
                }
                async with _httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
                        headers=headers, json=payload
                    )

            elif platform in ("instagram", "facebook"):
                ch_type = platform
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type=?",
                    (restaurant_id, ch_type)
                ).fetchone()
                page_token = ch["token"] if ch else ""
                if not page_token or not external_id:
                    return
                payload = {"recipient": {"id": external_id}, "message": {"text": message_text}}
                async with _httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        "https://graph.facebook.com/v19.0/me/messages",
                        params={"access_token": page_token},
                        json=payload
                    )
        finally:
            conn.close()
    except Exception as _e:
        logger.warning(f"[order] customer confirmed notify failed (non-fatal): {_e}")


@app.patch("/api/orders/{oid}/status")
async def update_order_status(oid: str, req: Request, background_tasks: BackgroundTasks, user=Depends(current_user)):
    body = await req.json()
    action = body.get("action", "advance")
    conn = database.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND restaurant_id=?",
                         (oid, user["restaurant_id"])).fetchone()
    if not order:
        conn.close()
        raise HTTPException(404, "Order not found")
    BLOCKED_FROM = {"delivered", "cancelled"}
    VALID_STATUSES = {"pending", "confirmed", "preparing", "on_way", "delivered", "cancelled"}

    if action == "cancel":
        if order["status"] in BLOCKED_FROM:
            conn.close()
            raise HTTPException(400, "لا يمكن إلغاء طلب مكتمل أو ملغى مسبقاً")
        new_status = "cancelled"
        log_activity(conn, user["restaurant_id"], "order_cancelled", "order", oid,
                     f"تم إلغاء الطلب #{oid[:8]}", user["id"], user["name"])
    elif action == "advance":
        if order["status"] in BLOCKED_FROM:
            conn.close()
            raise HTTPException(400, "لا يمكن تقديم هذا الطلب")
        new_status = STATUS_FLOW.get(order["status"])
        if not new_status:
            conn.close()
            raise HTTPException(400, "لا يمكن تقديم هذا الطلب")
        log_activity(conn, user["restaurant_id"], "order_status_changed", "order", oid,
                     f"تغيير حالة الطلب إلى {new_status}", user["id"], user["name"])
    else:
        if action not in VALID_STATUSES:
            conn.close()
            raise HTTPException(400, "حالة غير صحيحة")
        allowed = ALLOWED_TRANSITIONS.get(order["status"], set())
        if action not in allowed:
            conn.close()
            raise HTTPException(400, f"لا يمكن الانتقال من {order['status']} إلى {action}")
        new_status = action
        log_activity(conn, user["restaurant_id"], "order_status_changed", "order", oid,
                     f"تغيير حالة الطلب إلى {new_status}", user["id"], user["name"])

    conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, oid))

    # Notify on new pending order
    if new_status == "pending":
        create_notification(conn, user["restaurant_id"], "new_order",
                            "طلب جديد في الانتظار",
                            f"الطلب #{oid[:8]} في انتظار التأكيد", "order", oid)

    # Notify customer when order is confirmed
    if new_status == "confirmed":
        order_dict = dict(order)
        background_tasks.add_task(_notify_customer_confirmed, order_dict, user["restaurant_id"])

    conn.commit()
    conn.close()
    return {"status": new_status}


@app.patch("/api/orders/{oid}")
async def update_order(oid: str, data: OrderUpdate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM orders WHERE id=? AND restaurant_id=?",
                        (oid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Order not found")
    upd = {}
    if data.notes is not None: upd["notes"] = data.notes
    if data.address is not None: upd["address"] = data.address
    if upd:
        conn.execute(f"UPDATE orders SET {','.join(k+'=?' for k in upd)} WHERE id=?",
                     list(upd.values()) + [oid])
        conn.commit()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/orders/{oid}")
async def delete_order(oid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM orders WHERE id=? AND restaurant_id=?",
                        (oid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Order not found")
    conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
    conn.execute("DELETE FROM orders WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/api/conversations")
async def list_conversations(
    mode: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = """
        SELECT cv.*, c.name AS customer_name, c.platform, c.phone
        FROM conversations cv JOIN customers c ON cv.customer_id = c.id
        WHERE cv.restaurant_id=?
    """
    params = [rid]
    if mode:
        q += " AND cv.mode=?"; params.append(mode)
    if status:
        q += " AND cv.status=?"; params.append(status)
    if search:
        q += " AND (c.name LIKE ? OR c.phone LIKE ?)"; params += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY cv.updated_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/conversations/{cid}/messages")
async def get_messages(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    msgs = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,)).fetchall()
    conn.close()
    return [dict(m) for m in msgs]


@app.post("/api/conversations/{cid}/messages")
async def send_message(cid: str, data: MsgCreate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    mid = str(uuid.uuid4())
    conn.execute("INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'staff', ?)",
                 (mid, cid, data.content))
    conn.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP, unread_count=0, mode='human' WHERE id=?", (cid,))
    conn.commit()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(msg)


@app.get("/api/outbound/failed")
async def list_failed_messages(user=Depends(current_user)):
    """Return last 50 failed outbound messages for this restaurant."""
    conn = database.get_db()
    rows = conn.execute(
        """SELECT id, conversation_id, platform, recipient_id, content, error, created_at
           FROM outbound_messages
           WHERE restaurant_id=? AND status='failed'
           ORDER BY created_at DESC LIMIT 50""",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.patch("/api/conversations/{cid}/messages/{mid}")
async def edit_message(cid: str, mid: str, req: Request, user=Depends(current_user)):
    body = await req.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "المحتوى مطلوب")
    conn = database.get_db()
    msg = conn.execute(
        "SELECT m.* FROM messages m JOIN conversations c ON m.conversation_id=c.id "
        "WHERE m.id=? AND m.conversation_id=? AND c.restaurant_id=?",
        (mid, cid, user["restaurant_id"])
    ).fetchone()
    if not msg:
        conn.close()
        raise HTTPException(404, "الرسالة غير موجودة")
    conn.execute("UPDATE messages SET content=? WHERE id=?", (content, mid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/conversations/{cid}/mode")
async def toggle_mode(cid: str, req: Request, user=Depends(current_user)):
    body = await req.json()
    mode = body.get("mode", "bot")
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    conn.execute("UPDATE conversations SET mode=? WHERE id=?", (mode, cid))
    log_activity(conn, user["restaurant_id"], "conversation_mode_changed", "conversation", cid,
                 f"تغيير وضع المحادثة إلى {mode}", user["id"], user["name"])
    conn.commit()
    conn.close()
    return {"mode": mode}


@app.patch("/api/conversations/{cid}/urgent")
async def set_urgent(cid: str, req: Request, user=Depends(current_user)):
    body = await req.json()
    urgent = bool(body.get("urgent", True))
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    conn.execute("UPDATE conversations SET urgent=? WHERE id=?", (int(urgent), cid))
    conn.commit()
    conn.close()
    return {"urgent": urgent}


@app.patch("/api/conversations/{cid}/read")
async def mark_read(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    conn.execute("UPDATE conversations SET unread_count=0 WHERE id=? AND restaurant_id=?",
                 (cid, user["restaurant_id"]))
    conn.commit()
    conn.close()
    return {"unread_count": 0}


@app.post("/api/conversations/{cid}/ai-reply")
async def ai_reply(cid: str, user=Depends(current_user)):
    if not openai_client:
        raise HTTPException(503, "OpenAI غير مهيأ — أضف OPENAI_API_KEY في ملف .env")
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    s = conn.execute("SELECT * FROM settings WHERE restaurant_id=?", (user["restaurant_id"],)).fetchone()
    r = conn.execute("SELECT * FROM restaurants WHERE id=?", (user["restaurant_id"],)).fetchone()
    msgs = list(reversed(conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 10", (cid,)).fetchall()))
    conn.close()

    bot_name = (s["bot_name"] if s else None) or "AI Assistant"
    rest_name = (r["name"] if r else None) or "المطعم"
    system_prompt = (
        f"أنت {bot_name}، مساعد ذكاء اصطناعي لمطعم {rest_name}.\n"
        "مهمتك مساعدة العملاء بشكل ودي ومحترف.\nأجب باختصار وبفائدة."
    )
    chat_msgs = [{"role": "system", "content": system_prompt}]
    for m in msgs:
        role = "user" if m["role"] == "customer" else "assistant"
        chat_msgs.append({"role": role, "content": m["content"]})

    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=chat_msgs,
        max_tokens=300,
    )
    ai_content = resp.choices[0].message.content

    conn = database.get_db()
    mid = str(uuid.uuid4())
    conn.execute("INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'bot', ?)",
                 (mid, cid, ai_content))
    conn.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
    conn.commit()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(msg)


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM settings WHERE restaurant_id=?",
                       (user["restaurant_id"],)).fetchone()
    conn.close()
    return dict(row) if row else {}


@app.put("/api/settings")
async def update_settings(data: SettingsUpdate, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    if not conn.execute("SELECT id FROM settings WHERE restaurant_id=?", (rid,)).fetchone():
        conn.execute("INSERT INTO settings (id, restaurant_id) VALUES (?, ?)", (str(uuid.uuid4()), rid))
        conn.commit()
    upd = {}
    if data.restaurant_name is not None: upd["restaurant_name"] = data.restaurant_name
    if data.restaurant_description is not None: upd["restaurant_description"] = data.restaurant_description
    if data.restaurant_phone is not None: upd["restaurant_phone"] = data.restaurant_phone
    if data.restaurant_address is not None: upd["restaurant_address"] = data.restaurant_address
    if data.working_hours is not None: upd["working_hours"] = json.dumps(data.working_hours)
    if data.bot_name is not None: upd["bot_name"] = data.bot_name
    if data.bot_personality is not None: upd["bot_personality"] = data.bot_personality
    if data.bot_language is not None: upd["bot_language"] = data.bot_language
    if data.bot_welcome is not None: upd["bot_welcome"] = data.bot_welcome
    if data.bot_enabled is not None: upd["bot_enabled"] = int(data.bot_enabled)
    if data.security_2fa is not None: upd["security_2fa"] = int(data.security_2fa)
    if data.security_session_timeout is not None: upd["security_session_timeout"] = data.security_session_timeout
    if data.payment_methods is not None: upd["payment_methods"] = data.payment_methods
    if data.business_type is not None: upd["business_type"] = data.business_type
    if data.delivery_time is not None: upd["delivery_time"] = data.delivery_time
    if data.notify_chat_id is not None: upd["notify_chat_id"] = data.notify_chat_id
    if data.delivery_fee is not None: upd["delivery_fee"] = data.delivery_fee
    if data.min_order is not None: upd["min_order"] = data.min_order
    if data.report_frequency is not None and data.report_frequency in ("none", "daily", "weekly"):
        upd["report_frequency"] = data.report_frequency
    if data.menu_url is not None: upd["menu_url"] = data.menu_url
    if upd:
        conn.execute(f"UPDATE settings SET {','.join(k+'=?' for k in upd)} WHERE restaurant_id=?",
                     list(upd.values()) + [rid])
        # Keep restaurants.name in sync so JWT always has the current name
        if "restaurant_name" in upd and upd["restaurant_name"]:
            conn.execute("UPDATE restaurants SET name=? WHERE id=?", (upd["restaurant_name"], rid))
        conn.commit()
    row = conn.execute("SELECT * FROM settings WHERE restaurant_id=?", (rid,)).fetchone()
    conn.close()
    return dict(row)


# ── Channels ──────────────────────────────────────────────────────────────────

def _mask_token(val: str) -> str:
    """Show first 6 chars + asterisks for long secrets."""
    if not val or len(val) < 8:
        return val
    return val[:6] + "****" + val[-3:]


@app.get("/api/channels")
async def list_channels(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM channels WHERE restaurant_id=? ORDER BY type",
                        (user["restaurant_id"],)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        # Mask sensitive fields in list view — frontend fills inputs from DB on edit
        # Do NOT mask here so the UI can pre-fill the fields correctly
        result.append(d)
    return result


@app.put("/api/channels/{ch_type}")
async def update_channel(ch_type: str, data: ChannelUpdate, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    existing = conn.execute("SELECT * FROM channels WHERE restaurant_id=? AND type=?",
                            (rid, ch_type)).fetchone()
    token_action = "provided" if data.token else "not provided"
    logger.info(f"[channel] save {ch_type} for restaurant={rid} token={token_action}")
    if not existing:
        conn.execute("""
            INSERT INTO channels (id, restaurant_id, type, name, token, webhook_url, username, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), rid, ch_type,
              data.name or "", data.token or "", data.webhook_url or "",
              data.username or "", int(data.enabled or False)))
    else:
        upd = {}
        if data.name is not None: upd["name"] = data.name
        if data.token is not None: upd["token"] = data.token
        if data.webhook_url is not None: upd["webhook_url"] = data.webhook_url
        if data.username is not None: upd["username"] = data.username
        if data.enabled is not None: upd["enabled"] = int(data.enabled)
        if data.webhook_secret is not None: upd["webhook_secret"] = data.webhook_secret
        if data.admin_chat_id is not None: upd["admin_chat_id"] = data.admin_chat_id
        if data.phone_number_id is not None: upd["phone_number_id"] = data.phone_number_id
        if data.business_account_id is not None: upd["business_account_id"] = data.business_account_id
        if data.verify_token is not None: upd["verify_token"] = data.verify_token
        if data.app_id is not None: upd["app_id"] = data.app_id
        if data.app_secret is not None: upd["app_secret"] = data.app_secret
        if data.page_id is not None: upd["page_id"] = data.page_id
        if data.page_name is not None: upd["page_name"] = data.page_name
        if data.bot_username is not None: upd["bot_username"] = data.bot_username
        if upd:
            conn.execute(f"UPDATE channels SET {','.join(k+'=?' for k in upd)} WHERE restaurant_id=? AND type=?",
                         list(upd.values()) + [rid, ch_type])

    log_activity(conn, rid, "channel_updated", "channel", ch_type,
                 f"تحديث إعدادات قناة {ch_type}", user["id"], user["name"])
    conn.commit()
    row = conn.execute("SELECT * FROM channels WHERE restaurant_id=? AND type=?", (rid, ch_type)).fetchone()
    conn.close()
    return dict(row)


@app.post("/api/channels/{ch_type}/test")
async def test_channel(ch_type: str, user=Depends(current_user)):
    import httpx as _httpx
    conn = database.get_db()
    rid = user["restaurant_id"]
    ch = conn.execute("SELECT * FROM channels WHERE restaurant_id=? AND type=?",
                      (rid, ch_type)).fetchone()
    if not ch:
        conn.close()
        raise HTTPException(404, "القناة غير موجودة")
    if not ch["token"]:
        logger.warning(f"[channel] test {ch_type} failed — no token in DB for restaurant={rid}")
        conn.close()
        raise HTTPException(400, "يجب إدخال التوكن أولاً")
    logger.info(f"[channel] testing {ch_type} for restaurant={rid}")

    rid = user["restaurant_id"]
    result = {"success": False, "message": "", "detail": {}}

    if ch_type == "telegram":
        try:
            r = _httpx.get(
                f"https://api.telegram.org/bot{ch['token']}/getMe",
                timeout=10
            )
            data = r.json()
            if data.get("ok"):
                bot_info = data.get("result", {})
                result = {
                    "success": True,
                    "message": f"اتصال ناجح — البوت: @{bot_info.get('username', '')}",
                    "detail": bot_info,
                }
                conn.execute(
                    "UPDATE channels SET verified=1, connection_status='connected', last_error='', last_tested_at=CURRENT_TIMESTAMP, bot_username=? WHERE restaurant_id=? AND type=?",
                    (bot_info.get("username", ""), rid, ch_type)
                )
                logger.info(f"[telegram] test OK — bot=@{bot_info.get('username', '?')} restaurant={rid}")
            else:
                err = data.get("description", "خطأ غير معروف")
                result = {"success": False, "message": f"فشل الاتصال: {err}", "detail": data}
                conn.execute(
                    "UPDATE channels SET connection_status='error', last_error=?, last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type=?",
                    (err, rid, ch_type)
                )
                logger.error(f"[telegram] test FAILED — restaurant={rid} | {err}")
        except Exception as e:
            err = str(e)
            result = {"success": False, "message": f"خطأ في الاتصال: {err}", "detail": {}}
            conn.execute(
                "UPDATE channels SET connection_status='error', last_error=?, last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type=?",
                (err, rid, ch_type)
            )
            logger.error(f"[telegram] test exception — restaurant={rid} | {err}")

    elif ch_type in ("whatsapp", "instagram", "facebook"):
        # For Meta platforms, verify the token is non-empty and try to call the Graph API
        try:
            r = _httpx.get(
                "https://graph.facebook.com/v19.0/me",
                params={"access_token": ch["token"], "fields": "name,id"},
                timeout=10,
            )
            data = r.json()
            if "error" not in data:
                result = {
                    "success": True,
                    "message": f"اتصال ناجح — الصفحة: {data.get('name', data.get('id', ''))}",
                    "detail": data,
                }
                conn.execute(
                    "UPDATE channels SET verified=1, connection_status='connected', last_error='', last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type=?",
                    (rid, ch_type)
                )
            else:
                err = data["error"].get("message", "خطأ غير معروف")
                result = {"success": False, "message": f"فشل التحقق: {err}", "detail": data}
                conn.execute(
                    "UPDATE channels SET connection_status='error', last_error=?, last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type=?",
                    (err, rid, ch_type)
                )
        except Exception as e:
            err = str(e)
            result = {"success": False, "message": f"خطأ في الاتصال: {err}", "detail": {}}
            conn.execute(
                "UPDATE channels SET connection_status='error', last_error=?, last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type=?",
                (err, rid, ch_type)
            )
    else:
        result = {"success": True, "message": f"تم اختبار قناة {ch_type}"}

    conn.commit()
    conn.close()
    return result


@app.post("/api/channels/telegram/register-webhook")
async def register_telegram_webhook(request: Request, user=Depends(current_user)):
    """Register this server's webhook URL with Telegram Bot API."""
    import httpx as _httpx

    # Prefer env var; if missing or localhost, derive from actual request URL
    base = os.getenv("BASE_URL", "").rstrip("/")
    if not base or "localhost" in base or "127.0.0.1" in base:
        base = str(request.base_url).rstrip("/")

    conn = database.get_db()
    rid = user["restaurant_id"]
    ch = conn.execute(
        "SELECT * FROM channels WHERE restaurant_id=? AND type='telegram'", (rid,)
    ).fetchone()

    if not ch:
        conn.close()
        raise HTTPException(400, "قناة Telegram غير موجودة — احفظ إعدادات القناة أولاً")
    if not ch["token"]:
        logger.warning(f"[telegram] register-webhook failed — no token in DB for restaurant={rid}")
        conn.close()
        raise HTTPException(400, "Bot Token فارغ — أدخل التوكن واضغط حفظ أولاً")

    webhook_url = f"{base}/webhook/telegram/{rid}"
    logger.info(f"[telegram] registering webhook for restaurant={rid} url={webhook_url}")

    try:
        r = _httpx.post(
            f"https://api.telegram.org/bot{ch['token']}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "edited_message", "callback_query"]},
            timeout=15,
        )
        data = r.json()
        if data.get("ok"):
            # Verify registration via getWebhookInfo
            info_r = _httpx.get(
                f"https://api.telegram.org/bot{ch['token']}/getWebhookInfo", timeout=10
            )
            info = info_r.json().get("result", {})
            conn.execute(
                "UPDATE channels SET webhook_url=?, connection_status='connected', last_error='', last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
                (webhook_url, rid)
            )
            conn.commit()
            conn.close()
            logger.info(f"[telegram] webhook registered OK — restaurant={rid} url={webhook_url}")
            return {
                "success": True,
                "message": f"✅ تم تسجيل الويب هوك بنجاح",
                "webhook_url": webhook_url,
                "telegram_info": info,
            }
        else:
            err = data.get("description", "فشل التسجيل")
            conn.execute(
                "UPDATE channels SET last_error=?, connection_status='error', last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
                (err, rid)
            )
            conn.commit()
            conn.close()
            logger.error(f"[telegram] setWebhook FAILED — restaurant={rid} | {err}")
            raise HTTPException(400, f"رفض Telegram الطلب: {err}")
    except HTTPException:
        raise
    except Exception as e:
        conn.close()
        logger.error(f"[telegram] register-webhook exception — restaurant={rid} | {e}")
        raise HTTPException(500, f"خطأ في الاتصال بـ Telegram: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ── INTEGRATIONS HUB ─────────────────────────────────────────────────────────
# Scalable channel connection framework (OAuth2 / Embedded Signup / bot_token)
# ══════════════════════════════════════════════════════════════════════════════

# get_adapter, PLATFORM_CATALOG, _secrets imported at top of file


def _channel_to_dict(row) -> dict:
    """Convert a channels row to a clean dict safe for the frontend."""
    d = dict(row)
    # Never expose raw secrets to the API
    for f in ("app_secret", "webhook_secret"):
        if d.get(f):
            v = d[f]
            d[f] = v[:4] + "****" if len(v) > 4 else "****"
    return d


def _get_or_create_channel(conn, restaurant_id: str, platform: str) -> dict:
    """Return existing channel row as dict, or create a bare-minimum skeleton."""
    row = conn.execute(
        "SELECT * FROM channels WHERE restaurant_id=? AND type=?",
        (restaurant_id, platform)
    ).fetchone()
    if row:
        return dict(row)
    # Create skeleton row so the card renders with 'disconnected' state
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO channels (id, restaurant_id, type, name, token, enabled, connection_status) "
        "VALUES (?,?,?,?,?,0,'disconnected')",
        (cid, restaurant_id, platform, PLATFORM_CATALOG.get(platform, {}).get("display_name", platform), "")
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM channels WHERE id=?", (cid,)).fetchone())


@app.get("/api/integrations/catalog")
async def integrations_catalog(user=Depends(current_user)):
    """Return platform catalog + current connection state for each platform."""
    conn = database.get_db()
    rid  = user["restaurant_id"]
    try:
        rows = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? ORDER BY type",
            (rid,)
        ).fetchall()
        channels_by_type = {dict(r)["type"]: _channel_to_dict(r) for r in rows}

        result = []
        for platform, meta in sorted(PLATFORM_CATALOG.items(), key=lambda x: x[1].get("order", 99)):
            ch = channels_by_type.get(platform, {})
            entry = {
                **meta,
                "platform":       platform,
                "has_adapter":    get_adapter(platform) is not None,
                "meta_app_configured": bool(META_APP_ID),
                # channel state
                "channel_id":         ch.get("id", ""),
                "connected":          ch.get("connection_status") == "connected",
                "connection_status":  ch.get("connection_status", "disconnected"),
                "enabled":            bool(ch.get("enabled", 0)),
                "reconnect_needed":   bool(ch.get("reconnect_needed", 0)),
                "account_display_name": ch.get("account_display_name", ""),
                "account_picture_url":  ch.get("account_picture_url", ""),
                "phone_number_display": ch.get("phone_number_display", ""),
                "page_name":          ch.get("page_name", ""),
                "last_error":         ch.get("last_error", ""),
                "last_tested_at":     ch.get("last_tested_at", ""),
                "token_expires_at":   ch.get("token_expires_at", ""),
                "oauth_completed_at": ch.get("oauth_completed_at", ""),
                "webhook_url":        f"{BASE_URL}/webhook/{platform}/{rid}" if ch.get("id") else "",
                # For Telegram — expose non-secret fields for form prefill
                "bot_username":   ch.get("bot_username", "") if platform == "telegram" else "",
                "has_token":      bool(ch.get("token", "")),
            }
            result.append(entry)
        return result
    finally:
        conn.close()


@app.post("/api/integrations/oauth/start")
async def integrations_oauth_start(data: dict, user=Depends(current_user)):
    """
    Initiate OAuth for Facebook or Instagram.
    Returns {auth_url, state} — frontend opens auth_url in a popup.
    """
    platform     = (data.get("platform") or "").lower()
    adapter      = get_adapter(platform)
    if not adapter or adapter.auth_type not in ("oauth2",):
        raise HTTPException(400, f"Platform '{platform}' does not support OAuth flow")
    if not META_APP_ID:
        raise HTTPException(400, "META_APP_ID غير مضبوط — أضفه إلى ملف .env")

    redirect_uri = f"{BASE_URL}/oauth/meta/callback"
    state        = _secrets.token_hex(24)
    expires_at   = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO oauth_states (id, restaurant_id, user_id, platform, state, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), user["restaurant_id"], user["id"], platform, state, expires_at)
        )
        conn.commit()
    finally:
        conn.close()

    auth_url = adapter.build_auth_url(state, redirect_uri)
    return {"auth_url": auth_url, "state": state}


@app.get("/api/integrations/oauth/callback")
async def integrations_oauth_callback(
    code:  str = None,
    state: str = None,
    error: str = None,
    req: Request = None
):
    """
    Meta redirects here after the user approves the OAuth dialog.
    This endpoint exchanges the code, then redirects the browser back to the
    dashboard with an oauth_session ID so the JS can complete the flow.
    """
    from fastapi.responses import RedirectResponse as _Redirect

    frontend_base = BASE_URL

    if error or not state or not code:
        return _Redirect(f"{frontend_base}/#channels?error=access_denied")

    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT * FROM oauth_states WHERE state=? AND used=0",
            (state,)
        ).fetchone()
        if not row:
            return _Redirect(f"{frontend_base}/#channels?error=invalid_state")

        row = dict(row)
        if row["expires_at"] < datetime.utcnow().isoformat():
            return _Redirect(f"{frontend_base}/#channels?error=state_expired")

        # Mark used
        conn.execute("UPDATE oauth_states SET used=1 WHERE state=?", (state,))
        conn.commit()

        platform     = row["platform"]
        redirect_uri = f"{BASE_URL}/oauth/meta/callback"
        adapter      = get_adapter(platform)

        try:
            result = adapter.exchange_code(code, redirect_uri)
        except Exception as exc:
            err_str = str(exc)
            logger.error(f"[oauth] exchange_code failed platform={platform} redirect_uri={redirect_uri} error={err_str}")
            # Write error to channel card so it's visible in the dashboard
            try:
                _ch = conn.execute(
                    "SELECT id FROM channels WHERE restaurant_id=? AND type=?",
                    (row["restaurant_id"], platform)
                ).fetchone()
                if _ch:
                    conn.execute(
                        "UPDATE channels SET last_error=?, reconnect_needed=1, connection_status='error' WHERE id=?",
                        (f"OAuth فشل: {err_str[:200]}", _ch["id"])
                    )
                    conn.commit()
            except Exception:
                pass
            from urllib.parse import quote as _quote
            hint = _quote(err_str[:120], safe="")
            return _Redirect(f"{frontend_base}/#channels?error=exchange_failed&hint={hint}")

        # Store pages/accounts in oauth_states so the picker endpoint can return them
        pages_json = json.dumps(result.get("pages") or result.get("accounts") or [])
        conn.execute(
            "UPDATE oauth_states SET pages_json=? WHERE state=?",
            (pages_json, state)
        )
        # Also stash access_token + expiry temporarily in pages_json row
        pending_json = json.dumps({
            "access_token":     result.get("access_token", ""),
            "token_expires_at": result.get("token_expires_at", ""),
            "scopes_granted":   result.get("scopes_granted", ""),
            "pages":            result.get("pages") or result.get("accounts") or [],
        })
        conn.execute("UPDATE oauth_states SET pages_json=? WHERE state=?", (pending_json, state))
        conn.commit()

        # Redirect back with state ID as session token (the JS will call /pending/{state})
        return _Redirect(f"{frontend_base}/#channels?oauth_session={state}&platform={platform}")
    finally:
        conn.close()


@app.get("/oauth/meta/callback")
async def oauth_meta_callback_clean(
    code:  str = None,
    state: str = None,
    error: str = None,
    req: Request = None
):
    """Clean public OAuth callback URL registered with Meta Developer Console."""
    return await integrations_oauth_callback(code=code, state=state, error=error, req=req)


@app.get("/api/integrations/oauth/pending/{state_id}")
async def integrations_oauth_pending(state_id: str, user=Depends(current_user)):
    """
    Return the pages/accounts list for the page-picker modal.
    Called by the JS after it detects oauth_session in the URL hash.
    """
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT * FROM oauth_states WHERE state=? AND restaurant_id=?",
            (state_id, user["restaurant_id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "OAuth session not found")
        data = json.loads(row["pages_json"] or "{}")
        return {
            "platform": row["platform"],
            "pages":    data.get("pages", []),
            "access_token":     data.get("access_token", ""),
            "token_expires_at": data.get("token_expires_at", ""),
            "scopes_granted":   data.get("scopes_granted", ""),
        }
    finally:
        conn.close()


@app.post("/api/integrations/oauth/select-page")
async def integrations_oauth_select_page(data: dict, user=Depends(current_user)):
    """
    After the page picker: save the chosen page, subscribe webhook, finalize connection.
    Body: {state_id, page_id, page_name, page_token, picture_url, platform}
    For Instagram: body uses account_id, account_name, account_username, page_id, page_token
    """
    state_id    = data.get("state_id", "")
    platform    = (data.get("platform") or "").lower()
    rid         = user["restaurant_id"]

    conn = database.get_db()
    try:
        # Validate state ownership
        row = conn.execute(
            "SELECT * FROM oauth_states WHERE state=? AND restaurant_id=?",
            (state_id, rid)
        ).fetchone()
        if not row:
            raise HTTPException(400, "OAuth session غير صالح")

        row        = dict(row)
        pending    = json.loads(row["pages_json"] or "{}")
        long_token = pending.get("access_token", "")
        expires_at = pending.get("token_expires_at", "")
        scopes     = pending.get("scopes_granted", "")

        verify_token = str(uuid.uuid4())
        now_iso      = datetime.utcnow().isoformat()

        if platform == "facebook":
            page_token   = data.get("page_token", long_token)
            page_id      = data.get("page_id", "")
            page_name    = data.get("page_name", "")
            picture_url  = data.get("picture_url", "")

            channel_data = {
                "restaurant_id": rid, "token": page_token, "page_id": page_id,
                "page_name": page_name, "verify_token": verify_token,
            }
            adapter = get_adapter("facebook")
            try:
                adapter.subscribe_webhook(channel_data, BASE_URL)
                webhook_ok = True
            except Exception as exc:
                logger.warning(f"[fb] webhook subscribe failed: {exc}")
                webhook_ok = False

            ch = _get_or_create_channel(conn, rid, "facebook")
            conn.execute("""
                UPDATE channels SET
                    token=?, page_id=?, page_name=?, verify_token=?,
                    token_expires_at=?, scopes_granted=?, oauth_completed_at=?,
                    account_display_name=?, account_picture_url=?,
                    connected_by_user_id=?, connection_status=?,
                    enabled=1, verified=1, reconnect_needed=0, last_error='',
                    last_tested_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (page_token, page_id, page_name, verify_token,
                  expires_at, scopes, now_iso,
                  page_name, picture_url,
                  user["id"], "connected" if webhook_ok else "connected",
                  ch["id"]))

        elif platform == "instagram":
            account_id    = data.get("account_id", "")
            account_name  = data.get("account_name", "")
            account_user  = data.get("account_username", "")
            picture_url   = data.get("picture_url", "")
            page_id       = data.get("page_id", "")
            page_token    = data.get("page_token", long_token)

            channel_data = {
                "restaurant_id": rid, "token": page_token, "page_id": page_id,
                "business_account_id": account_id, "verify_token": verify_token,
            }
            adapter = get_adapter("instagram")
            try:
                adapter.subscribe_webhook(channel_data, BASE_URL)
                webhook_ok = True
            except Exception as exc:
                logger.warning(f"[ig] webhook subscribe failed: {exc}")
                webhook_ok = False

            ch = _get_or_create_channel(conn, rid, "instagram")
            conn.execute("""
                UPDATE channels SET
                    token=?, page_id=?, business_account_id=?,
                    verify_token=?, token_expires_at=?, scopes_granted=?,
                    oauth_completed_at=?, account_display_name=?,
                    account_picture_url=?, username=?,
                    connected_by_user_id=?, connection_status=?,
                    enabled=1, verified=1, reconnect_needed=0, last_error='',
                    last_tested_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (page_token, page_id, account_id,
                  verify_token, expires_at, scopes, now_iso,
                  account_name or account_user, picture_url, account_user,
                  user["id"], "connected",
                  ch["id"]))

        conn.commit()
        log_activity(conn, rid, "channel_oauth_connected", "channel", platform,
                     f"OAuth connection completed for {platform}",
                     user_id=user["id"], user_name=user.get("name", ""))
        conn.commit()

        updated_ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type=?", (rid, platform)
        ).fetchone()
        return {"ok": True, "channel": _channel_to_dict(updated_ch)}
    finally:
        conn.close()


@app.post("/api/integrations/whatsapp/embedded-signup")
async def integrations_wa_embedded_signup(data: dict, user=Depends(current_user)):
    """
    Exchange a WhatsApp Embedded Signup code → access token → store connection.
    Body: {code, waba_id, phone_number_id}
    """
    code            = data.get("code", "")
    waba_id         = data.get("waba_id", "")
    phone_number_id = data.get("phone_number_id", "")
    rid             = user["restaurant_id"]

    if not code:
        raise HTTPException(400, "code مطلوب")
    if not META_APP_ID:
        raise HTTPException(400, "META_APP_ID غير مضبوط")

    adapter = get_adapter("whatsapp")
    conn    = database.get_db()
    try:
        try:
            result = adapter.exchange_code(code)
        except Exception as exc:
            raise HTTPException(400, f"فشل تبادل التوكن: {exc}")

        token      = result["access_token"]
        expires_at = result.get("token_expires_at", "")

        # Confirm phone number + get display number
        phone_display = phone_number_id
        if waba_id and phone_number_id:
            try:
                numbers = adapter.get_waba_phone_numbers(token, waba_id)
                for n in numbers:
                    if n["id"] == phone_number_id:
                        phone_display = n.get("display_phone_number", phone_number_id)
                        break
            except Exception as exc:
                logger.warning(f"[wa] get_waba_phone_numbers failed: {exc}")

        verify_token = str(uuid.uuid4())
        channel_data = {
            "restaurant_id":  rid,
            "token":          token,
            "waba_id":        waba_id,
            "business_account_id": waba_id,
            "phone_number_id": phone_number_id,
            "verify_token":   verify_token,
        }

        # Subscribe webhook
        try:
            adapter.subscribe_webhook(channel_data, BASE_URL)
        except Exception as exc:
            logger.warning(f"[wa] webhook subscribe failed: {exc}")

        now_iso = datetime.utcnow().isoformat()
        ch = _get_or_create_channel(conn, rid, "whatsapp")
        conn.execute("""
            UPDATE channels SET
                token=?, waba_id=?, business_account_id=?,
                phone_number_id=?, phone_number_display=?,
                verify_token=?, token_expires_at=?,
                oauth_completed_at=?, account_display_name=?,
                connected_by_user_id=?, connection_status=?,
                enabled=1, verified=1, reconnect_needed=0, last_error='',
                last_tested_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (token, waba_id, waba_id,
              phone_number_id, phone_display,
              verify_token, expires_at,
              now_iso, phone_display,
              user["id"], "connected",
              ch["id"]))
        conn.commit()

        log_activity(conn, rid, "channel_oauth_connected", "channel", "whatsapp",
                     "WhatsApp Embedded Signup completed",
                     user_id=user["id"], user_name=user.get("name", ""))
        conn.commit()

        updated = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='whatsapp'", (rid,)
        ).fetchone()
        return {"ok": True, "channel": _channel_to_dict(updated)}
    finally:
        conn.close()


@app.post("/api/integrations/{platform}/disconnect")
async def integrations_disconnect(platform: str, user=Depends(current_user)):
    """Disconnect a channel — clears token and sets status to disconnected."""
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT id FROM channels WHERE restaurant_id=? AND type=?",
            (rid, platform)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "Channel not found")
        conn.execute("""
            UPDATE channels SET
                token='', page_id='', page_name='',
                business_account_id='', phone_number_id='', phone_number_display='',
                waba_id='', token_expires_at='', scopes_granted='',
                account_display_name='', account_picture_url='',
                connected_by_user_id='', oauth_completed_at='',
                verify_token='', enabled=0, verified=0,
                connection_status='disconnected', reconnect_needed=0,
                last_error='', last_tested_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (ch["id"],))
        conn.execute(
            "UPDATE connection_errors SET resolved=1, resolved_at=CURRENT_TIMESTAMP "
            "WHERE channel_id=? AND resolved=0", (ch["id"],)
        )
        conn.commit()
        log_activity(conn, rid, "channel_disconnected", "channel", platform,
                     f"Channel {platform} disconnected",
                     user_id=user["id"], user_name=user.get("name", ""))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/integrations/{platform}/reconnect")
async def integrations_reconnect(platform: str, user=Depends(current_user)):
    """
    Initiate reconnect:
    - For oauth2 platforms: same as oauth/start
    - For embedded_signup: returns instructions for JS to re-run the SDK
    - For bot_token: returns current token form data
    """
    adapter = get_adapter(platform)
    if not adapter:
        raise HTTPException(400, f"Platform '{platform}' غير مدعوم")

    if adapter.auth_type == "oauth2":
        return await integrations_oauth_start({"platform": platform}, user)

    if adapter.auth_type == "embedded_signup":
        return {
            "auth_type": "embedded_signup",
            "meta_app_id": META_APP_ID,
            "message": "أعد تشغيل WhatsApp Embedded Signup من خلال الزر أدناه",
        }

    raise HTTPException(400, "Use the manual form to reconnect this platform")


@app.post("/api/integrations/{platform}/toggle")
async def integrations_toggle(platform: str, data: dict, user=Depends(current_user)):
    """Enable or disable a connected channel without disconnecting it."""
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT id, connection_status FROM channels WHERE restaurant_id=? AND type=?",
            (rid, platform)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "Channel not found")
        if dict(ch)["connection_status"] not in ("connected",):
            raise HTTPException(400, "يجب ربط القناة أولاً قبل تفعيلها")
        enabled = 1 if data.get("enabled") else 0
        conn.execute("UPDATE channels SET enabled=? WHERE id=?", (enabled, ch["id"]))
        conn.commit()
        return {"ok": True, "enabled": bool(enabled)}
    finally:
        conn.close()


@app.get("/api/integrations/{platform}/errors")
async def integrations_channel_errors(platform: str, user=Depends(current_user)):
    """Return last 20 connection errors for this platform."""
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        rows = conn.execute("""
            SELECT ce.* FROM connection_errors ce
            JOIN channels ch ON ce.channel_id = ch.id
            WHERE ce.restaurant_id=? AND ce.platform=?
            ORDER BY ce.created_at DESC LIMIT 20
        """, (rid, platform)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Super Admin — Integrations Monitoring ─────────────────────────────────────

@app.get("/api/super/integrations")
async def super_integrations_list(
    page: int = 1, limit: int = 50,
    status_filter: str = "",
    platform_filter: str = "",
    admin=Depends(current_super_admin)
):
    """All restaurant channel connections with rich status for super admin."""
    conn = database.get_db()
    try:
        offset = (page - 1) * limit
        where_clauses = ["1=1"]
        params: list = []
        if status_filter:
            where_clauses.append("ch.connection_status=?")
            params.append(status_filter)
        if platform_filter:
            where_clauses.append("ch.type=?")
            params.append(platform_filter)
        where = " AND ".join(where_clauses)

        rows = conn.execute(f"""
            SELECT ch.*, r.name as restaurant_name, r.status as restaurant_status
            FROM channels ch
            JOIN restaurants r ON ch.restaurant_id = r.id
            WHERE {where}
            ORDER BY ch.connection_status DESC, ch.last_tested_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        total = conn.execute(f"""
            SELECT COUNT(*) FROM channels ch
            JOIN restaurants r ON ch.restaurant_id = r.id
            WHERE {where}
        """, params).fetchone()[0]

        result = []
        for r in rows:
            d = dict(r)
            for f in ("token", "app_secret", "webhook_secret", "refresh_token"):
                if d.get(f):
                    d[f] = "****"
            result.append(d)
        return {"total": total, "page": page, "limit": limit, "channels": result}
    finally:
        conn.close()


@app.get("/api/super/integrations/stats")
async def super_integrations_stats(admin=Depends(current_super_admin)):
    """KPI counts per platform for super admin dashboard."""
    conn = database.get_db()
    try:
        rows = conn.execute("""
            SELECT type,
                COUNT(*) as total,
                SUM(CASE WHEN connection_status='connected' THEN 1 ELSE 0 END) as connected,
                SUM(CASE WHEN connection_status='error'     THEN 1 ELSE 0 END) as error_count,
                SUM(CASE WHEN reconnect_needed=1            THEN 1 ELSE 0 END) as reconnect_needed
            FROM channels
            GROUP BY type
        """).fetchall()

        by_platform = {}
        for r in rows:
            d = dict(r)
            by_platform[d["type"]] = {
                "total":            d["total"],
                "connected":        d["connected"],
                "error_count":      d["error_count"],
                "reconnect_needed": d["reconnect_needed"],
            }

        unresolved = conn.execute(
            "SELECT COUNT(*) FROM connection_errors WHERE resolved=0"
        ).fetchone()[0]
        reconnect_total = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE reconnect_needed=1"
        ).fetchone()[0]

        return {
            "by_platform":            by_platform,
            "total_errors_unresolved": unresolved,
            "total_reconnect_needed":  reconnect_total,
        }
    finally:
        conn.close()


@app.get("/api/super/integrations/errors")
async def super_integrations_errors(admin=Depends(current_super_admin)):
    """All unresolved connection errors across all restaurants, newest first."""
    conn = database.get_db()
    try:
        rows = conn.execute("""
            SELECT ce.*, r.name as restaurant_name, ch.name as channel_name
            FROM connection_errors ce
            JOIN restaurants r ON ce.restaurant_id = r.id
            LEFT JOIN channels ch ON ce.channel_id = ch.id
            WHERE ce.resolved=0
            ORDER BY ce.created_at DESC
            LIMIT 100
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/super/integrations/{channel_id}/force-disconnect")
async def super_force_disconnect(channel_id: str, admin=Depends(current_super_admin)):
    """Super admin: force-disconnect a channel (clears token + disables)."""
    conn = database.get_db()
    try:
        ch = conn.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Channel not found")
        ch = dict(ch)
        conn.execute("""
            UPDATE channels SET
                token='', enabled=0, connection_status='disconnected',
                reconnect_needed=0, last_error='Force-disconnected by super admin',
                last_tested_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (channel_id,))
        conn.commit()
        _sa_log(conn, admin["id"], admin.get("name", ""), "force_disconnect_channel",
                "channel", channel_id,
                f"Force-disconnected {ch['type']} for restaurant {ch['restaurant_id']}",
                "super")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/super/integrations/{channel_id}/resolve-errors")
async def super_resolve_errors(channel_id: str, admin=Depends(current_super_admin)):
    """Super admin: mark all errors resolved for a channel."""
    conn = database.get_db()
    try:
        conn.execute(
            "UPDATE connection_errors SET resolved=1, resolved_at=CURRENT_TIMESTAMP "
            "WHERE channel_id=? AND resolved=0", (channel_id,)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/super/integrations/{channel_id}/errors")
async def super_channel_errors(channel_id: str, admin=Depends(current_super_admin)):
    """Super admin: error log for a specific channel."""
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM connection_errors WHERE channel_id=? ORDER BY created_at DESC LIMIT 50",
            (channel_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Staff Management ──────────────────────────────────────────────────────────

@app.get("/api/staff")
async def list_staff(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT id, name, email, role, created_at, last_login FROM users WHERE restaurant_id=? ORDER BY created_at",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/staff", status_code=201)
async def create_staff(data: StaffCreate, user=Depends(require_role("owner", "manager"))):
    if data.role == "owner" and user["role"] != "owner":
        raise HTTPException(403, "فقط المالك يمكنه إضافة مالك آخر")
    conn = database.get_db()
    _check_plan_limit(conn, user["restaurant_id"], user.get("plan", "trial"), "staff", "users")
    existing = conn.execute("SELECT id FROM users WHERE email=?", (data.email,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "البريد الإلكتروني مستخدم بالفعل")
    uid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO users (id, restaurant_id, email, password_hash, name, role)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (uid, user["restaurant_id"], data.email, _hash_password(data.password), data.name, data.role))
    log_activity(conn, user["restaurant_id"], "staff_added", "user", uid,
                 f"تمت إضافة {data.name} بدور {data.role}", user["id"], user["name"])
    conn.commit()
    row = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/staff/{uid}")
async def update_staff_role(uid: str, data: StaffUpdate, user=Depends(require_role("owner"))):
    conn = database.get_db()
    target = conn.execute(
        "SELECT * FROM users WHERE id=? AND restaurant_id=?", (uid, user["restaurant_id"])
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "المستخدم غير موجود")
    if uid == user["id"]:
        conn.close()
        raise HTTPException(400, "لا يمكنك تغيير دورك بنفسك")
    conn.execute("UPDATE users SET role=? WHERE id=?", (data.role, uid))
    log_activity(conn, user["restaurant_id"], "staff_role_changed", "user", uid,
                 f"تغيير دور {target['name']} إلى {data.role}", user["id"], user["name"])
    conn.commit()
    row = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/staff/{uid}")
async def delete_staff(uid: str, user=Depends(require_role("owner"))):
    conn = database.get_db()
    target = conn.execute(
        "SELECT * FROM users WHERE id=? AND restaurant_id=?", (uid, user["restaurant_id"])
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "المستخدم غير موجود")
    if uid == user["id"]:
        conn.close()
        raise HTTPException(400, "لا يمكنك حذف حسابك الخاص")
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    log_activity(conn, user["restaurant_id"], "staff_removed", "user", uid,
                 f"تمت إزالة {target['name']}", user["id"], user["name"])
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}


# ── Bot Config ────────────────────────────────────────────────────────────────

@app.get("/api/bot-config")
async def get_bot_config(user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM bot_config WHERE restaurant_id=?", (user["restaurant_id"],)
    ).fetchone()
    conn.close()
    if not row:
        return {
            "restaurant_id": user["restaurant_id"],
            "system_prompt": "",
            "sales_prompt": "",
            "escalation_keywords": [],
            "fallback_message": "سأحيلك لأحد موظفينا الآن، انتظر قليلاً. 🙏",
            "max_bot_turns": 15,
            "auto_handoff_enabled": True,
            "order_extraction_enabled": True,
            "memory_enabled": True,
            "escalation_threshold": 3,
        }
    d = dict(row)
    try:
        d["escalation_keywords"] = json.loads(d.get("escalation_keywords") or "[]")
    except Exception:
        d["escalation_keywords"] = []
    d["auto_handoff_enabled"] = bool(d.get("auto_handoff_enabled", 1))
    d["order_extraction_enabled"] = bool(d.get("order_extraction_enabled", 1))
    d["memory_enabled"] = bool(d.get("memory_enabled", 1))
    return d


@app.put("/api/bot-config")
async def update_bot_config(data: BotConfigUpdate, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    existing = conn.execute("SELECT id FROM bot_config WHERE restaurant_id=?", (rid,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO bot_config (id, restaurant_id) VALUES (?, ?)",
            (str(uuid.uuid4()), rid)
        )
        conn.commit()

    upd = {}
    if data.system_prompt is not None: upd["system_prompt"] = data.system_prompt
    if data.sales_prompt is not None: upd["sales_prompt"] = data.sales_prompt
    if data.escalation_keywords is not None: upd["escalation_keywords"] = json.dumps(data.escalation_keywords)
    if data.fallback_message is not None: upd["fallback_message"] = data.fallback_message
    if data.max_bot_turns is not None: upd["max_bot_turns"] = data.max_bot_turns
    if data.auto_handoff_enabled is not None: upd["auto_handoff_enabled"] = int(data.auto_handoff_enabled)
    if data.order_extraction_enabled is not None: upd["order_extraction_enabled"] = int(data.order_extraction_enabled)
    if data.memory_enabled is not None: upd["memory_enabled"] = int(data.memory_enabled)
    if data.escalation_threshold is not None: upd["escalation_threshold"] = data.escalation_threshold

    if upd:
        conn.execute(f"UPDATE bot_config SET {','.join(k+'=?' for k in upd)} WHERE restaurant_id=?",
                     list(upd.values()) + [rid])
        changed_fields = ", ".join(upd.keys())
        log_activity(conn, rid, "bot_config_updated", "bot_config", rid,
                     f"تعديل إعدادات البوت: {changed_fields}", user["id"], user.get("name", ""))
        conn.commit()

    row = conn.execute("SELECT * FROM bot_config WHERE restaurant_id=?", (rid,)).fetchone()
    conn.close()
    d = dict(row)
    try:
        d["escalation_keywords"] = json.loads(d.get("escalation_keywords") or "[]")
    except Exception:
        d["escalation_keywords"] = []
    d["auto_handoff_enabled"] = bool(d.get("auto_handoff_enabled", 1))
    d["order_extraction_enabled"] = bool(d.get("order_extraction_enabled", 1))
    d["memory_enabled"] = bool(d.get("memory_enabled", 1))
    return d


@app.get("/api/bot-config/corrections")
async def list_bot_corrections(user=Depends(current_user)):
    """List all corrections for this restaurant, newest first."""
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM bot_corrections WHERE restaurant_id=? ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/bot-config/corrections")
async def add_bot_correction(data: dict, user=Depends(current_user)):
    """Add a correction. Deduplicates by exact text — won't insert duplicates."""
    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    rid = user["restaurant_id"]
    added_by = user.get("name") or user.get("email") or ""
    conn = database.get_db()
    # Dedup: exact same text for same restaurant → reactivate if inactive
    existing = conn.execute(
        "SELECT id, is_active FROM bot_corrections WHERE restaurant_id=? AND text=?",
        (rid, text)
    ).fetchone()
    if existing:
        if not existing["is_active"]:
            conn.execute("UPDATE bot_corrections SET is_active=1, added_by=?, created_at=CURRENT_TIMESTAMP WHERE id=?",
                         (added_by, existing["id"]))
            conn.commit()
        conn.close()
        return {"ok": True, "correction_added": text, "deduped": True}
    conn.execute(
        "INSERT INTO bot_corrections (id, restaurant_id, text, added_by, is_active) VALUES (?, ?, ?, ?, 1)",
        (str(uuid.uuid4()), rid, text, added_by)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "correction_added": text}


@app.patch("/api/bot-config/corrections/{cid}")
async def toggle_bot_correction(cid: str, data: dict, user=Depends(current_user)):
    """Activate or deactivate a correction."""
    conn = database.get_db()
    row = conn.execute(
        "SELECT id FROM bot_corrections WHERE id=? AND restaurant_id=?",
        (cid, user["restaurant_id"])
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Correction not found")
    is_active = int(bool(data.get("is_active", True)))
    conn.execute("UPDATE bot_corrections SET is_active=? WHERE id=?", (is_active, cid))
    conn.commit()
    conn.close()
    return {"ok": True, "is_active": bool(is_active)}


@app.delete("/api/bot-config/corrections/{cid}")
async def delete_bot_correction(cid: str, user=Depends(current_user)):
    """Permanently delete a correction."""
    conn = database.get_db()
    conn.execute(
        "DELETE FROM bot_corrections WHERE id=? AND restaurant_id=?",
        (cid, user["restaurant_id"])
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Bot Simulate (Algorithm 1: Test → Classify → Fix → Re-test) ───────────────

@app.post("/api/bot/simulate")
async def simulate_bot(data: dict, user=Depends(current_user)):
    """
    Algorithm 1 — Test → Classify → Fix → Re-test.
    Run a scenario (list of customer messages) through the bot without
    affecting production conversations. Returns bot replies + validation flags.
    """
    messages_in = data.get("messages", [])
    scenario = data.get("scenario", "manual_test")

    if not messages_in or not isinstance(messages_in, list):
        raise HTTPException(400, "messages must be a non-empty array")
    if len(messages_in) > 20:
        raise HTTPException(400, "max 20 messages per simulation")

    rid = user["restaurant_id"]
    sim_conv_id = f"__sim_{str(uuid.uuid4())[:8]}"
    sim_customer_id = f"__simulate__{rid}"

    conn = database.get_db()
    try:
        # Upsert simulation customer (reused across runs)
        existing = conn.execute(
            "SELECT id FROM customers WHERE id=?", (sim_customer_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO customers (id, restaurant_id, name, phone, platform) VALUES (?,?,?,?,?)",
                (sim_customer_id, rid, "Simulate Test", "0000000000", "telegram")
            )
        conn.execute(
            "INSERT INTO conversations (id, restaurant_id, customer_id, mode, status) VALUES (?,?,?,?,?)",
            (sim_conv_id, rid, sim_customer_id, "bot", "open")
        )
        conn.commit()
    finally:
        conn.close()

    results = []
    try:
        for msg in messages_in:
            customer_text = str(msg).strip()
            if not customer_text:
                continue

            # Persist customer message so bot sees history
            conn = database.get_db()
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), sim_conv_id, "customer", customer_text)
            )
            conn.commit()
            conn.close()

            from services import bot as _bot
            bot_result = _bot.process_message(rid, sim_conv_id, customer_text)
            reply = bot_result.get("reply", "")

            # Persist bot reply so next turn sees context
            conn = database.get_db()
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), sim_conv_id, "bot", reply)
            )
            conn.commit()
            conn.close()

            results.append({
                "customer": customer_text,
                "bot": reply,
                "action": bot_result.get("action", "reply"),
                "has_order": bool(
                    bot_result.get("confirmed_order") or bot_result.get("extracted_order")
                ),
            })
    finally:
        # Always clean up simulation data
        conn = database.get_db()
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (sim_conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (sim_conv_id,))
        conn.commit()
        conn.close()

    return {
        "scenario": scenario,
        "turns": len(results),
        "results": results,
    }


# ── Activity Log ──────────────────────────────────────────────────────────────

@app.get("/api/activity")
async def get_activity(
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = "SELECT * FROM activity_log WHERE restaurant_id=?"
    params = [rid]
    if action:
        q += " AND action=?"; params.append(action)
    if entity_type:
        q += " AND entity_type=?"; params.append(entity_type)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit + 1, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    has_more = len(rows) > limit
    return {"items": [dict(r) for r in rows[:limit]], "has_more": has_more}


# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/api/notifications")
async def get_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = "SELECT * FROM notifications WHERE restaurant_id=?"
    params = [rid]
    if unread_only:
        q += " AND is_read=0"
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/notifications/unread-count")
async def unread_count(user=Depends(current_user)):
    conn = database.get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE restaurant_id=? AND is_read=0",
        (user["restaurant_id"],)
    ).fetchone()[0]
    conn.close()
    return {"count": count}


@app.patch("/api/notifications/{nid}/read")
async def mark_notification_read(nid: str, user=Depends(current_user)):
    conn = database.get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE id=? AND restaurant_id=?",
        (nid, user["restaurant_id"])
    )
    conn.commit()
    conn.close()
    return {"is_read": True}


@app.post("/api/notifications/read-all")
async def mark_all_read(user=Depends(current_user)):
    conn = database.get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE restaurant_id=?",
        (user["restaurant_id"],)
    )
    conn.commit()
    conn.close()
    return {"message": "تم تحديد الكل كمقروء"}


# ══════════════════════════════════════════════════════════════════════════════
# ── UNIFIED META WEBHOOK  /webhooks/meta ─────────────────────────────────────
# Single callback URL for ALL Meta platforms (FB, IG, WA).
# Configure once in Meta Developer Console → Webhooks → Callback URL.
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/webhooks/meta")
async def meta_webhook_verify(req: Request):
    """
    Meta sends a GET to verify the webhook URL.
    Paste META_VERIFY_TOKEN into Meta Developer Console → Verify Token.
    """
    p = dict(req.query_params)
    mode      = p.get("hub.mode", "")
    token     = p.get("hub.verify_token", "")
    challenge = p.get("hub.challenge", "")

    if mode == "subscribe" and token and META_VERIFY_TOKEN and token == META_VERIFY_TOKEN:
        logger.info("[webhooks/meta] verification OK")
        return PlainTextResponse(challenge)

    logger.warning(f"[webhooks/meta] verify failed — mode={mode} token_match={token==META_VERIFY_TOKEN}")
    raise HTTPException(403, "Webhook verification failed")


@app.post("/webhooks/meta")
async def meta_webhook_unified(req: Request, background_tasks: BackgroundTasks):
    """
    Unified incoming-event handler for Facebook, Instagram, and WhatsApp.
    Routes each event to the correct restaurant by looking up page_id or
    phone_number_id in the channels table.
    """
    raw_body = await req.body()

    # HMAC signature check using app-level META_APP_SECRET
    sig_header = req.headers.get("X-Hub-Signature-256", "")
    if sig_header and META_APP_SECRET:
        import hmac as _hmac, hashlib as _hashlib
        expected = "sha256=" + _hmac.new(
            META_APP_SECRET.encode(), raw_body, _hashlib.sha256
        ).hexdigest()
        if not _hmac.compare_digest(expected, sig_header):
            logger.warning("[webhooks/meta] invalid HMAC signature — rejecting")
            raise HTTPException(403, "Invalid signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    background_tasks.add_task(_route_meta_event, payload)
    return {"status": "ok"}


def _route_meta_event(payload: dict) -> None:
    """
    Route a unified Meta event to the correct restaurant webhook handler.

    Routing key:
      WhatsApp → phone_number_id per change entry
      Instagram → page_id (entry.id)
      Facebook  → page_id (entry.id)
    """
    object_type = payload.get("object", "")
    conn = database.get_db()
    try:
        if object_type == "whatsapp_business_account":
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    phone_id = (change.get("value") or {}).get("metadata", {}).get("phone_number_id", "")
                    if not phone_id:
                        continue
                    row = conn.execute(
                        "SELECT restaurant_id FROM channels WHERE phone_number_id=? AND type='whatsapp' AND enabled=1",
                        (phone_id,)
                    ).fetchone()
                    if row:
                        logger.info(f"[webhooks/meta] WA → restaurant={row['restaurant_id'][:8]}")
                        webhooks.handle_whatsapp(row["restaurant_id"], payload)

        elif object_type == "instagram":
            for entry in payload.get("entry", []):
                page_id = entry.get("id", "")
                if not page_id:
                    continue
                row = conn.execute(
                    "SELECT restaurant_id FROM channels WHERE page_id=? AND type='instagram' AND enabled=1",
                    (page_id,)
                ).fetchone()
                if row:
                    logger.info(f"[webhooks/meta] IG → restaurant={row['restaurant_id'][:8]}")
                    webhooks.handle_instagram(row["restaurant_id"], payload)

        elif object_type == "page":
            for entry in payload.get("entry", []):
                page_id = entry.get("id", "")
                if not page_id:
                    continue
                row = conn.execute(
                    "SELECT restaurant_id FROM channels WHERE page_id=? AND type='facebook' AND enabled=1",
                    (page_id,)
                ).fetchone()
                if row:
                    logger.info(f"[webhooks/meta] FB → restaurant={row['restaurant_id'][:8]}")
                    webhooks.handle_facebook(row["restaurant_id"], payload)

        else:
            logger.debug(f"[webhooks/meta] unknown object type: {object_type}")

    except Exception as exc:
        logger.error(f"[webhooks/meta] routing error: {exc}")
    finally:
        conn.close()


# ── Connect shortcuts (authenticated — used by dashboard connect buttons) ─────

@app.get("/api/debug/oauth-log")
async def debug_oauth_log():
    """Show last 10 OAuth attempts across all restaurants — public debug endpoint."""
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT platform, state, used, expires_at, pages_json, created_at, restaurant_id "
            "FROM oauth_states ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        result = []
        for r in rows:
            r = dict(r)
            pj = r.get("pages_json") or ""
            try:
                parsed = json.loads(pj) if pj else {}
                pages_count = len(parsed.get("pages", []))
                has_token   = bool(parsed.get("access_token", ""))
            except Exception:
                pages_count = -1
                has_token   = False
            result.append({
                "platform":     r["platform"],
                "used":         r["used"],
                "expires_at":   r["expires_at"],
                "created_at":   r.get("created_at", ""),
                "pages_count":  pages_count,
                "has_token":    has_token,
                "pages_json_len": len(pj),
            })
        ch_rows = conn.execute(
            "SELECT type, connection_status, last_error, reconnect_needed FROM channels "
            "WHERE type IN ('facebook','instagram','whatsapp') ORDER BY updated_at DESC LIMIT 10"
        ).fetchall()
        channels = [dict(r) for r in ch_rows]
        return {"oauth_attempts": result, "channels": channels}
    finally:
        conn.close()


@app.get("/api/debug/meta")
async def debug_meta_config():
    """
    Returns Meta integration config status (no secrets exposed).
    Use this to verify environment variables are set correctly in production.
    """
    return {
        "base_url":               BASE_URL,
        "redirect_uri":           f"{BASE_URL}/oauth/meta/callback",
        "webhook_url":            f"{BASE_URL}/webhooks/meta",
        "meta_app_id_set":        bool(META_APP_ID),
        "meta_app_id_prefix":     META_APP_ID[:6] + "…" if META_APP_ID else "",
        "meta_app_secret_set":    bool(META_APP_SECRET),
        "meta_verify_token_set":  bool(META_VERIFY_TOKEN),
        "meta_wa_config_id_set":  bool(META_WA_CONFIG_ID),
        "meta_wa_config_id_prefix": META_WA_CONFIG_ID[:6] + "…" if META_WA_CONFIG_ID else "NOT SET — WhatsApp Embedded Signup will fail",
    }


@app.post("/connect/facebook")
async def connect_facebook(user=Depends(current_user)):
    """Start Facebook OAuth flow. Returns {auth_url, state}."""
    return await integrations_oauth_start({"platform": "facebook"}, user)


@app.post("/connect/instagram")
async def connect_instagram(user=Depends(current_user)):
    """Start Instagram OAuth flow. Returns {auth_url, state}."""
    return await integrations_oauth_start({"platform": "instagram"}, user)


@app.post("/connect/whatsapp")
async def connect_whatsapp(user=Depends(current_user)):
    """Return WhatsApp Embedded Signup config. Returns {meta_app_id, config_id, auth_type}."""
    if not META_APP_ID:
        raise HTTPException(400, "META_APP_ID غير مضبوط في .env")
    if not META_WA_CONFIG_ID:
        raise HTTPException(400, "META_WA_CONFIG_ID غير مضبوط — أضفه من Meta Business Manager → Facebook Login for Business → Configuration ID")
    return {
        "auth_type":    "embedded_signup",
        "meta_app_id":  META_APP_ID,
        "config_id":    META_WA_CONFIG_ID,
        "webhook_url":  f"{BASE_URL}/webhooks/meta",
        "verify_token": META_VERIFY_TOKEN,
    }


# ── Webhooks (public, no auth) ────────────────────────────────────────────────

@app.post("/webhook/telegram/{restaurant_id}")
async def webhook_telegram(restaurant_id: str, req: Request, background_tasks: BackgroundTasks):
    update = await req.json()
    logger.info(f"[webhook] telegram POST received — restaurant={restaurant_id} update_id={update.get('update_id','?')}")
    background_tasks.add_task(webhooks.handle_telegram, restaurant_id, update)
    return {"ok": True}


@app.get("/webhook/whatsapp/{restaurant_id}")
async def verify_whatsapp(restaurant_id: str, req: Request):
    params = dict(req.query_params)
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    conn = database.get_db()
    ch = conn.execute(
        "SELECT * FROM channels WHERE restaurant_id=? AND type='whatsapp'", (restaurant_id,)
    ).fetchone()
    conn.close()

    stored_token = ch["verify_token"] if ch and "verify_token" in ch.keys() else ""
    if mode == "subscribe" and token and token == stored_token:
        # Meta requires plain text challenge response — JSON would fail verification
        return PlainTextResponse(challenge)
    logger.warning(f"[whatsapp] webhook verify failed: restaurant={restaurant_id} mode={mode}")
    raise HTTPException(403, "Verification failed")


@app.post("/webhook/whatsapp/{restaurant_id}")
async def webhook_whatsapp(restaurant_id: str, req: Request, background_tasks: BackgroundTasks):
    raw_body = await req.body()
    sig_header = req.headers.get("X-Hub-Signature-256", "")
    if sig_header:
        conn = database.get_db()
        ch = conn.execute(
            "SELECT app_secret FROM channels WHERE restaurant_id=? AND type='whatsapp'",
            (restaurant_id,)
        ).fetchone()
        conn.close()
        app_secret = (ch["app_secret"] if ch and ch["app_secret"] else "") if ch else ""
        if app_secret:
            import hmac as _hmac, hashlib as _hashlib
            expected = "sha256=" + _hmac.new(
                app_secret.encode(), raw_body, _hashlib.sha256
            ).hexdigest()
            if not _hmac.compare_digest(expected, sig_header):
                logger.warning(f"[whatsapp] invalid HMAC signature — restaurant={restaurant_id}")
                raise HTTPException(403, "Invalid signature")
    data = await req.json()
    logger.info(f"[webhook] whatsapp POST received — restaurant={restaurant_id}")
    background_tasks.add_task(webhooks.handle_whatsapp, restaurant_id, data)
    return {"status": "ok"}


@app.get("/webhook/instagram/{restaurant_id}")
async def verify_instagram(restaurant_id: str, req: Request):
    params = dict(req.query_params)
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    conn = database.get_db()
    ch = conn.execute(
        "SELECT * FROM channels WHERE restaurant_id=? AND type='instagram'", (restaurant_id,)
    ).fetchone()
    conn.close()

    stored_token = ch["verify_token"] if ch and "verify_token" in ch.keys() else ""
    if mode == "subscribe" and token and token == stored_token:
        return PlainTextResponse(challenge)
    logger.warning(f"[instagram] webhook verify failed: restaurant={restaurant_id} mode={mode}")
    raise HTTPException(403, "Verification failed")


@app.post("/webhook/instagram/{restaurant_id}")
async def webhook_instagram(restaurant_id: str, req: Request, background_tasks: BackgroundTasks):
    data = await req.json()
    logger.info(f"[webhook] instagram POST received — restaurant={restaurant_id}")
    background_tasks.add_task(webhooks.handle_instagram, restaurant_id, data)
    return {"status": "ok"}


@app.get("/webhook/facebook/{restaurant_id}")
async def verify_facebook(restaurant_id: str, req: Request):
    params = dict(req.query_params)
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    conn = database.get_db()
    ch = conn.execute(
        "SELECT * FROM channels WHERE restaurant_id=? AND type='facebook'", (restaurant_id,)
    ).fetchone()
    conn.close()

    stored_token = ch["verify_token"] if ch and "verify_token" in ch.keys() else ""
    if mode == "subscribe" and token and token == stored_token:
        return PlainTextResponse(challenge)
    logger.warning(f"[facebook] webhook verify failed: restaurant={restaurant_id} mode={mode}")
    raise HTTPException(403, "Verification failed")


@app.post("/webhook/facebook/{restaurant_id}")
async def webhook_facebook(restaurant_id: str, req: Request, background_tasks: BackgroundTasks):
    data = await req.json()
    logger.info(f"[webhook] facebook POST received — restaurant={restaurant_id}")
    background_tasks.add_task(webhooks.handle_facebook, restaurant_id, data)
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# ── SUPER ADMIN ROUTES ────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class SuperLoginReq(BaseModel):
    email: str
    password: str

class SuperSubUpdate(BaseModel):
    plan: Optional[str] = None
    status: Optional[str] = None
    price: Optional[float] = None
    end_date: Optional[str] = None
    notes: Optional[str] = None
    next_payment_date: Optional[str] = None

class SuperRestUpdate(BaseModel):
    status: Optional[str] = None        # active / suspended / expired
    internal_notes: Optional[str] = None
    plan: Optional[str] = None


class SuperRestCreate(BaseModel):
    name: str
    owner_email: str
    owner_password: str
    owner_name: str
    plan: Optional[str] = "trial"
    phone: Optional[str] = ""
    address: Optional[str] = ""


def _sa_log(conn, admin_id: str, admin_name: str, action: str,
            target_type: str = "", target_id: str = "",
            description: str = "", ip: str = ""):
    try:
        conn.execute(
            "INSERT INTO super_admin_log (id, admin_id, admin_name, action, target_type, target_id, description, ip) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), admin_id, admin_name, action, target_type, target_id, description, ip)
        )
    except Exception:
        pass


# ── Super Admin Auth ──────────────────────────────────────────────────────────

@app.post("/api/super/auth/login")
async def super_login(request: Request, data: SuperLoginReq):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate(ip, limit=5, window=60):
        raise HTTPException(429, "طلبات كثيرة — حاول بعد دقيقة")
    conn = database.get_db()
    admin = conn.execute("SELECT * FROM super_admins WHERE email=?", (data.email,)).fetchone()
    if not admin or not _verify_password(data.password, admin["password_hash"]):
        conn.close()
        raise HTTPException(401, "بيانات الدخول غير صحيحة")
    _sa_log(conn, admin["id"], admin["name"], "login", ip=ip)
    conn.commit()
    conn.close()
    token = create_token({"sub": admin["id"], "is_super": True})
    return {"token": token, "admin": {"id": admin["id"], "name": admin["name"], "email": admin["email"]}}


# ── Super Admin Dashboard KPIs ────────────────────────────────────────────────

@app.get("/api/super/dashboard")
async def super_dashboard(admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        total_rests   = conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()[0]
        active_rests  = conn.execute("SELECT COUNT(*) FROM restaurants WHERE status='active'").fetchone()[0]
        suspended     = conn.execute("SELECT COUNT(*) FROM restaurants WHERE status='suspended'").fetchone()[0]
        expired_subs  = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE status='expired'").fetchone()[0]
        expiring_soon = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE status='active' AND end_date != '' "
            "AND end_date <= date('now', '+7 days') AND end_date >= date('now')"
        ).fetchone()[0]

        mrr = conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM subscriptions WHERE status='active'"
        ).fetchone()[0]

        arr = round(mrr * 12, 2)

        total_orders  = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        total_convs   = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        active_chans  = conn.execute("SELECT COUNT(*) FROM channels WHERE enabled=1").fetchone()[0]
        total_users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

        # Recent restaurants (last 5 joined)
        recent = conn.execute("""
            SELECT r.id, r.name, r.plan, r.status, r.created_at,
                   s.status AS sub_status, s.end_date,
                   (SELECT u.name FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_name,
                   (SELECT u.email FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_email
            FROM restaurants r LEFT JOIN subscriptions s ON s.restaurant_id = r.id
            ORDER BY r.created_at DESC LIMIT 5
        """).fetchall()

        return {
            "total_restaurants": total_rests,
            "active_restaurants": active_rests,
            "suspended_restaurants": suspended,
            "expired_subscriptions": expired_subs,
            "expiring_soon": expiring_soon,
            "mrr": round(mrr, 2),
            "arr": arr,
            "total_orders": total_orders,
            "total_conversations": total_convs,
            "active_channels": active_chans,
            "total_users": total_users,
            "recent_restaurants": [dict(r) for r in recent],
        }
    finally:
        conn.close()


# ── Restaurants List ──────────────────────────────────────────────────────────

@app.get("/api/super/restaurants")
async def super_list_restaurants(
    status: Optional[str] = None,
    plan: Optional[str] = None,
    search: Optional[str] = None,
    admin=Depends(current_super_admin),
):
    conn = database.get_db()
    try:
        q = """
            SELECT r.id, r.name, r.description, r.phone, r.address, r.plan,
                   r.status, r.internal_notes, r.created_at, r.last_activity_at,
                   s.status AS sub_status, s.plan AS sub_plan, s.price,
                   s.start_date, s.end_date, s.trial_ends_at,
                   s.last_payment_date, s.next_payment_date,
                   (SELECT u.name  FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_name,
                   (SELECT u.email FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_email,
                   (SELECT u.id    FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_id,
                   (SELECT COUNT(*) FROM orders  o WHERE o.restaurant_id=r.id) AS total_orders,
                   (SELECT COUNT(*) FROM conversations cv WHERE cv.restaurant_id=r.id) AS total_conversations,
                   (SELECT COUNT(*) FROM users u2 WHERE u2.restaurant_id=r.id) AS total_staff,
                   (SELECT COUNT(*) FROM channels ch WHERE ch.restaurant_id=r.id AND ch.enabled=1) AS active_channels
            FROM restaurants r LEFT JOIN subscriptions s ON s.restaurant_id=r.id
            WHERE 1=1
        """
        params = []
        if status:
            q += " AND r.status=?"; params.append(status)
        if plan:
            q += " AND (r.plan=? OR s.plan=?)"; params += [plan, plan]
        if search:
            q += " AND (r.name LIKE ? OR owner_email LIKE ? OR owner_name LIKE ?)"
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]
        q += " ORDER BY r.created_at DESC"
        rows = conn.execute(q, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Days remaining
            end = d.get("end_date") or ""
            if end:
                try:
                    from datetime import date as _date
                    delta = (_date.fromisoformat(end) - _date.today()).days
                    d["days_remaining"] = delta
                except Exception:
                    d["days_remaining"] = None
            else:
                d["days_remaining"] = None
            result.append(d)
        return result
    finally:
        conn.close()


# ── Create Restaurant ─────────────────────────────────────────────────────────

@app.post("/api/super/restaurants", status_code=201)
async def super_create_restaurant(
    data: SuperRestCreate,
    request: Request,
    admin=Depends(current_super_admin),
):
    """Create a new restaurant with an owner account and default channels/settings."""
    conn = database.get_db()
    try:
        # Check email is not already taken
        if conn.execute("SELECT id FROM users WHERE email=?", (data.owner_email,)).fetchone():
            raise HTTPException(400, f"البريد الإلكتروني مستخدم بالفعل: {data.owner_email}")

        rid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        plan = data.plan or "trial"

        # Restaurant row
        conn.execute(
            "INSERT INTO restaurants (id, name, description, phone, address, plan, status) VALUES (?,?,?,?,?,?,'active')",
            (rid, data.name, "", data.phone or "", data.address or "", plan),
        )

        # Owner user
        pw_hash = _bcrypt.hashpw(data.owner_password.encode(), _bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (id, restaurant_id, email, password_hash, name, role) VALUES (?,?,?,?,?,?)",
            (uid, rid, data.owner_email, pw_hash, data.owner_name, "owner"),
        )

        # Default channels (empty tokens)
        for ch_type in ["telegram", "whatsapp", "instagram", "facebook"]:
            conn.execute(
                "INSERT INTO channels (id, restaurant_id, type, name, enabled, verified) VALUES (?,?,?,?,0,0)",
                (str(uuid.uuid4()), rid, ch_type, f"قناة {ch_type}"),
            )

        # Settings row
        conn.execute(
            "INSERT INTO settings (id, restaurant_id, restaurant_name, bot_enabled) VALUES (?,?,?,1)",
            (str(uuid.uuid4()), rid, data.name),
        )

        # Bot config row
        conn.execute(
            "INSERT INTO bot_config (id, restaurant_id, system_prompt, sales_prompt) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), rid,
             f"أنت مساعد ذكاء اصطناعي لـ {data.name}. ساعد العملاء بكل ود واحترافية.",
             ""),
        )

        # Subscription
        from datetime import date as _date, timedelta as _td
        today = _date.today().isoformat()
        trial_end = (_date.today() + _td(days=14)).isoformat()
        conn.execute(
            "INSERT INTO subscriptions (id, restaurant_id, plan, status, price, start_date, end_date, trial_ends_at, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), rid, plan,
             "trial" if plan == "trial" else "active",
             0.0, today,
             (_date.today() + _td(days=365)).isoformat() if plan != "trial" else trial_end,
             trial_end, "Created by super admin"),
        )

        conn.commit()
        _sa_log(conn, admin["id"], admin["name"], "restaurant_created", "restaurant", rid,
                f"إنشاء مطعم: {data.name}", request.client.host if request.client else "")
        conn.commit()
        logger.info(f"[super] restaurant created — name={data.name} email={data.owner_email} plan={plan}")
        return {
            "id": rid,
            "name": data.name,
            "owner_email": data.owner_email,
            "plan": plan,
            "message": f"✅ تم إنشاء المطعم بنجاح — يمكن الدخول الآن بـ {data.owner_email}",
        }
    finally:
        conn.close()


# ── Restaurant Details ─────────────────────────────────────────────────────────

@app.get("/api/super/restaurants/{rid}")
async def super_get_restaurant(rid: str, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        r = conn.execute("SELECT * FROM restaurants WHERE id=?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, "المطعم غير موجود")
        d = dict(r)

        sub = conn.execute("SELECT * FROM subscriptions WHERE restaurant_id=?", (rid,)).fetchone()
        d["subscription"] = dict(sub) if sub else None

        staff = conn.execute(
            "SELECT id, name, email, role, created_at, last_login FROM users WHERE restaurant_id=? ORDER BY role",
            (rid,)
        ).fetchall()
        d["staff"] = [dict(u) for u in staff]

        products_count = conn.execute("SELECT COUNT(*) FROM products WHERE restaurant_id=?", (rid,)).fetchone()[0]
        customers_count = conn.execute("SELECT COUNT(*) FROM customers WHERE restaurant_id=?", (rid,)).fetchone()[0]
        orders_count = conn.execute("SELECT COUNT(*) FROM orders WHERE restaurant_id=?", (rid,)).fetchone()[0]
        convs_count = conn.execute("SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", (rid,)).fetchone()[0]
        channels = conn.execute("SELECT type, enabled, verified, connection_status, last_error, last_tested_at FROM channels WHERE restaurant_id=?", (rid,)).fetchall()
        products_with_variants_count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE restaurant_id=? AND variants IS NOT NULL AND variants != '[]' AND variants != ''",
            (rid,)
        ).fetchone()[0]

        d["stats"] = {
            "products": products_count,
            "customers": customers_count,
            "orders": orders_count,
            "conversations": convs_count,
        }
        d["total_products"] = products_count
        d["total_orders"] = orders_count
        d["total_conversations"] = convs_count
        d["total_staff"] = len([u for u in staff])
        d["products_with_variants"] = products_with_variants_count
        d["channels"] = [dict(c) for c in channels]

        bot_cfg = conn.execute("SELECT * FROM bot_config WHERE restaurant_id=?", (rid,)).fetchone()
        d["bot_config"] = dict(bot_cfg) if bot_cfg else None

        # Recent activity
        activity = conn.execute(
            "SELECT * FROM activity_log WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 10", (rid,)
        ).fetchall()
        d["recent_activity"] = [dict(a) for a in activity]

        return d
    finally:
        conn.close()


# ── Update Restaurant (status, notes, plan) ───────────────────────────────────

@app.patch("/api/super/restaurants/{rid}")
async def super_update_restaurant(rid: str, data: SuperRestUpdate,
                                   request: Request, admin=Depends(current_super_admin)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM restaurants WHERE id=?", (rid,)).fetchone():
        conn.close()
        raise HTTPException(404, "المطعم غير موجود")
    upd = {}
    if data.status is not None: upd["status"] = data.status
    if data.internal_notes is not None: upd["internal_notes"] = data.internal_notes
    if data.plan is not None: upd["plan"] = data.plan
    if upd:
        conn.execute(
            f"UPDATE restaurants SET {','.join(k+'=?' for k in upd)} WHERE id=?",
            list(upd.values()) + [rid]
        )
    action = f"تحديث مطعم: {', '.join(upd.keys())}" if upd else "فحص مطعم"
    _sa_log(conn, admin["id"], admin["name"], action, "restaurant", rid, "",
            request.client.host if request.client else "")
    conn.commit()
    row = conn.execute("SELECT * FROM restaurants WHERE id=?", (rid,)).fetchone()
    conn.close()
    return dict(row)


# ── Subscription Management ───────────────────────────────────────────────────

@app.get("/api/super/subscriptions")
async def super_list_subscriptions(
    status: Optional[str] = None,
    admin=Depends(current_super_admin),
):
    conn = database.get_db()
    try:
        q = """
            SELECT s.*, r.name AS restaurant_name, r.status AS restaurant_status,
                   (SELECT u.name FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_name,
                   (SELECT u.email FROM users u WHERE u.restaurant_id=r.id AND u.role='owner' LIMIT 1) AS owner_email
            FROM subscriptions s JOIN restaurants r ON s.restaurant_id=r.id
        """
        params = []
        if status:
            q += " WHERE s.status=?"; params.append(status)
        q += " ORDER BY s.end_date ASC"
        rows = conn.execute(q, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            end = d.get("end_date") or ""
            if end:
                try:
                    from datetime import date as _date
                    d["days_remaining"] = (_date.fromisoformat(end) - _date.today()).days
                except Exception:
                    d["days_remaining"] = None
            else:
                d["days_remaining"] = None
            result.append(d)
        return result
    finally:
        conn.close()


@app.put("/api/super/subscriptions/{rid}")
async def super_update_subscription(rid: str, data: SuperSubUpdate,
                                     request: Request, admin=Depends(current_super_admin)):
    conn = database.get_db()
    existing = conn.execute("SELECT * FROM subscriptions WHERE restaurant_id=?", (rid,)).fetchone()
    if not existing:
        # Create subscription if not exists
        conn.execute("""
            INSERT INTO subscriptions (id, restaurant_id, plan, status, price, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), rid,
              data.plan or "basic", data.status or "trial",
              data.price or 0, data.notes or ""))
    else:
        upd = {}
        if data.plan is not None: upd["plan"] = data.plan
        if data.status is not None: upd["status"] = data.status
        if data.price is not None: upd["price"] = data.price
        if data.end_date is not None: upd["end_date"] = data.end_date
        if data.notes is not None: upd["notes"] = data.notes
        if data.next_payment_date is not None: upd["next_payment_date"] = data.next_payment_date
        upd["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if upd:
            conn.execute(
                f"UPDATE subscriptions SET {','.join(k+'=?' for k in upd)} WHERE restaurant_id=?",
                list(upd.values()) + [rid]
            )
        # Sync plan to restaurant table
        if data.plan:
            conn.execute("UPDATE restaurants SET plan=? WHERE id=?", (data.plan, rid))

    _sa_log(conn, admin["id"], admin["name"], "subscription_updated", "restaurant", rid, "",
            request.client.host if request.client else "")
    conn.commit()
    row = conn.execute("SELECT * FROM subscriptions WHERE restaurant_id=?", (rid,)).fetchone()
    conn.close()
    return dict(row)


# ── Impersonation ─────────────────────────────────────────────────────────────

@app.post("/api/super/restaurants/{rid}/impersonate")
async def impersonate(rid: str, request: Request, admin=Depends(current_super_admin)):
    conn = database.get_db()
    owner = conn.execute(
        "SELECT * FROM users WHERE restaurant_id=? AND role='owner' LIMIT 1", (rid,)
    ).fetchone()
    if not owner:
        conn.close()
        raise HTTPException(404, "لا يوجد مالك لهذا المطعم")
    # 2-hour impersonation token
    token = create_token({
        "sub": owner["id"],
        "restaurant_id": rid,
        "impersonated_by": admin["id"],
    }, hours=2)
    _sa_log(conn, admin["id"], admin["name"], "impersonation", "restaurant", rid,
            f"دخول كـ {owner['name']} ({owner['email']})",
            request.client.host if request.client else "")
    conn.commit()
    conn.close()
    return {"token": token, "owner": {"name": owner["name"], "email": owner["email"]}}


# ── Platform Analytics ────────────────────────────────────────────────────────

@app.get("/api/super/analytics")
async def super_analytics(admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        # Monthly new restaurants (last 12 months)
        monthly = []
        for i in range(11, -1, -1):
            month_start = (datetime.now().replace(day=1) - timedelta(days=i*30)).strftime("%Y-%m")
            count = conn.execute(
                "SELECT COUNT(*) FROM restaurants WHERE strftime('%Y-%m', created_at)=?",
                (month_start,)
            ).fetchone()[0]
            monthly.append({"month": month_start, "count": count})

        # Plan distribution
        plan_rows = conn.execute(
            "SELECT plan, COUNT(*) as cnt FROM subscriptions GROUP BY plan"
        ).fetchall()
        plans = [dict(r) for r in plan_rows]

        # Channel usage
        chan_rows = conn.execute(
            "SELECT type, COUNT(*) as total, SUM(enabled) as active FROM channels GROUP BY type"
        ).fetchall()
        channels = [dict(r) for r in chan_rows]

        # Top 10 restaurants by orders
        top_rests = conn.execute("""
            SELECT r.name, r.plan, COUNT(o.id) as order_count,
                   COALESCE(SUM(o.total),0) as revenue
            FROM restaurants r LEFT JOIN orders o ON o.restaurant_id=r.id
            GROUP BY r.id ORDER BY order_count DESC LIMIT 10
        """).fetchall()

        # MRR trend (last 6 months)
        mrr_trend = conn.execute(
            "SELECT COALESCE(SUM(price),0) as mrr FROM subscriptions WHERE status='active'"
        ).fetchone()

        # Total platform revenue (all orders)
        platform_revenue = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE status='delivered'"
        ).fetchone()[0]

        return {
            "monthly_new_restaurants": monthly,
            "plan_distribution": plans,
            "channel_usage": channels,
            "top_restaurants": [dict(r) for r in top_rests],
            "current_mrr": round(mrr_trend[0] if mrr_trend else 0, 2),
            "platform_total_revenue": round(platform_revenue, 2),
        }
    finally:
        conn.close()


# ── System Health ─────────────────────────────────────────────────────────────

@app.get("/api/super/system")
async def super_system(admin=Depends(current_super_admin)):
    import time as _t
    results = {}

    # DB check
    try:
        conn = database.get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        results["db"] = {"status": "ok", "type": "PostgreSQL" if database.IS_POSTGRES else "SQLite"}
    except Exception as e:
        results["db"] = {"status": "error", "error": str(e)}

    # OpenAI check
    if OPENAI_API_KEY:
        try:
            if openai_client:
                openai_client.models.list()
                results["openai"] = {"status": "ok"}
            else:
                results["openai"] = {"status": "not_configured"}
        except Exception as e:
            results["openai"] = {"status": "error", "error": str(e)[:100]}
    else:
        results["openai"] = {"status": "missing_key"}

    # Backend check
    results["backend"] = {"status": "ok", "version": "3.0.0", "base_url": BASE_URL}

    # Webhook stats (last 24h activity log entries)
    try:
        conn = database.get_db()
        webhook_count = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action IN ('bot_replied','new_message') "
            "AND created_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        error_count = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action LIKE '%error%' "
            "AND created_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        recent_activity = conn.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        # Recent super admin logs
        sa_logs = conn.execute(
            "SELECT * FROM super_admin_log ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        results["webhooks"] = {"messages_24h": webhook_count, "errors_24h": error_count}
        results["recent_activity"] = [dict(a) for a in recent_activity]
        results["super_admin_logs"] = [dict(l) for l in sa_logs]
    except Exception as e:
        results["webhooks"] = {"status": "error", "error": str(e)}

    return results


# ── Alerts ────────────────────────────────────────────────────────────────────

@app.get("/api/super/alerts")
async def super_alerts(admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        alerts = []

        # 1. Subscriptions expiring in 7 days
        expiring = conn.execute("""
            SELECT r.name, s.end_date, s.status
            FROM subscriptions s JOIN restaurants r ON s.restaurant_id=r.id
            WHERE s.status='active' AND s.end_date != ''
              AND s.end_date <= date('now', '+7 days') AND s.end_date >= date('now')
        """).fetchall()
        for row in expiring:
            alerts.append({
                "type": "expiring_soon",
                "level": "warning",
                "title": "اشتراك سينتهي قريباً",
                "message": f"مطعم {row['name']} — ينتهي {row['end_date']}",
            })

        # 2. Expired subscriptions still active
        expired = conn.execute("""
            SELECT r.name, s.end_date
            FROM subscriptions s JOIN restaurants r ON s.restaurant_id=r.id
            WHERE s.status='active' AND s.end_date != '' AND s.end_date < date('now')
        """).fetchall()
        for row in expired:
            alerts.append({
                "type": "expired",
                "level": "error",
                "title": "اشتراك منتهي",
                "message": f"مطعم {row['name']} — انتهى {row['end_date']}",
            })

        # 3. No OpenAI key
        if not OPENAI_API_KEY:
            alerts.append({
                "type": "missing_key",
                "level": "error",
                "title": "OPENAI_API_KEY مفقود",
                "message": "الذكاء الاصطناعي والنسخ الصوتي لن يعملا",
            })

        # 4. BASE_URL not configured for production
        if BASE_URL == "http://localhost:8000":
            alerts.append({
                "type": "config",
                "level": "warning",
                "title": "BASE_URL غير مضبوط",
                "message": "تسجيل Telegram webhook يتطلب BASE_URL صحيحاً",
            })

        # 5. Suspended restaurants
        suspended = conn.execute(
            "SELECT COUNT(*) FROM restaurants WHERE status='suspended'"
        ).fetchone()[0]
        if suspended > 0:
            alerts.append({
                "type": "suspended",
                "level": "info",
                "title": f"{suspended} مطاعم موقوفة",
                "message": "توجد مطاعم حالتها suspended",
            })

        # 6. Channels with errors
        chan_errors = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE connection_status='error'"
        ).fetchone()[0]
        if chan_errors > 0:
            alerts.append({
                "type": "channel_error",
                "level": "warning",
                "title": f"{chan_errors} قناة بها أخطاء",
                "message": "تحقق من إعدادات القنوات",
            })

        return {"alerts": alerts, "count": len(alerts)}
    finally:
        conn.close()


# ── Delete / Archive Restaurant ───────────────────────────────────────────────

@app.delete("/api/super/restaurants/{rid}")
async def super_delete_restaurant(rid: str, request: Request, admin=Depends(current_super_admin)):
    conn = database.get_db()
    r = conn.execute("SELECT name FROM restaurants WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "المطعم غير موجود")
    # Soft delete: set status to archived
    conn.execute("UPDATE restaurants SET status='archived' WHERE id=?", (rid,))
    _sa_log(conn, admin["id"], admin["name"], "restaurant_archived", "restaurant", rid,
            f"أرشفة مطعم: {r['name']}", request.client.host if request.client else "")
    conn.commit()
    conn.close()
    return {"message": f"تم أرشفة مطعم {r['name']}"}


# ── Super Admin: All Conversations ───────────────────────────────────────────

@app.get("/api/super/conversations")
async def super_all_conversations(
    restaurant_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin=Depends(current_super_admin),
):
    """View all bot conversations across all restaurants."""
    conn = database.get_db()
    try:
        q = """
            SELECT cv.id, cv.mode, cv.status, cv.urgent, cv.unread_count,
                   cv.created_at, cv.updated_at,
                   r.name AS restaurant_name, r.id AS restaurant_id,
                   c.name AS customer_name, c.phone AS customer_phone,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=cv.id) AS msg_count,
                   (SELECT m2.content FROM messages m2 WHERE m2.conversation_id=cv.id ORDER BY m2.created_at DESC LIMIT 1) AS last_message
            FROM conversations cv
            JOIN restaurants r ON r.id=cv.restaurant_id
            JOIN customers c ON c.id=cv.customer_id
            WHERE 1=1
        """
        params = []
        if restaurant_id:
            q += " AND cv.restaurant_id=?"; params.append(restaurant_id)
        if status:
            q += " AND cv.status=?"; params.append(status)
        q += " ORDER BY cv.updated_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(q, params).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM conversations" +
            (" WHERE restaurant_id=?" if restaurant_id else ""),
            ([restaurant_id] if restaurant_id else [])
        ).fetchone()[0]
        return {"items": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


@app.get("/api/super/conversations/{conv_id}/messages")
async def super_get_conv_messages(conv_id: str, admin=Depends(current_super_admin)):
    """Get all messages for a conversation (super admin view)."""
    conn = database.get_db()
    try:
        msgs = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conv_id,)
        ).fetchall()
        conv = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if not conv:
            raise HTTPException(404, "المحادثة غير موجودة")
        return {"conversation": dict(conv), "messages": [dict(m) for m in msgs]}
    finally:
        conn.close()


@app.patch("/api/super/conversations/{conv_id}/messages/{msg_id}")
async def super_edit_message(conv_id: str, msg_id: str, data: MsgCreate,
                              request: Request, admin=Depends(current_super_admin)):
    """Edit a bot message content (super admin only)."""
    conn = database.get_db()
    try:
        msg = conn.execute("SELECT * FROM messages WHERE id=? AND conversation_id=?",
                           (msg_id, conv_id)).fetchone()
        if not msg:
            raise HTTPException(404, "الرسالة غير موجودة")
        conn.execute("UPDATE messages SET content=? WHERE id=?", (data.content, msg_id))
        _sa_log(conn, admin["id"], admin["name"], "message_edited", "message", msg_id,
                f"تعديل رسالة في المحادثة {conv_id}", request.client.host if request.client else "")
        conn.commit()
        return {"message": "تم التعديل"}
    finally:
        conn.close()


# ── Super Admin: Reset Restaurant Data ────────────────────────────────────────

@app.post("/api/super/restaurants/{rid}/reset-data")
async def super_reset_restaurant_data(rid: str, request: Request, admin=Depends(current_super_admin)):
    """Delete all transactional data (orders, conversations, customers) for a restaurant.
    Keeps: restaurant record, users, settings, channels, products.
    """
    conn = database.get_db()
    try:
        r = conn.execute("SELECT name FROM restaurants WHERE id=?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, "المطعم غير موجود")
        # Delete messages → conversations → orders/order_items → customers
        conv_ids = [row[0] for row in conn.execute(
            "SELECT id FROM conversations WHERE restaurant_id=?", (rid,)).fetchall()]
        for cid in conv_ids:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        conn.execute("DELETE FROM conversations WHERE restaurant_id=?", (rid,))
        order_ids = [row[0] for row in conn.execute(
            "SELECT id FROM orders WHERE restaurant_id=?", (rid,)).fetchall()]
        for oid in order_ids:
            conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
        conn.execute("DELETE FROM orders WHERE restaurant_id=?", (rid,))
        conn.execute("DELETE FROM customers WHERE restaurant_id=?", (rid,))
        conn.execute("DELETE FROM activity_log WHERE restaurant_id=?", (rid,))
        conn.execute("DELETE FROM notifications WHERE restaurant_id=?", (rid,))
        conn.execute("DELETE FROM conversation_memory WHERE restaurant_id=?", (rid,))
        conn.execute("DELETE FROM menu_import_sessions WHERE restaurant_id=?", (rid,))
        _sa_log(conn, admin["id"], admin["name"], "data_reset", "restaurant", rid,
                f"حذف جميع البيانات التجريبية لمطعم: {r['name']}",
                request.client.host if request.client else "")
        conn.commit()
        return {"message": f"تم حذف جميع البيانات التجريبية لمطعم {r['name']}"}
    finally:
        conn.close()


# ── Super Admin: Support PIN ──────────────────────────────────────────────────

class SuperPINUpdate(BaseModel):
    support_pin: str

@app.patch("/api/super/auth/pin")
async def super_update_pin(data: SuperPINUpdate, admin=Depends(current_super_admin)):
    """Set or update support PIN for the super admin account."""
    if len(data.support_pin) < 4:
        raise HTTPException(400, "رمز الدعم يجب أن يكون 4 أرقام على الأقل")
    conn = database.get_db()
    conn.execute("UPDATE super_admins SET support_pin=? WHERE id=?",
                 (data.support_pin, admin["id"]))
    conn.commit()
    conn.close()
    return {"message": "تم تحديث رمز الدعم"}

@app.get("/api/super/auth/me")
async def super_get_me(admin=Depends(current_super_admin)):
    """Return current super admin profile (without password hash)."""
    return {"id": admin["id"], "name": admin["name"], "email": admin["email"],
            "support_pin": admin.get("support_pin", ""), "created_at": admin.get("created_at", "")}


# ── Platform Config (public) ─────────────────────────────────────────────────

@app.get("/api/platform/config")
async def get_platform_config():
    """Public endpoint — returns non-sensitive platform settings."""
    conn = database.get_db()
    try:
        rows = conn.execute("SELECT key, value FROM platform_config").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


@app.patch("/api/super/platform-config")
async def update_platform_config(data: dict, admin=Depends(current_super_admin)):
    """Super admin only — update platform config values."""
    conn = database.get_db()
    try:
        for key, value in data.items():
            conn.execute(
                "INSERT INTO platform_config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
        conn.commit()
        return {"message": "تم التحديث"}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# ── MENU IMPORT (Smart Upload) ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_MENU_IMPORT_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "menu_imports")
os.makedirs(_MENU_IMPORT_UPLOAD_DIR, exist_ok=True)

_ALLOWED_MENU_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".pdf", ".docx", ".txt", ".csv", ".xlsx", ".xls"
}
_MAX_FILE_SIZE_MB = 20


class ImportApproveItem(BaseModel):
    """One item from the preview that the user has reviewed."""
    temp_id:     str
    action:      str          # create | skip | update_existing
    name:        Optional[str]  = None
    category:    Optional[str]  = None
    price:       Optional[float] = None
    description: Optional[str]  = None
    variants:    Optional[list]  = None
    image_url:   Optional[str]  = None


class ImportApproveReq(BaseModel):
    items: List[ImportApproveItem]


def _run_menu_parse(session_id: str, restaurant_id: str,
                    file_paths: List[str], file_names: List[str]) -> None:
    """Background task: upload menu files to Supabase, parse them, store results in DB."""
    from services import storage as _storage

    conn = database.get_db()
    try:
        # Mark as parsing
        conn.execute(
            "UPDATE menu_import_sessions SET status='parsing' WHERE id=?", (session_id,)
        )
        conn.commit()

        # ── Upload files to Supabase Storage (backend-only, service role) ──────
        file_urls: List[str] = []
        for local_path, fname in zip(file_paths, file_names):
            storage_path = _storage.menu_file_path(restaurant_id, session_id, fname)
            try:
                pub_url = _storage.upload_file(
                    local_path,
                    str(_storage.BUCKET_MENUS),
                    storage_path,
                )
                if pub_url:
                    file_urls.append(pub_url)
                    logger.info(f"Menu file uploaded to Supabase: {pub_url}")
                else:
                    logger.debug(f"Supabase not configured — file kept local: {fname}")
            except Exception as upload_err:
                logger.warning(f"Supabase upload skipped for {fname}: {upload_err}")
                # Non-fatal — parsing continues from local temp file

        # Save Supabase URLs immediately (even if partial)
        if file_urls:
            conn.execute(
                "UPDATE menu_import_sessions SET file_urls=? WHERE id=?",
                (json.dumps(file_urls), session_id)
            )
            conn.commit()

        # ── Parse files with OpenAI ───────────────────────────────────────────
        items = _menu_parser.parse_files(file_paths, file_names, session_id)

        # ── Detect duplicates against existing products ───────────────────────
        items = _menu_parser.detect_duplicates(items, restaurant_id)

        # ── Store results ─────────────────────────────────────────────────────
        conn.execute("""
            UPDATE menu_import_sessions
            SET status='ready', raw_items=?, total_extracted=?,
                file_urls=?, completed_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (json.dumps(items), len(items), json.dumps(file_urls), session_id))
        conn.commit()
        logger.info(f"menu_import session={session_id} ready: {len(items)} items, "
                    f"{len(file_urls)} files in Supabase")

    except Exception as exc:
        logger.error(f"menu_import parse error session={session_id}: {exc}")
        conn.execute(
            "UPDATE menu_import_sessions SET status='failed', error=? WHERE id=?",
            (str(exc)[:500], session_id)
        )
        conn.commit()
    finally:
        conn.close()
        # Clean up local temp files (Supabase is the permanent store)
        for p in file_paths:
            try:
                os.unlink(p)
            except Exception:
                pass
        try:
            session_dir = os.path.join(_MENU_IMPORT_UPLOAD_DIR, session_id)
            os.rmdir(session_dir)
        except Exception:
            pass


@app.post("/api/menu-import/upload", status_code=201)
async def menu_import_upload(
    request:          Request,
    background_tasks: BackgroundTasks,
    files:            List[UploadFile] = File(...),
    user=Depends(require_role("owner", "manager")),
):
    """
    Upload one or more menu files (images / PDF / Word / Excel / CSV).
    Returns a session_id to poll for parsing status.
    """
    if not files:
        raise HTTPException(400, "لم يتم رفع أي ملف")

    if not OPENAI_API_KEY:
        raise HTTPException(503, "OPENAI_API_KEY غير مضبوط — الميزة غير متاحة")

    session_id  = str(uuid.uuid4())
    session_dir = os.path.join(_MENU_IMPORT_UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    saved_paths: List[str] = []
    saved_names: List[str] = []

    for f in files:
        fname = f.filename or "menu"
        ext   = Path(fname).suffix.lower()
        if ext not in _ALLOWED_MENU_EXTS:
            raise HTTPException(
                400,
                f"نوع الملف غير مدعوم: {ext}. المدعوم: صور، PDF، Word، Excel، CSV"
            )

        content = await f.read()
        size_mb = len(content) / (1024 * 1024)
        if size_mb > _MAX_FILE_SIZE_MB:
            raise HTTPException(400, f"حجم الملف {fname} كبير جداً (الحد {_MAX_FILE_SIZE_MB} MB)")

        dest = os.path.join(session_dir, f"{uuid.uuid4()}{ext}")
        with open(dest, "wb") as out:
            out.write(content)
        saved_paths.append(dest)
        saved_names.append(fname)

    # Create session record
    conn = database.get_db()
    conn.execute("""
        INSERT INTO menu_import_sessions
          (id, restaurant_id, status, file_names, file_count)
        VALUES (?, ?, 'pending', ?, ?)
    """, (session_id, user["restaurant_id"], json.dumps(saved_names), len(saved_names)))
    conn.commit()
    conn.close()

    # Start background parsing
    background_tasks.add_task(
        _run_menu_parse, session_id, user["restaurant_id"], saved_paths, saved_names
    )

    return {
        "session_id": session_id,
        "file_count": len(saved_names),
        "file_names": saved_names,
        "status":     "pending",
    }


@app.get("/api/menu-import/{session_id}/status")
async def menu_import_status(session_id: str, user=Depends(current_user)):
    """Poll parsing status: pending → parsing → ready | failed."""
    conn = database.get_db()
    row  = conn.execute(
        "SELECT * FROM menu_import_sessions WHERE id=? AND restaurant_id=?",
        (session_id, user["restaurant_id"])
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "جلسة الاستيراد غير موجودة")
    r = dict(row)
    return {
        "session_id":       r["id"],
        "status":           r["status"],
        "file_count":       r["file_count"],
        "total_extracted":  r["total_extracted"],
        "approved_count":   r["approved_count"],
        "error":            r["error"],
        "completed_at":     r["completed_at"],
    }


@app.get("/api/menu-import/{session_id}/preview")
async def menu_import_preview(session_id: str, user=Depends(current_user)):
    """Return all extracted items for user review."""
    conn = database.get_db()
    row  = conn.execute(
        "SELECT * FROM menu_import_sessions WHERE id=? AND restaurant_id=?",
        (session_id, user["restaurant_id"])
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "جلسة الاستيراد غير موجودة")
    r = dict(row)
    if r["status"] not in ("ready", "approved"):
        raise HTTPException(400, f"الجلسة ليست جاهزة للمراجعة (الحالة: {r['status']})")
    items = json.loads(r["raw_items"] or "[]")
    return {
        "session_id":      r["id"],
        "status":          r["status"],
        "total_extracted": r["total_extracted"],
        "file_names":      json.loads(r["file_names"] or "[]"),
        "items":           items,
    }


@app.post("/api/menu-import/{session_id}/approve")
async def menu_import_approve(
    session_id: str,
    data: ImportApproveReq,
    user=Depends(require_role("owner", "manager")),
):
    """
    Approve the reviewed items. Creates / updates products in the DB.
    Handles duplicate detection: create | skip | update_existing.
    """
    conn = database.get_db()
    row  = conn.execute(
        "SELECT * FROM menu_import_sessions WHERE id=? AND restaurant_id=?",
        (session_id, user["restaurant_id"])
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "جلسة الاستيراد غير موجودة")

    r = dict(row)
    if r["status"] not in ("ready", "approved"):
        conn.close()
        raise HTTPException(400, "يجب أن تكون الجلسة في حالة ready")

    plan = user.get("plan", "trial")
    created = updated = skipped = 0
    errors  = []

    for item in data.items:
        if item.action == "skip":
            skipped += 1
            continue

        name        = (item.name        or "").strip()
        category    = (item.category    or "عام").strip()
        price       = item.price
        description = (item.description or "").strip()
        variants    = json.dumps(item.variants or [])
        image_url   = (item.image_url   or "").strip()

        if not name:
            skipped += 1
            continue

        try:
            if item.action == "update_existing" and item.temp_id:
                # Find the existing product by temp_id lookup via existing_match in session
                session_items = json.loads(r["raw_items"] or "[]")
                existing_id = None
                for si in session_items:
                    if si.get("temp_id") == item.temp_id and si.get("existing_match"):
                        existing_id = si["existing_match"]["id"]
                        break
                if existing_id:
                    conn.execute("""
                        UPDATE products
                        SET name=?, category=?, price=?, description=?,
                            variants=?, image_url=?, import_batch_id=?
                        WHERE id=? AND restaurant_id=?
                    """, (name, category, price, description, variants,
                          image_url, session_id, existing_id, user["restaurant_id"]))
                    updated += 1
                    continue

            # Check plan limit before creating
            limit = _plan_limit(plan, "products")
            current_count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE restaurant_id=?",
                (user["restaurant_id"],)
            ).fetchone()[0]
            if current_count >= limit:
                errors.append(f"وصلت حد الخطة ({limit} منتج) — تم إيقاف الاستيراد عند {created} منتج جديد")
                skipped += (len(data.items) - created - updated - skipped)
                break

            pid = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO products
                  (id, restaurant_id, name, price, category, description,
                   icon, variants, available, image_url, gallery_images,
                   import_batch_id, confidence)
                VALUES (?, ?, ?, ?, ?, ?, '🍽️', ?, 1, ?, '[]', ?, 1.0)
            """, (pid, user["restaurant_id"], name, price, category,
                  description, variants, image_url, session_id))
            created += 1

        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:100]}")
            skipped += 1

    conn.execute("""
        UPDATE menu_import_sessions
        SET status='approved', approved_count=?, skipped_count=?
        WHERE id=?
    """, (created + updated, skipped, session_id))
    conn.commit()
    log_activity(conn, user["restaurant_id"], "menu_imported", "product", session_id,
                 f"تم استيراد {created} منتج جديد، {updated} محدّث، {skipped} متجاوز",
                 user["id"], user["name"])
    conn.commit()
    conn.close()

    return {
        "created":  created,
        "updated":  updated,
        "skipped":  skipped,
        "errors":   errors,
        "session_id": session_id,
    }


@app.get("/api/menu-import/history")
async def menu_import_history(user=Depends(current_user)):
    """Return the last 10 import sessions for this restaurant."""
    conn = database.get_db()
    rows = conn.execute("""
        SELECT id, status, file_names, file_count, total_extracted,
               approved_count, skipped_count, created_at, completed_at, error
        FROM menu_import_sessions
        WHERE restaurant_id=?
        ORDER BY created_at DESC LIMIT 10
    """, (user["restaurant_id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Static files ──────────────────────────────────────────────────────────────

if os.path.exists("public"):
    app.mount("/static", StaticFiles(directory="public"), name="static")


@app.get("/")
async def root():
    return FileResponse("public/login.html")


@app.get("/app")
async def app_page():
    return FileResponse("public/app.html")


@app.get("/register")
@app.get("/register.html")
async def register_page():
    return FileResponse("public/register.html")


@app.get("/super/login")
async def super_login_page():
    return FileResponse("public/super_login.html")


@app.get("/super")
async def super_admin_page():
    return FileResponse("public/super.html")


@app.get("/menu/{restaurant_id}")
async def public_menu_page(restaurant_id: str):
    return FileResponse("public/menu.html")


@app.get("/privacy")
async def privacy_policy():
    return FileResponse("public/privacy.html")


@app.get("/api/public/menu/{restaurant_id}")
async def public_menu_data(restaurant_id: str):
    """Public menu endpoint — no authentication required."""
    conn = database.get_db()
    try:
        rest = conn.execute(
            "SELECT name, description, address, phone FROM restaurants WHERE id=?",
            (restaurant_id,)
        ).fetchone()
        if not rest:
            raise HTTPException(404, "المطعم غير موجود")
        settings_row = conn.execute(
            "SELECT business_type, restaurant_name, restaurant_description, restaurant_phone, restaurant_address FROM settings WHERE restaurant_id=?",
            (restaurant_id,)
        ).fetchone()
        s = dict(settings_row) if settings_row else {}

        products = conn.execute(
            "SELECT name, price, category, description, icon, available, sold_out_date, variants FROM products WHERE restaurant_id=? AND available=1 ORDER BY category, name",
            (restaurant_id,)
        ).fetchall()
    finally:
        conn.close()

    from datetime import date as _date
    today = str(_date.today())
    items = []
    for p in products:
        sold_out = (p["sold_out_date"] or "") == today
        items.append({
            "name": p["name"],
            "price": p["price"],
            "category": p["category"],
            "description": p["description"] or "",
            "icon": p["icon"] or "🍽️",
            "sold_out": sold_out,
            "variants": json.loads(p["variants"] or "[]"),
        })

    return {
        "restaurant_name": s.get("restaurant_name") or dict(rest).get("name", ""),
        "restaurant_description": s.get("restaurant_description") or dict(rest).get("description", ""),
        "restaurant_phone": s.get("restaurant_phone") or dict(rest).get("phone", ""),
        "restaurant_address": s.get("restaurant_address") or dict(rest).get("address", ""),
        "business_type": s.get("business_type", "restaurant"),
        "products": items,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
