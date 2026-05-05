"""
services/order_brain.py — NUMBER 27/29: Deterministic Order State Machine

Tracks per-conversation order state so the bot never loses context mid-flow.
The session state is injected into the LLM system prompt so GPT-4o-mini always
knows exactly what was collected and what the next step is.

NUMBER 29 fixes:
- phone added as required slot
- Arabic-Indic digit normalization for phone extraction
- Arabic number word AFTER product for qty extraction (فرايز اثنين)
- generate_next_directive() — injects exact next question as imperative
- generate_confirmation_message() — NUMBER 30 formatted confirmation
- to_dict() / from_dict() — DB persistence survives server restarts
"""
from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List

logger = logging.getLogger("restaurant-saas")

# Session expires after 2 hours of inactivity
_SESSION_TTL = 7200.0

# ── Keyword lists ──────────────────────────────────────────────────────────────

FRUSTRATION_PHRASES = [
    "غبي", "ما تفهم", "مو فاهم", "ليش ما تفهم",
    "تعبتني", "تعبت منك", "مو ذكي", "أغبى", "فاشل",
    "ما تعرف تشتغل", "ليش تكرر", "ليش رجعت تسأل",
    "قلتلك", "سبق قلت", "قلت لك", "سبق وقلت",
]

DELIVERY_KEYWORDS = [
    "توصيل", "يجيلي", "أوصله", "يجي لي", "توصل لي",
    "يوصل", "توصيل للبيت", "أريد توصيل",
]

PICKUP_KEYWORDS = [
    "استلام", "آخذه", "أجي", "أستلم", "آخذه بنفسي",
    "بالاستلام", "يجي ياخذه", "استلم من المطعم",
    "آخذه من المطعم", "أكدر آجي",
]

PAYMENT_MAP = {
    "كاش": "كاش",
    "نقد": "كاش",
    "كارد": "كارد",
    "بطاقة": "كارد",
    "كي كارد": "كارد",
    "visa": "كارد",
    "زين كاش": "زين كاش",
    "زين": "زين كاش",
    "فلوس": "كاش",
}

CONFIRMATION_KEYWORDS = [
    "ثبت", "أكمل", "تمام ثبته", "أكمله", "ثبته",
    "خلاص ثبت", "نعم ثبت", "اكمل", "ثبتها", "نثبتها",
    "تمام أكمل", "نعم أكمل", "تمام نكمل",
    # Iraqi Arabic affirmations when session is complete
    "اي ثبت", "صح ثبت", "اقفل الطلب", "أغلق الطلب", "اختم الطلب",
]

CANCELLATION_KEYWORDS = [
    "ألغ الطلب", "الغ الطلب", "ألغيه", "شيل الطلب",
    "احذف الطلب", "ما أريده", "شلت الطلب", "لا ما أريد الطلب",
]

# Arabic number words → int
_AR_NUMBERS = {
    "واحد": 1, "وحدة": 1, "وحده": 1, "اثنين": 2, "ثنتين": 2,
    "ثلاثة": 3, "ثلاث": 3, "أربعة": 4, "أربع": 4,
    "خمسة": 5, "خمس": 5, "ستة": 6, "ست": 6,
    "سبعة": 7, "سبع": 7, "ثمانية": 8, "ثماني": 8,
    "تسعة": 9, "تسع": 9, "عشرة": 10, "عشر": 10,
}

# Common Iraqi delivery areas for address extraction
_IRAQ_AREAS = [
    "الكرادة", "المنصور", "الكرخ", "الرصافة", "العلوية", "الجادرية",
    "الدورة", "الزعفرانية", "البياع", "الغزالية", "الحارثية",
    "الصدر", "الشعب", "التاجي", "أبو غريب", "الاعظمية",
    "الكرادة الشرقية", "الكرادة الغربية", "الكاظمية", "الأعظمية",
    "المحمودية", "اليرموك", "الحيدرخانة", "الوزيرية", "البتاوين",
    "الشرطة الخامسة", "السيدية", "الشعلة", "الحبيبية", "زيونة",
    "بغداد الجديدة", "النهضة", "الطالبية", "القاهرة",
    "أحمد أغا", "الأمين", "الشماعية", "الدواسة", "الجهاد",
    "الشعلة", "الحسينية", "الطارمية", "السيدية",
]

