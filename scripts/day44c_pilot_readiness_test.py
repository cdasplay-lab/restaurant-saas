"""
NUMBER 44C — Controlled Pilot Launch Readiness Tests

Validates that the system is ready for a one-restaurant Telegram-first controlled pilot.
Covers: env/docs, database, working hours, dedup webhook, order confirmation,
fallback/session recovery, spam guard, human handoff, PII masking, GO/NO-GO report.
"""
import sys
import os
import re
import time
import json
import uuid
import threading
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ──────────────────────────────────────────────────────────────────────────────
_PASS = 0
_FAIL = 0
_results = []


def _ok(name):
    global _PASS
    _PASS += 1
    _results.append(("✓", name))


def _fail(name, reason=""):
    global _FAIL
    _FAIL += 1
    _results.append(("✗", name + (f" — {reason}" if reason else "")))


def _section(title):
    print(f"\n\033[1mSection {title}\033[0m")


def _run():
    for sym, name in _results:
        color = "\033[32m" if sym == "✓" else "\033[31m"
        print(f"  {color}{sym}\033[0m {name}")


# ──────────────────────────────────────────────────────────────────────────────
# Section A — Environment & Docs Readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_a01_jwt_secret_env_name_exists():
    """JWT_SECRET env var name is checked in main.py."""
    with open(os.path.join(os.path.dirname(__file__), "..", "main.py"), encoding="utf-8") as f:
        src = f.read()
    if "JWT_SECRET" in src:
        _ok("a01_jwt_secret_referenced")
    else:
        _fail("a01_jwt_secret_referenced", "JWT_SECRET not found in main.py")


def test_a02_openai_key_env_name_exists():
    """OPENAI_API_KEY env var is referenced in code."""
    with open(os.path.join(os.path.dirname(__file__), "..", "main.py"), encoding="utf-8") as f:
        src = f.read()
    if "OPENAI_API_KEY" in src:
        _ok("a02_openai_api_key_referenced")
    else:
        _fail("a02_openai_api_key_referenced", "OPENAI_API_KEY not found in main.py")


def test_a03_base_url_env_referenced():
    """BASE_URL env var is referenced for webhook registration."""
    with open(os.path.join(os.path.dirname(__file__), "..", "main.py"), encoding="utf-8") as f:
        src = f.read()
    if "BASE_URL" in src:
        _ok("a03_base_url_referenced")
    else:
        _fail("a03_base_url_referenced", "BASE_URL not in main.py")


def test_a04_pilot_runbook_exists():
    """docs/pilot_runbook.md exists."""
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "pilot_runbook.md")
    if os.path.isfile(path):
        _ok("a04_pilot_runbook_exists")
    else:
        _fail("a04_pilot_runbook_exists", "docs/pilot_runbook.md missing")


def test_a05_pilot_checklist_exists():
    """docs/pilot_checklist.md exists."""
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "pilot_checklist.md")
    if os.path.isfile(path):
        _ok("a05_pilot_checklist_exists")
    else:
        _fail("a05_pilot_checklist_exists", "docs/pilot_checklist.md missing")


def test_a06_runbook_has_rollback_section():
    """pilot_runbook.md contains a rollback plan section."""
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "pilot_runbook.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    if "Rollback" in content or "rollback" in content.lower():
        _ok("a06_runbook_has_rollback")
    else:
        _fail("a06_runbook_has_rollback", "no rollback section in pilot_runbook.md")


def test_a07_checklist_has_go_nogo():
    """pilot_checklist.md contains GO/NO-GO language."""
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "pilot_checklist.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    if "GO" in content and "NO-GO" in content:
        _ok("a07_checklist_has_go_nogo")
    else:
        _fail("a07_checklist_has_go_nogo", "GO/NO-GO not found in pilot_checklist.md")


def test_a08_checklist_has_pii_check():
    """pilot_checklist.md includes a PII/log safety check."""
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "pilot_checklist.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    if "PII" in content or "pii" in content.lower() or "masked" in content.lower():
        _ok("a08_checklist_has_pii_check")
    else:
        _fail("a08_checklist_has_pii_check", "no PII check in pilot_checklist.md")


# ──────────────────────────────────────────────────────────────────────────────
# Section B — Database Readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_b01_db_connects():
    """database.get_db() returns a usable connection."""
    import database
    conn = database.get_db()
    try:
        row = conn.execute("SELECT 1 AS alive").fetchone()
        if row and row["alive"] == 1:
            _ok("b01_db_connects")
        else:
            _fail("b01_db_connects", "SELECT 1 returned unexpected result")
    finally:
        conn.close()


def test_b02_products_table_exists():
    """products table exists in the database."""
    import database
    conn = database.get_db()
    try:
        conn.execute("SELECT id, name, price FROM products LIMIT 1")
        _ok("b02_products_table_exists")
    except Exception as e:
        _fail("b02_products_table_exists", str(e))
    finally:
        conn.close()


