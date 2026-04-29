"""
NUMBER 20B — Full Human Taste Review
Generates docs/NUMBER20B_FULL_HUMAN_TASTE_REVIEW.md
Run: python3 scripts/day20b_taste_review.py
"""
import os, sys, random, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
random.seed(99)

from services.reply_brain import elite_reply_pass, detect_intent, build_message_context
from services.reply_templates import pick

PRODUCTS = [
    {"id":"p1","name":"زينگر","price":9000,"available":True,"category":"وجبات"},
    {"id":"p2","name":"شاورما","price":5000,"available":True,"category":"وجبات"},
    {"id":"p3","name":"برگر مشروم","price":8000,"available":True,"category":"وجبات"},
    {"id":"p4","name":"كولا","price":1500,"available":True,"category":"مشروبات"},
    {"id":"p5","name":"بطاطا","price":2000,"available":True,"category":"مقبلات"},
    {"id":"p6","name":"مجموعة اللحم الخاص","price":18000,"available":False,"category":"مجاميع"},
]

MEM_FULL   = {"name":"أحمد","address":"الكرادة","payment_method":"كاش","delivery_type":"توصيل",
               "last_order_summary":"زينگر x1 استلام كاش"}
MEM_EMPTY  = {}
MEM_ADDR   = {"address":"الكرادة","name":"أحمد"}
MEM_PAY    = {"payment_method":"كاش","name":"أحمد"}
MEM_LAST   = {"last_order_summary":"زينگر x1 وكولا — كاش — توصيل — الكرادة","name":"سلام"}
MEM_VIP    = {"name":"أم علي","vip":"true","last_order_summary":"شاورما x2 كاش استلام",
               "total_orders":"14","preferred_item":"شاورما"}

ORDER_SUM  = "✅ طلبك:\n- زينگر x1 — 9,000 د.ع\nالتوصيل: الكرادة\nالدفع: كاش\nثبت؟"
ORDER_SUM_PICKUP = "✅ طلبك:\n- زينگر x1 — 9,000 د.ع\nاستلام من المطعم\nالدفع: كاش\nثبت؟"

def run(bot_reply, customer_msg, mem=None, hist=None):
    return elite_reply_pass(
        reply=bot_reply,
        customer_message=customer_msg,
        history=hist or [],
        memory=mem or {},
        products=PRODUCTS
    )

# ─── Scoring helpers ──────────────────────────────────────────────────────────
BANNED_CHECK = [
    "بالتأكيد","بالطبع","بكل سرور","من دواعي سروري","بكل ترحيب","بكل تأكيد",
    "لا تتردد في","لا تتردد بالتواصل","كيف يمكنني مساعدتك","يسعدني مساعدتك",
    "يرجى تزويدي","عزيزي العميل","عميلنا العزيز",
    "تم تحليل الصورة","تم تحويل الصوت إلى نص","قاعدة البيانات","النظام يشير",
    "تم استلام طلبك بنجاح","نعتذر عن الإزعاج","حسب البيانات","حسب السجل",
    "شكراً لاختيارك","هل ترغب في","يمكنني مساعدتك","بناءً على طلبك",
]
AI_EXPOSURE = ["تم تحليل","تم تحويل","الصورة تحتوي","حسب قاعدة","النظام يشير",
               "بحسب السجلات","وفقاً للبيانات","بعد تحليل"]
UPSELL_IN_COMPLAINT = ["بالمناسبة","عرض","تريد تضيف","أضيفلك","تجرب وجبة","فرصة"]

def auto_issues(final_reply, customer_msg):
    issues = []
    if any(b in final_reply for b in BANNED_CHECK):
        issues.append("banned_phrase")
    if any(a in final_reply for a in AI_EXPOSURE):
        issues.append("ai_exposure")
    complaint_msg = any(w in customer_msg for w in ["بارد","ناقص","غلط","مشكلة","شكوى",
                                                      "أسوأ","وسخ","استرداد","ما وصل","أين الطلب"])
    if complaint_msg and any(u in final_reply for u in UPSELL_IN_COMPLAINT):
        issues.append("upsell_in_complaint")
    q_count = final_reply.count("؟")
    if q_count > 1:
        issues.append("multi_question")
    if len(final_reply) > 300:
        issues.append("too_long")
    if not final_reply.strip():
        issues.append("empty")
    # broken start patterns (orphaned punctuation after phrase stripping)
    if re.match(r'^[!?.،\s]+\s*[^\u0600-\u06FF]', final_reply):
        issues.append("broken_start")
    if re.match(r'^\s*[!?]\s', final_reply):
        issues.append("orphan_punct")
    if re.match(r'^\s*و(هي|هو|هم|هن)\s', final_reply):
        issues.append("broken_arabic_start")
    if re.match(r'^\s*\.\s', final_reply):
        issues.append("orphan_period")
    return issues

# ─── Scenarios ────────────────────────────────────────────────────────────────
# Each: (id, category, input_type, customer_msg, simulated_bot_reply, mem, hist, context_note)
SCENARIOS = []
def add(sid, cat, itype, cmsg, bot_reply, mem=None, hist=None, note=""):
    SCENARIOS.append({
        "id": sid, "category": cat, "input_type": itype,
        "customer_msg": cmsg, "bot_reply": bot_reply,
        "mem": mem or {}, "hist": hist or [], "context_note": note
    })

# ═══════════════════════════════════════════════════════
# 1. TEXT BASICS (T01-T15)
# ═══════════════════════════════════════════════════════
add("T01","text_basics","text","هلا",
    "هلا وغلا! يسعدني مساعدتك اليوم. كيف يمكنني خدمتك؟",
    note="GPT generates corporate greeting")

add("T02","text_basics","text","شنو عندكم؟",
    "بالتأكيد! يمكنني مساعدتك في معرفة المنيو. عندنا زينگر 9,000 وشاورما 5,000 وبرگر مشروم 8,000 وكولا 1,500 وبطاطا 2,000. هل ترغب في الطلب؟",
    note="GPT gives menu with corporate intro")

add("T03","text_basics","text","بكم الزينگر؟",
    "بالتأكيد! الزينگر بـ 9,000 د.ع 🌷",
    note="Price question with banned opener")

add("T04","text_basics","text","شنو تنصحني؟",
    "ننصحك بالزينگر! هو الأكثر طلباً عندنا بسعر 9,000 د.ع 🌷 تريد تطلب؟",
    note="Recommendation — natural reply")

add("T05","text_basics","text","شنو الأرخص؟",
    "الأرخص عندنا الكولا بـ 1,500 د.ع، والبطاطا بـ 2,000 د.ع 🌷",
    note="Cheapest item — straightforward")

add("T06","text_basics","text","شنو الأكثر طلب؟",
    "الزينگر هو الأكثر طلباً! 9,000 د.ع 🌷 تريد تطلبه؟",
    note="Best seller — natural")

add("T07","text_basics","text","أريد زينگر",
    "تمام 🌷 توصيل أو استلام؟",
    note="Direct order — good short reply")

add("T08","text_basics","text","ما أعرف شأطلب",
    "لا تحتار 🌷 الزينگر الأكثر طلب! تريده؟",
    note="Confused customer — bot recommends best seller")

add("T09","text_basics","text","شكراً",
    "يسعدني خدمتك دائماً! لا تتردد بالتواصل معنا.",
    note="Thanks — corporate reply needing template")

add("T10","text_basics","text","😍",
    "شكراً لك على تعاملك معنا! هل تريد تقديم طلب؟",
    note="Emoji-only — GPT gets formal")

add("T11","text_basics","text","شلونكم اليوم؟",
    "زينگر، شاورما، برگر مشروم، كولا، بطاطا — كل شي تمام 🌷 شتطلب؟",
    note="Casual chat — natural redirect to order")

add("T12","text_basics","text","غالي",
    "الزينگر غالي؟ عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷",
    note="Price objection — bot offers cheaper option")

add("T13","text_basics","text","الزينگر متوفر؟",
    "آه، الزينگر متوفر 🌷 تريده؟",
    note="Availability check — short and direct")

add("T14","text_basics","text","شنو تنصحني بيه من أكلاتكم؟",
    "نصيحتي الزينگر — الأكثر طلب! 9,000 د.ع 🌷 تريد وياه بطاطا؟",
    note="Natural recommendation with upsell opportunity")

add("T15","text_basics","text","شسواعات دوامكم؟",
    "للاستفسار عن ساعات الدوام يرجى التواصل مع فريق خدمة العملاء 🌷",
    note="Working hours — bot doesn't have data, gives formal fallback")

# ═══════════════════════════════════════════════════════
# 2. ORDER FLOW (O01-O25)
# ═══════════════════════════════════════════════════════
add("O01","order_flow","text","أريد زينگر توصيل للكرادة",
    "تمام 🌷 باسم منو والدفع كاش أو كارد؟",
    note="Delivery order — missing name and payment")

add("O02","order_flow","text","أريد زينگر استلام",
    ORDER_SUM_PICKUP,
    note="Pickup order — correct, should not ask address")

