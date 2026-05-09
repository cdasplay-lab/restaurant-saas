#!/usr/bin/env python3
"""
NUMBER 34 — Hard Story Reply + Voice Stress Test
Goal: Expose truth about current bot capabilities. No fixes here — report only.
"""
import sys, os, json, time, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Colour helpers ────────────────────────────────────────────────────────────
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

def _result(label, status, expected, actual, root_cause="", fix=""):
    global PASS_COUNT, FAIL_COUNT, WARN_COUNT
    if status == "PASS":
        PASS_COUNT += 1
        print(f"  {GREEN}✓ PASS{RESET}  {label}")
    elif status == "FAIL":
        FAIL_COUNT += 1
        FAILURES.append({"label": label, "expected": expected, "actual": actual,
                          "root_cause": root_cause, "fix": fix, "severity": "FAIL"})
        print(f"  {RED}✗ FAIL{RESET}  {label}")
        print(f"         expected : {expected}")
        print(f"         actual   : {actual}")
    elif status == "WARN":
        WARN_COUNT += 1
        FAILURES.append({"label": label, "expected": expected, "actual": actual,
                          "root_cause": root_cause, "fix": fix, "severity": "WARN"})
        print(f"  {YELLOW}⚠ WARN{RESET}  {label}")
        print(f"         gap      : {actual}")

def section(title):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")

# ── Setup: temp-file SQLite DB for tests ─────────────────────────────────────
import tempfile, atexit
import database
_tmpdb = tempfile.mktemp(suffix=".test34.db")
atexit.register(lambda: os.unlink(_tmpdb) if os.path.exists(_tmpdb) else None)
database.DB_PATH = _tmpdb
database.init_db()  # creates all tables + runs migrations

# Seed restaurant + products
import uuid as _uuid
RID  = str(_uuid.uuid4())
UID  = str(_uuid.uuid4())
CONV = str(_uuid.uuid4())
CUST = str(_uuid.uuid4())

conn = database.get_db()
conn.execute("INSERT INTO restaurants (id,name,plan) VALUES (?,?,'professional')",
             (RID, "مطعم الاختبار"))
conn.execute("INSERT INTO users (id,restaurant_id,email,password_hash,name,role) VALUES (?,?,?,?,'Test Owner','owner')",
             (UID, RID, "test@test.com", "x"))

PRODUCTS = [
    (str(_uuid.uuid4()), RID, "برگر كلاسيك",  14000, "برگر",  1, ""),
    (str(_uuid.uuid4()), RID, "كولا",          2500,  "مشروبات", 1, ""),
    (str(_uuid.uuid4()), RID, "فرايز",         3500,  "جانبي",  1, ""),
    (str(_uuid.uuid4()), RID, "سلطة",          4000,  "جانبي",  1, ""),
    (str(_uuid.uuid4()), RID, "برگر دجاج",    13000, "برگر",  1, ""),
    (str(_uuid.uuid4()), RID, "بيتزا مارگريتا",18000, "بيتزا", 0, ""),  # unavailable
]
for p in PRODUCTS:
    conn.execute("INSERT INTO products (id,restaurant_id,name,price,category,available,image_url) VALUES (?,?,?,?,?,?,?)", p)

conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST, RID, "telegram", "علي"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV, RID, CUST))
conn.execute("INSERT INTO bot_config (id,restaurant_id,order_extraction_enabled) VALUES (?,?,1)",
             (str(_uuid.uuid4()), RID))
conn.commit()
conn.close()

from services import bot as _bot
from services.bot import _detect_menu_image_intent, _get_menu_images
from services import webhooks as _wh
import inspect

# ══════════════════════════════════════════════════════════════════════════════
# A. VOICE HARD TESTS
# ══════════════════════════════════════════════════════════════════════════════
section("A. Voice Hard Tests (simulate transcripts)")

# A1 — Clear voice order
section_label = "A1 — Clear voice order (full details)"
transcript = "اريد ٢ برگر و٢ كولا توصيل للكرادة اسمي علي ورقمي 07710005018"
try:
    result = _bot.process_message(RID, CONV, transcript)
    reply  = result.get("reply", "")
    order  = result.get("extracted_order") or {}
    items  = order.get("items", []) if order else []
    names  = [i.get("name","").lower() for i in items]
    has_burger   = any("برگر" in n or "برجر" in n or "burger" in n for n in names)
    has_cola     = any("كولا" in n or "cola" in n for n in names)
    has_delivery = any(kw in reply.lower() for kw in ["توصيل","توصل","كرادة","عنوان","طلبك"])
    has_name     = any(kw in reply.lower() for kw in ["علي","اسم"])
    no_crash = True
