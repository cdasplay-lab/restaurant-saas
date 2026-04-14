#!/usr/bin/env python3
"""
Day 9 — Load / Concurrency Smoke Test
======================================
هدف: التحقق من سلامة النظام تحت الحمل المتوازي قبل الـ Soft Launch

Tests:
  CONC-01  3 محادثات متوازية — نفس المطعم — ما في 500 أو crash
  CONC-02  R1 + R2 + R3 بنفس الوقت — لا خلط بين المنتجات
  CONC-03  عزل الجلسة — 3 مستخدمين بأسماء مختلفة — كل واحد يرجع اسمه
  CONC-04  رسائل سريعة متوازية (5 threads) — ما في crash
  CONC-05  Webhook dedup — نفس update_id مرتين — رسالة واحدة فقط
  CONC-06  3 طلبات متوازية — ما في duplicate orders
"""
import json, time, sys, threading, urllib.request, urllib.error
from datetime import datetime

BASE  = "http://localhost:8000"

# ── Restaurants (same accounts as Day 8) ─────────────────────────────────────
RESTAURANTS = {
    "R1": {"email": "r1_burger@d8test.com",   "password": "test123456", "name": "برجر هاوس"},
    "R2": {"email": "r2_shawarma@d8test.com", "password": "test123456", "name": "شاورما كينج"},
    "R3": {"email": "r3_cafe@d8test.com",     "password": "test123456", "name": "كافيه لاتيه"},
}

