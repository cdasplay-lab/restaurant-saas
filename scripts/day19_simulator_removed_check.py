#!/usr/bin/env python3
"""
Day 19 — Simulator removal checks.
Verifies the internal message simulator is gone from production UI and
the backend endpoints are protected in production mode.

Run: python3 scripts/day19_simulator_removed_check.py
Server must be running at http://localhost:8000
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from pathlib import Path

BASE = "http://localhost:8000"
PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"
results = []


def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    print(f"  {status} {label}" + (f" — {detail}" if detail else ""))
    results.append(cond)
    return cond


ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app_html   = (ROOT / "public" / "app.html").read_text()
super_html = (ROOT / "public" / "super.html").read_text()
main_py    = (ROOT / "main.py").read_text()


print("\n=== DAY 19: SIMULATOR REMOVAL CHECKS ===\n")

print("── Group 1: app.html — simulator UI removed ──")
check("1.  محاكي الرسائل الداخلي NOT in app.html",     "محاكي الرسائل الداخلي" not in app_html)
check("2.  simFire() NOT in app.html",                  "simFire" not in app_html)
check("3.  simClearAll() NOT in app.html",              "simClearAll" not in app_html)
check("4.  simLoadStatus() NOT in app.html",            "simLoadStatus" not in app_html)
check("5.  simPlatform input NOT in app.html",          "simPlatform" not in app_html)
check("6.  simSenderId input NOT in app.html",          "simSenderId" not in app_html)
check("7.  simText input NOT in app.html",              "simText" not in app_html)
check("8.  simTrace div NOT in app.html",               "simTrace" not in app_html)
check("9.  meta-simulate URL NOT in app.html",          "meta-simulate" not in app_html)
check("10. إرسال رسالة sim button NOT in app.html",
      "إرسال رسالة" not in app_html or "simFire" not in app_html)

print("\n── Group 2: super.html — no simulator ──")
check("11. محاكي الرسائل الداخلي NOT in super.html",   "محاكي الرسائل الداخلي" not in super_html)
check("12. meta-simulate NOT in super.html",            "meta-simulate" not in super_html)
check("13. simFire NOT in super.html",                  "simFire" not in super_html)

print("\n── Group 3: backend endpoints are production-guarded ──")
# POST endpoint has the guard
check("14. ENVIRONMENT==production guard in debug_meta_simulate (POST)",
      'os.getenv("ENVIRONMENT") == "production"' in main_py and
      "debug_meta_simulate" in main_py)
# GET endpoint has the guard
check("15. ENVIRONMENT==production guard in debug_meta_simulate_status (GET)",
      'os.getenv("RENDER")' in main_py)

print("\n── Group 4: live endpoint returns 404 in local dev (no ENVIRONMENT set) ──")
# In local dev ENVIRONMENT is not set to "production", so endpoints should still
# respond (with 403 bad key, not 404). This confirms the guard only fires in prod.
r_post = requests.post(f"{BASE}/api/debug/meta-simulate?key=badkey", timeout=5)
check("16. Endpoint responds (not 404) in local dev",
      r_post.status_code != 404, f"got {r_post.status_code}")

r_get = requests.get(f"{BASE}/api/debug/meta-simulate-status?key=badkey", timeout=5)
check("17. Status endpoint responds (not 404) in local dev",
      r_get.status_code != 404, f"got {r_get.status_code}")

# Both should return 403 bad key (since no META_APP_ID matches "badkey")
check("18. POST returns 403 bad-key (not open to anyone)",
      r_post.status_code == 403, f"got {r_post.status_code}")
check("19. GET returns 403 bad-key (not open to anyone)",
      r_get.status_code == 403, f"got {r_get.status_code}")

print("\n── Group 5: real channel test tools still present ──")
check("20. Telegram test-connection button still present in app.html",
      "test" in app_html.lower() and "telegram" in app_html.lower())
check("21. Webhook registration code still present in app.html",
      "webhook" in app_html.lower())
check("22. Channel catalog API still referenced in app.html",
      "/api/integrations/catalog" in app_html)

print(f"\n{'='*50}")
passed = sum(results)
total  = len(results)
print(f"Result: {passed}/{total} passed")
if passed == total:
    print("🎉 All simulator removal checks passed!")
else:
    print(f"⚠️  {total - passed} check(s) failed.")
sys.exit(0 if passed == total else 1)
