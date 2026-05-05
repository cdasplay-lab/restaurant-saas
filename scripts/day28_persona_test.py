"""
NUMBER 31 — Arabic Persona Engine Tests
Tests: dialect detection, confirm+ask pattern, guard width.
Run: python3 scripts/day28_persona_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.bot import _detect_dialect, _GULF_MARKERS, _IRAQI_MARKERS

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


# ── A. Dialect Detection ──────────────────────────────────────────────────────
print("\n── A. Dialect detection ──")

def test_A01_iraqi_detected():
    msgs = [
        {"role": "user", "content": "شنو عدكم؟"},
        {"role": "assistant", "content": "عندنا برجر وزينجر"},
        {"role": "user", "content": "أريد برجر كلش حار هسه"},
    ]
    result = _detect_dialect(msgs)
    if result == "iraqi":
        ok("A01 Iraqi dialect detected (شنو / كلش / هسه)")
    else:
        fail("A01 Iraqi not detected", f"got {result!r}")

def test_A02_gulf_detected():
    msgs = [
        {"role": "user", "content": "وش عندكم؟"},
        {"role": "assistant", "content": "عندنا برجر وزينجر"},
        {"role": "user", "content": "ابي برجر وايد"},
    ]
    result = _detect_dialect(msgs)
    if result == "gulf":
        ok("A02 Gulf dialect detected (وش / ابي / وايد)")
    else:
        fail("A02 Gulf not detected", f"got {result!r}")

def test_A03_default_iraqi_empty():
    result = _detect_dialect([])
    if result == "iraqi":
        ok("A03 empty history → default iraqi")
    else:
        fail("A03 empty history default", f"got {result!r}")

def test_A04_mixed_leans_iraqi():
    # One iraqi marker vs zero gulf
    msgs = [{"role": "user", "content": "هسه أريد برجر"}]
    result = _detect_dialect(msgs)
    if result == "iraqi":
        ok("A04 single iraqi marker → iraqi")
    else:
        fail("A04 mixed leans iraqi", f"got {result!r}")

def test_A05_assistant_messages_ignored():
    # Only assistant messages with gulf markers — should default to iraqi
    msgs = [
        {"role": "assistant", "content": "وش تريد ابي أساعدك"},
    ]
    result = _detect_dialect(msgs)
    if result == "iraqi":
        ok("A05 assistant gulf markers don't affect dialect → iraqi")
    else:
        fail("A05 assistant messages should be ignored", f"got {result!r}")

test_A01_iraqi_detected()
test_A02_gulf_detected()
test_A03_default_iraqi_empty()
test_A04_mixed_leans_iraqi()
test_A05_assistant_messages_ignored()


# ── B. Persona Guard (confirm+ask) ──────────────────────────────────────────
print("\n── B. Persona guard — confirm+ask width ──")

# We test the guard logic directly by simulating what bot.py does.
# The guard: if has_items + collecting + no ؟ + len <= 100 → append directive

from services.order_brain import OrderBrain, OrderSession, OrderItem

PRODUCTS = [
    {"id": "p1", "name": "برجر", "price": 8000, "available": True, "sold_out_date": ""},
    {"id": "p2", "name": "كولا", "price": 1500, "available": True, "sold_out_date": ""},
]

def _apply_guard(sess, reply_text):
    """Simulate the NUMBER 31 guard logic from bot.py."""
    if (
        sess is not None
        and sess.has_items()
        and sess.confirmation_status == "collecting"
        and "؟" not in reply_text
        and "?" not in reply_text
        and len(reply_text.strip()) <= 100
    ):
        missing = sess.next_missing_field()
        next_q = sess.generate_next_directive(PRODUCTS)
        if missing and next_q and next_q not in reply_text:
            sep = " — " if reply_text.strip().rstrip(" .،🌷") else ""
            reply_text = reply_text.rstrip(" .،") + sep + next_q
    return reply_text

def test_B01_bare_ack_gets_directive():
    sess = OrderSession("b01", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    result = _apply_guard(sess, "تمام 🌷")
    if "؟" in result or "توصيل" in result or "استلام" in result:
        ok("B01 bare 'تمام 🌷' → gets next directive (order_type question)")
    else:
        fail("B01 bare ack not extended", f"got: {result!r}")

def test_B02_short_ack_with_address():
    sess = OrderSession("b02", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "delivery"
    sess.address = "الكرادة"
    # Next missing = customer_name
    result = _apply_guard(sess, "وصل 🌷 الكرادة.")
    if "شسمك" in result or "اسمك" in result or "؟" in result:
        ok("B02 'وصل 🌷 الكرادة.' → appends name question")
    else:
        fail("B02 short ack with address not extended", f"got: {result!r}")

def test_B03_long_reply_not_extended():
    sess = OrderSession("b03", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    long_reply = "عندنا برجر كلاسيك وبرجر مع الجبن وبرجر مشروم — كل واحد بسعر مختلف. تفضل اختار اللي تريده من القائمة. الأسعار تبدأ من 8000 دينار."
    assert len(long_reply) > 100, "test setup: reply should be > 100 chars"
    result = _apply_guard(sess, long_reply)
    if result == long_reply:
        ok("B03 reply >100 chars not extended")
    else:
        fail("B03 long reply was incorrectly extended", f"extended to: {result[:80]!r}")

def test_B04_reply_with_question_not_extended():
    sess = OrderSession("b04", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    reply = "توصيل لو استلام؟"
    result = _apply_guard(sess, reply)
    if result == reply:
        ok("B04 reply already has ؟ → not extended")
    else:
        fail("B04 reply with question was incorrectly extended", f"got: {result!r}")

def test_B05_complete_session_not_extended():
    sess = OrderSession("b05", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.order_type = "pickup"
    sess.customer_name = "علي"
    sess.phone = "07901234567"
    sess.payment_method = "كاش"
    # Session complete — no missing field
    result = _apply_guard(sess, "تمام 🌷")
    # next_missing_field is None, so guard should NOT append anything
    # (because _missing is None)
    if result == "تمام 🌷":
        ok("B05 complete session → no directive appended")
    else:
        fail("B05 complete session was extended", f"got: {result!r}")

def test_B06_confirmed_status_not_extended():
    sess = OrderSession("b06", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    sess.confirmation_status = "confirmed"
    result = _apply_guard(sess, "تمام 🌷")
    if result == "تمام 🌷":
        ok("B06 confirmed status → guard skips")
    else:
        fail("B06 confirmed status guard triggered", f"got: {result!r}")

def test_B07_directive_not_doubled():
    sess = OrderSession("b07", "r1")
    sess.items.append(OrderItem(name="برجر", qty=1, price=8000))
    next_q = sess.generate_next_directive(PRODUCTS)
    # Reply already contains the next directive
    reply = f"تمام 🌷 {next_q}"
    result = _apply_guard(sess, reply)
    # Should not duplicate
    count = result.count(next_q)
    if count == 1:
        ok("B07 directive not doubled when already present")
    else:
        fail("B07 directive duplicated", f"count={count} in {result!r}")

test_B01_bare_ack_gets_directive()
test_B02_short_ack_with_address()
test_B03_long_reply_not_extended()
test_B04_reply_with_question_not_extended()
test_B05_complete_session_not_extended()
test_B06_confirmed_status_not_extended()
test_B07_directive_not_doubled()


# ── C. Gulf dialect note injection ──────────────────────────────────────────
print("\n── C. Dialect note in system prompt ──")

def test_C01_gulf_note_in_prompt():
    from services.bot import _build_system_prompt
    gulf_msgs = [
        {"role": "user", "content": "وش عندكم ابي أطلب"},
    ]
    prompt = _build_system_prompt(
        restaurant={"name": "مطعم تست", "address": "", "phone": ""},
        settings={"bot_name": "بوت", "payment_methods": "كاش"},
        bot_cfg={},
        products=[{"id": "p1", "name": "برجر", "price": 8000, "available": True, "sold_out_date": "", "category": "رئيسي", "description": "", "icon": "🍔", "variants": []}],
        memory={},
        customer={},
        history=gulf_msgs,
        customer_message="وش عندكم",
    )
    if "خليجية" in prompt or "تفضل" in prompt or "أبشر" in prompt:
        ok("C01 Gulf dialect note injected into system prompt")
    else:
        fail("C01 Gulf dialect note missing from prompt")

def test_C02_iraqi_no_note():
    from services.bot import _build_system_prompt
    iraqi_msgs = [
        {"role": "user", "content": "شنو عدكم كلش ابي أطلب هسه"},
    ]
    prompt = _build_system_prompt(
        restaurant={"name": "مطعم تست", "address": "", "phone": ""},
        settings={"bot_name": "بوت", "payment_methods": "كاش"},
        bot_cfg={},
        products=[{"id": "p1", "name": "برجر", "price": 8000, "available": True, "sold_out_date": "", "category": "رئيسي", "description": "", "icon": "🍔", "variants": []}],
        memory={},
        customer={},
        history=iraqi_msgs,
        customer_message="شنو عدكم",
    )
    if "خليجية" not in prompt:
        ok("C02 Iraqi dialect → no Gulf note in prompt")
    else:
        fail("C02 Iraqi incorrectly flagged as Gulf")

def test_C03_confirm_ask_section_in_prompt():
    from services.bot import _build_system_prompt
    prompt = _build_system_prompt(
        restaurant={"name": "مطعم", "address": "", "phone": ""},
        settings={"bot_name": "بوت", "payment_methods": "كاش"},
        bot_cfg={},
        products=[],
        memory={},
        customer={},
    )
    if "NUMBER 31" in prompt and "تأكيد + السؤال التالي" in prompt:
        ok("C03 NUMBER 31 confirm+ask section present in system prompt")
    else:
        fail("C03 NUMBER 31 section missing from prompt")

test_C01_gulf_note_in_prompt()
test_C02_iraqi_no_note()
test_C03_confirm_ask_section_in_prompt()


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
total = _passed + _failed
if _failed == 0:
    print(f"\033[32m✅ ALL PASSED — {_passed}/{total} tests passed\033[0m")
else:
    print(f"\033[31m❌ {_failed} FAILED — {_passed}/{total} tests passed\033[0m")
    sys.exit(1)
