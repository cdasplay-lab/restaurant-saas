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

# NUMBER 44A — extended to 12h to survive human-handoff pauses and server restarts
_SESSION_TTL = 43200.0

# NUMBER 43 — Quantity sanity check: cap unreasonable quantities
MAX_QTY = 20

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

# All canonical payment method names (values of PAYMENT_MAP).
_CANONICAL_METHODS = set(PAYMENT_MAP.values())


def parse_allowed_payment_methods(raw: str) -> list:
    """Split a settings string like 'كاش، كارد' into a list of canonical names."""
    if not raw:
        return []
    import re as _re
    parts = _re.split(r"[،,/\|]+", raw)
    result = []
    for p in parts:
        p = p.strip()
        if p in _CANONICAL_METHODS:
            result.append(p)
    return result

CONFIRMATION_KEYWORDS = [
    # NUMBER 42 RISK-01 — explicit confirm phrases only; bare affirmations removed
    # to prevent accidental order fire during mid-collection conversation
    "ثبت", "أكمل", "تمام ثبته", "أكمله", "ثبته",
    "خلاص ثبت", "نعم ثبت", "اكمل", "ثبتها", "نثبتها",
    "تمام أكمل", "نعم أكمل", "تمام نكمل",
    "اي ثبت", "صح ثبت", "اقفل الطلب", "أغلق الطلب", "اختم الطلب",
]

CANCELLATION_KEYWORDS = [
    "ألغ الطلب", "الغ الطلب", "ألغيه", "شيل الطلب",
    "احذف الطلب", "ما أريده", "شلت الطلب", "لا ما أريد الطلب",
]

# NUMBER 37 — Item notes / special instructions
# Words that signal a customization note on the preceding product
_NOTE_MODIFIERS = [
    "بدون", "مع إضافة", "مع ", "حار", "بارد", "ساخن", "إضافي", "extra",
    "medium", "well done", "rare", "زيادة", "أقل", "ناعم",
    "مقرمش", "طازج", "مطبوخ", "بدون صوص", "بدون بصل",
    "بدون خس", "بدون طماطم", "بدون توابل", "بدون ثوم", "بدون مايونيز",
    "مضاعف", "نصف", "إضافة", "سادة", "خفيف الحر", "حار زيادة",
    "مع صوص", "مع جبن", "مع بيض", "مع خضار",
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
    "برگر":     "برجر",   # NUMBER 41A — Arabic Kaf variant (ك vs ك)
    "برگز":     "برجر",   # alternative spelling
    "زنجر":     "زينجر",
    "زينگر":    "زينجر",
    "بروستد":   "بروستد",   # exact but common misspelling target
    "مبروستد":  "بروستد",
    "برستد":    "بروستد",
    "كوكاكولا": "كولا",
    "كوكا":     "كولا",
    "كولا":     "بيبسي",   # NUMBER 41A — common: customer says كولا, menu says بيبسي
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
    # Baghdad districts
    "الكرادة", "المنصور", "الكرخ", "الرصافة", "العلوية", "الجادرية",
    "الدورة", "الزعفرانية", "البياع", "الغزالية", "الحارثية",
    "الصدر", "الشعب", "التاجي", "أبو غريب", "الاعظمية",
    "الكرادة الشرقية", "الكرادة الغربية", "الكاظمية", "الأعظمية",
    "المحمودية", "اليرموك", "الحيدرخانة", "الوزيرية", "البتاوين",
    "الشرطة الخامسة", "السيدية", "الشعلة", "الحبيبية", "زيونة",
    "بغداد الجديدة", "النهضة", "الطالبية", "القاهرة",
    "أحمد أغا", "الأمين", "الشماعية", "الدواسة", "الجهاد",
    "الشعلة", "الحسينية", "الطارمية", "السيدية",
    # NUMBER 42 POLISH-04 — major Iraqi cities
    "البصرة", "بصرة", "الموصل", "موصل", "النجف", "نجف",
    "كربلاء", "كربلاء المقدسة", "أربيل", "اربيل", "هولير",
    "السليمانية", "سليمانية", "دهوك", "كركوك",
    "الناصرية", "ناصرية", "الحلة", "حلة", "بابل",
    "الكوت", "كوت", "العمارة", "عمارة", "الديوانية", "ديوانية",
    "الرمادي", "رمادي", "الفلوجة", "فلوجة", "سامراء",
    "تكريت", "بعقوبة", "الخالص",
]

# Map next missing field → Iraqi Arabic question text
_FIELD_QUESTION = {
    "items":          "شنو تحب تطلب؟",
    "order_type":     "توصيل لو استلام؟",
    "address":        "وين عنوان التوصيل؟",
    "customer_name":  "شنو اسمك؟",
    "phone":          "شنو رقم هاتفك؟",
    "payment_method": "كاش لو كي كارد؟",
}

