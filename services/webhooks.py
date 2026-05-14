"""
Webhook handlers for Telegram, WhatsApp, Instagram, and Facebook Messenger.
"""
import uuid
import json
import os
import base64
import logging
import threading
from typing import Optional

import httpx

import database
from services import bot
from services import story_cache as _sc
from services.ws_manager import ws_manager

logger = logging.getLogger("restaurant-saas")

# ── Per-conversation mutex — prevents double bot reply on concurrent events ──
_conv_locks: dict = {}          # conv_id → threading.Lock
_conv_locks_mu = threading.Lock()

def _get_conv_lock(conv_id: str) -> threading.Lock:
    """Return (and lazily create) a per-conversation lock."""
    with _conv_locks_mu:
        if conv_id not in _conv_locks:
            _conv_locks[conv_id] = threading.Lock()
        return _conv_locks[conv_id]


_BLOCKED_SUB_STATUSES = {"expired", "suspended", "cancelled"}

def _subscription_active(restaurant_id: str) -> bool:
    """Return False if the restaurant's subscription is blocked. Fails open on DB error."""
    try:
        conn = database.get_db()
        sub  = conn.execute("SELECT status FROM subscriptions WHERE restaurant_id=?", (restaurant_id,)).fetchone()
        rest = conn.execute("SELECT status FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
        conn.close()
        sub_status  = (sub  and sub["status"])  or "active"
        rest_status = (rest and rest["status"]) or "active"
        return sub_status not in _BLOCKED_SUB_STATUSES and rest_status not in _BLOCKED_SUB_STATUSES
    except Exception:
        return True  # fail-open: don't block if DB check fails


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_openai_client = None

# ── Dedup: prevent double-processing of the same event (DB-backed, survives restarts) ──
def _is_duplicate_event(restaurant_id: str, provider: str, event_id: str) -> bool:
    """Return True if this event was already processed. Inserts on first seen, survives restarts."""
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO processed_events (id, restaurant_id, provider, event_id) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), restaurant_id, provider, str(event_id))
        )
        conn.commit()
        return False
    except Exception:
        # UNIQUE constraint violation → duplicate
        return True
    finally:
        conn.close()


def _get_openai():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if not OPENAI_API_KEY:
        return None
    try:
        import openai
        _openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        return _openai_client
    except Exception:
        return None


# ── Telegram ──────────────────────────────────────────────────────────────────

def handle_telegram(restaurant_id: str, update: dict) -> None:
    """Process an incoming Telegram update (text or voice/audio)."""
    update_id = update.get("update_id", "?")
    logger.info(f"[telegram] incoming update #{update_id} for restaurant {restaurant_id}")

    # Dedup — Telegram retries if we don't respond in time, causing double replies
    if _is_duplicate_event(restaurant_id, "telegram", update_id):
        logger.info(f"[telegram] duplicate update #{update_id} — skipping")
        return

    # Early check: restaurant must exist before we do anything
    _conn = database.get_db()
    _rest = _conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    _conn.close()
    if not _rest:
        logger.error(
            f"[telegram] ORPHANED WEBHOOK — restaurant_id={restaurant_id} does NOT exist in DB. "
            f"update_id={update_id}. SQLite was wiped on Render deploy. "
            "ACTION REQUIRED: migrate to PostgreSQL (render.yaml already configured) then re-register webhook."
        )
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
        logger.debug(f"[telegram] update #{update_id} has no message — skipping")
        return

    chat_id = str(message.get("chat", {}).get("id", ""))
    from_user = message.get("from", {})
    first = from_user.get("first_name", "")
    last = from_user.get("last_name", "")
    username = from_user.get("username", "")
    display_name = f"{first} {last}".strip() or username or "مجهول"
    external_id = str(from_user.get("id", chat_id))

    # Detect message type
    text = message.get("text", "").strip()
    media_type = ""
    media_url = ""
    voice_transcript = ""

    voice_obj = message.get("voice") or message.get("audio")
    voice_error_code = ""
    voice_processing_sent = False
    if voice_obj and not text:
        media_type = "voice"
        file_id = voice_obj.get("file_id", "")
        voice_duration = int(voice_obj.get("duration", 0))

        conn_tmp = database.get_db()
        ch_tmp = conn_tmp.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='telegram'",
            (restaurant_id,)
        ).fetchone()
        bot_token = ch_tmp["token"] if ch_tmp else None
        conn_tmp.close()

        if bot_token and file_id:
            # Send wait message before the slow download+transcription
            from services.voice_service import VOICE_PROCESSING_AR as _VPRO
            if _subscription_active(restaurant_id):
                try:
                    _send_telegram(bot_token, chat_id, _VPRO)
                    voice_processing_sent = True
                except Exception:
                    pass
            media_url, voice_transcript, voice_error_code = _download_and_transcribe_telegram(
                bot_token, file_id, duration=voice_duration
            )
        text = "[رسالة صوتية]" if voice_error_code else (voice_transcript or "[رسالة صوتية]")

    # Handle photo messages via OpenAI Vision
    photo_obj = message.get("photo")
    if photo_obj and not text:
        media_type = "image"
        largest = photo_obj[-1]  # Telegram sends multiple sizes; last = largest
        file_id = largest.get("file_id", "")

        conn_tmp = database.get_db()
        ch_tmp = conn_tmp.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='telegram'",
            (restaurant_id,)
        ).fetchone()
        bot_token_tmp = ch_tmp["token"] if ch_tmp else None
        conn_tmp.close()

        if bot_token_tmp and file_id:
            media_url, image_text = _download_and_describe_telegram_image(
                bot_token_tmp, file_id, restaurant_id
            )
            text = image_text or "[العميل أرسل صورة]"
        else:
            text = "[العميل أرسل صورة]"

    if not text:
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='telegram'",
            (restaurant_id,)
        ).fetchone()
        bot_token = ch["token"] if ch else None

        customer = _find_or_create_customer(
            conn, restaurant_id, "telegram", external_id, display_name, ""
        )
        prior_convs = conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE restaurant_id=? AND customer_id=?",
            (restaurant_id, customer["id"])
        ).fetchone()
        is_first_contact = (prior_convs["n"] == 0)
        conversation = _find_or_create_conversation(
            conn, restaurant_id, customer["id"],
            channel="telegram", first_contact=is_first_contact
        )
        conn.commit()

        channel_data = {
            "platform": "telegram",
            "bot_token": bot_token,
            "chat_id": chat_id,
        }
        extra = {
            "media_type": media_type,
            "media_url": media_url,
            "voice_transcript": voice_transcript,
            "voice_error_code": voice_error_code,
            "voice_processing_sent": voice_processing_sent,
        }
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    finally:
        conn.close()


def _download_and_transcribe_telegram(
    bot_token: str, file_id: str, duration: int = 0
) -> tuple:
    """
    Download a Telegram voice file and transcribe via voice_service.
    Returns (file_url, transcript, error_code).
    """
    from services import voice_service as _vs
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                params={"file_id": file_id}
            )
            r.raise_for_status()
            file_info = r.json().get("result", {})
            file_path = file_info.get("file_path", "")
            file_size = int(file_info.get("file_size", 0))
            if not file_path:
                return "", "", _vs.ERR_FAILED

            # Size pre-check before downloading
            if file_size and file_size > _vs.VOICE_MAX_BYTES:
                logger.warning(
                    f"[telegram] voice too large: {file_size} bytes > {_vs.VOICE_MAX_BYTES}"
                )
                return f"https://api.telegram.org/file/bot{bot_token}/{file_path}", "", _vs.ERR_TOO_LARGE

            file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

        audio_bytes = _vs.download_audio_from_url(file_url)
        if not audio_bytes:
            # download_audio_from_url already enforces size cap (returns b"" if exceeded)
            if file_size and file_size > _vs.VOICE_MAX_BYTES:
                return file_url, "", _vs.ERR_TOO_LARGE
            return file_url, "", _vs.ERR_FAILED

        tr = _vs.transcribe_voice_message(
            audio_bytes, filename="voice.ogg", mime_type="audio/ogg",
            channel="telegram", duration_seconds=duration,
        )
        return file_url, tr["text"], tr.get("error_code", "")

    except Exception as e:
        logger.error(f"[telegram] voice transcription error: {e}")
        return "", "", _vs.ERR_FAILED


def _vision_describe(img_bytes: bytes, restaurant_id: str, platform: str = "") -> str:
    """Send raw image bytes to OpenAI Vision. Returns Arabic description string."""
    client_ai = _get_openai()
    if not client_ai:
        return "[العميل أرسل صورة]"

    conn_tmp = database.get_db()
    products = conn_tmp.execute(
        "SELECT name FROM products WHERE restaurant_id=? AND available=1",
        (restaurant_id,)
    ).fetchall()
    conn_tmp.close()
    menu_names = "، ".join(p["name"] for p in products) if products else ""
    menu_hint = f"قائمة المطعم تحتوي على: {menu_names}." if menu_names else ""

    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    try:
        response = client_ai.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"العميل أرسل هذه الصورة. {menu_hint}\n"
                            "بجملة قصيرة بالعربي: ما الذي تظهره الصورة؟ "
                            "إذا كانت الصورة لمنتج موجود في قائمة المطعم اذكر اسمه بالضبط. "
                            "لا تضف تحيات أو شرح — فقط وصف ما في الصورة."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "low"}
                    }
                ]
            }],
            max_tokens=80,
        )
        desc = response.choices[0].message.content.strip()
        logger.info(f"[{platform}] vision result: {desc}")
        return f"[صورة من العميل: {desc}]"
    except Exception as e:
        logger.error(f"[{platform}] vision call error: {e}")
        return "[العميل أرسل صورة]"


def _download_and_describe_telegram_image(bot_token: str, file_id: str, restaurant_id: str) -> tuple:
    """Download a Telegram photo and describe via Vision. Returns (file_url, description)."""
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                params={"file_id": file_id}
            )
            r.raise_for_status()
            file_path = r.json().get("result", {}).get("file_path", "")
            if not file_path:
                return "", ""
            file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            img_bytes = client.get(file_url).content
        return file_url, _vision_describe(img_bytes, restaurant_id, "telegram")
    except Exception as e:
        logger.error(f"[telegram] image download error: {e}")
        return "", "[العميل أرسل صورة]"