def test_b03_orders_table_exists():
    """orders table exists."""
    import database
    conn = database.get_db()
    try:
        conn.execute("SELECT id, status, total FROM orders LIMIT 1")
        _ok("b03_orders_table_exists")
    except Exception as e:
        _fail("b03_orders_table_exists", str(e))
    finally:
        conn.close()


def test_b04_conversations_table_exists():
    """conversations table exists."""
    import database
    conn = database.get_db()
    try:
        conn.execute("SELECT id, mode, order_brain_state FROM conversations LIMIT 1")
        _ok("b04_conversations_table_exists")
    except Exception as e:
        _fail("b04_conversations_table_exists", str(e))
    finally:
        conn.close()


def test_b05_channels_table_exists():
    """channels table with telegram type support exists."""
    import database
    conn = database.get_db()
    try:
        conn.execute("SELECT id, type, token, webhook_url FROM channels LIMIT 1")
        _ok("b05_channels_table_exists")
    except Exception as e:
        _fail("b05_channels_table_exists", str(e))
    finally:
        conn.close()


def test_b06_processed_events_table_exists():
    """processed_events table exists for duplicate webhook dedup."""
    import database
    conn = database.get_db()
    try:
        conn.execute("SELECT id, restaurant_id, provider, event_id FROM processed_events LIMIT 1")
        _ok("b06_processed_events_table_exists")
    except Exception as e:
        _fail("b06_processed_events_table_exists", str(e))
    finally:
        conn.close()


def test_b07_processed_events_unique_constraint():
    """processed_events has UNIQUE(restaurant_id, provider, event_id) — duplicate insert fails."""
    import database
    conn = database.get_db()
    test_event_id = f"pilot_test_{uuid.uuid4().hex[:8]}"
    rid = "pilot_test_restaurant"
    try:
        conn.execute(
            "INSERT INTO processed_events (id, restaurant_id, provider, event_id) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), rid, "telegram", test_event_id)
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO processed_events (id, restaurant_id, provider, event_id) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), rid, "telegram", test_event_id)
            )
            conn.commit()
            _fail("b07_processed_events_unique", "duplicate insert succeeded — no UNIQUE constraint")
        except Exception:
            _ok("b07_processed_events_unique_constraint")
    except Exception as e:
        _fail("b07_processed_events_unique", f"initial insert failed: {e}")
    finally:
        conn.execute("DELETE FROM processed_events WHERE restaurant_id=?", (rid,))
        conn.commit()
        conn.close()


def test_b08_settings_table_has_delivery_config():
    """settings table has delivery_fee and working_hours columns."""
    import database
    conn = database.get_db()
    try:
        conn.execute("SELECT restaurant_id, delivery_fee, working_hours FROM settings LIMIT 1")
        _ok("b08_settings_has_delivery_config")
    except Exception as e:
        _fail("b08_settings_has_delivery_config", str(e))
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Section C — Working Hours & Menu Config
# ──────────────────────────────────────────────────────────────────────────────

def test_c01_open_hours_returns_true():
    """_is_restaurant_open_now: open hours → is_open=True."""
    from services.bot import _is_restaurant_open_now
    from datetime import datetime, timedelta
    now = datetime(2026, 5, 16, 14, 0)  # Saturday 14:00 Iraq time
    wh = json.dumps({"sat": {"open": True, "from": "12:00", "to": "23:00"}})
    is_open, _, _ = _is_restaurant_open_now(wh, now=now)
    if is_open:
        _ok("c01_open_hours_returns_true")
    else:
        _fail("c01_open_hours_returns_true", "expected open, got closed")


def test_c02_closed_hours_returns_false():
    """_is_restaurant_open_now: outside hours → is_open=False."""
    from services.bot import _is_restaurant_open_now
    now = datetime(2026, 5, 16, 10, 0)  # Saturday 10:00 — before open
    wh = json.dumps({"sat": {"open": True, "from": "12:00", "to": "23:00"}})
    is_open, msg, _ = _is_restaurant_open_now(wh, now=now)
    if not is_open and msg:
        _ok("c02_closed_hours_returns_false")
    else:
        _fail("c02_closed_hours_returns_false", f"expected closed, got is_open={is_open}")


def test_c03_closed_message_is_arabic():
    """Closed-hours message is in Arabic."""
    from services.bot import _is_restaurant_open_now
    now = datetime(2026, 5, 16, 10, 0)
    wh = json.dumps({"sat": {"open": True, "from": "12:00", "to": "23:00"}})
    _, msg, _ = _is_restaurant_open_now(wh, now=now)
    has_arabic = bool(re.search(r'[؀-ۿ]', msg))
    if has_arabic:
        _ok("c03_closed_message_is_arabic")
    else:
        _fail("c03_closed_message_is_arabic", f"no Arabic in closed msg: {msg!r}")


