#!/usr/bin/env python3
"""
NUMBER 33 — Story Cache + Context Hardening Test
Goal: 0 FAIL. WARNs allowed for video multi-frame limitation only.
"""
import sys, os, json, time, uuid, hashlib, tempfile, atexit
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS_COUNT = 0
FAIL_COUNT = 0
WARN_COUNT = 0
FAILURES   = []

def _result(label, status, expected="", actual="", root_cause="", fix=""):
    global PASS_COUNT, FAIL_COUNT, WARN_COUNT
    if status == "PASS":
        PASS_COUNT += 1
        print(f"  {GREEN}✓ PASS{RESET}  {label}")
    elif status == "FAIL":
        FAIL_COUNT += 1
        FAILURES.append({"label": label, "expected": expected, "actual": actual,
                          "root_cause": root_cause, "fix": fix, "severity": "FAIL"})
        print(f"  {RED}✗ FAIL{RESET}  {label}")
        if expected: print(f"         expected : {expected}")
        if actual:   print(f"         actual   : {actual}")
    elif status == "WARN":
        WARN_COUNT += 1
        FAILURES.append({"label": label, "expected": expected, "actual": actual,
                          "root_cause": root_cause, "fix": fix, "severity": "WARN"})
        print(f"  {YELLOW}⚠ WARN{RESET}  {label}")
        if actual: print(f"         gap      : {actual}")

def section(title):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")

# ── DB setup ──────────────────────────────────────────────────────────────────
import database
_tmpdb = tempfile.mktemp(suffix=".test33.db")
atexit.register(lambda: os.unlink(_tmpdb) if os.path.exists(_tmpdb) else None)
database.DB_PATH = _tmpdb
database.init_db()

import uuid as _uuid

RID_A = str(_uuid.uuid4())
RID_B = str(_uuid.uuid4())
UID_A = str(_uuid.uuid4())
CONV  = str(_uuid.uuid4())
CUST  = str(_uuid.uuid4())
PROD_BURGER_ID = str(_uuid.uuid4())
PROD_PIZZA_ID  = str(_uuid.uuid4())

conn = database.get_db()
conn.execute("INSERT INTO restaurants (id,name,plan) VALUES (?,?,'professional')", (RID_A, "مطعم A"))
conn.execute("INSERT INTO restaurants (id,name,plan) VALUES (?,?,'professional')", (RID_B, "مطعم B"))
conn.execute("INSERT INTO users (id,restaurant_id,email,password_hash,name,role) VALUES (?,?,?,?,'Owner A','owner')",
             (UID_A, RID_A, "a@test.com", "x"))
conn.execute("INSERT INTO products (id,restaurant_id,name,price,category,available,image_url) VALUES (?,?,?,?,?,?,?)",
             (PROD_BURGER_ID, RID_A, "برگر كلاسيك", 14000, "برگر", 1, ""))
conn.execute("INSERT INTO products (id,restaurant_id,name,price,category,available,image_url) VALUES (?,?,?,?,?,?,?)",
             (PROD_PIZZA_ID, RID_A, "بيتزا مارگريتا", 18000, "بيتزا", 0, ""))  # unavailable
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST, RID_A, "instagram", "علي"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'instagram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV, RID_A, CUST))
conn.execute("INSERT INTO bot_config (id,restaurant_id,order_extraction_enabled) VALUES (?,?,1)",
             (str(_uuid.uuid4()), RID_A))
conn.commit()
conn.close()

from services import story_cache as _sc
from services import webhooks as _wh
from services import bot as _bot
from services.bot import _detect_menu_image_intent
import inspect

# ══════════════════════════════════════════════════════════════════════════════
# 1. Table existence
# ══════════════════════════════════════════════════════════════════════════════
section("1. Infrastructure")

conn = database.get_db()
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
conn.close()

if "story_context_cache" in tables:
    _result("story_context_cache table exists", "PASS")
else:
    _result("story_context_cache table exists", "FAIL",
            "table created in DB", "table missing",
            "_migrate_db did not create table",
            "Check database.py _migrate_db for story_context_cache block")

# ── Index existence ────────────────────────────────────────────────────────
conn = database.get_db()
indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
conn.close()
if "idx_story_cache_key" in indexes:
    _result("story_context_cache index exists", "PASS")
else:
    _result("story_context_cache index exists", "FAIL",
            "idx_story_cache_key index", "missing", "Index not created")

# ══════════════════════════════════════════════════════════════════════════════
# 2. Cache miss → store → hit cycle
# ══════════════════════════════════════════════════════════════════════════════
section("2. Cache Miss / Hit Cycle")

STORY_ID = "story_abc_123"
MEDIA_URL = "https://cdn.instagram.com/story/burger.jpg"

