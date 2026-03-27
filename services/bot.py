"""
AI Bot Service for Restaurant SaaS Platform
Handles conversation processing, order extraction, and escalation detection.
"""
import os
import json
import re
from typing import Optional

import database

# ── Constants ─────────────────────────────────────────────────────────────────

ESCALATION_PHRASES_AR = [
    "مدير", "موظف", "انسان", "إنسان", "شكوى", "استرداد",
    "مشكلة", "ألغ", "إلغ", "مسؤول",
]

ORDER_KEYWORDS = ["أريد", "أطلب", "عايز", "بدي", "ابي", "حابب", "اطلب", "ابغى"]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_openai_client = None


def _get_client():
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


# ── Public API ────────────────────────────────────────────────────────────────

def process_message(restaurant_id: str, conversation_id: str, customer_message: str) -> dict:
    """
    Process an incoming customer message and return a bot reply dict:
      {
        "reply": str,
        "action": "reply" | "escalate",
        "extracted_order": dict | None,
      }
    """
    conn = database.get_db()
    try:
        # Load conversation + customer
        conv = conn.execute(
            "SELECT * FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        if not conv:
            return {"reply": "حدث خطأ، تعذر العثور على المحادثة.", "action": "reply", "extracted_order": None}

        customer = conn.execute(
            "SELECT * FROM customers WHERE id=?", (conv["customer_id"],)
        ).fetchone()

        # Load bot config
        _bot_cfg_row = conn.execute(
            "SELECT * FROM bot_config WHERE restaurant_id=?", (restaurant_id,)
        ).fetchone()
        bot_cfg = dict(_bot_cfg_row) if _bot_cfg_row else None

        # Load settings
        settings = conn.execute(
            "SELECT * FROM settings WHERE restaurant_id=?", (restaurant_id,)
        ).fetchone()

        # Load restaurant
        restaurant = conn.execute(
            "SELECT * FROM restaurants WHERE id=?", (restaurant_id,)
        ).fetchone()

        # Load products for menu
        products = conn.execute(
            "SELECT * FROM products WHERE restaurant_id=? AND available=1 ORDER BY category, name",
            (restaurant_id,)
        ).fetchall()

        # Load customer memory
        memory_rows = conn.execute(
            "SELECT memory_key, memory_value FROM conversation_memory WHERE restaurant_id=? AND customer_id=?",
            (restaurant_id, conv["customer_id"])
        ).fetchall()
        memory = {r["memory_key"]: r["memory_value"] for r in memory_rows}

        # Load last N messages for context
        max_turns = (bot_cfg["max_bot_turns"] if bot_cfg else 15) or 15
        history = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (conversation_id, max_turns * 2)
        ).fetchall()
        history = list(reversed(history))

    finally:
        conn.close()

    # Check escalation conditions
    custom_keywords = []
    if bot_cfg and bot_cfg.get("escalation_keywords"):
        try:
            custom_keywords = json.loads(bot_cfg["escalation_keywords"])
        except Exception:
            custom_keywords = []

    if _detect_escalation(customer_message, custom_keywords):
        fallback = (
            (bot_cfg["fallback_message"] if bot_cfg else None)
            or "سأحيلك لأحد موظفينا الآن، انتظر قليلاً. 🙏"
        )
        # Save memory from this message
        if customer and bot_cfg and bot_cfg.get("memory_enabled", 1):
            _update_memory_from_conversation(restaurant_id, conv["customer_id"], customer_message)
        return {"reply": fallback, "action": "escalate", "extracted_order": None}

    # Check bot turn count limit
    bot_turn_count = conv["bot_turn_count"] if "bot_turn_count" in conv.keys() else 0
    max_bot = (bot_cfg["max_bot_turns"] if bot_cfg else 15) or 15
    auto_handoff = (bot_cfg["auto_handoff_enabled"] if bot_cfg else 1)
    if auto_handoff and bot_turn_count >= max_bot:
        fallback = (
            (bot_cfg["fallback_message"] if bot_cfg else None)
            or "سأحيلك لأحد موظفينا الآن، انتظر قليلاً. 🙏"
        )
        return {"reply": fallback, "action": "escalate", "extracted_order": None}

    # Build system prompt
    system_prompt = _build_system_prompt(
        restaurant=dict(restaurant) if restaurant else {},
        settings=dict(settings) if settings else {},
        bot_cfg=dict(bot_cfg) if bot_cfg else {},
        products=[dict(p) for p in products],
        memory=memory,
        customer=dict(customer) if customer else {},
    )

    # Call OpenAI
    client = _get_client()
    if not client:
        # No OpenAI key — return a simple fallback
        return {
            "reply": "مرحباً! يسعدني مساعدتك. كيف يمكنني خدمتك؟",
            "action": "reply",
            "extracted_order": None,
        }

    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        role = "user" if h["role"] == "customer" else "assistant"
        messages.append({"role": role, "content": h["content"]})
    messages.append({"role": "user", "content": customer_message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        reply_text = response.choices[0].message.content.strip()
    except Exception as e:
        reply_text = "عذراً، حدث خطأ تقني. يرجى المحاولة مجدداً أو التواصل مع فريقنا مباشرة."
        return {"reply": reply_text, "action": "reply", "extracted_order": None}

    # Extract order if enabled
    extracted_order = None
    order_enabled = (bot_cfg["order_extraction_enabled"] if bot_cfg else 1)
    if order_enabled and any(kw in customer_message for kw in ORDER_KEYWORDS):
        extracted_order = _extract_order_from_message(customer_message, [dict(p) for p in products])

    # Update customer memory
    if customer and bot_cfg and bot_cfg.get("memory_enabled", 1):
        _update_memory_from_conversation(restaurant_id, conv["customer_id"], customer_message)

    return {"reply": reply_text, "action": "reply", "extracted_order": extracted_order}


# ── Private helpers ───────────────────────────────────────────────────────────

def _detect_escalation(message: str, custom_keywords: list) -> bool:
    """Return True if the message contains any escalation phrase."""
    all_phrases = ESCALATION_PHRASES_AR + (custom_keywords or [])
    for phrase in all_phrases:
        if phrase and phrase in message:
            return True
    return False


def _build_system_prompt(
    restaurant: dict,
    settings: dict,
    bot_cfg: dict,
    products: list,
    memory: dict,
    customer: dict,
) -> str:
    """Build the full system prompt for the AI bot."""
    bot_name = settings.get("bot_name") or "مساعد ذكي"
    rest_name = restaurant.get("name") or settings.get("restaurant_name") or "المطعم"
    rest_address = restaurant.get("address") or settings.get("restaurant_address") or ""
    rest_phone = restaurant.get("phone") or settings.get("restaurant_phone") or ""
    welcome = settings.get("bot_welcome") or "مرحباً! كيف يمكنني مساعدتك؟"

    # Build menu by category
    menu_by_cat: dict[str, list] = {}
    for p in products:
        cat = p.get("category", "عام")
        if cat not in menu_by_cat:
            menu_by_cat[cat] = []
        icon = p.get("icon", "🍽️")
        menu_by_cat[cat].append(f"  {icon} {p['name']} — {p['price']} ريال")
        if p.get("description"):
            menu_by_cat[cat][-1] += f" ({p['description']})"

    menu_text = ""
    for cat, items in menu_by_cat.items():
        menu_text += f"\n### {cat}\n" + "\n".join(items) + "\n"

    # Customer info
    cust_name = customer.get("name") or memory.get("name") or "العميل"
    is_vip = bool(customer.get("vip"))
    vip_note = "\n⭐ هذا العميل VIP — قدم له خدمة مميزة وعروضاً خاصة." if is_vip else ""

    # Memory
    memory_text = ""
    if memory:
        prefs = []
        if memory.get("preferences"):
            prefs.append(f"تفضيلات: {memory['preferences']}")
        if memory.get("favorite_item"):
            prefs.append(f"الوجبة المفضلة: {memory['favorite_item']}")
        if memory.get("address"):
            prefs.append(f"عنوان التوصيل المعتاد: {memory['address']}")
        if memory.get("allergies"):
            prefs.append(f"حساسية: {memory['allergies']}")
        if prefs:
            memory_text = "\n### معلومات العميل المحفوظة\n" + "\n".join(f"- {p}" for p in prefs)

    # Custom prompts from bot_config
    custom_system = bot_cfg.get("system_prompt") or ""
    sales_prompt = bot_cfg.get("sales_prompt") or ""

    prompt = f"""أنت {bot_name}، مساعد ذكاء اصطناعي احترافي لـ{rest_name}.
اسم العميل: {cust_name}{vip_note}

## معلومات المطعم
- الاسم: {rest_name}
- العنوان: {rest_address}
- الهاتف: {rest_phone}
- رسالة الترحيب: {welcome}

## قائمة الطعام
{menu_text}
{memory_text}

## تعليمات عامة
- أجب دائماً بلغة العميل (عربي أو إنجليزي بحسب رسالته).
- كن ودياً، مختصراً، ومفيداً.
- عند طلب الغداء أو العشاء اسأل عن العنوان لتسهيل التوصيل.
- اقترح وجبات مناسبة بناءً على تفضيلات العميل إن وجدت.
- لا تخترع معلومات غير موجودة في قائمة الطعام أعلاه.
- إذا لم تستطع الإجابة على سؤال، أخبر العميل بأنك ستحوله لموظف.
"""

    if custom_system:
        prompt += f"\n## تعليمات إضافية\n{custom_system}\n"

    if sales_prompt:
        prompt += f"\n## المبيعات والعروض\n{sales_prompt}\n"

    return prompt


def _save_memory(restaurant_id: str, customer_id: str, key: str, value: str) -> None:
    """Save or update a memory entry for a customer."""
    conn = database.get_db()
    try:
        conn.execute("""
            INSERT INTO conversation_memory (id, restaurant_id, customer_id, memory_key, memory_value, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(restaurant_id, customer_id, memory_key)
            DO UPDATE SET memory_value=excluded.memory_value, updated_at=CURRENT_TIMESTAMP
        """, (
            __import__("uuid").uuid4().__str__(),
            restaurant_id, customer_id, key, value
        ))
        conn.commit()
    finally:
        conn.close()


def _update_memory_from_conversation(restaurant_id: str, customer_id: str, message: str) -> None:
    """Extract name and preferences from message and save to memory."""
    import uuid as _uuid

    # Simple heuristic extraction
    updates = {}

    # Name extraction (Arabic patterns)
    name_patterns = [
        r"اسمي\s+([\u0600-\u06FF\s]+?)(?:\s|$|،|,)",
        r"أنا\s+([\u0600-\u06FF]+)(?:\s|$|،|,)",
        r"my name is\s+([A-Za-z\s]+?)(?:\s|$|,)",
        r"i'm\s+([A-Za-z\s]+?)(?:\s|$|,)",
        r"i am\s+([A-Za-z\s]+?)(?:\s|$|,)",
    ]
    for pat in name_patterns:
        m = re.search(pat, message, re.IGNORECASE)
        if m:
            updates["name"] = m.group(1).strip()
            break

    # Address extraction
    address_patterns = [
        r"العنوان\s*[:،]\s*([\u0600-\u06FF\s\d]+?)(?:\n|$|،)",
        r"أسكن في\s+([\u0600-\u06FF\s]+?)(?:\s|$|،)",
        r"address[:\s]+(.+?)(?:\n|$|,)",
    ]
    for pat in address_patterns:
        m = re.search(pat, message, re.IGNORECASE)
        if m:
            updates["address"] = m.group(1).strip()
            break

    # Preference: no onion
    if "بدون بصل" in message or "بلا بصل" in message:
        updates["preferences"] = "بدون بصل"
    elif "بدون حار" in message or "غير حار" in message:
        updates["preferences"] = "غير حار"
    elif "حار" in message and "جداً" in message:
        updates["preferences"] = "حار جداً"

    # Allergies
    if "حساسية" in message:
        updates["allergies"] = message[max(0, message.index("حساسية") - 5):message.index("حساسية") + 30].strip()

    conn = database.get_db()
    try:
        for key, value in updates.items():
            conn.execute("""
                INSERT INTO conversation_memory (id, restaurant_id, customer_id, memory_key, memory_value, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(restaurant_id, customer_id, memory_key)
                DO UPDATE SET memory_value=excluded.memory_value, updated_at=CURRENT_TIMESTAMP
            """, (str(_uuid.uuid4()), restaurant_id, customer_id, key, value))
        conn.commit()
    finally:
        conn.close()


def _extract_order_from_message(message: str, products: list) -> Optional[dict]:
    """Try to extract an order from the customer's message."""
    found_items = []
    msg_lower = message.lower()

    for p in products:
        name = p.get("name", "")
        # Check if product name appears in message
        if name and name in message:
            # Try to detect quantity
            qty = 1
            qty_patterns = [
                rf"(\d+)\s*(?:قطعة|وجبة|طلب)?\s*{re.escape(name)}",
                rf"{re.escape(name)}\s*(?:x|×)?\s*(\d+)",
            ]
            for pat in qty_patterns:
                m = re.search(pat, message)
                if m:
                    try:
                        qty = int(m.group(1))
                    except Exception:
                        pass
                    break
            found_items.append({
                "product_id": p["id"],
                "name": name,
                "price": p["price"],
                "quantity": qty,
            })

    if not found_items:
        return None

    return {
        "items": found_items,
        "total": sum(i["price"] * i["quantity"] for i in found_items),
    }
