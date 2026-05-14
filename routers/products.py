"""
routers/products.py — NUMBER 43: Products CRUD extracted from main.py.

Routes: GET/POST /api/products, GET/PATCH/DELETE /api/products/{pid},
        PATCH /api/products/{pid}/availability,
        PATCH /api/products/{product_id}/sold-out-today

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import json
import uuid
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from dependencies import current_user, require_role
from helpers import _check_plan_limit, log_activity

router = APIRouter()


class ProductCreate(BaseModel):
    name: str
    price: float
    category: str = "Main"
    description: str = ""
    icon: str = "🍽️"
    variants: list = []
    available: bool = True
    image: str = ""
    image_url: str = ""
    gallery_images: list = []


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    variants: Optional[list] = None
    available: Optional[bool] = None
    image: Optional[str] = None
    image_url: Optional[str] = None
    gallery_images: Optional[list] = None


def _decode_product(row) -> dict:
    d = dict(row)
    d["variants"] = json.loads(d.get("variants") or "[]")
    d["gallery_images"] = json.loads(d.get("gallery_images") or "[]")
    return d


@router.get("/api/products")
async def list_products(category: Optional[str] = None, user=Depends(current_user)):
    conn = database.get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM products WHERE restaurant_id=? AND category=? ORDER BY name",
            (user["restaurant_id"], category)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM products WHERE restaurant_id=? ORDER BY name",
            (user["restaurant_id"],)).fetchall()
    conn.close()
    return [_decode_product(r) for r in rows]


@router.get("/api/products/{pid}")
async def get_product(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM products WHERE id=? AND restaurant_id=?",
        (pid, user["restaurant_id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Product not found")
    return _decode_product(row)


@router.post("/api/products", status_code=201)
async def create_product(data: ProductCreate, user=Depends(current_user)):
    conn = database.get_db()
    _check_plan_limit(conn, user["restaurant_id"], user.get("plan", "trial"), "products", "products")
    pid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO products (id, restaurant_id, name, price, category, description, icon, variants, available, image, image_url, gallery_images)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (pid, user["restaurant_id"], data.name, data.price, data.category,
          data.description, data.icon, json.dumps(data.variants), int(data.available),
          data.image, data.image_url, json.dumps(data.gallery_images)))
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _decode_product(row)


@router.patch("/api/products/{pid}")
async def update_product(pid: str, data: ProductUpdate, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM products WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Product not found")
    upd = {}
    if data.name is not None: upd["name"] = data.name
    if data.price is not None: upd["price"] = data.price
    if data.category is not None: upd["category"] = data.category
    if data.description is not None: upd["description"] = data.description
    if data.icon is not None: upd["icon"] = data.icon
    if data.variants is not None: upd["variants"] = json.dumps(data.variants)
    if data.available is not None: upd["available"] = int(data.available)
    if data.image is not None: upd["image"] = data.image
    if data.image_url is not None: upd["image_url"] = data.image_url
    if data.gallery_images is not None: upd["gallery_images"] = json.dumps(data.gallery_images)
    if upd:
        sets = ", ".join(f"{k}=?" for k in upd) + ", updated_at=datetime('now')"
        conn.execute(f"UPDATE products SET {sets} WHERE id=?", list(upd.values()) + [pid])
        log_activity(conn, user["restaurant_id"], "product_updated", "product", pid,
                     f"تعديل المنتج: {', '.join(upd.keys())}", user["id"], user.get("name", ""))
        conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _decode_product(row)


@router.patch("/api/products/{pid}/availability")
async def toggle_availability(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("SELECT available FROM products WHERE id=? AND restaurant_id=?",
                       (pid, user["restaurant_id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Product not found")
    new_val = 1 - row["available"]
    conn.execute("UPDATE products SET available=? WHERE id=?", (new_val, pid))
    conn.commit()
    conn.close()
    return {"available": bool(new_val)}


@router.patch("/api/products/{product_id}/sold-out-today")
async def toggle_sold_out_today(product_id: str, user=Depends(require_role("owner", "manager", "staff"))):
    conn = database.get_db()
    try:
        p = conn.execute("SELECT * FROM products WHERE id=? AND restaurant_id=?",
                         (product_id, user["restaurant_id"])).fetchone()
        if not p:
            raise HTTPException(404, "المنتج غير موجود")
        today = _date.today().isoformat()
        current = p["sold_out_date"] if "sold_out_date" in p.keys() else ""
        new_val = today if current != today else ""
        conn.execute("UPDATE products SET sold_out_date=? WHERE id=?", (new_val, product_id))
        conn.commit()
        return {"sold_out_today": new_val == today}
    finally:
        conn.close()


@router.delete("/api/products/{pid}")
async def delete_product(pid: str, user=Depends(current_user)):
    conn = database.get_db()
    if not conn.execute("SELECT id FROM products WHERE id=? AND restaurant_id=?",
                        (pid, user["restaurant_id"])).fetchone():
        conn.close()
        raise HTTPException(404, "Product not found")
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"message": "تم الحذف"}