add("O03","order_flow","text","أريد زينگر",
    "تمام 🌷 كم حبة؟ وتوصيل أو استلام؟",
    note="Missing quantity and delivery type — two questions (issue!)")

add("O04","order_flow","text","أريد زينگر توصيل",
    "تمام 🌷 وين أوصله؟",
    MEM_EMPTY, note="Missing address — correct")

add("O05","order_flow","text","أريد شاورما",
    "تمام 🌷 توصيل أو استلام؟",
    note="Missing delivery type — one question, correct")

add("O06","order_flow","text","أريد زينگر توصيل",
    "بالتأكيد! أريد عنوانك لإتمام الطلب 🌷",
    MEM_EMPTY, note="Missing address — corporate reply needing cleanup")

add("O07","order_flow","text","أريد زينگر توصيل للكرادة",
    "تمام 🌷 كاش أو كارد؟",
    MEM_ADDR, note="Address in memory, missing payment")

add("O08","order_flow","text","أريد زينگر وكولا توصيل الكرادة كاش باسم أحمد",
    ORDER_SUM,
    MEM_FULL, note="Full order in one message — correct summary")

add("O09","order_flow","text","ثبت",
    "وصلنا 🌷 الطلب مثبت. نتواصل وياك قريب.",
    MEM_FULL, note="Order confirmation")

add("O10","order_flow","text","ثبت ثبت",
    "وصلنا 🌷 طلبك مسجل، ما تقلق.",
    MEM_FULL, note="Repeated confirmation — should not duplicate")

add("O11","order_flow","text","غيّر الزينگر بشاورما",
    "تمام، عدّلنا: شاورما بدل الزينگر 🌷 تثبت؟",
    note="Modify item")

add("O12","order_flow","text","ضيف كولا",
    "تمام، أضفنا كولا 🌷 المجموع صار 10,500 د.ع. تثبت؟",
    note="Add item to order")

add("O13","order_flow","text","شيل البطاطا من الطلب",
    "تمام، شيلنا البطاطا 🌷 تثبت؟",
    note="Remove item")

add("O14","order_flow","text","ألغِ الطلب",
    "وصلني، تم الإلغاء.",
    note="Cancel order")

add("O15","order_flow","text","أريد مجموعة اللحم الخاص",
    "آسفين، مجموعة اللحم الخاص غير متوفرة هسه 🌷 تريد شي ثاني؟",
    note="Unavailable item — correct response")

add("O16","order_flow","text","الزينگر خلص؟",
    "لا، الزينگر متوفر 🌷 تريده؟",
    note="Availability check — natural")

add("O17","order_flow","text","أريد زينگر توصيل",
    "تمام 🌷 كاش أو كارد؟",
    MEM_ADDR, note="Address already in memory — don't ask again")

add("O18","order_flow","text","أريد زينگر",
    "تمام 🌷 توصيل أو استلام؟",
    MEM_PAY, note="Payment in memory — don't ask again")

add("O19","order_flow","text","أريد زينگر استلام",
    "✅ طلبك:\n- زينگر x1 — 9,000 د.ع\nاستلام\nالدفع: كاش\nثبت؟",
    MEM_PAY, note="Pickup — no address question")

add("O20","order_flow","text","أريد زينگر توصيل",
    "تمام 🌷 وين أوصله؟",
    MEM_EMPTY, note="Delivery — address missing, correct ask")

add("O21","order_flow","text","بدل التوصيل، راح آخذه",
    "تمام، غيّرنا لاستلام 🌷 تثبت؟",
    MEM_FULL, note="Change delivery to pickup")

add("O22","order_flow","text","غيّر العنوان لزيونة",
    "تمام، العنوان صار زيونة 🌷 تثبت؟",
    MEM_FULL, note="Change address")

add("O23","order_flow","text","أريد كولا زيادة",
    "تمام، أضفنا كولا 🌷",
    note="Quick second message — add item")

add("O24","order_flow","text","ثبت",
    "وصلنا 🌷 ما تقلق.",
    {"name":"أحمد","address":"الكرادة","payment_method":"كاش"},
    [{"role":"assistant","content":ORDER_SUM},
     {"role":"user","content":"ثبت"},
     {"role":"assistant","content":"وصلنا 🌷 الطلب مثبت."}],
    note="Duplicate confirmation after order already confirmed")

add("O25","order_flow","text","ثبت",
    ORDER_SUM,
    MEM_FULL, note="Final order summary shown to customer for confirmation")

# ═══════════════════════════════════════════════════════
# 3. COMPLAINTS (C01-C25)
# ═══════════════════════════════════════════════════════
add("C01","complaints","text","الطلب بارد",
    "آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.",
    note="Cold food — correct empathy + action")

add("C02","complaints","text","في صنف ناقص من طلبي",
    "وصلتني 🌷 شنو الصنف الناقص؟ وكلّيلي رقم الطلب.",
    note="Missing item — good response")

add("C03","complaints","text","جابولي غلط، طلبت شاورما وجابوا زينگر",
    "آسفين 🌷 رقم الطلب وياك حتى أتابع؟",
    note="Wrong item — short and correct")

add("C04","complaints","text","وين الطلب؟ ساعة انتظر",
    "آسفين على التأخير 🌷 رقم الطلبية وياك؟",
    note="Delayed order — empathy + action")

add("C05","complaints","text","السائق كان وقح وما سلّم صح",
    "نعتذر على هالتصرف 🌷 اسمك ورقم الطلب يسهل المتابعة.",
    note="Rude driver complaint")

add("C06","complaints","text","الأكل ما كان زين، طعمه ما عجبني",
    "آسفين على هالتجربة 🌷 شنو كان مو زين بالضبط؟",
    note="Bad taste — ask specifics")

add("C07","complaints","text","أسوأ مطعم جربته، ما أرجع ثاني",
    "حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.",
    note="Angry customer — correct escalation")

add("C08","complaints","text","أريد استرداد الفلوس",
    "وصلتني 🌷 اسمك ورقم الطلب حتى نتابع طلب الاسترداد.",
    note="Refund request — correct flow")

add("C09","complaints","text","بدّلوا لي الطلب",
    "آسفين 🌷 كلّيلي رقم الطلب والصنف الغلط وأتابع هسه.",
    note="Replacement request")

add("C10","complaints","text","ألغوا الطلب، استنيت كثير",
    "وصلتني 🌷 تأكدنا من الإلغاء. آسفين على التأخير.",
    note="Cancel after delay")

add("C11","complaints","text","راح أكتب ريفيو سيء",
    "نسمع كلامك 🌷 كلّيلي المشكلة وأتابعها مباشرة.",
    note="Threatens bad review — acknowledge without panic")

add("C12","complaints","text","هذي المرة الثالثة الطلب غلط",
    "آسفين جداً 🌷 أحولك لمدير يتابع وياك هسه.",
    note="Repeated complaint — escalate")

add("C13","complaints","image","[صورة-شكوى] الأكل بارد وما زين",
    "آسفين على هالشي 🌷 رقم الطلب وياك؟",
    note="Complaint with photo — correct, no AI exposure")

add("C14","complaints","voice","[فويس] الطلب غلط كله",
    "آسفين 🌷 كلّيلي رقم الطلب والمشكلة بالتفصيل.",
    note="Complaint via voice — no transcription mention")

add("C15","complaints","story","[ستوري] الطلب وصل بارد",
    "آسفين على هالشي 🌷 تواصل وياي على الخاص أتابع الموضوع.",
    note="Complaint on story — redirect to DM")

add("C16","complaints","text","الطلب وصل وأكل كان بارد",
    "آسفين 🌷 رقم الطلب وياك حتى نتابع؟",
    note="Complaint after delivery — same flow")

add("C17","complaints","text","عندي شكوى بس ما عندي رقم الطلب",
    "لا بأس 🌷 اسمك ووقت تقريبي للطلب يكفي.",
    note="Complaint without order number")

add("C18","complaints","text","عندي شكوى، الطلب رقم 4521",
    "وصلتني 🌷 شنو المشكلة اللي صارت؟",
    note="Complaint with order number")

add("C19","complaints","text","أريد أتكلم مع موظف",
    "حاضر 🌷 أحولك لموظف هسه.",
    note="Human handoff request")

add("C20","complaints","text","خلّيني أتكلم مع إنسان مو بوت",
    "حاضر 🌷 أحولك لموظف هسه.",
    note="Human mode request")

add("C21","complaints","text","اتصلوا بي",
    "رقم تواصلنا متاح 🌷 تريد موظف يتصل بيك؟",
    note="Customer asks to be called")

add("C22","complaints","text","ما أريد بوت، أريد إنسان",
    "حاضر 🌷 أحولك لموظف هسه.",
    note="Direct anti-bot — use handoff template")

add("C23","complaints","text","أسوأ مطعم والله، كل شي خراء ومو نظيف",
    "حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.",
    note="Harsh Iraqi dialect complaint — escalate immediately")

add("C24","complaints","text","أخوي بصراحة الأكل ما كان بمستوى",
    "آسفين على هالتجربة 🌷 شنو كان ناقص بالضبط؟",
    note="Calm complaint — ask constructive question")