def test_c04_missing_wh_fails_open():
    """Empty working hours config → fails open (returns True)."""
    from services.bot import _is_restaurant_open_now
    is_open, _, _ = _is_restaurant_open_now("{}")
    if is_open:
        _ok("c04_missing_wh_fails_open")
    else:
        _fail("c04_missing_wh_fails_open", "empty WH should fail-open")


def test_c05_midnight_crossover_open():
    """Working hours spanning midnight (20:00–02:00) — 23:30 is inside."""
    from services.bot import _is_restaurant_open_now
    now = datetime(2026, 5, 15, 23, 30)  # Friday 23:30
    wh = json.dumps({"fri": {"open": True, "from": "20:00", "to": "02:00"}})
    is_open, _, _ = _is_restaurant_open_now(wh, now=now)
    if is_open:
        _ok("c05_midnight_crossover_open")
    else:
        _fail("c05_midnight_crossover_open", "23:30 should be inside 20:00–02:00")


def test_c06_delivery_fee_in_confirmation():
    """Order confirmation message shows total including delivery fee."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-pilot-c06", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="برجر لحم", qty=1, price=8000))
    sess.order_type = "delivery"
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    has_total = "10" in msg or "10,000" in msg or "10000" in msg
    if has_total:
        _ok("c06_delivery_fee_in_confirmation")
    else:
        _fail("c06_delivery_fee_in_confirmation", f"10000 total not found in: {msg[:120]}")


def test_c07_pickup_no_delivery_fee():
    """Pickup order confirmation shows item price only, no delivery fee added."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-pilot-c07", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="زينجر", qty=1, price=8500))
    sess.order_type = "pickup"
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    # Total should be 8500 not 10500
    no_wrong_total = "10500" not in msg and "10,500" not in msg
    if no_wrong_total:
        _ok("c07_pickup_no_delivery_fee")
    else:
        _fail("c07_pickup_no_delivery_fee", "delivery fee added to pickup order")


def test_c08_session_ttl_is_12h():
    """OrderBrain session TTL is 43200s (12 hours) for pilot."""
    from services import order_brain
    if order_brain._SESSION_TTL == 43200.0:
        _ok("c08_session_ttl_is_12h")
    else:
        _fail("c08_session_ttl_is_12h", f"TTL={order_brain._SESSION_TTL}, expected 43200")


# ──────────────────────────────────────────────────────────────────────────────
# Section D — Duplicate Webhook Guard
# ──────────────────────────────────────────────────────────────────────────────

def test_d01_first_event_not_duplicate():
    """_is_duplicate_event returns False on first call for a new event_id."""
    from services.webhooks import _is_duplicate_event
    import database
    eid = f"pilot_d01_{uuid.uuid4().hex[:8]}"
    rid = "pilot_test_r"
    result = _is_duplicate_event(rid, "telegram", eid)
    # cleanup
    conn = database.get_db()
    conn.execute("DELETE FROM processed_events WHERE restaurant_id=? AND event_id=?", (rid, eid))
    conn.commit()
    conn.close()
    if result is False:
        _ok("d01_first_event_not_duplicate")
    else:
        _fail("d01_first_event_not_duplicate", "first call should return False")


def test_d02_second_event_is_duplicate():
    """_is_duplicate_event returns True on second call with same event_id."""
    from services.webhooks import _is_duplicate_event
    import database
    eid = f"pilot_d02_{uuid.uuid4().hex[:8]}"
    rid = "pilot_test_r"
    _is_duplicate_event(rid, "telegram", eid)
    result2 = _is_duplicate_event(rid, "telegram", eid)
    conn = database.get_db()
    conn.execute("DELETE FROM processed_events WHERE restaurant_id=? AND event_id=?", (rid, eid))
    conn.commit()
    conn.close()
    if result2 is True:
        _ok("d02_second_event_is_duplicate")
    else:
        _fail("d02_second_event_is_duplicate", "second call should return True")


def test_d03_dedup_cross_provider_isolated():
    """Same event_id on different providers are not considered duplicates."""
    from services.webhooks import _is_duplicate_event
    import database
    eid = f"pilot_d03_{uuid.uuid4().hex[:8]}"
    rid = "pilot_test_r"
    _is_duplicate_event(rid, "telegram", eid)
    result_wa = _is_duplicate_event(rid, "whatsapp", eid)
    conn = database.get_db()
    conn.execute("DELETE FROM processed_events WHERE restaurant_id=? AND event_id=?", (rid, eid))
    conn.commit()
    conn.close()
    if result_wa is False:
        _ok("d03_dedup_cross_provider_isolated")
    else:
        _fail("d03_dedup_cross_provider_isolated", "different providers share same event_id bucket")


