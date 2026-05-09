#!/usr/bin/env python3
"""
scripts/day36c_next_question_controller_test.py — NUMBER 36C tests.

Tests: deterministic next-question controller + notes fix + order summary.

Usage:
  python3 scripts/day36c_next_question_controller_test.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOLD = "\033[1m"; RED = "\033[31m"; GRN = "\033[32m"; RST = "\033[0m"
_pass = _fail = 0
_results: list = []

def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1; _results.append((True, label, detail))
        print(f"  {GRN}✓{RST} {label}")
    else:
        _fail += 1; _results.append((False, label, detail))
        print(f"  {RED}✗{RST} {label}" + (f"  — {detail}" if detail else ""))


from services.order_brain import OrderSession, OrderItem, _FIELD_QUESTION

PRODUCTS = [
    {"name": "برجر دجاج",   "price": 8000,  "available": 1},
    {"name": "برجر لحم",    "price": 10000, "available": 1},
    {"name": "زينجر",       "price": 7000,  "available": 1},
    {"name": "كولا",        "price": 1500,  "available": 1},
    {"name": "بطاطا مقلية", "price": 2500,  "available": 1},
]

# ── Inline _backend_next_reply (mirrors bot.py nested def) ────────────────────

def _backend_next_reply(ob, prods_list, unknowns, fee=0) -> str:
    if unknowns:
        names_str = "، ".join(f"«{n}»" for n in unknowns[:2])
        extra = " وغيرها" if len(unknowns) > 2 else ""
        return (
            f"ما لقيت {names_str}{extra} بالمنيو 🌷 — "
            f"تكدر تشوف المنيو وتكلني شنو بالضبط تريد؟"
        )
    if ob is None:
        return "وصلت 🌷 — شنو تحب تطلب؟"
    if ob.is_complete():
        return ob.order_summary_for_confirmation(delivery_fee=fee)
    next_f = ob.next_missing_field()
    if next_f == "items":
        return ob.generate_next_directive(prods_list)
    if next_f and next_f in _FIELD_QUESTION:
        return "تمام 🌷 — " + _FIELD_QUESTION[next_f]
    return ob.generate_next_directive(prods_list) or "كمّلنا؟ 🌷"


# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{BOLD}{'═'*58}")
print("  NUMBER 36C — Deterministic Next Question Controller")
print(f"{'═'*58}{RST}\n")


# T1 — Unknown items return clarification ─────────────────────────────────────
print(f"{BOLD}T1 — Unknown items → clarification{RST}")
s = OrderSession(conversation_id="t1", restaurant_id="r1")
r = _backend_next_reply(s, PRODUCTS, ["ساندويچ سري لانكا"])
check("unknown item: name appears in reply", "ساندويچ سري لانكا" in r, r[:80])
check("unknown item: asks to check menu", any(w in r for w in ["منيو", "تكدر"]), r[:80])

# Two unknowns
r2 = _backend_next_reply(s, PRODUCTS, ["وجبة كاجو", "سمبوسة خاصة"])
check("two unknowns: both names in reply", "وجبة كاجو" in r2 and "سمبوسة خاصة" in r2, r2[:80])

# Three unknowns → only first two + وغيرها
r3 = _backend_next_reply(s, PRODUCTS, ["a", "b", "c"])
check("three unknowns: وغيرها appended", "وغيرها" in r3, r3[:80])


# T2 — Step-by-step slot progression ─────────────────────────────────────────
print(f"\n{BOLD}T2 — Slot progression: each step asks correct next field{RST}")

s2 = OrderSession(conversation_id="t2", restaurant_id="r1")

# No items yet → ask for item + menu
r_items = _backend_next_reply(s2, PRODUCTS, [])
check("no items → asks for product (منيو listed)", any(p["name"] in r_items for p in PRODUCTS), r_items[:80])

# After item added → ask order_type
s2.items = [OrderItem(name="برجر دجاج", qty=1, price=8000)]
r_ot = _backend_next_reply(s2, PRODUCTS, [])
check("after item → asks order_type", "توصيل" in r_ot or "استلام" in r_ot, r_ot[:80])
check("after item → prefixed with تمام 🌷", "تمام 🌷" in r_ot, r_ot[:80])

# After delivery chosen → ask address
s2.order_type = "delivery"
r_addr = _backend_next_reply(s2, PRODUCTS, [])
check("delivery chosen → asks address", "عنوان" in r_addr or "وين" in r_addr, r_addr[:80])
check("address question prefixed with تمام 🌷", "تمام 🌷" in r_addr, r_addr[:80])

# After address → ask name
s2.address = "الكرادة"
r_name = _backend_next_reply(s2, PRODUCTS, [])
check("address given → asks name", "شسمك" in r_name or "اسم" in r_name, r_name[:80])

# After name → ask phone
s2.customer_name = "علي"
r_phone = _backend_next_reply(s2, PRODUCTS, [])
check("name given → asks phone", "رقم" in r_phone or "هاتف" in r_phone, r_phone[:80])

# After phone → ask payment
s2.phone = "07901234567"
r_pay = _backend_next_reply(s2, PRODUCTS, [])
check("phone given → asks payment", "كاش" in r_pay or "كارد" in r_pay, r_pay[:80])

# Payment set → is_complete → show summary
s2.payment_method = "كاش"
check("all fields set → is_complete()", s2.is_complete())
r_sum = _backend_next_reply(s2, PRODUCTS, [])
check("complete → summary contains item name", "برجر دجاج" in r_sum, r_sum[:80])
check("complete → summary asks to confirm", "نثبت" in r_sum or "ثبت" in r_sum, r_sum[:80])


# T3 — Pickup: no address asked ───────────────────────────────────────────────
print(f"\n{BOLD}T3 — Pickup order: address is not required{RST}")
s3 = OrderSession(conversation_id="t3", restaurant_id="r1")
s3.items = [OrderItem(name="زينجر", qty=1, price=7000)]
s3.order_type = "pickup"
s3.customer_name = "سارة"
s3.phone = "07912345678"
s3.payment_method = "كارد"
check("pickup, all fields → is_complete()", s3.is_complete(),
      f"missing={s3.missing_fields()}")
r3 = _backend_next_reply(s3, PRODUCTS, [])
check("complete pickup → summary (no address line)", "استلام من المطعم" in r3, r3[:80])


# T4 — order_summary_for_confirmation format ──────────────────────────────────
print(f"\n{BOLD}T4 — order_summary_for_confirmation format{RST}")
s4 = OrderSession(conversation_id="t4", restaurant_id="r1")
s4.items = [
    OrderItem(name="برجر دجاج", qty=2, price=8000, notes="بدون بصل"),
    OrderItem(name="كولا",      qty=1, price=1500),
]
s4.order_type = "delivery"
s4.address = "المنصور"
s4.customer_name = "محمد"
s4.phone = "07701234567"
s4.payment_method = "كاش"

summary = s4.order_summary_for_confirmation(delivery_fee=0)
check("summary: item name present", "برجر دجاج" in summary, summary[:120])
check("summary: qty × shown", "× 2" in summary, summary[:120])
check("summary: notes shown (بدون بصل)", "بدون بصل" in summary, summary[:120])
check("summary: address shown", "المنصور" in summary, summary[:120])
check("summary: customer name shown", "محمد" in summary, summary[:120])
check("summary: phone shown", "07701234567" in summary, summary[:120])
check("summary: payment shown", "كاش" in summary, summary[:120])
check("summary: ends with confirmation ask", "نثبت" in summary, summary[-50:])

# With delivery fee
summary_fee = s4.order_summary_for_confirmation(delivery_fee=2000)
total_expected = (8000 * 2 + 1500 + 2000)
check("summary with fee: total correct",
      f"{total_expected:,}" in summary_fee or str(total_expected) in summary_fee,
      summary_fee[:200])
check("summary with fee: fee line present", "رسوم التوصيل" in summary_fee, summary_fee[:200])


# T5 — notes= fix: OrderItem constructor uses 'notes' not 'note' ──────────────
print(f"\n{BOLD}T5 — OrderItem 'notes' field (not 'note'){RST}")
try:
    item = OrderItem(name="برجر دجاج", qty=1, price=8000, notes="حار")
    check("OrderItem(notes=...) accepted", item.notes == "حار", f"notes={item.notes!r}")
except TypeError as e:
    check("OrderItem(notes=...) accepted", False, str(e))

# Ensure 'note=' keyword raises TypeError (was the old broken call)
try:
    _bad = OrderItem(name="x", qty=1, price=0, note="something")
    check("OrderItem(note=...) should raise TypeError", False, "no error raised")
except TypeError:
    check("OrderItem(note=...) correctly raises TypeError", True)


# T6 — ob is None edge case ───────────────────────────────────────────────────
print(f"\n{BOLD}T6 — Edge cases{RST}")
r_none = _backend_next_reply(None, PRODUCTS, [])
check("ob=None: no crash, returns question", "?" in r_none or "؟" in r_none or r_none, r_none[:80])

r_no_prods = _backend_next_reply(OrderSession("x","r"), [], [])
check("no products: no crash", bool(r_no_prods), r_no_prods[:80])


# ─────────────────────────────────────────────────────────────────────────────
total = _pass + _fail
pct   = round(100 * _pass / total) if total else 0
print(f"\n{BOLD}{'═'*58}")
print(f"  Result: {_pass}/{total} passed ({pct}%)")
print(f"{'═'*58}{RST}\n")
if _fail:
    print("Failed tests:")
    for ok, label, detail in _results:
        if not ok:
            print(f"  {RED}✗{RST} {label}" + (f" — {detail}" if detail else ""))
    sys.exit(1)
else:
    print(f"{GRN}All tests passed.{RST}")
    sys.exit(0)
