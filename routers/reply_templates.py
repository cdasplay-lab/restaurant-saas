"""
routers/reply_templates.py — NUMBER 43: Reply Templates extracted from main.py.

Routes: GET/POST /api/reply-templates, DELETE /api/reply-templates/{tid}

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

import database
from dependencies import current_user

router = APIRouter()


@router.get("/api/reply-templates")
async def list_reply_templates(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM reply_templates WHERE restaurant_id=? ORDER BY created_at ASC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/reply-templates", status_code=201)
async def create_reply_template(req: Request, user=Depends(current_user)):
    body = await req.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "العنوان والمحتوى مطلوبان")
    tid = str(uuid.uuid4())
    conn = database.get_db()
    conn.execute(
        "INSERT INTO reply_templates (id, restaurant_id, title, content) VALUES (?,?,?,?)",
        (tid, user["restaurant_id"], title, content)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM reply_templates WHERE id=?", (tid,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/reply-templates/{tid}")
async def delete_reply_template(tid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM reply_templates WHERE id=? AND restaurant_id=?",
                        (tid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404)
    conn.execute("DELETE FROM reply_templates WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"ok": True}
