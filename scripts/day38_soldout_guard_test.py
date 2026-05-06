"""
NUMBER 42 — Sold-Out Guard Tests
Run: python3 scripts/day38_soldout_guard_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import OrderBrain, OrderSession, OrderItem

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


def products(sold_out=False):
    """Build a products list with برجر available/sold-out."""
    return [
        {"id": "p1", "name": "برجر",  "price": 8000, "sold_out_date": "2024-01-01" if sold_out else ""},
        {"id": "p2", "name": "كولا",   "price": 1500, "sold_out_date": ""},
        {"id": "p3", "name": "زينجر", "price": 9000, "sold_out_date": ""},
    ]


def sess():
    return OrderSession("t", "r1")


# ── A. Sold-out product not added ─────────────────────────────────────────────
print("\n── A. Sold-out product not added to session ──")


def test_A01_soldout_not_added():
    s = sess()
    OrderBrain.update_from_message(s, "أريد برجر", products(sold_out=True), is_bot_reply=False)
    if not s.has_items():
        ok("A01 sold-out برجر → not added to session")
    else:
        fail("A01", f"items={[i.name for i in s.items]}")


def test_A02_available_product_added_normally():
    s = sess()
    OrderBrain.update_from_message(s, "أريد برجر", products(sold_out=False), is_bot_reply=False)
    if s.has_items() and s.items[0].name == "برجر":
        ok("A02 available برجر → added normally")
    else:
        fail("A02", f"items={[i.name for i in s.items]}")


def test_A03_soldout_recorded_in_session():
    s = sess()
    OrderBrain.update_from_message(s, "أريد برجر", products(sold_out=True), is_bot_reply=False)
    if "برجر" in s.sold_out_rejected:
        ok("A03 sold-out name recorded in session.sold_out_rejected")
    else:
        fail("A03", f"sold_out_rejected={s.sold_out_rejected}")


def test_A04_available_leaves_rejected_empty():
    s = sess()
    OrderBrain.update_from_message(s, "أريد برجر", products(sold_out=False), is_bot_reply=False)
    if s.sold_out_rejected == []:
        ok("A04 available product → sold_out_rejected stays empty")
    else:
        fail("A04", f"sold_out_rejected={s.sold_out_rejected}")


def test_A05_other_items_still_added():
    s = sess()
    prods = [
        {"id": "p1", "name": "برجر", "price": 8000, "sold_out_date": "2024-01-01"},
        {"id": "p2", "name": "كولا",  "price": 1500, "sold_out_date": ""},
    ]
    OrderBrain.update_from_message(s, "برجر وكولا", prods, is_bot_reply=False)
    names = [i.name for i in s.items]
    if "كولا" in names and "برجر" not in names:
        ok("A05 mixed order: available كولا added, sold-out برجر blocked")
    else:
        fail("A05", f"items={names} rejected={s.sold_out_rejected}")


test_A01_soldout_not_added()
test_A02_available_product_added_normally()
test_A03_soldout_recorded_in_session()
test_A04_available_leaves_rejected_empty()
test_A05_other_items_still_added()


# ── B. sold_out_date values ───────────────────────────────────────────────────
print("\n── B. sold_out_date field variations ──")


def test_B01_empty_string_is_available():
    s = sess()
    prods = [{"id": "p1", "name": "برجر", "price": 8000, "sold_out_date": ""}]
    OrderBrain.update_from_message(s, "أريد برجر", prods, is_bot_reply=False)
    if s.has_items():
        ok("B01 sold_out_date='' → product available")
    else:
        fail("B01", "empty string should be available")


def test_B02_none_is_available():
    s = sess()
    prods = [{"id": "p1", "name": "برجر", "price": 8000, "sold_out_date": None}]
    OrderBrain.update_from_message(s, "أريد برجر", prods, is_bot_reply=False)
    if s.has_items():
        ok("B02 sold_out_date=None → product available")
    else:
        fail("B02", "None should be available")


def test_B03_missing_key_is_available():
    s = sess()
    prods = [{"id": "p1", "name": "برجر", "price": 8000}]  # no sold_out_date key
    OrderBrain.update_from_message(s, "أريد برجر", prods, is_bot_reply=False)
    if s.has_items():
        ok("B03 missing sold_out_date key → product available (backward compat)")
    else:
        fail("B03", "missing key should be treated as available")


def test_B04_date_string_is_soldout():
    s = sess()
    prods = [{"id": "p1", "name": "برجر", "price": 8000, "sold_out_date": "2024-01-15"}]
    OrderBrain.update_from_message(s, "أريد برجر", prods, is_bot_reply=False)
    if not s.has_items() and "برجر" in s.sold_out_rejected:
        ok("B04 sold_out_date='2024-01-15' → blocked")
    else:
        fail("B04", f"items={[i.name for i in s.items]}")


test_B01_empty_string_is_available()
test_B02_none_is_available()
test_B03_missing_key_is_available()
test_B04_date_string_is_soldout()


# ── C. Transient reset ────────────────────────────────────────────────────────
print("\n── C. sold_out_rejected resets each message ──")


def test_C01_resets_next_message():
    s = sess()
    prods = [
        {"id": "p1", "name": "برجر", "price": 8000, "sold_out_date": "2024-01-01"},
        {"id": "p2", "name": "كولا",  "price": 1500, "sold_out_date": ""},
    ]
    OrderBrain.update_from_message(s, "أريد برجر", prods, is_bot_reply=False)
    assert "برجر" in s.sold_out_rejected
    # Second message — no sold-out product mentioned
    OrderBrain.update_from_message(s, "أريد كولا", prods, is_bot_reply=False)
    if s.sold_out_rejected == []:
        ok("C01 sold_out_rejected resets to [] on next message")
    else:
        fail("C01", f"still has rejected={s.sold_out_rejected}")


def test_C02_no_duplicate_in_rejected():
    s = sess()
    prods = [{"id": "p1", "name": "برجر", "price": 8000, "sold_out_date": "2024-01-01"}]
    OrderBrain.update_from_message(s, "أريد برجر وبرجر ثاني", prods, is_bot_reply=False)
    if s.sold_out_rejected.count("برجر") == 1:
        ok("C02 same sold-out product not duplicated in rejected list")
    else:
        fail("C02", f"rejected={s.sold_out_rejected}")


test_C01_resets_next_message()
test_C02_no_duplicate_in_rejected()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
