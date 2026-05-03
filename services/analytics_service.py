"""
services/analytics_service.py
NUMBER 23 — Advanced Analytics

Query functions for voice, menu-image, and super-admin analytics.
All SQL uses ? placeholders (SQLite/PostgreSQL compatible via _PgConnection adapter).
"""
import logging

logger = logging.getLogger("restaurant-saas")


# ── Restaurant-level analytics ─────────────────────────────────────────────────

def get_voice_analytics(conn, restaurant_id: str, date_from: str = None, date_to: str = None) -> dict:
    """Voice transcription analytics for a single restaurant."""
    date_filter = ""
    params: list = [restaurant_id]
    if date_from:
        date_filter += " AND DATE(m.created_at) >= ?"
        params.append(date_from)
    if date_to:
        date_filter += " AND DATE(m.created_at) <= ?"
        params.append(date_to)

    try:
        total_voice = conn.execute(
            "SELECT COUNT(*) FROM messages m "
            "JOIN conversations cv ON m.conversation_id = cv.id "
            f"WHERE cv.restaurant_id = ? AND m.media_type = 'voice'{date_filter}",
            params,
        ).fetchone()[0]

        status_rows = conn.execute(
            "SELECT COALESCE(m.transcription_status,'not_required') AS ts, COUNT(*) AS cnt "
            "FROM messages m JOIN conversations cv ON m.conversation_id = cv.id "
            f"WHERE cv.restaurant_id = ? AND m.media_type = 'voice'{date_filter} "
            "GROUP BY ts",
            params,
        ).fetchall()
        by_status = {r["ts"]: r["cnt"] for r in status_rows}

        success  = by_status.get("success", 0)
        failed   = by_status.get("failed", 0)
        skipped  = by_status.get("skipped", 0)
        success_rate = round(success / total_voice * 100, 1) if total_voice > 0 else 0.0

        channel_rows = conn.execute(
            "SELECT cv.channel, COUNT(*) AS cnt "
            "FROM messages m JOIN conversations cv ON m.conversation_id = cv.id "
            f"WHERE cv.restaurant_id = ? AND m.media_type = 'voice'{date_filter} "
            "GROUP BY cv.channel",
            params,
        ).fetchall()
        by_channel = [{"channel": r["channel"], "count": r["cnt"]} for r in channel_rows]

        return {
            "total_voice_messages": total_voice,
            "success":  success,
            "failed":   failed,
            "skipped":  skipped,
            "success_rate": success_rate,
            "by_channel": by_channel,
            "by_status":  dict(by_status),
        }
    except Exception as e:
        logger.error(f"[analytics] get_voice_analytics: {e}")
        return {
            "total_voice_messages": 0, "success": 0, "failed": 0,
            "skipped": 0, "success_rate": 0.0, "by_channel": [], "by_status": {},
            "error": str(e)[:200],
        }


def get_menu_image_analytics(conn, restaurant_id: str) -> dict:
    """Menu image catalog stats for a single restaurant."""
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM menu_images WHERE restaurant_id = ?",
            (restaurant_id,),
        ).fetchone()[0]

        active = conn.execute(
            "SELECT COUNT(*) FROM menu_images WHERE restaurant_id = ? AND is_active = 1",
            (restaurant_id,),
        ).fetchone()[0]

        cat_rows = conn.execute(
            "SELECT COALESCE(NULLIF(category,''), 'غير محدد') AS cat, COUNT(*) AS cnt "
            "FROM menu_images WHERE restaurant_id = ? AND is_active = 1 "
            "GROUP BY cat ORDER BY cnt DESC",
            (restaurant_id,),
        ).fetchall()
        by_category = [{"category": r["cat"], "count": r["cnt"]} for r in cat_rows]

        return {
            "total_images":    total,
            "active_images":   active,
            "inactive_images": total - active,
            "by_category":     by_category,
        }
    except Exception as e:
        logger.error(f"[analytics] get_menu_image_analytics: {e}")
        return {
            "total_images": 0, "active_images": 0,
            "inactive_images": 0, "by_category": [],
            "error": str(e)[:200],
        }


# ── Super-admin platform analytics ────────────────────────────────────────────

