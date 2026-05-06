"""
NUMBER 33+34 — Upselling Engine + Order Status Queries Tests
Run: python3 scripts/day30_upsell_status_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.order_brain import OrderSession, OrderItem, OrderBrain

_passed = 0
_failed = 0

PRODUCTS = [
    {"id": "p1", "name": "برجر", "price": 8000, "available": True},
    {"id": "p2", "name": "زينجر", "price": 9000, "available": True},
    {"id": "p3", "name": "كولا", "price": 1500, "available": True},
    {"id": "p4", "name": "بطاطا", "price": 2000, "available": True},
    {"id": "p5", "name": "بروستد", "price": 7500, "available": True},
]


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


# ── A. Upsell suggestion generation ─────────────────────────────────────────
print("\n── A. Upsell suggestion ──")


def test_A01_main_no_drink_suggests_drink():
    sess = fresh("a01")
    sess.items.append(OrderItem("برجر", 1, 8000))
    result = sess._get_upsell_suggestion(PRODUCTS)
    if "كولا" in result or "مشروب" in result:
        ok("A01 main dish alone → suggests drink (كولا)")
    else:
        fail("A01 main no drink", f"got: {result!r}")


def test_A02_main_and_drink_no_side_suggests_side():
    sess = fresh("a02")
    sess.items.append(OrderItem("برجر", 1, 8000))
    sess.items.append(OrderItem("كولا", 1, 1500))
    result = sess._get_upsell_suggestion(PRODUCTS)
    if "بطاطا" in result or "فرايز" in result:
        ok("A02 main + drink → suggests side (بطاطا)")
    else:
        fail("A02 main+drink no side", f"got: {result!r}")


def test_A03_full_order_no_upsell():
    sess = fresh("a03")
    sess.items.append(OrderItem("برجر", 1, 8000))
    sess.items.append(OrderItem("كولا", 1, 1500))
    sess.items.append(OrderItem("بطاطا", 1, 2000))
    result = sess._get_upsell_suggestion(PRODUCTS)
    if result == "":
        ok("A03 full order (main+drink+side) → no upsell")
    else:
        fail("A03 full order upsell", f"got: {result!r}")


def test_A04_only_drink_no_upsell():
    sess = fresh("a04")
    sess.items.append(OrderItem("كولا", 2, 1500))
    result = sess._get_upsell_suggestion(PRODUCTS)
    if result == "":
        ok("A04 drink only → no upsell (don't push food)")
    else:
        fail("A04 drink only upsell", f"got: {result!r}")


def test_A05_empty_session_no_upsell():
    sess = fresh("a05")
    result = sess._get_upsell_suggestion(PRODUCTS)
    if result == "":
        ok("A05 empty session → no upsell")
    else:
        fail("A05 empty upsell", f"got: {result!r}")


test_A01_main_no_drink_suggests_drink()
test_A02_main_and_drink_no_side_suggests_side()
test_A03_full_order_no_upsell()
test_A04_only_drink_no_upsell()
test_A05_empty_session_no_upsell()


# ── B. Upsell timing in directive ────────────────────────────────────────────
print("\n── B. Upsell directive timing ──")


def test_B01_directive_upsell_before_order_type():
    sess = fresh("b01")
    sess.items.append(OrderItem("برجر", 1, 8000))
    # Next missing is order_type — but upsell should intercept first
    directive = sess.generate_next_directive(PRODUCTS)
    if "كولا" in directive or "مشروب" in directive or "نضيف" in directive:
        ok("B01 first directive for main dish → upsell (not توصيل question)")
    else:
        fail("B01 directive not upsell", f"got: {directive!r}")


def test_B02_second_call_gives_order_type():
    sess = fresh("b02")
    sess.items.append(OrderItem("برجر", 1, 8000))
    # First call sets upsell_offered=True
    sess.generate_next_directive(PRODUCTS)
    assert sess.upsell_offered, "upsell_offered should be True after first call"
    # Second call should give order_type question
    directive2 = sess.generate_next_directive(PRODUCTS)
    if "توصيل" in directive2 or "استلام" in directive2:
        ok("B02 second directive → order_type question (توصيل لو استلام)")
    else:
        fail("B02 second directive", f"got: {directive2!r}")


def test_B03_upsell_offered_flag_set():
    sess = fresh("b03")
    sess.items.append(OrderItem("برجر", 1, 8000))
    assert not sess.upsell_offered
    sess.generate_next_directive(PRODUCTS)
    if sess.upsell_offered:
        ok("B03 upsell_offered=True after first directive call")
    else:
        fail("B03 flag not set", "upsell_offered still False")


def test_B04_no_upsell_when_already_offered():
    sess = fresh("b04")
    sess.items.append(OrderItem("برجر", 1, 8000))
    sess.upsell_offered = True  # manually mark as offered
    directive = sess.generate_next_directive(PRODUCTS)
    if "توصيل" in directive or "استلام" in directive:
        ok("B04 upsell already offered → goes to order_type directly")
    else:
        fail("B04 upsell already offered", f"got: {directive!r}")


def test_B05_no_upsell_if_no_suitable_product():
    # Products list has no drink → no upsell suggestion
    products_no_drink = [
        {"id": "p1", "name": "برجر", "price": 8000, "available": True},
        {"id": "p2", "name": "بطاطا", "price": 2000, "available": True},
    ]
    sess = fresh("b05")
    sess.items.append(OrderItem("برجر", 1, 8000))
    directive = sess.generate_next_directive(products_no_drink)
    # Should fall through to order_type since no drink in menu
    if "توصيل" in directive or "استلام" in directive or "مشروب" in directive:
        ok("B05 no drink in menu → upsell tries generic or skips to order_type")
    else:
        fail("B05 no drink in menu", f"got: {directive!r}")


def test_B06_upsell_detected_from_bot_reply():
    sess = fresh("b06")
    sess.items.append(OrderItem("برجر", 1, 8000))
    updated = []
    # Simulate bot reply with upsell phrase
    OrderBrain.update_from_message(sess, "تريد نضيف كولا وياه؟ 🥤", PRODUCTS, is_bot_reply=True)
    if sess.upsell_offered:
        ok("B06 upsell phrase in bot reply → upsell_offered=True")
    else:
        fail("B06 bot reply detection", "upsell_offered still False")


test_B01_directive_upsell_before_order_type()
test_B02_second_call_gives_order_type()
test_B03_upsell_offered_flag_set()
test_B04_no_upsell_when_already_offered()
test_B05_no_upsell_if_no_suitable_product()
test_B06_upsell_detected_from_bot_reply()


# ── C. Order Status (NUMBER 34) ──────────────────────────────────────────────
print("\n── C. Order status query detection ──")

from services.bot import _ORDER_STATUS_PHRASES, _get_order_status_context


def test_C01_status_query_detected():
    phrases_to_test = ["وين طلبي", "وصل طلبي", "متى يجي الطلب", "حالة الطلب"]
    all_ok = True
    for phrase in phrases_to_test:
        if not any(p in phrase for p in _ORDER_STATUS_PHRASES):
            all_ok = False
            print(f"    not detected: {phrase!r}")
    if all_ok:
        ok("C01 all order status phrases detected")
    else:
        fail("C01 status phrases")


def test_C02_non_status_not_detected():
    non_phrases = ["هلا", "أريد برجر", "شكراً", "كاش", "الكرادة"]
    all_ok = True
    for phrase in non_phrases:
        if any(p in phrase for p in _ORDER_STATUS_PHRASES):
            all_ok = False
            print(f"    false positive: {phrase!r}")
    if all_ok:
        ok("C02 no false positives for non-status messages")
    else:
        fail("C02 false positives")


def test_C03_status_context_no_match_returns_empty():
    # Non-status message → empty string (no DB query)
    result = _get_order_status_context("r1", "c1", "أريد برجر")
    if result == "":
        ok("C03 non-status message → empty context (no DB call)")
    else:
        fail("C03 non-status not empty", f"got: {result!r}")


def test_C04_status_labels_complete():
    from services.bot import _ORDER_STATUS_LABELS
    required = ["pending", "confirmed", "preparing", "ready", "delivered", "cancelled"]
    missing = [s for s in required if s not in _ORDER_STATUS_LABELS]
    if not missing:
        ok("C04 all order status labels defined")
    else:
        fail("C04 missing status labels", f"missing: {missing}")


test_C01_status_query_detected()
test_C02_non_status_not_detected()
test_C03_status_context_no_match_returns_empty()
test_C04_status_labels_complete()


# ── D. Serialization (to_dict / from_dict) ──────────────────────────────────
print("\n── D. Serialization ──")


def test_D01_upsell_serialized():
    sess = fresh("d01")
    sess.upsell_offered = True
    d = sess.to_dict()
    restored = OrderSession.from_dict(d)
    if restored.upsell_offered:
        ok("D01 upsell_offered survives to_dict/from_dict")
    else:
        fail("D01 upsell not serialized")


def test_D02_upsell_false_default():
    sess = fresh("d02")
    d = sess.to_dict()
    d.pop("upsell_offered", None)  # simulate old saved session without field
    restored = OrderSession.from_dict(d)
    if not restored.upsell_offered:
        ok("D02 old session without upsell_offered defaults to False")
    else:
        fail("D02 default wrong")


test_D01_upsell_serialized()
test_D02_upsell_false_default()


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
