"""
scripts/reset_super_admin_password.py

One-time super admin password reset utility.
Reads the new password from RESET_SUPER_ADMIN_PASSWORD env var — never from argv.
Connects using the same DATABASE_URL + normalization logic the app uses.
Uses the same bcrypt hashing the app login system uses.

Usage (Render one-off command or locally against the Render DB):
    RESET_SUPER_ADMIN_PASSWORD="<new_password>" \
    DATABASE_URL="<render_postgres_url>"         \
    python scripts/reset_super_admin_password.py
"""
import os
import re
import sys
import uuid

# ── 1. Read new password from env — never from argv ──────────────────────────
new_password = os.environ.get("RESET_SUPER_ADMIN_PASSWORD", "")
if not new_password:
    print("ERROR: RESET_SUPER_ADMIN_PASSWORD env variable is not set or empty.", file=sys.stderr)
    sys.exit(1)

if len(new_password) < 8:
    print("ERROR: Password must be at least 8 characters.", file=sys.stderr)
    sys.exit(1)

# ── 2. Hash using the same method as the app ─────────────────────────────────
try:
    import bcrypt as _bcrypt
except ImportError:
    print("ERROR: bcrypt not installed. Run: pip install bcrypt", file=sys.stderr)
    sys.exit(1)

password_hash = _bcrypt.hashpw(new_password.encode(), _bcrypt.gensalt()).decode()
del new_password  # remove from memory as soon as hashed

# ── 3. Resolve DATABASE_URL using same normalization as database.py ───────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    url = url.replace("sslmode=no-verify", "sslmode=require")
    _hm = re.search(r'@([^/:@]+)', url)
    if _hm:
        _host = _hm.group(1)
        _is_external = '.' in _host
        if _is_external and 'sslmode=' not in url:
            url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url

DATABASE_URL = _normalize_db_url(DATABASE_URL)

if not DATABASE_URL:
    print("ERROR: DATABASE_URL env variable is not set.", file=sys.stderr)
    sys.exit(1)

# ── 4. Connect and upsert the super admin record ─────────────────────────────
SA_EMAIL = "superadmin@platform.com"
SA_NAME  = "Super Admin"

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # Upsert: update password if row exists, insert if not
    cur.execute("SELECT id FROM super_admins WHERE email = %s", (SA_EMAIL,))
    row = cur.fetchone()

    if row:
        cur.execute(
            "UPDATE super_admins SET password_hash = %s WHERE email = %s",
            (password_hash, SA_EMAIL),
        )
    else:
        cur.execute(
            "INSERT INTO super_admins (id, email, password_hash, name) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), SA_EMAIL, password_hash, SA_NAME),
        )

    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print(f"ERROR: Database operation failed — {e}", file=sys.stderr)
    sys.exit(1)

print("Super admin password reset complete")
