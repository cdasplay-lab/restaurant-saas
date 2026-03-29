"""
AI Bot Service for Restaurant SaaS Platform
Handles conversation processing, order extraction, and escalation detection.
"""
import os
import json
import re
import logging
from typing import Optional

import database

logger = logging.getLogger("restaurant-saas")

# ── Constants ─────────────────────────────────────────────────────────────────

ESCALATION_PHRASES_AR = [
    "مدير", "موظف", "انسان", "إنسان", "شكوى", "استرداد",
    "مشكلة", "ألغ", "إلغ", "مسؤول",
]

ORDER_KEYWORDS = [
    "أريد", "أطلب", "عايز", "بدي", "ابي", "حابب", "اطلب", "ابغى",
    "اريد", "اطلب", "ابغ", "خذلي", "جيبلي", "وياه", "وياهم", "اضيف",
    "اخذ", "اشتري", "طلب", "طلبي",
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_openai_client = None


def _get_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if not OPENAI_API_KEY:
        logger.error("[bot] OPENAI_API_KEY is not set — bot cannot call OpenAI")
        return None
    try:
        import openai
        _openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        logger.info(f"[bot] OpenAI client initialized (key prefix: {OPENAI_API_KEY[:8]}...)")
        return _openai_client
    except Exception as e:
        logger.error(f"[bot] Failed to initialize OpenAI client: {e}", exc_info=True)
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
        logger.error(f"[bot] No OpenAI client for restaurant={restaurant_id} — OPENAI_API_KEY missing or failed to init")
        return {
            "reply": "مرحباً! يسعدني مساعدتك. كيف يمكنني خدمتك؟",
            "action": "reply",
            "extracted_order": None,
        }

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        role = "user" if h["role"] == "customer" else "assistant"
        messages.append({"role": role, "content": h["content"]})
    messages.append({"role": "user", "content": customer_message})

    try:
        logger.info(f"[bot] calling OpenAI model={model} restaurant={restaurant_id} conv={conversation_id}")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=800,
            temperature=0.75,
        )
        reply_text = response.choices[0].message.content.strip()
        logger.info(f"[bot] OpenAI reply OK — restaurant={restaurant_id} reply_len={len(reply_text)}")
    except Exception as e:
        logger.error(f"[bot] OpenAI call FAILED — restaurant={restaurant_id} model={model} error={e}", exc_info=True)
        reply_text = "عذراً، حدث خطأ تقني. يرجى المحاولة مجدداً أو التواصل مع فريقنا مباشرة."
        return {"reply": reply_text, "action": "reply", "extracted_order": None}

    # Extract order if enabled (keyword-based, from customer message)
    extracted_order = None
    order_enabled = (bot_cfg["order_extraction_enabled"] if bot_cfg else 1)
    if order_enabled and any(kw in customer_message for kw in ORDER_KEYWORDS):
        extracted_order = _extract_order_from_message(customer_message, [dict(p) for p in products])

    # Auto-detect confirmed order from bot's own reply (✅ summary block)
    confirmed_order = None
    if order_enabled:
        confirmed_order = _parse_confirmed_order(reply_text, memory, [dict(p) for p in products])

    # Update customer memory
    if customer and bot_cfg and bot_cfg.get("memory_enabled", 1):
        _update_memory_from_conversation(restaurant_id, conv["customer_id"], customer_message)

    return {
        "reply": reply_text,
        "action": "reply",
        "extracted_order": extracted_order,
        "confirmed_order": confirmed_order,
    }


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
        price_str = f"{int(p['price']):,}" if p.get("price") else "—"
        line = f"  {icon} {p['name']} — {price_str} د.ع"
        if p.get("description"):
            line += f" ({p['description']})"
        menu_by_cat[cat].append(line)

    menu_text = ""
    for cat, items in menu_by_cat.items():
        menu_text += f"\n### {cat}\n" + "\n".join(items) + "\n"

    # Customer info
    cust_name = customer.get("name") or memory.get("name") or ""
    is_vip = bool(customer.get("vip"))
    vip_note = "\n⭐ هذا العميل VIP — قدم له خدمة مميزة واهتمام خاص." if is_vip else ""

    # Memory
    memory_lines = []
    if memory:
        if memory.get("preferences"):
            memory_lines.append(f"تفضيلاته: {memory['preferences']}")
        if memory.get("favorite_item"):
            memory_lines.append(f"وجبته المفضلة: {memory['favorite_item']}")
        if memory.get("address"):
            memory_lines.append(f"عنوان التوصيل المعتاد: {memory['address']}")
        if memory.get("allergies"):
            memory_lines.append(f"حساسية: {memory['allergies']}")
    memory_text = (
        "\n### معلومات العميل المحفوظة\n" + "\n".join(f"- {l}" for l in memory_lines)
        if memory_lines else ""
    )

    # Custom prompts from bot_config
    custom_system = bot_cfg.get("system_prompt") or ""
    sales_prompt_extra = bot_cfg.get("sales_prompt") or ""

    cust_greeting = f"اسم العميل: {cust_name}" if cust_name else ""

    prompt = f"""أنت {bot_name}، موظف مبيعات محترف ومتحمس يعمل لدى {rest_name}.
{cust_greeting}{vip_note}

## معلومات المطعم
- الاسم: {rest_name}
- العنوان: {rest_address}
- الهاتف: {rest_phone}
- رسالة الترحيب: {welcome}

## قائمة الطعام (الأسعار بالدينار العراقي)
{menu_text}
{memory_text}

## أسلوب التعامل
- تحدث بنبرة ودية ومحببة كموظف مبيعات حقيقي، استخدم اللهجة العراقية الدارجة.
- رحّب بالعميل بحرارة عند بداية المحادثة واعرض قائمة الطعام منظمةً بالأقسام.
- اسأل العميل عن تفضيلاته (الحجم، الصوص، الإضافات) قبل إتمام الطلب.
- اقترح وجبات مكملة أو مشروبات بشكل طبيعي دون إلحاح ("بتحب تضيف...؟ يطلع معها زين").
- عند تأكيد الطلب، اعرض ملخصاً واضحاً بهذا الشكل:
  ✅ طلبك:
  • [اسم الوجبة] × [الكمية] — [السعر] د.ع
  ──────────────
  💰 المجموع: [الإجمالي] د.ع
- بعد عرض الملخص، اطلب عنوان التوصيل إذا لم يكن محفوظاً.
- اذكر طرق الدفع المتاحة (كاش / دفع إلكتروني) بعد تأكيد العنوان.
- لا تخترع منتجات أو أسعاراً خارج القائمة أعلاه.
- العملة دائماً: دينار عراقي (د.ع) — لا تذكر ريال أو أي عملة أخرى.
- إذا سأل العميل عن شيء خارج نطاق المطعم، أعده بلطف لموضوع الطلب.
- إذا طلب التحدث مع موظف أو أبدى شكوى، أخبره بأنك ستحوله لفريق الدعم.

## ردود الستوري (Story Replies)
إذا جاءت الرسالة تبدأ بـ [العميل يرد على ستوري...]:
- إذا كان الستوري عن منتج محدد → رحّب بحرارة، اذكر المنتج بالاسم والسعر مباشرة، وابدأ flow الطلب.
  مثال: "يسلمون 😍 هذا [اسم المنتج] مالنا! تحب تطلبه الحين؟"
- إذا قال العميل "واو" أو أرسل إيموجي فقط → اشكره بحرارة واربطها بالمنتج.
  مثال: "شكراً على كلامك الجميل 🙏 يسعدنا إعجابك بـ[المنتج]! تحب تجربه؟"
- إذا سأل عن السعر → أجبه مباشرة واقترح الطلب.
- إذا كان ستوري فيديو بدون تحديد منتج → رحّب واسأله بشكل طبيعي عما يرغب به.
- حوّل كل رد على ستوري إلى فرصة بيع طبيعية وغير متكلفة.
"""

    if custom_system:
        prompt += f"\n## تعليمات إضافية من المطعم\n{custom_system}\n"

    if sales_prompt_extra:
        prompt += f"\n## عروض وحملات خاصة\n{sales_prompt_extra}\n"

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


