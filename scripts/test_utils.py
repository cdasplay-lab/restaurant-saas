"""
Shared utilities for all test layers.
Usage: import test_utils as U
"""
import json, time, urllib.request

BASE  = "http://localhost:8000"
BOLD  = "\033[1m"
RED   = "\033[31m"
GRN   = "\033[32m"
YLW   = "\033[33m"
RST   = "\033[0m"


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_token(email="admin@restaurant.com", password="admin123"):
    data = json.dumps({"email": email, "password": password}).encode()
    req  = urllib.request.Request(
        f"{BASE}/api/auth/login", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["token"]


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_get(path, token):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get_products(token):
    """Returns list of product dicts from DB (all, including unavailable)."""
    return _api_get("/api/products", token)


def get_settings(token):
    """Returns restaurant settings dict."""
    return _api_get("/api/settings", token)


def get_bot_config(token):
    """Returns bot_config dict."""
    return _api_get("/api/bot-config", token)


# ── Bot simulate ──────────────────────────────────────────────────────────────

def simulate(messages, token, scenario="default", timeout=90):
    if isinstance(messages, str):
        messages = [messages]
    data = json.dumps({"messages": messages, "scenario": scenario}).encode()
    req  = urllib.request.Request(
        f"{BASE}/api/bot/simulate", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body    = json.loads(r.read())
            results = body.get("results", [])
            return results[-1].get("bot", "") if results else ""
    except Exception as e:
        return f"ERROR:{e}"


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_reply(reply, keywords):
    """2=pass short, 1=pass long, 0=fail."""
    if reply.startswith("ERROR"):    return 0
    if not any(k in reply for k in keywords): return 0
    return 2 if len(reply) <= 160 else 1


def none_of(reply, banned):
    """Returns True if reply contains NONE of the banned phrases (good)."""
    return not any(b in reply for b in banned)


# ── Data helpers ──────────────────────────────────────────────────────────────

def price_fmt(price):
    """6000.0 → '6,000'  (matches bot output format)."""
    return f"{int(price):,}"


def price_keywords(price):
    """Return both comma and no-comma forms: ['6,000', '6000']."""
    n = int(price)
    return [f"{n:,}", str(n)]


def name_keywords(name):
    """Split product name into individual word keywords."""
    return [w for w in name.split() if len(w) >= 2]


def available_products(products):
    return [p for p in products if p.get("available", 1)]


def unavailable_products(products):
    return [p for p in products if not p.get("available", 1)]


# ── Test runner ───────────────────────────────────────────────────────────────

def run_suite(suite_name, tests, token, delay=1.0):
    """
    Run a list of test tuples: (id, msg_or_msgs, keywords, label)
    msg_or_msgs can be str (single) or list (multi-turn).
    Returns (passed, total, pct).
    """
    print(f"\n{BOLD}{'═'*58}")
    print(f"  {suite_name}")
    print(f"{'═'*58}{RST}\n")

    results = []
    for t in tests:
        qid      = t[0]
        msgs     = t[1]
        keywords = t[2]
        label    = t[3] if len(t) > 3 else (msgs if isinstance(msgs, str) else msgs[-1])

        reply = simulate(msgs, token)
        score = score_reply(reply, keywords)

        col   = GRN if score == 2 else (YLW if score == 1 else RED)
        short = reply[:70] + ("…" if len(reply) > 70 else "")
        lbl   = str(label)[:35]
        print(f"  [{col}{score}{RST}] [{qid:3}] {lbl:<37} → {short}")

        results.append((qid, score, reply, label))
        time.sleep(delay)

    passed = sum(1 for _, s, *_ in results if s > 0)
    total  = len(results)
    pct    = passed * 100 // total if total else 0
    col    = GRN if pct >= 97 else (YLW if pct >= 90 else RED)

    print(f"\n{BOLD}{'═'*58}")
    print(f"  PASS {passed}/{total} ({pct}%)")
    print(f"{'═'*58}{RST}")

    failures = [(qid, r, lbl) for qid, s, r, lbl in results if s == 0]
    if failures:
        print(f"\n{RED}{BOLD}FAILURES:{RST}")
        for qid, r, lbl in failures:
            print(f"  [{qid:3}] {lbl}")
            print(f"        رد: {r[:80]}")

    return passed, total, pct