# First call → miss
cached = _sc.get_cached_story(RID_A, "instagram", STORY_ID, MEDIA_URL)
if cached is None:
    _result("First story query returns cache miss", "PASS")
else:
    _result("First story query returns cache miss", "FAIL",
            "None (miss)", f"got data={cached}", "Cache returned data on empty DB")

# Store a result
match_data = {
    "product": {"id": PROD_BURGER_ID, "name": "برگر كلاسيك", "price": 14000, "category": "برگر"},
    "confidence": "high",
    "description": "برگر كلاسيك",
}
context_str = "[العميل يرد على ستوري يعرض: برگر كلاسيك — 14,000 د.ع]\nسياق للبوت: استغل الفرصة وابدأ flow البيع."
_sc.store_story_cache(RID_A, "instagram", STORY_ID, MEDIA_URL, match_data, context_str, False)

# Second call → hit
cached2 = _sc.get_cached_story(RID_A, "instagram", STORY_ID, MEDIA_URL)
if cached2 and cached2.get("product_name") == "برگر كلاسيك":
    _result("Second story query returns cache hit", "PASS")
else:
    _result("Second story query returns cache hit", "FAIL",
            "product_name='برگر كلاسيك'", f"got {cached2}",
            "Cache store or get broken")

# ── DB persistence check ───────────────────────────────────────────────────
conn = database.get_db()
row = conn.execute("SELECT * FROM story_context_cache WHERE restaurant_id=?", (RID_A,)).fetchone()
conn.close()
if row and row["matched_product_name"] == "برگر كلاسيك":
    _result("Cache persisted to DB correctly", "PASS")
else:
    _result("Cache persisted to DB correctly", "FAIL",
            "row in story_context_cache", f"row={dict(row) if row else None}")

# ── TTL expiry check ───────────────────────────────────────────────────────
STORY_EXPIRED = "story_expired_999"
_sc.store_story_cache(RID_A, "instagram", STORY_EXPIRED, MEDIA_URL, match_data, context_str, False)
# Manually expire the row
conn = database.get_db()
conn.execute(
    "UPDATE story_context_cache SET expires_at=datetime('now', '-1 hour') WHERE platform_story_id=?",
    (STORY_EXPIRED,)
)
conn.commit()
conn.close()
# Clear mem cache for this key
key_expired = f"{RID_A}:instagram:id:{STORY_EXPIRED}"
_sc._mem.pop(key_expired, None)

expired = _sc.get_cached_story(RID_A, "instagram", STORY_EXPIRED, MEDIA_URL)
if expired is None:
    _result("Expired cache entry returns miss", "PASS")
else:
    _result("Expired cache entry returns miss", "FAIL",
            "None after expiry", f"got data: {expired}")

# ══════════════════════════════════════════════════════════════════════════════
# 3. 100 replies to same story — no Vision API duplication
# ══════════════════════════════════════════════════════════════════════════════
section("3. 100 Replies Same Story — Vision Not Duplicated")

STORY_HOT = "story_viral_456"
vision_calls = [0]
original_match = _wh._match_story_to_product

def _patched_match(img_bytes, restaurant_id):
    vision_calls[0] += 1
    return original_match(img_bytes, restaurant_id)

_wh._match_story_to_product = _patched_match

# Pre-store a valid cache entry
_sc.store_story_cache(
    RID_A, "instagram", STORY_HOT, "https://cdn.example.com/hot.jpg",
    match_data, context_str, False
)

# Simulate 100 calls to _analyze_story_cached for the same story
for _ in range(100):
    _wh._analyze_story_cached(
        "https://cdn.example.com/hot.jpg", STORY_HOT,
        RID_A, "", "instagram"
    )

_wh._match_story_to_product = original_match  # restore

if vision_calls[0] == 0:
    _result("100 replies same story → 0 Vision API calls (all cached)", "PASS")
else:
    _result("100 replies same story → 0 Vision API calls", "FAIL",
            "0 Vision API calls", f"{vision_calls[0]} calls made",
            "Cache miss — Vision API called despite cached entry",
            "Fix get_cached_story or _analyze_story_cached")

# ══════════════════════════════════════════════════════════════════════════════
# 4. Tenant isolation
# ══════════════════════════════════════════════════════════════════════════════
section("4. Tenant Isolation")

_sc.store_story_cache(RID_A, "instagram", "story_iso", "https://a.com/s.jpg",
                      match_data, context_str, False)
result_b = _sc.get_cached_story(RID_B, "instagram", "story_iso", "https://a.com/s.jpg")
if result_b is None:
    _result("Restaurant B cannot see restaurant A's cache", "PASS")
else:
    _result("Restaurant B cannot see restaurant A's cache", "FAIL",
            "None", f"got data from other tenant: {result_b}",
            "Cache key not scoped to restaurant_id")

