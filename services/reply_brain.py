"""
Elite Reply Brain — NUMBER 20
Unified context builder, intent detector, decision engine,
and elite reply pass.

Integration point: called after Algorithm 6 in bot.py.
NEVER raises — always returns a valid reply string.
Feature flag: ELITE_REPLY_ENGINE=true (default) / false
"""
import os
import re
import logging
from typing import Optional

logger = logging.getLogger("restaurant-saas")

ELITE_ENABLED = os.getenv("ELITE_REPLY_ENGINE", "true").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# Intent detection patterns
# Order matters — more specific patterns first
# ─────────────────────────────────────────────────────────────────────────────

INTENT_PATTERNS: list = [
    # Webhook media tags — FIRST: very specific, must not be swallowed by text patterns
    ("voice_order",            ["[فويس]", "[voice]", "[audio]"]),
    ("image_complaint",        ["[صورة-شكوى]", "[image-complaint]"]),
    ("image_menu",             ["[صورة-منيو]", "[image-menu]"]),
    ("image_product",          ["[صورة]", "[image]"]),
    ("story_reply",            ["[ستوري]", "[story]", "[reel]", "[post]"]),

    # Complaints — check BEFORE order patterns
    ("angry_complaint",        ["أسوأ", "خراء", "وسخ", "غشاش", "ما أجي ثاني", "ما أرجع"]),
    ("refund_replace",         ["أريد استرداد", "رجعوا فلوسي", "استرداد", "بدّلها", "ابدل"]),
    ("complaint_cold_food",    ["بارد", "طعام بارد", "أكل بارد", "الأكل بارد"]),
    ("complaint_missing_item", ["ناقص", "غلط", "مو صح", "ما جاء", "ما وصل", "مفقود"]),
    ("complaint_delay",        ["أين الطلب", "وين الطلب", "ليش يتأخر", "طال الانتظار", "ساعة انتظر"]),
    ("complaint",              ["شكوى", "مشكلة", "غلط", "خطأ", "ما زبط", "زبالة"]),

    # Human handoff — before direct_order so "ابي موظف" hits here not direct_order
    # NUMBER 20D: added callback request phrases
    ("human_handoff",          ["أريد موظف", "اريد موظف", "كلمني موظف", "أريد مدير", "اريد مدير",
                                 "ما أريد بوت", "ما اريد بوت", "أريد إنسان", "اريد انسان",
                                 "موظف", "مدير", "إنسان", "شخص حقيقي",
                                 "أحچي ويا بوت", "ما أريد أحچي",
                                 "اتصلوا بي", "اتصل علي", "اتصل بي",
                                 "خلي موظف يتصل", "أريد اتصال", "ما أريد بوت اتصلوا",
                                 "تصلوا بي", "أبي تتصلون"]),

    # Memory
    ("memory_same_order",      ["مثل آخر مرة", "نفس الطلب", "مثل قبل", "نفسه من قبل",
                                 "كرر الطلب", "نفس الطلب السابق"]),

    # Order cancellation — BEFORE repeated_confirmation to catch "ما أريد أكمل"
    # (otherwise "أكمل" in repeated_confirmation would match first)
    ("cancel_order",           ["ألغ", "إلغاء", "الغ", "لا أريد", "بطّل", "ما أريد أكمل",
                                 "ما أريد الطلب"]),

    # Repeated confirmation
    ("repeated_confirmation",  ["ثبت", "أكمل", "أكمله", "ثبته", "تمام ثبته", "خلاص ثبت",
                                 "أكيد ثبت", "تمام أكمل", "نعم أكمل", "نعم"]),

    # Order modification
    ("modify_order",           ["عدّل", "غيّر", "بدّل", "ضيف", "أضف", "احذف", "شيل",
                                 "احذف من الطلب", "شيل من الطلب"]),

    # Unavailable
    ("unavailable_item",       ["خلصان", "غير موجود", "ما فيه", "نفد"]),

    # Price / recommendation / menu — BEFORE direct_order so "أريد المنيو" doesn't
    # get swallowed by the generic "أريد" pattern
    # cheapest BEFORE price_question — "الأخف بالسعر" must not match "سعر" in price_question
    ("cheapest_item",          ["أرخص", "أقل سعر", "الأقل سعر", "الأخف", "رخيص",
                                 "ميزانية", "ميزانيتي", "هواي فلوس", "ما عندي فلوس",
                                 "أقل وجبة"]),
    ("price_question",         ["بكم", "شسعره", "كم سعره", "كم ثمنه", "شعر",
                                 "ثمن", "سعر", "شكد", "قيمة", "الكلفة"]),
    ("recommendation",         ["تنصحني", "تنصح", "أنصحك", "الأحسن", "الأفضل",
                                 "شنو أحسن", "الأكثر طلب", "تقترح", "أقترح",
                                 "توصية", "اقتراح", "توصيتك", "الأشهر"]),
    ("menu_request",           ["المنيو", "منيو", "منو عندكم", "شنو عندكم", "شو عندكم",
                                 "إيش عندكم", "القائمة", "الوجبات", "الأكلات", "كل شي",
                                 "شو فيه", "متوفر", "أكلاتكم", "أكلات", "قائمة الطعام",
                                 "قائمة", "أشوف المنيو", "وريني شو", "وريني قائمة",
                                 "الأصناف"]),

    # Direct order keywords
    ("direct_order",           ["أريد", "اريد", "أطلب", "اطلب", "عايز", "بدي", "ابي",
                                 "حابب", "ابغى", "خذلي", "جيبلي", "وياه",
                                 "اشتري", "طلبي"]),

    # Slot filling
    ("order_missing_address",  ["العنوان", "أرسل العنوان", "عنواني", "منطقة", "شارع"]),
    ("order_missing_name",     ["اسمي", "اسمك", "باسم", "شسمك"]),
    ("order_missing_payment",  ["كاش", "كارد", "بطاقة", "زين كاش", "الدفع"]),
    ("order_missing_delivery", ["توصيل", "استلام", "يجي ياخذه", "آخذه"]),

    # Thanks
    ("thanks",                 ["شكراً", "شكرا", "تسلم", "يسلم", "مشكور", "الله يعطيك",
                                 "يعطيك العافية", "يسعدك", "عاشت إيدك", "ممنون"]),

    # Greeting
    ("greeting",               ["هلا", "مرحبا", "مرحباً", "أهلين", "أهلا", "السلام",
                                 "صباح", "مساء", "حياك", "كيف الحال", "كيفك", "شلونك"]),

    # Emoji only
    ("emoji_positive",         ["😍", "❤️", "🥰", "😘", "💙", "💚", "💛", "🧡", "💜",
                                 "❤", "♥", "😻", "🫶", "👍", "🙌"]),
]

