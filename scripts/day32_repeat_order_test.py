"""
NUMBER 36 — Repeat Last Order Tests
Tests: phrase detection, session prefill, no-history fallback, serialization.
Run: python3 scripts/day32_repeat_order_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import (
    OrderBrain, OrderSession, OrderItem,
    REPEAT_ORDER_PHRASES,
)

_passed = 0
_failed = 0

PRODUCTS = [
    {"id": "p1", "name": "برجر",  "price": 8000, "available": True},
    {"id": "p2", "name": "زينجر", "price": 9000, "available": True},
    {"id": "p3", "name": "كولا",  "price": 1500, "available": True},
    {"id": "p4", "name": "بطاطا", "price": 2000, "available": True},
]


def ok(name):
    global _passed
    _passed += 1
    print(f"  \033[32m✅ {name}\033[0m")


def fail(name, detail=""):
    global _failed
    _failed += 1
    print(f"  \033[31m❌ {name}{' | ' + detail if detail else ''}\033[0m")


def fresh(cid="test"):
    return OrderSession(conversation_id=cid, restaurant_id="r1")


# ── A. Repeat phrase detection ────────────────────────────────────────────────
print("\n── A. Repeat phrase detection ──")

_POSITIVE_PHRASES = [
    "نفس الطلب السابق",
    "نفس الطلبة السابقة",
    "رجعلي نفس الطلب",
    "اعيد نفس الطلب",
    "نفس الطلب",
    "نفس الطلبة",
    "كرر طلبي",
]

_NEGATIVE_PHRASES = [
    "أريد برجر",
    "هلا شلونكم",
    "كاش",
    "توصيل",
    "نفس الشارع",   # "نفس" but not order-related
]


def test_A01_positive_phrases_detected():
    all_ok = True
    for phrase in _POSITIVE_PHRASES:
        if not any(rp in phrase for rp in REPEAT_ORDER_PHRASES):
            all_ok = False
            print(f"    not detected: {phrase!r}")
    if all_ok:
        ok("A01 all repeat phrases detected")
    else:
        fail("A01 missing repeat phrases")


def test_A02_negative_phrases_not_detected():
    all_ok = True
    for phrase in _NEGATIVE_PHRASES:
        if any(rp in phrase for rp in REPEAT_ORDER_PHRASES):
            all_ok = False
            print(f"    false positive: {phrase!r}")
    if all_ok:
        ok("A02 no false positives for non-repeat messages")
    else:
        fail("A02 false positives in repeat detection")


def test_A03_flag_set_via_update():
    sess = fresh("a03")
    updated = OrderBrain.update_from_message(sess, "نفس الطلب السابق", PRODUCTS)
    if sess.repeat_order_detected and "repeat_order_detected=True" in updated:
        ok("A03 'نفس الطلب السابق' → repeat_order_detected=True via update_from_message()")
    else:
        fail("A03 flag not set", f"flag={sess.repeat_order_detected} updated={updated}")
    OrderBrain.clear_session("a03")


def test_A04_flag_not_set_when_items_exist():
    # If session already has items, repeat phrase should be treated as regular message
    sess = fresh("a04")
    sess.items.append(OrderItem("برجر", 1, 8000))
    OrderBrain.update_from_message(sess, "نفس الطلب السابق", PRODUCTS)
    if not sess.repeat_order_detected:
        ok("A04 repeat phrase ignored when session already has items")
    else:
        fail("A04 repeat triggered with existing items")
    OrderBrain.clear_session("a04")


test_A01_positive_phrases_detected()
test_A02_negative_phrases_not_detected()
test_A03_flag_set_via_update()
test_A04_flag_not_set_when_items_exist()


# ── B. Session prefill from previous order ────────────────────────────────────
print("\n── B. Session prefill ──")

_PREV_ORDER_ITEMS = [
    {"name": "برجر",  "qty": 1, "price": 8000.0},
    {"name": "كولا",  "qty": 2, "price": 1500.0},
]


def test_B01_prefill_adds_items():
    sess = fresh("b01")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    names = {it.name for it in sess.items}
    if names == {"برجر", "كولا"} and len(sess.items) == 2:
        ok("B01 prefill_from_items() adds all items to session")
    else:
        fail("B01 prefill items", f"items={[it.name for it in sess.items]}")


def test_B02_prefill_preserves_qty():
    sess = fresh("b02")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    cola = next((it for it in sess.items if it.name == "كولا"), None)
    if cola and cola.qty == 2:
        ok("B02 prefill preserves quantity (كولا × 2)")
    else:
        fail("B02 prefill qty", f"cola qty={cola and cola.qty}")


def test_B03_prefill_preserves_price():
    sess = fresh("b03")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    burger = next((it for it in sess.items if it.name == "برجر"), None)
    if burger and burger.price == 8000.0:
        ok("B03 prefill preserves price (برجر = 8000)")
    else:
        fail("B03 prefill price", f"burger price={burger and burger.price}")


def test_B04_prefill_sets_upsell_offered():
    sess = fresh("b04")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    if sess.upsell_offered:
        ok("B04 prefill sets upsell_offered=True (skip upsell on repeat order)")
    else:
        fail("B04 upsell not set after prefill")


def test_B05_prefill_clears_repeat_detected():
    sess = fresh("b05")
    sess.repeat_order_detected = True
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    if not sess.repeat_order_detected:
        ok("B05 prefill clears repeat_order_detected flag")
    else:
        fail("B05 repeat_order_detected still True after prefill")


def test_B06_prefill_empty_list_no_items():
    sess = fresh("b06")
    sess.prefill_from_items([])
    if not sess.has_items():
        ok("B06 prefill with empty list → no items added")
    else:
        fail("B06 empty prefill", f"items={[it.name for it in sess.items]}")


def test_B07_prefill_skips_empty_name():
    sess = fresh("b07")
    sess.prefill_from_items([{"name": "", "qty": 1, "price": 0}, {"name": "برجر", "qty": 1, "price": 8000}])
    if len(sess.items) == 1 and sess.items[0].name == "برجر":
        ok("B07 prefill skips items with empty name")
    else:
        fail("B07 empty name item", f"items={[it.name for it in sess.items]}")


def test_B08_after_prefill_next_missing_is_order_type():
    sess = fresh("b08")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    # upsell is already offered, so next_missing_field → order_type
    missing = sess.next_missing_field()
    if missing == "order_type":
        ok("B08 after prefill → next_missing_field() == 'order_type'")
    else:
        fail("B08 next missing after prefill", f"got={missing!r}")


test_B01_prefill_adds_items()
test_B02_prefill_preserves_qty()
test_B03_prefill_preserves_price()
test_B04_prefill_sets_upsell_offered()
test_B05_prefill_clears_repeat_detected()
test_B06_prefill_empty_list_no_items()
test_B07_prefill_skips_empty_name()
test_B08_after_prefill_next_missing_is_order_type()


# ── C. No previous order (failed repeat) ─────────────────────────────────────
print("\n── C. No previous order (repeat_order_failed) ──")


def test_C01_failed_note_in_prompt_section():
    sess = fresh("c01")
    sess.repeat_order_failed = True
    section = sess.to_prompt_section()
    if "ما في طلب سابق" in section or "طلب السابق" in section or "تكرار" in section:
        ok("C01 repeat_order_failed=True → prompt section notes no previous order")
    else:
        fail("C01 failed note missing", f"section excerpt: {section[:200]!r}")


def test_C02_failed_flag_shows_in_prompt_even_no_items():
    sess = fresh("c02")
    sess.repeat_order_failed = True
    section = sess.to_prompt_section()
    if section:
        ok("C02 repeat_order_failed → to_prompt_section() returns non-empty")
    else:
        fail("C02 empty prompt section with failed flag")


def test_C03_failed_not_set_when_items_found():
    sess = fresh("c03")
    sess.repeat_order_detected = True
    # Simulate bot.py hook: items found → prefill, no failed flag
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    if not sess.repeat_order_failed:
        ok("C03 no repeat_order_failed when items successfully prefilled")
    else:
        fail("C03 failed flag wrongly set after prefill")


test_C01_failed_note_in_prompt_section()
test_C02_failed_flag_shows_in_prompt_even_no_items()
test_C03_failed_not_set_when_items_found()


# ── D. Serialization ──────────────────────────────────────────────────────────
print("\n── D. Serialization ──")


def test_D01_repeat_detected_survives_roundtrip():
    sess = fresh("d01")
    sess.repeat_order_detected = True
    d = sess.to_dict()
    restored = OrderSession.from_dict(d)
    if restored.repeat_order_detected:
        ok("D01 repeat_order_detected survives to_dict/from_dict")
    else:
        fail("D01 repeat_detected not serialized")


def test_D02_repeat_failed_survives_roundtrip():
    sess = fresh("d02")
    sess.repeat_order_failed = True
    d = sess.to_dict()
    restored = OrderSession.from_dict(d)
    if restored.repeat_order_failed:
        ok("D02 repeat_order_failed survives to_dict/from_dict")
    else:
        fail("D02 repeat_failed not serialized")


def test_D03_old_session_defaults_to_false():
    sess = fresh("d03")
    d = sess.to_dict()
    d.pop("repeat_order_detected", None)
    d.pop("repeat_order_failed", None)
    restored = OrderSession.from_dict(d)
    if not restored.repeat_order_detected and not restored.repeat_order_failed:
        ok("D03 old session without repeat fields defaults to False")
    else:
        fail("D03 default wrong", f"detected={restored.repeat_order_detected} failed={restored.repeat_order_failed}")


def test_D04_prefilled_items_survive_roundtrip():
    sess = fresh("d04")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    d = sess.to_dict()
    restored = OrderSession.from_dict(d)
    names = {it.name for it in restored.items}
    if names == {"برجر", "كولا"} and restored.upsell_offered:
        ok("D04 prefilled items + upsell_offered survive to_dict/from_dict")
    else:
        fail("D04 prefill not serialized", f"items={list(names)} upsell={restored.upsell_offered}")


test_D01_repeat_detected_survives_roundtrip()
test_D02_repeat_failed_survives_roundtrip()
test_D03_old_session_defaults_to_false()
test_D04_prefilled_items_survive_roundtrip()


# ── E. Directive after prefill ────────────────────────────────────────────────
print("\n── E. Directive after prefill ──")


def test_E01_directive_skips_to_order_type():
    sess = fresh("e01")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    directive = sess.generate_next_directive(PRODUCTS)
    if "توصيل" in directive or "استلام" in directive:
        ok("E01 after prefill → directive asks توصيل/استلام (skips items + upsell)")
    else:
        fail("E01 directive after prefill", f"got: {directive!r}")


def test_E02_items_summary_correct():
    sess = fresh("e02")
    sess.prefill_from_items(_PREV_ORDER_ITEMS)
    summary = sess.items_summary()
    if "برجر" in summary and "كولا" in summary:
        ok("E02 items_summary() correct after prefill")
    else:
        fail("E02 summary after prefill", f"got: {summary!r}")


test_E01_directive_skips_to_order_type()
test_E02_items_summary_correct()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
