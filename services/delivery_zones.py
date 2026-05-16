
"""
Delivery Zones — منطقة التوصيل
Maps Iraqi neighborhoods to delivery fees for each restaurant.

How it works:
1. Restaurant owner configures zones via dashboard (JSON in settings.delivery_zones)
2. When customer mentions an area, we match it and return the fee
3. If area is not in any zone → ask for clarification or reject
4. If area is in a zone → auto-set delivery fee in OrderBrain

Zone format (stored in settings.delivery_zones as JSON):
{
  "zones": [
    {
      "name": "قريبة",
      "fee": 1000,
      "areas": ["المنصور", "الكرادة", "العيادية"],
      "estimated_minutes": 20
    },
    {
      "name": "بعيدة",
      "fee": 3000,
      "areas": ["الدورة", "البياع", "شمال بغداد"],
      "estimated_minutes": 45
    }
  ],
  "out_of_range_message": "عذراً، المنطقة خارج نطاق التوصيل 🙏"
}
"""

import json
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("restaurant-saas")


# ── Baghdad Area Database — comprehensive neighborhood list ──────────────────
# Used for auto-suggestion when owner is setting up zones.
# Each area has common aliases/variations that customers might use.

BAGHDAD_AREAS = {
    # ── الكرخ (Karkh - West Baghdad) ──
    "المنصور": {
        "aliases": ["المنصور", "منصور", "حي المنصور"],
        "district": "الكرخ",
        "common_ref": "قرب مركز شرطة المنصور",
    },
    "ال Mansour الجديد": {
        "aliases": ["المنصور الجديد", "منصور الجديد"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "العيادية": {
        "aliases": ["العيادية", "عياضية", "حي العيادية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الكرادة": {
        "aliases": ["الكرادة", "كرادة", "كراده", "حي الكرادة", "الكراده"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الكرادة الشرقية": {
        "aliases": ["الكرادة الشرقية", "كرادة شرقية", "كراده شرقية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "أبو غريب": {
        "aliases": ["أبو غريب", "ابو غريب", "أبو غريب", "ابو غريب"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الحارثية": {
        "aliases": ["الحارثية", "حارثية", "حي الحارثية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الجامعة": {
        "aliases": ["الجامعة", "جامعة بغداد", "حي الجامعة", "باب المعظم"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "المطارية": {
        "aliases": ["المطارية", "مطار", "قرب المطار", "حي المطارية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "العامرية": {
        "aliases": ["العامرية", "عامرية", "عامرية بغداد", "حي العامرية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الخضراء": {
        "aliases": ["الخضراء", "حي الخضراء", "خضراء"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "القادسية": {
        "aliases": ["القادسية", "قادسية", "حي القادسية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الربيع": {
        "aliases": ["الربيع", "حي الربيع", "ربيع"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الزعفرانية": {
        "aliases": ["الزعفرانية", "زعفرانية", "حي الزعفرانية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الدورة": {
        "aliases": ["الدورة", "دورة", "حي الدورة"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "البياع": {
        "aliases": ["البياع", "بياع", "حي البياع"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الشعلة": {
        "aliases": ["الشعلة", "شعلة", "حي الشعلة"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "حرية": {
        "aliases": ["حرية", "الحرية", "حي الحرية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "المحمودية": {
        "aliases": ["المحمودية", "محمودية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "اليوسفية": {
        "aliases": ["اليوسفية", "يوسفية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "اللطيفية": {
        "aliases": ["اللطيفية", "لطيفية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الرشيد": {
        "aliases": ["الرشيد", "حي الرشيد"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الشرقاط": {
        "aliases": ["الشرقاط", "شرقاط"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الغزالية": {
        "aliases": ["الغزالية", "غزالية", "حي الغزالية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الجادرية": {
        "aliases": ["الجادرية", "جادرية", "حي الجادرية"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "بغداد الجديدة": {
        "aliases": ["بغداد الجديدة", "بغداد جديد"],
        "district": "الكرخ",
        "common_ref": "",
    },

    # ── الرصافة (Rusafa - East Baghdad) ──
    "الأعظمية": {
        "aliases": ["الأعظمية", "أعظمية", "اعظمية", "حي الأعظمية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الكاظمية": {
        "aliases": ["الكاظمية", "كاظمية", "كاظميه", "حي الكاظمية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الرصافة": {
        "aliases": ["الرصافة", "رصافة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الشورجة": {
        "aliases": ["الشورجة", "شورجة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "المتنبي": {
        "aliases": ["المتنبي", "شارع المتنبي", "شورجة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الرشيد": {
        "aliases": ["شارع الرشيد", "الرشيد"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "البتاويين": {
        "aliases": ["البتاويين", "بتاويين", "حي البتاويين"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الكرخ": {
        "aliases": ["الكرخ"],
        "district": "الكرخ",
        "common_ref": "",
    },
    "الصدرية": {
        "aliases": ["الصدرية", "صدرية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "النعمانية": {
        "aliases": ["النعمانية", "نعمانية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الوزيرية": {
        "aliases": ["الوزيرية", "وزيرية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "بأبي": {
        "aliases": ["بأبي", "حي بأبي"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "زيونة": {
        "aliases": ["زيونة", "حي زيونة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "المسعود": {
        "aliases": ["المسعود", "مسعود"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "غدير": {
        "aliases": ["غدير", "حي غدير"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "أور": {
        "aliases": ["أور", "حي أور"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الزعيم": {
        "aliases": ["الزعيم", "حي الزعيم"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "صدر بغداد": {
        "aliases": ["صدر بغداد", "مدينة الصدر", "مدينة الصدر", "الصدر", "حي الصدر"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "حسينية": {
        "aliases": ["حسينية", "الحسينية", "حي الحسينية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الشعب": {
        "aliases": ["الشعب", "حي الشعب", "شعب"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الفضل": {
        "aliases": ["الفضل", "حي الفضل", "فضل"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "العطيفية": {
        "aliases": ["العطيفية", "عطيفية", "حي العطيفية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "القاهرة": {
        "aliases": ["القاهرة", "حي القاهرة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الأمين": {
        "aliases": ["الأمين", "امين", "حي الأمين"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الكنيسة": {
        "aliases": ["الكنيسة", "حي الكنيسة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الجمهورية": {
        "aliases": ["الجمهورية", "حي الجمهورية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الوثبة": {
        "aliases": ["الوثبة", "حي الوثبة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "العلاوية": {
        "aliases": ["العلاوية", "علاوية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الصالحية": {
        "aliases": ["الصالحية", "صالحية", "حي الصالحية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "العدل": {
        "aliases": ["العدل", "حي العدل"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الإسكان": {
        "aliases": ["الإسكان", "اسكان", "حي الإسكان"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الطوارئ": {
        "aliases": ["الطوارئ", "حي الطوارئ"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "المشاهدة": {
        "aliases": ["المشاهدة", "مشاهدة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "الراشدية": {
        "aliases": ["الراشدية", "راشدية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "سبع أبكار": {
        "aliases": ["سبع أبكار", "سبع ابكار", "7 أبكار"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "القيارة": {
        "aliases": ["القيارة", "قيارة"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "النعيرية": {
        "aliases": ["النعيرية", "نعيرية"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "النهروان": {
        "aliases": ["النهروان", "نهروان"],
        "district": "الرصافة",
        "common_ref": "",
    },
    "معامل الكبريت": {
        "aliases": ["معامل الكبريت"],
        "district": "الرصافة",
        "common_ref": "",
    },

    # ── مناطق شمال بغداد ──
    "التاجي": {
        "aliases": ["التاجي", "تاجي"],
        "district": "شمال بغداد",
        "common_ref": "",
    },
    "الطريمية": {
        "aliases": ["الطريمية", "طريمية"],
        "district": "شمال بغداد",
        "common_ref": "",
    },

    # ── مناطق جنوب بغداد ──
    "المسيب": {
        "aliases": ["المسيب", "مسيب"],
        "district": "جنوب بغداد",
        "common_ref": "",
    },
    "الإسكندرية": {
        "aliases": ["الإسكندرية", "اسكندرية", "اسكندريه"],
        "district": "جنوب بغداد",
        "common_ref": "",
    },
}


def get_all_areas_list() -> list:
    """Return flat list of all area names for UI autocomplete."""
    return sorted(BAGHDAD_AREAS.keys())


def get_areas_by_district(district: str) -> list:
    """Return areas belonging to a specific district."""
    return [name for name, info in BAGHDAD_AREAS.items() if info["district"] == district]


def get_area_info(area_name: str) -> Optional[dict]:
    """Get area info by name or alias. Returns {name, district, common_ref} or None."""
    if not area_name:
        return None
    area_clean = area_name.strip()
    # Direct match
    if area_clean in BAGHDAD_AREAS:
        info = BAGHDAD_AREAS[area_clean]
        return {"name": area_clean, "district": info["district"], "common_ref": info["common_ref"]}
    # Alias match
    area_lower = area_clean.lower()
    for name, info in BAGHDAD_AREAS.items():
        for alias in info["aliases"]:
            if alias.lower() == area_lower:
                return {"name": name, "district": info["district"], "common_ref": info["common_ref"]}
    return None


# ── Zone Matching Engine ─────────────────────────────────────────────────────

def parse_delivery_zones(zones_raw) -> dict:
    """Parse delivery_zones from DB (could be JSON string or dict).
    Returns the zone config dict with a 'zones' list, or empty default."""
    if not zones_raw:
        return {"zones": [], "out_of_range_message": "عذراً، المنطقة خارج نطاق التوصيل 🙏"}
    try:
        if isinstance(zones_raw, str):
            parsed = json.loads(zones_raw)
        else:
            parsed = zones_raw
        # Validate structure
        if not isinstance(parsed, dict) or "zones" not in parsed:
            return {"zones": [], "out_of_range_message": "عذراً، المنطقة خارج نطاق التوصيل 🙏"}
        return parsed
    except (json.JSONDecodeError, TypeError):
        return {"zones": [], "out_of_range_message": "عذراً، المنطقة خارج نطاق التوصيل 🙏"}


def match_area_to_zone(
    customer_text: str,
    zones_config: dict,
) -> Optional[dict]:
    """
    Match customer's address text to a delivery zone.

    Returns:
        {
            "zone_name": str,        # e.g. "قريبة"
            "fee": int,              # e.g. 1000
            "area_name": str,        # e.g. "المنصور"
            "estimated_minutes": int, # e.g. 20
            "district": str,         # e.g. "الكرخ"
        }
        or None if no match found.
    """
    if not customer_text or not zones_config:
        return None

    zones = zones_config.get("zones", [])
    if not zones:
        return None

    text_lower = customer_text.strip().lower()

    # Strategy 1: Direct match against zone areas
    for zone in zones:
        zone_areas = zone.get("areas", [])
        for area in zone_areas:
            area_clean = area.strip()
            # Exact match
            if area_clean.lower() == text_lower:
                area_info = get_area_info(area_clean)
                return {
                    "zone_name": zone.get("name", ""),
                    "fee": int(zone.get("fee", 0)),
                    "area_name": area_clean,
                    "estimated_minutes": int(zone.get("estimated_minutes", 0)),
                    "district": area_info["district"] if area_info else "",
                }
            # Area name is part of customer text (e.g. "المنصور قرب الشرطة")
            if area_clean.lower() in text_lower:
                area_info = get_area_info(area_clean)
                return {
                    "zone_name": zone.get("name", ""),
                    "fee": int(zone.get("fee", 0)),
                    "area_name": area_clean,
                    "estimated_minutes": int(zone.get("estimated_minutes", 0)),
                    "district": area_info["district"] if area_info else "",
                }

    # Strategy 2: Match via BAGHDAD_AREAS aliases, then check if area is in a zone
    for area_name, info in BAGHDAD_AREAS.items():
        for alias in info["aliases"]:
            if alias.lower() in text_lower:
                # Found the area — now check which zone it belongs to
                for zone in zones:
                    zone_areas = zone.get("areas", [])
                    # Check if this area name or any of its aliases is in the zone
                    if area_name in zone_areas or any(a in zone_areas for a in info["aliases"]):
                        return {
                            "zone_name": zone.get("name", ""),
                            "fee": int(zone.get("fee", 0)),
                            "area_name": area_name,
                            "estimated_minutes": int(zone.get("estimated_minutes", 0)),
                            "district": info["district"],
                        }

    # Strategy 3: Fuzzy — check if any zone area is a substring of customer text
    # (handles "قرب مركز شرطة المنصور" → "المنصور")
    for zone in zones:
        zone_areas = zone.get("areas", [])
        for area in zone_areas:
            area_clean = area.strip()
            # Check if area name appears anywhere in customer text
            area_words = area_clean.split()
            if len(area_words) >= 2:
                # Multi-word area: all words must appear
                if all(w.lower() in text_lower for w in area_words):
                    area_info = get_area_info(area_clean)
                    return {
                        "zone_name": zone.get("name", ""),
                        "fee": int(zone.get("fee", 0)),
                        "area_name": area_clean,
                        "estimated_minutes": int(zone.get("estimated_minutes", 0)),
                        "district": area_info["district"] if area_info else "",
                    }

    return None


def is_area_in_range(customer_text: str, zones_config: dict) -> Tuple[bool, Optional[dict]]:
    """
    Check if a customer's mentioned area is within delivery range.

    Returns:
        (in_range: bool, match_info: dict or None)
        - (True, match_info) if area is in a zone
        - (False, None) if area is not in any zone
        - (True, None) if no zones configured (fail-open)
        - (False, None) if area is known in BAGHDAD_AREAS but not in any zone (out of range)
    """
    if not zones_config or not zones_config.get("zones"):
        # No zones configured — fail open, use default delivery_fee
        return True, None

    match = match_area_to_zone(customer_text, zones_config)
    if match:
        return True, match

    # Check if the area is a known Baghdad area but not in any zone
    area_info = get_area_info(customer_text.strip())
    if area_info:
        # Known area but not in any zone → out of range
        return False, None

    # Unknown area — could be anything, fail open (let GPT handle)
    return True, None


def get_delivery_fee_for_area(
    customer_text: str,
    zones_config: dict,
    default_fee: int = 0,
) -> Tuple[int, Optional[str]]:
    """
    Get the delivery fee for a customer's area.

    Returns:
        (fee: int, area_name: str or None)
        - If area matches a zone: returns zone fee and area name
        - If no zones configured: returns default_fee
        - If area not in range: returns -1 and None (caller should reject)
    """
    if not zones_config or not zones_config.get("zones"):
        return default_fee, None

    match = match_area_to_zone(customer_text, zones_config)
    if match:
        return match["fee"], match["area_name"]
    # Area not in any zone
    return -1, None


def get_out_of_range_message(zones_config: dict) -> str:
    """Get the configured out-of-range message, or default."""
    return (zones_config or {}).get(
        "out_of_range_message",
        "عذراً، المنطقة خارج نطاق التوصيل 🙏"
    )


def get_covered_areas_list(zones_config: dict) -> list:
    """Return list of all areas covered by the restaurant's zones."""
    zones = (zones_config or {}).get("zones", [])
    areas = []
    for zone in zones:
        for area in zone.get("areas", []):
            if area not in areas:
                areas.append(area)
    return areas


def _extract_area_from_text(text: str) -> Optional[dict]:
    """
    Extract area name from customer text by scanning all BAGHDAD_AREAS aliases.
    Returns the first matching area info dict, or None.
    Handles cases like:
    - "توصلون المنصور؟" → المنصور
    - "عندكم توصيل لأبو غريب؟" → أبو غريب
    - "أنا بالكرادة" → الكرادة
    - "قرب مركز شرطة المنصور" → المنصور
    """
    text_lower = text.lower()
    # Sort by alias length (longest first) to match "الكرادة الشرقية" before "الكرادة"
    all_aliases = []
    for area_name, info in BAGHDAD_AREAS.items():
        for alias in info["aliases"]:
            all_aliases.append((alias, area_name, info))
    all_aliases.sort(key=lambda x: len(x[0]), reverse=True)

    for alias, area_name, info in all_aliases:
        if alias.lower() in text_lower:
            return {"name": area_name, "district": info["district"], "common_ref": info["common_ref"]}
    return None


def build_zone_reply(customer_text: str, zones_config: dict) -> Optional[str]:
    """
    Build a reply when customer asks about delivery to their area.
    Used when customer says things like:
    - "توصلون المنصور؟"
    - "أنا بالكرادة"
    - "توصيل للدورة؟"

    Returns:
        - Reply string if we can determine the answer
        - None if we can't determine (let GPT handle it)
    """
    if not zones_config or not zones_config.get("zones"):
        return None  # No zones configured, let default logic handle it

    # Delivery inquiry keywords
    _delivery_inquiry = [
        "توصلون", "توصيل", "توصل", "توصّل", "توصلكم",
        "عندكم توصيل", "في توصيل", "فيه توصيل",
        "أنا بـ", "أنا بال", "أنا في", "ساكن بـ",
        "ابي توصيل", "أريد توصيل",
    ]

    is_inquiry = any(kw in customer_text for kw in _delivery_inquiry)

    # First try: match_area_to_zone (handles full text matching)
    in_range, match_info = is_area_in_range(customer_text, zones_config)

    if in_range and match_info:
        fee = match_info["fee"]
        area = match_info["area_name"]
        minutes = match_info.get("estimated_minutes", 0)
        if fee > 0:
            reply = f"أي توصلنا {area} 🌷 — رسوم التوصيل {fee:,} د.ع"
            if minutes:
                reply += f" ويوصل خلال ~{minutes} دقيقة"
            return reply
        else:
            reply = f"أي توصلنا {area} 🌷 — التوصيل مجاني!"
            if minutes:
                reply += f" ويوصل خلال ~{minutes} دقيقة"
            return reply
    elif not in_range:
        # Area is known but out of range — give specific message
        _known_area = _extract_area_from_text(customer_text)
        if _known_area:
            _oor_msg = get_out_of_range_message(zones_config)
            return f"عذراً، ما نوصل {_known_area['name']} 🙏 — {_oor_msg}"
        return get_out_of_range_message(zones_config)

    # in_range=True but no match_info = unknown area, no inquiry keywords
    # Try extracting area from text anyway
    _extracted = _extract_area_from_text(customer_text)
    if _extracted:
        # Check if this area is in a zone
        _zone_match = match_area_to_zone(_extracted["name"], zones_config)
        if _zone_match:
            fee = _zone_match["fee"]
            area = _zone_match["area_name"]
            minutes = _zone_match.get("estimated_minutes", 0)
            if fee > 0:
                reply = f"أي توصلنا {area} 🌷 — رسوم التوصيل {fee:,} د.ع"
                if minutes:
                    reply += f" ويوصل خلال ~{minutes} دقيقة"
                return reply
            else:
                reply = f"أي توصلنا {area} 🌷 — التوصيل مجاني!"
                if minutes:
                    reply += f" ويوصل خلال ~{minutes} دقيقة"
                return reply
        else:
            # Known Baghdad area but not in any zone
            _oor_msg = get_out_of_range_message(zones_config)
            return f"عذراً، ما نوصل {_extracted['name']} 🙏 — {_oor_msg}"

    # Can't determine the area — let GPT handle
    return None


# ── Default zone templates for new restaurants ──────────────────────────────

DEFAULT_ZONE_TEMPLATES = {
    "baghdad_center": {
        "zones": [
            {
                "name": "قريبة",
                "fee": 1000,
                "areas": ["المنصور", "الكرادة", "العيادية", "الجادرية", "بغداد الجديدة"],
                "estimated_minutes": 20,
            },
            {
                "name": "متوسطة",
                "fee": 2000,
                "areas": ["الحارثية", "العامرية", "حرية", "الخضراء", "القادسية", "الربيع", "زيونة", "الأعظمية"],
                "estimated_minutes": 30,
            },
            {
                "name": "بعيدة",
                "fee": 3000,
                "areas": ["الدورة", "البياع", "الشعلة", "الكاظمية", "صدر بغداد", "أبو غريب"],
                "estimated_minutes": 45,
            },
        ],
        "out_of_range_message": "عذراً، المنطقة خارج نطاق التوصيل 🙏 تقدر تستلم من المطعم مباشرة"
    },
    "baghdad_karkh": {
        "zones": [
            {
                "name": "قريبة",
                "fee": 1000,
                "areas": ["المنصور", "الكرادة", "العيادية", "الجادرية", "الحارثية"],
                "estimated_minutes": 15,
            },
            {
                "name": "متوسطة",
                "fee": 2000,
                "areas": ["العامرية", "حرية", "الخضراء", "القادسية", "الربيع", "الغزالية"],
                "estimated_minutes": 30,
            },
            {
                "name": "بعيدة",
                "fee": 3000,
                "areas": ["الدورة", "البياع", "الشعلة", "أبو غريب", "الزعفرانية"],
                "estimated_minutes": 45,
            },
        ],
        "out_of_range_message": "عذراً، نوصّل بالكرخ وبعض مناطق الرصافة 🙏"
    },
    "baghdad_rusafa": {
        "zones": [
            {
                "name": "قريبة",
                "fee": 1000,
                "areas": ["الأعظمية", "زيونة", "غدير", "الكاظمية"],
                "estimated_minutes": 15,
            },
            {
                "name": "متوسطة",
                "fee": 2000,
                "areas": ["الشعب", "حسينية", "البتاويين", "الوزيرية", "بأبي"],
                "estimated_minutes": 30,
            },
            {
                "name": "بعيدة",
                "fee": 3000,
                "areas": ["صدر بغداد", "الراشدية", "النهروان"],
                "estimated_minutes": 45,
            },
        ],
        "out_of_range_message": "عذراً، نوصّل بالرصافة وبعض مناطق الكرخ 🙏"
    },
    "empty": {
        "zones": [],
        "out_of_range_message": "عذراً، المنطقة خارج نطاق التوصيل 🙏"
    },
}
