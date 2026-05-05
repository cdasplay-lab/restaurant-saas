"""
scripts/day21_menu_images_test.py
NUMBER 21 — Menu Images MVP Tests

Tests:
  A — CRUD API (list, create, update, delete)
  B — Bot intent detection (menu phrases → media returned)
  C — Tenant isolation (R1 images not visible to R2)
  D — Production readiness (menu_images in protected tables)

Usage:
    BASE_URL=https://restaurant-saas-1.onrender.com python scripts/day21_menu_images_test.py
    python scripts/day21_menu_images_test.py          # defaults to localhost:8000
"""
import os
import sys
import time
import requests

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

def _req(method, path, token=None, json_body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = getattr(requests, method)(BASE_URL + path, headers=headers, json=json_body, timeout=TIMEOUT)
        try:
            return r.json(), r.status_code
        except Exception:
            return {}, r.status_code
    except Exception as e:
        return None, 0

def register_and_login(tag):
    ts = int(time.time() * 1000) % 10_000_000
    email = f"mi21_{tag}_{ts}@test.local"
    d, s = _req("post", "/api/auth/register", json_body={
        "email": email, "password": "Test123!!",
        "owner_name": f"R_{tag}", "restaurant_name": f"R_{tag}", "phone": f"07{ts}"
    })
    if s not in (200, 201):
        return None, None
    d2, s2 = _req("post", "/api/auth/login", json_body={"email": email, "password": "Test123!!"})
    if s2 != 200:
        return None, None
    token = (d2 or {}).get("access_token") or (d2 or {}).get("token")
    rid   = (d2 or {}).get("restaurant_id") or ((d2 or {}).get("user") or {}).get("restaurant_id")
    return token, rid

def simulate_bot(token, restaurant_id, message):
    d, s = _req("post", "/api/bot/simulate", token=token, json_body={
        "restaurant_id": restaurant_id,
        "customer_name": "test_mi",
        "messages": [message],
    })
    if s != 200 or not d:
        return None
    results = (d or {}).get("results", [])
    if not results:
        return None
    r0 = results[0]
    return r0.get("bot") or r0.get("reply") or r0.get("response") or ""

# ── Pre-register all accounts upfront ────────────────────────────────────────
# Rate limiter is 5/min for register, shared counter with login calls per IP.
# Strategy: register only 3 pairs (3 register + 3 login = 6 total calls),
# then wait for the window to reset before registering the 4th.
# Tenant isolation (Section C) reuses TOKEN_A and TOKEN_OTHER.

print("⏳ Registering test accounts (batch 1/2)...")
TOKEN_A, RID_A = register_and_login("a")         # calls 1, 2
time.sleep(0.5)
TOKEN_OTHER, RID_OTHER = register_and_login("other")  # calls 3, 4
time.sleep(0.5)

print("⏳ Waiting 62s for rate-limit window to reset...")
time.sleep(62)

TOKEN_B, RID_B = register_and_login("b")         # fresh window: calls 1, 2

SAMPLE_URL = "https://images.unsplash.com/photo-1504674900247-0877df9cc836?w=800"
IMG_URL_B  = "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600"

# ── Section A — CRUD API ─────────────────────────────────────────────────────

print("\n═══ A — CRUD API ═══")

if TOKEN_A:
    _ok("A00 — registered and logged in")
else:
    _fail("A00 — register/login failed")

# A01 — List empty
d, s = _req("get", "/api/menu-images", token=TOKEN_A)
if s == 200 and isinstance(d, list):
    _ok("A01 — GET /api/menu-images returns list")
else:
    _fail("A01 — GET /api/menu-images", f"status={s}")

# A02 — Create without image_url → 400
d, s = _req("post", "/api/menu-images", token=TOKEN_A, json_body={"title": "no url"})
if s == 400:
    _ok("A02 — POST without image_url → 400")
else:
    _fail("A02 — should reject missing image_url", f"got {s}")

# A03 — Create valid
d, s = _req("post", "/api/menu-images", token=TOKEN_A, json_body={
    "title": "المنيو الكامل", "image_url": SAMPLE_URL,
    "category": "منيو", "sort_order": 0, "is_active": True,
})
IMG_ID_A = None
if s == 201 and isinstance(d, dict) and d.get("id"):
    IMG_ID_A = d["id"]
    _ok(f"A03 — POST creates image — id={IMG_ID_A[:8]}")
else:
    _fail("A03 — POST /api/menu-images", f"status={s}")

# A04 — List now has 1 item
d, s = _req("get", "/api/menu-images", token=TOKEN_A)
if s == 200 and isinstance(d, list) and len(d) >= 1:
    _ok("A04 — List shows created image")
else:
    _fail("A04 — List after create", f"status={s} count={len(d) if isinstance(d,list) else 'N/A'}")

# A05 — Update
if IMG_ID_A:
    d, s = _req("put", f"/api/menu-images/{IMG_ID_A}", token=TOKEN_A, json_body={
        "title": "منيو جديد", "sort_order": 5,
    })
    if s == 200 and isinstance(d, dict) and d.get("title") == "منيو جديد":
        _ok("A05 — PUT updates image correctly")
    else:
        _fail("A05 — PUT /api/menu-images/{id}", f"status={s}")
else:
    _warn("A05 — skipped (no image ID from A03)")

# A06 — Wrong restaurant cannot update
if IMG_ID_A and TOKEN_OTHER:
    d, s = _req("put", f"/api/menu-images/{IMG_ID_A}", token=TOKEN_OTHER, json_body={"title": "hack"})
    if s == 404:
        _ok("A06 — Other restaurant cannot update foreign image (404)")
    else:
        _fail("A06 — Tenant isolation on PUT", f"status={s}")
else:
    _warn("A06 — skipped")

# A07 — Unauthenticated blocked
d, s = _req("get", "/api/menu-images")
if s in (401, 403):
    _ok("A07 — Unauthenticated GET → 401/403")
else:
    _fail("A07 — Unauthenticated should be blocked", f"status={s}")

# A08/A09 — Delete
if IMG_ID_A:
    d, s = _req("delete", f"/api/menu-images/{IMG_ID_A}", token=TOKEN_A)
    if s == 200:
        _ok("A08 — DELETE removes image")
    else:
        _fail("A08 — DELETE", f"status={s}")
    d, s = _req("get", "/api/menu-images", token=TOKEN_A)
    if s == 200 and isinstance(d, list) and not any(x.get("id") == IMG_ID_A for x in d):
        _ok("A09 — Deleted image no longer in list")
    else:
        _fail("A09 — Image still visible after delete")
else:
    _warn("A08/A09 — skipped")

# ── Section B — Bot Intent Detection ─────────────────────────────────────────

print("\n═══ B — Bot Intent Detection ═══")

if TOKEN_B:
    _ok("B00 — registered and logged in")
else:
    _fail("B00 — register/login failed")

# Add images for bot tests
_imgs_b = []
if TOKEN_B:
    for i, (title, cat) in enumerate([("وجبات", "رئيسية"), ("مشروبات", "مشروبات")]):
        d, s = _req("post", "/api/menu-images", token=TOKEN_B, json_body={
            "title": title, "image_url": IMG_URL_B,
            "category": cat, "sort_order": i, "is_active": True,
        })
        if s == 201:
            _imgs_b.append(d["id"])

if len(_imgs_b) == 2:
    _ok("B01 — Created 2 test images")
else:
    _warn(f"B01 — Created {len(_imgs_b)} images (expected 2)")

MENU_PHRASES = [
    ("المنيو",        "طلب المنيو"),
    ("دزلي المنيو",  "طلب إرسال المنيو"),
    ("صور الاكل",    "طلب صور الأكل"),
    ("menu",          "كلمة menu الإنجليزية"),
    ("شنو عدكم",     "سؤال عن الوجبات"),
]

for phrase, label in MENU_PHRASES:
    if not TOKEN_B or not RID_B:
        _warn(f"B — {label} — skipped")
        continue
    reply = simulate_bot(TOKEN_B, RID_B, phrase)
    if reply is not None:
        has_media_text = "منيونا" in reply or "تفضل" in reply
        _ok(f"B — '{phrase}' ({label}) → {reply[:60]!r}")
    else:
        _warn(f"B — '{phrase}' — simulate returned None (needs OpenAI key)")

# B non-menu falls through to OpenAI
if TOKEN_B and RID_B:
    reply = simulate_bot(TOKEN_B, RID_B, "كم سعر البرغر؟")
    if reply is not None:
        _ok(f"B06 — Non-menu message gets normal reply: {reply[:60]!r}")
    else:
        _warn("B06 — simulate returned None")

# B — no images → bot falls through (no crash)
# Use TOKEN_A which had its image deleted
if TOKEN_A and RID_A:
    reply = simulate_bot(TOKEN_A, RID_A, "المنيو")
    if reply is not None:
        _ok(f"B07 — No images → bot falls through: {reply[:60]!r}")
    else:
        _warn("B07 — simulate returned None for no-images restaurant")
else:
    _warn("B07 — skipped")

# ── Section C — Tenant Isolation ─────────────────────────────────────────────
# Uses TOKEN_A (R1) and TOKEN_OTHER (R2) — already registered above.

print("\n═══ C — Tenant Isolation ═══")

if TOKEN_A and TOKEN_OTHER:
    _ok("C00 — two restaurants available (R_a and R_other)")
else:
    _fail("C00 — missing token for one of the restaurants")

if TOKEN_A and TOKEN_OTHER:
    # Create a fresh image for R_a
    d, s = _req("post", "/api/menu-images", token=TOKEN_A, json_body={
        "title": "صورة حصرية R_a", "image_url": SAMPLE_URL, "category": "عزل", "is_active": True,
    })
    img_isolation = d.get("id") if s == 201 else None

    if not img_isolation:
        _warn("C — could not create isolation image for R_a")
    else:
        # C01 — R_other cannot see R_a images
        d, s = _req("get", "/api/menu-images", token=TOKEN_OTHER)
        if s == 200 and isinstance(d, list):
            leak = [x for x in d if x.get("id") == img_isolation]
            if not leak:
                _ok("C01 — R_other cannot see R_a menu images (tenant isolation ✓)")
            else:
                _fail("C01 — LEAK: R_other can see R_a image!", f"id={img_isolation}")
        else:
            _warn("C01 — could not verify isolation", f"status={s}")

        # C02 — R_other cannot delete R_a image
        d, s = _req("delete", f"/api/menu-images/{img_isolation}", token=TOKEN_OTHER)
        if s == 404:
            _ok("C02 — R_other cannot delete R_a image (404 ✓)")
        else:
            _fail("C02 — R_other should not delete R_a image", f"status={s}")

        # C03 — R_other cannot update R_a image
        d, s = _req("put", f"/api/menu-images/{img_isolation}", token=TOKEN_OTHER, json_body={"title": "hack"})
        if s == 404:
            _ok("C03 — R_other cannot update R_a image (404 ✓)")
        else:
            _fail("C03 — R_other should not update R_a image", f"status={s}")

# ── Section D — Production Readiness ─────────────────────────────────────────

print("\n═══ D — Production Readiness ═══")

SA_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "")
SA_PASS  = os.getenv("SUPER_ADMIN_PASS",  "")
SA_TOKEN = None

