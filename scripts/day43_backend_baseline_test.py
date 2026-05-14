#!/usr/bin/env python3
"""
scripts/day43_backend_baseline_test.py — NUMBER 43: Backend Baseline Tests

Tests current API behavior via TestClient (HTTP-level).
Categories: health, auth-guards, login, register, authenticated access.

Rules: tests only EXISTING behavior — no new features assumed.
"""
from __future__ import annotations
import os, sys, tempfile, uuid as _uuid_mod
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Must set DB_PATH before any database/main import ─────────────────────────
_tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tf.close()
os.environ["DB_PATH"] = _tf.name
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-number43-baseline")

BOLD = "\033[1m"; RED = "\033[31m"; GRN = "\033[32m"; RST = "\033[0m"
_pass = _fail = 0

def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  {GRN}✓{RST} {label}")
    else:
        _fail += 1
        print(f"  {RED}✗{RST} {label}" + (f"  — {detail}" if detail else ""))

print(f"\n{BOLD}{'═'*60}")
print("  NUMBER 43 — Backend Baseline Tests")
print(f"{'═'*60}{RST}\n")

# ── Import app (DB_PATH is already set above) ─────────────────────────────────
from starlette.testclient import TestClient
import main as _main_mod
app = _main_mod.app

# TestClient triggers lifespan (database.init_db) on enter
_client = TestClient(app, raise_server_exceptions=False)
_client.__enter__()


# ── Section 1: Health endpoints (no auth) ────────────────────────────────────
print(f"{BOLD}Section 1 — Health endpoints{RST}")

r = _client.get("/health")
check("GET /health → 200", r.status_code == 200, f"status={r.status_code}")
_hj = r.json() if r.status_code == 200 else {}
check("GET /health has 'status' field", "status" in _hj, f"keys={list(_hj.keys())}")
check("GET /health status is ok or degraded", _hj.get("status") in ("ok", "degraded"))
check("GET /health has 'db' field", "db" in _hj)
check("GET /health has 'version' field", "version" in _hj)

r = _client.get("/api/health")
check("GET /api/health → 200", r.status_code == 200, f"status={r.status_code}")
_ahj = r.json() if r.status_code == 200 else {}
check("GET /api/health has 'status' field", "status" in _ahj)
check("GET /api/health has 'env' dict", isinstance(_ahj.get("env"), dict))
check("GET /api/health has 'version'", _ahj.get("version") == "3.0.0")
check("GET /api/health has 'db_backend'", "db_backend" in _ahj)


# ── Section 2: Auth guards — unauthenticated → 401 ───────────────────────────
print(f"\n{BOLD}Section 2 — Auth guards (no token → 401){RST}")

PROTECTED = [
    ("/api/auth/me",            "GET",  "auth/me"),
    ("/api/products",           "GET",  "products"),
    ("/api/orders",             "GET",  "orders"),
    ("/api/conversations",      "GET",  "conversations"),
    ("/api/channels",           "GET",  "channels"),
    ("/api/analytics/summary",  "GET",  "analytics/summary"),
    ("/api/settings",           "GET",  "settings"),
    ("/api/customers",          "GET",  "customers"),
    ("/api/bot-config",         "GET",  "bot-config"),
    ("/api/staff",              "GET",  "staff"),
]
for path, method, label in PROTECTED:
    r = _client.request(method, path)
    check(
        f"No token → 401 on {label}",
        r.status_code == 401,
        f"got={r.status_code}",
    )


# ── Section 3: Auth — bad login ───────────────────────────────────────────────
print(f"\n{BOLD}Section 3 — Auth: bad credentials{RST}")

r = _client.post("/api/auth/login", json={"email": "nobody@nowhere.com", "password": "wrongpass"})
check("Bad login → 401", r.status_code == 401, f"got={r.status_code}")

r = _client.post("/api/auth/login", json={"email": "nobody@nowhere.com", "password": ""})
check("Empty password → 401 or 422", r.status_code in (401, 422), f"got={r.status_code}")

r = _client.post("/api/auth/login", json={})
check("Missing fields → 422", r.status_code == 422, f"got={r.status_code}")


# ── Section 4: Register → token → authenticated access ───────────────────────
print(f"\n{BOLD}Section 4 — Register and authenticated access{RST}")

_email = f"test_{_uuid_mod.uuid4().hex[:8]}@example.com"
r = _client.post("/api/auth/register", json={
    "restaurant_name": "مطعم الاختبار",
    "owner_name": "مختبر النظام",
    "email": _email,
    "password": "TestPass1234",
})
check("POST /api/auth/register → 200 or 201", r.status_code in (200, 201), f"got={r.status_code} body={r.text[:120]}")
_reg_body = r.json() if r.status_code in (200, 201) else {}
check("Register returns 'token'", "token" in _reg_body, f"keys={list(_reg_body.keys())}")
_token = _reg_body.get("token", "")
_headers = {"Authorization": f"Bearer {_token}"} if _token else {}

check("Register returns 'user' dict", isinstance(_reg_body.get("user"), dict))
check("Register user has 'restaurant_id'", bool(_reg_body.get("user", {}).get("restaurant_id")))

# Duplicate email → 400
r2 = _client.post("/api/auth/register", json={
    "restaurant_name": "مطعم ثاني",
    "owner_name": "آخر",
    "email": _email,
    "password": "TestPass1234",
})
check("Duplicate email → 400", r2.status_code == 400, f"got={r2.status_code}")

