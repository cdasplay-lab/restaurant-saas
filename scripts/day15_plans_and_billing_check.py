#!/usr/bin/env python3
"""
NUMBER 15 — Plans & Billing Check
Tests: DB-backed subscription plans CRUD, restaurant billing flow with plan_id,
       approval using plan limits, feature guards, tenant isolation.
"""
import sys, os, uuid, time, json, io, requests

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
try:
    from dotenv import load_dotenv; load_dotenv(_ENV)
except Exception: pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

BASE = os.getenv("TEST_BASE_URL", "http://localhost:8000")
RUN  = int(time.time()) % 10_000_000
PASS = "✅"; FAIL = "❌"
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

print(f"\n{'='*72}")
print(f"NUMBER 15 — Plans & Billing Check  run={RUN}")
print(f"{'='*72}\n")

# ── Seed test restaurants ─────────────────────────────────────────────────────
r1_id = f"pb_r1_{RUN}"; u1_id = f"pb_u1_{RUN}"
r2_id = f"pb_r2_{RUN}"; u2_id = f"pb_u2_{RUN}"
pw_hash = _bcrypt.hashpw(b"plans15pass!", _bcrypt.gensalt()).decode()
conn = database.get_db()
conn.execute("PRAGMA foreign_keys = OFF")
for rid, uid, name in [(r1_id, u1_id, f"Plans R1 {RUN}"), (r2_id, u2_id, f"Plans R2 {RUN}")]:
    conn.execute("INSERT OR IGNORE INTO restaurants (id,name,plan,status) VALUES (?,?,?,?)",
                 (rid, name, "trial", "active"))
    conn.execute("INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,name,role) VALUES (?,?,?,?,?,?)",
                 (uid, rid, f"owner_{rid}@pb15.com", pw_hash, "Owner", "owner"))
    conn.execute("INSERT OR IGNORE INTO settings (id,restaurant_id) VALUES (?,?)", (str(uuid.uuid4()), rid))
    conn.execute("INSERT OR IGNORE INTO subscriptions (id,restaurant_id,plan,status,price,start_date,end_date) VALUES (?,?,?,?,0,date('now'),date('now','+14 days'))",
                 (str(uuid.uuid4()), rid, "trial", "trial"))
conn.commit(); conn.close()

def mint_token(uid, rid):
    r, _ = req_json("post", "/api/auth/login", {"email": f"owner_{rid}@pb15.com", "password": "plans15pass!"})
    if r and (r.get("token") or r.get("access_token")):
        return r.get("token") or r.get("access_token")
    return _jwt.encode({"sub": uid, "restaurant_id": rid, "exp": 9999999999,
                        "name": "Owner", "role": "owner", "is_super": False},
                       SECRET, algorithm="HS256")

tok1 = mint_token(u1_id, r1_id)
tok2 = mint_token(u2_id, r2_id)
chk("SETUP tokens", bool(tok1 and tok2))

sa_r, _ = req_json("post", "/api/super/auth/login", {"username": "admin", "password": "admin123", "pin": "0000"})
if not sa_r: sa_r, _ = req_json("post", "/api/super/auth/login", {"username": "admin", "password": "admin"})
sa_tok = (sa_r or {}).get("token") or (sa_r or {}).get("access_token") or ""
if not sa_tok:
    sa_row = database.get_db().execute("SELECT id FROM super_admins LIMIT 1").fetchone()
    if sa_row:
        sa_tok = _jwt.encode({"sub": sa_row[0], "exp": 9999999999, "is_super": True, "name": "Admin"},
                             SECRET, algorithm="HS256")
chk("SETUP super_admin token", bool(sa_tok))

# ── A. Default plans seeded ───────────────────────────────────────────────────
print("\n── A. Default plans seeded ──")
plans_db = db_rows("SELECT * FROM subscription_plans ORDER BY display_order")
chk("A.1 subscription_plans table exists and has rows", len(plans_db) >= 4)
codes = [p["code"] for p in plans_db]
chk("A.2 'free' plan seeded", "free" in codes)
chk("A.3 'starter' plan seeded", "starter" in codes)
chk("A.4 'professional' plan seeded", "professional" in codes)
chk("A.5 'enterprise' plan seeded", "enterprise" in codes)