def _download_and_describe_url(url: str, restaurant_id: str, platform: str, headers: Optional[dict] = None) -> tuple:
    """Download an image from a URL and describe via Vision. Returns (url, description)."""
    try:
        with httpx.Client(timeout=20) as client:
            img_bytes = client.get(url, headers=headers or {}).content
        return url, _vision_describe(img_bytes, restaurant_id, platform)
    except Exception as e:
        logger.error(f"[{platform}] image download error: {e}")
        return url, "[العميل أرسل صورة]"


# ── Smart Story Analysis ───────────────────────────────────────────────────────

def _get_product_image_bytes(image_url: str) -> Optional[bytes]:
    """Download a product image from Supabase/CDN. Returns bytes or None."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(image_url)
            if resp.status_code == 200 and len(resp.content) > 0:
                return resp.content
    except Exception:
        pass
    return None


def _fetch_story_media(media_url: str, story_id: str, access_token: str) -> tuple:
    """
    Download story media. Handles images and videos.
    For videos: tries to get thumbnail from Graph API first.
    Returns (img_bytes, is_video).
    """
    if not media_url:
        return b"", False
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(media_url)
            content_type = resp.headers.get("content-type", "").lower()

            if "image" in content_type:
                return resp.content, False

            if "video" in content_type:
                # Try Graph API for thumbnail
                if story_id and access_token:
                    try:
                        thumb_resp = client.get(
                            f"https://graph.facebook.com/v19.0/{story_id}",
                            params={"fields": "thumbnail_url", "access_token": access_token},
                            timeout=8,
                        )
                        thumb_url = thumb_resp.json().get("thumbnail_url", "")
                        if thumb_url:
                            tb = client.get(thumb_url)
                            if tb.status_code == 200:
                                logger.info(f"[story] got video thumbnail via Graph API story_id={story_id}")
                                return tb.content, True
                    except Exception as e:
                        logger.warning(f"[story] Graph API thumbnail failed: {e}")
                # No thumbnail — return empty to trigger text-only fallback
                logger.info(f"[story] video with no thumbnail, story_id={story_id}")
                return b"", True

            # Unknown content type — try as image
            return resp.content, False
    except Exception as e:
        logger.error(f"[story] media fetch error: {e}")
        return b"", False


def _match_story_to_product(img_bytes: bytes, restaurant_id: str) -> dict:
    """
    Two-pass Vision product matching against the restaurant's stored product images.

    Pass 1 — cheap: ask Vision which product name (from text list) matches the image.
    Pass 2 — visual: send up to 4 candidate product images side-by-side for exact match.

    Returns: {"product": dict|None, "description": str, "confidence": "high"|"medium"|"low"}
    """
    client_ai = _get_openai()
    if not client_ai or not img_bytes:
        return {"product": None, "description": "", "confidence": "low"}

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    story_b64 = base64.b64encode(img_bytes).decode("utf-8")

    # Load all products
    conn_tmp = database.get_db()
    rows = conn_tmp.execute(
        "SELECT id, name, price, category, image_url FROM products WHERE restaurant_id=? AND available=1",
        (restaurant_id,)
    ).fetchall()
    conn_tmp.close()

    all_products = [dict(r) for r in rows]
    if not all_products:
        return {"product": None, "description": "", "confidence": "low"}

    product_names = [p["name"] for p in all_products]

    # ── Pass 1: text-name match ────────────────────────────────────────────────
    try:
        r1 = client_ai.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {
                    "type": "text",
                    "text": (
                        f"قائمة منتجات المطعم: {', '.join(product_names)}\n"
                        "انظر لهذه الصورة — ما اسم المنتج من القائمة أعلاه الذي تراه؟ "
                        "أجب باسم المنتج فقط بدون أي كلام آخر. "
                        "إذا لم تجد تطابقاً واضحاً اكتب: غير محدد"
                    )
                },
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{story_b64}", "detail": "low"}}
            ]}],
            max_tokens=25,
        )
        pass1 = r1.choices[0].message.content.strip()
        logger.info(f"[story] Pass1={pass1}")
    except Exception as e:
        logger.error(f"[story] Pass1 error: {e}")
        return {"product": None, "description": "", "confidence": "low"}

    if pass1 == "غير محدد":
        return {"product": None, "description": "محتوى من المطعم", "confidence": "low"}

    # Exact match first
    for p in all_products:
        if p["name"].strip() == pass1.strip():
            return {"product": p, "description": p["name"], "confidence": "high"}

    # Partial match → candidates for Pass 2
    candidates = [p for p in all_products if pass1 in p["name"] or p["name"] in pass1]
    if not candidates:
        candidates = all_products  # widen if no partial match

    products_with_images = [p for p in candidates if p.get("image_url")][:4]

    if not products_with_images:
        # No product images stored — trust Pass 1
        best = candidates[0] if candidates else None
        return {"product": best, "description": pass1, "confidence": "medium"}

    # ── Pass 2: visual comparison ──────────────────────────────────────────────
    content_parts = [
        {
            "type": "text",
            "text": "الصورة الأولى هي صورة الستوري. الصور التالية هي منتجات المطعم. أي منتج يطابق صورة الستوري؟ أجب باسم المنتج فقط."
        },
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{story_b64}", "detail": "low"}},
    ]
    candidate_map = {}
    for p in products_with_images:
        pb = _get_product_image_bytes(p["image_url"])
        if not pb:
            continue
        pb64 = base64.b64encode(pb).decode("utf-8")
        content_parts.append({"type": "text", "text": f"▶ {p['name']}"})
        content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{pb64}", "detail": "low"}})
        candidate_map[p["name"]] = p

    if len(candidate_map) == 0:
        best = candidates[0] if candidates else None
        return {"product": best, "description": pass1, "confidence": "medium"}

    try:
        r2 = client_ai.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content_parts}],
            max_tokens=25,
        )
        pass2 = r2.choices[0].message.content.strip()
        logger.info(f"[story] Pass2={pass2}")
        for name, p in candidate_map.items():
            if name == pass2 or name in pass2 or pass2 in name:
                return {"product": p, "description": p["name"], "confidence": "high"}
        # Pass2 gave something different — fallback to Pass1 best
        best = candidates[0] if candidates else None
        return {"product": best, "description": pass1, "confidence": "medium"}
    except Exception as e:
        logger.error(f"[story] Pass2 error: {e}")
        best = candidates[0] if candidates else None
        return {"product": best, "description": pass1, "confidence": "medium"}


def _analyze_story(media_url: str, story_id: str, restaurant_id: str,
                   access_token: str = "", platform: str = "") -> dict:
    """
    Full story analysis pipeline.
    Returns a dict: {context_str, product_id, product_name, product_price,
                     product_category, confidence, is_video}
    """
    def _result(context_str: str, match: dict = None, is_video: bool = False) -> dict:
        p = (match or {}).get("product") or {}
        return {
            "context_str": context_str,
            "product_id": p.get("id", ""),
            "product_name": p.get("name", ""),
            "product_price": float(p.get("price", 0) or 0),
            "product_category": p.get("category", ""),
            "confidence": (match or {}).get("confidence", "low"),
            "is_video": is_video,
        }

    if not media_url:
        return _result("[العميل يرد على ستوري للمطعم — اسأله بودية عما يرغب به]")

    img_bytes, is_video = _fetch_story_media(media_url, story_id, access_token)
    video_tag = " [فيديو]" if is_video else ""
    analysis_tag = " (video_thumbnail_only)" if is_video else ""

    if not img_bytes:
        return _result(
            f"[العميل يرد على ستوري فيديو للمطعم — رحّب به واسأله عما يشتهيه]{analysis_tag}",
            is_video=True,
        )

    match = _match_story_to_product(img_bytes, restaurant_id)
    product = match.get("product")
    confidence = match.get("confidence", "low")
    description = match.get("description", "")

    if product and confidence in ("high", "medium"):
        price_str = f"{int(product['price']):,}" if product.get("price") else ""
        price_part = f" — {price_str} د.ع" if price_str else ""
        conf_note = "" if confidence == "high" else " (تقريباً)"
        ctx = (
            f"[العميل يرد على ستوري{video_tag} يعرض: {product['name']}{price_part}{conf_note}]"
            f"\nسياق للبوت: هذا المنتج موجود في قائمتك. استغل الفرصة وابدأ flow البيع مباشرة.{analysis_tag}"
        )
        return _result(ctx, match=match, is_video=is_video)

    if description and description not in ("محتوى من المطعم", ""):
        ctx = f"[العميل يرد على ستوري{video_tag} يظهر: {description} — اسأله إذا يريد تجربته]{analysis_tag}"
        return _result(ctx, match=match, is_video=is_video)

    return _result(
        f"[العميل يرد على ستوري{video_tag} للمطعم — رحّب به وابدأ محادثة البيع]{analysis_tag}",
        is_video=is_video,
    )


def _analyze_story_cached(
    media_url: str, story_id: str, restaurant_id: str,
    access_token: str = "", platform: str = "",
) -> dict:
    """
    Cache-aware wrapper around _analyze_story.
    Returns same dict as _analyze_story. Never raises.
    """
    channel = platform or "instagram"
    cached = _sc.get_cached_story(restaurant_id, channel, story_id, media_url)
    if cached is not None:
        logger.info(
            f"[story-cache] HIT restaurant={restaurant_id[:8]} story={story_id[:20] if story_id else 'url-key'}"
        )
        return cached

    logger.info(
        f"[story-cache] MISS — running Vision API restaurant={restaurant_id[:8]} story={story_id[:20] if story_id else 'url'}"
    )
    try:
        result = _analyze_story(media_url, story_id, restaurant_id, access_token, platform)
        is_failure = not result.get("product_name") and result.get("confidence", "low") == "low"
        # Build match_data shape expected by store_story_cache
        match_data = {
            "product": {
                "id": result["product_id"],
                "name": result["product_name"],
                "price": result["product_price"],
                "category": result["product_category"],
            } if result.get("product_id") else None,
            "confidence": result["confidence"],
            "description": result.get("product_name", ""),
        }
        _sc.store_story_cache(
            restaurant_id, channel, story_id, media_url,
            match_data, result["context_str"], result["is_video"],
            is_failure=is_failure,
        )
        return result
    except Exception as e:
        logger.error(f"[story-cache] analysis error: {e}")
        return {
            "context_str": "[العميل يرد على ستوري للمطعم — رحّب به وابدأ محادثة البيع]",
            "product_id": "", "product_name": "", "product_price": 0.0,
            "product_category": "", "confidence": "low", "is_video": False,
        }


# ── Deterministic story reply (NUMBER 33) ────────────────────────────────────

_STORY_ORDER_TRIGGERS = {
    # نية الشراء / الطلب — عراقي
    "اريد", "أريد", "ابي", "أبي", "بدي", "ودي", "ودني", "حابب", "حاب", "حابي",
    "باخذ", "بآخذ", "اخذ", "آخذ", "خذلي", "اخذلي", "خذيلي",
    "جهزلي", "جهزلنا", "جهز", "جهزي", "جهزوا", "جهّزلي",
    "اطلب", "أطلب", "طلبت", "بطلب", "نطلب", "تطلب", "طلبي", "طلب",
    "اشتري", "أشتري", "بشتري", "اشتريلي",
    "واحد", "اثنين", "ثنتين", "ثلاثة", "اربعة", "خمسة",
    "هذا", "هذاك", "هذي", "هاي", "هاذا", "هاذي", "هذول",
    "اريد هذا", "أريد هذا", "ابي اطلب", "أبي أطلب", "حابب اطلب",
    "زبوني", "طلبتي", "ابي اسوي طلب", "اريد اطلب",
    # خليجي / سعودي
    "ودي اطلب", "ابغى", "أبغى", "ابغ", "أبغ", "بغيت",
    "اطلبلي", "اطلبيلي", "ودني اطلب",
    # لبناني / شامي
    "بدي", "بدي هيدا", "كيف بطلب", "كيفية الطلب",
    # إجراء مباشر
    "اضيف", "أضيف", "ضيفلي", "ضيف", "اكمل", "أكمل", "تأكيد", "اكد",
}
_STORY_PRICE_TRIGGERS = {
    # عراقي
    "شكد", "شقد", "شگد", "شچد", "بشكد", "بشقد", "بشگد",
    "كم سعره", "كم ثمنه", "كم يكلف", "يكلف كم", "سعره كم",
    "السعر كم", "الثمن كم", "سعره شكد", "بكم الواحد",
    "السعر", "الثمن", "سعره", "سعرها", "سعر", "ثمنه", "ثمنها",
    "بكم", "بچم", "بگم", "كم",
    "الكلفة", "تكلفة", "تكلف", "كلفته", "كلفتها",
    # خليجي / سعودي
    "بكام", "كام", "بكم يكون", "وش سعره", "وش ثمنه",
    # لبناني / شامي
    "قديش", "قداش", "كم تمنو", "تمنو قديش",
}
_STORY_AVAIL_TRIGGERS = {
    # عراقي
    "موجود", "موجوده", "متوفر", "متوفره",
    "عدكم", "عدك", "عندكم", "عندك", "عنده", "عندهم",
    "تعدك", "تعدكم",
    "في", "لو في", "لو موجود", "لو متوفر",
    "تتوفر", "يتوفر", "تلكون", "لكو",
    # خليجي / سعودي
    "عندكم", "يتوفر عندكم", "موجود عندكم", "فيه",
    "يوجد", "تلقون",
    # لبناني / شامي
    "عندكن", "في عندكن", "موجود عندكن",
}
_STORY_EMOJI_ONLY = {
    "🔥", "😍", "❤️", "🤤", "😋", "👍", "🌹", "❤", "💜", "🤩",
    "😻", "💛", "😮", "👏", "💯", "🙌", "✨", "💫", "🫶", "🤌",
    "💚", "💙", "🧡", "💖", "💗", "😱", "🥰", "😘", "👌", "🫡",
    "😲", "🤯", "😤", "💥", "⭐", "🌟",
}


def _is_emoji_only(text: str) -> bool:
    """True when the message contains only emoji characters (no Arabic/Latin text)."""
    stripped = text.strip()
    if not stripped:
        return False
    for ch in stripped:
        if ch in _STORY_EMOJI_ONLY:
            continue
        cp = ord(ch)
        # Allow standard emoji/symbol unicode ranges
        if (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) or cp in (0xFE0F, 0x200D):
            continue
        return False
    return True


def _build_deterministic_story_reply(
    customer_msg: str, story_ctx: dict, restaurant_id: str,
) -> str:
    """
    Return a deterministic reply string when story has a matched product
    and the customer message fits a known intent pattern.
    Returns "" if no deterministic reply is appropriate (→ fall through to AI).
    """
    product_name = story_ctx.get("product_name", "")
    product_price = story_ctx.get("product_price", 0)
    confidence = story_ctx.get("confidence", "low")

    if not product_name or confidence not in ("high", "medium"):
        # Unknown story or low confidence: give a gentle redirect
        msg_norm = customer_msg.strip()
        is_order_ish = any(t in msg_norm for t in _STORY_ORDER_TRIGGERS)
        is_price_ish = any(t in msg_norm for t in _STORY_PRICE_TRIGGERS)
        is_emoji = _is_emoji_only(msg_norm)
        if is_order_ish or is_price_ish or is_emoji:
            return (
                "وصلتني ردة فعلك على الستوري 🌷 "
                "بس حتى أتأكد، تحب أدزلك المنيو أو تكتبلي شنو المنتج اللي تقصده؟"
            )
        return ""

    price_str = f"{int(product_price):,} د.ع" if product_price else ""
    msg_norm = customer_msg.strip()

    # Check product availability (real-time from DB)
    product_available = True
    if story_ctx.get("product_id"):
        try:
            conn = database.get_db()
            row = conn.execute(
                "SELECT available FROM products WHERE id=? AND restaurant_id=?",
                (story_ctx["product_id"], restaurant_id)
            ).fetchone()
            conn.close()
            if row is not None:
                product_available = bool(row["available"])
        except Exception:
            pass

    if not product_available:
        return (
            f"حالياً {product_name} مو متوفر 🌷 "
            "أگدر أقترحلك الأقرب من المنيو — شنو تحب؟"
        )

    # Price inquiry
    if any(t in msg_norm for t in _STORY_PRICE_TRIGGERS):
        price_part = f"سعره {price_str}" if price_str else "راجع معنا للسعر"
        return f"{product_name} {price_part} 🌷 تحب أجهزلك واحد؟"

    # Availability check
    if any(t in msg_norm for t in _STORY_AVAIL_TRIGGERS):
        return f"إي متوفر 🌷 {product_name}" + (f" — {price_str}" if price_str else "") + ". تحب أجهزلك واحد؟"

    # Order intent or emoji-only
    is_order_intent = any(t in msg_norm for t in _STORY_ORDER_TRIGGERS)
    is_emoji = _is_emoji_only(msg_norm)

    if is_emoji:
        price_part = f"سعره {price_str}" if price_str else ""
        return (
            f"يعجبك؟ 🌷 هذا {product_name}" +
            (f" {price_part}" if price_part else "") +
            ". تحب أجهزلك واحد؟"
        )

    if is_order_intent:
        return (
            f"أكيد 🌷 أجهزلك {product_name}. "
            "تحبه توصيل لو استلام؟"
        )

    return ""


def _download_and_transcribe_whatsapp_voice(media_id: str, access_token: str) -> tuple:
    """
    Resolve a WhatsApp voice media ID, download safely, and transcribe.
    Returns (url, transcript, error_code).
    """
    from services import voice_service as _vs
    try:
        auth = {"Authorization": f"Bearer {access_token}"}
        with httpx.Client(timeout=15) as client:
            r = client.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=auth)
            r.raise_for_status()
            media_url = r.json().get("url", "")
            if not media_url:
                return "", "", _vs.ERR_FAILED

        audio_bytes = _vs.download_audio_from_url(media_url, headers=auth)
        if not audio_bytes:
            return media_url, "", _vs.ERR_TOO_LARGE if media_url else _vs.ERR_FAILED

        tr = _vs.transcribe_voice_message(
            audio_bytes, filename="voice.ogg", mime_type="audio/ogg", channel="whatsapp"
        )
        return media_url, tr["text"], tr.get("error_code", "")

    except Exception as e:
        logger.error(f"[whatsapp] voice transcription error: {e}")
        return "", "", _vs.ERR_FAILED


def _download_and_describe_whatsapp_image(media_id: str, access_token: str, restaurant_id: str) -> tuple:
    """Resolve a WhatsApp media ID to bytes then describe via Vision."""
    try:
        auth = {"Authorization": f"Bearer {access_token}"}
        with httpx.Client(timeout=20) as client:
            # Step 1: resolve media URL
            r = client.get(
                f"https://graph.facebook.com/v19.0/{media_id}",
                headers=auth
            )
            r.raise_for_status()
            media_url = r.json().get("url", "")
            if not media_url:
                return "", "[العميل أرسل صورة]"
            # Step 2: download bytes
            img_bytes = client.get(media_url, headers=auth).content
        return media_url, _vision_describe(img_bytes, restaurant_id, "whatsapp")
    except Exception as e:
        logger.error(f"[whatsapp] image download error: {e}")
        return "", "[العميل أرسل صورة]"


# ── WhatsApp ──────────────────────────────────────────────────────────────────

def handle_whatsapp(restaurant_id: str, data: dict) -> None:
    """Process an incoming WhatsApp Cloud API message."""
    _conn = database.get_db()
    _rest = _conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    _conn.close()
    if not _rest:
        logger.error(f"[whatsapp] ORPHANED WEBHOOK — restaurant_id={restaurant_id} not in DB. Re-register after PostgreSQL migration.")
        return

    try:
        entry = data["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages")
        if not messages:
            return
        msg = messages[0]
    except (KeyError, IndexError):
        return

    msg_type = msg.get("type", "")
    if msg_type not in ("text", "image", "audio", "voice"):
        return

    # Dedup — WhatsApp retries unacknowledged messages
    wamid = msg.get("id", "")
    if wamid and _is_duplicate_event(restaurant_id, "whatsapp", wamid):
        logger.info(f"[whatsapp] duplicate message {wamid} — skipping")
        return

    text = ""
    media_type = ""
    media_url = ""
    voice_transcript = ""

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "").strip()
    elif msg_type == "image":
        media_type = "image"
        caption = msg.get("image", {}).get("caption", "").strip()
        media_id = msg.get("image", {}).get("id", "")
        conn_pre = database.get_db()
        ch_pre = conn_pre.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='whatsapp'",
            (restaurant_id,)
        ).fetchone()
        token_pre = ch_pre["token"] if ch_pre else None
        conn_pre.close()
        if media_id and token_pre:
            media_url, image_text = _download_and_describe_whatsapp_image(
                media_id, token_pre, restaurant_id
            )
            text = (caption + " " + image_text).strip() if caption else image_text
        else:
            text = caption or "[العميل أرسل صورة]"
    elif msg_type in ("audio", "voice"):
        media_type = "voice"
        media_id = msg.get(msg_type, {}).get("id", "")
        wa_voice_error_code = ""
        wa_voice_processing_sent = False
        conn_pre = database.get_db()
        ch_pre = conn_pre.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='whatsapp'",
            (restaurant_id,)
        ).fetchone()
        token_pre = ch_pre["token"] if ch_pre else None
        phone_num_id_pre = ch_pre["phone_number_id"] if ch_pre and "phone_number_id" in ch_pre.keys() else None
        conn_pre.close()
        if media_id and token_pre:
            from services.voice_service import VOICE_PROCESSING_AR as _VPRO_WA
            wa_sender = msg.get("from", "")
            # Send processing wait message before slow transcription
            if wa_sender and phone_num_id_pre and _subscription_active(restaurant_id):
                try:
                    _send_whatsapp(token_pre, phone_num_id_pre, wa_sender, _VPRO_WA)
                    wa_voice_processing_sent = True
                except Exception:
                    pass
            media_url, voice_transcript, wa_voice_error_code = _download_and_transcribe_whatsapp_voice(
                media_id, token_pre
            )
        text = "[رسالة صوتية]" if wa_voice_error_code else (voice_transcript or "[رسالة صوتية]")

    if not text:
        return

    external_id = msg.get("from", "")
    contacts = value.get("contacts", [{}])
    name = contacts[0].get("profile", {}).get("name", "WhatsApp User") if contacts else "WhatsApp User"

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='whatsapp'",
            (restaurant_id,)
        ).fetchone()
        access_token = ch["token"] if ch else None
        phone_number_id = ch["phone_number_id"] if ch and "phone_number_id" in ch.keys() else None

        customer = _find_or_create_customer(
            conn, restaurant_id, "whatsapp", external_id, name, external_id
        )
        prior_convs_wa = conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE restaurant_id=? AND customer_id=?",
            (restaurant_id, customer["id"])
        ).fetchone()
        is_first_contact_wa = (prior_convs_wa["n"] == 0)
        conversation = _find_or_create_conversation(
            conn, restaurant_id, customer["id"],
            channel="whatsapp", first_contact=is_first_contact_wa
        )
        conn.commit()

        channel_data = {
            "platform": "whatsapp",
            "access_token": access_token,
            "phone_number_id": phone_number_id,
            "to": external_id,
        }
        extra = {
            "media_type": media_type,
            "media_url": media_url,
            "voice_transcript": voice_transcript,
            "voice_error_code": wa_voice_error_code if media_type == "voice" else "",
            "voice_processing_sent": wa_voice_processing_sent if media_type == "voice" else False,
        }
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    finally:
        conn.close()


# ── Instagram ─────────────────────────────────────────────────────────────────

def handle_instagram(restaurant_id: str, data: dict) -> None:
    """Process an incoming Instagram message, including story replies."""
    logger.info(
        f"[meta-live-message] platform=instagram restaurant={restaurant_id[:8]} "
        f"entries={len(data.get('entry', []))} raw={json.dumps(data)[:300]}"
    )

    _conn = database.get_db()
    _rest = _conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    _conn.close()
    if not _rest:
        logger.error(
            f"[ig-error] ORPHANED WEBHOOK — restaurant_id={restaurant_id} not in DB"
        )
        return

    try:
        entry = data["entry"][0]
        messaging = entry.get("messaging", [])
        if not messaging:
            logger.warning(
                f"[ig-incoming] no messaging[] in entry — "
                f"entry keys={list(entry.keys())} "
                f"(could be a non-DM event: story_mention, comment, etc.)"
            )
            return
        messaging = messaging[0]
    except (KeyError, IndexError) as exc:
        logger.error(f"[ig-error] failed to parse entry/messaging: {exc} — data={json.dumps(data)[:300]}")
        return

    sender_id = messaging.get("sender", {}).get("id", "")
    recipient_id = messaging.get("recipient", {}).get("id", "")
    message = messaging.get("message", {})
    text = message.get("text", "").strip()
    logger.info(
        f"[ig-parsed] sender={sender_id} recipient={recipient_id} "
        f"has_text={bool(text)} text_preview={text[:60] if text else 'EMPTY'} "
        f"attachments={len(message.get('attachments', []))}"
    )
    if not sender_id:
        logger.warning(f"[ig-error] no sender_id in messaging — skipping")
        return

    # Filter echo messages: sent BY the page/business account back to itself.
    # sender_id == entry["id"] means the page sent this message (our own reply echoed back).
    # is_echo flag is also set by Meta on echoed messages.
    entry_ig_id = entry.get("id", "")
    if message.get("is_echo") or (entry_ig_id and sender_id == entry_ig_id):
        logger.info(f"[ig-echo] skipping echo message — sender={sender_id} entry={entry_ig_id} mid={message.get('mid','')[:20]}")
        return

    # Dedup — Meta retries unacknowledged messages
    mid_ig = message.get("mid", "")
    if mid_ig and _is_duplicate_event(restaurant_id, "instagram", mid_ig):
        logger.info(f"[instagram] duplicate message {mid_ig} — skipping")
        return

    media_type = ""
    media_url_ig = ""
    voice_transcript_ig = ""
    ig_voice_error_code = ""
    ig_voice_processing_sent = False

    # Handle attachments — image first, then audio/voice
    attachments = message.get("attachments", [])
    for att in attachments:
        att_type = att.get("type", "")
        if att_type == "image":
            media_type = "image"
            img_url = att.get("payload", {}).get("url", "")
            if img_url:
                media_url_ig, image_text = _download_and_describe_url(
                    img_url, restaurant_id, "instagram"
                )
                if not text:
                    text = image_text
                else:
                    text = text + " " + image_text
            break
        elif att_type == "audio":
            media_type = "voice"
            audio_url = att.get("payload", {}).get("url", "")
            media_url_ig = audio_url
            if audio_url:
                # Fetch token early to send processing wait message
                _conn_ig = database.get_db()
                _ch_ig = _conn_ig.execute(
                    "SELECT * FROM channels WHERE restaurant_id=? AND type='instagram'",
                    (restaurant_id,)
                ).fetchone()
                _token_ig = _ch_ig["token"] if _ch_ig else None
                _conn_ig.close()
                from services.voice_service import VOICE_PROCESSING_AR as _VPRO_IG
                if _token_ig and sender_id and _subscription_active(restaurant_id):
                    try:
                        _send_facebook_messenger(_token_ig, sender_id, _VPRO_IG)
                        ig_voice_processing_sent = True
                    except Exception:
                        pass
                try:
                    from services import voice_service as _vs
                    audio_bytes = _vs.download_audio_from_url(audio_url)
                    if audio_bytes:
                        tr = _vs.transcribe_voice_message(
                            audio_bytes, filename="voice.mp4", mime_type="audio/mp4",
                            channel="instagram", restaurant_id=restaurant_id,
                        )
                        voice_transcript_ig = tr["text"]
                        ig_voice_error_code = tr.get("error_code", "")
                        logger.info(
                            f"[ig-voice] transcription status={tr['transcription_status']} "
                            f"len={len(voice_transcript_ig)} restaurant={restaurant_id[:8]}"
                        )
                    else:
                        from services import voice_service as _vs
                        ig_voice_error_code = _vs.ERR_FAILED
                except Exception as _ve:
                    logger.error(f"[ig-voice] transcription error: {_ve}")
                    from services import voice_service as _vs
                    ig_voice_error_code = _vs.ERR_FAILED
            text = "[رسالة صوتية]" if ig_voice_error_code else (voice_transcript_ig or "[رسالة صوتية]")
            break

    # Detect story reply context
    replied_story_id = ""
    replied_story_text = ""
    replied_story_media_url = ""
    story_context = ""

    reply_to = message.get("reply_to", {})
    if reply_to:
        story = reply_to.get("story", {})
        if story:
            replied_story_id = story.get("id", "")
            replied_story_media_url = story.get("url", "")
            replied_story_text = text
        if not text:
            text = reply_to.get("text", "").strip()

    # Story reply with no text (reaction/emoji tap) — still engage
    if replied_story_id and not text:
        text = "👍"

    if not text:
        logger.warning(
            f"[meta-live-message] platform=instagram sender={sender_id} "
            f"DROPPED — no text and no parseable attachment "
            f"message_keys={list(message.keys())}"
        )
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='instagram'",
            (restaurant_id,)
        ).fetchone()
        access_token = ch["token"] if ch else None
        if not ch:
            logger.warning(f"[ig-error] no instagram channel row for restaurant={restaurant_id[:8]}")

        # Analyze story media with cache (image OR video thumbnail)
        story_ctx_data = {}
        if replied_story_id and replied_story_media_url:
            story_ctx_data = _analyze_story_cached(
                replied_story_media_url, replied_story_id,
                restaurant_id, access_token or "", "instagram"
            )
            story_context = story_ctx_data.get("context_str", "")
            logger.info(f"[ig-parsed] story_context: {story_context[:80]}")

        customer = _find_or_create_customer(
            conn, restaurant_id, "instagram", sender_id, "Instagram User", ""
        )
        # first_contact = customer has no prior conversations with this restaurant
        prior_convs = conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE restaurant_id=? AND customer_id=?",
            (restaurant_id, customer["id"])
        ).fetchone()
        is_first_contact = (prior_convs["n"] == 0)
        conversation = _find_or_create_conversation(
            conn, restaurant_id, customer["id"],
            channel="instagram", first_contact=is_first_contact
        )
        conn.commit()
        logger.info(
            f"[meta-live-message] platform=instagram "
            f"first_contact={is_first_contact} is_new_conv={conversation.get('_is_new', False)} "
            f"customer={customer['id'][:8]} conversation={conversation['id'][:8]} "
            f"sender={sender_id} text={text[:60]}"
        )

        channel_data = {
            "platform": "instagram",
            "access_token": access_token,
            "recipient_id": sender_id,
        }
        extra = {
            "media_type": media_type,
            "media_url": media_url_ig,
            "voice_transcript": voice_transcript_ig,
            "voice_error_code": ig_voice_error_code,
            "voice_processing_sent": ig_voice_processing_sent,
            "replied_story_id": replied_story_id,
            "replied_story_text": replied_story_text,
            "replied_story_media_url": replied_story_media_url,
            "story_context": story_context,
            "story_ctx_data": story_ctx_data,
        }
        logger.info(f"[ig-parsed] calling _process_incoming — text={text[:60]}")
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    except Exception as exc:
        logger.error(f"[ig-error] unhandled exception in handle_instagram: {exc}", exc_info=True)
    finally:
        conn.close()


# ── Facebook Messenger ────────────────────────────────────────────────────────

def handle_facebook(restaurant_id: str, data: dict) -> None:
    """Process an incoming Facebook Messenger message."""
    logger.info(
        f"[meta-live-message] platform=facebook restaurant={restaurant_id[:8]} "
        f"entries={len(data.get('entry', []))} raw={json.dumps(data)[:300]}"
    )

    _conn = database.get_db()
    _rest = _conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    _conn.close()
    if not _rest:
        logger.error(f"[facebook] ORPHANED WEBHOOK — restaurant_id={restaurant_id} not in DB")
        return

    try:
        entry = data["entry"][0]
        messaging = entry["messaging"][0]
    except (KeyError, IndexError) as exc:
        logger.error(
            f"[facebook] failed to parse entry/messaging: {exc} — "
            f"entry keys={list(data['entry'][0].keys()) if data.get('entry') else 'NO_ENTRIES'} "
            f"raw={json.dumps(data)[:300]}"
        )
        return

    sender_id = messaging.get("sender", {}).get("id", "")
    recipient_id = messaging.get("recipient", {}).get("id", "")
    message = messaging.get("message", {})
    text = message.get("text", "").strip()
    mid_fb = message.get("mid", "")
    logger.info(
        f"[meta-live-message] platform=facebook sender={sender_id} "
        f"recipient={recipient_id} mid={mid_fb} "
        f"has_text={bool(text)} text_preview={text[:60] if text else 'EMPTY'} "
        f"attachments={len(message.get('attachments', []))}"
    )
    if not sender_id:
        logger.warning(f"[facebook] no sender_id — dropping")
        return

    # Filter echo messages: page sending its own replies back as webhook events.
    entry_fb_id = entry.get("id", "")
    if message.get("is_echo") or (entry_fb_id and sender_id == entry_fb_id):
        logger.info(f"[fb-echo] skipping echo message — sender={sender_id} entry={entry_fb_id} mid={mid_fb[:20]}")
        return

    # Dedup — Meta retries unacknowledged messages
    mid_fb = message.get("mid", "")
    if mid_fb and _is_duplicate_event(restaurant_id, "facebook", mid_fb):
        logger.info(f"[facebook] duplicate message {mid_fb} — skipping")
        return

    media_type_fb = ""
    media_url_fb = ""
    voice_transcript_fb = ""
    fb_voice_error_code = ""
    fb_voice_processing_sent = False
    replied_story_id_fb = ""
    replied_story_media_url_fb = ""
    story_context_fb = ""

    # Handle attachments — image first, then audio/voice
    attachments = message.get("attachments", [])
    for att in attachments:
        att_type = att.get("type", "")
        if att_type == "image":
            media_type_fb = "image"
            img_url = att.get("payload", {}).get("url", "")
            if img_url:
                media_url_fb, image_text = _download_and_describe_url(
                    img_url, restaurant_id, "facebook"
                )
                if not text:
                    text = image_text
                else:
                    text = text + " " + image_text
            break
        elif att_type == "audio":
            media_type_fb = "voice"
            audio_url = att.get("payload", {}).get("url", "")
            media_url_fb = audio_url
            if audio_url:
                # Fetch token early to send processing wait message
                _conn_fb = database.get_db()
                _ch_fb = _conn_fb.execute(
                    "SELECT * FROM channels WHERE restaurant_id=? AND type='facebook'",
                    (restaurant_id,)
                ).fetchone()
                _token_fb = _ch_fb["token"] if _ch_fb else None
                _conn_fb.close()
                from services.voice_service import VOICE_PROCESSING_AR as _VPRO_FB
                if _token_fb and sender_id and _subscription_active(restaurant_id):
                    try:
                        _send_facebook_messenger(_token_fb, sender_id, _VPRO_FB)
                        fb_voice_processing_sent = True
                    except Exception:
                        pass
                try:
                    from services import voice_service as _vs
                    audio_bytes = _vs.download_audio_from_url(audio_url)
                    if audio_bytes:
                        tr = _vs.transcribe_voice_message(
                            audio_bytes, filename="voice.mp4", mime_type="audio/mp4",
                            channel="facebook", restaurant_id=restaurant_id,
                        )
                        voice_transcript_fb = tr["text"]
                        fb_voice_error_code = tr.get("error_code", "")
                        logger.info(
                            f"[fb-voice] transcription status={tr['transcription_status']} "
                            f"len={len(voice_transcript_fb)} restaurant={restaurant_id[:8]}"
                        )
                    else:
                        from services import voice_service as _vs
                        fb_voice_error_code = _vs.ERR_FAILED
                except Exception as _ve:
                    logger.error(f"[fb-voice] transcription error: {_ve}")
                    from services import voice_service as _vs
                    fb_voice_error_code = _vs.ERR_FAILED
            text = "[رسالة صوتية]" if fb_voice_error_code else (voice_transcript_fb or "[رسالة صوتية]")
            break

    # Detect story reply (Facebook uses same structure as Instagram)
    reply_to_fb = message.get("reply_to", {})
    if reply_to_fb:
        story_fb = reply_to_fb.get("story", {})
        if story_fb:
            replied_story_id_fb = story_fb.get("id", "")
            replied_story_media_url_fb = story_fb.get("url", "")
        if not text:
            text = reply_to_fb.get("text", "").strip()

    if replied_story_id_fb and not text:
        text = "👍"

    if not text:
        logger.warning(
            f"[meta-live-message] platform=facebook sender={sender_id} "
            f"DROPPED — no text and no parseable attachment "
            f"message_keys={list(message.keys())}"
        )
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='facebook'",
            (restaurant_id,)
        ).fetchone()
        page_token = ch["token"] if ch else None

        # Analyze story media with cache
        story_ctx_data_fb = {}
        if replied_story_id_fb and replied_story_media_url_fb:
            story_ctx_data_fb = _analyze_story_cached(
                replied_story_media_url_fb, replied_story_id_fb,
                restaurant_id, page_token or "", "facebook"
            )
            story_context_fb = story_ctx_data_fb.get("context_str", "")
            logger.info(f"[facebook] story_context: {story_context_fb[:80]}")

        customer = _find_or_create_customer(
            conn, restaurant_id, "facebook", sender_id, "Facebook User", ""
        )
        prior_convs = conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE restaurant_id=? AND customer_id=?",
            (restaurant_id, customer["id"])
        ).fetchone()
        is_first_contact = (prior_convs["n"] == 0)
        conversation = _find_or_create_conversation(
            conn, restaurant_id, customer["id"],
            channel="facebook", first_contact=is_first_contact
        )
        conn.commit()
        logger.info(
            f"[meta-live-message] platform=facebook "
            f"first_contact={is_first_contact} is_new_conv={conversation.get('_is_new', False)} "
            f"customer={customer['id'][:8]} conversation={conversation['id'][:8]} "
            f"sender={sender_id} text={text[:60]}"
        )

        channel_data = {
            "platform": "facebook",
            "page_token": page_token,
            "access_token": page_token,
            "recipient_id": sender_id,
        }
        extra = {
            "media_type": media_type_fb,
            "media_url": media_url_fb,
            "voice_transcript": voice_transcript_fb,
            "voice_error_code": fb_voice_error_code,
            "voice_processing_sent": fb_voice_processing_sent,
            "replied_story_id": replied_story_id_fb,
            "replied_story_media_url": replied_story_media_url_fb,
            "story_context": story_context_fb,
            "story_ctx_data": story_ctx_data_fb,
        }
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    finally:
        conn.close()


# ── Core processing ───────────────────────────────────────────────────────────

def _process_incoming(
    restaurant_id: str,
    customer: dict,
    conversation: dict,
    content: str,
    channel_data: dict,
    extra: Optional[dict] = None,
) -> None:
    """Core handler: save message, run bot or escalate, send reply, log activity."""
    if extra is None:
        extra = {}

    conn = database.get_db()
    try:
        conv_id = conversation["id"]
        customer_id = customer["id"]
        platform = channel_data.get("platform", "unknown")

        import time as _t
        req_id = str(uuid.uuid4())[:8]
        _t_start = _t.monotonic()
        logger.info(
            f"[incoming] req={req_id} restaurant={restaurant_id} "
            f"conv={conv_id} channel={platform} customer={customer_id} "
            f"msg_len={len(content)} preview={content[:60]!r}"
        )

        # 1. Save customer message with optional media/story metadata
        msg_id = str(uuid.uuid4())
        _is_voice = extra.get("media_type") == "voice"
        _vt       = extra.get("voice_transcript", "")
        if not _is_voice:
            _t_status = "not_required"
        elif _vt:
            _t_status = "success"
        else:
            _t_status = "failed"
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, media_type, media_url, voice_transcript,
                replied_story_id, replied_story_text, replied_story_media_url,
                transcription_status, transcription_error, transcription_provider, transcribed_at)
               VALUES (?, ?, 'customer', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, conv_id, content,
                extra.get("media_type", ""),
                extra.get("media_url", ""),
                _vt,
                extra.get("replied_story_id", ""),
                extra.get("replied_story_text", ""),
                extra.get("replied_story_media_url", ""),
                _t_status,
                extra.get("transcription_error", ""),
                extra.get("transcription_provider", "openai_whisper" if _is_voice and _vt else ""),
                extra.get("transcribed_at", ""),
            )
        )
        conn.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (conv_id,)
        )
        conn.commit()

        # 2. Subscription guard: check if AI/outbound is allowed for this restaurant
        _BLOCKED_STATUSES = {"expired", "suspended", "cancelled"}
        _PLAN_AI = {"free": False, "trial": True, "starter": True, "professional": True, "enterprise": True}
        try:
            _sub  = conn.execute("SELECT status, plan FROM subscriptions WHERE restaurant_id=?", (restaurant_id,)).fetchone()
            _rest = conn.execute("SELECT plan, status FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
            _sub_status  = (_sub  and _sub["status"])  or "active"
            _rest_status = (_rest and _rest["status"]) or "active"
            _plan        = (_sub  and _sub["plan"])    or (_rest and _rest["plan"]) or "trial"
            _ai_blocked  = (_sub_status in _BLOCKED_STATUSES or _rest_status in _BLOCKED_STATUSES
                            or not _PLAN_AI.get(_plan, True))
        except Exception as _sub_err:
            logger.warning(f"[subscription-guard] check failed for {restaurant_id}: {_sub_err}")
            _ai_blocked = False  # fail-open: don't block on DB error

        if _ai_blocked:
            _block_reason = (
                "الاشتراك موقوف" if (_sub_status == "suspended" or _rest_status == "suspended")
                else "الاشتراك منتهي" if (_sub_status == "expired" or _rest_status == "expired")
                else "الاشتراك ملغى" if (_sub_status == "cancelled")
                else f"خطة {_plan} لا تتضمن الردود الآلية"
            )
            logger.info(
                f"[subscription-guard] BLOCKED restaurant={restaurant_id} "
                f"sub_status={_sub_status} rest_status={_rest_status} plan={_plan} "
                f"reason={_block_reason!r}"
            )
            # Log the blocked attempt in outbound_messages for visibility
            try:
                conn.execute(
                    """INSERT INTO outbound_messages
                       (id, restaurant_id, conversation_id, platform, recipient_id, content, status, error)
                       VALUES (?, ?, ?, ?, '', '', 'blocked_subscription', ?)""",
                    (str(uuid.uuid4()), restaurant_id, conv_id, platform, _block_reason)
                )
                conn.commit()
            except Exception:
                pass
            return  # inbound saved; AI reply blocked

        # 3. Acquire per-conversation lock before bot processing to prevent double replies
        _conv_lock = _get_conv_lock(conv_id)
        with _conv_lock:
            # Re-read conversation inside the lock so we see the freshest state
            conv_row = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
            mode = conv_row["mode"] if conv_row else "bot"

            if mode == "bot":
                # Voice guard — handle all error codes and failed transcriptions
                if extra.get("media_type") == "voice" and content == "[رسالة صوتية]":
                    from services import voice_service as _vs
                    _verr = extra.get("voice_error_code", "")
                    if _verr == _vs.ERR_TOO_LARGE:
                        _vfb_reply = _vs.VOICE_TOO_LARGE_AR
                    elif _verr == _vs.ERR_TOO_LONG:
                        _vfb_reply = _vs.VOICE_TOO_LONG_AR
                    elif _verr == _vs.ERR_UNCLEAR:
                        _vfb_reply = _vs.VOICE_UNCLEAR_AR
                    else:
                        _vfb_reply = _vs.VOICE_FALLBACK_AR
                    logger.info(
                        f"[voice-guard] req={req_id} conv={conv_id} "
                        f"error_code={_verr!r} reply_len={len(_vfb_reply)}"
                    )
                    recipient_id = (
                        channel_data.get("chat_id") or
                        channel_data.get("to") or
                        channel_data.get("recipient_id") or ""
                    )
                    # Log the processing "wait" message already sent to the customer
                    if extra.get("voice_processing_sent"):
                        conn.execute(
                            """INSERT INTO outbound_messages
                               (id, restaurant_id, conversation_id, platform, recipient_id, content, status, error)
                               VALUES (?, ?, ?, ?, ?, ?, 'sent', '')""",
                            (str(uuid.uuid4()), restaurant_id, conv_id, platform, recipient_id,
                             _vs.VOICE_PROCESSING_AR[:500])
                        )
                    _send_reply(channel_data, _vfb_reply)
                    conn.execute(
                        "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'bot', ?)",
                        (str(uuid.uuid4()), conv_id, _vfb_reply)
                    )
                    conn.execute(
                        """INSERT INTO outbound_messages
                           (id, restaurant_id, conversation_id, platform, recipient_id, content, status, error)
                           VALUES (?, ?, ?, ?, ?, ?, 'sent', '')""",
                        (str(uuid.uuid4()), restaurant_id, conv_id, platform, recipient_id, _vfb_reply[:500])
                    )
                    conn.commit()
                    return

                # ── Deterministic story reply (NUMBER 33) ───────────────────
                if extra.get("replied_story_id"):
                    story_ctx_data = extra.get("story_ctx_data") or {}
                    det_reply = _build_deterministic_story_reply(
                        content, story_ctx_data, restaurant_id
                    )
                    if det_reply:
                        logger.info(
                            f"[story-det] req={req_id} conv={conv_id} "
                            f"product={story_ctx_data.get('product_name','?')!r} "
                            f"reply_len={len(det_reply)}"
                        )
                        _send_reply(channel_data, det_reply)
                        recipient_id = (
                            channel_data.get("chat_id") or
                            channel_data.get("to") or
                            channel_data.get("recipient_id") or ""
                        )
                        conn.execute(
                            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'bot', ?)",
                            (str(uuid.uuid4()), conv_id, det_reply)
                        )
                        conn.execute(
                            """INSERT INTO outbound_messages
                               (id, restaurant_id, conversation_id, platform, recipient_id, content, status, error)
                               VALUES (?, ?, ?, ?, ?, ?, 'sent', '')""",
                            (str(uuid.uuid4()), restaurant_id, conv_id, platform, recipient_id, det_reply[:500])
                        )
                        conn.commit()
                        return

                # Build context for bot — use rich story_context if available
                bot_input = content
                if extra.get("replied_story_id"):
                    story_ctx = extra.get("story_context") or "[العميل يرد على ستوري للمطعم]"
                    bot_input = (
                        f"{story_ctx}\n\n"
                        f"تعليمات: الزبون يرد على الستوري. إذا قال 'هذا' أو 'اريد' أو أرسل إيموجي "
                        f"فهو يقصد المنتج المذكور في الستوري. ابدأ flow البيع مباشرة.\n\n"
                        f"رد العميل: {content}"
                    )
                elif extra.get("media_type") == "voice":
                    bot_input = f"[فويس] {content}"

                logger.info(f"[bot-call] req={req_id} conv={conv_id} restaurant={restaurant_id}")
                # Run AI bot (sync; timeout=30s is set on the OpenAI client)
                result = bot.process_message(restaurant_id, conv_id, bot_input)
                reply_text = result.get("reply", "")
                action = result.get("action", "reply")
                extracted_order = result.get("extracted_order")

                logger.info(
                    f"[bot-reply] req={req_id} conv={conv_id} action={action} "
                    f"reply_len={len(reply_text)} preview={reply_text[:60]!r}"
                )

                # Save bot reply
                bot_msg_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'bot', ?)",
                    (bot_msg_id, conv_id, reply_text)
                )

                # Increment bot turn count
                conn.execute(
                    "UPDATE conversations SET bot_turn_count=COALESCE(bot_turn_count,0)+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (conv_id,)
                )

                if action == "escalate":
                    # Atomic escalation guard — re-read mode from DB (not the stale snapshot)
                    # to prevent double notification from concurrent threads
                    fresh_conv = conn.execute(
                        "SELECT mode FROM conversations WHERE id=?", (conv_id,)
                    ).fetchone()
                    if fresh_conv and fresh_conv["mode"] != "human":
                        conn.execute(
                            "UPDATE conversations SET mode='human', handoff_reason=?, escalated_at=CURRENT_TIMESTAMP WHERE id=?",
                            ("escalation_requested", conv_id)
                        )
                        conn.commit()  # commit before notification so second thread sees 'human'
                        _create_notification(
                            conn, restaurant_id,
                            "escalation",
                            "طلب تحويل للموظف",
                            f"العميل {customer.get('name', '')} يطلب التحدث مع موظف",
                            "conversation", conv_id
                        )
                        ws_manager.broadcast_sync(restaurant_id, "escalation", {
                            "conv_id": conv_id,
                            "name": customer.get("name", ""),
                            "platform": platform,
                        })

                conn.commit()

                # Send reply via platform + log result
                send_ok, send_err = _send_reply(channel_data, reply_text)

                # Send menu images if bot returned media (menu image intent)
                for _img in result.get("media", []):
                    _img_ok, _img_err = _send_image_via_channel(
                        channel_data,
                        _img.get("url", ""),
                        _img.get("caption", ""),
                    )
                    if not _img_ok:
                        logger.warning(f"[image-send] failed for conv={conv_id}: {_img_err}")

                recipient_id = (
                    channel_data.get("chat_id") or
                    channel_data.get("to") or
                    channel_data.get("recipient_id") or ""
                )
                _tag = f"[{platform[:2]}-reply-{'sent' if send_ok else 'error'}]"
                logger.info(
                    f"{_tag} req={req_id} restaurant={restaurant_id} "
                    f"conv={conv_id} recipient={recipient_id[:12] if recipient_id else '?'} "
                    f"reply_len={len(reply_text)}"
                    + (f" error={send_err}" if not send_ok else "")
                )
                conn.execute(
                    """INSERT INTO outbound_messages
                       (id, restaurant_id, conversation_id, platform, recipient_id, content, status, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()), restaurant_id, conv_id,
                        platform, recipient_id,
                        reply_text[:500],
                        "sent" if send_ok else "failed",
                        send_err,
                    )
                )
                conn.commit()

                # Auto-create order in DB when bot confirmed a complete order (✅ summary)
                confirmed_order = result.get("confirmed_order")
                if confirmed_order and confirmed_order.get("items"):
                    _auto_create_order(
                        conn, restaurant_id, customer, platform, confirmed_order, conv_id
                    )

                # Fallback keyword-based notification (no DB order record, just alert)
                elif extracted_order:
                    _create_notification(
                        conn, restaurant_id,
                        "new_order",
                        "طلب جديد من البوت",
                        f"العميل {customer.get('name', '')} طلب {len(extracted_order.get('items', []))} منتجات",
                        "customer", customer_id
                    )
                    conn.commit()
                    ws_manager.broadcast_sync(restaurant_id, "new_order", {
                        "name": customer.get("name", ""),
                        "platform": platform,
                        "conv_id": conv_id,
                    })

                _log_activity(
                    conn, restaurant_id,
                    "bot_replied",
                    "conversation", conv_id,
                    f"البوت رد على {customer.get('name', '')} عبر {platform}"
                )

            else:
                # Human mode — increment unread
                conn.execute(
                    "UPDATE conversations SET unread_count=COALESCE(unread_count,0)+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (conv_id,)
                )
                conn.commit()

                _create_notification(
                    conn, restaurant_id,
                    "new_message",
                    "رسالة جديدة",
                    f"رسالة جديدة من {customer.get('name', '')} عبر {platform}",
                    "conversation", conv_id
                )
                conn.commit()
                ws_manager.broadcast_sync(restaurant_id, "new_message", {
                    "conv_id": conv_id,
                    "name": customer.get("name", ""),
                    "platform": platform,
                })

                _log_activity(
                    conn, restaurant_id,
                    "new_message",
                    "conversation", conv_id,
                    f"رسالة من {customer.get('name', '')} عبر {platform}"
                )

        conn.commit()
        logger.info(
            f"[incoming-done] req={req_id} conv={conv_id} "
            f"elapsed={(_t.monotonic()-_t_start)*1000:.0f}ms"
        )
    finally:
        conn.close()