except Exception as e:
    has_burger = has_cola = has_delivery = has_name = no_crash = False
    reply = str(e)

if no_crash and has_burger and has_cola:
    _result(section_label, "PASS", "items extracted + reply sent", f"burger={has_burger} cola={has_cola}")
elif no_crash and (has_burger or has_cola):
    _result(section_label, "WARN", "all items extracted",
            f"partial extraction: burger={has_burger} cola={has_cola}",
            "OrderBrain may not extract all items in one turn",
            "NUMBER 32: multi-item extraction hardening")
else:
    _result(section_label, "FAIL", "برگر + كولا extracted",
            f"items={items} reply={reply[:80]}",
            "OrderBrain or _extract_order_from_message failed",
            "Review order extraction regex/AI prompt")

# Reset conv state for next test
conn = database.get_db()
try: conn.execute("UPDATE conversations SET order_brain_state='' WHERE id=?", (CONV,)); conn.commit()
finally: conn.close()

# A2 — Voice with missing fields
CONV2 = str(_uuid.uuid4())
CUST2 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST2, RID, "telegram", "زبون2"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV2, RID, CUST2))
conn.commit(); conn.close()

section_label = "A2 — Voice missing fields (no address/name/phone)"
transcript2 = "اريد برگرين وكولا"
try:
    r2 = _bot.process_message(RID, CONV2, transcript2)
    reply2 = r2.get("reply", "")
    bad_guess = any(kw in reply2 for kw in ["07","٠٧","بغداد","الكرادة","اسمك"])
    asks_missing = any(kw in reply2 for kw in ["توصيل","أكل محل","اسمك","رقمك","عنوان","كيف","وين"])
    no_restart   = "أهلاً" not in reply2[:20]
except Exception as e:
    reply2 = str(e); bad_guess = True; asks_missing = False; no_restart = False

if asks_missing and not bad_guess:
    _result(section_label, "PASS", "asks missing field without guessing", reply2[:80])
elif bad_guess:
    _result(section_label, "FAIL", "no hallucinated address/name",
            f"bot guessed fields: {reply2[:100]}",
            "OrderBrain or GPT hallucinating delivery details",
            "Add explicit 'never guess customer info' to system prompt")
else:
    _result(section_label, "WARN", "ask for missing fields",
            f"reply unclear: {reply2[:100]}",
            "Bot may be giving generic reply instead of targeted question",
            "Improve missing-field prompting in OrderBrain")

# A3 — Unclear voice
CONV3 = str(_uuid.uuid4())
CUST3 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST3, RID, "telegram", "زبون3"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV3, RID, CUST3))
conn.commit(); conn.close()

section_label = "A3 — Unclear voice / gibberish transcript"
transcript3 = "ممم اريد شنو يعني اه لا ادري"
try:
    r3 = _bot.process_message(RID, CONV3, transcript3)
    reply3 = r3.get("reply", "")
    order3 = r3.get("extracted_order") or {}
    no_fake_order = not order3.get("items")
    asks_clarify = any(kw in reply3 for kw in ["اكتب","اوضح","توضيح","تفصيل","مو واضح","فاهم","تقصد"])
except Exception as e:
    reply3 = str(e); no_fake_order = False; asks_clarify = False

if no_fake_order and asks_clarify:
    _result(section_label, "PASS", "no fake order + asks clarification", reply3[:80])
elif no_fake_order:
    _result(section_label, "WARN", "ask for clarification",
            f"no fake order ✓ but clarification unclear: {reply3[:100]}",
            "Bot didn't explicitly ask to clarify/rewrite message",
            "Add unclear-voice handling in exception playbook")
else:
    _result(section_label, "FAIL", "no order created from gibberish",
            f"extracted_order={order3}",
            "Order extraction creating items from meaningless tokens",
            "Add minimum confidence threshold in _extract_order_from_message")

# A4 — Oversized voice (no size check exists)
section_label = "A4 — Large voice file size limit check"
tg_source  = inspect.getsource(_wh._download_and_transcribe_telegram)
has_size_check  = "size" in tg_source.lower() or "duration" in tg_source.lower() or "limit" in tg_source.lower()
has_long_msg    = "طويل" in tg_source or "long" in tg_source.lower()
wa_source  = inspect.getsource(_wh._download_and_transcribe_whatsapp_voice)
has_wa_size_check = "size" in wa_source.lower() or "duration" in wa_source.lower()

if has_size_check or has_long_msg:
    _result(section_label, "PASS", "size/duration guard exists", "guard found in source")
