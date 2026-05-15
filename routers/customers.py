"""
routers/customers.py — NUMBER 43: Customers CRUD extracted from main.py.

Routes: GET/PATCH/DELETE /api/customers, GET /api/customers/{cid}

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from dependencies import current_user

router = APIRouter()


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    vip: Optional[bool] = None
    preferences: Optional[str] = None
    favorite_item: Optional[str] = None


@router.get("/api/customers")
async def list_customers(search: Optional[str] = None, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    if search:
        rows = conn.execute(
            "SELECT * FROM customers WHERE restaurant_id=? AND (name LIKE ? OR phone LIKE ?) ORDER BY total_spent DESC",
            (rid, f"%{search}%", f"%{search}%")).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM customers WHERE restaurant_id=? ORDER BY total_spent DESC", (rid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/customers/{cid}")
async def get_customer(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM customers WHERE id=? AND restaurant_id=?",
                       (cid, user["restaurant_id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Customer not found")
    orders = conn.execute(
        "SELECT * FROM orders WHERE customer_id=? ORDER BY created_at DESC LIMIT 10", (cid,)).fetchall()
    conn.close()
    result = dict(row)
    result["orders"] = [dict(o) for o in orders]
    return result


@router.patch("/api/customers/{cid}")
async def update_customer(cid: str, data: CustomerUpdate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM customers WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Customer not found")
    upd = {}
    if data.name is not None: upd["name"] = data.name
    if data.phone is not None: upd["phone"] = data.phone
    if data.vip is not None: upd["vip"] = int(data.vip)
    if data.preferences is not None: upd["preferences"] = data.preferences
    if data.favorite_item is not None: upd["favorite_item"] = data.favorite_item
    if upd:
        conn.execute(f"UPDATE customers SET {','.join(k+'=?' for k in upd)} WHERE id=?",
                     list(upd.values()) + [cid])
        conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/customers/{cid}")
async def delete_customer(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM customers WHERE id=? AND restaurant_id=?",
                        (cid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Customer not found")
    conn.execute("DELETE FROM customers WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}
