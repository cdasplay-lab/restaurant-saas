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

# NUMBER 36 — Repeat last order phrases
REPEAT_ORDER_PHRASES = [
    "نفس الطلب السابق", "نفس الطلبة السابقة", "نفس طلبتي",
    "رجعلي نفس الطلب", "رجعلي طلبتي", "اعيد نفس الطلب",
    "أعيد نفس الطلب", "نفس الطلب", "كرر طلبي", "كرر نفس الطلب",
    "نفس الطلبة", "اكرر الطلب", "أكرر الطلب",
]

# NUMBER 35 — Order Edit Engine
# Prefixes that signal item removal ("شيل الكولا", "احذف البطاطا", "ما أريد الكولا")
REMOVE_PREFIXES = [
    "شيل", "اشيل", "شيله", "شيلها", "شيلهم",
    "احذف", "حذف", "حذفه", "حذفها",
    "ما أريد", "ما اريد", "ما ابي",
    "امسح", "مسح",
    "لا أريد", "لا اريد",
]

# Patterns that signal full order reset
CLEAR_ORDER_PHRASES = [
    "ابدأ من جديد", "ابدأ من الأول", "من أول وجديد",
    "امسح الطلب كله", "امسح الطلب",
    "ابدأ طلب جديد", "أبدأ من الصفر", "أبدأ من جديد",
    "الغ كل شي وابدأ",
]

# Regex for item swap: "بدل الكولا بسفن أب" / "غير البرجر لزينجر"
_SWAP_RE = re.compile(
    r'(?:بدل|غيّر|غير|بدّل)\s+(.{2,20}?)\s+(?:بـ?|لـ?|إلى\s*|الى\s*|على\s*)\s*(.{2,20})',
    re.UNICODE,
)

# NUMBER 32 — order intent keywords (customer wants to order but may not name a product)
ORDER_INTENT_KEYWORDS = [
    "أريد", "اريد", "ابي", "أبي", "أبغى", "ابغى", "عايز", "بدي",
    "أطلب", "اطلب", "خذلي", "جيبلي", "أخذ", "أشتري", "أجيب",
    "حابب آخذ", "حابب أطلب", "أوصيلي", "وصّلي",
]

