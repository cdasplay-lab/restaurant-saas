"""
NUMBER 32 — Multi-Item + Edge Cases Tests
Tests: multi-item extraction, fuzzy matching, ambiguous intent, confirmation total.
Run: python3 scripts/day29_multi_item_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import (
    OrderBrain, OrderSession, OrderItem,
    _extract_items, _fuzzy_product_match,
)

_passed = 0
_failed = 0

PRODUCTS = [
    {"id": "p1", "name": "برجر", "price": 8000, "available": True, "sold_out_date": ""},
    {"id": "p2", "name": "زينجر", "price": 9000, "available": True, "sold_out_date": ""},
    {"id": "p3", "name": "كولا", "price": 1500, "available": True, "sold_out_date": ""},
    {"id": "p4", "name": "بطاطا", "price": 2000, "available": True, "sold_out_date": ""},
    {"id": "p5", "name": "بروستد", "price": 7500, "available": True, "sold_out_date": ""},
    {"id": "p6", "name": "شاورما", "price": 6000, "available": True, "sold_out_date": ""},
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


# ── A. Multi-item extraction ──────────────────────────────────────────────────
print("\n── A. Multi-item extraction ──")


def test_A01_two_items():
    sess = fresh("a01")
    updated = []
    _extract_items(sess, "أريد برجر وكولا", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "برجر" in names and "كولا" in names and len(sess.items) == 2:
        ok("A01 'برجر وكولا' → 2 items extracted")
    else:
        fail("A01 two items", f"got items={[it.name for it in sess.items]}")


def test_A02_three_items():
    sess = fresh("a02")
    updated = []
    _extract_items(sess, "خذلي برجر وكولا وبطاطا", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if names == {"برجر", "كولا", "بطاطا"}:
        ok("A02 'برجر وكولا وبطاطا' → 3 items extracted")
    else:
        fail("A02 three items", f"got={names}")


def test_A03_qty_per_item():
    sess = fresh("a03")
    updated = []
    _extract_items(sess, "أريد 2 برجر واثنين كولا", PRODUCTS, updated)
    burger = next((it for it in sess.items if it.name == "برجر"), None)
    cola = next((it for it in sess.items if it.name == "كولا"), None)
    if burger and burger.qty == 2 and cola and cola.qty == 2:
        ok("A03 qty per item: 2 برجر and اثنين كولا → both qty=2")
    else:
        fail("A03 qty per item", f"برجر={burger and burger.qty} كولا={cola and cola.qty}")


def test_A04_no_duplicate_items():
    sess = fresh("a04")
    updated = []
    _extract_items(sess, "أريد برجر", PRODUCTS, updated)
    _extract_items(sess, "برجر", PRODUCTS, updated)  # second message same item
    if len(sess.items) == 1:
        ok("A04 no duplicate — برجر mentioned twice → only 1 entry")
    else:
        fail("A04 duplicate item", f"got {len(sess.items)} items")


def test_A05_qty_update_existing():
    sess = fresh("a05")
    updated = []
    _extract_items(sess, "أريد برجر", PRODUCTS, updated)
    updated2 = []
    _extract_items(sess, "خليها 2 برجر", PRODUCTS, updated2)
    burger = next((it for it in sess.items if it.name == "برجر"), None)
    if burger and burger.qty == 2:
        ok("A05 qty update: 1 → 2 for existing برجر item")
    else:
        fail("A05 qty update", f"got qty={burger and burger.qty}")


def test_A06_not_in_menu_no_item_added():
    sess = fresh("a06")
    updated = []
    _extract_items(sess, "أريد بيتزا", PRODUCTS, updated)
    if not sess.has_items():
        ok("A06 'بيتزا' not in menu → no item added")
    else:
        fail("A06 not in menu item added", f"got items={[it.name for it in sess.items]}")


test_A01_two_items()
test_A02_three_items()
test_A03_qty_per_item()
test_A04_no_duplicate_items()
test_A05_qty_update_existing()
test_A06_not_in_menu_no_item_added()


# ── B. Fuzzy / alias matching ────────────────────────────────────────────────
print("\n── B. Fuzzy matching ──")


def test_B01_alias_brkr():
    result = _fuzzy_product_match("أريد بركر", PRODUCTS)
    if result and result["name"] == "برجر":
        ok("B01 'بركر' alias → برجر")
    else:
        fail("B01 بركر alias", f"got={result and result.get('name')!r}")


def test_B02_alias_znjer():
    result = _fuzzy_product_match("أريد زنجر", PRODUCTS)
    if result and result["name"] == "زينجر":
        ok("B02 'زنجر' alias → زينجر")
    else:
        fail("B02 زنجر alias", f"got={result and result.get('name')!r}")


def test_B03_alias_shawarma():
    result = _fuzzy_product_match("أريد شاورمة", PRODUCTS)
    if result and result["name"] == "شاورما":
        ok("B03 'شاورمة' alias → شاورما")
    else:
        fail("B03 شاورمة alias", f"got={result and result.get('name')!r}")


def test_B04_alias_potato():
    result = _fuzzy_product_match("أريد بطاطس", PRODUCTS)
    if result and result["name"] == "بطاطا":
        ok("B04 'بطاطس' alias → بطاطا")
    else:
        fail("B04 بطاطس alias", f"got={result and result.get('name')!r}")


def test_B05_no_false_positive_plain_text():
    # "كلام عادي" has no food items at all
    result = _fuzzy_product_match("شكراً كلش", PRODUCTS)
    if result is None:
        ok("B05 random text → no fuzzy match (no false positive)")
    else:
        fail("B05 false positive", f"matched: {result and result.get('name')!r}")


def test_B06_fuzzy_item_added_to_session():
    sess = fresh("b06")
    updated = []
    _extract_items(sess, "أريد بركر", PRODUCTS, updated)
    if sess.has_items() and sess.items[0].name == "برجر":
        ok("B06 fuzzy 'بركر' → برجر added to session")
    else:
        fail("B06 fuzzy item in session", f"items={[it.name for it in sess.items]}")


test_B01_alias_brkr()
test_B02_alias_znjer()
test_B03_alias_shawarma()
test_B04_alias_potato()
test_B05_no_false_positive_plain_text()
test_B06_fuzzy_item_added_to_session()


# ── C. Ambiguous order intent detection ─────────────────────────────────────
print("\n── C. Ambiguous intent detection ──")


def test_C01_intent_without_product():
    sess = OrderBrain.get_or_create("c01", "r1")
    OrderBrain.update_from_message(sess, "أريد أكل", PRODUCTS)
    if sess.order_intent_detected and not sess.has_items():
        ok("C01 'أريد أكل' → order_intent_detected=True, no items")
    else:
        fail("C01 ambiguous intent", f"intent={sess.order_intent_detected} items={sess.has_items()}")
    OrderBrain.clear_session("c01")


def test_C02_intent_with_product_no_flag():
    sess = OrderBrain.get_or_create("c02", "r1")
    OrderBrain.update_from_message(sess, "أريد برجر", PRODUCTS)
    if sess.has_items() and not sess.order_intent_detected:
        ok("C02 'أريد برجر' → item added, order_intent_detected stays False")
    else:
        fail("C02 intent with product", f"items={sess.has_items()} intent={sess.order_intent_detected}")
    OrderBrain.clear_session("c02")


def test_C03_intent_flag_in_prompt():
    sess = fresh("c03")
    sess.order_intent_detected = True
    section = sess.to_prompt_section()
    if "أعرب عن نية الطلب" in section or "أصناف" in section:
        ok("C03 order_intent_detected → prompt section mentions menu suggestion")
    else:
        fail("C03 intent not in prompt", f"section excerpt: {section[:100]!r}")


def test_C04_no_intent_on_greeting():
    sess = OrderBrain.get_or_create("c04", "r1")
    OrderBrain.update_from_message(sess, "هلا شلونكم", PRODUCTS)
    if not sess.order_intent_detected:
        ok("C04 greeting 'هلا شلونكم' → no order intent flagged")
    else:
        fail("C04 greeting triggered intent", f"intent={sess.order_intent_detected}")
    OrderBrain.clear_session("c04")


test_C01_intent_without_product()
test_C02_intent_with_product_no_flag()
test_C03_intent_flag_in_prompt()
test_C04_no_intent_on_greeting()


# ── D. Confirmation receipt with total ──────────────────────────────────────
print("\n── D. Confirmation receipt with total ──")


def test_D01_single_item_total():
    sess = fresh("d01")
    sess.items.append(OrderItem(name="برجر", qty=2, price=8000))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message(order_number="AB12")
    if "16,000" in msg or "16000" in msg:
        ok("D01 single item total: 2 × 8000 = 16,000 د.ع")
    else:
        fail("D01 total price", f"msg excerpt: {msg[:200]!r}")


def test_D02_multi_item_total():
    sess = fresh("d02")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.items.append(OrderItem(name="كولا", qty=2, price=1500))
    sess.order_type = "delivery"
    sess.address = "الكرادة"
    sess.customer_name = "محمد"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message(order_number="CD34")
    # Total = 8000 + 3000 = 11,000
    if "11,000" in msg or "11000" in msg:
        ok("D02 multi-item total: برجر 8000 + كولا×2 3000 = 11,000 د.ع")
    else:
        fail("D02 total price", f"msg: {msg}")


def test_D03_zero_price_items_no_total():
    sess = fresh("d03")
    sess.items.append(OrderItem(name="برجر", qty=1, price=0))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message()
    if "المجموع" not in msg:
        ok("D03 zero-price items → no total line shown")
    else:
        fail("D03 zero price total shown", f"msg: {msg[:100]!r}")


def test_D04_receipt_has_order_number():
    sess = fresh("d04")
    sess.items.append(OrderItem(name="زينجر", qty=1, price=9000))
    sess.order_type = "pickup"
    sess.customer_name = "سامي"
    sess.phone = "07901234567"
    sess.payment_method = "كارد"
    msg = sess.generate_confirmation_message(order_number="XY99")
    if "#XY99" in msg:
        ok("D04 receipt includes order number #XY99")
    else:
        fail("D04 order number missing", f"msg: {msg[:200]!r}")


test_D01_single_item_total()
test_D02_multi_item_total()
test_D03_zero_price_items_no_total()
test_D04_receipt_has_order_number()


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
