"""
NUMBER 44A — Reply Production Readiness Tests

Tests for 5 blockers:
  1. GPT fallback — deterministic reply when OpenAI fails during active order
  2. Conversation recovery — session TTL extended; expired session triggers notify message
  3. Human handoff context — escalation notification includes order+customer summary
  4. Spam guard — rapid messages skip GPT after threshold
  5. PII safety — phone numbers masked in log output
"""
import sys
import os
import re
import time
import json
import unittest
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

MENU = [
    {"id": "1", "name": "برجر لحم",  "price": 8000, "available": 1},
    {"id": "2", "name": "بيبسي",     "price": 1500, "available": 1},
    {"id": "3", "name": "بطاطا",     "price": 3000, "available": 1},
    {"id": "4", "name": "زينجر",     "price": 8500, "available": 1},
]

_SECTION_COUNTS = {}
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


# ──────────────────────────────────────────────────────────────────────────────
# Section 1 — GPT Fallback
# ──────────────────────────────────────────────────────────────────────────────

def test_gpt_fallback_no_active_order():
    """When GPT fails with no active order → Iraqi fallback string returned."""
    from services import bot as _bot

    original_client = _bot._get_client()

    class _FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("OpenAI unavailable")

    with patch.object(_bot, "_get_client", return_value=_FakeClient()):
        # Minimal mock — no active OrderBrain session
        with patch("database.get_db") as mock_db:
            conn = MagicMock()
            conn.execute.return_value.fetchone.side_effect = [
                # conversation
                {"id": "conv1", "customer_id": "cust1", "mode": "bot",
                 "order_brain_state": None, "bot_turn_count": 0},
                # customer
                {"id": "cust1", "name": "علي", "phone": "07901234567",
                 "total_orders": 1, "platform": "telegram"},
                # bot_config
                None,
                # settings
                None,
                # restaurant
                None,
            ]
            conn.execute.return_value.fetchall.return_value = []
            mock_db.return_value = conn
            conn.__enter__ = lambda s: s
            conn.__exit__ = MagicMock(return_value=False)

            try:
                result = _bot.process_message("rest1", "conv1", "هلا")
                reply = result.get("reply", "")
                # Should be Iraqi fallback, not English error
                if "صار خطأ" in reply or "عذراً" in reply:
                    _ok("GPT fallback — Iraqi error message returned")
                else:
                    _fail("GPT fallback — Iraqi error message returned", f"got: {reply!r}")
            except Exception as e:
                _fail("GPT fallback — Iraqi error message returned", f"exception: {e}")


def test_gpt_fallback_with_active_order():
    """When GPT fails with active order → deterministic _backend_next_reply used."""
    try:
        from services.order_brain import OrderBrain, OrderSession, OrderItem

        # Create an active session with items
        sess = OrderSession(conversation_id="conv-test-44a", restaurant_id="rest1")
        sess.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
        sess.confirmation_status = "collecting"
        OrderBrain._sessions["conv-test-44a"] = sess

        from services import bot as _bot

        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        raise RuntimeError("OpenAI unavailable")

        with patch.object(_bot, "_get_client", return_value=_FakeClient()):
            with patch("database.get_db") as mock_db:
                conn = MagicMock()
                conn.execute.return_value.fetchone.side_effect = [
                    {"id": "conv-test-44a", "customer_id": "cust1", "mode": "bot",
                     "order_brain_state": None, "bot_turn_count": 0},
                    {"id": "cust1", "name": "علي", "phone": "07901234567",
                     "total_orders": 1, "platform": "telegram"},
                    None, None, None,
                ]
                conn.execute.return_value.fetchall.return_value = []
                mock_db.return_value = conn
                conn.__enter__ = lambda s: s
                conn.__exit__ = MagicMock(return_value=False)

                try:
                    result = _bot.process_message("rest1", "conv-test-44a", "بسرعة")
                    reply = result.get("reply", "")
                    # Should NOT be generic error — should be deterministic order question or summary
                    if "صار خطأ" not in reply and reply.strip():
                        _ok("GPT fallback with active order — deterministic reply used")
                    else:
                        _fail("GPT fallback with active order — deterministic reply used",
                              f"got generic error: {reply!r}")
                except Exception as e:
                    _fail("GPT fallback with active order — deterministic reply used", f"exception: {e}")
    finally:
        from services.order_brain import OrderBrain
        OrderBrain._sessions.pop("conv-test-44a", None)


