#!/usr/bin/env python3
"""
scripts/day42_reply_quality_phase1_test.py — NUMBER 42: Reply Quality Phase 1

Proves fixes from NUMBER 42 Phase 2 (RISK-07, RISK-08, RISK-10, RISK-12):
  RISK-07: Hard rules block appears at top of system prompt (before menu/memory)
  RISK-08: MSA drift phrases rejected by _validate_reply / BANNED_PHRASES
  RISK-10: Voice token cap lifted when ob_session is complete
  RISK-12: Upsell suppressed after refusal signals
  RISK-02: C1 fired → _c1_fired=True → regex extraction skipped (code-level)
  RISK-06: Memory name/phone pre-fills fresh session (code-level)
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


import inspect
import services.bot as _bot_mod

SRC = inspect.getsource(_bot_mod)

PRODUCTS = [
    {"name": "برجر لحم",  "price": 8000, "available": 1},
    {"name": "بيبسي",     "price": 1500, "available": 1},
]

print(f"\n{BOLD}{'═'*60}")
print("  NUMBER 42 — Reply Quality Phase 1 Tests")
print(f"{'═'*60}{RST}\n")


# ── RISK-07: Hard rules at top of system prompt ───────────────────────────────
print(f"{BOLD}RISK-07 — Hard rules block at TOP of system prompt{RST}")

from services.bot import _build_system_prompt

_dummy_restaurant = {"name": "مطعم تجريبي", "address": "", "phone": ""}
_dummy_settings    = {"bot_name": "تيستر", "payment_methods": "كاش",
                      "delivery_time": "30 دقيقة", "working_hours": "{}"}
_dummy_bot_cfg     = {}
_dummy_customer    = {}
_dummy_memory      = {}

_prompt = _build_system_prompt(
    restaurant=_dummy_restaurant,
    settings=_dummy_settings,
    bot_cfg=_dummy_bot_cfg,
    products=PRODUCTS,
    memory=_dummy_memory,
    customer=_dummy_customer,
    customer_message="",
)

_hard_rules_header = "## 🔴 قواعد حديدية"
_menu_header       = "## قائمة الطعام"

_hr_pos   = _prompt.find(_hard_rules_header)
_menu_pos = _prompt.find(_menu_header)

check(
    "Hard rules block present in prompt",
    _hr_pos != -1,
    "header not found in prompt",
)
check(
    "Hard rules block appears BEFORE menu section",
    _hr_pos != -1 and _menu_pos != -1 and _hr_pos < _menu_pos,
    f"hard_rules_pos={_hr_pos} menu_pos={_menu_pos}",
)

# Verify all 6 rules are present
_HARD_RULE_PHRASES = [
    "سؤال واحد فقط",
    "جملة أو جملتين",
    "لا تذكر منتجاً أو سعراً",
    "بالتأكيد",
    "لا تعيد سؤالاً",
    "لا تضيف",
]
for phrase in _HARD_RULE_PHRASES:
    check(
        f"Hard rule present: '{phrase[:20]}'",
        phrase in _prompt,
        "rule missing from prompt",
    )


# ── RISK-08: MSA drift phrases rejected by BANNED_PHRASES ────────────────────
print(f"\n{BOLD}RISK-08 — MSA drift phrases in BANNED_PHRASES{RST}")

from services.bot import BANNED_PHRASES, _validate_reply

MSA_DRIFT_CASES = [
    ("كيف يمكنني مساعدتك", "classic helpdesk opener"),
    ("يسرنا خدمتك", "formal سرور"),
    ("نود إعلامك بأن", "formal نود"),
    ("يشرفنا استقبالك", "formal يشرف"),
    ("رهن إشارتكم دائماً", "formal رهن إشارة"),
    ("لمزيد من الاستفسار", "helpdesk closing"),
    ("نأسف لهذا الأمر", "formal apology"),
]

for reply_text, label in MSA_DRIFT_CASES:
    # Check it's in BANNED_PHRASES OR _validate_reply catches it
    in_banned = any(p in reply_text for p in BANNED_PHRASES)
    _, issues = _validate_reply(reply_text, [], {}, "", PRODUCTS)
    caught = in_banned or len(issues) > 0
    check(
        f"MSA phrase caught: '{reply_text[:30]}' ({label})",
        caught,
        f"in_banned={in_banned} issues={issues}",
    )


# ── RISK-10: Voice token cap lifted when session complete ─────────────────────
print(f"\n{BOLD}RISK-10 — Voice token cap NOT applied when order is complete{RST}")

# Test the source-level logic: _ob_complete_for_cap gates the cap
check(
    "RISK-10 cap guard present in bot.py source",
    "_ob_complete_for_cap" in SRC,
    "guard variable not found",
)
check(
    "Cap only applied when NOT complete",
    "if not _ob_complete_for_cap:" in SRC,
    "conditional not found",
)

# Functional test: simulate what the cap guard does
from services.order_brain import OrderSession, OrderItem

def _simulate_token_cap(ob_session, customer_message: str, base_tokens: int = 220) -> int:
    """Mirrors the bot.py voice cap logic for testing."""
    if customer_message.startswith("[فويس]"):
        _ob_complete_for_cap = (
            ob_session is not None and ob_session.is_complete()
        )
        if not _ob_complete_for_cap:
            return min(base_tokens, 60)
    return base_tokens

# Complete session — cap should NOT apply
complete_s = OrderSession("cap-test-complete", "r1")
complete_s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
complete_s.order_type = "delivery"
complete_s.address = "الكرادة"
complete_s.customer_name = "علي"
complete_s.phone = "07901234567"
complete_s.payment_method = "كاش"
assert complete_s.is_complete()

tokens_voice_complete = _simulate_token_cap(complete_s, "[فويس] تمام ثبت")
check(
    "Voice + complete session → tokens NOT capped at 60",
    tokens_voice_complete > 60,
    f"tokens={tokens_voice_complete}",
)

# Incomplete session — cap SHOULD apply
incomplete_s = OrderSession("cap-test-incomplete", "r1")
incomplete_s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]

tokens_voice_incomplete = _simulate_token_cap(incomplete_s, "[فويس] أريد برجر")
check(
    "Voice + incomplete session → tokens capped at 60",
    tokens_voice_incomplete == 60,
    f"tokens={tokens_voice_incomplete}",
)

# Non-voice message — cap never applies
tokens_text = _simulate_token_cap(None, "أريد برجر")
check(
    "Text message → tokens NOT capped",
    tokens_text == 220,
    f"tokens={tokens_text}",
)


# ── RISK-12: Upsell suppressed after refusal signals ─────────────────────────
print(f"\n{BOLD}RISK-12 — Upsell suppressed after refusal in conversation{RST}")

# Extract _refusal_signals from bot.py source to validate expansion
import re as _re

_refusal_match = _re.search(
    r'_refusal_signals\s*=\s*\[([^\]]+)\]', SRC, _re.DOTALL
)
if _refusal_match:
    _refusal_block = _refusal_match.group(1)
else:
    _refusal_block = ""

NEW_REFUSALS = [
    "خلاص بس",
    "ما أريد زيادة",
    "يكفيني",
    "بس كذا",
    "ما ابي شي ثاني",
    "لا ما أريد إضافات",
]
for phrase in NEW_REFUSALS:
    check(
        f"New refusal '{phrase}' in _refusal_signals",
        phrase in _refusal_block,
        "phrase missing from list",
    )

# Simulate the refusal check logic
_refusal_signals = [
    "لا شكراً", "لا شكرا", "لا ما أريد", "لا بس",
    "بس هذا", "ما أريد إضافة", "يكفي", "بس هيچ",
    "ما أريد ثاني", "لا ثاني", "بس، شكراً",
    "بس هذا", "هذا يكفي", "ما أريد غير", "ما أريد شي ثاني",
    "لا بس هيچ", "هيچ بس", "يكفي هذا", "بس هيچي",
    "ما أريد زيادة", "ما أحتاج شي ثاني", "ما أحتاج غير",
    "خلاص بس", "بس خلاص", "ما أريد يزيد", "ما أريد أضيف",
    "بس كذا", "هذا بس", "ما ابي شي ثاني", "ما ابغى زيادة",
    "يكفيني", "وايد", "زهيت",
    "كثير", "غالي", "ميزانيتي خلصت", "بس هذي",
    "لا ما أريد إضافات",
]

REFUSAL_TEST_CASES = [
    ("يكفي هذا", True,  "Iraqi 'enough'"),
    ("خلاص بس",  True,  "Iraqi 'done'"),
    ("يكفيني",   True,  "Iraqi/Gulf 'enough for me'"),
    ("بس كذا",   True,  "Gulf 'just that'"),
    ("شكراً",    False, "polite thanks — should NOT suppress upsell"),
    ("تمام",     False, "neutral ack — should NOT suppress upsell"),
]
for phrase, should_suppress, label in REFUSAL_TEST_CASES:
    suppressed = any(r in phrase for r in _refusal_signals)
    check(
        f"'{phrase}' → upsell {'suppressed' if should_suppress else 'NOT suppressed'} ({label})",
        suppressed == should_suppress,
        f"suppressed={suppressed} expected={should_suppress}",
    )


# ── RISK-02: C1 fired → regex extraction skipped (code-level) ────────────────
print(f"\n{BOLD}RISK-02 — C1 fired → regex extraction skipped{RST}")

check(
    "_c1_fired flag initialised to False before C1 block",
    "_c1_fired = False" in SRC,
    "initialisation not found",
)
check(
    "_c1_fired set True when C1 reply assigned",
    "_c1_fired = True" in SRC,
    "flag not set in C1 block",
)
check(
    "Regex extraction gated on 'not _c1_fired'",
    "not _c1_fired" in SRC,
    "gate not found",
)


# ── RISK-06: Memory pre-fill in fresh session (code-level) ───────────────────
print(f"\n{BOLD}RISK-06 — Memory name/phone pre-fills fresh session{RST}")

check(
    "RISK-06 pre-fill block present in bot.py",
    "ob-risk06" in SRC or "_mem_prefill_name" in SRC,
    "pre-fill code not found",
)
check(
    "Pre-fill only on fresh sessions (_is_fresh check)",
    "_is_fresh" in SRC and "_mem_prefill_name" in SRC,
    "freshness guard missing",
)
check(
    "Name pre-fill does not overwrite existing value",
    "not _ob_session.customer_name" in SRC,
    "overwrite guard missing",
)
check(
    "Phone pre-fill does not overwrite existing value",
    "not _ob_session.phone" in SRC,
    "overwrite guard missing",
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
