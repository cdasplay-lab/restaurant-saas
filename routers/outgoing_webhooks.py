"""
routers/outgoing_webhooks.py — NUMBER 43: Outgoing Webhooks extracted from main.py.

Routes: GET/POST/PATCH/DELETE /api/outgoing-webhooks,
        POST /api/outgoing-webhooks/{wid}/test

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

import database
from dependencies import current_user

router = APIRouter()

_VALID_EVENTS = {
    "order.created", "order.confirmed", "order.preparing",
    "order.on_way", "order.delivered", "order.cancelled",
}


@router.get("/api/outgoing-webhooks")
async def list_outgoing_webhooks(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM outgoing_webhooks WHERE restaurant_id=? ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return {"webhooks": [dict(r) for r in rows]}


@router.post("/api/outgoing-webhooks", status_code=201)
async def create_outgoing_webhook(req: Request, user=Depends(current_user)):
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL يجب أن يبدأ بـ http:// أو https://")
    name = (body.get("name") or "Webhook").strip()[:100]
    secret = (body.get("secret") or "").strip()[:200]
    events_raw = body.get("events") or ["order.created"]
    events = [e for e in events_raw if e in _VALID_EVENTS] or ["order.created"]
    wid = str(uuid.uuid4())
    conn = database.get_db()
    conn.execute(
        "INSERT INTO outgoing_webhooks (id, restaurant_id, name, url, secret, events) VALUES (?,?,?,?,?,?)",
        (wid, user["restaurant_id"], name, url, secret, json.dumps(events))
    )
    conn.commit()
    row = conn.execute("SELECT * FROM outgoing_webhooks WHERE id=?", (wid,)).fetchone()
    conn.close()
    return dict(row)


@router.patch("/api/outgoing-webhooks/{wid}")
async def update_outgoing_webhook(wid: str, req: Request, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM outgoing_webhooks WHERE id=? AND restaurant_id=?",
                        (wid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Webhook not found")
    body = await req.json()
    allowed = {"name", "url", "secret", "events", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "events" in updates:
        updates["events"] = json.dumps([e for e in updates["events"] if e in _VALID_EVENTS])
    if not updates:
        conn.close()
        return {"ok": True}
    vals = list(updates.values())
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE outgoing_webhooks SET {set_clause} WHERE id=?", [*vals, wid])
    conn.commit()
    row = conn.execute("SELECT * FROM outgoing_webhooks WHERE id=?", (wid,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/outgoing-webhooks/{wid}")
async def delete_outgoing_webhook(wid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM outgoing_webhooks WHERE id=? AND restaurant_id=?",
                        (wid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Webhook not found")
    conn.execute("DELETE FROM outgoing_webhooks WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/outgoing-webhooks/{wid}/test")
async def test_outgoing_webhook(wid: str, user=Depends(current_user)):
    import httpx as _httpx
    conn = database.get_db()
    row = conn.execute("SELECT * FROM outgoing_webhooks WHERE id=? AND restaurant_id=?",
                       (wid, user["restaurant_id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Webhook not found")
    payload = json.dumps({"event": "ping", "data": {"message": "test ping"}}).encode()
    try:
        async with _httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(row["url"], content=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Restaurant-Event": "ping"})
        return {"ok": r.status_code < 400, "status_code": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}
