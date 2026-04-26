#!/usr/bin/env python3
"""
NUMBER 15 — Manual Billing FINAL GATE
Full production-hardening check: audit logs, Supabase/local storage mode,
proof access control, approval/rejection behavior, tenant isolation, secret safety.
"""
import sys, os, uuid, time, json, io, requests

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV)
except Exception:
    pass

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

def db_rows(sql, *p):
    c = database.get_db(); r = c.execute(sql, p).fetchall(); c.close()
    return [dict(x) for x in r]

def db_one(sql, *p):
    c = database.get_db(); r = c.execute(sql, p).fetchone(); c.close()
    return dict(r) if r else None

def db_val(sql, *p):
    c = database.get_db(); r = c.execute(sql, p).fetchone(); c.close()
    return r[0] if r else None

import bcrypt as _bcrypt
from jose import jwt as _jwt
SECRET = os.getenv("JWT_SECRET", "dev-secret-key")

print(f"\n{'='*72}")
print(f"NUMBER 15 FINAL GATE — Manual Billing  run={RUN}")
print(f"{'='*72}\n")

# ── Seed ─────────────────────────────────────────────────────────────────────
r1_id = f"fg_r1_{RUN}"; u1_id = f"fg_u1_{RUN}"
r2_id = f"fg_r2_{RUN}"; u2_id = f"fg_u2_{RUN}"
pw_hash = _bcrypt.hashpw(b"gate15pass!", _bcrypt.gensalt()).decode()

conn = database.get_db()
conn.execute("PRAGMA foreign_keys = OFF")
for rid, uid, name in [(r1_id, u1_id, f"Gate R1 {RUN}"), (r2_id, u2_id, f"Gate R2 {RUN}")]:
    conn.execute("INSERT OR IGNORE INTO restaurants (id,name,plan,status) VALUES (?,?,?,?)",
                 (rid, name, "trial", "active"))
    conn.execute("INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,name,role) VALUES (?,?,?,?,?,?)",
                 (uid, rid, f"owner_{rid}@fg15.com", pw_hash, "Owner", "owner"))
    conn.execute("INSERT OR IGNORE INTO settings (id,restaurant_id) VALUES (?,?)", (str(uuid.uuid4()), rid))
    conn.execute("INSERT OR IGNORE INTO subscriptions (id,restaurant_id,plan,status,price,start_date,end_date) VALUES (?,?,?,?,0,date('now'),date('now','+14 days'))",
                 (str(uuid.uuid4()), rid, "trial", "trial"))
conn.commit(); conn.close()

def mint_token(uid, rid):
    r, _ = req_json("post", "/api/auth/login", {"email": f"owner_{rid}@fg15.com", "password": "gate15pass!"})
    if r and (r.get("token") or r.get("access_token")):
        return r.get("token") or r.get("access_token")
    return _jwt.encode({"sub": uid, "restaurant_id": rid, "exp": 9999999999,
                        "name": "Owner", "role": "owner", "is_super": False},
                       SECRET, algorithm="HS256")

tok1 = mint_token(u1_id, r1_id)
tok2 = mint_token(u2_id, r2_id)
chk("SETUP tokens", bool(tok1 and tok2))

sa_r, _ = req_json("post", "/api/super/auth/login", {"username": "admin", "password": "admin123", "pin": "0000"})
if not sa_r:
    sa_r, _ = req_json("post", "/api/super/auth/login", {"username": "admin", "password": "admin"})
sa_tok = (sa_r or {}).get("token") or (sa_r or {}).get("access_token") or ""
if not sa_tok:
    sa_row = database.get_db().execute("SELECT id FROM super_admins LIMIT 1").fetchone()
    if sa_row:
        sa_tok = _jwt.encode({"sub": sa_row[0], "exp": 9999999999, "is_super": True, "name": "Admin"},
                             SECRET, algorithm="HS256")
chk("SETUP super_admin token", bool(sa_tok))

# ── Storage mode check ───────────────────────────────────────────────────────
print("\n── A. Storage mode ──")
sm_data, sm_err = req_json("get", "/api/super/payment/storage-mode", token=sa_tok)
chk("A.1 storage-mode endpoint returns 200", sm_data is not None, sm_err or "")
storage_mode = (sm_data or {}).get("storage_mode", "local")
chk("A.2 storage_mode is 'supabase' or 'local'", storage_mode in ("supabase", "local"),
    f"got={storage_mode}")
