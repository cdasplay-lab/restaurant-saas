"""
NUMBER 40 — Delivery Time Estimate in Confirmation Tests
Run: python3 scripts/day36_delivery_time_test.py
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


def delivery_sess():
    s = OrderSession("t", "r1")
    s.items.append(OrderItem("برجر", 1, 8000))
    s.order_type = "delivery"
    s.address = "الكرادة"
    s.customer_name = "علي"
    s.phone = "07901234567"
    s.payment_method = "كاش"
    return s


def pickup_sess():
    s = OrderSession("t", "r1")
    s.items.append(OrderItem("برجر", 1, 8000))
    s.order_type = "pickup"
    s.customer_name = "علي"
    s.phone = "07901234567"
    s.payment_method = "كاش"
    return s


print("\n── A. Delivery time in receipt ──")


def test_A01_time_shown_for_delivery():
    msg = delivery_sess().generate_confirmation_message(delivery_time="30 دقيقة")
    if "وقت التوصيل" in msg and "30 دقيقة" in msg:
        ok("A01 delivery receipt shows delivery_time")
    else:
        fail("A01", f"excerpt: {msg[:200]!r}")


def test_A02_time_after_address_line():
    msg = delivery_sess().generate_confirmation_message(delivery_time="30 دقيقة")
    lines = msg.split("\n")
    addr_idx = next((i for i, l in enumerate(lines) if "توصيل" in l and "الكرادة" in l), -1)
    time_idx = next((i for i, l in enumerate(lines) if "وقت التوصيل" in l), -1)
    if addr_idx >= 0 and time_idx == addr_idx + 1:
        ok("A02 time line appears directly after address line")
    else:
        fail("A02 position", f"addr={addr_idx} time={time_idx}")


def test_A03_no_time_for_pickup():
    msg = pickup_sess().generate_confirmation_message(delivery_time="30 دقيقة")
    if "وقت التوصيل" not in msg:
        ok("A03 pickup receipt → no delivery time shown")
    else:
        fail("A03 pickup got time")


def test_A04_empty_time_no_line():
    msg = delivery_sess().generate_confirmation_message(delivery_time="")
    if "وقت التوصيل" not in msg:
        ok("A04 empty delivery_time → no time line")
    else:
        fail("A04 empty time still shown")


def test_A05_time_and_fee_together():
    msg = delivery_sess().generate_confirmation_message(delivery_fee=2000, delivery_time="45 دقيقة")
    has_fee  = "رسوم التوصيل" in msg and "2,000" in msg
    has_time = "وقت التوصيل" in msg and "45 دقيقة" in msg
    if has_fee and has_time:
        ok("A05 fee and delivery_time both shown together")
    else:
        fail("A05", f"fee={has_fee} time={has_time}")


def test_A06_backward_compat_no_param():
    msg = delivery_sess().generate_confirmation_message(order_number="XY1")
    if "#XY1" in msg and "وقت التوصيل" not in msg:
        ok("A06 backward compat: calling without delivery_time still works")
    else:
        fail("A06", f"excerpt: {msg[:200]!r}")


def test_A07_various_time_strings():
    for t in ["30-45 دقيقة", "ساعة تقريباً", "20 min"]:
        msg = delivery_sess().generate_confirmation_message(delivery_time=t)
        if t not in msg:
            fail("A07 time string", f"'{t}' missing from receipt")
            return
    ok("A07 various time string formats shown correctly")


test_A01_time_shown_for_delivery()
test_A02_time_after_address_line()
test_A03_no_time_for_pickup()
test_A04_empty_time_no_line()
test_A05_time_and_fee_together()
test_A06_backward_compat_no_param()
test_A07_various_time_strings()


print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
