"""
routers/promo_codes.py — NUMBER 43: Promo Codes extracted from main.py.

Routes: GET/POST/PATCH/DELETE /api/promo-codes, POST /api/promo-codes/validate

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

import database
from dependencies import current_user

router = APIRouter()


@router.get("/api/promo-codes")
async def list_promo_codes(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM promo_codes WHERE restaurant_id=? ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return {"promo_codes": [dict(r) for r in rows]}


@router.post("/api/promo-codes", status_code=201)
async def create_promo_code(req: Request, user=Depends(current_user)):
    data = await req.json()
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(400, "كود الخصم مطلوب")
    discount_type  = data.get("discount_type", "percent")
    discount_value = float(data.get("discount_value") or 0)
    min_order      = float(data.get("min_order") or 0)
    max_uses       = int(data.get("max_uses") or 0)
    expires_at     = str(data.get("expires_at") or "")
    if discount_type not in ("percent", "fixed"):
        raise HTTPException(400, "discount_type يجب أن يكون percent أو fixed")
    if discount_type == "percent" and not (0 < discount_value <= 100):
        raise HTTPException(400, "نسبة الخصم يجب أن تكون بين 1 و 100")
    pid = str(uuid.uuid4())
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO promo_codes (id, restaurant_id, code, discount_type, discount_value, "
            "min_order, max_uses, expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, user["restaurant_id"], code, discount_type, discount_value,
             min_order, max_uses, expires_at)
        )
        conn.commit()
    except Exception as _e:
        conn.close()
        raise HTTPException(409, "الكود موجود مسبقاً") from _e
    row = conn.execute("SELECT * FROM promo_codes WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row)


@router.patch("/api/promo-codes/{pid}")
async def update_promo_code(pid: str, req: Request, user=Depends(current_user)):
    data = await req.json()
    conn = database.get_db()
    if not conn.execute("SELECT id FROM promo_codes WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404)
    allowed = {"discount_type", "discount_value", "min_order", "max_uses", "expires_at", "is_active"}
    upd = {k: v for k, v in data.items() if k in allowed}
    if upd:
        set_clause = ", ".join(f"{k}=?" for k in upd)
        set_clause += ", updated_at=CURRENT_TIMESTAMP"
        vals = list(upd.values())
        conn.execute(f"UPDATE promo_codes SET {set_clause} WHERE id=?", [*vals, pid])
        conn.commit()
    row = conn.execute("SELECT * FROM promo_codes WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/api/promo-codes/{pid}")
async def delete_promo_code(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM promo_codes WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404)
    conn.execute("DELETE FROM promo_codes WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/promo-codes/validate")
async def validate_promo_code(req: Request, user=Depends(current_user)):
    data = await req.json()
    code  = (data.get("code") or "").strip().upper()
    total = float(data.get("order_total") or 0)
    if not code:
        raise HTTPException(400, "كود مطلوب")
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM promo_codes WHERE restaurant_id=? AND code=? AND is_active=1",
        (user["restaurant_id"], code)
    ).fetchone()
    conn.close()
    if not row:
        return {"valid": False, "reason": "الكود غير صحيح أو منتهي"}
    row = dict(row)
    if row["expires_at"] and row["expires_at"] < str(datetime.now().date()):
        return {"valid": False, "reason": "انتهت صلاحية الكود"}
    if row["max_uses"] > 0 and row["uses_count"] >= row["max_uses"]:
        return {"valid": False, "reason": "استُنفد الحد الأقصى لاستخدامات هذا الكود"}
    if total < row["min_order"]:
        return {"valid": False, "reason": f"الحد الأدنى للطلب لاستخدام هذا الكود {row['min_order']:,.0f} د.ع"}
    if row["discount_type"] == "percent":
        discount = round(total * row["discount_value"] / 100)
    else:
        discount = min(row["discount_value"], total)
    return {
        "valid": True,
        "discount_type": row["discount_type"],
        "discount_value": row["discount_value"],
        "discount_amount": discount,
        "final_total": max(0, total - discount),
    }