# NUMBER 32 — common product name aliases / dialectal variations
# Map fuzzy name → canonical name substring to look for in products list
_PRODUCT_ALIASES: Dict[str, str] = {
    "بركر":     "برجر",
    "بيرجر":    "برجر",
    "بوركر":    "برجر",
    "بورغر":    "برجر",
    "زنجر":     "زينجر",
    "زينگر":    "زينجر",
    "بروستد":   "بروستد",   # exact but common misspelling target
    "مبروستد":  "بروستد",
    "برستد":    "بروستد",
    "كوكاكولا": "كولا",
    "كوكا":     "كولا",
    "ببسي":     "بيبسي",
    "شاورمة":   "شاورما",
    "شورما":    "شاورما",
    "فريز":     "فرايز",
    "فرايس":    "فرايز",
    "بطاطس":    "بطاطا",
    "بطاطيس":   "بطاطا",
    "دجاجة":    "دجاج",
    "فراخ":     "دجاج",
    "وجبة دجاج": "دجاج",
}

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
    order_intent_detected: bool = False      # NUMBER 32 — customer wants to order but no product matched
    upsell_offered: bool = False             # NUMBER 33 — upsell was offered this session (offer once only)
    repeat_order_detected: bool = False      # NUMBER 36 — customer asked to repeat last order (DB lookup pending)
    repeat_order_failed: bool = False        # NUMBER 36 — repeat requested but no previous order found in DB
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

    # ── NUMBER 35 — Order Edit ─────────────────────────────────────────────────

    def remove_item(self, product_name: str) -> bool:
        """Remove item by name. Returns True if removed."""
        before = len(self.items)
        self.items = [it for it in self.items if it.name != product_name]
        return len(self.items) < before

    def clear_order(self) -> None:
        """Reset all order fields except customer identity (name + phone)."""
        self.items.clear()
        self.order_type = None
        self.address = None
        self.payment_method = None
        self.confirmation_status = "collecting"
        self.upsell_offered = False
        self.order_intent_detected = False

    def prefill_from_items(self, prev_items: List[dict]) -> None:
        """
        NUMBER 36 — Pre-fill session items from a previous order.
        prev_items: list of dicts with keys: name, qty/quantity, price.
        Sets upsell_offered=True (customer wants exact repeat — skip upsell).
        """
        self.items.clear()
        for d in prev_items:
            name  = (d.get("name") or "").strip()
            qty   = int(d.get("qty") or d.get("quantity") or 1)
            price = float(d.get("price") or 0)
            if name:
                self.items.append(OrderItem(name=name, qty=qty, price=price))
        self.upsell_offered = True    # exact repeat — skip upsell
        self.repeat_order_detected = False

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

    def _get_upsell_suggestion(self, products: List[dict]) -> str:
        """
        NUMBER 33 — Return a one-line upsell suggestion based on current items.
        Returns '' if no suitable upsell exists or session already has both main + drink + side.
        """
        if not self.items:
            return ""

        _MAIN_KW  = ["برجر", "زينجر", "بروستد", "شاورما", "دجاج", "لحم", "ستيك", "سندويش"]
        _DRINK_KW = ["كولا", "بيبسي", "جوس", "عصير", "ماء", "مشروب", "شاي", "قهوة", "ليمون"]
        _SIDE_KW  = ["بطاطا", "فرايز", "بطاطس", "سلطة", "خبز"]

        has_main  = any(any(kw in it.name for kw in _MAIN_KW) for it in self.items)
        has_drink = any(any(kw in it.name for kw in _DRINK_KW) for it in self.items)
        has_side  = any(any(kw in it.name for kw in _SIDE_KW) for it in self.items)

        if has_main and not has_drink:
            for p in (products or []):
                pname = (p.get("name") or "")
                if any(kw in pname for kw in _DRINK_KW):
                    return f"تريد نضيف {pname} وياه؟ 🥤"
            return "نضيف مشروب وياه؟ 🥤"

        if has_main and has_drink and not has_side:
            for p in (products or []):
                pname = (p.get("name") or "")
                if any(kw in pname for kw in _SIDE_KW):
                    return f"نضيف {pname} وياها؟ 🍟"

        return ""

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

        # NUMBER 33 — Upsell Engine: intercept order_type step with one upsell offer
        if next_f == "order_type" and not self.upsell_offered:
            suggestion = self._get_upsell_suggestion(products or [])
            if suggestion:
                self.upsell_offered = True
                return suggestion

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
        """NUMBER 30/32 — Generate the final formatted order confirmation with total."""
        lines = ["✅ طلبك وصلنا!"]
        lines.append("━━━━━━━━━━━━━")
        total = 0
        for item in self.items:
            item_total = int(item.price) * item.qty
            total += item_total
            price_str = f" — {item_total:,} د.ع" if item.price else ""
            lines.append(f"• {item.name} × {item.qty}{price_str}")
        lines.append("━━━━━━━━━━━━━")
        if total > 0:
            lines.append(f"💰 المجموع: {total:,} د.ع")
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
            or self.order_intent_detected
            or self.repeat_order_failed
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

        if self.order_intent_detected and not self.has_items():
            lines.append("")
            lines.append("⚠️ العميل أعرب عن نية الطلب لكن ما ذكر منتجاً من القائمة — اذكر 3-4 أصناف متاحة واسأله أيهم يريد")

        if self.repeat_order_failed:
            lines.append("")
            lines.append("⚠️ العميل طلب تكرار الطلب السابق لكن ما في طلب سابق — اخبره بلطف واسأل شنو يحب يطلب اليوم")

        if self.customer_frustrated:
            lines.append("")
            lines.append("⚠️ العميل أبدى إحباطاً — اعتذر بجملة واحدة قصيرة ثم واصل من الخطوة التالية مباشرة")

        if self.has_items() and self.confirmation_status == "collecting":
            lines.append("")
            lines.append("ℹ️ العميل يقدر يقول 'شيل [منتج]' أو 'بدل [أ] بـ[ب]' أو 'ابدأ من جديد' لتعديل الطلب")

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
            "order_intent_detected": self.order_intent_detected,
            "upsell_offered": self.upsell_offered,
            "repeat_order_detected": self.repeat_order_detected,
            "repeat_order_failed": self.repeat_order_failed,
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
        sess.order_intent_detected = d.get("order_intent_detected", False)
        sess.upsell_offered = d.get("upsell_offered", False)
        sess.repeat_order_detected = d.get("repeat_order_detected", False)
        sess.repeat_order_failed = d.get("repeat_order_failed", False)
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
            # NUMBER 33 — detect upsell in bot reply → mark as offered
            _UPSELL_SIGNALS = ["تريد نضيف", "نضيف", "تريد تضيف", "تحب تضيف", "تبي تضيف"]
            if not session.upsell_offered and any(sig in message for sig in _UPSELL_SIGNALS):
                session.upsell_offered = True
                updated.append("upsell_offered=True")
            session.touch()
            return updated

        msg = message.strip()

        # NUMBER 36 — Repeat last order detection (DB lookup handled in bot.py)
        if any(phrase in msg for phrase in REPEAT_ORDER_PHRASES) and not session.has_items():
            session.repeat_order_detected = True
            updated.append("repeat_order_detected=True")
            session.touch()
            return updated  # bot.py will handle DB lookup before proceeding

        # NUMBER 35 — Order Edit: clear / swap / remove BEFORE adding new items
        if any(phrase in msg for phrase in CLEAR_ORDER_PHRASES):
            session.clear_order()
            updated.append("order_cleared")
            session.touch()
            return updated

        _names_before_edit = {it.name for it in session.items}
        _apply_swap(session, msg, products, updated)
        _apply_remove(session, msg, products, updated)
        # Items removed/swapped away must not be re-added by _extract_items()
        _edit_removed = _names_before_edit - {it.name for it in session.items}

        # 1. Items — match product names (exact + fuzzy)
        _items_before = len(session.items)
        _extract_items(session, msg, products, updated, skip_names=_edit_removed)

        # NUMBER 32 — detect order intent without a matched product
        _has_intent = any(kw in msg for kw in ORDER_INTENT_KEYWORDS)
        _items_added = len(session.items) > _items_before
        if _has_intent and not _items_added and not session.has_items():
            session.order_intent_detected = True

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

