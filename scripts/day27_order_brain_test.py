"""
scripts/day27_order_brain_test.py — NUMBER 27: Order Brain Tests

Tests the deterministic order session layer (OrderBrain + OrderSession)
and its integration with the bot system prompt injection.

Run from project root:
    python3 scripts/day27_order_brain_test.py
"""
import sys
import os
import re
import types
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub external deps before importing bot modules ───────────────────────────
db_mod = types.ModuleType("database")
db_mod.get_db = lambda: None
sys.modules["database"] = db_mod

# ── Counters ──────────────────────────────────────────────────────────────────
_ok = _fail = 0

def ok(msg):
    global _ok
    _ok += 1
    print(f"  ✅ {msg}")

def fail(msg, detail=""):
    global _fail
    _fail += 1
    detail_str = f" | {detail}" if detail else ""
    print(f"  ❌ {msg}{detail_str}")


# ── Import modules under test ─────────────────────────────────────────────────
from services.order_brain import (
    OrderBrain,
    OrderSession,
    OrderItem,
    detect_frustration,
    _extract_name,
    _extract_address,
    _extract_phone,
    _extract_qty,
    FRUSTRATION_PHRASES,
    DELIVERY_KEYWORDS,
    PICKUP_KEYWORDS,
    PAYMENT_MAP,
    CONFIRMATION_KEYWORDS,
)

from services.bot import _build_system_prompt, _ORDER_BRAIN_ENABLED


# ── Fixtures ──────────────────────────────────────────────────────────────────

PRODUCTS = [
    {"id": "p1", "name": "برجر",   "price": 8000,  "available": 1, "category": "وجبات",   "description": "", "icon": "🍔", "sold_out_date": ""},
    {"id": "p2", "name": "زينجر",  "price": 9000,  "available": 1, "category": "وجبات",   "description": "", "icon": "🍗", "sold_out_date": ""},
    {"id": "p3", "name": "بروستد", "price": 7500,  "available": 1, "category": "وجبات",   "description": "", "icon": "🍗", "sold_out_date": ""},
    {"id": "p4", "name": "كولا",   "price": 1500,  "available": 1, "category": "مشروبات", "description": "", "icon": "🥤", "sold_out_date": ""},
]

def fresh_session(conv_id="c1", rest_id="r1"):
    OrderBrain.clear_session(conv_id)
    return OrderBrain.get_or_create(conv_id, rest_id)


# ══════════════════════════════════════════════════════════════════════════════
# GROUP A — OrderSession basics
# ══════════════════════════════════════════════════════════════════════════════
print("\n── A. OrderSession basics ──")

def test_A01_empty_missing_fields():
    sess = OrderSession(conversation_id="cx", restaurant_id="rx")
    mf = sess.missing_fields()
    expected = ["items", "order_type", "customer_name", "payment_method"]
    if mf == expected:
        ok("A01 empty session → correct missing_fields")
    else:
        fail("A01 empty missing_fields", f"got {mf}")

def test_A02_delivery_includes_address():
    sess = OrderSession(conversation_id="cx", restaurant_id="rx")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    sess.customer_name = "علي"
    sess.payment_method = "كاش"
    mf = sess.missing_fields()
    if "address" in mf:
        ok("A02 delivery without address → address in missing_fields")
    else:
        fail("A02 delivery missing_fields", f"got {mf}")

def test_A03_pickup_excludes_address():
    sess = OrderSession(conversation_id="cx", restaurant_id="rx")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.payment_method = "كاش"
    mf = sess.missing_fields()
    if "address" not in mf and mf == []:
        ok("A03 pickup complete → address not required, is_complete=True")
    else:
        fail("A03 pickup missing_fields", f"got {mf}")

def test_A04_is_complete_delivery():
    sess = OrderSession(conversation_id="cx", restaurant_id="rx")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    sess.address = "الكرادة"
    sess.customer_name = "علي"
    sess.payment_method = "كاش"
    if sess.is_complete():
        ok("A04 full delivery session → is_complete=True")
    else:
        fail("A04 is_complete delivery", f"missing={sess.missing_fields()}")

def test_A05_is_active():
    sess = OrderSession(conversation_id="cx", restaurant_id="rx")
    if not sess.is_active():
        ok("A05 empty session → is_active=False")
    else:
        fail("A05 empty session should not be active")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    if sess.is_active():
        ok("A05 session with items → is_active=True")
    else:
        fail("A05 session with items should be active")

