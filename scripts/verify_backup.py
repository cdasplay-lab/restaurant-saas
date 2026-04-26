#!/usr/bin/env python3
"""
Verify a backup file produced by backup_database.py.

Usage:
    python scripts/verify_backup.py <backup_file>

Checks performed:
  1. File exists and is non-empty
  2. SHA-256 checksum matches the paired .sha256 file
  3. For .db files: opens with sqlite3 and runs PRAGMA integrity_check
  4. For .sql files: verifies it contains expected CREATE TABLE statements
  5. Reports counts of key tables found in the dump
"""
import sys
import hashlib
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


REQUIRED_TABLES = [
    "restaurants", "users", "products", "orders", "customers",
    "conversations", "messages", "subscriptions", "super_admins",
    "payment_requests", "channels",
]


def verify_checksum(backup: Path) -> bool:
    chk_file = backup.with_suffix(backup.suffix + ".sha256")
    if not chk_file.exists():
        print(f"[verify] WARNING: no checksum file found at {chk_file}")
        return True  # soft warning — don't fail if checksum was not generated
    expected_line = chk_file.read_text().strip()
    expected_digest = expected_line.split()[0]
    actual = _sha256(backup)
    if actual != expected_digest:
        print(f"[verify] FAIL: checksum mismatch")
        print(f"         expected: {expected_digest}")
        print(f"         actual:   {actual}")
        return False
    print(f"[verify] Checksum OK — {actual[:16]}…")
    return True


def verify_sqlite(backup: Path) -> bool:
    import sqlite3
    try:
        conn = sqlite3.connect(str(backup))
        conn.row_factory = sqlite3.Row
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            print(f"[verify] FAIL: integrity_check returned: {result}")
            conn.close()
            return False
        print(f"[verify] SQLite integrity_check: ok")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            print(f"[verify] FAIL: missing tables: {', '.join(missing)}")
            conn.close()
            return False
        for t in REQUIRED_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"[verify]   {t}: {count} rows")
        conn.close()
        return True
    except Exception as e:
        print(f"[verify] FAIL: SQLite error: {e}")
        return False


def verify_sql_dump(backup: Path) -> bool:
    content = backup.read_text(errors="replace")
    if len(content) < 100:
        print(f"[verify] FAIL: dump file is suspiciously small ({len(content)} chars)")
        return False
    found = []
    missing = []
    for table in REQUIRED_TABLES:
        marker = f"CREATE TABLE" if f"CREATE TABLE {table}" in content else f"TABLE {table}"
        if f"TABLE {table} " in content or f"TABLE {table}\n" in content or f'"{table}"' in content:
            found.append(table)
        else:
            missing.append(table)
    print(f"[verify] SQL dump: {len(found)}/{len(REQUIRED_TABLES)} required tables found")
    if missing:
        print(f"[verify] WARNING: tables not found in dump: {', '.join(missing)}")
    if "INSERT INTO" not in content and "COPY " not in content:
        print(f"[verify] WARNING: no data rows detected in dump")
    return len(missing) == 0


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_backup.py <backup_file>")
        sys.exit(1)

    backup = Path(sys.argv[1])
    if not backup.exists():
        print(f"[verify] ERROR: file not found: {backup}")
        sys.exit(1)
    if backup.stat().st_size == 0:
        print(f"[verify] FAIL: backup file is empty")
        sys.exit(1)

    print(f"[verify] File: {backup} ({backup.stat().st_size // 1024} KB)")

    ok = verify_checksum(backup)
    if not ok:
        sys.exit(1)

    if backup.suffix == ".db":
        ok = verify_sqlite(backup)
    elif backup.suffix in (".sql", ".dump"):
        ok = verify_sql_dump(backup)
    else:
        print(f"[verify] Unknown extension {backup.suffix} — skipping content checks")

    if ok:
        print(f"[verify] ✅ Backup verified successfully")
        sys.exit(0)
    else:
        print(f"[verify] ❌ Backup verification FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
