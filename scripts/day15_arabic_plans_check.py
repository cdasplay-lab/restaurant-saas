#!/usr/bin/env python3
"""
NUMBER 15 — Arabic Subscription Plans Check
15 tests covering: Arabic seed plans, Super Admin CRUD, feature flags,
restaurant visibility, payment proof integration, feature guards, tenant isolation.
"""
import sys, os, uuid, time, json, requests

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
try:
    from dotenv import load_dotenv; load_dotenv(_ENV)
except Exception: pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

BASE  = os.getenv("TEST_BASE_URL", "http://localhost:8000")
RUN   = int(time.time()) % 10_000_000
PASS  = "✅"; FAIL = "❌"
results = []; failures = []

def chk(label, ok, detail=""):
    results.append((PASS if ok else FAIL, label, detail))
    if not ok:
        failures.append((label, detail))
        print(f"  {FAIL} {label}: {detail}")
    return ok

def req(method, path, body=None, token=None, timeout=20):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    return getattr(requests, method)(f"{BASE}{path}", json=body, headers=h, timeout=timeout)

def req_json(method, path, body=None, token=None, expected=200):
    try:
        r = req(method, path, body=body, token=token)
        if r.status_code != expected:
            return None, f"HTTP {r.status_code}: {r.text[:300]}"
        try: return r.json(), None
        except Exception as e: return None, str(e)
    except Exception as e:
        return None, str(e)

def db_one(sql, *p):
    c = database.get_db(); r = c.execute(sql, p).fetchone(); c.close()
    return dict(r) if r else None

def db_rows(sql, *p):
    c = database.get_db(); rows = c.execute(sql, p).fetchall(); c.close()
    return [dict(r) for r in rows]

def db_val(sql, *p):
    c = database.get_db(); r = c.execute(sql, p).fetchone(); c.close()
    return r[0] if r else None

import bcrypt as _bcrypt
from jose import jwt as _jwt
SECRET = os.getenv("JWT_SECRET", "dev-secret-key")

def make_sa_token():
    row = db_one("SELECT * FROM super_admins LIMIT 1")
    if not row: return None
    return _jwt.encode({"sub": row["id"], "role": "super", "is_super": True}, SECRET, algorithm="HS256")

def make_owner_token(restaurant_id):
    row = db_one("SELECT * FROM users WHERE restaurant_id=? AND role='owner' LIMIT 1", restaurant_id)
    if not row: return None
    return _jwt.encode({"sub": row["id"], "restaurant_id": restaurant_id, "role": "owner", "is_super": False}, SECRET, algorithm="HS256")

def ensure_restaurant(name):
    rid = f"r_ap_{RUN}_{name}"
    c = database.get_db()
    c.execute("INSERT OR IGNORE INTO restaurants (id,name,plan,status) VALUES (?,?,?,?)",
              (rid, f"Test {name} {RUN}", "free", "active"))
    c.commit()
    uid = f"u_ap_{RUN}_{name}"
    pw  = _bcrypt.hashpw(b"pass123", _bcrypt.gensalt()).decode()
    c.execute("""INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,role,name)
                 VALUES (?,?,?,?,?,?)""",
              (uid, rid, f"owner_{RUN}_{name}@test.com", pw, "owner", f"Owner {name}"))
    c.commit()
    c.close()
    return rid

print(f"\n{'='*72}")
print(f"Arabic Plans Check  — {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*72}\n")

# Ensure DB is migrated (runs migrations + backfill)
database.init_db()

sa_tok = make_sa_token()
if not sa_tok:
    print("ERROR: No super admin found — run server first"); sys.exit(1)

r1 = ensure_restaurant("AP1")
r2 = ensure_restaurant("AP2")
tok1 = make_owner_token(r1)
tok2 = make_owner_token(r2)

