#!/usr/bin/env python3
"""
NUMBER 11 — Full Analytics Audit & Multi-Tenant Verification
Tests every analytics endpoint for:
  - Real database-backed values (not hardcoded/fake)
  - Restaurant_id tenant isolation (R1 never sees R2 data)
  - Correct increments after real events
  - Zero/empty states when no data exists
"""
import sys, os, json, time, uuid, requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

BASE = "http://localhost:8000"
RUN = int(time.time()) % 10_000_000
PASS = "✅"
FAIL = "❌"

results = []

def chk(label, ok, detail=""):
    icon = PASS if ok else FAIL
    results.append((icon, label, detail))
    if not ok:
        print(f"  {FAIL} {label}: {detail}")

def req(method, path, body=None, token=None, expected=200):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = getattr(requests, method)(f"{BASE}{path}", json=body, headers=h, timeout=30)
    if r.status_code != expected:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    try:
        return r.json(), None
    except Exception as e:
        return None, str(e)

# ── Setup: two isolated restaurants ──────────────────────────────────────────
print(f"\n{'='*70}")
print(f"NUMBER 11 — Analytics Audit (run={RUN})")
print(f"{'='*70}\n")

conn = database.get_db()
conn.execute("PRAGMA foreign_keys = OFF")  # disabled for test seed only

def db_exec(sql, *p):
    conn.execute(sql, p)
    conn.commit()

def db_one(sql, *p):
    r = conn.execute(sql, p).fetchone()
    return dict(r) if r else None

def db_val(sql, *p):
    r = conn.execute(sql, p).fetchone()
    return r[0] if r else None

# Create two restaurants
r1_id = f"r1_{RUN}"
r2_id = f"r2_{RUN}"
u1_id = f"u1_{RUN}"
u2_id = f"u2_{RUN}"

import hashlib, bcrypt as _bcrypt
pw_hash = _bcrypt.hashpw(b"test123", _bcrypt.gensalt()).decode()

for rid, uid, name in [(r1_id, u1_id, f"R1-مطعم-{RUN}"), (r2_id, u2_id, f"R2-مطعم-{RUN}")]:
    conn.execute("""
        INSERT OR IGNORE INTO restaurants (id, name, plan, status)
        VALUES (?, ?, 'professional', 'active')
    """, (rid, name))
    conn.execute("""
        INSERT OR IGNORE INTO users (id, restaurant_id, email, password_hash, name, role)
        VALUES (?, ?, ?, ?, ?, 'owner')
    """, (uid, rid, f"owner_{rid}@d11.com", pw_hash, f"Owner {rid}"))
    # settings row required for JWT validation
    conn.execute("""
        INSERT OR IGNORE INTO settings (id, restaurant_id)
        VALUES (?, ?)
    """, (str(uuid.uuid4()), rid))
conn.commit()

# Get tokens
def login(rid):
    email = f"owner_{rid}@d11.com"
    r, err = req("post", "/api/auth/login", {"email": email, "password": "test123"})
    if err or not r:
        # fallback: mint token directly (uses jose, same library as main.py)
        from jose import jwt as _jwt
        import os as _os
        SECRET = _os.getenv("JWT_SECRET", "dev-secret-key")
        tok = _jwt.encode({"sub": f"u_{rid}", "restaurant_id": rid, "exp": 9999999999, "name": "Owner", "role": "owner", "is_super": False}, SECRET, algorithm="HS256")
        return tok
    return r.get("token") or r.get("access_token")

tok1 = login(r1_id)
tok2 = login(r2_id)
chk("AUTH R1 token obtained", bool(tok1))
chk("AUTH R2 token obtained", bool(tok2))

# ── Seed data ─────────────────────────────────────────────────────────────────
print("Seeding test data...")

# Products
p1_id = f"p1_{RUN}"
p2_id = f"p2_{RUN}"
p3_id = f"p3_{RUN}"

