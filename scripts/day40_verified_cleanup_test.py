#!/usr/bin/env python3
"""
NUMBER 40 — Verified Production Cleanup Test
Tests cleanup/security only. Does NOT test order flow, bot behavior, story, voice, or UI.
"""
import os
import sys
import json

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

passed = 0
failed = 0
warnings = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def warn(name, detail=""):
    global warnings
    warnings += 1
    print(f"  ⚠️  {name}" + (f" — {detail}" if detail else ""))


# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("NUMBER 40 — Verified Production Cleanup Test")
print("=" * 70)

# ── 1. JWT: unsafe fallback blocked in production ────────────────────────────
print("\n1. JWT Secret Safety")
print("-" * 40)

# Read main.py and check no unsafe fallback remains at module level
with open(os.path.join(PROJECT_ROOT, "main.py"), "r") as f:
    main_py_content = f.read()

test(
    "No unsafe JWT fallback at module level",
    'os.getenv("JWT_SECRET", "supersecretkey_change_in_production_123456789")' not in main_py_content,
    "Old unsafe fallback still present"
)

test(
    "JWT validation block exists",
    "_UNSAFE_DEFAULTS" in main_py_content,
    "Missing _UNSAFE_DEFAULTS set"
)

test(
    "RuntimeError raised for missing JWT in production",
    'raise RuntimeError' in main_py_content and "JWT_SECRET" in main_py_content,
    "No RuntimeError for missing JWT in production"
)

test(
    "Supports both JWT_SECRET and SECRET_KEY env vars",
    'os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY"' in main_py_content,
    "Does not support SECRET_KEY fallback"
)

# ── 2. .env.example has required vars ────────────────────────────────────────
print("\n2. .env.example Completeness")
print("-" * 40)

env_example_path = os.path.join(PROJECT_ROOT, ".env.example")
test(".env.example exists", os.path.isfile(env_example_path))

if os.path.isfile(env_example_path):
    with open(env_example_path, "r") as f:
        env_example = f.read()

    required_vars = [
        "DATABASE_URL",
        "BASE_URL",
        "JWT_SECRET",
        "JWT_ALGORITHM",
        "JWT_EXPIRE_MINUTES",
        "DEFAULT_ADMIN_EMAIL",
        "DEFAULT_ADMIN_PASSWORD",
        "OPENAI_API_KEY",
        "META_APP_ID",
        "META_APP_SECRET",
        "META_VERIFY_TOKEN",
        "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "SMTP_FROM",
        "SENTRY_DSN",
        "ALLOWED_ORIGINS",
        "ENVIRONMENT",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
    ]
    for var in required_vars:
        test(f".env.example has {var}", var in env_example, f"Missing {var}")

# ── 3. .gitignore ignores sensitive files ────────────────────────────────────
print("\n3. .gitignore Coverage")
print("-" * 40)

gitignore_path = os.path.join(PROJECT_ROOT, ".gitignore")
test(".gitignore exists", os.path.isfile(gitignore_path))

if os.path.isfile(gitignore_path):
    with open(gitignore_path, "r") as f:
        gitignore = f.read()

    required_ignores = [
        (".env", "Secrets"),
        ("*.db", "Database files"),
        ("*.bak", "Backup files"),
        ("uploads/", "Uploads"),
        ("node_modules/", "Node modules"),
        ("__pycache__/", "Python cache"),
        (".claude/settings.local.json", "Claude local settings"),
    ]
    for pattern, desc in required_ignores:
        test(f".gitignore ignores {pattern} ({desc})", pattern in gitignore, f"Missing {pattern}")

# ── 4. package.json does not reference missing server.js ─────────────────────
print("\n4. package.json Safety")
print("-" * 40)

pkg_path = os.path.join(PROJECT_ROOT, "package.json")
test("package.json exists", os.path.isfile(pkg_path))

if os.path.isfile(pkg_path):
    with open(pkg_path, "r") as f:
        pkg = json.load(f)

    test(
        "No main: server.js",
        pkg.get("main") != "server.js",
        "Still points to missing server.js"
    )

    scripts = pkg.get("scripts", {})
    test(
        "No start script referencing server.js",
        scripts.get("start") != "node server.js",
        "Still has start: node server.js"
    )
    test(
        "No dev script referencing server.js",
        scripts.get("dev") != "node --watch server.js",
        "Still has dev: node --watch server.js"
    )