# Simple intents where templates are preferred
TEMPLATE_PREFERRED_INTENTS = {
    "greeting", "thanks", "emoji_positive", "casual_chat",
    "human_handoff", "repeated_confirmation", "blocked_subscription",
    "duplicate_message", "cancel_order",
}

# Intents that NEVER use templates (factual replies needed)
TEMPLATE_FORBIDDEN_INTENTS = {
    "price_question", "menu_request", "unavailable_item",
    "order_missing_address", "order_missing_payment",
    "voice_order", "memory_same_order",
}


def detect_intent(message: str, history: list = None, memory: dict = None) -> str:
    """
    Deterministic intent detection from customer message.
    Returns intent string.
    """
    if not message:
        return "casual_chat"

    msg = message.strip()

    # Pure emoji check
    text_only = re.sub(r'[\U0001F300-\U0001FFFF\U00002600-\U000027BF\s]+', '', msg)
    if not text_only and len(msg) > 0:
        return "emoji_positive"

    # Pattern matching
    msg_lower = msg.lower()
    matched_intent = None
    for intent, patterns in INTENT_PATTERNS:
        for p in patterns:
            if p in msg or p in msg_lower:
                matched_intent = intent
                break
        if matched_intent:
            break

    # NUMBER 20D — Fix E: secondary complaint scan for voice messages.
    # [فويس] matches voice_order first, but if the voice content contains
    # complaint/anger keywords, override to the appropriate complaint intent.
    if matched_intent == "voice_order":
        VOICE_ANGRY_WORDS = ["أسوأ", "زعلت", "سيء", "غشاش", "خراء", "وسخ",
                              "ما أجي ثاني", "ما أرجع", "فلوسي رجعوا"]
        VOICE_COMPLAINT_WORDS = ["بارد", "ناقص", "غلط", "تأخير", "مو زين",
                                  "تعبان", "ما عجبني", "فلوسي", "رجعوا", "اشتكي",
                                  "مشكلة", "شكوى"]
        if any(w in msg for w in VOICE_ANGRY_WORDS):
            return "angry_complaint"
        if any(w in msg for w in VOICE_COMPLAINT_WORDS):
            return "complaint"

    if matched_intent:
        return matched_intent

    # Fallback: short message is likely casual
    if len(msg.strip()) <= 3:
        return "emoji_positive"

    return "casual_chat"