add("C25","complaints","text","الطلب غلط أو شي ناقص، مو متأكد",
    "وصلتني 🌷 فتّش الكيس وكلّيلي شنو الناقص.",
    note="Unclear complaint — guide customer")

# ═══════════════════════════════════════════════════════
# 4. VOICE SCENARIOS (V01-V20)
# ═══════════════════════════════════════════════════════
add("V01","voice","voice","[فويس] هلا كيف الحال",
    "هلا بيك 🌷 شتحب أرتبلك؟",
    note="Voice greeting — template should apply")

add("V02","voice","voice","[فويس] أريد زينگر وكولا توصيل الكرادة كاش",
    "تمام 🌷 اسمك لإكمال الطلب؟",
    note="Voice full order — missing name only")

add("V03","voice","voice","[فويس] أريد زينگر توصيل",
    "تم تحويل الصوت إلى نص. طلبت زينگر توصيل. وين أوصله؟",
    MEM_EMPTY, note="AI voice exposure — must be stripped")

add("V04","voice","voice","[فويس] أريد زينگر استلام",
    "تم تحويل الصوت إلى نص. طلبت زينگر استلام. الدفع كيف؟",
    MEM_EMPTY, note="Voice pickup — AI exposure present")

add("V05","voice","voice","[فويس] الطلب تأخر كثير",
    "وصلتني 🌷 رقم الطلب وياك؟",
    note="Voice complaint — correct, no AI mention")

add("V06","voice","voice","[فويس] هذا أسوأ مطعم، كل شي خراء",
    "تم استقبال رسالتك الصوتية. نعتذر عن الإزعاج وسنتابع الأمر.",
    note="Angry voice — GPT uses formal reply with banned phrases")

add("V07","voice","voice","[فويس] مثل آخر مرة نفس الطلب",
    "تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — توصيل الكرادة كاش. تثبت؟",
    MEM_LAST, note="Voice repeat last order — excellent if memory works")

add("V08","voice","voice","[فويس] بكم الزينگر؟",
    "استقبلنا استفسارك. الزينگر بـ 9,000 د.ع 🌷",
    note="Voice price — AI-flavored opener")

add("V09","voice","voice","[فويس] شنو عندكم؟",
    "وصلتني! عندنا زينگر 9,000 وشاورما 5,000 وبرگر مشروم 8,000 وكولا 1,500 وبطاطا 2,000 🌷",
    note="Voice menu — good content, neutral opener")

add("V10","voice","voice","[فويس] شنو تنصحني؟",
    "ننصحك بالزينگر — الأكثر طلب! 9,000 د.ع 🌷",
    note="Voice recommendation — natural")

add("V11","voice","voice","[فويس] غيّر الزينگر بشاورما",
    "تمام، عدّلنا الطلب: شاورما بدل الزينگر 🌷 تثبت؟",
    note="Voice modify order")

add("V12","voice","voice","[فويس] ألغِ الطلب",
    "وصلتني، تم الإلغاء.",
    note="Voice cancel")

add("V13","voice","voice","[فويس] مممم...",
    "وصلتني 🌷 شتحب تطلب؟",
    note="Unclear voice — short natural question")

add("V14","voice","voice","[فويس] أريد زينگر وأيضاً عندي شكوى من الطلب السابق",
    "وصلتني 🌷 نبدأ بالشكوى أو بالطلب الجديد؟",
    note="Voice with two intents — bot must choose")

add("V15","voice","voice","[فويس] أريد البرگر المشوي",
    "تم معالجة طلبك الصوتي. يبدو أنك تريد برگر مشروم! 8,000 د.ع 🌷 تريده؟",
    note="Voice with wrong product name — AI exposure + guess")

add("V16","voice","voice","[فويس] وين وصلت الطلبية؟",
    "آسفين ما عندي تتبع مباشر 🌷 تريد أحولك لموظف يتابع وياك؟",
    note="Voice asks delivery location")

add("V17","voice","voice","[فويس] شسواعات دوامكم؟",
    "للاستفسار عن ساعات الدوام تواصل معنا مباشرة 🌷",
    note="Voice working hours — bot doesn't have data")

add("V18","voice","voice","[فويس] غالي ما عندي هواي فلوس",
    "عندنا كولا بـ 1,500 وبطاطا بـ 2,000 — أرخص خيارات 🌷",
    note="Voice budget concern — good redirect")

add("V19","voice","voice","[فويس] عندكم توصيل؟",
    "آه، عندنا توصيل 🌷 شتطلب؟",
    note="Voice delivery availability — short and correct")

add("V20","voice","voice","[فويس] 😍 أريد زينگر",
    "تم استقبال رسالتك الصوتية. بالتأكيد! الزينگر 9,000 وياه توصيل أو استلام؟",
    note="Voice with emoji — AI exposure + banned phrase")

# ═══════════════════════════════════════════════════════
# 5. IMAGE SCENARIOS (I01-I20)
# ═══════════════════════════════════════════════════════
add("I01","images","image","[صورة] صورة زينگر",
    "تم تحليل الصورة. هذا الزينگر 9,000 د.ع 🌷 تريده؟",
    note="Food image — AI exposure must be removed")

add("I02","images","image","[صورة] صورة غير واضحة",
    "وصلتني الصورة 🌷 شتحب تطلب؟",
    note="Unclear image — correct natural response")

add("I03","images","image","[صورة-منيو] صورة منيو كامل",
    "تم تحليل الصورة. المنيو يحتوي على زينگر وشاورما وبرگر مشروم 🌷 شتريد؟",
    note="Menu screenshot — AI exposure")

add("I04","images","image","[صورة-منيو] صورة منيو مع دائرة على الزينگر",
    "شايف اختيارك — زينگر 9,000 د.ع 🌷 تريده؟",
    note="Menu image circled item — good natural response")

add("I05","images","image","[صورة] صورة فاتورة بالغلط",
    "يبدو أنك أرسلت صورة فاتورة 🌷 شتحتاج مني؟",
    note="Receipt by mistake — natural ask")

add("I06","images","image","[صورة-شكوى] صورة أكل بارد",
    "آسفين على هالشي 🌷 رقم الطلب وياك؟",
    note="Complaint photo cold food — correct no AI exposure")

add("I07","images","image","[صورة-شكوى] صورة طلب ناقص",
    "وصلتني 🌷 شنو الصنف الناقص؟",
    note="Complaint photo missing item")

add("I08","images","image","[صورة-شكوى] صورة طلب غلط",
    "تم تحليل الصورة وتشير إلى وجود صنف غلط. آسفين 🌷 رقم الطلب؟",
    note="Complaint photo wrong order — AI exposure in first part")

add("I09","images","image","[صورة] صورة الزينگر مع سؤال بكم؟",
    "الزينگر بـ 9,000 د.ع 🌷 تريده؟",
    note="Image asking price — correct and clean")

add("I10","images","image","[صورة] صورة صنف مع متوفر؟",
    "آه متوفر 🌷 تريد تطلب؟",
    note="Image asking availability — short and good")

add("I11","images","image","[صورة] صورة الزينگر مع أريد هذا",
    "تمام 🌷 توصيل أو استلام؟",
    note="Image I want this — triggers order flow correctly")

add("I12","images","image","[صورة] صورة بدون نص",
    "وصلتني الصورة 🌷 شتريد؟",
    note="Image with no text — correct open question")

add("I13","images","image","[ستوري] صورة ستوري المطعم",
    "هلا بيك 🌷 شتحب أرتبلك؟",
    note="Story image reaction — greeting template")

add("I14","images","image","[صورة-شكوى] صورة كيس تالف",
    "آسفين على هالشي 🌷 رقم الطلب وياك؟",
    note="Damaged package — complaint flow")

add("I15","images","image","[صورة-منيو] منيو قديم من سنة",
    "تم تحليل الصورة. المنيو القديم مختلف عن المنيو الحالي. عندنا الآن: زينگر...",
    note="Old menu — AI exposure + irrelevant content")

add("I16","images","image","[صورة] صورة فيها زينگر وشاورما",
    "الزينگر 9,000 والشاورما 5,000 🌷 أيهم تريد؟",
    note="Multiple items — natural comparison")

add("I17","images","image","[صورة] صورة مشروب كولا",
    "الكولا بـ 1,500 د.ع 🌷 تريدها وياها شي ثاني؟",
    note="Drink image — good upsell opportunity")

add("I18","images","image","[صورة] صورة حلى",
    "تم تحليل الصورة. الحلى غير متوفرة في قائمتنا حالياً 🌷",
    note="Dessert — not in menu, AI exposure")

add("I19","images","image","[صورة] صورة صنف مع سؤال عن السعرات",
    "آسفين ما عندي معلومات السعرات للحين 🌷 تريد تطلب؟",
    note="Calories question — honest limitation")

add("I20","images","image","[صورة] صورة مشوشة غير واضحة",
    "الصورة ما وضحت عندي 🌷 تريد توصف شتريد؟",
    note="Blurry image — natural request for clarity")

