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
    if msg_type != "text":
        return

    text = msg.get("text", {}).get("body", "").strip()
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
        _process_incoming(restaurant_id, customer, conversation, text, channel_data)
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

    # Detect story reply context
    replied_story_id = ""
    replied_story_text = ""
    replied_story_media_url = ""

    reply_to = message.get("reply_to", {})
    if reply_to:
        story = reply_to.get("story", {})
        if story:
            replied_story_id = story.get("id", "")
            replied_story_media_url = story.get("url", "")
            # Story text may appear in the message text itself; keep it for context
            replied_story_text = text  # the user's message in context of the story

        if not text:
            text = reply_to.get("text", "").strip()

    if not text:
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='instagram'",
            (restaurant_id,)
        ).fetchone()
        access_token = ch["token"] if ch else None

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
            "replied_story_id": replied_story_id,
            "replied_story_text": replied_story_text,
            "replied_story_media_url": replied_story_media_url,
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
    if not text or not sender_id:
        return

    conn = database.get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM channels WHERE restaurant_id=? AND type='facebook'",
            (restaurant_id,)
        ).fetchone()
        page_token = ch["token"] if ch else None

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
        _process_incoming(restaurant_id, customer, conversation, text, channel_data)
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
            # Build context hint for story replies
            bot_input = content
            if extra.get("replied_story_id"):
                hint = f"[المستخدم يرد على قصة (Story)] "
                if extra.get("replied_story_media_url"):
                    hint += f"رابط القصة: {extra['replied_story_media_url']} — "
                bot_input = hint + content

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

            # Create new_order notification if order extracted
            if extracted_order:
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
