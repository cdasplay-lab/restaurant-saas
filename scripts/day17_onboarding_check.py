#!/usr/bin/env python3
"""
NUMBER 17 — Restaurant Onboarding Flow & SA Approval Workflow
20 tests covering: fresh restaurant state, profile completion, payment flow,
SA approval/rejection, menu, channel status, bot test, launch readiness,
access control, and no fake data.
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

def make_owner_token(restaurant_id, user_id=None):
    if user_id:
        row = db_one("SELECT * FROM users WHERE id=?", user_id)
    else:
        row = db_one("SELECT * FROM users WHERE restaurant_id=? AND role='owner' LIMIT 1", restaurant_id)
    if not row: return None
    return _jwt.encode({"sub": row["id"], "restaurant_id": restaurant_id, "role": "owner", "is_super": False}, SECRET, algorithm="HS256")

def ensure_restaurant(name, phone="", address="", plan="trial"):
    rid = f"r_ob17_{RUN}_{name}"
    c = database.get_db()
    c.execute("INSERT OR IGNORE INTO restaurants (id,name,phone,address,plan,status) VALUES (?,?,?,?,?,?)",
              (rid, f"Test {name} {RUN}", phone, address, plan, "active"))
    c.commit()
    uid = f"u_ob17_{RUN}_{name}"
    pw  = _bcrypt.hashpw(b"pass123", _bcrypt.gensalt()).decode()
    c.execute("INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,role,name) VALUES (?,?,?,?,?,?)",
              (uid, rid, f"ob17_{RUN}_{name}@test.com", pw, "owner", f"Owner {name}"))
    c.commit(); c.close()
    return rid, uid

print(f"\n{'='*72}")
print(f"Onboarding Check  — {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*72}\n")

database.init_db()

sa_tok = make_sa_token()
if not sa_tok:
    print("ERROR: No super admin found"); sys.exit(1)

# Fresh restaurant — no profile, no menu, no channel, no payment
r1, u1 = ensure_restaurant("Fresh", phone="", address="")
tok1 = make_owner_token(r1, u1)

# Restaurant with complete profile
r2, u2 = ensure_restaurant("Complete", phone="07901234567", address="Baghdad")
tok2 = make_owner_token(r2, u2)

# Restaurant with products
r3, u3 = ensure_restaurant("WithMenu", phone="07901234567", address="Mosul")
tok3 = make_owner_token(r3, u3)
c = database.get_db()
c.execute("INSERT OR IGNORE INTO products (id,restaurant_id,name,price,available) VALUES (?,?,?,?,?)",
          (f"prod17_{RUN}", r3, "برغر", 5000, 1))
c.commit(); c.close()

# Restaurant for payment flow
r4, u4 = ensure_restaurant("PayFlow", phone="07901234567", address="Erbil")
tok4 = make_owner_token(r4, u4)

# ── 1. Fresh restaurant: GET /api/onboarding/status returns correct keys ──────
print("── 1. Schema check ──")
d, e = req_json("get", "/api/onboarding/status", token=tok1)
chk("1.1 GET /api/onboarding/status returns 200", d is not None, e or "")
chk("1.2 response has required top-level keys",
    d is not None and all(k in d for k in ("restaurant_id","overall_status","progress_percent","steps","launch_ready","payment_review","subscription")),
    str(list(d.keys())) if d else "")
chk("1.3 steps list has 8 items", d is not None and len(d.get("steps", [])) == 8, f"count={len(d.get('steps',[])) if d else 0}")
step_keys = {s["key"] for s in d.get("steps", [])} if d else set()
chk("1.4 all expected step keys present",
    step_keys == {"profile","plan","payment","approval","menu","channels","bot_test","launch"},
    str(step_keys))

# ── 2. Fresh restaurant: overall_status not_ready ────────────────────────────
print("── 2. Fresh restaurant state ──")
chk("2.1 fresh restaurant: launch_ready=False", d is not None and not d.get("launch_ready"), "")
chk("2.2 fresh restaurant: overall_status not_ready or almost_ready",
    d is not None and d.get("overall_status") in ("not_ready", "almost_ready"), str(d.get("overall_status") if d else ""))

# ── 3. Profile step ───────────────────────────────────────────────────────────
print("── 3. Profile step ──")
d_fresh, _ = req_json("get", "/api/onboarding/status", token=tok1)
d_comp, _  = req_json("get", "/api/onboarding/status", token=tok2)
step_fresh_profile = next((s for s in d_fresh.get("steps", []) if s["key"] == "profile"), {}) if d_fresh else {}
step_comp_profile  = next((s for s in d_comp.get("steps", []) if s["key"] == "profile"), {}) if d_comp else {}
chk("3.1 fresh (no phone/address): profile step incomplete", step_fresh_profile.get("status") == "incomplete", str(step_fresh_profile))
chk("3.2 with phone+address: profile step complete", step_comp_profile.get("status") == "complete", str(step_comp_profile))
chk("3.3 profile_complete field accurate", d_comp is not None and d_comp.get("profile_complete") == True, "")

# ── 4. Menu step ──────────────────────────────────────────────────────────────
print("── 4. Menu step ──")
d_nomenu, _ = req_json("get", "/api/onboarding/status", token=tok1)
d_menu, _   = req_json("get", "/api/onboarding/status", token=tok3)
step_nomenu = next((s for s in d_nomenu.get("steps",[]) if s["key"] == "menu"), {}) if d_nomenu else {}
step_menu   = next((s for s in d_menu.get("steps",[]) if s["key"] == "menu"), {}) if d_menu else {}
chk("4.1 no products: menu step incomplete", step_nomenu.get("status") == "incomplete", str(step_nomenu))
chk("4.2 with product: menu step complete", step_menu.get("status") == "complete", str(step_menu))
chk("4.3 products_count accurate",
    d_menu is not None and d_menu.get("products_count", 0) >= 1,
    f"count={d_menu.get('products_count') if d_menu else 0}")

# ── 5. Payment + Approval steps ───────────────────────────────────────────────
print("── 5. Payment flow ──")
d_pre, _ = req_json("get", "/api/onboarding/status", token=tok4)
pay_step  = next((s for s in d_pre.get("steps",[]) if s["key"] == "payment"), {}) if d_pre else {}
chk("5.1 no payment proof: payment step not_submitted", pay_step.get("status") == "not_submitted", str(pay_step))

# Ensure a payment method exists, then submit proof
c = database.get_db()
pm_id = f"pm17_{RUN}"
c.execute("INSERT OR IGNORE INTO payment_methods (id,method_name,currency,is_active) VALUES (?,?,?,?)",
          (pm_id, "تحويل بنكي", "IQD", 1))
c.commit(); c.close()

# Submit payment proof via API (endpoint uses Form fields, not JSON)
try:
    _r = requests.post(
        f"{BASE}/api/billing/payment-proof",
        data={"plan": "starter", "amount": 25000, "currency": "IQD",
              "payment_method_id": pm_id, "payer_name": f"Test {RUN}",
              "reference_number": f"REF{RUN}"},
        headers={"Authorization": f"Bearer {tok4}"},
        timeout=20
    )
    if _r.status_code in (200, 201):
        pr_resp = _r.json()
        pr_err = None
    else:
        pr_resp = None
        pr_err = f"HTTP {_r.status_code}: {_r.text[:300]}"
except Exception as _e:
    pr_resp = None
    pr_err = str(_e)
chk("5.2 submit payment proof returns 200/201", pr_resp is not None, pr_err or "")

d_pending, _ = req_json("get", "/api/onboarding/status", token=tok4)
pay_step_p = next((s for s in d_pending.get("steps",[]) if s["key"] == "payment"), {}) if d_pending else {}
app_step_p = next((s for s in d_pending.get("steps",[]) if s["key"] == "approval"), {}) if d_pending else {}
chk("5.3 after proof submission: payment step pending", pay_step_p.get("status") == "pending", str(pay_step_p))
chk("5.4 after proof submission: approval step pending_review", app_step_p.get("status") == "pending_review", str(app_step_p))
chk("5.5 payment_review.status = pending", d_pending is not None and d_pending.get("payment_review",{}).get("status") == "pending", "")

# ── 6. Super Admin Approval ───────────────────────────────────────────────────
print("── 6. SA Approval ──")
pr_id = (pr_resp or {}).get("id") or db_val(
    "SELECT id FROM payment_requests WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1", r4)
chk("6.1 payment_request_id obtainable", bool(pr_id), f"pr_id={pr_id}")

if pr_id:
    appr, e_appr = req_json("post", f"/api/super/payment-requests/{pr_id}/approve",
                             body={"internal_note": "Test approval"}, token=sa_tok)
    chk("6.2 SA approve returns ok=True", appr is not None and appr.get("ok"), e_appr or str(appr))

    d_appr, _ = req_json("get", "/api/onboarding/status", token=tok4)
    pay_step_a = next((s for s in d_appr.get("steps",[]) if s["key"] == "payment"), {}) if d_appr else {}
    app_step_a = next((s for s in d_appr.get("steps",[]) if s["key"] == "approval"), {}) if d_appr else {}
    chk("6.3 after SA approval: payment step approved", pay_step_a.get("status") == "approved", str(pay_step_a))
    chk("6.4 after SA approval: approval step approved", app_step_a.get("status") == "approved", str(app_step_a))
    chk("6.5 after SA approval: subscription.status=active",
        d_appr is not None and d_appr.get("subscription",{}).get("status") == "active", str(d_appr.get("subscription") if d_appr else ""))

# ── 7. SA Rejection ───────────────────────────────────────────────────────────
print("── 7. SA Rejection ──")
# Create a new payment request for r1 and reject it
c = database.get_db()
pr2_id = str(uuid.uuid4())
c.execute("INSERT INTO payment_requests (id,restaurant_id,plan,amount,currency,status) VALUES (?,?,?,?,?,?)",
          (pr2_id, r1, "starter", 25000, "IQD", "pending"))
c.commit(); c.close()

rej, e_rej = req_json("post", f"/api/super/payment-requests/{pr2_id}/reject",
                       body={"reason": "الصورة غير واضحة"}, token=sa_tok)
chk("7.1 SA reject returns ok=True", rej is not None and rej.get("ok"), e_rej or str(rej))

d_rej, _ = req_json("get", "/api/onboarding/status", token=tok1)
pay_step_r = next((s for s in d_rej.get("steps",[]) if s["key"] == "payment"), {}) if d_rej else {}
app_step_r = next((s for s in d_rej.get("steps",[]) if s["key"] == "approval"), {}) if d_rej else {}
chk("7.2 after SA rejection: payment step rejected", pay_step_r.get("status") == "rejected", str(pay_step_r))
chk("7.3 after SA rejection: approval step rejected", app_step_r.get("status") == "rejected", str(app_step_r))
chk("7.4 reject_reason propagated to payment_review",
    (d_rej or {}).get("payment_review",{}).get("reject_reason") == "الصورة غير واضحة",
    str(d_rej.get("payment_review") if d_rej else ""))
chk("7.5 subscription NOT activated after rejection",
    (d_rej or {}).get("subscription",{}).get("status") not in ("active",),
    str(d_rej.get("subscription") if d_rej else ""))

# ── 8. Channel step ───────────────────────────────────────────────────────────
print("── 8. Channel step ──")
d_nch, _ = req_json("get", "/api/onboarding/status", token=tok1)
ch_step_nch = next((s for s in d_nch.get("steps",[]) if s["key"] == "channels"), {}) if d_nch else {}
chk("8.1 no connected channels: channel step incomplete", ch_step_nch.get("status") == "incomplete", str(ch_step_nch))

# Add a "connected" channel directly in DB
c = database.get_db()
c.execute("INSERT OR IGNORE INTO channels (id,restaurant_id,type,token,enabled,connection_status) VALUES (?,?,?,?,?,?)",
          (f"ch17_{RUN}", r3, "telegram", "fake_token_12345", 1, "ok"))
c.commit(); c.close()

d_ch, _ = req_json("get", "/api/onboarding/status", token=tok3)
ch_step = next((s for s in d_ch.get("steps",[]) if s["key"] == "channels"), {}) if d_ch else {}
chk("8.2 with connected channel: channel step complete", ch_step.get("status") == "complete", str(ch_step))
chk("8.3 connected_channels_count >= 1", (d_ch or {}).get("connected_channels_count", 0) >= 1, "")

# ── 9. Bot test endpoint ──────────────────────────────────────────────────────
print("── 9. Bot test ──")
d_bt, e_bt = req_json("post", "/api/onboarding/test-bot", body={}, token=tok1)
chk("9.1 POST /api/onboarding/test-bot returns 200", d_bt is not None, e_bt or "")
chk("9.2 response has status field", d_bt is not None and "status" in d_bt, str(d_bt))
chk("9.3 bot test status is pass or fail (not error)", d_bt is not None and d_bt.get("status") in ("pass","fail"), str(d_bt))

# After bot test, onboarding status reflects it
d_bt_status, _ = req_json("get", "/api/onboarding/status", token=tok1)
bot_step = next((s for s in d_bt_status.get("steps",[]) if s["key"] == "bot_test"), {}) if d_bt_status else {}
chk("9.4 bot_test step status updated after test", bot_step.get("status") in ("pass","fail"), str(bot_step))

# ── 10. launch_ready logic ────────────────────────────────────────────────────
print("── 10. Launch readiness ──")
# r3 has: products ✓, channel ✓, profile ✓ — but on trial plan (no payment required for trial)
d_r3, _ = req_json("get", "/api/onboarding/status", token=tok3)
chk("10.1 launch_ready is boolean", d_r3 is not None and isinstance(d_r3.get("launch_ready"), bool), "")
# On trial with products + channel + profile → should be launch_ready
chk("10.2 trial restaurant with products+channel+profile is launch_ready",
    (d_r3 or {}).get("launch_ready") == True,
    f"d_r3 steps: {[(s['key'],s['status']) for s in d_r3.get('steps',[])]}") if d_r3 else chk("10.2 skip", False, "d_r3 is None")

# ── 11. launch step status ────────────────────────────────────────────────────
print("── 11. Launch step ──")
launch_step = next((s for s in (d_r3 or {}).get("steps",[]) if s["key"] == "launch"), {})
chk("11.1 launch step status=ready when launch_ready", launch_step.get("status") == "ready" if (d_r3 or {}).get("launch_ready") else launch_step.get("status") != "ready", str(launch_step))

# ── 12. Tenant isolation ──────────────────────────────────────────────────────
print("── 12. Tenant isolation ──")
d_r1, _ = req_json("get", "/api/onboarding/status", token=tok1)
d_r2, _ = req_json("get", "/api/onboarding/status", token=tok2)
chk("12.1 r1 sees only own restaurant_id", d_r1 is not None and d_r1.get("restaurant_id") == r1, str(d_r1.get("restaurant_id") if d_r1 else ""))
chk("12.2 r2 sees only own restaurant_id", d_r2 is not None and d_r2.get("restaurant_id") == r2, str(d_r2.get("restaurant_id") if d_r2 else ""))
chk("12.3 r1 products_count not inflated by r3 products", (d_r1 or {}).get("products_count",0) == 0, f"got {d_r1.get('products_count') if d_r1 else 'N/A'}")

# ── 13. Restaurant cannot approve own payment ─────────────────────────────────
print("── 13. Self-approval blocked ──")
pr3_id = str(uuid.uuid4())
c = database.get_db()
c.execute("INSERT INTO payment_requests (id,restaurant_id,plan,amount,currency,status) VALUES (?,?,?,?,?,?)",
          (pr3_id, r2, "starter", 25000, "IQD", "pending"))
c.commit(); c.close()
r_self = req("post", f"/api/super/payment-requests/{pr3_id}/approve", body={}, token=tok2)
chk("13.1 restaurant user cannot approve payment (401/403)", r_self.status_code in (401, 403), f"got {r_self.status_code}")

# ── 14. SA can see all restaurants ───────────────────────────────────────────
print("── 14. SA onboarding list ──")
d_sa, e_sa = req_json("get", "/api/super/onboarding/restaurants", token=sa_tok)
chk("14.1 SA GET /api/super/onboarding/restaurants returns 200", d_sa is not None, e_sa or "")
chk("14.2 response has restaurants list + total", d_sa is not None and "restaurants" in d_sa and "total" in d_sa, str(d_sa))
chk("14.3 total >= 4 (our test restaurants)", d_sa is not None and d_sa.get("total", 0) >= 4, f"total={d_sa.get('total') if d_sa else 0}")

# ── 15. SA onboarding detail ─────────────────────────────────────────────────
print("── 15. SA onboarding detail ──")
d_det, e_det = req_json("get", f"/api/super/onboarding/restaurants/{r3}", token=sa_tok)
chk("15.1 SA detail returns 200", d_det is not None, e_det or "")
chk("15.2 detail has restaurant_name", d_det is not None and bool(d_det.get("restaurant_name")), "")
chk("15.3 detail has steps list", d_det is not None and isinstance(d_det.get("steps"), list), "")
chk("15.4 detail products_count >= 1", d_det is not None and d_det.get("products_count", 0) >= 1, "")

# ── 16. SA filter: pending_payment ───────────────────────────────────────────
print("── 16. SA filters ──")
d_filt, _ = req_json("get", "/api/super/onboarding/restaurants?filter=pending_payment", token=sa_tok)
if d_filt:
    pending_rids = {r["restaurant_id"] for r in d_filt.get("restaurants", [])}
    chk("16.1 pending_payment filter returns only pending-pay restaurants",
        all(r["payment_status"] == "pending" for r in d_filt.get("restaurants", [])),
        str([r["payment_status"] for r in d_filt.get("restaurants",[])][:5]))
else:
    chk("16.1 pending_payment filter", False, "no response")

# ── 17. No access without auth ───────────────────────────────────────────────
print("── 17. Unauthenticated access ──")
r_unauth = req("get", "/api/onboarding/status")
chk("17.1 unauthenticated returns 401/403", r_unauth.status_code in (401, 403), f"got {r_unauth.status_code}")
r_sa_unauth = req("get", "/api/super/onboarding/restaurants")
chk("17.2 SA endpoint without token returns 401/403", r_sa_unauth.status_code in (401, 403), f"got {r_sa_unauth.status_code}")

# ── 18. Progress percent accuracy ────────────────────────────────────────────
print("── 18. Progress percent ──")
if d_r3:
    completed = sum(1 for s in d_r3.get("steps",[]) if s["status"] in ("complete","approved","pass","ready"))
    expected_pct = round(completed / 8 * 100)
    chk("18.1 progress_percent matches completed/total ratio",
        d_r3.get("progress_percent") == expected_pct,
        f"got={d_r3.get('progress_percent')}, expected={expected_pct}")
else:
    chk("18.1 skip", False, "d_r3 not available")

# ── 19. Onboarding reflects real payment approval (no static data) ────────────
print("── 19. Real-state verification ──")
# r4 was approved → verify subscription is now active in DB
sub_row = db_one("SELECT status FROM subscriptions WHERE restaurant_id=?", r4)
chk("19.1 approval updated subscriptions table (not fake)", sub_row is not None and sub_row.get("status") == "active", str(sub_row))
# r1 was rejected → subscription NOT active
sub_r1 = db_one("SELECT status FROM subscriptions WHERE restaurant_id=?", r1)
chk("19.2 rejection did not activate subscription",
    sub_r1 is None or sub_r1.get("status") not in ("active",),
    str(sub_r1))

# ── 20. SA detail for non-existent restaurant ────────────────────────────────
print("── 20. Edge cases ──")
r_404 = req("get", "/api/super/onboarding/restaurants/nonexistent-id", token=sa_tok)
chk("20.1 detail for non-existent restaurant returns 404", r_404.status_code == 404, f"got {r_404.status_code}")

# ── Summary ────────────────────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for r in results if r[0] == PASS)
print(f"\n{'='*72}")
print(f"Results: {passed}/{total} passed")
if failures:
    print(f"\nFailed checks:")
    for lbl, det in failures:
        print(f"  {FAIL} {lbl}: {det}")
else:
    print("All checks passed!")
print(f"{'='*72}\n")

sys.exit(0 if not failures else 1)