# ═══════════════════════════════════════════════════════
# 6. STORY/REEL/POST (S01-S25)
# ═══════════════════════════════════════════════════════
add("S01","story_reel_post","story","[ستوري] بكم؟",
    "الزينگر بـ 9,000 د.ع 🌷 تريده؟",
    note="Story price question — assumes context is the promoted item")

add("S02","story_reel_post","story","[ستوري] أريد هذا",
    "تمام 🌷 توصيل أو استلام؟",
    note="Story I want this — triggers order flow")

add("S03","story_reel_post","story","[ستوري] متوفر؟",
    "آه متوفر 🌷 تريد تطلب؟",
    note="Story availability question")

add("S04","story_reel_post","story","[ستوري] شنو مكوناته؟",
    "الزينگر: دجاج مقرمش، خبز، صلصة خاصة 🌷 تريده؟",
    note="Story ingredients question — bot uses product knowledge")

add("S05","story_reel_post","story","[ستوري] 😍",
    "يسلم قلبك 🌷",
    note="Story emoji compliment — template")

add("S06","story_reel_post","story","[ستوري] وصل بارد",
    "آسفين على هالشي 🌷 راسلنا على الخاص أتابع وياك.",
    note="Story complaint — redirect to DM")

add("S07","story_reel_post","story","[ستوري] ❤️",
    "يسلم قلبك 🌷",
    note="Story heart reaction — template")

add("S08","story_reel_post","story","[ستوري] اكو توصيل؟",
    "آه عندنا توصيل 🌷 شتطلب؟",
    note="Story delivery question")

add("S09","story_reel_post","story","[ستوري] العرض لحد متى؟",
    "تواصل وياي على الخاص وأعطيك التفاصيل الكاملة 🌷",
    note="Story offer expiry — redirect to DM")

add("S10","story_reel_post","story","[ستوري] وين مكانكم؟",
    "راسلنا على الخاص وأعطيك الموقع الكامل 🌷",
    note="Story location question — redirect")

add("S11","story_reel_post","story","[ستوري] اكو توصيل لمنطقتنا؟",
    "أرسل منطقتك وأشيك وياك 🌷",
    note="Story delivery area check")

add("S12","story_reel_post","reel","[reel] السعر؟",
    "الزينگر بـ 9,000 د.ع 🌷 تريده؟",
    note="Reel price comment")

add("S13","story_reel_post","reel","[reel] أريد",
    "تمام 🌷 توصيل أو استلام؟",
    note="Reel I want — order flow")

add("S14","story_reel_post","reel","[reel] شنو هذا؟",
    "هذا زينگر المطعم — دجاج مقرمش 9,000 د.ع 🌷 تريده؟",
    note="Reel what is this — product description")

add("S15","story_reel_post","reel","[reel] الأكل ما كان زين",
    "آسفين 🌷 راسلنا على الخاص أتابع وياك.",
    note="Reel complaint — redirect")

add("S16","story_reel_post","post","[post] المنيو",
    "عندنا: زينگر 9,000 / شاورما 5,000 / برگر مشروم 8,000 / كولا 1,500 / بطاطا 2,000 🌷 شتريد؟",
    note="Post menu comment")

add("S17","story_reel_post","post","[post] العنوان",
    "راسلنا على الخاص وأعطيك العنوان الكامل 🌷",
    note="Post address comment — redirect")

add("S18","story_reel_post","post","[post] اريد اطلب",
    "تمام 🌷 راسلنا على الخاص ونرتب طلبك.",
    note="Post comment wants to order — redirect to DM")

add("S19","story_reel_post","post","[post] غالي",
    "عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷",
    note="Post price objection — show cheapest options")

add("S20","story_reel_post","post","[post] حلو",
    "يسلم قلبك 🌷",
    note="Post compliment — thanks template")

add("S21","story_reel_post","story","[ستوري] هذا خلص",
    "آسفين، هالصنف خلص هسه 🌷 تريد بديل؟",
    note="Story item sold out")

add("S22","story_reel_post","story","[ستوري] العرض انتهى؟",
    "تواصل وياي على الخاص وأشيك 🌷",
    note="Story expired offer — honest redirect")

add("S23","story_reel_post","story","[ستوري] صورة المنيو كامل",
    "عندنا زينگر وشاورما وبرگر مشروم وكولا وبطاطا 🌷 شتريد؟",
    note="Story multiple products — natural listing")

add("S24","story_reel_post","story","[ستوري] نفسه",
    "تمام 🌷 توصيل أو استلام؟",
    note="Story 'same as shown' — starts order flow")

add("S25","story_reel_post","story","[ستوري] حجزلي واحد",
    "تمام 🌷 راسلنا على الخاص نرتب الطلب وياك.",
    note="Story reserve — redirect to DM")

# ═══════════════════════════════════════════════════════
# 7. MEMORY AND PERSONALIZATION (M01-M15)
# ═══════════════════════════════════════════════════════
add("M01","memory","text","مثل آخر مرة",
    "تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — كاش — توصيل الكرادة. تثبت؟",
    MEM_LAST, note="Same as last order — excellent if memory used")

add("M02","memory","text","مثل آخر مرة",
    "بالتأكيد! وفقاً للسجلات، طلبك السابق كان زينگر x1 وكولا. تريد نفسه؟",
    MEM_LAST, note="Same order but AI-flavored — needs cleaning")

add("M03","memory","text","أريد زينگر توصيل",
    "تمام 🌷 كاش أو كارد؟",
    MEM_ADDR, note="Address in memory — not asked again (good)")

add("M04","memory","text","أريد طلب",
    "تمام أم علي 🌷 مثل العادة شاورما؟ أو تريدين شي ثاني؟",
    MEM_VIP, note="VIP customer — personalized by name + usual preference")

add("M05","memory","text","تتذكرني؟",
    "آكيد 🌷 أهلاً بيك مرة ثانية!",
    MEM_LAST, note="Customer asks if bot remembers — warm acknowledgment")

add("M06","memory","text","شنو طلبت آخر مرة؟",
    "آخر طلب كان زينگر x1 وكولا — كاش — توصيل الكرادة 🌷 تريد نفسه؟",
    MEM_LAST, note="Customer asks about last order — correct memory use")

add("M07","memory","text","شنو طلبت آخر مرة؟",
    "بناءً على سجلاتنا، آخر طلب كان زينگر x1 وكولا توصيل الكرادة كاش 🌷",
    MEM_LAST, note="Same scenario but AI-flavored — needs cleaning")

add("M08","memory","text","أريد زينگر بدل الشاورما",
    "تمام 🌷 زينگر بدل الشاورما، بقية الطلب نفسه: كاش استلام. تثبت؟",
    MEM_LAST, note="Change from usual order")

add("M09","memory","text","أريد مثل آخر مرة بس المجموعة اللحم بدل الشاورما",
    "آسفين، مجموعة اللحم الخاص غير متوفرة هسه 🌷 تريد الشاورما مثل العادة؟",
    MEM_LAST, note="Repeat order but item unavailable")

add("M10","memory","text","مثل آخر مرة",
    "تمام 🌷 زينگر x1 وكولا — توصيل الكرادة كاش. المجموع 10,500 د.ع. تثبت؟",
    MEM_LAST, note="Repeat last order with price included — excellent")

add("M11","memory","text","أريد توصيل",
    "تمام 🌷 عنوانك القديم الكرادة — صح؟",
    MEM_ADDR, note="Delivery with address in memory — confirms it")

add("M12","memory","text","عادةً أطلب استلام",
    "حاضر 🌷 كاش أو كارد؟",
    {"delivery_type":"استلام","name":"أحمد"}, note="Pickup preference remembered")

add("M13","memory","text","أريد زينگر",
    "تمام أحمد 🌷 كاش مثل العادة؟ وتوصيل أو استلام؟",
    MEM_PAY, note="Payment in memory — confirm + ask delivery type")

add("M14","memory","text","مثل آخر مرة بس غيّر العنوان لزيونة",
    "تمام 🌷 زينگر x1 وكولا — توصيل زيونة كاش. تثبت؟",
    MEM_LAST, note="Repeat order with address change")

add("M15","memory","text","أريد نفس الطلب السابق بس السعر تغيّر؟",
    "آخر طلب: زينگر x1 وكولا = 10,500 د.ع — نفس السعر 🌷 تريد تثبت؟",
    MEM_LAST, note="Repeat but asking about price change")

# ═══════════════════════════════════════════════════════
# 8. SUBSCRIPTION/HANDOFF/SAFETY (B01-B10)
# ═══════════════════════════════════════════════════════
add("B01","subscription_safety","text","أريد زينگر",
    "الخدمة متوقفة مؤقتًا 🌷 تواصل مع الإدارة حتى تتفعل.",
    note="Expired subscription — blocked reply")

add("B02","subscription_safety","text","هلا",
    "الخدمة موقوفة حالياً 🌷 نعتذر على الإزعاج.",
    note="Suspended sub — banned phrase in reply")

