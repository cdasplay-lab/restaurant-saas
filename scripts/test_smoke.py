#!/usr/bin/env python3
"""
Smoke Test — Quick sanity check after any change
==================================================
~20 critical tests: mix of core behavior + 1 DB-driven price check.
Target: ≥90% in <3 minutes. Run after every prompt/code change.

Coverage:
  • Identity & greeting
  • Emoji response
  • Price accuracy (DB-driven, 1 product)
  • Customization
  • Address & phone acceptance
  • Handoff trigger
  • Iraqi slang
  • BANNED phrases absent
  • No generic opener on greeting
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import test_utils as U

DELAY = 0.8


def build_tests(token):
    products = U.available_products(U.get_products(token))
    p1 = products[0] if products else {"name": "وجبة", "price": 5000}
    p1n  = p1["name"]
    p1p  = U.price_keywords(p1["price"])     # ['6,000', '6000']
    p1pk = U.price_fmt(p1["price"])

    return [
        # Identity
        (1,  "من أنت؟",                     ["مساعد","بوت","آلي","مطعم"],
         "identity"),

        # Greeting
        (2,  "هلا",                         ["🌷","حياك","هلا","أهلاً"],
         "greeting"),

        # Emoji
        (3,  "😍",                          ["🌷","يسلم","حياك","تحب","😍"],
         "emoji 😍"),

        # DB-driven price
        (4,  f"بكم {p1n}؟",                 p1p + U.name_keywords(p1n),
         f"price: {p1n} → {p1pk}"),

        # Availability — accept positive OR "not today + alternative" (bot variance)
        (5,  f"{p1n} موجود اليوم؟",         ["نعم","موجود","أكيد","إي","متاح","تجرب","القائمة","آسف"],
         f"availability: {p1n}"),

        # Customization (multi-turn)
        (6,  [f"أريد {p1n}", "بدون بصل"],   ["🌷","بدون","بصل","تمام","أكيد"],
         "customization: بدون بصل"),

        # Change of mind
        (7,  [f"أريد {p1n}", "شيله"],        ["تمام","🌷","شلناه","شلنا","أزلنا","حذفنا"],
         "change: شيل"),

        # Address
        (8,  [f"أريد {p1n}", "عنواني حي الجادرية"],
             ["وصلت","🌷","الجادرية","تمام"],
         "address accept"),

        # Phone
        (9,  [f"أريد {p1n}", "هذا رقمي 07901234567"],
             ["وصلت","🌷","تمام","نكمل","توصيل","استلام","رقم"],
         "phone accept"),

        # Deferred data
        (10, [f"أريد {p1n}", "أرسل الرقم بعدين"],
             ["تمام","🌷","وقتك","راحتك","بعدين"],
         "deferred phone"),

        # Handoff
        (11, "أريد موظف",                   ["موظف","فريق","بشري","إنسان"],
         "handoff"),

        # Complaint
        (12, "الطلب السابق كان ناقص",       ["آسف","ناقص","موظف","فريق"],
         "complaint"),

        # Iraqi slang: هسه (يوصل هسه = does it deliver now?)
        (13, [f"أريد {p1n}", "يوصل هسه؟"],  ["هسه","الحين","نعم","دقيقة","وقت","توصيل"],
         "slang: هسه"),

        # Iraqi slang: حار كلش (single message to avoid context loss)
        (14, f"أريد {p1n} حار كلش",         ["🌷","حار","أكيد","تمام","وصلت"],
         "slang: حار كلش"),

        # No-bot request
        (15, "ما أريد أحچي ويا بوت",        ["موظف","بشري","فريق","إنسان"],
         "no-bot request"),

        # Story context
        (16, (f"[العميل يرد على ستوري يعرض: {p1n} — {p1pk} د.ع]\n"
              f"سياق للبوت: استغل الفرصة وابدأ flow البيع مباشرة.\n"
              f"رد العميل: أريد هذا"),
             U.name_keywords(p1n) + [p1pk, "🌷", "أكيد"],
         "story reply"),

        # Emoji ❤️
        (17, "❤️",                          ["🌷","يسلم","حياك","❤️","شكراً"],
         "emoji ❤️"),

        # Negative: rude (should not crash)
        (18, "انتم ما تسوون شي",             ["آسف","نحسن","نكمل","موظف","فريق"],
         "negative feedback"),

        # Multi-product order count
        (19, f"أريد 2 من {p1n}",
             ["2","اثنين","×","🌷","أكيد"] + U.name_keywords(p1n),
         "quantity: 2"),

        # BANNED check: greeting should not start sales pitch with generic opener
        (20, "هلا بيك",                     ["🌷","حياك","هلا","أهلاً","تحب"],
         "no generic opener"),
    ]


def run(token=None):
    if token is None:
        token = U.get_token()

    tests = build_tests(token)
    passed, total, pct = U.run_suite(
        "🚀 Smoke Test (20 questions)", tests, token, delay=DELAY
    )

    # BANNED phrases check on a fresh greeting
    reply = U.simulate("هلا، شنو عندكم اليوم؟", token)
    banned = [
        "أنا هنا لمساعدتك", "كيف يمكنني مساعدتك",
        "كيف يمكنني خدمتك", "ما أقدر أخزن العناوين",
        "ما أقدر أستلم أرقام",
    ]
    clean = U.none_of(reply, banned)
    sym = f"{U.GRN}✅{U.RST}" if clean else f"{U.RED}❌{U.RST}"
    print(f"\n  {sym} BANNED phrases: {'نظيف ✓' if clean else 'وجد عبارة ممنوعة!'}")

    return passed, total, pct


if __name__ == "__main__":
    run()
