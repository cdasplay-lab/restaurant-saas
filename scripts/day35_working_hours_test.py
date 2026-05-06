"""
NUMBER 39 — Working Hours Check Tests
Run: python3 scripts/day35_working_hours_test.py
"""
import sys, os, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.bot import _is_restaurant_open_now, _find_next_open_day

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


def wh(day_cfg: dict) -> str:
    """Build a working_hours JSON with one day config applied to all days."""
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return json.dumps({d: dict(day_cfg) for d in days})


def wh_today(open_: bool, from_="10:00", to_="22:00") -> str:
    """Build working_hours where every day is configured but only today matters."""
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return json.dumps({d: {"open": open_, "from": from_, "to": to_} for d in days})


def at(h, m=0):
    """datetime with given hour/minute on a Monday (weekday=0)."""
    return datetime(2024, 1, 1, h, m)   # 2024-01-01 is Monday


# ── A. Day-level closed ───────────────────────────────────────────────────────
print("\n── A. Day-level closed ──")


def test_A01_day_closed():
    raw = wh({"open": False})
    is_open, msg, _ = _is_restaurant_open_now(raw, now=at(14))
    if not is_open and "مغلقون" in msg:
        ok("A01 day marked closed → is_open=False, msg contains 'مغلقون'")
    else:
        fail("A01", f"is_open={is_open} msg={msg!r}")


def test_A02_day_open_in_hours():
    raw = wh_today(open_=True, from_="10:00", to_="22:00")
    is_open, msg, _ = _is_restaurant_open_now(raw, now=at(14))
    if is_open:
        ok("A02 day open, time 14:00 within 10–22 → is_open=True")
    else:
        fail("A02", f"is_open={is_open} msg={msg!r}")


def test_A03_before_opening():
    raw = wh_today(open_=True, from_="10:00", to_="22:00")
    is_open, msg, _ = _is_restaurant_open_now(raw, now=at(8))
    if not is_open and "مغلقون" in msg:
        ok("A03 day open, time 08:00 before opening → is_open=False")
    else:
        fail("A03", f"is_open={is_open} msg={msg!r}")


def test_A04_after_closing():
    raw = wh_today(open_=True, from_="10:00", to_="22:00")
    is_open, msg, _ = _is_restaurant_open_now(raw, now=at(23))
    if not is_open and "مغلقون" in msg:
        ok("A04 day open, time 23:00 after closing → is_open=False")
    else:
        fail("A04", f"is_open={is_open} msg={msg!r}")


def test_A05_exactly_at_opening():
    raw = wh_today(open_=True, from_="10:00", to_="22:00")
    is_open, _, _ = _is_restaurant_open_now(raw, now=at(10, 0))
    if is_open:
        ok("A05 exactly at opening time (10:00) → is_open=True")
    else:
        fail("A05 at opening time", "should be open")


def test_A06_exactly_at_closing():
    raw = wh_today(open_=True, from_="10:00", to_="22:00")
    is_open, _, _ = _is_restaurant_open_now(raw, now=at(22, 0))
    if is_open:
        ok("A06 exactly at closing time (22:00) → is_open=True (inclusive)")
    else:
        fail("A06 at closing time")


test_A01_day_closed()
test_A02_day_open_in_hours()
test_A03_before_opening()
test_A04_after_closing()
test_A05_exactly_at_opening()
test_A06_exactly_at_closing()


# ── B. Midnight crossover ─────────────────────────────────────────────────────
print("\n── B. Midnight crossover (e.g., 20:00–02:00) ──")


def test_B01_crossover_before_midnight():
    raw = wh_today(open_=True, from_="20:00", to_="02:00")
    is_open, _, _ = _is_restaurant_open_now(raw, now=at(21))
    if is_open:
        ok("B01 crossover hours 20–02, time 21:00 → is_open=True")
    else:
        fail("B01", "should be open at 21:00 in 20:00–02:00 window")


def test_B02_crossover_after_midnight():
    raw = wh_today(open_=True, from_="20:00", to_="02:00")
    is_open, _, _ = _is_restaurant_open_now(raw, now=at(1))
    if is_open:
        ok("B02 crossover hours 20–02, time 01:00 (after midnight) → is_open=True")
    else:
        fail("B02", "should be open at 01:00 in 20:00–02:00 window")


def test_B03_crossover_closed_midday():
    raw = wh_today(open_=True, from_="20:00", to_="02:00")
    is_open, _, _ = _is_restaurant_open_now(raw, now=at(12))
    if not is_open:
        ok("B03 crossover hours 20–02, time 12:00 → is_open=False")
    else:
        fail("B03", "should be closed at 12:00 in 20:00–02:00 window")


test_B01_crossover_before_midnight()
test_B02_crossover_after_midnight()
test_B03_crossover_closed_midday()


# ── C. Edge cases ─────────────────────────────────────────────────────────────
print("\n── C. Edge cases ──")


def test_C01_empty_wh_fail_open():
    is_open, msg, _ = _is_restaurant_open_now("{}", now=at(14))
    if is_open:
        ok("C01 empty working_hours → fail-open (is_open=True)")
    else:
        fail("C01 empty wh should fail-open", f"is_open={is_open}")


def test_C02_invalid_json_fail_open():
    is_open, _, _ = _is_restaurant_open_now("NOT JSON", now=at(14))
    if is_open:
        ok("C02 invalid JSON → fail-open (is_open=True)")
    else:
        fail("C02 invalid json", f"is_open={is_open}")


def test_C03_no_from_to_assumed_open():
    raw = json.dumps({"mon": {"open": True}})  # open but no from/to
    is_open, _, _ = _is_restaurant_open_now(raw, now=at(14))
    if is_open:
        ok("C03 day open but no from/to → assume open all day")
    else:
        fail("C03 no from/to", f"is_open={is_open}")


def test_C04_next_open_day_found():
    # All days closed except Wednesday
    days = {d: {"open": False} for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}
    days["wed"] = {"open": True, "from": "10:00", "to": "22:00"}
    raw = json.dumps(days)
    # now = Monday 14:00
    is_open, _, next_open = _is_restaurant_open_now(raw, now=at(14))
    if not is_open and "الأربعاء" in next_open:
        ok("C04 closed Monday, next open = Wednesday → next_open_info mentions الأربعاء")
    else:
        fail("C04 next open day", f"is_open={is_open} next={next_open!r}")


def test_C05_status_msg_contains_hours():
    raw = wh_today(open_=True, from_="09:00", to_="21:00")
    is_open, msg, _ = _is_restaurant_open_now(raw, now=at(14))
    if is_open and "09:00" in msg and "21:00" in msg:
        ok("C05 open status msg contains open/close times")
    else:
        fail("C05 msg content", f"msg={msg!r}")


test_C01_empty_wh_fail_open()
test_C02_invalid_json_fail_open()
test_C03_no_from_to_assumed_open()
test_C04_next_open_day_found()
test_C05_status_msg_contains_hours()


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