else:
    _result(section_label, "FAIL",
            "reject voice > limit with friendly message",
            "no size/duration check in _download_and_transcribe_telegram or _wa variant",
            "Voice files up to 20MB accepted by Whisper API, but Telegram limits to 20MB. "
            "No guard for >2min messages causing slow responses.",
            "NUMBER 32: Add MAX_VOICE_SECONDS=120 guard before Whisper call")

# A4b — WhatsApp voice size check
if has_wa_size_check:
    _result("A4b — WhatsApp voice size limit", "PASS", "guard exists", "found")
else:
    _result("A4b — WhatsApp voice size limit", "WARN",
            "size guard in WA voice handler",
            "no size guard in _download_and_transcribe_whatsapp_voice",
            "Same issue as Telegram — no duration/size limit enforced",
            "NUMBER 32: Add same guard to WA handler")

# A5 — Noisy/unrelated transcript
CONV5 = str(_uuid.uuid4())
CUST5 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST5, RID, "telegram", "زبون5"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV5, RID, CUST5))
conn.commit(); conn.close()

section_label = "A5 — Noisy unrelated transcript (حديد سيارة تمرين)"
noise = "حديد سيارة تمرين سقف كاميرا"
try:
    r5 = _bot.process_message(RID, CONV5, noise)
    reply5 = r5.get("reply","")
    order5 = r5.get("extracted_order") or {}
    no_order = not order5.get("items")
    no_product_hallucination = not any(p[2] in reply5 for p in PRODUCTS if p[2] not in ["برگر كلاسيك"])
except Exception as e:
    reply5 = str(e); no_order = False; no_product_hallucination = False

if no_order:
    _result(section_label, "PASS", "no fake order from noise", reply5[:80])
else:
    _result(section_label, "FAIL", "no order extracted from unrelated words",
            f"order={order5}",
            "Order extraction too aggressive — matches Arabic words to products",
            "Add intent confidence gate before extraction")

# A6 — Frustrated customer
CONV6 = str(_uuid.uuid4())
CUST6 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST6, RID, "telegram", "زبون6"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV6, RID, CUST6))
conn.commit(); conn.close()

section_label = "A6 — Frustrated customer (غبي ليش ما فهمت)"
frustration_msg = "غبي ليش ما فهمت طلبي"
try:
    r6 = _bot.process_message(RID, CONV6, frustration_msg)
    reply6 = r6.get("reply","")
    has_apology   = any(kw in reply6 for kw in ["آسف","اسف","معذرة","عذراً","عذرا","سامحني","اسفين"])
    no_restart_gr = reply6.count("أهلاً") + reply6.count("اهلا") < 2
    not_rude_back = not any(kw in reply6 for kw in ["أنا بوت","لست","لا أفهم"])
except Exception as e:
    reply6 = str(e); has_apology = False; no_restart_gr = False; not_rude_back = False

if has_apology and no_restart_gr:
    _result(section_label, "PASS", "brief apology + no restart", reply6[:80])
elif not_rude_back and no_restart_gr:
    _result(section_label, "WARN", "apology detected",
            f"polite but no explicit apology: {reply6[:100]}",
            "Bot handles frustration but doesn't explicitly apologize",
            "Add 'عذراً' to frustration response template")
else:
    _result(section_label, "FAIL", "apology + no restart greeting",
            f"reply={reply6[:100]}",
            "Frustration detection may have missed or bot restarted flow",
            "Check detect_frustration() and EXCEPTION_PLAYBOOK frustration branch")

# A7 — Duplicate webhook deduplication
section_label = "A7 — Duplicate webhook deduplication"
dup_rid = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO restaurants (id,name,plan) VALUES (?,?,'trial')",
             (dup_rid, "dup test"))
conn.commit(); conn.close()

first  = _wh._is_duplicate_event(dup_rid, "telegram", "upd_999")
second = _wh._is_duplicate_event(dup_rid, "telegram", "upd_999")
third  = _wh._is_duplicate_event(dup_rid, "telegram", "upd_000")

if not first and second and not third:
    _result(section_label, "PASS", "first=False second=True third=False",
            f"first={first} second={second} third={third}")
else:
    _result(section_label, "FAIL", "idempotent dedup via processed_events table",
            f"first={first} second={second} third={third}",
            "processed_events table or UNIQUE constraint not working",
            "Check database schema for processed_events UNIQUE(restaurant_id,provider,event_id)")

# A8 — Voice "waiting" acknowledgment check
section_label = "A8 — Voice processing wait message (UX gap)"
tg_handle_source = inspect.getsource(_wh.handle_telegram)
has_wait_msg = any(kw in tg_handle_source for kw in ["لحظة","انتظر","جاري","processing"])
if has_wait_msg:
    _result(section_label, "PASS", "wait message sent during Whisper processing", "found")
