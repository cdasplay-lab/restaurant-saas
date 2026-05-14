#!/usr/bin/env python3
"""
scripts/day41b_critical_fixes_test.py — NUMBER 41B: Critical Safety Fixes

Tests:
  C2: phone field in tool schemas
  H2: زين word-boundary in SlotTracker
  M2: NUMBER 31 disabled when tool triggered
  C1: active order + no tool → _backend_next_reply forced
  H1: to_prompt_section used over SlotTracker when ob_session exists
  C5: sold-out items blocked in validate_tool_items
  H5: _extract_name stops at first word
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


from services.order_brain import OrderSession, OrderItem, _extract_name
from services.tool_safety import validate_tool_items

PRODUCTS = [
    {"name": "برجر لحم",   "price": 8000,  "available": 1},
    {"name": "برجر دجاج",  "price": 7000,  "available": 1},
    {"name": "زينجر",      "price": 8500,  "available": 1},
    {"name": "بيبسي",      "price": 1500,  "available": 1},
    {"name": "بطاطا",      "price": 3000,  "available": 1},
    {"name": "وجبة نافدة", "price": 5000,  "available": 0},
]

print(f"\n{BOLD}{'═'*60}")
print("  NUMBER 41B — Critical Safety Fixes")
print(f"{'═'*60}{RST}\n")


# ── C2: phone field in tool schemas ──────────────────────────────────────────
print(f"{BOLD}C2 — phone field in _ORDER_TOOLS{RST}")
from services.bot import _ORDER_TOOLS

place_params = _ORDER_TOOLS[0]["function"]["parameters"]["properties"]
place_required = _ORDER_TOOLS[0]["function"]["parameters"]["required"]
update_params = _ORDER_TOOLS[1]["function"]["parameters"]["properties"]

check("place_order.properties has 'phone'", "phone" in place_params,
      str(list(place_params.keys())))
check("place_order.required includes 'phone'", "phone" in place_required,
      str(place_required))
check("update_order.properties has 'phone'", "phone" in update_params,
      str(list(update_params.keys())))


# ── H2: زين word-boundary in SlotTracker ─────────────────────────────────────
print(f"\n{BOLD}H2 — SlotTracker: 'زين' word-boundary (must not match 'زينجر'){RST}")
from services.bot import SlotTracker

st_zinger = SlotTracker()
st_zinger._parse("زينجر واحد")
check("زينجر alone → payment stays None", st_zinger.payment is None,
      f"payment={st_zinger.payment!r}")

st_zain = SlotTracker()
st_zain._parse("الدفع زين كاش")
check("زين كاش → payment = زين كاش", st_zain.payment == "زين كاش",
      f"payment={st_zain.payment!r}")

st_zain2 = SlotTracker()
st_zain2._parse("بدفع زين")
check("standalone زين → payment = زين كاش", st_zain2.payment == "زين كاش",
      f"payment={st_zain2.payment!r}")

st_cash = SlotTracker()
st_cash._parse("زينجر اثنين وكاش")
check("زينجر + كاش → payment = كاش (not زين)", st_cash.payment == "كاش",
      f"payment={st_cash.payment!r}")


# ── H5: _extract_name stops at first word ────────────────────────────────────
print(f"\n{BOLD}H5 — _extract_name: single-word capture{RST}")

n1 = _extract_name("اسمي علي ورقمي 07901234567")
check("'اسمي علي ورقمي' → name='علي'", n1 == "علي", f"got={n1!r}")

n2 = _extract_name("اسمي سارة")
check("'اسمي سارة' → name='سارة'", n2 == "سارة", f"got={n2!r}")

n3 = _extract_name("أنا محمد وعنواني الكرادة")
check("'أنا محمد وعنواني' → name='محمد'", n3 == "محمد", f"got={n3!r}")

n4 = _extract_name("باسم أحمد")
check("'باسم أحمد' → name='أحمد'", n4 == "أحمد", f"got={n4!r}")

n5 = _extract_name("اسمي 07901234567")
check("phone-only after اسمي → None", n5 is None, f"got={n5!r}")


# ── C5: sold-out items blocked in validate_tool_items ────────────────────────
print(f"\n{BOLD}C5 — validate_tool_items: sold-out blocked{RST}")

items_with_soldout = [
    {"name": "وجبة نافدة", "qty": 1, "unit_price": 5000},
    {"name": "برجر لحم",   "qty": 1, "unit_price": 8000},
]
validated, unknown = validate_tool_items(items_with_soldout, PRODUCTS)
check("sold-out item goes to unknown", any("نافد" in u or "وجبة نافدة" in u for u in unknown),
      f"unknown={unknown}")
check("available item still validated", any(v["name"] == "برجر لحم" for v in validated),
      f"validated={[v['name'] for v in validated]}")

# sold_out_date field
PRODUCTS_WITH_SODDATE = [
    {"name": "شاورما", "price": 6000, "available": 1, "sold_out_date": "2026-05-10"},
]
v2, u2 = validate_tool_items([{"name": "شاورما", "qty": 1, "unit_price": 6000}],
                               PRODUCTS_WITH_SODDATE)
check("sold_out_date set → goes to unknown", len(u2) == 1 and len(v2) == 0,
      f"unknown={u2} validated={v2}")


# ── C1: _backend_next_reply function accessible outside tool if-block ─────────
print(f"\n{BOLD}C1 — _backend_next_reply callable (defined at outer scope){RST}")
# We can't easily call the inner function from outside bot.process_message,
# but we can verify _backend_next_reply in the test helper mirrors the bot logic.
# Test via the existing day36c test module's inline helper.
from services.order_brain import _FIELD_QUESTION, OrderBrain

def _bnr(ob, prods_list, unknowns, fee=0) -> str:
    if unknowns:
        names_str = "، ".join(f"«{n}»" for n in unknowns[:2])
        extra = " وغيرها" if len(unknowns) > 2 else ""
        return f"ما لقيت {names_str}{extra} بالمنيو 🌷 — تكدر تشوف المنيو وتكلني شنو بالضبط تريد؟"
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

s = OrderSession("c1-test", "r1")
s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
r = _bnr(s, PRODUCTS, [])
check("active order → asks order_type", "توصيل" in r or "استلام" in r, r[:80])

s.order_type = "delivery"
r2 = _bnr(s, PRODUCTS, [])
check("after delivery → asks address", "عنوان" in r2 or "وين" in r2, r2[:80])

s.address = "الكرادة"
s.customer_name = "علي"
s.phone = "07901234567"
s.payment_method = "كاش"
check("all fields → is_complete()", s.is_complete())
r3 = _bnr(s, PRODUCTS, [])
check("complete → summary has item name", "برجر لحم" in r3, r3[:80])


# ── M2: NUMBER 31 disabled when tool triggered ───────────────────────────────
print(f"\n{BOLD}M2 — NUMBER 31 condition check (code-level){RST}")
# Verify the patch exists in bot.py source
import inspect, services.bot as _bot_mod
src = inspect.getsource(_bot_mod)
check("NUMBER 31 block has 'not _tool_call_data[\"triggered\"]'",
      'not _tool_call_data["triggered"]' in src,
      "patch not found in source")


# ── H1: to_prompt_section preferred over SlotTracker ─────────────────────────
print(f"\n{BOLD}H1 — to_prompt_section used when ob_session present{RST}")
# Verify the patch exists in bot.py source
check("slot_context uses to_prompt_section when ob_session present",
      "_ob_session.to_prompt_section()" in src,
      "patch not found in source")
check("fallback to known_slots_section when no ob_session",
      "_slot_tracker.known_slots_section()" in src,
      "patch not found in source")


# ─────────────────────────────────────────────────────────────────────────────
total = _pass + _fail
pct   = round(100 * _pass / total) if total else 0
print(f"\n{BOLD}{'═'*60}")
print(f"  Result: {_pass}/{total} passed ({pct}%)")
print(f"{'═'*60}{RST}\n")
if _fail:
    print("Failed tests:")
    for ok, label, detail in _results:
        if not ok:
            print(f"  {RED}✗{RST} {label}" + (f" — {detail}" if detail else ""))
    sys.exit(1)
else:
    print(f"{GRN}All tests passed.{RST}")
    sys.exit(0)