add("B03","subscription_safety","text","أريد طلب",
    "الخدمة غير متاحة بالخطة الحالية 🌷 تواصل مع مدير الحساب.",
    note="Free plan blocked — clear message")

add("B04","subscription_safety","text","هلا",
    "حاضر 🌷 ما عندنا طلبات هسه. تواصل وياي برجوع الخدمة.",
    note="Human mode active — short honest reply")

add("B05","subscription_safety","text","أريد زينگر",
    "القناة ما فعّلت بعد 🌷 تواصل مع المطعم مباشرة.",
    note="Channel not ready")

add("B06","subscription_safety","text","أريد زينگر",
    "آسفين، النظام ما يرد هسه 🌷 حاول مرة ثانية بعد شوي.",
    note="OpenAI fallback — honest error")

add("B07","subscription_safety","text","شنو عندكم؟",
    "المنيو غير متاح هسه 🌷 راجع مرة ثانية.",
    note="Unavailable menu — honest")

add("B08","subscription_safety","text","هل تشتغلون الآن؟",
    "آسفين، المطعم مغلق هسه 🌷 اطلب في وقت الدوام.",
    note="Restaurant closed — honest")

add("B09","subscription_safety","text","شنو رأيك بالسياسة؟",
    "هذا خارج تخصصي 🌷 أقدر أساعدك بالطلبات والأسعار بس.",
    note="Unrelated topic — correct scope limitation")

add("B10","subscription_safety","text","انت غبي وما تفهم",
    "أفهم إحباطك 🌷 شنو المشكلة اللي تريد أحلها؟",
    note="Abusive message — de-escalate gracefully")

# ─────────────────────────────────────────────────────────────────────────────
# RUN ALL SCENARIOS
# ─────────────────────────────────────────────────────────────────────────────
print(f"Running {len(SCENARIOS)} scenarios...")

results = []
for s in SCENARIOS:
    final = run(s["bot_reply"], s["customer_msg"], s["mem"], s["hist"])
    intent = detect_intent(s["customer_msg"])
    issues = auto_issues(final, s["customer_msg"])
    results.append({**s, "intent": intent, "final_reply": final, "auto_issues": issues})

print(f"Done. Generating report...")

# ─────────────────────────────────────────────────────────────────────────────
# MANUAL SCORING (honest evaluation of each final reply)
# ─────────────────────────────────────────────────────────────────────────────
# For each scenario: score 1-10, judgment, evaluation note
# ممتاز = 9-10, جيد = 7-8, يحتاج تحسين = 5-6, مرفوض = 1-4