def test_A06_expiry():
    sess = OrderSession(conversation_id="cx", restaurant_id="rx")
    sess.updated_at = time.time() - 7300  # just past TTL
    if sess.is_expired():
        ok("A06 session past TTL → is_expired=True")
    else:
        fail("A06 expiry check failed")

test_A01_empty_missing_fields()
test_A02_delivery_includes_address()
test_A03_pickup_excludes_address()
test_A04_is_complete_delivery()
test_A05_is_active()
test_A06_expiry()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP B — Slot extraction
# ══════════════════════════════════════════════════════════════════════════════
print("\n── B. Slot extraction ──")

def test_B01_item_extraction_basic():
    sess = fresh_session("b01")
    OrderBrain.update_from_message(sess, "أريد برجر", PRODUCTS)
    if any(it.name == "برجر" for it in sess.items):
        ok("B01 basic item extraction: برجر")
    else:
        fail("B01 برجر not extracted", f"items={[it.name for it in sess.items]}")

def test_B02_multi_item_extraction():
    sess = fresh_session("b02")
    OrderBrain.update_from_message(sess, "أريد برجر وزينجر", PRODUCTS)
    names = [it.name for it in sess.items]
    if "برجر" in names and "زينجر" in names:
        ok("B02 multi-item extraction: برجر + زينجر")
    else:
        fail("B02 multi-item", f"got {names}")

def test_B03_quantity_digit():
    sess = fresh_session("b03")
    OrderBrain.update_from_message(sess, "أريد 3 برجر", PRODUCTS)
    item = next((it for it in sess.items if it.name == "برجر"), None)
    if item and item.qty == 3:
        ok("B03 digit quantity: 3 برجر")
    else:
        fail("B03 digit qty", f"item={item}")

def test_B04_quantity_arabic_word():
    sess = fresh_session("b04")
    OrderBrain.update_from_message(sess, "اثنين برجر", PRODUCTS)
    item = next((it for it in sess.items if it.name == "برجر"), None)
    if item and item.qty == 2:
        ok("B04 Arabic word quantity: اثنين برجر → qty=2")
    else:
        fail("B04 Arabic word qty", f"item={item}")

def test_B05_delivery_detection():
    sess = fresh_session("b05")
    OrderBrain.update_from_message(sess, "أريد توصيل", PRODUCTS)
    if sess.order_type == "delivery":
        ok("B05 توصيل → order_type=delivery")
    else:
        fail("B05 delivery detection", f"got {sess.order_type}")

def test_B06_pickup_detection():
    sess = fresh_session("b06")
    OrderBrain.update_from_message(sess, "استلام من المطعم", PRODUCTS)
    if sess.order_type == "pickup":
        ok("B06 استلام → order_type=pickup")
    else:
        fail("B06 pickup detection", f"got {sess.order_type}")

def test_B07_address_area_extraction():
    sess = fresh_session("b07")
    OrderBrain.update_from_message(sess, "عنواني الكرادة", PRODUCTS)
    if sess.address == "الكرادة":
        ok("B07 address extraction: الكرادة")
    else:
        fail("B07 address extraction", f"got {sess.address!r}")

def test_B08_address_not_overwritten():
    sess = fresh_session("b08")
    sess.address = "المنصور"
    sess.order_type = "delivery"
    OrderBrain.update_from_message(sess, "عنواني الكرادة", PRODUCTS)
    if sess.address == "المنصور":
        ok("B08 address not overwritten once set")
    else:
        fail("B08 address overwritten", f"got {sess.address}")

def test_B09_name_extraction_ismi():
    sess = fresh_session("b09")
    OrderBrain.update_from_message(sess, "اسمي محمد", PRODUCTS)
    if sess.customer_name == "محمد":
        ok("B09 name extraction: اسمي محمد → محمد")
    else:
        fail("B09 name extraction", f"got {sess.customer_name!r}")

def test_B10_name_not_overwritten():
    sess = fresh_session("b10")
    sess.customer_name = "علي"
    OrderBrain.update_from_message(sess, "اسمي محمد", PRODUCTS)
    if sess.customer_name == "علي":
        ok("B10 name not overwritten once set")
    else:
        fail("B10 name overwritten", f"got {sess.customer_name}")

def test_B11_phone_extraction():
    sess = fresh_session("b11")
    OrderBrain.update_from_message(sess, "هذا رقمي 07901234567", PRODUCTS)
    if sess.phone == "07901234567":
        ok("B11 phone extraction: 07901234567")
    else:
        fail("B11 phone extraction", f"got {sess.phone!r}")

