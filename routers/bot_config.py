"""
routers/bot_config.py — NUMBER 43: Bot Config extracted from main.py.

Routes: GET/PUT /api/bot-config,
        GET/POST /api/bot-config/corrections,
        PATCH/DELETE /api/bot-config/corrections/{cid}

Unchanged behavior — same URLs, same response shapes, same auth guards.
"""
from __future__ import annotations
import json
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from dependencies import current_user
from helpers import log_activity

router = APIRouter()


class BotConfigUpdate(BaseModel):
    system_prompt: Optional[str] = None
    sales_prompt: Optional[str] = None
    escalation_keywords: Optional[List[str]] = None
    fallback_message: Optional[str] = None
    max_bot_turns: Optional[int] = None
    auto_handoff_enabled: Optional[bool] = None
    order_extraction_enabled: Optional[bool] = None
    memory_enabled: Optional[bool] = None
    escalation_threshold: Optional[int] = None
    # Brand Voice
    voice_tone: Optional[str] = None
    dialect_override: Optional[str] = None
    custom_greeting: Optional[str] = None
    custom_farewell: Optional[str] = None
    brand_keywords: Optional[str] = None


def _decode_bot_config(d: dict) -> dict:
    try:
        d["escalation_keywords"] = json.loads(d.get("escalation_keywords") or "[]")
    except Exception:
        d["escalation_keywords"] = []
    d["auto_handoff_enabled"] = bool(d.get("auto_handoff_enabled", 1))
    d["order_extraction_enabled"] = bool(d.get("order_extraction_enabled", 1))
    d["memory_enabled"] = bool(d.get("memory_enabled", 1))
    return d


@router.get("/api/bot-config")
async def get_bot_config(user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM bot_config WHERE restaurant_id=?", (user["restaurant_id"],)
    ).fetchone()
    conn.close()
    if not row:
        return {
            "restaurant_id": user["restaurant_id"],
            "system_prompt": "",
            "sales_prompt": "",
            "escalation_keywords": [],
            "fallback_message": "سأحيلك لأحد موظفينا الآن، انتظر قليلاً. 🙏",
            "max_bot_turns": 15,
            "auto_handoff_enabled": True,
            "order_extraction_enabled": True,
            "memory_enabled": True,
            "escalation_threshold": 3,
        }
    return _decode_bot_config(dict(row))


@router.put("/api/bot-config")
async def update_bot_config(data: BotConfigUpdate, user=Depends(current_user)):
    conn = database.get_db()
    rid = user["restaurant_id"]
    existing = conn.execute("SELECT id FROM bot_config WHERE restaurant_id=?", (rid,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO bot_config (id, restaurant_id) VALUES (?, ?)",
            (str(uuid.uuid4()), rid)
        )
        conn.commit()

    upd = {}
    if data.system_prompt is not None: upd["system_prompt"] = data.system_prompt
    if data.sales_prompt is not None: upd["sales_prompt"] = data.sales_prompt
    if data.escalation_keywords is not None: upd["escalation_keywords"] = json.dumps(data.escalation_keywords)
    if data.fallback_message is not None: upd["fallback_message"] = data.fallback_message
    if data.max_bot_turns is not None: upd["max_bot_turns"] = data.max_bot_turns
    if data.auto_handoff_enabled is not None: upd["auto_handoff_enabled"] = int(data.auto_handoff_enabled)
    if data.order_extraction_enabled is not None: upd["order_extraction_enabled"] = int(data.order_extraction_enabled)
    if data.memory_enabled is not None: upd["memory_enabled"] = int(data.memory_enabled)
    if data.escalation_threshold is not None: upd["escalation_threshold"] = data.escalation_threshold
    if data.voice_tone is not None: upd["voice_tone"] = data.voice_tone
    if data.dialect_override is not None: upd["dialect_override"] = data.dialect_override
    if data.custom_greeting is not None: upd["custom_greeting"] = data.custom_greeting
    if data.custom_farewell is not None: upd["custom_farewell"] = data.custom_farewell
    if data.brand_keywords is not None: upd["brand_keywords"] = data.brand_keywords

    if upd:
        conn.execute(f"UPDATE bot_config SET {','.join(k+'=?' for k in upd)} WHERE restaurant_id=?",
                     list(upd.values()) + [rid])
        log_activity(conn, rid, "bot_config_updated", "bot_config", rid,
                     f"تعديل إعدادات البوت: {', '.join(upd.keys())}", user["id"], user.get("name", ""))
        conn.commit()

    row = conn.execute("SELECT * FROM bot_config WHERE restaurant_id=?", (rid,)).fetchone()
    conn.close()
    return _decode_bot_config(dict(row))


@router.get("/api/bot-config/corrections")
async def list_bot_corrections(user=Depends(current_user)):
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM bot_corrections WHERE restaurant_id=? ORDER BY created_at DESC",
        (user["restaurant_id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/bot-config/corrections")
async def add_bot_correction(data: dict, user=Depends(current_user)):
    trigger_text    = (data.get("trigger_text") or "").strip()
    correction_text = (data.get("correction_text") or "").strip()
    legacy_text     = (data.get("text") or "").strip()

    if trigger_text and correction_text:
        display_text = f'إذا قال "{trigger_text[:60]}" → "{correction_text[:60]}"'
    elif legacy_text:
        display_text = legacy_text
    else:
        raise HTTPException(400, "trigger_text+correction_text or text is required")

    rid = user["restaurant_id"]
    added_by = user.get("name") or user.get("email") or ""
    conn = database.get_db()

    if trigger_text:
        existing = conn.execute(
            "SELECT id FROM bot_corrections WHERE restaurant_id=? AND trigger_text=?",
            (rid, trigger_text)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE bot_corrections SET correction_text=?, text=?, is_active=1, added_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (correction_text, display_text, added_by, existing["id"])
            )
            conn.commit(); conn.close()
            return {"ok": True, "deduped": True}
    else:
        existing = conn.execute(
            "SELECT id, is_active FROM bot_corrections WHERE restaurant_id=? AND text=?",
            (rid, display_text)
        ).fetchone()
        if existing:
            if not existing["is_active"]:
                conn.execute("UPDATE bot_corrections SET is_active=1, added_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (added_by, existing["id"]))
                conn.commit()
            conn.close()
            return {"ok": True, "deduped": True}

    conn.execute(
        "INSERT INTO bot_corrections (id, restaurant_id, text, trigger_text, correction_text, added_by, is_active) VALUES (?,?,?,?,?,?,1)",
        (str(uuid.uuid4()), rid, display_text, trigger_text, correction_text, added_by)
    )
    conn.commit(); conn.close()
    return {"ok": True}


@router.patch("/api/bot-config/corrections/{cid}")
async def toggle_bot_correction(cid: str, data: dict, user=Depends(current_user)):
    conn = database.get_db()
    row = conn.execute(
        "SELECT id FROM bot_corrections WHERE id=? AND restaurant_id=?",
        (cid, user["restaurant_id"])
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Correction not found")
    is_active = int(bool(data.get("is_active", True)))
    conn.execute("UPDATE bot_corrections SET is_active=? WHERE id=?", (is_active, cid))
    conn.commit()
    conn.close()
    return {"ok": True, "is_active": bool(is_active)}


@router.delete("/api/bot-config/corrections/{cid}")
async def delete_bot_correction(cid: str, user=Depends(current_user)):
    conn = database.get_db()
    conn.execute(
        "DELETE FROM bot_corrections WHERE id=? AND restaurant_id=?",
        (cid, user["restaurant_id"])
    )
    conn.commit()
    conn.close()
    return {"ok": True}