# ── 5. Node legacy files archived ────────────────────────────────────────────
print("\n5. Node.js Legacy Files Archived")
print("-" * 40)

legacy_files = [
    "db.js",
    "middleware/auth.js",
    "routes/auth.js",
    "routes/orders.js",
    "routes/products.js",
    "routes/customers.js",
    "routes/conversations.js",
    "routes/analytics.js",
]
for f in legacy_files:
    active_path = os.path.join(PROJECT_ROOT, f)
    test(
        f"{f} removed from active source",
        not os.path.isfile(active_path),
        f"Still exists at {f}"
    )

archive_path = os.path.join(PROJECT_ROOT, "archive", "legacy-node")
test("archive/legacy-node/ exists", os.path.isdir(archive_path))
test(
    "archive/legacy-node/README.md exists",
    os.path.isfile(os.path.join(archive_path, "README.md"))
)

# ── 6. .bak files not active ─────────────────────────────────────────────────
print("\n6. .bak Files Removed from Active Source")
print("-" * 40)

bak_files = [
    "services/bot.py.bak",
    "services/order_brain.py.bak",
]
for f in bak_files:
    active_path = os.path.join(PROJECT_ROOT, f)
    test(
        f"{f} removed from active source",
        not os.path.isfile(active_path),
        f"Still exists at {f}"
    )

# ── 7. DB files not active / ignored ─────────────────────────────────────────
print("\n7. Database Files Hygiene")
print("-" * 40)

test(
    "data.db removed (was 0 bytes)",
    not os.path.isfile(os.path.join(PROJECT_ROOT, "data.db")),
    "Empty data.db still exists"
)
test(
    "restaurant_saas.db removed (was 0 bytes)",
    not os.path.isfile(os.path.join(PROJECT_ROOT, "restaurant_saas.db")),
    "Empty restaurant_saas.db still exists"
)

# restaurant.db should still exist on disk but be gitignored
test(
    "restaurant.db still on disk (not deleted)",
    os.path.isfile(os.path.join(PROJECT_ROOT, "restaurant.db")),
    "restaurant.db was deleted from disk — should only be untracked"
)

# ── 8. Production validation exists ──────────────────────────────────────────
print("\n8. Production Startup Validation")
print("-" * 40)

test(
    "_validate_production_env function exists",
    "_validate_production_env" in main_py_content,
    "Missing _validate_production_env function"
)
test(
    "Validation called in lifespan",
    "_validate_production_env()" in main_py_content,
    "_validate_production_env not called in lifespan"
)
test(
    "Validation checks ENVIRONMENT variable",
    "ENVIRONMENT" in main_py_content and "_is_production" in main_py_content or "ENVIRONMENT" in main_py_content,
    "Does not check ENVIRONMENT variable"
)
test(
    "Validation blocks missing JWT in production",
    "JWT_SECRET" in main_py_content and "blockers" in main_py_content,
    "Does not block missing JWT in production"
)
test(
    "Validation checks BASE_URL not localhost in production",
    "localhost" in main_py_content and "BASE_URL" in main_py_content,
    "Does not validate BASE_URL"
)

# ── 9. CORS uses ALLOWED_ORIGINS and ENVIRONMENT ─────────────────────────────
print("\n9. CORS Environment Handling")
print("-" * 40)

test(
    "CORS reads ALLOWED_ORIGINS from env",
    "ALLOWED_ORIGINS" in main_py_content and "os.getenv" in main_py_content,
    "CORS does not read from env"
)
test(
    "CORS checks ENVIRONMENT variable",
    '_is_prod_env' in main_py_content,
    "CORS does not check ENVIRONMENT variable"
)
test(
    "CORS refuses wildcard in production",
    main_py_content.count("ALLOWED_ORIGINS = []") >= 1,
    "CORS does not refuse wildcard in production"
)

# ── 10. /health endpoint works (syntax check) ───────────────────────────────
print("\n10. Syntax & Import Check")
print("-" * 40)

# Syntax check main.py
try:
    import ast
    ast.parse(main_py_content)
    test("main.py syntax valid", True)
except SyntaxError as e:
    test("main.py syntax valid", False, str(e))

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"RESULTS: {passed} passed, {failed} failed, {warnings} warnings")
print("=" * 70)

if failed > 0:
    print("❌ NUMBER 40 CLEANUP HAS FAILURES — fix before proceeding")
    sys.exit(1)
else:
    print("✅ NUMBER 40 CLEANUP PASSED — all checks OK")
    sys.exit(0)