# Map next missing field → Iraqi Arabic question text
_FIELD_QUESTION = {
    "items":          "شنو تحب تطلب؟",
    "order_type":     "توصيل لو استلام؟",
    "address":        "وين العنوان؟",
    "customer_name":  "شسمك؟",
    "phone":          "شنو رقم هاتفك؟",
    "payment_method": "كاش لو كي كارد؟",
}

_FIELD_NEXT = {
    "items":          "اسأل عن المنتج المطلوب",
    "order_type":     "اسأل: توصيل لو استلام؟",
    "address":        "اسأل: وين العنوان؟",
    "customer_name":  "اسأل: شسمك؟",
    "phone":          "اسأل: شنو رقم هاتفك؟",
    "payment_method": "اسأل: كاش لو كي كارد؟",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize_digits(s: str) -> str:
    """Convert Arabic-Indic digits (٠١٢...) to ASCII digits (012...)."""
    return s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class OrderItem:
    name: str
    qty: int
    price: float
    product_id: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "qty": self.qty, "price": self.price,
                "product_id": self.product_id, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "OrderItem":
        return cls(name=d["name"], qty=d["qty"], price=d["price"],
                   product_id=d.get("product_id"), notes=d.get("notes", ""))


@dataclass
class OrderSession:
    conversation_id: str
    restaurant_id: str
    items: List[OrderItem] = field(default_factory=list)
    order_type: Optional[str] = None          # "delivery" | "pickup"
    address: Optional[str] = None
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    payment_method: Optional[str] = None
    confirmation_status: str = "collecting"   # collecting | awaiting_confirm | confirmed | cancelled
    last_question_asked: Optional[str] = None
    customer_frustrated: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def touch(self) -> None:
        self.updated_at = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.updated_at) > _SESSION_TTL

    def has_items(self) -> bool:
        return len(self.items) > 0

    def is_active(self) -> bool:
        return self.has_items() and self.confirmation_status in ("collecting", "awaiting_confirm")

    def reset_frustration(self) -> None:
        self.customer_frustrated = False

    # ── Slot logic ─────────────────────────────────────────────────────────────

    def missing_fields(self) -> List[str]:
        missing = []
        if not self.items:
            missing.append("items")
        if self.order_type is None:
            missing.append("order_type")
        if self.order_type == "delivery" and not self.address:
            missing.append("address")
        if not self.customer_name:
            missing.append("customer_name")
        if not self.phone:                       # NUMBER 29 — phone is required
            missing.append("phone")
        if not self.payment_method:
            missing.append("payment_method")
        return missing

    def next_missing_field(self) -> Optional[str]:
        m = self.missing_fields()
        return m[0] if m else None

    def is_complete(self) -> bool:
        return len(self.missing_fields()) == 0

    # ── Prompt section ─────────────────────────────────────────────────────────

    def items_summary(self) -> str:
        if not self.items:
            return "—"
        return "، ".join(f"{item.name} × {item.qty}" for item in self.items)

    def generate_next_directive(self, products: List[dict] = None) -> str:
        """
        Returns the EXACT next message the bot must send.
        Injected at end of system prompt as imperative instruction.
        """
        next_f = self.next_missing_field()

        if next_f == "items":
            names = []
            for p in (products or []):
                if p.get("name") and p.get("available", 1):
                    names.append(p["name"])
            menu_str = "، ".join(names[:8])
            if menu_str:
                return f"شنو تحب تطلب؟ عندنا: {menu_str}"
            return _FIELD_QUESTION["items"]

        if next_f in _FIELD_QUESTION:
            return _FIELD_QUESTION[next_f]

        # All complete — request confirmation
        summary = self.items_summary()
        order_t = "توصيل" if self.order_type == "delivery" else "استلام"
        addr_part = f" — {self.address}" if self.address else ""
        return (
            f"طلبك: {summary}، {order_t}{addr_part}، "
            f"{self.customer_name}، {self.phone}، {self.payment_method}. تثبت؟"
        )

    def generate_confirmation_message(self, order_number: str = "") -> str:
        """NUMBER 30 — Generate the final formatted order confirmation."""
        lines = ["✅ طلبك وصلنا!"]
        lines.append("━━━━━━━━━━━━━")
        for item in self.items:
            lines.append(f"• {item.name} × {item.qty}")
        lines.append("━━━━━━━━━━━━━")
        if self.order_type == "delivery":
            lines.append(f"🚗 توصيل — {self.address or '—'}")
        else:
            lines.append("🏪 استلام من المطعم")
        lines.append(f"👤 {self.customer_name or '—'}")
        if self.phone:
            lines.append(f"📞 {self.phone}")
        lines.append(f"💵 {self.payment_method or 'كاش'}")
        if order_number:
            lines.append("━━━━━━━━━━━━━")
            lines.append(f"رقم طلبك: #{order_number}")
        lines.append("")
        lines.append("يوصلك قريب إن شاء الله 🌷")
        return "\n".join(lines)

    def to_prompt_section(self) -> str:
        """
        Returns the ORDER STATE block injected into the system prompt.
        Always returned if session has any data — never returns "" if items exist.
        """
        has_any = (
            self.has_items() or self.order_type is not None
            or self.customer_name or self.phone
        )
        if not has_any:
            return ""

        lines = [
            "## 🔴 حالة الطلب الجارية — اقرأ أولاً قبل أي رد",
            "",
            "⚠️ لا تبدأ من الصفر — لا تقل 'هلا بيك' أو أي ترحيب — واصل من حيث توقفت",
            "",
        ]

        lines.append(f"{'✅' if self.items else '⬜'} المنتجات: {self.items_summary()}")
        lines.append(f"{'✅' if self.order_type else '⬜'} نوع الطلب: {self.order_type or 'لم يُحدد'}")

        if self.order_type == "delivery":
            lines.append(f"{'✅' if self.address else '⬜'} العنوان: {self.address or 'لم يُذكر'}")
        elif self.order_type == "pickup":
            lines.append("✅ العنوان: لا يُحتاج (استلام)")

        lines.append(f"{'✅' if self.customer_name else '⬜'} الاسم: {self.customer_name or 'لم يُذكر'}")
        lines.append(f"{'✅' if self.phone else '⬜'} الهاتف: {self.phone or 'لم يُذكر'}")
        lines.append(f"{'✅' if self.payment_method else '⬜'} الدفع: {self.payment_method or 'لم يُذكر'}")

        missing = self.missing_fields()
        if missing:
            next_f = missing[0]
            lines.append("")
            lines.append(f"⏭️ الخطوة التالية الإلزامية: {_FIELD_NEXT.get(next_f, next_f)}")
            lines.append("لا تسأل عن أي خطوة سبق إجابتها — اسأل عن الخطوة التالية فقط")
        else:
            lines.append("")
            lines.append("⏭️ كل المعلومات مكتملة — اطلب التأكيد بملخص قصير")

        if self.customer_frustrated:
            lines.append("")
            lines.append("⚠️ العميل أبدى إحباطاً — اعتذر بجملة واحدة قصيرة ثم واصل من الخطوة التالية مباشرة")

        lines.append("")
        return "\n".join(lines)

    # ── Serialization (DB persistence) ────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "restaurant_id": self.restaurant_id,
            "items": [i.to_dict() for i in self.items],
            "order_type": self.order_type,
            "address": self.address,
            "customer_name": self.customer_name,
            "phone": self.phone,
            "payment_method": self.payment_method,
            "confirmation_status": self.confirmation_status,
            "last_question_asked": self.last_question_asked,
            "customer_frustrated": self.customer_frustrated,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OrderSession":
        sess = cls(
            conversation_id=d["conversation_id"],
            restaurant_id=d["restaurant_id"],
        )
        sess.items = [OrderItem.from_dict(i) for i in d.get("items", [])]
        sess.order_type = d.get("order_type")
        sess.address = d.get("address")
        sess.customer_name = d.get("customer_name")
        sess.phone = d.get("phone")
        sess.payment_method = d.get("payment_method")
        sess.confirmation_status = d.get("confirmation_status", "collecting")
        sess.last_question_asked = d.get("last_question_asked")
        sess.customer_frustrated = d.get("customer_frustrated", False)
        sess.created_at = d.get("created_at", time.time())
        sess.updated_at = d.get("updated_at", time.time())
        return sess