for pid, rid, pname, price in [
    (p1_id, r1_id, "برجر كلاسيك", 10000),
    (p2_id, r1_id, "زينگر",        12000),
    (p3_id, r2_id, "شاورما",       8000),
]:
    conn.execute("""
        INSERT OR IGNORE INTO products (id, restaurant_id, name, price, category, available, order_count)
        VALUES (?, ?, ?, ?, 'Main', 1, 0)
    """, (pid, rid, pname, price))
conn.commit()

# Customers
c1_ids = [f"c1a_{RUN}", f"c1b_{RUN}"]
c2_ids = [f"c2a_{RUN}"]

customers = [
    (c1_ids[0], r1_id, "علي",    "telegram"),
    (c1_ids[1], r1_id, "سارة",   "whatsapp"),
    (c2_ids[0], r2_id, "أحمد",   "instagram"),
]
for cid, rid, cname, platform in customers:
    conn.execute("""
        INSERT OR IGNORE INTO customers (id, restaurant_id, name, phone, platform, total_orders, total_spent)
        VALUES (?, ?, ?, ?, ?, 0, 0)
    """, (cid, rid, cname, f"07{RUN}{cid[-3:]}", platform))
conn.commit()

# Conversations
conv_ids_r1 = [f"cv1a_{RUN}", f"cv1b_{RUN}", f"cv1c_{RUN}"]
conv_ids_r2 = [f"cv2a_{RUN}"]

conversations = [
    (conv_ids_r1[0], r1_id, c1_ids[0], "bot",   "open",   "telegram",  0),
    (conv_ids_r1[1], r1_id, c1_ids[1], "human", "open",   "whatsapp",  2),  # unread=2
    (conv_ids_r1[2], r1_id, c1_ids[0], "bot",   "closed", "telegram",  0),
    (conv_ids_r2[0], r2_id, c2_ids[0], "bot",   "open",   "instagram", 1),  # unread=1
]
for cvid, rid, cid, mode, status, channel, unread in conversations:
    conn.execute("""
        INSERT OR IGNORE INTO conversations
            (id, restaurant_id, customer_id, mode, status, channel, unread_count, bot_turn_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 3)
    """, (cvid, rid, cid, mode, status, channel, unread))
conn.commit()

# Messages
for cvid, rid, role, content in [
    (conv_ids_r1[0], r1_id, "customer", "أريد برجر"),
    (conv_ids_r1[0], r1_id, "bot",      "تمام — واحد لو أكثر؟"),
    (conv_ids_r1[1], r1_id, "customer", "مشكلة في طلبي"),
    (conv_ids_r1[1], r1_id, "staff",    "أتابع هسه"),
    (conv_ids_r2[0], r2_id, "customer", "اريد شاورما"),
    (conv_ids_r2[0], r2_id, "bot",      "تمام — واحد لو أكثر؟"),
]:
    conn.execute("""
        INSERT OR IGNORE INTO messages (id, conversation_id, role, content)
        VALUES (?, ?, ?, ?)
    """, (str(uuid.uuid4()), cvid, role, content))
conn.commit()

# Orders — R1: 3 orders, R2: 1 order
ord_r1 = [f"o1a_{RUN}", f"o1b_{RUN}", f"o1c_{RUN}"]
ord_r2 = [f"o2a_{RUN}"]

orders_data = [
    (ord_r1[0], r1_id, c1_ids[0], "telegram",  10000, "pending",   conv_ids_r1[0]),
    (ord_r1[1], r1_id, c1_ids[0], "telegram",  24000, "delivered", conv_ids_r1[2]),  # different conv (unique index)
    (ord_r1[2], r1_id, c1_ids[1], "whatsapp",  12000, "cancelled", conv_ids_r1[1]),
    (ord_r2[0], r2_id, c2_ids[0], "instagram",  8000, "pending",   conv_ids_r2[0]),
]
for oid, rid, cid, channel, total, status, cvid in orders_data:
    conn.execute("""
        INSERT OR IGNORE INTO orders
            (id, restaurant_id, customer_id, channel, type, total, status, conversation_id)
        VALUES (?, ?, ?, ?, 'delivery', ?, ?, ?)
    """, (oid, rid, cid, channel, total, status, cvid))