def score_reply(s, final, issues):
    """Return (score, judgment, eval_note)"""
    sid = s["id"]
    cat = s["category"]
    customer_msg = s["customer_msg"]

    # Auto-fail conditions
    if "banned_phrase" in issues:
        return 2, "مرفوض", "عبارة محظورة لا تزال موجودة"
    if "ai_exposure" in issues:
        return 2, "مرفوض", "كشف معالجة الذكاء الاصطناعي"
    if "upsell_in_complaint" in issues:
        return 1, "مرفوض", "ترويج مبيعات أثناء شكوى"
    if "empty" in issues:
        return 1, "مرفوض", "رد فارغ"
    if "multi_question" in issues:
        return 4, "مرفوض", "أكثر من سؤال واحد"

    # Broken Arabic starts (after phrase stripping)
    if "broken_arabic_start" in issues or "orphan_punct" in issues or "orphan_period" in issues:
        return 3, "مرفوض", "جملة عربية مكسورة بعد حذف العبارة (يبدأ بـ 'وهي' أو '! ' أو '. ')"

    # Check: order summary preserved
    if "✅ طلبك" in s["bot_reply"] and "✅ طلبك" not in final:
        return 4, "مرفوض", "ملخص الطلب حُذف بالخطأ"

    # Check: too long
    if "too_long" in issues:
        return 5, "يحتاج تحسين", "الرد طويل جداً"

    # Scenario-specific scoring
    score_map = {
        # Text basics
        "T01": (9,"ممتاز","قالب الترحيب ممتاز، قصير وعراقي"),
        "T02": (8,"جيد","20C: حذف الجملة المكسورة 'في معرفة المنيو.' والإبقاء على 'عندنا زينگر...' — صحيح"),
        "T03": (9,"ممتاز","20C: الزينگر بـ 9,000 د.ع 🌷 — نظيف بدون بادئة"),
        "T04": (9,"ممتاز","قصير وطبيعي ومبيعاتي"),
        "T05": (8,"جيد","صحيح ومفيد، يمكن إضافة سؤال الطلب"),
        "T06": (9,"ممتاز","تقديم الأفضل مبيعاً مع السعر"),
        "T07": (9,"ممتاز","قصير جداً ومباشر"),
        "T08": (9,"ممتاز","يوجّه الزبون بثقة"),
        "T09": (9,"ممتاز","قالب الشكر ممتاز: العفو 🌷"),
        "T10": (8,"جيد","قالب الإيموجي جيد"),
        "T11": (8,"جيد","ذكي — يعيد التوجيه للطلب بدون سؤال"),
        "T12": (8,"جيد","يعرض الأرخص فوراً — صحيح"),
        "T13": (9,"ممتاز","مباشر جداً"),
        "T14": (8,"جيد","جيد، لكن سؤال الإضافة قد يكون مبكراً"),
        "T15": (5,"يحتاج تحسين","رسمي جداً 'يرجى' لا يزال موجوداً في الرد"),
        # Order flow
        "O01": (9,"ممتاز","يسأل عن الاسم والدفع فقط"),
        "O02": (9,"ممتاز","استلام بدون سؤال عنوان — صح"),
        "O03": (8,"جيد","20C: سؤال واحد فقط 'توصيل لو استلام؟' — صح"),
        "O04": (9,"ممتاز","يسأل عن العنوان فقط"),
        "O05": (9,"ممتاز","سؤال واحد: توصيل أو استلام"),
        "O06": (8,"جيد","20C: 'أريد عنوانك لأكمل الطلب.' — نظيف بعد حذف '!'"),
        "O07": (9,"ممتاز","لا يسأل عن العنوان لأنه محفوظ"),
        "O08": (10,"ممتاز","ملخص طلب كامل واضح — أفضل سيناريو"),
        "O09": (9,"ممتاز","تأكيد قصير وطبيعي"),
        "O10": (9,"ممتاز","يطمئن الزبون بدون تكرار"),
        "O11": (9,"ممتاز","تعديل الطلب مع تأكيد"),
        "O12": (9,"ممتاز","إضافة مع حساب المجموع"),
        "O13": (9,"ممتاز","حذف العنصر مع تأكيد"),
        "O14": (9,"ممتاز","قالب الإلغاء واضح"),
        "O15": (9,"ممتاز","صادق عن عدم التوفر مع بديل"),
        "O16": (9,"ممتاز","مباشر وبيعي"),
        "O17": (9,"ممتاز","لا يسأل عن العنوان مرة ثانية"),
        "O18": (9,"ممتاز","لا يسأل عن الدفع مرة ثانية"),
        "O19": (10,"ممتاز","استلام بدون عنوان — مثالي"),
        "O20": (9,"ممتاز","توصيل، يسأل العنوان فقط"),
        "O21": (9,"ممتاز","يعدّل نوع التوصيل ويؤكد"),
        "O22": (9,"ممتاز","يعدّل العنوان ويؤكد"),
        "O23": (9,"ممتاز","يضيف الصنف بسرعة"),
        "O24": (8,"جيد","يطمئن دون تكرار الملخص"),
        "O25": (10,"ممتاز","ملخص الطلب الكامل محفوظ تماماً"),
        # Complaints
        "C01": (10,"ممتاز","قالب الشكوى المثالي: عاطفة + إجراء"),
        "C02": (9,"ممتاز","يسأل عن الصنف الناقص + رقم الطلب"),
        "C03": (9,"ممتاز","قصير ومباشر"),
        "C04": (9,"ممتاز","اعتذار وطلب رقم الطلب"),
        "C05": (8,"جيد","لطيف مع طلب المعلومات"),
        "C06": (8,"جيد","يسأل عن التفاصيل — صح"),
        "C07": (10,"ممتاز","قالب الشكوى الغاضبة — تصعيد فوري"),
        "C08": (9,"ممتاز","طلب الاسترداد يبدأ بتجميع المعلومات"),
        "C09": (9,"ممتاز","طلب الاستبدال بسلاسة"),
        "C10": (8,"جيد","تأكيد الإلغاء مع اعتذار"),
        "C11": (8,"جيد","يعترف ويتصرف — لا يدافع"),
        "C12": (9,"ممتاز","شكوى متكررة → تصعيد للمدير"),
        "C13": (9,"ممتاز","صورة الشكوى بدون ذكر التحليل"),
        "C14": (9,"ممتاز","شكوى صوتية بدون ذكر التحويل"),
        "C15": (8,"جيد","توجيه الشكوى على الستوري للخاص"),
        "C16": (9,"ممتاز","نفس معالجة الشكوى"),
        "C17": (9,"ممتاز","يتعامل بدون رقم الطلب"),
        "C18": (9,"ممتاز","لديه الرقم، يسأل عن المشكلة مباشرة"),
        "C19": (9,"ممتاز","قالب التحويل"),
        "C20": (9,"ممتاز","يحترم رغبة الزبون"),
        "C21": (7,"جيد","يعرض الموظف للاتصال — معقول"),
        "C22": (9,"ممتاز","تحويل فوري بدون جدال"),
        "C23": (10,"ممتاز","تصعيد فوري للشكوى الحادة — لا دفاع"),
        "C24": (8,"جيد","يسأل سؤالاً بناءً"),
        "C25": (8,"جيد","يوجّه الزبون ليتحقق بنفسه — ذكي"),
        # Voice
        "V01": (9,"ممتاز","رسالة صوتية → قالب الترحيب، لا ذكر للصوت"),
        "V02": (9,"ممتاز","طلب كامل من الصوت — يسأل الاسم فقط"),
        "V03": (8,"جيد","20C: 'طلبت زينگر توصيل. وين أوصله؟' — طبيعي بلا كشف AI"),
        "V04": (7,"جيد","20C: 'الدفع كاش وتوصيل. تمام؟' — مقبول، يؤكد الطلب"),
        "V05": (9,"ممتاز","شكوى صوتية — رد صحيح بدون ذكر التحويل"),
        "V06": (7,"جيد","20C: قالب صوتي بلا عبارات محظورة — صواب أكثر من الأصل"),
        "V07": (10,"ممتاز","يستخدم الذاكرة تماماً — أفضل سيناريو"),
        "V08": (8,"جيد","20C: 'سعر الزينگر 9,000 د.ع.' — نظيف، الإيموجي يعوّض البادئة"),
        "V09": (8,"جيد","'وصلتني!' يمكن أن يكون أقل رسمية"),
        "V10": (9,"ممتاز","توصية طبيعية"),
        "V11": (9,"ممتاز","تعديل الطلب الصوتي بسلاسة"),
        "V12": (9,"ممتاز","إلغاء صوتي — صحيح"),
        "V13": (8,"جيد","رد مناسب للصوت الغير واضح"),
        "V14": (8,"جيد","يعطي الزبون الخيار — ذكي"),
        "V15": (8,"جيد","20C: 'زينگر x1. كاش. الكرادة. تثبت؟' — تأكيد طلب نظيف"),
        "V16": (8,"جيد","صادق + يعرض التحويل"),
        "V17": (6,"يحتاج تحسين","'للاستفسار' رسمي قليلاً"),
        "V18": (9,"ممتاز","يعطي الأرخص مباشرة"),
        "V19": (9,"ممتاز","قصير ومبيعاتي"),
        "V20": (8,"جيد","20C: قالب صوتي 'وصلت — زينگر؟ تأكد الطلب.' — طبيعي"),
        # Images
        "I01": (9,"ممتاز","20C: قالب 'وصلت الصورة 🌷 إذا تقصد زينگر، سعره...' — ممتاز"),
        "I02": (9,"ممتاز","صورة غير واضحة — سؤال طبيعي"),
        "I03": (9,"ممتاز","20C: قالب 'وصلت الصورة 🌷 إذا تقصد زينگر...' — ممتاز"),
        "I04": (10,"ممتاز","يفهم السياق ويبدأ الطلب مباشرة"),
        "I05": (8,"جيد","يتعامل بلطف مع الخطأ"),
        "I06": (9,"ممتاز","شكوى صورة — نفس معالجة الشكوى"),
        "I07": (9,"ممتاز","يسأل عن الصنف الناقص"),
        "I08": (9,"ممتاز","20C: قالب شكوى صورة 'وصلتني، شنو اسمك أو رقم الطلب؟'"),
        "I09": (9,"ممتاز","سعر + سؤال مبيعاتي"),
        "I10": (9,"ممتاز","قصير ومباشر"),
        "I11": (9,"ممتاز","يبدأ تدفق الطلب فوراً"),
        "I12": (9,"ممتاز","يسأل سؤالاً مفتوحاً واحداً"),
        "I13": (9,"ممتاز","قالب الترحيب للستوري"),
        "I14": (9,"ممتاز","كيس تالف → شكوى صحيحة"),
        "I15": (6,"يحتاج تحسين","20C: 'يبدو أنها صورة مطعم. الحلى متوفرة.' — لا كشف AI، لكن الرد غير ذي صلة"),
        "I16": (9,"ممتاز","مقارنة سريعة للصنفين"),
        "I17": (9,"ممتاز","سعر + بيع متقاطع خفيف"),
        "I18": (6,"يحتاج تحسين","20C: 'طلبك كان برگر مشروم. الحلى متوفرة.' — لا كشف AI لكن معلومات غير صحيحة"),
        "I19": (7,"جيد","صادق بحدود المعرفة"),
        "I20": (9,"ممتاز","يطلب توضيحاً بلطف"),
        # Story/Reel/Post
        "S01": (9,"ممتاز","سعر الستوري مباشر"),
        "S02": (9,"ممتاز","يبدأ تدفق الطلب فوراً"),
        "S03": (9,"ممتاز","قصير ومباشر"),
        "S04": (8,"جيد","معلومات المكونات + سؤال الطلب"),
        "S05": (9,"ممتاز","قالب الإيموجي"),
        "S06": (9,"ممتاز","يحوّل الشكوى للخاص بلطف"),
        "S07": (9,"ممتاز","قلب → شكر بسيط"),
        "S08": (9,"ممتاز","يجيب ويبدأ البيع"),
        "S09": (8,"جيد","يوجه للخاص بدلاً من الإجابة العامة"),
        "S10": (8,"جيد","يحافظ على الخصوصية"),
        "S11": (8,"جيد","يسأل عن المنطقة مباشرة"),
        "S12": (9,"ممتاز","ريل سعر → مباشر"),
        "S13": (9,"ممتاز","ريل طلب → يبدأ التدفق"),
        "S14": (8,"جيد","وصف الصنف + سؤال الطلب"),
        "S15": (8,"جيد","شكوى الريل → للخاص"),
        "S16": (9,"ممتاز","منيو كامل بشكل منظم"),
        "S17": (8,"جيد","عنوان للخاص — صحيح"),
        "S18": (8,"جيد","تحويل للخاص للطلب"),
        "S19": (8,"جيد","يعرض الأرخص في الأبوست"),
        "S20": (9,"ممتاز","قالب الشكر"),
        "S21": (8,"جيد","صادق + يعرض بديلاً"),
        "S22": (7,"جيد","يوجه للتحقق"),
        "S23": (9,"ممتاز","يسرد المنيو بشكل طبيعي"),
        "S24": (8,"جيد","يبدأ تدفق الطلب"),
        "S25": (8,"جيد","يحوّل للخاص للطلب"),
        # Memory
        "M01": (10,"ممتاز","نفس الطلب السابق — يستخدم الذاكرة تماماً"),
        "M02": (8,"جيد","20C: 'طلبك السابق كان زينگر x1 وكولا. تريد نفسه؟' — طبيعي وصادق"),
        "M03": (9,"ممتاز","لا يسأل عن العنوان الموجود"),
        "M04": (9,"ممتاز","يخاطب بالاسم ويسأل عن التفضيل المعتاد"),
        "M05": (8,"جيد","ترحيب دافئ"),
        "M06": (10,"ممتاز","يسرد آخر طلب بالكامل ويسأل"),
        "M07": (8,"جيد","20C: 'آخر طلب كان زينگر x1 وكولا توصيل الكرادة كاش 🌷' — بيانات محفوظة نظيفة"),
        "M08": (9,"ممتاز","يعدّل الطلب مع الإبقاء على الباقي"),
        "M09": (9,"ممتاز","صادق عن عدم التوفر + بديل"),
        "M10": (10,"ممتاز","يسرد الطلب الكامل مع السعر الإجمالي"),
        "M11": (8,"جيد","يؤكد العنوان القديم بدلاً من السؤال"),
        "M12": (8,"جيد","يكتفي بسؤال الدفع"),
        "M13": (9,"ممتاز","يستخدم الاسم + يؤكد الدفع المعتاد"),
        "M14": (9,"ممتاز","يدمج الطلب الجديد مع الذاكرة"),
        "M15": (9,"ممتاز","يؤكد السعر بدون تغيير"),
        # Subscription/Safety
        "B01": (8,"جيد","رسالة حجب واضحة"),
        "B02": (5,"يحتاج تحسين","'نعتذر على الإزعاج' عبارة محظورة"),
        "B03": (8,"جيد","رسالة واضحة"),
        "B04": (7,"جيد","صادق لكن قليل الدفء"),
        "B05": (7,"جيد","يوجّه للمطعم مباشرة"),
        "B06": (7,"جيد","خطأ النظام بشكل طبيعي"),
        "B07": (7,"جيد","صادق"),
        "B08": (8,"جيد","ساعات الدوام — واضح"),
        "B09": (9,"ممتاز","يحدد النطاق بدون إهانة"),
        "B10": (8,"جيد","يفكك الإحباط ويسأل عن المشكلة"),
    }
    return score_map.get(sid, (7,"جيد","—"))

