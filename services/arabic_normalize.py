"""
services/arabic_normalize.py — NUMBER 41A: Shared Arabic normalization for order matching.

All product matching (order_brain + tool_safety) must go through these helpers
so that "برگر لحم", "كولا", "بطاطا" etc. are consistently matched regardless
of spelling variant.
"""
from __future__ import annotations

import re
from typing import Optional, List, Dict

# ── Character-level normalization ──────────────────────────────────────────────

# Arabic variant characters that should be treated as equivalent
_CHAR_MAP = str.maketrans({
    "گ": "ج",   # Persian Gaf → Jim (برگ� = برجر)
    "ك": "ك",   # normalize to standard Arabic Kaf
    "ى": "ي",   # Alif Maqsura → Ya
    "ه": "ه",   # keep as-is, just explicit
})

# Arabic-Indic digits → ASCII
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for matching: variant chars, digits, whitespace."""
    if not text:
        return ""
    t = text.translate(_CHAR_MAP)
    t = t.translate(_DIGIT_MAP)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ── Product name normalization ─────────────────────────────────────────────────

# Alias map: variant spellings → canonical substring to search for in product names
# The VALUE is what we look for in the product list.
_PRODUCT_ALIASES_NORMALIZED: Dict[str, str] = {
    # Burger variants
    "برگر": "برجر",
    "برگز": "برجر",
    "بركر": "برجر",
    "بيرجر": "برجر",
    "بوركر": "برجر",
    "بورغر": "برجر",
    # Cola/Pepsi variants → look for "بيبسي" in product names
    "كولا": "بيبسي",
    "كوكا": "بيبسي",
    "كوكاكولا": "بيبسي",
    "بيبسي": "بيبسي",
    "ببسي": "بيبسي",
    "pepsi": "بيبسي",
    "cola": "بيبسي",
    # Potato/Fries variants → look for "بطاطا" in product names
    "بطاطا": "بطاطا",
    "بطاطس": "بطاطا",
    "بطاطيس": "بطاطا",
    "فرايز": "بطاطا",
    "فريز": "بطاطا",
    "فرايس": "بطاطا",
    "fries": "بطاطا",
    # Chicken variants
    "فراخ": "دجاج",
    "دجاجة": "دجاج",
    # Shawarma variants
    "شاورمة": "شاورما",
    "شورما": "شاورما",
    # Zinger variants
    "زنجر": "زينجر",
    "زينگر": "زينجر",
    # Broasted
    "بروستد": "بروستد",
    "مبروستد": "بروستد",
    "برستد": "بروستد",
}

# Reverse map: for a given product name, what aliases point to it?
# Built automatically from _PRODUCT_ALIASES_NORMALIZED
def _build_reverse_aliases() -> Dict[str, List[str]]:
    """Build reverse alias map: canonical → [alias1, alias2, ...]"""
    rev: Dict[str, List[str]] = {}
    for alias, canonical in _PRODUCT_ALIASES_NORMALIZED.items():
        if canonical not in rev:
            rev[canonical] = []
        rev[canonical].append(alias)
    return rev

_REVERSE_ALIASES = _build_reverse_aliases()


def resolve_alias(word: str) -> Optional[str]:
    """Given a word from customer message, return the canonical product substring.
    Returns None if no alias matches."""
    w = normalize_arabic(word).strip()
    if w in _PRODUCT_ALIASES_NORMALIZED:
        return _PRODUCT_ALIASES_NORMALIZED[w]
    return None


def find_product_by_alias(msg: str, products: List[dict]) -> List[dict]:
    """
    NUMBER 41A — Find all products that match any alias in the message.
    Returns list of matching product dicts.
    Uses specificity: longest alias match first.
    """
    norm_msg = normalize_arabic(msg)
    matches = []

    # Sort aliases by length descending (most specific first)
    sorted_aliases = sorted(_PRODUCT_ALIASES_NORMALIZED.keys(), key=len, reverse=True)

    matched_canonicals = set()  # track which canonicals we already matched

    for alias in sorted_aliases:
        norm_alias = normalize_arabic(alias)
        if norm_alias not in norm_msg:
            continue
        canonical = _PRODUCT_ALIASES_NORMALIZED[alias]
        if canonical in matched_canonicals:
            continue
        # Find product(s) whose name contains the canonical substring
        for p in products:
            pname = normalize_arabic((p.get("name") or "").strip())
            if canonical in pname:
                if p not in matches:
                    matches.append(p)
                    matched_canonicals.add(canonical)

    return matches


def find_product_name_in_session(alias_name: str, session_items: list) -> Optional[str]:
    """
    NUMBER 41A — Given an alias name (e.g. "كولا"), find the matching session item name.
    This solves: session has "بيبسي" but customer says "شيل كولا".
    Returns the session item name if found, None otherwise.
    """
    canonical = resolve_alias(alias_name)
    if not canonical:
        # Fallback 1 — direct substring match
        for it in session_items:
            if alias_name in it.name or it.name in alias_name:
                return it.name
        # Fallback 2 — normalized substring match (handles گ→ج, أ→ا, ال prefix etc.)
        # e.g. "برگر" → "برجر" which IS a substring of "برجر لحم"
        # e.g. "البرگر" → strip ال → "برجر" which IS a substring of "برجر لحم"
        alias_norm = normalize_arabic(alias_name)
        alias_stripped = alias_norm[2:] if alias_norm.startswith("ال") else alias_norm
        for it in session_items:
            it_norm = normalize_arabic(it.name)
            if alias_norm in it_norm or (alias_stripped and alias_stripped in it_norm):
                return it.name
        return None

    for it in session_items:
        it_name_norm = normalize_arabic(it.name)
        if canonical in it_name_norm:
            return it.name
        # Also check if the item name has an alias that resolves to same canonical
        it_canonical = resolve_alias(it.name)
        if it_canonical and it_canonical == canonical:
            return it.name

    return None


# ── Specificity matching for burgers ──────────────────────────────────────────

# Meat keywords that indicate a specific type
_MEAT_KEYWORDS = {"لحم", "beef", "لحمة"}
_CHICKEN_KEYWORDS = {"دجاج", "دجاجة", "فراخ", "chicken", "فرخة"}
_FISH_KEYWORDS = {"سمك", "fish", "روبيان", "جمبري"}


def filter_products_by_specificity(
    customer_text: str,
    candidate_products: List[dict],
) -> List[dict]:
    """
    NUMBER 41A — Filter candidate products by specificity keywords.
    If customer says "برجر لحم", only return beef burgers (reject chicken).
    If customer says "برجر" alone and multiple types exist, return all (caller should ask clarification).
    """
    norm = normalize_arabic(customer_text)

    has_meat = any(kw in norm for kw in _MEAT_KEYWORDS)
    has_chicken = any(kw in norm for kw in _CHICKEN_KEYWORDS)
    has_fish = any(kw in norm for kw in _FISH_KEYWORDS)

    if not has_meat and not has_chicken and not has_fish:
        # No specificity keyword — return all candidates
        return candidate_products

    # Drink/side keywords — ALWAYS kept regardless of meat keywords
    _DRINK_KW = {"كولا", "بيبسي", "مشروب", "عصير", "جوس", "ماء", "شاي", "قهوة", "ليمون"}
    _SIDE_KW = {"بطاطا", "بطاطس", "فرايز", "سلطة", "خبز"}

    # Filter by requested type, but ALWAYS keep drinks/sides
    filtered = []
    for p in candidate_products:
        pname = normalize_arabic((p.get("name") or ""))

        # Always keep drinks and sides regardless of meat keywords
        if any(kw in pname for kw in _DRINK_KW) or any(kw in pname for kw in _SIDE_KW):
            filtered.append(p)
            continue

        if has_meat and any(kw in pname for kw in _MEAT_KEYWORDS):
            filtered.append(p)
        elif has_chicken and any(kw in pname for kw in _CHICKEN_KEYWORDS):
            filtered.append(p)
        elif has_fish and any(kw in pname for kw in _FISH_KEYWORDS):
            filtered.append(p)

    # If filtering removed everything, fall back to all candidates
    return filtered if filtered else candidate_products