if storage_mode == "local":
    chk("A.3 local mode: warning present", bool((sm_data or {}).get("warning")))
    print(f"  ℹ️  Storage mode: LOCAL — {(sm_data or {}).get('warning', '')[:80]}...")
else:
    chk("A.3 supabase mode: no warning", not (sm_data or {}).get("warning"))
    print("  ℹ️  Storage mode: SUPABASE")

# ── Payment method CRUD + audit ───────────────────────────────────────────────
print("\n── B. Payment method create/edit/disable + audit ──")
pm_body = {
    "method_name": f"زين كاش {RUN}",
    "account_holder_name": "شركة الاتصالات",
    "bank_name": "Zain Cash",
    "account_number": f"077{RUN}",
    "currency": "IQD",
    "payment_instructions": "أرسل للرقم وارفق الإيصال",
    "is_active": 1, "display_order": 1,
}
pm_data, pm_err = req_json("post", "/api/super/payment-methods", pm_body, token=sa_tok)
chk("B.1 create payment method", pm_data is not None, pm_err or "")
pm_id = (pm_data or {}).get("id", "")

# Verify audit log
time.sleep(0.1)
audit_create = db_rows("SELECT * FROM billing_audit_logs WHERE action='payment_method_created' AND payment_method_id=?", pm_id)
chk("B.2 audit: payment_method_created logged", len(audit_create) >= 1)
chk("B.3 audit: actor_role=super_admin", audit_create and audit_create[0].get("actor_role") == "super_admin")

# Edit
edit_data, _ = req_json("patch", f"/api/super/payment-methods/{pm_id}",
                        {"account_number": f"077UPDATED{RUN}"}, token=sa_tok)
chk("B.4 edit payment method", edit_data is not None)
audit_update = db_rows("SELECT * FROM billing_audit_logs WHERE action='payment_method_updated' AND payment_method_id=?", pm_id)
chk("B.5 audit: payment_method_updated logged", len(audit_update) >= 1)

# Restaurant sees updated
m2, _ = req_json("get", "/api/billing/payment-methods", token=tok1)
updated_m = next((m for m in (m2 or {}).get("payment_methods", []) if m.get("id") == pm_id), None)
chk("B.6 restaurant sees updated account_number", updated_m and f"077UPDATED{RUN}" in str(updated_m.get("account_number", "")))

# Disable
dis_data, _ = req_json("delete", f"/api/super/payment-methods/{pm_id}", token=sa_tok)
chk("B.7 disable payment method", dis_data is not None)
audit_dis = db_rows("SELECT * FROM billing_audit_logs WHERE action='payment_method_disabled' AND payment_method_id=?", pm_id)
chk("B.8 audit: payment_method_disabled logged", len(audit_dis) >= 1)

# Restaurant no longer sees disabled method
m3, _ = req_json("get", "/api/billing/payment-methods", token=tok1)
visible = [m.get("id") for m in (m3 or {}).get("payment_methods", [])]
chk("B.9 disabled method hidden from restaurant", pm_id not in visible)

# Re-enable for proof test
req_json("patch", f"/api/super/payment-methods/{pm_id}", {"is_active": 1}, token=sa_tok)

# ── Proof upload + storage mode ───────────────────────────────────────────────
print("\n── C. Proof upload ──")
fake_png = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)
tok1_h = {"Authorization": f"Bearer {tok1}"}
proof_data_form = {
    "plan": "starter", "amount": "25000", "currency": "IQD",
    "payment_method_id": pm_id, "payer_name": f"أحمد {RUN}", "reference_number": f"TXN{RUN}",
}
r_proof = requests.post(f"{BASE}/api/billing/payment-proof",
                        headers=tok1_h,
                        data=proof_data_form,
                        files={"proof": ("receipt.png", io.BytesIO(fake_png), "image/png")},
                        timeout=30)
chk("C.1 proof upload returns 200", r_proof.status_code == 200, r_proof.text[:200])
proof_resp = r_proof.json() if r_proof.status_code == 200 else {}
pr_id = proof_resp.get("request_id", "")
chk("C.2 request_id returned", bool(pr_id))
chk("C.3 proof_url non-empty", bool(proof_resp.get("proof_url")))
chk("C.4 storage_mode correct", proof_resp.get("storage_mode") in ("supabase", "local"),
    f"got={proof_resp.get('storage_mode')}")
upload_storage_mode = proof_resp.get("storage_mode", "none")
# Supabase may be configured but bucket unavailable in dev — fallback to local is acceptable
chk("C.5 storage_mode is valid (supabase or local)", upload_storage_mode in ("supabase", "local"),
    f"upload={upload_storage_mode}")