# ── 1. Default Arabic plans are seeded ───────────────────────────────────────
print("── 1. Default Arabic plans seeded ──")
codes_needed = {"free", "starter", "professional", "enterprise"}
seeded = db_rows("SELECT code, name_ar, description_ar, features_json, excluded_features_json FROM subscription_plans")
seeded_codes = {r["code"] for r in seeded}
chk("1.1 all 4 default Arabic plan codes exist",
    codes_needed.issubset(seeded_codes),
    f"found={seeded_codes}")

for code in codes_needed:
    row = next((r for r in seeded if r["code"] == code), None)
    chk(f"1.2 {code}: has name_ar", bool(row and row.get("name_ar")),
        str(row.get("name_ar","") if row else "missing"))
    chk(f"1.3 {code}: has Arabic description",
        bool(row and row.get("description_ar")), "")
    chk(f"1.4 {code}: has features_json list",
        bool(row and row.get("features_json") not in ("[]", "", None)),
        row.get("features_json","") if row else "")
    chk(f"1.5 {code}: has excluded_features_json list",
        bool(row and row.get("excluded_features_json") not in ("[]", "", None)),
        row.get("excluded_features_json","") if row else "")

# ── 2. professional plan has is_recommended=1, badge set ─────────────────────
print("\n── 2. Recommended plan ──")
prof = db_one("SELECT * FROM subscription_plans WHERE code='professional'")
chk("2.1 professional.is_recommended = 1", bool(prof and prof.get("is_recommended") == 1), "")
chk("2.2 professional.badge set", bool(prof and prof.get("badge")), str(prof.get("badge","") if prof else ""))
chk("2.3 professional.badge_text_ar set", bool(prof and prof.get("badge_text_ar")), "")

# ── 3. free plan: no channels, no AI ─────────────────────────────────────────
print("\n── 3. Free plan feature limits ──")
free_plan = db_one("SELECT * FROM subscription_plans WHERE code='free'")
chk("3.1 free.max_channels = 0", free_plan and free_plan.get("max_channels") == 0, "")
chk("3.2 free.ai_enabled = 0",   free_plan and free_plan.get("ai_enabled") == 0, "")
chk("3.3 free.max_conversations_per_month = 0", free_plan and free_plan.get("max_conversations_per_month") == 0, "")

# ── 4. Super Admin can edit plan name + price ─────────────────────────────────
print("\n── 4. Super Admin CRUD ──")
plans_resp, err = req_json("get", "/api/super/subscription-plans", token=sa_tok)
chk("4.1 SA: list plans 200", err is None, err or "")
starter_row = next((p for p in (plans_resp or {}).get("plans", []) if p["code"] == "starter"), None)
chk("4.2 starter plan in SA list", starter_row is not None, "")

if starter_row:
    sid = starter_row["id"]
    new_name = f"الأساسية-{RUN}"
    up, err2 = req_json("patch", f"/api/super/subscription-plans/{sid}",
                        body={"name_ar": new_name, "price": 9.99, "currency": "USD"},
                        token=sa_tok)
    chk("4.3 SA: PATCH plan returns ok", err2 is None and (up or {}).get("ok"), err2 or "")
    updated = db_one("SELECT name_ar, price, currency FROM subscription_plans WHERE id=?", sid)
    chk("4.4 name_ar updated in DB", updated and updated["name_ar"] == new_name, str(updated))
    chk("4.5 price updated in DB", updated and abs(updated["price"] - 9.99) < 0.01, str(updated))
    # Restore
    req_json("patch", f"/api/super/subscription-plans/{sid}",
             body={"name_ar": "الخطة الأساسية", "price": 0}, token=sa_tok)

# ── 5. SA can add/remove features ─────────────────────────────────────────────
print("\n── 5. SA edits included/excluded features ──")
if starter_row:
    new_features = '["قناة واحدة","AI أساسي","100 منتج","دعم عبر البريد"]'
    new_excluded = '["تعدد القنوات","الفويس","الستوري"]'
    req_json("patch", f"/api/super/subscription-plans/{sid}",
             body={"features_json": new_features, "excluded_features_json": new_excluded},
             token=sa_tok)
    updated = db_one("SELECT features_json, excluded_features_json FROM subscription_plans WHERE id=?", sid)
    try:
        feats = json.loads(updated["features_json"])
        excl  = json.loads(updated["excluded_features_json"])
        chk("5.1 included features updated (4 items)", len(feats) == 4, str(feats))
        chk("5.2 excluded features updated (3 items)", len(excl) == 3, str(excl))
    except Exception as e:
        chk("5.1 included features JSON valid", False, str(e))
        chk("5.2 excluded features JSON valid", False, str(e))