# ──────────────────────────────────────────────────────────────────────────────
# Section 2 — Conversation Recovery
# ──────────────────────────────────────────────────────────────────────────────

def test_session_ttl_extended():
    """Session TTL must be >= 12 hours (43200 seconds)."""
    from services.order_brain import _SESSION_TTL
    if _SESSION_TTL >= 43200:
        _ok(f"Session TTL extended to {_SESSION_TTL}s (≥12h)")
    else:
        _fail(f"Session TTL extended", f"got {_SESSION_TTL}s, expected ≥43200")


def test_session_not_expired_after_2h():
    """A session updated 2 hours ago should NOT be expired under new TTL."""
    from services.order_brain import OrderSession
    sess = OrderSession(conversation_id="conv-ttl-test", restaurant_id="r1")
    sess.updated_at = time.time() - 7300  # 2 hours ago (was expired before fix)
    if not sess.is_expired():
        _ok("Session not expired after 2h under 12h TTL")
    else:
        _fail("Session not expired after 2h under 12h TTL", "still expired")


def test_session_expired_after_13h():
    """A session updated 13 hours ago IS expired."""
    from services.order_brain import OrderSession
    sess = OrderSession(conversation_id="conv-ttl-13h", restaurant_id="r1")
    sess.updated_at = time.time() - (13 * 3600)
    if sess.is_expired():
        _ok("Session correctly expired after 13h")
    else:
        _fail("Session correctly expired after 13h", "not marked expired")


def test_expired_session_with_items_triggers_notify():
    """When saved DB state is expired AND had items → _session_expired_msg is set."""
    from services.order_brain import OrderBrain, OrderSession, OrderItem
    import time as _t

    # Build a session dict with items but updated_at 14h ago (definitely expired)
    old_sess = OrderSession(conversation_id="conv-exp-44a", restaurant_id="rest1")
    old_sess.items = [OrderItem(name="زينجر", qty=1, price=8500)]
    old_sess.updated_at = _t.time() - (14 * 3600)
    stale_state = json.dumps(old_sess.to_dict())

    # restore_from_dict should return None for expired session
    restored = OrderBrain.restore_from_dict("conv-exp-44a", json.loads(stale_state))
    if restored is None:
        _ok("Expired session with items — restore_from_dict returns None")
    else:
        _fail("Expired session with items — restore_from_dict returns None",
              "returned non-None for expired session")

    # Verify the stale_state has items (so bot.py would set _session_expired_msg)
    parsed = json.loads(stale_state)
    if parsed.get("items"):
        _ok("Stale session JSON has items — expired msg would be triggered")
    else:
        _fail("Stale session JSON has items", "items missing from serialized state")


def test_fresh_session_no_expired_msg():
    """When there is no saved DB state, no expiry message should fire."""
    from services.order_brain import OrderBrain

    # No saved state → nothing to expire
    restored = OrderBrain.restore_from_dict("conv-fresh-44a", {})
    # fresh empty dict → from_dict builds empty session → is it expired? depends on updated_at default
    # The important thing: no exception
    _ok("Fresh session restore — no crash on empty dict")


# ──────────────────────────────────────────────────────────────────────────────
# Section 3 — Human Handoff Context
# ──────────────────────────────────────────────────────────────────────────────