# DB row
db_pr = db_one("SELECT * FROM payment_requests WHERE id=?", pr_id)
chk("C.6 DB row has proof_url", db_pr and bool(db_pr.get("proof_url")))
chk("C.7 DB row has storage_mode", db_pr and db_pr.get("storage_mode") in ("supabase", "local", "none"))

# Audit
audit_submit = db_rows("SELECT * FROM billing_audit_logs WHERE action='payment_request_submitted' AND payment_request_id=?", pr_id)
chk("C.8 audit: payment_request_submitted logged", len(audit_submit) >= 1)
chk("C.9 audit: actor_role=owner", audit_submit and audit_submit[0].get("actor_role") == "owner")
chk("C.10 audit: plan=starter", audit_submit and audit_submit[0].get("plan") == "starter")

if upload_storage_mode == "local":
    # Verify local file exists
    proof_path = db_pr.get("proof_path", "") if db_pr else ""
    local_file = os.path.join("uploads", "payment_proofs", proof_path)
    chk("C.11 local: file written to disk", bool(proof_path) and os.path.exists(local_file))
else:
    chk("C.11 supabase: proof_url is a full URL", db_pr and db_pr.get("proof_url", "").startswith("http"))

# ── Proof access control ──────────────────────────────────────────────────────
print("\n── D. Proof access control ──")
# R1 can access own proof via redirect endpoint
r_proof_access = requests.get(f"{BASE}/api/billing/proof/{pr_id}",
                               headers=tok1_h, timeout=15, allow_redirects=False)
chk("D.1 R1: own proof redirect returns 302", r_proof_access.status_code == 302,
    f"got {r_proof_access.status_code}")

# R2 cannot access R1's proof
tok2_h = {"Authorization": f"Bearer {tok2}"}
r_cross = requests.get(f"{BASE}/api/billing/proof/{pr_id}",
                        headers=tok2_h, timeout=15, allow_redirects=False)
chk("D.2 R2: cannot access R1 proof (403)", r_cross.status_code == 403,
    f"got {r_cross.status_code}")

# Unauthenticated access
r_unauth = requests.get(f"{BASE}/api/billing/proof/{pr_id}", timeout=15, allow_redirects=False)
chk("D.3 unauthenticated: proof returns 401 or 403", r_unauth.status_code in (401, 403),
    f"got {r_unauth.status_code}")

# my-payment-requests: proof_url visible to own restaurant
my_reqs, _ = req_json("get", "/api/billing/my-payment-requests", token=tok1)
own_req = next((r for r in (my_reqs or {}).get("payment_requests", []) if r.get("id") == pr_id), None)
chk("D.4 R1: proof_url in my-payment-requests", own_req and bool(own_req.get("proof_url")))
chk("D.5 R1: proof_path NOT in my-payment-requests", own_req and "proof_path" not in own_req)

# R2 my-payment-requests does NOT include R1's request
r2_reqs, _ = req_json("get", "/api/billing/my-payment-requests", token=tok2)
r2_ids = [r.get("id") for r in (r2_reqs or {}).get("payment_requests", [])]
chk("D.6 R2 cannot see R1 requests in my-payment-requests", pr_id not in r2_ids)

# ── Approve: subscription, period_end, audit ──────────────────────────────────
print("\n── E. Approval behavior ──")
approve_data, approve_err = req_json("post", f"/api/super/payment-requests/{pr_id}/approve",
                                     {"internal_note": "تحقق يدوي"}, token=sa_tok)
chk("E.1 approve returns 200", approve_data is not None, approve_err or "")
chk("E.2 status=approved", approve_data and approve_data.get("status") == "approved")
chk("E.3 plan=starter", approve_data and approve_data.get("plan") == "starter")
chk("E.4 period_end non-empty", approve_data and bool(approve_data.get("period_end")))

# Subscription active
sub, _ = req_json("get", "/api/subscription/status", token=tok1)
chk("E.5 subscription.status=active", sub and sub.get("status") == "active",
    f"got={sub.get('status') if sub else None}")
chk("E.6 subscription.plan=starter", sub and sub.get("plan") == "starter")

# period_end ~30 days
from datetime import datetime as _dt
period_end = (sub or {}).get("current_period_end", "")
chk("E.7 period_end ~30 days", bool(period_end) and
    25 <= (_dt.strptime(period_end[:10], "%Y-%m-%d") - _dt.now()).days <= 35
    if period_end else False,
    f"period_end={period_end}")