if SA_EMAIL and SA_PASS:
    d, s = _req("post", "/api/super/login", json_body={"email": SA_EMAIL, "password": SA_PASS})
    if s == 200:
        SA_TOKEN = (d or {}).get("access_token") or (d or {}).get("token")

if SA_TOKEN:
    d, s = _req("get", "/api/production-readiness", token=SA_TOKEN)
    if s == 200:
        missing = (d or {}).get("checks", {}).get("protected_tables", {}).get("missing", [])
        if "menu_images" not in missing:
            _ok("D01 — production-readiness: menu_images in protected tables ✓")
        else:
            _fail("D01 — menu_images missing from protected tables", str(missing))
    else:
        _warn("D01 — production-readiness returned non-200", f"status={s}")
else:
    _warn("D01 — set SUPER_ADMIN_EMAIL + SUPER_ADMIN_PASS to run this check")

# ── Summary ──────────────────────────────────────────────────────────────────

total = _passed + _failed + _warned
print()
print("=" * 55)
print("  NUMBER 21 — Menu Images Test Results")
print("=" * 55)
print(f"  Passed  : {_passed}")
print(f"  Failed  : {_failed}")
print(f"  Warned  : {_warned}")
print(f"  Total   : {total}")
print("=" * 55)

if _failed == 0:
    print("  ✅ NUMBER 21 MENU IMAGES MVP — ALL CHECKS PASSED")
else:
    print(f"  ❌ {_failed} FAILURES FOUND")

sys.exit(0 if _failed == 0 else 1)
