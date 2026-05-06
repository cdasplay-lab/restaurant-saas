"""
NUMBER 43 — Quantity Sanity Check Tests
Run: python3 scripts/day39_qty_sanity_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import OrderBrain, OrderSession, OrderItem, MAX_QTY

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


PRODS = [
    {"id": "p1", "name": "برجر",  "price": 8000, "sold_out_date": ""},
    {"id": "p2", "name": "كولا",  "price": 1500, "sold_out_date": ""},
]


def sess():
    return OrderSession("t", "r1")


# ── A. Normal quantities pass through ────────────────────────────────────────
print("\n── A. Normal quantities pass through unchanged ──")


def test_A01_qty_one():
    s = sess()
    OrderBrain.update_from_message(s, "أريد برجر", PRODS, is_bot_reply=False)
    if s.items and s.items[0].qty == 1:
        ok("A01 qty=1 → unchanged")
    else:
        fail("A01", f"qty={s.items[0].qty if s.items else 'no items'}")


def test_A02_qty_max():
    s = sess()
    OrderBrain.update_from_message(s, f"أريد {MAX_QTY} برجر", PRODS, is_bot_reply=False)
    if s.items and s.items[0].qty == MAX_QTY:
        ok(f"A02 qty={MAX_QTY} (exactly MAX_QTY) → not capped")
    else:
        fail("A02", f"qty={s.items[0].qty if s.items else 'no items'}")


def test_A03_qty_five():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 5 برجر", PRODS, is_bot_reply=False)
    if s.items and s.items[0].qty == 5 and s.qty_capped == []:
        ok("A03 qty=5 → no capping")
    else:
        fail("A03", f"qty={s.items[0].qty if s.items else '?'} capped={s.qty_capped}")


test_A01_qty_one()
test_A02_qty_max()
test_A03_qty_five()


# ── B. Excessive quantities are capped ───────────────────────────────────────
print("\n── B. Excessive quantities are capped ──")


def test_B01_qty_over_max_capped():
    s = sess()
    OrderBrain.update_from_message(s, f"أريد {MAX_QTY + 1} برجر", PRODS, is_bot_reply=False)
    if s.items and s.items[0].qty == MAX_QTY:
        ok(f"B01 qty={MAX_QTY+1} → capped to {MAX_QTY}")
    else:
        fail("B01", f"qty={s.items[0].qty if s.items else 'no items'}")


def test_B02_large_qty_capped():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 100 برجر", PRODS, is_bot_reply=False)
    if s.items and s.items[0].qty == MAX_QTY:
        ok(f"B02 qty=100 → capped to {MAX_QTY}")
    else:
        fail("B02", f"qty={s.items[0].qty if s.items else 'no items'}")


def test_B03_capped_name_recorded():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 50 برجر", PRODS, is_bot_reply=False)
    if "برجر" in s.qty_capped:
        ok("B03 capped product name recorded in session.qty_capped")
    else:
        fail("B03", f"qty_capped={s.qty_capped}")


def test_B04_normal_qty_not_in_capped():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 3 برجر", PRODS, is_bot_reply=False)
    if s.qty_capped == []:
        ok("B04 normal qty → qty_capped stays empty")
    else:
        fail("B04", f"qty_capped={s.qty_capped}")


def test_B05_item_still_added_when_capped():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 999 برجر", PRODS, is_bot_reply=False)
    if s.has_items() and s.items[0].qty == MAX_QTY:
        ok("B05 item added despite capping (not blocked, just capped)")
    else:
        fail("B05", f"items={[i.name for i in s.items]}")


test_B01_qty_over_max_capped()
test_B02_large_qty_capped()
test_B03_capped_name_recorded()
test_B04_normal_qty_not_in_capped()
test_B05_item_still_added_when_capped()


# ── C. Transient reset ────────────────────────────────────────────────────────
print("\n── C. qty_capped resets each message ──")


def test_C01_resets_on_next_message():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 50 برجر", PRODS, is_bot_reply=False)
    assert "برجر" in s.qty_capped
    OrderBrain.update_from_message(s, "أريد كولا", PRODS, is_bot_reply=False)
    if s.qty_capped == []:
        ok("C01 qty_capped resets to [] on next message")
    else:
        fail("C01", f"still has capped={s.qty_capped}")


def test_C02_no_duplicate_in_capped():
    s = sess()
    OrderBrain.update_from_message(s, "أريد 100 برجر وبرجر", PRODS, is_bot_reply=False)
    if s.qty_capped.count("برجر") <= 1:
        ok("C02 same item not duplicated in qty_capped")
    else:
        fail("C02", f"qty_capped={s.qty_capped}")


test_C01_resets_on_next_message()
test_C02_no_duplicate_in_capped()


# ── D. MAX_QTY constant ───────────────────────────────────────────────────────
print("\n── D. MAX_QTY constant ──")


def test_D01_max_qty_is_20():
    if MAX_QTY == 20:
        ok("D01 MAX_QTY == 20")
    else:
        fail("D01", f"MAX_QTY={MAX_QTY}")


def test_D02_boundary_exactly_at_max():
    s = sess()
    OrderBrain.update_from_message(s, f"أريد {MAX_QTY} كولا", PRODS, is_bot_reply=False)
    if s.items and s.items[0].qty == MAX_QTY and s.qty_capped == []:
        ok(f"D02 qty exactly {MAX_QTY} → not capped (boundary inclusive)")
    else:
        fail("D02", f"qty={s.items[0].qty if s.items else '?'} capped={s.qty_capped}")


test_D01_max_qty_is_20()
test_D02_boundary_exactly_at_max()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
