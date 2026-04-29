"""
Elite Reply Quality Gate — NUMBER 20 / fixed in NUMBER 20C
Extended quality checks on top of Algorithm 6 in bot.py.
Algorithm 6 already handles: banned phrases, repeated greeting,
known info, multiple questions, too long, pickup address, upsell refusal,
complaint upsell, duplicate summary, dangling punctuation.

This module adds: extended banned phrases, technical AI exposure,
tone rewriting, broken-start repair, and a comprehensive quality score.
"""
import re
import logging

logger = logging.getLogger("restaurant-saas")

# ─────────────────────────────────────────────────────────────────────────────
# Extended banned phrases (in ADDITION to what bot.py Algorithm 6 already has)
# ─────────────────────────────────────────────────────────────────────────────

ELITE_BANNED_ADDITIONAL = [
    # NUMBER 20 spec additions
    "يرجى تزويدي",
    "كيف يمكنني مساعدتك",
    "يسعدني مساعدتك",
    "عزيزي العميل",
    "نعتذر عن الإزعاج",
    "نعتذر على الإزعاج",
    "تم استلام طلبك بنجاح",
    "حسب البيانات",
    "حسب السجل",
    "قاعدة البيانات",
    "تم تحليل الصورة",
    "تم تحويل الصوت إلى نص",
    "الصورة تحتوي على",
    "حسب التحليل",
    "يرجى الانتظار",
    "شكراً لاختيارك",
    "هل ترغب في",
    "يمكنني مساعدتك",
    "بناءً على طلبك",
    "تمت معالجة",
    "عميلنا العزيز",
    "لا تتردد بالتواصل",
    "يرجى العلم",
    "نود إعلامك",
    # AI/media processing exposure — NUMBER 20
    "تم تحويل",
    "الصوت إلى نص",
    "الصورة تحتوي",
    "بعد تحليل",
    "استناداً إلى",
    "وفقاً للبيانات",
    "النظام يشير",
    "بحسب السجلات",
    # NUMBER 20C additions — memory/DB exposure
    "وفقاً للسجلات",
    "بناءً على سجلاتنا",
    "بناءً على سجلك",
    "سجلاتنا تبين",
    "نظامنا يوضح",
    "بناءً على سجلات",
    # NUMBER 20C additions — voice/image AI exposure
    "استقبلنا رسالتك الصوتية",
    "تم معالجة طلبك الصوتي",
    "استقبلنا استفسارك",
    "من خلال الصورة",
    "حسب الصورة",
    "حسب الفويس",
    "من خلال الفويس",
    "بناءً على الفويس",
    "من خلال التسجيل",
    "بناءً على الصورة",
    "تم تحديد",
    "تم التعرف على",
    "يظهر في الصورة",
    "يظهر من الصوت",
    "تحليل الطلب",
    "تحليل رسالتك",
    "تم رصد",
    # Corporate/formal filler words
    "بالتأكيد",
    "بالطبع",
    "بكل سرور",
    "من دواعي سروري",
    "بكل ترحيب",
    "بكل تأكيد",
    "لا تتردد في التواصل",
    "يسرنا",
    "يسرني",
    # Over-formal openers
    "أودّ الإشارة",
    "يسرني إعلامك",
    "يشرفني خدمتك",
    "أتشرف بخدمتك",
    "تفضل بقبول",
    "مع خالص الاحترام",
    # ChatGPT-style explanations
    "هذا يعني أن",
    "بمعنى آخر",
    "وبشكل عام",
    "للإجابة على سؤالك",
    "بالنسبة لسؤالك",
    # Fake urgency / marketing
    "عرض محدود",
    "فرصة لا تفوتك",
    "اطلب الآن قبل النفاد",
    "عرض خاص اليوم فقط",
]

# Technical AI/media exposure patterns (regex)
TECH_EXPOSURE_PATTERNS = [
    r"تم\s+تحليل",
    r"تم\s+تحويل",
    r"الصورة\s+تحتوي",
    r"حسب\s+(البيانات|السجل|التحليل|قاعدة|الصورة|الفويس)",
    r"النظام\s+(يشير|يقول|يوضح)",
    r"بحسب\s+السجلات?",
    r"وفقاً?\s+(للبيانات|للسجلات?)",
    r"بناءً\s+على\s+(سجلات?|الصورة|الفويس|التسجيل)",
    r"من\s+خلال\s+(الصورة|الفويس|التسجيل)",
    r"استقبلنا\s+(رسالتك|استفسارك)",
    r"تم\s+(تحديد|التعرف|رصد|معالجة)",
    r"يظهر\s+(في|من)\s+(الصورة|الصوت)",
    r"سجلاتنا\s+تبين",
    r"نظامنا\s+يوضح",
]

