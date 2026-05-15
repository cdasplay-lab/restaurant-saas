"""
routers/staff.py — NUMBER 43: Staff Management extracted from main.py.

Routes: GET/POST /api/staff, PATCH/DELETE /api/staff/{uid}

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from dependencies import current_user, require_role
from helpers import _check_plan_limit, log_activity, _hash_password

router = APIRouter()


class StaffCreate(BaseModel):
    email: str
    name: str
    password: str
    role: str = "staff"


class StaffUpdate(BaseModel):
    role: str


@router.get("/api/staff")
async def list_staff(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT id, name, email, role, created_at, last_login FROM users WHERE restaurant_id=? ORDER BY created_at",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/staff", status_code=201)
async def create_staff(data: StaffCreate, user=Depends(require_role("owner", "manager"))):
    if data.role == "owner" and user["role"] != "owner":
        raise HTTPException(403, "فقط المالك يمكنه إضافة مالك آخر")
    conn = database.get_db()
    _check_plan_limit(conn, user["restaurant_id"], user.get("plan", "trial"), "staff", "users")
    existing = conn.execute("SELECT id FROM users WHERE email=?", (data.email,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "البريد الإلكتروني مستخدم بالفعل")
    uid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO users (id, restaurant_id, email, password_hash, name, role)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (uid, user["restaurant_id"], data.email, _hash_password(data.password), data.name, data.role))
    log_activity(conn, user["restaurant_id"], "staff_added", "user", uid,
                 f"تمت إضافة {data.name} بدور {data.role}", user["id"], user["name"])
    conn.commit()
    row = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    conn.close()
    return dict(row)


@router.patch("/api/staff/{uid}")
async def update_staff_role(uid: str, data: StaffUpdate, user=Depends(require_role("owner"))):
    conn = database.get_db()
    target = conn.execute(
        "SELECT * FROM users WHERE id=? AND restaurant_id=?", (uid, user["restaurant_id"])
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "المستخدم غير موجود")
    if uid == user["id"]:
        conn.close()
        raise HTTPException(400, "لا يمكنك تغيير دورك بنفسك")
    conn.execute("UPDATE users SET role=? WHERE id=?", (data.role, uid))
    log_activity(conn, user["restaurant_id"], "staff_role_changed", "user", uid,
                 f"تغيير دور {target['name']} إلى {data.role}", user["id"], user["name"])
    conn.commit()
    row = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/staff/{uid}")
async def delete_staff(uid: str, user=Depends(require_role("owner"))):
    conn = database.get_db()
    target = conn.execute(
        "SELECT * FROM users WHERE id=? AND restaurant_id=?", (uid, user["restaurant_id"])
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "المستخدم غير موجود")
    if uid == user["id"]:
        conn.close()
        raise HTTPException(400, "لا يمكنك حذف حسابك الخاص")
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    log_activity(conn, user["restaurant_id"], "staff_removed", "user", uid,
                 f"تمت إزالة {target['name']}", user["id"], user["name"])
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}