# ── Send helpers ──────────────────────────────────────────────────────────────

def _send_reply(channel_data: dict, text: str) -> tuple:
    """Dispatch reply to the correct platform. Returns (success: bool, error: str)."""
    platform = channel_data.get("platform", "")
    try:
        if platform == "telegram":
            bot_token = channel_data.get("bot_token")
            chat_id = channel_data.get("chat_id")
            if not bot_token:
                return False, "Bot Token غير مضبوط في قاعدة البيانات"
            if not chat_id:
                return False, "chat_id مفقود في بيانات القناة"
            _send_telegram(bot_token, chat_id, text)
        elif platform == "whatsapp":
            access_token = channel_data.get("access_token")
            phone_number_id = channel_data.get("phone_number_id")
            to = channel_data.get("to")
            if not access_token:
                return False, "WhatsApp access_token مفقود — أعد الربط عبر OAuth"
            if not phone_number_id:
                return False, "WHATSAPP_PHONE_NUMBER_ID غير مضبوط"
            if not to:
                return False, "رقم المستلم (to) مفقود"
            _send_whatsapp(access_token, phone_number_id, to, text)
        elif platform in ("instagram", "facebook"):
            page_token = channel_data.get("access_token") or channel_data.get("page_token")
            recipient_id = channel_data.get("recipient_id")
            if not page_token:
                return False, f"{platform} page_token مفقود — أعد الربط عبر OAuth"
            if not recipient_id:
                return False, "recipient_id مفقود"
            _send_facebook_messenger(page_token, recipient_id, text)
        return True, ""
    except Exception as e:
        logger.error(f"[webhooks] send error on {platform}: {e}")
        return False, str(e)