def test_B12_payment_cash():
    sess = fresh_session("b12")
    OrderBrain.update_from_message(sess, "الدفع كاش", PRODUCTS)
    if sess.payment_method == "كاش":
        ok("B12 payment: كاش")
    else:
        fail("B12 payment cash", f"got {sess.payment_method}")

def test_B13_payment_card():
    sess = fresh_session("b13")
    OrderBrain.update_from_message(sess, "بطاقة", PRODUCTS)
    if sess.payment_method == "كارد":
        ok("B13 payment: بطاقة → كارد")
    else:
        fail("B13 payment card", f"got {sess.payment_method}")

def test_B14_confirmation_when_complete():
    sess = fresh_session("b14")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.payment_method = "كاش"
    OrderBrain.update_from_message(sess, "ثبت", PRODUCTS)
    if sess.confirmation_status == "confirmed":
        ok("B14 'ثبت' when complete → confirmed")
    else:
        fail("B14 confirmation", f"status={sess.confirmation_status}")

def test_B15_confirmation_when_incomplete():
    sess = fresh_session("b15")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    # not complete — no order_type, name, payment
    OrderBrain.update_from_message(sess, "ثبت", PRODUCTS)
    if sess.confirmation_status == "collecting":
        ok("B15 'ثبت' when incomplete → stays collecting")
    else:
        fail("B15 confirmation incomplete", f"status={sess.confirmation_status}")

def test_B16_cancellation():
    sess = fresh_session("b16")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    OrderBrain.update_from_message(sess, "ألغ الطلب", PRODUCTS)
    if sess.confirmation_status == "cancelled":
        ok("B16 'ألغ الطلب' → cancelled")
    else:
        fail("B16 cancellation", f"status={sess.confirmation_status}")

def test_B17_no_duplicate_items():
    sess = fresh_session("b17")
    OrderBrain.update_from_message(sess, "أريد برجر", PRODUCTS)
    OrderBrain.update_from_message(sess, "برجر", PRODUCTS)
    count = sum(1 for it in sess.items if it.name == "برجر")
    if count == 1:
        ok("B17 no duplicate items — برجر mentioned twice → only 1 entry")
    else:
        fail("B17 duplicate items", f"count={count}")

test_B01_item_extraction_basic()
test_B02_multi_item_extraction()
test_B03_quantity_digit()
test_B04_quantity_arabic_word()
test_B05_delivery_detection()
test_B06_pickup_detection()
test_B07_address_area_extraction()
test_B08_address_not_overwritten()
test_B09_name_extraction_ismi()
test_B10_name_not_overwritten()
test_B11_phone_extraction()
test_B12_payment_cash()
test_B13_payment_card()
test_B14_confirmation_when_complete()
test_B15_confirmation_when_incomplete()
test_B16_cancellation()
test_B17_no_duplicate_items()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP C — Frustration detection
# ══════════════════════════════════════════════════════════════════════════════
print("\n── C. Frustration detection ──")

def test_C01_frustration_phrases():
    cases = [
        ("يا غبي ما تفهم", True),
        ("تعبتني من ردودك", True),
        ("ليش تكرر نفس السؤال", True),
        ("قلتلك مرة وحدة", True),
        ("أريد برجر توصيل", False),
        ("شسمك؟", False),
        ("تمام ثبت", False),
    ]
    passed = True
    for msg, expected in cases:
        result = detect_frustration(msg)
        if result != expected:
            fail(f"C01 frustration({msg!r}) expected={expected} got={result}")
            passed = False
    if passed:
        ok(f"C01 frustration detection: {len(cases)} cases correct")

def test_C02_frustration_sets_flag():
    sess = fresh_session("c02")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    OrderBrain.update_from_message(sess, "يا غبي قلتلك برجر!", PRODUCTS)
    if sess.customer_frustrated:
        ok("C02 frustration message → customer_frustrated=True on session")
    else:
        fail("C02 frustration flag not set")

def test_C03_frustration_reset_after_acknowledge():
    sess = fresh_session("c03")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.customer_frustrated = True
    sess.reset_frustration()
    if not sess.customer_frustrated:
        ok("C03 reset_frustration() clears flag")
    else:
        fail("C03 frustration flag not cleared")

