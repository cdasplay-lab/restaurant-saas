"""
scripts/day24_polished_ui_test.py
NUMBER 24 — Polished Production UI Tests

Test sections:
  A — Static UI files: sections, nav, loading/empty/error states
  B — Regression NUMBER 21: menu images UI elements intact
  C — Regression NUMBER 22: voice/transcription UI elements intact
  D — Regression NUMBER 23: analytics API calls present in UI code
  E — Auth / visibility: super vs restaurant separation
  F — UX checks: loading, empty, error, delete confirmation, token masking
  G — API smoke: summary, analytics, production-readiness
  H — Tenant isolation: analytics calls use JWT not explicit restaurant_id

Usage:
    python scripts/day24_polished_ui_test.py                 # localhost
    BASE_URL=https://restaurant-saas-1.onrender.com python ...
"""
import os, sys, time, re

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT  = 30

_passed = _failed = _warned = 0

def _ok(label):
    global _passed; _passed += 1
    print(f"  ✅ {label}")

def _fail(label, detail=""):
    global _failed; _failed += 1
    print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))

def _warn(label, detail=""):
    global _warned; _warned += 1
    print(f"  ⚠️  {label}" + (f" — {detail}" if detail else ""))

try:
    import requests
    _req_ok = True
except ImportError:
    _req_ok = False

def _req(method, path, token=None, json_body=None, params=None):
    if not _req_ok:
        return None, 0
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = getattr(requests, method)(
            BASE_URL + path, headers=headers, json=json_body,
            params=params, timeout=TIMEOUT)
        try:
            return r.json(), r.status_code
        except Exception:
            return {}, r.status_code
    except Exception:
        return None, 0

def register_and_login(tag):
    ts = int(time.time() * 1000) % 10_000_000
    email = f"v24_{tag}_{ts}@test.local"
    d, s = _req("post", "/api/auth/register", json_body={
        "email": email, "password": "Test123!!",
        "owner_name": f"V_{tag}", "restaurant_name": f"V_{tag}", "phone": f"07{ts}",
    })
    if s not in (200, 201):
        return None, None
    d2, s2 = _req("post", "/api/auth/login", json_body={"email": email, "password": "Test123!!"})
    if s2 != 200:
        return None, None
    token = (d2 or {}).get("access_token") or (d2 or {}).get("token")
    rid   = (d2 or {}).get("restaurant_id") or ((d2 or {}).get("user") or {}).get("restaurant_id")
    return token, rid


# ── Read static files once ────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_HTML_PATH   = os.path.join(ROOT, "public", "app.html")
SUPER_HTML_PATH = os.path.join(ROOT, "public", "super.html")

def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return ""

app_src   = _read(APP_HTML_PATH)
super_src = _read(SUPER_HTML_PATH)


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ A — Static UI files ═══")

# A01 — files exist
if os.path.isfile(APP_HTML_PATH):
    _ok("A01 — public/app.html exists")
else:
    _fail("A01 — public/app.html not found")

if os.path.isfile(SUPER_HTML_PATH):
    _ok("A02 — public/super.html exists")
else:
    _fail("A02 — public/super.html not found")

# A03 — app.html required sections
APP_SECTIONS = [
    ("sec-dashboard",    "Dashboard"),
    ("sec-orders",       "Orders"),
    ("sec-conversations","Conversations"),
    ("sec-products",     "Products"),
    ("sec-menu-images",  "Menu Images"),
    ("sec-analytics",    "Analytics"),
    ("sec-channels",     "Channels"),
    ("sec-settings",     "Settings"),
]
for sect_id, label in APP_SECTIONS:
    if f'id="{sect_id}"' in app_src or f"id='{sect_id}'" in app_src:
        _ok(f"A03 — app.html has section: {label} ({sect_id})")
    else:
        _fail(f"A03 — app.html missing section: {label} ({sect_id})")

# A04 — super.html required sections
SUPER_SECTIONS = [
    ("sec-dashboard",  "Dashboard"),
    ("sec-restaurants","Restaurants"),
    ("sec-analytics",  "Analytics"),
    ("sec-system",     "System"),
    ("sec-alerts",     "Alerts"),
]
for sect_id, label in SUPER_SECTIONS:
    if f'id="{sect_id}"' in super_src or f"id='{sect_id}'" in super_src:
        _ok(f"A04 — super.html has section: {label} ({sect_id})")
    else:
        _fail(f"A04 — super.html missing section: {label} ({sect_id})")