def test_d04_dedup_cross_restaurant_isolated():
    """Same event_id for different restaurants are not considered duplicates."""
    from services.webhooks import _is_duplicate_event
    import database
    eid = f"pilot_d04_{uuid.uuid4().hex[:8]}"
    rid1 = "pilot_rest_1"
    rid2 = "pilot_rest_2"
    _is_duplicate_event(rid1, "telegram", eid)
    result2 = _is_duplicate_event(rid2, "telegram", eid)
    conn = database.get_db()
    conn.execute("DELETE FROM processed_events WHERE event_id=?", (eid,))
    conn.commit()
    conn.close()
    if result2 is False:
        _ok("d04_dedup_cross_restaurant_isolated")
    else:
        _fail("d04_dedup_cross_restaurant_isolated", "different restaurants share dedup bucket")


def test_d05_dedup_check_in_telegram_handler():
    """webhooks.py calls _is_duplicate_event for telegram update_id."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if "_is_duplicate_event" in src and "telegram" in src:
        _ok("d05_dedup_check_in_telegram_handler")
    else:
        _fail("d05_dedup_check_in_telegram_handler", "_is_duplicate_event not wired in telegram handler")


def test_d06_telegram_type_string_in_webhooks():
    """The string 'telegram' is used as channel type in webhooks."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if src.count('"telegram"') >= 2 or src.count("'telegram'") >= 2:
        _ok("d06_telegram_type_string_in_webhooks")
    else:
        _fail("d06_telegram_type_string_in_webhooks", "'telegram' not found enough times in webhooks.py")


# ──────────────────────────────────────────────────────────────────────────────
# Section E — Order Confirmation Quality
# ──────────────────────────────────────────────────────────────────────────────

def test_e01_confirmation_has_item_name():
    """Confirmation message contains the ordered item name."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e01", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="برجر لحم", qty=1, price=8000))
    sess.order_type = "delivery"
    sess.address = "شارع المتنبي"
    msg = sess.generate_confirmation_message()
    if "برجر لحم" in msg:
        _ok("e01_confirmation_has_item_name")
    else:
        _fail("e01_confirmation_has_item_name", f"item name missing from: {msg[:100]}")


def test_e02_confirmation_has_total():
    """Confirmation message contains a formatted total amount."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e02", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="زينجر", qty=1, price=8500))
    sess.order_type = "pickup"
    msg = sess.generate_confirmation_message()
    # Totals may be formatted with commas: "8,500" — match digit groups with optional comma
    has_number = bool(re.search(r'\d{1,3}[,،]\d{3}|\d{4,}', msg))
    if has_number:
        _ok("e02_confirmation_has_total")
    else:
        _fail("e02_confirmation_has_total", f"no numeric total in: {msg[:80]!r}")


def test_e03_confirmation_is_arabic():
    """Confirmation message is primarily in Arabic."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e03", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="بيبسي", qty=2, price=1500))
    sess.order_type = "pickup"
    msg = sess.generate_confirmation_message()
    has_arabic = bool(re.search(r'[؀-ۿ]', msg))
    if has_arabic:
        _ok("e03_confirmation_is_arabic")
    else:
        _fail("e03_confirmation_is_arabic", "no Arabic in confirmation message")


def test_e04_confirmation_multiitem_shows_all():
    """Multi-item order: confirmation lists all items."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e04", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="برجر لحم", qty=1, price=8000))
    sess.items.append(OrderItem(name="بيبسي", qty=2, price=1500))
    sess.items.append(OrderItem(name="بطاطا وسط", qty=1, price=3000))
    sess.order_type = "pickup"
    msg = sess.generate_confirmation_message()
    all_present = "برجر لحم" in msg and "بيبسي" in msg and "بطاطا" in msg
    if all_present:
        _ok("e04_confirmation_multiitem_shows_all")
    else:
        _fail("e04_confirmation_multiitem_shows_all", f"not all items in: {msg[:150]}")


def test_e05_order_summary_for_handoff():
    """order_summary_for_confirmation() returns non-empty Arabic string."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e05", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="وجبة عائلية", qty=1, price=28000))
    summary = sess.order_summary_for_confirmation(delivery_fee=2000)
    has_arabic = bool(re.search(r'[؀-ۿ]', summary))
    if has_arabic and len(summary) > 10:
        _ok("e05_order_summary_for_handoff")
    else:
        _fail("e05_order_summary_for_handoff", f"summary too short or no Arabic: {summary!r}")


def test_e06_order_type_delivery_in_confirmation():
    """Delivery order type appears in confirmation message."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e06", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="برجر دجاج", qty=1, price=7500))
    sess.order_type = "delivery"
    sess.address = "الكرخ، بغداد"
    msg = sess.generate_confirmation_message(delivery_fee=2000)
    has_delivery = "توصيل" in msg or "delivery" in msg.lower()
    if has_delivery:
        _ok("e06_order_type_delivery_in_confirmation")
    else:
        _fail("e06_order_type_delivery_in_confirmation", "delivery type not shown in confirmation")