# Short password → 400
r3 = _client.post("/api/auth/register", json={
    "restaurant_name": "مطعم ثالث",
    "owner_name": "شخص",
    "email": f"test_{_uuid_mod.uuid4().hex[:8]}@example.com",
    "password": "abc",
})
check("Short password (<6 chars) → 400", r3.status_code == 400, f"got={r3.status_code}")


# ── Section 5: Login with registered user ────────────────────────────────────
print(f"\n{BOLD}Section 5 — Login and /api/auth/me{RST}")

r = _client.post("/api/auth/login", json={"email": _email, "password": "TestPass1234"})
check("Login with valid creds → 200", r.status_code == 200, f"got={r.status_code}")
_login_body = r.json() if r.status_code == 200 else {}
check("Login returns 'token'", "token" in _login_body)
_login_token = _login_body.get("token", _token)
_auth = {"Authorization": f"Bearer {_login_token}"}

r = _client.get("/api/auth/me", headers=_auth)
check("GET /api/auth/me with token → 200", r.status_code == 200, f"got={r.status_code}")
_me = r.json() if r.status_code == 200 else {}
check("/api/auth/me returns email", _me.get("email") == _email, f"email={_me.get('email')}")
check("/api/auth/me returns role", "role" in _me)


# ── Section 6: Authenticated resource endpoints ───────────────────────────────
print(f"\n{BOLD}Section 6 — Authenticated resource endpoints{RST}")

AUTH_GET_200 = [
    ("/api/products",           "products list"),
    ("/api/orders",             "orders list"),
    ("/api/conversations",      "conversations list"),
    ("/api/channels",           "channels list"),
    ("/api/analytics/summary",  "analytics summary"),
    ("/api/settings",           "settings"),
    ("/api/customers",          "customers list"),
    ("/api/bot-config",         "bot-config"),
    ("/api/staff",              "staff list"),
    # /api/subscription/status excluded: requires seeded subscription_plans rows
    # (known risk: 500 on empty DB — RISK-SUB-01)
]
for path, label in AUTH_GET_200:
    r = _client.get(path, headers=_auth)
    check(
        f"Auth GET {label} → 200",
        r.status_code == 200,
        f"got={r.status_code} body={r.text[:80]}",
    )


# ── Section 7: Products CRUD ──────────────────────────────────────────────────
print(f"\n{BOLD}Section 7 — Products CRUD{RST}")

r = _client.post("/api/products", headers=_auth, json={
    "name": "برجر تجريبي",
    "price": 8000,
    "category": "برجر",
    "available": True,
})
check("POST /api/products → 201", r.status_code == 201, f"got={r.status_code} body={r.text[:120]}")
_pid = r.json().get("id") if r.status_code == 201 else None

if _pid:
    r = _client.get(f"/api/products/{_pid}", headers=_auth)
    check("GET /api/products/{id} → 200", r.status_code == 200)
    check("Product has correct name", r.json().get("name") == "برجر تجريبي")

    r = _client.patch(f"/api/products/{_pid}", headers=_auth, json={"price": 9000})
    check("PATCH /api/products/{id} → 200", r.status_code == 200, f"got={r.status_code}")
    check("Updated price = 9000", r.json().get("price") == 9000)

    r = _client.delete(f"/api/products/{_pid}", headers=_auth)
    check("DELETE /api/products/{id} → 200 or 204", r.status_code in (200, 204))

    r = _client.get(f"/api/products/{_pid}", headers=_auth)
    check("GET deleted product → 404", r.status_code == 404)
else:
    for _ in range(4):
        _fail += 1
        print(f"  {RED}✗{RST} (skipped: product create failed)")


# ── Section 8: 404 for unknown resources ─────────────────────────────────────
print(f"\n{BOLD}Section 8 — 404 for unknown resources{RST}")

r = _client.get("/api/products/doesnotexist", headers=_auth)
check("GET unknown product → 404", r.status_code == 404, f"got={r.status_code}")

r = _client.get("/api/orders/doesnotexist", headers=_auth)
check("GET unknown order → 404", r.status_code == 404, f"got={r.status_code}")


# ── Section 9: router extraction smoke test ──────────────────────────────────
print(f"\n{BOLD}Section 9 — Health router extraction (NUMBER 43){RST}")

from routers.health import _env_present as _ep_from_router
check("_env_present importable from routers.health", callable(_ep_from_router))
check("_env_present('PATH') True (PATH is always set)", _ep_from_router("PATH") is True)
check("_env_present('__UNSET_VAR_NUMBER43__') False", _ep_from_router("__UNSET_VAR_NUMBER43__") is False)

# Verify /health and /api/health still work via TestClient after extraction
r = _client.get("/health")
check("/health still reachable after router extraction", r.status_code == 200)
r = _client.get("/api/health")
check("/api/health still reachable after router extraction", r.status_code == 200)


# ── Teardown ──────────────────────────────────────────────────────────────────
try:
    _client.__exit__(None, None, None)
except Exception:
    pass
try:
    os.unlink(_tf.name)
except Exception:
    pass


# ── Summary ───────────────────────────────────────────────────────────────────
total = _pass + _fail
pct   = round(100 * _pass / total) if total else 0
print(f"\n{BOLD}{'═'*60}")
print(f"  Result: {_pass}/{total} passed ({pct}%)")
print(f"{'═'*60}{RST}\n")

if _fail:
    print(f"{RED}FAILED — {_fail} test(s) failed{RST}\n")
    sys.exit(1)
else:
    print(f"{GRN}All tests passed.{RST}\n")
    sys.exit(0)