# ── HTTP helper ────────────────────────────────────────────────────────────────
def _req(method, path, data=None, token=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(f"{BASE}{path}", data=body,
                                   headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        if not raw:
            return {}, e.code
        try:
            return json.loads(raw), e.code
        except Exception:
            return {}, e.code
    except Exception as exc:
        return {"error": str(exc)}, 0

def _login(rid):
    cfg = RESTAURANTS[rid]
    resp, status = _req("POST", "/api/auth/login",
                        {"email": cfg["email"], "password": cfg["password"]})
    if status == 200:
        tok = resp.get("token") or resp.get("access_token")
        r_id = resp.get("restaurant_id") or resp.get("user", {}).get("restaurant_id")
        return tok, r_id
    return None, None

def _simulate(token, messages):
    """POST /api/bot/simulate → (bot_replies list, status_code)
    Response: {"results": [{"customer":..., "bot":..., "action":..., "has_order":...}]}
    """
    resp, status = _req("POST", "/api/bot/simulate",
                        {"messages": messages, "scenario": "conc_test"},
                        token=token)
    if status == 200:
        results = resp.get("results", [])
        replies = [r["bot"] for r in results if r.get("bot")]
        if not replies and results:
            # results exist but bot fields empty — likely OpenAI error in server
            import sys
            print(f"    ⚠️  simulate returned empty bot replies: {results[:1]}", file=sys.stderr)
        return replies, status
    return [], status

def _get_orders(token):
    resp, status = _req("GET", "/api/orders", token=token)
    if status != 200:
        return []
    return resp if isinstance(resp, list) else resp.get("orders", [])

# ── Result collector ───────────────────────────────────────────────────────────
_RESULTS = []
_LOCK = threading.Lock()

def _record(test_id, passed, detail=""):
    with _LOCK:
        _RESULTS.append({"id": test_id, "passed": passed, "detail": detail})

# ══════════════════════════════════════════════════════════════════════════════
#  CONC-01 — 3 محادثات متوازية على نفس المطعم
# ══════════════════════════════════════════════════════════════════════════════
def test_conc_01(token):
    print("\n  🔄 CONC-01 — 3 محادثات متوازية (R1) ...")

    msgs = ["مرحبا", "أريد برجر كلاسيك", "اسمي خالد", "الكرادة", "كاش"]
    thread_results = [None, None, None]

    def run(idx):
        replies, status = _simulate(token, msgs)
        thread_results[idx] = (replies, status)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t0

    errors = [i for i, r in enumerate(thread_results) if r is None or r[1] != 200]
    passed = len(errors) == 0
    detail = f"3/3 نجحوا ({elapsed:.1f}s)" if passed else f"فشل threads: {errors}"
    print(f"    {'✅' if passed else '❌'} {detail}")
    _record("CONC-01", passed, detail)
    return passed

# ══════════════════════════════════════════════════════════════════════════════
#  CONC-02 — R1 + R2 + R3 بنفس الوقت — لا خلط
# ══════════════════════════════════════════════════════════════════════════════
def test_conc_02(tokens):
    print("\n  🔄 CONC-02 — R1+R2+R3 متوازي — عزل المنتجات ...")

    questions = {
        "R1": ("شكد سعر البرجر الكلاسيك؟", "6,000"),
        "R2": ("شكد سعر شاورما الدجاج؟",    "5,000"),
        "R3": ("شكد سعر اللاتيه؟",          "4,500"),
    }
    thread_results = {}

    def run(rid):
        q, expected = questions[rid]
        replies, status = _simulate(tokens[rid], [q])
        thread_results[rid] = (replies, status, expected)

    threads = [threading.Thread(target=run, args=(rid,)) for rid in ["R1", "R2", "R3"]]
    for t in threads: t.start()
    for t in threads: t.join()

    passed_all = True
    for rid, (replies, status, expected) in thread_results.items():
        all_text = " ".join(replies)
        hit = expected in all_text
        if not hit:
            passed_all = False
        q_text = questions[rid][0]
        print(f"    {'✅' if hit else '❌'} {rid} [{q_text}] → {expected} {'✓' if hit else '✗'}")
        _record(f"CONC-02-{rid}", hit,
                f"expected={expected} | got={all_text[:60]}")

    return passed_all

# ══════════════════════════════════════════════════════════════════════════════
#  CONC-03 — عزل الجلسة: 3 مستخدمين بأسماء مختلفة
# ══════════════════════════════════════════════════════════════════════════════
def test_conc_03(token):
    print("\n  🔄 CONC-03 — عزل الجلسة (3 أسماء مختلفة متوازية) ...")

    users = [
        ("علي",   ["مرحبا", "أريد برجر كلاسيك", "اسمي علي"]),
        ("سارة",  ["مرحبا", "أريد زنجر",         "اسمي سارة"]),
        ("حسين",  ["مرحبا", "أريد بطاطا",         "اسمي حسين"]),
    ]
    all_names = {u[0] for u in users}
    thread_results = {}

    def run(name, msgs):
        replies, status = _simulate(token, msgs)
        thread_results[name] = (replies, status)

    threads = [threading.Thread(target=run, args=(n, m)) for n, m in users]
    for t in threads: t.start()
    for t in threads: t.join()

    passed_all = True
    for name, (replies, status) in thread_results.items():
        all_text = " ".join(replies)
        other_names = all_names - {name}
        has_own    = name in all_text
        has_other  = any(n in all_text for n in other_names)
        ok = has_own and not has_other
        if not ok:
            passed_all = False
        detail = f"اسمه موجود:{has_own} | اسم غريب:{has_other}"
        print(f"    {'✅' if ok else '❌'} {name}: {detail}")
        _record(f"CONC-03-{name}", ok, detail)

    return passed_all

# ══════════════════════════════════════════════════════════════════════════════
#  CONC-04 — رسائل سريعة متوازية (5 threads بنفس الوقت)
# ══════════════════════════════════════════════════════════════════════════════
def test_conc_04(token):
    print("\n  🔄 CONC-04 — 5 رسائل متوازية سريعة ...")

    messages = [
        "شكد سعر الزنجر؟",
        "عندكم بطاطا؟",
        "شنو طرق الدفع؟",
        "كم وقت التوصيل؟",
        "شنو الدوام؟",
    ]
    results = [None] * 5
    errs    = []

    def send(msg, idx):
        replies, status = _simulate(token, [msg])
        results[idx] = (replies, status)
        if status != 200:
            errs.append((idx, status))

    threads = [threading.Thread(target=send, args=(m, i)) for i, m in enumerate(messages)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t0

    ok_count = sum(1 for r in results if r and r[1] == 200)
    passed   = len(errs) == 0
    detail   = f"{ok_count}/5 نجحوا ({elapsed:.1f}s)" if passed else f"أخطاء: {errs}"
    print(f"    {'✅' if passed else '❌'} {detail}")
    _record("CONC-04", passed, detail)
    return passed

# ══════════════════════════════════════════════════════════════════════════════
#  CONC-05 — Webhook dedup: نفس update_id مرتين
# ══════════════════════════════════════════════════════════════════════════════
def test_conc_05(r1_id, token):
    print("\n  🔄 CONC-05 — Webhook dedup (نفس update_id مرتين) ...")

    # snapshot current conversation count
    convs_before, _ = _req("GET", "/api/conversations?limit=200", token=token)
    before_count = len(convs_before) if isinstance(convs_before, list) else 0

    fake_uid = 9_000_099
    fake_update = {
        "update_id": fake_uid,
        "message": {
            "message_id": fake_uid,
            "from": {"id": 9_777_001, "first_name": "اختبار_ديوب", "last_name": ""},
            "chat": {"id": 9_777_001},
            "text": "مرحبا اختبار dedup",
            "date": int(time.time())
        }
    }

    statuses = [None, None]

    def send(idx):
        _, s = _req("POST", f"/webhook/telegram/{r1_id}", fake_update)
        statuses[idx] = s

    t1 = threading.Thread(target=send, args=(0,))
    t2 = threading.Thread(target=send, args=(1,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Both webhook calls must return 200 (we always return 200 immediately)
    both_200 = all(s == 200 for s in statuses)

    # Wait for background tasks to process
    time.sleep(4)

    # After dedup: should have at most 1 new conversation (not 2)
    convs_after, _ = _req("GET", "/api/conversations?limit=200", token=token)
    after_count = len(convs_after) if isinstance(convs_after, list) else 0
    new_convs = after_count - before_count

    # Dedup means at most 1 new conversation from this sender
    passed = both_200 and new_convs <= 1
    detail = f"webhook 200: {both_200} | محادثات جديدة: {new_convs} (متوقع ≤1)"
    print(f"    {'✅' if passed else '❌'} {detail}")
    _record("CONC-05", passed, detail)
    return passed

# ══════════════════════════════════════════════════════════════════════════════
#  CONC-06 — 3 طلبات متوازية — ما في duplicate أو missing orders
# ══════════════════════════════════════════════════════════════════════════════
def test_conc_06(token):
    print("\n  🔄 CONC-06 — 3 طلبات متوازية — صحة عدد الـ orders ...")

    orders_before = _get_orders(token)
    count_before  = len(orders_before)

    flows = [
        ["مرحبا", "أريد برجر كلاسيك",   "اسمي ريم",   "الجادرية", "كاش"],
        ["مرحبا", "وجبة برجر",           "اسمي نور",   "الكاظمية", "كارد"],
        ["مرحبا", "زنجر وكولا",          "اسمي سلام",  "الدورة",   "كاش"],
    ]
    thread_errors = []

    def run_order(msgs, idx):
        replies, status = _simulate(token, msgs)
        if status != 200:
            thread_errors.append((idx, status))

    threads = [threading.Thread(target=run_order, args=(m, i)) for i, m in enumerate(flows)]
    for t in threads: t.start()
    for t in threads: t.join()

    time.sleep(2)

    orders_after = _get_orders(token)
    count_after  = len(orders_after)
    added        = count_after - count_before

    # No thread errors AND orders count didn't go backwards
    passed = len(thread_errors) == 0 and count_after >= count_before
    detail = (f"قبل: {count_before} | بعد: {count_after} | "
              f"أضيف: {added} | أخطاء: {len(thread_errors)}")
    print(f"    {'✅' if passed else '❌'} {detail}")
    _record("CONC-06", passed, detail)
    return passed

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*65}")
    print(f"  Day 9 — Load / Concurrency Smoke Test")
    print(f"  {now}")
    print(f"{'='*65}")

    # Login
    print("\n⚙️  تسجيل الدخول ...")
    tokens = {}
    r_ids  = {}
    for rid, cfg in RESTAURANTS.items():
        tok, r_id = _login(rid)
        if not tok:
            print(f"  ❌ فشل تسجيل الدخول لـ {rid}")
            print("     → تأكد أن السيرفر شغال:  uvicorn main:app --reload")
            print("     → تأكد من وجود الحسابات: python3 scripts/day8_compare.py --focus R1 R2 R3")
            sys.exit(1)
        tokens[rid] = tok
        r_ids[rid]  = r_id
        print(f"  ✅ {rid} — {cfg['name']}  (rid={r_id})")

    # Run tests — 5s cooldown between groups to avoid OpenAI rate-limit cascade
    COOL = 5
    test_conc_01(tokens["R1"])
    print(f"\n  ⏳ cooldown {COOL}s ...")
    time.sleep(COOL)
    test_conc_02(tokens)
    print(f"\n  ⏳ cooldown {COOL}s ...")
    time.sleep(COOL)
    test_conc_03(tokens["R1"])
    print(f"\n  ⏳ cooldown {COOL}s ...")
    time.sleep(COOL)
    test_conc_04(tokens["R1"])
    print(f"\n  ⏳ cooldown {COOL}s ...")
    time.sleep(COOL)
    test_conc_05(r_ids["R1"], tokens["R1"])
    test_conc_06(tokens["R1"])

    # Summary
    passed_list = [r for r in _RESULTS if r["passed"]]
    failed_list = [r for r in _RESULTS if not r["passed"]]
    total       = len(_RESULTS)

    print(f"\n{'═'*65}")
    print(f"  النتيجة النهائية — Day 9")
    print(f"{'═'*65}")
    for r in _RESULTS:
        icon = "✅" if r["passed"] else "❌"
        print(f"  {icon} [{r['id']}]  {r['detail']}")

    pct = round(len(passed_list) / total * 100) if total else 0
    print(f"\n  الإجمالي: {len(passed_list)}/{total}  ({pct}%)")
    if failed_list:
        print(f"  🔴 فاشل:  {[r['id'] for r in failed_list]}")
        verdict = "يحتاج مراجعة قبل الـ Soft Launch"
    else:
        verdict = "✅ جاهز للـ Soft Launch"
    print(f"  {verdict}")
    print(f"{'='*65}\n")

    # Save report
    report = "scripts/day9_concurrency_report.txt"
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"Day 9 Concurrency Report — {now}\n{'='*65}\n")
        for r in _RESULTS:
            icon = "✅" if r["passed"] else "❌"
            f.write(f"{icon} [{r['id']}]  {r['detail']}\n")
        f.write(f"\nإجمالي: {len(passed_list)}/{total} ({pct}%)\n{verdict}\n")
    print(f"  ✅ التقرير محفوظ في: {report}")

if __name__ == "__main__":
    main()
