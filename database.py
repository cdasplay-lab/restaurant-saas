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
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or [])
        return _PgCursor(cur)

    def executescript(self, script: str):
        """Execute a multi-statement SQL script (DDL)."""
        # Adjust CREATE TABLE defaults for PostgreSQL
        script = script.replace(
            "TEXT DEFAULT CURRENT_TIMESTAMP",
            "TEXT DEFAULT to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')"
        )
        cur = self._conn.cursor()
        # Split by ; and execute each non-empty statement
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in stmts:
            try:
                cur.execute(stmt)
            except Exception as e:
                # Ignore "already exists" errors during DDL
                if "already exists" in str(e).lower():
                    self._conn.rollback()
                else:
                    self._conn.rollback()
                    print(f"[DB] DDL error (ignored): {e}")
        cur.close()

    def commit(self):
        self._conn.commit()

    def close(self):
        if self._pool is not None:
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
)
"""


def _create_indexes(conn):
    """Create performance indexes (safe — IF NOT EXISTS)."""
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
    ]
    for idx in indexes:
        try:
            conn.execute(idx)
        except Exception:
            pass


def _migrate_db(conn):
    """Add new columns to existing tables. Safe — ignores if column already exists."""
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
        # users new columns
        ("users", "last_login",               "TEXT"),
        # messages new columns (media + voice + story reply)
        ("messages", "media_type",            "TEXT DEFAULT ''"),
        ("messages", "media_url",             "TEXT DEFAULT ''"),
        ("messages", "voice_transcript",      "TEXT DEFAULT ''"),
        ("messages", "replied_story_id",      "TEXT DEFAULT ''"),
        ("messages", "replied_story_text",    "TEXT DEFAULT ''"),
        ("messages", "replied_story_media_url", "TEXT DEFAULT ''"),
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
        # super_admins — support PIN for restaurant owner recovery
        ("super_admins", "support_pin", "TEXT DEFAULT ''"),
    ]

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


def init_db():
    conn = get_db()
    if IS_POSTGRES:
        conn.executescript(CREATE_TABLES_SQL)
        conn.commit()
    else:
        c = conn.cursor()
        c.executescript(CREATE_TABLES_SQL)
        conn.commit()
        c = conn  # reuse for seed check

    _migrate_db(conn)
    _create_indexes(conn)

    if IS_POSTGRES:
        row = conn.execute("SELECT COUNT(*) as cnt FROM restaurants").fetchone()
        existing = row["cnt"]
    else:
        row = conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()
        existing = row[0]

    if existing == 0:
        _seed_data(conn)

    # Seed super admin independently
    if IS_POSTGRES:
        sa_row = conn.execute("SELECT COUNT(*) as cnt FROM super_admins").fetchone()
        sa_count = sa_row["cnt"]
    else:
        sa_row = conn.execute("SELECT COUNT(*) FROM super_admins").fetchone()
        sa_count = sa_row[0]
    if sa_count == 0:
        _seed_super_admin(conn)

    conn.commit()
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