else:
    _result(section_label, "WARN",
            "send 'جاري المعالجة...' before Whisper call",
            "no acknowledgment message sent while Whisper transcribes (3-5s silence to customer)",
            "No pre-processing acknowledgment — customer waits silently",
            "NUMBER 32: Send 'لحظة 🌷' before Whisper call then send real reply")

# ══════════════════════════════════════════════════════════════════════════════
# B. STORY REPLY TESTS
# ══════════════════════════════════════════════════════════════════════════════
section("B. Instagram/Facebook Story Reply Tests")

BURGER_PRODUCT = next(p for p in PRODUCTS if p[2] == "برگر كلاسيك")
BURGER_ID      = BURGER_PRODUCT[0]

# B1 — Story reply "اريد هذا" with matched product context
CONV_S1 = str(_uuid.uuid4())
CUST_S1 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_S1, RID, "instagram", "زبون_IG"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'instagram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_S1, RID, CUST_S1))
conn.commit(); conn.close()

story_ctx = "[العميل يرد على ستوري يعرض: برگر كلاسيك — 14,000 د.ع]\nسياق للبوت: هذا المنتج موجود في قائمتك. استغل الفرصة وابدأ flow البيع مباشرة."

section_label = "B1 — Story reply 'اريد هذا' with matched product context"
# Test through the deterministic layer (production path in _process_incoming)
from services.webhooks import _build_deterministic_story_reply
burger_ctx_b1 = {
    "product_id": BURGER_ID,
    "product_name": "برگر كلاسيك",
    "product_price": 14000,
    "product_category": "برگر",
    "confidence": "high",
    "is_video": False,
}
try:
    reply_b1 = _build_deterministic_story_reply("اريد هذا", burger_ctx_b1, RID)
    mentions_product = reply_b1 and ("برگر" in reply_b1 or "كلاسيك" in reply_b1)
    moves_to_order   = reply_b1 and any(kw in reply_b1 for kw in ["توصيل","استلام","أجهزلك","كم"])
    not_generic      = not reply_b1 or "أهلاً وسهلاً" not in reply_b1[:15]
except Exception as e:
    reply_b1 = str(e); mentions_product = False; moves_to_order = False; not_generic = False

if mentions_product and moves_to_order and not_generic:
    _result(section_label, "PASS", "deterministic: product + order flow, no generic greeting", reply_b1[:80])
elif mentions_product:
    _result(section_label, "WARN", "order flow reply",
            f"product mentioned but no delivery/pickup prompt: {reply_b1[:100]}",
            "Deterministic reply mentions product but does not move to order flow",
            "Add delivery/استلام to order intent reply template")
else:
    _result(section_label, "FAIL",
            "deterministic reply with product name + order flow",
            f"got: {reply_b1!r}",
            "_build_deterministic_story_reply not matching 'اريد هذا' pattern",
            "Check _STORY_ORDER_TRIGGERS and burger_ctx product_name")

# B2 — Story reply price question
CONV_S2 = str(_uuid.uuid4())
CUST_S2 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_S2, RID, "instagram", "زبون_IG2"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'instagram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_S2, RID, CUST_S2))
conn.commit(); conn.close()

section_label = "B2 — Story reply 'شكد سعره؟' → correct price"
msg_b2 = f"{story_ctx}\n\nرسالة الزبون: شكد سعره؟"
try:
    rb2 = _bot.process_message(RID, CONV_S2, msg_b2)
    reply_b2 = rb2.get("reply","")
    has_price = "14" in reply_b2 or "14,000" in reply_b2 or "١٤" in reply_b2
    no_wrong_price = "15" not in reply_b2 and "13" not in reply_b2
except Exception as e:
    reply_b2 = str(e); has_price = False; no_wrong_price = False

if has_price and no_wrong_price:
    _result(section_label, "PASS", "correct price 14,000 in reply", reply_b2[:80])
elif has_price:
    _result(section_label, "WARN", "price mentioned but may have noise", reply_b2[:100])
else:
    _result(section_label, "FAIL", "answer with price 14,000 from story context",
            f"reply: {reply_b2[:100]}",
            "Bot not extracting price from story_context string",
            "Add price to story context format more explicitly")

# B3 — Story reply with emoji only
CONV_S3 = str(_uuid.uuid4())
CUST_S3 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_S3, RID, "instagram", "زبون_IG3"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'instagram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_S3, RID, CUST_S3))
conn.commit(); conn.close()