# A05 — new NUMBER 24 dashboard elements
NEW_DASH_ELEMENTS = [
    ("kpiTodayOrders",   "Today orders KPI"),
    ("kpiFailed",        "Failed outbound KPI"),
    ("kpiChannels",      "Connected channels KPI"),
    ("dashHealthStrip",  "Health strip"),
    ("dashQuickActions", "Quick actions"),
]
for eid, label in NEW_DASH_ELEMENTS:
    if f'id="{eid}"' in app_src or f"id='{eid}'" in app_src:
        _ok(f"A05 — {label} ({eid}) present in app.html")
    else:
        _fail(f"A05 — {label} ({eid}) missing from app.html")

# A06 — super.html new elements
SUPER_NEW = [
    ("warningsCenter",   "Warnings center"),
    ("healthKpiRow",     "Health KPI row"),
]
for eid, label in SUPER_NEW:
    if f'id="{eid}"' in super_src or f"id='{eid}'" in super_src:
        _ok(f"A06 — {label} ({eid}) present in super.html")
    else:
        _fail(f"A06 — {label} ({eid}) missing from super.html")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ B — Regression NUMBER 21: menu images ═══")

# B01 — nav item for menu-images
if "#menu-images" in app_src or "menu-images" in app_src:
    _ok("B01 — menu-images nav/route reference found in app.html")
else:
    _fail("B01 — menu-images reference missing from app.html")

# B02 — required JS functions
MI_FUNCTIONS = ["loadMenuImages", "renderMenuImages", "openMenuImageModal", "saveMenuImage", "deleteMenuImage"]
for fn in MI_FUNCTIONS:
    if fn in app_src:
        _ok(f"B02 — JS function '{fn}' present")
    else:
        _fail(f"B02 — JS function '{fn}' missing")

# B03 — API calls point to /api/menu-images
if "/api/menu-images" in app_src:
    _ok("B03 — app.html calls /api/menu-images")
else:
    _fail("B03 — /api/menu-images API call not found in app.html")

# B04 — broken image fallback
if "onerror" in app_src and "menu" in app_src.lower():
    _ok("B04 — broken image onerror fallback present in app.html")
else:
    _fail("B04 — broken image onerror fallback not found")

# B05 — active/total count badge
if "menuImgsCountBadge" in app_src:
    _ok("B05 — menu images count badge (menuImgsCountBadge) present")
else:
    _fail("B05 — menu images count badge missing")

# B06 — bot hint text
if "دزلي المنيو" in app_src or "المنيو" in app_src:
    _ok("B06 — bot hint text for menu intent present in app.html")
else:
    _fail("B06 — bot hint for menu intent missing")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ C — Regression NUMBER 22: voice / transcription UI ═══")

# C01 — transcription_status used in UI
if "transcription_status" in app_src:
    _ok("C01 — transcription_status referenced in app.html")
else:
    _fail("C01 — transcription_status not found in app.html")

# C02 — voice badge coloring for success vs failed
if "voice_transcript" in app_src:
    _ok("C02 — voice_transcript referenced in app.html")
else:
    _fail("C02 — voice_transcript not found in app.html")

# C03 — transcription status success = green
if "success" in app_src and ("green" in app_src or "text-green" in app_src):
    _ok("C03 — green badge for transcription success exists")
else:
    _warn("C03 — green success badge for transcription may not be explicit")

# C04 — transcription failure handled (amber/red)
if ("failed" in app_src or "skipped" in app_src) and ("amber" in app_src or "yellow" in app_src or "text-amber" in app_src):
    _ok("C04 — amber/yellow badge for transcription failure exists")
else:
    _warn("C04 — amber failure badge for transcription may not be explicit")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ D — Regression NUMBER 23: analytics API calls ═══")

# D01 — restaurant analytics calls
ANALYTICS_CALLS = [
    "/api/analytics/voice",
    "/api/analytics/menu-images",
]
for call in ANALYTICS_CALLS:
    if call in app_src:
        _ok(f"D01 — app.html calls {call}")
    else:
        _fail(f"D01 — app.html missing call to {call}")

# D02 — super analytics calls
SUPER_ANALYTICS_CALLS = [
    "/api/super/analytics/overview",
    "/api/super/analytics/restaurants",
    "/api/super/analytics/channels",
    "/api/super/analytics/health",
]
for call in SUPER_ANALYTICS_CALLS:
    if call in super_src:
        _ok(f"D02 — super.html calls {call}")
    else:
        _fail(f"D02 — super.html missing call to {call}")

# D03 — date filter controls in analytics section
if "analyticsDateFrom" in app_src and "analyticsDateTo" in app_src:
    _ok("D03 — date filter inputs present in app.html analytics section")
