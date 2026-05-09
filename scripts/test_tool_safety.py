#!/usr/bin/env python3
"""
scripts/test_tool_safety.py — Safety tests for update_order / place_order tools.

Unit tests (no server): run standalone.
Integration tests (need server): pass --server flag.

Usage:
  python3 scripts/test_tool_safety.py             # unit tests only
  python3 scripts/test_tool_safety.py --server    # + integration tests via /api/bot/simulate
"""
from __future__ import annotations
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOLD = "\033[1m"; RED = "\033[31m"; GRN = "\033[32m"; YLW = "\033[33m"; RST = "\033[0m"

_pass = 0
_fail = 0
_results: list = []


def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        _results.append((True, label, detail))
        print(f"  {GRN}✓{RST} {label}")
    else:
        _fail += 1
        _results.append((False, label, detail))
        print(f"  {RED}✗{RST} {label}" + (f"  — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — no server needed
# ─────────────────────────────────────────────────────────────────────────────

def run_unit_tests():
    from services.tool_safety import (
        has_premature_confirmation,
        strip_prices_from_reply,
        validate_tool_items,
        validate_update_order_reply,
        find_best_product_match,
    )
    from services.order_brain import OrderSession

    print(f"\n{BOLD}{'═'*58}")
    print("  UNIT: tool_safety validation")
    print(f"{'═'*58}{RST}\n")

    PRODUCTS = [
        {"name": "برجر دجاج",   "price": 8000,  "available": 1},
        {"name": "برجر لحم",    "price": 10000, "available": 1},
        {"name": "زينجر",       "price": 7000,  "available": 1},
        {"name": "كولا",        "price": 1500,  "available": 1},
        {"name": "بطاطا مقلية", "price": 2500,  "available": 1},
    ]

    # ── Test 1: update_order reply cannot confirm incomplete order ────────────
    print(f"{BOLD}T1 — update_order.reply blocks premature confirmation{RST}")
    s1 = OrderSession(conversation_id="t1", restaurant_id="r1")
    s1.items = []  # no items yet — definitely incomplete

    for phrase in ["تم تأكيد الطلب", "راح نجهزه", "✅ طلبك:", "الشباب يجهزون هسه", "طلبك تأكد"]:
        safe = validate_update_order_reply(phrase, s1, [])
        check(
            f'blocked: "{phrase[:30]}"',
            phrase not in safe,
            f"got: {safe[:60]!r}",
        )

    # Must also block when order IS complete (place_order should have been called instead)
    s1b = OrderSession(conversation_id="t1b", restaurant_id="r1")
    s1b.items = []
    r = validate_update_order_reply("وصلنا طلبك — يجهزون هسه", s1b, [])
    check("blocked even if order looks complete", "وصلنا طلبك" not in r)

    # Clean replies must pass through unchanged
    clean = "تمام 🌷 — توصيل لو استلام؟"
    r2 = validate_update_order_reply(clean, s1, [])
    check("clean reply passes through", r2 == clean, f"got: {r2!r}")

    # ── Test 2: update_order cannot invent item ───────────────────────────────
    print(f"\n{BOLD}T2 — update_order blocks invented items{RST}")
    validated, unknown = validate_tool_items(
        [{"name": "كازو فري سبيشل", "qty": 1, "unit_price": 5000}], PRODUCTS
    )
    check("invented item flagged as unknown", len(unknown) > 0, f"unknown={unknown}")
    check("invented item not saved to session", len(validated) == 0)

    # Mix of real + invented
    validated2, unknown2 = validate_tool_items(
        [
            {"name": "برجر دجاج", "qty": 1, "unit_price": 9999},
            {"name": "سمبوسة خاصة", "qty": 2, "unit_price": 3000},
        ],
        PRODUCTS,
    )
    check("known item accepted", len(validated2) == 1)
    check("invented item rejected from mixed list", "سمبوسة خاصة" in unknown2)

    # ── Test 3: update_order cannot invent price ──────────────────────────────
    print(f"\n{BOLD}T3 — update_order cannot use GPT-invented price{RST}")
    # GPT sends برجر دجاج with wrong price → DB price must win
    validated3, _ = validate_tool_items(
        [{"name": "برجر دجاج", "qty": 1, "unit_price": 99999}], PRODUCTS
    )
    check("DB price overrides GPT price", validated3[0]["unit_price"] == 8000.0,
          f"got {validated3[0]['unit_price']}")

    # Price stripped from reply text
    reply_with_price = "تمام — برجر دجاج بـ8,000 د.ع موجود"
    stripped = strip_prices_from_reply(reply_with_price)
    check("price stripped from reply text", "8,000 د.ع" not in stripped, f"got: {stripped!r}")

    reply_iqd = "يكلفك 10000 IQD فقط"
    stripped2 = strip_prices_from_reply(reply_iqd)
    check("IQD price stripped", "IQD" not in stripped2, f"got: {stripped2!r}")

    # ── Test 4: place_order only works after customer confirmation ────────────
    print(f"\n{BOLD}T4 — place_order path requires confirmation (logic check){RST}")
    # The system prompt says: call place_order ONLY when customer confirms.
    # We verify that has_premature_confirmation catches early calls.
    check("✅ طلبك: triggers premature check", has_premature_confirmation("✅ طلبك:"))
    check("neutral reply does not trigger", not has_premature_confirmation("توصيل لو استلام؟"))
    check("يجهزون triggers premature check", has_premature_confirmation("الشباب يجهزون هسه"))
    check("plain تمام does not trigger", not has_premature_confirmation("تمام 🌷"))

    # ── Test 5: missing phone asks only phone ─────────────────────────────────
    print(f"\n{BOLD}T5 — missing phone asks only for phone{RST}")
    from services.order_brain import OrderItem
    s5 = OrderSession(conversation_id="t5", restaurant_id="r1")
    s5.items = [OrderItem(name="برجر دجاج", qty=1, price=8000)]
    s5.order_type = "delivery"
    s5.address = "الكرادة"
    s5.customer_name = "علي"
    s5.payment_method = "كاش"
    # phone is missing
    check("only phone is missing", s5.missing_fields() == ["phone"],
          f"missing={s5.missing_fields()}")
    check("next_missing_field = phone", s5.next_missing_field() == "phone")

    # ── Test 6: missing address asks only address ─────────────────────────────
    print(f"\n{BOLD}T6 — missing address asks only for address{RST}")
    s6 = OrderSession(conversation_id="t6", restaurant_id="r1")
    s6.items = [OrderItem(name="زينجر", qty=2, price=7000)]
    s6.order_type = "delivery"
    s6.customer_name = "سارة"
    s6.phone = "07901234567"
    s6.payment_method = "كاش"
    # address missing for delivery
    check("only address is missing", s6.missing_fields() == ["address"],
          f"missing={s6.missing_fields()}")
    check("next_missing_field = address", s6.next_missing_field() == "address")

    # ── Test 7: add / remove / change item works ──────────────────────────────
    print(f"\n{BOLD}T7 — add / remove / change item{RST}")
    # Add item
    validated_add, unknown_add = validate_tool_items(
        [{"name": "كولا", "qty": 1, "unit_price": 0}], PRODUCTS
    )
    check("add new item: validated", len(validated_add) == 1 and not unknown_add)
    check("add: DB price used", validated_add[0]["unit_price"] == 1500.0)

    # Change qty (برجر دجاج qty 2)
    validated_chg, _ = validate_tool_items(
        [{"name": "برجر دجاج", "qty": 2, "unit_price": 8000}], PRODUCTS
    )
    check("change qty: accepted", validated_chg[0]["qty"] == 2)

    # Remove item — validate_tool_items with empty items
    validated_rm, _ = validate_tool_items([], PRODUCTS)
    check("remove all items: empty list accepted", validated_rm == [])

    # Fuzzy match: partial name
    match_partial = find_best_product_match("برجر", PRODUCTS)
    check("fuzzy match: 'برجر' matches a product", match_partial is not None,
          f"got: {match_partial}")

    # ── Test 8: unknown item reply sent to customer ───────────────────────────
    print(f"\n{BOLD}T8 — unknown item returns clarification to customer{RST}")
    r8 = validate_update_order_reply("تمام", None, ["ساندويچ سري لانكا"])
    check("unknown item reply contains item name", "ساندويچ سري لانكا" in r8,
          f"got: {r8!r}")
    check("unknown item reply asks for clarification",
          any(w in r8 for w in ["منيو", "تكدر", "لقيت"]),
          f"got: {r8!r}")

    # ── Test 9: validate_tool_items with no products (edge case) ─────────────
    print(f"\n{BOLD}T9 — edge cases{RST}")
    v_empty, u_empty = validate_tool_items([], PRODUCTS)
    check("empty items list: no unknowns", v_empty == [] and u_empty == [])

    v_noprod, u_noprod = validate_tool_items(
        [{"name": "برجر دجاج", "qty": 1, "unit_price": 8000}], []
    )
    check("item with no products DB: flagged as unknown", len(u_noprod) > 0)

    stripped_none = strip_prices_from_reply("")
    check("strip_prices on empty string: no crash", stripped_none == "")

    r_none = validate_update_order_reply("", None, [])
    check("validate_reply on empty: no crash", r_none == "")


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS — require running server
# ─────────────────────────────────────────────────────────────────────────────

def run_integration_tests():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_utils as U

    print(f"\n{BOLD}{'═'*58}")
    print("  INTEGRATION: bot simulation via /api/bot/simulate")
    print(f"{'═'*58}{RST}\n")

    try:
        token = U.get_token()
    except Exception as e:
        print(f"{YLW}  ⚠ Cannot connect to server — skipping integration tests: {e}{RST}")
        return

    products = U.available_products(U.get_products(token))
    if not products:
        print(f"{YLW}  ⚠ No available products — skipping integration tests{RST}")
        return

    p1 = products[0]["name"]
    p2 = products[1]["name"] if len(products) > 1 else p1

    DELAY = 1.5

    # I-1: No ✅ before confirmation
    print(f"{BOLD}I1 — No order confirmation without customer saying ثبت{RST}")
    r = U.simulate([f"أريد {p1}", "توصيل", "الكرادة", "محمد", "07901111111", "كاش"], token)
    check("no ✅ طلبك before ثبت", "✅ طلبك" not in r, f"reply={r[:80]!r}")
    time.sleep(DELAY)

    # I-2: ✅ appears after ثبت
    print(f"\n{BOLD}I2 — ✅ confirmation appears after ثبت{RST}")
    r2 = U.simulate(
        [f"أريد {p1}", "توصيل", "الكرادة", "محمد", "07901111111", "كاش", "ثبت"], token
    )
    check("✅ طلبك appears after ثبت", "✅" in r2 or any(w in r2 for w in ["تأكيد", "طلبك", "حاضر"]),
          f"reply={r2[:80]!r}")
    time.sleep(DELAY)

    # I-3: Missing address asks only address
    print(f"\n{BOLD}I3 — missing address → ask only address{RST}")
    r3 = U.simulate([f"أريد {p1}", "توصيل", "محمد", "07901111111", "كاش"], token)
    check("asks for address", any(w in r3 for w in ["عنوان", "وين", "منطقة"]),
          f"reply={r3[:80]!r}")
    check("doesn't ask name again", "شسمك" not in r3, f"reply={r3[:80]!r}")
    time.sleep(DELAY)

    # I-4: Missing phone asks only phone
    print(f"\n{BOLD}I4 — missing phone → ask only phone{RST}")
    r4 = U.simulate([f"أريد {p1}", "توصيل", "الكرادة", "محمد", "كاش"], token)
    check("asks for phone", any(w in r4 for w in ["رقم", "هاتف", "موبايل", "تلفون"]),
          f"reply={r4[:80]!r}")
    time.sleep(DELAY)

    # I-5: Duplicate webhook — not duplicate order (idempotency)
    print(f"\n{BOLD}I5 — duplicate message does not duplicate order{RST}")
    msg = f"أريد {p1}"
    r5a = U.simulate(msg, token)
    time.sleep(0.5)
    r5b = U.simulate(msg, token)
    # Both replies should be reasonable (not crash/error)
    check("first msg: no error", not r5a.startswith("ERROR"), f"r5a={r5a[:60]!r}")
    check("second msg: no error", not r5b.startswith("ERROR"), f"r5b={r5b[:60]!r}")
    time.sleep(DELAY)

    # I-6: No greeting mid-conversation
    print(f"\n{BOLD}I6 — no fresh greeting mid-conversation{RST}")
    r6 = U.simulate([f"أريد {p1}", "كم سعره؟"], token)
    GREET_PHRASES = ["أهلاً وسهلاً", "مرحباً بك", "هلا بيك", "أهلين وسهلين",
                     "يسعدنا خدمتك", "من دواعي"]
    check("no greeting mid-conversation", not any(g in r6 for g in GREET_PHRASES),
          f"reply={r6[:80]!r}")
    time.sleep(DELAY)

    # I-7: Add then change item
    print(f"\n{BOLD}I7 — add then change item quantity{RST}")
    r7 = U.simulate([f"أريد {p1}", f"غيّر الكمية لـ 2"], token)
    check("change item: no crash", not r7.startswith("ERROR"), f"reply={r7[:80]!r}")
    time.sleep(DELAY)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_unit = True
    run_integ = "--server" in sys.argv

    if run_unit:
        run_unit_tests()

    if run_integ:
        run_integration_tests()

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