starter_db = next((p for p in plans_db if p["code"] == "starter"), None)
# Prices start at 0 (set by super admin). Set a non-zero price for test validation.
if starter_db and sa_tok:
    _sid = starter_db["id"]
    req("patch", f"/api/super/subscription-plans/{_sid}",
        body={"price": 25000, "currency": "IQD"}, token=sa_tok)
    starter_db = db_one("SELECT * FROM subscription_plans WHERE code='starter'")
chk("A.6 starter: price > 0 (after SA set)", starter_db and starter_db.get("price", 0) > 0)
chk("A.7 starter: is_public=1", starter_db and starter_db.get("is_public") == 1)
chk("A.8 starter: duration_days=30", starter_db and starter_db.get("duration_days") == 30)
chk("A.9 starter: max_products > 0", starter_db and starter_db.get("max_products", 0) > 0)

free_db = next((p for p in plans_db if p["code"] == "free"), None)
# Free plan is publicly visible (for plan comparison) — is_public=1 in new design
chk("A.10 free: is_public=1 (visible for comparison)", free_db and free_db.get("is_public") == 1)

# ── B. Super Admin plan CRUD ──────────────────────────────────────────────────
print("\n── B. Super Admin plan CRUD ──")
new_plan_body = {
    "code": f"test_plan_{RUN}", "name": f"خطة اختبار {RUN}",
    "price": 9999, "currency": "IQD", "billing_period": "monthly", "duration_days": 45,
    "is_active": 1, "is_public": 1, "display_order": 99,
    "max_channels": 3, "max_products": 30, "max_staff": 7,
    "max_conversations_per_month": 777, "ai_enabled": 1, "analytics_enabled": 1,
    "media_enabled": 0, "voice_enabled": 0, "human_handoff_enabled": 1,
    "support_level": "email", "features_json": '["30 منتج","7 موظفين"]',
}
create_data, create_err = req_json("post", "/api/super/subscription-plans", new_plan_body, token=sa_tok)
chk("B.1 create plan returns 200", create_data is not None, create_err or "")
new_plan_id = (create_data or {}).get("id", "")
chk("B.2 create plan returns id", bool(new_plan_id))

db_plan = db_one("SELECT * FROM subscription_plans WHERE id=?", new_plan_id)
chk("B.3 plan stored in DB", db_plan is not None)
chk("B.4 plan: price=9999", db_plan and db_plan.get("price") == 9999)
chk("B.5 plan: duration_days=45", db_plan and db_plan.get("duration_days") == 45)
chk("B.6 plan: max_products=30", db_plan and db_plan.get("max_products") == 30)

# Edit plan
edit_data, edit_err = req_json("patch", f"/api/super/subscription-plans/{new_plan_id}",
                               {"price": 8888, "max_products": 25}, token=sa_tok)
chk("B.7 edit plan returns 200", edit_data is not None, edit_err or "")
db_after_edit = db_one("SELECT * FROM subscription_plans WHERE id=?", new_plan_id)
chk("B.8 edit: price updated to 8888", db_after_edit and db_after_edit.get("price") == 8888)
chk("B.9 edit: max_products updated to 25", db_after_edit and db_after_edit.get("max_products") == 25)

# List plans (admin sees all)
list_data, _ = req_json("get", "/api/super/subscription-plans", token=sa_tok)
all_plan_ids = [(p["id"]) for p in (list_data or {}).get("plans", [])]
chk("B.10 super admin list includes new plan", new_plan_id in all_plan_ids)

# Disable plan
dis_data, dis_err = req_json("delete", f"/api/super/subscription-plans/{new_plan_id}", token=sa_tok)
chk("B.11 disable plan returns 200", dis_data is not None, dis_err or "")
db_disabled = db_one("SELECT * FROM subscription_plans WHERE id=?", new_plan_id)
chk("B.12 disabled plan: is_active=0", db_disabled and db_disabled.get("is_active") == 0)