def build_message_context(
    customer_message: str,
    history: list = None,
    memory: dict = None,
    products: list = None,
) -> dict:
    """
    Build a lightweight context dict from available info.
    Used by the quality gate and template engine.
    """
    history = history or []
    memory = memory or {}
    products = products or []

    # Normalize history rows (may be sqlite3.Row objects)
    norm_history = []
    for h in history:
        if isinstance(h, dict):
            norm_history.append(h)
        else:
            try:
                norm_history.append(dict(h))
            except Exception:
                pass

    intent = detect_intent(customer_message, norm_history, memory)

    # Extract available info from memory
    known_name = memory.get("name", "") or ""
    known_address = memory.get("address", "") or ""
    known_payment = memory.get("payment_method", "") or ""
    last_order = memory.get("last_order_summary", "") or ""

    # Detect complaint/angry mode from conversation history
    recent_text = " ".join(h.get("content", "") for h in norm_history[-6:])
    is_complaint = (
        intent in ("complaint", "angry_complaint", "complaint_cold_food",
                   "complaint_missing_item", "complaint_delay", "refund_replace") or
        any(w in customer_message for w in ["مشكلة", "شكوى", "غلط", "بارد", "ناقص"])
    )

    # Build menu short summary (for templates)
    available = [p for p in products
                 if isinstance(p, dict) and p.get("available", True)]
    menu_items = [p.get("name", "") for p in available[:6]]
    menu_short = "، ".join(menu_items) if menu_items else ""

    # Best seller (most common in memory/history — heuristic: first item)
    best_seller = available[0] if available else {}
    cheapest = min(available, key=lambda p: p.get("price", 9999), default={})

    # Has order summary in recent bot replies?
    recent_bot = [h.get("content", "") for h in norm_history[-4:]
                  if h.get("role") in ("bot", "assistant")]
    has_recent_order_summary = any("✅ طلبك" in r or "طلبك:" in r for r in recent_bot)

    return {
        "intent": intent,
        "customer_message": customer_message,
        "known_name": known_name,
        "known_address": known_address,
        "known_payment": known_payment,
        "last_order": last_order,
        "is_complaint": is_complaint,
        "has_recent_order_summary": has_recent_order_summary,
        "menu_short": menu_short,
        "best_seller_name": best_seller.get("name", "") if best_seller else "",
        "best_seller_price": best_seller.get("price", "") if best_seller else "",
        "cheapest_name": cheapest.get("name", "") if cheapest else "",
        "cheapest_price": cheapest.get("price", "") if cheapest else "",
        # Short-hand aliases for templates
        "item": best_seller.get("name", "") if best_seller else "",
        "price": str(int(best_seller.get("price", 0))) if best_seller and best_seller.get("price") else "",
        "name": known_name,
        "address": known_address,
        "alt": "",  # filled by caller if needed
        "menu": menu_short,
    }


def elite_reply_pass(
    reply: str,
    customer_message: str,
    history: list = None,
    memory: dict = None,
    products: list = None,
) -> str:
    """
    Main entry point — elite quality layer.
    Enhances the existing bot reply.
    NEVER raises. Always returns a valid reply string.

    Steps:
    1. Build context
    2. Detect intent
    3. Run extended quality gate
    4. Use template if needed
    5. Return best available reply
    """
    if not ELITE_ENABLED:
        return reply

    try:
        from services.reply_quality import extended_quality_gate, should_use_template
        from services.reply_templates import pick, has_template

        # 1. Build context
        ctx = build_message_context(customer_message, history, memory, products)
        intent = ctx["intent"]

        # 1b. Override intent when bot reply signals subscription block.
        # Customer may say "هلا" (greeting) but the reply is about the service
        # being off — the quality gate must route to blocked_subscription template.
        _BLOCKED_MARKERS = ["الخدمة موقوفة", "الخدمة متوقفة", "موقوفة مؤقتاً", "موقوفة حالياً"]
        if any(m in reply for m in _BLOCKED_MARKERS):
            intent = "blocked_subscription"
            ctx["intent"] = "blocked_subscription"

        # 2. Quality gate
        is_ok, issues, fixed = extended_quality_gate(reply, ctx)

        # Log quality issues (non-blocking)
        if issues:
            logger.debug(f"[elite_reply] intent={intent} issues={issues} len={len(reply)}")

        # 3. Template replacement decision
        if should_use_template(intent, fixed, issues, ctx):
            tmpl = pick(intent, ctx)
            if tmpl:
                logger.debug(f"[elite_reply] template used for intent={intent}")
                return tmpl

        # 4. Return fixed reply (quality gate output)
        result = fixed if fixed and len(fixed.strip()) >= 3 else reply

        # 5. Final sanity: if result is empty, fall back
        if not result or not result.strip():
            return reply

        return result

    except Exception as e:
        logger.warning(f"[elite_reply] exception — falling back to original: {e}")
        return reply  # golden fallback — never crash