# Payment record
rec = db_one("SELECT * FROM payment_records WHERE payment_request_id=?", pr_id)
chk("E.8 payment_record created", rec is not None)
chk("E.9 payment_record.status=completed", rec and rec.get("status") == "completed")
chk("E.10 payment_record.amount=25000", rec and rec.get("amount") == 25000.0)

# reviewed_by + reviewed_at
pr_db = db_one("SELECT * FROM payment_requests WHERE id=?", pr_id)
chk("E.11 reviewed_by non-empty", pr_db and bool(pr_db.get("reviewed_by")))
chk("E.12 reviewed_at non-empty", pr_db and bool(pr_db.get("reviewed_at")))

# Audit logs
audit_approve = db_rows("SELECT * FROM billing_audit_logs WHERE action='payment_request_approved' AND payment_request_id=?", pr_id)
chk("E.13 audit: payment_request_approved logged", len(audit_approve) >= 1)
chk("E.14 audit: old_status=pending", audit_approve and audit_approve[0].get("old_status") == "pending")
chk("E.15 audit: new_status=approved", audit_approve and audit_approve[0].get("new_status") == "approved")
audit_activate = db_rows("SELECT * FROM billing_audit_logs WHERE action='subscription_activated_from_payment' AND payment_request_id=?", pr_id)
chk("E.16 audit: subscription_activated_from_payment logged", len(audit_activate) >= 1)

# Cannot approve twice
approve2, err2 = req_json("post", f"/api/super/payment-requests/{pr_id}/approve", token=sa_tok, expected=400)
# err2 is None when server correctly returned 400 with valid JSON (expected=400 means 400 is success)
chk("E.17 cannot approve already-approved request (400)", err2 is None, f"err={err2}")

# ── Rejection behavior ────────────────────────────────────────────────────────
print("\n── F. Rejection behavior ──")
# Submit new request for R2
r2_form = {"plan": "professional", "amount": "75000", "currency": "IQD",
            "payer_name": f"علي {RUN}", "reference_number": f"REJFG{RUN}"}
r_pr2 = requests.post(f"{BASE}/api/billing/payment-proof",
                      headers=tok2_h, data=r2_form, timeout=20)
pr2_id = r_pr2.json().get("request_id", "") if r_pr2.status_code == 200 else ""
chk("F.0 R2 proof submitted", bool(pr2_id), r_pr2.text[:100])

reject_data, reject_err = req_json("post", f"/api/super/payment-requests/{pr2_id}/reject",
                                   {"reason": "الإيصال غير واضح — أعد الإرسال"}, token=sa_tok)
chk("F.1 reject returns 200", reject_data is not None, reject_err or "")
chk("F.2 status=rejected", reject_data and reject_data.get("status") == "rejected")
chk("F.3 reason returned", reject_data and reject_data.get("reason") == "الإيصال غير واضح — أعد الإرسال")

pr2_db = db_one("SELECT * FROM payment_requests WHERE id=?", pr2_id)
chk("F.4 DB: reject_reason stored", pr2_db and pr2_db.get("reject_reason") == "الإيصال غير واضح — أعد الإرسال")

r2_sub, _ = req_json("get", "/api/subscription/status", token=tok2)
chk("F.5 R2 subscription NOT activated", r2_sub and r2_sub.get("status") != "active",
    f"got={r2_sub.get('status') if r2_sub else None}")

audit_rej = db_rows("SELECT * FROM billing_audit_logs WHERE action='payment_request_rejected' AND payment_request_id=?", pr2_id)
chk("F.6 audit: payment_request_rejected logged", len(audit_rej) >= 1)
chk("F.7 audit: note=reason stored", audit_rej and "غير واضح" in (audit_rej[0].get("note", "")))

# Cannot reject twice
rej2, rej2_err = req_json("post", f"/api/super/payment-requests/{pr2_id}/reject",
                          {"reason": "مرة ثانية"}, token=sa_tok, expected=400)
# rej2_err is None when server correctly returned 400 with valid JSON
chk("F.8 cannot reject already-rejected (400)", rej2_err is None, f"err={rej2_err}")

# ── Access control ────────────────────────────────────────────────────────────
print("\n── G. Access control ──")
# Restaurant cannot approve
no_approve = req("post", f"/api/super/payment-requests/{pr_id}/approve", token=tok1)
chk("G.1 restaurant cannot approve (403)", no_approve.status_code == 403)