# Fragments that signal a broken sentence start after phrase stripping
BROKEN_START_PATTERNS = [
    r"^وهي\s",
    r"^وهو\s",
    r"^وهم\s",
    r"^وأنه\s",
    r"^وأنها\s",
    r"^وتحتوي",
    r"^وتظهر",
    r"^وتكشف",
    r"^وتشير",
    r"^على\s+أن",
    r"^من\s+خلال",
    r"^بناءً",
    r"^وفقاً",
    r"^بالنسبة",
    r"^الصورة\s+",   # any sentence starting with "الصورة ..." after stripping
    r"^الفويس\s",
    r"^التسجيل\s",
    r"^تحتوي\s",
    r"^في\s+(معرفة|تتبع|مساعدة|خدمة|الحصول|تحليل)",
]

# Signs the reply is corporate/formal (detect and flag)
CORPORATE_SIGNALS = [
    "يرجى",
    "نود أن",
    "نأمل أن",
    "نتمنى أن",
    "يتشرف",
    "بكل سرور",
    "من دواعي سروري",
]

MAX_REPLY_LENGTH = 300
MAX_QUESTIONS = 1

# Intents where multi-question is always a problem (not just simple intents)
STRICT_ONE_QUESTION_INTENTS = {
    "direct_order", "order_missing_address", "order_missing_name",
    "order_missing_payment", "order_missing_delivery",
    "greeting", "thanks", "emoji_positive", "casual_chat",
    "story_reply", "voice_order", "image_product", "image_menu",
    "recommendation", "price_question", "cheapest_item", "menu_request",
}


def extended_quality_gate(reply: str, ctx: dict) -> tuple:
    """
    Run extended quality checks.
    Returns: (is_acceptable, issues_list, cleaned_reply)
    Algorithm 6 already ran — this is the elite second pass.
    """
    if not reply or not reply.strip():
        return False, ["empty_reply"], "تمام 🌷"

    fixed = reply
    issues = []
    critical = False

    # 1. Extended banned phrases
    for phrase in ELITE_BANNED_ADDITIONAL:
        if phrase in fixed:
            fixed = fixed.replace(phrase, "").strip()
            issues.append(f"elite_banned:{phrase[:25]}")

    # 2. Technical AI/media exposure (regex — whole sentence removal)
    for pattern in TECH_EXPOSURE_PATTERNS:
        if re.search(pattern, fixed, re.IGNORECASE):
            issues.append(f"tech_exposure:{pattern[:30]}")
            fixed = re.sub(
                r'[^.!؟]*' + pattern + r'[^.!؟]*[.!؟]?',
                '',
                fixed,
                flags=re.IGNORECASE,
            ).strip()
            critical = True

    # 3. Post-strip cleanup — orphaned leading punctuation
    fixed = _clean_leading_punctuation(fixed)

    # 4. Broken sentence start detection — if broken, mark critical so template kicks in
    if _is_broken_start(fixed):
        issues.append("broken_start")
        critical = True

    # 5. Corporate signal detection
    corp_count = sum(1 for s in CORPORATE_SIGNALS if s in fixed)
    if corp_count >= 2:
        issues.append(f"corporate_tone:{corp_count}")

    # 6. Length check
    if len(fixed) > MAX_REPLY_LENGTH:
        truncated = _truncate_at_sentence(fixed, MAX_REPLY_LENGTH)
        if truncated and len(truncated) >= 10:
            fixed = truncated
        issues.append(f"too_long:{len(reply)}")

    # 7. Multiple questions
    intent = ctx.get("intent", "")
    q_count = fixed.count("؟")
    enforce_one_q = (
        q_count > MAX_QUESTIONS and
        (intent in STRICT_ONE_QUESTION_INTENTS or q_count > MAX_QUESTIONS)
    )
    if enforce_one_q:
        fixed = _keep_best_question(fixed, intent)
        issues.append(f"multi_question:{q_count}")

    # 8. Empty after sanitize
    cleaned = re.sub(r'[\s🌷،.؟!?\u200b-\u200f]+', '', fixed)
    if not cleaned:
        issues.append("empty_after_gate")
        critical = True
        fixed = "تمام 🌷"

    # 9. Dangling standalone question mark / whitespace collapse
    fixed = re.sub(r'(^|\s+)[؟?](\s*|$)', r'\1', fixed).strip()
    fixed = re.sub(r'[ \t]{2,}', ' ', fixed).strip()

    # 10. Final leading punctuation pass (catches edge cases after step 7-9)
    fixed = _clean_leading_punctuation(fixed)

    # 11. Complaint + upsell (extra check beyond Algorithm 6)
    is_complaint = ctx.get("is_complaint", False)
    if is_complaint or intent in ("complaint", "angry_customer", "complaint_cold_food",
                                   "complaint_missing_item", "complaint_wrong_order",
                                   "complaint_delay", "refund_replace"):
        upsell_signals = ["بالمناسبة", "تريد تضيف", "تحب تضيف", "أضيفلك",
                          "عرض", "أيضاً عندنا", "تجرب"]
        for us in upsell_signals:
            if us in fixed:
                fixed = re.sub(
                    r'[^.!؟]*' + re.escape(us) + r'[^.!؟]*[.!؟]?',
                    '',
                    fixed,
                ).strip()
                fixed = _clean_leading_punctuation(fixed)
                issues.append("complaint_upsell_removed")
                break

    # 12. Reply too short after all fixes
    stripped = re.sub(r'[\s🌷،.؟!?\u200b-\u200f]+', '', fixed)
    if len(stripped) < 3 and len(fixed) < 8:
        issues.append("reply_too_short")
        critical = True
    # Context-aware minimum lengths
    elif is_complaint or intent in ("angry_complaint", "complaint", "complaint_cold_food",
                                     "complaint_missing_item", "complaint_delay",
                                     "refund_replace", "human_handoff"):
        # Complaints need at least a meaningful sentence (12 meaningful chars)
        if len(stripped) < 12:
            issues.append("reply_too_short")
            critical = True
    elif intent in ("voice_order", "image_product", "image_menu", "image_complaint",
                    "story_reply", "direct_order", "price_question", "menu_request"):
        # These intents should produce substantive replies (≥8 meaningful chars)
        if len(stripped) < 8:
            issues.append("reply_too_short")
            critical = True

    is_acceptable = not critical and len([
        i for i in issues if
        "critical" in i or "tech_exposure" in i or "empty" in i or "broken_start" in i
    ]) == 0
    return is_acceptable, issues, fixed


