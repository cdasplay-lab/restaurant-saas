"""
Webhook handlers for Telegram, WhatsApp, Instagram, and Facebook Messenger.
"""
import uuid
import json
import os
import logging
from typing import Optional

import httpx

import database
from services import bot

logger = logging.getLogger("restaurant-saas")


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_openai_client = None


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
    if voice_obj and not text:
        media_type = "voice"
        file_id = voice_obj.get("file_id", "")

        conn_tmp = database.get_db()
        ch_tmp = conn_tmp.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='telegram'",
            (restaurant_id,)
        ).fetchone()
        bot_token = ch_tmp["token"] if ch_tmp else None
        conn_tmp.close()

        if bot_token and file_id:
            media_url, voice_transcript = _download_and_transcribe_telegram(
                bot_token, file_id
            )
        text = voice_transcript or "[رسالة صوتية]"

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
        conversation = _find_or_create_conversation(conn, restaurant_id, customer["id"])
        conn.commit()

        channel_data = {
            "platform": "telegram",
            "bot_token": bot_token,
            "chat_id": chat_id,
        }
        extra = {"media_type": media_type, "media_url": media_url, "voice_transcript": voice_transcript}
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    finally:
        conn.close()


def _download_and_transcribe_telegram(bot_token: str, file_id: str) -> tuple:
    """Download a voice file from Telegram and transcribe via Whisper. Returns (file_url, transcript)."""
    try:
        with httpx.Client(timeout=15) as client:
            # Get file path from Telegram
            r = client.get(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                params={"file_id": file_id}
            )
            r.raise_for_status()
            file_path = r.json().get("result", {}).get("file_path", "")
            if not file_path:
                return "", ""

            file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

            # Download the audio bytes
            audio_resp = client.get(file_url)
            audio_resp.raise_for_status()
            audio_bytes = audio_resp.content

        # Transcribe with Whisper
        client_openai = _get_openai()
        if not client_openai:
            return file_url, ""

        import io
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"

        result = client_openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ar",
        )
        transcript = result.text.strip()
        return file_url, transcript

    except Exception as e:
        logger.error(f"[telegram] voice transcription error: {e}")
        return "", ""


def _vision_describe(img_bytes: bytes, restaurant_id: str, platform: str = "") -> str:
    """Send raw image bytes to OpenAI Vision. Returns Arabic description string."""
    import base64
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
    import base64
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
                   access_token: str = "", platform: str = "") -> str:
    """
    Full story analysis pipeline. Returns a rich context string for the bot.
    Handles both image stories and video stories (via thumbnail).
    """
    if not media_url:
        return "[العميل يرد على ستوري للمطعم — اسأله بودية عما يرغب به]"

    img_bytes, is_video = _fetch_story_media(media_url, story_id, access_token)
    video_tag = " [فيديو]" if is_video else ""

    if not img_bytes:
        # Video with no thumbnail — still engage warmly
        return f"[العميل يرد على ستوري فيديو للمطعم — رحّب به واسأله عما يشتهيه]"

    match = _match_story_to_product(img_bytes, restaurant_id)
    product = match.get("product")
    confidence = match.get("confidence", "low")
    description = match.get("description", "")

    if product and confidence in ("high", "medium"):
        price_str = f"{int(product['price']):,}" if product.get("price") else ""
        price_part = f" — {price_str} د.ع" if price_str else ""
        conf_note = "" if confidence == "high" else " (تقريباً)"
        return (
            f"[العميل يرد على ستوري{video_tag} يعرض: {product['name']}{price_part}{conf_note}]"
            f"\nسياق للبوت: هذا المنتج موجود في قائمتك. استغل الفرصة وابدأ flow البيع مباشرة."
        )

    if description and description not in ("محتوى من المطعم", ""):
        return f"[العميل يرد على ستوري{video_tag} يظهر: {description} — اسأله إذا يريد تجربته]"

    return f"[العميل يرد على ستوري{video_tag} للمطعم — رحّب به وابدأ محادثة البيع]"


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
    if msg_type not in ("text", "image"):
        return

    text = ""
    media_type = ""
    media_url = ""

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "").strip()
    elif msg_type == "image":
        media_type = "image"
        caption = msg.get("image", {}).get("caption", "").strip()
        media_id = msg.get("image", {}).get("id", "")
        # Need access_token to resolve WhatsApp media — load channel first
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
        conversation = _find_or_create_conversation(conn, restaurant_id, customer["id"])
        conn.commit()

        channel_data = {
            "platform": "whatsapp",
            "access_token": access_token,
            "phone_number_id": phone_number_id,
            "to": external_id,
        }
        extra = {"media_type": media_type, "media_url": media_url}
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    finally:
        conn.close()


# ── Instagram ─────────────────────────────────────────────────────────────────

