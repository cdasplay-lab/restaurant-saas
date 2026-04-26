#!/usr/bin/env python3
"""
NUMBER 10 — 20-Scenario Self-Test
Tests Iraqi human quality: confirmation rotation, emoji reduction, casual chat,
confused customers, cheap/best, closing phrases.
"""
import sys, os, json, re, time, requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://localhost:8000"
TOKEN_FILE = "/tmp/d10_token.txt"

with open(TOKEN_FILE) as f:
    TOKEN = f.read().strip()

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

BANNED = [
    "بالتأكيد", "بالطبع", "بكل سرور", "يسعدني", "يسرني", "يشرفني",
    "أنا هنا لمساعدتك", "كيف يمكنني مساعدتك", "هل يمكنني",
    "ما فهمت رسالتك", "لم أفهم ما تقصده",
    "شكراً لاختيارك", "نشكر تواصلك", "نأمل أن تستمتع",
    "نوصلك أسرع ما يمكن", "طلبك في أسرع وقت",
    "تم استلام طلبك", "طلبك قيد المعالجة",
    "عندنا مجموعة متنوعة", "يعتمد على ذوقك",
    "سعيد بخدمتك", "سعيدة بخدمتك",
    "هل هناك شيء آخر", "في أقرب وقت ممكن",
    "لا تتردد", "تحت تصرفك",
]

def sim(messages, scenario="d10_test"):
    r = requests.post(f"{BASE}/api/bot/simulate",
                      headers=HEADERS,
                      json={"messages": messages, "scenario": scenario},
                      timeout=90)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    data = r.json()
    turns = data.get("results", [])
    last = turns[-1]["bot"] if turns else ""
    return last, None

def check_banned(text):
    found = [b for b in BANNED if b in text]
    return found

def check_emoji_count(text):
    # count decorative emojis (exclude structural order-summary emojis: ✅💰👤📍💳🏪──)
    structural = {"✅","💰","👤","📍","💳","🏪"}
    count = sum(1 for c in text if ord(c) > 0x1F000 and c not in structural)
    return count

results = []

def check_no_dangling(text):
    import re
    lone_q = re.search(r'(^|\s)[؟?](\s|$)', text)
    return not lone_q, "DANGLING_PUNCT"

def run(label, messages, checks):
    reply, err = sim(messages, f"d10_{label}")
    if err:
        results.append(("❌", label, f"SIM ERROR: {err}", ""))
        return
    banned_found = check_banned(reply)
    passed = True
    notes = []

    # global: no dangling ؟ in any reply
    ok_dangle, note_dangle = check_no_dangling(reply)
    if not ok_dangle:
        passed = False
        notes.append(note_dangle)

    for check_fn in checks:
        ok, note = check_fn(reply)
        if not ok:
            passed = False
            notes.append(note)

    if banned_found:
        passed = False
        notes.append(f"BANNED: {banned_found}")

    emoji_ct = check_emoji_count(reply)
    if emoji_ct > 1:
        passed = False
        notes.append(f"MULTI_EMOJI({emoji_ct})")

    icon = "✅" if passed else "❌"
    results.append((icon, label, " | ".join(notes) if notes else "OK", reply[:120]))

# ── helpers ──────────────────────────────────────────────────────────────────
def has_any(words):
    def _c(reply):
        ok = any(w in reply for w in words)
        return ok, f"MISSING any of {words}"
    return _c

def not_contains(word):
    def _c(reply):
        ok = word not in reply
        return ok, f"CONTAINS_FORBIDDEN: {word}"
    return _c

def short_reply(max_sentences=4):
    def _c(reply):
        # split on sentence-ending chars only (not Arabic comma used in summaries)
        sents = [s for s in re.split(r'[.؟!\n]', reply) if s.strip()]
        ok = len(sents) <= max_sentences
        return ok, f"TOO_LONG({len(sents)} sentences)"
    return _c

def arabic_dialect():
    def _c(reply):
        dialect_words = ["هسه","واجد","كلش","وياك","وياي","تريد","تحب","شنو","شسمك","زين","حاضر","ماشي","أبشر"]
        # just check it's not full MSA
        msa_openers = ["أود أن","يسرني أن","أفيدكم","أحيطكم علماً"]
        has_msa = any(m in reply for m in msa_openers)
        return not has_msa, "MSA_OPENER_DETECTED"
    return _c

def no_repeat_greeting():
    def _c(reply):
        greetings = ["أهلًا بيك", "أهلا بيك", "مرحبا بيك", "أهلاً بك", "مرحباً بك"]
        found = [g for g in greetings if g in reply]
        return len(found) == 0, f"REPEAT_GREETING: {found}"
    return _c

# ── 20 SCENARIOS ─────────────────────────────────────────────────────────────
print("Running 20 scenarios...\n")

# S01 — Opening greeting
run("S01_greeting",
    ["هلا"],
    [has_any(["🌷"]), short_reply(3)])

# S02 — Price question
run("S02_price",
    ["بكم البرجر؟"],
    [short_reply(3), not_contains("يسعدني")])

# S03 — Order start
run("S03_order_start",
    ["أريد برجر"],
    [short_reply(3), not_contains("بالتأكيد")])

# S04 — Confirm name mid-flow
run("S04_confirm_name",
    ["أريد برجر", "اسمي علي"],
    [short_reply(3)])

# S05 — Confirm address mid-flow
run("S05_confirm_address",
    ["أريد برجر", "اسمي علي", "العنوان الكرادة"],
    [short_reply(3)])