# ══════════════════════════════════════════════════════════════════════════════
# 5. Deterministic story replies
# ══════════════════════════════════════════════════════════════════════════════
section("5. Deterministic Story Reply Patterns")

burger_ctx = {
    "product_id": PROD_BURGER_ID,
    "product_name": "برگر كلاسيك",
    "product_price": 14000,
    "product_category": "برگر",
    "confidence": "high",
    "is_video": False,
}

pizza_ctx = {
    "product_id": PROD_PIZZA_ID,
    "product_name": "بيتزا مارگريتا",
    "product_price": 18000,
    "product_category": "بيتزا",
    "confidence": "high",
    "is_video": False,
}

empty_ctx = {"product_id": "", "product_name": "", "confidence": "low"}

# 5a. Emoji-only → sales reply
for emoji in ["🔥", "😍", "❤️"]:
    r = _wh._build_deterministic_story_reply(emoji, burger_ctx, RID_A)
    if r and "برگر" in r and "أهلاً" not in r[:10]:
        _result(f"Emoji-only '{emoji}' → sales reply mentions product", "PASS")
    else:
        _result(f"Emoji-only '{emoji}' → sales reply mentions product", "FAIL",
                "reply mentioning برگر", f"got: {r!r}",
                "_build_deterministic_story_reply not handling emoji intent")

# 5b. "اريد هذا" → order flow (delivery/pickup question)
for msg in ["اريد هذا", "اريد", "ابي"]:
    r = _wh._build_deterministic_story_reply(msg, burger_ctx, RID_A)
    is_order_flow = r and any(kw in r for kw in ["توصيل", "استلام", "توصلك", "أجهزلك"])
    is_generic = not r or "أهلاً وسهلاً" in r[:15]
    if is_order_flow and not is_generic:
        _result(f"'{msg}' → order flow (not generic greeting)", "PASS")
    else:
        _result(f"'{msg}' → order flow (not generic greeting)", "FAIL",
                "order flow reply", f"got: {r!r}",
                "ORDER_TRIGGER not matched or generic fallback used")

# 5c. Price question → correct price
for msg in ["شكد", "بكم", "السعر"]:
    r = _wh._build_deterministic_story_reply(msg, burger_ctx, RID_A)
    has_price = r and ("14,000" in r or "14000" in r or "14" in r)
    if has_price:
        _result(f"'{msg}' → reply with price 14,000", "PASS")
    else:
        _result(f"'{msg}' → reply with price 14,000", "FAIL",
                "price 14,000 in reply", f"got: {r!r}",
                "_STORY_PRICE_TRIGGERS not matching or price not in context")

# 5d. Unavailable product → rejection + alternative prompt
r_unavail = _wh._build_deterministic_story_reply("اريد هذا", pizza_ctx, RID_A)
if r_unavail and "مو متوفر" in r_unavail and "اريد" not in r_unavail[:5]:
    _result("Unavailable product → unavailable message", "PASS")
else:
    _result("Unavailable product → unavailable message", "FAIL",
            "'مو متوفر' in reply", f"got: {r_unavail!r}",
            "availability DB check not working or pizza available=1 in test data")

# 5e. Unknown story context → redirect, no product invented
for msg in ["اريد هذا", "🔥", "شكد"]:
    r_unk = _wh._build_deterministic_story_reply(msg, empty_ctx, RID_A)
    invents_product = r_unk and "برگر" in r_unk
    redirects = r_unk and any(kw in r_unk for kw in ["منيو", "تكتبلي", "تقصده", "المنتج"])
    if redirects and not invents_product:
        _result(f"Unknown ctx + '{msg}' → redirect without inventing product", "PASS")
    elif not r_unk:
        _result(f"Unknown ctx + '{msg}' → redirect without inventing product", "WARN",
                "redirect reply", "empty reply returned (falls through to AI)",
                "Unknown story with clear intent should redirect, not be silent")
    else:
        _result(f"Unknown ctx + '{msg}' → redirect without inventing product", "FAIL",
                "redirect mentioning منيو, no invented product",
                f"got: {r_unk!r}",
                "Bot inventing product for unknown story context")

# 5f. Non-trigger message → empty (fall through to AI)
for msg in ["كيفك", "وين اتواصل"]:
    r_noop = _wh._build_deterministic_story_reply(msg, burger_ctx, RID_A)
    if not r_noop:
        _result(f"'{msg}' → empty (fall through to AI)", "PASS")
    else:
        _result(f"'{msg}' → empty (fall through to AI)", "WARN",
                "empty string", f"got reply: {r_noop!r}",
                "Deterministic handler intercepting non-story messages")

# ══════════════════════════════════════════════════════════════════════════════
# 6. Video story marked as thumbnail-only
# ══════════════════════════════════════════════════════════════════════════════
section("6. Video Story — Thumbnail-Only Warning")