def get_super_overview_analytics(conn) -> dict:
    """Platform-wide KPI snapshot for super admin."""
    def q(sql):
        return conn.execute(sql).fetchone()[0]

    try:
        return {
            "total_restaurants":  q("SELECT COUNT(*) FROM restaurants"),
            "active_restaurants": q("SELECT COUNT(*) FROM restaurants WHERE status='active'"),
            "total_orders":       q("SELECT COUNT(*) FROM orders WHERE status != 'cancelled'"),
            "platform_revenue":   round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE status != 'cancelled'"), 2),
            "total_customers":    q("SELECT COUNT(*) FROM customers"),
            "total_conversations": q("SELECT COUNT(*) FROM conversations"),
            "total_voice_messages": q("SELECT COUNT(*) FROM messages WHERE media_type = 'voice'"),
            "total_menu_images":  q("SELECT COUNT(*) FROM menu_images WHERE is_active = 1"),
            "active_channels":    q("SELECT COUNT(*) FROM channels WHERE enabled = 1"),
            "current_mrr":        round(q("SELECT COALESCE(SUM(price),0) FROM subscriptions WHERE status='active'"), 2),
            "today_orders":       q("SELECT COUNT(*) FROM orders WHERE DATE(created_at)=DATE('now') AND status!='cancelled'"),
            "today_revenue":      round(q("SELECT COALESCE(SUM(total),0) FROM orders WHERE DATE(created_at)=DATE('now') AND status!='cancelled'"), 2),
        }
    except Exception as e:
        logger.error(f"[analytics] get_super_overview_analytics: {e}")
        return {"error": str(e)[:200]}