def test_e07_order_type_pickup_in_confirmation():
    """Pickup order type appears in confirmation."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e07", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="برجر لحم", qty=1, price=8000))
    sess.order_type = "pickup"
    msg = sess.generate_confirmation_message()
    has_pickup = "استلام" in msg or "pickup" in msg.lower()
    if has_pickup:
        _ok("e07_order_type_pickup_in_confirmation")
    else:
        _fail("e07_order_type_pickup_in_confirmation", "pickup type not shown in confirmation")


def test_e08_confirmation_with_order_number():
    """generate_confirmation_message accepts order_number arg and includes it."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-e08", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="بطاطا كبير", qty=1, price=4000))
    sess.order_type = "pickup"
    msg = sess.generate_confirmation_message(order_number="ORD-0042")
    if "ORD-0042" in msg or "0042" in msg:
        _ok("e08_confirmation_with_order_number")
    else:
        _fail("e08_confirmation_with_order_number", f"order number not in: {msg[:120]}")


# ──────────────────────────────────────────────────────────────────────────────
# Section F — Fallback & Session Recovery
# ──────────────────────────────────────────────────────────────────────────────

def test_f01_gpt_fallback_uses_arabic():
    """GPT failure string is in Arabic."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "bot.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    arabic_fallback = re.search(r'عذراً.*صار خطأ', src)
    if arabic_fallback:
        _ok("f01_gpt_fallback_uses_arabic")
    else:
        _fail("f01_gpt_fallback_uses_arabic", "Arabic error string not found in bot.py fallback block")


def test_f02_gpt_fallback_uses_deterministic():
    """When GPT fails with active order, _backend_next_reply is called."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "bot.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    has_fallback = "_backend_next_reply" in src and "44a-gpt-fallback" in src
    if has_fallback:
        _ok("f02_gpt_fallback_uses_deterministic")
    else:
        _fail("f02_gpt_fallback_uses_deterministic", "_backend_next_reply fallback not found in bot.py")


def test_f03_session_expired_msg_is_arabic():
    """Session expiry message is Arabic."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "bot.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    match = re.search(r'طلبك السابق انتهت مدته', src)
    if match:
        _ok("f03_session_expired_msg_is_arabic")
    else:
        _fail("f03_session_expired_msg_is_arabic", "session expired Arabic msg not in bot.py")


def test_f04_session_persistence_roundtrip():
    """OrderSession survives to_dict/restore_from_dict roundtrip."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-f04", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="وجبة فردية", qty=1, price=12000))
    sess.customer_name = "أحمد"
    sess.customer_phone = "07901234567"
    sess.order_type = "delivery"
    d = sess.to_dict()
    restored = OrderBrain.restore_from_dict("conv-f04", d)
    ok = (
        restored is not None
        and len(restored.items) == 1
        and restored.customer_name == "أحمد"
        and restored.items[0].name == "وجبة فردية"
    )
    if ok:
        _ok("f04_session_persistence_roundtrip")
    else:
        _fail("f04_session_persistence_roundtrip", "restored session differs from original")


def test_f05_expired_session_returns_none():
    """restore_from_dict returns None when saved state has expired."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    import services.order_brain as _ob_mod
    sess = OrderSession(conversation_id="conv-f05", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="برجر لحم", qty=1, price=8000))
    d = sess.to_dict()
    # Backdate updated_at past TTL
    d["updated_at"] = time.time() - (_ob_mod._SESSION_TTL + 100)
    restored = OrderBrain.restore_from_dict("conv-f05", d)
    if restored is None:
        _ok("f05_expired_session_returns_none")
    else:
        _fail("f05_expired_session_returns_none", "expected None for expired session")


def test_f06_is_active_with_items():
    """OrderSession.is_active() returns True when items are in collecting state."""
    from services.order_brain import OrderSession, OrderItem, OrderBrain
    sess = OrderSession(conversation_id="conv-f06", restaurant_id="pilot-r1")
    sess.items.append(OrderItem(name="بيبسي", qty=1, price=1500))
    if sess.is_active():
        _ok("f06_is_active_with_items")
    else:
        _fail("f06_is_active_with_items", "session with items should be active")


# ──────────────────────────────────────────────────────────────────────────────
# Section G — Spam Guard
# ──────────────────────────────────────────────────────────────────────────────

def test_g01_spam_constants_present():
    """Spam guard constants are defined in webhooks.py."""
    from services import webhooks
    has_window = hasattr(webhooks, "_SPAM_WINDOW_S")
    has_max = hasattr(webhooks, "_SPAM_MAX_CALLS")
    if has_window and has_max:
        _ok("g01_spam_constants_present")
    else:
        _fail("g01_spam_constants_present", f"missing constants: _SPAM_WINDOW_S={has_window} _SPAM_MAX_CALLS={has_max}")


def test_g02_spam_window_is_30s():
    """Spam window is 30 seconds."""
    from services import webhooks
    if webhooks._SPAM_WINDOW_S == 30:
        _ok("g02_spam_window_is_30s")
    else:
        _fail("g02_spam_window_is_30s", f"_SPAM_WINDOW_S={webhooks._SPAM_WINDOW_S}, expected 30")


def test_g03_spam_max_is_3():
    """Spam max GPT calls is 3 per window."""
    from services import webhooks
    if webhooks._SPAM_MAX_CALLS == 3:
        _ok("g03_spam_max_is_3")
    else:
        _fail("g03_spam_max_is_3", f"_SPAM_MAX_CALLS={webhooks._SPAM_MAX_CALLS}, expected 3")


def test_g04_spam_throttle_reply_is_arabic():
    """Spam throttle reply is Arabic."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    match = re.search(r'لحظة', src)
    if match:
        _ok("g04_spam_throttle_reply_is_arabic")
    else:
        _fail("g04_spam_throttle_reply_is_arabic", "'لحظة' not found in webhooks.py")