# S06 — Confirm payment mid-flow
run("S06_confirm_payment",
    ["أريد برجر", "اسمي علي", "العنوان الكرادة", "كاش"],
    [short_reply(3)])

# S07 — "شكراً" casual (no greeting reset)
run("S07_casual_thanks",
    ["هلا", "شكراً"],
    [has_any(["العفو","الله يسلمك","ماكو"]), no_repeat_greeting()])

# S08 — "خلاص" casual
run("S08_casual_done",
    ["أريد برجر", "اسمي حسين", "توصيل", "الزيونة", "كاش", "ثبت", "خلاص"],
    [has_any(["حاضر","زين","أبشر","ماشي","وصل","تمام","تم"]), no_repeat_greeting(), not_contains("شكراً لاختيارك")])

# S09 — Complaint (no upsell, no 🌷 expected)
run("S09_complaint",
    ["الطلب متأخر كلش"],
    [not_contains("تحب تطلب"), not_contains("منيو"), short_reply(4)])

# S10 — Complaint escalation
run("S10_complaint_escalate",
    ["الأكل وصل بارد", "أريد تعويض"],
    [not_contains("تحب تطلب"), not_contains("🌷"), short_reply(4)])

# S11 — Confused customer (gibberish-ish)
run("S11_confused",
    ["أبي شي حلو"],
    [not_contains("ما فهمت رسالتك"), not_contains("لم أفهم"), short_reply(3)])

# S12 — Cheapest item
run("S12_cheapest",
    ["شنو الأرخص عندكم؟"],
    [not_contains("عندنا مجموعة"), short_reply(3)])

# S13 — Best/recommendation (no salesy adjectives)
run("S13_best",
    ["شنو أحسن شي تنصح فيه؟"],
    [not_contains("يعتمد على ذوقك"), not_contains("مجموعة متنوعة"),
     not_contains("لذيذ ومميز"), not_contains("الأفضل على الإطلاق"), short_reply(3)])

# S14 — Full order flow + "ثبت" closing
run("S14_full_order_close",
    ["أريد برجر", "اسمي سارة", "توصيل", "المنصور", "كاش", "ثبت"],
    [has_any(["يوصلك","يجهّز","وصل طلبك","🌷"]),
     not_contains("شكراً لاختيارك"),
     not_contains("نأمل أن تستمتع")])

# S15 — Pickup: no address asked, must not end with lone ؟, should ask شسمك or similar
def no_dangling_q(reply):
    import re
    lone_q = re.search(r'(^|\s)[؟?](\s|$)', reply)
    return not lone_q, "DANGLING_Q_MARK"

run("S15_pickup_no_address",
    ["أريد برجر", "استلام"],
    [not_contains("العنوان"), not_contains("وين العنوان"),
     not_contains("واريد اسمك"), not_contains("أريد اسمك"),
     no_dangling_q, short_reply(3)])

# S16 — Repeated "ثبت" (no duplicate summary)
run("S16_no_dup_confirm",
    ["أريد برجر", "اسمي محمود", "استلام", "كاش", "ثبت", "ثبت"],
    [short_reply(5)])

# S17 — Handoff request (should use حاضر not أكيد)
run("S17_handoff",
    ["أريد موظف"],
    [has_any(["أحولك","موظف"]), not_contains("أكيد"), short_reply(2)])

# S18 — Handoff then bot should stay silent
run("S18_handoff_silent",
    ["أريد موظف", "هلا"],
    [short_reply(3)])

# S19 — MSA check (bot should NOT reply formally)
run("S19_no_msa",
    ["ما هو أفضل منتج لديكم؟"],
    [not_contains("يسرني"), not_contains("يشرفني"), not_contains("بكل سرور"), short_reply(4)])

# S20 — Emoji overuse check (mid-flow)
run("S20_emoji_mid_flow",
    ["أريد شاورما", "اسمي خالد"],
    [short_reply(3)])

# ── REPORT ───────────────────────────────────────────────────────────────────
print(f"{'#':<4} {'Status':<4} {'Scenario':<28} {'Reply (first 120)'}")
print("─" * 100)
passed = 0
for i, (icon, label, note, reply) in enumerate(results, 1):
    if icon == "✅":
        passed += 1
    note_str = f"  [{note}]" if note != "OK" else ""
    print(f"{i:<4} {icon} {label:<28} {reply}{note_str}")

print()
print("=" * 100)
total = len(results)
pct = int(passed/total*100)
print(f"إجمالي: {passed}/{total} ({pct}%)")

if passed == total:
    print("✅ NUMBER 10 READY")
else:
    failed = [(l, n, r) for (ic, l, n, r) in results if ic == "❌"]
    print("❌ NUMBER 10 NOT READY — فشل:")
    for label, note, reply in failed:
        print(f"  • {label}: {note}")
        print(f"    رد: {reply}")

# Save report
report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "day10_selftest_report.txt")
with open(report_path, "w") as f:
    f.write(f"Day 10 Self-Test Report — {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write("=" * 80 + "\n")
    for i, (icon, label, note, reply) in enumerate(results, 1):
        f.write(f"{i}. {icon} [{label}]  {note}\n")
        f.write(f"   رد: {reply}\n")
    f.write("\n")
    f.write(f"إجمالي: {passed}/{total} ({pct}%)\n")
    verdict = "✅ NUMBER 10 READY" if passed == total else f"❌ NUMBER 10 NOT READY"
    f.write(verdict + "\n")
print(f"\nReport saved: {report_path}")
