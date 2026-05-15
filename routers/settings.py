"""
routers/settings.py — NUMBER 43: Settings extracted from main.py.

Routes: GET/PUT /api/settings

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import database
from dependencies import current_user

router = APIRouter()


class SettingsUpdate(BaseModel):
    restaurant_name: Optional[str] = None
    restaurant_description: Optional[str] = None
    restaurant_phone: Optional[str] = None
    restaurant_address: Optional[str] = None
    working_hours: Optional[dict] = None
    bot_name: Optional[str] = None
    bot_personality: Optional[str] = None
    bot_language: Optional[str] = None
    bot_welcome: Optional[str] = None
    bot_enabled: Optional[bool] = None
    security_2fa: Optional[bool] = None
    security_session_timeout: Optional[int] = None
    payment_methods: Optional[str] = None
    business_type: Optional[str] = None
    delivery_time: Optional[str] = None
    notify_chat_id: Optional[str] = None
    delivery_fee: Optional[int] = None
    min_order: Optional[int] = None
    report_frequency: Optional[str] = None  # none / daily / weekly
    menu_url: Optional[str] = None


@router.get("/api/settings")
async def get_settings(user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM settings WHERE restaurant_id=?",
                       (user["restaurant_id"],)).fetchone()
    conn.close()
    return dict(row) if row else {}


@router.put("/api/settings")
async def update_settings(data: SettingsUpdate, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    if not conn.execute("SELECT id FROM settings WHERE restaurant_id=?", (rid,)).fetchone():
        conn.execute("INSERT INTO settings (id, restaurant_id) VALUES (?, ?)", (str(uuid.uuid4()), rid))
        conn.commit()
    upd = {}
    if data.restaurant_name is not None: upd["restaurant_name"] = data.restaurant_name
    if data.restaurant_description is not None: upd["restaurant_description"] = data.restaurant_description
    if data.restaurant_phone is not None: upd["restaurant_phone"] = data.restaurant_phone
    if data.restaurant_address is not None: upd["restaurant_address"] = data.restaurant_address
    if data.working_hours is not None: upd["working_hours"] = json.dumps(data.working_hours)
    if data.bot_name is not None: upd["bot_name"] = data.bot_name
    if data.bot_personality is not None: upd["bot_personality"] = data.bot_personality
    if data.bot_language is not None: upd["bot_language"] = data.bot_language
    if data.bot_welcome is not None: upd["bot_welcome"] = data.bot_welcome
    if data.bot_enabled is not None: upd["bot_enabled"] = int(data.bot_enabled)
    if data.security_2fa is not None: upd["security_2fa"] = int(data.security_2fa)
    if data.security_session_timeout is not None: upd["security_session_timeout"] = data.security_session_timeout
    if data.payment_methods is not None: upd["payment_methods"] = data.payment_methods
    if data.business_type is not None: upd["business_type"] = data.business_type
    if data.delivery_time is not None: upd["delivery_time"] = data.delivery_time
    if data.notify_chat_id is not None: upd["notify_chat_id"] = data.notify_chat_id
    if data.delivery_fee is not None: upd["delivery_fee"] = data.delivery_fee
    if data.min_order is not None: upd["min_order"] = data.min_order
    if data.report_frequency is not None and data.report_frequency in ("none", "daily", "weekly"):
        upd["report_frequency"] = data.report_frequency
    if data.menu_url is not None: upd["menu_url"] = data.menu_url
    if upd:
        conn.execute(f"UPDATE settings SET {','.join(k+'=?' for k in upd)} WHERE restaurant_id=?",
                     list(upd.values()) + [rid])
        # Keep restaurants.name in sync so JWT always has the current name
        if "restaurant_name" in upd and upd["restaurant_name"]:
            conn.execute("UPDATE restaurants SET name=? WHERE id=?", (upd["restaurant_name"], rid))
        conn.commit()
    row = conn.execute("SELECT * FROM settings WHERE restaurant_id=?", (rid,)).fetchone()
    conn.close()
    return dict(row)