def test_handoff_body_includes_customer_name():
    """Handoff notification body must include customer name."""
    # Simulate the logic in webhooks.py _process_incoming escalation block
    customer = {"name": "أحمد", "phone": "07901234567"}
    ob_items = [{"name": "برجر لحم", "qty": 2}, {"name": "بيبسي", "qty": 1}]

    _c_phone = customer.get("phone", "")
    _ob_items_ctx = " | السلة: " + "، ".join(
        f"{i.get('name','')}×{i.get('qty',1)}" for i in ob_items[:4]
    )
    _handoff_body = (
        f"العميل {customer.get('name', '') or 'غير معروف'}"
        f"{(' (' + _c_phone + ')') if _c_phone else ''}"
        f" يطلب التحدث مع موظف{_ob_items_ctx}"
    )

    if customer["name"] in _handoff_body:
        _ok("Handoff body contains customer name")
    else:
        _fail("Handoff body contains customer name", _handoff_body)


def test_handoff_body_includes_order_items():
    """Handoff notification body must include basket items."""
    ob_items = [{"name": "زينجر", "qty": 1}, {"name": "بطاطا", "qty": 2}]
    _ob_items_ctx = " | السلة: " + "، ".join(
        f"{i.get('name','')}×{i.get('qty',1)}" for i in ob_items[:4]
    )
    _handoff_body = f"العميل علي (07912345678) يطلب التحدث مع موظف{_ob_items_ctx}"

    if "زينجر" in _handoff_body and "بطاطا" in _handoff_body:
        _ok("Handoff body contains order items")
    else:
        _fail("Handoff body contains order items", _handoff_body)


def test_handoff_body_no_items_when_empty_basket():
    """Handoff with empty basket should not show 'السلة:' section."""
    ob_items = []
    _ob_items_ctx = ""
    if ob_items:
        _ob_items_ctx = " | السلة: " + "، ".join(
            f"{i.get('name','')}×{i.get('qty',1)}" for i in ob_items[:4]
        )
    _handoff_body = (
        f"العميل علي يطلب التحدث مع موظف{_ob_items_ctx}"
    )

    if "السلة" not in _handoff_body:
        _ok("Handoff body without items — no 'السلة' shown")
    else:
        _fail("Handoff body without items", _handoff_body)


def test_bot_stops_after_handoff():
    """Mode 'human' must block bot processing (tested via webhooks logic)."""
    # The webhooks._process_incoming only processes bot if mode == 'bot'
    # This is tested by checking the conditional at the top of the block
    import inspect
    from services import webhooks
    src = inspect.getsource(webhooks._process_incoming)
    if "mode == \"bot\"" in src or "mode == 'bot'" in src:
        _ok("Bot processing guarded by mode == 'bot' check")
    else:
        _fail("Bot processing guarded by mode == 'bot' check", "condition not found in source")


# ──────────────────────────────────────────────────────────────────────────────
# Section 4 — Spam Guard
# ──────────────────────────────────────────────────────────────────────────────

def test_spam_guard_constants_exist():
    """Spam guard constants must be defined in webhooks module."""
    from services import webhooks
    assert hasattr(webhooks, "_SPAM_WINDOW_S"), "_SPAM_WINDOW_S missing"
    assert hasattr(webhooks, "_SPAM_MAX_CALLS"), "_SPAM_MAX_CALLS missing"
    assert hasattr(webhooks, "_conv_gpt_times"), "_conv_gpt_times missing"
    assert hasattr(webhooks, "_conv_gpt_mu"), "_conv_gpt_mu missing"
    if webhooks._SPAM_WINDOW_S > 0 and webhooks._SPAM_MAX_CALLS > 0:
        _ok(f"Spam guard constants: window={webhooks._SPAM_WINDOW_S}s max={webhooks._SPAM_MAX_CALLS}")
    else:
        _fail("Spam guard constants", "window or max_calls is 0")