def handle_instagram(restaurant_id: str, data: dict) -> None:
    """Process an incoming Instagram message, including story replies."""
    _conn = database.get_db()
    _rest = _conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    _conn.close()
    if not _rest:
        logger.error(f"[instagram] ORPHANED WEBHOOK — restaurant_id={restaurant_id} not in DB. Re-register after PostgreSQL migration.")
        return

    try:
        entry = data["entry"][0]
        messaging = entry["messaging"][0]
    except (KeyError, IndexError):
        return

    sender_id = messaging.get("sender", {}).get("id", "")
    message = messaging.get("message", {})
    text = message.get("text", "").strip()
    if not sender_id:
        return

    media_type = ""
    media_url_ig = ""

    # Handle image attachments
    attachments = message.get("attachments", [])
    for att in attachments:
        if att.get("type") == "image":
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
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='instagram'",
            (restaurant_id,)
        ).fetchone()
        access_token = ch["token"] if ch else None

        # Analyze story media smartly (image OR video thumbnail)
        if replied_story_id and replied_story_media_url:
            story_context = _analyze_story(
                replied_story_media_url, replied_story_id,
                restaurant_id, access_token or "", "instagram"
            )
            logger.info(f"[instagram] story_context: {story_context[:80]}")

        customer = _find_or_create_customer(
            conn, restaurant_id, "instagram", sender_id, "Instagram User", ""
        )
        conversation = _find_or_create_conversation(conn, restaurant_id, customer["id"])
        conn.commit()

        channel_data = {
            "platform": "instagram",
            "access_token": access_token,
            "recipient_id": sender_id,
        }
        extra = {
            "media_type": media_type,
            "media_url": media_url_ig,
            "replied_story_id": replied_story_id,
            "replied_story_text": replied_story_text,
            "replied_story_media_url": replied_story_media_url,
            "story_context": story_context,
        }
        _process_incoming(restaurant_id, customer, conversation, text, channel_data, extra)
    finally:
        conn.close()


# ── Facebook Messenger ────────────────────────────────────────────────────────

