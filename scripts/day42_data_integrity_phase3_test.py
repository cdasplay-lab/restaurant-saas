#!/usr/bin/env python3
"""
scripts/day42_data_integrity_phase3_test.py — NUMBER 42 Phase 3: Data Integrity

Proves fixes:
  RISK-05: Promo increment deferred into order INSERT transaction (webhooks.py)
  RISK-11: _parse_confirmed_order runs AFTER OrderBrain block (not before)
  RISK-13: _parse_confirmed_order gated — skipped when session is still active
"""
from __future__ import annotations
import sys, os, re, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOLD = "\033[1m"; RED = "\033[31m"; GRN = "\033[32m"; RST = "\033[0m"
_pass = _fail = 0

def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  {GRN}✓{RST} {label}")
    else:
        _fail += 1
        print(f"  {RED}✗{RST} {label}" + (f"  — {detail}" if detail else ""))


import inspect
import services.bot as _bot_mod
import services.webhooks as _wh_mod

BOT_SRC = inspect.getsource(_bot_mod)
WH_SRC  = inspect.getsource(_wh_mod)

print(f"\n{BOLD}{'═'*60}")
print("  NUMBER 42 Phase 3 — Data Integrity Tests")
print(f"{'═'*60}{RST}\n")


# ── RISK-05 A: promo commit removed from bot.py ───────────────────────────────
print(f"{BOLD}RISK-05 A — Promo increment NOT committed in bot.py{RST}")

# The old code had: _promo_conn.execute("UPDATE promo_codes SET uses_count=uses_count+1 ...")
# followed by _promo_conn.commit() inside the promo block.
# After fix: UPDATE is removed; only a SELECT + discount calculation happens there.

# Extract the promo validation block from bot.py source
_promo_block_match = re.search(
    r'_promo_id_to_increment\s*=\s*None.*?_promo_conn\.close\(\)',
    BOT_SRC, re.DOTALL
)
_promo_block = _promo_block_match.group(0) if _promo_block_match else ""

check(
    "bot.py promo block has no _promo_conn.commit()",
    "_promo_conn.commit()" not in _promo_block,
    "found commit() inside promo block — increment not deferred",
)
check(
    "bot.py promo block has no UPDATE promo_codes",
    "UPDATE promo_codes" not in _promo_block,
    "found UPDATE inside promo block — increment not deferred",
)
check(
    "_promo_id_to_increment set when promo valid",
    "_promo_id_to_increment = _pc[\"id\"]" in _promo_block,
    "promo ID not captured",
)


# ── RISK-05 B: promo_code_id injected into confirmed_order dict ───────────────
print(f"\n{BOLD}RISK-05 B — promo_code_id injected into confirmed_order{RST}")

check(
    "confirmed_order['promo_code_id'] assignment present in bot.py",
    "confirmed_order[\"promo_code_id\"] = _promo_id_to_increment" in BOT_SRC,
    "injection code not found",
)
check(
    "injection gated on confirmed_order being non-None",
    "if confirmed_order and \"_promo_id_to_increment\" in dir() and _promo_id_to_increment:" in BOT_SRC,
    "gate missing",
)


# ── RISK-05 C: promo increment inside _auto_create_order transaction ──────────
print(f"\n{BOLD}RISK-05 C — Promo increment inside _auto_create_order transaction{RST}")

check(
    "webhooks.py _auto_create_order reads promo_code_id from order_data",
    "order_data.get(\"promo_code_id\")" in WH_SRC,
    "promo_code_id not read in _auto_create_order",
)

# Verify UPDATE is before conn.commit() in _auto_create_order source
_aco_src = inspect.getsource(_wh_mod._auto_create_order)
_update_pos = _aco_src.find("UPDATE promo_codes SET uses_count")
_commit_pos = _aco_src.rfind("conn.commit()")  # last commit in the function

check(
    "promo UPDATE appears before conn.commit() in _auto_create_order",
    _update_pos != -1 and _commit_pos != -1 and _update_pos < _commit_pos,
    f"update_pos={_update_pos} commit_pos={_commit_pos}",
)


# ── RISK-05 D: functional — promo increment happens inside DB transaction ─────
print(f"\n{BOLD}RISK-05 D — Functional: promo increment atomic with order insert{RST}")

# Build in-memory SQLite DB and test _auto_create_order with promo_code_id
import uuid as _uuid