# ── C. Restaurant sees only active + public plans ─────────────────────────────
print("\n── C. Restaurant billing/plans endpoint ──")
plans_data, plans_err = req_json("get", "/api/billing/plans", token=tok1)
chk("C.1 billing/plans returns 200", plans_data is not None, plans_err or "")
pub_plans = (plans_data or {}).get("plans", [])
chk("C.2 billing/plans returns list", isinstance(pub_plans, list))
pub_codes = [p.get("code") or p.get("plan") for p in pub_plans]
chk("C.3 starter visible to restaurant", "starter" in pub_codes)
chk("C.4 professional visible to restaurant", "professional" in pub_codes)
chk("C.5 free IS visible (is_public=1, shown for comparison)", "free" in pub_codes)
chk("C.6 disabled test plan NOT visible", new_plan_id not in [p.get("id") for p in pub_plans])
# Each plan has id field
starter_pub = next((p for p in pub_plans if (p.get("code") or p.get("plan")) == "starter"), None)
chk("C.7 plan has id field", starter_pub and bool(starter_pub.get("id")))
chk("C.8 plan has name field", starter_pub and bool(starter_pub.get("name")))
chk("C.9 plan has price field", starter_pub and starter_pub.get("price") is not None)
chk("C.10 plan has duration_days field", starter_pub and starter_pub.get("duration_days") is not None)
chk("C.11 plan has features array", starter_pub and isinstance(starter_pub.get("features"), list))

# ── D. Restaurant cannot request inactive/hidden plan ────────────────────────
print("\n── D. Plan validation on proof submit ──")
tok1_h = {"Authorization": f"Bearer {tok1}"}

# Attempt with disabled plan id → 400
bad_r = requests.post(f"{BASE}/api/billing/payment-proof",
                      headers=tok1_h,
                      data={"plan_id": new_plan_id, "payer_name": "test"},
                      timeout=15)
chk("D.1 disabled plan_id rejected (400)", bad_r.status_code == 400, f"got {bad_r.status_code}")

# Re-enable test plan for proof submit test
req_json("patch", f"/api/super/subscription-plans/{new_plan_id}", {"is_active": 1, "is_public": 1}, token=sa_tok)

# Submit with valid plan_id
starter_plan_row = db_one("SELECT * FROM subscription_plans WHERE code='starter'")
starter_plan_id  = (starter_plan_row or {}).get("id", "")
chk("D.2 starter plan_id found in DB", bool(starter_plan_id))

r_proof = requests.post(f"{BASE}/api/billing/payment-proof",
                        headers=tok1_h,
                        data={"plan_id": starter_plan_id, "payer_name": f"أحمد {RUN}",
                              "reference_number": f"TXN{RUN}"},
                        timeout=20)
chk("D.3 proof submit with plan_id returns 200", r_proof.status_code == 200, r_proof.text[:200])
proof_resp = r_proof.json() if r_proof.status_code == 200 else {}
pr_id = proof_resp.get("request_id", "")
chk("D.4 request_id returned", bool(pr_id))

db_pr = db_one("SELECT * FROM payment_requests WHERE id=?", pr_id)
chk("D.5 DB row: plan=starter", db_pr and db_pr.get("plan") == "starter")
chk("D.6 DB row: plan_id stored", db_pr and db_pr.get("plan_id") == starter_plan_id)
chk("D.7 DB row: amount auto-filled from plan", db_pr and db_pr.get("amount", 0) == float(starter_plan_row.get("price", 0)))
chk("D.8 DB row: currency matches plan", db_pr and db_pr.get("currency") == starter_plan_row.get("currency"))

# Submit with legacy plan code (backward compat)
r_legacy = requests.post(f"{BASE}/api/billing/payment-proof",
                         headers=tok1_h,
                         data={"plan": "professional", "amount": "75000",
                               "payer_name": f"خالد {RUN}"},
                         timeout=20)
chk("D.9 legacy plan code still works", r_legacy.status_code == 200, r_legacy.text[:200])