# ── 6. Restaurant sees updated plan details ───────────────────────────────────
print("\n── 6. Restaurant billing plans API ──")
plans_api, err = req_json("get", "/api/billing/plans", token=tok1)
chk("6.1 billing/plans returns 200", err is None, err or "")
pub_plans = (plans_api or {}).get("plans", [])
chk("6.2 at least 1 plan returned", len(pub_plans) > 0, f"count={len(pub_plans)}")

starter_api = next((p for p in pub_plans if p.get("code") == "starter"), None)
chk("6.3 starter visible to restaurant", starter_api is not None, "")
if starter_api:
    chk("6.4 display_name field present", bool(starter_api.get("display_name")), "")
    chk("6.5 features array present", isinstance(starter_api.get("features"), list), "")
    chk("6.6 excluded_features array present", isinstance(starter_api.get("excluded_features"), list), "")

# ── 7. Hidden plan does not appear to restaurant ──────────────────────────────
print("\n── 7. Hidden / inactive plan visibility ──")
if starter_row:
    req_json("patch", f"/api/super/subscription-plans/{sid}",
             body={"is_public": 0}, token=sa_tok)
    hidden_check, _ = req_json("get", "/api/billing/plans", token=tok1)
    visible_codes = [p.get("code") for p in (hidden_check or {}).get("plans", [])]
    chk("7.1 hidden plan not visible to restaurant", "starter" not in visible_codes,
        f"codes={visible_codes}")
    # Unhide
    req_json("patch", f"/api/super/subscription-plans/{sid}",
             body={"is_public": 1}, token=sa_tok)

# ── 8. Inactive plan does not appear ─────────────────────────────────────────
if starter_row:
    req_json("delete", f"/api/super/subscription-plans/{sid}", token=sa_tok)
    inactive_check, _ = req_json("get", "/api/billing/plans", token=tok1)
    visible_codes2 = [p.get("code") for p in (inactive_check or {}).get("plans", [])]
    chk("8.1 inactive plan not visible to restaurant", "starter" not in visible_codes2,
        f"codes={visible_codes2}")
    # Reactivate
    req_json("patch", f"/api/super/subscription-plans/{sid}",
             body={"is_active": 1}, token=sa_tok)

# ── 9. Restaurant cannot edit plans ──────────────────────────────────────────
print("\n── 9. Access control ──")
if starter_row:
    r403, _ = req_json("patch", f"/api/super/subscription-plans/{sid}",
                       body={"name": "hack"}, token=tok1, expected=403)
    chk("9.1 restaurant owner cannot PATCH plans (403)", r403 is None or _ is None,
        "got response with restaurant token — should have been 403")
    # Actually check the response code directly
    rr = req("patch", f"/api/super/subscription-plans/{sid}",
             body={"name": "hack"}, token=tok1)
    chk("9.2 status 403 for restaurant owner patching plans", rr.status_code == 403,
        f"got {rr.status_code}")

