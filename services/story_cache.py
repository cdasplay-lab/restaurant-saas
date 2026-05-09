"""
NUMBER 33 — Story context cache.
Prevents repeated Vision API calls for the same story_id.

Cache hierarchy:
  1. In-memory dict (fast, process-local, capped at 500 entries)
  2. DB table story_context_cache (persistent, survives restart, tenant-isolated)

Key: {restaurant_id}:{channel}:id:{story_id}   (when story_id known)
   or {restaurant_id}:{channel}:url:{url_hash}  (fallback by media URL)
"""
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Optional

import database

logger = logging.getLogger("restaurant-saas")

STORY_CACHE_TTL_HOURS = float(os.getenv("STORY_CACHE_TTL_HOURS", "24"))
_FAIL_TTL_HOURS = 0.5  # 30-min negative cache on analysis failure
_MEM_MAX = 500         # max in-memory entries before oldest eviction

# In-memory fallback: cache_key → (expires_unix_ts, data_dict)
_mem: dict = {}
_mem_counter = {"hits": 0, "misses": 0, "stores": 0}
_db_counter   = {"hits": 0, "misses": 0, "stores": 0}


# ── helpers ───────────────────────────────────────────────────────────────────

def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _db_key(restaurant_id: str, channel: str, story_id: str, url_hash: str) -> str:
    if story_id:
        return f"{restaurant_id}:{channel}:id:{story_id}"
    return f"{restaurant_id}:{channel}:url:{url_hash}"


# ── public API ────────────────────────────────────────────────────────────────

def get_cached_story(
    restaurant_id: str,
    channel: str,
    story_id: str,
    media_url: str,
) -> Optional[dict]:
    """
    Return cached story analysis dict or None if not cached / expired.
    Dict keys: context_str, product_id, product_name, product_price,
               product_category, confidence, is_video, raw
    """
    uhash = _url_hash(media_url) if media_url else ""
    key = _db_key(restaurant_id, channel, story_id, uhash)
    now = time.time()

    # 1. In-memory
    if key in _mem:
        exp, data = _mem[key]
        if exp > now:
            _mem_counter["hits"] += 1
            logger.debug(f"[story-cache] MEM HIT {key[:50]}")
            return data
        del _mem[key]

    _mem_counter["misses"] += 1

    # 2. DB
    try:
        conn = database.get_db()
        try:
            row = conn.execute(
                """SELECT * FROM story_context_cache
                   WHERE cache_key=? AND expires_at > datetime('now')
                   LIMIT 1""",
                (key,)
            ).fetchone()
        finally:
            conn.close()

        if row:
            data = _row_to_dict(row)
            # warm in-memory
            _store_mem(key, data, ttl_sec=STORY_CACHE_TTL_HOURS * 3600)
            _db_counter["hits"] += 1
            logger.info(f"[story-cache] DB HIT {key[:50]}")
            return data
    except Exception as e:
        logger.warning(f"[story-cache] DB get error: {e}")

    _db_counter["misses"] += 1
    return None


def store_story_cache(
    restaurant_id: str,
    channel: str,
    story_id: str,
    media_url: str,
    match_data: dict,
    context_str: str,
    is_video: bool,
    is_failure: bool = False,
) -> None:
    """
    Persist story analysis. is_failure=True stores a short negative cache.
    match_data: {product: dict|None, confidence: str, description: str}
    """
    uhash = _url_hash(media_url) if media_url else ""
    key = _db_key(restaurant_id, channel, story_id, uhash)
    ttl_h = _FAIL_TTL_HOURS if is_failure else STORY_CACHE_TTL_HOURS
    ttl_sec = ttl_h * 3600

    product = match_data.get("product") or {}
    data = {
        "context_str": context_str,
        "product_id": product.get("id", ""),
        "product_name": product.get("name", ""),
        "product_price": float(product.get("price", 0) or 0),
        "product_category": product.get("category", ""),
        "confidence": match_data.get("confidence", "low"),
        "is_video": is_video,
        "raw": match_data,
    }

    _store_mem(key, data, ttl_sec=ttl_sec)

    try:
        conn = database.get_db()
        try:
            conn.execute(
                """INSERT INTO story_context_cache
                   (id, cache_key, restaurant_id, channel,
                    platform_story_id, media_url_hash, story_type,
                    matched_product_id, matched_product_name, matched_product_price,
                    matched_category, confidence, analysis_summary, raw_analysis_json,
                    created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           datetime('now'), datetime('now'), datetime('now', ?))
                   ON CONFLICT(cache_key) DO UPDATE SET
                     matched_product_id=excluded.matched_product_id,
                     matched_product_name=excluded.matched_product_name,
                     matched_product_price=excluded.matched_product_price,
                     matched_category=excluded.matched_category,
                     confidence=excluded.confidence,
                     analysis_summary=excluded.analysis_summary,
                     raw_analysis_json=excluded.raw_analysis_json,
                     updated_at=datetime('now'),
                     expires_at=excluded.expires_at""",
                (
                    str(uuid.uuid4()), key, restaurant_id, channel,
                    story_id or "", uhash,
                    "video" if is_video else "image",
                    data["product_id"], data["product_name"], data["product_price"],
                    data["product_category"], data["confidence"],
                    context_str,
                    json.dumps(match_data, ensure_ascii=False, default=str),
                    f"+{ttl_h} hours",
                )
            )
            conn.commit()
            _db_counter["stores"] += 1
            logger.info(f"[story-cache] DB STORE {key[:50]} ttl_h={ttl_h}")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[story-cache] DB store error: {e}")


def get_stats() -> dict:
    return {
        "mem": dict(_mem_counter),
        "db": dict(_db_counter),
        "mem_size": len(_mem),
    }


# ── internal ──────────────────────────────────────────────────────────────────

def _store_mem(key: str, data: dict, ttl_sec: float) -> None:
    if len(_mem) >= _MEM_MAX:
        # evict the entry that expires soonest
        oldest = min(_mem, key=lambda k: _mem[k][0])
        del _mem[oldest]
    _mem[key] = (time.time() + ttl_sec, data)
    _mem_counter["stores"] += 1


def _row_to_dict(row) -> dict:
    return {
        "context_str": row["analysis_summary"] or "",
        "product_id": row["matched_product_id"] or "",
        "product_name": row["matched_product_name"] or "",
        "product_price": float(row["matched_product_price"] or 0),
        "product_category": row["matched_category"] or "",
        "confidence": row["confidence"] or "low",
        "is_video": (row["story_type"] == "video"),
        "raw": json.loads(row["raw_analysis_json"] or "{}"),
    }
