#!/usr/bin/env python3
"""
NUMBER 41A — Order Flow Core Test
Tests order flow logic only. Does NOT test story, voice, UI, Capacitor, or analytics.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ── Import modules ────────────────────────────────────────────────────────────
from services.order_brain import (
    OrderSession, OrderItem, OrderBrain,
    _extract_items, _extract_qty, _extract_name, _extract_phone,
    _extract_address, _apply_remove, _apply_increase, _apply_swap,
    _decrease_item_qty, _increase_item_qty, _extract_decrease_qty,
    _fuzzy_product_match, _PRODUCT_ALIASES,
    _FIELD_QUESTION,
    DELIVERY_KEYWORDS, PICKUP_KEYWORDS, PAYMENT_MAP,
    CONFIRMATION_KEYWORDS, REMOVE_PREFIXES, _INCREASE_PREFIXES,
)
from services.tool_safety import find_best_product_match, validate_tool_items
from services.arabic_normalize import (
    normalize_arabic, resolve_alias, find_product_by_alias,
    filter_products_by_specificity, find_product_name_in_session,
)

# ── Test products (simulated menu) ────────────────────────────────────────────
PRODUCTS = [
    {"id": "1", "name": "برجر لحم", "price": 8000, "available": 1},
    {"id": "2", "name": "برجر دجاج", "price": 7000, "available": 1},
    {"id": "3", "name": "برجر كلاسيك", "price": 7500, "available": 1},
    {"id": "4", "name": "بيبسي", "price": 1500, "available": 1},
    {"id": "5", "name": "بطاطا", "price": 3000, "available": 1},
    {"id": "6", "name": "زينجر", "price": 8500, "available": 1},
    {"id": "7", "name": "ماء", "price": 500, "available": 1},
]


def make_session(conv_id="test-conv", rest_id="test-rest"):
    return OrderSession(conversation_id=conv_id, restaurant_id=rest_id)


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("NUMBER 41A — Order Flow Core Test")
print("=" * 70)

# ── A. New Order: Full extraction ────────────────────────────────────────────
print("\nA. New Order — Full Extraction")
print("-" * 40)

msg_a = "أريد ٢ برگر لحم و٢ كولا وبطاطا واحد توصيل للكرادة اسمي علي ورقمي 07710005018"
sess_a = make_session()
updated_a = OrderBrain.update_from_message(sess_a, msg_a, PRODUCTS)

# Items
test("Extracted 3 items", len(sess_a.items) == 3, f"got {len(sess_a.items)}")
_burger = next((it for it in sess_a.items if "لحم" in it.name), None)
_cola = next((it for it in sess_a.items if "بيبسي" in it.name), None)
_fries = next((it for it in sess_a.items if "بطاطا" in it.name), None)

test("Burger is برجر لحم (not دجاج/كلاسيك)", _burger is not None and "لحم" in _burger.name,
     f"got {_burger.name if _burger else 'None'}")
test("Burger qty = 2", _burger and _burger.qty == 2, f"got {_burger.qty if _burger else 'N/A'}")
test("Cola/Pepsi matched", _cola is not None, "كولا alias should match بيبسي")
test("Cola qty = 2", _cola and _cola.qty == 2, f"got {_cola.qty if _cola else 'N/A'}")
test("بطاطا not dropped", _fries is not None, "بطاطا must not be silently dropped")
test("Fries qty = 1", _fries and _fries.qty == 1, f"got {_fries.qty if _fries else 'N/A'}")

# Order type
test("Order type = delivery", sess_a.order_type == "delivery", f"got {sess_a.order_type}")

# Address
test("Address = الكرادة", sess_a.address == "الكرادة", f"got {sess_a.address}")

# Name
test("Customer name = علي", sess_a.customer_name == "علي", f"got {sess_a.customer_name}")

# Phone
test("Phone = 07710005018", sess_a.phone == "07710005018", f"got {sess_a.phone}")

# Payment — should be missing
test("Payment method is missing (needs to be asked)", sess_a.payment_method is None)

# Missing fields should only be payment
_missing = sess_a.missing_fields()
test("Only payment_method is missing", _missing == ["payment_method"], f"got {_missing}")

# ── B. Ambiguous Burger ──────────────────────────────────────────────────────
print("\nB. Ambiguous Burger — Clarification Required")
print("-" * 40)

msg_b = "أريد برگر"
sess_b = make_session()
updated_b = OrderBrain.update_from_message(sess_b, msg_b, PRODUCTS)

# NUMBER 41A — Ambiguous "برگر" with multiple burgers should NOT pick randomly
# It should set clarification_needed instead
_no_random_burger = not any("برجر" in it.name for it in sess_b.items)
test("No random burger selected for ambiguous برجر", _no_random_burger,
     f"should not pick a burger randomly, got {[it.name for it in sess_b.items]}")

test("Clarification needed is set", sess_b.clarification_needed is not None,
     f"clarification_needed should be set, got {sess_b.clarification_needed}")

test("Clarification mentions لحم and دجاج",
     sess_b.clarification_needed is not None and "لحم" in sess_b.clarification_needed and "دجاج" in sess_b.clarification_needed,
     f"got {sess_b.clarification_needed}")

# Specific matching still works: "برگر لحم" → beef burger only
sess_b2 = make_session()
updated_b2 = OrderBrain.update_from_message(sess_b2, "أريد برگر لحم", PRODUCTS)
_has_laham = any("لحم" in it.name for it in sess_b2.items)
_no_dajaj = not any("دجاج" in it.name for it in sess_b2.items)
test("برگر لحم matches beef burger", _has_laham, "should match برجر لحم")
test("برگر لحم does NOT match chicken", _no_dajaj, "should not match برجر دجاج")
test("No clarification for specific برگر لحم", sess_b2.clarification_needed is None,
     f"specific match should not trigger clarification, got {sess_b2.clarification_needed}")

# tool_safety find_best_product_match should prefer specific
_match_laham = find_best_product_match("برجر لحم", PRODUCTS)
test("find_best_product_match prefers برجر لحم over other burgers",
     _match_laham is not None and "لحم" in _match_laham.get("name", ""),
     f"got {_match_laham}")

_match_ambiguous = find_best_product_match("برجر", PRODUCTS)
test("find_best_product_match returns a burger for ambiguous برجر",
     _match_ambiguous is not None and "برجر" in _match_ambiguous.get("name", ""))

# ── C. Order Modification ────────────────────────────────────────────────────
print("\nC. Order Modification — Decrease/Increase")
print("-" * 40)

sess_c = make_session()
sess_c.items = [
    OrderItem(name="برجر لحم", qty=2, price=8000),
    OrderItem(name="بيبسي", qty=2, price=1500),
    OrderItem(name="بطاطا", qty=1, price=3000),
]
sess_c.customer_name = "علي"
sess_c.phone = "07710005018"
sess_c.order_type = "delivery"
sess_c.address = "الكرادة"

msg_c = "لا شيل كولا وحدة وزيد بطاطا"
updated_c = []
_apply_remove(sess_c, msg_c, PRODUCTS, updated_c)
_apply_increase(sess_c, msg_c, PRODUCTS, updated_c)

_cola_after = next((it for it in sess_c.items if "بيبسي" in it.name), None)
_fries_after = next((it for it in sess_c.items if "بطاطا" in it.name), None)

test("Cola decreased to 1 (not removed)", _cola_after is not None and _cola_after.qty == 1,
     f"got qty={_cola_after.qty if _cola_after else 'removed'}")
test("Fries increased to 2", _fries_after is not None and _fries_after.qty == 2,
     f"got qty={_fries_after.qty if _fries_after else 'N/A'}")
test("Customer name preserved", sess_c.customer_name == "علي")
test("Phone preserved", sess_c.phone == "07710005018")
test("Address preserved", sess_c.address == "الكرادة")
test("Order type preserved", sess_c.order_type == "delivery")

# ── C2. Full remove ──────────────────────────────────────────────────────────
print("\nC2. Full Remove — شيل البيبسي")
print("-" * 40)

sess_c2 = make_session()
sess_c2.items = [
    OrderItem(name="برجر لحم", qty=2, price=8000),
    OrderItem(name="بيبسي", qty=2, price=1500),
]
msg_c2 = "شيل البيبسي"
updated_c2 = []
_apply_remove(sess_c2, msg_c2, PRODUCTS, updated_c2)

_cola_c2 = next((it for it in sess_c2.items if "بيبسي" in it.name), None)
test("بيبسي fully removed", _cola_c2 is None)
test("برجر لحم still present", any("لحم" in it.name for it in sess_c2.items))

# ── D. Confirmation Rules ────────────────────────────────────────────────────
print("\nD. Confirmation Rules")
print("-" * 40)

sess_d = make_session()
sess_d.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
sess_d.order_type = "pickup"
sess_d.customer_name = "أحمد"
sess_d.phone = "07700111222"
sess_d.payment_method = "كاش"

test("Session is complete", sess_d.is_complete())
test("Missing fields is empty", len(sess_d.missing_fields()) == 0)

# Confirmation should not fire without explicit keyword
sess_d2 = make_session()
sess_d2.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
sess_d2.order_type = "pickup"
sess_d2.customer_name = "أحمد"
sess_d2.phone = "07700111222"
sess_d2.payment_method = "كاش"
test("New session starts as collecting", sess_d2.confirmation_status == "collecting")

# Simulate confirmation keyword
msg_confirm = "نعم ثبت"
OrderBrain.update_from_message(sess_d2, msg_confirm, PRODUCTS)
test("Confirmation fires on explicit keyword", sess_d2.confirmation_status == "confirmed")

# Incomplete order should NOT confirm
sess_d3 = make_session()
sess_d3.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
sess_d3.order_type = "pickup"
# Missing: customer_name, phone, payment_method
msg_confirm3 = "نعم"
OrderBrain.update_from_message(sess_d3, msg_confirm3, PRODUCTS)
test("Incomplete order does NOT confirm", sess_d3.confirmation_status != "confirmed",
     f"got {sess_d3.confirmation_status}")

# ── E. Summary Format ────────────────────────────────────────────────────────
print("\nE. Summary Format")
print("-" * 40)

sess_e = make_session()
sess_e.items = [
    OrderItem(name="برجر لحم", qty=2, price=8000),
    OrderItem(name="بيبسي", qty=2, price=1500),
    OrderItem(name="بطاطا", qty=1, price=3000),
]
sess_e.order_type = "delivery"
sess_e.address = "الكرادة"
sess_e.customer_name = "علي"
sess_e.phone = "07710005018"
sess_e.payment_method = "كاش"

summary = sess_e.order_summary_for_confirmation(delivery_fee=2000)

test("Summary has 🧾 header", "🧾" in summary)
test("Summary has ━━━ separator", "━━━━" in summary)
test("Summary has 💰 المجموع", "💰 المجموع" in summary)
test("Summary has 🚗 النوع", "🚗 النوع" in summary)
test("Summary has 📍 العنوان", "📍 العنوان" in summary)
test("Summary has 👤 الاسم", "👤 الاسم" in summary)
test("Summary has 📞 الهاتف", "📞 الهاتف" in summary)
test("Summary has 💳 الدفع", "💳 الدفع" in summary)
test("Summary has confirmation question", "نثبت الطلب" in summary or "تعدل" in summary)
test("Summary includes delivery fee", "2,000" in summary or "رسوم التوصيل" in summary)

# ── F. Item Matching Specificity ─────────────────────────────────────────────
print("\nF. Item Matching — Specificity First")
print("-" * 40)

# "برجر لحم" should match "برجر لحم" not "برجر دجاج" or "برجر كلاسيك"
sess_f1 = make_session()
msg_f1 = "أريد برجر لحم"
updated_f1 = []
_extract_items(sess_f1, msg_f1, PRODUCTS, updated_f1)
_f1_burger = next((it for it in sess_f1.items if "برجر" in it.name), None)
test("برجر لحم matches beef burger specifically",
     _f1_burger is not None and "لحم" in _f1_burger.name,
     f"got {_f1_burger.name if _f1_burger else 'None'}")

# "بطاطا" should match
sess_f2 = make_session()
msg_f2 = "أريد بطاطا"
updated_f2 = []
_extract_items(sess_f2, msg_f2, PRODUCTS, updated_f2)
_f2_fries = next((it for it in sess_f2.items if "بطاطا" in it.name), None)
test("بطاطا matches directly", _f2_fries is not None, "بطاطا must not be dropped")

# ── G. Alias Matching ────────────────────────────────────────────────────────
print("\nG. Alias Matching")
print("-" * 40)

# "كولا" should match via alias to "بيبسي"
test("كولا alias exists in _PRODUCT_ALIASES", "كولا" in _PRODUCT_ALIASES)

# ── H. _extract_decrease_qty ─────────────────────────────────────────────────
print("\nH. Quantity Decrease Extraction")
print("-" * 40)

test("شيل بيبسي وحدة → decrease 1", _extract_decrease_qty("شيل بيبسي وحدة", "بيبسي") == 1)
test("شيل بيبسي 2 → decrease 2", _extract_decrease_qty("شيل بيبسي 2", "بيبسي") == 2)
test("شيل وحدة بيبسي → decrease 1", _extract_decrease_qty("شيل وحدة بيبسي", "بيبسي") == 1)

# ── I. Field Questions ───────────────────────────────────────────────────────
print("\nI. Field Questions — No Bad Phrases")
print("-" * 40)

test("customer_name question is شنو اسمك؟", _FIELD_QUESTION["customer_name"] == "شنو اسمك؟")
test("address question is وين عنوان التوصيل؟", _FIELD_QUESTION["address"] == "وين عنوان التوصيل؟")
test("No شسمك in questions", "شسمك" not in _FIELD_QUESTION["customer_name"])

# ── J. tool_safety validate_tool_items ───────────────────────────────────────
print("\nJ. Tool Safety — Item Validation")
print("-" * 40)

_items_input = [
    {"name": "برجر لحم", "qty": 2, "unit_price": 8000},
    {"name": "كولا", "qty": 2, "unit_price": 1500},
    {"name": "بطاطا", "qty": 1, "unit_price": 3000},
]
_validated, _unknown = validate_tool_items(_items_input, PRODUCTS)
test("validate_tool_items returns 3 validated", len(_validated) == 3, f"got {len(_validated)}")
test("No unknown items", len(_unknown) == 0, f"got {_unknown}")

# Unknown item
_items_unknown = [
    {"name": "شاورما", "qty": 1, "unit_price": 5000},
]
_validated2, _unknown2 = validate_tool_items(_items_unknown, PRODUCTS)
test("Unknown item detected", len(_unknown2) == 1, f"got {len(_unknown2)}")

# ── K. Emoji mapping (tested via summary) ────────────────────────────────────
print("\nK. Emoji Mapping (via summary)")
print("-" * 40)

_sess_k = make_session()
_sess_k.items = [
    OrderItem(name="برجر لحم", qty=1, price=8000),
    OrderItem(name="بيبسي", qty=1, price=1500),
    OrderItem(name="بطاطا", qty=1, price=3000),
]
_sess_k.order_type = "pickup"
_summary_k = _sess_k.order_summary_for_confirmation()
test("Burger gets 🍔 in summary", "🍔" in _summary_k)
test("Pepsi gets 🥤 in summary", "🥤" in _summary_k)
test("Fries gets 🍟 in summary", "🍟" in _summary_k)

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 70)

if failed > 0:
    print("❌ NUMBER 41A ORDER FLOW HAS FAILURES — fix before proceeding")
    sys.exit(1)
else:
    print("✅ NUMBER 41A ORDER FLOW PASSED — all checks OK")
    sys.exit(0)
