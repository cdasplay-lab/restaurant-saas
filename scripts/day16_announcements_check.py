#!/usr/bin/env python3
"""
NUMBER 16 — In-App Announcements Check
20 tests covering: SA CRUD, validation, targeting (all/plan/status/restaurant),
date filtering, inactive filtering, dismissals, non-dismissible guard,
access control, cascade delete.
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

def make_owner_token(restaurant_id):
    row = db_one("SELECT * FROM users WHERE restaurant_id=? AND role='owner' LIMIT 1", restaurant_id)
    if not row: return None
    return _jwt.encode({"sub": row["id"], "restaurant_id": restaurant_id, "role": "owner", "is_super": False}, SECRET, algorithm="HS256")

def ensure_restaurant(name, plan="starter", status="active"):
    rid = f"r_ann_{RUN}_{name}"
    c = database.get_db()
    c.execute("INSERT OR IGNORE INTO restaurants (id,name,plan,status) VALUES (?,?,?,?)",
              (rid, f"Test {name} {RUN}", plan, status))
    c.commit()
    uid = f"u_ann_{RUN}_{name}"
    pw  = _bcrypt.hashpw(b"pass123", _bcrypt.gensalt()).decode()
    c.execute("""INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,role,name)
                 VALUES (?,?,?,?,?,?)""",
              (uid, rid, f"ann_{RUN}_{name}@test.com", pw, "owner", f"Owner {name}"))
    c.commit()
    c.close()
    return rid

print(f"\n{'='*72}")
print(f"Announcements Check  — {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*72}\n")

database.init_db()

sa_tok = make_sa_token()
if not sa_tok:
    print("ERROR: No super admin found — run server first"); sys.exit(1)

r1 = ensure_restaurant("A1", plan="starter", status="active")
r2 = ensure_restaurant("A2", plan="professional", status="active")
r3 = ensure_restaurant("A3", plan="starter", status="expired")
tok1 = make_owner_token(r1)
tok2 = make_owner_token(r2)
tok3 = make_owner_token(r3)

# track created IDs for cleanup
_created_ids = []

def sa_create(body):
    d, e = req_json("post", "/api/super/announcements", body=body, token=sa_tok, expected=201)
    if d and d.get("id"):
        _created_ids.append(d["id"])
    return d, e

# ── A. Super Admin: list (empty start) ───────────────────────────────────────
print("── A. SA list (initial) ──")
d, e = req_json("get", "/api/super/announcements", token=sa_tok)
chk("A.1 SA list returns 200 with announcements key", d is not None and "announcements" in d, e or "")

# ── B. SA Create ─────────────────────────────────────────────────────────────
print("── B. SA Create ──")
d, e = sa_create({
    "title": f"إعلان تجريبي {RUN}",
    "message": "رسالة الاختبار",
    "type": "info",
    "placement": "dashboard_top_banner",
    "priority": 5,
    "is_active": 1,
    "is_dismissible": 1,
    "target_all": 1,
})
chk("B.1 SA create announcement returns 201 with id", d is not None and bool(d.get("id")), e or str(d))
ann_id = d["id"] if d else None

chk("B.2 announcement persisted in DB", bool(ann_id and db_one("SELECT id FROM announcements WHERE id=?", ann_id)), "")

# ── C. Validation ─────────────────────────────────────────────────────────────
print("── C. Validation ──")
r = req("post", "/api/super/announcements", body={"title": "", "type": "info", "placement": "dashboard_top_banner"}, token=sa_tok)
chk("C.1 empty title rejected (400)", r.status_code == 400, f"got {r.status_code}")

r = req("post", "/api/super/announcements", body={"title": "test", "type": "unknown_type", "placement": "dashboard_top_banner"}, token=sa_tok)
chk("C.2 invalid type rejected (400)", r.status_code == 400, f"got {r.status_code}")

r = req("post", "/api/super/announcements", body={"title": "test", "type": "info", "placement": "invalid_place"}, token=sa_tok)
chk("C.3 invalid placement rejected (400)", r.status_code == 400, f"got {r.status_code}")

r = req("post", "/api/super/announcements", body={
    "title": "test", "type": "info", "placement": "dashboard_top_banner",
    "cta_url": "javascript:alert(1)"
}, token=sa_tok)
chk("C.4 unsafe CTA URL rejected (400)", r.status_code == 400, f"got {r.status_code}")

# safe CTA should work
d2, e2 = sa_create({"title": f"CTA safe {RUN}", "type": "info", "placement": "billing_page",
                     "cta_url": "https://example.com", "target_all": 1})
chk("C.5 safe https CTA URL accepted", d2 is not None, e2 or "")

d3, e3 = sa_create({"title": f"CTA relative {RUN}", "type": "info", "placement": "billing_page",
                     "cta_url": "/billing", "target_all": 1})
chk("C.6 relative CTA URL (/billing) accepted", d3 is not None, e3 or "")

# ── D. SA PATCH ───────────────────────────────────────────────────────────────
print("── D. SA PATCH ──")
if ann_id:
    d, e = req_json("patch", f"/api/super/announcements/{ann_id}",
                    body={"title": f"عنوان محدّث {RUN}", "priority": 9}, token=sa_tok)
    chk("D.1 PATCH returns ok=True", d is not None and d.get("ok"), e or str(d))
    updated = db_one("SELECT title, priority FROM announcements WHERE id=?", ann_id)
    chk("D.2 DB title updated", updated and f"محدّث {RUN}" in (updated.get("title") or ""), str(updated))
    chk("D.3 DB priority updated to 9", updated and updated.get("priority") == 9, str(updated))

r = req("patch", "/api/super/announcements/nonexistent-id", body={"title": "x"}, token=sa_tok)
chk("D.4 PATCH non-existent returns 404", r.status_code == 404, f"got {r.status_code}")

# ── E. Access control ─────────────────────────────────────────────────────────
print("── E. Access control ──")
r = req("get", "/api/super/announcements")
chk("E.1 SA list without token returns 401/403", r.status_code in (401, 403), f"got {r.status_code}")

r = req("post", "/api/super/announcements",
        body={"title": "hack", "type": "info", "placement": "dashboard_top_banner"},
        token=tok1)
chk("E.2 restaurant user cannot create SA announcement (401/403)", r.status_code in (401, 403), f"got {r.status_code}")

# ── F. Restaurant endpoint: target_all=1 visible to all ───────────────────────
print("── F. target_all visibility ──")
d, e = req_json("get", "/api/announcements", token=tok1)
chk("F.1 restaurant GET /api/announcements returns 200", d is not None, e or "")
anns = d.get("announcements", []) if d else []
# ann_id should be visible (target_all=1, is_active=1)
visible_ids = {a["id"] for a in anns}
chk("F.2 target_all=1 active announcement visible to restaurant", ann_id in visible_ids, f"ids={list(visible_ids)[:5]}")

# ── G. Inactive not shown to restaurant ───────────────────────────────────────
print("── G. Inactive hidden from restaurants ──")
d_inact, _ = sa_create({"title": f"Inactive {RUN}", "type": "warning",
                          "placement": "dashboard_top_banner", "is_active": 0, "target_all": 1})
inact_id = d_inact["id"] if d_inact else None
d, _ = req_json("get", "/api/announcements", token=tok1)
anns_r = d.get("announcements", []) if d else []
active_ids = {a["id"] for a in anns_r}
chk("G.1 is_active=0 announcement hidden from restaurant", inact_id not in active_ids, f"inact_id={inact_id}, visible={list(active_ids)[:5]}")
# SA can still see it
d_sa, _ = req_json("get", "/api/super/announcements", token=sa_tok)
sa_ids = {a["id"] for a in d_sa.get("announcements", [])} if d_sa else set()
chk("G.2 SA sees inactive announcement", inact_id in sa_ids, "")

# ── H. Target by plan ─────────────────────────────────────────────────────────
print("── H. Plan-targeted announcements ──")
d_plan, _ = sa_create({"title": f"Pro only {RUN}", "type": "upgrade",
                        "placement": "dashboard_top_banner", "is_active": 1,
                        "target_all": 0, "target_plans_json": '["professional"]'})
plan_ann_id = d_plan["id"] if d_plan else None

d1, _ = req_json("get", "/api/announcements", token=tok1)  # starter
d2, _ = req_json("get", "/api/announcements", token=tok2)  # professional
ids1 = {a["id"] for a in d1.get("announcements", [])} if d1 else set()
ids2 = {a["id"] for a in d2.get("announcements", [])} if d2 else set()
chk("H.1 professional-only ann hidden from starter restaurant", plan_ann_id not in ids1, f"plan_ann_id={plan_ann_id}, ids1={list(ids1)[:5]}")
chk("H.2 professional-only ann visible to professional restaurant", plan_ann_id in ids2, f"plan_ann_id={plan_ann_id}, ids2={list(ids2)[:5]}")

# ── I. Target by restaurant_id ────────────────────────────────────────────────
print("── I. Restaurant-ID-targeted announcements ──")
d_rid, _ = sa_create({"title": f"R2 only {RUN}", "type": "info",
                       "placement": "billing_page", "is_active": 1,
                       "target_all": 0, "target_restaurant_ids_json": json.dumps([r2])})
rid_ann_id = d_rid["id"] if d_rid else None

d1b, _ = req_json("get", "/api/announcements", token=tok1)
d2b, _ = req_json("get", "/api/announcements", token=tok2)
ids1b = {a["id"] for a in d1b.get("announcements", [])} if d1b else set()
ids2b = {a["id"] for a in d2b.get("announcements", [])} if d2b else set()
chk("I.1 restaurant-id-targeted ann hidden from other restaurant", rid_ann_id not in ids1b, "")
chk("I.2 restaurant-id-targeted ann visible to target restaurant", rid_ann_id in ids2b, "")

# ── J. Target by status ───────────────────────────────────────────────────────
print("── J. Status-targeted announcements ──")
d_st, _ = sa_create({"title": f"Expired only {RUN}", "type": "payment",
                      "placement": "billing_page", "is_active": 1,
                      "target_all": 0, "target_statuses_json": '["expired"]'})
st_ann_id = d_st["id"] if d_st else None

d1c, _ = req_json("get", "/api/announcements", token=tok1)  # active
d3c, _ = req_json("get", "/api/announcements", token=tok3)  # expired
ids1c = {a["id"] for a in d1c.get("announcements", [])} if d1c else set()
ids3c = {a["id"] for a in d3c.get("announcements", [])} if d3c else set()
chk("J.1 expired-status ann hidden from active restaurant", st_ann_id not in ids1c, "")
chk("J.2 expired-status ann visible to expired restaurant", st_ann_id in ids3c, "")

# ── K. Date filtering ─────────────────────────────────────────────────────────
print("── K. Date filtering ──")
from datetime import datetime, timedelta
past  = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
future= (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")

d_future, _ = sa_create({"title": f"Future start {RUN}", "type": "info",
                          "placement": "dashboard_top_banner", "is_active": 1,
                          "target_all": 1, "starts_at": future})
d_expired_date, _ = sa_create({"title": f"Past end {RUN}", "type": "info",
                                "placement": "dashboard_top_banner", "is_active": 1,
                                "target_all": 1, "ends_at": past})
future_id = d_future["id"] if d_future else None
expired_date_id = d_expired_date["id"] if d_expired_date else None

d_rest, _ = req_json("get", "/api/announcements", token=tok1)
rest_ids = {a["id"] for a in d_rest.get("announcements", [])} if d_rest else set()
chk("K.1 future-start announcement hidden from restaurant", future_id not in rest_ids, f"future_id={future_id}")
chk("K.2 past-end announcement hidden from restaurant", expired_date_id not in rest_ids, f"expired_date_id={expired_date_id}")

# ── L. Dismissals ─────────────────────────────────────────────────────────────
print("── L. Dismissals ──")
if ann_id:
    d_dis, e_dis = req_json("post", f"/api/announcements/{ann_id}/dismiss", body={}, token=tok1)
    chk("L.1 dismiss returns ok=True", d_dis is not None and d_dis.get("ok"), e_dis or str(d_dis))

    d_after, _ = req_json("get", "/api/announcements", token=tok1)
    after_ids = {a["id"] for a in d_after.get("announcements", [])} if d_after else set()
    chk("L.2 dismissed announcement no longer shown to dismissing user", ann_id not in after_ids, "")

    # another user in same restaurant still sees it
    uid2 = f"u_ann2_{RUN}_A1"
    c = database.get_db()
    pw2 = _bcrypt.hashpw(b"pass123", _bcrypt.gensalt()).decode()
    c.execute("INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,role,name) VALUES (?,?,?,?,?,?)",
              (uid2, r1, f"ann2_{RUN}@test.com", pw2, "staff", "Staff A1"))
    c.commit(); c.close()
    tok_staff = _jwt.encode({"sub": uid2, "restaurant_id": r1, "role": "staff", "is_super": False}, SECRET, algorithm="HS256")
    d_staff, _ = req_json("get", "/api/announcements", token=tok_staff)
    staff_ids = {a["id"] for a in d_staff.get("announcements", [])} if d_staff else set()
    chk("L.3 dismissal is per-user: other user still sees it", ann_id in staff_ids, f"staff_ids={list(staff_ids)[:5]}")

# ── M. Non-dismissible guard ──────────────────────────────────────────────────
print("── M. Non-dismissible ──")
d_nd, _ = sa_create({"title": f"Non-dismissible {RUN}", "type": "maintenance",
                      "placement": "dashboard_top_banner", "is_active": 1,
                      "target_all": 1, "is_dismissible": 0})
nd_id = d_nd["id"] if d_nd else None
if nd_id:
    r_nd = req("post", f"/api/announcements/{nd_id}/dismiss", body={}, token=tok1)
    chk("M.1 dismissing non-dismissible returns 403", r_nd.status_code == 403, f"got {r_nd.status_code}")

# ── N. SA Delete + cascade ────────────────────────────────────────────────────
print("── N. SA Delete ──")
d_del, _ = sa_create({"title": f"To delete {RUN}", "type": "info",
                       "placement": "modal_once", "is_active": 1, "target_all": 1})
del_id = d_del["id"] if d_del else None
if del_id:
    # create a dismissal record to verify cascade
    c = database.get_db()
    c.execute("INSERT OR IGNORE INTO announcement_dismissals (id,announcement_id,restaurant_id,user_id,dismissed_at) VALUES (?,?,?,?,?)",
              (str(uuid.uuid4()), del_id, r1, f"u_ann_{RUN}_A1", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit(); c.close()

    d_del_res, e_del = req_json("delete", f"/api/super/announcements/{del_id}", token=sa_tok)
    chk("N.1 SA delete returns ok=True", d_del_res is not None and d_del_res.get("ok"), e_del or str(d_del_res))
    chk("N.2 announcement removed from DB", db_one("SELECT id FROM announcements WHERE id=?", del_id) is None, "")
    dismissal_count = db_val("SELECT COUNT(*) FROM announcement_dismissals WHERE announcement_id=?", del_id)
    chk("N.3 dismissal records cascade-deleted", dismissal_count == 0, f"count={dismissal_count}")

r_404 = req("delete", "/api/super/announcements/nonexistent-id", token=sa_tok)
chk("N.4 delete non-existent returns 404", r_404.status_code == 404, f"got {r_404.status_code}")

# ── O. Restaurant cannot dismiss non-existent announcement ───────────────────
print("── O. Dismiss non-existent ──")
r_404d = req("post", "/api/announcements/nonexistent-id/dismiss", body={}, token=tok1)
chk("O.1 dismiss non-existent returns 404", r_404d.status_code == 404, f"got {r_404d.status_code}")

# ── Cleanup ────────────────────────────────────────────────────────────────────
for aid in _created_ids:
    try:
        req("delete", f"/api/super/announcements/{aid}", token=sa_tok)
    except Exception:
        pass

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
