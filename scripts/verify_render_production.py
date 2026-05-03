"""
scripts/verify_render_production.py

Verifies Render production after super admin password reset.
Reads SA password from RESET_SUPER_ADMIN_PASSWORD env var.
Does NOT print the password or the token.

Usage:
    RESET_SUPER_ADMIN_PASSWORD="<your_new_password>" \
    python scripts/verify_render_production.py
"""
import os, sys, json

try:
    import requests
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr); sys.exit(1)

BASE = "https://restaurant-saas-1.onrender.com"
SA_EMAIL = "superadmin@platform.com"
SA_PASS  = os.environ.get("RESET_SUPER_ADMIN_PASSWORD", "")

if not SA_PASS:
    print("ERROR: RESET_SUPER_ADMIN_PASSWORD not set", file=sys.stderr); sys.exit(1)

_ok = _fail = 0
def ok(msg):  global _ok;   _ok   += 1; print(f"  ✅ {msg}")
def fail(msg): global _fail; _fail += 1; print(f"  ❌ {msg}")

# ── 1. /health ─────────────────────────────────────────────────────────────
print("\n── /health ──")
r = requests.get(f"{BASE}/health", timeout=30)
d = r.json()
ok("reachable")                                          if r.status_code == 200   else fail(f"status {r.status_code}")
ok(f"db = {d.get('db')}")                               if d.get("db") == "ok"    else fail(f"db = {d.get('db')}")
ok(f"db_backend = {d.get('db_backend')}")               if d.get("db_backend") == "postgresql" else fail(f"db_backend = {d.get('db_backend')}")

# ── 2. Super admin login ────────────────────────────────────────────────────
print("\n── SA login ──")
r2 = requests.post(f"{BASE}/api/super/auth/login",
    json={"email": SA_EMAIL, "password": SA_PASS}, timeout=30)
d2 = r2.json()
token = d2.get("access_token") or d2.get("token", "")
del SA_PASS

if r2.status_code == 200 and token:
    ok("SA login succeeded")
else:
    fail(f"SA login failed — HTTP {r2.status_code}: {d2.get('detail','')}")
    print(f"\n❌ {_fail} failed — cannot continue without SA token"); sys.exit(1)

# ── 3. /api/production-readiness ───────────────────────────────────────────
print("\n── /api/production-readiness ──")
r3 = requests.get(f"{BASE}/api/production-readiness",
    headers={"Authorization": f"Bearer {token}"}, timeout=60)
del token
d3 = r3.json()

status  = d3.get("status")
blockers= d3.get("blockers", [])
checks  = d3.get("checks", {})
ai_s    = checks.get("ai_safety", {})

ok(f"status = {status}")                  if status   == "ready" else fail(f"status = {status}")
ok(f"blockers = []")                      if blockers == []      else fail(f"blockers = {blockers}")
ok("ai_safety.ok = true")                 if ai_s.get("ok")                      else fail("ai_safety.ok = false")
ok("ai_safety.safety_tables_ok = true")   if ai_s.get("safety_tables_ok")        else fail(f"safety_tables_ok = {ai_s.get('safety_tables_ok')}")
ok("ai_safety.learning_switch_col_ok = true") if ai_s.get("learning_switch_col_ok") else fail(f"learning_switch_col_ok = {ai_s.get('learning_switch_col_ok')}")

# ── Spot-check other milestone checks ──────────────────────────────────────
print("\n── Previous milestone checks ──")
for key in ["menu_images", "voice", "analytics", "ai_learning"]:
    if key in checks:
        v = checks[key]
        chk_ok = v.get("ok") if isinstance(v, dict) else bool(v)
        ok(f"{key}.ok = {chk_ok}") if chk_ok else fail(f"{key}.ok = {chk_ok}")
    else:
        print(f"  ⚪ {key} — not in checks (may use different key)")

print(f"\n{'✅ ALL PASSED' if _fail == 0 else '❌ FAILURES: ' + str(_fail)} ({_ok} passed, {_fail} failed)")
sys.exit(0 if _fail == 0 else 1)
