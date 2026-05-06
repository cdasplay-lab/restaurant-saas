"""
NUMBER 37 — Item Notes / Special Instructions Tests
Tests: note extraction, receipt display, serialization.
Run: python3 scripts/day33_item_notes_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import (
    OrderBrain, OrderSession, OrderItem,
    _extract_item_note, _extract_items,
)

_passed = 0
_failed = 0

PRODUCTS = [
    {"id": "p1", "name": "برجر",  "price": 8000, "available": True},
    {"id": "p2", "name": "زينجر", "price": 9000, "available": True},
    {"id": "p3", "name": "كولا",  "price": 1500, "available": True},
    {"id": "p4", "name": "بطاطا", "price": 2000, "available": True},
    {"id": "p5", "name": "ستيك",  "price": 12000, "available": True},
]
ALL_NAMES = [p["name"] for p in PRODUCTS]


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


# ── A. Note extraction ────────────────────────────────────────────────────────
print("\n── A. Note extraction ──")


def test_A01_bidoon_onion():
    note = _extract_item_note("برجر بدون بصل", "برجر", ALL_NAMES)
    if "بدون بصل" in note:
        ok("A01 'برجر بدون بصل' → note='بدون بصل'")
    else:
        fail("A01 بدون بصل", f"got: {note!r}")


def test_A02_spicy():
    note = _extract_item_note("زينجر حار زيادة", "زينجر", ALL_NAMES)
    if "حار" in note:
        ok("A02 'زينجر حار زيادة' → note contains 'حار'")
    else:
        fail("A02 حار زيادة", f"got: {note!r}")


def test_A03_cold_drink():
    note = _extract_item_note("كولا بارد", "كولا", ALL_NAMES)
    if "بارد" in note:
        ok("A03 'كولا بارد' → note='بارد'")
    else:
        fail("A03 بارد", f"got: {note!r}")


def test_A04_extra_sauce():
    note = _extract_item_note("برجر مع صوص إضافي", "برجر", ALL_NAMES)
    if "إضافي" in note or "صوص" in note:
        ok("A04 'برجر مع صوص إضافي' → note contains sauce note")
    else:
        fail("A04 مع صوص", f"got: {note!r}")


def test_A05_no_modifier_no_note():
    note = _extract_item_note("أريد برجر وكولا", "برجر", ALL_NAMES)
    if note == "":
        ok("A05 'برجر وكولا' → no note (no modifier keyword)")
    else:
        fail("A05 no modifier", f"got: {note!r}")


def test_A06_note_stops_before_next_product():
    # "برجر بدون بصل وكولا" — note for برجر should not include "كولا"
    note = _extract_item_note("برجر بدون بصل وكولا", "برجر", ALL_NAMES)
    if note and "كولا" not in note:
        ok("A06 note stops before next product name ('كولا' excluded from برجر note)")
    else:
        fail("A06 note bleeds into next product", f"got: {note!r}")


def test_A07_steak_well_done():
    note = _extract_item_note("ستيك well done", "ستيك", ALL_NAMES)
    if "well done" in note:
        ok("A07 'ستيك well done' → note='well done'")
    else:
        fail("A07 well done", f"got: {note!r}")


def test_A08_note_not_set_for_clean_order():
    sess = fresh("a08")
    updated = []
    _extract_items(sess, "أريد برجر وكولا", PRODUCTS, updated)
    notes = [it.notes for it in sess.items]
    if all(n == "" for n in notes):
        ok("A08 clean order 'برجر وكولا' → both items have empty notes")
    else:
        fail("A08 unexpected notes", f"notes={notes}")


test_A01_bidoon_onion()
test_A02_spicy()
test_A03_cold_drink()
test_A04_extra_sauce()
test_A05_no_modifier_no_note()
test_A06_note_stops_before_next_product()
test_A07_steak_well_done()
test_A08_note_not_set_for_clean_order()


# ── B. Note attached to session items ─────────────────────────────────────────
print("\n── B. Notes attached to items via update_from_message ──")


def test_B01_note_in_item_after_extract():
    sess = fresh("b01")
    updated = []
    _extract_items(sess, "برجر بدون بصل", PRODUCTS, updated)
    burger = next((it for it in sess.items if it.name == "برجر"), None)
    if burger and "بدون بصل" in (burger.notes or ""):
        ok("B01 _extract_items() sets notes on برجر item")
    else:
        fail("B01 note not in item", f"notes={burger and burger.notes!r}")


def test_B02_note_via_update_from_message():
    sess = fresh("b02")
    sess.conversation_id = "b02"
    OrderBrain._sessions["b02"] = sess
    OrderBrain.update_from_message(sess, "زينجر حار زيادة", PRODUCTS)
    zinger = next((it for it in sess.items if it.name == "زينجر"), None)
    if zinger and "حار" in (zinger.notes or ""):
        ok("B02 update_from_message() sets note on زينجر")
    else:
        fail("B02 note via update", f"notes={zinger and zinger.notes!r}")
    OrderBrain.clear_session("b02")


def test_B03_two_items_independent_notes():
    sess = fresh("b03")
    updated = []
    _extract_items(sess, "برجر بدون بصل وكولا بارد", PRODUCTS, updated)
    burger = next((it for it in sess.items if it.name == "برجر"), None)
    cola   = next((it for it in sess.items if it.name == "كولا"), None)
    burger_ok = burger and "بدون بصل" in (burger.notes or "")
    cola_ok   = cola   and "بارد" in (cola.notes or "")
    if burger_ok and cola_ok:
        ok("B03 two items each get their own independent notes")
    else:
        fail("B03 independent notes", f"برجر={burger and burger.notes!r} كولا={cola and cola.notes!r}")


def test_B04_note_not_duplicated_on_qty_update():
    sess = fresh("b04")
    updated = []
    _extract_items(sess, "برجر بدون بصل", PRODUCTS, updated)
    _extract_items(sess, "خليها 2 برجر", PRODUCTS, updated)
    burger = next((it for it in sess.items if it.name == "برجر"), None)
    # qty should be updated, note should still be there
    if burger and burger.qty == 2 and "بدون بصل" in (burger.notes or ""):
        ok("B04 qty update doesn't erase existing note")
    else:
        fail("B04 note after qty update", f"qty={burger and burger.qty} notes={burger and burger.notes!r}")


test_B01_note_in_item_after_extract()
test_B02_note_via_update_from_message()
test_B03_two_items_independent_notes()
test_B04_note_not_duplicated_on_qty_update()


# ── C. Confirmation receipt shows notes ──────────────────────────────────────
print("\n── C. Notes in confirmation receipt ──")


def test_C01_receipt_shows_note():
    sess = fresh("c01")
    sess.items.append(OrderItem("برجر", 1, 8000, notes="بدون بصل"))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message()
    if "بدون بصل" in msg:
        ok("C01 confirmation receipt includes item note 'بدون بصل'")
    else:
        fail("C01 note missing from receipt", f"msg excerpt: {msg[:200]!r}")


def test_C02_receipt_note_below_item_line():
    sess = fresh("c02")
    sess.items.append(OrderItem("زينجر", 1, 9000, notes="حار زيادة"))
    sess.order_type = "pickup"
    sess.customer_name = "سامي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message()
    lines = msg.split("\n")
    item_line_idx = next((i for i, l in enumerate(lines) if "زينجر" in l), -1)
    note_line_idx = next((i for i, l in enumerate(lines) if "حار زيادة" in l), -1)
    if item_line_idx >= 0 and note_line_idx == item_line_idx + 1:
        ok("C02 note line appears directly below item line in receipt")
    else:
        fail("C02 note position", f"item_line={item_line_idx} note_line={note_line_idx}")


def test_C03_no_note_line_when_empty():
    sess = fresh("c03")
    sess.items.append(OrderItem("برجر", 1, 8000, notes=""))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message()
    # Should not have a "↳" arrow line when no notes
    if "↳" not in msg:
        ok("C03 no '↳' note line when item has empty notes")
    else:
        fail("C03 unwanted note line", f"msg excerpt: {msg[:200]!r}")


def test_C04_multi_item_notes_in_receipt():
    sess = fresh("c04")
    sess.items.append(OrderItem("برجر", 1, 8000, notes="بدون بصل"))
    sess.items.append(OrderItem("كولا", 2, 1500, notes="بارد"))
    sess.items.append(OrderItem("بطاطا", 1, 2000, notes=""))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    msg = sess.generate_confirmation_message()
    has_burger_note = "بدون بصل" in msg
    has_cola_note   = "بارد" in msg
    note_lines = [l for l in msg.split("\n") if l.strip().startswith("↳")]
    # Only 2 note lines (not 3, because بطاطا has no notes)
    if has_burger_note and has_cola_note and len(note_lines) == 2:
        ok("C04 multi-item: only items with notes get a note line")
    else:
        fail("C04 multi notes", f"note_lines={note_lines!r}")


test_C01_receipt_shows_note()
test_C02_receipt_note_below_item_line()
test_C03_no_note_line_when_empty()
test_C04_multi_item_notes_in_receipt()


# ── D. Serialization ──────────────────────────────────────────────────────────
print("\n── D. Serialization ──")


def test_D01_note_in_to_dict():
    item = OrderItem("برجر", 1, 8000, notes="بدون بصل")
    d = item.to_dict()
    if d.get("notes") == "بدون بصل":
        ok("D01 OrderItem.to_dict() includes notes field")
    else:
        fail("D01 to_dict notes", f"got: {d.get('notes')!r}")


def test_D02_note_in_from_dict():
    item = OrderItem.from_dict({"name": "برجر", "qty": 1, "price": 8000, "notes": "بدون بصل"})
    if item.notes == "بدون بصل":
        ok("D02 OrderItem.from_dict() restores notes field")
    else:
        fail("D02 from_dict notes", f"got: {item.notes!r}")


def test_D03_note_survives_session_roundtrip():
    sess = fresh("d03")
    sess.items.append(OrderItem("برجر", 1, 8000, notes="حار زيادة"))
    d = sess.to_dict()
    restored = OrderSession.from_dict(d)
    burger = next((it for it in restored.items if it.name == "برجر"), None)
    if burger and burger.notes == "حار زيادة":
        ok("D03 note survives full session to_dict/from_dict roundtrip")
    else:
        fail("D03 session roundtrip notes", f"got: {burger and burger.notes!r}")


def test_D04_old_item_without_notes_defaults_empty():
    item = OrderItem.from_dict({"name": "برجر", "qty": 1, "price": 8000})
    if item.notes == "":
        ok("D04 old OrderItem dict without 'notes' key defaults to empty string")
    else:
        fail("D04 default notes", f"got: {item.notes!r}")


test_D01_note_in_to_dict()
test_D02_note_in_from_dict()
test_D03_note_survives_session_roundtrip()
test_D04_old_item_without_notes_defaults_empty()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