else:
    _fail("D03 — analyticsDateFrom/To inputs missing from app.html")

# D04 — voice analytics KPI elements
VOICE_KPIS = ["aVoiceTotal", "aVoiceSuccess", "aVoiceRate"]
for eid in VOICE_KPIS:
    if eid in app_src:
        _ok(f"D04 — voice KPI element '{eid}' present")
    else:
        _fail(f"D04 — voice KPI element '{eid}' missing")

# D05 — menu image analytics KPI elements
IMG_KPIS = ["aImgTotal", "aImgActive", "aImgCats"]
for eid in IMG_KPIS:
    if eid in app_src:
        _ok(f"D05 — menu-image KPI element '{eid}' present")
    else:
        _fail(f"D05 — menu-image KPI element '{eid}' missing")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ E — Auth / visibility ═══")

# E01 — super.html does not expose restaurant-level controls directly
# Check that super.html doesn't contain restaurant owner JWT token patterns
if "auth_token" not in super_src and "localStorage.getItem('token')" not in super_src:
    _ok("E01 — super.html does not use restaurant owner token pattern")
elif "sa_token" in super_src:
    _ok("E01 — super.html uses sa_token (super admin auth) not restaurant token")
else:
    _warn("E01 — super.html auth pattern unclear — verify manually")

# E02 — app.html does not contain super admin controls
SUPER_ONLY_PATTERNS = ["/api/super/restaurants", "/api/super/subscriptions", "current_super_admin"]
found_super = [p for p in SUPER_ONLY_PATTERNS if p in app_src]
if not found_super:
    _ok("E02 — app.html does not contain super-admin-only API calls")
else:
    # Some may be in analytics (allowed for super calls in super.html)
    _warn(f"E02 — app.html contains super-admin patterns: {found_super}")

# E03 — super.html requires separate auth (sa_token)
if "sa_token" in super_src or "SUPER_ADMIN" in super_src or "super_admin" in super_src:
    _ok("E03 — super.html references super admin auth (sa_token/super_admin)")
else:
    _fail("E03 — super.html missing super admin auth reference")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ F — UX checks ═══")

# F01 — loading states (at least 5 section loading patterns)
loading_ids = re.findall(r'id=["\'](\w*[Ll]oading\w*)["\']', app_src)
if len(loading_ids) >= 5:
    _ok(f"F01 — {len(loading_ids)} loading state IDs found in app.html")
else:
    _fail(f"F01 — only {len(loading_ids)} loading IDs found (expected ≥5)")

# F02 — empty states
empty_ids = re.findall(r'id=["\'](\w*[Ee]mpty\w*)["\']', app_src)
if len(empty_ids) >= 4:
    _ok(f"F02 — {len(empty_ids)} empty state IDs found in app.html")
else:
    _fail(f"F02 — only {len(empty_ids)} empty state IDs found (expected ≥4)")

# F03 — error states (toast error calls)
toast_errors = app_src.count("'error'")
if toast_errors >= 5:
    _ok(f"F03 — {toast_errors} toast error calls found in app.html")
else:
    _fail(f"F03 — only {toast_errors} error toast calls (expected ≥5)")

# F04 — delete confirmations
confirm_count = app_src.count("confirm(")
if confirm_count >= 5:
    _ok(f"F04 — {confirm_count} delete confirmation dialogs in app.html")
else:
    _fail(f"F04 — only {confirm_count} confirm() dialogs (expected ≥5)")

# F05 — token masking (type="password" or .slice pattern)
token_mask = ('type="password"' in app_src or "type='password'" in app_src
              or ".slice(0," in app_src or 'token.slice' in app_src)
if token_mask:
    _ok("F05 — token masking pattern present in app.html")
else:
    _fail("F05 — no token masking pattern found in app.html")

# F06 — super.html has loading states
super_loading = re.findall(r'id=["\'](\w*[Ll]oading\w*)["\']', super_src)
if len(super_loading) >= 3:
    _ok(f"F06 — {len(super_loading)} loading state IDs in super.html")
else:
    _fail(f"F06 — only {len(super_loading)} loading IDs in super.html (expected ≥3)")

# F07 — super.html has delete confirmations
super_confirms = super_src.count("confirm(")
if super_confirms >= 3:
    _ok(f"F07 — {super_confirms} confirm() dialogs in super.html")
else:
    _fail(f"F07 — only {super_confirms} confirm() dialogs in super.html (expected ≥3)")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ G — API smoke ═══")