def _clean_leading_punctuation(text: str) -> str:
    """Remove orphaned leading punctuation left after phrase stripping."""
    if not text:
        return text
    # Strip leading: ! . ، ؛ : — - ، space combinations
    text = re.sub(r'^[\s!.،؛:،\-—،؟?،،]+', '', text).strip()
    # Strip leading conjunctions that are broken standalone
    text = re.sub(r'^(وهي|وهو|وهم|وأنه|وأنها)\s+', '', text).strip()
    # If starts with a broken fragment followed by a substantial sentence, drop the fragment
    if _first_sentence_is_broken(text):
        rest = _drop_first_sentence(text)
        # Only drop if the remainder is long enough to be a meaningful reply
        if rest and len(re.sub(r'[\s🌷،.؟!?\u200b-\u200f]+', '', rest)) >= 10:
            text = rest.strip()
        # else: leave as-is — broken_start detector will flag it for template replacement
    return text


def _first_sentence_is_broken(text: str) -> bool:
    """Check if the first sentence (up to first . or ؟) is a broken fragment."""
    if not text:
        return False
    # Find first sentence boundary
    m = re.search(r'[.؟!]', text)
    if not m:
        return False
    first = text[:m.start()].strip()
    if len(first) < 2:
        return False
    return _is_broken_start(first)


def _drop_first_sentence(text: str) -> str:
    """Drop first sentence and return the rest."""
    m = re.search(r'[.؟!]\s*', text)
    if not m:
        return text
    return text[m.end():].strip()


def _is_broken_start(text: str) -> bool:
    """Return True if the reply starts with a broken fragment."""
    if not text:
        return False
    for pat in BROKEN_START_PATTERNS:
        if re.match(pat, text.strip(), re.IGNORECASE):
            return True
    return False