_FIELD_NEXT = {
    "items":          "اسأل عن المنتج المطلوب",
    "order_type":     "اسأل: توصيل لو استلام؟",
    "address":        "اسأل: وين عنوان التوصيل؟",
    "customer_name":  "اسأل: شنو اسمك؟",
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
    clarification_needed: Optional[str] = None                  # NUMBER 41A — ambiguous item (e.g. generic burger) needs clarification
    confirmation_status: str = "collecting"   # collecting | awaiting_confirm | confirmed | cancelled
    last_question_asked: Optional[str] = None
    customer_frustrated: bool = False
    order_intent_detected: bool = False      # NUMBER 32 — customer wants to order but no product matched
    upsell_offered: bool = False             # NUMBER 33 — upsell was offered this session (offer once only)
    repeat_order_detected: bool = False      # NUMBER 36 — customer asked to repeat last order (DB lookup pending)
    repeat_order_failed: bool = False        # NUMBER 36 — repeat requested but no previous order found in DB
    sold_out_rejected: List[str] = field(default_factory=list)  # NUMBER 42 — transient, not persisted
    qty_capped: List[str] = field(default_factory=list)         # NUMBER 43 — transient: items whose qty was capped
    promo_code: Optional[str] = None                            # promo code entered by customer
    promo_discount: int = 0                                     # discount amount in currency units
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

    def invalid_payment_method(self, allowed_raw: str) -> bool:
        """True if payment_method is set but not in the restaurant's allowed list."""
        if not self.payment_method:
            return False
        allowed = parse_allowed_payment_methods(allowed_raw)
        if not allowed:
            return False
        return self.payment_method not in allowed

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

    def order_summary_for_confirmation(self, delivery_fee: int = 0) -> str:
        """NUMBER 41A — Professional receipt-style pre-confirmation summary."""
        _CATEGORY_EMOJI_LOCAL = {
            "برجر": "🍔", "زينجر": "🍔", "بروستد": "🍗", "دجاج": "🍗",
            "شاورما": "🥙", "ستيك": "🥩", "لحم": "🥩", "سندويش": "🥪",
            "كولا": "🥤", "بيبسي": "🥤", "مشروب": "🥤", "عصير": "🧃",
            "جوس": "🧃", "ماء": "💧", "شاي": "🍵", "قهوة": "☕",
            "بطاطا": "🍟", "فرايز": "🍟", "بطاطس": "🍟", "سلطة": "🥗",
            "حلا": "🍰", "كيك": "🍰", "ايسكريم": "🍦", "بيتزا": "🍕",
            "نودلز": "🍜", "كباب": "🍢",
        }
        def _emoji(name):
            for kw, em in _CATEGORY_EMOJI_LOCAL.items():
                if kw in name:
                    return em
            return "•"

        lines = ["🧾 تأكيد الطلب"]
        items_sum = 0
        for item in self.items:
            item_total = int(item.price) * item.qty
            items_sum += item_total
            emoji = _emoji(item.name)
            note_str = f" ({item.notes})" if item.notes else ""
            lines.append(f"{emoji} {item.qty}× {item.name}{note_str}")

        lines.append("━━━━━━━━━━━━━━━━")
        _fee = delivery_fee if (self.order_type == "delivery" and delivery_fee > 0) else 0
        total = items_sum + _fee
        if total > 0:
            if _fee > 0:
                lines.append(f"🚚 رسوم التوصيل: {_fee:,} د.ع")
            lines.append(f"💰 المجموع: {int(total):,} د.ع")

        if self.order_type == "delivery":
            lines.append(f"🚗 النوع: توصيل")
            lines.append(f"📍 العنوان: {self.address or '—'}")
        else:
            lines.append("🚗 النوع: استلام")

        if self.customer_name:
            lines.append(f"👤 الاسم: {self.customer_name}")
        if self.phone:
            lines.append(f"📞 الهاتف: {self.phone}")
        if self.payment_method:
            lines.append(f"💳 الدفع: {self.payment_method}")

        lines.append("هل كل شي صحيح؟ ✅ نثبت الطلب لو تحب تعدل؟")
        return "\n".join(lines)

    def items_total(self) -> int:
        """NUMBER 38 — Sum of all item prices × quantities (excludes delivery fee)."""
        return sum(int(it.price) * it.qty for it in self.items)

    def is_below_min_order(self, min_order: int) -> bool:
        """NUMBER 38 — True if items total is below the restaurant's minimum order amount."""
        return min_order > 0 and self.items_total() < min_order

    def generate_confirmation_message(self, order_number: str = "", delivery_fee: int = 0, delivery_time: str = "") -> str:
        """NUMBER 30/32/38/40 — Generate the final formatted order confirmation with total."""
        lines = ["✅ طلبك وصلنا!"]
        lines.append("━━━━━━━━━━━━━")
        items_sum = 0
        for item in self.items:
            item_total = int(item.price) * item.qty
            items_sum += item_total
            price_str = f" — {item_total:,} د.ع" if item.price else ""
            lines.append(f"• {item.name} × {item.qty}{price_str}")
            if item.notes:
                lines.append(f"  ↳ {item.notes}")
        lines.append("━━━━━━━━━━━━━")
        # NUMBER 38 — add delivery fee line and include in grand total
        _fee = delivery_fee if (self.order_type == "delivery" and delivery_fee > 0) else 0
        grand_total = items_sum + _fee
        if _fee > 0:
            lines.append(f"🚚 رسوم التوصيل: {_fee:,} د.ع")
        # Promo code discount
        _discount = int(self.promo_discount) if self.promo_discount else 0
        if _discount > 0 and self.promo_code:
            lines.append(f"🎟️ خصم ({self.promo_code}): -{_discount:,} د.ع")
            grand_total = max(0, grand_total - _discount)
        if grand_total > 0:
            lines.append(f"💰 المجموع: {grand_total:,} د.ع")
        if self.order_type == "delivery":
            lines.append(f"🚗 توصيل — {self.address or '—'}")
            if delivery_time:
                lines.append(f"🕐 وقت التوصيل: {delivery_time}")
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
            "promo_code": self.promo_code,
            "promo_discount": self.promo_discount,
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
        sess.promo_code = d.get("promo_code")
        sess.promo_discount = int(d.get("promo_discount") or 0)
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

        # NUMBER 42/43 — Reset transient lists each message
        session.sold_out_rejected = []
        session.qty_capped = []
        session.clarification_needed = None  # NUMBER 41A — reset ambiguity flag each new message

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
        _apply_increase(session, msg, products, updated)  # NUMBER 41A
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
            # NUMBER 45B — context-aware: bot just asked for name, accept bare Arabic word
            if not name and session.next_missing_field() == "customer_name":
                name = _extract_bare_name(msg)
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

        # 6b. Promo code — detect uppercase alphanumeric code preceded by trigger word
        if not session.promo_code:
            _promo_m = re.search(
                r'(?:كود|رمز|خصم|بروموكود|promo|code)[:\s]+([A-Z0-9]{3,20})',
                msg, re.IGNORECASE
            )
            if _promo_m:
                session.promo_code = _promo_m.group(1).upper()
                updated.append(f"promo_code={session.promo_code}")

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


def _extract_item_note(msg: str, product_name: str, all_product_names: List[str] = None) -> str:
    """
    NUMBER 37 — Extract special-instruction notes that follow a product name.
    Collects ALL modifier phrases (بدون X, مع إضافة Y, حار, etc.) in one pass.
    Examples:
      "برجر بدون بصل مع إضافة جبن" → "بدون بصل مع إضافة جبن"
      "زينجر حار زيادة بدون صوص"   → "حار زيادة بدون صوص"
    """
    idx = msg.find(product_name)
    if idx < 0:
        return ""
    after = msg[idx + len(product_name):idx + len(product_name) + 80]

    # Stop at hard boundaries (comma, period, newline)
    stop = re.search(r'[،,\.\n]', after)
    if stop:
        after = after[:stop.start()]

    after = after.strip()
    if not after:
        return ""

    # Don't treat another product name as part of the note
    for pname in (all_product_names or []):
        if pname != product_name and pname in after:
            after = after[:after.find(pname)].strip()

    # Keep only if at least one known modifier is present
    if not any(mod in after for mod in _NOTE_MODIFIERS):
        return ""

    # Collect modifier phrases explicitly to avoid grabbing noise
    collected = []
    remaining = after
    for mod in sorted(_NOTE_MODIFIERS, key=len, reverse=True):  # longest first
        pos = remaining.find(mod)
        if pos < 0:
            continue
        # Extract modifier + up to next modifier or boundary
        chunk_start = pos
        chunk = remaining[chunk_start:chunk_start + 25].strip()
        # Stop chunk at next modifier
        for other_mod in _NOTE_MODIFIERS:
            if other_mod == mod:
                continue
            other_pos = chunk.find(other_mod)
            if 0 < other_pos:
                chunk = chunk[:other_pos].strip()
        if chunk and chunk not in collected:
            collected.append(chunk)

    if collected:
        return " ".join(collected)[:50].strip()

    return after[:35].strip()


def _extract_items(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
    skip_names: set = None,
) -> None:
    """Match product names in message and update session items (exact + fuzzy).
    NUMBER 41A — specificity-first matching: longer/more-specific names match before shorter ones.
    If customer says "برجر لحم" and menu has "برجر لحم" and "برجر دجاج",
    only "برجر لحم" matches — never a random burger.
    """
    skip_names = skip_names or set()
    matched_ids: set = set()
    all_names = [(p.get("name") or "").strip() for p in products]

    # NUMBER 41A — Sort products by name length descending (most specific first)
    # This ensures "برجر لحم" is checked before "برجر" alone
    sorted_products = sorted(products, key=lambda p: len((p.get("name") or "").strip()), reverse=True)

    # Track which words in msg were consumed by a specific match
    _consumed_spans: list = []

    for p in sorted_products:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if name in skip_names:
            continue
        if name in msg:
            # NUMBER 42 — Sold-out guard: skip and record blocked item
            if p.get("sold_out_date"):
                if name not in session.sold_out_rejected:
                    session.sold_out_rejected.append(name)
                    updated.append(f"soldout_blocked:{name}")
                continue
            qty = _extract_qty(msg, name)
            # NUMBER 43 — Quantity sanity check: cap unreasonable values
            if qty > MAX_QTY:
                if name not in session.qty_capped:
                    session.qty_capped.append(name)
                updated.append(f"qty_capped:{name}:{qty}→{MAX_QTY}")
                logger.info(f"[order_brain43] qty capped: {name} {qty}→{MAX_QTY}")
                qty = MAX_QTY
            existing = next((it for it in session.items if it.name == name), None)
            if existing:
                if existing.qty != qty:
                    existing.qty = qty
                    updated.append(f"qty_update:{name}×{qty}")
                # NUMBER 37 — update/append note if new instruction detected
                note = _extract_item_note(msg, name, all_names)
                if note:
                    if not existing.notes:
                        existing.notes = note
                    elif note not in existing.notes:
                        existing.notes = (existing.notes + " " + note).strip()[:60]
            else:
                note = _extract_item_note(msg, name, all_names)
                session.items.append(OrderItem(
                    name=name,
                    qty=qty,
                    price=float(p.get("price") or 0),
                    product_id=str(p.get("id") or ""),
                    notes=note,
                ))
                updated.append(f"item_added:{name}×{qty}" + (f"[note:{note[:15]}]" if note else ""))
            matched_ids.add(str(p.get("id") or name))

    # NUMBER 41A — Alias/normalization fallback: match via arabic_normalize
    # Run even if some items matched — we want to match remaining aliases too
    if len(matched_ids) < len(products):
        from services.arabic_normalize import find_product_by_alias, filter_products_by_specificity, normalize_arabic
        _alias_matches = find_product_by_alias(msg, products)
        if _alias_matches:
            # Apply specificity filter (e.g. "برگر لحم" → only beef burgers)
            _filtered = filter_products_by_specificity(msg, _alias_matches)
            # NUMBER 41A — Ambiguity: if multiple burger-type items match without specificity, ask clarification
            _burger_kw = {"برجر", "برغر", "بركر", "برگر"}
            _msg_norm = normalize_arabic(msg)
            _msg_has_specificity = any(kw in _msg_norm for kw in
                ("لحم", "beef", "لحمة", "دجاج", "دجاجة", "فراخ", "chicken", "سمك", "fish", "روبيان", "جمبري"))
            _ambig_ids = set()  # NUMBER 41A — Track IDs of ambiguous items to skip
            if not _msg_has_specificity and len(_filtered) > 1:
                _base_groups = {}
                for fp in _filtered:
                    fp_name = normalize_arabic((fp.get("name") or ""))
                    for bk in _burger_kw:
                        if bk in fp_name:
                            _base_groups.setdefault(bk, []).append(fp)
                            break
                for bk, group in _base_groups.items():
                    if len(group) > 1:
                        _names = [(g.get("name") or "").strip() for g in group]
                        session.clarification_needed = "أكيد 🌷 تحب " + " لو ".join(_names) + "؟"
                        updated.append("clarification_needed:" + ",".join(_names))
                        # NUMBER 41A — Mark all items in ambiguous group for skipping
                        for g in group:
                            _ambig_ids.add(str(g.get("id") or g.get("name")))
            # NUMBER 41A — Remove ambiguous items from session.items and _filtered
            if _ambig_ids:
                # Build set of ambiguous product names
                _ambig_names = set()
                for p in products:
                    if str(p.get("id") or p.get("name")) in _ambig_ids:
                        _ambig_names.add((p.get("name") or "").strip())
                # Remove from session items
                session.items = [it for it in session.items if it.name not in _ambig_names]
                # Remove from _filtered
                _filtered = [f for f in _filtered if str(f.get("id") or f.get("name")) not in _ambig_ids]
                # NUMBER 41B — prevent fuzzy fallback from re-adding these ambiguous items
                matched_ids.update(_ambig_ids)
            for fuzzy_p in _filtered:
                fname = (fuzzy_p.get("name") or "").strip()
                if fname in skip_names:
                    continue
                if fuzzy_p.get("sold_out_date"):
                    if fname not in session.sold_out_rejected:
                        session.sold_out_rejected.append(fname)
                        updated.append(f"soldout_blocked_alias:{fname}")
                    continue
                # NUMBER 41A — try qty extraction with both product name and alias
                qty = _extract_qty(msg, fname)
                if qty == 1:
                    # Try extracting qty using alias names that map to this product
                    from services.arabic_normalize import _PRODUCT_ALIASES_NORMALIZED, resolve_alias
                    _fname_canon = resolve_alias(fname)
                    for alias, canon in _PRODUCT_ALIASES_NORMALIZED.items():
                        if canon == _fname_canon and alias in msg:
                            qty2 = _extract_qty(msg, alias)
                            if qty2 > 1:
                                qty = qty2
                                break
                if qty > MAX_QTY:
                    if fname not in session.qty_capped:
                        session.qty_capped.append(fname)
                    updated.append(f"qty_capped:{fname}:{qty}→{MAX_QTY}")
                    qty = MAX_QTY
                existing = next((it for it in session.items if it.name == fname), None)
                if existing:
                    if existing.qty != qty:
                        existing.qty = qty
                        updated.append(f"qty_update:{name}×{qty}")
                else:
                    note = _extract_item_note(msg, fname, all_names)
                    session.items.append(OrderItem(
                        name=fname,
                        qty=qty,
                        price=float(fuzzy_p.get("price") or 0),
                        product_id=str(fuzzy_p.get("id") or ""),
                        notes=note,
                    ))
                    updated.append(f"item_added_alias:{fname}×{qty}")
                    logger.info(f"[order_brain41a] alias match: msg={msg[:30]!r} → product={fname!r}")
                matched_ids.add(str(fuzzy_p.get("id") or fname))

    # NUMBER 32 — fuzzy fallback: try alias/ال-strip if no exact match found yet
    if not matched_ids:
        fuzzy_p = _fuzzy_product_match(msg, products)
        if fuzzy_p:
            fname = (fuzzy_p.get("name") or "").strip()
            if fname in skip_names:
                return
            # NUMBER 42 — Sold-out guard on fuzzy match too
            if fuzzy_p.get("sold_out_date"):
                if fname not in session.sold_out_rejected:
                    session.sold_out_rejected.append(fname)
                    updated.append(f"soldout_blocked_fuzzy:{fname}")
                return
            existing = next((it for it in session.items if it.name == fname), None)
            if not existing:
                qty = _extract_qty(msg, fname)
                # NUMBER 43 — Quantity sanity check on fuzzy path too
                if qty > MAX_QTY:
                    if fname not in session.qty_capped:
                        session.qty_capped.append(fname)
                    updated.append(f"qty_capped:{fname}:{qty}→{MAX_QTY}")
                    logger.info(f"[order_brain43] qty capped (fuzzy): {fname} {qty}→{MAX_QTY}")
                    qty = MAX_QTY
                note = _extract_item_note(msg, fname, all_names)
                session.items.append(OrderItem(
                    name=fname,
                    qty=qty,
                    price=float(fuzzy_p.get("price") or 0),
                    product_id=str(fuzzy_p.get("id") or ""),
                    notes=note,
                ))
                updated.append(f"item_added_fuzzy:{fname}×{qty}")
                logger.info(f"[order_brain32] fuzzy match: msg_excerpt={msg[:30]!r} → product={fname!r}")


def _extract_qty(msg: str, product_name: str) -> int:
    """Extract quantity for a specific product mention. NUMBER 41A — also try normalized msg."""
    # Try original msg first
    result = _extract_qty_impl(msg, product_name)
    if result != 1:
        return result
    # NUMBER 41A — try with normalized msg (برگ→برجر) and normalized product name
    try:
        from services.arabic_normalize import normalize_arabic
        norm_msg = normalize_arabic(msg)
        norm_name = normalize_arabic(product_name)
        if norm_msg != msg or norm_name != product_name:
            result2 = _extract_qty_impl(norm_msg, norm_name)
            if result2 != 1:
                return result2
    except Exception:
        pass
    return 1


def _extract_qty_impl(msg: str, product_name: str) -> int:
    """Internal: Extract quantity for a specific product mention."""
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


# NUMBER 41A — Words that should NOT be captured as part of a name
_NAME_STOP_WORDS = {"ورقمي", "ورقم", "وعنواني", "وعنوان", "وتوصيل", "واستلام", "وكاش", "وكارد",
                    "وهاتفي", "وهاتف", "ورقمه", "والموبايل", "والموبايلي", "وبايد",
                    "في", "من", "إلى", "الى", "هو", "هي", "على", "مع", "لو"}

_BARE_NAME_SKIP = {
    "تمام", "اوكي", "اوك", "نعم", "اه", "ايه", "لا", "لأ", "ما",
    "كاش", "كارد", "توصيل", "استلام", "بطاقة", "نقد", "فلوس",
    "شكرا", "شكراً", "هلا", "مرحبا", "سلام", "صح", "زين", "كلش",
    "أريد", "اريد", "ابي", "أبي", "جيبلي", "خذلي", "أضيف", "اضيف",
}

def _extract_bare_name(msg: str) -> Optional[str]:
    """NUMBER 45B — Accept bare Arabic name when context is customer_name step.
    e.g. bot asks 'شنو اسمك؟', customer replies 'محمد' or 'محمد علي'.
    """
    words = msg.strip().split()
    if not words or len(words) > 3:
        return None
    first = words[0]
    if not re.match(r'^[؀-ۿ]{2,15}$', first):
        return None
    if first in _BARE_NAME_SKIP:
        return None
    # Accept two-word name if second word also looks like a name
    if len(words) >= 2:
        second = words[1]
        if re.match(r'^[؀-ۿ]{2,12}$', second) and second not in _BARE_NAME_SKIP:
            return f"{first} {second}"
    return first


def _extract_name(msg: str) -> Optional[str]:
    """Extract customer name from message. NUMBER 41B H5 — single word only, stop at space/conjunction."""
    # Use [^\s،؟?]{2,15} to stop at whitespace/punctuation — never captures multi-word strings
    patterns = [
        r'اسمي\s+([^\s،؟?]{2,15})',
        r'(?:أنا|انا)\s+([^\s،؟?]{2,10})(?:\s|$|،)',
        r'(?:شسمك[؟?]|شنو اسمك[؟?])\s*([^\s،؟?]{2,10})',
        r'(?:باسم|أطلب باسم)\s+([^\s،؟?]{2,10})',
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            candidate = m.group(1).strip()
            # Filter out stop words and non-Arabic strings that look like phone numbers
            if candidate in _NAME_STOP_WORDS:
                continue
            if re.search(r'\d', candidate):
                continue
            if len(candidate) >= 2:
                return candidate
    return None


def _extract_address(msg: str) -> Optional[str]:
    """Extract delivery address/area from message. NUMBER 41A — handle 'للكرادة' pattern."""
    _PUNCT = re.compile(r'^[\s؟?،.:!]+|[\s؟?،.:!]+$')

    # Labeled address patterns — exclude ؟ from captured chars
    patterns = [
        r'(?:العنوان|عنواني|عنوان التوصيل)[:\s،]*([؁-ۿ\s\d،]+?)(?:\.|\n|$)',
        r'(?:أسكن في|أسكن|منطقتي|حيي|منطقة|في حي)\s+([؁-ۿ\s]{3,30})(?:\s|$|،)',
        # NUMBER 41A — "توصيل للكرادة" / "للكرادة" / "إلى الكرادة"
        r'(?:توصيل\s*(?:لل?|إلى\s*|الى\s*))((?:ال)?[؀-ۿ]{3,20})(?:\s|$|،|\.)',
        r'لل((?:ال)?[؀-ۿ]{3,20})(?:\s|$|،|\.)',
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            candidate = _PUNCT.sub("", m.group(1))
            if len(candidate) >= 3:
                # NUMBER 41A — if candidate matches an area without "ال", add it
                for area in _IRAQ_AREAS:
                    area_no_al = re.sub(r'^ال', '', area)
                    if area_no_al == candidate and area.startswith('ال'):
                        return area
                return candidate

    # NUMBER 45B — location-descriptor phrases capture full address (checked first
    # so "منصور قرب المول" returns full text, not just the area name)
    _LOC_INDICATORS = [
        "قرب", "جنب", "أمام", "وراء", "خلف", "بجانب", "مقابل",
        "بالقرب", "زقاق", "شارع", "حارة", "محلة", "طريق",
    ]
    if len(msg.strip()) >= 5 and any(ind in msg for ind in _LOC_INDICATORS):
        return msg.strip()

    # Direct Iraqi area name mention (with ال prefix)
    for area in _IRAQ_AREAS:
        if area in msg:
            return area

    # NUMBER 45B — also try without ال prefix: "منصور" → matches "المنصور"
    for area in _IRAQ_AREAS:
        area_no_al = re.sub(r'^ال', '', area)
        if len(area_no_al) >= 5 and area_no_al in msg:
            return area  # return canonical form

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

def _decrease_item_qty(session: OrderSession, name: str, amount: int, updated: List[str]) -> None:
    """NUMBER 41A — Decrease item quantity by amount. Remove if qty reaches 0."""
    existing = next((it for it in session.items if it.name == name), None)
    if not existing:
        return
    old_qty = existing.qty
    new_qty = max(0, old_qty - amount)
    if new_qty <= 0:
        session.items = [it for it in session.items if it.name != name]
        updated.append(f"item_removed:{name}")
    else:
        existing.qty = new_qty
        updated.append(f"qty_decreased:{name}:{old_qty}→{new_qty}")


def _increase_item_qty(session: OrderSession, name: str, amount: int, price: float, product_id: str, updated: List[str]) -> None:
    """NUMBER 41A — Increase item quantity by amount. Add item if not in session."""
    existing = next((it for it in session.items if it.name == name), None)
    if existing:
        old_qty = existing.qty
        existing.qty = old_qty + amount
        updated.append(f"qty_increased:{name}:{old_qty}→{existing.qty}")
    else:
        session.items.append(OrderItem(name=name, qty=amount, price=price, product_id=product_id))
        updated.append(f"item_added:{name}×{amount}")


def _session_item_matches_msg(item_name: str, msg: str) -> bool:
    """NUMBER 41A — Check if a session item name matches any reference in msg, including aliases."""
    # Direct match
    if item_name in msg or f"ال{item_name}" in msg:
        return True
    # Alias match: e.g. msg says "كولا" but session has "بيبسي"
    from services.arabic_normalize import find_product_name_in_session, resolve_alias
    # Check if any word in msg resolves to the same canonical as the item
    canonical_item = resolve_alias(item_name)
    if canonical_item:
        # Check all aliases that map to this canonical
        from services.arabic_normalize import _PRODUCT_ALIASES_NORMALIZED
        for alias, canon in _PRODUCT_ALIASES_NORMALIZED.items():
            if canon == canonical_item and alias in msg:
                return True
    return False


def _apply_remove(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
) -> None:
    """
    NUMBER 35/41A — Remove or decrease items explicitly named with a removal prefix.
    Handles:
    - "شيل الكولا" = remove all cola (matches بيبسي via alias)
    - "شيل كولا وحدة" = decrease cola by 1
    - "احذف البطاطا" = remove all potato
    - "ما أريد الكولا" = remove all cola
    - "بدون الكولا" = remove all cola
    """
    for item in list(session.items):
        name = item.name

        # "بدون [name]" pattern — always full remove (exact phrase match only)
        if f"بدون {name}" in msg or f"بدون ال{name}" in msg:
            if session.remove_item(name):
                updated.append(f"item_removed:{name}")
            continue
        # Also check alias for بدون pattern (exact phrase)
        from services.arabic_normalize import resolve_alias, _PRODUCT_ALIASES_NORMALIZED
        for alias, canon in _PRODUCT_ALIASES_NORMALIZED.items():
            if f"بدون {alias}" in msg or f"بدون ال{alias}" in msg:
                item_canon = resolve_alias(name)
                if item_canon and item_canon == canon:
                    if session.remove_item(name):
                        updated.append(f"item_removed:{name} (via alias)")
                    break

        # Check for quantity decrease: "شيل [name] وحدة/اثنين/2" etc.
        # NUMBER 41A — prefix must be NEAR the item name, not just both present
        _decrease_match = None
        for prefix in REMOVE_PREFIXES:
            if prefix not in msg:
                continue
            # Check if prefix appears near the item name (within 4 words)
            _name_variants = [name, f"ال{name}"]
            # Also check aliases
            from services.arabic_normalize import resolve_alias, _PRODUCT_ALIASES_NORMALIZED
            for alias, canon in _PRODUCT_ALIASES_NORMALIZED.items():
                if canon == resolve_alias(name):
                    _name_variants.append(alias)
                    _name_variants.append(f"ال{alias}")
            for nv in _name_variants:
                # NUMBER 41A — prefix must be BEFORE and NEAR the name
                # "شيل كولا" = prefix at 3, name at 7, dist=4, prefix before name ✅
                # "شيل كولا وزيد بطاطا" = prefix at 3, بطاطا at 22, dist=19 ✅ BUT
                #   there is a CLOSER name (كولا) to this prefix, so بطاطا should not match
                for m_prefix in re.finditer(re.escape(prefix), msg):
                    # Find the CLOSEST name variant AFTER this prefix
                    _best_name_match = None
                    _best_dist = 999
                    for nv2 in _name_variants:
                        for m_name in re.finditer(re.escape(nv2), msg):
                            if m_name.start() >= m_prefix.start():
                                d = m_name.start() - m_prefix.start()
                                if d < _best_dist:
                                    _best_dist = d
                                    _best_name_match = nv2
                    # Only match if THIS name variant is the closest one to this prefix
                    if _best_name_match == nv and _best_dist <= 15:
                        _decrease_match = prefix
                        break
                if _decrease_match:
                    break
            if _decrease_match:
                break

        if _decrease_match:
            # Try to extract quantity to decrease
            dec_qty = _extract_decrease_qty(msg, name)
            # Also try alias-based qty extraction
            if not dec_qty:
                from services.arabic_normalize import _PRODUCT_ALIASES_NORMALIZED as _PAN
                for alias, canon in _PRODUCT_ALIASES_NORMALIZED.items():
                    if canon == resolve_alias(name) and alias in msg:
                        dec_qty = _extract_decrease_qty(msg, alias)
                        if dec_qty:
                            break
            if dec_qty and dec_qty < item.qty:
                # Partial decrease
                _decrease_item_qty(session, name, dec_qty, updated)
            else:
                # Full remove (no qty specified, or qty >= current)
                if session.remove_item(name):
                    updated.append(f"item_removed:{name}")


# NUMBER 41A — Quantity decrease extraction: "شيل كولا وحدة/2/اثنين"
_DECREASE_QTY_RE = re.compile(
    r'(?:شيل|اشيل|شيله|احذف|حذف|ما أريد|ما اريد|ما ابي|امسح|لا أريد|لا اريد)'
    r'\s+(?:ال)?[\u0600-\u06FF\w]{2,20}?'
    r'\s+(\d+|واحد|وحدة|وحده|اثنين|ثنتين|ثلاثة|ثلاث|أربعة|أربع|خمسة|خمس)',
    re.UNICODE,
)


def _extract_decrease_qty(msg: str, product_name: str) -> Optional[int]:
    """NUMBER 41A — Extract quantity to decrease from phrases like 'شيل كولا وحدة' or 'شيل 2 كولا'."""
    # Pattern: prefix + product_name + number word/digit
    for prefix in REMOVE_PREFIXES:
        # "شيل كولا وحدة" / "شيل كولا 1"
        m = re.search(
            re.escape(prefix) + r'\s+(?:ال)?' + re.escape(product_name) +
            r'\s+(\d+|واحد|وحدة|وحده|اثنين|ثنتين|ثلاثة|ثلاث|أربعة|أربع|خمسة|خمس)',
            msg, re.UNICODE,
        )
        if m:
            val = m.group(1)
            if val.isdigit():
                return int(val)
            return _AR_NUMBERS.get(val, 1)

        # "شيل وحدة كولا" / "شيل 2 كولا"
        m = re.search(
            re.escape(prefix) + r'\s+(\d+|واحد|وحدة|وحده|اثنين|ثنتين|ثلاثة|ثلاث|أربعة|أربع|خمسة|خمس)'
            r'\s+(?:ال)?' + re.escape(product_name),
            msg, re.UNICODE,
        )
        if m:
            val = m.group(1)
            if val.isdigit():
                return int(val)
            return _AR_NUMBERS.get(val, 1)

    return None


# NUMBER 41A — Increase keywords: "زيد", "ضيف", "حط"
_INCREASE_PREFIXES = [
    "زيد", "زود", "ضيف", "أضيف", "اضيف", "حط", "أضف", "اضف",
    "نضيف", "نزيد", "زودلي", "ضيفلي", "زيدلي",
]


def _apply_increase(
    session: OrderSession,
    msg: str,
    products: List[dict],
    updated: List[str],
) -> None:
    """NUMBER 41A — Handle 'زيد بطاطا' / 'ضيف كولا' — increase or add item. Supports alias matching."""
    for prefix in _INCREASE_PREFIXES:
        if prefix not in msg:
            continue
        # Try to match a product name after the prefix
        for p in products:
            pname = (p.get("name") or "").strip()
            if not pname:
                continue
            if pname in msg:
                qty = _extract_qty(msg, pname)
                _increase_item_qty(
                    session, pname, qty,
                    float(p.get("price") or 0),
                    str(p.get("id") or ""),
                    updated,
                )
                return
        # NUMBER 41A — Alias match: "ضيف كولا" → find بيبسي via alias
        from services.arabic_normalize import find_product_by_alias, filter_products_by_specificity
        _alias_matches = find_product_by_alias(msg, products)
        if _alias_matches:
            _filtered = filter_products_by_specificity(msg, _alias_matches)
            if _filtered:
                p = _filtered[0]
                pname = (p.get("name") or "").strip()
                qty = _extract_qty(msg, pname)
                _increase_item_qty(
                    session, pname, qty,
                    float(p.get("price") or 0),
                    str(p.get("id") or ""),
                    updated,
                )
                return
        # Fuzzy match
        fuzzy_p = _fuzzy_product_match(msg, products)
        if fuzzy_p:
            fname = (fuzzy_p.get("name") or "").strip()
            qty = _extract_qty(msg, fname)
            _increase_item_qty(
                session, fname, qty,
                float(fuzzy_p.get("price") or 0),
                str(fuzzy_p.get("id") or ""),
                updated,
            )
            return


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