# ── OrderBrain singleton ───────────────────────────────────────────────────────

class OrderBrain:
    """
    Module-level singleton keyed by conversation_id.
    Provides deterministic order state that survives across LLM calls.
    """
    _sessions: Dict[str, OrderSession] = {}

    # ── Session management ─────────────────────────────────────────────────────

    @classmethod
    def get_session(cls, conversation_id: str) -> Optional[OrderSession]:
        sess = cls._sessions.get(conversation_id)
        if sess is None:
            return None
        if sess.is_expired():
            del cls._sessions[conversation_id]
            logger.debug(f"[order_brain] session expired: conv={conversation_id}")
            return None
        return sess

    @classmethod
    def get_or_create(cls, conversation_id: str, restaurant_id: str) -> OrderSession:
        sess = cls.get_session(conversation_id)
        if sess is None:
            sess = OrderSession(conversation_id=conversation_id, restaurant_id=restaurant_id)
            cls._sessions[conversation_id] = sess
            logger.debug(f"[order_brain] new session: conv={conversation_id} rest={restaurant_id}")
        return sess

    @classmethod
    def restore_from_dict(cls, conversation_id: str, data: dict) -> Optional[OrderSession]:
        """Restore a session from a serialized dict (loaded from DB)."""
        try:
            sess = OrderSession.from_dict(data)
            if sess.is_expired():
                return None
            cls._sessions[conversation_id] = sess
            logger.debug(f"[order_brain] session restored from DB: conv={conversation_id}")
            return sess
        except Exception as e:
            logger.warning(f"[order_brain] restore failed: {e}")
            return None

    @classmethod
    def clear_session(cls, conversation_id: str) -> None:
        cls._sessions.pop(conversation_id, None)

    @classmethod
    def cleanup_expired(cls) -> int:
        expired = [k for k, v in cls._sessions.items() if v.is_expired()]
        for k in expired:
            del cls._sessions[k]
        return len(expired)

    # ── State extraction ───────────────────────────────────────────────────────

    @classmethod
    def update_from_message(
        cls,
        session: OrderSession,
        message: str,
        products: List[dict],
        is_bot_reply: bool = False,
    ) -> List[str]:
        """
        Parse message and update session slots.
        Returns list of field names that changed.
        """
        updated: List[str] = []

        if is_bot_reply:
            # Detect confirmation receipt sent by bot
            if any(p in message for p in ["✅ طلبك", "✅ طلبك:", "المجموع:", "طلبك وصلنا"]):
                if session.confirmation_status == "collecting":
                    session.confirmation_status = "awaiting_confirm"
                    updated.append("confirmation_status=awaiting_confirm")
            # Detect cancellation confirmed by bot
            if any(kw in message for kw in ["شلنا الطلب", "الغيت الطلب", "الطلب ملغي", "ألغيت الطلب"]):
                session.confirmation_status = "cancelled"
                updated.append("confirmation_status=cancelled")
            session.touch()
            return updated

        msg = message.strip()

        # 1. Items — match product names
        _extract_items(session, msg, products, updated)

        # 2. Order type
        if session.order_type is None:
            if any(kw in msg for kw in DELIVERY_KEYWORDS):
                session.order_type = "delivery"
                updated.append("order_type=delivery")
            elif any(kw in msg for kw in PICKUP_KEYWORDS):
                session.order_type = "pickup"
                updated.append("order_type=pickup")

        # 3. Address (only for delivery or undecided)
        if session.order_type != "pickup" and not session.address:
            addr = _extract_address(msg)
            if addr:
                session.address = addr
                updated.append(f"address={addr[:25]}")

        # 4. Customer name
        if not session.customer_name:
            name = _extract_name(msg)
            if name:
                session.customer_name = name
                updated.append(f"customer_name={name}")

        # 5. Phone — normalize Arabic-Indic digits before matching
        if not session.phone:
            phone = _extract_phone(msg)
            if phone:
                session.phone = phone
                updated.append(f"phone={phone[:14]}")

        # 6. Payment method — use word-boundary regex to avoid "زين" matching "زينجر"
        if not session.payment_method:
            for kw, method in PAYMENT_MAP.items():
                if re.search(r'(?<![؀-ۿ\w])' + re.escape(kw) + r'(?![؀-ۿ\w])', msg):
                    session.payment_method = method
                    updated.append(f"payment_method={method}")
                    break

        # 7. Confirmation
        if any(kw in msg for kw in CONFIRMATION_KEYWORDS):
            if session.is_complete():
                session.confirmation_status = "confirmed"
                updated.append("confirmation_status=confirmed")

        # 8. Cancellation
        if any(kw in msg for kw in CANCELLATION_KEYWORDS):
            session.confirmation_status = "cancelled"
            updated.append("confirmation_status=cancelled")

        # 9. Frustration flag
        if detect_frustration(msg) and not session.customer_frustrated:
            session.customer_frustrated = True
            updated.append("customer_frustrated=True")

        if updated:
            logger.info(f"[order_brain] conv={session.conversation_id} updated={updated}")

        session.touch()
        return updated


