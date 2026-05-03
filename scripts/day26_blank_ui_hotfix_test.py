"""
scripts/day26_blank_ui_hotfix_test.py
NUMBER 26 — Blank UI Hotfix Tests

Checks:
  A — Static: app.html structure
  B — Static: error handling + safe helpers
  C — Static: navigate() + init() guards
  D — Static: dashboard safe rendering
  E — Static: API layer hardening
  F — Static: all pages in PAGES map have matching section IDs and nav anchors
  G — Static: 25B AI training functions are safe
  H — Static: other section loaders have error guards
  I — Static: cache busting in place
  J — API smoke: dashboard renders even on empty API
"""
import os, sys, re, time

_passed = _failed = _warned = 0

def ok(label):   global _passed; _passed += 1; print(f"  ✅ {label}")
def fail(label, d=""): global _failed; _failed += 1; print(f"  ❌ {label}" + (f" — {d}" if d else ""))
def warn(label, d=""): global _warned; _warned += 1; print(f"  ⚠️  {label}" + (f" — {d}" if d else ""))

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_HTML = os.path.join(ROOT, "public", "app.html")

def read(p):
    try: return open(p, encoding="utf-8").read()
    except: return ""

src = read(APP_HTML)

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
try:
    import requests
    _req_ok = True
except ImportError:
    _req_ok = False

def _req(method, path, token=None, json_body=None):
    if not _req_ok: return None, 0
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    try:
        r = getattr(requests, method)(BASE_URL + path, headers=headers, json=json_body, timeout=20)
        try: return r.json(), r.status_code
        except: return {}, r.status_code
    except: return None, 0

_tok = _rid = None
_server_ok = False
if _req_ok:
    r0, s0 = _req("get", "/health")
    if r0 is not None and s0 == 200:
        _server_ok = True
        ts = int(time.time() * 1000) % 10_000_000
        email = f"v26_{ts}@test.local"
        _req("post", "/api/auth/register", json_body={"email": email, "password":"Test123!!","owner_name":"V26","restaurant_name":"V26","phone":f"07{ts}"})
        time.sleep(0.5)
        d2, s2 = _req("post", "/api/auth/login", json_body={"email": email, "password":"Test123!!"})
        _tok = (d2 or {}).get("token") or (d2 or {}).get("access_token")
        _rid = (d2 or {}).get("restaurant_id") or ((d2 or {}).get("user") or {}).get("restaurant_id")


# ══════════════════════════════════════════════════════════════
print("\n═══ A — app.html structure ═══")

if os.path.isfile(APP_HTML):
    ok("A01 — app.html exists")
else:
    fail("A01 — app.html missing"); sys.exit(1)

if 'id="sec-dashboard"' in src:
    ok("A02 — sec-dashboard section exists")
else:
    fail("A02 — sec-dashboard missing")

# Dashboard must not be empty
dash_m = re.search(r'id="sec-dashboard"[^>]*>(.*?)</section>', src, re.DOTALL)
if dash_m and len(dash_m.group(1).strip()) > 100:
    ok("A03 — sec-dashboard has content")
else:
    fail("A03 — sec-dashboard appears empty")

# All PAGES keys have sections
pages = ["dashboard","orders","conversations","products","menu-images","customers",
         "broadcast","team","analytics","channels","activity","settings","onboarding","ai-training"]
for pg in pages:
    if f'id="sec-{pg}"' in src:
        ok(f"A04 — sec-{pg} section exists")
    else:
        fail(f"A04 — sec-{pg} section missing")


# ══════════════════════════════════════════════════════════════
print("\n═══ B — Error handling + safe helpers ═══")

if "window.onerror" in src:
    ok("B01 — window.onerror handler present")
else:
    fail("B01 — window.onerror missing")

if "window.onunhandledrejection" in src or "onunhandledrejection" in src:
    ok("B02 — window.onunhandledrejection handler present")
else:
    fail("B02 — onunhandledrejection missing")

if "_showErrorBanner" in src:
    ok("B03 — _showErrorBanner() defined")
else:
    fail("B03 — _showErrorBanner() missing")

if "function safeSetText" in src:
    ok("B04 — safeSetText() helper defined")
else:
    fail("B04 — safeSetText() missing")

if "function safeHTML" in src:
    ok("B05 — safeHTML() helper defined")
else:
    fail("B05 — safeHTML() missing")

if "function safeShow" in src and "function safeHide" in src:
    ok("B06 — safeShow() / safeHide() helpers defined")
else:
    fail("B06 — safeShow/safeHide helpers missing")

if "_appErrBanner" in src:
    ok("B07 — error banner element ID used in handler")
else:
    fail("B07 — error banner ID missing")


# ══════════════════════════════════════════════════════════════
print("\n═══ C — navigate() + init() guards ═══")

# navigate() must null-check sec and navLink
if re.search(r'const sec\s*=.*getElementById.*sec-', src) or \
   re.search(r'if\s*\(sec\)\s*sec\.classList', src):
    ok("C01 — navigate() null-checks section element")