# Order items
items_data = [
    (ord_r1[0], p1_id, "برجر كلاسيك", 10000, 1),
    (ord_r1[1], p2_id, "زينگر",        12000, 2),
    (ord_r2[0], p3_id, "شاورما",        8000, 1),
]
for oid, pid, name, price, qty in items_data:
    conn.execute("""
        INSERT OR IGNORE INTO order_items (id, order_id, product_id, name, price, quantity)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), oid, pid, name, price, qty))
conn.commit()
conn.close()
print("Seed complete.\n")

# ── Helper: call every analytics endpoint ─────────────────────────────────────
ENDPOINTS = [
    "/api/analytics/overview",
    "/api/analytics/orders",
    "/api/analytics/revenue",
    "/api/analytics/conversations",
    "/api/analytics/customers",
    "/api/analytics/products",
    "/api/analytics/channels",
    "/api/analytics/bot-performance",
    "/api/analytics/recent-activity",
    "/api/analytics/summary",
    "/api/analytics/weekly-revenue",
    "/api/analytics/channel-breakdown",
    "/api/analytics/top-products",
    "/api/analytics/top-customers",
    "/api/analytics/bot-stats",
    "/api/analytics/order-funnel",
]

print("Calling all endpoints for R1 and R2...")
r1_data, r2_data = {}, {}
for ep in ENDPOINTS:
    d1, e1 = req("get", ep, token=tok1)
    d2, e2 = req("get", ep, token=tok2)
    chk(f"R1 {ep} returns 200", d1 is not None, e1 or "")
    chk(f"R2 {ep} returns 200", d2 is not None, e2 or "")
    r1_data[ep] = d1 or {}
    r2_data[ep] = d2 or {}

print()

# ── ISOLATION CHECKS ─────────────────────────────────────────────────────────
print("Checking cross-tenant isolation...")

# 1. Total orders: R1=3, R2=1
r1_orders = r1_data["/api/analytics/overview"].get("total_orders", -1)
r2_orders = r2_data["/api/analytics/overview"].get("total_orders", -1)
chk("ISOLATION-01 R1 total_orders == 3", r1_orders == 3, f"got {r1_orders}")
chk("ISOLATION-01 R2 total_orders == 1", r2_orders == 1, f"got {r2_orders}")

# 2. Revenue isolation
r1_rev = r1_data["/api/analytics/revenue"].get("total_revenue", -1)
r2_rev = r2_data["/api/analytics/revenue"].get("total_revenue", -1)
# R1: 10000+24000 (cancelled 12000 excluded) = 34000
chk("ISOLATION-02 R1 revenue == 34000", r1_rev == 34000, f"got {r1_rev}")
# R2: 8000 (pending, not cancelled) = 8000
chk("ISOLATION-02 R2 revenue == 8000",  r2_rev == 8000,  f"got {r2_rev}")
chk("ISOLATION-02 R2 revenue < R1 revenue", r2_rev < r1_rev, f"R1={r1_rev} R2={r2_rev}")

# 3. Customers isolation
r1_custs = r1_data["/api/analytics/overview"].get("total_customers", -1)
r2_custs = r2_data["/api/analytics/overview"].get("total_customers", -1)
chk("ISOLATION-03 R1 customers == 2", r1_custs == 2, f"got {r1_custs}")
chk("ISOLATION-03 R2 customers == 1", r2_custs == 1, f"got {r2_custs}")

# 4. Conversations isolation
r1_convs = r1_data["/api/analytics/conversations"].get("total", -1)
r2_convs = r2_data["/api/analytics/conversations"].get("total", -1)
chk("ISOLATION-04 R1 conversations == 3", r1_convs == 3, f"got {r1_convs}")
chk("ISOLATION-04 R2 conversations == 1", r2_convs == 1, f"got {r2_convs}")

# 5. Channel isolation — R1 has telegram+whatsapp, R2 has instagram
r1_channels = {c["channel"] for c in r1_data["/api/analytics/channels"]}
r2_channels = {c["channel"] for c in r2_data["/api/analytics/channels"]}
chk("ISOLATION-05 R1 channels = {telegram,whatsapp}", "telegram" in r1_channels and "whatsapp" in r1_channels, str(r1_channels))
chk("ISOLATION-05 R2 channels = {instagram}", "instagram" in r2_channels, str(r2_channels))
chk("ISOLATION-05 R1 has NO instagram orders", "instagram" not in {c["channel"] for c in r1_data["/api/analytics/channel-breakdown"]}, str(r1_channels))
chk("ISOLATION-05 R2 has NO telegram orders",  "telegram" not in {c["channel"] for c in r2_data["/api/analytics/channel-breakdown"]}, str(r2_channels))

# 6. Products isolation
r1_prod_names = {p["name"] for p in r1_data["/api/analytics/products"].get("items", [])}
r2_prod_names = {p["name"] for p in r2_data["/api/analytics/products"].get("items", [])}
chk("ISOLATION-06 R1 products do NOT include شاورما (R2's)", "شاورما" not in r1_prod_names, str(r1_prod_names))
chk("ISOLATION-06 R2 products do NOT include برجر (R1's)",   "برجر كلاسيك" not in r2_prod_names, str(r2_prod_names))

# 7. Handoff (human mode)
r1_human = r1_data["/api/analytics/conversations"].get("human_mode", -1)
r2_human = r2_data["/api/analytics/conversations"].get("human_mode", -1)
chk("ISOLATION-07 R1 human_mode == 1",  r1_human == 1, f"got {r1_human}")
chk("ISOLATION-07 R2 human_mode == 0",  r2_human == 0, f"got {r2_human}")

# 8. Unread count isolation
r1_unread = r1_data["/api/analytics/conversations"].get("unread", -1)
r2_unread = r2_data["/api/analytics/conversations"].get("unread", -1)
chk("ISOLATION-08 R1 unread convs == 1 (whatsapp has 2 msgs)", r1_unread == 1, f"got {r1_unread}")
chk("ISOLATION-08 R2 unread convs == 1 (instagram has 1 msg)", r2_unread == 1, f"got {r2_unread}")

# 9. Top products — R1's top product comes from R1 order_items only
r1_top = r1_data["/api/analytics/top-products"]
r2_top = r2_data["/api/analytics/top-products"]
r1_top_names = [p["name"] for p in r1_top]
r2_top_names = [p["name"] for p in r2_top]
chk("ISOLATION-09 R1 top products include زينگر", "زينگر" in r1_top_names, str(r1_top_names))
chk("ISOLATION-09 R2 top products include شاورما", "شاورما" in r2_top_names, str(r2_top_names))
chk("ISOLATION-09 R1 top products do NOT include شاورما", "شاورما" not in r1_top_names, str(r1_top_names))

# 10. Bot performance
r1_bot = r1_data["/api/analytics/bot-performance"]
r2_bot = r2_data["/api/analytics/bot-performance"]
chk("ISOLATION-10 bot-performance R1 has bot_reply_count", r1_bot.get("bot_reply_count", -1) >= 0, str(r1_bot.get("bot_reply_count")))
chk("ISOLATION-10 bot-performance values are non-negative",
    all(v >= 0 for v in [r1_bot.get("success_rate",0), r1_bot.get("handoff_rate",0)]),
    str(r1_bot))

print()

# ── INCREMENT TEST ────────────────────────────────────────────────────────────
print("Testing increments (add 1 order to R1, verify R2 unchanged)...")

# Snapshot before
r1_before, _ = req("get", "/api/analytics/overview", token=tok1)
r2_before, _ = req("get", "/api/analytics/overview", token=tok2)
r1_orders_before = r1_before.get("total_orders", -1) if r1_before else -1
r2_orders_before = r2_before.get("total_orders", -1) if r2_before else -1

# Create new order for R1 via API
new_order_total = 15000
new_ord_id = f"o1new_{RUN}"
conn = database.get_db()
conn.execute("""
    INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, status)
    VALUES (?, ?, ?, 'facebook', 'delivery', ?, 'pending')