def _classify_telegram_error(status_code: int, description: str) -> str:
    """Map a Telegram API failure to a human-readable diagnostic string."""
    desc = (description or "").lower()
    if status_code == 401 or "unauthorized" in desc or "invalid token" in desc:
        return "توكن غير صالح (401 Unauthorized) — تحقق من Bot Token"
    if "bot was blocked" in desc or "user is deactivated" in desc:
        return "المستخدم حجب البوت أو حذف حسابه"
    if "chat not found" in desc or status_code == 400:
        return f"chat_id غير صحيح أو البوت لم يبدأ محادثة مع المستخدم بعد: {description}"
    if status_code == 403 or "forbidden" in desc:
        return f"مرفوض (403 Forbidden) — ربما حجب المستخدم البوت: {description}"
    if "too many requests" in desc or status_code == 429:
        return "تجاوز حد المعدل (429 Too Many Requests) — أبطئ الإرسال"
    if status_code and status_code >= 500:
        return f"خطأ في خوادم Telegram ({status_code}) — أعد المحاولة لاحقاً"
    return f"Telegram API error ({status_code}): {description}"


def _send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    """Send a message via Telegram Bot API."""
    import re as _re
    # Strip markdown that GPT generates but Telegram displays as plain symbols
    text = _re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=_re.DOTALL)   # **bold** → bold
    text = _re.sub(r'\*(.+?)\*', r'\1', text, flags=_re.DOTALL)        # *italic* → italic
    text = _re.sub(r'^#{1,6}\s+', '', text, flags=_re.MULTILINE)       # ### Heading → Heading
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Do NOT use parse_mode=HTML — unescaped < > & in AI replies causes silent 400 from Telegram
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(url, json={"chat_id": chat_id, "text": text})
        result = r.json()
        if result.get("ok"):
            logger.info(f"[telegram] sendMessage OK → chat_id={chat_id}")
        else:
            description = result.get("description", str(result))
            friendly = _classify_telegram_error(r.status_code, description)
            logger.error(f"[telegram] sendMessage FAILED → chat_id={chat_id} | {friendly}")
            raise Exception(friendly)
    except Exception:
        raise


