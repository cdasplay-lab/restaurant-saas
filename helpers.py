"""
helpers.py — NUMBER 43: Shared business-logic helpers.

Extracted from main.py to allow routers to import without circular deps.
Contents: plan limits/features, subscription state, log_activity,
          create_notification, _check_plan_limit, password hashing.
"""
from __future__ import annotations
import uuid
import logging
from typing import Optional
from fastapi import HTTPException
import bcrypt as _bcrypt
import database

logger = logging.getLogger("restaurant-saas")

# ── Plan constants ────────────────────────────────────────────────────────────

PLAN_LIMITS: dict = {
    "free":         {"products": 5,    "staff": 1,   "channels": 0},
    "trial":        {"products": 10,   "staff": 2,   "channels": 1},
    "starter":      {"products": 50,   "staff": 5,   "channels": 2},
    "professional": {"products": 200,  "staff": 15,  "channels": 4},
    "enterprise":   {"products": 9999, "staff": 9999, "channels": 10},
}

PLAN_FEATURES: dict = {
    "free":         {"ai": False, "analytics": False, "media": False, "handoff": False, "max_conversations": 0},
    "trial":        {"ai": True,  "analytics": True,  "media": False, "handoff": True,  "max_conversations": 200},
    "starter":      {"ai": True,  "analytics": False, "media": False, "handoff": True,  "max_conversations": 500},
    "professional": {"ai": True,  "analytics": True,  "media": True,  "handoff": True,  "max_conversations": 5000},
    "enterprise":   {"ai": True,  "analytics": True,  "media": True,  "handoff": True,  "max_conversations": 999999},
}

BLOCKED_STATUSES = {"expired", "suspended", "cancelled"}


# ── Plan DB helpers ───────────────────────────────────────────────────────────

def _get_plan_record(conn, plan_id: str = "", plan_code: str = "") -> Optional[dict]:
    """Load a subscription_plans row by id or code. Returns dict or None."""
    row = None
    if plan_id:
        row = conn.execute("SELECT * FROM subscription_plans WHERE id=?", (plan_id,)).fetchone()
    if not row and plan_code:
        row = conn.execute("SELECT * FROM subscription_plans WHERE code=?", (plan_code,)).fetchone()
    return dict(row) if row else None


def _plan_features_from_db(conn, plan_code: str) -> dict:
    """Return features dict for plan_code, reading DB first, falling back to PLAN_FEATURES."""
    row = _get_plan_record(conn, plan_code=plan_code)
    if row:
        return {
            "ai":                           bool(row.get("ai_enabled", 1)),
            "analytics":                    bool(row.get("analytics_enabled", 0)),
            "advanced_analytics":           bool(row.get("advanced_analytics_enabled", 0)),
            "media":                        bool(row.get("media_enabled", 0)),
            "voice":                        bool(row.get("voice_enabled", 0)),
            "image":                        bool(row.get("image_enabled", 0)),
            "video":                        bool(row.get("video_enabled", 0)),
            "story_reply":                  bool(row.get("story_reply_enabled", 0)),
            "handoff":                      bool(row.get("human_handoff_enabled", 1)),
            "multi_channel":                bool(row.get("multi_channel_enabled", 0)),
            "memory":                       bool(row.get("memory_enabled", 0)),
            "upsell":                       bool(row.get("upsell_enabled", 0)),
            "smart_recommendations":        bool(row.get("smart_recommendations_enabled", 0)),
            "menu_image_understanding":     bool(row.get("menu_image_understanding_enabled", 0)),
            "live_readiness":               bool(row.get("live_readiness_status_enabled", 0)),
            "priority_support":             bool(row.get("priority_support_enabled", 0)),
            "setup_assistance":             bool(row.get("setup_assistance_enabled", 0)),
            "telegram":                     bool(row.get("telegram_enabled", 1)),
            "whatsapp":                     bool(row.get("whatsapp_enabled", 1)),
            "instagram":                    bool(row.get("instagram_enabled", 1)),
            "facebook":                     bool(row.get("facebook_enabled", 1)),
            "max_conversations":            int(row.get("max_conversations_per_month", 200)),
        }
    return PLAN_FEATURES.get(plan_code, PLAN_FEATURES["trial"])


def _plan_limits_from_db(conn, plan_code: str) -> dict:
    """Return limits dict for plan_code, reading DB first, falling back to PLAN_LIMITS."""
    row = _get_plan_record(conn, plan_code=plan_code)
    if row:
        return {
            "products":       int(row.get("max_products", 10)),
            "staff":          int(row.get("max_staff", 2)),
            "channels":       int(row.get("max_channels", 1)),
            "team_members":   int(row.get("max_team_members", 2)),
            "branches":       int(row.get("max_branches", 1)),
            "customers":      int(row.get("max_customers", 0)),
            "ai_replies":     int(row.get("max_ai_replies_per_month", 0)),
        }
    return PLAN_LIMITS.get(plan_code, PLAN_LIMITS["trial"])