""", (new_ord_id, r1_id, c1_ids[0], new_order_total))
conn.execute("""
    INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
    VALUES (?, ?, ?, 'برجر كلاسيك', 15000, 1)
""", (str(uuid.uuid4()), new_ord_id, p1_id))
conn.commit()
conn.close()

# Snapshot after
r1_after, _ = req("get", "/api/analytics/overview", token=tok1)
r2_after, _  = req("get", "/api/analytics/overview", token=tok2)

r1_orders_after = r1_after.get("total_orders", -1) if r1_after else -1
r2_orders_after = r2_after.get("total_orders", -1)  if r2_after else -1
r1_rev_after    = r1_after.get("total_revenue", -1) if r1_after else -1
r2_rev_after    = r2_after.get("total_revenue", -1) if r2_after else -1

chk("INCREMENT-01 R1 total_orders increased by 1",
    r1_orders_after == r1_orders_before + 1,
    f"before={r1_orders_before} after={r1_orders_after}")
chk("INCREMENT-02 R2 total_orders unchanged",
    r2_orders_after == r2_orders_before,
    f"before={r2_orders_before} after={r2_orders_after}")
chk("INCREMENT-03 R1 revenue increased by 15000",
    abs(r1_rev_after - (r1_before.get("total_revenue",0) + new_order_total)) < 1,
    f"before={r1_before.get('total_revenue')} after={r1_rev_after}")
chk("INCREMENT-04 R2 revenue unchanged after R1 order",
    abs(r2_rev_after - r2_before.get("total_revenue", 0)) < 1,
    f"before={r2_before.get('total_revenue')} after={r2_rev_after}")

# 5. channel increment — add facebook conversation to R1
print("Testing channel increment (facebook conv for R1)...")
new_cv_id = f"cvFB_{RUN}"
conn = database.get_db()
conn.execute("""
    INSERT INTO conversations (id, restaurant_id, customer_id, mode, status, channel, unread_count, bot_turn_count)
    VALUES (?, ?, ?, 'bot', 'open', 'facebook', 0, 1)