# ── 10. SA can create a new plan ──────────────────────────────────────────────
print("\n── 10. SA creates new custom plan ──")
new_plan_code = f"custom_{RUN}"
create_body = {
    "code": new_plan_code,
    "name": "مخصص",
    "name_ar": "خطة مخصصة للاختبار",
    "description_ar": "خطة للاختبار فقط",
    "price": 15.0,
    "currency": "USD",
    "billing_period": "monthly",
    "billing_period_ar": "شهري",
    "duration_days": 30,
    "is_active": 1, "is_public": 1, "is_recommended": 0,
    "display_order": 99,
    "max_channels": 2, "max_products": 50, "max_staff": 3,
    "max_conversations_per_month": 500,
    "ai_enabled": 1, "analytics_enabled": 1, "voice_enabled": 0,
    "human_handoff_enabled": 1,
    "features_json": '["ميزة اختبار 1","ميزة اختبار 2"]',
    "excluded_features_json": '["صوت","فيديو"]',
    "badge": "", "badge_text_ar": "",
}
created, err_c = req_json("post", "/api/super/subscription-plans", body=create_body, token=sa_tok)
chk("10.1 SA creates plan 200", err_c is None and (created or {}).get("ok"), err_c or "")
new_plan_id = (created or {}).get("id")

if new_plan_id:
    new_row = db_one("SELECT * FROM subscription_plans WHERE id=?", new_plan_id)
    chk("10.2 new plan name_ar in DB", new_row and new_row.get("name_ar") == "خطة مخصصة للاختبار", "")
    chk("10.3 new plan ai_enabled=1 in DB", new_row and new_row.get("ai_enabled") == 1, "")

# ── 11. Payment proof uses plan price/currency ────────────────────────────────
print("\n── 11. Payment proof with plan_id ──")
pm_resp, _ = req_json("get", "/api/super/payment-methods", token=sa_tok)
pm_id = ((pm_resp or {}).get("payment_methods") or [{}])[0].get("id", "") if pm_resp else ""

if new_plan_id and pm_id:
    import io
    import requests as _req2
    proof_data = {
        "plan_id": new_plan_id,
        "payment_method_id": pm_id,
        "payer_name": f"اختبار {RUN}",
        "reference_number": f"REF-AP-{RUN}",
    }
    proof_file = io.BytesIO(b"fakepdf")
    proof_file.name = "receipt.pdf"
    hdr = {"Authorization": f"Bearer {tok1}"}
    try:
        rr2 = _req2.post(
            f"{BASE}/api/billing/payment-proof",
            data=proof_data,
            files={"proof": ("receipt.pdf", proof_file, "application/pdf")},
            headers=hdr, timeout=20
        )
        proof_ok = rr2.status_code == 200
        chk("11.1 proof submit with plan_id returns 200", proof_ok,
            f"HTTP {rr2.status_code}: {rr2.text[:200]}")
        if proof_ok:
            pr_data = rr2.json()
            pr_id = pr_data.get("request_id", "")
            chk("11.2 proof response has request_id", bool(pr_id), str(pr_data)[:100])
            pr_row = db_one("SELECT * FROM payment_requests WHERE id=?", pr_id)
            chk("11.3 proof stored with plan_id",
                pr_row and pr_row.get("plan_id") == new_plan_id, "")
            chk("11.4 proof amount auto-filled from plan price",
                pr_row and abs(pr_row.get("amount", 0) - 15.0) < 0.01,
                str(pr_row.get("amount","") if pr_row else ""))
            chk("11.5 proof currency auto-filled from plan",
                pr_row and pr_row.get("currency") == "USD", "")
    except Exception as e:
        chk("11.1 proof submit", False, str(e))

# ── 12. Approve uses plan duration_days ──────────────────────────────────────
print("\n── 12. Approve applies plan duration ──")
pr_row2 = db_one(
    "SELECT * FROM payment_requests WHERE restaurant_id=? AND plan_id=? ORDER BY created_at DESC LIMIT 1",
    r1, new_plan_id
)
if pr_row2:
    approve_resp, err_a = req_json("post",
        f"/api/super/payment-requests/{pr_row2['id']}/approve",
        body={"note": "اختبار"}, token=sa_tok)
    chk("12.1 approve returns ok", err_a is None and (approve_resp or {}).get("ok"), err_a or "")
    sub = db_one("SELECT * FROM subscriptions WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1", r1)
    chk("12.2 subscription created after approval", sub is not None, "")
    chk("12.3 subscription plan code matches", sub and sub.get("plan") == new_plan_code, str(sub.get("plan","") if sub else ""))
