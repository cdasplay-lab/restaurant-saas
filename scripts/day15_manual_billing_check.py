#!/usr/bin/env python3
"""
NUMBER 15 — Manual Billing, Payment Proof, and Super Admin Payment Settings
Test script covering all 15 required scenarios.
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
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
        try: return r.json(), None
        except Exception as e: return None, str(e)
    except Exception as e:
        return None, str(e)

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
print(f"NUMBER 15 — Manual Billing Check  run={RUN}")
print(f"{'='*72}\n")

# ── Seed restaurants ──────────────────────────────────────────────────────────
r1_id = f"b15_r1_{RUN}"; u1_id = f"b15_u1_{RUN}"
r2_id = f"b15_r2_{RUN}"; u2_id = f"b15_u2_{RUN}"
pw_hash = _bcrypt.hashpw(b"test15pass!", _bcrypt.gensalt()).decode()

conn = database.get_db()
conn.execute("PRAGMA foreign_keys = OFF")
for rid, uid, name in [(r1_id, u1_id, f"Billing R1 {RUN}"), (r2_id, u2_id, f"Billing R2 {RUN}")]:
    conn.execute("INSERT OR IGNORE INTO restaurants (id,name,plan,status) VALUES (?,?,?,?)",
                 (rid, name, "trial", "active"))
    conn.execute("INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,name,role) VALUES (?,?,?,?,?,?)",
                 (uid, rid, f"owner_{rid}@b15.com", pw_hash, "Owner", "owner"))
    conn.execute("INSERT OR IGNORE INTO settings (id,restaurant_id) VALUES (?,?)", (str(uuid.uuid4()), rid))
    conn.execute("INSERT OR IGNORE INTO subscriptions (id,restaurant_id,plan,status,price,start_date,end_date) VALUES (?,?,?,?,0,date('now'),date('now','+14 days'))",
                 (str(uuid.uuid4()), rid, "trial", "trial"))
conn.commit(); conn.close()

def mint_token(uid, rid):
    r, _ = req_json("post", "/api/auth/login", {"email": f"owner_{rid}@b15.com", "password": "test15pass!"})
    if r and (r.get("token") or r.get("access_token")):
        return r.get("token") or r.get("access_token")
    return _jwt.encode({"sub": uid, "restaurant_id": rid, "exp": 9999999999,
                        "name": "Owner", "role": "owner", "is_super": False},
                       SECRET, algorithm="HS256")

tok1 = mint_token(u1_id, r1_id)
tok2 = mint_token(u2_id, r2_id)
chk("SETUP R1/R2 tokens minted", bool(tok1 and tok2))

# Super admin token
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

# ── 1. Super Admin creates payment method ─────────────────────────────────────
print("\n── 1. Super Admin creates payment method ──")
pm_body = {
    "method_name": f"تحويل بنكي {RUN}",
    "account_holder_name": "شركة الاتصالات",
    "bank_name": "البنك الأهلي العراقي",
    "account_number": "IQ12345678901234",
    "iban": "IQ98NBIQ000000000000001234",
    "phone_number": "07700000000",
    "currency": "IQD",
    "payment_instructions": "حوّل المبلغ وارفق الإيصال",
    "is_active": 1,
    "display_order": 1,
}
pm_data, pm_err = req_json("post", "/api/super/payment-methods", pm_body, token=sa_tok)
chk("1.1 Super: create payment method returns 200", pm_data is not None, pm_err or "")
pm_id = (pm_data or {}).get("id", "")
chk("1.2 Super: returned id", bool(pm_id))
chk("1.3 Super: method_name matches", pm_data and pm_data.get("method_name") == pm_body["method_name"])
db_pm = db_one("SELECT * FROM payment_methods WHERE id=?", pm_id)
chk("1.4 Super: payment_method in DB", db_pm is not None)

# ── 2. Restaurant sees active payment method ──────────────────────────────────
print("\n── 2. Restaurant sees active payment method ──")
methods_data, methods_err = req_json("get", "/api/billing/payment-methods", token=tok1)
chk("2.1 Restaurant: /api/billing/payment-methods returns 200", methods_data is not None, methods_err or "")
methods = (methods_data or {}).get("payment_methods", [])
our_method = next((m for m in methods if m.get("id") == pm_id), None)
chk("2.2 Restaurant: sees the new payment method", our_method is not None)
chk("2.3 Restaurant: no CVV/password fields in response",
    all(k not in str(methods) for k in ["cvv", "password", "pin", "otp"]))

# ── 3. Super Admin edits payment method, restaurant sees updated details ──────
print("\n── 3. Super Admin edits method, restaurant sees update ──")
edit_body = {"account_number": "IQ_UPDATED_9999", "bank_name": "بنك التحديث"}
edit_data, edit_err = req_json("patch", f"/api/super/payment-methods/{pm_id}", edit_body, token=sa_tok)
chk("3.1 Super: PATCH payment method returns 200", edit_data is not None, edit_err or "")
chk("3.2 Super: account_number updated in response", edit_data and edit_data.get("account_number") == "IQ_UPDATED_9999")
# Restaurant re-fetches and sees updated
methods2, _ = req_json("get", "/api/billing/payment-methods", token=tok1)
updated_m = next((m for m in (methods2 or {}).get("payment_methods", []) if m.get("id") == pm_id), None)
chk("3.3 Restaurant: sees updated account_number", updated_m and updated_m.get("account_number") == "IQ_UPDATED_9999")

# ── 4. Restaurant submits payment proof ───────────────────────────────────────
print("\n── 4. Restaurant submits payment proof ──")
tok_h = {"Authorization": f"Bearer {tok1}"}
# With a fake PNG (1x1 pixel)
fake_png = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)
files = {"proof": ("receipt.png", io.BytesIO(fake_png), "image/png")}
data = {
    "plan": "starter",
    "amount": "25000",
    "currency": "IQD",
    "payment_method_id": pm_id,
    "payer_name": f"أحمد محمد {RUN}",
    "reference_number": f"TXN{RUN}",
}
r_proof = requests.post(f"{BASE}/api/billing/payment-proof", headers=tok_h,
                        data=data, files=files, timeout=20)
chk("4.1 Restaurant: POST /api/billing/payment-proof returns 200",
    r_proof.status_code == 200, f"got {r_proof.status_code}: {r_proof.text[:200]}")
proof_data = r_proof.json() if r_proof.status_code == 200 else {}
pr_id = proof_data.get("request_id", "")
chk("4.2 Restaurant: returns request_id", bool(pr_id))
chk("4.3 Restaurant: status=pending", proof_data.get("status") == "pending")
chk("4.4 Restaurant: proof_url points to uploads", "/uploads/payment_proofs/" in proof_data.get("proof_url", ""))
db_pr = db_one("SELECT * FROM payment_requests WHERE id=?", pr_id)
chk("4.5 DB: payment_request stored", db_pr is not None)
chk("4.6 DB: proof_path non-empty", db_pr and bool(db_pr.get("proof_path")))
# Verify file actually exists
if db_pr and db_pr.get("proof_path"):
    proof_file = os.path.join("uploads", "payment_proofs", db_pr["proof_path"])
    chk("4.7 File: proof file written to disk", os.path.exists(proof_file))
else:
    chk("4.7 File: proof file written to disk", False, "proof_path empty")

# ── 5. Restaurant cannot approve its own payment ──────────────────────────────
print("\n── 5. Restaurant cannot approve own payment ──")
no_approve = req("post", f"/api/super/payment-requests/{pr_id}/approve", token=tok1)
chk("5.1 Restaurant: approve via super endpoint returns 403",
    no_approve.status_code == 403, f"got {no_approve.status_code}")

# ── 6. Super Admin sees the request ───────────────────────────────────────────
print("\n── 6. Super Admin sees the request ──")
sa_reqs, sa_reqs_err = req_json("get", "/api/super/payment-requests", token=sa_tok)
chk("6.1 Super: /api/super/payment-requests returns 200", sa_reqs is not None, sa_reqs_err or "")
all_reqs = (sa_reqs or {}).get("payment_requests", [])
our_req = next((r for r in all_reqs if r.get("id") == pr_id), None)
chk("6.2 Super: our request appears in list", our_req is not None)
chk("6.3 Super: proof_url present", our_req and bool(our_req.get("proof_url")))
chk("6.4 Super: restaurant_name present", our_req and bool(our_req.get("restaurant_name")))

# Filter by pending
pending_reqs, _ = req_json("get", "/api/super/payment-requests?status=pending", token=sa_tok)
pending_ids = [(r.get("id")) for r in (pending_reqs or {}).get("payment_requests", [])]
chk("6.5 Super: request appears in pending filter", pr_id in pending_ids)

# ── 7. Super Admin approves the request ───────────────────────────────────────
print("\n── 7. Super Admin approves the request ──")
approve_data, approve_err = req_json("post", f"/api/super/payment-requests/{pr_id}/approve",
                                     {"internal_note": "دفعة مؤكدة"}, token=sa_tok)
chk("7.1 Super: approve returns 200", approve_data is not None, approve_err or "")
chk("7.2 Super: status=approved in response", approve_data and approve_data.get("status") == "approved")
chk("7.3 Super: plan in response", approve_data and approve_data.get("plan") == "starter")

# ── 8. Restaurant subscription becomes active ────────────────────────────────
print("\n── 8. Subscription activated after approval ──")
sub_data, sub_err = req_json("get", "/api/subscription/status", token=tok1)
chk("8.1 Subscription: /api/subscription/status returns 200", sub_data is not None, sub_err or "")
chk("8.2 Subscription: status=active after approve", sub_data and sub_data.get("status") == "active",
    f"got={sub_data.get('status') if sub_data else None}")
chk("8.3 Subscription: plan=starter after approve", sub_data and sub_data.get("plan") == "starter",
    f"got={sub_data.get('plan') if sub_data else None}")

# ── 9. current_period_end extends 30 days ────────────────────────────────────
print("\n── 9. current_period_end is ~30 days from now ──")
period_end = (sub_data or {}).get("current_period_end", "")
chk("9.1 Subscription: current_period_end non-empty", bool(period_end))
if period_end:
    from datetime import datetime as _dt
    try:
        end_dt = _dt.strptime(period_end[:10], "%Y-%m-%d")
        days_left = (end_dt - _dt.now()).days
        chk("9.2 Subscription: period_end is 25-35 days away", 25 <= days_left <= 35,
            f"days_left={days_left}")
    except Exception as e:
        chk("9.2 Subscription: period_end is 25-35 days away", False, str(e))
else:
    chk("9.2 Subscription: period_end is 25-35 days away", False, "empty period_end")

# ── 10. Payment record created ────────────────────────────────────────────────
print("\n── 10. Payment record created ──")
pay_rec = db_one("SELECT * FROM payment_records WHERE payment_request_id=?", pr_id)
chk("10.1 DB: payment_record created", pay_rec is not None)
chk("10.2 DB: payment_record.plan=starter", pay_rec and pay_rec.get("plan") == "starter")
chk("10.3 DB: payment_record.status=completed", pay_rec and pay_rec.get("status") == "completed")
chk("10.4 DB: payment_record.amount=25000", pay_rec and pay_rec.get("amount") == 25000.0)

# ── 11. Rejected request: reason stored, subscription not activated ───────────
print("\n── 11. Rejected request: reason stored, subscription unchanged ──")
# Submit new proof for R2
data2 = {"plan": "professional", "amount": "75000", "currency": "IQD",
          "payer_name": f"علي {RUN}", "reference_number": f"REJ{RUN}"}
r_proof2 = requests.post(f"{BASE}/api/billing/payment-proof",
                         headers={"Authorization": f"Bearer {tok2}"},
                         data=data2, timeout=20)
chk("11.0 R2: submit proof returns 200", r_proof2.status_code == 200, r_proof2.text[:100])
pr2_id = r_proof2.json().get("request_id", "") if r_proof2.status_code == 200 else ""

reject_data, reject_err = req_json("post", f"/api/super/payment-requests/{pr2_id}/reject",
                                   {"reason": "الإيصال غير واضح"}, token=sa_tok)
chk("11.1 Super: reject returns 200", reject_data is not None, reject_err or "")
chk("11.2 Super: status=rejected in response", reject_data and reject_data.get("status") == "rejected")
chk("11.3 Super: reason stored", reject_data and reject_data.get("reason") == "الإيصال غير واضح")

db_pr2 = db_one("SELECT * FROM payment_requests WHERE id=?", pr2_id)
chk("11.4 DB: reject_reason stored", db_pr2 and db_pr2.get("reject_reason") == "الإيصال غير واضح")

r2_sub, _ = req_json("get", "/api/subscription/status", token=tok2)
chk("11.5 R2: subscription NOT activated after rejection",
    r2_sub and r2_sub.get("status") != "active",
    f"got={r2_sub.get('status') if r2_sub else None}")

# ── 12. R1 cannot see R2 payment requests ────────────────────────────────────
print("\n── 12. R1 cannot see R2 payment requests ──")
my_reqs, _ = req_json("get", "/api/billing/my-payment-requests", token=tok1)
r1_req_ids = [r.get("id") for r in (my_reqs or {}).get("payment_requests", [])]
chk("12.1 R1: my-payment-requests only shows own requests", pr_id in r1_req_ids)
chk("12.2 R1: R2 request NOT in R1's list", pr2_id not in r1_req_ids,
    f"pr2_id={pr2_id} found in R1 list")

# ── 13. No CVV/OTP/password fields stored or returned ─────────────────────────
print("\n── 13. No CVV/OTP/password fields ──")
all_pm_str = json.dumps((methods_data or {}).get("payment_methods", []))
all_pr_str = json.dumps((sa_reqs or {}).get("payment_requests", []))
chk("13.1 payment_methods response: no CVV/password keys",
    all(k not in all_pm_str.lower() for k in ["cvv", "cvc", "password", "otp"]))
chk("13.2 payment_requests response: no CVV/password keys",
    all(k not in all_pr_str.lower() for k in ["cvv", "cvc", "password", "otp"]))

# ── 14. Inactive payment method does not appear to restaurants ────────────────
print("\n── 14. Inactive method hidden from restaurants ──")
# Disable the payment method
dis_data, dis_err = req_json("delete", f"/api/super/payment-methods/{pm_id}", token=sa_tok)
chk("14.1 Super: DELETE (disable) returns 200", dis_data is not None, dis_err or "")
methods3, _ = req_json("get", "/api/billing/payment-methods", token=tok1)
visible_ids = [m.get("id") for m in (methods3 or {}).get("payment_methods", [])]
chk("14.2 Restaurant: disabled method no longer visible", pm_id not in visible_ids,
    f"pm_id still in list: {pm_id in visible_ids}")
# Super admin still sees it
sa_pm_all, _ = req_json("get", "/api/super/payment-methods", token=sa_tok)
sa_all_ids = [m.get("id") for m in (sa_pm_all or {}).get("payment_methods", [])]
chk("14.3 Super: disabled method still in admin list", pm_id in sa_all_ids)

# ── 15. File type/size validation ─────────────────────────────────────────────
print("\n── 15. File type/size validation ──")
# Wrong extension: .exe
bad_file = {"proof": ("malware.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")}
r_bad = requests.post(f"{BASE}/api/billing/payment-proof",
                      headers=tok_h,
                      data={"plan": "starter", "amount": "1", "payer_name": "Test"},
                      files=bad_file, timeout=20)
chk("15.1 Bad extension (.exe) rejected with 400", r_bad.status_code == 400,
    f"got {r_bad.status_code}: {r_bad.text[:100]}")

# Oversized file (6MB)
big_file = {"proof": ("big.png", io.BytesIO(b"X" * (6 * 1024 * 1024)), "image/png")}
r_big = requests.post(f"{BASE}/api/billing/payment-proof",
                      headers=tok_h,
                      data={"plan": "starter", "amount": "1", "payer_name": "Test"},
                      files=big_file, timeout=60)
chk("15.2 Oversized file (6MB) rejected with 400", r_big.status_code == 400,
    f"got {r_big.status_code}: {r_big.text[:100]}")

# Valid PDF (tiny)
fake_pdf = b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj"
pdf_file = {"proof": ("receipt.pdf", io.BytesIO(fake_pdf), "application/pdf")}
r_pdf = requests.post(f"{BASE}/api/billing/payment-proof",
                      headers=tok_h,
                      data={"plan": "starter", "amount": "1", "payer_name": "PDF Test"},
                      files=pdf_file, timeout=20)
chk("15.3 Valid PDF accepted (200)", r_pdf.status_code == 200,
    f"got {r_pdf.status_code}: {r_pdf.text[:100]}")

# ── Results ────────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for r in results if r[0] == PASS)

print(f"\n{'='*72}")
print(f"Manual Billing Check  {passed}/{total} checks PASS")
print(f"{'='*72}")

report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "day15_manual_billing_report.txt")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("NUMBER 15 — Manual Billing, Payment Proof, and Super Admin Payment Settings\n")
    f.write(f"Run: {RUN} — {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write("=" * 72 + "\n")
    for icon, label, detail in results:
        f.write(f"{icon} {label}{': ' + detail if detail else ''}\n")
    f.write("\n" + "=" * 72 + "\n")
    f.write(f"Total: {passed}/{total} ({100*passed//total}%)\n")
    if not failures:
        f.write("✅ NUMBER 15 CLOSED\n")
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
    print("\n✅ NUMBER 15 CLOSED")