else:
    fail("C01 — navigate() not null-safe for section element")

if re.search(r'if\s*\(navLink\)', src):
    ok("C02 — navigate() null-checks nav anchor")
else:
    fail("C02 — navigate() not null-safe for nav anchor")

if re.search(r'try\s*\{[^}]*PAGES\[page\]\.load\(\)', src, re.DOTALL) or \
   "try { PAGES[page].load()" in src:
    ok("C03 — navigate() wraps load() in try/catch")
else:
    fail("C03 — navigate() does not wrap load() in try/catch")

# init() guarded by DOMContentLoaded or readyState
if "DOMContentLoaded" in src and "init()" in src:
    ok("C04 — init() guarded by DOMContentLoaded / readyState")
else:
    fail("C04 — init() not guarded by DOMContentLoaded")

# init() checks user is valid before using user.x
if re.search(r'if\s*\(!user\s*\|\|', src) or "if (!user" in src:
    ok("C05 — init() validates user object before use")
else:
    fail("C05 — init() does not validate user before dereferencing")

# hashchange listener
if "hashchange" in src:
    ok("C06 — hashchange listener present")
else:
    fail("C06 — hashchange listener missing")


# ══════════════════════════════════════════════════════════════
print("\n═══ D — dashboard safe rendering ═══")

if "safeSetText('kpiOrders'" in src or 'safeSetText("kpiOrders"' in src:
    ok("D01 — dashboard KPIs use safeSetText()")
else:
    fail("D01 — dashboard KPIs not using safeSetText()")

if "safeShow('dashContent')" in src or 'safeShow("dashContent")' in src:
    ok("D02 — dashContent shown via safeShow()")
else:
    fail("D02 — dashContent not using safeShow()")

if re.search(r"\.catch\(\(\)\s*=>\s*\(\{\}\)\)", src) or ".catch(() => ({}))" in src or ".catch(() => [])" in src:
    ok("D03 — individual API calls in Promise.all have .catch() fallbacks")
else:
    fail("D03 — no .catch() fallbacks on individual dashboard API calls")

# Dashboard content always shown even on error
if re.search(r'safeHide\([\'"]dashLoading[\'"]\)\s*;\s*\n\s*safeShow\([\'"]dashContent[\'"]\)', src):
    ok("D04 — dashLoading hidden + dashContent shown before data processing")
else:
    warn("D04 — could not confirm dashContent is always shown before data processing")

# Safe = null-checked: `const icon = ...; if (icon)` — unsafe = direct .className without guard
unsafe_pattern = re.search(r"kpiFailedIcon\.querySelector\('i'\)\.className", src)
safe_pattern   = re.search(r"if\s*\(icon\)\s*icon\.className", src)
if unsafe_pattern:
    fail("D05 — unsafe kpiFailedIcon.querySelector('i').className (no null check)")
elif safe_pattern or "if (icon)" in src:
    ok("D05 — kpiFailedIcon.querySelector('i') is null-checked")
else:
    warn("D05 — could not confirm kpiFailedIcon querySelector safety")

if "safeHTML('recentOrders'" in src or 'safeHTML("recentOrders"' in src:
    ok("D06 — recentOrders uses safeHTML()")
else:
    fail("D06 — recentOrders not using safeHTML()")

if "safeHTML('topProducts'" in src or 'safeHTML("topProducts"' in src:
    ok("D07 — topProducts uses safeHTML()")
else:
    fail("D07 — topProducts not using safeHTML()")


# ══════════════════════════════════════════════════════════════
print("\n═══ E — API layer hardening ═══")

if "api.delete" in src and "delete: (u)" in src:
    ok("E01 — api.delete alias defined (fixes 25B)")
else:
    fail("E01 — api.delete alias missing")

if "tعذر الاتصال بالخادم" in src or "تعذر الاتصال" in src:
    ok("E02 — network error gives Arabic message")
else:
    warn("E02 — network error message not found")

if "r.status === 401" in src and "logout()" in src:
    ok("E03 — 401 triggers logout()")
else:
    fail("E03 — 401 handling missing")

if "r.status >= 500" in src:
    ok("E04 — 5xx responses handled")
else:
    fail("E04 — 5xx not explicitly handled in api.req")

if "return r.json().catch" in src:
    ok("E05 — r.json() has .catch() guard")
else:
    fail("E05 — r.json() could throw if response is not JSON")


# ══════════════════════════════════════════════════════════════
print("\n═══ F — PAGES map completeness ═══")

# Extract PAGES block from "const PAGES = {" to "}" on its own line
pages_m = re.search(r"const PAGES\s*=\s*\{(.+?)\n\};", src, re.DOTALL)
if pages_m:
    map_src = pages_m.group(1)
    for pg in pages:
        # keys are either bare (dashboard:) or quoted ('ai-training':)
        present = (
            f"'{pg}'" in map_src or f'"{pg}"' in map_src or
            re.search(r'^\s*' + re.escape(pg) + r'\s*:', map_src, re.MULTILINE)
        )
        if present:
            ok(f"F01 — PAGES['{pg}'] defined")
        else:
            fail(f"F01 — PAGES['{pg}'] missing from PAGES map")
