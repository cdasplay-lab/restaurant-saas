"""
Database layer — supports both SQLite (default) and PostgreSQL (via DATABASE_URL).

SQLite:  used by default for local dev and single-server deploys.
         WAL mode enabled for better concurrency.
         Foreign keys enforced.

PostgreSQL: set DATABASE_URL=postgresql://user:pass@host:5432/dbname
            Uses psycopg2; rows are wrapped to behave like sqlite3.Row.
"""
import sqlite3
import uuid
import json
import os
import re
import threading
from datetime import datetime, timedelta

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", "restaurant.db")

# ── DATABASE_URL normalization (Render / Heroku compatibility) ────────────────
# Must run before IS_POSTGRES is set and before any pool is created.
def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    # postgres:// → postgresql:// (psycopg2 requires the longer scheme)
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # sslmode=no-verify is not a valid psycopg2 value — replace with require
    url = url.replace("sslmode=no-verify", "sslmode=require")
    # External host with no sslmode: append sslmode=require
    # Internal Render hosts have no dots (e.g. dpg-xxx-a); external hosts do
    _hm = re.search(r'@([^/:@]+)', url)
    if _hm:
        _host = _hm.group(1)
        _is_external = '.' in _host
        if _is_external and 'sslmode=' not in url:
            url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url

DATABASE_URL = _normalize_db_url(DATABASE_URL)
IS_POSTGRES = bool(DATABASE_URL)

# Connection pool (PostgreSQL only)
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                import psycopg2.pool
                _pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _pg_pool


# ── PostgreSQL compatibility layer ────────────────────────────────────────────

class _PgRow:
    """Wraps a psycopg2 RealDictRow so it behaves like sqlite3.Row.
    Supports both row["name"] and row[0] (numeric) access, dict(), .keys(), etc.
    """
    __slots__ = ("_d", "_keys")

    def __init__(self, data: dict):
        self._d = dict(data)
        self._keys = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._d[self._keys[key]]
        return self._d[key]

    def __contains__(self, key):
        return key in self._d

    def __iter__(self):
        return iter(self._d.values())

    def keys(self):
        return self._d.keys()

    def get(self, key, default=None):
        return self._d.get(key, default)

    def items(self):
        return self._d.items()

    def values(self):
        return self._d.values()

    # Allow dict(row)
    def __len__(self):
        return len(self._d)


class _PgCursor:
    """Wraps psycopg2 cursor, returning _PgRow objects."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return _PgRow(row) if row is not None else None

    def fetchall(self):
        return [_PgRow(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PgConnection:
    """Wraps a psycopg2 connection to provide a sqlite3-compatible interface."""

    def __init__(self, conn, pool=None):
        self._conn = conn
        self._pool = pool

    def execute(self, sql: str, params=None):
        import psycopg2.extras
        # INSERT OR IGNORE INTO t → INSERT INTO t ... ON CONFLICT DO NOTHING
        if "INSERT OR IGNORE INTO" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        # INSERT OR REPLACE INTO t → INSERT INTO t ... ON CONFLICT DO NOTHING (safe approximation)
        if "INSERT OR REPLACE INTO" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        # Replace ? with %s for psycopg2
        sql = sql.replace("?", "%s")
        # Store timestamps as text in the same format as SQLite
        sql = sql.replace(
            "CURRENT_TIMESTAMP",
            "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')"
        )
        # DATE(col) → (col)::date  (SQLite-only function)
        sql = re.sub(r'\bDATE\(([^)]+)\)', r'(\1)::date', sql)
        # strftime('%Y-%m', col) → to_char((col)::timestamp, 'YYYY-MM')
        sql = re.sub(r"strftime\('%Y-%m',\s*([^)]+)\)", r"to_char((\1)::timestamp, 'YYYY-MM')", sql)
        # strftime('%H', col) → to_char((col)::timestamp, 'HH24')
        sql = re.sub(r"strftime\('%H',\s*([^)]+)\)", r"to_char((\1)::timestamp, 'HH24')", sql)
        # datetime('now', %s || ' days') → (NOW() + (%s || ' days')::interval)  [parameterized]
        sql = re.sub(r"datetime\('now',\s*(%s)\s*\|\|\s*'([^']+)'\)", r"(NOW() + (\1 || '\2')::interval)", sql, flags=re.IGNORECASE)
        # datetime('now', '-N unit') → (NOW() + INTERVAL '-N unit')
        sql = re.sub(r"datetime\('now',\s*'([^']+)'\)", r"(NOW() + INTERVAL '\1')", sql, flags=re.IGNORECASE)
        # datetime('now') → text-comparable UTC string (matches SQLite TEXT storage format)
        # Must stay as TEXT so comparisons against TEXT columns (expires_at, etc.) work without cast.
        sql = re.sub(
            r"datetime\('now'\)",
            "to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')",
            sql, flags=re.IGNORECASE
        )
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or [])
        return _PgCursor(cur)

    def executescript(self, script: str):
        """Execute a multi-statement SQL script (DDL) with per-statement autocommit."""
        script = script.replace(
            "TEXT DEFAULT CURRENT_TIMESTAMP",
            "TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')"
        )
        # Use autocommit so each DDL statement is independent — one failure doesn't rollback others
        self._conn.autocommit = True
        cur = self._conn.cursor()
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        ok = 0
        for stmt in stmts:
            try:
                cur.execute(stmt)
                ok += 1
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"[DB] DDL error: {e!r}")
        cur.close()
        self._conn.autocommit = False
        print(f"[DB] executescript: {ok}/{len(stmts)} statements OK")

    def commit(self):
        if not self._conn.autocommit:
            self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        if self._pool is not None:
            # Always rollback before returning to pool — a failed query leaves the
            # connection in ABORTED state; the next thread would get InFailedSqlTransaction.
            try:
                self._conn.rollback()
            except Exception:
                pass
            self._pool.putconn(self._conn)
        else:
            self._conn.close()


def get_db():
    """Return a database connection. Uses PostgreSQL if DATABASE_URL is set, else SQLite."""
    if IS_POSTGRES:
        pool = _get_pg_pool()
        conn = pool.getconn()
        conn.autocommit = False
        return _PgConnection(conn, pool)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # better write concurrency
    conn.execute("PRAGMA synchronous = NORMAL")  # safe + faster than FULL
    conn.execute("PRAGMA busy_timeout = 5000")   # 5s wait on lock
    return conn


# ── Schema creation ───────────────────────────────────────────────────────────

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    address TEXT DEFAULT '',
    plan TEXT DEFAULT 'basic',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT DEFAULT 'staff',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    category TEXT DEFAULT 'Main',
    description TEXT DEFAULT '',
    icon TEXT DEFAULT '🍽️',
    variants TEXT DEFAULT '[]',
    available INTEGER DEFAULT 1,
    order_count INTEGER DEFAULT 0,
    image TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT DEFAULT '',
    platform TEXT DEFAULT 'telegram',
    vip INTEGER DEFAULT 0,
    preferences TEXT DEFAULT '',
    favorite_item TEXT DEFAULT '',
    total_orders INTEGER DEFAULT 0,
    total_spent REAL DEFAULT 0,
    last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    channel TEXT DEFAULT 'telegram',
    type TEXT DEFAULT 'delivery',
    total REAL NOT NULL DEFAULT 0,
    address TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    product_id TEXT,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER DEFAULT 1,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    mode TEXT DEFAULT 'bot',
    status TEXT DEFAULT 'open',
    urgent INTEGER DEFAULT 0,
    unread_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS settings (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT UNIQUE NOT NULL,
    restaurant_name TEXT DEFAULT '',
    restaurant_description TEXT DEFAULT '',
    restaurant_phone TEXT DEFAULT '',
    restaurant_address TEXT DEFAULT '',
    bot_name TEXT DEFAULT 'AI Assistant',
    bot_personality TEXT DEFAULT 'friendly',
    bot_language TEXT DEFAULT 'ar',
    bot_welcome TEXT DEFAULT 'مرحباً! كيف يمكنني مساعدتك؟',
    bot_enabled INTEGER DEFAULT 1,
    security_2fa INTEGER DEFAULT 0,
    security_session_timeout INTEGER DEFAULT 24,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT DEFAULT '',
    token TEXT DEFAULT '',
    webhook_url TEXT DEFAULT '',
    username TEXT DEFAULT '',
    enabled INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(restaurant_id, type),
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS activity_log (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    user_id TEXT DEFAULT NULL,
    user_name TEXT DEFAULT 'System',
    action TEXT NOT NULL,
    entity_type TEXT DEFAULT '',
    entity_id TEXT DEFAULT '',
    description TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    entity_type TEXT DEFAULT '',
    entity_id TEXT DEFAULT '',
    is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_config (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT UNIQUE NOT NULL,
    system_prompt TEXT DEFAULT '',
    sales_prompt TEXT DEFAULT '',
    escalation_keywords TEXT DEFAULT '[]',
    escalation_threshold INTEGER DEFAULT 3,
    order_extraction_enabled INTEGER DEFAULT 1,
    fallback_message TEXT DEFAULT 'سأحيلك لأحد موظفينا الآن، انتظر قليلاً. 🙏',
    memory_enabled INTEGER DEFAULT 1,
    max_bot_turns INTEGER DEFAULT 15,
    auto_handoff_enabled INTEGER DEFAULT 1,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS conversation_memory (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(restaurant_id, customer_id, memory_key)
);

CREATE TABLE IF NOT EXISTS super_admins (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS platform_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL UNIQUE,
    plan TEXT DEFAULT 'trial',
    status TEXT DEFAULT 'trial',
    price REAL DEFAULT 0,
    start_date TEXT DEFAULT CURRENT_TIMESTAMP,
    end_date TEXT DEFAULT '',
    trial_ends_at TEXT DEFAULT '',
    last_payment_date TEXT DEFAULT '',
    next_payment_date TEXT DEFAULT '',
    payment_method TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS super_admin_log (
    id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL,
    admin_name TEXT DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    description TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS menu_import_sessions (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    file_names TEXT DEFAULT '[]',
    file_count INTEGER DEFAULT 0,
    total_extracted INTEGER DEFAULT 0,
    approved_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    raw_items TEXT DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT DEFAULT '',
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS processed_events (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(restaurant_id, provider, event_id)
);

CREATE TABLE IF NOT EXISTS outbound_messages (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    conversation_id TEXT DEFAULT '',
    platform TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    error TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
"""


