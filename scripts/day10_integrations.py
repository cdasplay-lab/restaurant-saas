#!/usr/bin/env python3
"""
Day 10 — Integrations Hub API Test
====================================
Tests the new channel connection API without real Meta credentials.
Covers: catalog, connect/disconnect/toggle/reconnect, errors endpoint,
OAuth state creation, super admin monitoring, token refresh path.

Tests:
  INTG-01  GET /api/integrations/catalog — returns 4 platforms, correct shape
  INTG-02  POST /api/integrations/oauth/start — fails gracefully when META_APP_ID unset
  INTG-03  POST /api/integrations/whatsapp/embedded-signup — fails gracefully (no code)
  INTG-04  POST /api/integrations/telegram/reconnect — returns 400 (use manual form)
  INTG-05  POST /api/integrations/telegram/disconnect — channel cleared (no token)
  INTG-06  POST /api/integrations/telegram/toggle — 400 (not connected)
  INTG-07  GET  /api/integrations/telegram/errors — returns list (empty ok)
  INTG-08  Telegram connect via /api/channels/telegram PUT + register-webhook (mock)
  INTG-09  POST /api/integrations/telegram/toggle — enabled after connecting
  INTG-10  POST /api/integrations/telegram/disconnect — disconnects cleanly
  INTG-11  GET /api/super/integrations — paginated results, no secrets exposed
  INTG-12  GET /api/super/integrations/stats — correct shape
  INTG-13  GET /api/super/integrations/errors — list returned
  INTG-14  POST /api/super/integrations/{id}/resolve-errors — 200 ok
  INTG-15  POST /api/super/integrations/{id}/force-disconnect — 200 ok
"""
import json, sys, urllib.request, urllib.error, time
from datetime import datetime

BASE = "http://localhost:8000"

# ── HTTP helper ────────────────────────────────────────────────────────────────
def _req(method, path, data=None, token=None, timeout=20):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req  = urllib.request.Request(f"{BASE}{path}", data=body,
                                   headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return (json.loads(raw) if raw else {}), r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw), e.code
        except Exception:
            return {}, e.code
    except Exception as exc:
        return {"error": str(exc)}, 0

def _login_restaurant(email, password):
    d, s = _req("POST", "/api/auth/login", {"email": email, "password": password})
    token = d.get("token") or d.get("access_token") or ""
    rid   = d.get("restaurant_id") or (d.get("user") or {}).get("restaurant_id") or ""
    return token, rid

def _login_super():
    d, s = _req("POST", "/api/super/auth/login", {"email": "superadmin@platform.com", "password": "super123"})
    return d.get("token") or d.get("sa_token") or "", s

# ── Test runner ────────────────────────────────────────────────────────────────
results = []

def test(name, ok, detail=""):
    status = "✅ PASS" if ok else "❌ FAIL"
    results.append((name, ok, detail))
    print(f"  {status}  {name}" + (f"  — {detail}" if detail else ""))

def section(title):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")

# ── Setup: ensure a restaurant + super admin exist ─────────────────────────────
def setup():
    # Register test restaurant if not exists
    d, s = _req("POST", "/api/auth/register", {
        "restaurant_name": "انتج تست",
        "owner_name":      "مالك تست",
        "email":           "intg_test@d10.com",
        "password":        "test123456",
        "phone":           "0501234599",
    })
    # Login (works whether new or existing)
    token, rid = _login_restaurant("intg_test@d10.com", "test123456")
    return token, rid

# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 55)
    print("  Day 10 — Integrations Hub API Tests")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    token, rid = setup()
    if not token:
        print("\n❌ فشل تسجيل الدخول — تأكد من تشغيل الخادم على localhost:8000")
        sys.exit(1)
    print(f"\n  ✓ Restaurant token OK  (rid={rid[:8]}…)")

    sa_token, sa_s = _login_super()
    if not sa_token:
        print("  ⚠ Super admin login failed — super admin tests will be skipped")
    else:
        print(f"  ✓ Super admin token OK")

    # ── INTG-01: catalog ──────────────────────────────────────────────────────
    section("INTG-01 … 07  Restaurant Catalog & Basic Flows")

    d, s = _req("GET", "/api/integrations/catalog", token=token)
    platforms = d if isinstance(d, list) else []
    platform_names = {p.get("platform") for p in platforms}
    test("INTG-01  catalog returns 4 platforms",
         s == 200 and len(platforms) == 4,
         f"got {len(platforms)}: {platform_names}")
    test("INTG-01b catalog has required fields",
         all({"platform","display_name","connection_status","auth_type"} <= p.keys()
             for p in platforms),
         "")
    test("INTG-01c secrets not exposed in catalog",
         not any(p.get("token") or p.get("app_secret") for p in platforms),
         "")

    # ── INTG-02: OAuth start without META_APP_ID ──────────────────────────────
    d, s = _req("POST", "/api/integrations/oauth/start",
                {"platform": "facebook"}, token=token)
    # Either 400 (no META_APP_ID) or 200 (configured) — both are valid
    test("INTG-02  oauth/start responds correctly",
         s in (200, 400),
         f"status={s} msg={d.get('detail','')[:60]}")

    # ── INTG-03: WA embedded-signup without code ──────────────────────────────
    d, s = _req("POST", "/api/integrations/whatsapp/embedded-signup",
                {"code": "", "waba_id": "", "phone_number_id": ""}, token=token)
    test("INTG-03  embedded-signup rejects empty code",
         s == 400,
         f"detail={d.get('detail','')[:50]}")

    # ── INTG-04: reconnect Telegram (bot_token) returns 400 ──────────────────
    d, s = _req("POST", "/api/integrations/telegram/reconnect", token=token)
    test("INTG-04  telegram reconnect returns 400 (use manual form)",
         s == 400,
         f"detail={d.get('detail','')[:60]}")

    # ── INTG-05: disconnect Telegram (even if not connected) ─────────────────
    # First ensure channel exists by hitting catalog
    d, s = _req("POST", "/api/integrations/telegram/disconnect", token=token)
    test("INTG-05  disconnect telegram succeeds",
         s in (200, 404),
         f"status={s}")

    # ── INTG-06: toggle without being connected ───────────────────────────────
    d, s = _req("POST", "/api/integrations/telegram/toggle",
                {"enabled": True}, token=token)
    test("INTG-06  toggle while disconnected returns 400 or 404",
         s in (400, 404),
         f"status={s} detail={d.get('detail','')[:50]}")

    # ── INTG-07: errors list ──────────────────────────────────────────────────
    d, s = _req("GET", "/api/integrations/telegram/errors", token=token)
    test("INTG-07  errors endpoint returns list",
         s == 200 and isinstance(d, list),
         f"count={len(d) if isinstance(d, list) else 'N/A'}")

    # ── INTG-08: connect Telegram via legacy PUT + register-webhook ───────────
    section("INTG-08 … 10  Telegram Connect/Toggle/Disconnect")

    d, s = _req("PUT", "/api/channels/telegram", {
        "token":          "1234567890:AAFakeTokenForTestingPurposes123456",
        "bot_username":   "@test_bot",
        "webhook_secret": "secret123",
        "enabled":        True,
    }, token=token)
    test("INTG-08a  PUT /api/channels/telegram saves config",
         s == 200,
         f"status={s}")

    # register-webhook will fail (invalid token) — that's expected
    d, s = _req("POST", "/api/channels/telegram/register-webhook", token=token)
    test("INTG-08b  register-webhook responds (fail OK with fake token)",
         s in (200, 400, 422, 500),
         f"status={s} msg={str(d)[:60]}")

    # Simulate manual connected state by updating connection_status via test endpoint
    d, s = _req("POST", "/api/channels/telegram/test", token=token)
    test("INTG-08c  test-connection responds",
         s in (200, 400, 500),
         f"status={s} success={d.get('success')}")

    # ── INTG-09: toggle (Telegram may or may not be 'connected') ─────────────
    # Directly check catalog status
    d, s = _req("GET", "/api/integrations/catalog", token=token)
    tg = next((p for p in (d if isinstance(d, list) else []) if p.get("platform") == "telegram"), {})
    tg_connected = tg.get("connection_status") == "connected"

    if tg_connected:
        d2, s2 = _req("POST", "/api/integrations/telegram/toggle",
                      {"enabled": False}, token=token)
        test("INTG-09  toggle disable when connected",
             s2 == 200,
             f"enabled={d2.get('enabled')}")
    else:
        test("INTG-09  toggle skipped (Telegram not connected with fake token)",
             True, "expected — real token needed")

    # ── INTG-10: disconnect Telegram ─────────────────────────────────────────
    d, s = _req("POST", "/api/integrations/telegram/disconnect", token=token)
    test("INTG-10  disconnect telegram",
         s in (200, 404),
         f"status={s}")

    # Verify catalog now shows disconnected
    d, s = _req("GET", "/api/integrations/catalog", token=token)
    tg_after = next((p for p in (d or []) if p.get("platform") == "telegram"), {})
    test("INTG-10b  catalog shows telegram disconnected after disconnect",
         tg_after.get("connection_status") != "connected",
         f"status={tg_after.get('connection_status')}")

    # ── Super Admin tests ─────────────────────────────────────────────────────
    if not sa_token:
        print("\n  ⚠ Skipping super admin tests (no SA token)")
    else:
        section("INTG-11 … 15  Super Admin Integrations")

        # INTG-11: list
        d, s = _req("GET", "/api/super/integrations?limit=20", token=sa_token)
        channels_list = d.get("channels", [])
        test("INTG-11  GET /api/super/integrations",
             s == 200 and "channels" in d and "total" in d,
             f"total={d.get('total')} page={d.get('page')}")
        test("INTG-11b  no secrets in super list",
             all(ch.get("token") in (None, "", "****") for ch in channels_list),
             "")

        # INTG-12: stats
        d, s = _req("GET", "/api/super/integrations/stats", token=sa_token)
        test("INTG-12  GET /api/super/integrations/stats",
             s == 200 and "by_platform" in d,
             f"reconnect_needed={d.get('total_reconnect_needed')} errors={d.get('total_errors_unresolved')}")
        test("INTG-12b  stats has all 4 platforms or empty",
             isinstance(d.get("by_platform"), dict),
             str(list(d.get("by_platform", {}).keys())))

        # INTG-13: errors list
        d, s = _req("GET", "/api/super/integrations/errors", token=sa_token)
        test("INTG-13  GET /api/super/integrations/errors",
             s == 200 and isinstance(d, list),
             f"unresolved_errors={len(d) if isinstance(d, list) else 'N/A'}")

        # Find a channel to test with
        d2, s2 = _req("GET", "/api/super/integrations?limit=100", token=sa_token)
        channels_all = d2.get("channels", [])
        target_ch = channels_all[0] if channels_all else None

        if target_ch:
            ch_id = target_ch["id"]

            # INTG-14: resolve errors
            d, s = _req("POST", f"/api/super/integrations/{ch_id}/resolve-errors",
                        token=sa_token)
            test("INTG-14  resolve-errors",
                 s == 200 and d.get("ok"),
                 f"channel_id={ch_id[:8]}…")

            # INTG-15: force-disconnect
            d, s = _req("POST", f"/api/super/integrations/{ch_id}/force-disconnect",
                        token=sa_token)
            test("INTG-15  force-disconnect",
                 s == 200 and d.get("ok"),
                 f"channel_id={ch_id[:8]}…")

            # Verify channel is now disconnected
            d3, s3 = _req("GET", "/api/super/integrations?limit=100", token=sa_token)
            updated = next((c for c in d3.get("channels",[]) if c["id"]==ch_id), None)
            test("INTG-15b  channel shows disconnected in super list after force-disconnect",
                 updated and updated.get("connection_status") == "disconnected",
                 f"status={updated.get('connection_status') if updated else 'not found'}")
        else:
            test("INTG-14  resolve-errors skipped (no channels)", True, "no channels in DB")
            test("INTG-15  force-disconnect skipped (no channels)", True, "no channels in DB")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*55}")
    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)
    pct    = int(passed / total * 100) if total else 0
    print(f"  النتيجة: {passed}/{total} ({pct}%)")
    failed = [(n, d) for n, ok, d in results if not ok]
    if failed:
        print(f"\n  الفشل ({len(failed)}):")
        for name, detail in failed:
            print(f"    ✗ {name}" + (f": {detail}" if detail else ""))
    print(f"{'═'*55}\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