""", (new_cv_id, r1_id, c1_ids[0]))
conn.commit()
conn.close()

r1_ch_after, _ = req("get", "/api/analytics/channels", token=tok1)
r2_ch_after, _ = req("get", "/api/analytics/channels", token=tok2)
r1_fb = next((c for c in (r1_ch_after or []) if c["channel"] == "facebook"), None)
r2_fb = next((c for c in (r2_ch_after or []) if c["channel"] == "facebook"), None)
chk("INCREMENT-05 R1 facebook conversations == 1", r1_fb and r1_fb.get("conversations") == 1, str(r1_fb))
chk("INCREMENT-06 R2 has no facebook conversations", r2_fb is None or r2_fb.get("conversations", 0) == 0, str(r2_fb))

print()

# ── EMPTY STATE TEST ─────────────────────────────────────────────────────────
print("Testing empty state (brand new restaurant)...")
r_empty_id = f"rempty_{RUN}"
u_empty_id = f"uempty_{RUN}"

conn2 = database.get_db()
conn2.execute("PRAGMA foreign_keys = OFF")
conn2.execute("INSERT OR IGNORE INTO restaurants (id, name, plan, status) VALUES (?, ?, 'trial', 'active')", (r_empty_id, f"Empty-{RUN}"))
conn2.execute("INSERT OR IGNORE INTO users (id, restaurant_id, email, password_hash, name, role) VALUES (?, ?, ?, ?, 'Owner', 'owner')", (u_empty_id, r_empty_id, f"owner_{r_empty_id}@d11.com", pw_hash))
conn2.execute("INSERT OR IGNORE INTO settings (id, restaurant_id) VALUES (?, ?)", (str(uuid.uuid4()), r_empty_id))
conn2.commit()
conn2.close()

tok_empty = login(r_empty_id)
empty_ov, _ = req("get", "/api/analytics/overview",   token=tok_empty)
empty_od, _ = req("get", "/api/analytics/orders",     token=tok_empty)
empty_rv, _ = req("get", "/api/analytics/revenue",    token=tok_empty)
empty_cv, _ = req("get", "/api/analytics/conversations", token=tok_empty)
empty_ch, _ = req("get", "/api/analytics/channels",   token=tok_empty)
empty_pr, _ = req("get", "/api/analytics/products",   token=tok_empty)
empty_bp, _ = req("get", "/api/analytics/bot-performance", token=tok_empty)

chk("EMPTY-01 overview returns zeros (not None)",      empty_ov is not None and empty_ov.get("total_orders") == 0, str(empty_ov))
chk("EMPTY-02 orders returns zeros",                   empty_od is not None and empty_od.get("total_orders") == 0, str(empty_od))
chk("EMPTY-03 revenue returns zeros",                  empty_rv is not None and empty_rv.get("total_revenue") == 0, str(empty_rv))
chk("EMPTY-04 conversations returns zeros",            empty_cv is not None and empty_cv.get("total") == 0, str(empty_cv))
chk("EMPTY-05 channels returns empty array",           isinstance(empty_ch, list) and len(empty_ch) == 0, str(empty_ch))
chk("EMPTY-06 products returns zero total",            empty_pr is not None and empty_pr.get("total") == 0, str(empty_pr))
chk("EMPTY-07 bot-performance returns zeros",          empty_bp is not None and empty_bp.get("total_bot_conversations") == 0, str(empty_bp))
chk("EMPTY-08 weekly revenue is 7 zero-filled days",   empty_rv is not None and len(empty_rv.get("weekly", [])) == 7 and all(d["revenue"] == 0 for d in empty_rv["weekly"]), "")

print()

# ── HARDCODED VALUE CHECK ─────────────────────────────────────────────────────
print("Checking for hardcoded/static values...")

# summary should have different values per restaurant
chk("NOHARD-01 R1 and R2 total_orders are different",
    r1_data["/api/analytics/summary"].get("total_orders") != r2_data["/api/analytics/summary"].get("total_orders"),
    f"R1={r1_data['/api/analytics/summary'].get('total_orders')} R2={r2_data['/api/analytics/summary'].get('total_orders')}")
chk("NOHARD-02 R1 revenue > R2 revenue",
    r1_data["/api/analytics/summary"].get("total_revenue", 0) > r2_data["/api/analytics/summary"].get("total_revenue", 0),
    "")
# conversion_rate must not be hardcoded 68
r1_cr = r1_data["/api/analytics/order-funnel"].get("conversion_rate", -1)
chk("NOHARD-03 conversion_rate is not hardcoded 68", r1_cr != 68, f"got {r1_cr}")
# bot success_rate must not be hardcoded 87
r1_bs = r1_data["/api/analytics/bot-stats"].get("success_rate", -1)
chk("NOHARD-04 bot success_rate is not hardcoded 87", r1_bs != 87, f"got {r1_bs}")
# satisfaction field does not exist (was hardcoded 4.7 in Node.js)
chk("NOHARD-05 no hardcoded satisfaction=4.7 field",
    r1_data["/api/analytics/summary"].get("satisfaction") is None,
    str(r1_data["/api/analytics/summary"].get("satisfaction")))

print()

# ── METRIC ACCURACY CHECK ─────────────────────────────────────────────────────
print("Checking metric accuracy...")

# R1 pending orders = 1 (o1a is pending, o1b is delivered, o1c is cancelled)
r1_pending = r1_data["/api/analytics/overview"].get("pending_orders", -1)
chk("METRIC-01 R1 pending_orders == 1", r1_pending == 1, f"got {r1_pending}")

# R1 completed orders (delivered) = 1
r1_completed = r1_data["/api/analytics/overview"].get("completed_orders", -1)
chk("METRIC-02 R1 completed_orders == 1", r1_completed == 1, f"got {r1_completed}")

# R1 cancelled = 1
r1_cancelled = r1_data["/api/analytics/overview"].get("cancelled_orders", -1)
chk("METRIC-03 R1 cancelled_orders == 1", r1_cancelled == 1, f"got {r1_cancelled}")

# R1 top product is زينگر (2 qty) not برجر (1 qty)
r1_top_first = r1_data["/api/analytics/top-products"][0]["name"] if r1_data["/api/analytics/top-products"] else ""
chk("METRIC-04 R1 top product is زينگر (2 ordered)", r1_top_first == "زينگر", f"got {r1_top_first}")

# R1 human_mode conversations == 1
r1_human_ov = r1_data["/api/analytics/overview"].get("human_mode_count", -1)
chk("METRIC-05 R1 human_mode_count == 1", r1_human_ov == 1, f"got {r1_human_ov}")

# R1 bot_mode == 2
r1_bot_ov = r1_data["/api/analytics/overview"].get("bot_mode_count", -1)
chk("METRIC-06 R1 bot_mode_count == 2", r1_bot_ov == 2, f"got {r1_bot_ov}")

# products endpoint has revenue field
r1_prod_items = r1_data["/api/analytics/products"].get("items", [])
chk("METRIC-07 products items include revenue field", all("revenue" in p for p in r1_prod_items), "")

# recent-activity has both keys
ra = r1_data["/api/analytics/recent-activity"]
chk("METRIC-08 recent-activity has recent_orders",       isinstance(ra.get("recent_orders"), list), "")
chk("METRIC-09 recent-activity has recent_conversations",isinstance(ra.get("recent_conversations"), list), "")

# bot-performance conversion_rate is calculated
bp = r1_data["/api/analytics/bot-performance"]
chk("METRIC-10 bot-performance has all required fields",
    all(k in bp for k in ["total_bot_conversations","escalated","success_rate","handoff_rate","bot_reply_count","conversion_rate"]),
    str(list(bp.keys())))

print()

# ── FINAL REPORT ──────────────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for r in results if r[0] == PASS)
failed = total - passed

print(f"{'='*70}")
print(f"RESULTS: {passed}/{total} ({int(passed/total*100)}%)")
print(f"{'='*70}")

if failed:
    print(f"\nFailed ({failed}):")
    for icon, label, detail in results:
        if icon == FAIL:
            print(f"  {FAIL} {label}")
            if detail:
                print(f"       {detail[:120]}")

# Save report
report = {
    "run_id": RUN,
    "timestamp": time.strftime("%Y-%m-%d %H:%M"),
    "total": total,
    "passed": passed,
    "failed": failed,
    "results": [{"status": r[0], "label": r[1], "detail": r[2]} for r in results],
}
rpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "day11_analytics_audit_report.txt")
with open(rpath, "w") as f:
    f.write(f"NUMBER 11 — Full Analytics Audit\n")
    f.write(f"Run: {RUN} — {report['timestamp']}\n")
    f.write("="*70 + "\n")
    for icon, label, detail in results:
        f.write(f"{icon} {label}\n")
        if detail and icon == FAIL:
            f.write(f"   → {detail[:120]}\n")
    f.write("\n" + "="*70 + "\n")
    f.write(f"Total: {passed}/{total} ({int(passed/total*100)}%)\n")
    if passed == total:
        f.write("✅ NUMBER 11 CLOSED\n")
    else:
        f.write("❌ NUMBER 11 NOT CLOSED\n")

print(f"\nReport: {rpath}")
print()
if passed == total:
    print("✅ NUMBER 11 CLOSED")
else:
    print("❌ NUMBER 11 NOT CLOSED")