def _create_indexes(conn):
    """Create performance indexes (safe — IF NOT EXISTS)."""
    _pg = IS_POSTGRES and hasattr(conn, '_conn')
    if _pg:
        conn._conn.autocommit = True
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_orders_restaurant ON orders(restaurant_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_conversations_restaurant ON conversations(restaurant_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversations_customer ON conversations(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status)",
        "CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_customers_restaurant ON customers(restaurant_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_restaurant ON activity_log(restaurant_id)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_restaurant ON notifications(restaurant_id, is_read)",
        "CREATE INDEX IF NOT EXISTS idx_processed_events ON processed_events(restaurant_id, provider, event_id)",
        # Partial unique index: one order per conversation (WHERE guards against empty conv_id)
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_conv_dedup ON orders(conversation_id, restaurant_id) WHERE conversation_id != ''",
        "CREATE INDEX IF NOT EXISTS idx_outbound_messages_restaurant ON outbound_messages(restaurant_id, created_at)",
    ]
    for idx in indexes:
        try:
            conn.execute(idx)
        except Exception:
            pass
    if _pg:
        conn._conn.autocommit = False


def _migrate_db(conn):
    """Add new columns to existing tables. Safe — ignores if column already exists."""
    # Run all DDL in autocommit mode on PostgreSQL so one failure doesn't abort the transaction
    _pg = IS_POSTGRES and hasattr(conn, '_conn')
    if _pg:
        conn._conn.autocommit = True

    migrations = [
        # channels new columns
        ("channels", "webhook_secret",       "TEXT DEFAULT ''"),
        ("channels", "admin_chat_id",         "TEXT DEFAULT ''"),
        ("channels", "phone_number_id",       "TEXT DEFAULT ''"),
        ("channels", "business_account_id",   "TEXT DEFAULT ''"),
        ("channels", "verify_token",          "TEXT DEFAULT ''"),
        ("channels", "app_id",                "TEXT DEFAULT ''"),
        ("channels", "app_secret",            "TEXT DEFAULT ''"),
        ("channels", "page_id",               "TEXT DEFAULT ''"),
        ("channels", "page_name",             "TEXT DEFAULT ''"),
        ("channels", "bot_username",          "TEXT DEFAULT ''"),
        ("channels", "connection_status",     "TEXT DEFAULT 'unknown'"),
        ("channels", "last_error",            "TEXT DEFAULT ''"),
        ("channels", "last_tested_at",        "TEXT"),
        # conversations new columns
        ("conversations", "bot_turn_count",   "INTEGER DEFAULT 0"),
        ("conversations", "handoff_reason",   "TEXT DEFAULT ''"),
        ("conversations", "escalated_at",     "TEXT"),
        ("conversations", "order_brain_state","TEXT DEFAULT NULL"),
        # users new columns
        ("users", "last_login",               "TEXT"),
        # messages new columns (media + voice + story reply)
        ("messages", "media_type",            "TEXT DEFAULT ''"),
        ("messages", "media_url",             "TEXT DEFAULT ''"),
        ("messages", "voice_transcript",      "TEXT DEFAULT ''"),
        ("messages", "replied_story_id",      "TEXT DEFAULT ''"),
        ("messages", "replied_story_text",    "TEXT DEFAULT ''"),
        ("messages", "replied_story_media_url", "TEXT DEFAULT ''"),
        # messages — voice transcription tracking (NUMBER 22)
        ("messages", "media_mime_type",          "TEXT DEFAULT ''"),
        ("messages", "media_size",               "INTEGER DEFAULT 0"),
        ("messages", "transcription_status",     "TEXT DEFAULT 'not_required'"),
        ("messages", "transcription_error",      "TEXT DEFAULT ''"),
        ("messages", "transcription_provider",   "TEXT DEFAULT ''"),
        ("messages", "transcribed_at",           "TEXT DEFAULT ''"),
        # products new columns
        ("products", "image_url",             "TEXT DEFAULT ''"),
        ("products", "gallery_images",        "TEXT DEFAULT '[]'"),
        ("products", "import_batch_id",       "TEXT DEFAULT ''"),
        ("products", "confidence",            "REAL DEFAULT 1.0"),
        # restaurants new columns (super admin)
        ("restaurants", "status",             "TEXT DEFAULT 'active'"),
        ("restaurants", "internal_notes",     "TEXT DEFAULT ''"),
        ("restaurants", "last_activity_at",   "TEXT DEFAULT ''"),
        # menu_import_sessions — Supabase Storage URLs for uploaded files
        ("menu_import_sessions", "file_urls", "TEXT DEFAULT '[]'"),
        # restaurants — working hours JSON
        ("restaurants", "working_hours", "TEXT DEFAULT '{}'"),
        # settings — working hours JSON
        ("settings", "working_hours", "TEXT DEFAULT '{}'"),
        # settings — payment methods (free text, shown to bot)
        ("settings", "payment_methods", "TEXT DEFAULT 'كاش'"),
        # super_admins — support PIN for restaurant owner recovery
        ("super_admins", "support_pin", "TEXT DEFAULT ''"),
        # business type: restaurant or cafe
        ("restaurants", "business_type", "TEXT DEFAULT 'restaurant'"),
        ("settings", "business_type", "TEXT DEFAULT 'restaurant'"),
        # products — sold out today flag
        ("products", "sold_out_date", "TEXT DEFAULT ''"),
        # settings — delivery time estimate shown to bot
        ("settings", "delivery_time", "TEXT DEFAULT ''"),
        # settings — owner Telegram chat ID for order notifications
        ("settings", "notify_chat_id", "TEXT DEFAULT ''"),
        # settings — delivery fee (Iraqi Dinar)
        ("settings", "delivery_fee", "INTEGER DEFAULT 0"),
        # settings — delivery zones JSON (zone-based delivery fees per area)
        ("settings", "delivery_zones", "TEXT DEFAULT ''"),
        # settings — minimum order amount (Iraqi Dinar)
        ("settings", "min_order", "INTEGER DEFAULT 0"),
        # settings — automated report: none / daily / weekly
        ("settings", "report_frequency", "TEXT DEFAULT 'none'"),
        # settings — timestamp of last sent report (ISO string)
        ("settings", "report_last_sent", "TEXT DEFAULT ''"),
        # orders — conversation that produced this order (for dedup)
        ("orders", "conversation_id", "TEXT DEFAULT ''"),
        # settings — external menu URL (bot shares when customer asks for menu)
        ("settings", "menu_url", "TEXT DEFAULT ''"),
        # products + customers — track last modification time
        ("products",   "updated_at", "TEXT DEFAULT ''"),
        ("customers",  "updated_at", "TEXT DEFAULT ''"),
        # channels — OAuth & connection lifecycle columns
        ("channels", "token_expires_at",       "TEXT DEFAULT ''"),
        ("channels", "refresh_token",          "TEXT DEFAULT ''"),
        ("channels", "reconnect_needed",       "INTEGER DEFAULT 0"),
        ("channels", "account_picture_url",    "TEXT DEFAULT ''"),
        ("channels", "account_display_name",   "TEXT DEFAULT ''"),
        ("channels", "scopes_granted",         "TEXT DEFAULT ''"),
        ("channels", "oauth_completed_at",     "TEXT DEFAULT ''"),
        ("channels", "waba_id",                "TEXT DEFAULT ''"),
        ("channels", "connected_by_user_id",   "TEXT DEFAULT ''"),
        ("channels", "phone_number_display",   "TEXT DEFAULT ''"),
        # oauth_states — pages_json stores Meta page list during OAuth handshake
        ("oauth_states", "pages_json",         "TEXT DEFAULT '[]'"),
        # conversations — which platform channel this belongs to
        ("conversations", "channel",           "TEXT DEFAULT ''"),
        # conversations — 1 if this is the customer's first-ever message (request/cold-start)
        ("conversations", "first_contact",     "INTEGER DEFAULT 0"),
        # payment_requests — extended fields for Supabase storage + plan reference
        ("payment_requests", "proof_url",    "TEXT DEFAULT ''"),
        ("payment_requests", "storage_mode", "TEXT DEFAULT 'local'"),
        ("payment_requests", "plan_id",      "TEXT DEFAULT ''"),
        # subscription_plans — badge + excluded features for UI plan cards
        ("subscription_plans", "badge",                  "TEXT DEFAULT ''"),
        ("subscription_plans", "excluded_features_json", "TEXT DEFAULT '[]'"),
        # subscription_plans — Arabic display fields
        ("subscription_plans", "name_ar",                          "TEXT DEFAULT ''"),
        ("subscription_plans", "description_ar",                   "TEXT DEFAULT ''"),
        ("subscription_plans", "billing_period_ar",                "TEXT DEFAULT ''"),
        ("subscription_plans", "badge_text_ar",                    "TEXT DEFAULT ''"),
        ("subscription_plans", "is_recommended",                   "INTEGER DEFAULT 0"),
        # subscription_plans — extended limits
        ("subscription_plans", "max_customers",                    "INTEGER DEFAULT 0"),
        ("subscription_plans", "max_ai_replies_per_month",         "INTEGER DEFAULT 0"),
        ("subscription_plans", "max_team_members",                 "INTEGER DEFAULT 2"),
        ("subscription_plans", "max_branches",                     "INTEGER DEFAULT 1"),
        # subscription_plans — per-platform & feature flags
        ("subscription_plans", "telegram_enabled",                 "INTEGER DEFAULT 1"),
        ("subscription_plans", "whatsapp_enabled",                 "INTEGER DEFAULT 1"),
        ("subscription_plans", "instagram_enabled",                "INTEGER DEFAULT 1"),
        ("subscription_plans", "facebook_enabled",                 "INTEGER DEFAULT 1"),
        ("subscription_plans", "multi_channel_enabled",            "INTEGER DEFAULT 0"),
        ("subscription_plans", "memory_enabled",                   "INTEGER DEFAULT 0"),
        ("subscription_plans", "upsell_enabled",                   "INTEGER DEFAULT 0"),
        ("subscription_plans", "smart_recommendations_enabled",    "INTEGER DEFAULT 0"),
        ("subscription_plans", "advanced_analytics_enabled",       "INTEGER DEFAULT 0"),
        ("subscription_plans", "image_enabled",                    "INTEGER DEFAULT 0"),
        ("subscription_plans", "video_enabled",                    "INTEGER DEFAULT 0"),
        ("subscription_plans", "story_reply_enabled",              "INTEGER DEFAULT 0"),
        ("subscription_plans", "menu_image_understanding_enabled", "INTEGER DEFAULT 0"),
        ("subscription_plans", "live_readiness_status_enabled",    "INTEGER DEFAULT 0"),
        ("subscription_plans", "priority_support_enabled",         "INTEGER DEFAULT 0"),
        ("subscription_plans", "setup_assistance_enabled",         "INTEGER DEFAULT 0"),
        ("subscription_plans", "limits_json",                      "TEXT DEFAULT '{}'"),
        # subscriptions — billing & payment state fields
        ("subscriptions", "cancelled_at",           "TEXT DEFAULT ''"),
        ("subscriptions", "suspended_reason",       "TEXT DEFAULT ''"),
        ("subscriptions", "billing_email",          "TEXT DEFAULT ''"),
        ("subscriptions", "payment_provider",       "TEXT DEFAULT ''"),
        ("subscriptions", "payment_customer_id",    "TEXT DEFAULT ''"),
        ("subscriptions", "payment_subscription_id","TEXT DEFAULT ''"),
        # restaurants — onboarding profile fields
        ("restaurants", "delivery_area",            "TEXT DEFAULT ''"),
        ("restaurants", "payment_methods_info",     "TEXT DEFAULT ''"),
        # restaurants — onboarding bot test state
        ("restaurants", "onboarding_bot_test_status",  "TEXT DEFAULT 'not_tested'"),
        ("restaurants", "onboarding_bot_tested_at",    "TEXT DEFAULT ''"),
        # bot_corrections — extended fields for NUMBER 25 AI Training
        ("bot_corrections", "trigger_text",    "TEXT DEFAULT ''"),
        ("bot_corrections", "correction_text", "TEXT DEFAULT ''"),
        ("bot_corrections", "category",        "TEXT DEFAULT ''"),
        ("bot_corrections", "priority",        "INTEGER DEFAULT 0"),
        ("bot_corrections", "usage_count",     "INTEGER DEFAULT 0"),
        ("bot_corrections", "created_by",      "TEXT DEFAULT ''"),
        ("bot_corrections", "updated_at",      "TEXT DEFAULT CURRENT_TIMESTAMP"),
        # NUMBER 25B — soft-delete + learning switch
        ("bot_corrections",        "deleted_at",        "TEXT DEFAULT ''"),
        ("restaurant_knowledge",   "deleted_at",        "TEXT DEFAULT ''"),
        ("restaurants",            "ai_learning_enabled", "INTEGER DEFAULT 1"),
        # NUMBER 37 — item special instructions / notes
        ("order_items", "notes", "TEXT DEFAULT ''"),
        # #2 — Stripe payment gateway
        ("orders", "payment_status",     "TEXT DEFAULT 'unpaid'"),
        ("orders", "stripe_session_id",  "TEXT DEFAULT ''"),
        ("orders", "stripe_payment_url", "TEXT DEFAULT ''"),
        # settings — Stripe keys per restaurant (optional, overrides global)
        ("settings", "stripe_secret_key",      "TEXT DEFAULT ''"),
        ("settings", "stripe_publishable_key", "TEXT DEFAULT ''"),
        ("settings", "stripe_webhook_secret",  "TEXT DEFAULT ''"),
        ("settings", "stripe_enabled",         "INTEGER DEFAULT 0"),
        # multi-branch — orders and users carry the branch they belong to
        ("orders", "branch_id",  "TEXT DEFAULT ''"),
        ("users",  "branch_id",  "TEXT DEFAULT ''"),
        # menu_images — send tracking
        ("menu_images", "send_count",   "INTEGER DEFAULT 0"),
        ("menu_images", "last_sent_at", "TEXT DEFAULT ''"),
        # conversations — silence follow-up guard (1 = already sent follow-up)
        ("conversations", "followup_sent", "INTEGER DEFAULT 0"),
        # conversations — quality tracking
        ("conversations", "had_order",        "INTEGER DEFAULT 0"),
        ("conversations", "resolution_type",  "TEXT DEFAULT ''"),
        # bot_config — brand voice
        ("bot_config", "voice_tone",       "TEXT DEFAULT 'friendly'"),
        ("bot_config", "dialect_override", "TEXT DEFAULT 'auto'"),
        ("bot_config", "custom_greeting",  "TEXT DEFAULT ''"),
        ("bot_config", "custom_farewell",  "TEXT DEFAULT ''"),
        ("bot_config", "brand_keywords",   "TEXT DEFAULT ''"),
    ]

    # ── billing_audit_logs ───────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_audit_logs (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            actor_id TEXT DEFAULT '',
            actor_role TEXT DEFAULT '',
            restaurant_id TEXT DEFAULT '',
            payment_request_id TEXT DEFAULT '',
            payment_method_id TEXT DEFAULT '',
            old_status TEXT DEFAULT '',
            new_status TEXT DEFAULT '',
            amount REAL DEFAULT 0,
            currency TEXT DEFAULT '',
            plan TEXT DEFAULT '',
            note TEXT DEFAULT '',
            storage_mode TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_audit_rid ON billing_audit_logs(restaurant_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_audit_req ON billing_audit_logs(payment_request_id)")
    except Exception:
        pass

    # ── payment_methods ───────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_methods (
            id TEXT PRIMARY KEY,
            method_name TEXT NOT NULL,
            account_holder_name TEXT DEFAULT '',
            bank_name TEXT DEFAULT '',
            account_number TEXT DEFAULT '',
            iban TEXT DEFAULT '',
            phone_number TEXT DEFAULT '',
            currency TEXT DEFAULT 'IQD',
            payment_instructions TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            display_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── payment_requests ─────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_requests (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            plan TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'IQD',
            payment_method_id TEXT DEFAULT '',
            payer_name TEXT DEFAULT '',
            reference_number TEXT DEFAULT '',
            proof_path TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            reject_reason TEXT DEFAULT '',
            internal_note TEXT DEFAULT '',
            reviewed_by TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT DEFAULT '',
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_req_restaurant ON payment_requests(restaurant_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_req_status ON payment_requests(status, created_at)")
    except Exception:
        pass

    # ── payment_records ──────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_records (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            payment_request_id TEXT DEFAULT '',
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'IQD',
            method TEXT DEFAULT '',
            plan TEXT NOT NULL,
            period_start TEXT DEFAULT '',
            period_end TEXT DEFAULT '',
            status TEXT DEFAULT 'completed',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_rec_restaurant ON payment_records(restaurant_id, created_at)")
    except Exception:
        pass

    # Create oauth_states table (CSRF + session for OAuth flows)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_states (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            state TEXT UNIQUE NOT NULL,
            code_verifier TEXT DEFAULT '',
            redirect_back TEXT DEFAULT '',
            pages_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_states_state ON oauth_states(state)"
        )
    except Exception:
        pass

    # Create connection_errors table (error log per channel)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS connection_errors (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            restaurant_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            error_code TEXT DEFAULT '',
            error_message TEXT NOT NULL,
            error_type TEXT DEFAULT 'webhook',
            request_payload TEXT DEFAULT '',
            resolved INTEGER DEFAULT 0,
            resolved_at TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conn_errors_channel ON connection_errors(channel_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conn_errors_restaurant ON connection_errors(restaurant_id, resolved, created_at)"
        )
    except Exception:
        pass

    # Create bot_corrections table (structured corrections with metadata)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_corrections (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            text TEXT NOT NULL,
            added_by TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_corrections_dedup ON bot_corrections(restaurant_id, text)"
        )
    except Exception:
        pass

    # Create reply_templates table if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reply_templates (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # ── subscription_plans — editable plan catalogue ──────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscription_plans (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price REAL DEFAULT 0,
            currency TEXT DEFAULT 'IQD',
            billing_period TEXT DEFAULT 'monthly',
            duration_days INTEGER DEFAULT 30,
            is_active INTEGER DEFAULT 1,
            is_public INTEGER DEFAULT 1,
            display_order INTEGER DEFAULT 0,
            max_channels INTEGER DEFAULT 1,
            max_products INTEGER DEFAULT 10,
            max_staff INTEGER DEFAULT 2,
            max_conversations_per_month INTEGER DEFAULT 200,
            ai_enabled INTEGER DEFAULT 1,
            analytics_enabled INTEGER DEFAULT 0,
            media_enabled INTEGER DEFAULT 0,
            voice_enabled INTEGER DEFAULT 0,
            human_handoff_enabled INTEGER DEFAULT 1,
            support_level TEXT DEFAULT 'community',
            features_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            badge TEXT DEFAULT '',
            excluded_features_json TEXT DEFAULT '[]',
            name_ar TEXT DEFAULT '',
            description_ar TEXT DEFAULT '',
            billing_period_ar TEXT DEFAULT '',
            badge_text_ar TEXT DEFAULT '',
            is_recommended INTEGER DEFAULT 0,
            max_customers INTEGER DEFAULT 0,
            max_ai_replies_per_month INTEGER DEFAULT 0,
            max_team_members INTEGER DEFAULT 2,
            max_branches INTEGER DEFAULT 1,
            telegram_enabled INTEGER DEFAULT 1,
            whatsapp_enabled INTEGER DEFAULT 1,
            instagram_enabled INTEGER DEFAULT 1,
            facebook_enabled INTEGER DEFAULT 1,
            multi_channel_enabled INTEGER DEFAULT 0,
            memory_enabled INTEGER DEFAULT 0,
            upsell_enabled INTEGER DEFAULT 0,
            smart_recommendations_enabled INTEGER DEFAULT 0,
            advanced_analytics_enabled INTEGER DEFAULT 0,
            image_enabled INTEGER DEFAULT 0,
            video_enabled INTEGER DEFAULT 0,
            story_reply_enabled INTEGER DEFAULT 0,
            menu_image_understanding_enabled INTEGER DEFAULT 0,
            live_readiness_status_enabled INTEGER DEFAULT 0,
            priority_support_enabled INTEGER DEFAULT 0,
            setup_assistance_enabled INTEGER DEFAULT 0,
            limits_json TEXT DEFAULT '{}'
        )
    """)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_plans_code ON subscription_plans(code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_plans_active ON subscription_plans(is_active, is_public, display_order)")
    except Exception:
        pass

    # Seed default plans if table is empty
    existing_plans = conn.execute("SELECT COUNT(*) FROM subscription_plans").fetchone()[0]
    if existing_plans == 0:
        _seed_plans = [
            # (id, code, name, price, currency, billing_period, duration_days,
            #  is_active, is_public, display_order, max_channels, max_products, max_staff,
            #  max_conversations_per_month, ai_enabled, analytics_enabled, voice_enabled,
            #  human_handoff_enabled, support_level)
            ("plan_free",         "free",         "المجانية",    0,      "USD", "custom",  0,  1, 1, 1, 0,   20,   1,  0,      0, 0, 0, 0, "community"),
            ("plan_starter",      "starter",      "الأساسية",    0,      "USD", "monthly", 30, 1, 1, 2, 1,   100,  5,  1000,   1, 1, 0, 1, "email"),
            ("plan_professional", "professional", "الاحترافية",  0,      "USD", "monthly", 30, 1, 1, 3, 4,   1000, 15, 10000,  1, 1, 1, 1, "priority"),
            ("plan_enterprise",   "enterprise",   "المؤسسات",    0,      "USD", "monthly", 30, 1, 1, 4, 9999,9999, 9999,999999,1, 1, 1, 1, "dedicated"),
        ]
        for p in _seed_plans:
            conn.execute("""
                INSERT OR IGNORE INTO subscription_plans
                    (id, code, name, price, currency, billing_period, duration_days,
                     is_active, is_public, display_order, max_channels, max_products, max_staff,
                     max_conversations_per_month, ai_enabled, analytics_enabled, voice_enabled,
                     human_handoff_enabled, support_level)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, p)
        conn.commit()

    # NOTE: Arabic backfill for default plans runs AFTER migrations loop below,
    # because new columns (name_ar etc.) must exist first.

    # ── announcements — platform-wide in-app announcements/ads ───────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            message TEXT DEFAULT '',
            type TEXT DEFAULT 'info',
            priority INTEGER DEFAULT 0,
            cta_text TEXT DEFAULT '',
            cta_url TEXT DEFAULT '',
            placement TEXT DEFAULT 'dashboard_top_banner',
            target_all INTEGER DEFAULT 1,
            target_restaurant_ids_json TEXT DEFAULT '[]',
            target_plans_json TEXT DEFAULT '[]',
            target_statuses_json TEXT DEFAULT '[]',
            target_channel_problem_only INTEGER DEFAULT 0,
            target_expired_only INTEGER DEFAULT 0,
            starts_at TEXT DEFAULT '',
            ends_at TEXT DEFAULT '',
            is_dismissible INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS announcement_dismissals (
            id TEXT PRIMARY KEY,
            announcement_id TEXT NOT NULL,
            restaurant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            dismissed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            code TEXT NOT NULL,
            discount_type TEXT DEFAULT 'percent',
            discount_value REAL DEFAULT 0,
            min_order REAL DEFAULT 0,
            max_uses INTEGER DEFAULT 0,
            uses_count INTEGER DEFAULT 0,
            expires_at TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_code ON promo_codes(restaurant_id, code)"
        )
    except Exception:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_active ON announcements(is_active, placement, priority)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ann_dismissal ON announcement_dismissals(announcement_id, restaurant_id, user_id)")
    except Exception:
        pass
    conn.commit()

    # ── menu_images — restaurant menu/category photos for bot delivery ────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_images (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            image_url TEXT NOT NULL,
            category TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_menu_images_restaurant ON menu_images(restaurant_id, is_active, sort_order)")
    except Exception:
        pass
    conn.commit()

    # ── ai_feedback — customer/owner ratings on bot replies (NUMBER 25) ─────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_feedback (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            conversation_id TEXT DEFAULT '',
            message_id TEXT DEFAULT '',
            rating TEXT DEFAULT 'bad',
            reason TEXT DEFAULT '',
            suggested_correction TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT DEFAULT '',
            reviewed_by TEXT DEFAULT '',
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_feedback_restaurant ON ai_feedback(restaurant_id, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_feedback_conv ON ai_feedback(conversation_id)")
    except Exception:
        pass

    # ── restaurant_knowledge — owner-curated knowledge base (NUMBER 25) ────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurant_knowledge (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            is_active INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_restaurant ON restaurant_knowledge(restaurant_id, is_active, priority)")
    except Exception:
        pass

    # ── ai_quality_logs — per-reply quality tracking (NUMBER 25) ──────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_quality_logs (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            conversation_id TEXT DEFAULT '',
            message_id TEXT DEFAULT '',
            intent_detected TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            used_corrections INTEGER DEFAULT 0,
            used_knowledge INTEGER DEFAULT 0,
            escalation_triggered INTEGER DEFAULT 0,
            response_quality TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_quality_restaurant ON ai_quality_logs(restaurant_id, created_at)")
    except Exception:
        pass
    conn.commit()

    # ── bot_correction_versions — immutable snapshot per change (NUMBER 25B) ────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_correction_versions (
            id TEXT PRIMARY KEY,
            correction_id TEXT NOT NULL,
            restaurant_id TEXT NOT NULL,
            trigger_text TEXT DEFAULT '',
            correction_text TEXT DEFAULT '',
            category TEXT DEFAULT '',
            priority INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            version_number INTEGER DEFAULT 1,
            changed_by TEXT DEFAULT '',
            change_reason TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_corr_ver_correction ON bot_correction_versions(correction_id, version_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_corr_ver_restaurant ON bot_correction_versions(restaurant_id, created_at)")
    except Exception:
        pass

    # ── restaurant_knowledge_versions — immutable snapshot per change (NUMBER 25B)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurant_knowledge_versions (
            id TEXT PRIMARY KEY,
            knowledge_id TEXT NOT NULL,
            restaurant_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            content TEXT DEFAULT '',
            category TEXT DEFAULT '',
            priority INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            version_number INTEGER DEFAULT 1,
            changed_by TEXT DEFAULT '',
            change_reason TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_know_ver_knowledge ON restaurant_knowledge_versions(knowledge_id, version_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_know_ver_restaurant ON restaurant_knowledge_versions(restaurant_id, created_at)")
    except Exception:
        pass

    # ── shift_commands — staff real-time bot instructions ────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shift_commands (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            command_text TEXT NOT NULL,
            created_by TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shift_commands_rest ON shift_commands(restaurant_id, is_active)")
    except Exception:
        pass

    # ── exception_playbook — deterministic replies for hard situations ────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exception_playbook (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            trigger_keywords TEXT DEFAULT '[]',
            reply_text TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            priority INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exception_playbook_rest ON exception_playbook(restaurant_id, is_active)")
    except Exception:
        pass

    # ── bot_unclear_log — track questions bot couldn't answer (weekly report) ─
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_unclear_log (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            customer_message TEXT NOT NULL,
            conversation_id TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unclear_rest ON bot_unclear_log(restaurant_id, created_at)")
    except Exception:
        pass

    # ── ai_change_logs — full audit trail for all AI learning actions (NUMBER 25B)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_change_logs (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT DEFAULT '',
            actor_user_id TEXT DEFAULT '',
            actor_role TEXT DEFAULT '',
            entity_type TEXT DEFAULT '',
            entity_id TEXT DEFAULT '',
            action TEXT DEFAULT '',
            old_value_json TEXT DEFAULT '{}',
            new_value_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_change_restaurant ON ai_change_logs(restaurant_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_change_entity ON ai_change_logs(entity_type, entity_id)")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS outgoing_webhooks (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL,
            secret TEXT DEFAULT '',
            events TEXT NOT NULL DEFAULT '["order.confirmed"]',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_triggered_at TEXT DEFAULT NULL,
            last_status_code INTEGER DEFAULT NULL,
            fail_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ow_restaurant ON outgoing_webhooks(restaurant_id, is_active)")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            working_hours TEXT DEFAULT '{}',
            is_active INTEGER NOT NULL DEFAULT 1,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_branches_restaurant ON branches(restaurant_id, is_active)")
    except Exception:
        pass
    conn.commit()

    # ── story_context_cache — NUMBER 33: Vision API cache by story_id ──────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS story_context_cache (
            id TEXT PRIMARY KEY,
            cache_key TEXT UNIQUE NOT NULL,
            restaurant_id TEXT NOT NULL,
            channel TEXT DEFAULT 'instagram',
            platform_story_id TEXT DEFAULT '',
            media_url_hash TEXT DEFAULT '',
            story_type TEXT DEFAULT 'unknown',
            matched_product_id TEXT DEFAULT '',
            matched_product_name TEXT DEFAULT '',
            matched_product_price REAL DEFAULT 0,
            matched_category TEXT DEFAULT '',
            confidence TEXT DEFAULT 'low',
            analysis_summary TEXT DEFAULT '',
            raw_analysis_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL
        )
    """)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_story_cache_key ON story_context_cache(cache_key, expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_story_cache_restaurant ON story_context_cache(restaurant_id, channel)")
    except Exception:
        pass
    conn.commit()

    if IS_POSTGRES:
        # PostgreSQL supports ADD COLUMN IF NOT EXISTS
        for table, column, col_def in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}")
                conn.commit()
            except Exception:
                pass
    else:
        # SQLite: use try/except (no IF NOT EXISTS for ADD COLUMN in older versions)
        for table, column, col_def in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            except Exception:
                pass
        conn.commit()

    # ── Data fix: every WhatsApp channel must have a verify_token ─────────────
    # Runs after migrations so verify_token column is guaranteed to exist.
    try:
        import uuid as _uuid
        empty_wa = conn.execute(
            "SELECT id FROM channels WHERE type='whatsapp' AND (verify_token IS NULL OR verify_token='')"
        ).fetchall()
        for row in empty_wa:
            conn.execute(
                "UPDATE channels SET verify_token=? WHERE id=?",
                (str(_uuid.uuid4()), row["id"] if hasattr(row, "keys") else row[0])
            )
        if empty_wa:
            conn.commit()
    except Exception:
        pass

    # ── Data fix: full Arabic backfill for default plans (after migrations) ───
    # name_ar and other new columns are guaranteed to exist by this point.
    _arabic_plans_full = {
        "free": {
            "name": "المجانية",
            "name_ar": "الخطة المجانية",
            "description_ar": "للتجربة فقط، بدون تشغيل فعلي للقنوات",
            "billing_period_ar": "مجانية",
            "badge": "", "badge_text_ar": "", "is_recommended": 0,
            "is_public": 1, "display_order": 1,
            "max_channels": 0, "max_products": 20, "max_staff": 1,
            "max_conversations_per_month": 0, "max_team_members": 1, "max_branches": 1,
            "ai_enabled": 0, "analytics_enabled": 0, "advanced_analytics_enabled": 0,
            "voice_enabled": 0, "image_enabled": 0, "video_enabled": 0,
            "story_reply_enabled": 0, "human_handoff_enabled": 0,
            "multi_channel_enabled": 0, "memory_enabled": 0, "upsell_enabled": 0,
            "smart_recommendations_enabled": 0, "menu_image_understanding_enabled": 0,
            "live_readiness_status_enabled": 0, "priority_support_enabled": 0,
            "setup_assistance_enabled": 0, "support_level": "community",
            "telegram_enabled": 0, "whatsapp_enabled": 0,
            "instagram_enabled": 0, "facebook_enabled": 0,
            "features_json": '["دخول إلى لوحة التحكم","إضافة عدد محدود من المنتجات","تجربة شكل المنصة","عرض إعدادات المطعم الأساسية"]',
            "excluded_features_json": '["ربط قنوات التواصل","ردود الذكاء الاصطناعي","استقبال طلبات حقيقية","التحليلات المتقدمة","الفويس والصور والستوري","دعم فني مخصص"]',
        },
        "starter": {
            "name": "الأساسية",
            "name_ar": "الخطة الأساسية",
            "description_ar": "للمطاعم الصغيرة التي تريد قناة واحدة وردود بسيطة",
            "billing_period_ar": "شهري",
            "badge": "", "badge_text_ar": "", "is_recommended": 0,
            "is_public": 1, "display_order": 2,
            "max_channels": 1, "max_products": 100, "max_staff": 5,
            "max_conversations_per_month": 1000, "max_team_members": 5, "max_branches": 1,
            "ai_enabled": 1, "analytics_enabled": 1, "advanced_analytics_enabled": 0,
            "voice_enabled": 0, "image_enabled": 0, "video_enabled": 0,
            "story_reply_enabled": 0, "human_handoff_enabled": 1,
            "multi_channel_enabled": 0, "memory_enabled": 0, "upsell_enabled": 0,
            "smart_recommendations_enabled": 0, "menu_image_understanding_enabled": 0,
            "live_readiness_status_enabled": 0, "priority_support_enabled": 0,
            "setup_assistance_enabled": 0, "support_level": "email",
            "telegram_enabled": 1, "whatsapp_enabled": 1,
            "instagram_enabled": 1, "facebook_enabled": 1,
            "features_json": '["قناة واحدة فقط","ردود ذكاء اصطناعي أساسية","إدارة الطلبات","حفظ محادثات الزبائن","100 منتج","تحليلات بسيطة","تحويل يدوي للموظف"]',
            "excluded_features_json": '["تعدد القنوات","الفويس","فهم الصور والفيديو","الرد على الستوري","تحليلات متقدمة","دعم أولوية","فروع متعددة"]',
        },
        "professional": {
            "name": "الاحترافية",
            "name_ar": "الخطة الاحترافية",
            "description_ar": "الخطة المناسبة لمعظم المطاعم التي تريد تشغيل حقيقي على أكثر من قناة",
            "billing_period_ar": "شهري",
            "badge": "الأكثر طلبًا", "badge_text_ar": "الأكثر طلبًا", "is_recommended": 1,
            "is_public": 1, "display_order": 3,
            "max_channels": 4, "max_products": 1000, "max_staff": 15,
            "max_conversations_per_month": 10000, "max_team_members": 15, "max_branches": 3,
            "ai_enabled": 1, "analytics_enabled": 1, "advanced_analytics_enabled": 1,
            "voice_enabled": 1, "image_enabled": 1, "video_enabled": 1,
            "story_reply_enabled": 1, "human_handoff_enabled": 1,
            "multi_channel_enabled": 1, "memory_enabled": 1, "upsell_enabled": 1,
            "smart_recommendations_enabled": 1, "menu_image_understanding_enabled": 1,
            "live_readiness_status_enabled": 1, "priority_support_enabled": 0,
            "setup_assistance_enabled": 0, "support_level": "priority",
            "telegram_enabled": 1, "whatsapp_enabled": 1,
            "instagram_enabled": 1, "facebook_enabled": 1,
            "features_json": '["ربط عدة قنوات (Telegram/WhatsApp/Instagram/Facebook)","ردود ذكاء اصطناعي متقدمة","ذاكرة للزبائن والطلبات السابقة","اقتراحات بيع ورفع قيمة الطلب","إدارة الطلبات كاملة","الفويس","فهم الصور والمنيو","الرد على الستوري","تحليلات كاملة","متابعة أداء القنوات","تحويل للموظف","تنبيهات ومتابعة حالة القنوات"]',
            "excluded_features_json": '["دعم مخصص 24/7","تطويرات خاصة حسب الطلب","فروع غير محدودة","SLA خاص"]',
        },
        "enterprise": {
            "name": "المؤسسات",
            "name_ar": "خطة المؤسسات",
            "description_ar": "للمطاعم الكبيرة أو السلاسل أو من يحتاج تخصيص ودعم أعلى",
            "billing_period_ar": "مخصص",
            "badge": "للشركات والسلاسل", "badge_text_ar": "للشركات والسلاسل", "is_recommended": 0,
            "is_public": 1, "display_order": 4,
            "max_channels": 9999, "max_products": 9999, "max_staff": 9999,
            "max_conversations_per_month": 999999, "max_team_members": 9999, "max_branches": 9999,
            "ai_enabled": 1, "analytics_enabled": 1, "advanced_analytics_enabled": 1,
            "voice_enabled": 1, "image_enabled": 1, "video_enabled": 1,
            "story_reply_enabled": 1, "human_handoff_enabled": 1,
            "multi_channel_enabled": 1, "memory_enabled": 1, "upsell_enabled": 1,
            "smart_recommendations_enabled": 1, "menu_image_understanding_enabled": 1,
            "live_readiness_status_enabled": 1, "priority_support_enabled": 1,
            "setup_assistance_enabled": 1, "support_level": "dedicated",
            "telegram_enabled": 1, "whatsapp_enabled": 1,
            "instagram_enabled": 1, "facebook_enabled": 1,
            "features_json": '["كل مزايا الخطة الاحترافية","حدود أعلى للمحادثات والمنتجات","دعم أولوية","مساعدة في الإعداد والربط","أكثر من فرع إذا مدعوم","إعدادات خاصة حسب المطعم","تقارير متقدمة","صلاحيات فريق أوسع","متابعة تشغيل أقوى","إمكانية تخصيص بعض الردود والقواعد"]',
            "excluded_features_json": '["أي ميزة خارج الاتفاق المكتوب","تكاليف بوابات الدفع الخارجية إن وجدت"]',
        },
    }
    _bf_cols = list(next(iter(_arabic_plans_full.values())).keys())
    for _code, _vals in _arabic_plans_full.items():
        try:
            _existing = conn.execute("SELECT name_ar FROM subscription_plans WHERE code=?", (_code,)).fetchone()
            if _existing is not None and not (_existing[0] or ""):
                _set_clause = ", ".join(f"{c}=?" for c in _bf_cols)
                conn.execute(
                    f"UPDATE subscription_plans SET {_set_clause} WHERE code=?",
                    [_vals[c] for c in _bf_cols] + [_code]
                )
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass

    if _pg:
        conn._conn.autocommit = False


def _log_db_config():
    """Print safe DB startup info. Never prints passwords or full URLs."""
    backend = "PostgreSQL" if IS_POSTGRES else "SQLite"
    print(f"[DB] backend={backend}")
    if IS_POSTGRES:
        _hm = re.search(r'@([^/:@]+)', DATABASE_URL)
        _host = _hm.group(1) if _hm else ""
        _sm = re.search(r'sslmode=([^&\s]+)', DATABASE_URL)
        _sslmode = _sm.group(1) if _sm else "none"
        _internal = '.' not in _host if _host else None
        print(f"[DB] database_url_present=true")
        print(f"[DB] host_present={bool(_host)}")
        print(f"[DB] sslmode={_sslmode}")
        print(f"[DB] looks_internal={_internal}")
    else:
        print(f"[DB] database_url_present=false")


def init_db():
    _log_db_config()
    print(f"[DB] init_db starting — backend={'PostgreSQL' if IS_POSTGRES else 'SQLite'}")
    # Warn loudly if running SQLite in a production-like environment.
    # Data is stored on the local filesystem and WILL be wiped on every Render/Railway deploy.
    if not IS_POSTGRES:
        _is_prod = any(os.getenv(v) for v in ("RENDER", "RAILWAY_ENVIRONMENT", "HEROKU_APP_NAME", "FLY_APP_NAME"))
        _has_port = bool(os.getenv("PORT"))  # any cloud host sets PORT
        if _is_prod or _has_port:
            print(
                "\n⚠️  WARNING: Running SQLite in a cloud environment.\n"
                "   Data WILL be lost on every redeploy (ephemeral filesystem).\n"
                "   Set DATABASE_URL to a PostgreSQL connection string to persist data.\n"
            )
    conn = get_db()

    # ── Create schema ────────────────────────────────────────────────────────
    if IS_POSTGRES:
        conn.executescript(CREATE_TABLES_SQL)
        # executescript uses autocommit; no explicit commit needed
    else:
        c = conn.cursor()
        c.executescript(CREATE_TABLES_SQL)
        conn.commit()

    # ── Migrations & indexes ─────────────────────────────────────────────────
    _migrate_db(conn)
    _create_indexes(conn)
    if IS_POSTGRES:
        conn.commit()

    # ── Seed platform_config defaults ────────────────────────────────────────
    try:
        conn.execute(
            "INSERT OR IGNORE INTO platform_config (key, value) VALUES ('support_phone', '9647710005018')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO platform_config (key, value) VALUES ('platform_name', 'منصة المطاعم')"
        )
        conn.commit()
    except Exception:
        pass

    # ── Seed restaurant data (only if empty) ─────────────────────────────────
    try:
        if IS_POSTGRES:
            row = conn.execute("SELECT COUNT(*) as cnt FROM restaurants").fetchone()
            existing = int(row["cnt"])
        else:
            row = conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()
            existing = row[0]
        print(f"[DB] restaurants count = {existing}")
    except Exception as e:
        print(f"[DB] ERROR reading restaurants count: {e}")
        existing = 1  # assume non-empty to avoid bad seed attempt

    if existing == 0:
        try:
            _seed_data(conn)
            print("[DB] seed data inserted OK")
        except Exception as e:
            print(f"[DB] ERROR in _seed_data: {e}")
            try:
                conn.commit()  # partial commit — better than nothing
            except Exception:
                pass

    # ── Seed super admin (always ensure at least one exists) ─────────────────
    try:
        if IS_POSTGRES:
            sa_row = conn.execute("SELECT COUNT(*) as cnt FROM super_admins").fetchone()
            sa_count = int(sa_row["cnt"])
        else:
            sa_row = conn.execute("SELECT COUNT(*) FROM super_admins").fetchone()
            sa_count = sa_row[0]
        print(f"[DB] super_admins count = {sa_count}")
    except Exception as e:
        print(f"[DB] ERROR reading super_admins count: {e}")
        sa_count = 1

    if sa_count == 0:
        try:
            _seed_super_admin(conn)
        except Exception as e:
            print(f"[DB] ERROR in _seed_super_admin: {e}")

    try:
        conn.commit()
    except Exception:
        pass
    conn.close()
    print(f"✅ Database initialized ({'PostgreSQL' if IS_POSTGRES else 'SQLite'})")


# ── Password helper ───────────────────────────────────────────────────────────

def _hash(password: str) -> str:
    import bcrypt as _bcrypt
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


# ── Super admin seed ─────────────────────────────────────────────────────────

def _seed_super_admin(conn):
    conn.execute("""
        INSERT INTO super_admins (id, email, password_hash, name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(email) DO NOTHING
    """, (str(uuid.uuid4()), "superadmin@platform.com", _hash("super123"), "Super Admin"))
    conn.commit()
    print("✅ Super admin seeded — login: superadmin@platform.com / super123")


# ── Seed data ─────────────────────────────────────────────────────────────────

def _seed_data(conn):
    rid = str(uuid.uuid4())
    uid = str(uuid.uuid4())

    conn.execute("""
        INSERT INTO restaurants (id, name, description, phone, address, plan)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (rid, "مطعم النخبة", "أفضل المأكولات العربية الأصيلة",
          "+966-50-000-0000", "الرياض، حي النزهة، شارع الأمير", "professional"))

    conn.execute("""
        INSERT INTO users (id, restaurant_id, email, password_hash, name, role)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (uid, rid, "admin@restaurant.com", _hash("admin123"), "المدير", "owner"))

    mid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO users (id, restaurant_id, email, password_hash, name, role)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (mid, rid, "manager@restaurant.com", _hash("manager123"), "مدير العمليات", "manager"))

    sid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO users (id, restaurant_id, email, password_hash, name, role)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (sid, rid, "staff@restaurant.com", _hash("staff123"), "موظف خدمة العملاء", "staff"))

    products = [
        ("برجر كلاسيك",     35.0, "برجر",          "برجر لحم بقري مع خضار طازجة وصوص خاص",       "🍔", 85),
        ("بيتزا مارغريتا",  55.0, "بيتزا",          "بيتزا إيطالية بجبن الموزاريلا الطازج",        "🍕", 72),
        ("شاورما دجاج",     25.0, "شاورما",         "شاورما دجاج مشوي مع صوص الطحينة",            "🌯", 120),
        ("سلطة سيزر",       22.0, "سلطة",           "سلطة سيزر الكلاسيكية مع الخبز المحمص",       "🥗", 45),
        ("عصير برتقال",     12.0, "مشروبات",        "عصير برتقال طبيعي طازج معصور",               "🍊", 60),
        ("آيس كريم فانيليا", 15.0, "حلويات",        "آيس كريم فانيليا مع توبينج متنوع",            "🍨", 38),
        ("كوكا كولا",        8.0, "مشروبات",        "كوكا كولا باردة حجم 330 مل",                 "🥤", 95),
        ("كيك شوكولاتة",    28.0, "حلويات",         "كيك بلجيكية فاخرة بالشوكولاتة الداكنة",      "🎂", 29),
        ("فراخ مشوية",      45.0, "دجاج",           "فراخ مشوية بتتبيلة خاصة مع أرز",             "🍗", 67),
        ("سمك مقلي",        50.0, "مأكولات بحرية",  "سمك طازج مقلي مع بطاطس وصوص طرطور",         "🐟", 33),
    ]

    product_ids = []
    for name, price, cat, desc, icon, cnt in products:
        pid = str(uuid.uuid4())
        product_ids.append(pid)
        conn.execute("""
            INSERT INTO products (id, restaurant_id, name, price, category, description, icon, available, order_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (pid, rid, name, price, cat, desc, icon, cnt))

    customers_data = [
        ("أحمد محمد",    "+966501234567", "telegram",  1, "لا بصل",        "برجر كلاسيك",     15, 680.0),
        ("سارة علي",     "+966507654321", "whatsapp",  0, "نباتية",         "سلطة سيزر",        6, 245.0),
        ("خالد عبدالله", "+966509876543", "telegram",  1, "حار جداً",      "شاورما دجاج",     22, 990.0),
        ("نورا حسن",     "+966512345678", "instagram", 0, "بدون غلوتين",   "بيتزا مارغريتا",   8, 420.0),
        ("محمد يوسف",    "+966523456789", "telegram",  0, "لا تفضيلات",    "عصير برتقال",      3, 105.0),
        ("فاطمة أحمد",   "+966534567890", "facebook",  1, "صوص خفيف",     "فراخ مشوية",      18, 780.0),
    ]

    customer_ids = []
    for name, phone, platform, vip, pref, fav, tot_ord, tot_spent in customers_data:
        cid = str(uuid.uuid4())
        customer_ids.append(cid)
        conn.execute("""
            INSERT INTO customers (id, restaurant_id, name, phone, platform, vip, preferences,
                                   favorite_item, total_orders, total_spent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cid, rid, name, phone, platform, vip, pref, fav, tot_ord, tot_spent))

    now = datetime.now()
    orders_data = [
        (customer_ids[0], "telegram",  "delivery", "pending",   70.0,  now),
        (customer_ids[1], "whatsapp",  "pickup",   "confirmed", 22.0,  now - timedelta(hours=1)),
        (customer_ids[2], "telegram",  "delivery", "preparing", 50.0,  now - timedelta(hours=2)),
        (customer_ids[3], "instagram", "delivery", "on_way",   110.0,  now - timedelta(hours=3)),
        (customer_ids[0], "telegram",  "delivery", "delivered",  35.0, now - timedelta(days=1)),
        (customer_ids[4], "telegram",  "pickup",   "delivered",  20.0, now - timedelta(days=1)),
        (customer_ids[1], "whatsapp",  "delivery", "delivered",  77.0, now - timedelta(days=2)),
        (customer_ids[2], "telegram",  "delivery", "cancelled",  25.0, now - timedelta(days=2)),
        (customer_ids[5], "facebook",  "delivery", "delivered",  90.0, now - timedelta(days=1)),
        (customer_ids[3], "instagram", "pickup",   "delivered",  55.0, now - timedelta(days=3)),
    ]

    order_ids = []
    for cid, channel, otype, status, total, created in orders_data:
        oid = str(uuid.uuid4())
        order_ids.append(oid)
        conn.execute("""
            INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, status, address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (oid, rid, cid, channel, otype, total, status,
              "الرياض، حي النزهة، شارع الأمير", created.isoformat()))
        conn.execute("""
            INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), oid, product_ids[0], products[0][0], products[0][1], 1))

    conv_data = [
        (customer_ids[0], "bot",   "open",   0, 3),
        (customer_ids[1], "human", "open",   1, 0),
        (customer_ids[2], "bot",   "open",   0, 5),
        (customer_ids[3], "human", "closed", 0, 0),
        (customer_ids[4], "bot",   "open",   0, 1),
    ]

    conv_ids = []
    for cid, mode, status, urgent, unread in conv_data:
        conv_id = str(uuid.uuid4())
        conv_ids.append(conv_id)
        conn.execute("""
            INSERT INTO conversations (id, restaurant_id, customer_id, mode, status, urgent, unread_count, bot_turn_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (conv_id, rid, cid, mode, status, urgent, unread, 2))
        for role, content in [
            ("customer", "مرحباً، أريد طلب طعام"),
            ("bot",      "أهلاً وسهلاً! يسعدني مساعدتك. ماذا تريد أن تطلب؟"),
            ("customer", "ما هي العروض المتاحة اليوم؟"),
        ]:
            conn.execute("""
                INSERT INTO messages (id, conversation_id, role, content)
                VALUES (?, ?, ?, ?)
            """, (str(uuid.uuid4()), conv_id, role, content))

    conn.execute("""
        INSERT INTO settings (id, restaurant_id, restaurant_name, restaurant_description,
                              restaurant_phone, restaurant_address, bot_name, bot_welcome, bot_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (str(uuid.uuid4()), rid, "مطعم النخبة",
          "أفضل المأكولات العربية الأصيلة",
          "+966-50-000-0000",
          "الرياض، حي النزهة، شارع الأمير",
          "بوت النخبة",
          "أهلاً بك في مطعم النخبة! 🍽️ كيف يمكنني خدمتك؟"))

    for ch_type in ["telegram", "whatsapp", "instagram", "facebook"]:
        conn.execute("""
            INSERT INTO channels (id, restaurant_id, type, name, enabled, verified)
            VALUES (?, ?, ?, ?, 0, 0)
        """, (str(uuid.uuid4()), rid, ch_type, f"قناة {ch_type}"))

    conn.execute("""
        INSERT INTO bot_config (id, restaurant_id, system_prompt, sales_prompt,
                                escalation_keywords, fallback_message,
                                memory_enabled, max_bot_turns, auto_handoff_enabled,
                                order_extraction_enabled)
        VALUES (?, ?, ?, ?, ?, ?, 1, 15, 1, 1)
    """, (
        str(uuid.uuid4()), rid,
        "أنت مساعد ذكاء اصطناعي لمطعم النخبة. ساعد العملاء بكل ود واحترافية. "
        "قدم لهم قائمة الطعام عند الطلب، وساعدهم في اختيار وجباتهم المفضلة. "
        "اسأل عن العنوان عند تأكيد الطلب. كن مختصراً ومفيداً.",
        "💡 لا تنسَ الإشارة إلى عروض اليوم وأكثر الوجبات طلباً مثل الشاورما والبرجر. "
        "عند الطلب بأكثر من 3 وجبات، اقترح على العميل طقم العائلة للتوفير.",
        json.dumps(["غاضب", "متأخر", "خطأ", "استرجاع", "بطيء"]),
        "سأحيلك لأحد موظفينا الآن، انتظر قليلاً. 🙏",
    ))

    activity_entries = [
        ("login",           "user",      uid,          "تسجيل دخول المدير"),
        ("order_created",   "order",     order_ids[0], "طلب جديد من أحمد محمد"),
        ("order_created",   "order",     order_ids[1], "طلب جديد من سارة علي"),
        ("staff_added",     "user",      mid,          "تمت إضافة مدير العمليات"),
        ("channel_updated", "channel",   rid,          "تم تحديث إعدادات قناة تيليجرام"),
    ]
    for action, etype, eid, desc in activity_entries:
        conn.execute("""
            INSERT INTO activity_log (id, restaurant_id, user_id, user_name, action, entity_type, entity_id, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), rid, uid, "المدير", action, etype, eid, desc))

    notifications_entries = [
        ("new_order",   "طلب جديد",          "طلب جديد من أحمد محمد بقيمة 70 ريال",  "order",        order_ids[0]),
        ("new_order",   "طلب جديد",          "طلب جديد من سارة علي بقيمة 22 ريال",   "order",        order_ids[1]),
        ("new_message", "رسالة جديدة",       "رسالة من خالد عبدالله عبر تيليجرام",   "conversation", conv_ids[0]),
        ("escalation",  "طلب تحويل للموظف", "العميل نورا حسن تطلب التحدث مع موظف",  "conversation", conv_ids[1]),
    ]
    for ntype, title, msg, etype, eid in notifications_entries:
        conn.execute("""
            INSERT INTO notifications (id, restaurant_id, type, title, message, entity_type, entity_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), rid, ntype, title, msg, etype, eid))

    # Seed subscription for the restaurant
    trial_end = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    conn.execute("""
        INSERT INTO subscriptions (id, restaurant_id, plan, status, price,
                                   start_date, end_date, trial_ends_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), rid, "professional", "active", 299.0,
          datetime.now().strftime("%Y-%m-%d"),
          (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d"),
          trial_end,
          "Demo restaurant — created by seed"))

    conn.commit()
    print("✅ Seed data created — login: admin@restaurant.com / admin123")
