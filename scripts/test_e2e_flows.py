#!/usr/bin/env python3
"""
Layer 3 — End-to-End Flow Tests
=================================
Tests complete multi-turn conversation flows.
Product names/prices pulled from DB at runtime.

Flows:
  F1  Order flow            — select → customize → address → phone → confirm
  F2  Story-to-order flow   — story context → order → address
  F3  Change-of-mind flow   — order → change → re-order → confirm
  F4  Handoff flow          — order → complaint → handoff triggered
  F5  Cancellation flow     — order → full cancel → empty order
"""
import sys, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import test_utils as U

DELAY = 1.2


def _section(title):
    print(f"\n{U.BOLD}── {title} {'─'*(50-len(title))}{U.RST}")


def _check(label, reply, keywords, banned=None):
    score = U.score_reply(reply, keywords)
    ok_banned = U.none_of(reply, banned) if banned else True
    passed = score > 0 and ok_banned
    col    = U.GRN if passed else U.RED
    symbol = "✅" if passed else "❌"
    short  = reply[:70] + ("…" if len(reply) > 70 else "")
    print(f"  {symbol} {col}{label:<40}{U.RST} → {short}")
    return passed


def run(token=None):
    if token is None:
        token = U.get_token()

    products = U.available_products(U.get_products(token))
    if not products:
        print("❌ No available products in DB — cannot run flow tests")
        return 0, 5, 0

    p1 = products[0]
    p2 = products[1] if len(products) > 1 else products[0]
    p1n, p1p = p1["name"], U.price_fmt(p1["price"])
    p2n, p2p = p2["name"], U.price_fmt(p2["price"])

    print(f"\n{U.BOLD}{'═'*58}")
    print(f"  End-to-End Flow Tests (Layer 3)")
    print(f"  Using: [{p1n} — {p1p}] & [{p2n} — {p2p}]")
    print(f"{'═'*58}{U.RST}")

    results = []

    # ─────────────────────────────────────────────────────────────────────────
    # F1: Full order flow
    # ─────────────────────────────────────────────────────────────────────────
    _section("F1: Full Order Flow")
    flow = []
    passes = []

    flow.append(f"أريد {p1n}")
    r = U.simulate(flow[:], token); time.sleep(DELAY)
    passes.append(_check("طلب المنتج", r,
        U.name_keywords(p1n) + [U.price_fmt(p1["price"]), "🌷", "أكيد"]))

    flow.append("بدون بصل")
    r = U.simulate(flow[:], token); time.sleep(DELAY)
    passes.append(_check("تخصيص: بدون بصل", r,
        ["🌷", "بدون", "بصل", "تمام", "أكيد"]))

    flow.append("عنواني حي الجادرية شارع 5")
    r = U.simulate(flow[:], token); time.sleep(DELAY)
    passes.append(_check("استقبال العنوان", r,
        ["وصلت", "🌷", "الجادرية", "عنوان", "تمام"]))

    flow.append("07901111222")
    r = U.simulate(flow[:], token); time.sleep(DELAY)
    passes.append(_check("استقبال الرقم", r,
        ["وصلت", "🌷", "تمام", "نكمل", "رقم"]))

    f1_ok = sum(passes)
    results.append(("F1", f1_ok, 4, "Full Order Flow"))
    print(f"  → F1: {f1_ok}/4 passed")

    # ─────────────────────────────────────────────────────────────────────────
    # F2: Story-to-order flow
    # ─────────────────────────────────────────────────────────────────────────
    _section("F2: Story-to-Order Flow")
    story_msg = (
        f"[العميل يرد على ستوري يعرض: {p1n} — {p1p} د.ع]\n"
        f"سياق للبوت: هذا المنتج موجود في قائمتك. استغل الفرصة وابدأ flow البيع مباشرة.\n"
        f"رد العميل: أريد هذا"
    )
    flow2 = [story_msg]
    passes2 = []

    r = U.simulate(flow2[:], token); time.sleep(DELAY)
    passes2.append(_check("ستوري → استجابة بيعية", r,
        U.name_keywords(p1n) + [U.price_fmt(p1["price"]), "🌷", "أكيد"]))

    flow2.append("عنواني الكرادة داخل")
    r = U.simulate(flow2[:], token); time.sleep(DELAY)
    passes2.append(_check("ستوري → استقبال عنوان", r,
        ["وصلت", "🌷", "الكرادة", "تمام"]))

    f2_ok = sum(passes2)
    results.append(("F2", f2_ok, 2, "Story-to-Order"))
    print(f"  → F2: {f2_ok}/2 passed")

    # ─────────────────────────────────────────────────────────────────────────
    # F3: Change-of-mind flow
    # ─────────────────────────────────────────────────────────────────────────
    _section("F3: Change-of-Mind Flow")
    flow3 = [f"أريد {p1n}"]
    passes3 = []

    flow3.append("شيله")
    r = U.simulate(flow3[:], token); time.sleep(DELAY)
    passes3.append(_check("شيل المنتج", r,
        ["تمام", "🌷", "شلناه", "شلنا", "حذفنا", "أزلنا"]))

    flow3.append(f"أريد {p2n} بدل")
    r = U.simulate(flow3[:], token); time.sleep(DELAY)
    passes3.append(_check(f"طلب {p2n} بدل", r,
        U.name_keywords(p2n) + ["🌷", "أكيد", "تمام"]))

    f3_ok = sum(passes3)
    results.append(("F3", f3_ok, 2, "Change-of-Mind"))
    print(f"  → F3: {f3_ok}/2 passed")

    # ─────────────────────────────────────────────────────────────────────────
    # F4: Handoff flow
    # ─────────────────────────────────────────────────────────────────────────
    _section("F4: Handoff Flow")
    flow4 = [f"أريد {p1n}", "الطلب السابق ما وصل"]
    passes4 = []

    r = U.simulate(flow4[:], token); time.sleep(DELAY)
    passes4.append(_check("شكوى → handoff", r,
        ["آسف", "موظف", "فريق", "يتواصل", "يتابع"]))

    flow4.append("أريد موظف بشري")
    r = U.simulate(flow4[:], token); time.sleep(DELAY)
    passes4.append(_check("طلب صريح → handoff", r,
        ["موظف", "بشري", "فريق", "إنسان", "يتواصل"]))

    f4_ok = sum(passes4)
    results.append(("F4", f4_ok, 2, "Handoff Flow"))
    print(f"  → F4: {f4_ok}/2 passed")

    # ─────────────────────────────────────────────────────────────────────────
    # F5: Cancellation flow
    # ─────────────────────────────────────────────────────────────────────────
    _section("F5: Cancellation / Full Cancel")
    flow5 = [f"أريد {p1n}", f"وأريد {p2n}"]
    passes5 = []

    r = U.simulate(flow5[:], token); time.sleep(DELAY)
    passes5.append(_check("طلب منتجين", r,
        U.name_keywords(p1n) + U.name_keywords(p2n) +
        [U.price_fmt(p1["price"]), U.price_fmt(p2["price"])]))

    flow5.append("ألغ الطلب كله")
    r = U.simulate(flow5[:], token); time.sleep(DELAY)
    passes5.append(_check("إلغاء الطلب", r,
        ["إلغاء", "ألغي", "ألغينا", "تمام", "آسف", "مسح"]))

    f5_ok = sum(passes5)
    results.append(("F5", f5_ok, 2, "Cancellation"))
    print(f"  → F5: {f5_ok}/2 passed")

    # ─────────────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────────────
    total_pass  = sum(ok for _, ok, _, _ in results)
    total_count = sum(cnt for _, _, cnt, _ in results)
    pct = total_pass * 100 // total_count if total_count else 0
    col = U.GRN if pct >= 90 else (U.YLW if pct >= 75 else U.RED)

    print(f"\n{U.BOLD}{'═'*58}")
    print(f"  Flow Results:")
    for code, ok, cnt, label in results:
        c = U.GRN if ok == cnt else (U.YLW if ok >= cnt * 0.75 else U.RED)
        print(f"    {c}{code} — {label}: {ok}/{cnt}{U.RST}")
    print(f"\n  TOTAL PASS {total_pass}/{total_count} ({pct}%)")
    print(f"{'═'*58}{U.RST}")

    return total_pass, total_count, pct


if __name__ == "__main__":
    run()
