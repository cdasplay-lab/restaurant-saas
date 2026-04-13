#!/usr/bin/env python3
"""
Day 7 — Full E2E Pilot Simulation
===================================
10 سيناريوهات محادثة كاملة من البداية للنهاية.

التقييم على مستوين:
  1. كل رسالة: جودة الرد مقارنةً بالجواب المتوقع
  2. كل سيناريو: اتساق المحادثة (coherence checks)

الجواب المتوقع هو المرجع الأعلى.
"""
import json, time, urllib.request, datetime, sys
from collections import Counter

BASE  = "http://localhost:8000"
DELAY = 2.5

# ── نبرة باردة/رسمية أو عبارات ممنوعة ──────────────────────────────────────
COLD_PHRASES = [
    "يسعدنا", "لا تتردد", "تحت تصرفك", "من دواعي سروري",
    "بكل سرور", "يشرفنا", "يمكنك", "يمكنني", "بإمكاني",
    "لديك", "نحن نأسف", "نعتذر منكم", "إلى الخدمة",
    "انتظر شوي", "لحظة من فضلك", "ارجع بعد شوي",
]

# ── إعادة الترحيب في وسط المحادثة ────────────────────────────────────────────
REGREETING = [
    "هلا بيك 🌷 شلون أكدر أخدمك؟",
    "أني مساعد مطعم",
    "مرحبًا بك",
    "أهلاً بك",
]

# ── عنصر Expected بـ [meta] = نتيجة متغيرة، لا مقارنة حرفية ─────────────────
# الأجوبة المتوقعة تبدأ بـ META: إذا كانت وصفية لا حرفية


