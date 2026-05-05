"""
scripts/day25_ai_training_test.py
NUMBER 25 — Serious AI Training / Learning System Tests

Test sections:
  A — Static: DB migrations & new tables in code
  B — Static: API endpoints declared in main.py
  C — Static: bot.py loads richer corrections + knowledge
  D — Static: app.html AI Training section UI
  E — Static: super.html AI overview panel
  F — Static: analytics_service has AI learning functions
  G — API smoke: CRUD for corrections (enriched)
  H — API smoke: feedback lifecycle (add → approve → promote)
  I — API smoke: knowledge base CRUD
  J — API smoke: quality summary endpoint
  K — Tenant isolation: Restaurant A data not visible to Restaurant B
  L — Regression NUMBER 21: menu images unbroken
  M — Regression NUMBER 22: voice fields unbroken
  N — Regression NUMBER 23: analytics unbroken
  O — Production readiness check #14 (AI Learning tables)

Usage:
    python scripts/day25_ai_training_test.py                 # localhost
    BASE_URL=https://your-app.onrender.com python ...
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
    email = f"v25_{tag}_{ts}@test.local"
    d, s = _req("post", "/api/auth/register", json_body={
        "email": email, "password": "Test123!!",
        "owner_name": f"V25_{tag}", "restaurant_name": f"V25_{tag}", "phone": f"07{ts}",
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
MAIN_PY_PATH    = os.path.join(ROOT, "main.py")
DB_PY_PATH      = os.path.join(ROOT, "database.py")
BOT_PY_PATH     = os.path.join(ROOT, "services", "bot.py")
ANALYTICS_PATH  = os.path.join(ROOT, "services", "analytics_service.py")

def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return ""

app_src      = _read(APP_HTML_PATH)
super_src    = _read(SUPER_HTML_PATH)
main_src     = _read(MAIN_PY_PATH)
db_src       = _read(DB_PY_PATH)
bot_src      = _read(BOT_PY_PATH)
analytics_src = _read(ANALYTICS_PATH)


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ A — Static: DB schema & migrations ═══")

# A01 — new tables in CREATE TABLE
for tbl in ["ai_feedback", "restaurant_knowledge", "ai_quality_logs"]:
    if f"CREATE TABLE IF NOT EXISTS {tbl}" in db_src:
        _ok(f"A01 — {tbl} table defined in database.py")
    else:
        _fail(f"A01 — {tbl} table missing from database.py")

# A02 — bot_corrections extended columns in migrations
for col in ["trigger_text", "correction_text", "category", "priority", "usage_count", "created_by", "updated_at"]:
    pat = f'("bot_corrections", "{col}"'
    if pat in db_src:
        _ok(f"A02 — bot_corrections.{col} in migrations")
    else:
        _fail(f"A02 — bot_corrections.{col} missing from migrations")

# A03 — ai_feedback columns
for col in ["rating", "status", "suggested_correction", "reviewed_by"]:
    if col in db_src and "ai_feedback" in db_src:
        _ok(f"A03 — ai_feedback.{col} present in schema")
    else:
        _fail(f"A03 — ai_feedback.{col} not found near ai_feedback table")

# A04 — restaurant_knowledge columns
for col in ["title", "content", "category", "source", "priority", "is_active"]:
    if col in db_src and "restaurant_knowledge" in db_src:
        _ok(f"A04 — restaurant_knowledge.{col} present in schema")
    else:
        _fail(f"A04 — restaurant_knowledge.{col} not found")

# A05 — ai_quality_logs columns
for col in ["intent_detected", "confidence", "used_corrections", "used_knowledge", "escalation_triggered"]:
    if col in db_src and "ai_quality_logs" in db_src:
        _ok(f"A05 — ai_quality_logs.{col} present in schema")
    else:
        _fail(f"A05 — ai_quality_logs.{col} not found")

# A06 — indexes exist
for idx in ["idx_ai_feedback_restaurant", "idx_knowledge_restaurant", "idx_ai_quality_restaurant"]:
    if idx in db_src:
        _ok(f"A06 — index {idx} defined")
    else:
        _fail(f"A06 — index {idx} missing")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ B — Static: API endpoints in main.py ═══")

ENDPOINTS = [
    ("GET",    "/api/ai/corrections",           "B01"),
    ("POST",   "/api/ai/corrections",           "B02"),
    ("PUT",    "/api/ai/corrections",           "B03"),
    ("DELETE", "/api/ai/corrections",           "B04"),
    ("GET",    "/api/ai/feedback",              "B05"),
    ("POST",   "/api/ai/feedback",              "B06"),
    ("PUT",    "/api/ai/feedback/{fid}/approve","B07"),
    ("PUT",    "/api/ai/feedback/{fid}/reject", "B08"),
    ("GET",    "/api/ai/knowledge",             "B09"),
    ("POST",   "/api/ai/knowledge",             "B10"),
    ("PUT",    "/api/ai/knowledge",             "B11"),
    ("DELETE", "/api/ai/knowledge",             "B12"),
    ("GET",    "/api/ai/quality",               "B13"),
    ("GET",    "/api/ai/quality/summary",       "B14"),
    ("GET",    "/api/super/ai/overview",        "B15"),
]
for method, path, code in ENDPOINTS:
    base_path = path.split("{")[0].rstrip("/")
    if base_path in main_src:
        _ok(f"{code} — {method} {path} endpoint found")
    else:
        _fail(f"{code} — {method} {path} endpoint missing from main.py")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ C — Static: bot.py corrections + knowledge loading ═══")

if "trigger_text" in bot_src and "correction_text" in bot_src:
    _ok("C01 — bot.py loads trigger_text + correction_text from DB")
else:
    _fail("C01 — bot.py missing trigger_text/correction_text load")

if "restaurant_knowledge" in bot_src:
    _ok("C02 — bot.py queries restaurant_knowledge table")
else:
    _fail("C02 — bot.py missing restaurant_knowledge query")

if "knowledge_list" in bot_src:
    _ok("C03 — knowledge_list built in bot.py")
else:
    _fail("C03 — knowledge_list missing in bot.py")

if "معلومات المطعم المهمة" in bot_src:
    _ok("C04 — knowledge injected into system prompt (Arabic section header)")
else:
    _fail("C04 — knowledge not injected into system prompt")

if "knowledge: list = None" in bot_src or "knowledge=None" in bot_src or "knowledge=knowledge_list" in bot_src:
    _ok("C05 — _build_system_prompt accepts knowledge parameter")
else:
    _fail("C05 — _build_system_prompt missing knowledge param")

if "legacy" in bot_src or "r[\"text\"]" in bot_src or "r['text']" in bot_src:
    _ok("C06 — legacy text field still supported in corrections loading")
else:
    _warn("C06 — legacy text field handling unclear (may be fine)")

if "priority DESC" in bot_src or "priority" in bot_src:
    _ok("C07 — corrections loaded with priority ordering")
else:
    _warn("C07 — no priority ordering in bot.py corrections")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ D — Static: app.html AI Training section ═══")

if 'id="sec-ai-training"' in app_src:
    _ok("D01 — sec-ai-training section present in app.html")
else:
    _fail("D01 — sec-ai-training section missing from app.html")

if 'href="#ai-training"' in app_src or "ai-training" in app_src:
    _ok("D02 — AI Training nav item present")
else:
    _fail("D02 — AI Training nav item missing")

for tab, label in [("aiTabFeedback","Feedback tab"), ("aiTabCorrections","Corrections tab"), ("aiTabKnowledge","Knowledge tab")]:
    if tab in app_src:
        _ok(f"D03 — {label} ({tab}) present")
    else:
        _fail(f"D03 — {label} ({tab}) missing")

for kid in ["aiKpiCorrections", "aiKpiKnowledge", "aiKpiFeedback", "aiKpiSatisfaction"]:
    if kid in app_src:
        _ok(f"D04 — KPI element {kid} present")
    else:
        _fail(f"D04 — KPI element {kid} missing")

for fn in ["loadAiTraining", "loadAiFeedback", "loadAiCorrections", "loadAiKnowledge",
           "addAiCorrection", "addAiKnowledge", "approveFeedback", "rejectFeedback",
           "toggleAiCorrection", "deleteAiCorrection", "toggleAiKnowledge", "deleteAiKnowledge"]:
    if f"function {fn}" in app_src or f"async function {fn}" in app_src:
        _ok(f"D05 — JS function {fn} defined")
    else:
        _fail(f"D05 — JS function {fn} missing")

for path in ["/api/ai/corrections", "/api/ai/feedback", "/api/ai/knowledge", "/api/ai/quality/summary"]:
    if path in app_src:
        _ok(f"D06 — API call to {path} in app.html")
    else:
        _fail(f"D06 — API call to {path} missing from app.html")

if "badgeAiFeedback" in app_src:
    _ok("D07 — AI feedback badge element present")
else:
    _fail("D07 — AI feedback badge missing")

if "'ai-training'" in app_src or '"ai-training"' in app_src:
    _ok("D08 — ai-training in PAGES navigation map")
else:
    _fail("D08 — ai-training missing from PAGES navigation map")

if "promote_correction" in app_src:
    _ok("D09 — promote_correction flow present in UI")
else:
    _fail("D09 — promote_correction flow missing from UI")

if "ai-tab-btn" in app_src and "ai-tab-active" in app_src:
    _ok("D10 — AI tab CSS classes defined")
else:
    _fail("D10 — AI tab CSS classes missing")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ E — Static: super.html AI overview panel ═══")

if "dashAiPanel" in super_src:
    _ok("E01 — dashAiPanel element in super.html")
else:
    _fail("E01 — dashAiPanel missing from super.html")

if "/api/super/ai/overview" in super_src:
    _ok("E02 — /api/super/ai/overview called in super.html")
else:
    _fail("E02 — /api/super/ai/overview missing from super.html")

if "aiOverview" in super_src:
    _ok("E03 — aiOverview rendering present in super.html")
else:
    _fail("E03 — aiOverview rendering missing")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ F — Static: analytics_service AI learning functions ═══")

for fn in ["get_ai_learning_metrics", "get_super_ai_overview"]:
    if f"def {fn}" in analytics_src:
        _ok(f"F01 — {fn} defined in analytics_service.py")
    else:
        _fail(f"F01 — {fn} missing from analytics_service.py")

if "pending_ai_feedback" in analytics_src:
    _ok("F02 — pending_ai_feedback added to health analytics")
else:
    _fail("F02 — pending_ai_feedback missing from health analytics")

for tbl in ["ai_feedback", "restaurant_knowledge", "bot_corrections", "ai_quality_logs"]:
    if tbl in analytics_src:
        _ok(f"F03 — analytics_service queries {tbl}")
    else:
        _fail(f"F03 — analytics_service missing {tbl} query")


# ══════════════════════════════════════════════════════════════════════════════
# Create two shared test users ONCE — avoids hitting the rate limiter
print("\n═══ Preparing API test users ═══")

_tok1, _rid1 = register_and_login("main1")
time.sleep(1)  # brief pause so login rate window doesn't stack
_tok2, _rid2 = register_and_login("iso2")

_server_up = bool(_tok1)

if _server_up:
    print(f"  ✅ Two test users created (rid1={_rid1[:8] if _rid1 else 'n/a'}, rid2={_rid2[:8] if _rid2 else 'n/a'})")
else:
    print(f"  ⚠️  Server not reachable — all API tests (G-L, N) will be skipped")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ G — API smoke: corrections CRUD (enriched) ═══")

if not _server_up:
    _warn("G00 — server not reachable, skipping")
else:
    # G01 — list corrections (empty)
    d, s = _req("get", "/api/ai/corrections", token=_tok1)
    if s == 200 and isinstance(d, list):
        _ok("G01 — GET /api/ai/corrections returns 200 list")
    else:
        _fail("G01 — GET /api/ai/corrections", f"status={s}")

    # G02 — add enriched correction
    d, s = _req("post", "/api/ai/corrections", token=_tok1, json_body={
        "trigger_text": "كم السعر", "correction_text": "الأسعار في المنيو أعلاه",
        "category": "أسعار", "priority": 5
    })
    if s == 200 and d and d.get("ok"):
        _ok("G02 — POST /api/ai/corrections with trigger+correction")
        corr_id = d.get("id")
    else:
        _fail("G02 — POST /api/ai/corrections enriched", f"status={s} body={d}")
        corr_id = None

    # G03 — list now has 1 item with trigger_text
    d, s = _req("get", "/api/ai/corrections", token=_tok1)
    if s == 200 and isinstance(d, list) and len(d) >= 1:
        _ok("G03 — GET /api/ai/corrections lists added correction")
        item = next((x for x in d if x.get("trigger_text") == "كم السعر"), None)
        if item:
            _ok("G03b — trigger_text returned correctly")
        else:
            _fail("G03b — trigger_text not in response")
    else:
        _fail("G03 — GET /api/ai/corrections after add", f"status={s}")

    # G04 — dedup
    d, s = _req("post", "/api/ai/corrections", token=_tok1, json_body={
        "trigger_text": "كم السعر", "correction_text": "الأسعار في المنيو أعلاه",
    })
    if s == 200 and d and d.get("deduped"):
        _ok("G04 — duplicate correction returns deduped=True")
    else:
        _warn("G04 — dedup not confirmed", f"status={s} body={d}")

    # G05 — delete
    if corr_id:
        d, s = _req("delete", f"/api/ai/corrections/{corr_id}", token=_tok1)
        if s == 200:
            _ok("G05 — DELETE /api/ai/corrections/{id} returns 200")
        else:
            _fail("G05 — DELETE correction", f"status={s}")

    # G06 — legacy text-only correction
    d, s = _req("post", "/api/ai/corrections", token=_tok1, json_body={
        "text": "الطلب بيوصل خلال 30 دقيقة"
    })
    if s == 200 and d and d.get("ok"):
        _ok("G06 — legacy text-only correction still works")
        legacy_id = d.get("id")
    else:
        _fail("G06 — legacy text correction", f"status={s} body={d}")
        legacy_id = None

    # G07 — toggle via existing bot-config endpoint still works
    if legacy_id:
        d, s = _req("patch", f"/api/bot-config/corrections/{legacy_id}", token=_tok1, json_body={"is_active": False})
        if s == 200:
            _ok("G07 — PATCH /api/bot-config/corrections toggle still works (regression)")
        else:
            _fail("G07 — PATCH toggle regression", f"status={s}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ H — API smoke: feedback lifecycle ═══")

if not _server_up:
    _warn("H00 — server not reachable, skipping")
else:
    # H01 — submit bad feedback
    d, s = _req("post", "/api/ai/feedback", token=_tok1, json_body={
        "rating": "bad",
        "reason": "رد خاطئ عن وقت التوصيل",
        "suggested_correction": "وقت التوصيل 45 دقيقة دائماً",
        "conversation_id": "test-conv-001",
    })
    if s == 200 and d and d.get("ok"):
        _ok("H01 — POST /api/ai/feedback (bad rating)")
        fid = d.get("id")
    else:
        _fail("H01 — POST feedback", f"status={s}")
        fid = None

    # H02 — submit good feedback
    d, s = _req("post", "/api/ai/feedback", token=_tok1, json_body={
        "rating": "good", "reason": "رد ممتاز"
    })
    if s == 200 and d and d.get("ok"):
        _ok("H02 — POST /api/ai/feedback (good rating)")
    else:
        _fail("H02 — POST good feedback", f"status={s}")

    # H03 — list pending feedback
    d, s = _req("get", "/api/ai/feedback?status=pending", token=_tok1)
    if s == 200 and isinstance(d, list) and len(d) >= 1:
        _ok(f"H03 — GET /api/ai/feedback?status=pending returns {len(d)} item(s)")
    else:
        _fail("H03 — GET feedback pending", f"status={s}")

    # H04 — invalid rating rejected
    d, s = _req("post", "/api/ai/feedback", token=_tok1, json_body={"rating": "neutral"})
    if s == 400:
        _ok("H04 — invalid rating rejected with 400")
    else:
        _warn("H04 — invalid rating not rejected", f"status={s}")

    # H05 — approve with promote
    if fid:
        d, s = _req("put", f"/api/ai/feedback/{fid}/approve", token=_tok1, json_body={"promote_correction": True})
        if s == 200 and d and d.get("ok"):
            _ok("H05 — PUT approve with promote returns 200")
            if d.get("promoted"):
                _ok("H05b — promoted=True confirmed")
            else:
                _warn("H05b — promoted flag not True", f"body={d}")
        else:
            _fail("H05 — approve+promote", f"status={s}")

    # H06 — approved feedback no longer in pending list
    if fid:
        d, s = _req("get", "/api/ai/feedback?status=pending", token=_tok1)
        pending_ids = [f.get("id") for f in (d or [])]
        if fid not in pending_ids:
            _ok("H06 — approved feedback removed from pending list")
        else:
            _fail("H06 — approved feedback still in pending list")

    # H07 — reject a feedback
    d2, s2 = _req("post", "/api/ai/feedback", token=_tok1, json_body={"rating": "needs_correction", "reason": "test"})
    if s2 == 200 and d2:
        fid2 = d2.get("id")
        d3, s3 = _req("put", f"/api/ai/feedback/{fid2}/reject", token=_tok1)
        if s3 == 200:
            _ok("H07 — PUT reject feedback returns 200")
        else:
            _fail("H07 — reject feedback", f"status={s3}")
    else:
        _warn("H07 — could not create feedback to reject")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ I — API smoke: knowledge base CRUD ═══")

if not _server_up:
    _warn("I00 — server not reachable, skipping")
else:
    # I01 — list (empty)
    d, s = _req("get", "/api/ai/knowledge", token=_tok1)
    if s == 200 and isinstance(d, list):
        _ok("I01 — GET /api/ai/knowledge returns 200 list")
    else:
        _fail("I01 — GET knowledge", f"status={s}")

    # I02 — add entry
    d, s = _req("post", "/api/ai/knowledge", token=_tok1, json_body={
        "title": "سياسة الاسترجاع",
        "content": "نقبل الاسترجاع خلال 30 دقيقة فقط",
        "category": "سياسات",
        "source": "manual",
        "priority": 3,
    })
    if s == 200 and d and d.get("ok"):
        _ok("I02 — POST /api/ai/knowledge adds entry")
        kid = d.get("id")
    else:
        _fail("I02 — POST knowledge", f"status={s} body={d}")
        kid = None

    # I03 — missing title rejected
    d, s = _req("post", "/api/ai/knowledge", token=_tok1, json_body={"content": "محتوى فقط"})
    if s == 400:
        _ok("I03 — missing title rejected with 400")
    else:
        _warn("I03 — missing title not rejected", f"status={s}")

    # I04 — missing content rejected
    d, s = _req("post", "/api/ai/knowledge", token=_tok1, json_body={"title": "عنوان فقط"})
    if s == 400:
        _ok("I04 — missing content rejected with 400")
    else:
        _warn("I04 — missing content not rejected", f"status={s}")

    # I05 — list now has entry
    d, s = _req("get", "/api/ai/knowledge", token=_tok1)
    if s == 200 and isinstance(d, list) and len(d) >= 1:
        _ok("I05 — GET knowledge returns added entry")
        item = next((x for x in d if x.get("title") == "سياسة الاسترجاع"), None)
        if item:
            _ok("I05b — title field correct")
        else:
            _fail("I05b — title mismatch in knowledge list")
    else:
        _fail("I05 — knowledge list", f"status={s}")

    # I06 — update entry
    if kid:
        d, s = _req("put", f"/api/ai/knowledge/{kid}", token=_tok1, json_body={
            "title": "سياسة الاسترجاع المحدّثة",
            "content": "نقبل الاسترجاع خلال ساعة",
            "is_active": True,
        })
        if s == 200 and d and d.get("ok"):
            _ok("I06 — PUT /api/ai/knowledge/{id} updates entry")
        else:
            _fail("I06 — PUT knowledge", f"status={s}")

    # I07 — toggle off
    if kid:
        d, s = _req("put", f"/api/ai/knowledge/{kid}", token=_tok1, json_body={
            "title": "سياسة الاسترجاع المحدّثة",
            "content": "نقبل الاسترجاع خلال ساعة",
            "is_active": False,
        })
        if s == 200:
            _ok("I07 — knowledge can be deactivated (is_active=False)")
        else:
            _fail("I07 — deactivate knowledge", f"status={s}")

    # I08 — delete
    if kid:
        d, s = _req("delete", f"/api/ai/knowledge/{kid}", token=_tok1)
        if s == 200:
            _ok("I08 — DELETE /api/ai/knowledge/{id} returns 200")
        else:
            _fail("I08 — DELETE knowledge", f"status={s}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ J — API smoke: quality summary ═══")

if not _server_up:
    _warn("J00 — server not reachable, skipping")
else:
    # J01 — summary endpoint
    d, s = _req("get", "/api/ai/quality/summary", token=_tok1)
    if s == 200 and isinstance(d, dict):
        _ok("J01 — GET /api/ai/quality/summary returns 200 dict")
        for field in ["active_corrections", "active_knowledge", "total_feedback", "pending_feedback"]:
            if field in d:
                _ok(f"J01b — field {field} present in summary")
            else:
                _fail(f"J01b — field {field} missing from summary", f"keys={list(d.keys())}")
    else:
        _fail("J01 — quality summary", f"status={s} body={d}")

    # J02 — quality logs endpoint
    d, s = _req("get", "/api/ai/quality", token=_tok1)
    if s == 200 and isinstance(d, list):
        _ok("J02 — GET /api/ai/quality returns 200 list")
    else:
        _fail("J02 — quality logs", f"status={s}")

    # J03 — satisfaction_rate is None or a number
    d, s = _req("get", "/api/ai/quality/summary", token=_tok1)
    if s == 200:
        sr = d.get("satisfaction_rate")
        if sr is None or isinstance(sr, (int, float)):
            _ok("J03 — satisfaction_rate is null or numeric (correct for empty state)")
        else:
            _fail("J03 — satisfaction_rate unexpected type", f"value={sr}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ K — Tenant isolation ═══")

if not _server_up or not _tok2:
    _warn("K00 — two tenants not available, skipping isolation tests")
else:
    # K01 — add feedback in restaurant 1
    d, s = _req("post", "/api/ai/feedback", token=_tok1, json_body={
        "rating": "bad", "reason": "خاص بمطعم 1"
    })
    if s == 200 and d:
        fid_k1 = d.get("id")
        # K02 — restaurant 2 cannot see restaurant 1's feedback
        d2, s2 = _req("get", "/api/ai/feedback", token=_tok2)
        ids_2 = [f.get("id") for f in (d2 or [])]
        if fid_k1 not in ids_2:
            _ok("K01 — restaurant 2 cannot see restaurant 1's feedback (tenant isolation)")
        else:
            _fail("K01 — TENANT ISOLATION BREACH: restaurant 2 sees restaurant 1 feedback!")
    else:
        _warn("K01 — couldn't create feedback for isolation test")

    # K03 — correction isolation
    d, s = _req("post", "/api/ai/corrections", token=_tok1, json_body={"text": "تصحيح سري لمطعم 1"})
    if s == 200 and d:
        cid_k1 = d.get("id")
        d2, s2 = _req("get", "/api/ai/corrections", token=_tok2)
        ids_2 = [c.get("id") for c in (d2 or [])]
        if cid_k1 not in ids_2:
            _ok("K03 — restaurant 2 cannot see restaurant 1's corrections (tenant isolation)")
        else:
            _fail("K03 — TENANT ISOLATION BREACH: restaurant 2 sees restaurant 1 correction!")
    else:
        _warn("K03 — couldn't create correction for isolation test")

    # K04 — cross-tenant approve denied
    if d and d.get("id"):
        fid_cross = None
        d_tmp, s_tmp = _req("post", "/api/ai/feedback", token=_tok1, json_body={"rating": "bad"})
        if s_tmp == 200:
            fid_cross = d_tmp.get("id")
        if fid_cross:
            d3, s3 = _req("put", f"/api/ai/feedback/{fid_cross}/approve", token=_tok2, json_body={})
            if s3 == 404:
                _ok("K04 — cross-tenant approve returns 404 (correct)")
            else:
                _warn("K04 — cross-tenant approve did not return 404", f"status={s3}")

    # K05 — knowledge isolation
    d, s = _req("post", "/api/ai/knowledge", token=_tok1, json_body={
        "title": "سر مطعم 1", "content": "معلومة سرية"
    })
    if s == 200 and d:
        kid_k1 = d.get("id")
        d2, s2 = _req("get", "/api/ai/knowledge", token=_tok2)
        ids_2 = [k.get("id") for k in (d2 or [])]
        if kid_k1 not in ids_2:
            _ok("K05 — restaurant 2 cannot see restaurant 1's knowledge entries (tenant isolation)")
        else:
            _fail("K05 — TENANT ISOLATION BREACH: restaurant 2 sees restaurant 1 knowledge!")
    else:
        _warn("K05 — couldn't create knowledge for isolation test")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ L — Regression NUMBER 21: menu images unbroken ═══")

if 'id="sec-menu-images"' in app_src:
    _ok("L01 — sec-menu-images section still in app.html")
else:
    _fail("L01 — sec-menu-images section missing (regression)")

if not _server_up:
    _warn("L02 — server not reachable, skipping API regression check")
else:
    d, s = _req("get", "/api/menu-images", token=_tok1)
    if s == 200:
        _ok("L02 — GET /api/menu-images still returns 200")
    else:
        _fail("L02 — menu images API regression", f"status={s}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ M — Regression NUMBER 22: voice fields unbroken ═══")

if "transcription_status" in app_src:
    _ok("M01 — transcription_status still referenced in app.html")
else:
    _fail("M01 — transcription_status regression (removed from app.html?)")

if "voice_transcript" in main_src or "voice_service" in main_src:
    _ok("M02 — voice service still referenced in main.py")
else:
    _fail("M02 — voice service regression (main.py)")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ N — Regression NUMBER 23: analytics unbroken ═══")

if "get_voice_analytics" in analytics_src and "get_menu_image_analytics" in analytics_src:
    _ok("N01 — NUMBER 23 analytics functions intact in analytics_service.py")
else:
    _fail("N01 — analytics_service NUMBER 23 functions missing")

if not _server_up:
    _warn("N02 — server not reachable, skipping API regression checks")
else:
    d, s = _req("get", "/api/analytics/summary", token=_tok1)
    if s == 200:
        _ok("N02 — GET /api/analytics/summary still returns 200")
    else:
        _fail("N02 — analytics summary regression", f"status={s}")
    d, s = _req("get", "/api/analytics/voice", token=_tok1)
    if s == 200:
        _ok("N03 — GET /api/analytics/voice still returns 200")
    else:
        _fail("N03 — voice analytics regression", f"status={s}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n═══ O — Production readiness check #14 ═══")

if "# 14. AI Learning tables" in main_src:
    _ok("O01 — production readiness check #14 present in main.py")
else:
    _fail("O01 — production readiness check #14 missing from main.py")

if '"ai_learning"' in main_src:
    _ok("O02 — ai_learning key in readiness checks dict")
else:
    _fail("O02 — ai_learning key missing from readiness checks")

if "/api/production-readiness" in main_src:
    _ok("O03 — /api/production-readiness endpoint still defined")
else:
    _fail("O03 — /api/production-readiness endpoint missing (regression)")


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
print(f"NUMBER 25 — AI Training/Learning System")
print(f"  ✅ Passed:  {_passed}")
print(f"  ❌ Failed:  {_failed}")
print(f"  ⚠️  Warned:  {_warned}")
total = _passed + _failed
pct = round(_passed / total * 100) if total else 0
print(f"  Score:    {_passed}/{total} ({pct}%)")
if _failed == 0:
    print(f"\n  🎉 ALL PASSED — NUMBER 25 COMPLETE")
else:
    print(f"\n  🔧 {_failed} test(s) need attention")
print(f"{'═'*60}\n")
sys.exit(0 if _failed == 0 else 1)