def _classify_meta_error(status_code: int, result: dict) -> str:
    """Map a Meta Graph API error to a human-readable diagnostic string."""
    err = result.get("error", {})
    code = err.get("code", 0)
    msg  = err.get("message", str(result))
    if status_code == 401 or code in (190, 102):
        return f"رمز الوصول منتهي أو غير صالح (code={code}) — أعد الربط عبر OAuth"
    if code == 131030 or "phone number" in msg.lower():
        return f"رقم الهاتف غير موجود في WhatsApp أو الحساب غير مفعّل: {msg}"
    if code == 131049:
        return "المستخدم لم يبدأ محادثة مع البوت (نافذة 24 ساعة منتهية)"
    if code == 131047:
        return "رسالة مكررة — تم إرسالها مسبقاً"
    if status_code == 403 or code == 200:
        return f"مرفوض (403) — تحقق من صلاحيات التطبيق وموافقة Meta: {msg}"
    if status_code and status_code >= 500:
        return f"خطأ في خوادم Meta ({status_code}) — أعد المحاولة"
    return f"Meta API error (HTTP {status_code}, code={code}): {msg}"


def _send_whatsapp(access_token: str, phone_number_id: str, to: str, text: str) -> None:
    """Send a message via WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(url, headers=headers, json=payload)
        result = r.json()
        if r.status_code == 200:
            logger.info(f"[whatsapp] sendMessage OK → to={to}")
        else:
            friendly = _classify_meta_error(r.status_code, result)
            logger.error(f"[whatsapp] sendMessage FAILED → to={to} | {friendly}")
            raise Exception(friendly)
    except Exception:
        raise


def _send_facebook_messenger(page_token: str, recipient_id: str, text: str) -> None:
    """Send a message via Facebook Graph API (Messenger/Instagram)."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": page_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(url, params=params, json=payload)
        result = r.json()
        if r.status_code == 200:
            logger.info(f"[messenger] sendMessage OK → recipient={recipient_id}")
        else:
            friendly = _classify_meta_error(r.status_code, result)
            logger.error(f"[messenger] sendMessage FAILED → recipient={recipient_id} | {friendly}")
            raise Exception(friendly)
    except Exception:
        raise