# ══════════════════════════════════════════════════════════════════════════════
#  SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════
SCENARIOS = [

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S01",
        "name": "Order Flow كامل طبيعي",
        "goal": "تحية → منيو → منتج → تخصيص → اسم → عنوان → دفع → تأكيد",
        "thread": [
            ("هلا",
             "هلا بيك 🌷 شلون أكدر أخدمك؟"),
            ("عندكم منيو؟",
             "إي 🌷 عدنا برگر، زينگر، مشروبات، ومقبلات."),
            ("أريد بركر",
             "أكيد 🌷 بركر واحد؟"),
            ("بدون بصل",
             "أكيد 🌷 بدون بصل."),
            ("اسمي محمد",
             "تم محمد 🌷"),
            ("عنواني المنصور",
             "وصلني 🌷"),
            ("الدفع كاش",
             "أكيد 🌷"),
            ("يلا ثبت",
             "تم 🌷 ثبتت الطلب: بركر بدون بصل، الاسم محمد، العنوان المنصور، والدفع كاش."),
        ],
        "checks": [
            "no_regreeting",
            "no_repeat_name_q",
            "no_repeat_address_q",
            "single_summary",
            "no_early_confirm",
        ],
        "notes": [
            "الملخص النهائي يذكر: بركر، بدون بصل، محمد، المنصور، كاش",
            "ما يرجع يسأل الاسم بعد رسالة 5",
            "ما يرجع يسأل العنوان بعد رسالة 6",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S02",
        "name": "Multi-info من البداية",
        "goal": "التقاط كل المعلومات من رسالة واحدة — عدم إعادة الأسئلة",
        "thread": [
            ("أريد 2 زينگر، واحد حار وواحد عادي، اسمي علي، التوصيل للكرادة، والدفع كاش",
             "META: يؤكد كل المعلومات: 2 زينگر، حار وعادي، علي، الكرادة، كاش"),
            ("ثبت",
             "تم 🌷 ثبتت الطلب."),
        ],
        "checks": [
            "no_regreeting",
            "no_redundant_questions",
            "no_early_confirm",
        ],
        "notes": [
            "بعد رسالة 1: البوت ما يسأل عن معلومة ذُكرت",
            "إذا العنوان كافٍ: يثبت مباشرة",
            "إذا احتاج توضيح: يسأل عن الناقص فقط",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S03",
        "name": "تغيير رأي متعدد قبل التثبيت",
        "goal": "كل تبديل يُعتمد — آخر قرار هو المعتمد",
        "thread": [
            ("أريد بركر",           "أكيد 🌷 بركر واحد؟"),
            ("لا بدلها زينگر",      "تم 🌷 بدلناها زينگر."),
            ("لا رجعها بركر",       "تمام 🌷 رجعناها بركر."),
            ("خليها 2",             "تم 🌷 صارت 2."),
            ("لا، وحدة تكفي",       "تمام 🌷 صارت 1."),
            ("بدون بصل",            "أكيد 🌷 بدون بصل."),
            ("اسمي مصطفى",          "تم مصطفى 🌷"),
            ("استلام",              "تمام 🌷 استلام."),
            ("ثبت",
             "تم 🌷 ثبتت الطلب: بركر بدون بصل، باسم مصطفى، استلام."),
        ],
        "checks": [
            "no_regreeting",
            "single_summary",
            "no_early_confirm",
            "last_item_in_summary",
        ],
        "notes": [
            "الملخص يذكر: بركر (مو زينگر)، كمية 1 (مو 2)، مصطفى، استلام",
            "ردود قصيرة بكل تبديل — بدون شرح",
        ],
        # للتحقق من "آخر بند في الملخص"
        "_summary_must_contain": ["بركر", "مصطفى", "استلام"],
        "_summary_must_not_contain": ["زينگر", "كرادة"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S04",
        "name": "تغيير اسم + عنوان + نوع الطلب بعد الاكتمال",
        "goal": "كل تعديل يُطبَّق — آخر نسخة هي المعتمدة",
        "thread": [
            ("أريد زينگر",          "أكيد 🌷 زينگر واحد؟"),
            ("حار",                 "أكيد 🌷 حار."),
            ("اسمي علي",            "تم علي 🌷"),
            ("عنواني المنصور",      "وصلني 🌷"),
            ("كاش",                 "أكيد 🌷"),
            ("لا، العنوان الكرادة", "تمام 🌷 حدّثت العنوان إلى الكرادة."),
            ("لا، استلام مو توصيل","تمام 🌷 خليته استلام."),
            ("لا، الاسم محمد",      "تم محمد 🌷"),
            ("ثبت",
             "تم 🌷 ثبتت الطلب: زينگر حار، باسم محمد، استلام."),
        ],
        "checks": [
            "no_regreeting",
            "single_summary",
            "no_early_confirm",
            "last_item_in_summary",
        ],
        "notes": [
            "الملخص يذكر: محمد (مو علي)، استلام (مو توصيل)، زينگر حار",
            "إذا استلام: العنوان ما يظهر بالملخص",
        ],
        "_summary_must_contain": ["محمد", "استلام", "زينگر"],
        "_summary_must_not_contain": ["علي", "المنصور"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S05",
        "name": "شكوى تأخير + متابعة",
        "goal": "احتواء الشكوى — طلب أقل معلومات لازمة — لا بيع",
        "thread": [
            ("الطلب متأخر",
             "أعتذر على التأخير 🌷 كللي رقم الطلب أو الاسم."),
            ("اسمي محمد",
             "تمام 🌷 أراجع الطلب باسم محمد."),
            ("صار أكثر من ساعة",
             "أفهمك 🌷 أتابعها فورًا."),
            ("شنو حالة الطلب؟",
             "أشيكلك الحالة حالًا 🌷"),
            ("أريد حل هسه",
             "أكيد 🌷 أتابعها مباشرة، وإذا تحب أحولك لموظف."),
        ],
        "checks": [
            "no_regreeting",
            "no_upsell_in_complaint",
            "no_order_form_in_complaint",
        ],
        "notes": [
            "ممنوع: شنو تحب تطلب / توصيل أم استلام",
            "ممنوع: كثرة الاعتذار",
            "ما يفتح نموذج طلب جديد",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S06",
        "name": "شكوى + Handoff فوري",
        "goal": "Handoff سريع — ما يناقش بعد طلب الموظف — ما يرجع يبيع",
        "thread": [
            ("هذا مو طلبي",
             "أعتذر 🌷 كللي شنو وصلك وشنو كان طلبك."),
            ("طلبي كان بدون بصل",
             "وصلت 🌷"),
            ("ووصل ببصل",
             "أعتذر 🌷 أتابعها فورًا."),
            ("ما أريد أحچي ويا بوت",
             "أكيد 🌷 أحولك لموظف مباشرة."),
            ("أريد موظف هسه",
             "أكيد 🌷 تم التحويل هسه."),
        ],
        "checks": [
            "no_regreeting",
            "handoff_on_request",
            "no_sale_after_handoff",
            "no_repeat_problem_question",
            "no_banned_phrases",
        ],
        "notes": [
            "بعد رسالة 4: ما يسأل عن المشكلة مرة ثانية",
            "بعد رسالة 4: ما يفتح sale flow",
            "الـ handoff يكون فوري وقصير",
            "ممنوع: انتظر شوي",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S07",
        "name": "Story → DM → Order",
        "goal": "يبقى مرتبط بالمنتج — لا يتحول لـ DM عامة — لا يسأل 'شنو تحب تطلب؟'",
        "thread": [
            ("[رد على ستوري منتج] بكم هذا؟",
             "META: يذكر سعر أو يسأل عن المنتج بشكل طبيعي"),
            ("أريد 2",
             "أكيد 🌷 2 منه."),
            ("واحد بدون بصل",
             "تمام 🌷 واحد بدون بصل."),
            ("اسمي محمد",
             "تم محمد 🌷"),
            ("عنواني المنصور",
             "وصلني 🌷"),
            ("كاش",
             "أكيد 🌷"),
            ("ثبت",
             "META: ملخص يذكر: 2، واحد بدون بصل، محمد، المنصور، كاش"),
        ],
        "checks": [
            "no_regreeting",
            "single_summary",
            "no_early_confirm",
        ],
        "notes": [
            "الرسالة الأولى: لا يقول 'شنو تحب تطلب؟' — هناك سياق",
            "يكمل الطلب بشكل طبيعي بدون إعادة التعريف بنفسه",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S08",
        "name": "Story عرض + استفسار + طلب",
        "goal": "شرح العرض بشكل قصير — لا هلوسة — يكمل الطلب طبيعيًا",
        "thread": [
            ("[رد على ستوري عرض] ما فهمت العرض",
             "META: يشرح العرض بجملة قصيرة — لا يهلوس بتفاصيل غير موجودة"),
            ("أوك أريده",
             "تمام 🌷 أثبتلك العرض."),
            ("يوصل للكرادة؟",
             "META: إي/لا حسب التغطية — جملة واحدة"),
            ("اسمي سيف",
             "تم سيف 🌷"),
            ("عنواني الكرادة",
             "وصلني 🌷"),
            ("ثبت",
             "META: يثبت الطلب — يذكر: سيف، الكرادة"),
        ],
        "checks": [
            "no_regreeting",
            "no_hallucinate_offer",
            "single_summary",
            "no_early_confirm",
        ],
        "notes": [
            "شرح العرض: جملة واحدة بدون اختراع تفاصيل",
            "إذا التوصيل غير متاح: يوضح فورًا",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S09",
        "name": "Memory داخل الجلسة + تصحيح سلوك",
        "goal": "يتذكر الاسم — يطبق التصحيح فورًا — ما يكرر بدون داعي",
        "thread": [
            ("اسمي محمد",
             "تم محمد 🌷"),
            ("لا تكولي أستاذ",
             "وصلت محمد، راح أناديك بالاسم فقط 🌷"),
            ("أريد بركر",
             "أكيد 🌷 بركر واحد؟"),
            ("بدون بصل",
             "أكيد 🌷 بدون بصل."),
            ("شنو سجلت علي؟",
             "META: يذكر: بركر بدون بصل، محمد — لا يقول ما عندي سجل"),
            ("لا تعيد اسمي بكل رسالة",
             "أكيد 🌷 ما أكرره بدون داعي."),
            ("ثبت",
             "META: يطلب الناقص (عنوان؟ دفع؟) أو يثبت إذا مكتمل"),
        ],
        "checks": [
            "no_regreeting",
            "no_title_after_correction",
            "recalls_conversation_info",
            "no_repeat_name_after_instruction",
        ],
        "notes": [
            "بعد رسالة 2: ما يستخدم 'أستاذ' أو 'حجي' أو لقب",
            "رسالة 5: يتذكر بركر بدون بصل ومحمد",
            "بعد رسالة 6: ما يكرر الاسم بدون سبب",
        ],
        "_recall_at_msg_idx": 4,  # index of "شنو سجلت علي؟"
        "_recall_must_contain": ["بركر", "بدون بصل", "محمد"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":   "S10",
        "name": "عميل غامض ومستعجل",
        "goal": "ردود قصيرة — اقتراح واحد — يسأل عن الناقص فقط",
        "thread": [
            ("أريد أطلب بسرعة",
             "أكيد 🌷 كللي الطلب مباشرة."),
            ("أي شي",
             "META: يرشح خيار واحد مناسب — لا قائمة"),
            ("أرخص شي",
             "META: يذكر أرخص خيار — جملة واحدة"),
            ("اختصر",
             "تمام 🌷"),
            ("يلا",
             "META: يسأل عن الناقص فقط — الاسم أو العنوان — لا يعيد كل شيء"),
            ("كاش",
             "أكيد 🌷"),
            ("عنواني المنصور",
             "وصلني 🌷"),
            ("ثبت",
             "META: يثبت — يذكر المنتج المختار والمنصور وكاش"),
        ],
        "checks": [
            "no_regreeting",
            "no_over_suggest",
            "asks_only_missing",
        ],
        "notes": [
            "ما يفتح 4 اقتراحات",
            "ما يضيع من 'أي شي'",
            "يسأل عن الناقص فقط — لا يعيد ما ذُكر",
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  API helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_token():
    data = json.dumps({"email": "admin@restaurant.com",
                       "password": "admin123"}).encode()
    req  = urllib.request.Request(
        f"{BASE}/api/auth/login", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["token"]


def simulate_thread(messages_list, token):
    """Send cumulative thread, return bot reply for last message."""
    data = json.dumps({"messages": messages_list,
                       "scenario": "default"}).encode()
    req  = urllib.request.Request(
        f"{BASE}/api/bot/simulate", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body    = json.loads(r.read())
                results = body.get("results", [])
                return results[-1].get("bot", "") if results else ""
        except Exception as e:
            if attempt < 2:
                time.sleep(6)
            else:
                return f"ERROR:{e}"
    return "ERROR:max_retries"


# ══════════════════════════════════════════════════════════════════════════════
#  Per-message scoring
# ══════════════════════════════════════════════════════════════════════════════

def score_message(bot_reply, expected):
    """
    2 = جيد (مستوى الجواب المتوقع أو أفضل)
    1 = مقبول (الجواب المتوقع أفضل)
    0 = فشل
    Returns (score, note)
    """
    is_meta = expected.startswith("META:")

    # فشل: خطأ تقني
    if "عذراً، حدث خطأ تقني" in bot_reply or bot_reply.startswith("ERROR"):
        return 0, "❌ خطأ تقني"

    # فشل: نبرة باردة رسمية
    if any(p in bot_reply for p in COLD_PHRASES):
        return 0, "❌ نبرة رسمية"

    bot_len = len(bot_reply)
    exp_len = len(expected)

    # Meta expectations: أي رد معقول يقبل — فقط نتحقق الطول والنبرة
    if is_meta:
        if bot_len > 250:
            return 1, f"⚠️ طويل جدًا ({bot_len})"
        return 2, "✅"

    # ملخص تأكيد — رسالة "ثبت": السماح بـ receipt format (حتى 250 حرف)
    if "ثبتت" in expected or "✅" in expected or "ثبتت" in bot_reply:
        if bot_len <= 250:
            return 2, "✅"
        return 1, f"⚠️ ملخص طويل ({bot_len})"

    # توقعات حرفية قصيرة (≤ 20 حرف): البوت لازم يكون قصير
    if exp_len <= 20:
        if bot_len <= 50:
            return 2, "✅"
        elif bot_len <= 100:
            return 1, f"⚠️ طويل ({bot_len}>{exp_len})"
        else:
            return 0, f"❌ طويل جدًا ({bot_len}>{exp_len})"

    # عام: طول مقبول؟
    if bot_len > exp_len * 1.6:
        return 1, f"⚠️ طويل ({bot_len}>{exp_len})"

    return 2, "✅"


# ══════════════════════════════════════════════════════════════════════════════
#  Coherence checks
# ══════════════════════════════════════════════════════════════════════════════

def check_coherence(scenario, user_msgs, bot_replies):
    """Returns list of violation strings."""
    checks    = scenario["checks"]
    violations = []

    # ── no_regreeting: ما يرجع يرحب بعد الرسالة الأولى ──────────────────────
    if "no_regreeting" in checks:
        for i in range(1, len(bot_replies)):
            if any(g in bot_replies[i] for g in REGREETING):
                violations.append(f"re_greet|أعاد الترحيب في رسالة {i+1}")

    # ── no_repeat_name_q: ما يسأل الاسم بعد ما ذُكر ─────────────────────────
    if "no_repeat_name_q" in checks:
        name_given_at = None
        for i, msg in enumerate(user_msgs):
            if "اسمي " in msg:
                name_given_at = i
            elif name_given_at is not None:
                name_qs = ["شنو اسمك", "الاسم؟", "كللي اسمك", "اسمك؟",
                           "ذكر لي اسمك", "تعطيني اسمك"]
                if any(k in bot_replies[i] for k in name_qs):
                    violations.append(
                        f"repeat_name_q|سأل الاسم مرة ثانية في رسالة {i+1}")

    # ── no_repeat_address_q: ما يسأل العنوان بعد ما ذُكر ────────────────────
    if "no_repeat_address_q" in checks:
        addr_given_at = None
        for i, msg in enumerate(user_msgs):
            if "عنواني" in msg or "للكرادة" in msg or "للمنصور" in msg:
                addr_given_at = i
            elif addr_given_at is not None:
                addr_qs = ["العنوان؟", "وين تسكن", "كللي العنوان",
                           "أرسللي العنوان", "عنوانك؟"]
                if any(k in bot_replies[i] for k in addr_qs):
                    violations.append(
                        f"repeat_addr_q|سأل العنوان مرة ثانية في رسالة {i+1}")

    # ── single_summary: الملخص يجي مرة وحدة ────────────────────────────────
    if "single_summary" in checks:
        summary_markers = ["✅ طلبك", "تم 🌷 ثبتت", "ثبتت الطلب"]
        count = sum(
            1 for r in bot_replies
            if any(m in r for m in summary_markers)
        )
        if count > 1:
            violations.append(
                f"double_summary|أعاد ملخص الطلب {count} مرات")

    # ── no_early_confirm: ما يثبت الطلب قبل رسالة "ثبت" ─────────────────────
    if "no_early_confirm" in checks:
        confirm_markers = ["✅ طلبك", "تم 🌷 ثبتت", "ثبتت الطلب"]
        for i in range(len(bot_replies) - 1):
            if any(m in bot_replies[i] for m in confirm_markers):
                # تحقق: هل المستخدم قال "ثبت" في رسالة i أو أقدم؟
                if "ثبت" not in user_msgs[i]:
                    violations.append(
                        f"early_confirm|ثبت الطلب قبل طلب العميل في رسالة {i+1}")

    # ── no_redundant_questions: ما يسأل عن شيء ذُكر في نفس الرسالة الأولى ──
    if "no_redundant_questions" in checks:
        first_msg = user_msgs[0]
        given_info = []
        if "اسمي" in first_msg or "اسم" in first_msg:
            given_info.append("الاسم")
        if "كاش" in first_msg or "visa" in first_msg.lower():
            given_info.append("الدفع")
        if "عنوان" in first_msg or "للكرادة" in first_msg or "للمنصور" in first_msg:
            given_info.append("العنوان")

        for info in given_info:
            info_qs = {
                "الاسم":   ["شنو اسمك", "الاسم؟", "كللي اسمك"],
                "الدفع":   ["طريقة الدفع", "كاش أم", "دفع؟"],
                "العنوان": ["العنوان؟", "أرسللي العنوان", "كللي العنوان"],
            }
            for i, r in enumerate(bot_replies):
                if any(k in r for k in info_qs.get(info, [])):
                    violations.append(
                        f"redundant_q|سأل عن {info} رغم أنه ذُكر — رسالة {i+1}")

    # ── handoff_on_request: يحوّل فورًا عند الطلب ───────────────────────────
    if "handoff_on_request" in checks:
        for i, msg in enumerate(user_msgs):
            if "موظف" in msg or "ما أريد أحچي ويا بوت" in msg:
                handoff_kws = ["أحولك", "تم التحويل", "موظف"]
                if not any(k in bot_replies[i] for k in handoff_kws):
                    violations.append(
                        f"no_handoff|ما حوّل للموظف عند الطلب في رسالة {i+1}")

    # ── no_sale_after_handoff: ما يبيع بعد طلب الـ handoff ──────────────────
    if "no_sale_after_handoff" in checks:
        handoff_idx = None
        for i, msg in enumerate(user_msgs):
            if "موظف" in msg or "ما أريد أحچي ويا بوت" in msg:
                handoff_idx = i
                break
        if handoff_idx is not None:
            sale_kws = ["تحب تطلب", "عندنا", "إضافة", "ترشيح", "منيو"]
            for i in range(handoff_idx + 1, len(bot_replies)):
                if any(k in bot_replies[i] for k in sale_kws):
                    violations.append(
                        f"sale_after_handoff|تابع البيع بعد طلب handoff في رسالة {i+1}")

    # ── no_upsell_in_complaint: ما يبيع أثناء الشكوى ────────────────────────
    if "no_upsell_in_complaint" in checks:
        upsell_kws = ["شنو تحب تطلب", "تحب تطلب شي",
                      "تضيف", "منيو", "ترشيح لك"]
        for i, r in enumerate(bot_replies):
            if any(k in r for k in upsell_kws):
                violations.append(
                    f"upsell_complaint|حاول البيع أثناء الشكوى في رسالة {i+1}")

    # ── no_order_form_in_complaint: ما يفتح نموذج طلب أثناء الشكوى ──────────
    if "no_order_form_in_complaint" in checks:
        order_qs = ["توصيل أم استلام", "كللي عنوانك", "طريقة الدفع",
                    "توصيل أو استلام"]
        for i, r in enumerate(bot_replies):
            if any(k in r for k in order_qs):
                violations.append(
                    f"order_form|فتح نموذج طلب أثناء الشكوى في رسالة {i+1}")

    # ── no_repeat_problem_question: ما يعيد سؤال المشكلة بعد شرحها ──────────
    if "no_repeat_problem_question" in checks:
        problem_given = False
        for i, msg in enumerate(user_msgs):
            if "بدون بصل" in msg or "خطأ" in msg or "وصل" in msg:
                problem_given = True
            elif problem_given:
                repeat_qs = ["شنو المشكلة", "وين المشكلة",
                             "شنو اللي وصل", "كللي التفاصيل"]
                if any(k in bot_replies[i] for k in repeat_qs):
                    violations.append(
                        f"repeat_problem_q|أعاد سؤال المشكلة في رسالة {i+1}")

    # ── no_hallucinate_offer: ما يختلق تفاصيل عرض غير موجودة ────────────────
    if "no_hallucinate_offer" in checks:
        hallucinate_kws = [
            "خصم 50%", "خصم 30%", "مجاني", "هدية",
            "وجبتين", "بالنص", "وجبة مجانية",
        ]
        for i, r in enumerate(bot_replies):
            if any(k in r for k in hallucinate_kws):
                violations.append(
                    f"hallucinate|اخترع تفاصيل عرض في رسالة {i+1}: {r[:50]}")

    # ── no_title_after_correction: ما يستخدم لقب بعد التصحيح ────────────────
    if "no_title_after_correction" in checks:
        title_kws    = ["أستاذ", "حجي", "أبو "]
        corrected_at = None
        for i, msg in enumerate(user_msgs):
            if "لا تكولي أستاذ" in msg or "لا تستخدم" in msg:
                corrected_at = i
                break
        if corrected_at is not None:
            for i in range(corrected_at + 1, len(bot_replies)):
                if any(k in bot_replies[i] for k in title_kws):
                    violations.append(
                        f"title_after_correction|استخدم لقبًا بعد التصحيح في رسالة {i+1}")

    # ── recalls_conversation_info: يتذكر معلومات نفس الجلسة ─────────────────
    if "recalls_conversation_info" in checks:
        recall_idx  = scenario.get("_recall_at_msg_idx")
        must_recall = scenario.get("_recall_must_contain", [])
        if recall_idx is not None and recall_idx < len(bot_replies):
            reply = bot_replies[recall_idx]
            missing = [k for k in must_recall if k not in reply]
            if missing:
                violations.append(
                    f"recall_fail|لم يتذكر في رسالة {recall_idx+1}: {', '.join(missing)}")

    # ── no_repeat_name_after_instruction: ما يكرر الاسم بدون سبب ────────────
    if "no_repeat_name_after_instruction" in checks:
        instruction_at = None
        for i, msg in enumerate(user_msgs):
            if "لا تعيد اسمي" in msg or "لا تكتب اسمي" in msg:
                instruction_at = i
                break
        if instruction_at is not None:
            # Extract the name given earlier
            name = None
            for msg in user_msgs[:instruction_at]:
                if "اسمي " in msg:
                    name = msg.replace("اسمي ", "").strip()
                    break
            if name:
                last_idx = len(bot_replies) - 1
                for i in range(instruction_at + 1, len(bot_replies)):
                    # الرسالة الأخيرة (ثبت/ملخص) يحق فيها ذكر الاسم
                    if i == last_idx:
                        continue
                    if name in bot_replies[i]:
                        violations.append(
                            f"repeat_name|كرر الاسم '{name}' بعد التعليمات في رسالة {i+1}")

    # ── last_item_in_summary: الملخص النهائي يعكس آخر تعديل ─────────────────
    if "last_item_in_summary" in checks:
        must_have     = scenario.get("_summary_must_contain", [])
        must_not_have = scenario.get("_summary_must_not_contain", [])
        summary       = bot_replies[-1]
        # تخطّى إذا الرد الأخير ما كان ملخصًا
        is_real_summary = ("✅" in summary or "ثبتت" in summary or "طلبك" in summary)
        if not is_real_summary:
            pass  # البوت ما أرسل ملخصًا أصلاً — لا تعاقب
        else:
            # normalize گ ↔ ج ↔ غ للمقارنة (زينگر / زينجر / زينغر)
            summary_norm = summary.replace("ج", "گ").replace("غ", "گ").replace("گ", "گ")
            for item in must_have:
                item_norm = item.replace("ج", "گ").replace("غ", "گ")
                if item_norm not in summary_norm and item not in summary:
                    violations.append(
                        f"summary_missing|الملخص ناقص '{item}'")
            for item in must_not_have:
                if item in summary:
                    violations.append(
                        f"summary_wrong|الملخص يذكر '{item}' وكان المفروض حُذف")

    # ── no_over_suggest: لا يفتح قائمة اقتراحات طويلة ──────────────────────
    if "no_over_suggest" in checks:
        for i, r in enumerate(bot_replies):
            # 3+ bullet points أو منتجات
            if r.count("•") >= 3 or r.count("،") >= 4:
                violations.append(
                    f"over_suggest|أعطى قائمة طويلة في رسالة {i+1}")

    # ── no_banned_phrases: عبارات ممنوعة في أي رسالة ────────────────────────
    if "no_banned_phrases" in checks:
        banned = ["انتظر شوي", "لحظة من فضلك", "ارجع بعد شوي"]
        for i, r in enumerate(bot_replies):
            for phrase in banned:
                if phrase in r:
                    violations.append(
                        f"banned_phrase|استخدم '{phrase}' في رسالة {i+1}")

    # ── asks_only_missing: يسأل فقط عن الناقص ───────────────────────────────
    if "asks_only_missing" in checks:
        # بعد ما العنوان انذكر، ما يسأل عنه مرة ثانية
        addr_given = False
        name_given = False
        for i, msg in enumerate(user_msgs):
            if "عنواني" in msg:
                addr_given = True
            if "اسمي" in msg:
                name_given = True
            if addr_given:
                addr_qs = ["العنوان؟", "أرسللي العنوان", "كللي العنوان"]
                if any(k in bot_replies[i] for k in addr_qs):
                    violations.append(
                        f"redundant_addr_q|سأل عن العنوان بعد ما ذُكر في رسالة {i+1}")
            if name_given:
                name_qs = ["شنو اسمك", "الاسم؟", "كللي اسمك"]
                if any(k in bot_replies[i] for k in name_qs):
                    violations.append(
                        f"redundant_name_q|سأل عن الاسم بعد ما ذُكر في رسالة {i+1}")

    return violations


# ══════════════════════════════════════════════════════════════════════════════
#  Main runner
# ══════════════════════════════════════════════════════════════════════════════

def main():
    token = get_token()

    all_scenario_results = []
    issue_counter        = Counter()

    print(f"\n⏳ جاري تشغيل 10 سيناريوهات كاملة...\n")

    for scenario in SCENARIOS:
        sid   = scenario["id"]
        name  = scenario["name"]
        goal  = scenario["goal"]
        thread = scenario["thread"]

        print(f"\n{'━'*60}")
        print(f"  {sid} — {name}")
        print(f"  الهدف: {goal}")
        print(f"{'━'*60}")

        msgs_so_far = []
        bot_replies = []
        msg_results = []

        for i, (user_msg, expected) in enumerate(thread):
            msgs_so_far.append(user_msg)
            window = msgs_so_far[-20:]

            bot_reply = simulate_thread(window, token)

            # Retry up to 2 times on transient error
            for _retry in range(2):
                if "عذراً، حدث خطأ تقني" in bot_reply or bot_reply.startswith("ERROR"):
                    time.sleep(8)
                    bot_reply = simulate_thread(window, token)
                else:
                    break

            bot_replies.append(bot_reply)

            score, note = score_message(bot_reply, expected)
            msg_results.append((user_msg, bot_reply, expected, score, note))

            sym = "✅" if score >= 2 else ("⚠️" if score == 1 else "❌")

            # Truncate for display
            u_short = user_msg[:35].ljust(35)
            b_short = bot_reply[:55]
            e_short = (expected[:55] if not expected.startswith("META:")
                       else expected[5:55])

            print(f"  {sym} [M{i+1:02d}] {u_short}")
            print(f"         البوت   : {b_short}")
            print(f"         المتوقع : {e_short}")
            if note != "✅":
                print(f"         ملاحظة  : {note}")

            time.sleep(DELAY)

        # Sleep between scenarios to avoid server overload
        time.sleep(8)

        # ── Coherence checks ────────────────────────────────────────────────
        violations = check_coherence(
            scenario,
            [t[0] for t in thread],
            bot_replies,
        )

        # ── Scenario score ───────────────────────────────────────────────────
        msg_scores = [r[3] for r in msg_results]
        n_total    = len(msg_scores)
        n_pass     = sum(1 for s in msg_scores if s >= 2)
        msg_pct    = n_pass / n_total

        has_critical = len(violations) > 0

        if has_critical:
            s_score = 0
        elif msg_pct >= 0.75:
            s_score = 2
        else:
            s_score = 1

        s_sym = "✅" if s_score >= 2 else ("⚠️" if s_score == 1 else "❌")

        print(f"\n  {s_sym} {sid}: {n_pass}/{n_total} رسائل صح ({int(msg_pct*100)}%)"
              f"  |  خروقات: {len(violations)}")

        if violations:
            for v in violations:
                cat, detail = v.split("|", 1)
                print(f"       ⚠️  {detail}")
                issue_counter[cat] += 1

        all_scenario_results.append({
            "id":          sid,
            "name":        name,
            "score":       s_score,
            "msg_pct":     msg_pct,
            "n_pass":      n_pass,
            "n_total":     n_total,
            "violations":  violations,
            "msg_results": msg_results,
            "bot_replies": bot_replies,
        })

    # ══════════════════════════════════════════════════════════════════════════
    #  Summary
    # ══════════════════════════════════════════════════════════════════════════

    s_pass    = sum(1 for r in all_scenario_results if r["score"] >= 2)
    s_partial = sum(1 for r in all_scenario_results if r["score"] == 1)
    s_fail    = sum(1 for r in all_scenario_results if r["score"] == 0)
    total_pct = int(s_pass / len(SCENARIOS) * 100)

    print(f"\n{'='*60}")
    print(f"  Day 7 النتيجة: {s_pass}/10 سيناريوهات ناجحة ({total_pct}%)")
    print(f"  ناجح: {s_pass}  |  جزئي: {s_partial}  |  فاشل: {s_fail}")
    print(f"{'='*60}")

    # Per-scenario table
    print(f"\n  ملخص السيناريوهات:")
    for r in all_scenario_results:
        sym   = "✅" if r["score"] >= 2 else ("⚠️" if r["score"] == 1 else "❌")
        viol  = f"  ⚠️ {len(r['violations'])} خرق" if r["violations"] else ""
        print(f"  {sym} {r['id']} — {r['name'][:35]}"
              f"  ({r['n_pass']}/{r['n_total']} رسائل){viol}")

    # Top 3 issues
    print(f"\n  أكثر 3 مشاكل ظهرت:")
    # Readable category names
    cat_names = {
        "re_greet":             "إعادة الترحيب في وسط الجلسة",
        "repeat_name_q":        "تكرار سؤال الاسم",
        "repeat_addr_q":        "تكرار سؤال العنوان",
        "double_summary":       "تكرار ملخص الطلب",
        "early_confirm":        "التأكيد المبكر",
        "no_handoff":           "فشل تحويل الموظف",
        "sale_after_handoff":   "البيع بعد طلب الـ Handoff",
        "upsell_complaint":     "البيع أثناء الشكوى",
        "order_form":           "نموذج طلب أثناء الشكوى",
        "repeat_problem_q":     "إعادة سؤال المشكلة",
        "hallucinate":          "هلوسة تفاصيل العرض",
        "title_after_correction": "لقب بعد التصحيح",
        "recall_fail":          "فشل تذكر معلومات الجلسة",
        "repeat_name":          "تكرار الاسم بعد التعليمات",
        "summary_missing":      "ملخص ناقص معلومة",
        "summary_wrong":        "ملخص يذكر معلومة قديمة",
        "over_suggest":         "قائمة اقتراحات طويلة",
        "redundant_q":          "سؤال زائد عن معلومة مذكورة",
        "redundant_addr_q":     "سؤال زائد عن العنوان",
        "redundant_name_q":     "سؤال زائد عن الاسم",
    }
    if issue_counter:
        for cat, cnt in issue_counter.most_common(3):
            readable = cat_names.get(cat, cat)
            print(f"  • {readable}: {cnt} مرة")
    else:
        print(f"  • لا مشاكل coherence — ممتاز!")

    # Recommendation
    if s_pass >= 8:
        recommendation = "✅ نكمل بقية السيناريوهات (Day 7 جاهز للـ pilot)"
    elif s_pass >= 6:
        recommendation = "⚠️ نصلح المشاكل الـ 3 الأعلى أولًا ثم نكمل"
    else:
        recommendation = "❌ نصلح أولًا قبل نكمل — مشاكل أساسية"

    print(f"\n  التوصية: {recommendation}")
    print(f"{'='*60}\n")

    # Save report
    report_path = "scripts/day7_compare_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Day 7 — تقرير المحاكاة الكاملة | "
                f"{datetime.datetime.now():%Y-%m-%d %H:%M}\n")
        f.write(f"النتيجة: {s_pass}/10 ({total_pct}%)\n\n")
        for r in all_scenario_results:
            sym = "✅" if r["score"] >= 2 else ("⚠️" if r["score"] == 1 else "❌")
            f.write(f"{sym} {r['id']} — {r['name']}\n")
            f.write(f"   رسائل: {r['n_pass']}/{r['n_total']}"
                    f"  |  خروقات: {len(r['violations'])}\n")
            for qid, (user_msg, bot_reply, expected, score, note) in \
                    enumerate(r["msg_results"], 1):
                s = "✅" if score >= 2 else ("⚠️" if score == 1 else "❌")
                f.write(f"   {s} M{qid:02d}: {user_msg[:40]}\n")
                f.write(f"        البوت  : {bot_reply[:80]}\n")
                f.write(f"        المتوقع: {expected[:80]}\n")
            if r["violations"]:
                f.write(f"   خروقات:\n")
                for v in r["violations"]:
                    f.write(f"   ⚠️ {v.split('|',1)[1]}\n")
            f.write("\n")
        f.write(f"أكثر المشاكل:\n")
        for cat, cnt in issue_counter.most_common(5):
            f.write(f"  • {cat_names.get(cat, cat)}: {cnt}\n")
        f.write(f"\nالتوصية: {recommendation}\n")

    print(f"  ✅ التقرير محفوظ في: {report_path}")


if __name__ == "__main__":
    main()