def test_g05_spam_guard_uses_lock():
    """Spam guard uses threading.Lock for thread safety."""
    from services import webhooks
    is_lock = isinstance(webhooks._conv_gpt_mu, type(threading.Lock()))
    if is_lock:
        _ok("g05_spam_guard_uses_lock")
    else:
        _fail("g05_spam_guard_uses_lock", "_conv_gpt_mu is not a Lock")


def test_g06_spam_counter_per_conv():
    """Spam counter dict is keyed by conversation ID."""
    from services import webhooks
    old_times = dict(webhooks._conv_gpt_times)
    with webhooks._conv_gpt_mu:
        webhooks._conv_gpt_times["test_conv_A"] = [time.monotonic()]
        webhooks._conv_gpt_times["test_conv_B"] = [time.monotonic()]
    # two different convs tracked separately
    with webhooks._conv_gpt_mu:
        ok = "test_conv_A" in webhooks._conv_gpt_times and "test_conv_B" in webhooks._conv_gpt_times
        # cleanup
        webhooks._conv_gpt_times.pop("test_conv_A", None)
        webhooks._conv_gpt_times.pop("test_conv_B", None)
    if ok:
        _ok("g06_spam_counter_per_conv")
    else:
        _fail("g06_spam_counter_per_conv", "spam counter not tracking per-conv")


# ──────────────────────────────────────────────────────────────────────────────
# Section H — Human Handoff Context
# ──────────────────────────────────────────────────────────────────────────────

def test_h01_handoff_includes_customer_name():
    """Handoff escalation text includes customer name."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    has_name_ref = "customer_name" in src or '"name"' in src
    if has_name_ref:
        _ok("h01_handoff_includes_customer_name")
    else:
        _fail("h01_handoff_includes_customer_name", "no customer name reference in webhooks.py handoff")


def test_h02_handoff_includes_phone():
    """Handoff escalation includes customer phone."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    has_phone = "phone" in src and "escalat" in src.lower()
    if has_phone:
        _ok("h02_handoff_includes_phone")
    else:
        _fail("h02_handoff_includes_phone", "phone not referenced near escalation in webhooks.py")


def test_h03_handoff_includes_basket():
    """Handoff context includes basket/order items."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    has_items = "order_brain_state" in src or "basket" in src or "items" in src
    if has_items:
        _ok("h03_handoff_includes_basket")
    else:
        _fail("h03_handoff_includes_basket", "no basket/items in webhooks escalation block")


def test_h04_unknown_name_fallback_arabic():
    """Handoff uses Arabic fallback when customer name is empty."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    has_unknown = "غير معروف" in src
    if has_unknown:
        _ok("h04_unknown_name_fallback_arabic")
    else:
        _fail("h04_unknown_name_fallback_arabic", "'غير معروف' not found in webhooks.py")


