#!/usr/bin/env python3
"""
NUMBER 12 — Super Admin Live Readiness & Channel Health
Test suite: access control, status honesty, no-secret leakage.
"""

import sys, os, uuid, time, json, requests

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

results  = []
failures = []

def chk(label, ok, detail=""):
    results.append((PASS if ok else FAIL, label, detail))
    if not ok:
        failures.append((label, detail))
        print(f"  {FAIL} {label}: {detail}")
    return ok

def req(method, path, body=None, token=None, expected=200):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = getattr(requests, method)(f"{BASE}{path}", json=body, headers=h, timeout=15)
    if r.status_code != expected:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    try:
        return r.json(), None
    except Exception as e:
        return None, str(e)

def req_code(method, path, token=None):
    """Return actual HTTP status code."""
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = getattr(requests, method)(f"{BASE}{path}", headers=h, timeout=15)
    return r.status_code

def db_exec(sql, *p):
    c = database.get_db(); c.execute(sql, p); c.commit(); c.close()

def db_one(sql, *p):
    c = database.get_db(); r = c.execute(sql, p).fetchone(); c.close()
    return dict(r) if r else None

import bcrypt as _bcrypt
from jose import jwt as _jwt
SECRET = os.getenv("JWT_SECRET", "dev-secret-key")
pw_hash = _bcrypt.hashpw(b"test12!", _bcrypt.gensalt()).decode()

print(f"\n{'='*72}")
print(f"NUMBER 12 — Live Readiness Check  run={RUN}")
print(f"{'='*72}\n")

# ── Seed 3 restaurants ─────────────────────────────────────────────────────────
# R1: has Telegram token (simulates fully configured Telegram)
# R2: no channels at all (missing credentials)
# R3: has whatsapp channel row but no token (pending Meta)

r1_id = f"rd1_{RUN}"; r2_id = f"rd2_{RUN}"; r3_id = f"rd3_{RUN}"
u1_id = f"ru1_{RUN}"; u2_id = f"ru2_{RUN}"; u3_id = f"ru3_{RUN}"

conn = database.get_db()
conn.execute("PRAGMA foreign_keys = OFF")
for rid, uid, name in [
    (r1_id, u1_id, f"مطعم الجاهزية 1 {RUN}"),
    (r2_id, u2_id, f"مطعم الجاهزية 2 {RUN}"),
    (r3_id, u3_id, f"مطعم الجاهزية 3 {RUN}"),
]:
    conn.execute("INSERT OR IGNORE INTO restaurants (id,name,plan,status) VALUES (?,?,'starter','active')", (rid, name))
    conn.execute("INSERT OR IGNORE INTO users (id,restaurant_id,email,password_hash,name,role) VALUES (?,?,?,?,?,?)",
                 (uid, rid, f"owner_{rid}@rd.com", pw_hash, f"Owner {rid}", "owner"))
    conn.execute("INSERT OR IGNORE INTO settings (id,restaurant_id) VALUES (?,?)", (str(uuid.uuid4()), rid))
    conn.execute("INSERT OR IGNORE INTO bot_config (id,restaurant_id,order_extraction_enabled,memory_enabled,max_bot_turns) VALUES (?,?,1,1,20)",
                 (str(uuid.uuid4()), rid))

# R1: Telegram channel with token
tg_ch_id = str(uuid.uuid4())
conn.execute("""INSERT OR IGNORE INTO channels (id,restaurant_id,type,name,token,enabled,connection_status)
               VALUES (?,?,'telegram','Telegram','fake_bot_token_r1',1,'connected')""",
             (tg_ch_id, r1_id))

# R3: WhatsApp row but no token (pending Meta)
wa_ch_id = str(uuid.uuid4())
conn.execute("""INSERT OR IGNORE INTO channels (id,restaurant_id,type,name,token,enabled,connection_status)
               VALUES (?,?,'whatsapp','WhatsApp','',1,'unknown')""",
             (wa_ch_id, r3_id))