def quality_score(reply: str, ctx: dict) -> dict:
    """Return a quality score dict for logging/review hooks. score: 0-100."""
    is_ok, issues, _ = extended_quality_gate(reply, ctx)
    deductions = len(issues) * 10
    score = max(0, 100 - deductions)
    return {
        "score": score,
        "is_acceptable": is_ok,
        "issues": issues,
        "reply_length": len(reply),
        "question_count": reply.count("؟"),
        "intent": ctx.get("intent", "unknown"),
    }


def _truncate_at_sentence(text: str, max_len: int) -> str:
    """Truncate text at the last sentence boundary before max_len."""
    if len(text) <= max_len:
        return text
    chunk = text[:max_len]
    for sep in [".", "؟", "!", "،\n"]:
        idx = chunk.rfind(sep)
        if idx > max_len // 2:
            return chunk[:idx + 1].strip()
    idx = chunk.rfind(" ")
    if idx > 0:
        return chunk[:idx].strip()
    return chunk.strip()


def _keep_best_question(text: str, intent: str) -> str:
    """
    Keep only the single most important question.
    For order flow: priority is delivery > quantity > name > address > payment > confirm.
    For others: keep the last question.
    """
    parts = re.split(r'(?<=[؟?])\s*', text)
    meaningful = [p.strip() for p in parts if p.strip()]
    if len(meaningful) <= 1:
        return text

    # Always preserve order summary block
    order_markers = ["✅ طلبك", "طلبك:", "المجموع", "الإجمالي", "د.ع"]
    has_order = any(m in text for m in order_markers)

    if has_order:
        for i, part in enumerate(meaningful):
            if any(m in part for m in order_markers):
                remainder = " ".join(meaningful[i:])
                qs = remainder.split("؟")
                if len(qs) > 2:
                    remainder = qs[0] + "؟"
                return remainder
        return meaningful[-1]

    # For direct order: pick the highest-priority slot question
    if intent in ("direct_order", "order_missing_delivery", "order_missing_address",
                  "order_missing_name", "order_missing_payment"):
        priority_keywords = [
            ("توصيل", "استلام"),       # delivery type — ask first
            ("كم", "عدد", "شكد"),       # quantity
            ("اسم", "اسمك", "باسم"),    # name
            ("عنوان", "وين"),           # address
            ("كاش", "كارد", "دفع"),     # payment
        ]
        for keywords in priority_keywords:
            for part in meaningful:
                if any(kw in part for kw in keywords):
                    return _strip_leading_conjunction(part)
        return _strip_leading_conjunction(meaningful[0])

    # Default: keep last question
    return _strip_leading_conjunction(meaningful[-1]) if meaningful else text


def _strip_leading_conjunction(text: str) -> str:
    """Remove leading Arabic conjunctions like وتوصيل → توصيل."""
    return re.sub(r'^[وفأ]\s*(?=[^\s])', '', text).strip()


def should_use_template(intent: str, reply: str, issues: list, ctx: dict) -> bool:
    """
    Decide if we should replace the current reply with an elite template.
    NEVER replace if reply has order summary or price/item factual data we want to keep.
    """
    if not intent:
        return False

    PRESERVE_INTENTS = {
        "order_confirmation", "pickup_confirmed_with_items", "order_missing_item",
    }
    if intent in PRESERVE_INTENTS:
        return False

    # Never replace if reply has order summary or factual memory data
    PRESERVE_MARKERS = [
        "✅ طلبك", "طلبك:", "المجموع", "الإجمالي",
        "آخر طلب", "طلبك السابق", "نفس الطلب",
    ]
    if any(m in reply for m in PRESERVE_MARKERS):
        return False

    # Use template if reply has critical issues (tech exposure, broken start, empty, too short)
    if any(i.startswith("tech_exposure") or i.startswith("empty") or
           i in ("broken_start", "reply_too_short") for i in issues):
        return True

    # Use template for simple intents that don't need AI-generated text
    TEMPLATE_ELIGIBLE = {
        "greeting", "thanks", "emoji_positive", "casual_chat",
        "human_handoff", "repeated_confirmation", "blocked_subscription",
        "duplicate_message",
    }
    if intent in TEMPLATE_ELIGIBLE and issues:
        return True

    # Use template if reply is too long for simple intent
    SIMPLE_INTENTS = {"greeting", "thanks", "emoji_positive", "casual_chat", "ask_name",
                      "ask_address", "ask_delivery_type", "ask_payment", "order_quantity"}
    if intent in SIMPLE_INTENTS and len(reply) > 120:
        return True

    return False
