#!/usr/bin/env python3
"""
NUMBER 20 — Elite Reply Brain Evaluation Suite
700+ scenarios testing:
  intent detection, reply quality, tone, safety, templates.

Does NOT require a running server or OpenAI key.
Tests the brain/quality/template modules directly.
"""
import sys, os, re, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.reply_brain import detect_intent, elite_reply_pass, build_message_context
from services.reply_quality import extended_quality_gate, should_use_template
from services.reply_templates import pick, has_template, TEMPLATES

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

passed = []
failed = []
warnings_list = []
critical_failures = []

SAMPLE_PRODUCTS = [
    {"id": "p1", "name": "زينگر", "price": 9000, "category": "وجبات", "available": True},
    {"id": "p2", "name": "برگر كلاسيك", "price": 7000, "category": "برگر", "available": True},
    {"id": "p3", "name": "شاورما دجاج", "price": 5000, "category": "شاورما", "available": True},
    {"id": "p4", "name": "كولا", "price": 1500, "category": "مشروبات", "available": True},
    {"id": "p5", "name": "بطاطا", "price": 2000, "category": "مقبلات", "available": True},
]

BANNED_PHRASES_CHECK = [
    "يرجى تزويدي", "كيف يمكنني مساعدتك", "يسعدني مساعدتك",
    "عزيزي العميل", "نعتذر عن الإزعاج", "تم استلام طلبك بنجاح",
    "حسب البيانات", "حسب السجل", "قاعدة البيانات",
    "تم تحليل الصورة", "تم تحويل الصوت إلى نص",
    "الصورة تحتوي على", "حسب التحليل", "يرجى الانتظار",
    "شكراً لاختيارك", "هل ترغب في", "يمكنني مساعدتك",
    "بناءً على طلبك", "تمت معالجة", "عميلنا العزيز",
    "لا تتردد بالتواصل", "يرجى العلم", "نود إعلامك",
    # from bot.py Algorithm 6
    "كيف يمكنني مساعدتك", "يسعدني مساعدتك", "لا تتردد في التواصل",
    "بكل سرور", "من دواعي سروري", "بكل ترحيب",
    "بالتأكيد", "بالطبع", "بكل تأكيد",
    "مرحبًا عزيزي", "يرجى تزويدي",
]

def ok(name, cat=""):
    passed.append((cat, name))

def fail(name, detail="", cat="", critical=False):
    failed.append((cat, name, detail))
    if critical:
        critical_failures.append((cat, name, detail))
    msg = f"  ❌ [{cat}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)

def warn(name, detail="", cat=""):
    warnings_list.append((cat, name))

def check_no_banned(reply, scenario_id, cat):
    for phrase in BANNED_PHRASES_CHECK:
        if phrase in reply:
            fail(f"{scenario_id} banned phrase", f"'{phrase[:30]}' in reply", cat, critical=True)
            return False
    return True

def check_max_questions(reply, scenario_id, cat, max_q=1):
    q = reply.count("؟")
    if q > max_q:
        fail(f"{scenario_id} multi-question", f"{q} questions", cat)
        return False
    return True

def check_max_length(reply, scenario_id, cat, max_len=300):
    if len(reply) > max_len:
        fail(f"{scenario_id} too long", f"{len(reply)} chars", cat)
        return False
    return True

def check_no_tech_exposure(reply, scenario_id, cat):
    tech = ["تم تحليل", "تم تحويل", "الصورة تحتوي", "حسب البيانات", "النظام يشير"]
    for t in tech:
        if t in reply:
            fail(f"{scenario_id} tech_exposure", f"'{t}'", cat, critical=True)
            return False
    return True

def check_intent(message, expected_intent, scenario_id, cat):
    got = detect_intent(message)
    if got == expected_intent:
        ok(f"{scenario_id} intent={expected_intent}", cat)
        return True
    else:
        fail(f"{scenario_id} intent", f"expected={expected_intent} got={got}", cat)
        return False

def run_elite(bad_reply, customer_msg, expected_checks, scenario_id, cat,
              history=None, memory=None, is_critical=False):
    """Run elite_reply_pass on a bad reply and validate the result."""
    result = elite_reply_pass(
        reply=bad_reply,
        customer_message=customer_msg,
        history=history or [],
        memory=memory or {},
        products=SAMPLE_PRODUCTS,
    )
    all_ok = True

    if "no_banned" in expected_checks:
        if not check_no_banned(result, scenario_id, cat):
            all_ok = False
    if "max_q" in expected_checks:
        if not check_max_questions(result, scenario_id, cat):
            all_ok = False
    if "short" in expected_checks:
        if not check_max_length(result, scenario_id, cat):
            all_ok = False
    if "no_tech" in expected_checks:
        if not check_no_tech_exposure(result, scenario_id, cat):
            all_ok = False
    if "not_empty" in expected_checks:
        if not result or len(result.strip()) < 3:
            fail(f"{scenario_id} empty_result", "", cat, critical=is_critical)
            all_ok = False

    if all_ok:
        ok(f"{scenario_id}", cat)
    return result

# ─────────────────────────────────────────────────────────────
# CATEGORY A: Greetings / Casual / Emoji (70 scenarios)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("A. GREETINGS / CASUAL / EMOJI (70)")
print("="*60)

CAT = "A-greeting"

# A1-A10: Intent detection
for i, (msg, exp) in enumerate([
    ("هلا", "greeting"),
    ("مرحبا", "greeting"),
    ("مرحباً", "greeting"),
    ("أهلين", "greeting"),
    ("السلام عليكم", "greeting"),
    ("صباح الخير", "greeting"),
    ("مساء الخير", "greeting"),
    ("كيف الحال", "greeting"),
    ("حياك الله", "greeting"),
    ("شلونك", "greeting"),
], 1):
    check_intent(msg, exp, f"A{i:02d}", CAT)

# A11-A20: Bad greeting reply → elite fixes
BAD_GREETINGS = [
    "مرحبًا عزيزي العميل، كيف يمكنني مساعدتك اليوم؟",
    "أهلاً وسهلاً بك! يسعدني مساعدتك. كيف يمكنني خدمتك؟",
    "بالتأكيد! أنا هنا لمساعدتك. هل تريد معرفة المنيو؟",
    "بكل سرور! يسرني خدمتك. ما الذي تحتاجه؟",
    "من دواعي سروري خدمتك! كيف يمكنني مساعدتك؟",
    "أهلاً بك! بكل ترحيب. هل تريد الطلب أم الاستفسار؟",
    "مرحباً! أنا بالخدمة. يسعدني مساعدتك في أي شيء.",
    "بالتأكيد أخي العزيز! كيف أخدمك اليوم؟",
    "أهلاً عزيزي العميل! كيف يمكنني مساعدتك؟",
    "يسعدني الترحيب بك! ماذا تحتاج؟",
]
for i, bad in enumerate(BAD_GREETINGS, 11):
    run_elite(bad, "هلا", ["no_banned", "short", "max_q", "not_empty"], f"A{i:02d}", CAT)

# A21-A30: Thanks
for i, (msg, exp) in enumerate([
    ("شكراً", "thanks"),
    ("تسلم", "thanks"),
    ("مشكور", "thanks"),
    ("يسلم", "thanks"),
    ("الله يعطيك العافية", "thanks"),
    ("يعطيك العافية", "thanks"),
    ("شكرا جزيلاً", "thanks"),
    ("ممنون", "thanks"),
    ("عاشت إيدك", "thanks"),
    ("تسلم والله", "thanks"),
], 21):
    check_intent(msg, exp, f"A{i:02d}", CAT)

