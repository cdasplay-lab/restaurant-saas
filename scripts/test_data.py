#!/usr/bin/env python3
"""
Layer 2 — Restaurant Data Tests (DB-driven)
============================================
All expected values (names, prices, availability) are pulled
LIVE from the DB at test time. No hardcoded product data.

Works for any restaurant: change the DB → tests auto-adapt.

Validates:
  • Price accuracy      — bot mentions exact DB price
  • Name recognition    — bot uses the actual product name
  • Availability truth  — bot confirms available / denies unavailable
  • Sold-out handling   — bot offers alternatives for unavailable products
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import test_utils as U

DELAY = 1.0


def _price_check(product, token):
    """Price inquiry → reply must contain the exact DB price."""
    name  = product["name"]
    price = U.price_keywords(product["price"])
    msgs  = f"بكم {name}؟"
    reply = U.simulate(msgs, token)
    score = U.score_reply(reply, price + U.name_keywords(name))

    ok = any(k in reply for k in price)   # price specifically must appear
    return score if ok else 0, reply, msgs, f"price: {name} → {U.price_fmt(product['price'])}"


def _availability_positive(product, token):
    """Available product → bot must confirm it exists."""
    name  = product["name"]
    msgs  = f"{name} موجود اليوم؟"
    reply = U.simulate(msgs, token)
    keywords = ["نعم", "موجود", "اليوم", "أكيد", "إي", "متاح", "عندنا"]
    score = U.score_reply(reply, keywords)
    return score, reply, msgs, f"avail+: {name}"


def _availability_negative(product, token):
    """Unavailable product → bot must not confirm it OR offer alternative."""
    name  = product["name"]
    msgs  = f"أريد {name}"
    reply = U.simulate(msgs, token)
    # Bot should say not available OR suggest alternative
    keywords = ["خلص", "ما عدنا", "غير متوفر", "مو متوفر", "ما موجود",
                "بديل", "يرجع", "بكره", "آسف", "تجرب"]
    # Also acceptable: bot asks for clarification (very long order)
    score = U.score_reply(reply, keywords)
    return score, reply, msgs, f"avail-: {name} (sold out)"


def _name_recognition(product, token):
    """Bot must use the product's actual name (or a clear shorthand)."""
    name  = product["name"]
    words = U.name_keywords(name)         # each word individually
    msgs  = f"شنو هذا المنتج اللي اسمه {name}؟"
    reply = U.simulate(msgs, token)
    score = U.score_reply(reply, words)
    return score, reply, msgs, f"name: {name}"


def _order_total(products, token):
    """
    Order 2 products → reply total should match sum of their prices.
    Uses first 2 available products.
    """
    if len(products) < 2:
        return None
    p1, p2 = products[0], products[1]
    total  = int(p1["price"]) + int(p2["price"])
    total_fmt = U.price_fmt(total)
    alt_total = str(total)
    msgs = f"أريد {p1['name']} و{p2['name']}"
    reply = U.simulate(msgs, token)
    score = U.score_reply(reply, [total_fmt, alt_total,
                                   U.price_fmt(p1["price"]),
                                   U.price_fmt(p2["price"])])
    return score, reply, msgs, f"total: {p1['name']} + {p2['name']} = {total_fmt}"


def run(token=None):
    if token is None:
        token = U.get_token()

    products   = U.get_products(token)
    available  = U.available_products(products)
    unavail    = U.unavailable_products(products)

    print(f"\n{U.BOLD}{'═'*58}")
    print(f"  Restaurant Data Tests (Layer 2)")
    print(f"  Products in DB: {len(products)} total, "
          f"{len(available)} available, {len(unavail)} unavailable")
    print(f"{'═'*58}{U.RST}\n")

    results = []
    qid = 1

    # ── Price checks (all available products) ────────────────────────────────
    print(f"{U.BOLD}── Price checks ──────────────────────────────────────{U.RST}")
    for p in available:
        score, reply, msgs, label = _price_check(p, token)
        col   = U.GRN if score == 2 else (U.YLW if score == 1 else U.RED)
        short = reply[:65] + ("…" if len(reply) > 65 else "")
        print(f"  [{col}{score}{U.RST}] [{qid:3}] {label:<40} → {short}")
        results.append((qid, score, reply, label))
        qid += 1
        import time; time.sleep(DELAY)

    # ── Availability: available ───────────────────────────────────────────────
    print(f"\n{U.BOLD}── Availability (positive) ────────────────────────────{U.RST}")
    for p in available:
        score, reply, msgs, label = _availability_positive(p, token)
        col   = U.GRN if score == 2 else (U.YLW if score == 1 else U.RED)
        short = reply[:65] + ("…" if len(reply) > 65 else "")
        print(f"  [{col}{score}{U.RST}] [{qid:3}] {label:<40} → {short}")
        results.append((qid, score, reply, label))
        qid += 1
        import time; time.sleep(DELAY)

    # ── Availability: sold out ────────────────────────────────────────────────
    if unavail:
        print(f"\n{U.BOLD}── Availability (sold out) ────────────────────────────{U.RST}")
        for p in unavail:
            score, reply, msgs, label = _availability_negative(p, token)
            col   = U.GRN if score == 2 else (U.YLW if score == 1 else U.RED)
            short = reply[:65] + ("…" if len(reply) > 65 else "")
            print(f"  [{col}{score}{U.RST}] [{qid:3}] {label:<40} → {short}")
            results.append((qid, score, reply, label))
            qid += 1
            import time; time.sleep(DELAY)
    else:
        print(f"\n  (no unavailable products in DB — sold-out tests skipped)")

    # ── Order total (first 2 products) ───────────────────────────────────────
    print(f"\n{U.BOLD}── Order total check ──────────────────────────────────{U.RST}")
    res = _order_total(available, token)
    if res:
        score, reply, msgs, label = res
        col   = U.GRN if score == 2 else (U.YLW if score == 1 else U.RED)
        short = reply[:65] + ("…" if len(reply) > 65 else "")
        print(f"  [{col}{score}{U.RST}] [{qid:3}] {label:<40} → {short}")
        results.append((qid, score, reply, label))
        qid += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = sum(1 for _, s, *_ in results if s > 0)
    total  = len(results)
    pct    = passed * 100 // total if total else 0
    col    = U.GRN if pct >= 97 else (U.YLW if pct >= 90 else U.RED)

    print(f"\n{U.BOLD}{'═'*58}")
    print(f"  PASS {passed}/{total} ({pct}%)")
    print(f"{'═'*58}{U.RST}")

    failures = [(qid, r, lbl) for qid, s, r, lbl in results if s == 0]
    if failures:
        print(f"\n{U.RED}{U.BOLD}FAILURES:{U.RST}")
        for fid, r, lbl in failures:
            print(f"  [{fid:3}] {lbl}")
            print(f"        رد: {r[:80]}")

    return passed, total, pct


if __name__ == "__main__":
    run()