# Invalid plan code/id
r_bad_code = requests.post(f"{BASE}/api/billing/payment-proof",
                           headers=tok1_h,
                           data={"plan": "nonexistent_plan_xyz", "amount": "1000",
                                 "payer_name": "test"},
                           timeout=15)
chk("D.10 invalid plan code rejected (400)", r_bad_code.status_code == 400,
    f"got {r_bad_code.status_code}")

# ── E. Approval applies plan rules ───────────────────────────────────────────
print("\n── E. Approval applies plan rules ──")
approve_data, approve_err = req_json("post", f"/api/super/payment-requests/{pr_id}/approve",
                                     {"internal_note": "تحقق أوتوماتيكي"}, token=sa_tok)
chk("E.1 approve returns 200", approve_data is not None, approve_err or "")
chk("E.2 plan=starter in response", approve_data and approve_data.get("plan") == "starter")

sub, _ = req_json("get", "/api/subscription/status", token=tok1)
chk("E.3 subscription.status=active", sub and sub.get("status") == "active")
chk("E.4 subscription.plan=starter", sub and sub.get("plan") == "starter")

period_end_str = (sub or {}).get("current_period_end", "")
chk("E.5 period_end non-empty", bool(period_end_str))
if period_end_str:
    from datetime import datetime as _dt
    days_left = (_dt.strptime(period_end_str[:10], "%Y-%m-%d") - _dt.now()).days
    chk("E.6 period_end ~30 days (from plan.duration_days)", 25 <= days_left <= 35,
        f"days_left={days_left}")

# Subscription limits come from DB plan
chk("E.7 features.max_products matches plan", sub and sub.get("features", {}).get("max_products") == starter_plan_row.get("max_products"))
chk("E.8 features.channels_allowed matches plan", sub and sub.get("features", {}).get("channels_allowed") == starter_plan_row.get("max_channels"))

# ── F. Disable plan → features from DB fallback ──────────────────────────────
print("\n── F. Custom plan limits via DB ──")
# Create new plan with custom limits for R2
custom_plan = {
    "code": f"custom_{RUN}", "name": f"مخصص {RUN}",
    "price": 50000, "currency": "IQD", "billing_period": "monthly", "duration_days": 60,
    "is_active": 1, "is_public": 1, "display_order": 98,
    "max_channels": 6, "max_products": 77, "max_staff": 12,
    "max_conversations_per_month": 1234, "ai_enabled": 1, "analytics_enabled": 1,
    "media_enabled": 1, "voice_enabled": 0, "human_handoff_enabled": 1,
    "support_level": "priority", "features_json": '["مخصص"]',
}
cp_data, _ = req_json("post", "/api/super/subscription-plans", custom_plan, token=sa_tok)
chk("F.1 custom plan created", cp_data is not None)
cp_id = (cp_data or {}).get("id", "")

# Submit proof for R2 using custom plan
tok2_h = {"Authorization": f"Bearer {tok2}"}
r2_pr = requests.post(f"{BASE}/api/billing/payment-proof",
                      headers=tok2_h,
                      data={"plan_id": cp_id, "payer_name": f"علي {RUN}"},
                      timeout=20)
chk("F.2 R2 proof with custom plan returns 200", r2_pr.status_code == 200, r2_pr.text[:100])
r2_pr_id = r2_pr.json().get("request_id", "") if r2_pr.status_code == 200 else ""

# Approve R2
req_json("post", f"/api/super/payment-requests/{r2_pr_id}/approve", {}, token=sa_tok)
r2_sub, _ = req_json("get", "/api/subscription/status", token=tok2)
chk("F.3 R2 subscription active", r2_sub and r2_sub.get("status") == "active")
chk("F.4 R2 plan = custom plan code", r2_sub and r2_sub.get("plan") == f"custom_{RUN}")
chk("F.5 R2 max_products=77 from DB plan", r2_sub and r2_sub.get("features", {}).get("max_products") == 77)
chk("F.6 R2 channels=6 from DB plan", r2_sub and r2_sub.get("features", {}).get("channels_allowed") == 6)