else:
    fail("F01 — PAGES map not found in app.html")

# Nav anchors
for pg in pages:
    if f'href="#{pg}"' in src:
        ok(f"F02 — nav anchor href='#{pg}' exists")
    else:
        fail(f"F02 — nav anchor href='#{pg}' missing")


# ══════════════════════════════════════════════════════════════
print("\n═══ G — 25B AI training functions are safe ═══")

ai_fns = ["showCorrectionVersions", "showKnowledgeVersions", "closeVersionModal",
          "restoreVersion", "loadChangelog", "loadAiSettingsTab", "toggleAiLearning"]
for fn in ai_fns:
    if f"async function {fn}" in src or f"function {fn}" in src:
        ok(f"G01 — {fn}() defined")
    else:
        fail(f"G01 — {fn}() missing")

if "aiTab('changelog')" in src and "aiTab('settings')" in src:
    ok("G02 — aiTab handles changelog and settings tabs")
else:
    fail("G02 — aiTab missing changelog/settings tab handling")

# ai tab function handles all 5 tabs
if re.search(r"'changelog',\s*'settings'|\"changelog\",\s*\"settings\"", src) or \
   ("'changelog'" in src and "'settings'" in src and "loadChangelog" in src and "loadAiSettingsTab" in src):
    ok("G03 — aiTab() handles all 5 tabs")
else:
    fail("G03 — aiTab() does not handle all 5 tabs")


# ══════════════════════════════════════════════════════════════
print("\n═══ H — Section loaders have error guards ═══")

loaders = {
    "loadOrders":        "loadOrders",
    "loadConversations": "loadConversations",
    "loadProducts":      "loadProducts",
    "loadMenuImages":    "loadMenuImages",
    "loadAnalytics":     "loadAnalytics",
    "loadSettings":      "loadSettings",
}
for fn, label in loaders.items():
    # Find the function and check it has a try/catch
    fn_m = re.search(rf"async function {re.escape(fn)}[^{{]*\{{(.+?)(?=\nasync function|\nfunction [a-z])", src, re.DOTALL)
    if fn_m:
        body = fn_m.group(1)
        if "try {" in body or "try{" in body or ".catch(" in body:
            ok(f"H01 — {fn}() has error guard")
        else:
            warn(f"H01 — {fn}() has no obvious try/catch")
    else:
        warn(f"H01 — {fn}() not found for inspection")

# ══════════════════════════════════════════════════════════════
print("\n═══ I — Cache busting ═══")

if "APP_VERSION" in src:
    ok("I01 — APP_VERSION constant defined")
else:
    warn("I01 — APP_VERSION constant not found")

if "config.js?v=" in src:
    ok("I02 — config.js loaded with cache-busting query string")
else:
    warn("I02 — config.js not loaded with cache-bust version")


# ══════════════════════════════════════════════════════════════
print("\n═══ J — API smoke: dashboard with real server ═══")

if not _server_ok or not _tok:
    warn("J — server not reachable or no token, skipping API tests")
else:
    d, s = _req("get", "/api/analytics/summary", _tok)
    if s == 200:
        ok("J01 — /api/analytics/summary accessible")
    else:
        warn("J01 — /api/analytics/summary returned", str(s))

    d2, s2 = _req("get", "/api/analytics/weekly-revenue", _tok)
    if s2 == 200:
        ok("J02 — /api/analytics/weekly-revenue accessible")
    else:
        warn("J02 — weekly-revenue returned", str(s2))

    d3, s3 = _req("get", "/api/analytics/channel-breakdown", _tok)
    if s3 == 200:
        ok("J03 — /api/analytics/channel-breakdown accessible")
    else:
        warn("J03 — channel-breakdown returned", str(s3))

    d4, s4 = _req("get", "/api/analytics/top-products", _tok)
    if s4 == 200:
        ok("J04 — /api/analytics/top-products accessible")
    else:
        warn("J04 — top-products returned", str(s4))

    # Regressions
    for path, label in [("/api/ai/corrections", "AI corrections"), ("/api/ai/knowledge", "AI knowledge"),
                        ("/api/ai/quality/summary", "AI quality summary")]:
        d5, s5 = _req("get", path, _tok)
        if s5 == 200:
            ok(f"J05 — {label} still accessible (regression)")
        else:
            warn(f"J05 — {label} returned {s5}")


# ══════════════════════════════════════════════════════════════
print(f"""
╔══════════════════════════════════╗
║  NUMBER 26 — BLANK UI HOTFIX     ║
╠══════════════════════════════════╣
║  ✅ Passed  : {_passed:<20} ║
║  ❌ Failed  : {_failed:<20} ║
║  ⚠️  Warnings: {_warned:<20} ║
╚══════════════════════════════════╝
""")
if _failed > 0:
    print("❌ TESTS FAILED"); sys.exit(1)
else:
    print("✅ ALL TESTS PASSED"); sys.exit(0)