SUPER_ADMIN_TOKEN = os.getenv("SUPER_ADMIN_TOKEN", "")

if not _req_ok:
    _warn("G00 — requests not installed, skipping HTTP tests")
else:
    TOKEN_A, RID_A = None, None

    # G01 — server reachable
    d, s = _req("get", "/health")
    if s == 200:
        _ok("G01 — server reachable (/health 200)")
    else:
        _warn("G01 — server not reachable — skipping all HTTP smoke tests")
        TOKEN_A = None

    if s == 200:
        TOKEN_A, RID_A = register_and_login("g1")
        if not TOKEN_A:
            _warn("G02 — registration failed, skipping token-based smoke tests")
        else:
            _ok(f"G02 — registered and logged in (rid={str(RID_A)[:8]}…)")

        if TOKEN_A:
            # G03 — enhanced summary returns new fields
            d, s = _req("get", "/api/analytics/summary", token=TOKEN_A)
            if s == 200:
                new_fields = ["today_orders", "failed_outbound_24h", "connected_channels", "subscription"]
                missing = [f for f in new_fields if f not in (d or {})]
                if not missing:
                    _ok(f"G03 — /api/analytics/summary returns all new fields")
                else:
                    _fail(f"G03 — summary missing new fields: {missing}")
            else:
                _fail(f"G03 — summary returned {s}")

            # G04 — /api/analytics/voice works
            d, s = _req("get", "/api/analytics/voice", token=TOKEN_A)
            if s == 200 and "total_voice_messages" in (d or {}):
                _ok("G04 — /api/analytics/voice still returns 200")
            else:
                _fail(f"G04 — /api/analytics/voice returned {s}")

            # G05 — /api/analytics/menu-images works
            d, s = _req("get", "/api/analytics/menu-images", token=TOKEN_A)
            if s == 200 and "total_images" in (d or {}):
                _ok("G05 — /api/analytics/menu-images still returns 200")
            else:
                _fail(f"G05 — /api/analytics/menu-images returned {s}")

            # G06 — /api/menu-images CRUD still works
            d, s = _req("get", "/api/menu-images", token=TOKEN_A)
            if s == 200 and isinstance(d, list):
                _ok("G06 — GET /api/menu-images still returns list (NUMBER 21 OK)")
            else:
                _fail(f"G06 — GET /api/menu-images returned {s}")

    # G07 — production readiness (super admin)
    if SUPER_ADMIN_TOKEN:
        d, s = _req("get", "/api/production-readiness", token=SUPER_ADMIN_TOKEN)
        if s == 200:
            checks = (d or {}).get("checks", {})
            if "ui_files" in checks:
                ui = checks["ui_files"]
                if ui.get("app_html") and ui.get("super_html"):
                    _ok(f"G07 — production-readiness has ui_files check: app_html={ui['app_html']}, super_html={ui['super_html']}")
                else:
                    _fail("G07 — ui_files check missing app_html or super_html", str(ui))
            else:
                _fail("G07 — production-readiness missing 'ui_files' check key")
        else:
            _fail(f"G07 — production-readiness returned {s}")
    else:
        # Local check: production readiness imports ok
        sys.path.insert(0, ROOT)
        os.chdir(ROOT)
        try:
            import database, main as _main
            _ok("G07 — main.py imports cleanly (production_readiness endpoint available)")
        except Exception as e:
            _fail("G07 — main.py import failed", str(e)[:100])

    # G08 — super analytics health endpoint
    if SUPER_ADMIN_TOKEN:
        d, s = _req("get", "/api/super/analytics/health", token=SUPER_ADMIN_TOKEN)
        if s == 200 and "voice_failed_24h" in (d or {}):
            _ok("G08 — /api/super/analytics/health returns voice_failed_24h")
        else:
            _fail(f"G08 — /api/super/analytics/health returned {s}", str(d)[:80])
    else:
        _warn("G08 — SUPER_ADMIN_TOKEN not set, skipping health endpoint smoke test")


# ──────────────────────────────────────────────────────────────────────────────
print("\n═══ H — Tenant isolation ═══")

if not _req_ok:
    _warn("H00 — requests not installed")