section_label = "B3 — Story emoji-only reply '🔥'"
# Test through the deterministic layer (production path in _process_incoming)
try:
    reply_b3 = _build_deterministic_story_reply("🔥", burger_ctx_b1, RID)
    is_sales_reply   = reply_b3 and any(kw in reply_b3 for kw in ["برگر","كلاسيك","14","يعجبك","تحب","أجهزلك"])
    no_generic_greet = not reply_b3 or "أهلاً وسهلاً" not in reply_b3[:30]
except Exception as e:
    reply_b3 = str(e); is_sales_reply = False; no_generic_greet = False

if is_sales_reply and no_generic_greet:
    _result(section_label, "PASS", "deterministic: sales reply on emoji, not generic greeting", reply_b3[:80])
elif is_sales_reply:
    _result(section_label, "WARN", "sales reply without generic greeting",
            reply_b3[:100])
else:
    _result(section_label, "FAIL",
            "deterministic sales reply on emoji reaction with matched product",
            f"got: {reply_b3!r}",
            "_build_deterministic_story_reply not recognising 🔥 as emoji intent",
            "Check _is_emoji_only and emoji handling branch")

# B4 — Story product unavailable
CONV_S4 = str(_uuid.uuid4())
CUST_S4 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_S4, RID, "instagram", "زبون_IG4"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'instagram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_S4, RID, CUST_S4))
conn.commit(); conn.close()

section_label = "B4 — Story product unavailable → suggest alternative"
unavail_ctx = "[العميل يرد على ستوري يعرض: بيتزا مارگريتا — 18,000 د.ع]\nسياق للبوت: هذا المنتج موجود في قائمتك. استغل الفرصة وابدأ flow البيع مباشرة."
msg_b4 = f"{unavail_ctx}\n\nرسالة الزبون: اريد هذا"
try:
    rb4 = _bot.process_message(RID, CONV_S4, msg_b4)
    reply_b4 = rb4.get("reply","")
    not_fake_avail = not any(kw in reply_b4 for kw in ["حاضر","جاهز","أحضر لك","أجهزلك بيتزا"])
    offers_alt     = any(kw in reply_b4 for kw in ["برگر","دجاج","عندنا","بديل","منيو"])
except Exception as e:
    reply_b4 = str(e); not_fake_avail = True; offers_alt = False

conn2 = database.get_db()
pizza_avail = conn2.execute("SELECT available FROM products WHERE name='بيتزا مارگريتا' AND restaurant_id=?", (RID,)).fetchone()
conn2.close()

if pizza_avail and pizza_avail["available"] == 0:
    if not_fake_avail and offers_alt:
        _result(section_label, "PASS", "correctly rejects unavailable + suggests alternative", reply_b4[:80])
    elif not_fake_avail:
        _result(section_label, "WARN", "reject unavailable but offer alternative",
                f"rejected correctly but no alternative offered: {reply_b4[:100]}",
                "Bot doesn't cross-reference unavailable status from story context",
                "NUMBER 33: Add unavailable product check before story sales flow")
    else:
        _result(section_label, "FAIL", "not offer unavailable product as available",
                f"bot pretended product is available: {reply_b4[:100]}",
                "story_context doesn't include availability flag — GPT assumes available",
                "Inject availability status into story_context string")
else:
    _result(section_label, "WARN", "unavailable product test", "pizza product not found/wrong setup", "test setup issue")

# B5 — Story reply unmatched/no context
CONV_S5 = str(_uuid.uuid4())
CUST_S5 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_S5, RID, "instagram", "زبون_IG5"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'instagram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_S5, RID, CUST_S5))
conn.commit(); conn.close()

section_label = "B5 — Story reply with no product match + 'اريد هذا'"
no_match_ctx = "[العميل يرد على ستوري للمطعم — رحّب به وابدأ محادثة البيع]"
msg_b5 = f"{no_match_ctx}\n\nرسالة الزبون: اريد هذا"
try:
    rb5 = _bot.process_message(RID, CONV_S5, msg_b5)
    reply_b5 = rb5.get("reply","")
    no_hallucinate = not any(p[2] in reply_b5 and p[2] not in ["برگر كلاسيك","برگر دجاج"] for p in PRODUCTS)
    asks_or_menu   = any(kw in reply_b5 for kw in ["منيو","قائمة","تريد","اختار","شو","شنو"])
except Exception as e:
    reply_b5 = str(e); no_hallucinate = False; asks_or_menu = False

if asks_or_menu:
    _result(section_label, "PASS", "asks clarification or sends menu on unknown story", reply_b5[:80])
else:
    _result(section_label, "WARN", "ask clarification when product unknown",
            f"reply: {reply_b5[:100]}",
            "Bot replies but doesn't guide customer to menu/clarification",
            "Add explicit fallback for zero-context story replies")

