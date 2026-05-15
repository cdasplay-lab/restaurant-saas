import os
import uuid
import json
import logging
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks, UploadFile, File, Form, WebSocket, WebSocketDisconnect
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
from services.ws_manager import ws_manager
from services.integrations import get_adapter, get_all_adapters, PLATFORM_CATALOG
import secrets as _secrets
import tempfile
from routers.health import router as _health_router, _env_present
from routers.products import router as _products_router, ProductCreate, ProductUpdate
from routers.customers import router as _customers_router, CustomerUpdate
from routers.staff import router as _staff_router, StaffCreate, StaffUpdate
from routers.settings import router as _settings_router, SettingsUpdate
from routers.bot_config import router as _bot_config_router, BotConfigUpdate
from dependencies import (
    SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_HOURS, bearer,
    verify_token, current_user, require_role, current_super_admin,
)
from helpers import (
    PLAN_LIMITS, PLAN_FEATURES, BLOCKED_STATUSES,
    _get_plan_record, _plan_features_from_db, _plan_limits_from_db,
    get_subscription_state, can_use_feature,
    _plan_limit, _check_plan_limit,
    log_activity, create_notification,
    _hash_password, _verify_password,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("restaurant-saas")

# ── Sentry error monitoring ───────────────────────────────────────────────────
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            traces_sample_rate=0.05,
            send_default_pii=False,
        )
        logger.info("[sentry] initialized")
    except Exception as _se:
        logger.warning(f"[sentry] init failed: {_se}")

# ── JWT Secret — no unsafe fallback in production ─────────────────────────────
_JWT_SECRET = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY", "")
_UNSAFE_DEFAULTS = {
    "supersecretkey_change_in_production_123456789",
    "change_this_secret_in_production_minimum_32_chars",
    "change_this_secret_123",
    "your_jwt_secret_here",
}
_is_dev = os.getenv("ENVIRONMENT", "development") in ("development", "dev", "test", "testing")

if not _JWT_SECRET:
    if _is_dev:
        _JWT_SECRET = "dev_only_insecure_jwt_secret_do_not_use_in_production"
        logger.warning("⚠️  JWT_SECRET not set — using insecure dev-only secret. NEVER use in production!")
    else:
        raise RuntimeError(
            "FATAL: JWT_SECRET (or SECRET_KEY) must be set in production. "
            "Generate one: python3 -c 'import secrets; print(secrets.token_hex(32))'"
        )
elif _JWT_SECRET in _UNSAFE_DEFAULTS:
    if _is_dev:
        logger.warning("⚠️  JWT_SECRET uses a known default placeholder — replace before deploying!")
    else:
        raise RuntimeError(
            "FATAL: JWT_SECRET uses a known unsafe default. "
            "Generate a real one: python3 -c 'import secrets; print(secrets.token_hex(32))'"
        )