def _send_image_via_channel(channel_data: dict, image_url: str, caption: str = "") -> tuple:
    """Send a single image via the appropriate channel. Returns (success, error)."""
    platform = channel_data.get("platform", "")
    try:
        if platform == "telegram":
            bot_token = channel_data.get("bot_token")
            chat_id = channel_data.get("chat_id")
            if not bot_token or not chat_id:
                return False, "bot_token or chat_id missing"
            tg_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            payload = {"chat_id": chat_id, "photo": image_url}
            if caption:
                payload["caption"] = caption
            with httpx.Client(timeout=15) as client:
                r = client.post(tg_url, json=payload)
            result = r.json()
            if result.get("ok"):
                logger.info(f"[telegram] sendPhoto OK → chat_id={chat_id}")
                return True, ""
            description = result.get("description", str(result))
            friendly = _classify_telegram_error(r.status_code, description)
            logger.warning(f"[telegram] sendPhoto FAILED → {friendly}")
            return False, friendly

        elif platform == "whatsapp":
            access_token = channel_data.get("access_token")
            phone_number_id = channel_data.get("phone_number_id")
            to = channel_data.get("to")
            if not access_token or not phone_number_id or not to:
                return False, "WhatsApp credentials missing"
            wa_url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "image",
                "image": {"link": image_url, "caption": caption},
            }
            with httpx.Client(timeout=15) as client:
                r = client.post(wa_url, headers=headers, json=payload)
            result = r.json()
            if r.status_code == 200:
                logger.info(f"[whatsapp] sendImage OK → to={to}")
                return True, ""
            friendly = _classify_meta_error(r.status_code, result)
            logger.warning(f"[whatsapp] sendImage FAILED → {friendly}")
            return False, friendly

        elif platform in ("instagram", "facebook"):
            page_token = channel_data.get("access_token") or channel_data.get("page_token")
            recipient_id = channel_data.get("recipient_id")
            if not page_token or not recipient_id:
                return False, "page_token or recipient_id missing"
            fb_url = "https://graph.facebook.com/v19.0/me/messages"
            params = {"access_token": page_token}
            payload = {
                "recipient": {"id": recipient_id},
                "message": {
                    "attachment": {
                        "type": "image",
                        "payload": {"url": image_url, "is_reusable": True},
                    }
                },
            }
            with httpx.Client(timeout=15) as client:
                r = client.post(fb_url, params=params, json=payload)
            result = r.json()
            if r.status_code == 200:
                logger.info(f"[messenger] sendImage OK → recipient={recipient_id}")
                return True, ""
            friendly = _classify_meta_error(r.status_code, result)
            logger.warning(f"[messenger] sendImage FAILED → {friendly}")
            return False, friendly

        else:
            # Unknown platform — skip silently, text reply already sent
            return True, ""
    except Exception as e:
        logger.error(f"[webhooks] sendImage error on {platform}: {e}")
        return False, str(e)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _find_or_create_customer(
    conn,
    restaurant_id: str,
    platform: str,
    external_id: str,
    name: str,
    phone: str,
) -> dict:
    """Find existing customer by platform+external_id or create a new one."""
    # Guard: confirm restaurant exists before any INSERT (prevents FK violation after DB wipe)
    rest = conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    if not rest:
        raise ValueError(
            f"ORPHANED_WEBHOOK: restaurant_id={restaurant_id} not found in database. "
            "SQLite was likely wiped on Render deploy. Migrate to PostgreSQL and re-register the webhook."
        )

    row = conn.execute(
        """SELECT * FROM customers WHERE restaurant_id=? AND platform=? AND
           (phone=? OR id IN (
               SELECT customer_id FROM conversation_memory
               WHERE restaurant_id=? AND memory_key='external_id' AND memory_value=?
           ))""",
        (restaurant_id, platform, external_id, restaurant_id, external_id)
    ).fetchone()

    if row:
        conn.execute("UPDATE customers SET last_seen=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        return dict(row)

    # Create new customer
    cid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO customers (id, restaurant_id, name, phone, platform, vip,
                               preferences, favorite_item, total_orders, total_spent)
        VALUES (?, ?, ?, ?, ?, 0, '', '', 0, 0)
    """, (cid, restaurant_id, name or "مجهول", phone or external_id, platform))

    # Store external_id in memory for future lookups
    conn.execute("""
        INSERT INTO conversation_memory (id, restaurant_id, customer_id, memory_key, memory_value)
        VALUES (?, ?, ?, 'external_id', ?)
        ON CONFLICT(restaurant_id, customer_id, memory_key) DO UPDATE SET memory_value=excluded.memory_value
    """, (str(uuid.uuid4()), restaurant_id, cid, external_id))

    return {
        "id": cid,
        "restaurant_id": restaurant_id,
        "name": name or "مجهول",
        "phone": phone or external_id,
        "platform": platform,
        "vip": 0,
        "preferences": "",
        "total_orders": 0,
        "total_spent": 0.0,
    }


def _find_or_create_conversation(
    conn, restaurant_id: str, customer_id: str,
    channel: str = "", first_contact: bool = False
) -> dict:
    """Find open conversation for customer or create one.
    Returns the conversation dict with '_is_new' key indicating whether it was just created.
    """
    row = conn.execute(
        "SELECT * FROM conversations WHERE restaurant_id=? AND customer_id=? AND status='open' ORDER BY updated_at DESC LIMIT 1",
        (restaurant_id, customer_id)
    ).fetchone()

    if row:
        d = dict(row)
        d["_is_new"] = False
        return d

    conv_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO conversations
            (id, restaurant_id, customer_id, mode, status, urgent, unread_count, bot_turn_count, channel, first_contact)
        VALUES (?, ?, ?, 'bot', 'open', 0, 0, 0, ?, ?)
    """, (conv_id, restaurant_id, customer_id, channel, 1 if first_contact else 0))

    return {
        "id": conv_id,
        "restaurant_id": restaurant_id,
        "customer_id": customer_id,
        "mode": "bot",
        "status": "open",
        "urgent": 0,
        "unread_count": 0,
        "bot_turn_count": 0,
        "channel": channel,
        "first_contact": 1 if first_contact else 0,
        "_is_new": True,
    }