# ── Slot extraction helpers ────────────────────────────────────────────────────

def _extract_items(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
) -> None:
    """Match product names in message and update session items."""
    for p in products:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if name in msg:
            qty = _extract_qty(msg, name)
            existing = next((it for it in session.items if it.name == name), None)
            if existing:
                if existing.qty != qty:
                    existing.qty = qty
                    updated.append(f"qty_update:{name}×{qty}")
            else:
                session.items.append(OrderItem(
                    name=name,
                    qty=qty,
                    price=float(p.get("price") or 0),
                    product_id=str(p.get("id") or ""),
                ))
                updated.append(f"item_added:{name}×{qty}")


def _extract_qty(msg: str, product_name: str) -> int:
    """Extract quantity for a specific product mention."""
    # digit before product: "2 برجر" / "اثنين برجر"
    m = re.search(
        r'(\d+)\s*(?:حبة|حبات|وجبة|وجبات|قطعة|قطع)?\s*' + re.escape(product_name),
        msg,
    )
    if m:
        return int(m.group(1))

    # digit after product: "برجر 2" / "برجر × 2"
    m = re.search(re.escape(product_name) + r'\s*(?:x|×|عدد)?\s*(\d+)', msg)
    if m:
        return int(m.group(1))

    # Arabic word before product: "اثنين برجر"
    for ar_word, ar_val in _AR_NUMBERS.items():
        if re.search(ar_word + r'\s+' + re.escape(product_name), msg):
            return ar_val

    # Arabic word AFTER product: "برجر اثنين" / "فرايز اثنين"  ← NUMBER 29 fix
    for ar_word, ar_val in _AR_NUMBERS.items():
        if re.search(re.escape(product_name) + r'\s+' + ar_word + r'(?!\w)', msg):
            return ar_val

    return 1