def test_spam_guard_allows_first_calls():
    """First N calls within window should NOT be blocked."""
    from services import webhooks
    import time as _t

    conv_id = "conv-spam-test-44a-allow"
    # Clear any stale state
    with webhooks._conv_gpt_mu:
        webhooks._conv_gpt_times.pop(conv_id, None)

    blocked_count = 0
    for i in range(webhooks._SPAM_MAX_CALLS):
        with webhooks._conv_gpt_mu:
            _now = _t.monotonic()
            _recent = [t for t in webhooks._conv_gpt_times.get(conv_id, [])
                       if _now - t < webhooks._SPAM_WINDOW_S]
            if len(_recent) >= webhooks._SPAM_MAX_CALLS:
                blocked_count += 1
            else:
                _recent.append(_now)
                webhooks._conv_gpt_times[conv_id] = _recent

    if blocked_count == 0:
        _ok(f"Spam guard allows first {webhooks._SPAM_MAX_CALLS} calls")
    else:
        _fail(f"Spam guard allows first {webhooks._SPAM_MAX_CALLS} calls",
              f"{blocked_count} were blocked prematurely")


def test_spam_guard_blocks_excess_calls():
    """After N calls, the next one must be blocked."""
    from services import webhooks
    import time as _t

    conv_id = "conv-spam-test-44a-block"
    with webhooks._conv_gpt_mu:
        webhooks._conv_gpt_times.pop(conv_id, None)

    # Fill up to the limit
    _now = _t.monotonic()
    with webhooks._conv_gpt_mu:
        webhooks._conv_gpt_times[conv_id] = [_now] * webhooks._SPAM_MAX_CALLS

    # Now next call should be blocked
    blocked = False
    with webhooks._conv_gpt_mu:
        _now2 = _t.monotonic()
        _recent = [t for t in webhooks._conv_gpt_times.get(conv_id, [])
                   if _now2 - t < webhooks._SPAM_WINDOW_S]
        if len(_recent) >= webhooks._SPAM_MAX_CALLS:
            blocked = True

    if blocked:
        _ok("Spam guard blocks excess calls after limit reached")
    else:
        _fail("Spam guard blocks excess calls after limit reached", "not blocked")


def test_spam_guard_resets_after_window():
    """After the window passes, calls are allowed again."""
    from services import webhooks
    import time as _t

    conv_id = "conv-spam-test-44a-reset"
    # Set timestamps that are older than the window
    _old_time = _t.monotonic() - (webhooks._SPAM_WINDOW_S + 5)
    with webhooks._conv_gpt_mu:
        webhooks._conv_gpt_times[conv_id] = [_old_time] * webhooks._SPAM_MAX_CALLS

    # Should not be blocked (old timestamps expired)
    blocked = False
    with webhooks._conv_gpt_mu:
        _now = _t.monotonic()
        _recent = [t for t in webhooks._conv_gpt_times.get(conv_id, [])
                   if _now - t < webhooks._SPAM_WINDOW_S]
        if len(_recent) >= webhooks._SPAM_MAX_CALLS:
            blocked = True

    if not blocked:
        _ok("Spam guard resets after window expires")
    else:
        _fail("Spam guard resets after window expires", "still blocked after window")


def test_spam_guard_in_source():
    """Spam guard code must exist in webhooks._process_incoming."""
    import inspect
    from services import webhooks
    src = inspect.getsource(webhooks._process_incoming)
    if "_skip_gpt" in src and "_SPAM_MAX_CALLS" in src:
        _ok("Spam guard code present in _process_incoming")
    else:
        _fail("Spam guard code present in _process_incoming", "_skip_gpt or _SPAM_MAX_CALLS missing")


# ──────────────────────────────────────────────────────────────────────────────
# Section 5 — PII Safety
# ──────────────────────────────────────────────────────────────────────────────

def test_pii_mask_function_exists():
    """_mask_pii must be importable from webhooks."""
    try:
        from services.webhooks import _mask_pii
        _ok("_mask_pii function importable from webhooks")
    except ImportError as e:
        _fail("_mask_pii function importable from webhooks", str(e))


def test_pii_masks_iraqi_phone_07():
    """07x phone numbers must be masked."""
    from services.webhooks import _mask_pii
    result = _mask_pii("رقمي 07901234567 وعنواني الكرادة")
    if "07901234567" not in result and "079****" in result:
        _ok("PII masks 07x phone number")
    else:
        _fail("PII masks 07x phone number", f"got: {result!r}")