def _log_activity(
    conn,
    restaurant_id: str,
    action: str,
    entity_type: str = "",
    entity_id: str = "",
    description: str = "",
    user_id: Optional[str] = None,
    user_name: str = "System",
) -> None:
    """Insert an activity log entry."""
    try:
        conn.execute(
            """INSERT INTO activity_log (id, restaurant_id, user_id, user_name, action, entity_type, entity_id, description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), restaurant_id, user_id, user_name, action, entity_type, entity_id, description)
        )
    except Exception as e:
        print(f"[webhooks] _log_activity error: {e}")


def _auto_create_order(
    conn,
    restaurant_id: str,
    customer: dict,
    platform: str,
    order_data: dict,
    conversation_id: str = "",
) -> Optional[str]:
    """
    Automatically create an order + order_items record when the bot confirms an order.
    Returns the new order_id, or None if skipped (duplicate).
    """
    customer_id = customer["id"]
    total = order_data.get("total", 0)
    items = order_data.get("items", [])
    address = order_data.get("address", "")
    order_type = order_data.get("type", "delivery")  # "delivery" or "pickup"

    if not items or total <= 0:
        return None

    # Require address only for delivery orders
    if order_type == "delivery" and not address:
        logger.info(f"[order] skipping — delivery order needs address conv={conversation_id}")
        return None

    # Dedup primary: same conversation can only produce one order
    if conversation_id:
        existing = conn.execute(
            "SELECT id FROM orders WHERE conversation_id=? AND restaurant_id=?",
            (conversation_id, restaurant_id)
        ).fetchone()
        if existing:
            logger.info(f"[order] duplicate skipped — conv={conversation_id} already has order {existing['id']}")
            return None

    # Dedup fallback: same customer + total within 3 minutes (catches edge cases)
    from datetime import datetime as _datetime, timedelta as _td
    three_min_ago = (_datetime.utcnow() - _td(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
    existing = conn.execute(
        "SELECT id FROM orders WHERE restaurant_id=? AND customer_id=? AND total=? AND created_at >= ?",
        (restaurant_id, customer_id, total, three_min_ago)
    ).fetchone()
    if existing:
        logger.info(f"[order] duplicate skipped — customer={customer_id} total={total} within 3min")
        return None

    order_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, address, status, conversation_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (order_id, restaurant_id, customer_id, platform, order_type, total, address, conversation_id))

    for item in items:
        conn.execute("""
            INSERT INTO order_items (id, order_id, product_id, name, price, quantity, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), order_id,
            item.get("product_id"), item.get("name", ""),
            item.get("price", 0), item.get("quantity", 1),
            item.get("notes", ""),
        ))
        if item.get("product_id"):
            conn.execute(
                "UPDATE products SET order_count=COALESCE(order_count,0)+? WHERE id=?",
                (item.get("quantity", 1), item["product_id"])
            )

    # Update customer lifetime stats
    conn.execute("""
        UPDATE customers
        SET total_orders=COALESCE(total_orders,0)+1,
            total_spent=COALESCE(total_spent,0)+?
        WHERE id=?
    """, (total, customer_id))

    # Update favorite_item: most ordered product by this customer
    try:
        top = conn.execute("""
            SELECT oi.name, SUM(oi.quantity) AS qty
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            WHERE o.customer_id=? AND o.restaurant_id=?
            GROUP BY oi.name
            ORDER BY qty DESC
            LIMIT 1
        """, (customer_id, restaurant_id)).fetchone()
        if top:
            conn.execute(
                "UPDATE customers SET favorite_item=? WHERE id=?",
                (top["name"], customer_id)
            )
    except Exception as e:
        logger.warning(f"[order] favorite_item update failed: {e}")

    # Save last_order_summary to conversation_memory
    try:
        summary_parts = [
            f"{i.get('name', '')} ×{i.get('quantity', 1)}"
            for i in items[:3]
        ]
        summary = "، ".join(summary_parts) + f" — {int(total):,} د.ع"
        conn.execute("""
            INSERT INTO conversation_memory
                (id, restaurant_id, customer_id, memory_key, memory_value, updated_at)
            VALUES (?, ?, ?, 'last_order_summary', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(restaurant_id, customer_id, memory_key)
            DO UPDATE SET memory_value=excluded.memory_value, updated_at=CURRENT_TIMESTAMP
        """, (str(uuid.uuid4()), restaurant_id, customer_id, summary))
    except Exception as e:
        logger.warning(f"[order] last_order_summary save failed: {e}")

    # NUMBER 42 RISK-05 — increment promo uses_count inside same transaction as order INSERT.
    # This ensures the counter only goes up when an order actually lands in the DB.
    _promo_id = order_data.get("promo_code_id")
    if _promo_id:
        try:
            conn.execute(
                "UPDATE promo_codes SET uses_count=uses_count+1 WHERE id=?",
                (_promo_id,)
            )
            logger.info(f"[promo-risk05] uses_count incremented for promo_id={_promo_id} order={order_id}")
        except Exception as _pe:
            logger.warning(f"[promo-risk05] increment failed (non-fatal): {_pe}")

    conn.commit()
    # Quality tracking — mark conversation as converted
    try:
        conn.execute(
            "UPDATE conversations SET had_order=1, resolution_type='ordered' WHERE id=?",
            (conversation_id,)
        )
        conn.commit()
    except Exception:
        pass
    logger.info(
        f"[order] AUTO-CREATED order_id={order_id} total={total} "
        f"items={len(items)} platform={platform} customer={customer_id}"
    )

    _create_notification(
        conn, restaurant_id,
        "new_order",
        "🛒 طلب جديد",
        f"طلب جديد من {customer.get('name', '')} عبر {platform} — المجموع: {int(total):,} د.ع",
        "order", order_id,
    )
    conn.commit()
    ws_manager.broadcast_sync(restaurant_id, "new_order", {
        "order_id": order_id,
        "name": customer.get("name", ""),
        "platform": platform,
        "total": int(total),
        "conv_id": conversation_id,
    })

    # Send Telegram notification to owner if notify_chat_id is configured
    try:
        settings_row = conn.execute(
            "SELECT notify_chat_id FROM settings WHERE restaurant_id=?", (restaurant_id,)
        ).fetchone()
        notify_chat_id = settings_row["notify_chat_id"] if settings_row else ""
        if notify_chat_id:
            tg_channel = conn.execute(
                "SELECT bot_token FROM channels WHERE restaurant_id=? AND type='telegram' AND bot_token != '' LIMIT 1",
                (restaurant_id,)
            ).fetchone()
            tg_token = tg_channel["bot_token"] if tg_channel else ""
            if tg_token:
                rest_row = conn.execute(
                    "SELECT name FROM restaurants WHERE id=?", (restaurant_id,)
                ).fetchone()
                rest_name = rest_row["name"] if rest_row else "المطعم"
                items_summary = "\n".join(
                    f"  • {it.get('name','')} × {it.get('quantity',1)} — {int(it.get('price',0)):,} د.ع"
                    for it in items
                )
                msg_text = (
                    f"🔔 طلب جديد!\n"
                    f"👤 {customer.get('name','مجهول')} — {platform}\n"
                    f"📦 {items_summary}\n"
                    f"💰 المجموع: {int(total):,} د.ع"
                )
                httpx.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": notify_chat_id, "text": msg_text},
                    timeout=5,
                )
    except Exception as _tg_err:
        logger.warning(f"[order] Telegram notify failed (non-fatal): {_tg_err}")

    return order_id


def _create_notification(
    conn,
    restaurant_id: str,
    ntype: str,
    title: str,
    message: str,
    entity_type: str = "",
    entity_id: str = "",
) -> None:
    """Create a notification entry."""
    try:
        conn.execute(
            """INSERT INTO notifications (id, restaurant_id, type, title, message, entity_type, entity_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), restaurant_id, ntype, title, message, entity_type, entity_id)
        )
    except Exception as e:
        print(f"[webhooks] _create_notification error: {e}")