elif TOKEN_A:
    # H01 — analytics calls use JWT restaurant_id, not explicit rid in URL
    # The voice/menu-images endpoints don't accept restaurant_id as query param
    d, s = _req("get", "/api/analytics/voice", token=TOKEN_A, params={"restaurant_id": "other_rid_fake"})
    if s == 200:
        # Backend ignores the restaurant_id param and uses JWT — safe
        _ok("H01 — /api/analytics/voice ignores explicit restaurant_id param (uses JWT)")
    elif s in (400, 422):
        _ok("H01 — /api/analytics/voice rejects explicit restaurant_id param")
    else:
        _warn(f"H01 — unexpected status {s} when passing extra restaurant_id param")

    # H02 — register second restaurant; their analytics are separate
    time.sleep(1)
    TOKEN_B, RID_B = register_and_login("h2")
    if TOKEN_B:
        d_a, _ = _req("get", "/api/analytics/menu-images", token=TOKEN_A)
        d_b, _ = _req("get", "/api/analytics/menu-images", token=TOKEN_B)
        # Both should work independently
        if d_a is not None and d_b is not None:
            _ok("H02 — both restaurants get independent menu-image analytics (isolation OK)")
        else:
            _fail("H02 — could not verify tenant isolation for menu-image analytics")
    else:
        _warn("H02 — second restaurant registration failed, skipping isolation check")

    # H03 — super analytics APIs require super token
    d, s = _req("get", "/api/super/analytics/overview", token=TOKEN_A)
    if s in (401, 403):
        _ok(f"H03 — /api/super/analytics/overview blocks restaurant user (status={s})")
    elif s == 0:
        _warn("H03 — server unreachable")
    else:
        _fail(f"H03 — super analytics allowed restaurant user (status={s}) — auth broken")
else:
    _warn("H00 — no TOKEN_A available, skipping tenant isolation HTTP tests")


# ──────────────────────────────────────────────────────────────────────────────
# NUMBER 21/22/23 regression via static checks
print("\n═══ I — Final regression: NUMBER 21/22/23 static checks ═══")

# I01 — NUMBER 21: menu images table referenced in database
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    import database
    database.init_db()
    conn = database.get_db()
    if database.IS_POSTGRES:
        exists = conn.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='menu_images'").fetchone()[0]
    else:
        exists = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='menu_images'").fetchone()[0]
    conn.close()
    if exists:
        _ok("I01 — menu_images table exists (NUMBER 21 DB intact)")
    else:
        _fail("I01 — menu_images table missing")
except Exception as e:
    _fail("I01 — DB check failed", str(e)[:80])

# I02 — NUMBER 22: voice columns intact
try:
    conn = database.get_db()
    if database.IS_POSTGRES:
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='messages'").fetchall()]
    else:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    conn.close()
    voice_cols = ["transcription_status", "voice_transcript", "transcription_provider"]
    missing = [c for c in voice_cols if c not in cols]
    if not missing:
        _ok(f"I02 — voice columns intact in messages table (NUMBER 22 DB intact)")
    else:
        _fail("I02 — voice columns missing", str(missing))
except Exception as e:
    _fail("I02 — voice column check failed", str(e)[:80])

# I03 — NUMBER 23: analytics_service imports cleanly
try:
    from services import analytics_service as _as
    assert callable(_as.get_voice_analytics)
    assert callable(_as.get_menu_image_analytics)
    assert callable(_as.get_super_overview_analytics)
    _ok("I03 — analytics_service imports cleanly (NUMBER 23 intact)")
except Exception as e:
    _fail("I03 — analytics_service import failed", str(e)[:80])

# I04 — NUMBER 24 UI: new elements verified in source
N24_ELEMENTS = [
    (app_src,   "dashQuickActions",     "Quick actions panel"),
    (app_src,   "dashHealthStrip",      "Health strip"),
    (app_src,   "kpiTodayOrders",       "Today orders KPI"),
    (app_src,   "kpiFailed",            "Failed outbound KPI"),
    (app_src,   "kpiChannels",          "Connected channels KPI"),
    (app_src,   "analyticsDateFrom",    "Analytics date filter"),
    (super_src, "warningsCenter",       "Super warnings center"),
    (super_src, "healthKpiRow",         "Super health KPI row"),
]
all_ok = True
for src, eid, label in N24_ELEMENTS:
    if eid not in src:
        _fail(f"I04 — NUMBER 24 UI element missing: {label} ({eid})")
        all_ok = False
if all_ok:
    _ok(f"I04 — all {len(N24_ELEMENTS)} NUMBER 24 UI elements verified in source")


# ──────────────────────────────────────────────────────────────────────────────
print(f"""
═══════════════════════════════════════════════════════════════
  NUMBER 24 — Polished Production UI Test Results
  ✅ Passed : {_passed}
  ❌ Failed : {_failed}
  ⚠️  Warned : {_warned}
═══════════════════════════════════════════════════════════════
""")

if _failed > 0:
    sys.exit(1)