# B6 — Story cache check
section_label = "B6 — Story analysis cache (100 replies same story_id)"
wh_source_full = inspect.getsource(_wh)
has_story_cache = "_story_cache" in wh_source_full or "story_cache" in wh_source_full or "lru_cache" in wh_source_full
if has_story_cache:
    _result(section_label, "PASS", "story cache exists", "cache implementation found")
else:
    _result(section_label, "FAIL",
            "cache story Vision API result by story_id",
            "no story cache found — every reply triggers Vision API (Pass1+Pass2 = 2 API calls × 100 users = 200 calls)",
            "_analyze_story() called fresh per message with no memoization",
            "NUMBER 33: Add dict cache keyed by (restaurant_id, story_id) with TTL=24h")

# B7 — Video story thumbnail limitation
section_label = "B7 — Video story: only thumbnail analyzed (structural gap)"
fetch_src = inspect.getsource(_wh._fetch_story_media)
only_thumbnail = "thumbnail" in fetch_src.lower() and "frame" not in fetch_src.lower()
if only_thumbnail:
    _result(section_label, "WARN",
            "analyze full video frames for product matching",
            "only first thumbnail extracted from video story — product shown at 0:05 may be missed",
            "Structural limitation: Vision API processes single image, not video frames",
            "FUTURE: Extract 3 keyframes from video (start/mid/end) and analyze best match")
else:
    _result(section_label, "PASS", "video analysis beyond thumbnail", "multi-frame found")

# ══════════════════════════════════════════════════════════════════════════════
# C. CROSS-CHANNEL TESTS
# ══════════════════════════════════════════════════════════════════════════════
section("C. Cross-Channel Integrity Tests")

# C1 — Telegram voice path exists
section_label = "C1 — Telegram voice → Whisper path exists"
tg_has_voice = "whisper" in tg_source.lower() or "transcription" in tg_source.lower()
if tg_has_voice:
    _result(section_label, "PASS", "Whisper call in Telegram handler", "found")
else:
    _result(section_label, "FAIL", "Whisper transcription in TG handler", "not found", "Missing", "Add Whisper call")

# C2 — WhatsApp voice path exists
section_label = "C2 — WhatsApp voice → Whisper path exists"
wa_has_voice = "whisper" in wa_source.lower() or "transcription" in wa_source.lower()
if wa_has_voice:
    _result(section_label, "PASS", "Whisper call in WhatsApp handler", "found")
else:
    _result(section_label, "FAIL", "Whisper transcription in WA handler", "not found", "Missing", "Add Whisper call")

# C3 — Instagram story reply path
section_label = "C3 — Instagram story reply detection in handler"
ig_src = inspect.getsource(_wh.handle_instagram_live_message) if hasattr(_wh, "handle_instagram_live_message") else ""
if not ig_src:
    # Try finding in source
    ig_src = "\n".join(l for l in wh_source_full.split("\n") if "story" in l.lower())
has_story_detection = "reply_to" in ig_src and "story" in ig_src
if has_story_detection:
    _result(section_label, "PASS", "story reply detected in IG handler", "found")
else:
    _result(section_label, "FAIL", "story reply_to detection", "not found", "Missing", "Add story detection")

# C4 — Facebook story fallback
section_label = "C4 — Facebook story/audio safe fallback"
fb_src = wh_source_full
has_fb_voice_fallback = "facebook" in fb_src.lower() and ("audio" in fb_src.lower() or "voice" in fb_src.lower())
if has_fb_voice_fallback:
    _result(section_label, "PASS", "Facebook handler has audio/voice path", "found")
else:
    _result(section_label, "WARN", "Facebook audio fallback",
            "no explicit Facebook audio handling found",
            "Facebook Messenger voice clips may not be transcribed",
            "NUMBER 35: Add FB voice handling similar to WA")

# C5 — Menu image priority over AI text
section_label = "C5 — 'دزلي المنيو' triggers image not generic AI"
CONV_C5 = str(_uuid.uuid4())
CUST_C5 = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_C5, RID, "telegram", "زبون_c5"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_C5, RID, CUST_C5))
# add a menu image
conn.execute("INSERT INTO menu_images (id,restaurant_id,title,image_url,is_active,sort_order) VALUES (?,?,?,?,1,0)",
             (str(_uuid.uuid4()), RID, "المنيو الكامل", "https://example.com/menu.jpg"))
conn.commit(); conn.close()

rc5 = _bot.process_message(RID, CONV_C5, "دزلي المنيو")
has_media = bool(rc5.get("media"))
is_menu_intent = _detect_menu_image_intent("دزلي المنيو")

