#!/usr/bin/env python3
"""
Backup script for SQLite (local) and PostgreSQL (production) databases.

Usage:
    python scripts/backup_database.py [--output-dir ./backups]

On SQLite: copies the .db file with a timestamp suffix.
On PostgreSQL: uses pg_dump to create a plain-SQL dump.

The backup file is written to --output-dir (default: ./backups).
A .sha256 checksum file is written alongside each backup.
"""
import os
import sys
import shutil
import hashlib
import argparse
import subprocess
from datetime import datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_checksum(backup_path: Path) -> Path:
    digest = _sha256(backup_path)
    chk = backup_path.with_suffix(backup_path.suffix + ".sha256")
    chk.write_text(f"{digest}  {backup_path.name}\n")
    return chk


def backup_sqlite(db_path: str, output_dir: Path) -> Path:
    src = Path(db_path)
    if not src.exists():
        print(f"[backup] ERROR: SQLite file not found: {src}")
        sys.exit(1)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = output_dir / f"restaurant_{ts}.db"
    shutil.copy2(src, dest)
    chk = _write_checksum(dest)
    size_kb = dest.stat().st_size // 1024
    print(f"[backup] SQLite backup OK → {dest} ({size_kb} KB)")
    print(f"[backup] Checksum        → {chk}")
    return dest


def backup_postgres(database_url: str, output_dir: Path) -> Path:
    if not shutil.which("pg_dump"):
        print("[backup] ERROR: pg_dump not found — install postgresql-client")
        sys.exit(1)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = output_dir / f"restaurant_{ts}.sql"
    env = os.environ.copy()
    env["PGPASSWORD"] = ""  # pg_dump reads password from the URL directly
    try:
        with open(dest, "w") as f:
            subprocess.run(
                ["pg_dump", "--no-password", "--format=plain", database_url],
                stdout=f,
                stderr=subprocess.PIPE,
                env=env,
                check=True,
            )
    except subprocess.CalledProcessError as e:
        print(f"[backup] ERROR: pg_dump failed: {e.stderr.decode()[:500]}")
        sys.exit(1)
    chk = _write_checksum(dest)
    size_kb = dest.stat().st_size // 1024
    print(f"[backup] PostgreSQL backup OK → {dest} ({size_kb} KB)")
    print(f"[backup] Checksum           → {chk}")
    return dest


def main():
    parser = argparse.ArgumentParser(description="Database backup utility")
    parser.add_argument("--output-dir", default="./backups", help="Directory to write backups")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    database_url = os.getenv("DATABASE_URL", "")
    db_path = os.getenv("DB_PATH", "restaurant.db")

    if database_url:
        print(f"[backup] Mode: PostgreSQL (DATABASE_URL set)")
        backup_postgres(database_url, output_dir)
    else:
        print(f"[backup] Mode: SQLite ({db_path})")
        backup_sqlite(db_path, output_dir)

    print("[backup] Done.")


if __name__ == "__main__":
    main()