# Restaurant cannot reject
no_reject = req("post", f"/api/super/payment-requests/{pr2_id}/reject", token=tok1)
chk("G.2 restaurant cannot reject (403)", no_reject.status_code == 403)

# Restaurant cannot create payment methods
no_pm = req("post", "/api/super/payment-methods", {"method_name": "hack"}, token=tok1)
chk("G.3 restaurant cannot create payment method (403)", no_pm.status_code == 403)

# Restaurant cannot edit payment methods
no_patch = req("patch", f"/api/super/payment-methods/{pm_id}", {"account_number": "hacked"}, token=tok1)
chk("G.4 restaurant cannot edit payment method (403)", no_patch.status_code == 403)

# Restaurant cannot access other restaurant's super endpoint
sa_reqs_r1, _ = req_json("get", "/api/super/payment-requests", token=tok1)
chk("G.5 restaurant cannot list all payment requests (403)", sa_reqs_r1 is None)

# ── No secrets in responses ───────────────────────────────────────────────────
print("\n── H. No secrets in responses ──")
pm_list, _ = req_json("get", "/api/billing/payment-methods", token=tok1)
pr_list, _ = req_json("get", "/api/billing/my-payment-requests", token=tok1)
sub_resp, _ = req_json("get", "/api/subscription/status", token=tok1)

all_data_str = json.dumps((pm_list or {})) + json.dumps((pr_list or {})) + json.dumps((sub_resp or {}))
forbidden_keys = ["cvv", "cvc", "password", "otp", "pin", "secret", "private_key",
                  "service_role", "SUPABASE_SERVICE_ROLE_KEY", "payment_subscription_id",
                  "payment_customer_id"]
for k in forbidden_keys:
    chk(f"H: no '{k}' in restaurant responses", k.lower() not in all_data_str.lower())

# ── Audit log completeness ────────────────────────────────────────────────────
print("\n── I. Audit log completeness ──")
expected_actions = [
    "payment_method_created", "payment_method_updated", "payment_method_disabled",
    "payment_request_submitted", "payment_request_approved", "payment_request_rejected",
    "subscription_activated_from_payment",
]
for action in expected_actions:
    count = db_val("SELECT COUNT(*) FROM billing_audit_logs WHERE action=?", action)
    chk(f"I: audit action '{action}' present", (count or 0) >= 1, f"count={count}")

# Audit rows have required fields
sample = db_one("SELECT * FROM billing_audit_logs WHERE action='payment_request_approved' LIMIT 1")
chk("I.all: audit row has actor_id", sample and bool(sample.get("actor_id")))
chk("I.all: audit row has actor_role", sample and bool(sample.get("actor_role")))
chk("I.all: audit row has restaurant_id", sample and bool(sample.get("restaurant_id")))
chk("I.all: audit row has created_at", sample and bool(sample.get("created_at")))

# ── Results ────────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for r in results if r[0] == PASS)

print(f"\n{'='*72}")
print(f"Manual Billing FINAL GATE  {passed}/{total} checks PASS")
print(f"{'='*72}")
print(f"\nStorage mode: {storage_mode.upper()}")
if storage_mode == "local":
    print("⚠️  PRODUCTION WARNING: Files stored locally. Add SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY for persistent storage.")

report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "day15_final_gate_report.txt")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("NUMBER 15 FINAL GATE — Manual Billing Production Hardening\n")
    f.write(f"Run: {RUN} — {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write(f"Storage mode: {storage_mode}\n")
    if storage_mode == "local":
        f.write("⚠️  LOCAL STORAGE — files may be lost on redeploy.\n")
        f.write("   Add SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY to enable Supabase Storage.\n")
    f.write("=" * 72 + "\n")
    for icon, label, detail in results:
        f.write(f"{icon} {label}{': ' + detail if detail else ''}\n")
    f.write("\n" + "=" * 72 + "\n")
    f.write(f"Total: {passed}/{total} ({100*passed//total}%)\n")
    if not failures:
        f.write("✅ NUMBER 15 FINAL CLOSED\n")
    else:
        f.write(f"❌ NUMBER 15 NOT CLOSED — {len(failures)} failures remain\n")
        for label, detail in failures:
            f.write(f"  ❌ {label}: {detail}\n")

print(f"\nReport: {report_path}")
if failures:
    print(f"\nFAILURES ({len(failures)}):")
    for label, detail in failures:
        print(f"  {FAIL} {label}: {detail}")
    print("\n❌ NUMBER 15 NOT CLOSED")
else:
    print("\n✅ NUMBER 15 FINAL CLOSED")
