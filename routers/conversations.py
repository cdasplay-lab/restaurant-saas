"""
routers/conversations.py — NUMBER 43: Conversations extracted from main.py.

Routes: GET /api/conversations, GET/POST /api/conversations/{cid}/messages,
        GET /api/outbound/failed,
        PATCH /api/conversations/{cid}/messages/{mid},
        PATCH /api/conversations/{cid}/mode,
        PATCH /api/conversations/{cid}/urgent,
        PATCH /api/conversations/{cid}/read,
        POST /api/conversations/{cid}/ai-reply

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import database
from dependencies import current_user
from helpers import log_activity

logger = logging.getLogger("restaurant-saas")

router = APIRouter()

_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            try:
                import openai as _openai
                _openai_client = _openai.OpenAI(api_key=api_key)
            except ImportError:
                pass
    return _openai_client


class MsgCreate(BaseModel):
    content: str


@router.get("/api/conversations")
async def list_conversations(
    mode: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    customer_id: Optional[str] = None,
    user=Depends(current_user),
):
    conn = database.get_db()
    rid = user["restaurant_id"]
    q = """
        SELECT cv.*,
               c.name AS customer_name,
               c.platform,
               c.phone,
               COALESCE(
                 (SELECT memory_value FROM conversation_memory
                  WHERE customer_id=c.id AND restaurant_id=? AND memory_key='name'
                  ORDER BY updated_at DESC LIMIT 1),
                 c.name
               ) AS display_name,
               (SELECT content FROM messages
                WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1) AS last_message,
               (SELECT role FROM messages
                WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1) AS last_message_role,
               (SELECT created_at FROM messages
                WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1) AS last_message_at
        FROM conversations cv JOIN customers c ON cv.customer_id = c.id
        WHERE cv.restaurant_id=?
    """
    params = [rid, rid]
    if mode:
        q += " AND cv.mode=?"; params.append(mode)
    if status:
        q += " AND cv.status=?"; params.append(status)
    if search:
        q += """ AND (c.name LIKE ? OR c.phone LIKE ?
                   OR EXISTS(SELECT 1 FROM conversation_memory cm2
                              WHERE cm2.customer_id=c.id AND cm2.memory_key='name'
                                AND cm2.memory_value LIKE ?))"""
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if customer_id:
        q += " AND cv.customer_id=?"; params.append(customer_id)
    q += " ORDER BY COALESCE((SELECT created_at FROM messages WHERE conversation_id=cv.id ORDER BY created_at DESC LIMIT 1), cv.updated_at) DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/conversations/{cid}/messages")
async def get_messages(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    msgs = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,)).fetchall()
    conn.close()
    return [dict(m) for m in msgs]


@router.post("/api/conversations/{cid}/messages")
async def send_message(cid: str, data: MsgCreate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    mid = str(uuid.uuid4())
    conn.execute("INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'staff', ?)",
                 (mid, cid, data.content))
    conn.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP, unread_count=0, mode='human' WHERE id=?", (cid,))
    conn.commit()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(msg)


@router.get("/api/outbound/failed")
async def list_failed_messages(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        """SELECT id, conversation_id, platform, recipient_id, content, error, created_at
           FROM outbound_messages
           WHERE restaurant_id=? AND status='failed'
           ORDER BY created_at DESC LIMIT 50""",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.patch("/api/conversations/{cid}/messages/{mid}")
async def edit_message(cid: str, mid: str, req: Request, user=Depends(current_user)):
    body = await req.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "المحتوى مطلوب")
    conn = database.get_db()
    msg = conn.execute(
        "SELECT m.* FROM messages m JOIN conversations c ON m.conversation_id=c.id "
        "WHERE m.id=? AND m.conversation_id=? AND c.restaurant_id=?",
        (mid, cid, user["restaurant_id"])
    ).fetchone()
    if not msg:
        conn.close()
        raise HTTPException(404, "الرسالة غير موجودة")
    conn.execute("UPDATE messages SET content=? WHERE id=?", (content, mid))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.patch("/api/conversations/{cid}/mode")
async def toggle_mode(cid: str, req: Request, user=Depends(current_user)):
    body = await req.json()
    mode = body.get("mode", "bot")
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    conn.execute("UPDATE conversations SET mode=? WHERE id=?", (mode, cid))
    log_activity(conn, user["restaurant_id"], "conversation_mode_changed", "conversation", cid,
                 f"تغيير وضع المحادثة إلى {mode}", user["id"], user["name"])
    conn.commit()
    conn.close()
    return {"mode": mode}


@router.patch("/api/conversations/{cid}/urgent")
async def set_urgent(cid: str, req: Request, user=Depends(current_user)):
    body = await req.json()
    urgent = bool(body.get("urgent", True))
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    conn.execute("UPDATE conversations SET urgent=? WHERE id=?", (int(urgent), cid))
    conn.commit()
    conn.close()
    return {"urgent": urgent}


@router.patch("/api/conversations/{cid}/read")
async def mark_read(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    conn.execute("UPDATE conversations SET unread_count=0 WHERE id=? AND restaurant_id=?",
                 (cid, user["restaurant_id"]))
    conn.commit()
    conn.close()
    return {"unread_count": 0}


@router.post("/api/conversations/{cid}/ai-reply")
async def ai_reply(cid: str, user=Depends(current_user)):
    client = _get_openai_client()
    if not client:
        raise HTTPException(503, "OpenAI غير مهيأ — أضف OPENAI_API_KEY في ملف .env")
    conn = database.get_db()
    if not conn.execute("SELECT id FROM conversations WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Conversation not found")
    s = conn.execute("SELECT * FROM settings WHERE restaurant_id=?", (user["restaurant_id"],)).fetchone()
    r = conn.execute("SELECT * FROM restaurants WHERE id=?", (user["restaurant_id"],)).fetchone()
    msgs = list(reversed(conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 10", (cid,)).fetchall()))
    conn.close()

    bot_name = (s["bot_name"] if s else None) or "AI Assistant"
    rest_name = (r["name"] if r else None) or "المطعم"
    system_prompt = (
        f"أنت {bot_name}، مساعد ذكاء اصطناعي لمطعم {rest_name}.\n"
        "مهمتك مساعدة العملاء بشكل ودي ومحترف.\nأجب باختصار وبفائدة."
    )
    chat_msgs = [{"role": "system", "content": system_prompt}]
    for m in msgs:
        role = "user" if m["role"] == "customer" else "assistant"
        chat_msgs.append({"role": role, "content": m["content"]})

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=chat_msgs,
        max_tokens=300,
    )
    ai_content = resp.choices[0].message.content

    conn = database.get_db()
    mid = str(uuid.uuid4())
    conn.execute("INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'bot', ?)",
                 (mid, cid, ai_content))
    conn.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
    conn.commit()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(msg)
