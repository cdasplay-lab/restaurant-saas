"""
routers/products.py — NUMBER 43: Products + Menu Images + Categories + Upload.

Routes:
  GET/POST /api/products, GET/PATCH/DELETE /api/products/{pid},
  PATCH /api/products/{pid}/availability,
  PATCH /api/products/{product_id}/sold-out-today,
  GET/POST/PUT/DELETE /api/menu-images, POST /api/menu-images/reorder,
  PATCH /api/categories/rename, DELETE /api/categories/{name},
  POST /api/upload/product-image, POST /api/upload/bulk-product-images,
  POST /api/upload/gallery-image, POST /api/upload/menu-image

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import json
import logging
import os
import uuid
from datetime import date as _date
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

import database
from dependencies import current_user, require_role
from helpers import _check_plan_limit, log_activity

logger = logging.getLogger("restaurant-saas")

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


# ── Menu Images ────────────────────────────────────────────────────────────────

@router.get("/api/menu-images")
async def list_menu_images(user=Depends(current_user)):
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM menu_images WHERE restaurant_id=? ORDER BY sort_order ASC, created_at ASC",
            (user["restaurant_id"],)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/menu-images", status_code=201)
async def create_menu_image(data: dict, user=Depends(current_user)):
    image_url = (data.get("image_url") or "").strip()
    if not image_url:
        raise HTTPException(400, "image_url مطلوب")
    mid = str(uuid.uuid4())
    conn = database.get_db()
    try:
        conn.execute(
            """INSERT INTO menu_images (id, restaurant_id, title, image_url, category, sort_order, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                user["restaurant_id"],
                (data.get("title") or "").strip(),
                image_url,
                (data.get("category") or "").strip(),
                int(data.get("sort_order") or 0),
                1 if data.get("is_active", True) else 0,
            )
        )
        conn.commit()
        row = conn.execute("SELECT * FROM menu_images WHERE id=?", (mid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.put("/api/menu-images/{mid}")
async def update_menu_image(mid: str, data: dict, user=Depends(current_user)):
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT id FROM menu_images WHERE id=? AND restaurant_id=?",
            (mid, user["restaurant_id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "صورة المنيو غير موجودة")
        fields, vals = [], []
        for col in ("title", "image_url", "category"):
            if col in data:
                fields.append(f"{col}=?")
                vals.append((data[col] or "").strip())
        if "sort_order" in data:
            fields.append("sort_order=?")
            vals.append(int(data["sort_order"] or 0))
        if "is_active" in data:
            fields.append("is_active=?")
            vals.append(1 if data["is_active"] else 0)
        if not fields:
            raise HTTPException(400, "لا توجد بيانات للتحديث")
        fields.append("updated_at=CURRENT_TIMESTAMP")
        vals.append(mid)
        conn.execute(f"UPDATE menu_images SET {', '.join(fields)} WHERE id=?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM menu_images WHERE id=?", (mid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/api/menu-images/{mid}")
async def delete_menu_image(mid: str, user=Depends(current_user)):
    conn = database.get_db()
    try:
        if not conn.execute(
            "SELECT id FROM menu_images WHERE id=? AND restaurant_id=?",
            (mid, user["restaurant_id"])
        ).fetchone():
            raise HTTPException(404, "صورة المنيو غير موجودة")
        conn.execute("DELETE FROM menu_images WHERE id=?", (mid,))
        conn.commit()
        return {"message": "تم الحذف"}
    finally:
        conn.close()


@router.post("/api/menu-images/reorder")
async def reorder_menu_images(req: Request, user=Depends(current_user)):
    body = await req.json()
    items = body.get("items", [])
    if not items:
        return {"ok": True}
    conn = database.get_db()
    try:
        for item in items:
            conn.execute(
                "UPDATE menu_images SET sort_order=? WHERE id=? AND restaurant_id=?",
                (item["sort_order"], item["id"], user["restaurant_id"])
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── Categories ─────────────────────────────────────────────────────────────────

@router.patch("/api/categories/rename")
async def rename_category(data: dict, user=Depends(current_user)):
    old_name = (data.get("old_name") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    if not old_name or not new_name:
        raise HTTPException(400, "اسم الفئة مطلوب")
    conn = database.get_db()
    try:
        conn.execute(
            "UPDATE products SET category=? WHERE category=? AND restaurant_id=?",
            (new_name, old_name, user["restaurant_id"]))
        conn.commit()
        return {"message": "تم التعديل"}
    finally:
        conn.close()


@router.delete("/api/categories/{name}")
async def delete_category(name: str, user=Depends(current_user)):
    conn = database.get_db()
    try:
        conn.execute(
            "UPDATE products SET category='Main' WHERE category=? AND restaurant_id=?",
            (name, user["restaurant_id"]))
        conn.commit()
        return {"message": "تم حذف الفئة ونقل المنتجات إلى Main"}
    finally:
        conn.close()


# ── Upload: Product / Gallery / Menu Images ────────────────────────────────────

@router.post("/api/upload/product-image", status_code=201)
async def upload_product_image(
    file: UploadFile = File(...),
    product_id: str = "",
    user=Depends(require_role("owner", "manager")),
):
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"نوع الملف غير مدعوم: {ext}")

    content = await file.read()
    if len(content) / (1024 * 1024) > 10:
        raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 10 MB")

    pid = product_id or str(uuid.uuid4())
    fname = f"{uuid.uuid4()}{ext}"
    storage_path = _storage.product_storage_path(user["restaurant_id"], pid, fname)

    public_url = _storage.upload_bytes(
        content,
        _storage.BUCKET_PRODUCTS,
        storage_path,
        content_type=file.content_type or "image/jpeg",
    )

    if not public_url:
        return {"url": "", "message": "Supabase not configured — configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"}

    if product_id:
        conn = database.get_db()
        row = conn.execute("SELECT id FROM products WHERE id=? AND restaurant_id=?",
                           (product_id, user["restaurant_id"])).fetchone()
        if row:
            conn.execute("UPDATE products SET image_url=? WHERE id=?", (public_url, product_id))
            conn.commit()
        conn.close()

    return {"url": public_url, "product_id": pid}


@router.post("/api/upload/bulk-product-images", status_code=200)
async def bulk_upload_product_images(
    files: List[UploadFile] = File(...),
    user=Depends(require_role("owner", "manager")),
):
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT id, name FROM products WHERE restaurant_id=?",
            (user["restaurant_id"],),
        ).fetchall()
    finally:
        conn.close()

    product_map: dict = {dict(r)["name"].strip().lower(): dict(r) for r in rows}
    matched: list = []
    unmatched: list = []

    for f in files:
        filename = f.filename or ""
        ext = Path(filename).suffix.lower()
        stem = Path(filename).stem.strip()

        if ext not in ALLOWED:
            unmatched.append({"file": filename, "reason": f"نوع الملف غير مدعوم: {ext}"})
            continue

        content = await f.read()
        if len(content) > 10 * 1024 * 1024:
            unmatched.append({"file": filename, "reason": "الملف أكبر من 10 MB"})
            continue
        if not content:
            unmatched.append({"file": filename, "reason": "الملف فارغ"})
            continue

        product = product_map.get(stem.lower())
        if not product:
            unmatched.append({"file": filename, "reason": "لم يُعثر على منتج بهذا الاسم"})
            continue

        fname = f"{uuid.uuid4()}{ext}"
        storage_path = _storage.product_storage_path(
            user["restaurant_id"], product["id"], fname
        )
        public_url = _storage.upload_bytes(
            content,
            _storage.BUCKET_PRODUCTS,
            storage_path,
            content_type=f.content_type or "image/jpeg",
        )

        if not public_url:
            unmatched.append({"file": filename, "reason": "Supabase غير مُهيأ — تحقق من SUPABASE_URL"})
            continue

        conn = database.get_db()
        conn.execute("UPDATE products SET image_url=? WHERE id=?", (public_url, product["id"]))
        conn.commit()
        conn.close()

        matched.append({
            "file":         filename,
            "product_id":   product["id"],
            "product_name": product["name"],
            "image_url":    public_url,
        })

    return {
        "matched":         matched,
        "unmatched":       unmatched,
        "total":           len(files),
        "matched_count":   len(matched),
        "unmatched_count": len(unmatched),
    }


@router.post("/api/upload/gallery-image", status_code=201)
async def upload_gallery_image(
    file: UploadFile = File(...),
    product_id: str = "",
    user=Depends(require_role("owner", "manager")),
):
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"نوع الملف غير مدعوم: {ext}")

    content = await file.read()
    if len(content) / (1024 * 1024) > 10:
        raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 10 MB")

    pid = product_id or str(uuid.uuid4())
    fname = f"{uuid.uuid4()}{ext}"
    storage_path = _storage.gallery_image_path(user["restaurant_id"], pid, fname)

    public_url = _storage.upload_bytes(
        content,
        _storage.BUCKET_PRODUCTS,
        storage_path,
        content_type=file.content_type or "image/jpeg",
    )

    if not public_url:
        return {"url": "", "message": "Supabase not configured"}

    if product_id:
        conn = database.get_db()
        row = conn.execute("SELECT gallery_images FROM products WHERE id=? AND restaurant_id=?",
                           (product_id, user["restaurant_id"])).fetchone()
        if row:
            gallery = json.loads(row["gallery_images"] or "[]")
            gallery.append(public_url)
            conn.execute("UPDATE products SET gallery_images=? WHERE id=?",
                         (json.dumps(gallery), product_id))
            conn.commit()
        conn.close()

    return {"url": public_url, "product_id": pid}


@router.post("/api/upload/menu-image", status_code=201)
async def upload_menu_image(
    file: UploadFile = File(...),
    user=Depends(require_role("owner", "manager")),
):
    from services import storage as _storage

    ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"نوع الملف غير مدعوم: {ext}")

    content = await file.read()
    if len(content) / (1024 * 1024) > 15:
        raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 15 MB")

    fname = f"{uuid.uuid4()}{ext}"

    public_url = None
    try:
        storage_path = _storage.menu_image_path(user["restaurant_id"], fname)
        public_url = _storage.upload_bytes(
            content, _storage.BUCKET_MENUS, storage_path,
            content_type=file.content_type or "image/jpeg",
        )
    except Exception as e:
        logger.warning(f"Supabase upload failed, falling back to local: {e}")

    if not public_url:
        local_dir = Path("uploads/menu-images")
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / fname
        local_path.write_bytes(content)
        base = os.getenv("BASE_URL", "").rstrip("/")
        public_url = f"{base}/uploads/menu-images/{fname}"

    return {"url": public_url}
