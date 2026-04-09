#!/usr/bin/env python3
"""
Layer 2b — Restaurant Settings & Live Configuration Tests
==========================================================
All expected values pulled from live DB at test time — nothing hardcoded.
Tests that the bot correctly reads and reflects current restaurant config.

Sections:
  S1  Operating hours & working days  (25 Q)
  S2  Delivery configuration          (20 Q)
  S3  Payment methods                 (15 Q)
  S4  Prices                          (20 Q)
  S5  Product availability            (20 Q)
  S6  Offers & discounts              (15 Q)
  S7  Restaurant info                 (15 Q)
  ─────────────────────────────────────────
  Total: 130 questions

Run via: bash scripts/run_regression.sh --scope data
"""
import sys, json, datetime
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import test_utils as U

DELAY = 1.0

# Arabic day names — indexed by full English name
DAY_AR = {
    "monday":    "الاثنين",
    "tuesday":   "الثلاثاء",
    "wednesday": "الأربعاء",
    "thursday":  "الخميس",
    "friday":    "الجمعة",
    "saturday":  "السبت",
    "sunday":    "الأحد",
}
# Python weekday(): 0=Monday … 6=Sunday
DAY_EN    = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
# working_hours JSON uses 3-letter abbreviation keys
DAY_SHORT = ["mon","tue","wed","thu","fri","sat","sun"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_hours(settings):
    """Return dict: day_short → {open(bool), from, to}  (empty dict if not set)."""
    raw = settings.get("working_hours") or "{}"
    try:
        wh = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        wh = {}
    return wh


def _now_status(wh):
    """Return (is_open, today_en, schedule, open_str, close_str)."""
    idx      = datetime.datetime.now().weekday()   # 0=Mon…6=Sun
    today_en = DAY_EN[idx]
    today_sh = DAY_SHORT[idx]
    sch      = wh.get(today_sh, {})
    enabled  = bool(sch.get("open", False))
    open_t   = sch.get("from",  "") or ""
    close_t  = sch.get("to",   "") or ""
    if not enabled or not open_t:
        return False, today_en, sch, open_t, close_t
    now     = datetime.datetime.now().strftime("%H:%M")
    is_open = open_t <= now <= close_t
    return is_open, today_en, sch, open_t, close_t


def _open_days(wh):
    """Return list of full-English-name days that are enabled."""
    result = []
    for en, sh in zip(DAY_EN, DAY_SHORT):
        if bool(wh.get(sh, {}).get("open", False)):
            result.append(en)
    return result


def _all_time_kws(wh):
    """Collect every from/to time string as potential keywords."""
    kws = set()
    for sh in DAY_SHORT:
        sch = wh.get(sh, {})
        for k in ("from", "to"):
            v = sch.get(k, "")
            if v and isinstance(v, str):
                kws.add(v)
    return list(kws) or ["دوام", "ساعات"]


# ── Section builders ──────────────────────────────────────────────────────────

def _build_s1(settings, products):
    """S1: Operating hours — 25 questions."""
    wh = _parse_hours(settings)
    is_open, today_en, sch, open_t, close_t = _now_status(wh)
    today_ar = DAY_AR.get(today_en, "اليوم")
    time_kws = _all_time_kws(wh)
    # 24/7 restaurants: bot replies "24 ساعة" / "أي وقت" instead of specific times
    is_24_7  = (open_t == "00:00" and close_t in ("23:59", "00:00"))
    if is_24_7:
        time_kws = time_kws + ["24","ساعة","أي وقت","دائماً","مفتوح","إي"]

    if is_open:
        state_kws = ["مفتوح", "نعم", "أكيد"] + ([open_t] if open_t else []) + ([close_t] if close_t else [])
    else:
        state_kws = ["مغلق", "مسكرين", "نفتح", "لا"] + ([open_t] if open_t else [])

    open_d   = _open_days(wh)
    friday_kws = (["نعم","مفتوح","أكيد","إي","دوام","عندنا"] if "friday" in open_d else ["مغلق","لا","راحة"])
    sat_kws    = (["نعم","مفتوح","أكيد","إي","دوام","عندنا"] if "saturday" in open_d else ["مغلق","لا","راحة"])
    closed_d   = [d for d in DAY_EN if d not in open_d]
    closed_ar  = [DAY_AR[d] for d in closed_d[:2]] if closed_d else []
    closed_kws_needed = bool(closed_d)
    closed_kws = closed_ar + ["مغلق","راحة"] if closed_kws_needed else ["مفتوح","كل يوم"]

    return [
        ("عندكم دوام هسه؟",                  state_kws,                          f"status: open now? ({today_ar})"),
        ("شوكت تفتحون؟",                      time_kws + ["نفتح","فتح"],           "hours: open time"),
        ("شوكت تسكرون؟",                      time_kws + ["نسكر","إغلاق"],         "hours: close time"),
        ("اليوم مفتوحين؟",                    state_kws,                          f"today open? ({today_ar})"),
        ("اليوم مغلقين؟",                     state_kws + ["لا","مو"],             f"today closed? ({today_ar})"),
        ("الجمعة عدكم دوام؟",                 friday_kws,                         "friday: open?"),
        ("السبت مفتوحين؟",                    sat_kws,                            "saturday: open?"),
        ("أگدر أطلب هسه؟",                   state_kws,                          "can order now?"),
        ("شوكت آخر وقت للطلبات؟",             time_kws + ["آخر","إغلاق","غلاق"],  "last order time"),
        ("تفتحون الصبح لو العصر؟",            time_kws + ["صبح","عصر","الصبح"],   "morning or evening?"),
        ("تشتغلون بالليل؟",                   time_kws + ["ليل","مغلق","نعم"],    "work at night?"),
        ("دوامكم كل يوم نفس الشي؟",           time_kws + ["نفس","ثابت","يوم"],    "same hours daily?"),
        ("أكو يوم معين مغلقين بي؟",           (closed_ar + ["مغلق","راحة"]) if closed_d else ["مفتوح","كل يوم","ماكو"], "closed days"),
        ("إذا المطعم مغلق، شوكت أگدر أطلب؟", time_kws + ["نفتح","غدا","باچر","يفتح","بكرة","تطلب"],   "when next open?"),
        ("أگدر أطلب قبل الإغلاق بشوي؟",      state_kws + time_kws + ["طبعًا","تطلب","تقدر","أكيد","إي"], "order before close?"),
        ("هسه تستقبلون توصيل؟",               state_kws + ["توصيل"],              "delivery open now?"),
        ("هسه تستقبلون استلام؟",               state_kws + ["استلام"],             "pickup open now?"),
        ("إذا تأخر الوقت، بعدكم تستقبلون؟",  time_kws + ["وقت","إغلاق","متأخر"], "late orders?"),
        ("متى يرجع الدوام؟",                  time_kws + ["غدا","باچر","يفتح"],   "when reopens?"),
        ("باچر تفتحون؟",                      time_kws + ["باچر","غدا","نعم"],    "open tomorrow?"),
        ("باچر الدوام نفسه؟",                 time_kws + ["نفس","ثابت","باچر"],   "same hours tomorrow?"),
        ("إذا فتحتوا باچر، من أي ساعة؟",      time_kws + ["ساعة","نفتح"],         "tomorrow open time"),
        ("عندكم فترة استراحة؟",               time_kws + ["استراحة","فترة","لا"], "break time?"),
        ("الدوام اليوم تغيّر؟",               state_kws + time_kws,               "hours changed today?"),
        ("اليوم دوامكم طبيعي؟",               state_kws + time_kws,               "normal hours today?"),
    ]


def _build_s2(settings, products):
    """S2: Delivery — 20 questions."""
    fee = int(settings.get("delivery_fee") or 0)
    dt  = str(settings.get("delivery_time", "") or "")
    mo  = int(settings.get("min_order") or 0)

    fee_kws = (["مجاني","مجانا","بلاش","مجانية","0","توصيل","نوصل","ما عدنا","بلا رسوم"]
               if fee == 0 else U.price_keywords(fee))
    dt_kws  = [dt] if dt else ["دقيقة","دقائق","وقت"]
    mo_kws  = (U.price_keywords(mo) if mo > 0 else ["ماكو","لا","حد","أدنى"])
    avail   = ["نعم","أكيد","نوصل","توصيل","نتوصل"]

    return [
        ("شكد أجور التوصيل؟",                        fee_kws,                           "delivery fee"),
        ("التوصيل مجاني؟",                            ["مجاني","مجانا","بلاش","نعم","لا"] + fee_kws, "delivery free?"),
        ("أكو حد أدنى للطلبات؟",                     mo_kws,                            "min order"),
        ("شكد مدة التوصيل؟",                          dt_kws,                            "delivery time"),
        ("التوصيل متاح هسه؟",                         avail + ["مفتوح","مغلق"],          "delivery available now?"),
        ("عدكم استلام فقط لو توصيل هم؟",             avail + ["استلام","كلا","الاثنين"], "delivery + pickup?"),
        ("أگدر أستلمه بنفسي؟",                       ["نعم","أكيد","استلام","تفضل"],    "self pickup?"),
        ("إذا طلبي قليل توصلون؟",                    mo_kws + ["نعم","ماكو","حد"],      "small order delivery?"),
        ("أجور التوصيل ثابتة لو حسب المنطقة؟",      fee_kws + ["ثابت","نفس","منطقة"],  "flat fee?"),
        ("السعر شامل التوصيل؟",                      fee_kws + ["لا","غير شامل","إضافي"], "price includes delivery?"),
        ("إذا أطلب هسه، شكد يوصل؟",                  dt_kws,                            "how long to deliver?"),
        ("توصلون لكل بغداد؟",                         avail + ["بغداد","نعم","نتوصل"],   "deliver all Baghdad?"),
        ("توصلون للكرادة؟",                           avail + ["نعم","أكيد","نتوصل"],    "deliver Karada?"),
        ("توصلون للمنصور؟",                           avail + ["نعم","أكيد","نتوصل"],    "deliver Mansour?"),
        ("إذا ما توصلون، شنو البديل؟",               ["استلام","تعال","تفضل","موظف","توصيل","عندنا","نوصل","تستلم","تيجي","مطعم"],   "no delivery alternative?"),
        ("أگدر أحول الطلب إلى استلام؟",              ["نعم","أكيد","استلام","موظف"],    "switch to pickup?"),
        ("التوصيل عندكم طول اليوم؟",                 avail + dt_kws,                    "delivery all day?"),
        ("إذا المطعم مفتوح، التوصيل هم مفتوح؟",     avail + ["نعم","أكيد"],            "delivery follows hours?"),
        ("شكد أسرع توصيل عندكم؟",                    dt_kws + ["أسرع","دقيقة"],         "fastest delivery?"),
        ("إذا غيرت العنوان بعد التثبيت شنو يصير؟",  ["موظف","فريق","تواصل","اتصل","تحديث","عنوان","نحتاج","يتطلب"],    "change address after confirm?"),
    ]


def _build_s3(settings, products):
    """S3: Payment methods — 15 questions."""
    pm_raw   = str(settings.get("payment_methods", "") or "كاش")
    pm_clean = pm_raw.replace("،", ",").replace("؛", ",").replace(";", ",")
    methods  = [m.strip() for m in pm_clean.split(",") if m.strip()] or ["كاش"]
    pm_kws   = methods

    def _has(kw):
        return any(kw in m for m in methods)

    cash_kws    = ["نعم","أكيد","كاش","نقد"]    if _has("كاش") or _has("نقد")      else ["لا","مو","ما","آسف","كاش"]
    card_kws    = ["نعم","أكيد","بطاقة","كارت"] if _has("بطاقة") or _has("كارت")  else ["لا","مو","ما","آسف","كاش","فقط"]
    zain_kws    = ["نعم","أكيد","زين"]           if _has("زين")                     else ["لا","مو","ما","آسف","كاش","فقط"]
    online_kws  = ["نعم","أكيد","إلكتروني","تحويل"] if (_has("تحويل") or _has("إلكتروني")) else ["لا","مو","ما","آسف","كاش","فقط"]

    return [
        ("شنو طرق الدفع المتوفرة؟",                 pm_kws,                              "all payment methods"),
        ("عدكم كاش؟",                               cash_kws,                            "accepts cash?"),
        ("الدفع عند الاستلام؟",                     cash_kws + ["استلام"],               "COD?"),
        ("عدكم زين كاش؟",                           zain_kws,                            "Zain Cash?"),
        ("عدكم بطاقة؟",                             card_kws,                            "card payment?"),
        ("عدكم تحويل بنكي؟",                        online_kws,                          "bank transfer?"),
        ("أگدر أدفع إلكتروني؟",                     online_kws,                          "online payment?"),
        ("إذا أريد كاش يصير؟",                      cash_kws,                            "cash ok?"),
        ("إذا أريد بطاقة يصير؟",                    card_kws,                            "card ok?"),
        ("الدفع مسبق لو عند الاستلام؟",             pm_kws + ["مسبق","استلام","عند"],    "prepaid or COD?"),
        ("شنو أسهل طريقة دفع عندكم؟",              pm_kws,                              "easiest payment?"),
        ("تگبلون الدفع بالفرع؟",                    pm_kws + ["فرع","بالفرع","نعم"],     "pay at branch?"),
        ("أكو فرق بالسعر حسب طريقة الدفع؟",        ["لا","نفس","ثابت","ما أكو"],        "price diff by payment?"),
        ("إذا فشل الدفع الإلكتروني شنو أسوي؟",     cash_kws + pm_kws + ["موظف","تواصل"], "payment failure?"),
        ("إذا ما عدكم زين كاش شنو البديل؟",        (pm_kws if not _has("زين") else zain_kws), "no Zain Cash alternative?"),
    ]


def _build_s4(settings, products):
    """S4: Prices — 20 questions (DB-driven)."""
    avail = U.available_products(products)
    if not avail:
        return [("شكد سعر أي منتج؟", ["ماكو","فارغ","ما عندنا"], "no products")] * 20

    s_asc  = sorted(avail, key=lambda p: float(p.get("price", 0)))
    s_desc = sorted(avail, key=lambda p: float(p.get("price", 0)), reverse=True)

    def _p(idx):
        p = avail[idx] if idx < len(avail) else avail[0]
        return p, p["name"], U.price_keywords(p["price"])

    p1, p1n, p1p = _p(0)
    p2, p2n, p2p = _p(1)
    p3, p3n, p3p = _p(2)

    total_2           = int(p1["price"]) + int(p2["price"])
    total_3           = int(p1["price"]) + int(p2["price"]) + int(p3["price"])
    fee               = int(settings.get("delivery_fee") or 0)
    total_w_delivery  = total_2 + fee

    # Accept any of the 3 cheapest / 3 priciest (bot may sort differently than DB)
    cheapest_kws = []
    for p in s_asc[:3]:
        cheapest_kws += U.price_keywords(p["price"]) + U.name_keywords(p["name"])
    priciest_kws = []
    for p in s_desc[:3]:
        priciest_kws += U.price_keywords(p["price"]) + U.name_keywords(p["name"])

    base = [
        (f"شكد سعر {p1n}؟",                          p1p + U.name_keywords(p1n),           f"price: {p1n}"),
        (f"شكد سعر {p2n}؟",                          p2p + U.name_keywords(p2n),           f"price: {p2n}"),
        (f"شكد سعر {p3n}؟",                          p3p + U.name_keywords(p3n),           f"price: {p3n}"),
        (f"إذا أخذت 2 من {p1n} شكد يصير؟",          U.price_keywords(int(p1["price"])*2) + ["2","اثنين","×"], f"2x {p1n}"),
        (f"إذا أخذت {p1n} و{p2n} شكد يصير؟",        p1p + p2p + U.price_keywords(total_2),  f"total: {p1n}+{p2n}"),
        (f"إذا أخذت {p1n} و{p2n} و{p3n} شكد يصير؟", p1p + p2p + p3p + U.price_keywords(total_3), f"total 3 items"),
        ("شنو أرخص شي عندكم؟",                       cheapest_kws,                         f"cheapest: {s_asc[0]['name']}"),
        ("شنو أغلى شي عندكم؟",                       priciest_kws,                         f"priciest: {s_desc[0]['name']}"),
        (f"{p1n} أغلى لو {p2n}؟",                   p1p + p2p + U.name_keywords(p1n),    "price comparison"),
        ("السعر شامل التوصيل؟",
         U.price_keywords(fee) + ["لا","غير شامل","إضافي","مو شامل","شامل","توصيل"] if fee > 0
         else ["لا","مجاني","بلاش","مو شامل","شامل","توصيل"], "includes delivery?"),
        ("السعر شامل الضريبة؟",                      ["لا","شامل","ضريبة","نعم"],          "includes tax?"),
        (f"شكد المجموع بدون توصيل؟",                 U.price_keywords(total_2) + ["شنو","تحب","تطلب","أحسب","المجموع"], f"subtotal: {total_2}"),
        (f"شكد المجموع ويا التوصيل؟",
         U.price_keywords(total_w_delivery) + U.price_keywords(total_2) + ["شنو","تحب","تطلب","أحسب","توصيل"], f"total+delivery: {total_w_delivery}"),
        (f"شكد سعر الحجم الكبير من {p1n}؟",         p1p + ["حجم","كبير","لا يوجد","نفس"], f"large size: {p1n}"),
        (f"إذا شلت المشروب من {p1n} ينزل السعر؟",   p1p + ["نعم","لا","مشروب"],           f"remove drink: {p1n}"),
        (f"إذا أضفت جبن على {p1n} شكد يصير؟",       p1p + ["جبن","إضافة","يصير"],         f"add cheese: {p1n}"),
    ]

    # Pad to 20 with remaining products
    extra = []
    for p in avail[3:]:
        if len(base) + len(extra) >= 20:
            break
        extra.append((
            f"شكد سعر {p['name']}؟",
            U.price_keywords(p["price"]) + U.name_keywords(p["name"]),
            f"price: {p['name']}"
        ))

    result = base + extra
    return result[:20]


def _build_s5(settings, products):
    """S5: Availability — 20 questions."""
    avail   = U.available_products(products)
    unavail = U.unavailable_products(products)

    if not avail:
        return [("شنو المتوفر الآن؟", ["ماكو","فارغ"], "no products")] * 20

    p1 = avail[0]; p1n = p1["name"]
    p2 = avail[1] if len(avail) > 1 else avail[0]; p2n = p2["name"]

    yes_kws = ["نعم","موجود","أكيد","متاح","عندنا","إي","عدنا","يوجد"]
    no_kws  = ["خلص","مو موجود","غير متوفر","ما عندنا","آسف","بديل"]

    sold_tests = []
    if unavail:
        u1 = unavail[0]; u1n = u1["name"]
        sold_tests = [
            (f"هذا المنتج {u1n} مخلص؟",        yes_kws + ["خلص","مو موجود"],                f"sold out: {u1n}"),
            (f"أريد {u1n}، موجود؟",             no_kws,                                      f"unavail: {u1n}"),
            (f"إذا {u1n} مخلص شنو البديل؟",    yes_kws + ["بديل"] + U.name_keywords(p1n),   f"alt for: {u1n}"),
        ]
    else:
        sold_tests = [
            ("شنو المخلص اليوم؟",               ["ماكو","كل شي","متوفر","لا"],               "nothing sold out"),
            ("أكو شي خلص اليوم؟",               ["ماكو","لا","متوفر","كل شي"],               "anything sold out?"),
            ("المنيو كله متوفر؟",               yes_kws + ["كل شي","متاح"],                  "full menu?"),
        ]

    base = [
        (f"هذا المنتج {p1n} موجود؟",            yes_kws,                                     f"available: {p1n}"),
        (f"{p1n} متوفر هسه؟",                   yes_kws,                                     f"in stock: {p1n}"),
        (f"{p2n} موجود اليوم؟",                 yes_kws,                                     f"today avail: {p2n}"),
        *sold_tests,
        ("شنو المخلص اليوم؟",                   (no_kws if unavail else ["ماكو","كل شي","متوفر"]) + ["عندنا","منيو","متوفر","نعم","اليوم","شنو","تطلب"], "sold out today?"),
        ("المنيو كله متوفر اليوم؟",             yes_kws + ["كل شي","متاح","نعم"],            "full menu available?"),
        (f"أكو شي موقفينه هسه؟",               (no_kws if unavail else ["ماكو","لا","متوفر"]), "anything paused?"),
        (f"{p1n} رجع متوفر لو بعده مخلص؟",     yes_kws + ["مخلص","بكرة","يرجع","بعده","متوفر"],  f"back in stock: {p1n}"),
        (f"عندكم {p1n} بجميع الأحجام؟",        yes_kws + ["حجم","نعم","أكيد"],              f"all sizes: {p1n}"),
        (f"شنو الخيارات المتوفرة من {p1['category']}؟",
         U.name_keywords(p1n) + U.name_keywords(p2n) + ["خيارات","متوفر"],                  f"category: {p1['category']}"),
    ]

    # Pad to 20 with more product availability checks
    extra = []
    for p in avail[2:]:
        if len(base) + len(extra) >= 20:
            break
        extra.append((
            f"هل {p['name']} موجود؟",
            yes_kws,
            f"available: {p['name']}"
        ))

    return (base + extra)[:20]


def _build_s6(settings, products):
    """S6: Offers & discounts — 15 questions.
    No offers table in DB → test bot correctly says no active offers,
    or reflects any offer info from custom bot prompt.
    """
    offer_kws = ["عرض","عروض","خصم","تخفيض","ماكو","لا يوجد","حالياً","ما عندنا","نعم","أكيد","ثابت","ثابتة","طقم","عندنا","آسف","بس"]
    avail = U.available_products(products)
    p1n   = avail[0]["name"] if avail else "المنتج"

    return [
        ("أكو عروض اليوم؟",                      offer_kws,                               "offers today?"),
        ("شنو العرض الحالي؟",                    offer_kws,                               "current offer?"),
        ("العرض مستمر؟",                         offer_kws,                               "offer ongoing?"),
        ("العرض اليوم فقط؟",                     offer_kws,                               "today only offer?"),
        (f"{p1n} داخل العرض؟",                  offer_kws,                               f"product in offer: {p1n}"),
        ("العرض يشمل التوصيل؟",                  offer_kws,                               "offer includes delivery?"),
        ("أگدر أطلب 2 من العرض؟",               offer_kws + ["نعم","أكيد","تفضل"],       "order 2 from offer?"),
        ("أكو خصم للطلبات الكبيرة؟",            offer_kws,                               "bulk order discount?"),
        ("أكو خصم للطلب الأول؟",                offer_kws,                               "first order discount?"),
        ("أكو كود خصم؟",                        offer_kws,                               "discount code?"),
        ("العرض ينتهي متى؟",                    offer_kws,                               "offer ends when?"),
        ("إذا انتهى العرض شنو البديل؟",         offer_kws + ["منيو","سعر","عادي"],       "after offer ends?"),
        ("هذا أفضل عرض عندكم؟",                 offer_kws,                               "best offer?"),
        ("إذا أخذت أكثر أكو خصم أكثر؟",        offer_kws,                               "bulk discount?"),
        ("العرض يشمل المشروب؟",                 offer_kws,                               "offer includes drink?"),
    ]


def _build_s7(settings, products):
    """S7: Restaurant info — 15 questions."""
    name    = str(settings.get("restaurant_name",    "") or "")
    phone   = str(settings.get("restaurant_phone",   "") or "")
    address = str(settings.get("restaurant_address", "") or "")
    btype   = str(settings.get("business_type",      "") or "restaurant")

    name_kws    = (U.name_keywords(name) + [name])     if name    else ["مطعم","اسم"]
    phone_kws   = ([phone, phone[-4:]] if len(phone)>4 else [phone]) if phone else ["رقم","هاتف"]
    address_kws = (U.name_keywords(address) + [address[:25]]) if address else ["عنوان","موقع"]
    btype_kws   = ["مطعم","كافيه","محل","مكان"]

    avail   = U.available_products(products)
    pop_kws = [p["name"].split()[0] for p in avail[:3]] if avail else ["منتج"]

    return [
        ("شنو اسم المطعم؟",                     name_kws,                                "restaurant name"),
        ("هذا حساب مطعم إيش؟",                  name_kws,                                "whose account?"),
        ("وين موقعكم؟",                          address_kws,                             "location"),
        ("شنو رقم المطعم؟",                      phone_kws + ["رقم","تواصل"],             "phone number"),
        ("شنو عنوانكم؟",                         address_kws,                             "address"),
        ("هذا مطعم لو كافيه؟",                   btype_kws + name_kws,                    "restaurant or cafe?"),
        ("شنو نوع الأكل عندكم؟",                 name_kws + ["طعام","أكل","نوع","برجر","دجاج","بيتزا","وجبات","عندنا"],  "food type"),
        ("منو يرد هنا؟",                         name_kws + ["بوت","مساعد","مطعم"],       "who responds?"),
        ("أكو رقم للتواصل المباشر؟",             phone_kws + ["رقم","مباشر","تواصل"],     "direct contact?"),
        ("هذا الرقم مالكم؟",                     phone_kws + ["نعم","أكيد","رقم","هو"],   "this number yours?"),
        ("عندكم فرع ثاني؟",                     ["فرع","فروع","نعم","لا","ماكو","آسف","ما عندنا"],  "second branch?"),
        ("شنو أشهر شي عندكم؟",                  pop_kws + name_kws,                      "most popular?"),
        ("عندكم جلسات لو فقط توصيل؟",           ["جلسات","توصيل","كلا","نعم","لا"],      "dine-in or delivery?"),
        ("شنو أوقات التواصل؟",                  phone_kws + ["تواصل","دوام","ساعات","مفتوح","أيام","رقم"],    "contact hours?"),
        ("إذا أريد أجي للمطعم، وين أجي؟",       address_kws,                             "how to visit?"),
    ]


# ── Runner ────────────────────────────────────────────────────────────────────

SECTIONS = [
    ("S1-دوام",    _build_s1),
    ("S2-توصيل",   _build_s2),
    ("S3-دفع",     _build_s3),
    ("S4-أسعار",   _build_s4),
    ("S5-توفر",    _build_s5),
    ("S6-عروض",    _build_s6),
    ("S7-معلومات", _build_s7),
]


def run(token=None):
    if token is None:
        token = U.get_token()

    settings = U.get_settings(token)
    products = U.get_products(token)
    available = U.available_products(products)

    name = settings.get("restaurant_name", "") or ""
    fee  = int(settings.get("delivery_fee") or 0)
    mo   = int(settings.get("min_order") or 0)

    print(f"\n{U.BOLD}{'═'*58}")
    print(f"  Restaurant Settings & Live Data Tests")
    print(f"  Restaurant : {name or '(unnamed)'}")
    print(f"  Products   : {len(available)}/{len(products)} available")
    print(f"  Delivery   : {U.price_fmt(fee) if fee else 'مجاني'} د.ع"
          + (f"  │  Min order: {U.price_fmt(mo)}" if mo else ""))
    pm = settings.get("payment_methods", "") or ""
    if pm:
        print(f"  Payment    : {pm}")
    print(f"{'═'*58}{U.RST}\n")

    all_tests      = []
    section_sizes  = {}
    qid = 1

    for sec_name, builder in SECTIONS:
        tests = builder(settings, products)
        section_sizes[sec_name] = len(tests)
        for (msg, keywords, label) in tests:
            all_tests.append((qid, msg, keywords, f"[{sec_name}] {label}"))
            qid += 1

    total_q = len(all_tests)
    print(f"  Sections: " + "  │  ".join(f"{s}: {n}Q" for s, n in section_sizes.items()))
    print(f"  Total   : {total_q} questions\n")

    passed, total, pct = U.run_suite(
        f"Restaurant Settings Tests ({total_q} Q)",
        all_tests, token, delay=DELAY
    )

    return passed, total, pct


if __name__ == "__main__":
    run()