def get_subscription_state(conn, restaurant_id: str) -> dict:
    """Return the effective subscription state for a restaurant."""
    _sub  = conn.execute("SELECT * FROM subscriptions WHERE restaurant_id=?", (restaurant_id,)).fetchone()
    sub   = dict(_sub) if _sub else None
    rest  = conn.execute("SELECT plan, status FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    plan       = (sub  and sub["plan"])   or (rest and rest["plan"])   or "trial"
    sub_status = (sub  and sub["status"]) or "active"
    rest_status= (rest and rest["status"]) or "active"
    if rest_status == "suspended" or sub_status == "suspended":
        effective = "suspended"
    elif rest_status in ("expired", "cancelled") or sub_status in ("expired", "cancelled"):
        effective = sub_status if sub_status in ("expired", "cancelled") else rest_status
    else:
        effective = sub_status
    features   = _plan_features_from_db(conn, plan)
    blocked    = effective in BLOCKED_STATUSES or (not features.get("ai", True) and plan == "free")
    reason     = ""
    if effective == "suspended":
        reason = (sub and sub.get("suspended_reason")) or "الحساب موقوف — تواصل مع الدعم"
    elif effective == "expired":
        reason = "الاشتراك منتهي — جدد اشتراكك للاستمرار"
    elif effective == "cancelled":
        reason = "الاشتراك ملغى — تواصل مع الدعم لإعادة التفعيل"
    return {
        "plan":       plan,
        "status":     effective,
        "features":   features,
        "blocked":    blocked,
        "reason":     reason,
        "trial_ends_at":       sub["trial_ends_at"]       if sub else "",
        "current_period_end":  sub["end_date"]            if sub else "",
        "suspended_reason":    sub["suspended_reason"]    if sub else "",
        "cancelled_at":        sub["cancelled_at"]        if sub else "",
        "billing_email":       sub["billing_email"]       if sub else "",
        "payment_provider":    sub["payment_provider"]    if sub else "",
    }


def can_use_feature(conn, restaurant_id: str, feature: str):
    """Return (allowed: bool, reason: str). feature = 'ai'|'analytics'|'media'|'handoff'."""
    state = get_subscription_state(conn, restaurant_id)
    if state["blocked"] and feature != "billing":
        return False, state["reason"]
    if not state["features"].get(feature, True):
        return False, f"هذه الميزة غير متاحة في خطة {state['plan']} — رقّ خطتك"
    return True, ""


def _plan_limit(plan: str, resource: str, conn=None) -> int:
    if conn is not None:
        return _plan_limits_from_db(conn, plan).get(resource, 0)
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["trial"]).get(resource, 0)


def _check_plan_limit(conn, restaurant_id: str, plan: str, resource: str, table: str) -> None:
    """Raise 402 if the restaurant has reached its plan limit for the resource."""
    limit = _plan_limit(plan, resource, conn)
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE restaurant_id=?", (restaurant_id,)
    ).fetchone()[0]
    if count >= limit:
        raise HTTPException(
            402,
            f"وصلت إلى الحد الأقصى للخطة ({limit} {resource}). رقّ خطتك للمزيد."
        )


# ── Activity & Notification helpers ──────────────────────────────────────────

def log_activity(conn, restaurant_id, action, entity_type="", entity_id="", description="",
                 user_id=None, user_name="System"):
    try:
        conn.execute(
            "INSERT INTO activity_log (id, restaurant_id, user_id, user_name, action, entity_type, entity_id, description) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), restaurant_id, user_id, user_name, action, entity_type, entity_id, description)
        )
    except Exception as _e:
        logger.warning(f"[log_activity] failed action={action} restaurant={restaurant_id}: {_e}")


def create_notification(conn, restaurant_id, ntype, title, message, entity_type="", entity_id=""):
    try:
        conn.execute(
            "INSERT INTO notifications (id, restaurant_id, type, title, message, entity_type, entity_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), restaurant_id, ntype, title, message, entity_type, entity_id)
        )
    except Exception as _e:
        logger.warning(f"[create_notification] failed type={ntype} restaurant={restaurant_id}: {_e}")


# ── Password helpers ──────────────────────────────────────────────────────────

def _hash_password(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()


def _verify_password(pw: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False