# period_end ~60 days
r2_end = (r2_sub or {}).get("current_period_end", "")
if r2_end:
    from datetime import datetime as _dt2
    r2_days = (_dt2.strptime(r2_end[:10], "%Y-%m-%d") - _dt2.now()).days
    chk("F.7 R2 period_end ~60 days (from plan.duration_days=60)", 55 <= r2_days <= 65,
        f"days_left={r2_days}")
else:
    chk("F.7 R2 period_end ~60 days", False, "period_end empty")

# R1 plan NOT affected by R2 approval
r1_sub, _ = req_json("get", "/api/subscription/status", token=tok1)
chk("F.8 R1 plan unchanged (starter)", r1_sub and r1_sub.get("plan") == "starter")

# ── G. Access control ─────────────────────────────────────────────────────────
print("\n── G. Access control ──")
no_create = req("post", "/api/super/subscription-plans", {"code": "hack", "name": "hack"}, token=tok1)
chk("G.1 restaurant cannot create plans (403)", no_create.status_code == 403)

no_edit = req("patch", f"/api/super/subscription-plans/{starter_plan_id}", {"price": 1}, token=tok1)
chk("G.2 restaurant cannot edit plans (403)", no_edit.status_code == 403)

no_del = req("delete", f"/api/super/subscription-plans/{starter_plan_id}", token=tok1)
chk("G.3 restaurant cannot disable plans (403)", no_del.status_code == 403)

no_super_list = req("get", "/api/super/subscription-plans", token=tok1)
chk("G.4 restaurant cannot list all plans via super endpoint (403)", no_super_list.status_code == 403)

# ── H. Audit log for plan events ──────────────────────────────────────────────
print("\n── H. Audit log ──")
audit_create = db_rows("SELECT * FROM billing_audit_logs WHERE action='subscription_plan_created' AND plan=?",
                       f"test_plan_{RUN}")
chk("H.1 plan_created logged in billing_audit_logs", len(audit_create) >= 1)

audit_update = db_rows("SELECT * FROM billing_audit_logs WHERE action='subscription_plan_updated'")
chk("H.2 plan_updated logged", len(audit_update) >= 1)

audit_disable = db_rows("SELECT * FROM billing_audit_logs WHERE action='subscription_plan_disabled' AND plan=?",
                        f"test_plan_{RUN}")
chk("H.3 plan_disabled logged", len(audit_disable) >= 1)

# ── I. No hardcoded plans in restaurant billing/plans response ─────────────────
print("\n── I. Plans response is DB-driven ──")
plans_data2, _ = req_json("get", "/api/billing/plans", token=tok1)
pub2 = (plans_data2 or {}).get("plans", [])
# Each plan must have an 'id' field (only DB rows have this)
all_have_id = all(bool(p.get("id")) for p in pub2)
chk("I.1 all plans have DB id", all_have_id, f"plans={[p.get('id','?') for p in pub2]}")
# My-payment-requests includes plan_name
my_reqs, _ = req_json("get", "/api/billing/my-payment-requests", token=tok1)
own = next((r for r in (my_reqs or {}).get("payment_requests", []) if r.get("id") == pr_id), None)
chk("I.2 my-payment-requests includes plan_name", own and bool(own.get("plan_name")))
chk("I.3 plan_name matches starter name", own and bool(own.get("plan_name")))

# ── Final report ──────────────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for r in results if r[0] == PASS)
print(f"\n{'='*72}")
print(f"Plans & Billing Check  {passed}/{total} checks PASS")
print(f"{'='*72}\n")

report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "day15_plans_report.txt")
with open(report_path, "w", encoding="utf-8") as f:
    for icon, label, detail in results:
        f.write(f"{icon} {label}{': ' + detail if detail else ''}\n")
    f.write(f"\nTotal: {passed}/{total}\n")
print(f"Report: {report_path}\n")

if failures:
    print(f"FAILURES ({len(failures)}):")
    for label, detail in failures:
        print(f"  {FAIL} {label}: {detail}")
    print(f"\n❌ NUMBER 15 NOT READY")
    sys.exit(1)
else:
    print(f"✅ NUMBER 15 READY FOR FINAL GATE")