# Seed an outbound failed message for R1 (tests last_error field)
conn.execute("""INSERT INTO outbound_messages (id,restaurant_id,conversation_id,platform,recipient_id,content,status,error)
               VALUES (?,?,?,?,?,?,?,?)""",
             (str(uuid.uuid4()), r1_id, '', 'telegram', '12345', 'test', 'failed', 'Telegram: 403 Forbidden'))

conn.commit(); conn.close()

def get_token(rid, uid, email):
    r, e = req("post", "/api/auth/login", {"email": email, "password": "test12!"})
    if r and (r.get("token") or r.get("access_token")):
        return r.get("token") or r.get("access_token")
    return _jwt.encode({"sub": uid, "restaurant_id": rid, "exp": 9999999999,
                        "name": "Owner", "role": "owner", "is_super": False}, SECRET, algorithm="HS256")

tok1 = get_token(r1_id, u1_id, f"owner_{r1_id}@rd.com")
tok2 = get_token(r2_id, u2_id, f"owner_{r2_id}@rd.com")
chk("SETUP R1 token", bool(tok1))
chk("SETUP R2 token", bool(tok2))

# Super admin token
sa_r, sa_e = req("post", "/api/super/auth/login", {"username": "admin", "password": "admin123", "pin": "0000"})
if not sa_r:
    sa_r, sa_e = req("post", "/api/super/auth/login", {"username": "admin", "password": "admin"})
sa_tok = (sa_r or {}).get("token") or (sa_r or {}).get("access_token") or ""
if not sa_tok:
    # fallback — mint manually
    sa_id = database.get_db().execute("SELECT id FROM super_admins LIMIT 1").fetchone()
    if sa_id:
        sa_tok = _jwt.encode({"sub": sa_id[0], "exp": 9999999999, "is_super": True,
                              "name": "Admin"}, SECRET, algorithm="HS256")
chk("SETUP super_admin token", bool(sa_tok))

print("\n── 1. /health and /api/health ──")
h1, e1 = req("get", "/health")
chk("1.1 /health returns 200", h1 is not None, e1 or "")
chk("1.2 /health.status is ok|degraded", h1 and h1.get("status") in ("ok", "degraded"))

h2, e2 = req("get", "/api/health")
chk("1.3 /api/health returns 200", h2 is not None, e2 or "")
chk("1.4 /api/health has env block", h2 and "env" in h2)
chk("1.5 /api/health env fields present", h2 and all(k in h2["env"] for k in
    ["BASE_URL", "JWT_SECRET", "OPENAI_API_KEY", "META_APP_ID"]))
chk("1.6 /api/health no secret values in env (all bool)", h2 and all(
    isinstance(v, bool) for v in h2.get("env", {}).values()))
chk("1.7 /api/health has openai_configured flag", h2 and "openai_configured" in h2)
chk("1.8 /api/health has meta_configured flag", h2 and "meta_configured" in h2)

print("\n── 2. /api/live-readiness (per-restaurant) ──")
lr, err = req("get", "/api/live-readiness", token=tok1)
chk("2.1 live-readiness returns 200 for owner", lr is not None, err or "")
chk("2.2 live-readiness has channels", lr and "channels" in lr)
chk("2.3 live-readiness has ai block", lr and "ai" in lr)
chk("2.4 live-readiness has needs_attention", lr and "needs_attention" in lr)
chk("2.5 live-readiness has recommended_fix", lr and "recommended_fix" in lr)
chk("2.6 live-readiness scoped to own restaurant",
    lr and lr.get("restaurant_id") == r1_id, f"got={lr.get('restaurant_id') if lr else None}")

# Verify no secret token values appear in response
lr_str = json.dumps(lr or {})
chk("2.7 live-readiness no raw token in response",
    "fake_bot_token_r1" not in lr_str)

print("\n── 3. /api/channels/status ──")
cs, ce = req("get", "/api/channels/status", token=tok1)
chk("3.1 channels/status returns 200", cs is not None, ce or "")
chk("3.2 channels/status has channels dict", cs and "channels" in cs)
chk("3.3 channels/status scoped to own restaurant",
    cs and cs.get("restaurant_id") == r1_id)