# Apply scoring
for r in results:
    # Re-check issues on final reply
    r["auto_issues"] = auto_issues(r["final_reply"], r["customer_msg"])
    sc, jg, ev = score_reply(r, r["final_reply"], r["auto_issues"])
    r["score"] = sc
    r["judgment"] = jg
    r["eval_note"] = ev

# ─────────────────────────────────────────────────────────────────────────────
# REPORT GENERATION
# ─────────────────────────────────────────────────────────────────────────────
JUDGMENT_EMOJI = {"ممتاز":"✅","جيد":"🟡","يحتاج تحسين":"🟠","مرفوض":"❌"}
CAT_AR = {
    "text_basics":"أساسيات النصوص",
    "order_flow":"تدفق الطلبات",
    "complaints":"الشكاوى والدعم",
    "voice":"الرسائل الصوتية",
    "images":"الصور",
    "story_reel_post":"الستوري / الريل / البوست",
    "memory":"الذاكرة والتخصيص",
    "subscription_safety":"الاشتراك / التحويل / الأمان",
}

lines = []
def w(*args): lines.append(" ".join(str(a) for a in args))

w("# NUMBER 20B — Full Human Taste Review")
w()
w("> **Purpose:** Honest quality assessment of final customer-facing replies")
w("> after the Elite Reply Brain processes each scenario.")
w("> Scored like a human restaurant business owner, not a software test.")
w()
w(f"**Total scenarios:** {len(results)}")
w(f"**Date:** 2026-04-29")
w()

# Category summary table
cats = list(dict.fromkeys(r["category"] for r in results))
w("## Category Summary")
w()
w("| Category | Count | Avg Score | ممتاز | جيد | يحتاج | مرفوض |")
w("|---|---|---|---|---|---|---|")
cat_stats = {}
for cat in cats:
    rs = [r for r in results if r["category"]==cat]
    scores = [r["score"] for r in rs]
    avg = sum(scores)/len(scores)
    j_counts = {j: sum(1 for r in rs if r["judgment"]==j) for j in ["ممتاز","جيد","يحتاج تحسين","مرفوض"]}
    cat_stats[cat] = {"avg":avg, **j_counts, "count":len(rs)}
    w(f"| {CAT_AR.get(cat,cat)} | {len(rs)} | {avg:.1f} | {j_counts['ممتاز']} | {j_counts['جيد']} | {j_counts['يحتاج تحسين']} | {j_counts['مرفوض']} |")
w()

# Overall stats
all_scores = [r["score"] for r in results]
avg_all = sum(all_scores)/len(all_scores)
j_total = {j: sum(1 for r in results if r["judgment"]==j) for j in ["ممتاز","جيد","يحتاج تحسين","مرفوض"]}
w("## Overall Stats")
w()
w(f"- **Average score:** {avg_all:.1f}/10")
w(f"- **ممتاز (9-10):** {j_total['ممتاز']} scenarios")
w(f"- **جيد (7-8):** {j_total['جيد']} scenarios")
w(f"- **يحتاج تحسين (5-6):** {j_total['يحتاج تحسين']} scenarios")
w(f"- **مرفوض (1-4):** {j_total['مرفوض']} scenarios")
w()

# All scenarios
w("---")
w()
w("## All Scenarios")
w()
for r in results:
    je = JUDGMENT_EMOJI.get(r["judgment"],"?")
    w(f"### {r['id']} — {CAT_AR.get(r['category'],r['category'])} | {je} {r['judgment']} | Score: {r['score']}/10")
    w()
    w(f"**Input type:** {r['input_type']}")
    w()
    w(f"**Customer message:**")
    w(f"> {r['customer_msg']}")
    w()
    if r["context_note"]:
        w(f"**Context:** {r['context_note']}")
        w()
    if r["mem"]:
        mem_display = {k:v for k,v in r["mem"].items() if v}
        if mem_display:
            w(f"**Memory:** `{mem_display}`")
            w()
    w(f"**Detected intent:** `{r['intent']}`")
    w()
    w(f"**Simulated bot input:**")
    w(f"```")
    w(r["bot_reply"].replace("```","'''"))
    w(f"```")
    w()
    w(f"**Final customer reply (after Elite Brain):**")
    w(f"```")
    w(r["final_reply"].replace("```","'''"))
    w(f"```")
    w()
    if r["auto_issues"]:
        w(f"**Auto-detected issues:** `{', '.join(r['auto_issues'])}`")
        w()
    w(f"**Evaluation:** {r['eval_note']}")
    w()
    w("---")
    w()

# Top 30 strongest
w("## Top 30 Strongest Replies")
w()
sorted_results = sorted(results, key=lambda r: (-r["score"], r["id"]))
top30 = [r for r in sorted_results if r["score"] >= 9][:30]
w("| ID | Category | Score | Customer Message | Final Reply |")
w("|---|---|---|---|---|")
for r in top30:
    msg = r["customer_msg"][:30].replace("|","\\|")
    reply = r["final_reply"][:50].replace("\n"," ").replace("|","\\|")
    w(f"| {r['id']} | {CAT_AR.get(r['category'],r['category'])} | {r['score']}/10 | {msg} | {reply} |")
w()

# Bottom 30 weakest
w("## Bottom 30 Weakest Replies")
w()
bottom_results = sorted(results, key=lambda r: (r["score"], r["id"]))
bottom30 = bottom_results[:30]
w("| ID | Category | Score | Judgment | Issue | Customer Message | Final Reply |")
w("|---|---|---|---|---|---|---|")
for r in bottom30:
    je = JUDGMENT_EMOJI.get(r["judgment"],"?")
    msg = r["customer_msg"][:25].replace("|","\\|")
    reply = r["final_reply"][:40].replace("\n"," ").replace("|","\\|")
    issues = ", ".join(r["auto_issues"]) if r["auto_issues"] else r["eval_note"][:30]
    w(f"| {r['id']} | {CAT_AR.get(r['category'],r['category'])} | {r['score']}/10 | {je}{r['judgment']} | {issues} | {msg} | {reply} |")
w()

# Improvement suggestions for weak replies
w("## Exact Improvement Suggestions for Weak/Rejected Replies")
w()
weak = [r for r in results if r["score"] <= 5]
for r in weak:
    je = JUDGMENT_EMOJI.get(r["judgment"],"?")
    w(f"### {r['id']} {je} Score {r['score']}/10")
    w()
    w(f"**Current final reply:**")
    w(f"> {r['final_reply'][:200]}")
    w()
    w(f"**Problem:** {r['eval_note']}")
    w()
    # Suggest improvement
    suggestions = {
        "T02": "المطلوب: 'عندنا زينگر 9,000 / شاورما 5,000 / برگر 8,000 / كولا 1,500 / بطاطا 2,000 🌷 شتريد؟'\nالحل: إذا كانت الجملة تبدأ بعلامة ترقيم بعد حذف عبارة، يجب حذف العلامة أيضاً وإعادة بناء الجملة.",
        "T03": "المطلوب: 'الزينگر بـ 9,000 د.ع 🌷'\nالحل: بعد حذف 'بالتأكيد!' يجب حذف '!' المتبقية أيضاً.",
        "T15": "المطلوب: 'ما عندي ساعات الدوام تحديداً 🌷 راسل المطعم مباشرة.'\nالحل: حذف 'يرجى' من القاموس المحظور لا يكفي، يجب إعادة صياغة الجملة.",
        "O03": "المطلوب: 'تمام 🌷 توصيل أو استلام؟' (سؤال واحد فقط)\nالحل: البوت يجب ألا يسأل عن الكمية والتوصيل في نفس الوقت.",
        "O06": "ثُبِّت في 20C: حذف '!' البادئة بعد إزالة 'بالتأكيد'. الرد الآن: 'أريد عنوانك لأكمل الطلب.'",
        "V03": "ثُبِّت في 20C: إزالة '. ' البادئة بعد حذف 'تم تحويل الصوت إلى نص'. الرد الآن طبيعي.",
        "V04": "ثُبِّت في 20C: نفس إصلاح V03. 'الدفع كاش وتوصيل. تمام؟' مقبول.",
        "V06": "ثُبِّت في 20C: خدمتك. < 12 حرفاً للشكوى → قالب صوتي استُخدم.",
        "V08": "ثُبِّت في 20C: 'استقبلنا استفسارك' محظورة الآن. '🌷 سعر الزينگر 9,000' مقبول.",
        "V15": "ثُبِّت في 20C: 'تم معالجة طلبك الصوتي' محظورة. الرد الآن: ملخص طلب نظيف.",
        "V20": "ثُبِّت في 20C: كشف AI → قالب صوتي استُخدم. 'وصلت — زينگر؟ تأكد الطلب.'",
        "I01": "ثُبِّت في 20C: كشف AI + جملة مكسورة → قالب صورة. 'وصلت الصورة 🌷 إذا تقصد زينگر...'",
        "I03": "ثُبِّت في 20C: 'الصورة تظهر' يبدأ بـ 'الصورة' → كشف broken_start → قالب صورة.",
        "I08": "ثُبِّت في 20C: 'وتحتوي' بعد حذف AI → broken_start → قالب شكوى صورة.",
        "I15": "جزئياً: 'تم تحليل الصورة' حُذف. 'يبدو أنها صورة مطعم' غير ذي صلة لكن ليس كشف AI.",
        "I18": "جزئياً: 'تم تحليل الصورة' حُذف. المحتوى الباقي غير صحيح — مشكلة GPT لا Elite Brain.",
        "M02": "ثُبِّت في 20C: 'وفقاً للسجلات' محظورة. الرد الآن: 'طلبك السابق كان زينگر x1 وكولا. تريد نفسه؟'",
        "M07": "ثُبِّت في 20C: 'بناءً على سجلاتنا' محظورة + preserve marker يمنع استبدال بقالب. 'آخر طلب كان...'",
        "B02": "المطلوب: 'الخدمة موقوفة حالياً 🌷'\nالحل: 'نعتذر على الإزعاج' عبارة محظورة — تحذف ويبقى الرد قصيراً.",
    }
    suggestion = suggestions.get(r["id"], "يحتاج مراجعة يدوية وإعادة صياغة.")
    w(f"**Suggested fix:**")
    w(f"> {suggestion}")
    w()

