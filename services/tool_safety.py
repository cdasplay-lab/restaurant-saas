"""
services/tool_safety.py — Safety validation for function-calling tool results.

Guards against GPT generating in update_order.reply:
  1. Premature order confirmation before place_order succeeds
  2. Prices not validated from the DB
  3. Invented product names not in the menu

None of these functions call OpenAI — pure deterministic logic.
"""
from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger("restaurant-saas")

# ── Price detection ────────────────────────────────────────────────────────────
# Matches: "8,000 د.ع" | "8000 د.ع" | "بـ8000" | "بـ 8,000 دينار" | "8000 IQD"
_PRICE_PATTERN = re.compile(
    r"بـ?\s*\d[\d,،]*\s*(?:د\.ع|دينار|IQD)"
    r"|\d[\d,،]+\s*(?:د\.ع|دينار|IQD)",
    re.UNICODE,
)

# ── Premature confirmation phrases ─────────────────────────────────────────────
# Any of these in update_order.reply means GPT is confirming the order prematurely.
# place_order is the ONLY tool allowed to confirm.
_PREMATURE_CONFIRM_PHRASES = [
    "تم تأكيد الطلب",
    "تأكد الطلب",
    "تم الطلب",
    "طلبك تأكد",
    "وصلنا طلبك",
    "استلمنا الطلب",
    "استلمنا طلبك",
    "راح نجهزه",
    "جاري التجهيز",
    "نجهزه لك",
    "الشباب يجهزون",
    "يجهزون هسه",
    "جاهز الطلب",
    "✅ طلبك",
    "✅ طلبك:",
    "في الطريق",
    "على الطريق",
    "جاي إليك",
    "حاضر 🌷 الشباب",
    "طلبك وصلنا",
    "سيصلك الطلب",
    "طلبك في الطريق",
    "تم استلام الطلب",
]


# ── Public API ─────────────────────────────────────────────────────────────────

def has_premature_confirmation(text: str) -> bool:
    """True if text contains confirmation language forbidden in update_order.reply."""
    if not text:
        return False
    for phrase in _PREMATURE_CONFIRM_PHRASES:
        if phrase in text:
            return True
    return False


def strip_prices_from_reply(text: str) -> str:
    """
    Remove price mentions from a reply string.
    Prices belong only in the place_order confirmation summary, not in
    mid-conversation update_order.reply messages.
    """
    if not text:
        return text
    cleaned = _PRICE_PATTERN.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def find_best_product_match(name: str, products: list) -> Optional[dict]:
    """
    Return the best-matching product dict for a given item name.
    NUMBER 41A — uses arabic_normalize for alias matching + specificity filter.

    Tiers:
    0. Alias match via arabic_normalize (e.g. "كولا" → "بيبسي")
    1. Exact match
    2. Product name ⊆ item name — longest product name wins (specificity)
    3. Item name ⊆ product name — shortest product name wins (closest)
    4. Word-level overlap ≥ 1 word of len≥3 — highest overlap wins
    """
    if not name or not products:
        return None

    # NUMBER 41A — Tier 0: Alias match via arabic_normalize
    try:
        from services.arabic_normalize import find_product_by_alias, filter_products_by_specificity
        _alias_matches = find_product_by_alias(name, products)
        if _alias_matches:
            _filtered = filter_products_by_specificity(name, _alias_matches)
            if _filtered:
                return _filtered[0]
    except Exception:
        pass  # fallback to original logic

    name_l = name.strip().lower()

    # 1. Exact
    for p in products:
        if p.get("name", "").strip().lower() == name_l:
            return p

    # 2. Product name ⊆ item name — NUMBER 41A: longest product name wins
    candidates_t2 = []
    for p in products:
        p_l = p.get("name", "").strip().lower()
        if p_l and p_l in name_l:
            candidates_t2.append((len(p_l), p))
    if candidates_t2:
        candidates_t2.sort(key=lambda x: x[0], reverse=True)  # longest first
        return candidates_t2[0][1]

    # 3. Item name ⊆ product name — shortest product name wins (closest match)
    candidates_t3 = []
    for p in products:
        p_l = p.get("name", "").strip().lower()
        if p_l and name_l in p_l:
            candidates_t3.append((len(p_l), p))
    if candidates_t3:
        candidates_t3.sort(key=lambda x: x[0])  # shortest first
        return candidates_t3[0][1]

    # 4. Word-level: highest overlap wins (with tiebreaker on product name length)
    name_words = set(w for w in name_l.split() if len(w) >= 3)
    if name_words:
        best, best_score, best_len = None, 0, 0
        for p in products:
            p_l = p.get("name", "").strip().lower()
            p_words = set(p_l.split())
            # Exact word intersection first
            score = len(name_words & p_words)
            if score == 0:
                # Substring within a word (e.g. "برجر" within longer word)
                score = sum(
                    1 for nw in name_words
                    if any(nw in pw or pw in nw for pw in p_words if len(pw) >= 3)
                )
            # Tiebreaker: prefer longer product name (more specific)
            if score > best_score or (score == best_score and score > 0 and len(p_l) > best_len):
                best_score = score
                best_len = len(p_l)
                best = p
        if best_score > 0:
            return best

    return None