print("\n── 4. Access control — restaurant admin cannot access super endpoints ──")
code_lr = req_code("get", "/api/super/live-readiness", token=tok1)
chk("4.1 /api/super/live-readiness returns 403 for owner", code_lr == 403,
    f"got HTTP {code_lr}")

code_ch = req_code("get", "/api/super/channel-health", token=tok1)
chk("4.2 /api/super/channel-health returns 403 for owner", code_ch == 403,
    f"got HTTP {code_ch}")

code_no = req_code("get", "/api/super/live-readiness")
chk("4.3 /api/super/live-readiness returns 401/403 without token", code_no in (401, 403),
    f"got HTTP {code_no}")

print("\n── 5. /api/super/live-readiness ──")
slr, sle = req("get", "/api/super/live-readiness", token=sa_tok)
chk("5.1 super live-readiness returns 200", slr is not None, sle or "")
chk("5.2 has summary block", slr and "summary" in slr)
chk("5.3 has restaurants list", slr and "restaurants" in slr)
chk("5.4 summary has openai_configured", slr and "openai_configured" in slr.get("summary", {}))
chk("5.5 summary has meta_configured",   slr and "meta_configured" in slr.get("summary", {}))

# Verify our 3 test restaurants appear
rests = {r["restaurant_id"]: r for r in (slr or {}).get("restaurants", [])}
chk("5.6 R1 appears in results", r1_id in rests)
chk("5.7 R2 appears in results", r2_id in rests)
chk("5.8 R3 appears in results", r3_id in rests)

# Verify no raw tokens appear in super response
slr_str = json.dumps(slr or {})
chk("5.9 no raw bot token in super response", "fake_bot_token_r1" not in slr_str)

print("\n── 6. Status honesty ──")
if r1_id in rests:
    r1_data = rests[r1_id]
    tg_status = r1_data.get("channels", {}).get("telegram", {}).get("status")
    # R1 has a token — expect ok OR outbound_failed (we seeded a failed send in setup)
    chk("6.1 R1 Telegram is ok or outbound_failed (has token, has failed send)",
        tg_status in ("ok", "outbound_failed"), f"got={tg_status}")
    # last_error should be populated (we seeded a failed outbound)
    chk("6.2 R1 last_error populated from outbound_messages",
        r1_data.get("last_error") is not None,
        f"got={r1_data.get('last_error')}")

if r2_id in rests:
    r2_data = rests[r2_id]
    # R2 has no channels at all — all should be not_enabled
    tg2 = r2_data.get("channels", {}).get("telegram", {}).get("status")
    chk("6.3 R2 Telegram not_enabled (no channel row)", tg2 == "not_enabled",
        f"got={tg2}")
    chk("6.4 R2 needs_attention == False (not_enabled is OK)", not r2_data.get("needs_attention"),
        f"needs_attention={r2_data.get('needs_attention')}")

if r3_id in rests:
    r3_data = rests[r3_id]
    wa3 = r3_data.get("channels", {}).get("whatsapp", {}).get("status")
    # R3 has WA row but no token — if META_APP_ID configured: pending_meta, else missing_credentials
    from main import META_APP_ID, META_APP_SECRET
    if META_APP_ID and META_APP_SECRET:
        chk("6.5 R3 WhatsApp pending_meta (meta configured, no token)", wa3 == "pending_meta",
            f"got={wa3}")
    else:
        chk("6.5 R3 WhatsApp missing_credentials (meta not configured)", wa3 == "missing_credentials",
            f"got={wa3}")
    chk("6.6 R3 has recommended_fix", bool(r3_data.get("recommended_fix")))

print("\n── 7. /api/super/channel-health ──")
sch, sce = req("get", "/api/super/channel-health", token=sa_tok)
chk("7.1 channel-health returns 200", sch is not None, sce or "")
chk("7.2 has needs_attention list", sch and "needs_attention" in sch)
chk("7.3 has by_channel dict", sch and "by_channel" in sch)
chk("7.4 by_channel has telegram key", sch and "telegram" in sch.get("by_channel", {}))
chk("7.5 by_channel has whatsapp key", sch and "whatsapp" in sch.get("by_channel", {}))

