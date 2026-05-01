"""
Elite Iraqi Arabic Reply Templates — NUMBER 20
Curated natural templates per intent.  Never sound robotic.
"""
import random

# ─────────────────────────────────────────────────────────
# Template bank: intent → list of variants
# Variables: {item}, {price}, {name}, {address}, {alt}
# ─────────────────────────────────────────────────────────

TEMPLATES: dict = {

    # ── Greetings ──────────────────────────────────────────
    "greeting": [
        "هلا بيك 🌷 شتحب أرتبلك؟",
        "هلا وغلا، تريد تشوف المنيو لو تطلب مباشرة؟",
        "هلا 🌷 كل خير، شنو تحب؟",
        "أهلين 🌷 تفضل، شتريد؟",
        "هلا، بالخدمة 🌷 شتحب؟",
    ],

    # ── Menu / what do you have ────────────────────────────
    "menu_request": [
        "تفضل المنيو 🌷\n{menu}\nشتحب أطلبلك؟",
        "هذا المنيو 🌷\n{menu}\nشتريد؟",
        "عندنا:\n{menu}\nكلّيلي شتحب؟",
    ],

    "menu_request_short": [
        "عندنا {menu_short}. شتحب؟",
        "عندنا خيارات: {menu_short}. شتطلب؟",
        "تفضل، عندنا {menu_short}. كلّيلي شتريد 🌷",
    ],

    # ── Price question ─────────────────────────────────────
    "price_question": [
        "{item} بـ {price} د.ع. أرتبلك واحد؟",
        "سعره {price} د.ع، تريده توصيل لو استلام؟",
        "{item} بـ {price} د.ع 🌷 تطلبه؟",
        "{item} — {price} د.ع. تريده؟",
    ],

    # ── Recommendation ─────────────────────────────────────
    "recommendation": [
        "الـ{item} ينطلب هواي، خيار مرتب 🌷 تريده؟",
        "أكثر شي ينطلب عندنا الـ{item} — جربه؟",
        "الناس تطلب {item} هواي. أرتبلك واحد؟",
        "الـ{item} مرتب وسعره {price} د.ع. تريده؟",
    ],

    "recommendation_generic": [
        "الـ{item} خيار مرتب 🌷 تحبه؟",
        "جرب الـ{item}، ينطلب هواي 🌷",
        "الأكثر طلبًا عندنا الـ{item}. أرتبلك؟",
    ],

    # ── Cheapest ──────────────────────────────────────────
    "cheapest_item": [
        "أرخص شي عندنا الـ{item} بـ {price} د.ع. تريده؟",
        "{item} بـ {price} د.ع — الأخف بالسعر. تطلبه؟",
        "عندنا {item} بـ {price} د.ع، خيار اقتصادي 🌷 تريده؟",
    ],

    "suggest_cheaper": [
        "عندنا خيار أخف بالسعر — الـ{item} بـ {price} د.ع. تريده؟",
        "لو تريد أخف، الـ{item} بـ {price} د.ع. يصير؟",
        "عندنا {item} بـ {price} د.ع أرخص. تطلبه؟",
    ],

    # ── Direct order / quantity ────────────────────────────
    "direct_order": [
        "تمام 🌷 واحد لو أكثر؟",
        "حاضر، كم واحد تريد؟",
        "تمام، شكد عددها؟",
    ],

    "order_quantity": [
        "واحد لو أكثر؟",
        "شكد عددها؟",
        "تمام، كم؟",
    ],

    # ── Delivery / pickup ─────────────────────────────────
    "ask_delivery_type": [
        "توصيل لو استلام؟",
        "تريده يوصلك لو تيجي تاخذه؟",
        "استلام لو توصيل؟",
    ],

    "ask_address": [
        "تمام 🌷 وين أوصله؟",
        "شنو العنوان؟",
        "أرسلي العنوان 🌷",
        "وين تريده يوصلك؟",
    ],

    "pickup_confirmed": [
        "تمام، استلام 🌷 شسمك؟",
        "يصير استلام 🌷 باسم منو؟",
        "تمام، شسمك؟",
    ],

    # ── Name ──────────────────────────────────────────────
    "ask_name": [
        "شسمك 🌷؟",
        "باسم منو نثبته؟",
        "اسمك؟",
        "شنو اسمك؟",
    ],

    # ── Payment ───────────────────────────────────────────
    "ask_payment": [
        "تمام 🌷 الدفع كاش لو كارد؟",
        "شطريقة الدفع — كاش لو كارد؟",
        "كاش لو كارد؟",
    ],

    # ── Order confirmation prompt ──────────────────────────
    "confirm_order_prompt": [
        "تمام، أثبته؟",
        "صح؟ أكمل الطلب؟",
        "أكمل؟",
    ],

    # ── Repeated confirmation ──────────────────────────────
    "repeated_confirmation": [
        "تم 🌷 الطلب مثبت.",
        "مثبت هسه 🌷",
        "وصلنا 🌷 الطلب مثبت.",
    ],

    # ── Order modification ─────────────────────────────────
    "modify_order": [
        "تمام، شتغير؟",
        "حاضر، كلّيلي شتريد تعدل؟",
        "وصلني، شو التعديل؟",
    ],

    # ── Cancellation ──────────────────────────────────────
    "cancel_order": [
        "تمام، الطلب ملغي 🌷",
        "وصلني، تم الإلغاء.",
        "حاضر، ألغيناه.",
    ],

    # ── Upsell (soft, one attempt only) ───────────────────
    "upsell_soft": [
        "آخر مرة ضفت {item} وياه 🌷 تضيفه؟",
        "تريد تضيف {item} على الطلب؟",
        "عندنا {item} يكمل الطلب — تضيفه؟",
    ],

    # ── Unavailable item ───────────────────────────────────
    "unavailable_item": [
        "{item} خلص هسه — عندنا {alt} قريب منه. تريده؟",
        "{item} ما موجود هسه 🌷 جرب الـ{alt}؟",
        "خلصان اليوم، بس عندنا {alt} مرتب. تريده؟",
    ],

    "unavailable_no_alt": [
        "{item} خلصان هسه للأسف.",
        "هذا ما موجود هسه، عذرًا 🌷",
    ],

    # ── Story / reel reply ────────────────────────────────
    "story_reply_interest": [
        "هذا بـ {price} د.ع 🌷 توصيل لو استلام؟",
        "{item} — {price} د.ع. تريده؟",
        "هذا {item} بـ {price} د.ع. أرتبلك؟",
    ],

    "story_reply_question": [
        "تقصد {item}؟ سعره {price} د.ع.",
        "هذا {item}، بـ {price} د.ع. تريده؟",
    ],

    "story_reply_compliment": [
        "تسلم 🌷 تريد تجربه؟",
        "شكراً 🌷 تطلب؟",
        "يسلم ذوقك 🌷 تريده؟",
    ],

    # NUMBER 20D: single CTA only, Iraqi dialect أيه/إي not نعم
    "story_reply_available": [
        "أيه موجود 🌷 أرتبلك؟",
        "إي موجود 🌷 تريده؟",
        "موجود 🌷 راسلنا بالخاص ونثبت الطلب.",
    ],

    "story_reply_unavailable": [
        "خلصان هسه 🌷 أخبرك لمن يرجع.",
        "ما موجود هسه للأسف.",
    ],

    # ── Image replies ──────────────────────────────────────
    # NUMBER 20D: added safe fallback for unclear/hallucinated image cases
    "image_product": [
        "وصلت الصورة 🌷 إذا تقصد {item}، سعره {price} د.ع. تريده؟",
        "هذا {item} بـ {price} د.ع. تطلبه؟",
    ],

    "image_menu": [
        "وصلت الصورة 🌷 شتحب من المنيو هذا؟",
        "شنو تريد من هذا المنيو؟",
    ],

    "image_complaint": [
        "وصلتني الصورة 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.",
        "وصلتني، شنو اسمك أو رقم الطلب؟",
    ],

    "image_unclear": [
        "وصلت الصورة 🌷 شتريد بالضبط؟",
        "وصلتني 🌷 كلّيلي شتحب؟",
        "وصلتني 🌷 شتريد من المنيو؟",
    ],

    # Safe fallback when image context is unclear or GPT output is unreliable
    "image_safe_fallback": [
        "وصلت الصورة 🌷 شتريد بالضبط؟",
        "وصلتني 🌷 كلّيلي شتحب؟",
    ],

    # ── Voice replies ──────────────────────────────────────
    # NUMBER 20D: warmer, no exclamation, confirm order naturally
    "voice_order": [
        "وصلني الفويس 🌷 {item} واحد — صح؟",
        "وصلني 🌷 {item}، توصيل لو استلام؟",
        "تمام 🌷 {item} — واحد؟",
    ],

    "voice_confirm_payment": [
        "وصلني 🌷 كاش وتوصيل — صح؟",
        "تمام 🌷 كاش وتوصيل.",
    ],

    "voice_unclear": [
        "وصلني 🌷 ما وضح هواي — شتريد بالضبط؟",
        "وصلني 🌷 كلّيلي شتحب؟",
    ],

    "voice_same_as_last": [
        "وصلني 🌷 مثل آخر مرة — {last_order}، صح؟",
        "نفس الطلب السابق — {last_order}؟",
    ],

    # ── Memory / same as last time ─────────────────────────
    "memory_same_order": [
        "آخر مرة أخذت {last_order} 🌷 نفسه؟",
        "مثل آخر مرة — {last_order}؟",
        "آخر طلب كان {last_order} 🌷 نكرره؟",
    ],

    "memory_suggest_address": [
        "أوصله {address} مثل آخر مرة؟",
        "نفس عنوان {address}؟",
        "{address} — نفسه لو تغيّر؟",
    ],

    "memory_old_address": [
        "نفس عنوان {address} لو تغيّر؟",
        "عندك عنوان محفوظ — {address}. نفسه؟",
    ],

    # ── Complaint ──────────────────────────────────────────
    "complaint": [
        "آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.",
        "وصلتني، شنو اسمك أو رقم الطلب؟",
        "حاضر 🌷 كلّيلي رقم الطلب وأتابعها.",
    ],

    "complaint_cold_food": [
        "آسفين على هالشي 🌷 كلّيلي رقم الطلب وأتابعها.",
        "وصلتني، كلّيلي اسمك ونشوف الحل هسه.",
    ],

    "complaint_missing_item": [
        "وصلتني 🌷 كلّيلي رقم الطلب وأخلي الموظف يراجعها وياك.",
        "آسفين 🌷 شنو رقم الطلب حتى نتابع؟",
    ],

    "complaint_wrong_order": [
        "وصلتني 🌷 كلّيلي رقم الطلب وأتابعها هسه.",
        "آسفين على هالشي، رقم الطلب؟",
    ],

    "complaint_delay": [
        "وصلتني 🌷 أتابع الموضوع هسه، شنو رقم الطلب؟",
        "آسفين على التأخير 🌷 رقم الطلب؟",
    ],

    "angry_complaint": [
        "حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.",
        "وصلتني، أحولك للموظف هسه.",
        "حقك علينا، موظفنا يتواصل وياك.",
    ],

    "refund_replace": [
        "وصلتني 🌷 أحولك لموظف يتابع معك الحل.",
        "حاضر، كلّيلي رقم الطلب وأتابعها.",
    ],

    # ── Human handoff ──────────────────────────────────────
    # NUMBER 20D: added callback-specific variant
    "human_handoff": [
        "حاضر 🌷 أحولك لموظف هسه.",
        "تمام، موظفنا يتواصل وياك هسه 🌷",
        "وصلتني 🌷 أحولك.",
        "حاضر 🌷 موظفنا يتواصل وياك.",
    ],

    # ── Thanks ─────────────────────────────────────────────
    "thanks": [
        "العفو 🌷",
        "تدلل 🌷",
        "على عيني 🌷",
        "من ذوقك 🌷",
        "يسلم قلبك 🌷",
    ],

    # ── Positive emoji only ────────────────────────────────
    "emoji_positive": [
        "من ذوقك 🌷",
        "تسلم 🌷",
        "يسلم قلبك 🌷",
    ],

    "emoji_order_intent": [
        "هلا 🌷 شتحب أرتبلك؟",
        "تريد تطلب؟ 🌷",
    ],

    # ── Casual chat / off topic ────────────────────────────
    "casual_chat": [
        "هلا بيك 🌷 تريد تطلب شي؟",
        "شنو تحب؟ 🌷",
        "تفضل 🌷 شتريد؟",
    ],

    # ── Subscription blocked ───────────────────────────────
    # NUMBER 20D: no technical internals, no يرجى, always give direction
    "blocked_subscription": [
        "الخدمة موقوفة مؤقتاً 🌷 تواصل مع المطعم مباشرة.",
        "هسه الخدمة موقوفة 🌷 راسل الإدارة حتى تتفعل.",
        "الحساب يحتاج تفعيل 🌷 تواصل مع المطعم.",
    ],

    # ── Bot in human mode (should not reply) ──────────────
    "human_mode_silence": [
        "",  # empty — bot should not reply
    ],

    # ── Duplicate message ──────────────────────────────────
    "duplicate_message": [
        "وصلتني 🌷",
        "تمام.",
    ],
}


def pick(intent: str, ctx: dict = None) -> str:
    """Return a random template for this intent, substituting context vars."""
    variants = TEMPLATES.get(intent, [])
    if not variants:
        return ""
    tmpl = random.choice(variants)
    if not tmpl:
        return ""
    if ctx:
        tmpl = _fill(tmpl, ctx)
    return tmpl


def _fill(tmpl: str, ctx: dict) -> str:
    """Safely fill template variables from context."""
    try:
        return tmpl.format(
            item=ctx.get("item", ""),
            price=ctx.get("price", ""),
            name=ctx.get("name", ""),
            address=ctx.get("address", ""),
            alt=ctx.get("alt", ""),
            menu=ctx.get("menu", ""),
            menu_short=ctx.get("menu_short", ""),
            last_order=ctx.get("last_order", ""),
        )
    except (KeyError, IndexError):
        # Strip unfilled placeholders
        import re
        return re.sub(r"\{[^}]+\}", "", tmpl).strip()


def has_template(intent: str) -> bool:
    return bool(TEMPLATES.get(intent))
