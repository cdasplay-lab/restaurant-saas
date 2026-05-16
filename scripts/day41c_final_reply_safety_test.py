#!/usr/bin/env python3
"""
scripts/day41c_final_reply_safety_test.py — NUMBER 41C: Final Reply Safety

Proves fixes from NUMBER 41B that require unit-level verification:
  RISK-01: bare affirmations ("تمام"/"اي"/"نعم") must NOT confirm order
  RISK-01: explicit "ثبت" after complete session MUST confirm
  RISK-04: alias edit resolution — "شيل كولا" finds "بيبسي" in session
  RISK-04: alias edit resolution — "غيّر البرگر" finds "برجر لحم" by alias
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOLD = "\033[1m"; RED = "\033[31m"; GRN = "\033[32m"; RST = "\033[0m"
_pass = _fail = 0

def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  {GRN}✓{RST} {label}")
    else:
        _fail += 1
        print(f"  {RED}✗{RST} {label}" + (f"  — {detail}" if detail else ""))


from services.order_brain import (
    OrderSession, OrderItem, CONFIRMATION_KEYWORDS,
    OrderBrain,
)
from services.arabic_normalize import find_product_name_in_session

PRODUCTS = [
    {"name": "برجر لحم",  "price": 8000, "available": 1},
    {"name": "برجر دجاج", "price": 7000, "available": 1},
    {"name": "زينجر",     "price": 8500, "available": 1},
    {"name": "بيبسي",     "price": 1500, "available": 1},
    {"name": "بطاطا",     "price": 3000, "available": 1},
]

print(f"\n{BOLD}{'═'*60}")
print("  NUMBER 41C — Final Reply Safety Tests")
print(f"{'═'*60}{RST}\n")


# ── RISK-01 A: bare affirmations NOT in CONFIRMATION_KEYWORDS ────────────────
print(f"{BOLD}RISK-01 A — Bare affirmations removed from CONFIRMATION_KEYWORDS{RST}")

BARE_AFFIRMATIONS = ["تمام", "اي", "نعم", "ايوه", "آه", "اوكي", "okay", "ok", "صحيح"]
for word in BARE_AFFIRMATIONS:
    check(
        f"'{word}' NOT in CONFIRMATION_KEYWORDS",
        word not in CONFIRMATION_KEYWORDS,
        f"found in list — premature confirmation risk",
    )


# ── RISK-01 B: bare affirmation must NOT flip status on incomplete session ───
print(f"\n{BOLD}RISK-01 B — Bare affirmation must NOT confirm incomplete session{RST}")

for bare in ["تمام", "اي", "نعم", "ايوه"]:
    s = OrderSession(f"test-bare-{bare}", "r1")
    s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
    # session incomplete (missing order_type, address, name, phone, payment)
    OrderBrain.update_from_message(s, bare, PRODUCTS, is_bot_reply=False)
    check(
        f"'{bare}' on incomplete session → status stays collecting",
        s.confirmation_status != "confirmed",
        f"status={s.confirmation_status}",
    )


# ── RISK-01 C: "ثبت" after complete session MUST confirm ────────────────────
print(f"\n{BOLD}RISK-01 C — Explicit 'ثبت' after complete session MUST confirm{RST}")

def _make_complete_session(conv_id: str) -> OrderSession:
    s = OrderSession(conv_id, "r1")
    s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
    s.order_type = "delivery"
    s.address = "الكرادة"
    s.customer_name = "علي"
    s.phone = "07901234567"
    s.payment_method = "كاش"
    return s

EXPLICIT_CONFIRMS = ["ثبت", "أكمل", "ثبته", "اكمل", "نثبتها", "اقفل الطلب"]
for word in EXPLICIT_CONFIRMS:
    s = _make_complete_session(f"test-confirm-{word}")
    assert s.is_complete(), "setup error: session should be complete"
    OrderBrain.update_from_message(s, word, PRODUCTS, is_bot_reply=False)
    check(
        f"'{word}' on complete session → status confirmed",
        s.confirmation_status == "confirmed",
        f"status={s.confirmation_status}",
    )


# ── RISK-01 D: bare affirmation on complete session must NOT confirm ─────────
print(f"\n{BOLD}RISK-01 D — Bare affirmation on complete session must NOT confirm{RST}")

for bare in ["تمام", "اي", "نعم"]:
    s = _make_complete_session(f"test-bare-complete-{bare}")
    assert s.is_complete()
    OrderBrain.update_from_message(s, bare, PRODUCTS, is_bot_reply=False)
    check(
        f"'{bare}' on COMPLETE session → still NOT confirmed (needs explicit keyword)",
        s.confirmation_status != "confirmed",
        f"status={s.confirmation_status}",
    )


# ── RISK-04 A: alias edit — "شيل كولا" finds "بيبسي" in session ─────────────
print(f"\n{BOLD}RISK-04 A — Alias edit: 'شيل كولا' → finds 'بيبسي' in session{RST}")

session_items_cola = [
    OrderItem(name="بيبسي", qty=1, price=1500),
    OrderItem(name="برجر لحم", qty=1, price=8000),
]

# Customer says "شيل كولا" — كولا is an alias for بيبسي
_edit_target = None
for word in "شيل كولا".split():
    matched = find_product_name_in_session(word, session_items_cola)
    if matched:
        _edit_target = matched
        break

check(
    "'كولا' alias resolves to 'بيبسي' in session",
    _edit_target == "بيبسي",
    f"resolved={_edit_target!r}",
)


# ── RISK-04 B: alias edit — "غيّر البرگر" finds "برجر لحم" ─────────────────
print(f"\n{BOLD}RISK-04 B — Alias edit: 'غيّر البرگر' finds 'برجر لحم'{RST}")

session_items_burger = [
    OrderItem(name="برجر لحم", qty=1, price=8000),
    OrderItem(name="بيبسي", qty=1, price=1500),
]

_edit_target2 = None
for word in "غيّر البرگر".split():
    matched = find_product_name_in_session(word, session_items_burger)
    if matched:
        _edit_target2 = matched
        break

check(
    "'برگر' alias resolves to 'برجر لحم' in session",
    _edit_target2 == "برجر لحم",
    f"resolved={_edit_target2!r}",
)


# ── RISK-04 C: exact name match still works (no regression) ─────────────────
print(f"\n{BOLD}RISK-04 C — Exact name match still works (regression){RST}")

session_items_exact = [
    OrderItem(name="زينجر", qty=1, price=8500),
]
_exact = None
for word in "شيل زينجر".split():
    matched = find_product_name_in_session(word, session_items_exact)
    if matched:
        _exact = matched
        break

check(
    "exact 'زينجر' → resolves to 'زينجر'",
    _exact == "زينجر",
    f"resolved={_exact!r}",
)


# ── RISK-04 D: no false positive — unrelated word returns None ───────────────
print(f"\n{BOLD}RISK-04 D — No false positive on unrelated word{RST}")

_false_pos = find_product_name_in_session("غيّر", session_items_burger)
check(
    "'غيّر' alone does NOT match any session item",
    _false_pos is None,
    f"got={_false_pos!r}",
)


# ─────────────────────────────────────────────────────────────────────────────
total = _pass + _fail
pct   = round(100 * _pass / total) if total else 0
print(f"\n{BOLD}{'═'*60}")
print(f"  Result: {_pass}/{total} passed ({pct}%)")
print(f"{'═'*60}{RST}\n")

if _fail:
    print(f"{RED}FAILED — {_fail} test(s) failed{RST}\n")
    sys.exit(1)
else:
    print(f"{GRN}All tests passed.{RST}\n")
    sys.exit(0)
