"""
routers/branches.py — NUMBER 43: Branches extracted from main.py.

Routes: GET/POST /api/branches, PATCH/DELETE /api/branches/{bid},
        POST /api/branches/{bid}/set-default

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

import database
from dependencies import current_user
from helpers import _check_plan_limit

router = APIRouter()


@router.get("/api/branches")
async def list_branches(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM branches WHERE restaurant_id=? ORDER BY is_default DESC, created_at ASC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return {"branches": [dict(r) for r in rows]}


@router.post("/api/branches", status_code=201)
async def create_branch(req: Request, user=Depends(current_user)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "اسم الفرع مطلوب")
    conn = database.get_db()
    sub = conn.execute(
        "SELECT plan FROM subscriptions WHERE restaurant_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
        (user["restaurant_id"],)
    ).fetchone()
    plan = sub["plan"] if sub else "trial"
    _check_plan_limit(conn, user["restaurant_id"], plan, "branches", "branches")
    bid = str(uuid.uuid4())
    is_default = 1 if not conn.execute(
        "SELECT id FROM branches WHERE restaurant_id=?", (user["restaurant_id"],)
    ).fetchone() else 0
    conn.execute(
        "INSERT INTO branches (id, restaurant_id, name, address, phone, working_hours, is_default) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, user["restaurant_id"], name,
         body.get("address", ""), body.get("phone", ""),
         json.dumps(body.get("working_hours", {})), is_default)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return dict(row)


@router.patch("/api/branches/{bid}")
async def update_branch(bid: str, req: Request, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM branches WHERE id=? AND restaurant_id=?",
                        (bid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "الفرع غير موجود")
    body = await req.json()
    allowed = {"name", "address", "phone", "working_hours", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "working_hours" in updates:
        updates["working_hours"] = json.dumps(updates["working_hours"]) if isinstance(updates["working_hours"], dict) else updates["working_hours"]
    if not updates:
        conn.close()
        return {"ok": True}
    vals = list(updates.values())
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE branches SET {set_clause} WHERE id=?", [*vals, bid])
    conn.commit()
    row = conn.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/branches/{bid}")
async def delete_branch(bid: str, user=Depends(current_user)):
    conn = database.get_db()
    branch = conn.execute(
        "SELECT * FROM branches WHERE id=? AND restaurant_id=?",
        (bid, user["restaurant_id"])
    ).fetchone()
    if not branch:
        conn.close()
        raise HTTPException(404, "الفرع غير موجود")
    if branch["is_default"]:
        conn.close()
        raise HTTPException(400, "لا يمكن حذف الفرع الرئيسي")
    conn.execute("DELETE FROM branches WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/branches/{bid}/set-default")
async def set_default_branch(bid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM branches WHERE id=? AND restaurant_id=?",
                        (bid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "الفرع غير موجود")
    conn.execute("UPDATE branches SET is_default=0 WHERE restaurant_id=?", (user["restaurant_id"],))
    conn.execute("UPDATE branches SET is_default=1 WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    return {"ok": True}