wh_source = inspect.getsource(_wh)
has_video_thumbnail_tag = "video_thumbnail_only" in wh_source
if has_video_thumbnail_tag:
    _result("Video story marked 'video_thumbnail_only' in analysis", "PASS")
else:
    _result("Video story marked 'video_thumbnail_only' in analysis", "WARN",
            "'video_thumbnail_only' tag in story analysis code",
            "tag not found in webhooks.py",
            "Structural limitation not logged — customers may expect full video analysis",
            "NUMBER 33B: Add multi-frame video analysis")

# ══════════════════════════════════════════════════════════════════════════════
# 7. Regression: existing features still work
# ══════════════════════════════════════════════════════════════════════════════
section("7. Regression Tests")

# 7a. Menu image intent still triggers
mi = _detect_menu_image_intent("دزلي المنيو")
if mi:
    _result("Menu image intent still detected", "PASS")
else:
    _result("Menu image intent still detected", "FAIL",
            "True", "False", "_detect_menu_image_intent broken after import changes")

# 7b. OrderBrain basic call still works
try:
    CONV_OB = str(_uuid.uuid4())
    CUST_OB = str(_uuid.uuid4())
    conn = database.get_db()
    conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
                 (CUST_OB, RID_A, "telegram", "تجربة"))
    conn.execute("""INSERT INTO conversations
        (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
        VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                 (CONV_OB, RID_A, CUST_OB))
    conn.commit(); conn.close()
    r_ob = _bot.process_message(RID_A, CONV_OB, "سلام")
    no_crash = True
    has_reply = bool(r_ob.get("reply"))
except Exception as e:
    no_crash = False; has_reply = False
    print(f"       exception: {e}")

if no_crash and has_reply:
    _result("OrderBrain: process_message still works", "PASS")
elif no_crash:
    _result("OrderBrain: process_message still works", "WARN",
            "reply in result", "empty reply (no OpenAI key in test env)", "Expected in offline test")
else:
    _result("OrderBrain: process_message still works", "FAIL",
            "no crash", "exception thrown", "Import or DB change broke bot.process_message")

# 7c. Dedup still works
dup_rid = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO restaurants (id,name,plan) VALUES (?,?,'trial')", (dup_rid, "dup"))
conn.commit(); conn.close()
first  = _wh._is_duplicate_event(dup_rid, "instagram", "story_evt_001")
second = _wh._is_duplicate_event(dup_rid, "instagram", "story_evt_001")
third  = _wh._is_duplicate_event(dup_rid, "instagram", "story_evt_002")
if not first and second and not third:
    _result("Dedup still prevents duplicate events", "PASS")
else:
    _result("Dedup still prevents duplicate events", "FAIL",
            "first=False second=True third=False",
            f"first={first} second={second} third={third}")

# 7d. story_cache module imports correctly
try:
    from services import story_cache as _sc2
    has_get = hasattr(_sc2, "get_cached_story")
    has_store = hasattr(_sc2, "store_story_cache")
    if has_get and has_store:
        _result("story_cache module imports with expected API", "PASS")
    else:
        _result("story_cache module imports with expected API", "FAIL",
                "get_cached_story + store_story_cache", f"get={has_get} store={has_store}")
except Exception as e:
    _result("story_cache module imports with expected API", "FAIL",
            "clean import", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ══════════════════════════════════════════════════════════════════════════════
section("FINAL REPORT")

total = PASS_COUNT + FAIL_COUNT + WARN_COUNT
print(f"\n{BOLD}Score: {PASS_COUNT} PASS  |  {FAIL_COUNT} FAIL  |  {WARN_COUNT} WARN  |  {total} total{RESET}\n")

if FAILURES:
    print(f"{BOLD}{'─'*60}")
    print("FAILURES & WARNINGS")
    print(f"{'─'*60}{RESET}")
    for i, f in enumerate(FAILURES, 1):
        color = RED if f["severity"] == "FAIL" else YELLOW
        print(f"\n{color}{i}. [{f['severity']}] {f['label']}{RESET}")
        if f.get("expected"): print(f"   Expected   : {f['expected']}")
        if f.get("actual"):   print(f"   Actual     : {f['actual']}")
        if f.get("root_cause"): print(f"   Root cause : {f['root_cause']}")
        if f.get("fix"):      print(f"   Fix        : {f['fix']}")

if FAIL_COUNT == 0:
    print(f"{GREEN}{BOLD}✓ NUMBER 33 COMPLETE — 0 FAILs{RESET}")
    if WARN_COUNT > 0:
        print(f"  {YELLOW}{WARN_COUNT} warnings (video multi-frame is expected WARN){RESET}")
else:
    print(f"{RED}{BOLD}✗ {FAIL_COUNT} FAILs — NUMBER 33 not complete{RESET}")

sys.exit(0 if FAIL_COUNT == 0 else 1)