# ─────────────────────────────────────────────────────────────────────────────
# Decision Engine (structured — for future expansion)
# ─────────────────────────────────────────────────────────────────────────────

def make_decision(ctx: dict) -> dict:
    """
    Structured decision for an incoming message context.
    Returns a decision dict for routing/quality purposes.
    Not called in the main flow yet — ready for future use.
    """
    intent = ctx.get("intent", "casual_chat")
    is_complaint = ctx.get("is_complaint", False)
    has_order = ctx.get("has_recent_order_summary", False)

    # Complaint path
    if is_complaint or intent in ("complaint", "angry_complaint", "refund_replace",
                                   "complaint_cold_food", "complaint_missing_item",
                                   "complaint_delay"):
        return {
            "intent": intent,
            "action": "escalate" if intent == "angry_complaint" else "complaint_flow",
            "should_upsell": False,
            "should_handoff": intent == "angry_complaint",
            "should_create_order": False,
            "missing_slots": [],
            "confidence": 0.95,
            "safety_notes": ["no_upsell", "serious_tone"],
        }

    # Handoff path
    if intent == "human_handoff":
        return {
            "intent": intent,
            "action": "escalate",
            "should_upsell": False,
            "should_handoff": True,
            "should_create_order": False,
            "missing_slots": [],
            "confidence": 1.0,
            "safety_notes": ["no_ai_reply_after_handoff"],
        }

    # Order path
    if intent in ("direct_order", "repeated_confirmation", "modify_order", "cancel_order"):
        return {
            "intent": intent,
            "action": "order_flow",
            "should_upsell": intent == "direct_order" and not is_complaint,
            "should_handoff": False,
            "should_create_order": intent in ("direct_order", "repeated_confirmation"),
            "missing_slots": _detect_missing_slots(ctx),
            "confidence": 0.9,
            "safety_notes": [],
        }

    # Simple reply path
    return {
        "intent": intent,
        "action": "reply",
        "should_upsell": False,
        "should_handoff": False,
        "should_create_order": False,
        "missing_slots": [],
        "confidence": 0.7,
        "safety_notes": [],
    }


def _detect_missing_slots(ctx: dict) -> list:
    """Return list of missing order slots."""
    missing = []
    if not ctx.get("known_name"):
        missing.append("name")
    if not ctx.get("known_address") and "توصيل" in ctx.get("customer_message", ""):
        missing.append("address")
    if not ctx.get("known_payment"):
        missing.append("payment")
    return missing


# ── Multi-turn Order Summary ──────────────────────────────────────────────────

def build_order_summary(history: list, current_message: str = "", products: list = None) -> str:
    """
    Build a clean Arabic order confirmation summary from conversation history.
    Uses SlotTracker to extract all known slots.
    Returns formatted ✅ summary string, or "" if not enough data.
    NEVER raises.
    """
    try:
        from services.bot import SlotTracker
        tracker = SlotTracker().ingest(history or [], current_message)

        # Detect product name from order history
        items_text = ""
        if products:
            all_text = " ".join(
                (m.get("content") or "") for m in (history or [])
                if m.get("role") in ("customer", "user")
            ) + " " + current_message
            for p in products:
                pname = (p.get("name") or "").strip()
                if pname and pname in all_text:
                    qty = tracker.quantity or 1
                    price = p.get("price") or 0
                    price_str = f" — {int(qty * price):,} د.ع" if price else ""
                    items_text = f"{qty}x {pname}{price_str}"
                    break

        summary = tracker.order_summary(items_text)
        # Only return summary if we have at least 2 meaningful slots filled
        filled = sum(1 for v in [tracker.name, tracker.address, tracker.payment,
                                  tracker.delivery_type] if v)
        return summary if filled >= 2 else ""
    except Exception:
        return ""