def _parse_confirmed_order(reply_text: str, memory: dict, products: list) -> Optional[dict]:
    """
    Parse a confirmed order from the bot's own reply when it contains the ✅ summary block.
    Returns {"items": [...], "total": float, "address": str} or None.
    """
    if "✅" not in reply_text or "المجموع" not in reply_text:
        return None

    # Parse line items:  • name × qty — price د.ع  OR  • name — price د.ع
    items = []
    item_pat = re.compile(
        r'•\s+(.+?)(?:\s+[×x]\s*(\d+))?\s+—\s+([\d,٠-٩٬\.]+)\s+د\.ع',
        re.MULTILINE
    )
    _ar_en = str.maketrans('٠١٢٣٤٥٦٧٨٩٬', '0123456789,')
    for m in item_pat.finditer(reply_text):
        name = m.group(1).strip()
        qty = int(m.group(2)) if m.group(2) else 1
        price_raw = m.group(3).translate(_ar_en).replace(',', '')
        try:
            price = float(price_raw)
        except ValueError:
            continue
        product_id = next((p["id"] for p in products if p.get("name", "").strip() == name), None)
        items.append({"name": name, "quantity": qty, "price": price, "product_id": product_id})

    if not items:
        return None

    # Parse total
    total = 0.0
    total_m = re.search(r'المجموع[:\s]+([\d,٠-٩٬\.]+)\s+د\.ع', reply_text)
    if total_m:
        try:
            total = float(total_m.group(1).translate(_ar_en).replace(',', ''))
        except ValueError:
            total = sum(i["price"] * i["quantity"] for i in items)
    else:
        total = sum(i["price"] * i["quantity"] for i in items)

    # Extract delivery address from reply or memory
    address = ""
    for pat in [
        r'(?:توصيل الطلب إلى|التوصيل إلى|سيصلك إلى|عنوان التوصيل)[:\s]+([^\n.!؟]+)',
    ]:
        am = re.search(pat, reply_text)
        if am:
            address = am.group(1).strip()
            break
    if not address:
        address = memory.get("address", "")

    logger.info(f"[bot] confirmed_order parsed: items={len(items)} total={total} address={address[:30]}")
    return {"items": items, "total": total, "address": address}


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