def _extract_name(msg: str) -> Optional[str]:
    """Extract customer name from message."""
    patterns = [
        r'اسمي\s+([؀-ۿ]+(?:\s+[؀-ۿ]+)?)',
        r'(?:أنا|انا)\s+([؀-ۿ]{2,10})(?:\s|$|،)',
        r'(?:شسمك[؟?]|شنو اسمك[؟?])\s*([؀-ۿ]{2,10})',
        r'(?:باسم|أطلب باسم)\s+([؀-ۿ]{2,10})',
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) >= 2 and candidate not in ("في", "من", "إلى", "الى", "هو", "هي"):
                return candidate
    return None


def _extract_address(msg: str) -> Optional[str]:
    """Extract delivery address/area from message."""
    _PUNCT = re.compile(r'^[\s؟?،.:!]+|[\s؟?،.:!]+$')

    # Labeled address patterns — exclude ؟ from captured chars
    patterns = [
        r'(?:العنوان|عنواني|عنوان التوصيل)[:\s،]*([؁-ۿ\s\d،]+?)(?:\.|\n|$)',
        r'(?:أسكن في|أسكن|منطقتي|حيي|منطقة|في حي)\s+([؁-ۿ\s]{3,30})(?:\s|$|،)',
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            candidate = _PUNCT.sub("", m.group(1))
            if len(candidate) >= 3:
                return candidate

    # Direct Iraqi area name mention
    for area in _IRAQ_AREAS:
        if area in msg:
            return area

    return None


def _extract_phone(msg: str) -> Optional[str]:
    """
    Extract phone number from message.
    Normalizes Arabic-Indic digits (٠١٢...) to ASCII before matching.
    Supports 10-13 digit numbers (Iraqi local + international formats).
    """
    norm = _normalize_digits(msg)

    # Iraqi phone: 07xxxxxxxxx (11 digits)
    m = re.search(r'07[0-9]{9}', norm)
    if m:
        return m.group(0)
    # Phone starting with 7: 7xxxxxxxxx (10 digits)
    m = re.search(r'\b7[0-9]{9}\b', norm)
    if m:
        return m.group(0)
    # Generic 10-13 digit number (handles international formats like +964...)
    m = re.search(r'\b\d{10,13}\b', norm)
    if m:
        return m.group(0)
    return None


# ── Public utility ─────────────────────────────────────────────────────────────

def detect_frustration(message: str) -> bool:
    """Return True if message contains frustration signals."""
    return any(phrase in message for phrase in FRUSTRATION_PHRASES)
