"""
Elite Reply Quality Gate — NUMBER 20
Extended quality checks on top of Algorithm 6 in bot.py.
Algorithm 6 already handles: banned phrases, repeated greeting,
known info, multiple questions, too long, pickup address, upsell refusal,
complaint upsell, duplicate summary, dangling punctuation.

This module adds: extended banned phrases, technical AI exposure,
tone rewriting, and a comprehensive quality score.
"""
import re
import logging
import random

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
    # AI/media processing exposure
    "تم تحويل",
    "الصوت إلى نص",
    "الصورة تحتوي",
    "بعد تحليل",
    "استناداً إلى",
    "وفقاً للبيانات",
    "النظام يشير",
    "بحسب السجلات",
    # Corporate/formal filler words (must be removed from replies)
    "بالتأكيد",
    "بالطبع",
    "بكل سرور",
    "من دواعي سروري",
    "بكل ترحيب",
    "بكل تأكيد",
    "لا تتردد في التواصل",
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
    r"حسب\s+(البيانات|السجل|التحليل|قاعدة)",
    r"النظام\s+(يشير|يقول|يوضح)",
    r"بحسب\s+السجلات?",
    r"وفقاً?\s+للبيانات",
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

    # 2. Technical AI/media exposure
    for pattern in TECH_EXPOSURE_PATTERNS:
        if re.search(pattern, fixed, re.IGNORECASE):
            issues.append(f"tech_exposure:{pattern[:30]}")
            # Replace the sentence containing it with natural alternative
            fixed = re.sub(r'[^.!؟]*' + pattern + r'[^.!؟]*[.!؟]?', '', fixed,
                           flags=re.IGNORECASE).strip()
            critical = True

    # 3. Corporate signal detection
    corp_count = sum(1 for s in CORPORATE_SIGNALS if s in fixed)
    if corp_count >= 2:
        issues.append(f"corporate_tone:{corp_count}")

    # 4. Length check (after Algorithm 6, should be rare)
    if len(fixed) > MAX_REPLY_LENGTH:
        # Hard truncate at last sentence boundary before limit
        truncated = _truncate_at_sentence(fixed, MAX_REPLY_LENGTH)
        if truncated and len(truncated) >= 10:
            fixed = truncated
        issues.append(f"too_long:{len(reply)}")

    # 5. Multiple questions (re-check after fixes)
    q_count = fixed.count("؟")
    if q_count > MAX_QUESTIONS:
        fixed = _keep_last_question(fixed)
        issues.append(f"multi_question:{q_count}")

    # 6. Empty after sanitize
    cleaned = re.sub(r'[\s🌷،.؟!?\u200b-\u200f]+', '', fixed)
    if not cleaned:
        issues.append("empty_after_gate")
        critical = True
        fixed = "تمام 🌷"

    # 7. Dangling punctuation cleanup
    fixed = re.sub(r'(^|\s+)[؟?](\s*|$)', r'\1', fixed).strip()
    fixed = re.sub(r'[ \t]{2,}', ' ', fixed).strip()

    # 8. Complaint + upsell (extra check beyond Algorithm 6)
    is_complaint = ctx.get("is_complaint", False)
    intent = ctx.get("intent", "")
    if is_complaint or intent in ("complaint", "angry_customer", "complaint_cold_food",
                                   "complaint_missing_item", "complaint_wrong_order",
                                   "complaint_delay", "refund_replace"):
        upsell_signals = ["بالمناسبة", "تريد تضيف", "تحب تضيف", "أضيفلك", "عرض", "أيضاً عندنا", "تجرب"]
        for us in upsell_signals:
            if us in fixed:
                # Remove upsell sentence
                fixed = re.sub(r'[^.!؟]*' + re.escape(us) + r'[^.!؟]*[.!؟]?', '',
                               fixed).strip()
                issues.append("complaint_upsell_removed")
                break

    # 9. Reply length after all fixes — flag if too short (< 5 chars and no emoji)
    stripped = re.sub(r'[\s🌷،.؟!?\u200b-\u200f]+', '', fixed)
    if len(stripped) < 3 and len(fixed) < 8:
        issues.append("reply_too_short")
        critical = True

    is_acceptable = not critical and len([i for i in issues if "critical" in i or
                                          "tech_exposure" in i or "empty" in i]) == 0
    return is_acceptable, issues, fixed


def quality_score(reply: str, ctx: dict) -> dict:
    """
    Return a quality score dict for logging/review hooks.
    score: 0-100. 100 = perfect.
    """
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
    # Find last sentence boundary before max_len
    chunk = text[:max_len]
    for sep in [".", "؟", "!", "،\n"]:
        idx = chunk.rfind(sep)
        if idx > max_len // 2:
            return chunk[:idx + 1].strip()
    # Fall back: cut at last space
    idx = chunk.rfind(" ")
    if idx > 0:
        return chunk[:idx].strip()
    return chunk.strip()


def _keep_last_question(text: str) -> str:
    """Keep only the last question in a reply with multiple questions."""
    parts = re.split(r'(?<=[؟?])\s*', text)
    # Find last non-empty part
    meaningful = [p.strip() for p in parts if p.strip()]
    if len(meaningful) <= 1:
        return text

    # Keep the last question + any preceding order summary
    order_markers = ["✅ طلبك", "طلبك:", "المجموع", "الإجمالي", "د.ع"]
    has_order = any(m in text for m in order_markers)

    if has_order:
        # Find order summary block and last question
        for i, part in enumerate(meaningful):
            if any(m in part for m in order_markers):
                # Keep from order marker to end, but strip extra questions
                remainder = " ".join(meaningful[i:])
                # Remove extra questions from remainder
                qs = remainder.split("؟")
                if len(qs) > 2:
                    remainder = qs[0] + "؟ " + qs[-1].strip()
                return remainder
        return meaningful[-1]

    # No order info — just keep last question
    return meaningful[-1] if meaningful else text


def should_use_template(intent: str, reply: str, issues: list, ctx: dict) -> bool:
    """
    Decide if we should replace the current reply with an elite template.
    NEVER replace if:
    - reply has order summary
    - reply has price/item details we want to preserve
    - intent is complex order flow
    """
    if not intent:
        return False

    # Never replace these — they carry factual data
    PRESERVE_INTENTS = {
        "order_confirmation", "pickup_confirmed_with_items", "order_missing_item",
    }
    if intent in PRESERVE_INTENTS:
        return False

    # Never replace if reply has order summary
    if any(m in reply for m in ["✅ طلبك", "طلبك:", "المجموع", "الإجمالي"]):
        return False

    # Use template if reply has critical issues
    if any(i.startswith("tech_exposure") or i.startswith("empty") for i in issues):
        return True

    # Use template for simple intents that don't need AI
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