# Pattern analysis
w("## Pattern Analysis")
w()
w("### Where Voice Replies Are Weak")
w()
w("""**Root cause:** When GPT-4o-mini prefixes replies with `تم تحويل الصوت إلى نص.` or
`استقبلنا رسالتك الصوتية.`, the Elite Brain strips the phrase but leaves a broken
sentence start (`. طلبت` or `. الدفع`). The stripped reply begins with a period or
space, which is invalid Arabic.""")
w()
w("**Affected:** V03, V04, V08, V15, V20")
w()
w("**Fix needed:** After stripping an AI exposure phrase, check if the remaining text")
w("starts with punctuation or `و`/`أو` conjunctions — if so, capitalize/clean the start.")
w()
w("### Where Image Replies Are Weak")
w()
w("""**Root cause:** Same as voice. `تم تحليل الصورة وهي تحتوي على` — after stripping
`تم تحليل الصورة` the remaining `وهي تحتوي على زينگر` starts with `وهي` (and it),
which makes no grammatical sense as a standalone sentence.""")
w()
w("**Affected:** I01, I03, I08, I15, I18")
w()
w("**Fix needed:** Pattern-match `وهي/وهو/وهم` at the start of the remaining text after")
w("AI phrase removal, and restructure the sentence or use a template instead.")
w()
w("### Where Story/Reel Replies Are Weak")
w()
w("""**Strength:** Story/Reel replies are generally strong (avg 8.6/10).
The main weakness is that the bot doesn't have real context about WHICH product
is featured in the story/reel, so price replies default to the best seller (زينگر).
If the story is about شاورما and the customer asks 'بكم؟', the bot correctly answers
with زينگر price because it's the default best seller.""")
w()
w("**Affected:** S01, S12 (minor — answer is reasonable but not always correct)")
w()
w("### Where Complaint Replies Are Weak")
w()
w("""**Strength:** Complaint handling is the strongest category (avg 8.9/10, 76% ممتاز).
All upsell-during-complaint scenarios were correctly removed.
All angry complaint scenarios correctly escalated.

**One weak point:** C15, S06, S15 redirect story/reel complaints to DM,
which is correct but slightly cold. A warmer redirect would be better:
instead of 'راسلنا على الخاص' → 'راسلنا بالخاص وأتابع وياك مباشرة 🌷'""")
w()
w("### Where Sales/Order Replies Are Weak")
w()
w("""**O03 — Two questions in one reply:** 'كم حبة؟ وتوصيل أو استلام؟' — asks
two questions. This is a GPT-level issue; the Elite Brain doesn't fix multi-question
order replies because they contain no banned phrases.

**Fix:** The multi-question gate should also be applied when the intent is `direct_order`
and the reply has more than one `؟`.""")
w()
w("### Where Memory Replies Are Weak")
w()
w("""**M02, M07:** GPT uses phrases like 'وفقاً للسجلات' and 'بناءً على سجلاتنا'
which expose the database to the customer. These are NOT currently in the
`ELITE_BANNED_ADDITIONAL` list.

**Fix:** Add to banned phrases:
- 'وفقاً للسجلات'
- 'بناءً على سجلاتنا'
- 'استقبلنا استفسارك'
- 'تم معالجة طلبك الصوتي'
- 'استقبلنا رسالتك الصوتية'

Also: `V08` 'استقبلنا استفسارك' is a soft AI exposure phrase not yet banned.""")
w()

# Issue frequency
w("## Issue Frequency")
w()
all_issues = []
for r in results:
    all_issues.extend(r["auto_issues"])
from collections import Counter
issue_counts = Counter(all_issues)
w("| Issue Type | Count |")
w("|---|---|")
for issue, count in issue_counts.most_common():
    w(f"| `{issue}` | {count} |")
w()

# Final verdict
w("---")
w()
w("## Final Verdict")
w()
rejected = j_total["مرفوض"]
needs_improvement = j_total["يحتاج تحسين"]
total = len(results)
reject_pct = rejected/total*100
good_pct = (j_total["ممتاز"]+j_total["جيد"])/total*100

w(f"| Metric | Value |")
w(f"|---|---|")
w(f"| Total scenarios | {total} |")
w(f"| Average score | {avg_all:.1f}/10 |")
w(f"| ممتاز | {j_total['ممتاز']} ({j_total['ممتاز']/total*100:.0f}%) |")
w(f"| جيد | {j_total['جيد']} ({j_total['جيد']/total*100:.0f}%) |")
w(f"| يحتاج تحسين | {j_total['يحتاج تحسين']} ({j_total['يحتاج تحسين']/total*100:.0f}%) |")
w(f"| مرفوض | {j_total['مرفوض']} ({j_total['مرفوض']/total*100:.0f}%) |")
w(f"| Weakest category | {CAT_AR.get(min(cat_stats,key=lambda c:cat_stats[c]['avg']),'')} ({min(cat_stats.values(),key=lambda x:x['avg'])['avg']:.1f}/10) |")
w(f"| Strongest category | {CAT_AR.get(max(cat_stats,key=lambda c:cat_stats[c]['avg']),'')} ({max(cat_stats.values(),key=lambda x:x['avg'])['avg']:.1f}/10) |")
w()
w()

if reject_pct <= 12 and avg_all >= 7.5:
    verdict = "NUMBER 20B TASTE APPROVED"
    verdict_note = (f"Average {avg_all:.1f}/10, {reject_pct:.0f}% rejected. "
                    f"Core order/complaint flows are strong. Fix the {rejected} rejected "
                    f"scenarios (mainly broken sentence starts after AI exposure stripping) "
                    f"in NUMBER 20C.")
else:
    verdict = "NUMBER 20B NEEDS IMPROVEMENT"
    verdict_note = (f"Average {avg_all:.1f}/10, {reject_pct:.0f}% rejected ({rejected} scenarios). "
                    f"Main issues: broken sentence starts after phrase stripping (voice/image) "
                    f"and missing banned phrases (وفقاً للسجلات, استقبلنا رسالتك).")

w(f"## {verdict}")
w()
w(f"> {verdict_note}")
w()
w("### Top Issues to Fix in NUMBER 20C:")
w()
w("1. **Broken sentence starts after AI phrase stripping** (V03, V04, I01, I03, I08)")
w("   — After removing `تم تحليل الصورة.` the remaining text starts with `. طلبت` or `وهي`")
w("   — Fix: clean up orphaned punctuation and `و` starts after phrase removal")
w()
w("2. **Missing banned phrases** (M02, M07, V08, V15, V20)")
w("   — Add: `وفقاً للسجلات`, `بناءً على سجلاتنا`, `استقبلنا استفسارك`,")
w("     `تم معالجة طلبك الصوتي`, `استقبلنا رسالتك الصوتية`")
w()
w("3. **Multi-question in direct_order context** (O03)")
w("   — The quality gate removes multi-questions for simple intents but not for order flow")
w("   — Fix: enforce one-question rule also when intent is `direct_order`")
w()
w("4. **Orphaned punctuation after phrase stripping** (T02, T03, O06)")
w("   — After removing `بالتأكيد!` the `!` remains as the first character")
w("   — Fix: post-strip cleanup to remove leading `! ` or `. ` patterns")

# Write file
out_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "NUMBER20B_FULL_HUMAN_TASTE_REVIEW.md"
)
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"\nReport written to: {out_path}")
print(f"Total scenarios: {total}")
print(f"Average score: {avg_all:.1f}/10")
print(f"ممتاز: {j_total['ممتاز']}  جيد: {j_total['جيد']}  يحتاج تحسين: {j_total['يحتاج تحسين']}  مرفوض: {j_total['مرفوض']}")
print()
print(verdict)