def validate_tool_items(items: list, products: list) -> tuple:
    """
    Validate items from a tool call against the actual product list.

    Returns:
        (validated_items, unknown_names)
        validated_items — list of item dicts with DB-canonical name + DB price
        unknown_names   — names that had no match in products (GPT invented them)

    Rules:
    - Uses DB canonical name (not GPT's spelling)
    - Uses DB price (not GPT's price — Rule 4)
    - Items with no product match go to unknown_names (Rule 5+6)
    """
    if not items:
        return [], []

    validated: list = []
    unknown: list = []

    for item in items:
        raw_name = str(item.get("name", "")).strip()
        qty = max(1, int(item.get("qty", 1)))
        gpt_price = float(item.get("unit_price", 0))
        note = str(item.get("note", "") or "")

        if not raw_name:
            continue

        match = find_best_product_match(raw_name, products)
        if match:
            # NUMBER 41B C5 — reject sold-out items (available=0 or sold_out_date set)
            _avail_raw = match.get("available", match.get("is_available", None))
            _avail = int(_avail_raw) if _avail_raw is not None else 1
            if not _avail or match.get("sold_out_date"):
                unknown.append(f"[نافد] {match['name']}")
                logger.warning(f"[tool_safety] sold-out item blocked: {match['name']!r}")
                continue
            db_price = float(match.get("price") or gpt_price)
            validated.append({
                "name": match["name"],   # canonical name from DB
                "qty": qty,
                "unit_price": db_price,  # always DB price (Rule 4)
                "note": note,
            })
        else:
            unknown.append(raw_name)

    return validated, unknown


def validate_update_order_reply(
    reply: str,
    ob_session,             # OrderSession | None
    unknown_item_names: list,
) -> str:
    """
    Validate and sanitize an update_order.reply before it reaches the customer.

    Enforces:
    1. Block premature confirmation language (Rule 1+3)
    2. Strip prices (Rule 4)
    3. Replace reply with clarification for unknown items (Rule 5+6)

    Returns a safe reply string (may be the original, cleaned, or a replacement).
    """
    # Rule 5+6 — unknown items take highest priority
    if unknown_item_names:
        names_str = "، ".join(f"«{n}»" for n in unknown_item_names[:2])
        extra = "" if len(unknown_item_names) <= 2 else f" وغيرها"
        logger.warning(
            f"[tool_safety] unknown items blocked: {unknown_item_names}"
        )
        return (
            f"ما لقيت {names_str}{extra} بالمنيو 🌷 — "
            f"تكدر تشوف المنيو وتكلني شنو بالضبط تريد؟"
        )

    # Rule 1+3 — premature confirmation
    if has_premature_confirmation(reply):
        logger.warning(
            f"[tool_safety] premature confirmation blocked: {reply[:80]!r}"
        )
        if ob_session is not None:
            try:
                missing = ob_session.missing_fields()
                if missing:
                    from services.order_brain import _FIELD_QUESTION
                    q = _FIELD_QUESTION.get(missing[0])
                    if q:
                        return q
                    return ob_session.generate_next_directive([]) or "تكدر تكمّل؟ 🌷"
            except Exception:
                pass
        return "وصلت 🌷 — كمّلنا؟"

    # Rule 4 — strip prices from mid-conversation replies
    cleaned = strip_prices_from_reply(reply)
    if cleaned != reply:
        logger.info("[tool_safety] price stripped from update_order.reply")

    return cleaned or reply