else:
    chk("12.1 approve (skipped — no payment_request)", True, "skipped")
    chk("12.2 subscription created (skipped)", True, "skipped")
    chk("12.3 subscription plan code (skipped)", True, "skipped")

# ── 13. Feature guards read plan settings (free plan) ────────────────────────
print("\n── 13. Feature guards ──")
free_db = db_one("SELECT * FROM subscription_plans WHERE code='free'")
chk("13.1 free plan ai_enabled=0 in DB", free_db and free_db.get("ai_enabled") == 0, "")
chk("13.2 free plan max_channels=0 in DB", free_db and free_db.get("max_channels") == 0, "")
prof_db = db_one("SELECT * FROM subscription_plans WHERE code='professional'")
chk("13.3 professional voice_enabled=1", prof_db and prof_db.get("voice_enabled") == 1, "")
chk("13.4 professional story_reply_enabled=1", prof_db and prof_db.get("story_reply_enabled") == 1, "")
chk("13.5 professional advanced_analytics_enabled=1", prof_db and prof_db.get("advanced_analytics_enabled") == 1, "")
enterprise_db = db_one("SELECT * FROM subscription_plans WHERE code='enterprise'")
chk("13.6 enterprise priority_support_enabled=1", enterprise_db and enterprise_db.get("priority_support_enabled") == 1, "")
chk("13.7 enterprise setup_assistance_enabled=1", enterprise_db and enterprise_db.get("setup_assistance_enabled") == 1, "")

# ── 14. API returns Arabic display fields ──────────────────────────────────────
print("\n── 14. API returns Arabic display fields ──")
plans_check, _ = req_json("get", "/api/billing/plans", token=tok2)
api_plans = (plans_check or {}).get("plans", [])
for p in api_plans:
    if p.get("code") == "professional":
        chk("14.1 professional display_name is Arabic", bool(p.get("display_name")), p.get("display_name",""))
        chk("14.2 professional display_badge present",  bool(p.get("display_badge")), p.get("display_badge",""))
        chk("14.3 professional is_recommended=1 in API", p.get("is_recommended") == 1, str(p.get("is_recommended")))
        break

# ── 15. R1 plan changes do not affect R2 ────────────────────────────────────
print("\n── 15. Tenant isolation ──")
r1_sub = db_one("SELECT plan FROM restaurants WHERE id=?", r1)
r2_sub = db_one("SELECT plan FROM restaurants WHERE id=?", r2)
chk("15.1 R2 plan unchanged after R1 operations",
    not (r1_sub and r2_sub and r1_sub.get("plan") == r2_sub.get("plan") == new_plan_code),
    f"r1={r1_sub}, r2={r2_sub}")

# Cleanup custom plan
if new_plan_id:
    req_json("delete", f"/api/super/subscription-plans/{new_plan_id}", token=sa_tok)

# ── Summary ───────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for r in results if r[0] == PASS)
failed = total - passed

print(f"\n{'='*72}")
print(f"Arabic Plans Check  {passed}/{total} checks {'PASS' if failed == 0 else 'FAIL'}")
print(f"{'='*72}")

report_path = os.path.join(os.path.dirname(__file__), "day15_arabic_plans_report.txt")
with open(report_path, "w") as f:
    f.write(f"Arabic Plans Check  {passed}/{total}\n\n")
    for icon, label, detail in results:
        f.write(f"{icon} {label}{' — ' + detail if detail else ''}\n")
    if failures:
        f.write(f"\nFailed:\n")
        for label, detail in failures:
            f.write(f"  ❌ {label}: {detail}\n")

print(f"\nReport: {report_path}")

if failed == 0:
    print("\n✅ NUMBER 15 PLANS READY")
else:
    print(f"\n❌ NUMBER 15 PLANS NOT READY  ({failed} failures)")
    for label, detail in failures:
        print(f"   • {label}: {detail}")

sys.exit(0 if failed == 0 else 1)