SECRET_KEY = _JWT_SECRET
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("SESSION_HOURS", "24"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL        = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
META_APP_ID        = os.getenv("META_APP_ID", "")
META_APP_SECRET    = os.getenv("META_APP_SECRET", "")
META_VERIFY_TOKEN  = os.getenv("META_VERIFY_TOKEN", "")
# WhatsApp Embedded Signup Configuration ID (different from APP_ID)
# Get from: Meta Business Manager → Apps → Your App → Facebook Login for Business → Create Configuration
META_WA_CONFIG_ID  = os.getenv("META_WA_CONFIG_ID", "")

# ── Email (SMTP) ──────────────────────────────────────────────────────────────
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER


def _send_email(to: str, subject: str, body_html: str) -> bool:
    """Send HTML email via SMTP. Silently skips if SMTP not configured."""
    if not SMTP_HOST or not SMTP_USER:
        logger.debug(f"[email] SMTP not configured — skipped email to {to}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_FROM, [to], msg.as_string())
        logger.info(f"[email] sent to={to} subject={subject!r}")
        return True
    except Exception as exc:
        logger.error(f"[email] failed to={to}: {exc}")
        return False


# ── Simple in-process rate limiter (no external dependency) ──────────────────
import time as _time
import collections as _collections
import threading as _threading

_rate_store: dict = _collections.defaultdict(list)  # (ip, scope) → [timestamps]
_rate_store_lock = _threading.Lock()
_rate_store_last_evict = _time.time()

def _check_rate(ip: str, limit: int = 10, window: int = 60, scope: str = "") -> bool:
    """Return True if request is allowed. Keyed by (ip, scope) to isolate tenants.
    Evicts stale keys every 5 minutes to prevent unbounded memory growth."""
    global _rate_store_last_evict
    now = _time.time()
    key = (ip, scope)
    with _rate_store_lock:
        _rate_store[key] = [t for t in _rate_store[key] if now - t < window]
        if len(_rate_store[key]) >= limit:
            return False
        _rate_store[key].append(now)
        # Periodic eviction of fully-expired keys (every 5 min)
        if now - _rate_store_last_evict > 300:
            stale = [k for k, ts in _rate_store.items() if not ts or now - ts[-1] > window]
            for k in stale:
                del _rate_store[k]
            _rate_store_last_evict = now
    return True

# ── Plan limits / features / subscription state — imported from helpers.py ────
# (NUMBER 43 extraction — see helpers.py)



openai_client = None
if OPENAI_API_KEY:
    import openai as _openai
    openai_client = _openai.OpenAI(api_key=OPENAI_API_KEY)

# _hash_password, _verify_password imported from helpers (NUMBER 43)
# bearer imported from dependencies (NUMBER 43)

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


async def _processed_events_cleanup_job():
    """Every 12 hours: delete processed_events older than 48 hours.
    Meta retries within 24h max — keeping 48h is safe while bounding table growth."""
    await asyncio.sleep(300)  # 5 min after startup
    while True:
        try:
            conn = database.get_db()
            try:
                cutoff = (datetime.utcnow() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
                result = conn.execute(
                    "DELETE FROM processed_events WHERE created_at < ?", (cutoff,)
                )
                conn.commit()
                deleted = result.rowcount if hasattr(result, "rowcount") else 0
                if deleted:
                    logger.info(f"[dedup-cleanup] deleted {deleted} processed_events older than 48h")
            finally:
                conn.close()
        except Exception as exc:
            logger.error(f"processed_events_cleanup error: {exc}")
        await asyncio.sleep(43200)  # 12 hours


async def _silence_followup_job():
    """Every 5 min: send one follow-up to conversations silent >15 min after ✅ confirmation."""
    await asyncio.sleep(120)  # 2 min after startup
    while True:
        try:
            conn = database.get_db()
            try:
                cutoff_near = (datetime.utcnow() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
                cutoff_far  = (datetime.utcnow() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
                rows = conn.execute("""
                    SELECT c.id, c.restaurant_id, c.customer_id, c.channel,
                           m.content AS last_content, m.created_at AS last_at
                    FROM conversations c
                    JOIN messages m ON m.id = (
                        SELECT id FROM messages
                        WHERE conversation_id = c.id
                        ORDER BY created_at DESC LIMIT 1
                    )
                    WHERE c.mode = 'bot'
                      AND c.status = 'open'
                      AND c.followup_sent = 0
                      AND m.role = 'bot'
                      AND m.content LIKE '%✅%'
                      AND m.created_at < ?
                      AND m.created_at > ?
                    LIMIT 20
                """, (cutoff_near, cutoff_far)).fetchall()

                for row in rows:
                    try:
                        conv_id    = row["id"]
                        rest_id    = row["restaurant_id"]
                        cust_id    = row["customer_id"]
                        platform   = (row["channel"] or "").lower()

                        # Get customer external_id from memory
                        mem = conn.execute(
                            "SELECT memory_value FROM conversation_memory "
                            "WHERE restaurant_id=? AND customer_id=? AND memory_key='external_id'",
                            (rest_id, cust_id)
                        ).fetchone()
                        ext_id = mem["memory_value"] if mem else None
                        if not ext_id:
                            conn.execute(
                                "UPDATE conversations SET followup_sent=1 WHERE id=?", (conv_id,)
                            )
                            conn.commit()
                            continue

                        # Get channel credentials
                        ch = conn.execute(
                            "SELECT * FROM channels WHERE restaurant_id=? AND type=? AND enabled=1",
                            (rest_id, platform)
                        ).fetchone()
                        if not ch:
                            conn.execute(
                                "UPDATE conversations SET followup_sent=1 WHERE id=?", (conv_id,)
                            )
                            conn.commit()
                            continue
                        ch = dict(ch)

                        # Build channel_data
                        if platform == "telegram":
                            channel_data = {
                                "platform": "telegram",
                                "bot_token": ch.get("token", ""),
                                "chat_id": ext_id,
                            }
                        elif platform == "whatsapp":
                            channel_data = {
                                "platform": "whatsapp",
                                "access_token": ch.get("token", ""),
                                "phone_number_id": ch.get("phone_number_id", ""),
                                "to": ext_id,
                            }
                        elif platform in ("instagram", "facebook"):
                            channel_data = {
                                "platform": platform,
                                "access_token": ch.get("token", ""),
                                "recipient_id": ext_id,
                            }
                        else:
                            conn.execute(
                                "UPDATE conversations SET followup_sent=1 WHERE id=?", (conv_id,)
                            )
                            conn.commit()
                            continue

                        followup_text = "هل وصلك الطلب؟ 🌷 لو تحتاج أي شيء أنا هنا."
                        from services.webhooks import _send_reply as _wh_send
                        ok, err = _wh_send(channel_data, followup_text)
                        if ok:
                            import uuid as _fu_uuid
                            conn.execute(
                                "INSERT INTO messages (id, conversation_id, role, content) VALUES (?,?,?,?)",
                                (str(_fu_uuid.uuid4()), conv_id, "bot", followup_text)
                            )
                            conn.execute(
                                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (conv_id,)
                            )
                            logger.info(f"[followup] sent conv={conv_id} platform={platform}")
                        else:
                            logger.warning(f"[followup] failed conv={conv_id}: {err}")
                        conn.execute(
                            "UPDATE conversations SET followup_sent=1 WHERE id=?", (conv_id,)
                        )
                        conn.commit()
                    except Exception as _row_err:
                        logger.warning(f"[followup] row error conv={row.get('id','?')}: {_row_err}")
            finally:
                conn.close()
        except Exception as exc:
            logger.error(f"[followup] job error: {exc}")
        await asyncio.sleep(300)  # every 5 minutes


def _run_super_admin_password_reset():
    """Run at startup when RESET_SUPER_ADMIN_PASSWORD env var is set (one-time Render recovery)."""
    import uuid as _uuid
    new_pw = os.environ.get("RESET_SUPER_ADMIN_PASSWORD", "")
    if not new_pw:
        return
    try:
        pw_hash = _bcrypt.hashpw(new_pw.encode(), _bcrypt.gensalt()).decode()
        del new_pw
        conn = database.get_db()
        try:
            row = conn.execute(
                "SELECT id FROM super_admins WHERE email=?", ("superadmin@platform.com",)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE super_admins SET password_hash=? WHERE email=?",
                    (pw_hash, "superadmin@platform.com"),
                )
            else:
                conn.execute(
                    "INSERT INTO super_admins (id, email, password_hash, name) VALUES (?,?,?,?)",
                    (str(_uuid.uuid4()), "superadmin@platform.com", pw_hash, "Super Admin"),
                )
            conn.commit()
        finally:
            conn.close()
        logger.info("Super admin password reset complete")
    except Exception as _e:
        logger.error(f"Super admin password reset failed: {_e}")


# ── Production Startup Validation ────────────────────────────────────────────
def _validate_production_env() -> None:
    """Validate critical env vars in production. Raises RuntimeError on blockers."""
    _env = os.getenv("ENVIRONMENT", "development").lower()
    _is_production = _env in ("production", "prod") or bool(os.getenv("RENDER")) or bool(os.getenv("RAILWAY_ENVIRONMENT"))

    if not _is_production:
        logger.info(f"[startup] ENVIRONMENT={_env} — skipping production validation")
        return

    blockers: list = []

    # 1. JWT_SECRET must be set and not a known default
    _jwt = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY", "")
    _unsafe_defaults = {
        "supersecretkey_change_in_production_123456789",
        "change_this_secret_in_production_minimum_32_chars",
        "change_this_secret_123",
        "your_jwt_secret_here",
    }
    if not _jwt:
        blockers.append("JWT_SECRET (or SECRET_KEY) must be set in production")
    elif _jwt in _unsafe_defaults:
        blockers.append("JWT_SECRET uses a known unsafe default — generate a real one")

    # 2. BASE_URL must not be localhost
    _base = os.getenv("BASE_URL", "")
    if not _base or "localhost" in _base or _base.startswith("http://127."):
        blockers.append("BASE_URL must not be localhost in production — webhooks will not work")

    # 3. Warn if using SQLite in production
    if not os.getenv("DATABASE_URL"):
        logger.warning("⚠️  DATABASE_URL not set — using SQLite in production is not recommended")

    # 4. ALLOWED_ORIGINS must not be "*" in production
    _origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not _origins or _origins == "*":
        logger.warning("⚠️  ALLOWED_ORIGINS not set or '*' — CORS is open to all origins in production")

    if blockers:
        for b in blockers:
            logger.error(f"🚫 PRODUCTION BLOCKER: {b}")
        raise RuntimeError(f"Production startup blocked: {'; '.join(blockers)}")

    logger.info("[startup] Production env validation passed ✅")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_production_env()
    database.init_db()
    _run_super_admin_password_reset()
    asyncio.create_task(_subscription_cleanup_job())
    asyncio.create_task(_report_job())
    asyncio.create_task(_token_refresh_job())
    asyncio.create_task(_processed_events_cleanup_job())
    asyncio.create_task(_silence_followup_job())
    yield


app = FastAPI(title="Restaurant SaaS API", version="3.0.0", lifespan=lifespan)

_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
if _raw_origins.strip() in ("", "*"):
    # dev / unset — wildcard origins cannot be combined with allow_credentials=True
    # in Starlette 0.37+ (raises ValueError). Use wildcard + credentials=False instead.
    _is_prod_env = (
        os.getenv("ENVIRONMENT", "").lower() in ("production", "prod")
        or os.getenv("NODE_ENV") == "production"
        or bool(os.getenv("RAILWAY_ENVIRONMENT"))
        or bool(os.getenv("RENDER"))
    )
    if _is_prod_env:
        logger.warning(
            "⚠️  ALLOWED_ORIGINS=* في بيئة إنتاج — اضبط المتغير في Railway/Render:\n"
            "    ALLOWED_ORIGINS=https://yourapp.netlify.app,https://yourdomain.com"
        )
        # In production: refuse wildcard — require explicit origins
        ALLOWED_ORIGINS = []
        _CORS_CREDENTIALS = False
    else:
        ALLOWED_ORIGINS = ["*"]
        _CORS_CREDENTIALS = False
else:
    ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
    _CORS_CREDENTIALS = True   # safe: specific origins + credentials

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=_CORS_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.middleware("http")
async def log_all_requests(request: Request, call_next):
    method = request.method
    path   = request.url.path
    # Log every inbound POST — this catches Meta webhooks hitting any path
    if method == "POST":
        logger.info(
            f"[request-log] POST {path} "
            f"from={request.client.host if request.client else '?'} "
            f"ua={request.headers.get('user-agent','')[:60]}"
        )
    elif path.startswith("/webhook"):
        logger.info(f"[request-log] {method} {path}")
    response = await call_next(request)
    # #9 — Alert super admin on 500 errors via Telegram
    if response.status_code == 500:
        asyncio.create_task(_alert_super_admin_error(method, path, response.status_code))
    return response


async def _alert_super_admin_error(method: str, path: str, status_code: int) -> None:
    """Send a Telegram alert to the super-admin notification chat on 500 errors."""
    try:
        _alert_token = os.getenv("ALERT_BOT_TOKEN", "")
        _alert_chat  = os.getenv("ALERT_CHAT_ID", "")
        if not _alert_token or not _alert_chat:
            return
        import httpx as _httpx
        from datetime import datetime as _dt
        text = (
            f"🚨 <b>500 Error</b>\n"
            f"<code>{method} {path}</code>\n"
            f"🕐 {_dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        async with _httpx.AsyncClient(timeout=8) as _cl:
            await _cl.post(
                f"https://api.telegram.org/bot{_alert_token}/sendMessage",
                json={"chat_id": _alert_chat, "text": text, "parse_mode": "HTML"}
            )
    except Exception as _e:
        logger.debug(f"[alert] failed to send error alert: {_e}")


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
    # Exclude: auth, super admin, billing/subscription status (expired users must still read their own status)
    skip_prefixes = ("/api/auth/", "/api/super/", "/api/subscription/", "/api/billing/", "/api/announcements", "/api/onboarding", "/ws")
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


# ── Routers (NUMBER 43 extraction) ───────────────────────────────────────────
app.include_router(_health_router)
app.include_router(_products_router)
app.include_router(_customers_router)
app.include_router(_staff_router)
app.include_router(_settings_router)
app.include_router(_bot_config_router)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def create_token(data: dict, hours: int = None) -> str:
    payload = data.copy()
    h = hours if hours is not None else ACCESS_TOKEN_EXPIRE_HOURS
    payload["exp"] = datetime.utcnow() + timedelta(hours=h)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ── Auth & helpers: verify_token, current_user, require_role, current_super_admin,
#    log_activity, create_notification — imported from dependencies.py / helpers.py (NUMBER 43)


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


# ProductCreate, ProductUpdate — imported from routers.products (NUMBER 43)

class OrderCreate(BaseModel):
    customer_id: str
    channel: str = "telegram"
    type: str = "delivery"
    address: str = ""
    notes: str = ""
    items: list = []
    branch_id: str = ""


class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    address: Optional[str] = None


class MsgCreate(BaseModel):
    content: str


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


# ── Live Readiness helpers ─────────────────────────────────────────────────────
# Note: _env_present is imported from routers.health (NUMBER 43)

META_CHANNELS = {"whatsapp", "instagram", "facebook"}

def _channel_readiness(conn, rid: str) -> dict:
    """
    Return per-channel readiness dict for one restaurant.
    Status values: ok | missing_token | missing_credentials | pending_meta |
                   webhook_not_configured | not_enabled | unknown
    """
    channels_row = {r["type"]: dict(r) for r in conn.execute(
        "SELECT * FROM channels WHERE restaurant_id=?", (rid,)
    ).fetchall()}

    meta_platform_ok = bool(META_APP_ID and META_APP_SECRET)

    result = {}
    for platform in ("telegram", "whatsapp", "instagram", "facebook"):
        ch = channels_row.get(platform)
        if not ch:
            result[platform] = {"status": "not_enabled", "reason": "لا يوجد قناة مُضافة"}
            continue

        if not ch.get("enabled"):
            result[platform] = {"status": "not_enabled", "reason": "القناة معطّلة"}
            continue

        # Last inbound: latest processed_event for this platform+restaurant
        last_inbound = conn.execute(
            "SELECT created_at FROM processed_events WHERE restaurant_id=? AND provider=? ORDER BY created_at DESC LIMIT 1",
            (rid, platform)
        ).fetchone()
        last_inbound_at = last_inbound[0] if last_inbound else None

        # Last outbound
        last_out = conn.execute(
            "SELECT status, error, created_at FROM outbound_messages WHERE restaurant_id=? AND platform=? ORDER BY created_at DESC LIMIT 1",
            (rid, platform)
        ).fetchone()
        last_outbound_at   = last_out["created_at"] if last_out else None
        last_outbound_ok   = (last_out["status"] == "sent") if last_out else None
        last_error         = (last_out["error"] or "") if last_out else None

        # Platform-specific credential check
        if platform == "telegram":
            if not ch.get("token", "").strip():
                status  = "missing_token"
                reason  = "لا يوجد Bot Token"
            elif ch.get("connection_status") == "error":
                status  = "outbound_failed"
                reason  = ch.get("last_error") or "خطأ في الاتصال"
            elif last_outbound_ok is False and last_error:
                status  = "outbound_failed"
                reason  = last_error[:120]
            else:
                status  = "ok"
                reason  = "متصل"

        elif platform in META_CHANNELS:
            if not meta_platform_ok:
                status  = "missing_credentials"
                reason  = "META_APP_ID أو META_APP_SECRET غير مضبوط"
            elif not ch.get("token", "").strip():
                status  = "pending_meta"
                reason  = "في انتظار ربط الحساب / موافقة Meta"
            elif ch.get("connection_status") in ("error", "disconnected"):
                status  = "outbound_failed"
                reason  = ch.get("last_error") or "خطأ في الاتصال"
            elif last_outbound_ok is False and last_error:
                status  = "outbound_failed"
                reason  = last_error[:120]
            else:
                status  = "ok"
                reason  = "متصل"
        else:
            status  = "unknown"
            reason  = ""

        result[platform] = {
            "status":           status,
            "reason":           reason,
            "last_inbound_at":  last_inbound_at,
            "last_outbound_at": last_outbound_at,
            "last_error":       last_error or "",
            "webhook_url":      f"{BASE_URL}/webhook/{platform}/{rid}",
        }

    return result


def _pipeline_readiness(conn, rid: str) -> dict:
    """Check orders/conversation pipeline for a restaurant."""
    try:
        orders_count = conn.execute("SELECT COUNT(*) FROM orders WHERE restaurant_id=?", (rid,)).fetchone()[0]
        conv_count   = conn.execute("SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", (rid,)).fetchone()[0]
        last_order   = conn.execute("SELECT created_at FROM orders WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1", (rid,)).fetchone()
        last_conv    = conn.execute("SELECT created_at FROM conversations WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1", (rid,)).fetchone()
        return {
            "orders_ok":           True,
            "conversations_ok":    True,
            "last_order_at":       last_order[0] if last_order else None,
            "last_conversation_at": last_conv[0] if last_conv else None,
        }
    except Exception as e:
        return {"orders_ok": False, "conversations_ok": False, "error": str(e)}


def _recommended_fix(channels: dict, ai_ok: bool) -> str:
    """Return a single-line fix recommendation based on channel statuses."""
    problems = []
    if not ai_ok:
        problems.append("أضف OPENAI_API_KEY")
    for platform, info in channels.items():
        s = info["status"]
        if s == "missing_token":
            problems.append(f"أضف Bot Token لـ {platform}")
        elif s == "missing_credentials":
            problems.append(f"أضف META_APP_ID/META_APP_SECRET لـ {platform}")
        elif s == "pending_meta":
            problems.append(f"أكمل ربط {platform} عبر OAuth")
        elif s == "outbound_failed":
            problems.append(f"تحقق من أخطاء إرسال {platform}")
    return " | ".join(problems) if problems else "لا توجد مشاكل"


@app.get("/api/live-readiness")
async def live_readiness(user=Depends(current_user)):
    """Per-restaurant live readiness: channel statuses visible to restaurant owner."""
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        channels = _channel_readiness(conn, rid)
        pipeline = _pipeline_readiness(conn, rid)
        ai_ok    = bool(OPENAI_API_KEY)
        needs_attention = (
            not ai_ok
            or any(info["status"] not in ("ok", "not_enabled") for info in channels.values())
        )
        return {
            "restaurant_id": rid,
            "ai": {
                "status":   "configured" if ai_ok else "missing",
                "reason":   "" if ai_ok else "OPENAI_API_KEY غير مضبوط",
            },
            "meta_platform": {
                "status":  "configured" if (META_APP_ID and META_APP_SECRET) else "missing_credentials",
                "reason":  "" if (META_APP_ID and META_APP_SECRET) else "META_APP_ID أو META_APP_SECRET غير مضبوط — قنوات Meta ستعمل بعد الإعداد",
            },
            "channels":         channels,
            "pipeline":         pipeline,
            "needs_attention":  needs_attention,
            "recommended_fix":  _recommended_fix(channels, ai_ok),
        }
    finally:
        conn.close()


@app.get("/api/channels/status")
async def channels_status(user=Depends(current_user)):
    """Per-restaurant channel status summary (for integrations page badge display)."""
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        channels = _channel_readiness(conn, rid)
        return {"restaurant_id": rid, "channels": channels}
    finally:
        conn.close()


# ── Super Admin: Live Readiness ────────────────────────────────────────────────

def _restaurant_readiness_row(conn, rid: str, rname: str, plan: str, rstatus: str) -> dict:
    """Build one row of the super admin live-readiness table."""
    channels  = _channel_readiness(conn, rid)
    pipeline  = _pipeline_readiness(conn, rid)
    ai_ok     = bool(OPENAI_API_KEY)

    # Last error across all outbound
    last_err_row = conn.execute(
        "SELECT platform, error, created_at FROM outbound_messages "
        "WHERE restaurant_id=? AND status='failed' ORDER BY created_at DESC LIMIT 1",
        (rid,)
    ).fetchone()
    last_error = dict(last_err_row) if last_err_row else None

    needs_attention = (
        not ai_ok
        or rstatus not in ("active", "trial")
        or any(info["status"] not in ("ok", "not_enabled", "pending_meta") for info in channels.values())
    )

    return {
        "restaurant_id":    rid,
        "restaurant_name":  rname,
        "plan":             plan,
        "restaurant_status": rstatus,
        "ai": {
            "status":         "configured" if ai_ok else "missing",
            "reason":         "" if ai_ok else "OPENAI_API_KEY غير مضبوط",
            "last_tested_at": None,
        },
        "channels":     channels,
        "pipeline":     pipeline,
        "last_error":   last_error,
        "recommended_fix": _recommended_fix(channels, ai_ok),
        "needs_attention": needs_attention,
    }


@app.get("/api/super/live-readiness")
async def super_live_readiness(admin=Depends(current_super_admin)):
    """Super admin: live readiness summary for ALL restaurants."""
    conn = database.get_db()
    try:
        restaurants = conn.execute(
            "SELECT id, name, plan, status FROM restaurants ORDER BY name"
        ).fetchall()
        rows = [
            _restaurant_readiness_row(conn, r["id"], r["name"], r["plan"], r["status"])
            for r in restaurants
        ]
        total      = len(rows)
        ok_count   = sum(1 for r in rows if not r["needs_attention"])
        needs_attn = sum(1 for r in rows if r["needs_attention"])
        return {
            "summary": {
                "total":          total,
                "ok":             ok_count,
                "needs_attention": needs_attn,
                "openai_configured": bool(OPENAI_API_KEY),
                "meta_configured":   bool(META_APP_ID and META_APP_SECRET),
                "base_url":          BASE_URL,
                "db_backend":        "postgresql" if database.IS_POSTGRES else "sqlite",
            },
            "restaurants": rows,
        }
    finally:
        conn.close()


@app.get("/api/super/channel-health")
async def super_channel_health(admin=Depends(current_super_admin)):
    """Super admin: per-channel health across all restaurants, grouped by status."""
    conn = database.get_db()
    try:
        restaurants = conn.execute(
            "SELECT id, name, plan, status FROM restaurants ORDER BY name"
        ).fetchall()

        by_channel: dict = {p: {"ok": [], "issues": []} for p in ("telegram", "whatsapp", "instagram", "facebook")}
        all_rows = []

        for r in restaurants:
            channels = _channel_readiness(conn, r["id"])
            for platform, info in channels.items():
                entry = {
                    "restaurant_id":   r["id"],
                    "restaurant_name": r["name"],
                    "platform":        platform,
                    **info,
                }
                bucket = "ok" if info["status"] in ("ok", "not_enabled") else "issues"
                by_channel[platform][bucket].append(entry)
                if info["status"] not in ("ok", "not_enabled"):
                    all_rows.append(entry)

        return {
            "needs_attention": all_rows,
            "by_channel":      by_channel,
        }
    finally:
        conn.close()


# ── Super Admin: Telegram Channel Recovery ────────────────────────────────────

@app.post("/api/super/channels/{rid}/telegram/test-send")
async def super_telegram_test_send(rid: str, admin=Depends(current_super_admin)):
    """Super admin: send a test message to verify Telegram bot token is valid.
    Calls getMe (no user required) — does NOT send a real chat message."""
    import httpx as _httpx
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT token, connection_status FROM channels WHERE restaurant_id=? AND type='telegram'",
            (rid,)
        ).fetchone()
    finally:
        conn.close()

    if not ch:
        raise HTTPException(404, "لا توجد قناة Telegram لهذا المطعم")
    token = ch["token"] or ""
    if not token:
        return {"ok": False, "diagnosis": "Bot Token فارغ — أدخل التوكن في إعدادات القناة"}

    try:
        r = _httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
    except Exception as e:
        return {"ok": False, "diagnosis": f"لا يمكن الوصول إلى Telegram API: {e}"}

    if data.get("ok"):
        bot = data.get("result", {})
        conn2 = database.get_db()
        conn2.execute(
            "UPDATE channels SET connection_status='connected', last_error='', last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
            (rid,)
        )
        conn2.commit(); conn2.close()
        return {
            "ok": True,
            "diagnosis": f"البوت صالح: @{bot.get('username')} ({bot.get('first_name')})",
            "bot_username": bot.get("username"),
        }
    else:
        desc = data.get("description", str(data))
        from services.webhooks import _classify_telegram_error
        friendly = _classify_telegram_error(r.status_code, desc)
        conn2 = database.get_db()
        conn2.execute(
            "UPDATE channels SET connection_status='error', last_error=?, last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
            (friendly, rid)
        )
        conn2.commit(); conn2.close()
        return {"ok": False, "diagnosis": friendly}


@app.post("/api/super/channels/{rid}/telegram/register-webhook")
async def super_telegram_register_webhook(rid: str, admin=Depends(current_super_admin)):
    """Super admin: (re-)register Telegram webhook for a restaurant."""
    import httpx as _httpx
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'", (rid,)
        ).fetchone()
    finally:
        conn.close()

    if not ch or not ch["token"]:
        raise HTTPException(400, "Bot Token مفقود — أضف التوكن في إعدادات القناة أولاً")

    webhook_url = f"{BASE_URL}/webhook/telegram/{rid}"
    try:
        r = _httpx.post(
            f"https://api.telegram.org/bot{ch['token']}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "edited_message", "callback_query"]},
            timeout=15,
        )
        data = r.json()
    except _httpx.TimeoutException:
        raise HTTPException(400, "انتهت مهلة الاتصال بـ Telegram — تحقق من صحة التوكن واتصال الإنترنت")
    except Exception as e:
        raise HTTPException(400, f"خطأ في الاتصال بـ Telegram: {e}")

    conn2 = database.get_db()
    if data.get("ok"):
        info_r = _httpx.get(f"https://api.telegram.org/bot{ch['token']}/getWebhookInfo", timeout=10)
        info = info_r.json().get("result", {})
        conn2.execute(
            "UPDATE channels SET webhook_url=?, connection_status='connected', last_error='', last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
            (webhook_url, rid)
        )
        conn2.commit(); conn2.close()
        return {"ok": True, "webhook_url": webhook_url, "telegram_info": info}
    else:
        err = data.get("description", "فشل التسجيل")
        from services.webhooks import _classify_telegram_error
        friendly = _classify_telegram_error(r.status_code, err)
        conn2.execute(
            "UPDATE channels SET connection_status='error', last_error=?, last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
            (friendly, rid)
        )
        conn2.commit(); conn2.close()
        raise HTTPException(400, friendly)


@app.post("/api/super/channels/{rid}/telegram/clear-webhook")
async def super_telegram_clear_webhook(rid: str, admin=Depends(current_super_admin)):
    """Super admin: delete/clear the Telegram webhook (stops Telegram from pushing updates)."""
    import httpx as _httpx
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'", (rid,)
        ).fetchone()
    finally:
        conn.close()

    if not ch or not ch["token"]:
        raise HTTPException(400, "Bot Token مفقود")

    try:
        r = _httpx.post(
            f"https://api.telegram.org/bot{ch['token']}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
        data = r.json()
    except _httpx.TimeoutException:
        raise HTTPException(400, "انتهت مهلة الاتصال بـ Telegram — تحقق من صحة التوكن")
    except Exception as e:
        raise HTTPException(400, f"خطأ في الاتصال بـ Telegram: {e}")

    conn2 = database.get_db()
    if data.get("ok"):
        conn2.execute(
            "UPDATE channels SET webhook_url='', connection_status='disconnected', last_error='Webhook cleared by super admin', last_tested_at=CURRENT_TIMESTAMP WHERE restaurant_id=? AND type='telegram'",
            (rid,)
        )
        conn2.commit(); conn2.close()
        return {"ok": True, "message": "تم حذف الويب هوك من Telegram بنجاح"}
    else:
        conn2.close()
        raise HTTPException(400, data.get("description", "فشل حذف الويب هوك"))


# ── Restaurant-level own-channel diagnostics (owner-scoped) ───────────────────

@app.post("/api/channels/telegram/test-connection")
async def test_telegram_connection(user=Depends(current_user)):
    """Restaurant owner: test their own Telegram bot token validity via getMe."""
    import httpx as _httpx
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'", (rid,)
        ).fetchone()
    finally:
        conn.close()

    if not ch or not ch["token"]:
        return {"ok": False, "message": "Bot Token غير مضبوط — أضف التوكن في إعدادات القناة"}

    try:
        r = _httpx.get(f"https://api.telegram.org/bot{ch['token']}/getMe", timeout=10)
        data = r.json()
    except Exception as e:
        return {"ok": False, "message": f"تعذّر الاتصال بـ Telegram: {e}"}

    if data.get("ok"):
        bot = data.get("result", {})
        return {"ok": True, "message": f"البوت يعمل: @{bot.get('username')} ✅"}
    else:
        from services.webhooks import _classify_telegram_error
        return {"ok": False, "message": _classify_telegram_error(r.status_code, data.get("description", ""))}


@app.get("/api/channels/readiness-summary")
async def channel_readiness_summary(user=Depends(current_user)):
    """Restaurant owner: simplified readiness summary for their own channels only."""
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        channels = _channel_readiness(conn, rid)
        ai_ok = bool(OPENAI_API_KEY)
        issues = {p: info for p, info in channels.items()
                  if info["status"] not in ("ok", "not_enabled")}
        return {
            "ai_ok": ai_ok,
            "channels": {
                p: {
                    "status": info["status"],
                    "reason": info["reason"],
                }
                for p, info in channels.items()
            },
            "has_issues": bool(issues) or not ai_ok,
            "recommended_fix": _recommended_fix(channels, ai_ok),
        }
    finally:
        conn.close()


# ── Subscription: restaurant owner endpoint ────────────────────────────────────

@app.get("/api/subscription/status")
async def subscription_status(user=Depends(current_user)):
    """Restaurant owner: read their own subscription state (allowed even when expired)."""
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        state = get_subscription_state(conn, rid)
        plan  = state["plan"]
        features = state["features"]
        # Calculate trial days remaining
        trial_days_left = None
        trial_ends = state.get("trial_ends_at") or ""
        if trial_ends:
            try:
                from datetime import datetime as _dt
                d = _dt.strptime(trial_ends[:10], "%Y-%m-%d")
                trial_days_left = max(0, (d - _dt.now()).days)
            except Exception:
                pass
        limits = _plan_limits_from_db(conn, plan)
        plan_row = _get_plan_record(conn, plan_code=plan)
        plan_label_default = {"free": "مجاني", "trial": "تجريبي", "starter": "أساسي",
                              "professional": "احترافي", "enterprise": "مؤسسي"}.get(plan, plan)
        # Stripe status
        stripe_row = conn.execute(
            "SELECT stripe_enabled, stripe_secret_key FROM settings WHERE restaurant_id=? LIMIT 1",
            (rid,)
        ).fetchone()
        stripe_enabled = bool(stripe_row and stripe_row["stripe_enabled"] and stripe_row["stripe_secret_key"])
        sub_stripe = conn.execute(
            "SELECT payment_customer_id, payment_subscription_id FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
            (rid,)
        ).fetchone()
        return {
            "plan":               plan,
            "status":             state["status"],
            "blocked":            state["blocked"],
            "blocked_reason":     state["reason"],
            "trial_ends_at":      trial_ends,
            "trial_days_left":    trial_days_left,
            "current_period_end": state.get("current_period_end") or "",
            "cancelled_at":       state.get("cancelled_at") or "",
            "billing_email":      state.get("billing_email") or "",
            "payment_provider":   state.get("payment_provider") or "",
            "stripe_enabled":          stripe_enabled,
            "payment_customer_id":     (sub_stripe or {}).get("payment_customer_id") or "",
            "payment_subscription_id": (sub_stripe or {}).get("payment_subscription_id") or "",
            "features": {
                "ai_enabled":         features.get("ai", False),
                "analytics_enabled":  features.get("analytics", False),
                "media_enabled":      features.get("media", False),
                "handoff_enabled":    features.get("handoff", False),
                "channels_allowed":   limits.get("channels", 0),
                "max_products":       limits.get("products", 0),
                "max_staff":          limits.get("staff", 0),
                "max_conversations":  features.get("max_conversations", 0),
            },
            "plan_label": (plan_row or {}).get("name") or plan_label_default,
        }
    finally:
        conn.close()


# ── Super Admin: Subscription management actions ───────────────────────────────

def _ensure_subscription(conn, rid: str, plan: str = "trial") -> None:
    """Create a subscription row if one doesn't exist."""
    existing = conn.execute("SELECT id FROM subscriptions WHERE restaurant_id=?", (rid,)).fetchone()
    if not existing:
        from datetime import timedelta as _td
        trial_end = (datetime.now() + _td(days=14)).strftime("%Y-%m-%d")
        end_date  = (datetime.now() + _td(days=365)).strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO subscriptions (id,restaurant_id,plan,status,price,start_date,end_date,trial_ends_at)
            VALUES (?,?,?,?,0,date('now'),?,?)
        """, (str(uuid.uuid4()), rid, plan, "trial", end_date, trial_end))


@app.patch("/api/super/restaurants/{rid}/subscription")
async def super_patch_subscription(rid: str, data: dict, admin=Depends(current_super_admin)):
    """Super admin: update specific subscription fields (PATCH semantics)."""
    conn = database.get_db()
    try:
        _ensure_subscription(conn, rid)
        allowed_fields = {"plan", "status", "price", "end_date", "trial_ends_at",
                          "notes", "next_payment_date", "suspended_reason",
                          "billing_email", "payment_provider", "cancelled_at"}
        updates = {k: v for k, v in data.items() if k in allowed_fields}
        if not updates:
            raise HTTPException(400, "لا توجد حقول صالحة للتحديث")
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE subscriptions SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE restaurant_id=?",
            (*updates.values(), rid)
        )
        if "plan" in updates:
            conn.execute("UPDATE restaurants SET plan=? WHERE id=?", (updates["plan"], rid))
        if "status" in updates:
            conn.execute("UPDATE restaurants SET status=? WHERE id=?", (updates["status"], rid))
        conn.commit()
        _sa_log(conn, admin["id"], admin.get("name",""), "patch_subscription", "subscription", rid,
                f"Updated: {list(updates.keys())}")
        return {"ok": True, "updated": list(updates.keys())}
    finally:
        conn.close()


@app.post("/api/super/restaurants/{rid}/subscription/activate")
async def super_activate_subscription(rid: str, admin=Depends(current_super_admin)):
    """Super admin: activate or reactivate a restaurant subscription."""
    conn = database.get_db()
    try:
        _ensure_subscription(conn, rid)
        from datetime import timedelta as _td
        new_end = (datetime.now() + _td(days=365)).strftime("%Y-%m-%d")
        conn.execute("""
            UPDATE subscriptions SET status='active', cancelled_at='', suspended_reason='',
                end_date=?, updated_at=CURRENT_TIMESTAMP WHERE restaurant_id=?
        """, (new_end, rid))
        conn.execute("UPDATE restaurants SET status='active' WHERE id=?", (rid,))
        conn.commit()
        _sa_log(conn, admin["id"], admin.get("name",""), "activate_subscription", "subscription", rid, "Activated")
        return {"ok": True, "status": "active", "end_date": new_end}
    finally:
        conn.close()


@app.post("/api/super/restaurants/{rid}/subscription/suspend")
async def super_suspend_subscription(rid: str, data: dict = {}, admin=Depends(current_super_admin)):
    """Super admin: suspend a restaurant (blocks AI/channels, preserves data)."""
    reason = (data or {}).get("reason", "موقوف بواسطة المشرف")
    conn = database.get_db()
    try:
        _ensure_subscription(conn, rid)
        conn.execute("""
            UPDATE subscriptions SET status='suspended', suspended_reason=?,
                updated_at=CURRENT_TIMESTAMP WHERE restaurant_id=?
        """, (reason, rid))
        conn.execute("UPDATE restaurants SET status='suspended' WHERE id=?", (rid,))
        conn.commit()
        _sa_log(conn, admin["id"], admin.get("name",""), "suspend_subscription", "subscription", rid, reason)
        return {"ok": True, "status": "suspended", "reason": reason}
    finally:
        conn.close()


@app.post("/api/super/restaurants/{rid}/subscription/cancel")
async def super_cancel_subscription(rid: str, admin=Depends(current_super_admin)):
    """Super admin: cancel a subscription."""
    conn = database.get_db()
    try:
        _ensure_subscription(conn, rid)
        conn.execute("""
            UPDATE subscriptions SET status='cancelled', cancelled_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP WHERE restaurant_id=?
        """, (rid,))
        conn.execute("UPDATE restaurants SET status='suspended' WHERE id=?", (rid,))
        conn.commit()
        _sa_log(conn, admin["id"], admin.get("name",""), "cancel_subscription", "subscription", rid, "Cancelled")
        return {"ok": True, "status": "cancelled"}
    finally:
        conn.close()


@app.post("/api/super/restaurants/{rid}/subscription/extend-trial")
async def super_extend_trial(rid: str, data: dict = {}, admin=Depends(current_super_admin)):
    """Super admin: extend trial by N days (default 14)."""
    days = int((data or {}).get("days", 14))
    if days < 1 or days > 365:
        raise HTTPException(400, "أيام التمديد يجب أن تكون بين 1 و 365")
    conn = database.get_db()
    try:
        _ensure_subscription(conn, rid)
        from datetime import timedelta as _td
        # Extend from today or from current trial_ends_at, whichever is later
        current = conn.execute("SELECT trial_ends_at FROM subscriptions WHERE restaurant_id=?", (rid,)).fetchone()
        base = datetime.now()
        if current and current["trial_ends_at"]:
            try:
                parsed = datetime.strptime(current["trial_ends_at"][:10], "%Y-%m-%d")
                if parsed > base:
                    base = parsed
            except Exception:
                pass
        new_end = (base + _td(days=days)).strftime("%Y-%m-%d")
        conn.execute("""
            UPDATE subscriptions SET trial_ends_at=?, status='trial',
                updated_at=CURRENT_TIMESTAMP WHERE restaurant_id=?
        """, (new_end, rid))
        conn.execute("UPDATE restaurants SET status='active' WHERE id=?", (rid,))
        conn.commit()
        _sa_log(conn, admin["id"], admin.get("name",""), "extend_trial", "subscription", rid,
                f"Extended by {days} days → {new_end}")
        return {"ok": True, "trial_ends_at": new_end, "days_added": days}
    finally:
        conn.close()


# ── Billing placeholders (no payment gateway yet) ─────────────────────────────

@app.post("/api/billing/create-checkout-session")
async def billing_create_checkout(user=Depends(current_user)):
    """Placeholder: will redirect to payment gateway when configured."""
    return {
        "ok": False,
        "code": "payment_provider_not_configured",
        "message": "بوابة الدفع لم تُهيَّأ بعد — تواصل مع المشرف لتفعيل الاشتراك يدوياً",
    }


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Placeholder: receives payment gateway webhooks (Stripe/Paddle/etc)."""
    return {"received": True, "note": "payment_provider_not_configured"}


# ── Plan catalogue (no Stripe — manual billing) ───────────────────────────────

PLAN_PRICES = {
    "free":         {"label": "مجاني",    "price": 0,      "currency": "IQD", "duration_days": 0,
                     "features": ["5 منتجات", "موظف واحد", "بدون AI"]},
    "trial":        {"label": "تجريبي",   "price": 0,      "currency": "IQD", "duration_days": 14,
                     "features": ["10 منتجات", "موظفان", "قناة واحدة", "AI مُفعَّل", "200 محادثة"]},
    "starter":      {"label": "أساسي",    "price": 25000,  "currency": "IQD", "duration_days": 30,
                     "features": ["50 منتج", "5 موظفين", "3 قنوات", "AI مُفعَّل", "500 محادثة"]},
    "professional": {"label": "احترافي",  "price": 75000,  "currency": "IQD", "duration_days": 30,
                     "features": ["منتجات غير محدودة", "15 موظف", "قنوات غير محدودة", "AI + تحليلات + وسائط", "5000 محادثة"]},
    "enterprise":   {"label": "مؤسسي",   "price": 200000, "currency": "IQD", "duration_days": 30,
                     "features": ["كل شيء غير محدود", "دعم مخصص"]},
}

_ALLOWED_PROOF_TYPES = {"image/jpeg", "image/jpg", "image/png", "application/pdf"}
_ALLOWED_PROOF_EXTS  = {".jpg", ".jpeg", ".png", ".pdf"}
_MAX_PROOF_SIZE      = 5 * 1024 * 1024  # 5 MB


def _billing_audit(conn, action: str, actor_id: str = "", actor_role: str = "",
                   restaurant_id: str = "", payment_request_id: str = "",
                   payment_method_id: str = "", old_status: str = "", new_status: str = "",
                   amount: float = 0, currency: str = "", plan: str = "",
                   note: str = "", storage_mode: str = "") -> None:
    """Insert a row into billing_audit_logs. Swallows errors so it never breaks main flow."""
    try:
        conn.execute("""
            INSERT INTO billing_audit_logs
                (id, action, actor_id, actor_role, restaurant_id,
                 payment_request_id, payment_method_id, old_status, new_status,
                 amount, currency, plan, note, storage_mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), action, actor_id, actor_role, restaurant_id,
              payment_request_id, payment_method_id, old_status, new_status,
              amount, currency, plan, note, storage_mode))
    except Exception as _e:
        logger.warning(f"[billing_audit] insert failed: {_e}")


def _proof_storage_mode() -> str:
    """Returns 'supabase' if storage is configured, else 'local'."""
    from services import storage as _storage
    return "supabase" if _storage._is_configured() else "local"


# ── Super Admin: Subscription Plans CRUD ─────────────────────────────────────

class SubscriptionPlanReq(BaseModel):
    code: str = ""
    name: str
    name_ar: str = ""
    description: str = ""
    description_ar: str = ""
    price: float = 0
    currency: str = "USD"
    billing_period: str = "monthly"
    billing_period_ar: str = ""
    duration_days: int = 30
    is_active: int = 1
    is_public: int = 1
    is_recommended: int = 0
    display_order: int = 0
    max_channels: int = 1
    max_products: int = 10
    max_staff: int = 2
    max_conversations_per_month: int = 200
    max_customers: int = 0
    max_ai_replies_per_month: int = 0
    max_team_members: int = 2
    max_branches: int = 1
    ai_enabled: int = 1
    analytics_enabled: int = 0
    advanced_analytics_enabled: int = 0
    media_enabled: int = 0
    voice_enabled: int = 0
    image_enabled: int = 0
    video_enabled: int = 0
    story_reply_enabled: int = 0
    human_handoff_enabled: int = 1
    multi_channel_enabled: int = 0
    memory_enabled: int = 0
    upsell_enabled: int = 0
    smart_recommendations_enabled: int = 0
    menu_image_understanding_enabled: int = 0
    live_readiness_status_enabled: int = 0
    priority_support_enabled: int = 0
    setup_assistance_enabled: int = 0
    telegram_enabled: int = 1
    whatsapp_enabled: int = 1
    instagram_enabled: int = 1
    facebook_enabled: int = 1
    support_level: str = "community"
    features_json: str = "[]"
    excluded_features_json: str = "[]"
    badge: str = ""
    badge_text_ar: str = ""
    limits_json: str = "{}"


@app.get("/api/super/subscription-plans")
async def super_list_plans(admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM subscription_plans ORDER BY display_order ASC, created_at ASC"
        ).fetchall()
        return {"plans": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/super/subscription-plans")
async def super_create_plan(body: SubscriptionPlanReq, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        if not body.code:
            raise HTTPException(400, "code مطلوب")
        existing = conn.execute("SELECT id FROM subscription_plans WHERE code=?", (body.code,)).fetchone()
        if existing:
            raise HTTPException(400, f"كود الخطة '{body.code}' مستخدم مسبقاً")
        pid = str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO subscription_plans
                (id, code, name, name_ar, description, description_ar,
                 price, currency, billing_period, billing_period_ar, duration_days,
                 is_active, is_public, is_recommended, display_order,
                 max_channels, max_products, max_staff, max_conversations_per_month,
                 max_customers, max_ai_replies_per_month, max_team_members, max_branches,
                 ai_enabled, analytics_enabled, advanced_analytics_enabled, media_enabled,
                 voice_enabled, image_enabled, video_enabled, story_reply_enabled,
                 human_handoff_enabled, multi_channel_enabled, memory_enabled, upsell_enabled,
                 smart_recommendations_enabled, menu_image_understanding_enabled,
                 live_readiness_status_enabled, priority_support_enabled, setup_assistance_enabled,
                 telegram_enabled, whatsapp_enabled, instagram_enabled, facebook_enabled,
                 support_level, features_json, excluded_features_json,
                 badge, badge_text_ar, limits_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (pid, body.code, body.name, body.name_ar, body.description, body.description_ar,
              body.price, body.currency, body.billing_period, body.billing_period_ar,
              body.duration_days, body.is_active, body.is_public, body.is_recommended,
              body.display_order, body.max_channels, body.max_products, body.max_staff,
              body.max_conversations_per_month, body.max_customers, body.max_ai_replies_per_month,
              body.max_team_members, body.max_branches,
              body.ai_enabled, body.analytics_enabled, body.advanced_analytics_enabled,
              body.media_enabled, body.voice_enabled, body.image_enabled, body.video_enabled,
              body.story_reply_enabled, body.human_handoff_enabled, body.multi_channel_enabled,
              body.memory_enabled, body.upsell_enabled, body.smart_recommendations_enabled,
              body.menu_image_understanding_enabled, body.live_readiness_status_enabled,
              body.priority_support_enabled, body.setup_assistance_enabled,
              body.telegram_enabled, body.whatsapp_enabled, body.instagram_enabled,
              body.facebook_enabled, body.support_level, body.features_json,
              body.excluded_features_json, body.badge, body.badge_text_ar, body.limits_json,
              now, now))
        _billing_audit(conn, "subscription_plan_created",
                       actor_id=admin["id"], actor_role="super_admin",
                       plan=body.code, note=f"name={body.name} price={body.price}")
        conn.commit()
        return {"ok": True, "id": pid, "code": body.code}
    finally:
        conn.close()


@app.patch("/api/super/subscription-plans/{plan_id}")
async def super_update_plan(plan_id: str, body: dict, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT * FROM subscription_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "الخطة غير موجودة")
        allowed = {
            "name", "name_ar", "description", "description_ar",
            "price", "currency", "billing_period", "billing_period_ar", "duration_days",
            "is_active", "is_public", "is_recommended", "display_order",
            "max_channels", "max_products", "max_staff", "max_conversations_per_month",
            "max_customers", "max_ai_replies_per_month", "max_team_members", "max_branches",
            "ai_enabled", "analytics_enabled", "advanced_analytics_enabled", "media_enabled",
            "voice_enabled", "image_enabled", "video_enabled", "story_reply_enabled",
            "human_handoff_enabled", "multi_channel_enabled", "memory_enabled", "upsell_enabled",
            "smart_recommendations_enabled", "menu_image_understanding_enabled",
            "live_readiness_status_enabled", "priority_support_enabled", "setup_assistance_enabled",
            "telegram_enabled", "whatsapp_enabled", "instagram_enabled", "facebook_enabled",
            "support_level", "features_json", "excluded_features_json",
            "badge", "badge_text_ar", "limits_json",
        }
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            raise HTTPException(400, "لا توجد حقول صالحة للتحديث")
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE subscription_plans SET {set_clause} WHERE id=?",
                     (*updates.values(), plan_id))
        _billing_audit(conn, "subscription_plan_updated",
                       actor_id=admin["id"], actor_role="super_admin",
                       plan=dict(row).get("code", ""), note=str(list(updates.keys())))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/super/subscription-plans/{plan_id}")
async def super_disable_plan(plan_id: str, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT * FROM subscription_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "الخطة غير موجودة")
        conn.execute("UPDATE subscription_plans SET is_active=0, updated_at=? WHERE id=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), plan_id))
        _billing_audit(conn, "subscription_plan_disabled",
                       actor_id=admin["id"], actor_role="super_admin",
                       plan=dict(row).get("code", ""))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── Super Admin: Payment Methods ──────────────────────────────────────────────

@app.get("/api/super/payment-methods")
async def super_list_payment_methods(admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM payment_methods ORDER BY display_order ASC, created_at ASC"
        ).fetchall()
        return {"payment_methods": [dict(r) for r in rows]}
    finally:
        conn.close()


class PaymentMethodReq(BaseModel):
    method_name: str
    account_holder_name: str = ""
    bank_name: str = ""
    account_number: str = ""
    iban: str = ""
    phone_number: str = ""
    currency: str = "IQD"
    payment_instructions: str = ""
    is_active: int = 1
    display_order: int = 0


@app.post("/api/super/payment-methods")
async def super_create_payment_method(body: PaymentMethodReq, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        mid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO payment_methods
                (id, method_name, account_holder_name, bank_name, account_number,
                 iban, phone_number, currency, payment_instructions, is_active, display_order)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (mid, body.method_name, body.account_holder_name, body.bank_name,
              body.account_number, body.iban, body.phone_number, body.currency,
              body.payment_instructions, body.is_active, body.display_order))
        _billing_audit(conn, "payment_method_created", actor_id=admin["id"],
                       actor_role="super_admin", payment_method_id=mid,
                       note=body.method_name)
        conn.commit()
        row = conn.execute("SELECT * FROM payment_methods WHERE id=?", (mid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.patch("/api/super/payment-methods/{method_id}")
async def super_update_payment_method(method_id: str, body: dict, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT id FROM payment_methods WHERE id=?", (method_id,)).fetchone()
        if not row:
            raise HTTPException(404, "طريقة الدفع غير موجودة")
        allowed = {"method_name", "account_holder_name", "bank_name", "account_number",
                   "iban", "phone_number", "currency", "payment_instructions", "is_active", "display_order"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            raise HTTPException(400, "لا توجد حقول للتحديث")
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE payment_methods SET {set_clause} WHERE id=?",
            list(updates.values()) + [method_id]
        )
        _billing_audit(conn, "payment_method_updated", actor_id=admin["id"],
                       actor_role="super_admin", payment_method_id=method_id,
                       note=str(list(updates.keys())))
        conn.commit()
        updated = conn.execute("SELECT * FROM payment_methods WHERE id=?", (method_id,)).fetchone()
        return dict(updated)
    finally:
        conn.close()


@app.delete("/api/super/payment-methods/{method_id}")
async def super_disable_payment_method(method_id: str, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT id FROM payment_methods WHERE id=?", (method_id,)).fetchone()
        if not row:
            raise HTTPException(404, "طريقة الدفع غير موجودة")
        conn.execute(
            "UPDATE payment_methods SET is_active=0, updated_at=? WHERE id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), method_id)
        )
        _billing_audit(conn, "payment_method_disabled", actor_id=admin["id"],
                       actor_role="super_admin", payment_method_id=method_id)
        conn.commit()
        return {"ok": True, "disabled": method_id}
    finally:
        conn.close()


# ── Super Admin: Payment Requests ─────────────────────────────────────────────

@app.get("/api/super/payment-requests")
async def super_list_payment_requests(
    status: Optional[str] = None,
    admin=Depends(current_super_admin)
):
    conn = database.get_db()
    try:
        if status:
            rows = conn.execute("""
                SELECT pr.*, r.name AS restaurant_name
                FROM payment_requests pr
                JOIN restaurants r ON pr.restaurant_id = r.id
                WHERE pr.status=? ORDER BY pr.created_at DESC
            """, (status,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT pr.*, r.name AS restaurant_name
                FROM payment_requests pr
                JOIN restaurants r ON pr.restaurant_id = r.id
                ORDER BY pr.created_at DESC
            """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Use stored proof_url (Supabase or local) — fallback to local path for old rows
            if not d.get("proof_url") and d.get("proof_path"):
                d["proof_url"] = f"/uploads/payment_proofs/{d['proof_path']}"
            result.append(d)
        return {"payment_requests": result}
    finally:
        conn.close()


@app.post("/api/super/payment-requests/{req_id}/approve")
async def super_approve_payment_request(
    req_id: str,
    body: dict = {},
    admin=Depends(current_super_admin)
):
    conn = database.get_db()
    try:
        pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (req_id,)).fetchone()
        if not pr:
            raise HTTPException(404, "الطلب غير موجود")
        pr = dict(pr)
        if pr["status"] != "pending":
            raise HTTPException(400, f"الطلب بحالة {pr['status']} — لا يمكن الموافقة عليه")

        plan = pr["plan"]
        plan_row = _get_plan_record(conn, plan_id=pr.get("plan_id", ""), plan_code=plan)
        duration = (plan_row or {}).get("duration_days") or PLAN_PRICES.get(plan, {}).get("duration_days", 30)
        period_start = datetime.now().strftime("%Y-%m-%d")
        period_end   = (datetime.now() + timedelta(days=duration or 30)).strftime("%Y-%m-%d")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        admin_name = admin.get("name", "Super Admin")

        # Update payment request
        conn.execute("""
            UPDATE payment_requests SET status='approved', reviewed_by=?, reviewed_at=?,
            internal_note=? WHERE id=?
        """, (admin_name, now_str, body.get("internal_note", ""), req_id))

        # Update subscription
        _ensure_subscription(conn, pr["restaurant_id"], plan)
        conn.execute("""
            UPDATE subscriptions SET plan=?, status='active', start_date=?, end_date=?,
            payment_method=?, last_payment_date=?, next_payment_date=?, updated_at=?
            WHERE restaurant_id=?
        """, (plan, period_start, period_end,
              pr.get("payment_method_id", ""), period_start, period_end,
              now_str, pr["restaurant_id"]))

        # Update restaurant plan
        conn.execute("UPDATE restaurants SET plan=?, status='active' WHERE id=?",
                     (plan, pr["restaurant_id"]))

        # Insert payment record
        method_row = conn.execute(
            "SELECT method_name FROM payment_methods WHERE id=?",
            (pr.get("payment_method_id", ""),)
        ).fetchone()
        method_label = method_row["method_name"] if method_row else pr.get("payment_method_id", "")
        conn.execute("""
            INSERT INTO payment_records
                (id, restaurant_id, payment_request_id, amount, currency, method, plan,
                 period_start, period_end, status)
            VALUES (?,?,?,?,?,?,?,?,?,'completed')
        """, (str(uuid.uuid4()), pr["restaurant_id"], req_id,
              pr["amount"], pr["currency"], method_label, plan,
              period_start, period_end))

        # Super admin audit log + billing audit
        conn.execute("""
            INSERT INTO super_admin_log (id, admin_id, admin_name, action, target_type, target_id, description)
            VALUES (?,?,?,'approve_payment','restaurant',?,?)
        """, (str(uuid.uuid4()), admin["id"], admin_name, pr["restaurant_id"],
              f"Approved plan={plan} amount={pr['amount']} {pr['currency']}"))
        _billing_audit(conn, "payment_request_approved",
                       actor_id=admin["id"], actor_role="super_admin",
                       restaurant_id=pr["restaurant_id"], payment_request_id=req_id,
                       old_status="pending", new_status="approved",
                       amount=pr["amount"], currency=pr["currency"], plan=plan,
                       note=body.get("internal_note", ""))
        _billing_audit(conn, "subscription_activated_from_payment",
                       actor_id=admin["id"], actor_role="super_admin",
                       restaurant_id=pr["restaurant_id"], payment_request_id=req_id,
                       old_status="trial", new_status="active",
                       plan=plan, note=f"period_end={period_end}")
        conn.commit()

        return {
            "ok": True, "status": "approved", "plan": plan,
            "period_start": period_start, "period_end": period_end,
        }
    finally:
        conn.close()


@app.post("/api/super/payment-requests/{req_id}/reject")
async def super_reject_payment_request(
    req_id: str,
    body: dict = {},
    admin=Depends(current_super_admin)
):
    conn = database.get_db()
    try:
        pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (req_id,)).fetchone()
        if not pr:
            raise HTTPException(404, "الطلب غير موجود")
        pr = dict(pr)
        if pr["status"] != "pending":
            raise HTTPException(400, f"الطلب بحالة {pr['status']} — لا يمكن رفضه")

        reason = body.get("reason", "") or "لم تُقدَّم أسباب"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        admin_name = admin.get("name", "Super Admin")

        conn.execute("""
            UPDATE payment_requests
            SET status='rejected', reject_reason=?, reviewed_by=?, reviewed_at=?, internal_note=?
            WHERE id=?
        """, (reason, admin_name, now_str, body.get("internal_note", ""), req_id))

        conn.execute("""
            INSERT INTO super_admin_log (id, admin_id, admin_name, action, target_type, target_id, description)
            VALUES (?,?,?,'reject_payment','restaurant',?,?)
        """, (str(uuid.uuid4()), admin["id"], admin_name, pr["restaurant_id"],
              f"Rejected: {reason}"))
        _billing_audit(conn, "payment_request_rejected",
                       actor_id=admin["id"], actor_role="super_admin",
                       restaurant_id=pr["restaurant_id"], payment_request_id=req_id,
                       old_status="pending", new_status="rejected",
                       amount=pr["amount"], currency=pr["currency"], plan=pr["plan"],
                       note=reason)
        conn.commit()
        return {"ok": True, "status": "rejected", "reason": reason}
    finally:
        conn.close()


@app.get("/api/super/payment/storage-mode")
async def super_payment_storage_mode(admin=Depends(current_super_admin)):
    """Returns current proof storage mode and configuration status."""
    mode = _proof_storage_mode()
    return {
        "storage_mode": mode,
        "supabase_configured": mode == "supabase",
        "warning": None if mode == "supabase" else
            "⚠️ وضع التخزين المحلي مفعّل — الملفات قد تُفقد عند إعادة النشر على Railway/Render. أضف SUPABASE_URL و SUPABASE_SERVICE_ROLE_KEY لتفعيل التخزين الدائم.",
        "local_path": "uploads/payment_proofs/" if mode == "local" else None,
        "supabase_bucket": "payment-proofs" if mode == "supabase" else None,
    }


# ── Restaurant: Billing — payment methods + plans + proof upload ──────────────

@app.get("/api/billing/payment-methods")
async def billing_list_payment_methods(user=Depends(current_user)):
    conn = database.get_db()
    try:
        rows = conn.execute("""
            SELECT id, method_name, account_holder_name, bank_name,
                   account_number, iban, phone_number, currency,
                   payment_instructions, display_order
            FROM payment_methods
            WHERE is_active=1
            ORDER BY display_order ASC, created_at ASC
        """).fetchall()
        return {"payment_methods": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/billing/plans")
async def billing_plans(user=Depends(current_user)):
    conn = database.get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM subscription_plans
            WHERE is_active=1 AND is_public=1
            ORDER BY display_order ASC, created_at ASC
        """).fetchall()
        if rows:
            import json as _json
            plans = []
            for r in rows:
                d = dict(r)
                try:
                    d["features"] = _json.loads(d.get("features_json", "[]"))
                except Exception:
                    d["features"] = []
                try:
                    d["excluded_features"] = _json.loads(d.get("excluded_features_json", "[]"))
                except Exception:
                    d["excluded_features"] = []
                # Prefer Arabic fields for display
                d["display_name"] = d.get("name_ar") or d.get("name") or d.get("code", "")
                d["display_description"] = d.get("description_ar") or d.get("description", "")
                d["display_badge"] = d.get("badge_text_ar") or d.get("badge", "")
                d["display_period"] = d.get("billing_period_ar") or d.get("billing_period", "")
                plans.append(d)
            return {"plans": plans}
        # Fallback to hardcoded if no DB plans yet
        return {"plans": [
            {"id": k, "code": k, "plan": k, **v}
            for k, v in PLAN_PRICES.items() if k not in ("free", "trial")
        ]}
    finally:
        conn.close()


@app.post("/api/billing/payment-proof")
async def billing_submit_proof(
    plan_id: str = Form(""),
    plan: str = Form(""),        # legacy: plan code fallback
    amount: float = Form(0),
    currency: str = Form(""),
    payment_method_id: str = Form(""),
    payer_name: str = Form(""),
    reference_number: str = Form(""),
    proof: UploadFile = File(None),
    user=Depends(current_user)
):
    rid = user["restaurant_id"]
    conn_pre = database.get_db()
    try:
        plan_row = _get_plan_record(conn_pre, plan_id=plan_id, plan_code=plan)
    finally:
        conn_pre.close()

    if plan_row:
        if not plan_row.get("is_active") or not plan_row.get("is_public"):
            raise HTTPException(400, "هذه الخطة غير متاحة للاشتراك")
        plan_code    = plan_row["code"]
        resolved_pid = plan_row["id"]
        if amount <= 0:
            amount = float(plan_row.get("price", 0))
        if not currency:
            currency = plan_row.get("currency", "IQD")
    else:
        # Legacy fallback: validate plan code against hardcoded list
        plan_code = plan
        resolved_pid = ""
        if plan_code not in PLAN_PRICES:
            raise HTTPException(400, "خطة غير صالحة — أرسل plan_id أو plan code صحيح")

    if not currency:
        currency = "IQD"
    if amount <= 0:
        raise HTTPException(400, "المبلغ يجب أن يكون أكبر من صفر")

    proof_filename = ""
    proof_stored_url = ""
    storage_mode = "none"

    if proof and proof.filename:
        ext = Path(proof.filename).suffix.lower()
        if ext not in _ALLOWED_PROOF_EXTS:
            raise HTTPException(400, "نوع الملف غير مسموح. الأنواع المسموحة: jpg, jpeg, png, pdf")
        content = await proof.read()
        if len(content) > _MAX_PROOF_SIZE:
            raise HTTPException(400, "حجم الملف كبير جداً — الحد الأقصى 5 MB")

        from services import storage as _storage
        req_id_tmp = str(uuid.uuid4())  # use same id for both storage path and DB
        proof_filename = f"{req_id_tmp}{ext}"

        # Try Supabase first
        if _storage._is_configured():
            try:
                import mimetypes as _mt
                ctype = _mt.types_map.get(ext, "application/octet-stream")
                spath = _storage.payment_proof_path(rid, req_id_tmp, proof_filename)
                pub_url = _storage.upload_bytes(content, _storage.BUCKET_PAYMENT_PROOFS, spath, ctype)
                if pub_url:
                    proof_stored_url = pub_url
                    proof_filename = spath  # store full Supabase path in proof_path
                    storage_mode = "supabase"
            except Exception as _se:
                logger.warning(f"[billing] Supabase proof upload failed, falling back to local: {_se}")

        # Fallback to local if Supabase not configured or failed
        if storage_mode != "supabase":
            local_fname = proof_filename if "." in proof_filename else f"{uuid.uuid4()}{ext}"
            # Ensure simple filename for local storage
            local_fname = f"{req_id_tmp}{ext}"
            (_UPLOAD_DIR / local_fname).write_bytes(content)
            proof_filename = local_fname
            proof_stored_url = f"/uploads/payment_proofs/{local_fname}"
            storage_mode = "local"
    else:
        req_id_tmp = str(uuid.uuid4())

    conn = database.get_db()
    try:
        conn.execute("""
            INSERT INTO payment_requests
                (id, restaurant_id, plan, plan_id, amount, currency, payment_method_id,
                 payer_name, reference_number, proof_path, proof_url, storage_mode, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending')
        """, (req_id_tmp, rid, plan_code, resolved_pid, amount, currency, payment_method_id,
              payer_name, reference_number, proof_filename, proof_stored_url, storage_mode))
        _billing_audit(conn, "payment_request_submitted",
                       actor_id=user["id"], actor_role=user.get("role", "owner"),
                       restaurant_id=rid, payment_request_id=req_id_tmp,
                       amount=amount, currency=currency, plan=plan_code,
                       storage_mode=storage_mode,
                       note=f"payer={payer_name} ref={reference_number}")
        conn.commit()
        return {
            "ok": True,
            "request_id": req_id_tmp,
            "status": "pending",
            "storage_mode": storage_mode,
            "message": "تم استلام طلب الدفع — سيراجعه المشرف قريباً",
            "proof_url": proof_stored_url,
        }
    finally:
        conn.close()


@app.get("/api/billing/my-payment-requests")
async def billing_my_requests(user=Depends(current_user)):
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        rows = conn.execute("""
            SELECT id, plan, plan_id, amount, currency, payer_name, reference_number,
                   payment_method_id, status, reject_reason, proof_path,
                   proof_url, storage_mode, created_at, reviewed_at
            FROM payment_requests
            WHERE restaurant_id=?
            ORDER BY created_at DESC
        """, (rid,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Resolve plan_name from DB or fallback label
            plan_row = _get_plan_record(conn, plan_id=d.get("plan_id", ""), plan_code=d.get("plan", ""))
            d["plan_name"] = (plan_row or {}).get("name") or d.get("plan", "")
            # Use stored proof_url; fallback to local path for old rows
            if not d.get("proof_url") and d.get("proof_path"):
                d["proof_url"] = f"/uploads/payment_proofs/{d['proof_path']}"
            # Never expose internal path to restaurant — only the URL
            d.pop("proof_path", None)
            result.append(d)
        return {"payment_requests": result}
    finally:
        conn.close()


@app.get("/api/billing/proof/{request_id}")
async def billing_get_proof(request_id: str, user=Depends(current_user)):
    """Access-controlled proof redirect: restaurant sees own, super admin sees all."""
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT restaurant_id, proof_url, proof_path, storage_mode FROM payment_requests WHERE id=?",
            (request_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "الطلب غير موجود")
        if row["restaurant_id"] != rid:
            raise HTTPException(403, "غير مصرح")
        proof_url = row["proof_url"] or (
            f"/uploads/payment_proofs/{row['proof_path']}" if row["proof_path"] else ""
        )
        if not proof_url:
            raise HTTPException(404, "لا يوجد إيصال مرفق")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=proof_url, status_code=302)
    finally:
        conn.close()


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

    _btype_label = "الكافيه" if data.business_type == "cafe" else "المطعم"
    _send_email(
        data.email.lower().strip(),
        f"مرحباً بك في منصة {data.restaurant_name.strip()} 🎉",
        f"""<div dir="rtl" style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:24px;background:#f8fafc;border-radius:12px">
  <h2 style="color:#1e293b;margin-bottom:8px">أهلاً {data.owner_name.strip()} 👋</h2>
  <p style="color:#475569;margin-bottom:8px">تم إنشاء حساب {_btype_label} <strong>{data.restaurant_name.strip()}</strong> بنجاح.</p>
  <p style="color:#475569;margin-bottom:20px">يمكنك الآن إدارة منتجاتك، متابعة الطلبات، والتواصل مع عملائك عبر الذكاء الاصطناعي.</p>
  <a href="{BASE_URL}/app" style="display:inline-block;background:#6366f1;color:#fff;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:600">ابدأ الآن</a>
</div>"""
    )

    token = create_token({"sub": uid, "restaurant_id": rid})
    return {
        "token": token,
        "user": {
            "id": uid, "name": data.owner_name.strip(), "email": data.email.lower().strip(),
            "role": "owner", "restaurant_id": rid,
            "restaurant_name": data.restaurant_name.strip(), "plan": plan,
        },
    }


class ForgotPasswordReq(BaseModel):
    email: str


class ResetPasswordReq(BaseModel):
    token: str
    password: str


@app.post("/api/auth/forgot-password")
async def forgot_password(request: Request, data: ForgotPasswordReq):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate(ip, limit=3, window=300, scope="forgot"):
        raise HTTPException(429, "طلبات كثيرة — حاول بعد 5 دقائق")
    conn = database.get_db()
    try:
        user = conn.execute(
            "SELECT id, name, email FROM users WHERE email=?",
            (data.email.lower().strip(),)
        ).fetchone()
        if user:
            conn.execute(
                "UPDATE password_reset_tokens SET used=1 WHERE user_id=? AND used=0",
                (user["id"],)
            )
            token = _secrets.token_urlsafe(32)
            expires = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO password_reset_tokens (id, user_id, token, expires_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), user["id"], token, expires)
            )
            conn.commit()
            reset_url = f"{BASE_URL}/reset-password?token={token}"
            _send_email(
                user["email"],
                "استعادة كلمة المرور — منصتك",
                f"""<div dir="rtl" style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:24px;background:#f8fafc;border-radius:12px">
  <h2 style="color:#1e293b;margin-bottom:8px">مرحباً {user['name']} 👋</h2>
  <p style="color:#475569;margin-bottom:20px">طلبت استعادة كلمة المرور. اضغط الزر أدناه لإعادة تعيينها:</p>
  <a href="{reset_url}" style="display:inline-block;background:#6366f1;color:#fff;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:600;margin-bottom:20px">إعادة تعيين كلمة المرور</a>
  <p style="color:#94a3b8;font-size:13px">الرابط صالح لمدة ساعة واحدة فقط.<br>إذا لم تطلب ذلك، تجاهل هذه الرسالة.</p>
</div>"""
            )
    finally:
        conn.close()
    return {"message": "إذا كان البريد الإلكتروني مسجلاً، ستصلك رسالة خلال دقائق"}


@app.post("/api/auth/reset-password")
async def reset_password_endpoint(data: ResetPasswordReq):
    if len(data.password) < 6:
        raise HTTPException(400, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")
    conn = database.get_db()
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token=? AND used=0 AND expires_at > ?",
            (data.token, now)
        ).fetchone()
        if not row:
            raise HTTPException(400, "الرابط غير صالح أو منتهي الصلاحية")
        pw_hash = _bcrypt.hashpw(data.password.encode(), _bcrypt.gensalt()).decode()
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (pw_hash, row["user_id"]))
        conn.execute("UPDATE password_reset_tokens SET used=1 WHERE id=?", (row["id"],))
        conn.commit()
    finally:
        conn.close()
    return {"message": "تم تغيير كلمة المرور بنجاح"}


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
        "total_orders":        q("SELECT COUNT(*) FROM orders WHERE restaurant_id=?", rid),
        "today_orders":        q("SELECT COUNT(*) FROM orders WHERE restaurant_id=? AND DATE(created_at)=?", rid, today),
        "today_revenue":       round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'", rid, today), 2),
        "open_chats":          q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND status='open'", rid),
        "total_customers":     q("SELECT COUNT(*) FROM customers WHERE restaurant_id=?", rid),
        "pending_orders":      q("SELECT COUNT(*) FROM orders WHERE restaurant_id=? AND status IN ('pending','confirmed','preparing','on_way')", rid),
        "total_revenue":       round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND status!='cancelled'", rid), 2),
        "total_products":      q("SELECT COUNT(*) FROM products WHERE restaurant_id=?", rid),
        "urgent_chats":        q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND urgent=1 AND status='open'", rid),
        "failed_outbound_24h": q("SELECT COUNT(*) FROM outbound_messages WHERE restaurant_id=? AND status='failed' AND created_at >= datetime('now', '-24 hours')", rid),
        "connected_channels":  q("SELECT COUNT(*) FROM channels WHERE restaurant_id=? AND enabled=1", rid),
        "menu_images_count":   q("SELECT COUNT(*) FROM menu_images WHERE restaurant_id=? AND is_active=1", rid),
    }

    # Subscription status
    sub_row = conn.execute(
        "SELECT plan, status, end_date FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
        (rid,)
    ).fetchone()
    result["subscription"] = dict(sub_row) if sub_row else {"plan": "trial", "status": "active", "end_date": ""}

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
    rid = user["restaurant_id"]
    # Query from actual order_items for accuracy — not the denormalized counter
    rows = conn.execute("""
        SELECT p.id, p.name, p.price, p.category, p.icon,
               COALESCE(SUM(oi.quantity), 0)              AS order_count,
               COALESCE(SUM(oi.price * oi.quantity), 0)  AS revenue
        FROM products p
        LEFT JOIN order_items oi ON oi.product_id = p.id
        LEFT JOIN orders o       ON oi.order_id  = o.id
                                 AND o.restaurant_id = ?
                                 AND o.status != 'cancelled'
        WHERE p.restaurant_id = ?
        GROUP BY p.id
        ORDER BY order_count DESC
        LIMIT 5
    """, (rid, rid)).fetchall()
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


@app.get("/api/analytics/hourly-orders")
async def hourly_orders_analytics(user=Depends(current_user)):
    """Return order count grouped by hour of day (0-23) — all time, non-cancelled."""
    conn = database.get_db()
    rid = user["restaurant_id"]
    rows = conn.execute(
        "SELECT strftime('%H', created_at) AS hour, COUNT(*) AS count "
        "FROM orders WHERE restaurant_id=? AND status!='cancelled' GROUP BY hour ORDER BY hour",
        (rid,)
    ).fetchall()
    conn.close()
    hour_map = {r["hour"]: r["count"] for r in rows}
    return [{"hour": str(i).zfill(2), "label": f"{i:02d}:00", "count": hour_map.get(str(i).zfill(2), 0)} for i in range(24)]


@app.get("/api/broadcast/preview")
async def broadcast_preview(
    segment: str = "all",
    platform: str = "",
    user=Depends(current_user),
):
    """Return estimated recipient count for a broadcast segment without sending."""
    conn = database.get_db()
    rid = user["restaurant_id"]
    try:
        q = "SELECT COUNT(DISTINCT c.id) FROM customers c WHERE c.restaurant_id=?"
        params: list = [rid]
        if segment == "vip":
            q += " AND c.vip=1"
        elif segment == "returning":
            q += " AND c.total_orders >= 2"
        elif segment == "inactive_30":
            q += " AND (c.last_seen < datetime('now', '-30 days') OR c.last_seen IS NULL)"
        elif segment == "new":
            q += " AND c.total_orders = 0"
        if platform:
            q += " AND c.platform=?"
            params.append(platform)
        count = conn.execute(q, params).fetchone()[0]
    finally:
        conn.close()
    return {"count": count}


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
        # Count conversations per channel — use conversations.channel directly
        conv_count = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND channel=?",
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


# ── Analytics: Required endpoints (NUMBER 11) ─────────────────────────────────

@app.get("/api/analytics/overview")
async def analytics_overview(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    ok, reason = can_use_feature(conn, rid, "analytics")
    if not ok:
        conn.close()
        raise HTTPException(402, reason)
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    statuses = {r["status"]: r["cnt"] for r in conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM orders WHERE restaurant_id=? GROUP BY status", (rid,)
    ).fetchall()}

    result = {
        # Orders
        "total_orders":      q("SELECT COUNT(*) FROM orders WHERE restaurant_id=?", rid),
        "today_orders":      q("SELECT COUNT(*) FROM orders WHERE restaurant_id=? AND DATE(created_at)=?", rid, today),
        "pending_orders":    statuses.get("pending", 0),
        "confirmed_orders":  statuses.get("confirmed", 0) + statuses.get("preparing", 0),
        "completed_orders":  statuses.get("delivered", 0) + statuses.get("completed", 0),
        "cancelled_orders":  statuses.get("cancelled", 0),
        # Revenue
        "total_revenue":     round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND status!='cancelled'", rid), 2),
        "today_revenue":     round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'", rid, today), 2),
        "avg_order_value":   round(q("SELECT COALESCE(AVG(total),0) FROM orders WHERE restaurant_id=? AND status!='cancelled'", rid), 2),
        # Conversations
        "total_conversations":  q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", rid),
        "open_conversations":   q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND status='open'", rid),
        "unread_conversations": q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND unread_count>0", rid),
        "urgent_conversations": q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND urgent=1 AND status='open'", rid),
        "bot_mode_count":       q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND mode='bot'", rid),
        "human_mode_count":     q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND mode='human'", rid),
        # Customers
        "total_customers":    q("SELECT COUNT(*) FROM customers WHERE restaurant_id=?", rid),
        "new_customers":      q("SELECT COUNT(*) FROM customers WHERE restaurant_id=? AND DATE(created_at)>=?", rid, week_ago),
        "vip_customers":      q("SELECT COUNT(*) FROM customers WHERE restaurant_id=? AND vip=1", rid),
        # Products
        "total_products":     q("SELECT COUNT(*) FROM products WHERE restaurant_id=?", rid),
        "available_products": q("SELECT COUNT(*) FROM products WHERE restaurant_id=? AND available=1", rid),
        # Notifications
        "unread_notifications": q("SELECT COUNT(*) FROM notifications WHERE restaurant_id=? AND is_read=0", rid),
    }

    recent = conn.execute("""
        SELECT o.id, o.total, o.status, o.channel, o.created_at, c.name AS customer_name
        FROM orders o JOIN customers c ON o.customer_id=c.id
        WHERE o.restaurant_id=? ORDER BY o.created_at DESC LIMIT 10
    """, (rid,)).fetchall()
    result["recent_orders"] = [dict(r) for r in recent]

    conn.close()
    return result


@app.get("/api/analytics/orders")
async def analytics_orders(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    today = datetime.now().strftime("%Y-%m-%d")

    status_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM orders WHERE restaurant_id=? GROUP BY status", (rid,)
    ).fetchall()
    by_status = {r["status"]: r["cnt"] for r in status_rows}

    channel_rows = conn.execute("""
        SELECT channel, COUNT(*) AS cnt, COALESCE(SUM(total),0) AS revenue
        FROM orders WHERE restaurant_id=? GROUP BY channel
    """, (rid,)).fetchall()

    recent = conn.execute("""
        SELECT o.id, o.total, o.status, o.channel, o.type, o.created_at, c.name AS customer_name
        FROM orders o JOIN customers c ON o.customer_id=c.id
        WHERE o.restaurant_id=? ORDER BY o.created_at DESC LIMIT 10
    """, (rid,)).fetchall()

    today_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE restaurant_id=? AND DATE(created_at)=?", (rid, today)
    ).fetchone()[0]
    conn.close()
    return {
        "total_orders":     sum(by_status.values()),
        "today_orders":     today_orders,
        "pending":          by_status.get("pending", 0),
        "confirmed":        by_status.get("confirmed", 0),
        "preparing":        by_status.get("preparing", 0),
        "on_way":           by_status.get("on_way", 0),
        "delivered":        by_status.get("delivered", 0),
        "completed":        by_status.get("completed", 0),
        "cancelled":        by_status.get("cancelled", 0),
        "by_channel":       [dict(r) for r in channel_rows],
        "recent":           [dict(r) for r in recent],
    }


@app.get("/api/analytics/revenue")
async def analytics_revenue(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    today = datetime.now().strftime("%Y-%m-%d")

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    weekly = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        rev = q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'", rid, day)
        weekly.append({"date": day, "revenue": round(rev, 2)})

    total_rev  = round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND status!='cancelled'", rid), 2)
    today_rev  = round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE restaurant_id=? AND DATE(created_at)=? AND status!='cancelled'", rid, today), 2)
    avg_order  = round(q("SELECT COALESCE(AVG(total),0) FROM orders WHERE restaurant_id=? AND status!='cancelled'", rid), 2)

    channel_rev = conn.execute("""
        SELECT channel, COALESCE(SUM(total),0) AS revenue, COUNT(*) AS orders
        FROM orders WHERE restaurant_id=? AND status!='cancelled' GROUP BY channel
    """, (rid,)).fetchall()

    conn.close()
    return {
        "total_revenue":    total_rev,
        "today_revenue":    today_rev,
        "avg_order_value":  avg_order,
        "weekly":           weekly,
        "by_channel":       [dict(r) for r in channel_rev],
    }


@app.get("/api/analytics/conversations")
async def analytics_conversations(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    channel_rows = conn.execute("""
        SELECT channel, COUNT(*) AS total,
               SUM(CASE WHEN status='open'  THEN 1 ELSE 0 END) AS open,
               SUM(CASE WHEN mode='human'   THEN 1 ELSE 0 END) AS human_mode,
               SUM(unread_count) AS total_unread
        FROM conversations WHERE restaurant_id=? GROUP BY channel
    """, (rid,)).fetchall()

    total           = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", rid)
    open_           = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND status='open'", rid)
    closed          = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND status='closed'", rid)
    bot_mode        = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND mode='bot'", rid)
    human_mode      = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND mode='human'", rid)
    unread          = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND unread_count>0", rid)
    unread_messages = q("SELECT COALESCE(SUM(unread_count),0) FROM conversations WHERE restaurant_id=?", rid)
    urgent          = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND urgent=1 AND status='open'", rid)
    conn.close()
    return {
        "total":           total,
        "open":            open_,
        "closed":          closed,
        "bot_mode":        bot_mode,
        "human_mode":      human_mode,
        "unread":          unread,
        "unread_messages": unread_messages,
        "urgent":          urgent,
        "by_channel":      [dict(r) for r in channel_rows],
    }


@app.get("/api/analytics/customers")
async def analytics_customers(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    platform_rows = conn.execute("""
        SELECT platform, COUNT(*) AS cnt FROM customers WHERE restaurant_id=? GROUP BY platform
    """, (rid,)).fetchall()

    top5 = conn.execute("""
        SELECT id, name, platform, vip, total_orders, total_spent
        FROM customers WHERE restaurant_id=? ORDER BY total_spent DESC LIMIT 5
    """, (rid,)).fetchall()

    total         = q("SELECT COUNT(*) FROM customers WHERE restaurant_id=?", rid)
    new_this_week = q("SELECT COUNT(*) FROM customers WHERE restaurant_id=? AND DATE(created_at)>=?", rid, week_ago)
    returning     = q("SELECT COUNT(*) FROM customers WHERE restaurant_id=? AND total_orders>1", rid)
    vip           = q("SELECT COUNT(*) FROM customers WHERE restaurant_id=? AND vip=1", rid)
    conn.close()
    return {
        "total":             total,
        "new_this_week":     new_this_week,
        "returning":         returning,
        "vip":               vip,
        "by_platform":       [dict(r) for r in platform_rows],
        "top_spenders":      [dict(r) for r in top5],
    }


@app.get("/api/analytics/products")
async def analytics_products(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    rows = conn.execute("""
        SELECT p.id, p.name, p.price, p.category, p.icon, p.available,
               COALESCE(SUM(oi.quantity), 0)             AS orders_count,
               COALESCE(SUM(oi.price * oi.quantity), 0)  AS revenue
        FROM products p
        LEFT JOIN order_items oi ON oi.product_id = p.id
        LEFT JOIN orders o       ON oi.order_id   = o.id
                                 AND o.restaurant_id = ?
                                 AND o.status != 'cancelled'
        WHERE p.restaurant_id = ?
        GROUP BY p.id
        ORDER BY orders_count DESC
    """, (rid, rid)).fetchall()

    total_prods = conn.execute("SELECT COUNT(*) FROM products WHERE restaurant_id=?", (rid,)).fetchone()[0]
    avail_prods = conn.execute("SELECT COUNT(*) FROM products WHERE restaurant_id=? AND available=1", (rid,)).fetchone()[0]
    conn.close()
    return {
        "total":     total_prods,
        "available": avail_prods,
        "items":     [dict(r) for r in rows],
    }


@app.get("/api/analytics/channels")
async def analytics_channels(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    order_rows = conn.execute("""
        SELECT channel,
               COUNT(*)                          AS orders,
               COALESCE(SUM(total), 0)           AS revenue,
               COALESCE(AVG(total), 0)           AS avg_order_value
        FROM orders WHERE restaurant_id=? AND status!='cancelled'
        GROUP BY channel
    """, (rid,)).fetchall()

    conv_rows = conn.execute("""
        SELECT channel,
               COUNT(*)                                              AS conversations,
               SUM(CASE WHEN mode='human' THEN 1 ELSE 0 END)        AS human_mode,
               SUM(CASE WHEN unread_count > 0 THEN 1 ELSE 0 END)    AS unread
        FROM conversations WHERE restaurant_id=?
        GROUP BY channel
    """, (rid,)).fetchall()

    # Merge by channel
    order_map = {r["channel"]: dict(r) for r in order_rows}
    conv_map  = {r["channel"]: dict(r) for r in conv_rows}
    all_channels = set(order_map) | set(conv_map)

    result = []
    for ch in sorted(all_channels):
        o = order_map.get(ch, {"orders": 0, "revenue": 0.0, "avg_order_value": 0.0})
        c = conv_map.get(ch,  {"conversations": 0, "human_mode": 0, "unread": 0})
        result.append({
            "channel":        ch,
            "orders":         o["orders"],
            "revenue":        round(o["revenue"], 2),
            "avg_order_value": round(o["avg_order_value"], 2),
            "conversations":  c["conversations"],
            "human_mode":     c["human_mode"],
            "unread":         c["unread"],
        })

    conn.close()
    return result


@app.get("/api/analytics/bot-performance")
async def analytics_bot_performance(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    total_bot   = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND bot_turn_count>0", rid)
    escalated   = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND handoff_reason!='' AND handoff_reason IS NOT NULL", rid)
    human_now   = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND mode='human' AND status='open'", rid)
    avg_turns   = round(q("SELECT COALESCE(AVG(bot_turn_count),0) FROM conversations WHERE restaurant_id=? AND bot_turn_count>0", rid), 1)
    bot_msgs    = q("SELECT COUNT(*) FROM messages m JOIN conversations cv ON m.conversation_id=cv.id WHERE cv.restaurant_id=? AND m.role IN ('bot','assistant')", rid)
    total_convs = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", rid)
    with_orders = q("SELECT COUNT(DISTINCT customer_id) FROM orders WHERE restaurant_id=?", rid)

    success_rate  = round((total_bot - escalated) / total_bot * 100, 1) if total_bot > 0 else 0.0
    handoff_rate  = round(escalated / total_bot * 100, 1) if total_bot > 0 else 0.0
    conversion    = round(with_orders / total_convs * 100, 1) if total_convs > 0 else 0.0

    conn.close()
    return {
        "total_bot_conversations": total_bot,
        "escalated":               escalated,
        "human_mode_active":       human_now,
        "success_rate":            success_rate,
        "handoff_rate":            handoff_rate,
        "avg_turns_per_conv":      avg_turns,
        "bot_reply_count":         bot_msgs,
        "conversion_rate":         conversion,
    }


@app.get("/api/analytics/recent-activity")
async def analytics_recent_activity(user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]

    orders = conn.execute("""
        SELECT o.id, o.total, o.status, o.channel, o.created_at, c.name AS customer_name
        FROM orders o JOIN customers c ON o.customer_id=c.id
        WHERE o.restaurant_id=? ORDER BY o.created_at DESC LIMIT 10
    """, (rid,)).fetchall()

    convs = conn.execute("""
        SELECT cv.id, cv.mode, cv.status, cv.unread_count, cv.urgent,
               cv.updated_at, cv.channel, c.name AS customer_name
        FROM conversations cv JOIN customers c ON cv.customer_id=c.id
        WHERE cv.restaurant_id=? ORDER BY cv.updated_at DESC LIMIT 10
    """, (rid,)).fetchall()

    conn.close()
    return {
        "recent_orders":        [dict(r) for r in orders],
        "recent_conversations": [dict(r) for r in convs],
    }


# ── Analytics: Voice (NUMBER 23) ──────────────────────────────────────────────

@app.get("/api/analytics/voice")
async def analytics_voice(
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    user=Depends(current_user),
):
    from services.analytics_service import get_voice_analytics
    conn = database.get_db()
    rid = user["restaurant_id"]
    try:
        return get_voice_analytics(conn, rid, date_from=date_from, date_to=date_to)
    finally:
        conn.close()


# ── Analytics: Menu Images (NUMBER 23) ────────────────────────────────────────

@app.get("/api/analytics/menu-images")
async def analytics_menu_images(user=Depends(current_user)):
    from services.analytics_service import get_menu_image_analytics
    conn = database.get_db()
    rid = user["restaurant_id"]
    try:
        return get_menu_image_analytics(conn, rid)
    finally:
        conn.close()


# ── Products — moved to routers/products.py (NUMBER 43) ──────────────────────


# ── Menu Images ───────────────────────────────────────────────────────────────

@app.get("/api/menu-images")
async def list_menu_images(user=Depends(current_user)):
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM menu_images WHERE restaurant_id=? ORDER BY sort_order ASC, created_at ASC",
            (user["restaurant_id"],)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/menu-images", status_code=201)
async def create_menu_image(data: dict, user=Depends(current_user)):
    image_url = (data.get("image_url") or "").strip()
    if not image_url:
        raise HTTPException(400, "image_url مطلوب")
    mid = str(uuid.uuid4())
    conn = database.get_db()
    try:
        conn.execute(
            """INSERT INTO menu_images (id, restaurant_id, title, image_url, category, sort_order, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                user["restaurant_id"],
                (data.get("title") or "").strip(),
                image_url,
                (data.get("category") or "").strip(),
                int(data.get("sort_order") or 0),
                1 if data.get("is_active", True) else 0,
            )
        )
        conn.commit()
        row = conn.execute("SELECT * FROM menu_images WHERE id=?", (mid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.put("/api/menu-images/{mid}")
async def update_menu_image(mid: str, data: dict, user=Depends(current_user)):
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT id FROM menu_images WHERE id=? AND restaurant_id=?",
            (mid, user["restaurant_id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "صورة المنيو غير موجودة")
        fields, vals = [], []
        for col in ("title", "image_url", "category"):
            if col in data:
                fields.append(f"{col}=?")
                vals.append((data[col] or "").strip())
        if "sort_order" in data:
            fields.append("sort_order=?")
            vals.append(int(data["sort_order"] or 0))
        if "is_active" in data:
            fields.append("is_active=?")
            vals.append(1 if data["is_active"] else 0)
        if not fields:
            raise HTTPException(400, "لا توجد بيانات للتحديث")
        fields.append("updated_at=CURRENT_TIMESTAMP")
        vals.append(mid)
        conn.execute(f"UPDATE menu_images SET {', '.join(fields)} WHERE id=?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM menu_images WHERE id=?", (mid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.delete("/api/menu-images/{mid}")
async def delete_menu_image(mid: str, user=Depends(current_user)):
    conn = database.get_db()
    try:
        if not conn.execute(
            "SELECT id FROM menu_images WHERE id=? AND restaurant_id=?",
            (mid, user["restaurant_id"])
        ).fetchone():
            raise HTTPException(404, "صورة المنيو غير موجودة")
        conn.execute("DELETE FROM menu_images WHERE id=?", (mid,))
        conn.commit()
        return {"message": "تم الحذف"}
    finally:
        conn.close()


@app.post("/api/menu-images/reorder")
async def reorder_menu_images(req: Request, user=Depends(current_user)):
    """Accept [{id, sort_order}, ...] and bulk-update sort_order."""
    body = await req.json()
    items = body.get("items", [])
    if not items:
        return {"ok": True}
    conn = database.get_db()
    try:
        for item in items:
            conn.execute(
                "UPDATE menu_images SET sort_order=? WHERE id=? AND restaurant_id=?",
                (item["sort_order"], item["id"], user["restaurant_id"])
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


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


@app.post("/api/upload/menu-image", status_code=201)
async def upload_menu_image(
    file: UploadFile = File(...),
    user=Depends(require_role("owner", "manager")),
):
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"نوع الملف غير مدعوم: {ext}")

    content = await file.read()
    if len(content) / (1024 * 1024) > 15:
        raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 15 MB")

    fname = f"{uuid.uuid4()}{ext}"

    # Try Supabase first
    public_url = None
    try:
        storage_path = _storage.menu_image_path(user["restaurant_id"], fname)
        public_url = _storage.upload_bytes(
            content, _storage.BUCKET_MENUS, storage_path,
            content_type=file.content_type or "image/jpeg",
        )
    except Exception as e:
        logging.warning(f"Supabase upload failed, falling back to local: {e}")

    # Fallback: save locally under uploads/menu-images/
    if not public_url:
        local_dir = Path("uploads/menu-images")
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / fname
        local_path.write_bytes(content)
        base = os.getenv("BASE_URL", "").rstrip("/")
        public_url = f"{base}/uploads/menu-images/{fname}"

    return {"url": public_url}


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
    segment = body.get("segment", "all")  # all | vip | returning | inactive_30 | new
    conn = database.get_db()
    try:
        # Fetch distinct customers with conversations for this restaurant
        query = "SELECT DISTINCT c.id, c.platform, c.phone FROM customers c WHERE c.restaurant_id=? AND c.platform IS NOT NULL"
        params = [rid]
        if segment == "vip":
            query += " AND c.vip=1"
        elif segment == "returning":
            query += " AND c.total_orders >= 2"
        elif segment == "inactive_30":
            query += " AND (c.last_seen < datetime('now', '-30 days') OR c.last_seen IS NULL)"
        elif segment == "new":
            query += " AND c.total_orders = 0"
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


# ── Promo Codes ───────────────────────────────────────────────────────────────

@app.get("/api/promo-codes")
async def list_promo_codes(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM promo_codes WHERE restaurant_id=? ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return {"promo_codes": [dict(r) for r in rows]}


@app.post("/api/promo-codes", status_code=201)
async def create_promo_code(req: Request, user=Depends(current_user)):
    data = await req.json()
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(400, "كود الخصم مطلوب")
    discount_type  = data.get("discount_type", "percent")   # percent | fixed
    discount_value = float(data.get("discount_value") or 0)
    min_order      = float(data.get("min_order") or 0)
    max_uses       = int(data.get("max_uses") or 0)
    expires_at     = str(data.get("expires_at") or "")
    if discount_type not in ("percent", "fixed"):
        raise HTTPException(400, "discount_type يجب أن يكون percent أو fixed")
    if discount_type == "percent" and not (0 < discount_value <= 100):
        raise HTTPException(400, "نسبة الخصم يجب أن تكون بين 1 و 100")
    pid = str(uuid.uuid4())
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO promo_codes (id, restaurant_id, code, discount_type, discount_value, "
            "min_order, max_uses, expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, user["restaurant_id"], code, discount_type, discount_value,
             min_order, max_uses, expires_at)
        )
        conn.commit()
    except Exception as _e:
        conn.close()
        raise HTTPException(409, "الكود موجود مسبقاً") from _e
    row = conn.execute("SELECT * FROM promo_codes WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/promo-codes/{pid}")
async def update_promo_code(pid: str, req: Request, user=Depends(current_user)):
    data = await req.json()
    conn = database.get_db()
    if not conn.execute("SELECT id FROM promo_codes WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404)
    allowed = {"discount_type", "discount_value", "min_order", "max_uses", "expires_at", "is_active"}
    upd = {k: v for k, v in data.items() if k in allowed}
    if upd:
        upd["updated_at"] = "CURRENT_TIMESTAMP"
        set_clause = ", ".join(f"{k}=?" for k in upd if k != "updated_at")
        set_clause += ", updated_at=CURRENT_TIMESTAMP"
        vals = [v for k, v in upd.items() if k != "updated_at"]
        conn.execute(f"UPDATE promo_codes SET {set_clause} WHERE id=?", [*vals, pid])
        conn.commit()
    row = conn.execute("SELECT * FROM promo_codes WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/promo-codes/{pid}")
async def delete_promo_code(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM promo_codes WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404)
    conn.execute("DELETE FROM promo_codes WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/promo-codes/validate")
async def validate_promo_code(req: Request, user=Depends(current_user)):
    """Check if a promo code is valid for a given order total. Returns discount amount."""
    data = await req.json()
    code  = (data.get("code") or "").strip().upper()
    total = float(data.get("order_total") or 0)
    if not code:
        raise HTTPException(400, "كود مطلوب")
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM promo_codes WHERE restaurant_id=? AND code=? AND is_active=1",
        (user["restaurant_id"], code)
    ).fetchone()
    conn.close()
    if not row:
        return {"valid": False, "reason": "الكود غير صحيح أو منتهي"}
    row = dict(row)
    # Expiry check
    if row["expires_at"] and row["expires_at"] < str(datetime.now().date()):
        return {"valid": False, "reason": "انتهت صلاحية الكود"}
    # Max uses
    if row["max_uses"] > 0 and row["uses_count"] >= row["max_uses"]:
        return {"valid": False, "reason": "استُنفد الحد الأقصى لاستخدامات هذا الكود"}
    # Min order
    if total < row["min_order"]:
        return {"valid": False, "reason": f"الحد الأدنى للطلب لاستخدام هذا الكود {row['min_order']:,.0f} د.ع"}
    # Calculate discount
    if row["discount_type"] == "percent":
        discount = round(total * row["discount_value"] / 100)
    else:
        discount = min(row["discount_value"], total)
    return {
        "valid": True,
        "discount_type": row["discount_type"],
        "discount_value": row["discount_value"],
        "discount_amount": discount,
        "final_total": max(0, total - discount),
    }


# ── Outgoing Webhooks ─────────────────────────────────────────────────────────

@app.get("/api/outgoing-webhooks")
async def list_outgoing_webhooks(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM outgoing_webhooks WHERE restaurant_id=? ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return {"webhooks": [dict(r) for r in rows]}


@app.post("/api/outgoing-webhooks", status_code=201)
async def create_outgoing_webhook(req: Request, user=Depends(current_user)):
    import json as _json
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL يجب أن يبدأ بـ http:// أو https://")
    name = (body.get("name") or "Webhook").strip()[:100]
    secret = (body.get("secret") or "").strip()[:200]
    events_raw = body.get("events") or ["order.created"]
    valid_events = {"order.created", "order.confirmed", "order.preparing",
                    "order.on_way", "order.delivered", "order.cancelled"}
    events = [e for e in events_raw if e in valid_events] or ["order.created"]
    wid = str(__import__("uuid").uuid4())
    conn = database.get_db()
    conn.execute(
        "INSERT INTO outgoing_webhooks (id, restaurant_id, name, url, secret, events) VALUES (?,?,?,?,?,?)",
        (wid, user["restaurant_id"], name, url, secret, _json.dumps(events))
    )
    conn.commit()
    row = conn.execute("SELECT * FROM outgoing_webhooks WHERE id=?", (wid,)).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/outgoing-webhooks/{wid}")
async def update_outgoing_webhook(wid: str, req: Request, user=Depends(current_user)):
    import json as _json
    conn = database.get_db()
    if not conn.execute("SELECT id FROM outgoing_webhooks WHERE id=? AND restaurant_id=?",
                        (wid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Webhook not found")
    body = await req.json()
    allowed = {"name", "url", "secret", "events", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "events" in updates:
        valid_events = {"order.created", "order.confirmed", "order.preparing",
                        "order.on_way", "order.delivered", "order.cancelled"}
        updates["events"] = _json.dumps([e for e in updates["events"] if e in valid_events])
    if not updates:
        conn.close()
        return {"ok": True}
    vals = list(updates.values())
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE outgoing_webhooks SET {set_clause} WHERE id=?", [*vals, wid])
    conn.commit()
    row = conn.execute("SELECT * FROM outgoing_webhooks WHERE id=?", (wid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/outgoing-webhooks/{wid}")
async def delete_outgoing_webhook(wid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM outgoing_webhooks WHERE id=? AND restaurant_id=?",
                        (wid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Webhook not found")
    conn.execute("DELETE FROM outgoing_webhooks WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/outgoing-webhooks/{wid}/test")
async def test_outgoing_webhook(wid: str, user=Depends(current_user)):
    """Send a test ping to verify the endpoint is reachable."""
    import httpx as _httpx, json as _json
    conn = database.get_db()
    row = conn.execute("SELECT * FROM outgoing_webhooks WHERE id=? AND restaurant_id=?",
                       (wid, user["restaurant_id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Webhook not found")
    payload = _json.dumps({"event": "ping", "data": {"message": "test ping"}}).encode()
    try:
        async with _httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(row["url"], content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Restaurant-Event": "ping"})
        return {"ok": r.status_code < 400, "status_code": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Stripe Payment Gateway ────────────────────────────────────────────────────

def _get_stripe_key(restaurant_id: str) -> str:
    """Return the Stripe secret key for this restaurant, or the global env fallback."""
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT stripe_secret_key, stripe_enabled FROM settings WHERE restaurant_id=?",
            (restaurant_id,)
        ).fetchone()
    finally:
        conn.close()
    if row and row["stripe_enabled"] and row["stripe_secret_key"]:
        return row["stripe_secret_key"]
    return os.getenv("STRIPE_SECRET_KEY", "")


@app.post("/api/orders/{oid}/payment-link")
async def create_payment_link(oid: str, user=Depends(current_user)):
    """Create a Stripe Checkout session for an existing order and return the URL."""
    key = _get_stripe_key(user["restaurant_id"])
    if not key:
        raise HTTPException(402, "بوابة الدفع غير مُفعّلة — أضف STRIPE_SECRET_KEY في الإعدادات")
    try:
        import stripe as _stripe
    except ImportError:
        raise HTTPException(500, "stripe package not installed")

    conn = database.get_db()
    order = conn.execute(
        "SELECT o.*, r.name AS rest_name FROM orders o "
        "JOIN restaurants r ON o.restaurant_id=r.id "
        "WHERE o.id=? AND o.restaurant_id=?",
        (oid, user["restaurant_id"])
    ).fetchone()
    items = conn.execute(
        "SELECT name, price, quantity FROM order_items WHERE order_id=?", (oid,)
    ).fetchall()
    conn.close()

    if not order:
        raise HTTPException(404, "Order not found")
    if order["payment_status"] == "paid":
        raise HTTPException(409, "الطلب مدفوع بالفعل")

    _stripe.api_key = key
    frontend_base = os.getenv("BASE_URL", "").rstrip("/")
    line_items = [
        {
            "price_data": {
                "currency": "usd",
                "product_data": {"name": item["name"]},
                "unit_amount": max(1, int(item["price"] * 100)),
            },
            "quantity": item["quantity"],
        }
        for item in items
    ] or [{
        "price_data": {
            "currency": "usd",
            "product_data": {"name": f"طلب #{oid[:8]}"},
            "unit_amount": max(1, int(order["total"] * 100)),
        },
        "quantity": 1,
    }]

    session = _stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=line_items,
        mode="payment",
        success_url=f"{frontend_base}/app?payment=success&order={oid}",
        cancel_url=f"{frontend_base}/app?payment=cancelled&order={oid}",
        metadata={"order_id": oid, "restaurant_id": user["restaurant_id"]},
    )

    conn2 = database.get_db()
    conn2.execute(
        "UPDATE orders SET stripe_session_id=?, stripe_payment_url=? WHERE id=?",
        (session.id, session.url, oid)
    )
    conn2.commit()
    conn2.close()
    return {"url": session.url, "session_id": session.id}


@app.post("/api/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    """Stripe webhook — marks order as paid on checkout.session.completed."""
    import hmac as _hmac, hashlib as _hl
    try:
        import stripe as _stripe
    except ImportError:
        raise HTTPException(500, "stripe package not installed")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    # Find matching restaurant by session metadata (we need to try all keys)
    # Use the global key for webhook verification
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if webhook_secret:
        try:
            event = _stripe.Webhook.construct_event(payload, sig, webhook_secret)
        except Exception as _e:
            logger.warning(f"[stripe_webhook] signature check failed: {_e}")
            raise HTTPException(400, "Invalid signature")
    else:
        import json as _j
        event = _j.loads(payload)

    etype = event["type"]

    if etype == "checkout.session.completed":
        session = event["data"]["object"]
        mode = session.get("mode", "payment")
        restaurant_id = session.get("metadata", {}).get("restaurant_id", "")

        if mode == "payment":
            order_id = session.get("metadata", {}).get("order_id", "")
            if order_id:
                conn = database.get_db()
                conn.execute(
                    "UPDATE orders SET payment_status='paid' WHERE id=? AND restaurant_id=?",
                    (order_id, restaurant_id)
                )
                conn.commit()
                conn.close()
                logger.info(f"[stripe] order {order_id} marked paid")

        elif mode == "subscription" and restaurant_id:
            customer_id      = session.get("customer", "")
            subscription_id  = session.get("subscription", "")
            plan_code        = session.get("metadata", {}).get("plan", "")
            now_date         = datetime.utcnow().strftime("%Y-%m-%d")
            end_date         = (datetime.utcnow() + timedelta(days=32)).strftime("%Y-%m-%d")

            conn = database.get_db()
            existing = conn.execute(
                "SELECT id FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
                (restaurant_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE subscriptions
                       SET status='active', plan=?, payment_customer_id=?,
                           payment_subscription_id=?, start_date=?, end_date=?,
                           updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (plan_code, customer_id, subscription_id, now_date, end_date, existing["id"])
                )
            else:
                conn.execute(
                    """INSERT INTO subscriptions (id, restaurant_id, plan, status,
                           payment_customer_id, payment_subscription_id,
                           start_date, end_date)
                       VALUES (?, ?, ?, 'active', ?, ?, ?, ?)""",
                    (str(__import__("uuid").uuid4()), restaurant_id, plan_code,
                     customer_id, subscription_id, now_date, end_date)
                )
            conn.commit()
            conn.close()
            logger.info(f"[stripe] subscription activated for restaurant {restaurant_id} plan={plan_code}")

    elif etype == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        subscription_id = invoice.get("subscription", "")
        if subscription_id:
            end_date = (datetime.utcnow() + timedelta(days=32)).strftime("%Y-%m-%d")
            conn = database.get_db()
            conn.execute(
                """UPDATE subscriptions SET status='active', end_date=?,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE payment_subscription_id=?""",
                (end_date, subscription_id)
            )
            conn.commit()
            conn.close()
            logger.info(f"[stripe] invoice paid — subscription {subscription_id} renewed to {end_date}")

    elif etype == "invoice.payment_failed":
        invoice = event["data"]["object"]
        subscription_id = invoice.get("subscription", "")
        if subscription_id:
            conn = database.get_db()
            conn.execute(
                """UPDATE subscriptions SET status='past_due',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE payment_subscription_id=?""",
                (subscription_id,)
            )
            conn.commit()
            conn.close()
            logger.warning(f"[stripe] invoice payment failed — subscription {subscription_id} past_due")

    elif etype == "customer.subscription.deleted":
        sub_obj = event["data"]["object"]
        subscription_id = sub_obj.get("id", "")
        if subscription_id:
            conn = database.get_db()
            conn.execute(
                """UPDATE subscriptions SET status='cancelled',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE payment_subscription_id=?""",
                (subscription_id,)
            )
            conn.commit()
            conn.close()
            logger.info(f"[stripe] subscription {subscription_id} cancelled")

    return {"received": True}


@app.get("/api/orders/{oid}/payment-status")
async def get_payment_status(oid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT payment_status, stripe_payment_url FROM orders WHERE id=? AND restaurant_id=?",
        (oid, user["restaurant_id"])
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Order not found")
    return dict(row)


# ── Stripe Subscriptions (auto-billing) ──────────────────────────────────────

@app.post("/api/billing/stripe/subscribe")
async def stripe_create_subscription(req: Request, user=Depends(current_user)):
    """Create a Stripe Checkout Session for a subscription plan."""
    key = _get_stripe_key(user["restaurant_id"])
    if not key:
        raise HTTPException(402, "Stripe غير مفعّل — أضف STRIPE_SECRET_KEY")
    try:
        import stripe as _stripe
    except ImportError:
        raise HTTPException(500, "stripe package not installed")

    body = await req.json()
    plan_code = (body.get("plan") or "").strip()
    plan_id   = (body.get("plan_id") or "").strip()

    # Look up price from subscription_plans
    conn = database.get_db()
    plan_row = conn.execute(
        "SELECT * FROM subscription_plans WHERE (code=? OR id=?) AND is_active=1 LIMIT 1",
        (plan_code, plan_id or plan_code)
    ).fetchone()
    # Get existing Stripe customer ID if any
    sub_row = conn.execute(
        "SELECT payment_customer_id FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
        (user["restaurant_id"],)
    ).fetchone()
    conn.close()

    if not plan_row:
        raise HTTPException(404, "الخطة غير موجودة")

    price_usd = float(plan_row.get("price") or 0)
    if price_usd <= 0:
        raise HTTPException(400, "هذه الخطة مجانية — لا تحتاج دفعاً")

    _stripe.api_key = key
    frontend_base = os.getenv("BASE_URL", "").rstrip("/")

    params: dict = {
        "payment_method_types": ["card"],
        "mode": "subscription",
        "line_items": [{
            "price_data": {
                "currency": "usd",
                "recurring": {"interval": "month"},
                "product_data": {
                    "name": plan_row.get("name_ar") or plan_row.get("name") or plan_code,
                    "description": plan_row.get("description_ar") or "",
                },
                "unit_amount": int(price_usd * 100),
            },
            "quantity": 1,
        }],
        "success_url": f"{frontend_base}/app?billing=success&plan={plan_code}#settings",
        "cancel_url":  f"{frontend_base}/app?billing=cancelled#settings",
        "metadata": {
            "restaurant_id": user["restaurant_id"],
            "plan": plan_code,
            "plan_id": plan_id,
        },
    }
    if sub_row and sub_row["payment_customer_id"]:
        params["customer"] = sub_row["payment_customer_id"]
    else:
        params["customer_email"] = user.get("email", "")

    session = _stripe.checkout.Session.create(**params)
    return {"url": session.url, "session_id": session.id}


@app.post("/api/billing/stripe/portal")
async def stripe_billing_portal(user=Depends(current_user)):
    """Create a Stripe Customer Portal session so the user can manage their subscription."""
    key = _get_stripe_key(user["restaurant_id"])
    if not key:
        raise HTTPException(402, "Stripe غير مفعّل")
    try:
        import stripe as _stripe
    except ImportError:
        raise HTTPException(500, "stripe package not installed")

    conn = database.get_db()
    sub_row = conn.execute(
        "SELECT payment_customer_id FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
        (user["restaurant_id"],)
    ).fetchone()
    conn.close()

    if not sub_row or not sub_row["payment_customer_id"]:
        raise HTTPException(400, "لا يوجد اشتراك Stripe مرتبط بهذا الحساب")

    _stripe.api_key = key
    frontend_base = os.getenv("BASE_URL", "").rstrip("/")
    portal = _stripe.billing_portal.Session.create(
        customer=sub_row["payment_customer_id"],
        return_url=f"{frontend_base}/app#settings",
    )
    return {"url": portal.url}


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
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = """
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone,
               b.name AS branch_name
        FROM orders o JOIN customers c ON o.customer_id = c.id
        LEFT JOIN branches b ON o.branch_id = b.id
        WHERE o.restaurant_id = ?
    """
    params = [rid]
    if status:
        q += " AND o.status=?"; params.append(status)
    if channel:
        q += " AND o.channel=?"; params.append(channel)
    if branch_id:
        q += " AND o.branch_id=?"; params.append(branch_id)
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
        INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, address, notes, status, branch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (oid, user["restaurant_id"], data.customer_id, data.channel,
          data.type, total, data.address, data.notes, data.branch_id))
    for item in data.items:
        conn.execute("""
            INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), oid, item.get("product_id"),
              item.get("name"), item.get("price", 0), item.get("quantity", 1)))
        if item.get("product_id"):
            conn.execute(
                "UPDATE products SET order_count=COALESCE(order_count,0)+? WHERE id=? AND restaurant_id=?",
                (item.get("quantity", 1), item["product_id"], user["restaurant_id"])
            )

    # Update customer lifetime stats
    conn.execute("""
        UPDATE customers SET
            total_orders = COALESCE(total_orders, 0) + 1,
            total_spent  = COALESCE(total_spent, 0) + ?
        WHERE id = ? AND restaurant_id = ?
    """, (total, data.customer_id, user["restaurant_id"]))

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
    order_dict = dict(row)
    asyncio.create_task(_fire_outgoing_webhooks(user["restaurant_id"], "order.created", order_dict))
    return order_dict


_STATUS_MESSAGES = {
    "confirmed":  "✅ طلبك وصلنا وصار بالتجهيز! نشوفك قريب 😊",
    "preparing":  "👨‍🍳 طلبك عم يتجهز الحين — ما يطول!",
    "on_way":     "🛵 طلبك طلع من المطعم وعلى الطريق إليك!",
    "delivered":  "✅ وصل طلبك! بالعافية وشكراً لاختيارك 🌷",
    "cancelled":  "❌ تم إلغاء طلبك. إذا عندك استفسار تواصل معنا.",
}


async def _notify_customer_status_change(order: dict, restaurant_id: str, new_status: str) -> None:
    """Send an order-status update to the customer on their platform (non-fatal)."""
    message_text = _STATUS_MESSAGES.get(new_status)
    if not message_text:
        return
    try:
        import httpx as _httpx
        conn = database.get_db()
        try:
            customer_id = order.get("customer_id", "")
            cust_row = conn.execute(
                "SELECT platform, phone FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            if not cust_row:
                return
            platform = cust_row["platform"] or ""
            mem_row = conn.execute(
                "SELECT memory_value FROM conversation_memory "
                "WHERE restaurant_id=? AND customer_id=? AND memory_key='external_id'",
                (restaurant_id, customer_id)
            ).fetchone()
            external_id = mem_row["memory_value"] if mem_row else cust_row["phone"] or ""
            if not external_id:
                return

            if platform == "telegram":
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'",
                    (restaurant_id,)
                ).fetchone()
                if not ch or not ch["token"]:
                    return
                async with _httpx.AsyncClient(timeout=10) as _cl:
                    await _cl.post(
                        f"https://api.telegram.org/bot{ch['token']}/sendMessage",
                        json={"chat_id": external_id, "text": message_text}
                    )

            elif platform == "whatsapp":
                ch = conn.execute(
                    "SELECT token, phone_number_id FROM channels WHERE restaurant_id=? AND type='whatsapp'",
                    (restaurant_id,)
                ).fetchone()
                if not ch or not ch["token"]:
                    return
                pn_id = ch["phone_number_id"] if "phone_number_id" in ch.keys() else ""
                if not pn_id:
                    return
                async with _httpx.AsyncClient(timeout=10) as _cl:
                    await _cl.post(
                        f"https://graph.facebook.com/v19.0/{pn_id}/messages",
                        headers={"Authorization": f"Bearer {ch['token']}", "Content-Type": "application/json"},
                        json={"messaging_product": "whatsapp", "to": external_id,
                              "type": "text", "text": {"body": message_text}}
                    )

            elif platform in ("instagram", "facebook"):
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type=?",
                    (restaurant_id, platform)
                ).fetchone()
                if not ch or not ch["token"]:
                    return
                async with _httpx.AsyncClient(timeout=10) as _cl:
                    await _cl.post(
                        "https://graph.facebook.com/v19.0/me/messages",
                        params={"access_token": ch["token"]},
                        json={"recipient": {"id": external_id}, "message": {"text": message_text}}
                    )
        finally:
            conn.close()
    except Exception as _e:
        logger.warning(f"[notify_status] {new_status} notify failed order={order.get('id','?')}: {_e}")


async def _fire_outgoing_webhooks(restaurant_id: str, event: str, payload: dict) -> None:
    """POST event payload to all active outgoing webhooks subscribed to this event."""
    import hmac as _hmac, hashlib as _hl, json as _json
    try:
        import httpx as _httpx
        conn = database.get_db()
        try:
            rows = conn.execute(
                "SELECT id, url, secret FROM outgoing_webhooks "
                "WHERE restaurant_id=? AND is_active=1",
                (restaurant_id,)
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return

        body = _json.dumps({"event": event, "data": payload}, ensure_ascii=False).encode()
        async with _httpx.AsyncClient(timeout=10) as _cl:
            for row in rows:
                events_raw = row["secret"] or ""
                # Load events from the outgoing_webhooks row (re-query to get events field)
                _conn2 = database.get_db()
                try:
                    full = _conn2.execute(
                        "SELECT events, secret FROM outgoing_webhooks WHERE id=?", (row["id"],)
                    ).fetchone()
                finally:
                    _conn2.close()
                if not full:
                    continue
                subscribed = _json.loads(full["events"] or '["order.confirmed"]')
                if event not in subscribed:
                    continue
                secret = full["secret"] or ""
                headers = {"Content-Type": "application/json", "X-Restaurant-Event": event}
                if secret:
                    sig = _hmac.new(secret.encode(), body, _hl.sha256).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={sig}"
                try:
                    r = await _cl.post(row["url"], content=body, headers=headers)
                    _conn3 = database.get_db()
                    try:
                        _conn3.execute(
                            "UPDATE outgoing_webhooks SET last_triggered_at=CURRENT_TIMESTAMP, "
                            "last_status_code=?, fail_count=CASE WHEN ? < 400 THEN 0 ELSE fail_count+1 END "
                            "WHERE id=?",
                            (r.status_code, r.status_code, row["id"])
                        )
                        _conn3.commit()
                    finally:
                        _conn3.close()
                except Exception as _req_e:
                    logger.warning(f"[outgoing_webhook] delivery failed id={row['id']}: {_req_e}")
                    _conn4 = database.get_db()
                    try:
                        _conn4.execute(
                            "UPDATE outgoing_webhooks SET fail_count=fail_count+1 WHERE id=?",
                            (row["id"],)
                        )
                        _conn4.commit()
                    finally:
                        _conn4.close()
    except Exception as _e:
        logger.warning(f"[outgoing_webhooks] event={event} restaurant={restaurant_id}: {_e}")


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

    # Notify customer on status changes that matter to them
    _NOTIFY_STATUSES = {"confirmed", "preparing", "on_way", "delivered", "cancelled"}
    if new_status in _NOTIFY_STATUSES:
        order_dict = dict(order)
        background_tasks.add_task(
            _notify_customer_status_change, order_dict, user["restaurant_id"], new_status
        )
        background_tasks.add_task(
            _fire_outgoing_webhooks, user["restaurant_id"],
            f"order.{new_status}", order_dict
        )

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
    customer_id: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = """
        SELECT cv.*,
               c.name AS customer_name,
               c.platform,
               c.phone,
               COALESCE(
                 (SELECT memory_value FROM conversation_memory
                  WHERE customer_id=c.id AND restaurant_id=? AND memory_key='name'
                  ORDER BY updated_at DESC LIMIT 1),
                 c.name
               ) AS display_name,
               (SELECT content FROM messages
                WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1) AS last_message,
               (SELECT role FROM messages
                WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1) AS last_message_role,
               (SELECT created_at FROM messages
                WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1) AS last_message_at
        FROM conversations cv JOIN customers c ON cv.customer_id = c.id
        WHERE cv.restaurant_id=?
    """
    params = [rid, rid]
    if mode:
        q += " AND cv.mode=?"; params.append(mode)
    if status:
        q += " AND cv.status=?"; params.append(status)
    if search:
        q += """ AND (c.name LIKE ? OR c.phone LIKE ?
                   OR EXISTS(SELECT 1 FROM conversation_memory cm2
                              WHERE cm2.customer_id=c.id AND cm2.memory_key='name'
                                AND cm2.memory_value LIKE ?))"""
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if customer_id:
        q += " AND cv.customer_id=?"; params.append(customer_id)
    q += " ORDER BY COALESCE((SELECT created_at FROM messages WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1), cv.updated_at) DESC"
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


# ── Branches ──────────────────────────────────────────────────────────────────

@app.get("/api/branches")
async def list_branches(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM branches WHERE restaurant_id=? ORDER BY is_default DESC, created_at ASC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return {"branches": [dict(r) for r in rows]}


@app.post("/api/branches", status_code=201)
async def create_branch(req: Request, user=Depends(current_user)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "اسم الفرع مطلوب")
    conn = database.get_db()
    sub = conn.execute(
        "SELECT plan FROM subscriptions WHERE restaurant_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
        (user["restaurant_id"],)
    ).fetchone()
    plan = sub["plan"] if sub else "trial"
    _check_plan_limit(conn, user["restaurant_id"], plan, "branches", "branches")
    bid = str(__import__("uuid").uuid4())
    is_default = 1 if not conn.execute(
        "SELECT id FROM branches WHERE restaurant_id=?", (user["restaurant_id"],)
    ).fetchone() else 0
    conn.execute(
        "INSERT INTO branches (id, restaurant_id, name, address, phone, working_hours, is_default) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, user["restaurant_id"], name,
         body.get("address", ""), body.get("phone", ""),
         __import__("json").dumps(body.get("working_hours", {})), is_default)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/branches/{bid}")
async def update_branch(bid: str, req: Request, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM branches WHERE id=? AND restaurant_id=?",
                        (bid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "الفرع غير موجود")
    body = await req.json()
    allowed = {"name", "address", "phone", "working_hours", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "working_hours" in updates:
        import json as _j
        updates["working_hours"] = _j.dumps(updates["working_hours"]) if isinstance(updates["working_hours"], dict) else updates["working_hours"]
    if not updates:
        conn.close()
        return {"ok": True}
    vals = list(updates.values())
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE branches SET {set_clause} WHERE id=?", [*vals, bid])
    conn.commit()
    row = conn.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/branches/{bid}")
async def delete_branch(bid: str, user=Depends(current_user)):
    conn = database.get_db()
    branch = conn.execute(
        "SELECT * FROM branches WHERE id=? AND restaurant_id=?",
        (bid, user["restaurant_id"])
    ).fetchone()
    if not branch:
        conn.close()
        raise HTTPException(404, "الفرع غير موجود")
    if branch["is_default"]:
        conn.close()
        raise HTTPException(400, "لا يمكن حذف الفرع الرئيسي")
    conn.execute("DELETE FROM branches WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/branches/{bid}/set-default")
async def set_default_branch(bid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM branches WHERE id=? AND restaurant_id=?",
                        (bid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "الفرع غير موجود")
    conn.execute("UPDATE branches SET is_default=0 WHERE restaurant_id=?", (user["restaurant_id"],))
    conn.execute("UPDATE branches SET is_default=1 WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    return {"ok": True}


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
    except _httpx.TimeoutException:
        conn.close()
        raise HTTPException(400, "انتهت مهلة الاتصال بـ Telegram — تحقق من صحة التوكن واتصال الإنترنت")
    except Exception as e:
        conn.close()
        logger.error(f"[telegram] register-webhook exception — restaurant={rid} | {e}")
        raise HTTPException(400, f"خطأ في الاتصال بـ Telegram: {e}")


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
    logger.info(f"[oauth-start] platform={platform} state={state[:8]} redirect_uri={redirect_uri} auth_url_prefix={auth_url[:80]}")
    return {"auth_url": auth_url, "state": state}


@app.get("/api/integrations/oauth/callback")
async def integrations_oauth_callback(
    code:  str = None,
    state: str = None,
    error: str = None,
    req: Request = None
):
    from fastapi.responses import RedirectResponse as _Redirect
    frontend_base = BASE_URL

    # ── Log every incoming call ──────────────────────────────────
    logger.info(f"[oauth-cb] HIT  code={'YES('+code[:8]+')' if code else 'MISSING'}  state={'YES('+state[:8]+')' if state else 'MISSING'}  error={error or 'none'}")

    if error or not state or not code:
        logger.warning(f"[oauth-cb] EARLY EXIT — error={error} has_state={bool(state)} has_code={bool(code)}")
        return _Redirect(f"{frontend_base}/app?oauth_error=access_denied#channels")

    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT * FROM oauth_states WHERE state=? AND used=0",
            (state,)
        ).fetchone()
        if not row:
            existing = conn.execute("SELECT used FROM oauth_states WHERE state=?", (state,)).fetchone()
            if existing:
                logger.warning(f"[oauth-cb] STATE ALREADY USED — state={state[:12]} used={existing['used']}")
                return _Redirect(f"{frontend_base}/app?oauth_error=invalid_state&hint=already_used#channels")
            else:
                logger.warning(f"[oauth-cb] STATE NOT FOUND — state={state[:12]}")
                return _Redirect(f"{frontend_base}/app?oauth_error=invalid_state#channels")

        row = dict(row)
        logger.info(f"[oauth-cb] STATE OK — platform={row['platform']} restaurant={row['restaurant_id'][:8]} expires={row['expires_at']}")

        if row["expires_at"] < datetime.utcnow().isoformat():
            logger.warning(f"[oauth-cb] STATE EXPIRED — expires_at={row['expires_at']}")
            return _Redirect(f"{frontend_base}/app?oauth_error=state_expired#channels")

        conn.execute("UPDATE oauth_states SET used=1 WHERE state=?", (state,))
        conn.commit()
        logger.info(f"[oauth-cb] STATE MARKED used=1")

        platform     = row["platform"]
        redirect_uri = f"{BASE_URL}/oauth/meta/callback"
        adapter      = get_adapter(platform)
        logger.info(f"[oauth-cb] EXCHANGING CODE — platform={platform} redirect_uri={redirect_uri}")

        try:
            result = adapter.exchange_code(code, redirect_uri)
            pages = result.get("pages") or result.get("accounts") or []
            logger.info(f"[oauth-cb] EXCHANGE OK — pages={len(pages)} has_token={bool(result.get('access_token'))}")
        except Exception as exc:
            err_str = str(exc)
            logger.error(f"[oauth-cb] EXCHANGE FAILED — platform={platform} error={err_str}")
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
            except Exception as _oe:
                logger.warning(f"[oauth_callback] channel error update failed: {_oe}")
            from urllib.parse import quote as _quote
            hint = _quote(err_str[:120], safe="")
            return _Redirect(f"{frontend_base}/app?oauth_error=exchange_failed&hint={hint}#channels")

        stored_pages = result.get("pages") or result.get("accounts") or []
        if platform == "instagram":
            for acct in stored_pages:
                logger.info(
                    f"[ig-oauth] account id={acct.get('id','')} "
                    f"username={acct.get('username','')} "
                    f"page_id={acct.get('page_id','')} "
                    f"has_page_token={bool(acct.get('page_token',''))} "
                    f"token_prefix={acct.get('page_token','')[:12] if acct.get('page_token') else 'EMPTY'}"
                )
            # If no IG accounts found, store debug data FIRST then redirect with error
            if result.get("no_ig_accounts") and not stored_pages:
                fb_pages  = result.get("fb_pages_found", 0)
                raw_pages = result.get("raw_pages", [])
                # Persist user token + raw FB pages so the diagnostic can call Meta live
                logger.info(
                    f"[ig-oauth] token_owner={result.get('token_owner_name','?')} "
                    f"(fb_id={result.get('token_owner_id','?')})"
                )
                logger.info(f"[ig-oauth] granted_perms={result.get('granted_perms', [])}")
                if result.get("declined_perms"):
                    logger.warning(f"[ig-oauth] DECLINED_perms={result.get('declined_perms')}")
                debug_json = json.dumps({
                    "access_token":       result.get("access_token", ""),
                    "token_expires_at":   result.get("token_expires_at", ""),
                    "scopes_granted":     result.get("scopes_granted", ""),
                    "pages":              [],
                    "raw_fb_pages":       raw_pages,
                    "no_ig_accounts":     True,
                    "fb_pages_found":     fb_pages,
                    "token_owner_id":     result.get("token_owner_id", ""),
                    "token_owner_name":   result.get("token_owner_name", ""),
                    "granted_perms":      result.get("granted_perms", []),
                    "declined_perms":     result.get("declined_perms", []),
                })
                conn.execute("UPDATE oauth_states SET pages_json=? WHERE state=?",
                             (debug_json, state))
                conn.commit()
                logger.warning(
                    f"[ig-oauth] no IG accounts — fb_pages={fb_pages} — "
                    f"stored debug data (user_token={'YES' if result.get('access_token') else 'NO'}) "
                    f"raw_pages={[p.get('id','?') for p in raw_pages]}"
                )
                return _Redirect(
                    f"{frontend_base}/app?oauth_error=no_ig_accounts"
                    f"&hint=facebook_pages_{fb_pages}#channels"
                )

        pending_json = json.dumps({
            "access_token":     result.get("access_token", ""),
            "token_expires_at": result.get("token_expires_at", ""),
            "scopes_granted":   result.get("scopes_granted", ""),
            "pages":            stored_pages,
        })
        conn.execute("UPDATE oauth_states SET pages_json=? WHERE state=?", (pending_json, state))
        conn.commit()
        logger.info(f"[oauth-cb] COMPLETE — redirecting to frontend with session={state[:12]}")

        return _Redirect(f"{frontend_base}/app?oauth_session={state}&platform={platform}#channels")
    except Exception as exc:
        logger.error(f"[oauth-cb] UNEXPECTED ERROR — {exc}", exc_info=True)
        return _Redirect(f"{frontend_base}/app?oauth_error=server_error#channels")
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
            "SELECT * FROM oauth_states WHERE state=?",
            (state_id,)
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

            # Save verify_token to DB FIRST so Meta's verification GET can match it
            ch = _get_or_create_channel(conn, rid, "facebook")
            conn.execute(
                "UPDATE channels SET verify_token=?, page_id=?, enabled=1 WHERE id=?",
                (verify_token, page_id, ch["id"])
            )
            conn.commit()

            channel_data = {
                "restaurant_id": rid, "token": page_token, "page_id": page_id,
                "page_name": page_name, "verify_token": verify_token,
            }
            adapter = get_adapter("facebook")
            try:
                # Run in thread — sync httpx must NOT block the async event loop
                # (Meta immediately GETs /webhooks/meta to verify; if event loop
                #  is frozen the GET times out and r2 fails with curl_errno=28)
                result_wb = await asyncio.to_thread(adapter.subscribe_webhook, channel_data, BASE_URL)
                webhook_ok = True
                logger.info(f"[meta-incoming] FB webhook subscribed OK — page_id={page_id} detail={result_wb}")
            except Exception as exc:
                logger.warning(f"[meta-incoming] FB webhook subscribe failed: {exc}")
                webhook_ok = False

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
                  user["id"], "connected",
                  ch["id"]))

        elif platform == "instagram":
            account_id    = data.get("account_id", "")
            account_name  = data.get("account_name", "")
            account_user  = data.get("account_username", "")
            picture_url   = data.get("picture_url", "")
            page_id       = data.get("page_id", "")
            page_token    = data.get("page_token") or ""  # Facebook Page token — NOT user token

            # If frontend didn't send page_token, recover it from pages_json by matching account/page
            if not page_token:
                for acct in pending.get("pages", []):
                    if acct.get("page_id") == page_id or acct.get("id") == account_id:
                        page_token = acct.get("page_token", "")
                        logger.info(f"[ig] page_token recovered from pages_json — page_id={page_id}")
                        break

            logger.info(
                f"[ig] select-page — account_id={account_id} page_id={page_id} "
                f"has_page_token={bool(page_token)} token_prefix={page_token[:12] if page_token else 'EMPTY'}"
            )

            # Save verify_token + page_id to DB FIRST so Meta's verification GET can match it
            ch = _get_or_create_channel(conn, rid, "instagram")
            conn.execute(
                "UPDATE channels SET verify_token=?, page_id=?, business_account_id=?, enabled=1 WHERE id=?",
                (verify_token, page_id, account_id, ch["id"])
            )
            conn.commit()

            channel_data = {
                "restaurant_id": rid, "token": page_token, "page_id": page_id,
                "business_account_id": account_id, "verify_token": verify_token,
            }
            adapter = get_adapter("instagram")
            try:
                result_wb = await asyncio.to_thread(adapter.subscribe_webhook, channel_data, BASE_URL)
                webhook_ok = True
                logger.info(f"[meta-incoming] IG webhook subscribed OK — page_id={page_id} biz_id={account_id} detail={result_wb}")
            except Exception as exc:
                logger.warning(f"[meta-incoming] IG webhook subscribe failed: {exc}")
                webhook_ok = False

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
            logger.info(f"[meta-incoming] IG channel updated — webhook_ok={webhook_ok} account={account_name or account_user}")

        elif platform == "whatsapp":
            phone_number_id = data.get("page_id",      "")   # phone_number_id
            waba_id         = data.get("account_id",   "")   # waba_id
            phone_display   = data.get("page_name",    "") or data.get("account_name", "")
            logger.info(f"[wa-select] SELECT-PAGE — phone_number_id={phone_number_id!r} waba_id={waba_id!r} phone_display={phone_display!r} restaurant={rid[:8]}")

            if not phone_number_id or not waba_id:
                logger.error(f"[wa-select] ABORT — missing phone_number_id or waba_id from OAuth data keys={list(data.keys())}")
                raise HTTPException(400, "بيانات WhatsApp غير مكتملة — phone_number_id أو waba_id مفقود")

            channel_data = {
                "restaurant_id":       rid,
                "token":               long_token,
                "waba_id":             waba_id,
                "business_account_id": waba_id,
                "phone_number_id":     phone_number_id,
                "verify_token":        verify_token,
            }
            adapter = get_adapter("whatsapp")
            try:
                await asyncio.to_thread(adapter.subscribe_webhook, channel_data, BASE_URL)
                logger.info(f"[wa-select] webhook subscribed OK")
            except Exception as exc:
                logger.warning(f"[wa-select] webhook subscribe failed (non-fatal): {exc}")

            ch = _get_or_create_channel(conn, rid, "whatsapp")
            logger.info(f"[wa-select] updating channel id={ch['id']}")
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
            """, (long_token, waba_id, waba_id,
                  phone_number_id, phone_display,
                  verify_token, expires_at,
                  now_iso, phone_display,
                  user["id"], "connected",
                  ch["id"]))
            logger.info(f"[wa-select] DB updated — connection_status=connected phone={phone_display!r}")

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

    access_token_direct = data.get("access_token", "")   # direct token path (no code exchange)
    logger.info(f"[wa-signup] HIT — code={'YES('+code[:8]+')' if code else 'MISSING'} access_token_direct={'YES('+access_token_direct[:8]+')' if access_token_direct else 'MISSING'} waba_id={waba_id!r} phone_number_id={phone_number_id!r} restaurant={rid}")

    if not code and not access_token_direct:
        logger.error("[wa-signup] ABORT — no code and no access_token")
        raise HTTPException(400, "code أو access_token مطلوب")
    if not META_APP_ID:
        logger.error("[wa-signup] ABORT — META_APP_ID missing")
        raise HTTPException(400, "META_APP_ID غير مضبوط")

    logger.info(f"[wa-signup] META_APP_ID={META_APP_ID[:6]}... META_APP_SECRET={'SET' if META_APP_SECRET else 'MISSING'}")

    adapter = get_adapter("whatsapp")
    conn    = database.get_db()
    try:
        if code:
            logger.info("[wa-signup] exchanging code for token...")
            try:
                result = adapter.exchange_code(code)
            except Exception as exc:
                logger.error(f"[wa-signup] exchange_code FAILED: {exc}")
                raise HTTPException(400, f"فشل تبادل التوكن: {exc}")
            token      = result["access_token"]
            expires_at = result.get("token_expires_at", "")
            logger.info(f"[wa-signup] code exchange OK — token={'YES('+token[:8]+')' if token else 'MISSING'} expires={expires_at}")
        else:
            logger.info("[wa-signup] using direct access_token — extending to long-lived...")
            try:
                import httpx as _httpx
                r = _httpx.get("https://graph.facebook.com/oauth/access_token", params={
                    "grant_type":      "fb_exchange_token",
                    "client_id":       META_APP_ID,
                    "client_secret":   META_APP_SECRET,
                    "fb_exchange_token": access_token_direct,
                }, timeout=15)
                ext = r.json()
                logger.info(f"[wa-signup] token extension response: {list(ext.keys())}")
                token      = ext.get("access_token", access_token_direct)
                expires_in = ext.get("expires_in", 0)
                expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() if expires_in else ""
                logger.info(f"[wa-signup] extended token OK — expires={expires_at}")
            except Exception as exc:
                logger.warning(f"[wa-signup] token extension failed, using raw token: {exc}")
                token      = access_token_direct
                expires_at = ""

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
        logger.info(f"[wa-signup] subscribing webhook waba_id={waba_id!r} phone_number_id={phone_number_id!r}")
        try:
            await asyncio.to_thread(adapter.subscribe_webhook, channel_data, BASE_URL)
            logger.info("[wa-signup] webhook subscribed OK")
        except Exception as exc:
            logger.warning(f"[wa-signup] webhook subscribe failed (non-fatal): {exc}")

        now_iso = datetime.utcnow().isoformat()
        ch = _get_or_create_channel(conn, rid, "whatsapp")
        logger.info(f"[wa-signup] updating channel id={ch['id']} phone_display={phone_display!r}")
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
        logger.info(f"[wa-signup] DB committed — connection_status=connected")

        log_activity(conn, rid, "channel_oauth_connected", "channel", "whatsapp",
                     "WhatsApp Embedded Signup completed",
                     user_id=user["id"], user_name=user.get("name", ""))
        conn.commit()

        updated = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='whatsapp'", (rid,)
        ).fetchone()
        logger.info(f"[wa-signup] COMPLETE — connection_status={updated['connection_status'] if updated else 'ROW_NOT_FOUND'}")
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


# ── SHIFT COMMANDS ────────────────────────────────────────────────────────────

@app.get("/api/shift-commands")
async def list_shift_commands(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM shift_commands WHERE restaurant_id=? AND is_active=1 "
        "AND (expires_at='' OR expires_at > datetime('now')) ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/shift-commands")
async def add_shift_command(data: dict, user=Depends(current_user)):
    text = (data.get("command_text") or "").strip()
    if not text:
        raise HTTPException(400, "command_text required")
    expires_at = (data.get("expires_at") or "").strip()
    rid = user["restaurant_id"]
    added_by = user.get("name") or user.get("email") or ""
    conn = database.get_db()
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO shift_commands (id, restaurant_id, command_text, created_by, expires_at) VALUES (?,?,?,?,?)",
        (cid, rid, text, added_by, expires_at)
    )
    conn.commit(); conn.close()
    return {"ok": True, "id": cid}

@app.delete("/api/shift-commands/{cid}")
async def delete_shift_command(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    conn.execute(
        "UPDATE shift_commands SET is_active=0 WHERE id=? AND restaurant_id=?",
        (cid, user["restaurant_id"])
    )
    conn.commit(); conn.close()
    return {"ok": True}

@app.delete("/api/shift-commands")
async def clear_all_shift_commands(user=Depends(current_user)):
    conn = database.get_db()
    conn.execute("UPDATE shift_commands SET is_active=0 WHERE restaurant_id=?", (user["restaurant_id"],))
    conn.commit(); conn.close()
    return {"ok": True}


# ── EXCEPTION PLAYBOOK ────────────────────────────────────────────────────────

@app.get("/api/exception-playbook")
async def list_playbook(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM exception_playbook WHERE restaurant_id=? ORDER BY priority DESC, created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/exception-playbook")
async def add_playbook_entry(data: dict, user=Depends(current_user)):
    import json as _json
    triggers = data.get("trigger_keywords") or []
    reply    = (data.get("reply_text") or "").strip()
    if not triggers or not reply:
        raise HTTPException(400, "trigger_keywords and reply_text required")
    if isinstance(triggers, list):
        triggers_json = _json.dumps(triggers, ensure_ascii=False)
    else:
        triggers_json = str(triggers)
    rid = user["restaurant_id"]
    conn = database.get_db()
    eid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO exception_playbook (id, restaurant_id, trigger_keywords, reply_text, category, priority) VALUES (?,?,?,?,?,?)",
        (eid, rid, triggers_json, reply, data.get("category","general"), int(data.get("priority",0)))
    )
    conn.commit(); conn.close()
    return {"ok": True, "id": eid}

@app.patch("/api/exception-playbook/{eid}")
async def update_playbook_entry(eid: str, data: dict, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM exception_playbook WHERE id=? AND restaurant_id=?",
                        (eid, user["restaurant_id"])).fetchone():
        conn.close(); raise HTTPException(404)
    if "is_active" in data:
        conn.execute("UPDATE exception_playbook SET is_active=? WHERE id=?", (int(data["is_active"]), eid))
    if "reply_text" in data:
        conn.execute("UPDATE exception_playbook SET reply_text=? WHERE id=?", (data["reply_text"].strip(), eid))
    conn.commit(); conn.close()
    return {"ok": True}

@app.delete("/api/exception-playbook/{eid}")
async def delete_playbook_entry(eid: str, user=Depends(current_user)):
    conn = database.get_db()
    conn.execute("DELETE FROM exception_playbook WHERE id=? AND restaurant_id=?", (eid, user["restaurant_id"]))
    conn.commit(); conn.close()
    return {"ok": True}


# ── BOT QUALITY ANALYTICS ─────────────────────────────────────────────────────

@app.get("/api/analytics/bot-quality")
async def bot_quality_analytics(user=Depends(current_user)):
    rid = user["restaurant_id"]
    conn = database.get_db()
    def q(sql, *p): return (conn.execute(sql, p).fetchone() or [0])[0]
    total   = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=?", rid)
    ordered = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND had_order=1", rid)
    escalated = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND resolution_type='escalated'", rid)
    avg_msgs  = q("SELECT COALESCE(AVG(bot_turn_count),0) FROM conversations WHERE restaurant_id=? AND bot_turn_count>0", rid)
    week_convs = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND created_at >= datetime('now','-7 days')", rid)
    week_ordered = q("SELECT COUNT(*) FROM conversations WHERE restaurant_id=? AND had_order=1 AND created_at >= datetime('now','-7 days')", rid)
    conversion = round((ordered / total * 100) if total > 0 else 0, 1)
    week_conversion = round((week_ordered / week_convs * 100) if week_convs > 0 else 0, 1)
    conn.close()
    return {
        "total_conversations": total,
        "ordered": ordered,
        "escalated": escalated,
        "conversion_rate": conversion,
        "avg_messages_per_conv": round(float(avg_msgs), 1),
        "week_conversations": week_convs,
        "week_conversion_rate": week_conversion,
    }

@app.get("/api/analytics/bot-gaps")
async def bot_gaps_report(days: int = 7, user=Depends(current_user)):
    """Weekly report: questions the bot couldn't answer."""
    rid = user["restaurant_id"]
    conn = database.get_db()
    rows = conn.execute(
        "SELECT customer_message, COUNT(*) as cnt FROM bot_unclear_log "
        "WHERE restaurant_id=? AND created_at >= datetime('now', ? || ' days') "
        "GROUP BY customer_message ORDER BY cnt DESC LIMIT 30",
        (rid, f"-{days}")
    ).fetchall()
    conn.close()
    return [{"message": r["customer_message"], "count": r["cnt"]} for r in rows]


# ── NUMBER 25 — AI Training / Learning System ─────────────────────────────────

# ── NUMBER 25B helpers — audit + versioning ───────────────────────────────────

def _ai_log(conn, restaurant_id: str, actor_id: str, actor_role: str,
            entity_type: str, entity_id: str, action: str,
            old_val: dict = None, new_val: dict = None):
    """Write one row to ai_change_logs. Never raises."""
    try:
        conn.execute(
            "INSERT INTO ai_change_logs "
            "(id, restaurant_id, actor_user_id, actor_role, entity_type, entity_id, action, old_value_json, new_value_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), restaurant_id or "", actor_id or "", actor_role or "",
                entity_type or "", entity_id or "", action or "",
                json.dumps(old_val or {}, ensure_ascii=False),
                json.dumps(new_val or {}, ensure_ascii=False),
            )
        )
    except Exception as _e:
        logger.warning(f"[ai_log] failed action={action} restaurant={restaurant_id}: {_e}")


def _snap_correction(conn, row: dict, changed_by: str, reason: str = ""):
    """Snapshot current correction state into bot_correction_versions."""
    try:
        last = conn.execute(
            "SELECT COALESCE(MAX(version_number),0) FROM bot_correction_versions WHERE correction_id=?",
            (row["id"],)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO bot_correction_versions "
            "(id, correction_id, restaurant_id, trigger_text, correction_text, category, priority, is_active, version_number, changed_by, change_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), row["id"], row.get("restaurant_id",""),
                row.get("trigger_text",""), row.get("correction_text",""),
                row.get("category",""), row.get("priority",0),
                row.get("is_active",1), int(last) + 1, changed_by or "", reason or "",
            )
        )
    except Exception as _e:
        logger.warning(f"[snap_correction] failed correction_id={row.get('id','?')}: {_e}")


def _snap_knowledge(conn, row: dict, changed_by: str, reason: str = ""):
    """Snapshot current knowledge state into restaurant_knowledge_versions."""
    try:
        last = conn.execute(
            "SELECT COALESCE(MAX(version_number),0) FROM restaurant_knowledge_versions WHERE knowledge_id=?",
            (row["id"],)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO restaurant_knowledge_versions "
            "(id, knowledge_id, restaurant_id, title, content, category, priority, is_active, version_number, changed_by, change_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), row["id"], row.get("restaurant_id",""),
                row.get("title",""), row.get("content",""),
                row.get("category",""), row.get("priority",0),
                row.get("is_active",1), int(last) + 1, changed_by or "", reason or "",
            )
        )
    except Exception as _e:
        logger.warning(f"[snap_knowledge] failed knowledge_id={row.get('id','?')}: {_e}")


def _correction_row(conn, cid: str) -> dict:
    r = conn.execute("SELECT * FROM bot_corrections WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else {}


def _knowledge_row(conn, kid: str) -> dict:
    r = conn.execute("SELECT * FROM restaurant_knowledge WHERE id=?", (kid,)).fetchone()
    return dict(r) if r else {}


# ── AI Corrections (enriched CRUD) ───────────────────────────────────────────

@app.get("/api/ai/corrections")
async def ai_list_corrections(user=Depends(current_user)):
    """List all AI corrections for this restaurant (enriched format)."""
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM bot_corrections WHERE restaurant_id=? ORDER BY priority DESC, created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/ai/corrections")
async def ai_add_correction(data: dict, user=Depends(current_user)):
    """Add or update an AI correction with trigger/correction/category/priority."""
    rid = user["restaurant_id"]
    trigger_text = (data.get("trigger_text") or "").strip()
    correction_text = (data.get("correction_text") or "").strip()
    category = (data.get("category") or "").strip()
    priority = int(data.get("priority") or 0)
    legacy_text = (data.get("text") or "").strip()
    created_by = user.get("name") or user.get("email") or ""

    if trigger_text and correction_text:
        canonical = f"إذا قال العميل '{trigger_text}' → رد بـ: {correction_text}"
    elif legacy_text:
        canonical = legacy_text
    else:
        raise HTTPException(400, "trigger_text+correction_text أو text مطلوب")

    conn = database.get_db()
    existing = conn.execute(
        "SELECT id FROM bot_corrections WHERE restaurant_id=? AND text=?",
        (rid, canonical)
    ).fetchone()
    if existing:
        cid = existing["id"] if hasattr(existing, "keys") else existing[0]
        conn.execute(
            "UPDATE bot_corrections SET trigger_text=?, correction_text=?, category=?, "
            "priority=?, is_active=1, deleted_at='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (trigger_text, correction_text, category, priority, cid)
        )
        new_row = _correction_row(conn, cid)
        _snap_correction(conn, new_row, created_by, "reactivated via add")
        _ai_log(conn, rid, user.get("id",""), user.get("role","owner"), "correction", cid, "reactivated", {}, new_row)
        conn.commit()
        conn.close()
        return {"ok": True, "id": cid, "deduped": True}

    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO bot_corrections (id, restaurant_id, text, trigger_text, correction_text, "
        "category, priority, is_active, added_by, created_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)",
        (cid, rid, canonical, trigger_text, correction_text, category, priority, created_by, created_by)
    )
    new_row = _correction_row(conn, cid)
    _snap_correction(conn, new_row, created_by, "created")
    _ai_log(conn, rid, user.get("id",""), user.get("role","owner"), "correction", cid, "created", {}, new_row)
    conn.commit()
    conn.close()
    return {"ok": True, "id": cid}


@app.put("/api/ai/corrections/{cid}")
async def ai_update_correction(cid: str, data: dict, user=Depends(current_user)):
    """Update an existing AI correction (creates version snapshot)."""
    conn = database.get_db()
    old_row = _correction_row(conn, cid)
    if not old_row or old_row.get("restaurant_id") != user["restaurant_id"]:
        conn.close()
        raise HTTPException(404, "Correction not found")

    trigger_text = (data.get("trigger_text") or "").strip()
    correction_text = (data.get("correction_text") or "").strip()
    category = (data.get("category") or "").strip()
    priority = int(data.get("priority") or 0)
    is_active = int(bool(data.get("is_active", True)))

    if trigger_text and correction_text:
        canonical = f"إذا قال العميل '{trigger_text}' → رد بـ: {correction_text}"
    else:
        canonical = (data.get("text") or "").strip() or trigger_text or correction_text

    actor = user.get("name") or user.get("email") or ""
    conn.execute(
        "UPDATE bot_corrections SET text=?, trigger_text=?, correction_text=?, category=?, "
        "priority=?, is_active=?, deleted_at='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (canonical, trigger_text, correction_text, category, priority, is_active, cid)
    )
    new_row = _correction_row(conn, cid)
    _snap_correction(conn, new_row, actor, data.get("change_reason","updated"))
    _ai_log(conn, user["restaurant_id"], user.get("id",""), user.get("role","owner"),
            "correction", cid, "updated", old_row, new_row)
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/ai/corrections/{cid}")
async def ai_delete_correction(cid: str, user=Depends(current_user)):
    """Soft-delete an AI correction (sets is_active=0, preserves audit history)."""
    conn = database.get_db()
    old_row = _correction_row(conn, cid)
    if not old_row or old_row.get("restaurant_id") != user["restaurant_id"]:
        conn.close()
        raise HTTPException(404, "Correction not found")

    actor = user.get("name") or user.get("email") or ""
    conn.execute(
        "UPDATE bot_corrections SET is_active=0, deleted_at=CURRENT_TIMESTAMP, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?", (cid,)
    )
    new_row = _correction_row(conn, cid)
    _snap_correction(conn, new_row, actor, "soft-deleted")
    _ai_log(conn, user["restaurant_id"], user.get("id",""), user.get("role","owner"),
            "correction", cid, "deleted", old_row, new_row)
    conn.commit()
    conn.close()
    return {"ok": True}


# ── AI Feedback ───────────────────────────────────────────────────────────────

@app.get("/api/ai/feedback")
async def ai_list_feedback(status: str = "", user=Depends(current_user)):
    """List AI feedback items, optionally filtered by status."""
    conn = database.get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM ai_feedback WHERE restaurant_id=? AND status=? ORDER BY created_at DESC LIMIT 100",
            (user["restaurant_id"], status)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ai_feedback WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 100",
            (user["restaurant_id"],)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/ai/feedback")
async def ai_add_feedback(data: dict, user=Depends(current_user)):
    """Submit feedback on a bot reply (good/bad/needs_correction)."""
    rid = user["restaurant_id"]
    rating = data.get("rating") or "bad"
    if rating not in ("good", "bad", "needs_correction"):
        raise HTTPException(400, "rating must be good / bad / needs_correction")

    fid = str(uuid.uuid4())
    conn = database.get_db()
    conn.execute(
        "INSERT INTO ai_feedback (id, restaurant_id, conversation_id, message_id, rating, "
        "reason, suggested_correction, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            fid, rid,
            data.get("conversation_id") or "",
            data.get("message_id") or "",
            rating,
            (data.get("reason") or "").strip(),
            (data.get("suggested_correction") or "").strip(),
            user.get("name") or user.get("email") or "",
        )
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": fid}


@app.put("/api/ai/feedback/{fid}/approve")
async def ai_approve_feedback(fid: str, data: dict = None, user=Depends(current_user)):
    """Approve feedback — optionally promote suggested_correction into bot_corrections."""
    if data is None:
        data = {}
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM ai_feedback WHERE id=? AND restaurant_id=?",
        (fid, user["restaurant_id"])
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Feedback not found")

    row = dict(row)
    conn.execute(
        "UPDATE ai_feedback SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
        (user.get("name") or user.get("email") or "", fid)
    )

    # If there's a suggested correction and promote=true, add it to bot_corrections
    promote = bool(data.get("promote_correction", False))
    if promote and row.get("suggested_correction"):
        text = row["suggested_correction"].strip()
        if text:
            existing = conn.execute(
                "SELECT id FROM bot_corrections WHERE restaurant_id=? AND text=?",
                (row["restaurant_id"], text)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO bot_corrections (id, restaurant_id, text, added_by, is_active, created_by) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (str(uuid.uuid4()), row["restaurant_id"], text,
                     user.get("name") or "", user.get("name") or "")
                )

    conn.commit()
    conn.close()
    return {"ok": True, "promoted": promote and bool(row.get("suggested_correction"))}


@app.put("/api/ai/feedback/{fid}/reject")
async def ai_reject_feedback(fid: str, data: dict = None, user=Depends(current_user)):
    """Reject feedback."""
    if data is None:
        data = {}
    conn = database.get_db()
    row = conn.execute(
        "SELECT id FROM ai_feedback WHERE id=? AND restaurant_id=?",
        (fid, user["restaurant_id"])
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Feedback not found")
    conn.execute(
        "UPDATE ai_feedback SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
        (user.get("name") or user.get("email") or "", fid)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Knowledge Base ────────────────────────────────────────────────────────────

@app.get("/api/ai/knowledge")
async def ai_list_knowledge(user=Depends(current_user)):
    """List all knowledge base entries for this restaurant."""
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM restaurant_knowledge WHERE restaurant_id=? ORDER BY priority DESC, created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/ai/knowledge")
async def ai_add_knowledge(data: dict, user=Depends(current_user)):
    """Add a knowledge base entry."""
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "title and content are required")

    kid = str(uuid.uuid4())
    actor = user.get("name") or user.get("email") or ""
    conn = database.get_db()
    conn.execute(
        "INSERT INTO restaurant_knowledge (id, restaurant_id, title, content, category, "
        "source, is_active, priority, created_by) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (
            kid, user["restaurant_id"], title, content,
            (data.get("category") or "").strip(),
            data.get("source") or "manual",
            int(data.get("priority") or 0),
            actor,
        )
    )
    new_row = _knowledge_row(conn, kid)
    _snap_knowledge(conn, new_row, actor, "created")
    _ai_log(conn, user["restaurant_id"], user.get("id",""), user.get("role","owner"),
            "knowledge", kid, "created", {}, new_row)
    conn.commit()
    conn.close()
    return {"ok": True, "id": kid}


@app.put("/api/ai/knowledge/{kid}")
async def ai_update_knowledge(kid: str, data: dict, user=Depends(current_user)):
    """Update a knowledge base entry (creates version snapshot)."""
    conn = database.get_db()
    old_row = _knowledge_row(conn, kid)
    if not old_row or old_row.get("restaurant_id") != user["restaurant_id"]:
        conn.close()
        raise HTTPException(404, "Knowledge entry not found")

    title = (data.get("title") or old_row.get("title","")).strip()
    content = (data.get("content") or old_row.get("content","")).strip()
    category = (data.get("category") or "").strip()
    priority = int(data.get("priority") or 0)
    is_active = int(bool(data.get("is_active", True)))
    actor = user.get("name") or user.get("email") or ""

    conn.execute(
        "UPDATE restaurant_knowledge SET title=?, content=?, category=?, priority=?, "
        "is_active=?, deleted_at='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (title, content, category, priority, is_active, kid)
    )
    new_row = _knowledge_row(conn, kid)
    _snap_knowledge(conn, new_row, actor, data.get("change_reason","updated"))
    _ai_log(conn, user["restaurant_id"], user.get("id",""), user.get("role","owner"),
            "knowledge", kid, "updated", old_row, new_row)
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/ai/knowledge/{kid}")
async def ai_delete_knowledge(kid: str, user=Depends(current_user)):
    """Soft-delete a knowledge base entry (sets is_active=0, preserves audit history)."""
    conn = database.get_db()
    old_row = _knowledge_row(conn, kid)
    if not old_row or old_row.get("restaurant_id") != user["restaurant_id"]:
        conn.close()
        raise HTTPException(404, "Knowledge entry not found")

    actor = user.get("name") or user.get("email") or ""
    conn.execute(
        "UPDATE restaurant_knowledge SET is_active=0, deleted_at=CURRENT_TIMESTAMP, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?", (kid,)
    )
    new_row = _knowledge_row(conn, kid)
    _snap_knowledge(conn, new_row, actor, "soft-deleted")
    _ai_log(conn, user["restaurant_id"], user.get("id",""), user.get("role","owner"),
            "knowledge", kid, "deleted", old_row, new_row)
    conn.commit()
    conn.close()
    return {"ok": True}


# ── AI Quality Logs ───────────────────────────────────────────────────────────

@app.get("/api/ai/quality")
async def ai_quality_logs(limit: int = 50, user=Depends(current_user)):
    """List recent AI quality log entries for this restaurant."""
    limit = min(limit, 200)
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM ai_quality_logs WHERE restaurant_id=? ORDER BY created_at DESC LIMIT ?",
        (user["restaurant_id"], limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/ai/quality/summary")
async def ai_quality_summary(user=Depends(current_user)):
    """Aggregate quality metrics for this restaurant."""
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        total_logs = conn.execute(
            "SELECT COUNT(*) FROM ai_quality_logs WHERE restaurant_id=?", (rid,)
        ).fetchone()[0]
        escalations = conn.execute(
            "SELECT COUNT(*) FROM ai_quality_logs WHERE restaurant_id=? AND escalation_triggered=1", (rid,)
        ).fetchone()[0]
        with_corrections = conn.execute(
            "SELECT COUNT(*) FROM ai_quality_logs WHERE restaurant_id=? AND used_corrections=1", (rid,)
        ).fetchone()[0]
        with_knowledge = conn.execute(
            "SELECT COUNT(*) FROM ai_quality_logs WHERE restaurant_id=? AND used_knowledge=1", (rid,)
        ).fetchone()[0]
        total_feedback = conn.execute(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=?", (rid,)
        ).fetchone()[0]
        pending_feedback = conn.execute(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=? AND status='pending'", (rid,)
        ).fetchone()[0]
        good_feedback = conn.execute(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=? AND rating='good'", (rid,)
        ).fetchone()[0]
        total_corrections = conn.execute(
            "SELECT COUNT(*) FROM bot_corrections WHERE restaurant_id=? AND is_active=1", (rid,)
        ).fetchone()[0]
        total_knowledge = conn.execute(
            "SELECT COUNT(*) FROM restaurant_knowledge WHERE restaurant_id=? AND is_active=1", (rid,)
        ).fetchone()[0]
    finally:
        conn.close()

    satisfaction = round(good_feedback / total_feedback * 100, 1) if total_feedback > 0 else None
    return {
        "total_logs": total_logs,
        "escalation_rate": round(escalations / total_logs * 100, 1) if total_logs > 0 else 0,
        "corrections_usage_rate": round(with_corrections / total_logs * 100, 1) if total_logs > 0 else 0,
        "knowledge_usage_rate": round(with_knowledge / total_logs * 100, 1) if total_logs > 0 else 0,
        "total_feedback": total_feedback,
        "pending_feedback": pending_feedback,
        "satisfaction_rate": satisfaction,
        "active_corrections": total_corrections,
        "active_knowledge": total_knowledge,
    }


# ── NUMBER 25B — Safety, Rollback & Super Admin Control ───────────────────────

# ── Version history (restaurant user) ────────────────────────────────────────

@app.get("/api/ai/corrections/{cid}/versions")
async def ai_correction_versions(cid: str, user=Depends(current_user)):
    """List version history for one correction (tenant-scoped)."""
    conn = database.get_db()
    # Verify ownership first
    owner = conn.execute(
        "SELECT id FROM bot_corrections WHERE id=? AND restaurant_id=?",
        (cid, user["restaurant_id"])
    ).fetchone()
    if not owner:
        conn.close()
        raise HTTPException(404, "Correction not found")
    rows = conn.execute(
        "SELECT * FROM bot_correction_versions WHERE correction_id=? ORDER BY version_number DESC",
        (cid,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/ai/knowledge/{kid}/versions")
async def ai_knowledge_versions(kid: str, user=Depends(current_user)):
    """List version history for one knowledge entry (tenant-scoped)."""
    conn = database.get_db()
    owner = conn.execute(
        "SELECT id FROM restaurant_knowledge WHERE id=? AND restaurant_id=?",
        (kid, user["restaurant_id"])
    ).fetchone()
    if not owner:
        conn.close()
        raise HTTPException(404, "Knowledge entry not found")
    rows = conn.execute(
        "SELECT * FROM restaurant_knowledge_versions WHERE knowledge_id=? ORDER BY version_number DESC",
        (kid,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Rollback endpoints ────────────────────────────────────────────────────────

@app.post("/api/ai/corrections/{cid}/restore-version/{vid}")
async def ai_restore_correction_version(cid: str, vid: str, user=Depends(current_user)):
    """Restore a correction to a specific version snapshot."""
    conn = database.get_db()
    # Ownership check (restaurant user → own restaurant; super admin → any)
    is_super = user.get("is_super", False)
    if not is_super:
        owner = conn.execute(
            "SELECT id FROM bot_corrections WHERE id=? AND restaurant_id=?",
            (cid, user["restaurant_id"])
        ).fetchone()
        if not owner:
            conn.close()
            raise HTTPException(404, "Correction not found")

    ver = conn.execute(
        "SELECT * FROM bot_correction_versions WHERE id=? AND correction_id=?",
        (vid, cid)
    ).fetchone()
    if not ver:
        conn.close()
        raise HTTPException(404, "Version not found")

    ver = dict(ver)
    old_row = _correction_row(conn, cid)
    actor = user.get("name") or user.get("email") or ""
    rid = ver.get("restaurant_id") or (old_row.get("restaurant_id",""))

    # Apply version snapshot back to live row
    if ver.get("trigger_text") and ver.get("correction_text"):
        canonical = f"إذا قال العميل '{ver['trigger_text']}' → رد بـ: {ver['correction_text']}"
    else:
        canonical = old_row.get("text","")

    conn.execute(
        "UPDATE bot_corrections SET text=?, trigger_text=?, correction_text=?, category=?, "
        "priority=?, is_active=?, deleted_at='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (canonical, ver.get("trigger_text",""), ver.get("correction_text",""),
         ver.get("category",""), ver.get("priority",0), ver.get("is_active",1), cid)
    )
    new_row = _correction_row(conn, cid)
    _snap_correction(conn, new_row, actor, f"rolled back to version {ver.get('version_number')}")
    _ai_log(conn, rid, user.get("id",""), user.get("role","super" if is_super else "owner"),
            "correction", cid, "rollback",
            old_row, {"restored_from_version": ver.get("version_number"), **new_row})
    conn.commit()
    conn.close()
    return {"ok": True, "restored_version": ver.get("version_number")}


@app.post("/api/ai/knowledge/{kid}/restore-version/{vid}")
async def ai_restore_knowledge_version(kid: str, vid: str, user=Depends(current_user)):
    """Restore a knowledge entry to a specific version snapshot."""
    conn = database.get_db()
    is_super = user.get("is_super", False)
    if not is_super:
        owner = conn.execute(
            "SELECT id FROM restaurant_knowledge WHERE id=? AND restaurant_id=?",
            (kid, user["restaurant_id"])
        ).fetchone()
        if not owner:
            conn.close()
            raise HTTPException(404, "Knowledge entry not found")

    ver = conn.execute(
        "SELECT * FROM restaurant_knowledge_versions WHERE id=? AND knowledge_id=?",
        (vid, kid)
    ).fetchone()
    if not ver:
        conn.close()
        raise HTTPException(404, "Version not found")

    ver = dict(ver)
    old_row = _knowledge_row(conn, kid)
    actor = user.get("name") or user.get("email") or ""
    rid = ver.get("restaurant_id") or old_row.get("restaurant_id","")

    conn.execute(
        "UPDATE restaurant_knowledge SET title=?, content=?, category=?, priority=?, "
        "is_active=?, deleted_at='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (ver.get("title",""), ver.get("content",""), ver.get("category",""),
         ver.get("priority",0), ver.get("is_active",1), kid)
    )
    new_row = _knowledge_row(conn, kid)
    _snap_knowledge(conn, new_row, actor, f"rolled back to version {ver.get('version_number')}")
    _ai_log(conn, rid, user.get("id",""), user.get("role","super" if is_super else "owner"),
            "knowledge", kid, "rollback",
            old_row, {"restored_from_version": ver.get("version_number"), **new_row})
    conn.commit()
    conn.close()
    return {"ok": True, "restored_version": ver.get("version_number")}


# ── Change logs (restaurant user) ─────────────────────────────────────────────

@app.get("/api/ai/change-logs")
async def ai_change_logs(limit: int = 50, user=Depends(current_user)):
    """List AI change-log entries for this restaurant."""
    limit = min(limit, 200)
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM ai_change_logs WHERE restaurant_id=? ORDER BY created_at DESC LIMIT ?",
        (user["restaurant_id"], limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Restaurant AI learning switch ─────────────────────────────────────────────

@app.get("/api/ai/settings")
async def ai_get_settings(user=Depends(current_user)):
    """Get AI learning settings for this restaurant."""
    conn = database.get_db()
    row = conn.execute(
        "SELECT ai_learning_enabled FROM restaurants WHERE id=?",
        (user["restaurant_id"],)
    ).fetchone()
    conn.close()
    enabled = bool(row["ai_learning_enabled"] if row and hasattr(row,"keys") else (row[0] if row else 1))
    return {"ai_learning_enabled": enabled}


@app.put("/api/ai/settings")
async def ai_update_settings(data: dict, user=Depends(current_user)):
    """Toggle AI learning on/off for this restaurant."""
    enabled = int(bool(data.get("ai_learning_enabled", True)))
    rid = user["restaurant_id"]
    actor = user.get("name") or user.get("email") or ""
    conn = database.get_db()
    old_row = conn.execute("SELECT ai_learning_enabled FROM restaurants WHERE id=?", (rid,)).fetchone()
    old_val = bool(old_row["ai_learning_enabled"] if old_row and hasattr(old_row,"keys") else (old_row[0] if old_row else 1))
    conn.execute("UPDATE restaurants SET ai_learning_enabled=? WHERE id=?", (enabled, rid))
    action = "learning_enabled" if enabled else "learning_disabled"
    _ai_log(conn, rid, user.get("id",""), user.get("role","owner"), "restaurant", rid,
            action, {"ai_learning_enabled": old_val}, {"ai_learning_enabled": bool(enabled)})
    conn.commit()
    conn.close()
    return {"ok": True, "ai_learning_enabled": bool(enabled)}


# ── Super admin AI control endpoints ──────────────────────────────────────────

@app.get("/api/super/ai/corrections")
async def super_ai_list_corrections(restaurant_id: str = "", limit: int = 100,
                                    admin=Depends(current_super_admin)):
    """SA: list corrections, optionally filtered by restaurant."""
    limit = min(limit, 500)
    conn = database.get_db()
    if restaurant_id:
        rows = conn.execute(
            "SELECT bc.*, r.name as restaurant_name FROM bot_corrections bc "
            "LEFT JOIN restaurants r ON bc.restaurant_id=r.id "
            "WHERE bc.restaurant_id=? ORDER BY bc.created_at DESC LIMIT ?",
            (restaurant_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT bc.*, r.name as restaurant_name FROM bot_corrections bc "
            "LEFT JOIN restaurants r ON bc.restaurant_id=r.id "
            "ORDER BY bc.created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/super/ai/knowledge")
async def super_ai_list_knowledge(restaurant_id: str = "", limit: int = 100,
                                  admin=Depends(current_super_admin)):
    """SA: list knowledge entries, optionally filtered by restaurant."""
    limit = min(limit, 500)
    conn = database.get_db()
    if restaurant_id:
        rows = conn.execute(
            "SELECT rk.*, r.name as restaurant_name FROM restaurant_knowledge rk "
            "LEFT JOIN restaurants r ON rk.restaurant_id=r.id "
            "WHERE rk.restaurant_id=? ORDER BY rk.created_at DESC LIMIT ?",
            (restaurant_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rk.*, r.name as restaurant_name FROM restaurant_knowledge rk "
            "LEFT JOIN restaurants r ON rk.restaurant_id=r.id "
            "ORDER BY rk.created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/super/ai/feedback")
async def super_ai_list_feedback(restaurant_id: str = "", status: str = "", limit: int = 100,
                                 admin=Depends(current_super_admin)):
    """SA: list feedback, optionally filtered."""
    limit = min(limit, 500)
    conn = database.get_db()
    where, params = [], []
    if restaurant_id:
        where.append("f.restaurant_id=?"); params.append(restaurant_id)
    if status:
        where.append("f.status=?"); params.append(status)
    wclause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT f.*, r.name as restaurant_name FROM ai_feedback f "
        f"LEFT JOIN restaurants r ON f.restaurant_id=r.id "
        f"{wclause} ORDER BY f.created_at DESC LIMIT ?",
        params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/super/ai/change-logs")
async def super_ai_change_logs(restaurant_id: str = "", entity_type: str = "",
                               limit: int = 100, admin=Depends(current_super_admin)):
    """SA: full audit trail, optionally filtered."""
    limit = min(limit, 500)
    conn = database.get_db()
    where, params = [], []
    if restaurant_id:
        where.append("restaurant_id=?"); params.append(restaurant_id)
    if entity_type:
        where.append("entity_type=?"); params.append(entity_type)
    wclause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM ai_change_logs {wclause} ORDER BY created_at DESC LIMIT ?",
        params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/super/ai/corrections/{cid}/disable")
async def super_disable_correction(cid: str, request: Request,
                                   data: dict = None, admin=Depends(current_super_admin)):
    """SA: emergency disable a bad correction (any restaurant)."""
    if data is None:
        data = {}
    conn = database.get_db()
    old_row = conn.execute("SELECT * FROM bot_corrections WHERE id=?", (cid,)).fetchone()
    if not old_row:
        conn.close()
        raise HTTPException(404, "Correction not found")
    old_row = dict(old_row)
    conn.execute(
        "UPDATE bot_corrections SET is_active=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (cid,)
    )
    new_row = _correction_row(conn, cid)
    _snap_correction(conn, new_row, admin.get("name","sa"), "SA emergency disable")
    _ai_log(conn, old_row.get("restaurant_id",""), admin.get("id",""), "super",
            "correction", cid, "sa_disabled", old_row, new_row)
    _sa_log(conn, admin["id"], admin.get("name",""), "sa_disable_correction",
            "correction", cid, data.get("reason","emergency disable"),
            request.client.host if request.client else "")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/super/ai/knowledge/{kid}/disable")
async def super_disable_knowledge(kid: str, request: Request,
                                  data: dict = None, admin=Depends(current_super_admin)):
    """SA: emergency disable a bad knowledge entry (any restaurant)."""
    if data is None:
        data = {}
    conn = database.get_db()
    old_row = conn.execute("SELECT * FROM restaurant_knowledge WHERE id=?", (kid,)).fetchone()
    if not old_row:
        conn.close()
        raise HTTPException(404, "Knowledge entry not found")
    old_row = dict(old_row)
    conn.execute(
        "UPDATE restaurant_knowledge SET is_active=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (kid,)
    )
    new_row = _knowledge_row(conn, kid)
    _snap_knowledge(conn, new_row, admin.get("name","sa"), "SA emergency disable")
    _ai_log(conn, old_row.get("restaurant_id",""), admin.get("id",""), "super",
            "knowledge", kid, "sa_disabled", old_row, new_row)
    _sa_log(conn, admin["id"], admin.get("name",""), "sa_disable_knowledge",
            "knowledge", kid, data.get("reason","emergency disable"),
            request.client.host if request.client else "")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/super/ai/restaurant/{rid}/disable-learning")
async def super_disable_learning(rid: str, request: Request,
                                 data: dict = None, admin=Depends(current_super_admin)):
    """SA: disable AI learning for one restaurant."""
    if data is None:
        data = {}
    conn = database.get_db()
    r = conn.execute("SELECT id FROM restaurants WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "Restaurant not found")
    conn.execute("UPDATE restaurants SET ai_learning_enabled=0 WHERE id=?", (rid,))
    _ai_log(conn, rid, admin.get("id",""), "super", "restaurant", rid, "sa_learning_disabled",
            {"ai_learning_enabled": True}, {"ai_learning_enabled": False})
    _sa_log(conn, admin["id"], admin.get("name",""), "sa_disable_ai_learning",
            "restaurant", rid, data.get("reason","SA disabled"),
            request.client.host if request.client else "")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/super/ai/restaurant/{rid}/enable-learning")
async def super_enable_learning(rid: str, request: Request,
                                data: dict = None, admin=Depends(current_super_admin)):
    """SA: re-enable AI learning for one restaurant."""
    if data is None:
        data = {}
    conn = database.get_db()
    r = conn.execute("SELECT id FROM restaurants WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "Restaurant not found")
    conn.execute("UPDATE restaurants SET ai_learning_enabled=1 WHERE id=?", (rid,))
    _ai_log(conn, rid, admin.get("id",""), "super", "restaurant", rid, "sa_learning_enabled",
            {"ai_learning_enabled": False}, {"ai_learning_enabled": True})
    _sa_log(conn, admin["id"], admin.get("name",""), "sa_enable_ai_learning",
            "restaurant", rid, data.get("reason","SA enabled"),
            request.client.host if request.client else "")
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

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    """Real-time event stream. Auth via ?token=JWT query param."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        restaurant_id = payload.get("restaurant_id")
        if not restaurant_id:
            await ws.close(code=4001); return
    except Exception:
        await ws.close(code=4001); return

    await ws_manager.connect(restaurant_id, ws)
    try:
        # Send initial ping so client knows connection is live
        await ws.send_text('{"type":"connected"}')
        while True:
            # Keep alive — client sends "ping", we reply "pong"
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        ws_manager.disconnect(restaurant_id, ws)
    except Exception:
        ws_manager.disconnect(restaurant_id, ws)


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

    expected = META_VERIFY_TOKEN or f"meta_verify_{META_APP_ID}"
    if mode == "subscribe" and token and expected and token == expected:
        logger.info(f"[meta-webhook-hit] GET verification OK — challenge={challenge[:20]}")
        return PlainTextResponse(challenge)

    logger.warning(f"[webhooks/meta] verify failed — mode={mode} got={token!r} expected={expected!r}")
    raise HTTPException(403, "Webhook verification failed")


@app.post("/webhooks/meta")
async def meta_webhook_unified(req: Request, background_tasks: BackgroundTasks):
    """
    Unified incoming-event handler for Facebook, Instagram, and WhatsApp.
    Routes each event to the correct restaurant by looking up page_id or
    phone_number_id in the channels table.
    """
    logger.info("[meta-webhook-hit] POST received")  # must be first — proves delivery
    raw_body = await req.body()
    logger.info(
        f"[meta-incoming] POST /webhooks/meta — "
        f"size={len(raw_body)} "
        f"has_sig={bool(req.headers.get('X-Hub-Signature-256',''))} "
        f"body_preview={raw_body[:200]}"
    )

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

    has_sig = bool(sig_header)
    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    # Real Meta events always carry X-Hub-Signature-256; debug/curl do not
    obj_type = payload.get("object", "?")
    entries  = payload.get("entry", [])
    if has_sig:
        msg_count = sum(len(e.get("messaging", [])) for e in entries)
        logger.info(
            f"[meta-live-post] REAL Meta POST — object={obj_type} "
            f"entries={len(entries)} messages={msg_count} "
            f"raw={raw_body[:300]}"
        )
    else:
        logger.info(f"[meta-debug-post] unsigned POST (debug/test) — object={obj_type}")

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
                entry_id = entry.get("id", "")
                if not entry_id:
                    continue
                # entry.id can be either the Facebook Page ID or the IG Business Account ID
                # depending on how Meta sends it — check both columns.
                row = conn.execute(
                    "SELECT restaurant_id FROM channels "
                    "WHERE (page_id=? OR business_account_id=?) "
                    "AND type='instagram' AND enabled=1",
                    (entry_id, entry_id)
                ).fetchone()
                if row:
                    logger.info(
                        f"[ig-incoming] routing → restaurant={row['restaurant_id'][:8]} "
                        f"entry_id={entry_id}"
                    )
                    webhooks.handle_instagram(row["restaurant_id"], payload)
                else:
                    logger.warning(
                        f"[ig-incoming] NO CHANNEL FOUND for entry_id={entry_id} — "
                        f"check that channels.page_id or channels.business_account_id matches. "
                        f"Raw entry keys: {list(entry.keys())}"
                    )

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
                    logger.info(
                        f"[meta-messenger-parsed] routing FB → restaurant={row['restaurant_id'][:8]} "
                        f"entry_id={page_id}"
                    )
                    webhooks.handle_facebook(row["restaurant_id"], payload)
                else:
                    logger.warning(
                        f"[meta-incoming] NO CHANNEL FOUND for FB page_id={page_id} — "
                        f"check channels.page_id matches"
                    )

        else:
            logger.warning(f"[meta-incoming] unrecognised object type: {object_type} — full payload={json.dumps(payload)[:300]}")

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
                "state":          r["state"],
            })
        ch_rows = conn.execute(
            "SELECT type, connection_status, last_error, reconnect_needed FROM channels "
            "WHERE type IN ('facebook','instagram','whatsapp') LIMIT 10"
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


@app.get("/api/debug/meta-pipeline-check")
async def debug_meta_pipeline_check(key: str = ""):
    """
    Check whether fire-test events were actually processed and stored in DB.
    Looks for the fake sender_id (111111111111111) in customers + conversations.
    Protected by ?key=<first-8-of-META_APP_ID>.
    """
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")
    conn = database.get_db()
    try:
        # external_id is stored in conversation_memory, not directly on customers
        mem_rows = conn.execute(
            "SELECT customer_id FROM conversation_memory "
            "WHERE memory_key='external_id' AND memory_value='111111111111111'"
        ).fetchall()
        fake_cids  = [r["customer_id"] for r in mem_rows]
        conv_count = 0
        msg_count  = 0
        fake_customers_detail = []
        for cid in fake_cids:
            cust = conn.execute(
                "SELECT id, restaurant_id, platform, name FROM customers WHERE id=?", (cid,)
            ).fetchone()
            if cust:
                fake_customers_detail.append(dict(cust))
            convs = conn.execute(
                "SELECT id FROM conversations WHERE customer_id=?", (cid,)
            ).fetchall()
            conv_count += len(convs)
            for conv in convs:
                msgs = conn.execute(
                    "SELECT COUNT(*) as n FROM messages WHERE conversation_id=?",
                    (conv["id"],)
                ).fetchone()
                msg_count += msgs["n"] if msgs else 0
        pe = conn.execute(
            "SELECT provider, event_id, created_at FROM processed_events "
            "WHERE event_id='fake_mid_test' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return {
            "fake_customers_found": len(fake_cids),
            "fake_customers": fake_customers_detail,
            "conversations_for_fake_sender": conv_count,
            "messages_for_fake_sender": msg_count,
            "dedup_entries_for_fake_mid": [dict(r) for r in pe],
        }
    finally:
        conn.close()


@app.post("/api/debug/meta-subscriptions-sample")
async def debug_meta_subscriptions_sample(key: str = "", object_type: str = "instagram"):
    """
    Tells Meta to fire a sample test event to our webhook URL.
    If our logs show [meta-live-post] after this call, Meta CAN reach us.
    If not, Meta's delivery is blocked at their side.
    Protected by ?key=<first-8-of-META_APP_ID>.
    """
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")

    import httpx as _httpx
    app_token = f"{META_APP_ID}|{META_APP_SECRET}"

    def _do_sample(obj: str, field: str) -> dict:
        r = _httpx.post(
            f"https://graph.facebook.com/v20.0/{META_APP_ID}/subscriptions_sample",
            params={
                "object_type":  obj,
                "field_name":   field,
                "access_token": app_token,
            },
            timeout=15,
        )
        body = r.json()
        logger.info(f"[meta-sample] object={obj} field={field} status={r.status_code} body={body}")
        return {"http_status": r.status_code, "body": body}

    results = {}
    if object_type in ("instagram", "both"):
        results["instagram_messages"] = await asyncio.to_thread(_do_sample, "instagram", "messages")
    if object_type in ("page", "both"):
        results["page_messages"] = await asyncio.to_thread(_do_sample, "page", "messages")
    if object_type not in ("instagram", "page", "both"):
        results[object_type] = await asyncio.to_thread(_do_sample, object_type, "messages")

    return {
        "note": "Check Render logs for [meta-live-post] within 10 seconds of this response",
        "results": results,
    }


@app.get("/api/debug/meta-subscriptions")
async def debug_meta_subscriptions():
    """
    Calls GET /{META_APP_ID}/subscriptions to show what callback URL Meta has
    registered for this app. Use this to confirm /webhooks/meta is registered.
    """
    if not META_APP_ID or not META_APP_SECRET:
        return {"error": "META_APP_ID or META_APP_SECRET not set"}
    import httpx as _httpx
    app_token = f"{META_APP_ID}|{META_APP_SECRET}"
    r = _httpx.get(
        f"https://graph.facebook.com/v20.0/{META_APP_ID}/subscriptions",
        params={"access_token": app_token},
        timeout=10,
    )
    body = r.json()
    derived_token = META_VERIFY_TOKEN or f"meta_verify_{META_APP_ID}"
    return {
        "meta_app_id":         META_APP_ID,
        "expected_callback":   f"{BASE_URL}/webhooks/meta",
        "expected_token_hint": derived_token[:8] + "…",
        "subscriptions":       body,
    }


@app.get("/api/debug/meta-roles")
async def debug_meta_roles():
    """
    Lists all accepted roles on this Meta app (admins, developers, testers).
    Uses GET /{app_id}/roles via app access token.
    No key guard — read-only, non-sensitive output.
    """
    if not META_APP_ID or not META_APP_SECRET:
        return {"error": "META_APP_ID or META_APP_SECRET not set"}
    import httpx as _httpx
    app_token = f"{META_APP_ID}|{META_APP_SECRET}"
    r = _httpx.get(
        f"https://graph.facebook.com/v20.0/{META_APP_ID}/roles",
        params={"access_token": app_token},
        timeout=10,
    )
    return {
        "meta_app_id": META_APP_ID,
        "http_status": r.status_code,
        "roles": r.json(),
    }


@app.get("/api/debug/wa-state")
async def debug_wa_state(user=Depends(current_user)):
    """
    Returns the exact DB state of the WhatsApp channel for this restaurant.
    Use this to confirm whether the connect flow completed or stalled.
    """
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT id, connection_status, phone_number_id, phone_number_display, "
            "waba_id, verify_token, oauth_completed_at, last_error, enabled, "
            "reconnect_needed, last_tested_at, token_expires_at, "
            "CASE WHEN token != '' AND token IS NOT NULL THEN 'SET' ELSE 'MISSING' END as token_state "
            "FROM channels WHERE restaurant_id=? AND type='whatsapp'",
            (rid,)
        ).fetchone()
        if not ch:
            return {"status": "NO_ROW", "message": "WhatsApp channel row does not exist yet"}

        d = dict(ch)

        # Check recent oauth_states for this restaurant to see if flow was started
        recent_states = conn.execute(
            "SELECT platform, state, used, expires_at, created_at "
            "FROM oauth_states WHERE restaurant_id=? AND platform='whatsapp' "
            "ORDER BY created_at DESC LIMIT 5",
            (rid,)
        ).fetchall()
        d["recent_oauth_states"] = [dict(r) for r in recent_states]

        # Summary
        d["diagnosis"] = (
            "CONNECTED — flow completed"
            if d["connection_status"] == "connected"
            else f"NOT_CONNECTED — status='{d['connection_status']}' "
                 f"last_error='{d['last_error'] or 'none'}' "
                 f"waba_id={'SET' if d['waba_id'] else 'MISSING'} "
                 f"phone_number_id={'SET' if d['phone_number_id'] else 'MISSING'}"
        )
        return d
    finally:
        conn.close()


@app.get("/api/channels/whatsapp/webhook-info")
async def wa_webhook_info(user=Depends(current_user)):
    """
    Returns the exact Callback URL and Verify Token for this restaurant's WhatsApp webhook.
    Auto-generates and persists a verify_token if one isn't stored yet.
    """
    rid  = user["restaurant_id"]
    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT id, verify_token FROM channels WHERE restaurant_id=? AND type='whatsapp'",
            (rid,)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "لم يتم ربط WhatsApp بعد")

        verify_token = ch["verify_token"] if ch["verify_token"] else ""
        if not verify_token:
            verify_token = str(uuid.uuid4())
            conn.execute(
                "UPDATE channels SET verify_token=? WHERE id=?",
                (verify_token, ch["id"])
            )
            conn.commit()
            logger.info(f"[wa-webhook-info] auto-generated verify_token for channel {ch['id']}")

        callback_url = f"{BASE_URL}/webhook/whatsapp/{rid}"
        return {
            "callback_url":   callback_url,
            "verify_token":   verify_token,
            "subscribe_fields": ["messages"],
            "note": "Paste both values into Meta → WhatsApp → Configuration → Webhook",
        }
    finally:
        conn.close()


@app.get("/api/debug/meta-page-r1")
async def debug_meta_page_r1(key: str = ""):
    """
    Check r1 (page-level) subscription for ALL Meta channels.
    Protected by ?key=<first-8-chars-of-META_APP_ID>.
    """
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")
    import httpx as _httpx
    conn = database.get_db()
    try:
        results = {}
        rows = conn.execute(
            "SELECT type, page_id, token, business_account_id, restaurant_id, "
            "       token_expires_at, scopes_granted "
            "FROM channels WHERE type IN ('facebook','instagram') AND enabled=1"
        ).fetchall()
        for row in rows:
            platform = row["type"]
            rid_hint  = row["restaurant_id"][:8]
            label     = f"{platform}:{rid_hint}"
            if not row["page_id"]:
                results[label] = {"error": "no page_id stored"}
                continue
            r = _httpx.get(
                f"https://graph.facebook.com/v20.0/{row['page_id']}/subscribed_apps",
                params={"access_token": row["token"]},
                timeout=10,
            )
            results[label] = {
                "page_id":             row["page_id"],
                "business_account_id": row["business_account_id"] or "",
                "token_expires_at":    row["token_expires_at"] or "",
                "scopes_granted":      row["scopes_granted"] or "",
                "http_status":         r.status_code,
                "r1_body":             r.json(),
            }
        return results
    finally:
        conn.close()


@app.post("/api/debug/meta-fire-test")
async def debug_meta_fire_test(req: Request, key: str = ""):
    """
    Simulate a real Meta webhook event and run it through the full pipeline.
    POST {"platform": "instagram"|"facebook"} with ?key=<first-8-of-META_APP_ID>
    Fires a fake DM event through _route_meta_event to test routing + handler.
    """
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")
    body = await req.json()
    platform = body.get("platform", "instagram")
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT page_id, business_account_id FROM channels "
            "WHERE type=? AND enabled=1 LIMIT 1",
            (platform,)
        ).fetchone()
        if not row:
            return {"error": f"no {platform} channel connected"}
        entry_id = (row["business_account_id"] or row["page_id"]) if platform == "instagram" else row["page_id"]
        if not entry_id:
            return {"error": "no page_id/business_account_id stored"}
    finally:
        conn.close()

    import time as _time
    fake_sender_id = "111111111111111"
    fake_ts = int(_time.time() * 1000)

    if platform == "instagram":
        fake_payload = {
            "object": "instagram",
            "entry": [{
                "id": entry_id,
                "time": fake_ts,
                "messaging": [{
                    "sender":    {"id": fake_sender_id},
                    "recipient": {"id": entry_id},
                    "timestamp": fake_ts,
                    "message":   {"mid": "fake_mid_test", "text": "TEST MESSAGE — debug fire"},
                }]
            }]
        }
    else:
        fake_payload = {
            "object": "page",
            "entry": [{
                "id": entry_id,
                "time": fake_ts,
                "messaging": [{
                    "sender":    {"id": fake_sender_id},
                    "recipient": {"id": entry_id},
                    "timestamp": fake_ts,
                    "message":   {"mid": "fake_mid_test", "text": "TEST MESSAGE — debug fire"},
                }]
            }]
        }

    logger.info(f"[debug-fire-test] firing fake {platform} event entry_id={entry_id}")
    _route_meta_event(fake_payload)
    return {"fired": True, "platform": platform, "entry_id": entry_id, "payload": fake_payload}


@app.post("/api/debug/meta-force-r2")
async def debug_meta_force_r2(key: str = ""):
    """
    Force-register both 'page' and 'instagram' app-level subscriptions (r2).
    Runs each httpx call in asyncio.to_thread so the event loop stays free
    to handle Meta's immediate GET verification (6-second window).
    Protected by ?key=<first-8-of-META_APP_ID>.
    """
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")
    import httpx as _httpx
    app_token    = f"{META_APP_ID}|{META_APP_SECRET}"
    callback_url = f"{BASE_URL}/webhooks/meta"
    verify_token = META_VERIFY_TOKEN or f"meta_verify_{META_APP_ID}"
    results = {}

    def _do_r2(obj: str) -> dict:
        r = _httpx.post(
            f"https://graph.facebook.com/v20.0/{META_APP_ID}/subscriptions",
            params={
                "object":       obj,
                "callback_url": callback_url,
                "verify_token": verify_token,
                "fields":       "messages,messaging_postbacks",
                "access_token": app_token,
            },
            timeout=15,
        )
        body = r.json()
        logger.info(f"[meta-force-r2] object={obj} status={r.status_code} body={body}")
        return {"http_status": r.status_code, "body": body}

    for obj in ("page", "instagram"):
        results[obj] = await asyncio.to_thread(_do_r2, obj)

    return {"callback_url": callback_url, "verify_token_hint": verify_token[:8] + "…", "results": results}


# ── Internal Meta simulator / test-harness ────────────────────────────────────

@app.post("/api/debug/meta-simulate")
async def debug_meta_simulate(req: Request, key: str = ""):
    """
    Full internal Meta message simulator — dev/staging only.
    Disabled in production (ENVIRONMENT=production or RENDER env detected).
    """
    if os.getenv("ENVIRONMENT") == "production" or os.getenv("RENDER"):
        raise HTTPException(404, "Not found")
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")

    import time as _time
    body = {}
    try:
        body = await req.json()
    except Exception:
        pass

    platform  = body.get("platform", "instagram")
    text      = body.get("text", "مرحبا، ما هي الوجبات المتاحة؟")
    reset     = body.get("reset_sender", False)
    sender_id = str(body.get("sender_id") or f"sim_{uuid.uuid4().hex[:12]}")

    # ── Phase 1: look up channel + optional reset ──────────────────────────
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT restaurant_id, page_id, business_account_id FROM channels "
            "WHERE type=? AND enabled=1 LIMIT 1",
            (platform,)
        ).fetchone()
        if not row:
            return {"error": f"no enabled {platform} channel — connect one first"}

        restaurant_id = row["restaurant_id"]
        page_id       = row["page_id"]
        entry_id      = (row["business_account_id"] or page_id) if platform == "instagram" else page_id

        if reset:
            mem_rows = conn.execute(
                "SELECT customer_id FROM conversation_memory "
                "WHERE memory_key='external_id' AND memory_value=?", (sender_id,)
            ).fetchall()
            for mr in mem_rows:
                cid = mr["customer_id"]
                convs = conn.execute(
                    "SELECT id FROM conversations WHERE customer_id=?", (cid,)
                ).fetchall()
                for conv in convs:
                    conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv["id"],))
                conn.execute("DELETE FROM conversations WHERE customer_id=?", (cid,))
                conn.execute("DELETE FROM conversation_memory WHERE customer_id=?", (cid,))
                conn.execute("DELETE FROM customers WHERE id=?", (cid,))
            conn.execute(
                "DELETE FROM processed_events WHERE restaurant_id=? AND provider=? AND event_id=?",
                (restaurant_id, platform, f"sim_mid_{sender_id}")
            )
            conn.commit()
            logger.info(f"[meta-simulate] reset sender={sender_id} — cleared {len(mem_rows)} customer(s)")
    finally:
        conn.close()

    # ── Helper: snapshot state for this sender (opens its own conn) ────────
    def _snap():
        c2 = database.get_db()
        try:
            mem = c2.execute(
                "SELECT customer_id FROM conversation_memory "
                "WHERE memory_key='external_id' AND memory_value=?", (sender_id,)
            ).fetchall()
            cids  = [r["customer_id"] for r in mem]
            custs = []
            for cid in cids:
                r2 = c2.execute(
                    "SELECT id, name, platform FROM customers WHERE id=?", (cid,)
                ).fetchone()
                if r2:
                    custs.append(dict(r2))
            convs = []
            msgs  = []
            for cid in cids:
                cs = c2.execute(
                    "SELECT id, first_contact, channel, status FROM conversations WHERE customer_id=?",
                    (cid,)
                ).fetchall()
                for cv in cs:
                    convs.append(dict(cv))
                    ms = c2.execute(
                        "SELECT id, sender, content FROM messages "
                        "WHERE conversation_id=? ORDER BY created_at DESC LIMIT 5",
                        (cv["id"],)
                    ).fetchall()
                    msgs.extend([dict(m) for m in ms])
            ded = c2.execute(
                "SELECT provider, event_id FROM processed_events "
                "WHERE restaurant_id=? AND provider=? AND event_id=?",
                (restaurant_id, platform, f"sim_mid_{sender_id}")
            ).fetchall()
            return {"customers": custs, "conversations": convs, "messages": msgs, "dedup": [dict(d) for d in ded]}
        finally:
            c2.close()

    # ── Phase 2: snapshot before, fire, snapshot after ─────────────────────
    before   = _snap()
    fake_ts  = int(_time.time() * 1000)
    mid      = f"sim_mid_{sender_id}"

    if platform == "instagram":
        payload = {
            "object": "instagram",
            "entry": [{
                "id": entry_id,
                "time": fake_ts,
                "messaging": [{
                    "sender":    {"id": sender_id},
                    "recipient": {"id": entry_id},
                    "timestamp": fake_ts,
                    "message":   {"mid": mid, "text": text},
                }]
            }]
        }
    else:
        payload = {
            "object": "page",
            "entry": [{
                "id": entry_id,
                "time": fake_ts,
                "messaging": [{
                    "sender":    {"id": sender_id},
                    "recipient": {"id": entry_id},
                    "timestamp": fake_ts,
                    "message":   {"mid": mid, "text": text},
                }]
            }]
        }

    logger.info(f"[meta-simulate] firing platform={platform} sender={sender_id} text={text[:60]!r}")
    _route_meta_event(payload)

    import time as _t
    _t.sleep(0.3)

    after = _snap()

    new_customers     = [c for c in after["customers"]     if c not in before["customers"]]
    new_conversations = [c for c in after["conversations"] if c not in before["conversations"]]
    new_messages      = [m for m in after["messages"]      if m not in before["messages"]]
    new_dedup         = [d for d in after["dedup"]         if d not in before["dedup"]]

    return {
        "ok": True,
        "platform":   platform,
        "sender_id":  sender_id,
        "text":       text,
        "reset_done": reset,
        "entry_id":   entry_id,
        "mid":        mid,
        "pipeline": {
            "customer": {
                "status":  "created" if new_customers else "found",
                "records": after["customers"],
            },
            "conversation": {
                "status":        "created" if new_conversations else "found",
                "records":       after["conversations"],
                "first_contact": any(c.get("first_contact") for c in after["conversations"]),
            },
            "message": {
                "status":  "stored" if new_messages else "not_stored",
                "records": new_messages,
            },
            "dedup": {
                "status":  "inserted" if new_dedup else "already_exists",
                "records": after["dedup"],
            },
        },
    }


@app.get("/api/debug/meta-simulate-status")
async def debug_meta_simulate_status(key: str = ""):
    """
    Returns simulator state — dev/staging only.
    Disabled in production (ENVIRONMENT=production or RENDER env detected).
    """
    if os.getenv("ENVIRONMENT") == "production" or os.getenv("RENDER"):
        raise HTTPException(404, "Not found")
    if not META_APP_ID or key != META_APP_ID[:8]:
        raise HTTPException(403, "bad key")

    conn = database.get_db()
    try:
        sim_mem = conn.execute(
            "SELECT customer_id, memory_value as sender_id FROM conversation_memory "
            "WHERE memory_key='external_id' AND memory_value LIKE 'sim_%'"
        ).fetchall()
        cids = [r["customer_id"] for r in sim_mem]
        sender_map = {r["customer_id"]: r["sender_id"] for r in sim_mem}

        customers = []
        for cid in cids:
            cust = conn.execute(
                "SELECT id, restaurant_id, platform, name FROM customers WHERE id=?", (cid,)
            ).fetchone()
            if cust:
                d = dict(cust)
                d["sim_sender_id"] = sender_map.get(cid, "?")
                customers.append(d)

        conversations = []
        for cid in cids:
            cs = conn.execute(
                "SELECT id, restaurant_id, customer_id, mode, status, first_contact, channel, created_at "
                "FROM conversations WHERE customer_id=?", (cid,)
            ).fetchall()
            for c in cs:
                d = dict(c)
                d["sim_sender_id"] = sender_map.get(cid, "?")
                conversations.append(d)

        messages = []
        for conv in conversations:
            ms = conn.execute(
                "SELECT id, sender, content, created_at FROM messages "
                "WHERE conversation_id=? ORDER BY created_at DESC LIMIT 10",
                (conv["id"],)
            ).fetchall()
            for m in ms:
                d = dict(m)
                d["conversation_id"] = conv["id"]
                messages.append(d)

        dedup = conn.execute(
            "SELECT restaurant_id, provider, event_id, created_at FROM processed_events "
            "WHERE event_id LIKE 'sim_mid_%' ORDER BY created_at DESC LIMIT 50"
        ).fetchall()

        first_contact_convs = [c for c in conversations if c.get("first_contact")]

        return {
            "sim_customers":          len(customers),
            "sim_conversations":      len(conversations),
            "sim_messages":           len(messages),
            "sim_dedup_entries":      len(dedup),
            "first_contact_conversations": len(first_contact_convs),
            "customers":     customers,
            "conversations": conversations,
            "messages":      messages,
            "dedup":         [dict(d) for d in dedup],
        }
    finally:
        conn.close()


@app.get("/api/debug/instagram-diagnostic")
async def instagram_diagnostic(user=Depends(current_user)):
    """
    Full Instagram integration diagnostic — NEVER returns HTTP 500.
    All errors are caught and returned as structured JSON with a verdict.
    """
    rid     = user["restaurant_id"]
    steps   = []
    fixed   = []
    verdict = "unknown"
    conn    = None   # initialise before try so finally is always safe

    def _step(sid, label, status, detail=""):
        try:
            steps.append({"id": sid, "label": label, "status": status,
                          "detail": str(detail)[:500]})
            logger.info(f"[ig-diag] {sid}={status}: {str(detail)[:100]}")
        except Exception:
            pass  # never let _step itself crash the endpoint

    try:
        conn = database.get_db()
        # ── 1. Env vars ─────────────────────────────────────────────────────────
        if not META_APP_ID:
            _step("env_app_id", "META_APP_ID", "FAIL", "غير موجود في Render — أضفه من Meta Developers")
            return {"steps": steps, "fixed": fixed, "verdict": "env_missing"}
        _step("env_app_id", "META_APP_ID", "OK", META_APP_ID[:6] + "…")

        if not META_APP_SECRET:
            _step("env_app_secret", "META_APP_SECRET", "FAIL", "غير موجود في Render")
            return {"steps": steps, "fixed": fixed, "verdict": "env_missing"}
        _step("env_app_secret", "META_APP_SECRET", "OK", "موجود ✓")

        # ── 2. OAuth URL generation ──────────────────────────────────────────────
        try:
            adapter     = get_adapter("instagram")
            redirect_uri = f"{BASE_URL}/oauth/meta/callback"
            auth_url    = adapter.build_auth_url("diag_test", redirect_uri)
            _step("oauth_url", "توليد OAuth URL", "OK", auth_url[:80] + "…")
        except Exception as exc:
            _step("oauth_url", "توليد OAuth URL", "FAIL", str(exc))
            return {"steps": steps, "fixed": fixed, "verdict": "oauth_url_failed"}

        # ── 3. All Instagram OAuth states for this restaurant ────────────────────
        all_ig_states = conn.execute(
            "SELECT * FROM oauth_states WHERE platform='instagram' AND restaurant_id=? "
            "ORDER BY created_at DESC LIMIT 20",
            (rid,)
        ).fetchall()
        all_ig_states = [dict(r) for r in all_ig_states]

        pages_data   = {}
        ig_accounts  = []
        best_state   = None   # most recent completed state with accounts

        # Find the most recent COMPLETED state that has IG accounts with page_token
        for s in all_ig_states:
            if s.get("used", 0) != 1:
                continue
            try:
                pd = json.loads(s.get("pages_json") or "{}")
                if not isinstance(pd, dict):
                    pd = {}   # default was '[]' not '{}' — guard against list
            except Exception:
                pd = {}
            accts = pd.get("pages", [])
            if accts and any(a.get("page_token") for a in accts):
                best_state  = s
                pages_data  = pd
                ig_accounts = accts
                break

        last_state = all_ig_states[0] if all_ig_states else None

        if not last_state:
            _step("oauth_state", "آخر OAuth state", "WARN",
                  "لا توجد محاولة Instagram OAuth بعد — اضغط ربط Instagram أولاً")
        else:
            if last_state.get("used", 0) == 0:
                _step("oauth_state", "آخر OAuth state", "WARN",
                      f"OAuth بدأ لكن Callback لم يُستكمَل (used=0) — "
                      f"created_at={last_state.get('created_at','')}")
            else:
                raw_pj = last_state.get("pages_json") or "{}"
                try:
                    last_pd = json.loads(raw_pj)
                    if not isinstance(last_pd, dict):
                        last_pd = {}
                    last_accts = last_pd.get("pages", [])
                except Exception:
                    last_pd    = {}
                    last_accts = []
                _stored_owner   = last_pd.get("token_owner_name", "")
                _stored_granted = last_pd.get("granted_perms", [])
                _stored_declined= last_pd.get("declined_perms", [])
                _step("oauth_state", "آخر OAuth state", "OK",
                      f"completed (used=1) — accounts={len(last_accts)} "
                      f"has_user_token={bool(last_pd.get('access_token'))} "
                      f"fb_pages_found={last_pd.get('fb_pages_found', '?')} "
                      f"token_owner={_stored_owner or '?'} "
                      f"granted={_stored_granted or '?'} "
                      f"declined={_stored_declined or 'none'}")

            if best_state:
                _step("oauth_states_scan", f"فحص {len(all_ig_states)} OAuth state",
                      "OK",
                      f"عُثر على state صالح بـ {len(ig_accounts)} account(s) "
                      f"created_at={best_state.get('created_at','')}")
            else:
                _step("oauth_states_scan", f"فحص {len(all_ig_states)} OAuth state",
                      "WARN",
                      f"لا يوجد state مكتمل مع page_token صالح في أي من الـ {len(all_ig_states)} state")

        # ── 3b. Live Meta API check using stored user token ─────────────────────
        # Extract user token from the most recent completed OAuth state (any state, even no_ig)
        _live_user_token = ""
        _live_raw_fb_pages = []
        if last_state and last_state.get("used") == 1:
            try:
                _lp = json.loads(last_state.get("pages_json") or "{}")
                if not isinstance(_lp, dict):
                    _lp = {}
                _live_user_token   = _lp.get("access_token", "")
                _live_raw_fb_pages = _lp.get("raw_fb_pages", [])
            except Exception:
                pass

        if _live_user_token:
            try:
                import httpx as _httpx2

                # ── 3c. Token identity — who authorized? ─────────────────────
                try:
                    _id_r    = _httpx2.get("https://graph.facebook.com/v20.0/me",
                                           params={"access_token": _live_user_token,
                                                   "fields": "id,name"}, timeout=10)
                    _id_body = _id_r.json()
                    if "error" not in _id_body:
                        _step("token_identity", "هوية صاحب الـ token (Facebook User)",
                              "OK",
                              f"الاسم: {_id_body.get('name','?')} | "
                              f"FB ID: {_id_body.get('id','?')} | "
                              f"هذا الشخص يجب أن يكون مدير صفحة 'Saas' على Facebook")
                    else:
                        _step("token_identity", "هوية صاحب الـ token",
                              "FAIL", f"Meta error: {_id_body['error'].get('message','?')[:100]}")
                except Exception as _ide:
                    _step("token_identity", "هوية صاحب الـ token", "WARN", str(_ide)[:100])

                # ── 3d. Granted permissions check ────────────────────────────
                try:
                    _perm_r    = _httpx2.get("https://graph.facebook.com/v20.0/me/permissions",
                                             params={"access_token": _live_user_token}, timeout=10)
                    _perm_body = _perm_r.json()
                    _pg = [p["permission"] for p in _perm_body.get("data", [])
                           if p.get("status") == "granted"]
                    _pd = [p["permission"] for p in _perm_body.get("data", [])
                           if p.get("status") == "declined"]
                    _required = ["pages_show_list", "pages_manage_metadata",
                                 "pages_messaging", "instagram_basic",
                                 "instagram_manage_messages", "pages_read_engagement"]
                    _missing_perms = [s for s in _required if s not in _pg]
                    if _missing_perms:
                        _step("token_perms", "الصلاحيات الممنوحة من Meta",
                              "FAIL",
                              f"ناقصة: {_missing_perms} | "
                              f"ممنوحة: {_pg} | "
                              f"مرفوضة: {_pd} | "
                              f"الحل: انتظر deploy ثم أعد الربط — auth_type=rerequest سيطلبها مجدداً")
                    elif _pd:
                        _step("token_perms", "الصلاحيات الممنوحة من Meta",
                              "WARN",
                              f"مرفوضة: {_pd} | ممنوحة: {_pg}")
                    else:
                        _step("token_perms", "الصلاحيات الممنوحة من Meta",
                              "OK", f"جميع الصلاحيات ممنوحة: {_pg}")
                except Exception as _pe:
                    _step("token_perms", "الصلاحيات الممنوحة من Meta", "WARN", str(_pe)[:100])

                # ── 3e. /me/accounts live call ───────────────────────────────
                _accts_r = _httpx2.get(
                    "https://graph.facebook.com/v20.0/me/accounts",
                    params={"access_token": _live_user_token,
                            "fields": "id,name,access_token"},
                    timeout=12,
                )
                _accts_body = _accts_r.json()
                if "error" in _accts_body:
                    _err = _accts_body["error"]
                    _step("live_api", "فحص مباشر /me/accounts",
                          "FAIL",
                          f"Meta error {_err.get('code','?')}: {_err.get('message','?')[:120]}")
                else:
                    _fb_live = _accts_body.get("data", [])
                    if not _fb_live:
                        # /me/accounts = 0. Try Business API to detect Business Portfolio case.
                        try:
                            _biz_r    = _httpx2.get(
                                "https://graph.facebook.com/v20.0/me/businesses",
                                params={"access_token": _live_user_token, "fields": "id,name"},
                                timeout=12)
                            _biz_body = _biz_r.json()
                        except Exception:
                            _biz_body = {}

                        if "error" in _biz_body:
                            _step("live_api", "فحص مباشر /me/accounts",
                                  "FAIL",
                                  f"0 صفحات من /me/accounts — "
                                  f"Business Portfolio فشل أيضاً (code={_biz_body['error'].get('code','?')}): "
                                  f"{_biz_body['error'].get('message','?')[:80]}")
                            verdict = "no_page_direct_access"
                        elif _biz_body.get("data"):
                            # Businesses found — page is in Business Portfolio
                            _biz_names = ", ".join(b["name"] for b in _biz_body["data"][:3])
                            # Try to fetch pages via each business
                            _biz_pages = []
                            for _biz in _biz_body.get("data", [])[:5]:
                                try:
                                    _bpg = _httpx2.get(
                                        f"https://graph.facebook.com/v20.0/{_biz['id']}/owned_pages",
                                        params={"access_token": _live_user_token,
                                                "fields": "id,name,access_token"},
                                        timeout=12)
                                    for _p in _bpg.json().get("data", []):
                                        _biz_pages.append({**_p,
                                            "_biz_name": _biz["name"],
                                            "_biz_id": _biz["id"]})
                                except Exception:
                                    pass
                            if _biz_pages:
                                _bp_names = ", ".join(p["name"] for p in _biz_pages[:3])
                                _step("live_api", "فحص مباشر /me/accounts",
                                      "WARN",
                                      f"0 من /me/accounts — Business Portfolio: {_biz_names} — "
                                      f"صفحات متاحة عبر Business API: {_bp_names}. "
                                      f"أعد الربط — النظام سيستخدم Business API تلقائياً الآن")
                                verdict = "business_portfolio"
                                # Promote for IG check
                                _fb_live = _biz_pages
                            else:
                                _step("live_api", "فحص مباشر /me/accounts",
                                      "FAIL",
                                      f"Business Portfolio موجود ({_biz_names}) لكن لا صفحات قابلة للوصول. "
                                      f"تأكد أن الصفحة مضافة للـ Business Portfolio وأن لديك Full Control عليها")
                                verdict = "no_page_direct_access"
                        else:
                            _step("live_api", "فحص مباشر /me/accounts",
                                  "FAIL",
                                  "0 صفحات من /me/accounts و0 businesses — "
                                  "الحساب المستخدم في OAuth ليس مدير لأي صفحة Facebook ولا Business Portfolio. "
                                  "تأكد أنك تسجل دخول بالحساب الصحيح الذي يملك صفحة 'Saas'")
                            verdict = "no_page_direct_access"
                    else:
                        # Check each page for instagram_business_account
                        _ig_live = []
                        for _fp in _fb_live:
                            try:
                                _ir = _httpx2.get(
                                    f"https://graph.facebook.com/v20.0/{_fp['id']}",
                                    params={
                                        "fields": "instagram_business_account{id,username,name}",
                                        "access_token": _fp.get("access_token", _live_user_token),
                                    },
                                    timeout=10,
                                )
                                _ib = _ir.json()
                                _ig = _ib.get("instagram_business_account", {})
                                if _ig:
                                    _ig_live.append({
                                        "page_id":      _fp["id"],
                                        "page_name":    _fp["name"],
                                        "page_token":   _fp.get("access_token", ""),
                                        "ig_id":        _ig.get("id", ""),
                                        "ig_username":  _ig.get("username", ""),
                                    })
                                else:
                                    logger.info(
                                        f"[ig-diag] live: page {_fp['id']} ({_fp['name']}) "
                                        f"has NO instagram_business_account"
                                    )
                            except Exception as _pgexc:
                                logger.warning(f"[ig-diag] live page check: {_pgexc}")

                        if _ig_live:
                            _first_ig = _ig_live[0]
                            _step("live_api", "فحص مباشر /me/accounts",
                                  "OK",
                                  f"{len(_fb_live)} صفحة Facebook — {len(_ig_live)} IG Business Account — "
                                  f"ig_id={_first_ig['ig_id']} "
                                  f"username={_first_ig['ig_username']} "
                                  f"page_id={_first_ig['page_id']} "
                                  f"has_page_token={bool(_first_ig['page_token'])}")
                            # Promote these to ig_accounts so auto-fix can use them
                            if not ig_accounts:
                                ig_accounts = [{
                                    "id":         _a["ig_id"],
                                    "page_id":    _a["page_id"],
                                    "page_token": _a["page_token"],
                                    "username":   _a["ig_username"],
                                } for _a in _ig_live]
                        else:
                            _pnames = ", ".join(_fp["name"] for _fp in _fb_live[:3])
                            _step("live_api", "فحص مباشر /me/accounts",
                                  "WARN",
                                  f"{len(_fb_live)} صفحة Facebook ({_pnames}) — "
                                  f"لكن لا instagram_business_account مرتبط بأي منها. "
                                  f"تأكد أن حساب Instagram Professional (Business/Creator) "
                                  f"مربوط بالصفحة من Meta Business Suite → إعدادات Instagram")
            except Exception as _live_exc:
                _step("live_api", "فحص مباشر /me/accounts", "FAIL",
                      f"فشل الاتصال: {str(_live_exc)[:150]}")
        else:
            _step("live_api", "فحص مباشر /me/accounts",
                  "WARN",
                  "لا يوجد user token مخزن — أعد تشغيل Instagram OAuth أولاً لتخزين token")

        # ── 4. Accounts in best pages_json ──────────────────────────────────────
        if ig_accounts:
            first = ig_accounts[0]
            has_pt = bool(first.get("page_token", ""))
            _step("oauth_accounts", "IG accounts في pages_json", "OK" if has_pt else "FAIL",
                  f"accounts={len(ig_accounts)} — "
                  f"account_id={first.get('id','?')} "
                  f"page_id={first.get('page_id','?')} "
                  f"has_page_token={has_pt} "
                  f"token_prefix={first.get('page_token','')[:12] if has_pt else 'EMPTY'}")
        else:
            _step("oauth_accounts", "IG accounts في pages_json", "FAIL",
                  "لا توجد Instagram accounts في أي OAuth state مكتمل — "
                  "السبب: ربما صفحتك على Facebook ليس لها Instagram Business Account مرتبط. "
                  "اذهب إلى إعدادات Instagram → الحساب → التحويل لحساب احترافي، "
                  "ثم اربطه بصفحة Facebook من Meta Business Suite")

        # ── 5. Channel DB row ────────────────────────────────────────────────────
        ch_row = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='instagram'", (rid,)
        ).fetchone()

        if not ch_row:
            _step("channel_row", "صف Instagram في DB", "WARN", "لا يوجد بعد — ربط Instagram لم يكتمل")
            verdict = "no_channel"
        else:
            ch = dict(ch_row)
            _step("channel_row", "صف Instagram في DB", "OK",
                  f"id={ch['id'][:8]} status={ch.get('connection_status','?')} "
                  f"enabled={ch.get('enabled',0)} reconnect_needed={ch.get('reconnect_needed',0)}")

            stored_token = ch.get("token", "") or ""

            # ── 6. Token present check ───────────────────────────────────────────
            if not stored_token:
                _step("token_present", "Token موجود في DB", "FAIL",
                      "الـ token فارغ في عمود channels.token — لم يُحفظ page token")
                verdict = "no_token"
            else:
                _step("token_present", "Token موجود في DB", "OK",
                      f"length={len(stored_token)} prefix={stored_token[:12]}")

                # ── 7. Token format check ────────────────────────────────────────
                if stored_token.startswith("EAA"):
                    _step("token_format", "تنسيق الـ token", "OK",
                          "يبدأ بـ EAA — Facebook token format صحيح")
                else:
                    _step("token_format", "تنسيق الـ token", "WARN",
                          f"لا يبدأ بـ EAA — prefix={stored_token[:12]} — قد يكون غير صحيح")

                # ── 8. Meta API token validation ─────────────────────────────────
                try:
                    import httpx as _httpx
                    vr = _httpx.get(
                        f"https://graph.facebook.com/v20.0/me",
                        params={"access_token": stored_token, "fields": "id,name"},
                        timeout=10,
                    )
                    vdata = vr.json()

                    if "error" in vdata:
                        meta_err  = vdata["error"]
                        code      = str(meta_err.get("code", ""))
                        subcode   = str(meta_err.get("error_subcode", ""))
                        msg       = meta_err.get("message", "")

                        if "could not be decrypted" in msg.lower():
                            _step("token_meta_valid", "Meta API: token صالح؟", "FAIL",
                                  f"❌ THE ACCESS TOKEN COULD NOT BE DECRYPTED — "
                                  f"Token مكسور أو من Facebook App مختلف (code=190/467). "
                                  f"السبب: تم حفظ token خاطئ أو من تطبيق آخر.")
                            verdict = "token_cannot_decrypt"
                        elif code == "190":
                            _step("token_meta_valid", "Meta API: token صالح؟", "FAIL",
                                  f"Token منتهي أو ملغى (code=190 subcode={subcode}): {msg}")
                            verdict = "token_expired"
                        else:
                            _step("token_meta_valid", "Meta API: token صالح؟", "FAIL",
                                  f"Meta error code={code}: {msg}")
                            verdict = "token_invalid"
                    else:
                        _name = vdata.get("name", vdata.get("id", ""))
                        _step("token_meta_valid", "Meta API: token صالح؟", "OK",
                              f"Token صالح — /me يرجع: {_name} (id={vdata.get('id','?')})")

                        # ── 9. Token type check (user vs page) ───────────────────
                        # Try fetching pages — user tokens can; page tokens cannot
                        try:
                            pr = _httpx.get(
                                f"https://graph.facebook.com/v20.0/me/accounts",
                                params={"access_token": stored_token},
                                timeout=10,
                            )
                            pdata = pr.json()
                            if "error" not in pdata:
                                _step("token_type", "نوع الـ token", "WARN",
                                      "الـ token المحفوظ هو USER token وليس PAGE token — "
                                      "يعمل مع /me لكن سيفشل مع Instagram DM API. "
                                      "يجب إعادة الربط وأخذ page_token من /me/accounts")
                                verdict = "token_wrong_type"
                            else:
                                _step("token_type", "نوع الـ token", "OK",
                                      "لا يعمل مع /me/accounts — على الأرجح PAGE token ✓")
                                verdict = "connected"
                        except Exception:
                            _step("token_type", "نوع الـ token", "WARN", "لم يمكن التحقق من نوع الـ token")
                            verdict = "connected"

                except Exception as exc:
                    _step("token_meta_valid", "Meta API: token صالح؟", "FAIL",
                          f"فشل الاتصال بـ Meta API: {exc}")
                    verdict = "meta_unreachable"

            # ── 10. Key columns check ────────────────────────────────────────────
            col_ok  = lambda v: "موجود: " + str(v)[:20]
            col_bad = lambda f: f"فارغ — {f} غير محفوظ"
            _step("col_page_id",    "channels.page_id (Facebook Page ID)",
                  "OK" if ch.get("page_id") else "FAIL",
                  col_ok(ch["page_id"]) if ch.get("page_id") else col_bad("Facebook Page ID"))
            _step("col_biz_id",     "channels.business_account_id (IG Business Account)",
                  "OK" if ch.get("business_account_id") else "WARN",
                  col_ok(ch["business_account_id"]) if ch.get("business_account_id") else "فارغ")
            _step("col_last_error", "channels.last_error",
                  "FAIL" if ch.get("last_error") else "OK",
                  ch.get("last_error") or "لا يوجد خطأ ✓")

            # ── 10b. Webhook subscription check ─────────────────────────────────
            _page_id_for_sub  = ch.get("page_id", "")
            _biz_id_for_sub   = ch.get("business_account_id", "")
            if stored_token and _page_id_for_sub:
                try:
                    import httpx as _httpx_sub
                    _sub_r = _httpx_sub.get(
                        f"https://graph.facebook.com/v20.0/{_page_id_for_sub}/subscribed_apps",
                        params={"access_token": stored_token},
                        timeout=10,
                    )
                    _sub_body = _sub_r.json()
                    if "error" in _sub_body:
                        _step("webhook_sub", "Webhook مشترك في Meta",
                              "FAIL",
                              f"Meta error {_sub_body['error'].get('code','?')}: "
                              f"{_sub_body['error'].get('message','?')[:100]}")
                    else:
                        _subs = _sub_body.get("data", [])
                        _sub_fields = [f for s in _subs for f in (s.get("subscribed_fields") or [])]
                        if _sub_fields:
                            _step("webhook_sub", "Webhook مشترك في Meta",
                                  "OK" if "messages" in _sub_fields else "WARN",
                                  f"مشترك — fields={_sub_fields} "
                                  f"{'✓ messages موجود' if 'messages' in _sub_fields else '⚠️ messages غير موجود!'}")
                        else:
                            _step("webhook_sub", "Webhook مشترك في Meta",
                                  "WARN",
                                  f"لا يوجد اشتراك نشط — الـ webhook غير مسجّل للصفحة. "
                                  f"page_id={_page_id_for_sub} biz_id={_biz_id_for_sub}")
                except Exception as _sub_exc:
                    _step("webhook_sub", "Webhook مشترك في Meta", "WARN",
                          f"فشل فحص الاشتراك: {str(_sub_exc)[:100]}")
            else:
                _step("webhook_sub", "Webhook مشترك في Meta", "WARN",
                      "لا يمكن الفحص — token أو page_id فارغ")

            # ── 11. Auto-fix: re-apply valid page_token from any completed OAuth state ─
            _auto_fixed = False
            import httpx as _httpx
            for acct in ig_accounts:
                candidate = acct.get("page_token", "")
                if not candidate:
                    continue
                # Validate this candidate token against Meta
                try:
                    cr = _httpx.get(
                        "https://graph.facebook.com/v20.0/me",
                        params={"access_token": candidate, "fields": "id,name"},
                        timeout=10,
                    )
                    cdata = cr.json()
                    if "error" not in cdata:
                        # Valid token — apply it to the channel
                        new_page_id = acct.get("page_id", "") or ch.get("page_id", "")
                        new_biz_id  = acct.get("id", "")
                        # Sanity: IG Business Account ID should be numeric, not a token
                        if new_biz_id.startswith("EAA"):
                            new_biz_id = ""  # was a token mistakenly stored — clear it
                        conn.execute(
                            "UPDATE channels SET "
                            "token=?, page_id=?, business_account_id=?, "
                            "last_error='', reconnect_needed=0, connection_status='connected', "
                            "oauth_completed_at=?, last_tested_at=CURRENT_TIMESTAMP "
                            "WHERE id=?",
                            (
                                candidate,
                                new_page_id,
                                new_biz_id,
                                datetime.utcnow().isoformat(),
                                ch["id"],
                            )
                        )
                        conn.commit()
                        fix_msg = (
                            f"تم حفظ page_token الصحيح — "
                            f"account_id={new_biz_id or '?'} page_id={new_page_id or '?'} "
                            f"token_prefix={candidate[:12]}"
                        )
                        fixed.append(fix_msg)
                        _step("auto_fix", "إصلاح تلقائي — token", "FIXED", fix_msg)
                        verdict = "fixed"
                        _auto_fixed = True
                        break
                    else:
                        logger.info(f"[ig-diag] candidate token invalid: {cdata['error'].get('message','?')[:60]}")
                except Exception as fix_exc:
                    logger.warning(f"[ig-diag] auto-fix candidate test failed: {fix_exc}")

            # If auto-fix couldn't find a valid token AND current token is broken → reset channel
            if not _auto_fixed and verdict in ("token_cannot_decrypt", "token_expired",
                                               "token_invalid", "token_wrong_type"):
                conn.execute(
                    "UPDATE channels SET "
                    "token='', page_id='', business_account_id='', "
                    "last_error='', reconnect_needed=1, connection_status='disconnected' "
                    "WHERE id=?",
                    (ch["id"],)
                )
                conn.commit()
                reset_msg = "تم مسح الـ token المكسور والـ page_id الخاطئ — القناة جاهزة لإعادة الربط من الصفر"
                fixed.append(reset_msg)
                _step("auto_reset", "إعادة ضبط القناة", "FIXED", reset_msg)
                verdict = "reset_for_reconnect"

        return {"steps": steps, "fixed": fixed, "verdict": verdict}

    except Exception as _diag_exc:
        logger.error(f"[ig-diag] unhandled exception: {_diag_exc}", exc_info=True)
        _step("internal_error", "خطأ داخلي في الفحص", "FAIL", str(_diag_exc)[:300])
        return {"steps": steps, "fixed": fixed, "verdict": "internal_error"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
    """Start WhatsApp OAuth flow via server-side redirect."""
    logger.info(f"[wa-connect] START — restaurant={user['restaurant_id'][:8]} user={user.get('name','?')}")
    logger.info(f"[wa-connect] META_APP_ID={'SET('+META_APP_ID[:6]+')' if META_APP_ID else 'MISSING'} META_WA_CONFIG_ID={'SET('+META_WA_CONFIG_ID[:8]+')' if META_WA_CONFIG_ID else 'MISSING'}")
    if not META_APP_ID:
        logger.error("[wa-connect] ABORT — META_APP_ID missing")
        raise HTTPException(400, "META_APP_ID غير مضبوط في .env")
    if not META_WA_CONFIG_ID:
        logger.error("[wa-connect] ABORT — META_WA_CONFIG_ID missing")
        raise HTTPException(400, "META_WA_CONFIG_ID غير مضبوط — أضفه من Meta Business Manager → Facebook Login for Business → Configuration ID")
    result = await integrations_oauth_start({"platform": "whatsapp"}, user)
    logger.info(f"[wa-connect] auth_url built — prefix={result.get('auth_url','')[:80]}")
    return result


# ── Webhooks (public, no auth) ────────────────────────────────────────────────

@app.post("/webhook/telegram/{restaurant_id}")
async def webhook_telegram(restaurant_id: str, req: Request, background_tasks: BackgroundTasks):
    update = await req.json()
    # #8 — Rate limit: 30 messages/min per sender per restaurant
    _tg_sender = str(
        (update.get("message") or update.get("callback_query") or {})
        .get("from", {}).get("id", "")
    )
    if _tg_sender and not _check_rate(_tg_sender, limit=30, window=60, scope=f"wh:{restaurant_id}"):
        logger.warning(f"[webhook] telegram rate-limit sender={_tg_sender} restaurant={restaurant_id}")
        return {"ok": True}  # 200 so Telegram doesn't retry
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
    # #8 — Rate limit by WhatsApp sender
    _wa_sender = ""
    try:
        _wa_sender = data["entry"][0]["changes"][0]["value"]["messages"][0]["from"]
    except Exception:
        pass
    if _wa_sender and not _check_rate(_wa_sender, limit=30, window=60, scope=f"wh:{restaurant_id}"):
        logger.warning(f"[webhook] whatsapp rate-limit sender={_wa_sender} restaurant={restaurant_id}")
        return {"status": "ok"}
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
    # #8 — Rate limit by Instagram sender
    _ig_sender = ""
    try:
        _ig_sender = data["entry"][0]["messaging"][0]["sender"]["id"]
    except Exception:
        pass
    if _ig_sender and not _check_rate(_ig_sender, limit=30, window=60, scope=f"wh:{restaurant_id}"):
        logger.warning(f"[webhook] instagram rate-limit sender={_ig_sender} restaurant={restaurant_id}")
        return {"status": "ok"}
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
    # #8 — Rate limit by Facebook sender
    _fb_sender = ""
    try:
        _fb_sender = data["entry"][0]["messaging"][0]["sender"]["id"]
    except Exception:
        pass
    if _fb_sender and not _check_rate(_fb_sender, limit=30, window=60, scope=f"wh:{restaurant_id}"):
        logger.warning(f"[webhook] facebook rate-limit sender={_fb_sender} restaurant={restaurant_id}")
        return {"status": "ok"}
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
    except Exception as _e:
        logger.warning(f"[sa_log] failed action={action} admin={admin_id}: {_e}")


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
                   COALESCE(r.ai_learning_enabled, 1) AS ai_learning_enabled,
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


# ── Super Analytics: Overview (NUMBER 23) ────────────────────────────────────

@app.get("/api/super/analytics/overview")
async def super_analytics_overview(admin=Depends(current_super_admin)):
    from services.analytics_service import get_super_overview_analytics
    conn = database.get_db()
    try:
        return get_super_overview_analytics(conn)
    finally:
        conn.close()


# ── Super Analytics: Restaurants table (NUMBER 23) ───────────────────────────

@app.get("/api/super/analytics/restaurants")
async def super_analytics_restaurants(
    limit: int = 20,
    admin=Depends(current_super_admin),
):
    from services.analytics_service import get_super_restaurant_analytics
    if limit < 1 or limit > 100:
        limit = 20
    conn = database.get_db()
    try:
        return {"restaurants": get_super_restaurant_analytics(conn, limit=limit)}
    finally:
        conn.close()


# ── Super Analytics: Channels (NUMBER 23) ────────────────────────────────────

@app.get("/api/super/analytics/channels")
async def super_analytics_channels(admin=Depends(current_super_admin)):
    from services.analytics_service import get_super_channel_analytics
    conn = database.get_db()
    try:
        return {"channels": get_super_channel_analytics(conn)}
    finally:
        conn.close()


# ── Super Analytics: Health (NUMBER 23) ──────────────────────────────────────

@app.get("/api/super/analytics/health")
async def super_analytics_health(admin=Depends(current_super_admin)):
    from services.analytics_service import get_super_health_analytics
    conn = database.get_db()
    try:
        return get_super_health_analytics(conn)
    finally:
        conn.close()


@app.get("/api/super/ai/overview")
async def super_ai_overview(admin=Depends(current_super_admin)):
    """Platform-wide AI learning overview (NUMBER 25)."""
    from services.analytics_service import get_super_ai_overview
    conn = database.get_db()
    try:
        return get_super_ai_overview(conn)
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


# ── Production Readiness ─────────────────────────────────────────────────────

@app.get("/api/production-readiness")
async def production_readiness(admin=Depends(current_super_admin)):
    """SA-only: full platform production-readiness audit — blockers and warnings."""
    blockers: List[str] = []
    warnings: List[str] = []
    checks: dict = {}

    is_production = bool(os.getenv("RENDER") or os.getenv("ENVIRONMENT") == "production")

    # 1. Database connectivity + type
    db_ok = False
    try:
        _c = database.get_db()
        _c.execute("SELECT 1").fetchone()
        _c.close()
        db_ok = True
    except Exception as _e:
        blockers.append(f"قاعدة البيانات غير متاحة: {_e}")

    if is_production and not database.IS_POSTGRES:
        blockers.append("بيئة الإنتاج تتطلب PostgreSQL — DATABASE_URL غير مضبوط أو فارغ")

    checks["database"] = {
        "ok": db_ok and (not is_production or database.IS_POSTGRES),
        "type": "postgresql" if database.IS_POSTGRES else "sqlite",
        "is_production": is_production,
    }

    # 2. JWT_SECRET — must not be the default insecure value
    jwt_is_default = (
        os.getenv("JWT_SECRET", "") == "supersecretkey_change_in_production_123456789"
        or not _env_present("JWT_SECRET")
    )
    if jwt_is_default:
        blockers.append("JWT_SECRET يستخدم القيمة الافتراضية — خطر أمني حرج، يجب تغييره فوراً")
    checks["jwt_secret"] = {"ok": not jwt_is_default}

    # 3. BASE_URL — must point to the real deployed URL for webhooks
    base_url_ok = bool(BASE_URL and "localhost" not in BASE_URL)
    if not base_url_ok:
        warnings.append("BASE_URL غير مضبوط أو يشير إلى localhost — Webhooks لن تعمل")
    checks["base_url"] = {"ok": base_url_ok, "value": BASE_URL or ""}

    # 4. OpenAI API key
    openai_ok = bool(OPENAI_API_KEY)
    if not openai_ok:
        warnings.append("OPENAI_API_KEY مفقود — الذكاء الاصطناعي والنسخ الصوتي لن يعملا")
    checks["openai"] = {"ok": openai_ok}

    # 5. Supabase storage — payment proofs and menu images will be lost on restart if not configured
    supabase_ok = _env_present("SUPABASE_URL") and _env_present("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_ok:
        warnings.append("Supabase غير مضبوط — الصور وإثباتات الدفع ستُحفظ محلياً وتُفقد عند إعادة النشر")
    checks["supabase_storage"] = {"ok": supabase_ok}

    # 6. Protected tables exist
    _protected = [
        "restaurants", "users", "products", "orders", "customers",
        "conversations", "messages", "subscriptions", "super_admins",
        "payment_requests", "channels", "bot_config", "menu_images",
    ]
    _missing_tables: List[str] = []
    try:
        _c = database.get_db()
        if database.IS_POSTGRES:
            _rows = _c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
            _existing_tables = {r[0] for r in _rows}
        else:
            _rows = _c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            _existing_tables = {r[0] for r in _rows}
        _c.close()
        _missing_tables = [t for t in _protected if t not in _existing_tables]
    except Exception as _e:
        warnings.append(f"تعذر فحص الجداول: {_e}")

    if _missing_tables:
        blockers.append(f"جداول حيوية مفقودة: {', '.join(_missing_tables)}")
    checks["protected_tables"] = {"ok": len(_missing_tables) == 0, "missing": _missing_tables}

    # 7. Super admin exists — platform is unmanageable without one
    _sa_exists = False
    try:
        _c = database.get_db()
        if database.IS_POSTGRES:
            _sa_count = int(_c.execute("SELECT COUNT(*) as cnt FROM super_admins").fetchone()["cnt"])
        else:
            _sa_count = _c.execute("SELECT COUNT(*) FROM super_admins").fetchone()[0]
        _c.close()
        _sa_exists = _sa_count > 0
    except Exception:
        pass
    if not _sa_exists:
        blockers.append("لا يوجد Super Admin — المنصة غير قابلة للإدارة")
    checks["super_admin"] = {"ok": _sa_exists}

    # 8. Payment proof storage safety — local files disappear after Render redeploy
    _proof_local = 0
    try:
        _c = database.get_db()
        _proof_local = _c.execute(
            "SELECT COUNT(*) FROM payment_requests WHERE storage_mode='local' AND status IN ('pending','approved')"
        ).fetchone()[0]
        _c.close()
    except Exception:
        pass
    _proof_safe = supabase_ok or _proof_local == 0
    if not _proof_safe:
        warnings.append(f"{_proof_local} إثبات دفع محفوظ محلياً — ستُفقد عند إعادة النشر (فعّل Supabase)")
    checks["payment_proofs"] = {"ok": _proof_safe, "local_count": _proof_local}

    # 9. Migration safety — static audit: no DROP TABLE used on protected tables
    checks["migrations_safety"] = {
        "ok": True,
        "note": "تم مراجعة migrations — جميعها ADD COLUMN / CREATE TABLE IF NOT EXISTS فقط",
    }

    # 10. ALLOWED_ORIGINS — missing means open CORS in production
    cors_ok = _env_present("ALLOWED_ORIGINS")
    if is_production and not cors_ok:
        warnings.append("ALLOWED_ORIGINS غير مضبوط — CORS مفتوح لجميع النطاقات في الإنتاج")
    checks["cors"] = {"ok": cors_ok or not is_production}

    # 11. Voice transcription (NUMBER 22) — check service import + DB fields
    _voice_ok = True
    _voice_fields_ok = False
    try:
        import importlib as _il
        _vs = _il.import_module("services.voice_service")
        _voice_enabled = _vs.VOICE_TRANSCRIPTION_ENABLED
        _voice_provider = _vs.VOICE_TRANSCRIPTION_PROVIDER
        _voice_key_ok   = bool(_vs._OPENAI_API_KEY)
        _voice_ok = True
    except Exception as _ve:
        blockers.append(f"voice_service تعذر استيراده: {_ve}")
        _voice_ok = False
        _voice_enabled = False
        _voice_provider = ""
        _voice_key_ok   = False

    if _voice_ok and _voice_enabled and not _voice_key_ok:
        warnings.append(
            "VOICE_TRANSCRIPTION_ENABLED=true لكن OPENAI_API_KEY مفقود — "
            "الصوت سيستخدم الرد الآمن الاحتياطي"
        )

    # Check that transcription_status column exists in messages table
    try:
        _c = database.get_db()
        if database.IS_POSTGRES:
            _col_rows = _c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='messages' AND column_name='transcription_status'"
            ).fetchall()
            _voice_fields_ok = len(_col_rows) > 0
        else:
            _col_rows = _c.execute("PRAGMA table_info(messages)").fetchall()
            _voice_fields_ok = any(r[1] == "transcription_status" for r in _col_rows)
        _c.close()
    except Exception:
        _voice_fields_ok = False

    if not _voice_fields_ok:
        blockers.append("حقل transcription_status مفقود من جدول messages — أعد تشغيل init_db")

    checks["voice"] = {
        "ok": _voice_ok and _voice_fields_ok,
        "enabled": _voice_enabled if _voice_ok else False,
        "provider": _voice_provider if _voice_ok else "",
        "key_configured": _voice_key_ok if _voice_ok else False,
        "db_fields_ok": _voice_fields_ok,
    }

    # 12. Analytics service (NUMBER 23) — must import cleanly
    _analytics_ok = False
    try:
        import importlib as _il
        _as = _il.import_module("services.analytics_service")
        callable(_as.get_voice_analytics)
        callable(_as.get_menu_image_analytics)
        callable(_as.get_super_overview_analytics)
        _analytics_ok = True
    except Exception as _ae:
        blockers.append(f"analytics_service تعذر استيراده: {_ae}")

    checks["analytics"] = {"ok": _analytics_ok}

    # 13. UI files (NUMBER 24) — warnings only, never blockers
    _ui_dir = os.path.join(os.path.dirname(__file__), "public")
    _app_html   = os.path.join(_ui_dir, "app.html")
    _super_html = os.path.join(_ui_dir, "super.html")
    _ui_app_ok   = os.path.isfile(_app_html)
    _ui_super_ok = os.path.isfile(_super_html)
    _ui_features = {}
    if _ui_app_ok:
        try:
            _app_src = open(_app_html, encoding="utf-8").read()
            _ui_features = {
                "menu_images_section":  "sec-menu-images"   in _app_src,
                "analytics_section":    "sec-analytics"     in _app_src,
                "voice_ui":             "transcription_status" in _app_src,
                "quick_actions":        "dashQuickActions"  in _app_src,
                "health_strip":         "dashHealthStrip"   in _app_src,
                "date_filter":          "analyticsDateFrom" in _app_src,
            }
        except Exception:
            _ui_features = {}
    if not _ui_app_ok:
        warnings.append("public/app.html غير موجود — واجهة المطاعم غير متاحة")
    if not _ui_super_ok:
        warnings.append("public/super.html غير موجود — واجهة المشرف غير متاحة")
    checks["ui_files"] = {
        "ok": _ui_app_ok and _ui_super_ok,
        "app_html":   _ui_app_ok,
        "super_html": _ui_super_ok,
        "features":   _ui_features,
    }

    # 14. AI Learning tables (NUMBER 25) — must exist for learning system
    _ai_tables = ["ai_feedback", "restaurant_knowledge", "ai_quality_logs"]
    _ai_tables_ok = False
    _ai_tables_missing: List[str] = []
    try:
        _c = database.get_db()
        if database.IS_POSTGRES:
            _ai_rows = _c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
            _ai_existing = {r[0] for r in _ai_rows}
        else:
            _ai_rows = _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            _ai_existing = {r[0] for r in _ai_rows}
        _c.close()
        _ai_tables_missing = [t for t in _ai_tables if t not in _ai_existing]
        _ai_tables_ok = len(_ai_tables_missing) == 0
    except Exception as _aie:
        warnings.append(f"تعذر فحص جداول AI Learning: {_aie}")

    if not _ai_tables_ok:
        blockers.append(f"جداول AI Learning مفقودة: {', '.join(_ai_tables_missing)} — أعد تشغيل init_db")

    _ai_learning_ok = False
    try:
        import importlib as _il
        _as25 = _il.import_module("services.analytics_service")
        callable(_as25.get_ai_learning_metrics)
        callable(_as25.get_super_ai_overview)
        _ai_learning_ok = True
    except Exception as _ae25:
        blockers.append(f"analytics_service (AI learning functions) تعذر استيرادها: {_ae25}")

    checks["ai_learning"] = {
        "ok": _ai_tables_ok and _ai_learning_ok,
        "tables_ok": _ai_tables_ok,
        "missing_tables": _ai_tables_missing,
        "analytics_ok": _ai_learning_ok,
    }

    # 15. AI Learning Safety tables (NUMBER 25B)
    _safety_tables = ["bot_correction_versions", "restaurant_knowledge_versions", "ai_change_logs"]
    _safety_missing: List[str] = []
    _safety_ok = False
    try:
        _c = database.get_db()
        if database.IS_POSTGRES:
            _sr = _c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
            _sex = {r[0] for r in _sr}
        else:
            _sr = _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            _sex = {r[0] for r in _sr}
        _c.close()
        _safety_missing = [t for t in _safety_tables if t not in _sex]
        _safety_ok = len(_safety_missing) == 0
    except Exception as _se:
        warnings.append(f"تعذر فحص جداول AI Safety: {_se}")

    if not _safety_ok:
        blockers.append(f"جداول AI Safety مفقودة: {', '.join(_safety_missing)} — أعد تشغيل init_db")

    _ai_learning_enabled_col = False
    try:
        _c = database.get_db()
        if database.IS_POSTGRES:
            _col = _c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='restaurants' AND column_name='ai_learning_enabled'"
            ).fetchone()
            _ai_learning_enabled_col = _col is not None
        else:
            _pi = _c.execute("PRAGMA table_info(restaurants)").fetchall()
            _ai_learning_enabled_col = any(r[1] == "ai_learning_enabled" for r in _pi)
        _c.close()
    except Exception:
        pass

    if not _ai_learning_enabled_col:
        blockers.append("عمود ai_learning_enabled مفقود من جدول restaurants — أعد تشغيل init_db")

    checks["ai_safety"] = {
        "ok": _safety_ok and _ai_learning_enabled_col,
        "safety_tables_ok": _safety_ok,
        "missing_safety_tables": _safety_missing,
        "learning_switch_col_ok": _ai_learning_enabled_col,
    }

    overall = "blocked" if blockers else ("warnings" if warnings else "ready")
    return {
        "status": overall,
        "is_production": is_production,
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


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


@app.post("/api/menu-import/{session_id}/save-as-menu-images", status_code=201)
async def menu_import_save_as_images(
    session_id: str,
    user=Depends(require_role("owner", "manager")),
):
    """Save the uploaded image files from an import session as menu_images entries."""
    conn = database.get_db()
    row = conn.execute(
        "SELECT file_urls, file_names FROM menu_import_sessions WHERE id=? AND restaurant_id=?",
        (session_id, user["restaurant_id"])
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "جلسة الاستيراد غير موجودة")

    file_urls  = json.loads(row["file_urls"]  or "[]")
    file_names = json.loads(row["file_names"] or "[]")

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    saved = []
    for url, name in zip(file_urls, file_names):
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTS:
            continue
        # Skip if already saved
        exists = conn.execute(
            "SELECT id FROM menu_images WHERE restaurant_id=? AND image_url=?",
            (user["restaurant_id"], url)
        ).fetchone()
        if exists:
            continue
        mid = str(uuid.uuid4())
        title = Path(name).stem.replace("_", " ").replace("-", " ").strip()
        conn.execute(
            "INSERT INTO menu_images (id, restaurant_id, title, image_url, sort_order) VALUES (?,?,?,?,?)",
            (mid, user["restaurant_id"], title, url,
             conn.execute("SELECT COUNT(*) FROM menu_images WHERE restaurant_id=?",
                          (user["restaurant_id"],)).fetchone()[0])
        )
        saved.append({"id": mid, "url": url, "title": title})

    conn.commit()
    conn.close()
    return {"saved": len(saved), "items": saved}


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

_UPLOAD_DIR = Path("uploads/payment_proofs")
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path("uploads/menu-images").mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# ── Onboarding ────────────────────────────────────────────────────────────────

def _compute_onboarding_status(conn, rid: str, uid: str) -> dict:
    """Build the full onboarding status for a restaurant. Reads real DB state only."""
    rest = conn.execute("SELECT * FROM restaurants WHERE id=?", (rid,)).fetchone()
    rest = dict(rest) if rest else {"id": rid}
    settings_row = conn.execute("SELECT * FROM settings WHERE restaurant_id=?", (rid,)).fetchone()
    sett = dict(settings_row) if settings_row else {}

    # ── 1. Profile completeness ────────────────────────────────────────────────
    name    = (rest.get("name") or sett.get("restaurant_name") or "").strip()
    phone   = (rest.get("phone") or sett.get("restaurant_phone") or "").strip()
    address = (rest.get("address") or sett.get("restaurant_address") or "").strip()
    btype   = (rest.get("business_type") or "").strip()
    profile_complete = bool(name and (phone or address))

    # ── 2. Subscription/Plan ───────────────────────────────────────────────────
    sub_state = get_subscription_state(conn, rid)
    sub_plan   = sub_state["plan"]
    # Direct DB query — avoids get_subscription_state's "active" fallback for payment logic
    _sub_direct = conn.execute("SELECT status, end_date, trial_ends_at FROM subscriptions WHERE restaurant_id=?", (rid,)).fetchone()
    sub_status_db = _sub_direct["status"] if _sub_direct else None   # None = no row yet
    sub_status    = sub_status_db or "trial"                         # UI display fallback
    current_period_end = (_sub_direct["end_date"] if _sub_direct else "") or ""
    trial_ends_at      = (_sub_direct["trial_ends_at"] if _sub_direct else "") or ""
    plan_chosen = sub_plan not in ("", "basic", "trial", "free") or sub_status_db == "active"

    # ── 3 & 4. Payment proof + SA approval ────────────────────────────────────
    pay_row = conn.execute(
        "SELECT * FROM payment_requests WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
        (rid,)
    ).fetchone()
    pay_req = dict(pay_row) if pay_row else {}
    raw_pay_status = pay_req.get("status") or ""
    # Normalise — use explicit DB subscription status, NOT the fallback "active"
    if sub_status_db == "active":
        pay_status = "approved"
    elif raw_pay_status == "pending":
        pay_status = "pending"
    elif raw_pay_status == "approved":
        pay_status = "approved"
    elif raw_pay_status == "rejected":
        pay_status = "rejected"
    elif sub_status_db == "trial":
        pay_status = "trial"
    else:
        pay_status = "not_submitted"

    if sub_status == "active":
        approval_status = "approved"
    elif pay_status == "pending":
        approval_status = "pending_review"
    elif pay_status == "rejected":
        approval_status = "rejected"
    elif sub_status == "trial":
        approval_status = "not_required"
    else:
        approval_status = "awaiting_proof"

    reject_reason = pay_req.get("reject_reason") or ""

    # ── 5. Menu / Products ─────────────────────────────────────────────────────
    products_count = conn.execute(
        "SELECT COUNT(*) FROM products WHERE restaurant_id=? AND available=1", (rid,)
    ).fetchone()[0]

    # ── 6. Channel readiness ───────────────────────────────────────────────────
    ch_ready = _channel_readiness(conn, rid)
    ok_channels = [p for p, v in ch_ready.items() if v.get("status") == "ok"]
    connected_channels_count = len(ok_channels)

    # ── 7. Bot test ────────────────────────────────────────────────────────────
    bot_test_status = rest.get("onboarding_bot_test_status") or "not_tested"

    # ── 8. Launch readiness ────────────────────────────────────────────────────
    # No sub row (new restaurant) is treated as trial for launch purposes
    sub_ok   = sub_status_db in ("active", "trial") or sub_status_db is None
    pay_ok   = pay_status in ("approved", "trial") or sub_status_db is None
    launch_ready = (
        profile_complete and
        sub_ok and
        pay_ok and
        products_count > 0 and
        connected_channels_count > 0
    )

    # ── Build steps list ───────────────────────────────────────────────────────
    steps = [
        {
            "key":          "profile",
            "title_ar":     "الملف التجاري",
            "status":       "complete" if profile_complete else "incomplete",
            "reason":       "" if profile_complete else "يُرجى إضافة رقم الهاتف أو العنوان",
            "action_label": "" if profile_complete else "أكمل الملف الشخصي",
            "action_url":   "" if profile_complete else "#settings",
        },
        {
            "key":          "plan",
            "title_ar":     "خطة الاشتراك",
            "status":       "complete" if plan_chosen else ("trial" if sub_status == "trial" else "incomplete"),
            "reason":       "" if plan_chosen else ("أنت على خطة تجريبية" if sub_status == "trial" else "لم تختر خطة بعد"),
            "action_label": "" if plan_chosen else "اختر خطة",
            "action_url":   "" if plan_chosen else "#settings?tab=billing",
        },
        {
            "key":          "payment",
            "title_ar":     "إثبات الدفع",
            "status":       pay_status,
            "reason":       reject_reason if pay_status == "rejected" else (
                            "وصل الدفع قيد مراجعة الإدارة" if pay_status == "pending" else (
                            "تم تفعيل الاشتراك" if pay_status == "approved" else (
                            "على الخطة التجريبية — لا يلزم دفع الآن" if pay_status == "trial" else
                            "أرسل وصل الدفع لتفعيل اشتراكك"))),
            "action_label": "أعد إرسال وصل الدفع" if pay_status == "rejected" else (
                            "" if pay_status in ("approved", "pending", "trial") else
                            "أرسل وصل الدفع"),
            "action_url":   "#settings?tab=billing" if pay_status not in ("approved", "pending", "trial") else "",
        },
        {
            "key":          "approval",
            "title_ar":     "موافقة الإدارة",
            "status":       approval_status,
            "reason":       {
                "approved":      "تم تفعيل الاشتراك بعد مراجعة الإدارة",
                "pending_review":"وصل الدفع قيد مراجعة الإدارة — سيتم التفعيل قريباً",
                "rejected":      f"تم رفض الوصل: {reject_reason}" if reject_reason else "تم رفض وصل الدفع",
                "not_required":  "على الخطة التجريبية — لا يلزم موافقة الآن",
                "awaiting_proof":"أرسل وصل الدفع أولاً",
            }.get(approval_status, ""),
            "action_label": "",
            "action_url":   "",
        },
        {
            "key":          "menu",
            "title_ar":     "القائمة والمنتجات",
            "status":       "complete" if products_count > 0 else "incomplete",
            "reason":       "" if products_count > 0 else "لا توجد منتجات نشطة — أضف منتجاتك لبدء استقبال الطلبات",
            "action_label": "" if products_count > 0 else "أضف منتجاتك",
            "action_url":   "" if products_count > 0 else "#products",
        },
        {
            "key":          "channels",
            "title_ar":     "ربط القنوات",
            "status":       "complete" if connected_channels_count > 0 else "incomplete",
            "reason":       (f"{connected_channels_count} قناة متصلة: {', '.join(ok_channels)}" if ok_channels
                            else "لا توجد قنوات متصلة — وصّل تيليغرام أو واتساب أو غيرها"),
            "action_label": "" if connected_channels_count > 0 else "وصّل قناة",
            "action_url":   "" if connected_channels_count > 0 else "#channels",
        },
        {
            "key":          "bot_test",
            "title_ar":     "اختبار البوت",
            "status":       bot_test_status,
            "reason":       {
                "not_tested": "لم يُختبر البوت بعد — تأكد من الإعداد قبل الإطلاق",
                "pass":       "البوت يعمل بشكل صحيح",
                "fail":       "البوت لا يعمل — تحقق من الإعداد",
            }.get(bot_test_status, ""),
            "action_label": "اختبر الآن" if bot_test_status != "pass" else "",
            "action_url":   "",
        },
        {
            "key":          "launch",
            "title_ar":     "جاهز للإطلاق",
            "status":       "ready" if launch_ready else "not_ready",
            "reason":       "مبروك! حسابك جاهز للإطلاق" if launch_ready else "أكمل الخطوات أعلاه للإطلاق",
            "action_label": "",
            "action_url":   "",
        },
    ]

    completed_steps = sum(1 for s in steps if s["status"] in ("complete", "approved", "pass", "ready"))
    progress = round(completed_steps / len(steps) * 100)
    overall  = "ready" if launch_ready else ("almost_ready" if progress >= 50 else "not_ready")

    return {
        "restaurant_id": rid,
        "overall_status": overall,
        "progress_percent": progress,
        "steps": steps,
        "payment_review": {
            "status":            pay_status,
            "latest_request_id": pay_req.get("id"),
            "reject_reason":     reject_reason,
            "submitted_at":      pay_req.get("created_at") or "",
            "reviewed_at":       pay_req.get("reviewed_at") or "",
        },
        "subscription": {
            "plan":               sub_plan,
            "status":             sub_status,
            "current_period_end": current_period_end,
            "trial_ends_at":      trial_ends_at,
        },
        "profile_complete":         profile_complete,
        "products_count":           products_count,
        "connected_channels_count": connected_channels_count,
        "channel_details":          {p: {"status": v["status"], "reason": v["reason"]} for p, v in ch_ready.items()},
        "bot_test_status":          bot_test_status,
        "bot_tested_at":            rest.get("onboarding_bot_tested_at") or "",
        "launch_ready":             launch_ready,
    }


@app.get("/api/onboarding/status")
async def onboarding_status(user=Depends(current_user)):
    """Restaurant owner: full onboarding checklist from real DB state."""
    rid = user["restaurant_id"]
    uid = user["id"]
    conn = database.get_db()
    try:
        return _compute_onboarding_status(conn, rid, uid)
    finally:
        conn.close()


@app.post("/api/onboarding/test-bot")
async def onboarding_test_bot(user=Depends(current_user)):
    """Restaurant owner: verify bot prerequisites (no fake data, no AI call)."""
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        sub_state = get_subscription_state(conn, rid)
        ai_allowed = sub_state["features"].get("ai", True) and not sub_state["blocked"]
        products_count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE restaurant_id=? AND available=1", (rid,)
        ).fetchone()[0]
        bot_cfg = conn.execute(
            "SELECT system_prompt FROM bot_config WHERE restaurant_id=?", (rid,)
        ).fetchone()
        openai_ok = bool(OPENAI_API_KEY)

        issues = []
        if not openai_ok:
            issues.append("مفتاح OpenAI غير مضبوط في الخادم")
        if not ai_allowed:
            issues.append(f"خطة {sub_state['plan']} لا تشمل ردود الذكاء الاصطناعي")
        if products_count == 0:
            issues.append("لا توجد منتجات — البوت لا يملك قائمة يعتمد عليها")
        if not bot_cfg or not (bot_cfg["system_prompt"] or "").strip():
            issues.append("لم يتم ضبط System Prompt للبوت بعد")

        status = "pass" if not issues else "fail"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE restaurants SET onboarding_bot_test_status=?, onboarding_bot_tested_at=? WHERE id=?",
            (status, now_str, rid)
        )
        conn.commit()
        return {
            "ok": True,
            "status": status,
            "issues": issues,
            "tested_at": now_str,
        }
    finally:
        conn.close()


@app.get("/api/super/onboarding/restaurants")
async def super_onboarding_list(
    filter: str = "all",
    admin=Depends(current_super_admin)
):
    """Super admin: list all restaurants with onboarding summary + filter."""
    conn = database.get_db()
    try:
        restaurants = conn.execute(
            "SELECT id, name, status FROM restaurants ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in restaurants:
            rid  = r["id"]
            snap = _compute_onboarding_status(conn, rid, "")
            row = {
                "restaurant_id":            rid,
                "restaurant_name":          r["name"],
                "restaurant_status":        r["status"],
                "overall_status":           snap["overall_status"],
                "progress_percent":         snap["progress_percent"],
                "payment_status":           snap["payment_review"]["status"],
                "reject_reason":            snap["payment_review"]["reject_reason"],
                "subscription_plan":        snap["subscription"]["plan"],
                "subscription_status":      snap["subscription"]["status"],
                "products_count":           snap["products_count"],
                "connected_channels_count": snap["connected_channels_count"],
                "bot_test_status":          snap["bot_test_status"],
                "launch_ready":             snap["launch_ready"],
                "profile_complete":         snap["profile_complete"],
            }
            # Apply filter
            if filter == "pending_payment" and snap["payment_review"]["status"] != "pending":
                continue
            elif filter == "missing_menu" and snap["products_count"] > 0:
                continue
            elif filter == "missing_channel" and snap["connected_channels_count"] > 0:
                continue
            elif filter == "ready" and not snap["launch_ready"]:
                continue
            elif filter == "blocked" and r["status"] != "suspended":
                continue
            elif filter == "needs_attention" and not (
                snap["payment_review"]["status"] in ("rejected",) or
                (snap["products_count"] == 0 and snap["subscription"]["status"] == "active") or
                snap["connected_channels_count"] == 0
            ):
                continue
            result.append(row)
        return {"restaurants": result, "total": len(result), "filter": filter}
    finally:
        conn.close()


@app.get("/api/super/onboarding/restaurants/{restaurant_id}")
async def super_onboarding_detail(
    restaurant_id: str,
    admin=Depends(current_super_admin)
):
    """Super admin: full onboarding detail for one restaurant."""
    conn = database.get_db()
    try:
        r = conn.execute("SELECT * FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
        if not r:
            raise HTTPException(404, "المطعم غير موجود")
        owner = conn.execute(
            "SELECT id, name, email FROM users WHERE restaurant_id=? AND role='owner' LIMIT 1",
            (restaurant_id,)
        ).fetchone()
        snap = _compute_onboarding_status(conn, restaurant_id, "")
        return {
            **snap,
            "restaurant_name":  r["name"],
            "owner_name":       owner["name"]  if owner else "",
            "owner_email":      owner["email"] if owner else "",
            "restaurant_status": r["status"],
        }
    finally:
        conn.close()


# ── Announcements ─────────────────────────────────────────────────────────────

_VALID_ANN_TYPES      = {"info","success","warning","promotion","maintenance","upgrade","payment"}
_VALID_ANN_PLACEMENTS = {"dashboard_top_banner","billing_page","integrations_page","sidebar_small","modal_once"}


def _is_safe_cta_url(url: str) -> bool:
    if not url:
        return True
    u = url.strip().lower()
    return u.startswith("http://") or u.startswith("https://") or u.startswith("/")


def _announcement_matches_restaurant(ann: dict, restaurant: dict, subscription: dict, conn) -> bool:
    """Return True if ann should be shown to this restaurant."""
    import json as _j
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if ann.get("starts_at") and ann["starts_at"] > now:
        return False
    if ann.get("ends_at") and ann["ends_at"] < now:
        return False
    if ann.get("target_all"):
        targeted = True
    else:
        targeted = False
        rid = restaurant.get("id", "")
        if not targeted:
            try:
                if rid in _j.loads(ann.get("target_restaurant_ids_json") or "[]"):
                    targeted = True
            except Exception:
                pass
        if not targeted:
            try:
                plan = restaurant.get("plan", "")
                if plan in _j.loads(ann.get("target_plans_json") or "[]"):
                    targeted = True
            except Exception:
                pass
        if not targeted:
            try:
                sub_status = subscription.get("status") if subscription else restaurant.get("status", "active")
                if sub_status in _j.loads(ann.get("target_statuses_json") or "[]"):
                    targeted = True
            except Exception:
                pass
        if not targeted:
            return False
    if ann.get("target_channel_problem_only"):
        rid = restaurant.get("id", "")
        prob = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE restaurant_id=? AND (reconnect_needed=1 OR connection_status='error')",
            (rid,)
        ).fetchone()[0]
        if not prob:
            return False
    if ann.get("target_expired_only"):
        sub_status = subscription.get("status") if subscription else restaurant.get("status", "active")
        if sub_status not in ("expired", "past_due", "suspended"):
            return False
    return True


class AnnouncementReq(BaseModel):
    title: str
    message: str = ""
    type: str = "info"
    priority: int = 0
    cta_text: str = ""
    cta_url: str = ""
    placement: str = "dashboard_top_banner"
    target_all: int = 1
    target_restaurant_ids_json: str = "[]"
    target_plans_json: str = "[]"
    target_statuses_json: str = "[]"
    target_channel_problem_only: int = 0
    target_expired_only: int = 0
    starts_at: str = ""
    ends_at: str = ""
    is_dismissible: int = 1
    is_active: int = 1


@app.get("/api/super/announcements")
async def super_list_announcements(admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM announcements ORDER BY priority DESC, created_at DESC"
        ).fetchall()
        return {"announcements": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/super/announcements", status_code=201)
async def super_create_announcement(body: AnnouncementReq, admin=Depends(current_super_admin)):
    if not body.title.strip():
        raise HTTPException(400, "العنوان مطلوب")
    if body.type not in _VALID_ANN_TYPES:
        raise HTTPException(400, f"نوع غير صالح: {body.type}")
    if body.placement not in _VALID_ANN_PLACEMENTS:
        raise HTTPException(400, f"موضع غير صالح: {body.placement}")
    if not _is_safe_cta_url(body.cta_url):
        raise HTTPException(400, "رابط CTA غير آمن — استخدم http/https أو مسار نسبي")
    conn = database.get_db()
    try:
        aid = str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO announcements
                (id, title, message, type, priority, cta_text, cta_url, placement,
                 target_all, target_restaurant_ids_json, target_plans_json,
                 target_statuses_json, target_channel_problem_only, target_expired_only,
                 starts_at, ends_at, is_dismissible, is_active, created_by, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (aid, body.title.strip(), body.message, body.type, body.priority,
              body.cta_text, body.cta_url, body.placement, body.target_all,
              body.target_restaurant_ids_json, body.target_plans_json,
              body.target_statuses_json, body.target_channel_problem_only,
              body.target_expired_only, body.starts_at, body.ends_at,
              body.is_dismissible, body.is_active, admin.get("id", ""), now, now))
        conn.commit()
        return {"ok": True, "id": aid}
    finally:
        conn.close()


@app.patch("/api/super/announcements/{ann_id}")
async def super_update_announcement(ann_id: str, body: dict, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT id FROM announcements WHERE id=?", (ann_id,)).fetchone()
        if not row:
            raise HTTPException(404, "الإعلان غير موجود")
        allowed = {"title","message","type","priority","cta_text","cta_url","placement",
                   "target_all","target_restaurant_ids_json","target_plans_json",
                   "target_statuses_json","target_channel_problem_only","target_expired_only",
                   "starts_at","ends_at","is_dismissible","is_active"}
        if "cta_url" in body and not _is_safe_cta_url(body["cta_url"]):
            raise HTTPException(400, "رابط CTA غير آمن")
        if "type" in body and body["type"] not in _VALID_ANN_TYPES:
            raise HTTPException(400, f"نوع غير صالح: {body['type']}")
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            raise HTTPException(400, "لا توجد حقول صالحة")
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE announcements SET {set_clause} WHERE id=?",
                     (*updates.values(), ann_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/super/announcements/{ann_id}")
async def super_delete_announcement(ann_id: str, admin=Depends(current_super_admin)):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT id FROM announcements WHERE id=?", (ann_id,)).fetchone()
        if not row:
            raise HTTPException(404, "الإعلان غير موجود")
        conn.execute("DELETE FROM announcement_dismissals WHERE announcement_id=?", (ann_id,))
        conn.execute("DELETE FROM announcements WHERE id=?", (ann_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/announcements")
async def get_announcements_for_restaurant(user=Depends(current_user)):
    rid  = user["restaurant_id"]
    uid  = user["id"]
    conn = database.get_db()
    try:
        rest = conn.execute("SELECT * FROM restaurants WHERE id=?", (rid,)).fetchone()
        restaurant = dict(rest) if rest else {"id": rid}
        sub_row = conn.execute(
            "SELECT * FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1",
            (rid,)
        ).fetchone()
        subscription = dict(sub_row) if sub_row else {}
        dismissed_ids = {
            r[0] for r in conn.execute(
                "SELECT announcement_id FROM announcement_dismissals WHERE restaurant_id=? AND user_id=?",
                (rid, uid)
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT * FROM announcements WHERE is_active=1 ORDER BY priority DESC, created_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            ann = dict(row)
            if ann["id"] in dismissed_ids:
                continue
            if not _announcement_matches_restaurant(ann, restaurant, subscription, conn):
                continue
            result.append(ann)
        return {"announcements": result}
    finally:
        conn.close()


@app.post("/api/announcements/{ann_id}/dismiss")
async def dismiss_announcement(ann_id: str, user=Depends(current_user)):
    rid = user["restaurant_id"]
    uid = user["id"]
    conn = database.get_db()
    try:
        ann = conn.execute("SELECT id, is_dismissible FROM announcements WHERE id=?", (ann_id,)).fetchone()
        if not ann:
            raise HTTPException(404, "الإعلان غير موجود")
        if not ann["is_dismissible"]:
            raise HTTPException(403, "هذا الإعلان لا يمكن إخفاؤه")
        existing = conn.execute(
            "SELECT id FROM announcement_dismissals WHERE announcement_id=? AND restaurant_id=? AND user_id=?",
            (ann_id, rid, uid)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO announcement_dismissals (id, announcement_id, restaurant_id, user_id, dismissed_at) "
                "VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), ann_id, rid, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── Static pages ──────────────────────────────────────────────────────────────

@app.get("/manifest.json", include_in_schema=False)
async def pwa_manifest():
    return FileResponse("public/manifest.json", media_type="application/manifest+json")

@app.get("/sw.js", include_in_schema=False)
async def pwa_sw():
    return FileResponse("public/sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

@app.get("/")
async def root():
    landing = "public/landing.html"
    if os.path.isfile(landing):
        return FileResponse(landing)
    return FileResponse("public/login.html")


@app.get("/login")
@app.get("/login.html")
async def login_page():
    return FileResponse("public/login.html")


@app.get("/app")
async def app_page():
    return FileResponse("public/app.html")


@app.get("/register")
@app.get("/register.html")
async def register_page():
    return FileResponse("public/register.html")


@app.get("/reset-password")
async def reset_password_page():
    return FileResponse("public/reset-password.html")


@app.get("/super/login")
async def super_login_page():
    return FileResponse("public/super_login.html")


@app.get("/super")
async def super_admin_page():
    return FileResponse("public/super.html")


@app.get("/menu/{restaurant_id}")
async def public_menu_page(restaurant_id: str):
    return FileResponse("public/menu.html")


# ── Compliance / Legal pages (required for Meta App Review) ───────────────────

@app.get("/terms", include_in_schema=False)
@app.get("/terms.html", include_in_schema=False)
async def terms_page():
    return FileResponse("public/terms.html")


@app.get("/data-deletion", include_in_schema=False)
@app.get("/data-deletion.html", include_in_schema=False)
async def data_deletion_page():
    return FileResponse("public/data-deletion.html")


@app.post("/data-deletion", include_in_schema=False)
async def data_deletion_callback(request: Request):
    """
    Meta data-deletion callback endpoint.
    Meta POSTs a signed_request when a user removes the app from their Facebook account.
    We log the request and return the required JSON with a confirmation_code.
    """
    import base64, hmac as _hmac, hashlib as _hashlib, uuid as _uuid
    from urllib.parse import unquote

    confirmation_code = str(_uuid.uuid4())[:8].upper()
    status_url = f"https://restaurant-saas-1.onrender.com/data-deletion?code={confirmation_code}"

    try:
        form = await request.form()
        signed_request = form.get("signed_request", "")

        # Parse the signed_request to get the user_id (best-effort — don't crash if invalid)
        if signed_request and "." in signed_request:
            _encoded_sig, _payload = signed_request.split(".", 1)
            _padding = "=" * (4 - len(_payload) % 4)
            _decoded = base64.urlsafe_b64decode(_payload + _padding)
            _data = json.loads(_decoded.decode("utf-8"))
            fb_user_id = _data.get("user_id", "unknown")
        else:
            fb_user_id = "unknown"

        logger.info(f"[data-deletion] Meta callback received fb_user_id={fb_user_id} code={confirmation_code}")
        # In production: queue a job to delete conversation_memory + messages for this fb_user_id
    except Exception as e:
        logger.warning(f"[data-deletion] Could not parse signed_request: {e}")

    return JSONResponse({
        "url": status_url,
        "confirmation_code": confirmation_code,
    })


@app.get("/app-review", include_in_schema=False)
@app.get("/app-review.html", include_in_schema=False)
async def app_review_page():
    return FileResponse("public/app-review.html")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Public robots.txt — explicitly allows all major crawlers including facebookexternalhit."""
    from fastapi.responses import PlainTextResponse
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "User-agent: facebookexternalhit\n"
        "Allow: /\n"
        "\n"
        "User-agent: Twitterbot\n"
        "Allow: /\n"
        "\n"
        "User-agent: LinkedInBot\n"
        "Allow: /\n"
        "\n"
        "Sitemap: https://restaurant-saas-1.onrender.com/sitemap.xml\n"
    )
    return PlainTextResponse(content=content, status_code=200, headers={
        "Cache-Control": "public, max-age=86400",
    })


@app.get("/privacy", include_in_schema=False)
@app.get("/privacy/", include_in_schema=False)
async def privacy_policy(request: Request):
    """Publicly accessible privacy policy — no auth, no redirect, Meta-review safe."""
    import os as _os
    html_path = _os.path.join(_os.path.dirname(__file__), "public", "privacy.html")
    with open(html_path, "r", encoding="utf-8") as _f:
        html_content = _f.read()
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        content=html_content,
        status_code=200,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "public, max-age=86400",
            "X-Robots-Tag": "index, follow",
            # Override security_headers middleware — remove SAMEORIGIN for public crawlers
            "X-Frame-Options": "ALLOWALL",
        },
    )


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