# Verify no secrets in channel-health
sch_str = json.dumps(sch or {})
chk("7.6 no raw bot token in channel-health", "fake_bot_token_r1" not in sch_str)

print("\n── 8. Recommended fix completeness ──")
# A restaurant with no AI and no channels should recommend adding OPENAI_API_KEY
if r2_id in rests:
    fix2 = rests[r2_id].get("recommended_fix", "")
    # R2 has no issues (not_enabled counts as ok) — recommended_fix should say no problems
    chk("8.1 R2 recommended_fix says no problems", fix2 == "لا توجد مشاكل",
        f"got={fix2!r}")

if r3_id in rests:
    fix3 = rests[r3_id].get("recommended_fix", "")
    chk("8.2 R3 recommended_fix is non-empty (has WA issue)", bool(fix3) and fix3 != "لا توجد مشاكل",
        f"got={fix3!r}")

print("\n── 9. Pipeline data present ──")
if r1_id in rests:
    pipe = rests[r1_id].get("pipeline", {})
    chk("9.1 pipeline.orders_ok is bool", isinstance(pipe.get("orders_ok"), bool))
    chk("9.2 pipeline.conversations_ok is bool", isinstance(pipe.get("conversations_ok"), bool))

print("\n── 10. Last inbound_at populated from processed_events ──")
# Seed a processed_event for R1/telegram
conn2 = database.get_db()
conn2.execute("INSERT OR IGNORE INTO processed_events (id,restaurant_id,provider,event_id) VALUES (?,?,?,?)",
              (str(uuid.uuid4()), r1_id, "telegram", f"rd_test_{RUN}"))
conn2.commit(); conn2.close()

# Re-fetch
slr2, _ = req("get", "/api/super/live-readiness", token=sa_tok)
rests2 = {r["restaurant_id"]: r for r in (slr2 or {}).get("restaurants", [])}
if r1_id in rests2:
    tg_info = rests2[r1_id].get("channels", {}).get("telegram", {})
    chk("10.1 R1 telegram last_inbound_at populated",
        bool(tg_info.get("last_inbound_at")),
        f"got={tg_info.get('last_inbound_at')}")
    tg_out_at = tg_info.get("last_outbound_at")
    chk("10.2 R1 telegram last_outbound_at populated",
        bool(tg_out_at), f"got={tg_out_at}")
    tg_err = tg_info.get("last_error")
    # last_error should now show the seeded Telegram: 403 message
    # but status should be outbound_failed or ok depending on logic
    chk("10.3 R1 telegram last_error reflects failed outbound",
        tg_err and "403" in tg_err, f"got={tg_err!r}")
    chk("10.4 R1 telegram status = outbound_failed when last send failed",
        tg_info.get("status") == "outbound_failed", f"got={tg_info.get('status')}")

# ── Results ────────────────────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for r in results if r[0] == PASS)

print(f"\n{'='*72}")
print(f"Live Readiness Check  {passed}/{total} checks PASS")
print(f"{'='*72}")

report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "day12_live_readiness_report.txt")
with open(report_path, "w", encoding="utf-8") as f:
    f.write(f"NUMBER 12 — Super Admin Live Readiness & Channel Health\n")
    f.write(f"Run: {RUN} — {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write("=" * 72 + "\n")
    for icon, label, detail in results:
        suffix = f": {detail}" if detail else ""
        f.write(f"{icon} {label}{suffix}\n")
    f.write("\n" + "=" * 72 + "\n")
    f.write(f"Total: {passed}/{total} ({100*passed//total}%)\n")
    if not failures:
        f.write("✅ NUMBER 12 CLOSED\n")
    else:
        f.write(f"❌ NUMBER 12 NOT CLOSED — {len(failures)} failures remain\n")
        for label, detail in failures:
            f.write(f"  ❌ {label}: {detail}\n")

print(f"\nReport: {report_path}")
if failures:
    print(f"\nFAILURES ({len(failures)}):")
    for label, detail in failures:
        print(f"  ❌ {label}: {detail}")
    print("\n❌ NUMBER 12 NOT CLOSED")
else:
    print("\n✅ NUMBER 12 CLOSED")
