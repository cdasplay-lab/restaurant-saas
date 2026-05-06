"""
NUMBER 38 — Delivery Fee + Min Order Tests
Run: python3 scripts/day34_delivery_fee_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import OrderSession, OrderItem

_passed = 0
_failed = 0


def ok(name):
    global _passed
    _passed += 1
    print(f"  \033[32m✅ {name}\033[0m")


def fail(name, detail=""):
    global _failed
    _failed += 1
    print(f"  \033[31m❌ {name}{' | ' + detail if detail else ''}\033[0m")


def fresh(cid="test"):
    sess = OrderSession(conversation_id=cid, restaurant_id="r1")
    sess.order_type = "delivery"
    sess.address = "الكرادة"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    return sess


# ── A. items_total() ──────────────────────────────────────────────────────────
print("\n── A. items_total() ──")


def test_A01_empty_session():
    sess = fresh()
    if sess.items_total() == 0:
        ok("A01 empty session → items_total() = 0")
    else:
        fail("A01", f"got {sess.items_total()}")


def test_A02_single_item():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    if sess.items_total() == 8000:
        ok("A02 1 × برجر 8000 → items_total() = 8000")
    else:
        fail("A02", f"got {sess.items_total()}")


def test_A03_multi_item():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    sess.items.append(OrderItem("كولا",  2, 1500))
    if sess.items_total() == 11000:
        ok("A03 برجر 8000 + كولا×2 3000 → items_total() = 11000")
    else:
        fail("A03", f"got {sess.items_total()}")


def test_A04_qty_multiplied():
    sess = fresh()
    sess.items.append(OrderItem("زينجر", 3, 9000))
    if sess.items_total() == 27000:
        ok("A04 زينجر × 3 × 9000 → items_total() = 27000")
    else:
        fail("A04", f"got {sess.items_total()}")


test_A01_empty_session()
test_A02_single_item()
test_A03_multi_item()
test_A04_qty_multiplied()


# ── B. is_below_min_order() ───────────────────────────────────────────────────
print("\n── B. is_below_min_order() ──")


def test_B01_below_min():
    sess = fresh()
    sess.items.append(OrderItem("كولا", 1, 1500))
    if sess.is_below_min_order(5000):
        ok("B01 total 1500 < min 5000 → is_below_min_order = True")
    else:
        fail("B01", f"total={sess.items_total()}")


def test_B02_above_min():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    if not sess.is_below_min_order(5000):
        ok("B02 total 8000 >= min 5000 → is_below_min_order = False")
    else:
        fail("B02", f"total={sess.items_total()}")


def test_B03_exactly_at_min():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 5000))
    if not sess.is_below_min_order(5000):
        ok("B03 total == min → not below (allowed)")
    else:
        fail("B03", f"total={sess.items_total()}")


def test_B04_min_zero_never_blocked():
    sess = fresh()
    sess.items.append(OrderItem("كولا", 1, 500))
    if not sess.is_below_min_order(0):
        ok("B04 min_order=0 → never blocked")
    else:
        fail("B04 min 0 still blocking")


test_B01_below_min()
test_B02_above_min()
test_B03_exactly_at_min()
test_B04_min_zero_never_blocked()


# ── C. Delivery fee in receipt ────────────────────────────────────────────────
print("\n── C. Delivery fee in receipt ──")


def test_C01_fee_line_shown_for_delivery():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    if "رسوم التوصيل" in msg and "2,000" in msg:
        ok("C01 delivery fee line appears in receipt")
    else:
        fail("C01 no fee line", f"msg: {msg[:200]!r}")


def test_C02_fee_added_to_grand_total():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    # items 8000 + fee 2000 = total 10,000
    if "10,000" in msg:
        ok("C02 grand total = items 8000 + fee 2000 = 10,000")
    else:
        fail("C02 wrong total", f"msg: {msg[:200]!r}")


def test_C03_pickup_no_fee():
    sess = fresh()
    sess.order_type = "pickup"
    sess.address = None
    sess.items.append(OrderItem("برجر", 1, 8000))
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    if "رسوم التوصيل" not in msg and "10,000" not in msg:
        ok("C03 pickup order → no delivery fee added (even if fee param passed)")
    else:
        fail("C03 pickup got fee", f"msg: {msg[:200]!r}")


def test_C04_fee_zero_no_fee_line():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    msg = sess.generate_confirmation_message(delivery_fee=0)
    if "رسوم التوصيل" not in msg:
        ok("C04 delivery_fee=0 → no fee line in receipt")
    else:
        fail("C04 unexpected fee line", f"msg: {msg[:200]!r}")


def test_C05_multi_item_total_with_fee():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    sess.items.append(OrderItem("كولا",  2, 1500))
    msg = sess.generate_confirmation_message(delivery_fee=1500)
    # items 11,000 + fee 1,500 = 12,500
    if "12,500" in msg:
        ok("C05 multi-item: 11,000 + 1,500 fee = 12,500 grand total")
    else:
        fail("C05 total wrong", f"msg: {msg[:200]!r}")


def test_C06_fee_appears_before_total():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    lines = msg.split("\n")
    fee_idx   = next((i for i, l in enumerate(lines) if "رسوم التوصيل" in l), -1)
    total_idx = next((i for i, l in enumerate(lines) if "المجموع" in l), -1)
    if fee_idx >= 0 and total_idx >= 0 and fee_idx < total_idx:
        ok("C06 fee line appears before total line in receipt")
    else:
        fail("C06 order", f"fee_idx={fee_idx} total_idx={total_idx}")


test_C01_fee_line_shown_for_delivery()
test_C02_fee_added_to_grand_total()
test_C03_pickup_no_fee()
test_C04_fee_zero_no_fee_line()
test_C05_multi_item_total_with_fee()
test_C06_fee_appears_before_total()


# ── D. Receipt backward compat (no delivery_fee param) ───────────────────────
print("\n── D. Backward compatibility ──")


def test_D01_no_fee_param_works_as_before():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 2, 8000))
    msg = sess.generate_confirmation_message(order_number="AB12")
    if "16,000" in msg and "#AB12" in msg:
        ok("D01 calling without delivery_fee still works (backward compat)")
    else:
        fail("D01", f"msg: {msg[:200]!r}")


def test_D02_items_total_not_affected_by_fee():
    sess = fresh()
    sess.items.append(OrderItem("برجر", 1, 8000))
    # items_total() should always be items only, never include fee
    if sess.items_total() == 8000:
        ok("D02 items_total() never includes delivery fee")
    else:
        fail("D02", f"got {sess.items_total()}")


test_D01_no_fee_param_works_as_before()
test_D02_items_total_not_affected_by_fee()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