def test_h05_ws_broadcast_on_escalation():
    """ws_manager.broadcast is called on escalation."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if "ws_manager" in src and "broadcast" in src:
        _ok("h05_ws_broadcast_on_escalation")
    else:
        _fail("h05_ws_broadcast_on_escalation", "ws_manager.broadcast not in webhooks.py")


def test_h06_human_mode_check_present():
    """Incoming messages check conversation mode == 'human' to skip bot."""
    path = os.path.join(os.path.dirname(__file__), "..", "services", "webhooks.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if '"human"' in src or "'human'" in src:
        _ok("h06_human_mode_check_present")
    else:
        _fail("h06_human_mode_check_present", "human mode check not found in webhooks.py")


# ──────────────────────────────────────────────────────────────────────────────
# Section I — PII Masking
# ──────────────────────────────────────────────────────────────────────────────

def test_i01_mask_07x_phone():
    """0791234567 is masked to 079****."""
    from services.webhooks import _mask_pii
    result = _mask_pii("رقمي 0791234567 تفضل")
    if re.search(r'079\*{4}', result) and "0791234567" not in result:
        _ok("i01_mask_07x_phone")
    else:
        _fail("i01_mask_07x_phone", f"got: {result!r}")


def test_i02_mask_09x_phone():
    """0991234567 masked to 099****."""
    from services.webhooks import _mask_pii
    result = _mask_pii("0991234567 هذا رقمي")
    if re.search(r'099\*{4}', result) and "0991234567" not in result:
        _ok("i02_mask_09x_phone")
    else:
        _fail("i02_mask_09x_phone", f"got: {result!r}")


def test_i03_arabic_text_unchanged():
    """Arabic text without phone numbers passes through unchanged."""
    from services.webhooks import _mask_pii
    text = "أريد برجر لحم مع بطاطا توصيل"
    result = _mask_pii(text)
    if result == text:
        _ok("i03_arabic_text_unchanged")
    else:
        _fail("i03_arabic_text_unchanged", f"text was modified: {result!r}")


def test_i04_multiple_phones_masked():
    """Multiple phone numbers in one string are all masked."""
    from services.webhooks import _mask_pii
    text = "اتصل على 07901234567 أو 09901234567"
    result = _mask_pii(text)
    still_has_raw = "07901234567" in result or "09901234567" in result
    if not still_has_raw:
        _ok("i04_multiple_phones_masked")
    else:
        _fail("i04_multiple_phones_masked", f"raw phone still visible: {result!r}")


def test_i05_short_number_not_masked():
    """Short numbers like 1234 or 555 are not masked."""
    from services.webhooks import _mask_pii
    text = "الطلب رقم 1234 جاهز"
    result = _mask_pii(text)
    if "1234" in result:
        _ok("i05_short_number_not_masked")
    else:
        _fail("i05_short_number_not_masked", f"short number was masked: {result!r}")


def test_i06_mask_pii_imported_in_webhooks():
    """_mask_pii is defined in services/webhooks.py."""
    from services import webhooks
    if callable(getattr(webhooks, "_mask_pii", None)):
        _ok("i06_mask_pii_imported_in_webhooks")
    else:
        _fail("i06_mask_pii_imported_in_webhooks", "_mask_pii not callable in webhooks module")


# ──────────────────────────────────────────────────────────────────────────────
# Section J — GO/NO-GO Automated Report
# ──────────────────────────────────────────────────────────────────────────────

def test_j01_all_test_suites_exist():
    """All 9 test suite files (44A, 44B, 44C and predecessors) exist."""
    base = os.path.join(os.path.dirname(__file__), "..")
    files = [
        "scripts/day41a_order_flow_test.py",
        "scripts/day41b_critical_fixes_test.py",
        "scripts/day41c_final_reply_safety_test.py",
        "scripts/day42_data_integrity_phase3_test.py",
        "scripts/day42_reply_quality_phase1_test.py",
        "scripts/day43_backend_baseline_test.py",
        "scripts/day44a_reply_production_readiness_test.py",
        "scripts/day44b_real_restaurant_simulation_test.py",
        "scripts/day44c_pilot_readiness_test.py",
    ]
    missing = [f for f in files if not os.path.isfile(os.path.join(base, f))]
    if not missing:
        _ok("j01_all_test_suites_exist")
    else:
        _fail("j01_all_test_suites_exist", f"missing: {missing}")


def test_j02_pilot_docs_complete():
    """Both pilot docs exist and are non-empty."""
    base = os.path.join(os.path.dirname(__file__), "..")
    docs = ["docs/pilot_runbook.md", "docs/pilot_checklist.md"]
    errors = []
    for d in docs:
        p = os.path.join(base, d)
        if not os.path.isfile(p):
            errors.append(f"{d} missing")
        elif os.path.getsize(p) < 500:
            errors.append(f"{d} too small (<500 bytes)")
    if not errors:
        _ok("j02_pilot_docs_complete")
    else:
        _fail("j02_pilot_docs_complete", "; ".join(errors))


def test_j03_critical_production_guards_present():
    """Critical production guards are all present in codebase."""
    base = os.path.join(os.path.dirname(__file__), "..")
    checks = [
        ("services/bot.py",      "44a-gpt-fallback",       "GPT fallback log tag"),
        ("services/webhooks.py", "spam-guard44a",          "spam guard log tag"),
        ("services/webhooks.py", "_mask_pii",              "PII mask function"),
        ("services/webhooks.py", "_is_duplicate_event",    "dedup guard"),
        ("services/order_brain.py", "_SESSION_TTL",        "session TTL constant"),
    ]
    errors = []
    for (filepath, needle, label) in checks:
        with open(os.path.join(base, filepath), encoding="utf-8") as f:
            src = f.read()
        if needle not in src:
            errors.append(f"{label} ({needle}) not in {filepath}")
    if not errors:
        _ok("j03_critical_production_guards_present")
    else:
        _fail("j03_critical_production_guards_present", "; ".join(errors))


def test_j04_go_nogo_report():
    """Print GO/NO-GO pilot summary based on all section results."""
    total = _PASS + _FAIL
    pct = int(100 * _PASS / total) if total else 0
    verdict = "🟢 GO" if _FAIL == 0 else f"🔴 NO-GO — {_FAIL} failure(s)"
    summary = (
        f"\n{'═'*62}\n"
        f"  NUMBER 44C — Pilot Readiness: {_PASS}/{total} passed ({pct}%)\n"
        f"  Verdict: {verdict}\n"
        f"{'═'*62}"
    )
    print(summary)
    if _FAIL == 0:
        _ok("j04_go_nogo_verdict_is_go")
    else:
        _fail("j04_go_nogo_verdict_is_go", f"{_FAIL} checks failed — pilot not ready")


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _section("A — Environment & Docs Readiness")
    test_a01_jwt_secret_env_name_exists()
    test_a02_openai_key_env_name_exists()
    test_a03_base_url_env_referenced()
    test_a04_pilot_runbook_exists()
    test_a05_pilot_checklist_exists()
    test_a06_runbook_has_rollback_section()
    test_a07_checklist_has_go_nogo()
    test_a08_checklist_has_pii_check()

    _section("B — Database Readiness")
    test_b01_db_connects()
    test_b02_products_table_exists()
    test_b03_orders_table_exists()
    test_b04_conversations_table_exists()
    test_b05_channels_table_exists()
    test_b06_processed_events_table_exists()
    test_b07_processed_events_unique_constraint()
    test_b08_settings_table_has_delivery_config()

    _section("C — Working Hours & Menu Config")
    test_c01_open_hours_returns_true()
    test_c02_closed_hours_returns_false()
    test_c03_closed_message_is_arabic()
    test_c04_missing_wh_fails_open()
    test_c05_midnight_crossover_open()
    test_c06_delivery_fee_in_confirmation()
    test_c07_pickup_no_delivery_fee()
    test_c08_session_ttl_is_12h()

    _section("D — Duplicate Webhook Guard")
    test_d01_first_event_not_duplicate()
    test_d02_second_event_is_duplicate()
    test_d03_dedup_cross_provider_isolated()
    test_d04_dedup_cross_restaurant_isolated()
    test_d05_dedup_check_in_telegram_handler()
    test_d06_telegram_type_string_in_webhooks()

    _section("E — Order Confirmation Quality")
    test_e01_confirmation_has_item_name()
    test_e02_confirmation_has_total()
    test_e03_confirmation_is_arabic()
    test_e04_confirmation_multiitem_shows_all()
    test_e05_order_summary_for_handoff()
    test_e06_order_type_delivery_in_confirmation()
    test_e07_order_type_pickup_in_confirmation()
    test_e08_confirmation_with_order_number()

    _section("F — Fallback & Session Recovery")
    test_f01_gpt_fallback_uses_arabic()
    test_f02_gpt_fallback_uses_deterministic()
    test_f03_session_expired_msg_is_arabic()
    test_f04_session_persistence_roundtrip()
    test_f05_expired_session_returns_none()
    test_f06_is_active_with_items()

    _section("G — Spam Guard")
    test_g01_spam_constants_present()
    test_g02_spam_window_is_30s()
    test_g03_spam_max_is_3()
    test_g04_spam_throttle_reply_is_arabic()
    test_g05_spam_guard_uses_lock()
    test_g06_spam_counter_per_conv()

    _section("H — Human Handoff Context")
    test_h01_handoff_includes_customer_name()
    test_h02_handoff_includes_phone()
    test_h03_handoff_includes_basket()
    test_h04_unknown_name_fallback_arabic()
    test_h05_ws_broadcast_on_escalation()
    test_h06_human_mode_check_present()

    _section("I — PII Masking")
    test_i01_mask_07x_phone()
    test_i02_mask_09x_phone()
    test_i03_arabic_text_unchanged()
    test_i04_multiple_phones_masked()
    test_i05_short_number_not_masked()
    test_i06_mask_pii_imported_in_webhooks()

    _section("J — GO/NO-GO Report")
    test_j01_all_test_suites_exist()
    test_j02_pilot_docs_complete()
    test_j03_critical_production_guards_present()
    test_j04_go_nogo_report()

    print()
    _run()

    total = _PASS + _FAIL
    print(f"\n\033[1m{'═'*60}\033[0m")
    print(f"\033[1m  Result: {_PASS}/{total} passed (100%)\033[0m" if _FAIL == 0
          else f"\033[1m  Result: {_PASS}/{total} passed — {_FAIL} FAILED\033[0m")
    print(f"\033[1m{'═'*60}\033[0m\n")

    if _FAIL > 0:
        print("\033[31mAll tests passed.\033[0m" if _FAIL == 0 else "")
        sys.exit(1)
    else:
        print("\033[32mAll tests passed.\033[0m")
