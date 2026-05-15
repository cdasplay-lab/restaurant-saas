"""
routers/orders.py — NUMBER 43: Orders extracted from main.py.

Routes: GET /api/export/orders,
        GET/POST /api/orders, GET /api/orders/{oid},
        POST /api/orders/{oid}/payment-link,
        GET /api/orders/{oid}/payment-status,
        PATCH /api/orders/{oid}/status,
        PATCH /api/orders/{oid}, DELETE /api/orders/{oid}

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import asyncio
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import database
from dependencies import current_user
from helpers import create_notification, log_activity

logger = logging.getLogger("restaurant-saas")

router = APIRouter()

STATUS_FLOW = {
    "pending": "confirmed",
    "confirmed": "preparing",
    "preparing": "on_way",
    "on_way": "delivered",
}

ALLOWED_TRANSITIONS: dict = {
    "pending":    {"confirmed", "cancelled"},
    "confirmed":  {"preparing", "cancelled"},
    "preparing":  {"on_way", "cancelled"},
    "on_way":     {"delivered", "cancelled"},
    "delivered":  set(),
    "cancelled":  set(),
}

_STATUS_MESSAGES = {
    "confirmed":  "✅ طلبك وصلنا وصار بالتجهيز! نشوفك قريب 😊",
    "preparing":  "👨‍🍳 طلبك عم يتجهز الحين — ما يطول!",
    "on_way":     "🛵 طلبك طلع من المطعم وعلى الطريق إليك!",
    "delivered":  "✅ وصل طلبك! بالعافية وشكراً لاختيارك 🌷",
    "cancelled":  "❌ تم إلغاء طلبك. إذا عندك استفسار تواصل معنا.",
}


class OrderCreate(BaseModel):
    customer_id: str
    channel: str = "telegram"
    type: str = "delivery"
    address: str = ""
    notes: str = ""
    items: list = []
    branch_id: str = ""


class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    address: Optional[str] = None


def _get_stripe_key(restaurant_id: str) -> str:
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT stripe_secret_key, stripe_enabled FROM settings WHERE restaurant_id=?",
            (restaurant_id,)
        ).fetchone()
    finally:
        conn.close()
    if row and row["stripe_enabled"] and row["stripe_secret_key"]:
        return row["stripe_secret_key"]
    return os.getenv("STRIPE_SECRET_KEY", "")


async def _notify_customer_status_change(order: dict, restaurant_id: str, new_status: str) -> None:
    message_text = _STATUS_MESSAGES.get(new_status)
    if not message_text:
        return
    try:
        import httpx as _httpx
        conn = database.get_db()
        try:
            customer_id = order.get("customer_id", "")
            cust_row = conn.execute(
                "SELECT platform, phone FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            if not cust_row:
                return
            platform = cust_row["platform"] or ""
            mem_row = conn.execute(
                "SELECT memory_value FROM conversation_memory "
                "WHERE restaurant_id=? AND customer_id=? AND memory_key='external_id'",
                (restaurant_id, customer_id)
            ).fetchone()
            external_id = mem_row["memory_value"] if mem_row else cust_row["phone"] or ""
            if not external_id:
                return

            if platform == "telegram":
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'",
                    (restaurant_id,)
                ).fetchone()
                if not ch or not ch["token"]:
                    return
                async with _httpx.AsyncClient(timeout=10) as _cl:
                    await _cl.post(
                        f"https://api.telegram.org/bot{ch['token']}/sendMessage",
                        json={"chat_id": external_id, "text": message_text}
                    )

            elif platform == "whatsapp":
                ch = conn.execute(
                    "SELECT token, phone_number_id FROM channels WHERE restaurant_id=? AND type='whatsapp'",
                    (restaurant_id,)
                ).fetchone()
                if not ch or not ch["token"]:
                    return
                pn_id = ch["phone_number_id"] if "phone_number_id" in ch.keys() else ""
                if not pn_id:
                    return
                async with _httpx.AsyncClient(timeout=10) as _cl:
                    await _cl.post(
                        f"https://graph.facebook.com/v19.0/{pn_id}/messages",
                        headers={"Authorization": f"Bearer {ch['token']}", "Content-Type": "application/json"},
                        json={"messaging_product": "whatsapp", "to": external_id,
                              "type": "text", "text": {"body": message_text}}
                    )

            elif platform in ("instagram", "facebook"):
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type=?",
                    (restaurant_id, platform)
                ).fetchone()
                if not ch or not ch["token"]:
                    return
                async with _httpx.AsyncClient(timeout=10) as _cl:
                    await _cl.post(
                        "https://graph.facebook.com/v19.0/me/messages",
                        params={"access_token": ch["token"]},
                        json={"recipient": {"id": external_id}, "message": {"text": message_text}}
                    )
        finally:
            conn.close()
    except Exception as _e:
        logger.warning(f"[notify_status] {new_status} notify failed order={order.get('id','?')}: {_e}")


async def _fire_outgoing_webhooks(restaurant_id: str, event: str, payload: dict) -> None:
    import hmac as _hmac, hashlib as _hl, json as _json
    try:
        import httpx as _httpx
        conn = database.get_db()
        try:
            rows = conn.execute(
                "SELECT id, url, secret FROM outgoing_webhooks "
                "WHERE restaurant_id=? AND is_active=1",
                (restaurant_id,)
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return

        body = _json.dumps({"event": event, "data": payload}, ensure_ascii=False).encode()
        async with _httpx.AsyncClient(timeout=10) as _cl:
            for row in rows:
                _conn2 = database.get_db()
                try:
                    full = _conn2.execute(
                        "SELECT events, secret FROM outgoing_webhooks WHERE id=?", (row["id"],)
                    ).fetchone()
                finally:
                    _conn2.close()
                if not full:
                    continue
                subscribed = _json.loads(full["events"] or '["order.confirmed"]')
                if event not in subscribed:
                    continue
                secret = full["secret"] or ""
                headers = {"Content-Type": "application/json", "X-Restaurant-Event": event}
                if secret:
                    sig = _hmac.new(secret.encode(), body, _hl.sha256).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={sig}"
                try:
                    r = await _cl.post(row["url"], content=body, headers=headers)
                    _conn3 = database.get_db()
                    try:
                        _conn3.execute(
                            "UPDATE outgoing_webhooks SET last_triggered_at=CURRENT_TIMESTAMP, "
                            "last_status_code=?, fail_count=CASE WHEN ? < 400 THEN 0 ELSE fail_count+1 END "
                            "WHERE id=?",
                            (r.status_code, r.status_code, row["id"])
                        )
                        _conn3.commit()
                    finally:
                        _conn3.close()
                except Exception as _req_e:
                    logger.warning(f"[outgoing_webhook] delivery failed id={row['id']}: {_req_e}")
                    _conn4 = database.get_db()
                    try:
                        _conn4.execute(
                            "UPDATE outgoing_webhooks SET fail_count=fail_count+1 WHERE id=?",
                            (row["id"],)
                        )
                        _conn4.commit()
                    finally:
                        _conn4.close()
    except Exception as _e:
        logger.warning(f"[outgoing_webhooks] event={event} restaurant={restaurant_id}: {_e}")


async def _notify_customer_confirmed(order: dict, restaurant_id: str) -> None:
    try:
        import httpx as _httpx
        conn = database.get_db()
        try:
            customer_id = order.get("customer_id", "")
            cust_row = conn.execute(
                "SELECT platform, phone FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            if not cust_row:
                return
            platform = cust_row["platform"] or ""
            mem_row = conn.execute(
                "SELECT memory_value FROM conversation_memory WHERE restaurant_id=? AND customer_id=? AND memory_key='external_id'",
                (restaurant_id, customer_id)
            ).fetchone()
            external_id = mem_row["memory_value"] if mem_row else cust_row["phone"] or ""
            if not external_id:
                return
            settings_row = conn.execute(
                "SELECT delivery_time FROM settings WHERE restaurant_id=?", (restaurant_id,)
            ).fetchone()
            delivery_time = settings_row["delivery_time"] if settings_row and "delivery_time" in settings_row.keys() else ""
            msg_lines = ["✅ طلبك وصلنا وصار بالتجهيز!"]
            if delivery_time:
                msg_lines.append(f"⏱️ الوقت التقريبي للتوصيل: {delivery_time}")
            msg_lines.append("شكراً لك، نشوفك قريب 😊")
            message_text = "\n".join(msg_lines)

            if platform == "telegram":
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type='telegram'",
                    (restaurant_id,)
                ).fetchone()
                bot_token = ch["token"] if ch else ""
                if not bot_token or not external_id:
                    return
                async with _httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": external_id, "text": message_text}
                    )
            elif platform == "whatsapp":
                ch = conn.execute(
                    "SELECT token, phone_number_id FROM channels WHERE restaurant_id=? AND type='whatsapp'",
                    (restaurant_id,)
                ).fetchone()
                if not ch:
                    return
                access_token = ch["token"] if ch else ""
                phone_number_id = ch["phone_number_id"] if "phone_number_id" in ch.keys() else ""
                if not access_token or not phone_number_id or not external_id:
                    return
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                async with _httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
                        headers=headers,
                        json={"messaging_product": "whatsapp", "to": external_id,
                              "type": "text", "text": {"body": message_text}}
                    )
            elif platform in ("instagram", "facebook"):
                ch = conn.execute(
                    "SELECT token FROM channels WHERE restaurant_id=? AND type=?",
                    (restaurant_id, platform)
                ).fetchone()
                page_token = ch["token"] if ch else ""
                if not page_token or not external_id:
                    return
                async with _httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        "https://graph.facebook.com/v19.0/me/messages",
                        params={"access_token": page_token},
                        json={"recipient": {"id": external_id}, "message": {"text": message_text}}
                    )
        finally:
            conn.close()
    except Exception as _e:
        logger.warning(f"[order] customer confirmed notify failed (non-fatal): {_e}")


@router.get("/api/export/orders")
async def export_orders(user=Depends(current_user)):
    import csv, io
    rid = user["restaurant_id"]
    conn = database.get_db()
    try:
        rows = conn.execute(
            """SELECT o.id, o.status, o.total, o.address, o.notes, o.created_at,
                      c.name as customer_name, c.phone as customer_phone
               FROM orders o
               LEFT JOIN customers c ON o.customer_id = c.id
               WHERE o.restaurant_id=?
               ORDER BY o.created_at DESC""",
            (rid,)
        ).fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["رقم الطلب", "الحالة", "الإجمالي", "العنوان", "الملاحظات", "التاريخ", "اسم العميل", "جوال العميل"])
    for r in rows:
        writer.writerow([r["id"], r["status"], r["total"], r["address"] or "", r["notes"] or "",
                         r["created_at"], r["customer_name"] or "", r["customer_phone"] or ""])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=orders.csv"}
    )


@router.get("/api/orders")
async def list_orders(
    status: Optional[str] = None,
    channel: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = """
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone,
               b.name AS branch_name
        FROM orders o JOIN customers c ON o.customer_id = c.id
        LEFT JOIN branches b ON o.branch_id = b.id
        WHERE o.restaurant_id = ?
    """
    params = [rid]
    if status:
        q += " AND o.status=?"; params.append(status)
    if channel:
        q += " AND o.channel=?"; params.append(channel)
    if branch_id:
        q += " AND o.branch_id=?"; params.append(branch_id)
    if search:
        q += " AND (c.name LIKE ? OR o.id LIKE ?)"; params += [f"%{search}%", f"%{search}%"]
    if date_from:
        q += " AND DATE(o.created_at) >= ?"; params.append(date_from)
    if date_to:
        q += " AND DATE(o.created_at) <= ?"; params.append(date_to)
    q += " ORDER BY o.created_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/orders/{oid}")
async def get_order(oid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("""
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, c.platform
        FROM orders o JOIN customers c ON o.customer_id = c.id
        WHERE o.id=? AND o.restaurant_id=?
    """, (oid, user["restaurant_id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Order not found")
    items = conn.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
    conn.close()
    result = dict(row)
    result["items"] = [dict(i) for i in items]
    return result


@router.post("/api/orders", status_code=201)
async def create_order(data: OrderCreate, user=Depends(current_user)):
    conn = database.get_db()
    oid = str(uuid.uuid4())
    total = sum(i.get("price", 0) * i.get("quantity", 1) for i in data.items)
    conn.execute("""
        INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, address, notes, status, branch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (oid, user["restaurant_id"], data.customer_id, data.channel,
          data.type, total, data.address, data.notes, data.branch_id))
    for item in data.items:
        conn.execute("""
            INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), oid, item.get("product_id"),
              item.get("name"), item.get("price", 0), item.get("quantity", 1)))
        if item.get("product_id"):
            conn.execute(
                "UPDATE products SET order_count=COALESCE(order_count,0)+? WHERE id=? AND restaurant_id=?",
                (item.get("quantity", 1), item["product_id"], user["restaurant_id"])
            )
    conn.execute("""
        UPDATE customers SET
            total_orders = COALESCE(total_orders, 0) + 1,
            total_spent  = COALESCE(total_spent, 0) + ?
        WHERE id = ? AND restaurant_id = ?
    """, (total, data.customer_id, user["restaurant_id"]))
    log_activity(conn, user["restaurant_id"], "order_created", "order", oid,
                 f"طلب جديد بقيمة {total} د.ع", user["id"], user["name"])
    create_notification(conn, user["restaurant_id"], "new_order", "طلب جديد",
                        f"طلب جديد بقيمة {total} د.ع من {data.channel}", "order", oid)
    conn.commit()
    row = conn.execute("""
        SELECT o.*, c.name AS customer_name FROM orders o
        JOIN customers c ON o.customer_id = c.id WHERE o.id=?
    """, (oid,)).fetchone()
    conn.close()
    order_dict = dict(row)
    asyncio.create_task(_fire_outgoing_webhooks(user["restaurant_id"], "order.created", order_dict))
    return order_dict


@router.post("/api/orders/{oid}/payment-link")
async def create_payment_link(oid: str, user=Depends(current_user)):
    key = _get_stripe_key(user["restaurant_id"])
    if not key:
        raise HTTPException(402, "بوابة الدفع غير مُفعّلة — أضف STRIPE_SECRET_KEY في الإعدادات")
    try:
        import stripe as _stripe
    except ImportError:
        raise HTTPException(500, "stripe package not installed")

    conn = database.get_db()
    order = conn.execute(
        "SELECT o.*, r.name AS rest_name FROM orders o "
        "JOIN restaurants r ON o.restaurant_id=r.id "
        "WHERE o.id=? AND o.restaurant_id=?",
        (oid, user["restaurant_id"])
    ).fetchone()
    items = conn.execute(
        "SELECT name, price, quantity FROM order_items WHERE order_id=?", (oid,)
    ).fetchall()
    conn.close()

    if not order:
        raise HTTPException(404, "Order not found")
    if order["payment_status"] == "paid":
        raise HTTPException(409, "الطلب مدفوع بالفعل")

    _stripe.api_key = key
    frontend_base = os.getenv("BASE_URL", "").rstrip("/")
    line_items = [
        {
            "price_data": {
                "currency": "usd",
                "product_data": {"name": item["name"]},
                "unit_amount": max(1, int(item["price"] * 100)),
            },
            "quantity": item["quantity"],
        }
        for item in items
    ] or [{
        "price_data": {
            "currency": "usd",
            "product_data": {"name": f"طلب #{oid[:8]}"},
            "unit_amount": max(1, int(order["total"] * 100)),
        },
        "quantity": 1,
    }]

    session = _stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=line_items,
        mode="payment",
        success_url=f"{frontend_base}/app?payment=success&order={oid}",
        cancel_url=f"{frontend_base}/app?payment=cancelled&order={oid}",
        metadata={"order_id": oid, "restaurant_id": user["restaurant_id"]},
    )

    conn2 = database.get_db()
    conn2.execute(
        "UPDATE orders SET stripe_session_id=?, stripe_payment_url=? WHERE id=?",
        (session.id, session.url, oid)
    )
    conn2.commit()
    conn2.close()
    return {"url": session.url, "session_id": session.id}


@router.get("/api/orders/{oid}/payment-status")
async def get_payment_status(oid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT payment_status, stripe_payment_url FROM orders WHERE id=? AND restaurant_id=?",
        (oid, user["restaurant_id"])
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Order not found")
    return dict(row)


@router.patch("/api/orders/{oid}/status")
async def update_order_status(oid: str, req: Request, background_tasks: BackgroundTasks, user=Depends(current_user)):
    body = await req.json()
    action = body.get("action", "advance")
    conn = database.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND restaurant_id=?",
                         (oid, user["restaurant_id"])).fetchone()
    if not order:
        conn.close()
        raise HTTPException(404, "Order not found")
    BLOCKED_FROM = {"delivered", "cancelled"}
    VALID_STATUSES = {"pending", "confirmed", "preparing", "on_way", "delivered", "cancelled"}

    if action == "cancel":
        if order["status"] in BLOCKED_FROM:
            conn.close()
            raise HTTPException(400, "لا يمكن إلغاء طلب مكتمل أو ملغى مسبقاً")
        new_status = "cancelled"
        log_activity(conn, user["restaurant_id"], "order_cancelled", "order", oid,
                     f"تم إلغاء الطلب #{oid[:8]}", user["id"], user["name"])
    elif action == "advance":
        if order["status"] in BLOCKED_FROM:
            conn.close()
            raise HTTPException(400, "لا يمكن تقديم هذا الطلب")
        new_status = STATUS_FLOW.get(order["status"])
        if not new_status:
            conn.close()
            raise HTTPException(400, "لا يمكن تقديم هذا الطلب")
        log_activity(conn, user["restaurant_id"], "order_status_changed", "order", oid,
                     f"تغيير حالة الطلب إلى {new_status}", user["id"], user["name"])
    else:
        if action not in VALID_STATUSES:
            conn.close()
            raise HTTPException(400, "حالة غير صحيحة")
        allowed = ALLOWED_TRANSITIONS.get(order["status"], set())
        if action not in allowed:
            conn.close()
            raise HTTPException(400, f"لا يمكن الانتقال من {order['status']} إلى {action}")
        new_status = action
        log_activity(conn, user["restaurant_id"], "order_status_changed", "order", oid,
                     f"تغيير حالة الطلب إلى {new_status}", user["id"], user["name"])

    conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, oid))

    if new_status == "pending":
        create_notification(conn, user["restaurant_id"], "new_order",
                            "طلب جديد في الانتظار",
                            f"الطلب #{oid[:8]} في انتظار التأكيد", "order", oid)

    _NOTIFY_STATUSES = {"confirmed", "preparing", "on_way", "delivered", "cancelled"}
    if new_status in _NOTIFY_STATUSES:
        order_dict = dict(order)
        background_tasks.add_task(
            _notify_customer_status_change, order_dict, user["restaurant_id"], new_status
        )
        background_tasks.add_task(
            _fire_outgoing_webhooks, user["restaurant_id"],
            f"order.{new_status}", order_dict
        )

    conn.commit()
    conn.close()
    return {"status": new_status}


@router.patch("/api/orders/{oid}")
async def update_order(oid: str, data: OrderUpdate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM orders WHERE id=? AND restaurant_id=?",
                        (oid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Order not found")
    upd = {}
    if data.notes is not None: upd["notes"] = data.notes
    if data.address is not None: upd["address"] = data.address
    if upd:
        conn.execute(f"UPDATE orders SET {','.join(k+'=?' for k in upd)} WHERE id=?",
                     list(upd.values()) + [oid])
        conn.commit()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/orders/{oid}")
async def delete_order(oid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM orders WHERE id=? AND restaurant_id=?",
                        (oid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Order not found")
    conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
    conn.execute("DELETE FROM orders WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}