def _fuzzy_product_match(msg: str, products: List[dict]) -> Optional[dict]:
    """
    NUMBER 32 — Find a product approximately mentioned via dialect alias or ال-stripping.
    Returns first matching product dict, or None.
    """
    # Check alias table first (e.g. "بركر" → "برجر")
    for alias, canonical in _PRODUCT_ALIASES.items():
        if alias in msg:
            for p in products:
                name = (p.get("name") or "").strip()
                if canonical in name:
                    return p

    # Strip ال prefix from each word in msg and try again
    words = msg.split()
    stripped_words = {re.sub(r'^ال', '', w) for w in words if len(w) >= 4}
    for p in products:
        name = (p.get("name") or "").strip()
        name_noal = re.sub(r'^ال', '', name)
        if len(name_noal) >= 3 and name_noal in stripped_words:
            return p

    return None


def _extract_items(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
    skip_names: set = None,
) -> None:
    """Match product names in message and update session items (exact + fuzzy)."""
    skip_names = skip_names or set()
    matched_ids: set = set()

    for p in products:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if name in skip_names:
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
            matched_ids.add(str(p.get("id") or name))

    # NUMBER 32 — fuzzy fallback: try alias/ال-strip if no exact match found yet
    if not matched_ids:
        fuzzy_p = _fuzzy_product_match(msg, products)
        if fuzzy_p:
            fname = (fuzzy_p.get("name") or "").strip()
            if fname in skip_names:
                return
            existing = next((it for it in session.items if it.name == fname), None)
            if not existing:
                qty = _extract_qty(msg, fname)
                session.items.append(OrderItem(
                    name=fname,
                    qty=qty,
                    price=float(fuzzy_p.get("price") or 0),
                    product_id=str(fuzzy_p.get("id") or ""),
                ))
                updated.append(f"item_added_fuzzy:{fname}×{qty}")
                logger.info(f"[order_brain32] fuzzy match: msg_excerpt={msg[:30]!r} → product={fname!r}")


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


# ── NUMBER 35 — Order Edit helpers ────────────────────────────────────────────

def _apply_remove(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
) -> None:
    """
    NUMBER 35 — Remove items explicitly named with a removal prefix.
    Handles: "شيل الكولا", "احذف البطاطا", "ما أريد الكولا", "بدون الكولا"
    Only removes items that are already in the session.
    """
    for item in list(session.items):
        name = item.name

        # "بدون [name]" pattern
        if f"بدون {name}" in msg or f"بدون ال{name}" in msg:
            if session.remove_item(name):
                updated.append(f"item_removed:{name}")
            continue

        # REMOVE_PREFIXES + product name both present in message
        for prefix in REMOVE_PREFIXES:
            if prefix in msg and name in msg:
                if session.remove_item(name):
                    updated.append(f"item_removed:{name}")
                break


def _apply_swap(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
) -> None:
    """
    NUMBER 35 — Swap an item for another.
    Handles: "بدل الكولا بسفن أب", "غير البرجر لزينجر"
    Removes old item; adds new item if found in products (exact or fuzzy).
    """
    m = _SWAP_RE.search(msg)
    if not m:
        return

    from_text = m.group(1).strip()
    to_text   = m.group(2).strip()

    # Find existing item matching from_text
    removed_item = None
    for item in list(session.items):
        if item.name in from_text:
            removed_item = item
            break
        # Try alias reverse-lookup
        for alias, canonical in _PRODUCT_ALIASES.items():
            if alias in from_text and canonical in item.name:
                removed_item = item
                break
        if removed_item:
            break

    if not removed_item:
        return

    # Find new product matching to_text (exact then fuzzy)
    new_product = None
    for p in products:
        pname = (p.get("name") or "")
        if pname and pname in to_text:
            new_product = p
            break
    if not new_product:
        new_product = _fuzzy_product_match(to_text, products)

    qty = removed_item.qty
    session.remove_item(removed_item.name)
    updated.append(f"item_removed:{removed_item.name}")

    if new_product:
        fname = (new_product.get("name") or "").strip()
        if fname and not any(it.name == fname for it in session.items):
            session.items.append(OrderItem(
                name=fname,
                qty=qty,
                price=float(new_product.get("price") or 0),
                product_id=str(new_product.get("id") or ""),
            ))
            updated.append(f"item_swapped:{removed_item.name}→{fname}")
            logger.info(
                f"[order_brain35] swap: {removed_item.name!r} → {fname!r} "
                f"conv={session.conversation_id}"
            )


# ── Public utility ─────────────────────────────────────────────────────────────

def detect_frustration(message: str) -> bool:
    """Return True if message contains frustration signals."""
    return any(phrase in message for phrase in FRUSTRATION_PHRASES)
