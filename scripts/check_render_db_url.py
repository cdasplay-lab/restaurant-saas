"""
scripts/check_render_db_url.py

Diagnostic check for DATABASE_URL before deploying to Render.
Reads the env var, prints safe config info, and exits non-zero on blockers.

NEVER prints password, username, or full URL.

Usage:
    python scripts/check_render_db_url.py
    DATABASE_URL=postgresql://... python scripts/check_render_db_url.py
"""
import os
import re
import sys

DATABASE_URL = os.getenv("DATABASE_URL", "")
ENVIRONMENT  = os.getenv("ENVIRONMENT", "development")

VALID_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}

warnings = []
errors   = []

# ── Parse URL components (safe — never print creds) ──────────────────────────

url = DATABASE_URL

# Detect scheme
if not url:
    backend = "SQLite"
    url_present = False
else:
    url_present = True
    if url.startswith("postgres://"):
        backend = "PostgreSQL (needs postgres:// → postgresql:// fix)"
    elif url.startswith("postgresql://"):
        backend = "PostgreSQL"
    else:
        backend = "Unknown"
        errors.append("DATABASE_URL does not start with postgres:// or postgresql://")

# Extract host (without credentials)
host_present = False
host = ""
if url_present:
    hm = re.search(r'@([^/:@]+)', url)
    if hm:
        host = hm.group(1)
        host_present = True
    else:
        warnings.append("Could not parse host from DATABASE_URL")

# Extract db name
db_name_present = False
if url_present:
    dm = re.search(r'/([^/?]+)(\?|$)', url)
    if dm:
        db_name_present = bool(dm.group(1))

# Internal vs external
looks_internal = None
looks_external = None
if host:
    # Internal Render hosts: no dots (e.g. dpg-d73s1fdactks7384kfug-a)
    looks_internal = '.' not in host
    looks_external = not looks_internal

# sslmode
sslmode = "none"
sslmode_valid = None
if url_present:
    sm = re.search(r'sslmode=([^&\s]+)', url)
    if sm:
        sslmode = sm.group(1)
        sslmode_valid = sslmode in VALID_SSLMODES
    else:
        sslmode = "none"
        sslmode_valid = None  # absence is not an error by itself

# ── Checks ────────────────────────────────────────────────────────────────────

if not url_present and ENVIRONMENT == "production":
    warnings.append("DATABASE_URL is not set — app will use SQLite in production")

if sslmode == "no-verify":
    errors.append("sslmode=no-verify is INVALID — psycopg2 rejects it; use sslmode=require")
elif sslmode_valid is False:
    errors.append(f"sslmode={sslmode!r} is not a valid PostgreSQL sslmode value")

if looks_internal and looks_external is False:
    warnings.append(
        "Internal Render hostname detected — only resolves within the same Render region. "
        "If your web service and database are in different regions, use the External Database URL."
    )

if looks_external and sslmode == "none":
    warnings.append(
        "External hostname but no sslmode set — Render requires sslmode=require for external connections"
    )

if url_present and url.startswith("postgres://"):
    warnings.append("URL uses postgres:// scheme — database.py normalizes this automatically")

# safe_for_render decision
safe_for_render = (
    url_present
    and len(errors) == 0
    and host_present
    and db_name_present
    and sslmode != "no-verify"
    and sslmode_valid is not False
)

# ── Output ────────────────────────────────────────────────────────────────────

print("=" * 55)
print("  DATABASE_URL CHECK")
print("=" * 55)
print(f"  backend            : {backend}")
print(f"  url_present        : {url_present}")
print(f"  host_present       : {host_present}")
print(f"  db_name_present    : {db_name_present}")
print(f"  looks_internal     : {looks_internal}")
print(f"  looks_external     : {looks_external}")
print(f"  sslmode            : {sslmode}")
print(f"  sslmode_valid      : {sslmode_valid if sslmode != 'none' else 'n/a'}")
print(f"  ENVIRONMENT        : {ENVIRONMENT}")
print(f"  safe_for_render    : {safe_for_render}")
print()

if warnings:
    print("  WARNINGS:")
    for w in warnings:
        print(f"    ⚠  {w}")
    print()

if errors:
    print("  ERRORS:")
    for e in errors:
        print(f"    ✗  {e}")
    print()

if not errors and not warnings:
    print("  ✅ No issues detected")
elif not errors:
    print("  🟡 Warnings only — review before deploying")
else:
    print("  ❌ Errors found — fix before deploying")

print("=" * 55)

if errors:
    sys.exit(1)