def _make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE promo_codes (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT,
            code TEXT,
            uses_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT,
            customer_id TEXT,
            channel TEXT,
            type TEXT,
            total REAL,
            address TEXT,
            status TEXT,
            conversation_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE order_items (
            id TEXT PRIMARY KEY,
            order_id TEXT,
            product_id TEXT,
            name TEXT,
            price REAL,
            quantity INTEGER,
            notes TEXT
        );
        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT,
            name TEXT,
            total_orders INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0,
            favorite_item TEXT
        );
        CREATE TABLE conversation_memory (
            id TEXT PRIMARY KEY,
            restaurant_id TEXT,
            customer_id TEXT,
            memory_key TEXT,
            memory_value TEXT,
            updated_at DATETIME,
            UNIQUE(restaurant_id, customer_id, memory_key)
        );
    """)
    promo_id = str(_uuid.uuid4())
    customer_id = str(_uuid.uuid4())
    conn.execute(
        "INSERT INTO promo_codes VALUES (?, 'r1', 'SAVE10', 0, 1)",
        (promo_id,)
    )
    conn.execute(
        "INSERT INTO customers VALUES (?, 'r1', 'علي', 0, 0, NULL)",
        (customer_id,)
    )
    conn.commit()
    return conn, promo_id, customer_id

from services.webhooks import _auto_create_order

conn, promo_id, customer_id = _make_test_db()
customer = {"id": customer_id, "name": "علي"}
order_data = {
    "items": [{"name": "برجر لحم", "quantity": 1, "price": 8000.0, "product_id": None}],
    "total": 8000.0,
    "address": "الكرادة",
    "type": "delivery",
    "promo_code_id": promo_id,
}

_auto_create_order(conn, "r1", customer, "whatsapp", order_data, conversation_id="conv-test-01")

_promo_row = conn.execute("SELECT uses_count FROM promo_codes WHERE id=?", (promo_id,)).fetchone()
_order_row = conn.execute("SELECT id FROM orders WHERE restaurant_id='r1'").fetchone()

check(
    "Order inserted in DB after _auto_create_order",
    _order_row is not None,
    "order row missing from DB",
)
check(
    "Promo uses_count incremented to 1 after order insert",
    _promo_row is not None and _promo_row["uses_count"] == 1,
    f"uses_count={_promo_row['uses_count'] if _promo_row else 'N/A'}",
)

# Verify: order WITHOUT promo_code_id does NOT increment uses_count
conn2, promo_id2, customer_id2 = _make_test_db()
customer2 = {"id": customer_id2, "name": "سارة"}
order_data2 = {
    "items": [{"name": "زينجر", "quantity": 1, "price": 8500.0, "product_id": None}],
    "total": 8500.0,
    "address": "المنصور",
    "type": "delivery",
    # no promo_code_id
}
_auto_create_order(conn2, "r1", customer2, "whatsapp", order_data2, conversation_id="conv-test-02")
_promo_row2 = conn2.execute("SELECT uses_count FROM promo_codes WHERE id=?", (promo_id2,)).fetchone()
check(
    "Order without promo_code_id does NOT increment uses_count",
    _promo_row2 is not None and _promo_row2["uses_count"] == 0,
    f"uses_count={_promo_row2['uses_count'] if _promo_row2 else 'N/A'}",
)


# ── RISK-11: _parse_confirmed_order runs AFTER OrderBrain block ───────────────
print(f"\n{BOLD}RISK-11 — _parse_confirmed_order positioned AFTER OrderBrain block{RST}")

# Find line positions in the source
_ob_exc2_pos = BOT_SRC.find("order_brain] post-reply update failed")
_parse_call_pos = BOT_SRC.find("if order_enabled and _ob_not_active:")
_old_parse_pos  = BOT_SRC.find(
    "confirmed_order = _parse_confirmed_order(reply_text, memory"
)
_placeholder_pos = BOT_SRC.find(
    "confirmed_order is parsed AFTER the OrderBrain block"
)

check(
    "Placeholder comment at early position (before OrderBrain block)",
    _placeholder_pos != -1 and _placeholder_pos < _ob_exc2_pos,
    f"placeholder_pos={_placeholder_pos} ob_block_end={_ob_exc2_pos}",
)
check(
    "_parse_confirmed_order call is AFTER OrderBrain block end",
    _old_parse_pos != -1 and _ob_exc2_pos != -1 and _old_parse_pos > _ob_exc2_pos,
    f"parse_pos={_old_parse_pos} ob_block_end={_ob_exc2_pos}",
)
check(
    "_ob_not_active gate present",
    "_ob_not_active" in BOT_SRC,
    "gate variable not found",
)


# ── RISK-13: Gate logic — skip parsing when session is active ─────────────────
print(f"\n{BOLD}RISK-13 — Gate: skip _parse_confirmed_order when session is active{RST}")

from services.order_brain import OrderSession, OrderItem, OrderBrain

# Simulate the _ob_not_active gate logic (mirrors bot.py lines 1850-1853)
def _simulate_gate(ob_session) -> bool:
    """Returns True if parsing is allowed (mirrors bot.py _ob_not_active logic)."""
    return ob_session is None or not ob_session.is_active()

# Case 1: No session → parsing allowed
check(
    "No session (None) → gate allows parsing",
    _simulate_gate(None) is True,
    "gate blocked when session is None",
)

# Case 2: Active session (collecting) → parsing BLOCKED
active_s = OrderSession("gate-test-active", "r1")
active_s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
active_s.confirmation_status = "collecting"
check(
    "Active session (collecting + has items) → gate BLOCKS parsing",
    _simulate_gate(active_s) is False,
    f"is_active={active_s.is_active()} gate={_simulate_gate(active_s)}",
)

# Case 3: Awaiting confirm → also BLOCKED
awaiting_s = OrderSession("gate-test-awaiting", "r1")
awaiting_s.items = [OrderItem(name="زينجر", qty=1, price=8500)]
awaiting_s.confirmation_status = "awaiting_confirm"
check(
    "Session awaiting_confirm → gate BLOCKS parsing",
    _simulate_gate(awaiting_s) is False,
    f"is_active={awaiting_s.is_active()} gate={_simulate_gate(awaiting_s)}",
)

# Case 4: Confirmed session (just fired) → parsing ALLOWED
confirmed_s = OrderSession("gate-test-confirmed", "r1")
confirmed_s.items = [OrderItem(name="برجر لحم", qty=1, price=8000)]
confirmed_s.order_type = "delivery"
confirmed_s.address = "الكرادة"
confirmed_s.customer_name = "علي"
confirmed_s.phone = "07901234567"
confirmed_s.payment_method = "كاش"
confirmed_s.confirmation_status = "confirmed"
check(
    "Confirmed session → gate ALLOWS parsing",
    _simulate_gate(confirmed_s) is True,
    f"is_active={confirmed_s.is_active()} gate={_simulate_gate(confirmed_s)}",
)

# Case 5: Cancelled session → also allowed (no active order)
cancelled_s = OrderSession("gate-test-cancelled", "r1")
cancelled_s.confirmation_status = "cancelled"
check(
    "Cancelled session → gate ALLOWS parsing",
    _simulate_gate(cancelled_s) is True,
    f"is_active={cancelled_s.is_active()} gate={_simulate_gate(cancelled_s)}",
)

# Case 6: Session with no items (fresh) → allowed (is_active=False when no items)
fresh_s = OrderSession("gate-test-fresh", "r1")
check(
    "Fresh session (no items) → gate ALLOWS parsing",
    _simulate_gate(fresh_s) is True,
    f"is_active={fresh_s.is_active()} gate={_simulate_gate(fresh_s)}",
)


# ── RISK-13 functional: _parse_confirmed_order returns None for non-✅ text ────
print(f"\n{BOLD}RISK-13 Functional — _parse_confirmed_order returns None without ✅{RST}")

from services.bot import _parse_confirmed_order

PRODUCTS = [{"id": "1", "name": "برجر لحم", "price": 8000, "available": 1}]
MEMORY   = {}

NON_CONFIRM_REPLIES = [
    ("توصيل لو استلام؟",                      "mid-collection question"),
    ("تمام 🌷 وصلني",                          "ack — no ✅"),
    ("تمام",                                  "bare ack"),
    ("✅ طلبك جاهز بدون مجموع",               "✅ present but no المجموع"),
]
for text, label in NON_CONFIRM_REPLIES:
    result = _parse_confirmed_order(text, MEMORY, PRODUCTS)
    check(
        f"'{text[:35]}' → None ({label})",
        result is None,
        f"got={result}",
    )

# Full ✅ summary should return a proper dict
FULL_SUMMARY = """✅ طلبك:
• برجر لحم × 1 — 8,000 د.ع
📍 العنوان: الكرادة
💳 الدفع: كاش
المجموع: 8,000 د.ع"""

result_ok = _parse_confirmed_order(FULL_SUMMARY, MEMORY, PRODUCTS)
check(
    "Full ✅ summary → returns confirmed_order dict",
    result_ok is not None and result_ok.get("total", 0) > 0,
    f"got={result_ok}",
)
check(
    "Parsed total = 8000",
    result_ok is not None and result_ok.get("total") == 8000.0,
    f"total={result_ok.get('total') if result_ok else 'N/A'}",
)


# ─────────────────────────────────────────────────────────────────────────────
total = _pass + _fail
pct   = round(100 * _pass / total) if total else 0
print(f"\n{BOLD}{'═'*60}")
print(f"  Result: {_pass}/{total} passed ({pct}%)")
print(f"{'═'*60}{RST}\n")

if _fail:
    print(f"{RED}FAILED — {_fail} test(s) failed{RST}\n")
    sys.exit(1)
else:
    print(f"{GRN}All tests passed.{RST}\n")
    sys.exit(0)