def get_super_restaurant_analytics(conn, limit: int = 20) -> list:
    """Per-restaurant breakdown table for super admin."""
    try:
        rows = conn.execute("""
            SELECT
                r.id,
                r.name,
                r.plan,
                r.status,
                COALESCE(s.end_date, '')            AS sub_end_date,
                COALESCE(ord_s.total_orders,  0)    AS total_orders,
                COALESCE(ord_s.total_revenue, 0.0)  AS total_revenue,
                COALESCE(ch_s.channel_count,  0)    AS channel_count,
                COALESCE(vc_s.voice_count,    0)    AS voice_messages,
                COALESCE(img_s.image_count,   0)    AS menu_images,
                COALESCE(cu_s.customer_count, 0)    AS total_customers,
                r.created_at
            FROM restaurants r
            LEFT JOIN subscriptions s
                ON s.restaurant_id = r.id
            LEFT JOIN (
                SELECT restaurant_id,
                       COUNT(*) AS total_orders,
                       COALESCE(SUM(total), 0) AS total_revenue
                FROM orders WHERE status != 'cancelled'
                GROUP BY restaurant_id
            ) ord_s ON ord_s.restaurant_id = r.id
            LEFT JOIN (
                SELECT restaurant_id, COUNT(*) AS channel_count
                FROM channels WHERE enabled = 1
                GROUP BY restaurant_id
            ) ch_s ON ch_s.restaurant_id = r.id
            LEFT JOIN (
                SELECT cv.restaurant_id, COUNT(*) AS voice_count
                FROM messages m
                JOIN conversations cv ON m.conversation_id = cv.id
                WHERE m.media_type = 'voice'
                GROUP BY cv.restaurant_id
            ) vc_s ON vc_s.restaurant_id = r.id
            LEFT JOIN (
                SELECT restaurant_id, COUNT(*) AS image_count
                FROM menu_images WHERE is_active = 1
                GROUP BY restaurant_id
            ) img_s ON img_s.restaurant_id = r.id
            LEFT JOIN (
                SELECT restaurant_id, COUNT(*) AS customer_count
                FROM customers GROUP BY restaurant_id
            ) cu_s ON cu_s.restaurant_id = r.id
            ORDER BY total_orders DESC, r.name
            LIMIT ?
        """, (limit,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["total_revenue"] = round(float(d["total_revenue"]), 2)
            result.append(d)
        return result
    except Exception as e:
        logger.error(f"[analytics] get_super_restaurant_analytics: {e}")
        return []


def get_super_channel_analytics(conn) -> list:
    """Platform-wide channel type breakdown for super admin."""
    try:
        rows = conn.execute("""
            SELECT
                c.type AS channel,
                COUNT(*) AS total_configured,
                COALESCE(SUM(c.enabled), 0) AS active,
                COUNT(DISTINCT c.restaurant_id) AS restaurants_using,
                COALESCE(o_s.order_count, 0) AS total_orders,
                COALESCE(o_s.revenue, 0.0) AS total_revenue
            FROM channels c
            LEFT JOIN (
                SELECT channel,
                       COUNT(*) AS order_count,
                       COALESCE(SUM(total), 0) AS revenue
                FROM orders WHERE status != 'cancelled'
                GROUP BY channel
            ) o_s ON o_s.channel = c.type
            GROUP BY c.type
            ORDER BY total_orders DESC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["total_revenue"] = round(float(d["total_revenue"]), 2)
            result.append(d)
        return result
    except Exception as e:
        logger.error(f"[analytics] get_super_channel_analytics: {e}")
        return []


def get_super_health_analytics(conn) -> dict:
    """Platform health counts for super admin dashboard."""
    def q(sql):
        return conn.execute(sql).fetchone()[0]

    try:
        return {
            "suspended_restaurants":   q("SELECT COUNT(*) FROM restaurants WHERE status='suspended'"),
            "expired_subscriptions":   q("SELECT COUNT(*) FROM subscriptions WHERE status='active' AND end_date != '' AND end_date < date('now')"),
            "expiring_soon":           q("SELECT COUNT(*) FROM subscriptions WHERE status='active' AND end_date != '' AND end_date BETWEEN date('now') AND date('now', '+7 days')"),
            "channel_errors":          q("SELECT COUNT(*) FROM channels WHERE connection_status='error'"),
            "failed_outbound_24h":     q("SELECT COUNT(*) FROM outbound_messages WHERE status='failed' AND created_at >= datetime('now', '-24 hours')"),
            "open_conversations":      q("SELECT COUNT(*) FROM conversations WHERE status='open'"),
            "pending_payments":        q("SELECT COUNT(*) FROM payment_requests WHERE status='pending'"),
            "voice_failed_24h":        q("SELECT COUNT(*) FROM messages WHERE media_type='voice' AND transcription_status='failed' AND created_at >= datetime('now', '-24 hours')"),
            "pending_ai_feedback":     q("SELECT COUNT(*) FROM ai_feedback WHERE status='pending'"),
        }
    except Exception as e:
        logger.error(f"[analytics] get_super_health_analytics: {e}")
        return {"error": str(e)[:200]}


# ── AI Learning analytics (NUMBER 25) ─────────────────────────────────────────

def get_ai_learning_metrics(conn, restaurant_id: str) -> dict:
    """AI training / learning metrics for a single restaurant."""
    def q(sql, *params):
        try:
            return conn.execute(sql, params).fetchone()[0] or 0
        except Exception:
            return 0

    try:
        active_corrections = q(
            "SELECT COUNT(*) FROM bot_corrections WHERE restaurant_id=? AND is_active=1", restaurant_id
        )
        active_knowledge = q(
            "SELECT COUNT(*) FROM restaurant_knowledge WHERE restaurant_id=? AND is_active=1", restaurant_id
        )
        total_feedback = q(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=?", restaurant_id
        )
        pending_feedback = q(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=? AND status='pending'", restaurant_id
        )
        good_feedback = q(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=? AND rating='good'", restaurant_id
        )
        bad_feedback = q(
            "SELECT COUNT(*) FROM ai_feedback WHERE restaurant_id=? AND rating='bad'", restaurant_id
        )
        quality_logs_7d = q(
            "SELECT COUNT(*) FROM ai_quality_logs WHERE restaurant_id=? AND created_at >= datetime('now', '-7 days')", restaurant_id
        )
        return {
            "active_corrections": active_corrections,
            "active_knowledge": active_knowledge,
            "total_feedback": total_feedback,
            "pending_feedback": pending_feedback,
            "good_feedback": good_feedback,
            "bad_feedback": bad_feedback,
            "satisfaction_rate": round(good_feedback / total_feedback * 100, 1) if total_feedback > 0 else None,
            "quality_logs_7d": quality_logs_7d,
        }
    except Exception as e:
        logger.error(f"[analytics] get_ai_learning_metrics: {e}")
        return {"error": str(e)[:200]}


def get_super_ai_overview(conn) -> dict:
    """Platform-wide AI learning overview for super admin."""
    def q(sql):
        try:
            return conn.execute(sql).fetchone()[0] or 0
        except Exception:
            return 0

    try:
        return {
            "total_active_corrections":  q("SELECT COUNT(*) FROM bot_corrections WHERE is_active=1"),
            "total_active_knowledge":    q("SELECT COUNT(*) FROM restaurant_knowledge WHERE is_active=1"),
            "total_feedback":            q("SELECT COUNT(*) FROM ai_feedback"),
            "pending_feedback":          q("SELECT COUNT(*) FROM ai_feedback WHERE status='pending'"),
            "good_feedback":             q("SELECT COUNT(*) FROM ai_feedback WHERE rating='good'"),
            "quality_logs_7d":           q("SELECT COUNT(*) FROM ai_quality_logs WHERE created_at >= datetime('now', '-7 days')"),
        }
    except Exception as e:
        logger.error(f"[analytics] get_super_ai_overview: {e}")
        return {"error": str(e)[:200]}