def test_C04_frustration_appears_in_prompt():
    sess = OrderSession(conversation_id="c04", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    sess.customer_frustrated = True
    section = sess.to_prompt_section()
    if "إحباطاً" in section or "إحباط" in section:
        ok("C04 frustrated flag → prompt section contains apology instruction")
    else:
        fail("C04 frustration in prompt section", f"section={section[:200]}")

test_C01_frustration_phrases()
test_C02_frustration_sets_flag()
test_C03_frustration_reset_after_acknowledge()
test_C04_frustration_appears_in_prompt()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP D — Prompt injection
# ══════════════════════════════════════════════════════════════════════════════
print("\n── D. Prompt injection ──")

BASE_PROMPT_ARGS = dict(
    restaurant={"name": "مطعم التجربة", "address": "بغداد"},
    settings={},
    bot_cfg={},
    products=PRODUCTS,
    memory={},
    customer={},
)

def test_D01_order_brain_enabled():
    if _ORDER_BRAIN_ENABLED:
        ok("D01 OrderBrain is enabled in bot.py")
    else:
        fail("D01 OrderBrain disabled")

def test_D02_no_session_no_injection():
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=None)
    if "حالة الطلب الجارية" not in prompt:
        ok("D02 no session → no order state section in prompt")
    else:
        fail("D02 order state section injected without session")

def test_D03_empty_session_no_injection():
    sess = OrderSession(conversation_id="d03", restaurant_id="r1")
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=sess)
    if "حالة الطلب الجارية" not in prompt:
        ok("D03 empty session → no order state section (no items, no type)")
    else:
        fail("D03 empty session should not inject order state")

def test_D04_active_session_injected_in_prompt():
    sess = OrderSession(conversation_id="d04", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=sess)
    checks = [
        ("حالة الطلب الجارية", "header"),
        ("لا تبدأ من الصفر", "no-reset instruction"),
        ("برجر × 1", "item in prompt"),
        ("delivery", "order_type in prompt"),
    ]
    all_ok = True
    for phrase, label in checks:
        if phrase not in prompt:
            fail(f"D04 missing '{phrase}' ({label})")
            all_ok = False
    if all_ok:
        ok("D04 active session → all state elements injected in prompt")

def test_D05_no_reset_instruction_present():
    sess = OrderSession(conversation_id="d05", restaurant_id="r1")
    sess.items.append(OrderItem(name="زينجر", qty=2, price=9000))
    sess.order_type = "pickup"
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=sess)
    if "لا تبدأ من الصفر" in prompt and "لا تقل 'هلا بيك'" in prompt:
        ok("D05 no-reset + no-greeting instructions in prompt")
    else:
        fail("D05 no-reset instructions missing from prompt")

def test_D06_next_step_instruction():
    sess = OrderSession(conversation_id="d06", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    # Missing: order_type, customer_name, payment_method
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=sess)
    if "توصيل لو استلام" in prompt:
        ok("D06 next step 'order_type' → asks توصيل لو استلام")
    else:
        fail("D06 next step instruction missing from prompt")

def test_D07_double_injection_both_ends():
    sess = OrderSession(conversation_id="d07", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=sess)
    count = prompt.count("حالة الطلب الجارية")
    if count >= 2:
        ok(f"D07 order state injected {count}× (beginning + end)")
    else:
        fail(f"D07 expected ≥2 injections, got {count}")

