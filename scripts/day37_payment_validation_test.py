"""
NUMBER 41 — Payment Method Validation Tests
Run: python3 scripts/day37_payment_validation_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import (
    OrderSession, OrderItem,
    parse_allowed_payment_methods,
)

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


def sess(pm=None):
    s = OrderSession("t", "r1")
    s.items.append(OrderItem("برجر", 1, 8000))
    s.order_type = "delivery"
    s.address = "الكرادة"
    s.customer_name = "علي"
    s.phone = "07901234567"
    s.payment_method = pm
    return s


# ── A. parse_allowed_payment_methods() ───────────────────────────────────────
print("\n── A. parse_allowed_payment_methods() ──")


def test_A01_single_method():
    r = parse_allowed_payment_methods("كاش")
    if r == ["كاش"]:
        ok("A01 single method 'كاش' → ['كاش']")
    else:
        fail("A01", f"got {r}")


def test_A02_two_methods_arabic_comma():
    r = parse_allowed_payment_methods("كاش، كارد")
    if set(r) == {"كاش", "كارد"}:
        ok("A02 'كاش، كارد' → both methods parsed")
    else:
        fail("A02", f"got {r}")


def test_A03_western_comma():
    r = parse_allowed_payment_methods("كاش, كارد")
    if set(r) == {"كاش", "كارد"}:
        ok("A03 western comma delimiter works")
    else:
        fail("A03", f"got {r}")


def test_A04_all_three():
    r = parse_allowed_payment_methods("كاش، كارد، زين كاش")
    if set(r) == {"كاش", "كارد", "زين كاش"}:
        ok("A04 all three canonical methods parsed")
    else:
        fail("A04", f"got {r}")


def test_A05_empty_string():
    r = parse_allowed_payment_methods("")
    if r == []:
        ok("A05 empty string → []")
    else:
        fail("A05", f"got {r}")


def test_A06_unknown_method_ignored():
    r = parse_allowed_payment_methods("كاش، مجهول")
    if r == ["كاش"]:
        ok("A06 unknown method is silently ignored")
    else:
        fail("A06", f"got {r}")


test_A01_single_method()
test_A02_two_methods_arabic_comma()
test_A03_western_comma()
test_A04_all_three()
test_A05_empty_string()
test_A06_unknown_method_ignored()


# ── B. invalid_payment_method() ──────────────────────────────────────────────
print("\n── B. invalid_payment_method() ──")


def test_B01_valid_method_not_invalid():
    s = sess("كاش")
    if not s.invalid_payment_method("كاش، كارد"):
        ok("B01 كاش in allowed='كاش، كارد' → not invalid")
    else:
        fail("B01 should be valid")


def test_B02_invalid_method_detected():
    s = sess("زين كاش")
    if s.invalid_payment_method("كاش، كارد"):
        ok("B02 زين كاش not in allowed='كاش، كارد' → invalid")
    else:
        fail("B02 should be invalid")


def test_B03_no_payment_set_not_invalid():
    s = sess(None)
    if not s.invalid_payment_method("كاش"):
        ok("B03 payment_method=None → not invalid (no check needed yet)")
    else:
        fail("B03 None should not be flagged")


def test_B04_empty_allowed_not_invalid():
    s = sess("زين كاش")
    if not s.invalid_payment_method(""):
        ok("B04 empty allowed list → fail-open (not invalid)")
    else:
        fail("B04 empty allowed should fail-open")


def test_B05_single_allowed_exact():
    s = sess("كارد")
    if not s.invalid_payment_method("كارد"):
        ok("B05 كارد in allowed='كارد' → valid")
    else:
        fail("B05 should be valid")


def test_B06_single_allowed_wrong_method():
    s = sess("كارد")
    if s.invalid_payment_method("كاش"):
        ok("B06 كارد not in allowed='كاش' → invalid")
    else:
        fail("B06 should be invalid")


test_B01_valid_method_not_invalid()
test_B02_invalid_method_detected()
test_B03_no_payment_set_not_invalid()
test_B04_empty_allowed_not_invalid()
test_B05_single_allowed_exact()
test_B06_single_allowed_wrong_method()


# ── C. Session clears invalid method (simulated bot flow) ────────────────────
print("\n── C. Session clears invalid method (simulated) ──")


def test_C01_clear_invalid_method():
    s = sess("زين كاش")
    if s.invalid_payment_method("كاش، كارد"):
        s.payment_method = None
    if s.payment_method is None:
        ok("C01 invalid method cleared from session")
    else:
        fail("C01 method not cleared")


def test_C02_session_incomplete_after_clear():
    s = sess("زين كاش")
    s.order_type = "delivery"
    if s.invalid_payment_method("كاش"):
        s.payment_method = None
    if not s.is_complete():
        ok("C02 session incomplete after clearing invalid method")
    else:
        fail("C02 session should not be complete")


def test_C03_valid_method_session_stays():
    s = sess("كاش")
    if not s.invalid_payment_method("كاش"):
        pass  # no clearing
    if s.payment_method == "كاش":
        ok("C03 valid method stays in session unchanged")
    else:
        fail("C03 valid method should not be cleared")


test_C01_clear_invalid_method()
test_C02_session_incomplete_after_clear()
test_C03_valid_method_session_stays()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