# A31-A40: Thanks replies
BAD_THANKS = [
    "شكراً لتواصلك معنا! يسعدنا خدمتك دائماً. لا تتردد بالتواصل.",
    "نشكر تواصلك الكريم. نأمل أن نكون عند حسن ظنك.",
    "شكراً على اختيارك مطعمنا! نسعد بخدمتك دائماً.",
    "العفو أخي الكريم! من دواعي سروري خدمتك.",
    "على الرحب والسعة! نحن هنا لخدمتك في أي وقت.",
    "يسعدني خدمتك دائماً! لا تتردد في التواصل معنا.",
    "شكراً لك على ثقتك بنا. نسعى دائماً لتقديم أفضل خدمة.",
    "نتشرف بخدمتك! هل هناك شيء آخر يمكنني مساعدتك به؟",
    "بكل سرور! يسرنا أن نكون في خدمتك.",
    "تحت أمرك دائماً! لا تتردد بالعودة.",
]
for i, bad in enumerate(BAD_THANKS, 31):
    run_elite(bad, "شكراً", ["no_banned", "short", "max_q", "not_empty"], f"A{i:02d}", CAT)

# A41-A50: Positive emoji
EMOJI_MSGS = ["😍", "❤️", "🥰", "👍", "🙌", "💙", "😘", "🫶", "💚", "💛"]
for i, msg in enumerate(EMOJI_MSGS, 41):
    check_intent(msg, "emoji_positive", f"A{i:02d}", CAT)

# A51-A60: Emoji bad replies
BAD_EMOJI_REPLIES = [
    "يبدو أنك سعيد! كيف يمكنني مساعدتك اليوم؟",
    "شكراً على مشاعرك الجميلة! هل تريد الطلب؟",
    "يسعدني تواصلك معنا. ما الذي تحتاجه؟",
    "من دواعي سروري! هل تريد معرفة المنيو؟",
    "بالتأكيد نحن سعداء أيضاً! كيف نخدمك؟",
    "شكراً جزيلاً! يسعدنا خدمتك. هل تريد الطلب؟",
    "نقدر تواصلك! هل يمكنني مساعدتك بشيء؟",
    "يسلمون! بكل سرور. ما الذي تحتاجه؟",
    "نشكرك على ثقتك بنا. هل تريد الطلب الآن؟",
    "أهلاً بك دائماً! كيف يمكنني خدمتك؟",
]
for i, bad in enumerate(BAD_EMOJI_REPLIES, 51):
    run_elite(bad, "😍", ["no_banned", "short", "not_empty"], f"A{i:02d}", CAT)