def handle_facebook(restaurant_id: str, data: dict) -> None:
    """Process an incoming Facebook Messenger message."""
    _conn = database.get_db()
    _rest = _conn.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    _conn.close()
    if not _rest:
        logger.error(f"[facebook] ORPHANED WEBHOOK — restaurant_id={restaurant_id} not in DB. Re-register after PostgreSQL migration.")
        return

    try:
        entry = data["entry"][0]
        messaging = entry["messaging"][0]
    except (KeyError, IndexError):
        return

    sender_id = messaging.get("sender", {}).get("id", "")
    message = messaging.get("message", {})
    text = message.get("text", "").strip()
    if not sender_id:
        return

    media_type_fb = ""
    media_url_fb = ""
    replied_story_id_fb = ""
    replied_story_media_url_fb = ""
    story_context_fb = ""

    # Handle image attachments
    attachments = message.get("attachments", [])
    for att in attachments:
        if att.get("type") == "image":
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
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='facebook'",
            (restaurant_id,)
        ).fetchone()
        page_token = ch["token"] if ch else None

        # Analyze story media
        if replied_story_id_fb and replied_story_media_url_fb:
            story_context_fb = _analyze_story(
                replied_story_media_url_fb, replied_story_id_fb,
                restaurant_id, page_token or "", "facebook"
            )
            logger.info(f"[facebook] story_context: {story_context_fb[:80]}")

        customer = _find_or_create_customer(
            conn, restaurant_id, "facebook", sender_id, "Facebook User", ""
        )
        conversation = _find_or_create_conversation(conn, restaurant_id, customer["id"])
        conn.commit()

        channel_data = {
            "platform": "facebook",
            "page_token": page_token,
            "recipient_id": sender_id,
        }
        extra = {
            "media_type": media_type_fb,
            "media_url": media_url_fb,
            "replied_story_id": replied_story_id_fb,
            "replied_story_media_url": replied_story_media_url_fb,
            "story_context": story_context_fb,
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

        # 1. Save customer message with optional media/story metadata
        msg_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, media_type, media_url, voice_transcript,
                replied_story_id, replied_story_text, replied_story_media_url)
               VALUES (?, ?, 'customer', ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, conv_id, content,
                extra.get("media_type", ""),
                extra.get("media_url", ""),
                extra.get("voice_transcript", ""),
                extra.get("replied_story_id", ""),
                extra.get("replied_story_text", ""),
                extra.get("replied_story_media_url", ""),
            )
        )
        conn.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (conv_id,)
        )
        conn.commit()

        # 2. Reload conversation to get latest mode
        conv_row = conn.execute(
            "SELECT * FROM conversations WHERE id=?", (conv_id,)
        ).fetchone()
        mode = conv_row["mode"] if conv_row else "bot"

        if mode == "bot":
            # Build context for bot — use rich story_context if available
            bot_input = content
            if extra.get("replied_story_id"):
                story_ctx = extra.get("story_context") or "[العميل يرد على ستوري للمطعم]"
                bot_input = f"{story_ctx}\nرد العميل: {content}"

            # Run AI bot
            result = bot.process_message(restaurant_id, conv_id, bot_input)
            reply_text = result.get("reply", "")
            action = result.get("action", "reply")
            extracted_order = result.get("extracted_order")

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
                conn.execute(
                    "UPDATE conversations SET mode='human', handoff_reason=?, escalated_at=CURRENT_TIMESTAMP WHERE id=?",
                    ("escalation_requested", conv_id)
                )
                _create_notification(
                    conn, restaurant_id,
                    "escalation",
                    "طلب تحويل للموظف",
                    f"العميل {customer.get('name', '')} يطلب التحدث مع موظف",
                    "conversation", conv_id
                )

            conn.commit()

            # Send reply via platform
            _send_reply(channel_data, reply_text)

            # Auto-create order in DB when bot confirmed a complete order (✅ summary)
            confirmed_order = result.get("confirmed_order")
            if confirmed_order and confirmed_order.get("items"):
                _auto_create_order(
                    conn, restaurant_id, customer, platform, confirmed_order
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

            _log_activity(
                conn, restaurant_id,
                "new_message",
                "conversation", conv_id,
                f"رسالة من {customer.get('name', '')} عبر {platform}"
            )

        conn.commit()
    finally:
        conn.close()


# ── Send helpers ──────────────────────────────────────────────────────────────

def _send_reply(channel_data: dict, text: str) -> None:
    """Dispatch reply to the correct platform."""
    platform = channel_data.get("platform", "")
    try:
        if platform == "telegram":
            bot_token = channel_data.get("bot_token")
            chat_id = channel_data.get("chat_id")
            if bot_token and chat_id:
                _send_telegram(bot_token, chat_id, text)
        elif platform == "whatsapp":
            access_token = channel_data.get("access_token")
            phone_number_id = channel_data.get("phone_number_id")
            to = channel_data.get("to")
            if access_token and phone_number_id and to:
                _send_whatsapp(access_token, phone_number_id, to, text)
        elif platform in ("instagram", "facebook"):
            page_token = channel_data.get("access_token") or channel_data.get("page_token")
            recipient_id = channel_data.get("recipient_id")
            if page_token and recipient_id:
                _send_facebook_messenger(page_token, recipient_id, text)
    except Exception as e:
        logger.error(f"[webhooks] send error on {platform}: {e}")


def _send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Do NOT use parse_mode=HTML — unescaped < > & in AI replies causes silent 400 from Telegram
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(url, json={"chat_id": chat_id, "text": text})
        result = r.json()
        if result.get("ok"):
            logger.info(f"[telegram] sendMessage OK → chat_id={chat_id}")
        else:
            logger.error(f"[telegram] sendMessage FAILED → chat_id={chat_id} | {result.get('description', result)}")
    except Exception as e:
        logger.error(f"[telegram] sendMessage exception → chat_id={chat_id} | {e}")


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
            logger.error(f"[whatsapp] sendMessage FAILED → to={to} | {result}")
    except Exception as e:
        logger.error(f"[whatsapp] sendMessage exception → to={to} | {e}")


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
            logger.error(f"[messenger] sendMessage FAILED → recipient={recipient_id} | {result}")
    except Exception as e:
        logger.error(f"[messenger] sendMessage exception → recipient={recipient_id} | {e}")


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


def _find_or_create_conversation(conn, restaurant_id: str, customer_id: str) -> dict:
    """Find open conversation for customer or create one."""
    row = conn.execute(
        "SELECT * FROM conversations WHERE restaurant_id=? AND customer_id=? AND status='open' ORDER BY updated_at DESC LIMIT 1",
        (restaurant_id, customer_id)
    ).fetchone()

    if row:
        return dict(row)

    conv_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO conversations (id, restaurant_id, customer_id, mode, status, urgent, unread_count, bot_turn_count)
        VALUES (?, ?, ?, 'bot', 'open', 0, 0, 0)
    """, (conv_id, restaurant_id, customer_id))

    return {
        "id": conv_id,
        "restaurant_id": restaurant_id,
        "customer_id": customer_id,
        "mode": "bot",
        "status": "open",
        "urgent": 0,
        "unread_count": 0,
        "bot_turn_count": 0,
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
) -> Optional[str]:
    """
    Automatically create an order + order_items record when the bot confirms an order.
    Returns the new order_id, or None if skipped (duplicate).
    """
    customer_id = customer["id"]
    total = order_data.get("total", 0)
    items = order_data.get("items", [])
    address = order_data.get("address", "")

    if not items or total <= 0:
        return None

    # Dedup: skip if exact same order (same customer + total) placed in last 5 minutes
    existing = conn.execute("""
        SELECT id FROM orders
        WHERE restaurant_id=? AND customer_id=? AND total=?
        AND created_at > datetime('now', '-5 minutes')
    """, (restaurant_id, customer_id, total)).fetchone()
    if existing:
        logger.info(f"[order] duplicate skipped — customer={customer_id} total={total}")
        return None

    order_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, address, status)
        VALUES (?, ?, ?, ?, 'delivery', ?, ?, 'pending')
    """, (order_id, restaurant_id, customer_id, platform, total, address))

    for item in items:
        conn.execute("""
            INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), order_id,
            item.get("product_id"), item.get("name", ""),
            item.get("price", 0), item.get("quantity", 1),
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

    conn.commit()
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