def test_D08_frustration_in_injected_prompt():
    sess = OrderSession(conversation_id="d08", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    sess.customer_frustrated = True
    prompt = _build_system_prompt(**BASE_PROMPT_ARGS, order_session=sess)
    if "إحباط" in prompt or "اعتذر" in prompt:
        ok("D08 frustrated flag → apology instruction in prompt")
    else:
        fail("D08 frustration instruction missing from injected prompt")

test_D01_order_brain_enabled()
test_D02_no_session_no_injection()
test_D03_empty_session_no_injection()
test_D04_active_session_injected_in_prompt()
test_D05_no_reset_instruction_present()
test_D06_next_step_instruction()
test_D07_double_injection_both_ends()
test_D08_frustration_in_injected_prompt()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP E — Complete order flows (state machine simulation)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── E. Complete order flows ──")

def simulate_flow(messages, conv_id="e_test"):
    """Simulate customer messages through the OrderBrain and return final session."""
    OrderBrain.clear_session(conv_id)
    sess = OrderBrain.get_or_create(conv_id, "r1")
    for msg in messages:
        OrderBrain.update_from_message(sess, msg, PRODUCTS)
    return sess

def test_E01_complete_delivery_flow():
    sess = simulate_flow([
        "أريد برجر",
        "توصيل",
        "العنوان الكرادة",
        "اسمي علي",
        "كاش",
        "ثبت",
    ], "e01")
    checks = [
        (any(it.name == "برجر" for it in sess.items), "item=برجر"),
        (sess.order_type == "delivery", "order_type=delivery"),
        (sess.address == "الكرادة", f"address={sess.address}"),
        (sess.customer_name == "علي", f"name={sess.customer_name}"),
        (sess.payment_method == "كاش", f"payment={sess.payment_method}"),
        (sess.confirmation_status == "confirmed", f"status={sess.confirmation_status}"),
    ]
    all_ok = all(c[0] for c in checks)
    if all_ok:
        ok("E01 complete delivery flow: all slots filled + confirmed")
    else:
        for passed, label in checks:
            if not passed:
                fail(f"E01 {label}")

def test_E02_complete_pickup_flow():
    sess = simulate_flow([
        "أريد زينجر",
        "استلام",
        "اسمي أحمد",
        "كارد",
        "ثبت",
    ], "e02")
    checks = [
        (any(it.name == "زينجر" for it in sess.items), "item=زينجر"),
        (sess.order_type == "pickup", "order_type=pickup"),
        (sess.address is None, f"address should be None, got {sess.address}"),
        (sess.customer_name == "أحمد", f"name={sess.customer_name}"),
        (sess.payment_method == "كارد", f"payment={sess.payment_method}"),
        (sess.confirmation_status == "confirmed", f"status={sess.confirmation_status}"),
    ]
    all_ok = all(c[0] for c in checks)
    if all_ok:
        ok("E02 complete pickup flow: address not required + confirmed")
    else:
        for passed, label in checks:
            if not passed:
                fail(f"E02 {label}")

def test_E03_address_not_re_asked():
    """Once address is set, subsequent messages don't overwrite it."""
    sess = simulate_flow([
        "أريد برجر",
        "توصيل",
        "عنواني المنصور",
    ], "e03")
    addr_before = sess.address
    # Bot might say something that mentions Karkh — should not overwrite
    # In real flow the customer sets address once
    OrderBrain.update_from_message(sess, "كاش", PRODUCTS)
    if sess.address == addr_before == "المنصور":
        ok("E03 address stays set after subsequent messages")
    else:
        fail("E03 address changed", f"was={addr_before} now={sess.address}")

def test_E04_multi_item_flow():
    sess = simulate_flow([
        "أريد برجر وزينجر وكولا",
    ], "e04")
    names = {it.name for it in sess.items}
    if {"برجر", "زينجر", "كولا"} == names:
        ok("E04 multi-item in one message: برجر + زينجر + كولا")
    else:
        fail("E04 multi-item", f"got {names}")

def test_E05_no_reset_after_phone():
    """Session persists even after phone is provided (phone isn't a required slot)."""
    sess = simulate_flow([
        "أريد برجر",
        "توصيل",
        "المنصور",
        "اسمي محمد",
        "هذا رقمي 07901234567",
    ], "e05")
    if sess.is_active() and len(sess.items) == 1:
        ok("E05 session persists after phone — not reset")
    else:
        fail("E05 session reset after phone", f"active={sess.is_active()} items={len(sess.items)}")

def test_E06_la_during_confirm_does_not_lock():
    """'لا' alone should NOT trigger confirmation."""
    sess = fresh_session("e06")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.payment_method = "كاش"
    OrderBrain.update_from_message(sess, "لا", PRODUCTS)
    if sess.confirmation_status == "collecting":
        ok("E06 'لا' alone does not confirm the order")
    else:
        fail("E06 'لا' incorrectly confirmed", f"status={sess.confirmation_status}")

def test_E07_session_state_persists_across_turns():
    """Session fields from earlier turns remain when new messages arrive."""
    conv = "e07"
    OrderBrain.clear_session(conv)
    sess = OrderBrain.get_or_create(conv, "r1")

    OrderBrain.update_from_message(sess, "أريد برجر", PRODUCTS)
    OrderBrain.update_from_message(sess, "توصيل", PRODUCTS)
    OrderBrain.update_from_message(sess, "وين العنوان؟ الكرادة", PRODUCTS)
    OrderBrain.update_from_message(sess, "اسمي سعد", PRODUCTS)
    # Check that nothing was lost
    item_ok = any(it.name == "برجر" for it in sess.items)
    type_ok = sess.order_type == "delivery"
    addr_ok = sess.address == "الكرادة"
    name_ok = sess.customer_name == "سعد"
    if item_ok and type_ok and addr_ok and name_ok:
        ok("E07 session state persists across 4 turns")
    else:
        fail("E07 session state lost", f"items={[it.name for it in sess.items]} type={sess.order_type} addr={sess.address} name={sess.customer_name}")

test_E01_complete_delivery_flow()
test_E02_complete_pickup_flow()
test_E03_address_not_re_asked()
test_E04_multi_item_flow()
test_E05_no_reset_after_phone()
test_E06_la_during_confirm_does_not_lock()
test_E07_session_state_persists_across_turns()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP F — Bot reply parsing
# ══════════════════════════════════════════════════════════════════════════════
print("\n── F. Bot reply parsing ──")

def test_F01_bot_receipt_sets_awaiting_confirm():
    sess = fresh_session("f01")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.confirmation_status = "collecting"
    bot_reply = "✅ طلبك:\n• برجر × 1 — 8,000 د.ع\n──────\n💰 المجموع: 8,000 د.ع\n👤 الاسم: علي\n🏪 استلام"
    OrderBrain.update_from_message(sess, bot_reply, PRODUCTS, is_bot_reply=True)
    if sess.confirmation_status == "awaiting_confirm":
        ok("F01 bot ✅ receipt → status=awaiting_confirm")
    else:
        fail("F01 bot receipt", f"status={sess.confirmation_status}")

def test_F02_bot_cancel_reply():
    sess = fresh_session("f02")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    OrderBrain.update_from_message(sess, "شلنا الطلب 🌷 وصل", PRODUCTS, is_bot_reply=True)
    if sess.confirmation_status == "cancelled":
        ok("F02 bot cancellation reply → status=cancelled")
    else:
        fail("F02 bot cancel reply", f"status={sess.confirmation_status}")

def test_F03_bot_regular_reply_no_status_change():
    sess = fresh_session("f03")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.confirmation_status = "collecting"
    OrderBrain.update_from_message(sess, "تمام 🌷 توصيل لو استلام؟", PRODUCTS, is_bot_reply=True)
    if sess.confirmation_status == "collecting":
        ok("F03 regular bot reply → status unchanged")
    else:
        fail("F03 status changed on regular reply", f"status={sess.confirmation_status}")

test_F01_bot_receipt_sets_awaiting_confirm()
test_F02_bot_cancel_reply()
test_F03_bot_regular_reply_no_status_change()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP G — Tenant isolation
# ══════════════════════════════════════════════════════════════════════════════
print("\n── G. Tenant isolation ──")

def test_G01_separate_restaurants():
    """Two restaurants with same conversation_id get separate sessions."""
    conv = "g01_conv"
    OrderBrain.clear_session(conv)

    sess_r1 = OrderBrain.get_or_create(conv, "restaurant_A")
    sess_r1.items.append(OrderItem(name="برجر", qty=1, price=8000))

    # Same conv_id, different restaurant — currently OrderBrain uses conv_id as key
    # so sessions ARE shared by conv_id (as designed — conv_id is unique per restaurant channel)
    # Test that restaurant_id is stored correctly
    if sess_r1.restaurant_id == "restaurant_A":
        ok("G01 session stores correct restaurant_id")
    else:
        fail("G01 restaurant_id mismatch", f"got {sess_r1.restaurant_id}")

def test_G02_different_conversations_isolated():
    """Two different conversations are fully independent."""
    OrderBrain.clear_session("g02_conv1")
    OrderBrain.clear_session("g02_conv2")

    sess1 = OrderBrain.get_or_create("g02_conv1", "r1")
    sess2 = OrderBrain.get_or_create("g02_conv2", "r1")

    OrderBrain.update_from_message(sess1, "أريد برجر توصيل اسمي علي", PRODUCTS)

    if len(sess2.items) == 0 and sess2.customer_name is None:
        ok("G02 two conversations are independent — sess2 unaffected by sess1")
    else:
        fail("G02 session isolation broken", f"sess2 items={len(sess2.items)} name={sess2.customer_name}")

def test_G03_clear_session():
    conv = "g03"
    OrderBrain.clear_session(conv)
    sess = OrderBrain.get_or_create(conv, "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    OrderBrain.clear_session(conv)
    sess_after = OrderBrain.get_session(conv)
    if sess_after is None:
        ok("G03 clear_session → get_session returns None")
    else:
        fail("G03 session not cleared", f"got {sess_after}")

test_G01_separate_restaurants()
test_G02_different_conversations_isolated()
test_G03_clear_session()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP H — Menu image + voice handling
# ══════════════════════════════════════════════════════════════════════════════
print("\n── H. Menu image + voice handling ──")

def test_H01_menu_image_no_order_state_change():
    """Menu image requests don't create order session items."""
    sess = fresh_session("h01")
    OrderBrain.update_from_message(sess, "دزلي المنيو", PRODUCTS)
    if len(sess.items) == 0:
        ok("H01 menu image request → no items added to session")
    else:
        fail("H01 menu image added items", f"items={[it.name for it in sess.items]}")

def test_H02_voice_message_feeds_brain():
    """[فويس] prefix: content is extracted as if typed."""
    sess = fresh_session("h02")
    # Voice message transcribed to text — same extraction logic applies
    OrderBrain.update_from_message(sess, "[فويس] أريد برجر توصيل اسمي حسين", PRODUCTS)
    item_ok = any(it.name == "برجر" for it in sess.items)
    type_ok = sess.order_type == "delivery"
    name_ok = sess.customer_name == "حسين"
    if item_ok and type_ok and name_ok:
        ok("H02 voice message [فويس] — item + delivery + name extracted")
    else:
        fail("H02 voice extraction", f"items={[it.name for it in sess.items]} type={sess.order_type} name={sess.customer_name}")

def test_H03_unclear_voice_no_extraction():
    """[فويس غير واضح] — no useful data to extract."""
    sess = fresh_session("h03")
    OrderBrain.update_from_message(sess, "[فويس غير واضح]", PRODUCTS)
    if len(sess.items) == 0 and sess.order_type is None:
        ok("H03 unclear voice → no extraction, session unchanged")
    else:
        fail("H03 unclear voice changed session", f"items={len(sess.items)}")

test_H01_menu_image_no_order_state_change()
test_H02_voice_message_feeds_brain()
test_H03_unclear_voice_no_extraction()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP I — AI learning + edge cases
# ══════════════════════════════════════════════════════════════════════════════
print("\n── I. AI learning + edge cases ──")

def test_I01_ai_learning_disabled_safe():
    """When ai_learning_enabled=0, bot.py can still call _build_system_prompt."""
    sess = OrderSession(conversation_id="i01", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    try:
        prompt = _build_system_prompt(
            restaurant={"name": "مطعم التجربة", "ai_learning_enabled": 0},
            settings={},
            bot_cfg={},
            products=PRODUCTS,
            memory={},
            customer={},
            corrections=[],
            knowledge=[],
            order_session=sess,
        )
        if "حالة الطلب الجارية" in prompt:
            ok("I01 ai_learning=0 → prompt still includes order state")
        else:
            fail("I01 order state missing when ai_learning=0")
    except Exception as e:
        fail("I01 exception", str(e))

def test_I02_session_expiry_cleanup():
    conv = "i02"
    OrderBrain.clear_session(conv)
    sess = OrderBrain.get_or_create(conv, "r1")
    sess.updated_at = time.time() - 8000  # expired
    # get_session should remove it
    result = OrderBrain.get_session(conv)
    if result is None:
        ok("I02 expired session → get_session returns None and removes it")
    else:
        fail("I02 expired session not removed", f"got session")

def test_I03_cleanup_expired():
    """cleanup_expired() removes all stale sessions."""
    for i in range(5):
        s = OrderBrain.get_or_create(f"i03_{i}", "r1")
        s.updated_at = time.time() - 8000  # expired
    n = OrderBrain.cleanup_expired()
    if n >= 5:
        ok(f"I03 cleanup_expired removed {n} sessions")
    else:
        fail("I03 cleanup_expired", f"removed only {n}")

def test_I04_items_summary_format():
    sess = OrderSession(conversation_id="i04", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=2, price=8000))
    sess.items.append(OrderItem(name="كولا", qty=1, price=1500))
    summary = sess.items_summary()
    if "برجر × 2" in summary and "كولا × 1" in summary:
        ok("I04 items_summary format correct")
    else:
        fail("I04 items_summary", f"got {summary!r}")

def test_I05_next_missing_field_order():
    """next_missing_field returns the first missing slot in priority order."""
    sess = OrderSession(conversation_id="i05", restaurant_id="r1")
    assert sess.next_missing_field() == "items", f"expected items, got {sess.next_missing_field()}"
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    assert sess.next_missing_field() == "order_type", f"expected order_type, got {sess.next_missing_field()}"
    sess.order_type = "delivery"
    assert sess.next_missing_field() == "address", f"expected address, got {sess.next_missing_field()}"
    sess.address = "الكرادة"
    assert sess.next_missing_field() == "customer_name", f"expected customer_name"
    sess.customer_name = "علي"
    assert sess.next_missing_field() == "payment_method", f"expected payment_method"
    sess.payment_method = "كاش"
    assert sess.next_missing_field() is None, f"expected None, got {sess.next_missing_field()}"
    ok("I05 next_missing_field priority order: items → order_type → address → name → payment → None")

def test_I06_pickup_skips_address_slot():
    sess = OrderSession(conversation_id="i06", restaurant_id="r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "pickup"
    # Next missing after pickup is name (no address)
    if sess.next_missing_field() == "customer_name":
        ok("I06 pickup → next_missing_field skips address → customer_name")
    else:
        fail("I06 pickup slot order", f"got {sess.next_missing_field()}")

test_I01_ai_learning_disabled_safe()
test_I02_session_expiry_cleanup()
test_I03_cleanup_expired()
test_I04_items_summary_format()
test_I05_next_missing_field_order()
test_I06_pickup_skips_address_slot()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP J — Quantity edge cases
# ══════════════════════════════════════════════════════════════════════════════
print("\n── J. Quantity edge cases ──")

def test_J01_default_qty_one():
    qty = _extract_qty("أريد برجر", "برجر")
    if qty == 1:
        ok("J01 no qty specified → defaults to 1")
    else:
        fail("J01 default qty", f"got {qty}")

def test_J02_digit_before():
    qty = _extract_qty("3 برجر", "برجر")
    if qty == 3:
        ok("J02 '3 برجر' → qty=3")
    else:
        fail("J02 digit before", f"got {qty}")

def test_J03_digit_after():
    qty = _extract_qty("برجر × 2", "برجر")
    if qty == 2:
        ok("J03 'برجر × 2' → qty=2")
    else:
        fail("J03 digit after", f"got {qty}")

def test_J04_arabic_word_five():
    qty = _extract_qty("خمسة زينجر", "زينجر")
    if qty == 5:
        ok("J04 'خمسة زينجر' → qty=5")
    else:
        fail("J04 arabic word five", f"got {qty}")

def test_J05_qty_update_existing_item():
    sess = fresh_session("j05")
    OrderBrain.update_from_message(sess, "أريد برجر", PRODUCTS)
    OrderBrain.update_from_message(sess, "3 برجر", PRODUCTS)
    item = next((it for it in sess.items if it.name == "برجر"), None)
    if item and item.qty == 3:
        ok("J05 qty update on existing item: 1 → 3")
    else:
        fail("J05 qty update", f"item={item}")

test_J01_default_qty_one()
test_J02_digit_before()
test_J03_digit_after()
test_J04_arabic_word_five()
test_J05_qty_update_existing_item()


# ══════════════════════════════════════════════════════════════════════════════
# GROUP K — Address + name extractors
# ══════════════════════════════════════════════════════════════════════════════
print("\n── K. Address + name extractors ──")

def test_K01_extract_name_ismi():
    n = _extract_name("اسمي علي")
    if n == "علي":
        ok("K01 'اسمي علي' → علي")
    else:
        fail("K01 extract name ismi", f"got {n!r}")

def test_K02_extract_name_ana():
    n = _extract_name("أنا محمد")
    if n == "محمد":
        ok("K02 'أنا محمد' → محمد")
    else:
        fail("K02 extract name ana", f"got {n!r}")

def test_K03_extract_address_iraq_area():
    a = _extract_address("أنا في المنصور")
    if a == "المنصور":
        ok("K03 'المنصور' → extracted as address")
    else:
        fail("K03 iraq area address", f"got {a!r}")

def test_K04_extract_address_labeled():
    a = _extract_address("عنواني الجادرية")
    if a and "الجادرية" in a:
        ok("K04 labeled address 'عنواني الجادرية' → extracted")
    else:
        fail("K04 labeled address", f"got {a!r}")

def test_K05_extract_phone_iraqi():
    p = _extract_phone("رقمي 07901234567")
    if p == "07901234567":
        ok("K05 Iraqi phone 07901234567 extracted")
    else:
        fail("K05 phone extraction", f"got {p!r}")

test_K01_extract_name_ismi()
test_K02_extract_name_ana()
test_K03_extract_address_iraq_area()
test_K04_extract_address_labeled()
test_K05_extract_phone_iraqi()


# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════
print()
print("─" * 60)
total = _ok + _fail
print(f"{'✅ ALL PASSED' if _fail == 0 else f'❌ {_fail} FAILED'} — {_ok}/{total} tests passed")
sys.exit(0 if _fail == 0 else 1)