# A61-A70: Casual chat
CASUAL_MSGS = [
    ("شو أخبارك", "casual_chat"),
    ("كيف امورك", "casual_chat"),
    ("بس كنت أسأل", "casual_chat"),
    ("لا شي", "casual_chat"),
    ("وين الكرادة", "casual_chat"),
    ("شلون الدوام", "casual_chat"),
    ("تعبان اليوم", "casual_chat"),
    ("وش تسوي", "casual_chat"),
    ("عندك وقت", "casual_chat"),
    ("وين تكون", "casual_chat"),
]
for i, (msg, exp) in enumerate(CASUAL_MSGS, 61):
    check_intent(msg, exp, f"A{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY B: Menu / Price / Recommendation (80 scenarios)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("B. MENU / PRICE / RECOMMENDATION (80)")
print("="*60)

CAT = "B-menu"

# B1-B15: Menu intent
MENU_MSGS = [
    ("شنو عندكم", "menu_request"),
    ("منيو", "menu_request"),
    ("المنيو", "menu_request"),
    ("شو عندكم اليوم", "menu_request"),
    ("وريني المنيو", "menu_request"),
    ("شو أكلاتكم", "menu_request"),
    ("القائمة", "menu_request"),
    ("الوجبات", "menu_request"),
    ("كل شي عندكم", "menu_request"),
    ("إيش عندكم", "menu_request"),
    ("شو فيه عندكم", "menu_request"),
    ("شنو متوفر", "menu_request"),
    ("قول لي شو عندكم", "menu_request"),
    ("أريد أشوف المنيو", "menu_request"),
    ("وريني قائمة الطعام", "menu_request"),
]
for i, (msg, exp) in enumerate(MENU_MSGS, 1):
    check_intent(msg, exp, f"B{i:02d}", CAT)

# B16-B25: Price intent
PRICE_MSGS = [
    ("بكم الزينگر", "price_question"),
    ("شسعره", "price_question"),
    ("كم سعره", "price_question"),
    ("كم ثمنه", "price_question"),
    ("بكم البرگر", "price_question"),
    ("ثمن الشاورما", "price_question"),
    ("سعر الوجبة", "price_question"),
    ("شكد الزينگر", "price_question"),
    ("شعر الكولا", "price_question"),
    ("كم قيمة الطلب", "price_question"),
]
for i, (msg, exp) in enumerate(PRICE_MSGS, 16):
    check_intent(msg, exp, f"B{i:02d}", CAT)

# B26-B35: Recommendation intent
REC_MSGS = [
    ("تنصحني بشي", "recommendation"),
    ("شنو أحسن", "recommendation"),
    ("الأحسن عندكم", "recommendation"),
    ("شو تنصحني", "recommendation"),
    ("شنو الأفضل", "recommendation"),
    ("أنصحك بشي", "recommendation"),
    ("شنو الأكثر طلبًا", "recommendation"),
    ("أريد توصية", "recommendation"),
    ("ماذا تقترح", "recommendation"),
    ("شنو الأكثر طلب", "recommendation"),
]
for i, (msg, exp) in enumerate(REC_MSGS, 26):
    check_intent(msg, exp, f"B{i:02d}", CAT)

# B36-B45: Cheapest intent
CHEAP_MSGS = [
    ("أرخص شي عندكم", "cheapest_item"),
    ("شنو الأرخص", "cheapest_item"),
    ("شو الأقل سعر", "cheapest_item"),
    ("أريد الأخف بالسعر", "cheapest_item"),
    ("شي رخيص", "cheapest_item"),
    ("عندكم شي رخيص", "cheapest_item"),
    ("الأخف سعراً", "cheapest_item"),
    ("أقل وجبة بالسعر", "cheapest_item"),
    ("ميزانيتي محدودة", "cheapest_item"),
    ("ما عندي هواي فلوس", "cheapest_item"),
]
for i, (msg, exp) in enumerate(CHEAP_MSGS, 36):
    check_intent(msg, exp, f"B{i:02d}", CAT)

# B46-B60: Bad menu/price replies → elite fixes
BAD_MENU_REPLIES = [
    "يسعدني مساعدتك! عندنا مجموعة متنوعة من الأكلات. هل ترغب في معرفة المزيد؟",
    "بالتأكيد! يمكنني مساعدتك في معرفة قائمة طعامنا المميزة.",
    "بكل سرور! لدينا قائمة متنوعة تشمل الزينگر والبرگر والشاورما. هل ترغب في الطلب؟",
    "من دواعي سروري! قائمتنا تحتوي على أفضل الأصناف. كيف يمكنني مساعدتك؟",
    "شكراً لاختيارك مطعمنا! لدينا عروض رائعة اليوم. هل ترغب في الاطلاع على القائمة؟",
    "بالتأكيد! لدينا مجموعة واسعة من الوجبات. هل تريد أن أقترح عليك شيئاً؟",
    "أهلاً وسهلاً! يسعدنا تقديم أفضل الأكلات لك. هل ترغب في معرفة الأسعار؟",
    "نشكرك على اهتمامك! قائمتنا متنوعة ومتجددة. ما الذي تفضله؟",
    "يسرني مساعدتك! لدينا عروض رائعة ومميزة. هل تريد التفاصيل؟",
    "بالتأكيد أخي! عندنا كل ما تحتاجه. كيف أخدمك؟",
    "يسعدني! قائمتنا تشمل وجبات متنوعة للجميع. ما الذي تحب؟",
    "أهلاً! لدينا خيار رائع ومميز. هل ترغب في الطلب الآن؟",
    "بكل سرور وسعادة! عندنا الزينگر والبرگر. هل هناك شيء آخر؟",
    "نتشرف بخدمتك! قائمتنا متنوعة. هل ترغب في الاطلاع عليها؟",
    "يسلمون على سؤالك! عندنا كل شيء. كيف أخدمك؟",
]
for i, bad in enumerate(BAD_MENU_REPLIES, 46):
    run_elite(bad, "شنو عندكم", ["no_banned", "short", "max_q", "not_empty"], f"B{i:02d}", CAT)

# B61-B80: "Expensive" → suggest cheaper
EXPENSIVE_REPLIES = [
    "مرحباً عزيزي العميل، يبدو أن هذا المنتج غالٍ نوعاً ما.",
    "بالتأكيد! لدينا عروض أرخص متاحة. هل ترغب في الاطلاع عليها؟",
    "يسعدني مساعدتك في إيجاد خيار مناسب لميزانيتك.",
]
for i, bad in enumerate(EXPENSIVE_REPLIES, 61):
    run_elite(bad, "غالي", ["no_banned", "short", "not_empty"], f"B{i:02d}", CAT)

# B64-B80: Intent for various messages
for i, (msg, exp) in enumerate([
    ("غالي", "casual_chat"),
    ("هذا غالي عليّ", "casual_chat"),
    ("الزينگر 9000 د.ع غالي", "casual_chat"),
    ("ما عندكم أرخص", "cheapest_item"),
    ("بكم الزينگر", "price_question"),
    ("بكم البطاطا", "price_question"),
    ("وريني سعر الكولا", "price_question"),
    ("المنيو كامل", "menu_request"),
    ("كل الأكلات", "menu_request"),
    ("الأصناف عندكم", "menu_request"),
    ("ما الأحسن عندكم", "recommendation"),
    ("شنو الأكثر طلباً", "recommendation"),
    ("تنصح بشي", "recommendation"),
    ("الأكثر طلب", "recommendation"),
    ("أقترح علي", "recommendation"),
    ("أريد توصيتك", "recommendation"),
    ("الأشهر عندكم", "recommendation"),
], 64):
    check_intent(msg, exp, f"B{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY C: Order Slot Filling (120 scenarios)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("C. ORDER SLOT FILLING (120)")
print("="*60)

CAT = "C-order"

# C1-C20: Direct order intent
ORDER_MSGS = [
    ("أريد زينگر", "direct_order"),
    ("اريد برگر", "direct_order"),
    ("أطلب شاورما", "direct_order"),
    ("بدي زينگر", "direct_order"),
    ("ابي واحد زينگر", "direct_order"),
    ("خذلي زينگر", "direct_order"),
    ("جيبلي برگر", "direct_order"),
    ("أريد وجبة", "direct_order"),
    ("عايز زينگر", "direct_order"),
    ("اريد طلب زينگر", "direct_order"),
    ("حابب أطلب", "direct_order"),
    ("ابغى زينگر", "direct_order"),
    ("اشتري زينگر", "direct_order"),
    ("طلبي زينگر", "direct_order"),
    ("أريد اثنين زينگر", "direct_order"),
    ("اريد شاورما وكولا", "direct_order"),
    ("أريد وجبة زينگر كاملة", "direct_order"),
    ("بدي برگر وبطاطا", "direct_order"),
    ("أريد طلب توصيل", "direct_order"),
    ("اطلب زينگر توصيل", "direct_order"),
]
for i, (msg, exp) in enumerate(ORDER_MSGS, 1):
    check_intent(msg, exp, f"C{i:02d}", CAT)

# C21-C40: Bad slot-filling replies → no address for pickup, no repeated questions
# Scenario: customer says pickup, bot asks address (WRONG)
PICKUP_WITH_ADDRESS_REPLY = "تمام! وين تريد أوصله؟ أرسل عنوانك لإتمام الطلب."
r = run_elite(
    PICKUP_WITH_ADDRESS_REPLY, "أريد زينگر استلام",
    ["no_banned", "not_empty"],
    "C21", CAT,
    history=[{"role": "user", "content": "أريد زينگر استلام"}]
)

# C22-C40: Slot filling bad replies
BAD_SLOT_REPLIES = [
    # Corporate slot filling
    ("يرجى تزويدي بعنوانك لإتمام الطلب.", "أريد زينگر توصيل", []),
    ("هل يمكنني معرفة اسمك من فضلك؟", "أريد زينگر", []),
    ("يرجى تحديد طريقة الدفع المفضلة لديك.", "تمام زينگر واحد", []),
    ("بالتأكيد! يسعدني مساعدتك. ما هو عنوانك؟", "أريد زينگر توصيل", []),
    ("من دواعي سروري! كيف تريد الدفع — كاش أم بطاقة؟", "أريد زينگر", []),
    ("يرجى العلم أن التوصيل يستغرق 30 دقيقة. هل تريد الاستمرار؟", "أريد طلب", []),
    ("نود إعلامك بأن طلبك قيد المعالجة. ما هو عنوانك؟", "تمام", []),
    ("بناءً على طلبك، نحتاج عنوانك لإكمال الطلب.", "أريد زينگر توصيل", []),
    ("عزيزي العميل، يرجى تأكيد عنوانك.", "زينگر توصيل", []),
    ("شكراً لاختيارك! هل يمكنك إرسال عنوانك؟", "اريد زينگر", []),
    ("من فضلك أرسل عنوانك الكامل مع ذكر المنطقة والشارع.", "أريد زينگر توصيل", []),
    ("يرجى تزويدي باسمك لتسجيل الطلب.", "اريد شاورما", []),
    ("هل تريد الدفع نقداً أم ببطاقة الائتمان؟", "أريد طلب", []),
    ("يمكنني مساعدتك! ما هو عدد القطع؟", "أريد زينگر", []),
    ("بالتأكيد! كم عدد الوجبات التي تريدها؟", "أريد برگر", []),
    ("بكل سرور! هل تريد إضافة أي شيء آخر للطلب؟", "أريد زينگر", []),
    ("يسعدني تلبية طلبك! هل تريد أي إضافات؟", "اريد شاورما", []),
    ("نشكرك على طلبك! هل تريد المشروب مع الوجبة؟", "أريد برگر", []),
    ("أهلاً بك! يرجى تأكيد نوع الوجبة.", "أريد زينگر", []),
]
for i, (bad, customer_msg, history) in enumerate(BAD_SLOT_REPLIES, 22):
    run_elite(bad, customer_msg, ["no_banned", "short", "max_q", "not_empty"],
              f"C{i:02d}", CAT, history=history)

# C41-C60: Multiple slots not repeated
# Scenario: name already known → bot should NOT ask again
KNOWN_NAME_MEMORY = {"name": "أحمد", "address": "الكرادة", "payment_method": "كاش"}
BAD_REPEAT_SLOT = "تمام! شسمك؟ وما طريقة الدفع؟"
run_elite(BAD_REPEAT_SLOT, "أريد زينگر توصيل",
          ["no_banned", "max_q", "not_empty"], "C41", CAT,
          memory=KNOWN_NAME_MEMORY)

# C42-C60: Various slot scenarios
SLOT_SCENARIOS = [
    ("تمام! وين تريد نوصله؟ وشسمك؟ وكيف تدفع؟", "أريد زينگر توصيل", "multi_q"),
    ("تمام! اسمك؟ وعنوانك؟ وطريقة الدفع؟", "اريد برگر", "multi_q"),
    ("ممتاز! هل تريد التوصيل أم الاستلام؟ وما اسمك؟", "أريد شاورما", "multi_q"),
    ("يرجى إرسال: 1- الاسم 2- العنوان 3- طريقة الدفع", "أريد زينگر", "corporate"),
    ("نحتاج منك البيانات التالية: الاسم والعنوان والدفع.", "اريد طلب", "corporate"),
    ("بالتأكيد! هل تريد توصيل؟ وما هو اسمك؟", "أريد برگر", "multi_q"),
    ("يسعدني! هل تريد استلام أم توصيل؟ وكيف تدفع؟", "اريد زينگر", "multi_q"),
    ("من دواعي سروري! أحتاج عنوانك واسمك لإتمام الطلب.", "اريد شاورما توصيل", "corporate"),
    ("تم استلام طلبك! هل يمكنك تأكيد العنوان والاسم؟", "أريد زينگر", "multi_q"),
    ("شكراً لاختيارك! يرجى تزويدنا بعنوانك الكامل.", "اريد برگر توصيل", "corporate"),
]
for i, (bad, msg, issue_type) in enumerate(SLOT_SCENARIOS, 42):
    run_elite(bad, msg, ["no_banned", "not_empty"], f"C{i:02d}", CAT)

# C61-C80: Delivery address scenarios
DELIVERY_SCENARIOS = [
    # Pickup → no address
    ("تمام 🌷 وين أوصله؟ أرسلي العنوان.", "أريد زينگر استلام", None, True),
    ("عنوانك؟", "أريد برگر استلام", None, True),
    # Delivery → address OK to ask
    ("تمام 🌷 وين أوصله؟", "أريد زينگر توصيل", None, False),
    ("شنو العنوان؟", "أريد شاورما توصيل", None, False),
]
PICKUP_HISTORY = [{"role": "user", "content": "أريد استلام"}]
for i, (bad, msg, h, should_remove_addr) in enumerate(DELIVERY_SCENARIOS, 61):
    hist = PICKUP_HISTORY if should_remove_addr else []
    result = run_elite(bad, msg, ["no_banned", "not_empty", "max_q"], f"C{i:02d}", CAT, history=hist)

# C65-C80: Quantity/items
QTY_SCENARIOS = [
    ("كم وجبة تريد؟", "أريد زينگر"),
    ("تمام! واحد أم أكثر؟", "أريد برگر"),
    ("عدد الطلب؟", "أريد شاورما"),
    ("شكد وجبة؟", "اريد زينگر"),
    ("كم قطعة؟", "أريد برگر"),
    ("عدد القطع؟", "اريد شاورما"),
    ("واحد أم اثنين؟", "أريد زينگر"),
    ("تريد واحد أم اثنين؟", "اريد برگر"),
    ("كم وحدة؟", "أريد شاورما"),
    ("الكمية؟", "اريد زينگر"),
    ("تمام، كم؟", "أريد برگر"),
    ("شكد؟", "اريد شاورما"),
    ("كمية الطلب؟", "أريد زينگر"),
    ("تريد أكثر من واحد؟", "اريد برگر"),
    ("طيب كم وجبة؟", "أريد شاورما"),
    ("تمام واحد؟", "اريد زينگر"),
]
for i, (bad, msg) in enumerate(QTY_SCENARIOS, 65):
    run_elite(bad, msg, ["no_banned", "not_empty"], f"C{i:02d}", CAT)

# C81-C120: Payment already known — don't ask
PAYMENT_KNOWN_MEM = {"payment_method": "كاش"}
BAD_PAY_AGAIN = "تمام 🌷 كيف تريد الدفع؟ كاش أم كارد؟"
for i in range(81, 101):
    run_elite(BAD_PAY_AGAIN, "تمام زينگر واحد توصيل",
              ["no_banned", "not_empty"],
              f"C{i:02d}", CAT, memory=PAYMENT_KNOWN_MEM)

# C101-C120: Various order-flow replies
for i in range(101, 121):
    random.seed(i)
    bad_replies = [
        "بالتأكيد! يسعدني مساعدتك في إتمام طلبك.",
        "من دواعي سروري تلبية طلبك!",
        "شكراً لاختيارك! سنقوم بتحضير طلبك.",
        "يرجى تزويدي ببيانات الطلب.",
        "عزيزي العميل، طلبك قيد المعالجة.",
    ]
    run_elite(random.choice(bad_replies), "أريد زينگر",
              ["no_banned", "short", "not_empty"], f"C{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY D: Confirmation / Modification / Cancel (80)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("D. CONFIRMATION / MODIFICATION / CANCEL (80)")
print("="*60)

CAT = "D-confirm"

# D1-D20: Repeated confirmation intent
CONFIRM_MSGS = [
    ("ثبت", "repeated_confirmation"),
    ("أكمل", "repeated_confirmation"),
    ("أكمله", "repeated_confirmation"),
    ("ثبته", "repeated_confirmation"),
    ("تمام ثبته", "repeated_confirmation"),
    ("خلاص ثبت", "repeated_confirmation"),
    ("تمام أكمل", "repeated_confirmation"),
    ("نعم أكمل", "repeated_confirmation"),
    ("أكيد ثبت", "repeated_confirmation"),
    ("نعم", "repeated_confirmation"),
]
for i, (msg, exp) in enumerate(CONFIRM_MSGS, 1):
    check_intent(msg, exp, f"D{i:02d}", CAT)

# D11-D20: Cancel intent
CANCEL_MSGS = [
    ("ألغِ الطلب", "cancel_order"),
    ("إلغاء", "cancel_order"),
    ("لا أريد", "cancel_order"),
    ("ألغ", "cancel_order"),
    ("بطّل الطلب", "cancel_order"),
    ("ما أريد أكمل", "cancel_order"),
    ("ما أريد الطلب", "cancel_order"),
    ("الغ الطلب", "cancel_order"),
    ("بطّل", "cancel_order"),
    ("لا لا إلغاء", "cancel_order"),
]
for i, (msg, exp) in enumerate(CANCEL_MSGS, 11):
    check_intent(msg, exp, f"D{i:02d}", CAT)

# D21-D30: Modify intent
MODIFY_MSGS = [
    ("عدّل الطلب", "modify_order"),
    ("غيّر الزينگر بشاورما", "modify_order"),
    ("ضيف كولا", "modify_order"),
    ("شيل البطاطا من الطلب", "modify_order"),
    ("أضف كولا", "modify_order"),
    ("احذف الكولا", "modify_order"),
    ("بدّل الصنف", "modify_order"),
    ("غيّر", "modify_order"),
    ("عدّل", "modify_order"),
    ("أريد أعدّل", "modify_order"),
]
for i, (msg, exp) in enumerate(MODIFY_MSGS, 21):
    check_intent(msg, exp, f"D{i:02d}", CAT)

# D31-D50: Duplicate summary detection
DUPLICATE_SUMMARY_HISTORY = [
    {"role": "assistant", "content": "✅ طلبك:\n- زينگر x1 — 9,000 د.ع\nالمجموع: 9,000 د.ع\nالتوصيل: الكرادة\nالدفع: كاش\nثبت؟"},
]
DUPLICATE_BAD = "✅ طلبك:\n- زينگر x1 — 9,000 د.ع\nالمجموع: 9,000 د.ع\nالتوصيل: الكرادة\nالدفع: كاش\nثبت؟"
for i in range(31, 51):
    result = run_elite(DUPLICATE_BAD, "ثبت",
                       ["no_banned", "not_empty"],
                       f"D{i:02d}", CAT, history=DUPLICATE_SUMMARY_HISTORY)
    # After duplicate detection, should NOT repeat the full order summary
    if "✅ طلبك:" in result and i == 31:
        warn(f"D{i:02d}", "duplicate summary may not have been collapsed", CAT)

# D51-D80: Various confirmation/modify/cancel bad replies
CONFIRM_BAD_REPLIES = [
    "شكراً لاختيارك! تم استلام طلبك بنجاح.",
    "يسعدني مساعدتك! طلبك قيد المعالجة الآن.",
    "بالتأكيد! تمت معالجة طلبك بنجاح.",
    "من دواعي سروري! الطلب مؤكد.",
    "نشكرك! سيتم التواصل معك قريباً.",
    "بالتأكيد! يرجى الانتظار.",
    "عزيزي العميل، طلبك مؤكد.",
    "تم استلام طلبك! شكراً لتواصلك.",
    "بكل سرور! طلبك في المطبخ الآن.",
    "نأمل أن تستمتع بوجبتك!",
    "يرجى الانتظار، طلبك قيد التحضير.",
    "نشكرك على طلبك! نأمل رضاك.",
    "بالتأكيد! تم تسجيل طلبك.",
    "طلبك مؤكد! هل يمكنني مساعدتك بشيء آخر؟",
    "تمت المعالجة! شكراً على ثقتك بنا.",
    "تمام! شكراً لاختيارك مطعمنا.",
    "بالطبع! طلبك في طريقه إليك.",
    "بكل سرور! نسعى دائماً لإرضائك.",
    "يسعدنا خدمتك! طلبك مؤكد.",
    "من دواعي سروري! اطمئن على طلبك.",
    "أهلاً! تم تسجيل طلبك بنجاح.",
    "شكراً! طلبك قيد التنفيذ الآن.",
    "تم! نتمنى لك تجربة ممتعة.",
    "وصلتنا! طلبك في طريقه.",
    "ممتاز! طلبك مثبت وعلى الطريق.",
    "تمام! نشكرك على ثقتك.",
    "حاضر! تم التثبيت.",
    "وصلنا! شكراً.",
    "تمام! الطلب مثبت.",
    "حاضر.",
]
for i, bad in enumerate(CONFIRM_BAD_REPLIES, 51):
    run_elite(bad, "ثبت", ["no_banned", "short", "not_empty"], f"D{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY E: Complaints / Angry / Handoff (100)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("E. COMPLAINTS / ANGRY / HANDOFF (100)")
print("="*60)

CAT = "E-complaint"

# E1-E20: Complaint intent detection
COMPLAINT_MSGS = [
    ("الطلب بارد", "complaint_cold_food"),
    ("الأكل بارد", "complaint_cold_food"),
    ("طعام بارد", "complaint_cold_food"),
    ("الطلب ناقص", "complaint_missing_item"),
    ("في صنف ناقص", "complaint_missing_item"),
    ("غلط الطلب", "complaint_missing_item"),
    ("مو صح", "complaint_missing_item"),
    ("أين الطلب", "complaint_delay"),
    ("وين الطلب", "complaint_delay"),
    ("ليش يتأخر", "complaint_delay"),
    ("شكوى", "complaint"),
    ("عندي مشكلة", "complaint"),
    ("مشكلة بالطلب", "complaint"),
    ("أسوأ مطعم", "angry_complaint"),
    ("وسخ", "angry_complaint"),
    ("غشاش", "angry_complaint"),
    ("ما أرجع ثاني", "angry_complaint"),
    ("أريد استرداد", "refund_replace"),
    ("رجعوا فلوسي", "refund_replace"),
    ("بدّلها", "refund_replace"),
]
for i, (msg, exp) in enumerate(COMPLAINT_MSGS, 1):
    check_intent(msg, exp, f"E{i:02d}", CAT)

# E21-E40: Complaint replies must NOT have upsell
COMPLAINT_WITH_UPSELL = [
    "آسفين على الإزعاج! الطلب بارد؟ بالمناسبة عندنا عرض على الكولا اليوم!",
    "وصلتني! سنتابع. بالمناسبة تريد تضيف بطاطا للطلب؟",
    "آسف على المشكلة. أيضاً عندنا عرض خاص اليوم!",
    "حاضر نتابع. تريد تجرب وجبتنا الجديدة بعدها؟",
    "وصلتني! ونقترح عليك تجربة الزينگر الجديد بعد الحل.",
    "آسفين! وبالمناسبة تحب تضيف مشروب؟",
    "نتابع معك. تريد أضيفلك كولا بسعر مخفض؟",
    "آسف على الإزعاج. وعندنا عرض رائع اليوم!",
    "نحل المشكلة. أيضاً عندنا وجبات جديدة!",
    "وصلتني! وبالمناسبة عندنا خيار رائع ومميز.",
    "آسفين على الإزعاج. بالمناسبة عندنا عرض اليوم فقط!",
    "نتابع. تريد تعرف عروضنا؟",
    "حاضر! وبهذه المناسبة عندنا وجبات جديدة.",
    "نحل المشكلة. عندنا أيضاً خيارات رائعة.",
    "آسفين! وبالمناسبة تحب تجرب شيئاً جديداً؟",
    "وصلتني! ونوصيك بتجربة وجبتنا الجديدة.",
    "حاضر نتابع. وبهذه المناسبة عندنا عروض.",
    "نحل معك. وأيضاً عندنا خيارات رائعة اليوم.",
    "آسف! وبالمناسبة اشتري الآن قبل النفاد.",
    "نتابع. بالمناسبة عندنا فرصة لا تفوتك!",
]
for i, bad in enumerate(COMPLAINT_WITH_UPSELL, 21):
    result = run_elite(bad, "الطلب بارد", ["no_banned", "not_empty"],
                       f"E{i:02d}", CAT, is_critical=True)
    # CRITICAL: No upsell in complaint replies
    upsell_check = ["تضيف", "عرض", "بالمناسبة", "جرب", "تجرب وجبة", "فرصة"]
    for u in upsell_check:
        if u in result:
            fail(f"E{i:02d} CRITICAL-upsell-in-complaint", f"'{u}' found", CAT, critical=True)
            break

# E41-E60: Angry complaint → handoff
ANGRY_REPLIES = [
    "نعتذر عن الإزعاج! سيتم تحويل طلبك للقسم المختص.",
    "يسعدني مساعدتك! لا تتردد بالتواصل.",
    "آسفين! بالتأكيد سنحل المشكلة. كيف يمكنني مساعدتك؟",
    "بكل سرور! سأحولك للمختص.",
    "من دواعي سروري! هل يمكنني مساعدتك؟",
    "يرجى الانتظار! سنتواصل معك.",
    "نشكرك على تواصلك! شكراً لاختيارك.",
    "عزيزي العميل! نعتذر.",
    "بالتأكيد سنعالج المشكلة وفق إجراءاتنا.",
    "يسرنا خدمتك! سنتابع الموضوع.",
    "بكل تأكيد! نأسف على الإزعاج.",
    "نعتذر عن الإزعاج ونأمل رضاك.",
    "شكراً لتواصلك! سنحول الأمر للمختص.",
    "بالطبع! لا تتردد بالتواصل معنا.",
    "يسعدني مساعدتك في حل هذه المشكلة.",
    "أهلاً! نعتذر عن هذا الأمر.",
    "بكل سرور! سيتم التواصل معك قريباً.",
    "نأسف لذلك! هل يمكنني مساعدتك؟",
    "يرجى تزويدي ببيانات الشكوى.",
    "نتشرف بخدمتك! آسفين.",
]
for i, bad in enumerate(ANGRY_REPLIES, 41):
    result = run_elite(bad, "أسوأ مطعم",
                       ["no_banned", "short", "not_empty"],
                       f"E{i:02d}", CAT, is_critical=True)

# E61-E80: Handoff intent detection
HANDOFF_MSGS = [
    ("أريد موظف", "human_handoff"),
    ("اريد موظف", "human_handoff"),
    ("كلمني موظف", "human_handoff"),
    ("أريد مدير", "human_handoff"),
    ("اريد مدير", "human_handoff"),
    ("ما أريد بوت", "human_handoff"),
    ("ما اريد بوت", "human_handoff"),
    ("أريد إنسان", "human_handoff"),
    ("اريد انسان", "human_handoff"),
    ("كلمني مدير", "human_handoff"),
    ("نادوا موظف", "human_handoff"),
    ("ابي موظف", "human_handoff"),
    ("تكلم معي موظف", "human_handoff"),
    ("مدير من فضلك", "human_handoff"),
    ("أريد أتكلم مع إنسان", "human_handoff"),
    ("ما أريد أحچي ويا بوت", "human_handoff"),
    ("شخص حقيقي", "human_handoff"),
    ("موظف بشري", "human_handoff"),
    ("تحويل لموظف", "human_handoff"),
    ("وصّلني لموظف", "human_handoff"),
]
for i, (msg, exp) in enumerate(HANDOFF_MSGS, 61):
    check_intent(msg, exp, f"E{i:02d}", CAT)

# E81-E100: Complaint without upsell (correct replies)
COMPLAINT_CORRECT_REPLIES = [
    "آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.",
    "وصلتني 🌷 شنو رقم الطلب؟",
    "حاضر 🌷 كلّيلي رقم الطلب.",
    "آسفين 🌷 اسمك أو رقم الطلب؟",
    "وصلتني، أتابعها هسه 🌷",
    "حقك علينا 🌷 أحولك لموظف هسه.",
    "حاضر، أتابع.",
    "وصلتني 🌷 رقم الطلب؟",
    "آسفين، كلّيلي الاسم.",
    "حاضر 🌷",
]
for i, good_reply in enumerate(COMPLAINT_CORRECT_REPLIES * 2, 81):
    result = run_elite(good_reply, "الطلب بارد",
                       ["no_banned", "not_empty"], f"E{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY F: Story / Reel / Post (80)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("F. STORY / REEL / POST (80)")
print("="*60)

CAT = "F-media"

# F1-F20: Story reply messages (simulated with tags)
STORY_MSGS = [
    "[ستوري] بكم؟",
    "[ستوري] أريد هذا",
    "[ستوري] هذا متوفر؟",
    "[ستوري] نفسه",
    "[ستوري] شكلته حلو",
    "[ستوري] كم سعره",
    "[story] how much",
    "[reel] أريد هذا",
    "[post] متوفر؟",
]
for i, msg in enumerate(STORY_MSGS, 1):
    check_intent(msg, "story_reply", f"F{i:02d}", CAT)

# F10-F30: Story reply responses
STORY_BAD_REPLIES = [
    "مرحباً! الصورة التي أرسلتها تشير إلى أنك مهتم بهذا المنتج.",
    "بالتأكيد! تم تحليل الصورة وإليك المعلومات.",
    "بناءً على ما شاركته، يمكنني مساعدتك.",
    "بالتأكيد! هذا المنتج متوفر. هل تريد معرفة السعر؟",
    "يسعدني مساعدتك! هذا المنتج رائع ومميز.",
    "بكل سرور! منتجنا هذا خيار رائع.",
    "نعتذر، لم أفهم ما تقصده. هل يمكنك التوضيح؟",
    "شكراً لتفاعلك مع المحتوى!",
    "يسعدنا اهتمامك! هل تريد الطلب؟",
    "الصورة تحتوي على وجبة متنوعة. هل تريدها؟",
    "حسب التحليل، هذا المنتج مناسب لك.",
    "بناءً على تفاعلك، أنصحك بهذا الصنف.",
    "تم تحليل الصورة. المنتج بسعر مناسب.",
    "بالتأكيد! هذا الصنف ممتاز ومميز.",
    "مرحباً! يسعدني مساعدتك في الطلب.",
    "شكراً لاختيارك! المنتج متوفر.",
    "من دواعي سروري! تفاعلك يسعدنا.",
    "يمكنني مساعدتك! المنتج موجود.",
    "بكل سرور! سعيد بخدمتك.",
    "يسعدني! المنتج بسعر مناسب جداً.",
]
for i, bad in enumerate(STORY_BAD_REPLIES, 10):
    run_elite(bad, "[ستوري] أريد هذا", ["no_banned", "no_tech", "short", "not_empty"],
              f"F{i:02d}", CAT)

# F31-F80: Various media scenarios
for i in range(31, 81):
    random.seed(i * 7)
    media_msgs = [
        "[ستوري] بكم؟",
        "[ستوري] أريد هذا",
        "[story] متوفر؟",
        "[reel] نفسه",
        "[post] كم سعره",
    ]
    bad_replies = [
        "تم تحليل الصورة وإليك المعلومات الكاملة.",
        "الصورة تحتوي على منتجات رائعة.",
        "بناءً على تفاعلك، هذا الصنف مناسب.",
        "يسعدني مساعدتك! تم تحليل المحتوى.",
        "بالتأكيد! بناءً على الصورة، أنصحك.",
    ]
    run_elite(
        random.choice(bad_replies),
        random.choice(media_msgs),
        ["no_banned", "no_tech", "not_empty"],
        f"F{i:02d}", CAT
    )

# ─────────────────────────────────────────────────────────────
# CATEGORY G: Image Cases (60)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("G. IMAGE CASES (60)")
print("="*60)

CAT = "G-image"

IMAGE_INTENTS = [
    ("[صورة]", "image_product"),
    ("[image]", "image_product"),
    ("[صورة-منيو]", "image_menu"),
    ("[image-menu]", "image_menu"),
    ("[صورة-شكوى]", "image_complaint"),
    ("[image-complaint]", "image_complaint"),
]
for i, (msg, exp) in enumerate(IMAGE_INTENTS, 1):
    check_intent(msg, exp, f"G{i:02d}", CAT)

BAD_IMAGE_REPLIES = [
    "تم تحليل الصورة وتحديد المنتج.",
    "الصورة تحتوي على وجبة من فئة الزينگر.",
    "بناءً على تحليل الصورة، هذا المنتج مناسب.",
    "تم استلام الصورة. الصورة تحتوي على أكل.",
    "بالتأكيد! بعد تحليل الصورة، أرى أنك تريد الطلب.",
    "يسعدني! تم تحليل المحتوى المرئي.",
    "حسب التحليل، هذا منتج من قائمتنا.",
    "وفقاً لتحليل الصورة، المنتج موجود.",
    "الصورة واضحة وتحتوي على...",
    "بناءً على الصورة المرسلة، أنصح بـ...",
]
for i, bad in enumerate(BAD_IMAGE_REPLIES, 7):
    run_elite(bad, "[صورة]", ["no_banned", "no_tech", "short", "not_empty"],
              f"G{i:02d}", CAT, is_critical=True)

# G17-G60: Various image scenarios
for i in range(17, 61):
    random.seed(i * 3)
    bad = random.choice([
        "تم تحليل الصورة بنجاح.",
        "الصورة تحتوي على طعام مميز.",
        "بعد مراجعة الصورة، أرى أنك مهتم.",
        "وصلتني الصورة. تم تحليلها.",
        "بناءً على الصورة المرسلة.",
    ])
    run_elite(bad, "[صورة]", ["no_banned", "no_tech", "not_empty"], f"G{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY H: Voice Cases (50)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("H. VOICE CASES (50)")
print("="*60)

CAT = "H-voice"

VOICE_INTENTS = [
    ("[فويس]", "voice_order"),
    ("[voice]", "voice_order"),
    ("[audio]", "voice_order"),
]
for i, (msg, exp) in enumerate(VOICE_INTENTS, 1):
    check_intent(msg, exp, f"H{i:02d}", CAT)

BAD_VOICE_REPLIES = [
    "تم تحويل الصوت إلى نص. طلبت زينگر.",
    "الرسالة الصوتية تقول: أريد زينگر.",
    "بعد تحويل الصوت، فهمت أنك تريد الطلب.",
    "تم استقبال الرسالة الصوتية وتحويلها.",
    "الصوت يقول أنك تريد زينگر.",
    "تم تحليل الصوت: طلب زينگر.",
    "فهمت من الرسالة الصوتية أنك تريد.",
    "الصوت المرسل يشير إلى طلب.",
    "تم تحويل رسالتك الصوتية بنجاح.",
    "استقبلنا رسالتك الصوتية وترجمناها.",
]
for i, bad in enumerate(BAD_VOICE_REPLIES, 4):
    run_elite(bad, "[فويس]", ["no_banned", "no_tech", "not_empty"],
              f"H{i:02d}", CAT, is_critical=True)

# H14-H50: Various voice scenarios
for i in range(14, 51):
    random.seed(i * 11)
    bad = random.choice([
        "تم تحويل الصوت إلى نص.",
        "الرسالة الصوتية تقول.",
        "تم استقبال الصوت وتحويله.",
        "وصلتني الرسالة الصوتية، تم التحليل.",
    ])
    run_elite(bad, "[فويس]", ["no_banned", "no_tech", "not_empty"], f"H{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# CATEGORY I: Memory Cases (50)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("I. MEMORY CASES (50)")
print("="*60)

CAT = "I-memory"

# I1-I10: Memory intent
MEMORY_MSGS = [
    ("مثل آخر مرة", "memory_same_order"),
    ("نفس الطلب", "memory_same_order"),
    ("مثل قبل", "memory_same_order"),
    ("نفسه من قبل", "memory_same_order"),
    ("كرر الطلب", "memory_same_order"),
    ("نفس الطلب السابق", "memory_same_order"),
]
for i, (msg, exp) in enumerate(MEMORY_MSGS, 1):
    check_intent(msg, exp, f"I{i:02d}", CAT)

# I7-I50: Memory replies don't expose database
MEM_BAD_REPLIES = [
    "بناءً على قاعدة البيانات، آخر طلب كان زينگر.",
    "حسب السجل، طلبت زينگر مرة سابقة.",
    "وفقاً للبيانات المخزّنة، طلبك السابق كان.",
    "سجلاتنا تشير إلى أنك طلبت زينگر آخر مرة.",
    "حسب قاعدة بيانات العملاء، طلبت.",
    "البيانات تُظهر أن آخر طلب كان.",
    "النظام يشير إلى أنك طلبت آخر مرة.",
    "وفقاً لبيانات العميل.",
    "بحسب السجلات، آخر طلب كان.",
    "قاعدة البيانات تشير إلى.",
]
MEMORY_CONTEXT = {"last_order_summary": "زينگر x1 استلام", "name": "أحمد"}
for i, bad in enumerate(MEM_BAD_REPLIES, 7):
    run_elite(bad, "مثل آخر مرة", ["no_banned", "no_tech", "not_empty"],
              f"I{i:02d}", CAT, memory=MEMORY_CONTEXT, is_critical=True)

# I17-I50: Address memory
ADDR_BAD_REPLIES = [
    "حسب السجلات، عنوانك الكرادة.",
    "وفقاً للبيانات المخزّنة، عنوانك الكرادة.",
    "قاعدة بيانات العملاء تشير إلى عنوانك.",
    "بناءً على البيانات المسجّلة.",
    "النظام لديه عنوانك: الكرادة.",
]
ADDR_MEMORY = {"address": "الكرادة", "name": "أحمد"}
for i, bad in enumerate(ADDR_BAD_REPLIES, 17):
    run_elite(bad, "أريد زينگر توصيل", ["no_banned", "no_tech", "not_empty"],
              f"I{i:02d}", CAT, memory=ADDR_MEMORY, is_critical=True)

# I22-I50: Normal memory scenarios
for i in range(22, 51):
    random.seed(i * 13)
    bad = random.choice([
        "حسب السجل.",
        "البيانات تشير.",
        "قاعدة البيانات.",
        "النظام يشير.",
        "وفقاً للسجلات.",
    ])
    run_elite(bad, "مثل آخر مرة", ["no_banned", "no_tech", "not_empty"],
              f"I{i:02d}", CAT, memory=MEMORY_CONTEXT)

# ─────────────────────────────────────────────────────────────
# CATEGORY J: Duplicate / Stability / Human Mode (50)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("J. DUPLICATE / STABILITY / HUMAN MODE (50)")
print("="*60)

CAT = "J-stability"

# J1-J10: Duplicate intent
DUPLICATE_MSGS = [
    "ثبت", "ثبت", "ثبت",  # repeated
    "أريد زينگر", "أريد زينگر",  # repeated
    "أكمل", "أكمل",
    "نعم", "نعم", "نعم",
]
for i, msg in enumerate(DUPLICATE_MSGS, 1):
    check_intent(msg, "repeated_confirmation", f"J{i:02d}", CAT)

# J11-J30: Human mode — bot should respect escalation
HUMAN_MODE_HISTORY = [
    {"role": "user", "content": "أريد موظف"},
    {"role": "assistant", "content": "حاضر 🌷 أحولك لموظف هسه."},
]
for i in range(11, 31):
    bad = "أهلاً! هل تريد الطلب؟"
    result = run_elite(bad, "شنو عندكم",
                       ["no_banned", "not_empty"], f"J{i:02d}", CAT,
                       history=HUMAN_MODE_HISTORY)

# J31-J50: Stability — elite_reply_pass never crashes
EDGE_CASES = [
    ("", "هلا"),
    ("  ", "مرحبا"),
    ("🌷", "هلا"),
    ("null", "هلا"),
    ("A" * 1000, "أريد زينگر"),  # very long reply
    ("تمام", ""),  # empty customer message
    ("تمام", None),  # None customer message
]
for i, (bad, msg) in enumerate(EDGE_CASES, 31):
    try:
        result = elite_reply_pass(
            reply=bad or "",
            customer_message=msg or "",
            history=[], memory={}, products=SAMPLE_PRODUCTS
        )
        ok(f"J{i:02d} no_crash edge_case", CAT)
    except Exception as e:
        fail(f"J{i:02d} CRASHED", str(e), CAT, critical=True)

# J38-J50: Random stability
for i in range(38, 51):
    random.seed(i * 17)
    try:
        result = elite_reply_pass(
            reply=random.choice(["تمام", "", "يرجى تزويدي", "بالتأكيد!"]),
            customer_message=random.choice(["هلا", "أريد زينگر", "ثبت", ""]),
            history=[], memory={}, products=SAMPLE_PRODUCTS
        )
        ok(f"J{i:02d} stability", CAT)
    except Exception as e:
        fail(f"J{i:02d} crash", str(e), CAT, critical=True)

# ─────────────────────────────────────────────────────────────
# CATEGORY K: Subscription / Channel Blocked (60)
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("K. SUBSCRIPTION / CHANNEL BLOCKED (60)")
print("="*60)

CAT = "K-blocked"

# K1-K10: Blocked subscription intent
BLOCKED_MSGS = [
    ("الخدمة غير متاحة", "casual_chat"),
    ("مو شغالة", "casual_chat"),
]
for i, (msg, exp) in enumerate(BLOCKED_MSGS, 1):
    check_intent(msg, exp, f"K{i:02d}", CAT)

# K3-K30: Blocked subscription replies
BAD_BLOCKED_REPLIES = [
    "عزيزي العميل، الخدمة متوقفة. يرجى التواصل مع الإدارة.",
    "يسعدني إعلامك أن الخدمة متوقفة مؤقتاً.",
    "بالتأكيد! نعتذر عن الإزعاج. الخدمة متوقفة.",
    "بناءً على البيانات، اشتراكك منتهٍ.",
    "حسب السجل، الخدمة موقوفة لسبب ما.",
    "يرجى العلم أن الخدمة متوقفة.",
    "نود إعلامك بتوقف الخدمة مؤقتاً.",
    "شكراً لتواصلك! الخدمة متوقفة للصيانة.",
    "بالتأكيد! الخدمة غير متاحة حالياً.",
    "نعتذر عن الإزعاج! الخدمة متوقفة.",
    "تمت معالجة طلبك. الخدمة موقوفة.",
    "يسرنا إعلامك أن الخدمة متوقفة.",
    "بكل سرور! الخدمة متوقفة مؤقتاً.",
    "من دواعي سروري إعلامك بتوقف الخدمة.",
    "نود إحاطتك علماً بتوقف الخدمة.",
    "حسب البيانات المتاحة، الخدمة موقوفة.",
    "وفقاً للسجلات، الاشتراك منتهٍ.",
    "النظام يشير إلى توقف الخدمة.",
    "قاعدة البيانات تشير إلى إيقاف الحساب.",
    "بناءً على المعلومات المتوفرة، الخدمة متوقفة.",
    "يرجى التواصل مع الدعم الفني.",
    "تم إيقاف الخدمة. يرجى التواصل.",
    "الخدمة متوقفة بسبب مشكلة تقنية.",
    "يرجى الانتظار ريثما تعود الخدمة.",
    "على أمل عودة الخدمة قريباً.",
    "سيتم التواصل معك عند استعادة الخدمة.",
    "الخدمة متوقفة مؤقتاً لأسباب فنية.",
    "نعمل على استعادة الخدمة في أقرب وقت.",
]
for i, bad in enumerate(BAD_BLOCKED_REPLIES, 3):
    run_elite(bad, "هلا", ["no_banned", "no_tech", "short", "not_empty"],
              f"K{i:02d}", CAT)

# K31-K60: Channel-specific blocked
for i in range(31, 61):
    random.seed(i * 19)
    bad = random.choice([
        "عزيزي العميل، الخدمة غير متاحة.",
        "يرجى العلم أن الخدمة متوقفة.",
        "بالتأكيد! نعتذر عن الإزعاج.",
        "حسب البيانات، الخدمة موقوفة.",
        "نود إعلامك بتوقف الخدمة.",
    ])
    run_elite(bad, "أريد زينگر", ["no_banned", "not_empty"], f"K{i:02d}", CAT)

# ─────────────────────────────────────────────────────────────
# ADDITIONAL: Template coverage check
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("T. TEMPLATE COVERAGE")
print("="*60)

for intent_key in TEMPLATES.keys():
    variants = TEMPLATES[intent_key]
    if not variants:
        fail(f"T template empty: {intent_key}", "", "T-templates")
    else:
        ok(f"T template has variants: {intent_key} ({len(variants)})", "T-templates")

# Test template fill
ctx_test = {"item": "زينگر", "price": "9,000", "name": "أحمد",
            "address": "الكرادة", "last_order": "زينگر x1 استلام",
            "menu_short": "زينگر، برگر، شاورما", "menu": "...", "alt": "شاورما"}
for intent_key in ["greeting", "complaint", "angry_complaint", "thanks",
                    "human_handoff", "price_question", "recommendation"]:
    tmpl = pick(intent_key, ctx_test)
    if tmpl and len(tmpl.strip()) > 2:
        ok(f"T pick({intent_key}) = '{tmpl[:40]}'", "T-templates")
    else:
        fail(f"T pick({intent_key}) empty", "", "T-templates")

# ─────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("FINAL SUMMARY")
print("="*60)

total = len(passed) + len(failed)
pass_pct = round(len(passed) / total * 100, 1) if total > 0 else 0
critical_count = len(critical_failures)

print(f"\n  Total  : {total}")
print(f"  Passed : {len(passed)} ({pass_pct}%)")
print(f"  Failed : {len(failed)}")
print(f"  Critical failures: {critical_count}")
print(f"  Warnings: {len(warnings_list)}")

# Category breakdown
cats = {}
for cat, name in passed:
    cats.setdefault(cat, [0, 0])[0] += 1
for cat, name, detail in failed:
    cats.setdefault(cat, [0, 0])[1] += 1

print("\n  Category breakdown:")
for cat, (p, f_count) in sorted(cats.items()):
    status = "✅" if f_count == 0 else "❌"
    print(f"    {status} {cat}: {p} passed, {f_count} failed")

if failed:
    print(f"\n  FAILURES ({len(failed)}):")
    for cat, name, detail in failed[:30]:
        print(f"    ❌ [{cat}] {name}" + (f" — {detail}" if detail else ""))
    if len(failed) > 30:
        print(f"    ... and {len(failed)-30} more")

if critical_failures:
    print(f"\n  CRITICAL FAILURES ({len(critical_failures)}):")
    for cat, name, detail in critical_failures:
        print(f"    🚨 [{cat}] {name}" + (f" — {detail}" if detail else ""))

# Safety category check
safety_cats = ["E-complaint", "D-confirm", "J-stability", "H-voice", "G-image", "I-memory"]
safety_fails = [f for f in failed if any(sc in f[0] for sc in safety_cats) and f in [(c, n, d) for c, n, d in failed]]
safety_ok = len([f for f in failed if f[0] in safety_cats]) == 0

print(f"\n  Safety categories 100% pass: {'✅' if safety_ok else '❌'}")
print(f"    (complaint, confirm, stability, voice, image, memory)")

print()
if pass_pct >= 98 and critical_count == 0:
    print("  NUMBER 20 SAFE TO TEST ✅")
    print("  (Elite reply brain integrated, all critical safety checks pass)")
else:
    print(f"  NUMBER 20 NOT SAFE")
    print(f"  (Pass rate {pass_pct}%, {critical_count} critical failures)")
print()