if has_media and is_menu_intent:
    _result(section_label, "PASS", "media returned + menu intent detected", f"media_count={len(rc5['media'])}")
elif is_menu_intent and not has_media:
    _result(section_label, "WARN", "menu image returned when active images exist",
            "intent detected but no media in result — check menu_images table seeding",
            "Menu image fetch may have failed or DB isolation issue in test",
            "Verify menu_images query uses correct restaurant_id")
else:
    _result(section_label, "FAIL", "menu image intent + media in result",
            f"intent={is_menu_intent} media={has_media}",
            "Menu image intent not triggering image delivery",
            "Check _detect_menu_image_intent and _get_menu_images chain")

# ══════════════════════════════════════════════════════════════════════════════
# D. ORDERBRAIN REGRESSION
# ══════════════════════════════════════════════════════════════════════════════
section("D. OrderBrain Multi-Turn Regression")

CONV_OB = str(_uuid.uuid4())
CUST_OB = str(_uuid.uuid4())
conn = database.get_db()
conn.execute("INSERT INTO customers (id,restaurant_id,platform,name) VALUES (?,?,?,?)",
             (CUST_OB, RID, "telegram", "أحمد"))
conn.execute("""INSERT INTO conversations
    (id,restaurant_id,customer_id,channel,mode,status,created_at,updated_at)
    VALUES (?,?,?,'telegram','bot','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
             (CONV_OB, RID, CUST_OB))
conn.commit(); conn.close()

conversation = [
    ("سلام عليكم",             "greeting"),
    ("اريد اطلب",              "order_intent"),
    ("٢ برگر و٢ كولا وفرايز واحد وسلطة", "items"),
    ("توصيل",                  "delivery_type"),
    ("الكرادة شارع الاطباء",   "address"),
    ("أحمد أغا",               "name"),
    ("07710005018",            "phone"),
    ("اي",                     "confirm"),
]

ob_replies = []
ob_failed_turns = []

for turn_msg, turn_label in conversation:
    try:
        r_ob = _bot.process_message(RID, CONV_OB, turn_msg)
        ob_replies.append((turn_label, r_ob.get("reply",""), r_ob.get("extracted_order")))
    except Exception as e:
        ob_failed_turns.append((turn_label, str(e)))
        ob_replies.append((turn_label, f"ERROR:{e}", None))

# Evaluate final state
final_reply  = ob_replies[-1][1] if ob_replies else ""
final_order  = ob_replies[-1][2] if ob_replies else None

repeated_greeting = sum(1 for _,r,_ in ob_replies if "أهلاً وسهلاً" in r or "مرحباً" in r[:20]) > 1
no_reset          = not repeated_greeting
has_confirmation  = any(kw in final_reply for kw in ["تأكيد","✅","تم","مكتمل","شكراً","حاضر"])
total_burgers     = 0
total_colas       = 0
has_order_created = False

conn_ob = database.get_db()
orders = conn_ob.execute("SELECT * FROM orders WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 3", (RID,)).fetchall()
conn_ob.close()
if orders:
    has_order_created = True

section_label = "D1 — OrderBrain: no reset across 8 turns"
if no_reset:
    _result(section_label, "PASS", "no repeated greeting across turns", f"{len(ob_replies)} turns processed")
else:
    _result(section_label, "FAIL", "no repeated greeting/reset mid-conversation",
            f"greeting appeared {sum(1 for _,r,_ in ob_replies if 'أهلاً' in r)} times",
            "Bot restarted conversation flow mid-order",
            "Check OrderBrain session restore logic")

section_label = "D2 — OrderBrain: items captured (برگر×2, كولا×2, فرايز, سلطة)"
items_turn = ob_replies[2][2]  # items turn
if items_turn and items_turn.get("items"):
    item_names = [i.get("name","") for i in items_turn["items"]]
    has_b = any("برگر" in n or "برجر" in n for n in item_names)
    has_c = any("كولا" in n for n in item_names)
    if has_b and has_c:
        _result(section_label, "PASS", "برگر + كولا extracted from items turn", f"items={item_names}")
    else:
        _result(section_label, "WARN", "all 4 items captured",
                f"only got: {item_names}",
                "Partial extraction of multi-item orders",
                "NUMBER 32: Strengthen multi-item extraction")
else:
    _result(section_label, "WARN", "items extracted in items turn",
            f"no extracted_order in items turn reply={ob_replies[2][1][:80]}",
            "OrderBrain may collect items across turns rather than single extraction",
            "This may be expected behavior — check ob_session items accumulation")

section_label = "D3 — OrderBrain: order created in DB after confirmation"
if has_order_created:
    dup_orders = len(orders)
    if dup_orders == 1:
        _result(section_label, "PASS", "exactly 1 order created", f"order_id={orders[0]['id'][:8]}")
    else:
        _result(section_label, "WARN", "exactly 1 order (no duplicates)",
                f"{dup_orders} orders found in DB",
                "Possible duplicate order creation",
                "Check order dedup logic in _create_order_from_session")
else:
    _result(section_label, "WARN", "order created in DB after confirmation",
            f"no order in DB — confirm turn reply: {final_reply[:100]}",
            "Order may not have been confirmed or extracted_order missing required fields",
            "Check OrderBrain confirmation_status flow")

if ob_failed_turns:
    _result("D4 — OrderBrain: no crashes", "FAIL",
            "all 8 turns process without exception",
            f"crashed on turns: {[t for t,_ in ob_failed_turns]}",
            "Exception in process_message",
            "Fix exceptions in failing turns")
else:
    _result("D4 — OrderBrain: no crashes across all turns", "PASS",
            "no exceptions", f"all {len(conversation)} turns succeeded")

# ══════════════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ══════════════════════════════════════════════════════════════════════════════
section("FINAL REPORT")

total = PASS_COUNT + FAIL_COUNT + WARN_COUNT
print(f"\n{BOLD}Score: {PASS_COUNT} PASS  |  {FAIL_COUNT} FAIL  |  {WARN_COUNT} WARN  |  {total} total{RESET}\n")

if FAILURES:
    print(f"{BOLD}{'─'*60}")
    print(f"FAILURES & WARNINGS DETAIL")
    print(f"{'─'*60}{RESET}")
    for i, f in enumerate(FAILURES, 1):
        color = RED if f["severity"] == "FAIL" else YELLOW
        print(f"\n{color}{i}. [{f['severity']}] {f['label']}{RESET}")
        print(f"   Expected   : {f['expected']}")
        print(f"   Actual     : {f['actual']}")
        print(f"   Root cause : {f['root_cause']}")
        print(f"   Fix        : {f['fix']}")

# Top 5 weaknesses
fails_only  = [f for f in FAILURES if f["severity"] == "FAIL"]
warns_only  = [f for f in FAILURES if f["severity"] == "WARN"]

print(f"\n{BOLD}{'─'*60}")
print("TOP 5 BOT WEAKNESSES")
print(f"{'─'*60}{RESET}")
weaknesses = (fails_only + warns_only)[:5]
for i, w in enumerate(weaknesses, 1):
    icon = "🔴" if w["severity"] == "FAIL" else "🟡"
    print(f" {i}. {icon} {w['label']}")
    print(f"    → {w['fix']}")

# Launch blockers
print(f"\n{BOLD}{'─'*60}")
print("LAUNCH BLOCKERS (FAIL only)")
print(f"{'─'*60}{RESET}")
blockers = [f for f in FAILURES if f["severity"] == "FAIL"]
if blockers:
    for b in blockers:
        print(f" 🔴 {b['label']}")
        print(f"    Fix: {b['fix']}")
else:
    print(f" {GREEN}No hard blockers — all FAILs are fixable pre-launch{RESET}")

print(f"\n{BOLD}{'─'*60}")
print("CAN WAIT (WARN only)")
print(f"{'─'*60}{RESET}")
for w in warns_only:
    print(f" 🟡 {w['label']}")

# Recommendation
print(f"\n{BOLD}{'─'*60}")
print("RECOMMENDED NEXT NUMBER")
print(f"{'─'*60}{RESET}")

has_cache_fail  = any("cache" in f["label"].lower() or "cache" in f["fix"].lower() for f in fails_only)
has_voice_fail  = any("voice" in f["label"].lower() or "A4" in f["label"] for f in fails_only)
has_story_fail  = any("story" in f["label"].lower() or "B" in f["label"][:2] for f in fails_only)

if has_cache_fail or has_story_fail:
    rec = "NUMBER 33 — Story Reply Cache + Unavailability Guard"
    reason = "Story cache missing = FAIL (200 API calls per viral post). Story unavailability also needs fixing."
elif has_voice_fail:
    rec = "NUMBER 32 — Voice UX Upgrade (size guard + wait message)"
    reason = "Voice has no size/duration guard and no wait acknowledgment."
else:
    rec = "NUMBER 35 — Story/Voice Launch Hardening"
    reason = "No hard blockers found. Harden edge cases before go-live."

print(f"\n  {BOLD}{CYAN}→ {rec}{RESET}")
print(f"  {reason}\n")