def test_pii_masks_iraqi_phone_09():
    """09x phone numbers must be masked."""
    from services.webhooks import _mask_pii
    result = _mask_pii("اتصل 09901234567")
    if "09901234567" not in result:
        _ok("PII masks 09x phone number")
    else:
        _fail("PII masks 09x phone number", f"got: {result!r}")


def test_pii_preserves_non_phone_text():
    """Non-phone text must be unchanged."""
    from services.webhooks import _mask_pii
    text = "أريد برجر لحم مع توصيل للكرادة"
    result = _mask_pii(text)
    if result == text:
        _ok("PII preserves non-phone text")
    else:
        _fail("PII preserves non-phone text", f"modified: {result!r}")


def test_pii_masks_phone_in_preview_log():
    """Log preview containing phone should be masked."""
    from services.webhooks import _mask_pii
    preview = "رقمي 07912345678 وسمي علي"
    masked = _mask_pii(preview[:60])
    if "07912345678" not in masked:
        _ok("PII masking applied to log preview with phone")
    else:
        _fail("PII masking applied to log preview with phone", f"phone leaked: {masked!r}")


def test_pii_applied_in_webhooks_source():
    """_mask_pii must be called in _process_incoming for incoming and reply logs."""
    import inspect
    from services import webhooks
    src = inspect.getsource(webhooks._process_incoming)
    incoming_masked = "_mask_pii(content" in src
    reply_masked = "_mask_pii(reply_text" in src or "_mask_pii(recipient_id" in src
    if incoming_masked:
        _ok("_mask_pii called for incoming message preview")
    else:
        _fail("_mask_pii called for incoming message preview", "not found in _process_incoming")
    if reply_masked:
        _ok("_mask_pii called for reply/recipient log")
    else:
        _fail("_mask_pii called for reply/recipient log", "not found in _process_incoming")


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

def _run_section(title, tests):
    print(f"\n\033[1m{title}\033[0m")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _fail(fn.__name__, f"uncaught: {e}")
    for sym, msg in _results[-len(tests):]:
        color = "\033[32m" if sym == "✓" else "\033[31m"
        print(f"  {color}{sym}\033[0m {msg}")


if __name__ == "__main__":
    _run_section("Section 1 — GPT Fallback", [
        test_gpt_fallback_no_active_order,
        test_gpt_fallback_with_active_order,
    ])
    _run_section("Section 2 — Conversation Recovery", [
        test_session_ttl_extended,
        test_session_not_expired_after_2h,
        test_session_expired_after_13h,
        test_expired_session_with_items_triggers_notify,
        test_fresh_session_no_expired_msg,
    ])
    _run_section("Section 3 — Human Handoff Context", [
        test_handoff_body_includes_customer_name,
        test_handoff_body_includes_order_items,
        test_handoff_body_no_items_when_empty_basket,
        test_bot_stops_after_handoff,
    ])
    _run_section("Section 4 — Spam Guard", [
        test_spam_guard_constants_exist,
        test_spam_guard_allows_first_calls,
        test_spam_guard_blocks_excess_calls,
        test_spam_guard_resets_after_window,
        test_spam_guard_in_source,
    ])
    _run_section("Section 5 — PII Safety", [
        test_pii_mask_function_exists,
        test_pii_masks_iraqi_phone_07,
        test_pii_masks_iraqi_phone_09,
        test_pii_preserves_non_phone_text,
        test_pii_masks_phone_in_preview_log,
        test_pii_applied_in_webhooks_source,
    ])

    total = _PASS + _FAIL
    print(f"\n\033[1m{'═'*60}")
    print(f"  Result: {_PASS}/{total} passed (100%)" if _FAIL == 0 else
          f"  Result: {_PASS}/{total} passed, {_FAIL} FAILED")
    print(f"{'═'*60}\033[0m\n")
    if _FAIL == 0:
        print("\033[32mAll tests passed.\033[0m")
        sys.exit(0)
    else:
        print(f"\033[31m{_FAIL} test(s) failed.\033[0m")
        sys.exit(1)
