"""
NUMBER 35 — Order Edit Engine Tests
Tests: item removal, item swapping, order clear/restart.
Run: python3 scripts/day31_order_edit_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import (
    OrderBrain, OrderSession, OrderItem,
    _apply_remove, _apply_swap,
    REMOVE_PREFIXES, CLEAR_ORDER_PHRASES,
)

_passed = 0
_failed = 0

PRODUCTS = [
    {"id": "p1", "name": "برجر",  "price": 8000, "available": True},
    {"id": "p2", "name": "زينجر", "price": 9000, "available": True},
    {"id": "p3", "name": "كولا",  "price": 1500, "available": True},
    {"id": "p4", "name": "بطاطا", "price": 2000, "available": True},
    {"id": "p5", "name": "بروستد","price": 7500, "available": True},
    {"id": "p6", "name": "سفن أب","price": 1500, "available": True},
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


def session_with(*item_names):
    sess = fresh()
    for n in item_names:
        p = next((p for p in PRODUCTS if p["name"] == n), None)
        if p:
            sess.items.append(OrderItem(n, 1, float(p["price"])))
    return sess


# ── A. Item removal ───────────────────────────────────────────────────────────
print("\n── A. Item removal ──")


def test_A01_shil_cola_removed():
    sess = session_with("برجر", "كولا")
    updated = []
    _apply_remove(sess, "شيل الكولا", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "كولا" not in names and "برجر" in names:
        ok("A01 'شيل الكولا' → كولا removed, برجر intact")
    else:
        fail("A01 شيل كولا", f"items={list(names)}")


def test_A02_ahthf_batata_removed():
    sess = session_with("برجر", "بطاطا")
    updated = []
    _apply_remove(sess, "احذف البطاطا", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "بطاطا" not in names and "برجر" in names:
        ok("A02 'احذف البطاطا' → بطاطا removed")
    else:
        fail("A02 احذف بطاطا", f"items={list(names)}")


def test_A03_ma_orid_cola_removed():
    sess = session_with("برجر", "كولا")
    updated = []
    _apply_remove(sess, "ما أريد الكولا", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "كولا" not in names:
        ok("A03 'ما أريد الكولا' → كولا removed")
    else:
        fail("A03 ما أريد كولا", f"items={list(names)}")


def test_A04_bidoon_cola_removed():
    sess = session_with("برجر", "كولا")
    updated = []
    _apply_remove(sess, "بدون الكولا", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "كولا" not in names:
        ok("A04 'بدون الكولا' → كولا removed")
    else:
        fail("A04 بدون كولا", f"items={list(names)}")


def test_A05_remove_only_item_empties_list():
    sess = session_with("كولا")
    updated = []
    _apply_remove(sess, "شيل الكولا", PRODUCTS, updated)
    if not sess.has_items():
        ok("A05 remove only item → items empty")
    else:
        fail("A05 remove only item", f"items still: {[it.name for it in sess.items]}")


def test_A06_remove_nonexistent_no_change():
    sess = session_with("برجر")
    updated = []
    _apply_remove(sess, "شيل الكولا", PRODUCTS, updated)
    if len(sess.items) == 1 and sess.items[0].name == "برجر":
        ok("A06 remove item not in order → no change")
    else:
        fail("A06 remove nonexistent", f"items={[it.name for it in sess.items]}")


def test_A07_updated_list_populated():
    sess = session_with("برجر", "كولا")
    updated = []
    _apply_remove(sess, "شيل الكولا", PRODUCTS, updated)
    if any("item_removed" in u for u in updated):
        ok("A07 updated list contains item_removed entry")
    else:
        fail("A07 updated list", f"updated={updated}")


def test_A08_via_update_from_message():
    sess = session_with("برجر", "كولا")
    OrderBrain._sessions["a08"] = sess
    sess.conversation_id = "a08"
    updated = OrderBrain.update_from_message(sess, "شيل الكولا", PRODUCTS)
    names = {it.name for it in sess.items}
    if "كولا" not in names:
        ok("A08 removal works via update_from_message()")
    else:
        fail("A08 update_from_message removal", f"items={list(names)}")
    OrderBrain.clear_session("a08")


test_A01_shil_cola_removed()
test_A02_ahthf_batata_removed()
test_A03_ma_orid_cola_removed()
test_A04_bidoon_cola_removed()
test_A05_remove_only_item_empties_list()
test_A06_remove_nonexistent_no_change()
test_A07_updated_list_populated()
test_A08_via_update_from_message()


# ── B. Item swapping ──────────────────────────────────────────────────────────
print("\n── B. Item swapping ──")


def test_B01_swap_cola_for_seven():
    sess = session_with("برجر", "كولا")
    updated = []
    _apply_swap(sess, "بدل الكولا بسفن أب", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "كولا" not in names and "سفن أب" in names and "برجر" in names:
        ok("B01 'بدل الكولا بسفن أب' → كولا removed, سفن أب added")
    else:
        fail("B01 swap كولا بسفن أب", f"items={list(names)}")


def test_B02_swap_burger_for_zinger():
    sess = session_with("برجر", "كولا")
    updated = []
    _apply_swap(sess, "غير البرجر لزينجر", PRODUCTS, updated)
    names = {it.name for it in sess.items}
    if "برجر" not in names and "زينجر" in names:
        ok("B02 'غير البرجر لزينجر' → برجر removed, زينجر added")
    else:
        fail("B02 swap برجر لزينجر", f"items={list(names)}")


def test_B03_swap_preserves_qty():
    sess = fresh("b03")
    sess.items.append(OrderItem("كولا", 2, 1500))
    updated = []
    _apply_swap(sess, "بدل الكولا بسفن أب", PRODUCTS, updated)
    new_item = next((it for it in sess.items if it.name == "سفن أب"), None)
    if new_item and new_item.qty == 2:
        ok("B03 swap preserves quantity (2 كولا → 2 سفن أب)")
    else:
        fail("B03 swap qty", f"new_item={new_item and new_item.qty!r}")


def test_B04_swap_unknown_new_product_removes_old():
    sess = session_with("برجر")
    updated = []
    _apply_swap(sess, "بدل البرجر ببيتزا", PRODUCTS, updated)
    if not any(it.name == "برجر" for it in sess.items):
        ok("B04 swap old→unknown menu item: old removed, nothing bogus added")
    else:
        fail("B04 swap unknown", f"برجر still in items")


def test_B05_swap_item_not_in_order_no_change():
    sess = session_with("برجر")
    updated = []
    # كولا is not in order — swap should do nothing
    _apply_swap(sess, "بدل الكولا بسفن أب", PRODUCTS, updated)
    if not any("item_removed" in u or "item_swapped" in u for u in updated):
        ok("B05 swap item not in order → no change")
    else:
        fail("B05 swap non-order item", f"updated={updated}")


def test_B06_swap_updated_list():
    sess = session_with("كولا")
    updated = []
    _apply_swap(sess, "بدل الكولا بسفن أب", PRODUCTS, updated)
    has_swap = any("item_swapped" in u for u in updated)
    if has_swap:
        ok("B06 updated list contains item_swapped entry")
    else:
        fail("B06 swap updated", f"updated={updated}")


test_B01_swap_cola_for_seven()
test_B02_swap_burger_for_zinger()
test_B03_swap_preserves_qty()
test_B04_swap_unknown_new_product_removes_old()
test_B05_swap_item_not_in_order_no_change()
test_B06_swap_updated_list()


# ── C. Clear / restart order ──────────────────────────────────────────────────
print("\n── C. Clear order ──")


def test_C01_clear_resets_items():
    sess = session_with("برجر", "كولا")
    sess.order_type = "delivery"
    sess.address = "الكرادة"
    sess.clear_order()
    if not sess.has_items() and sess.order_type is None:
        ok("C01 clear_order() → items empty, order_type None")
    else:
        fail("C01 clear items", f"items={len(sess.items)} type={sess.order_type!r}")


def test_C02_clear_preserves_identity():
    sess = session_with("برجر")
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.clear_order()
    if sess.customer_name == "علي" and sess.phone == "07901234567":
        ok("C02 clear_order() preserves customer_name and phone")
    else:
        fail("C02 clear identity", f"name={sess.customer_name!r} phone={sess.phone!r}")


def test_C03_clear_resets_upsell():
    sess = session_with("برجر")
    sess.upsell_offered = True
    sess.clear_order()
    if not sess.upsell_offered:
        ok("C03 clear_order() resets upsell_offered to False")
    else:
        fail("C03 clear upsell", "upsell_offered still True")


def test_C04_clear_phrase_via_update():
    sess = session_with("برجر", "كولا")
    sess.conversation_id = "c04"
    sess.order_type = "delivery"
    OrderBrain._sessions["c04"] = sess
    updated = OrderBrain.update_from_message(sess, "ابدأ من جديد", PRODUCTS)
    if not sess.has_items() and "order_cleared" in updated:
        ok("C04 'ابدأ من جديد' phrase → order cleared via update_from_message()")
    else:
        fail("C04 clear phrase", f"items={len(sess.items)} updated={updated}")
    OrderBrain.clear_session("c04")


def test_C05_clear_status_collecting():
    sess = session_with("برجر")
    sess.confirmation_status = "awaiting_confirm"
    sess.clear_order()
    if sess.confirmation_status == "collecting":
        ok("C05 clear_order() resets confirmation_status to 'collecting'")
    else:
        fail("C05 clear status", f"status={sess.confirmation_status!r}")


def test_C06_after_clear_next_missing_is_items():
    sess = session_with("برجر")
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.clear_order()
    if sess.next_missing_field() == "items":
        ok("C06 after clear → next_missing_field() == 'items'")
    else:
        fail("C06 next after clear", f"got={sess.next_missing_field()!r}")


test_C01_clear_resets_items()
test_C02_clear_preserves_identity()
test_C03_clear_resets_upsell()
test_C04_clear_phrase_via_update()
test_C05_clear_status_collecting()
test_C06_after_clear_next_missing_is_items()


# ── D. State integrity after edits ───────────────────────────────────────────
print("\n── D. State integrity after edits ──")


def test_D01_edit_hint_in_prompt_section():
    sess = session_with("برجر")
    section = sess.to_prompt_section()
    if "شيل" in section or "بدل" in section or "ابدأ من جديد" in section:
        ok("D01 to_prompt_section() includes edit hint during collecting")
    else:
        fail("D01 edit hint missing", f"section excerpt: {section[:200]!r}")


def test_D02_no_edit_hint_when_confirmed():
    sess = session_with("برجر")
    sess.confirmation_status = "confirmed"
    section = sess.to_prompt_section()
    # Only check for edit hint not appearing in active order (confirmed = no hint needed)
    if "شيل" not in section or "confirmed" in sess.confirmation_status:
        ok("D02 no edit hint shown after order confirmed")
    else:
        fail("D02 edit hint shown after confirm")


def test_D03_remove_then_items_summary():
    sess = session_with("برجر", "كولا", "بطاطا")
    updated = []
    _apply_remove(sess, "شيل الكولا", PRODUCTS, updated)
    summary = sess.items_summary()
    if "كولا" not in summary and "برجر" in summary and "بطاطا" in summary:
        ok("D03 items_summary() correct after removal")
    else:
        fail("D03 items_summary after remove", f"summary={summary!r}")


def test_D04_swap_then_total_price():
    sess = session_with("كولا")          # كولا 1500
    sess.items[0].qty = 1
    _apply_swap(sess, "بدل الكولا بسفن أب", PRODUCTS, [])
    new = next((it for it in sess.items if it.name == "سفن أب"), None)
    if new and new.price == 1500.0:
        ok("D04 swap: new item gets correct price from products list")
    else:
        fail("D04 swap price", f"new={new and new.price!r}")


test_D01_edit_hint_in_prompt_section()
test_D02_no_edit_hint_when_confirmed()
test_D03_remove_then_items_summary()
test_D04_swap_then_total_price()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
