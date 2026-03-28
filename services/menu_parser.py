"""
Menu Import Parser — extracts products from menu files using OpenAI Vision.

Supported formats:
  Images : .jpg .jpeg .png .gif .webp  → OpenAI Vision (gpt-4o)
  PDF    : .pdf (multi-page)           → PyMuPDF renders pages → Vision
  Word   : .docx                       → python-docx text → GPT-4o
  Excel  : .xlsx .xls                  → openpyxl → GPT-4o
  CSV    : .csv                         → direct text → GPT-4o
  Text   : .txt                         → direct text → GPT-4o

Confidence scoring:
  1.0  = perfectly clear entry
  0.7+ = high confidence, ready to import
  0.5–0.7 = medium — flagged for review
  <0.5 = low — flagged, user must approve individually
"""

import os
import json
import uuid
import base64
import logging
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("menu-parser")

# Items with confidence below this threshold are flagged needs_review=True
REVIEW_THRESHOLD = 0.70

SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SUPPORTED_DOCS   = {".pdf", ".docx", ".txt", ".csv", ".xlsx", ".xls"}
ALL_SUPPORTED    = SUPPORTED_IMAGES | SUPPORTED_DOCS


# ── OpenAI client ──────────────────────────────────────────────────────────────

def _get_client():
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY غير مضبوط. أضفه في ملف .env")
    import openai
    return openai.OpenAI(api_key=key)


# ── Image → base64 ────────────────────────────────────────────────────────────

def _img_b64(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}
    mime = f"image/{mime_map.get(ext, 'jpeg')}"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


# ── Prompts ────────────────────────────────────────────────────────────────────

_VISION_PROMPT = """You are a professional restaurant menu parser. Carefully analyze this menu image.

Return ONLY a valid JSON array — no markdown, no explanation outside the array.

Each object in the array must have EXACTLY these fields:
{
  "name": "product name — keep original language (Arabic or English or both)",
  "category": "section / category name — keep original language",
  "price": number or null,
  "description": "short description or empty string",
  "variants": [{"name": "size or option", "price": null}],
  "confidence": 0.0–1.0,
  "needs_review": true or false,
  "source_note": "any parsing issue, or empty string"
}

STRICT RULES — violating these is unacceptable:
1. NEVER invent, guess, or estimate a price. If price text is unclear or missing → price: null.
2. NEVER invent product names. Only extract what is clearly visible.
3. confidence = 1.0 means 100% certain. Lower it for any ambiguity.
4. Set needs_review = true if: price is null, text is partially obscured, or meaning is ambiguous.
5. Preserve original Arabic and/or English text exactly as written.
6. If sizes/options exist (صغير/وسط/كبير or S/M/L), add them as variants.
7. price field is a number only — NO currency symbols.
8. Skip decorative headers or non-product lines.
9. If you see the same item mentioned multiple times, include it once.
10. Return the JSON array ONLY."""

_TEXT_PROMPT = """You are a professional restaurant menu parser. Extract all menu items from this text.

Return ONLY a valid JSON array — no markdown, no explanation outside the array.

Each object must have EXACTLY these fields:
{
  "name": "product name",
  "category": "section or category",
  "price": number or null,
  "description": "description or empty string",
  "variants": [{"name": "option name", "price": null}],
  "confidence": 0.0–1.0,
  "needs_review": true or false,
  "source_note": ""
}

Rules:
- DO NOT invent prices or names
- confidence = 1.0 for clearly structured data, lower for ambiguous entries
- If price is missing or unclear → price: null, needs_review: true
- Preserve original Arabic/English text
- Return ONLY the JSON array"""


# ── OpenAI calls ───────────────────────────────────────────────────────────────

def _vision_model() -> str:
    """Vision requires gpt-4o or gpt-4o-mini — force vision-capable model."""
    m = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    # Allow gpt-4o family; anything else falls back to gpt-4o-mini
    if m.startswith("gpt-4o"):
        return m
    return "gpt-4o-mini"


def _call_vision(image_paths: List[str], client) -> List[Dict]:
    """Send up to 4 images to gpt-4o Vision and extract menu items."""
    content = []
    for path in image_paths[:4]:
        try:
            content.append({
                "type": "image_url",
                "image_url": {"url": _img_b64(path), "detail": "high"}
            })
        except Exception as e:
            logger.warning(f"Image encode failed {path}: {e}")

    if not content:
        logger.warning("No images to send to Vision API")
        return []

    content.append({"type": "text", "text": _VISION_PROMPT})
    model = _vision_model()
    logger.info(f"Calling Vision API with model={model}, images={len(content)-1}")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=4000,
            temperature=0,
        )
        raw_text = resp.choices[0].message.content
        logger.info(f"Vision API response length={len(raw_text)}, preview={raw_text[:100]}")
        return _parse_json_response(raw_text)
    except Exception as e:
        err_str = str(e)
        logger.error(f"Vision API error (model={model}): {err_str}")
        if "insufficient_quota" in err_str or "429" in err_str:
            raise RuntimeError("OpenAI quota exceeded — أضف billing على https://platform.openai.com/account/billing")
        raise


def _call_text(text: str, client) -> List[Dict]:
    """Send text (up to 12k chars) to gpt-4o and extract menu items."""
    text = text[:12000].strip()
    if not text:
        return []
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    logger.info(f"Calling Text API with model={model}, text_len={len(text)}")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": f"{_TEXT_PROMPT}\n\nMenu text:\n{text}"}],
            max_tokens=4000,
            temperature=0,
        )
        raw_text = resp.choices[0].message.content
        logger.info(f"Text API response preview={raw_text[:100]}")
        return _parse_json_response(raw_text)
    except Exception as e:
        err_str = str(e)
        logger.error(f"Text API error: {err_str}")
        if "insufficient_quota" in err_str or "429" in err_str:
            raise RuntimeError("OpenAI quota exceeded — أضف billing على https://platform.openai.com/account/billing")
        raise


def _parse_json_response(raw: str) -> List[Dict]:
    """Strip markdown fences and parse JSON array from OpenAI response."""
    raw = raw.strip()
    # Strip ```json ... ``` fences
    if raw.startswith("```"):
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("[") or part.startswith("{"):
                raw = part
                break
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e} | raw[:200]={raw[:200]}")
        return []


# ── File type parsers ──────────────────────────────────────────────────────────

def _parse_pdf(path: str, client) -> List[Dict]:
    """Render each PDF page to a PNG image and extract via Vision."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not installed — trying text extraction fallback")
        # Fallback: extract embedded text from PDF
        try:
            import fitz
        except Exception:
            return _error_item("PyMuPDF غير مثبت. نفّذ: pip install PyMuPDF")

    doc = fitz.open(path)
    all_items: List[Dict] = []
    batch: List[str] = []
    tmp_files: List[str] = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            mat  = fitz.Matrix(2.0, 2.0)   # 2× zoom for better OCR quality
            pix  = page.get_pixmap(matrix=mat)
            tmp  = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            pix.save(tmp.name)
            tmp.close()
            tmp_files.append(tmp.name)
            batch.append(tmp.name)

            # Process in batches of 2 pages to stay within token limits
            if len(batch) >= 2 or page_num == len(doc) - 1:
                items = _call_vision(batch, client)
                for item in items:
                    item.setdefault("source_page", page_num + 1)
                all_items.extend(items)
                for f in batch:
                    try: os.unlink(f)
                    except Exception: pass
                batch = []
    finally:
        doc.close()
        for f in tmp_files:
            try: os.unlink(f)
            except Exception: pass

    return all_items


def _parse_docx(path: str, client) -> List[Dict]:
    """Extract text + tables from .docx and parse with GPT-4o."""
    try:
        from docx import Document
        doc  = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        for table in doc.tables:
            for row in table.rows:
                text += "\n" + " | ".join(cell.text.strip() for cell in row.cells)
        return _call_text(text, client)
    except ImportError:
        return _error_item("python-docx غير مثبت. نفّذ: pip install python-docx")
    except Exception as e:
        logger.error(f"DOCX error: {e}")
        return []


def _parse_spreadsheet(path: str, client) -> List[Dict]:
    """Parse .xlsx/.xls or .csv.
    - If the file has recognised column headers (name/price/category/...) → direct mapping, no OpenAI.
    - Otherwise → convert to text and call GPT-4o.
    """
    ext = Path(path).suffix.lower()
    rows: List[List[str]] = []
    try:
        if ext == ".csv":
            import csv
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                rows = list(csv.reader(f))
        else:
            try:
                import openpyxl
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                rows = [[str(c.value if c.value is not None else "").strip() for c in row]
                        for row in ws.iter_rows()]
            except ImportError:
                logger.warning("openpyxl not installed — falling back to text mode")
                with open(path, "r", errors="ignore") as f:
                    return _call_text(f.read(), client)
    except Exception as e:
        logger.error(f"Spreadsheet read error: {e}")
        return []

    if not rows:
        return []

    # ── Try direct column mapping first ───────────────────────────────────────
    items = _spreadsheet_direct(rows)
    if items:
        logger.info(f"Spreadsheet direct parse: {len(items)} items (no OpenAI needed)")
        return items

    # ── Fallback: unrecognised structure → send to OpenAI ─────────────────────
    logger.info("Spreadsheet: no recognised headers — falling back to OpenAI text parse")
    text = "\n".join(" | ".join(r) for r in rows[:500])
    return _call_text(text, client)


# ── Arabic + English column header synonyms ───────────────────────────────────
_COL_NAME     = {"name", "اسم", "الاسم", "product", "item", "المنتج", "product_name", "اسم المنتج"}
_COL_CATEGORY = {"category", "cat", "section", "قسم", "فئة", "القسم", "التصنيف", "الفئة", "group"}
_COL_PRICE    = {"price", "سعر", "السعر", "cost", "amount", "الثمن", "ثمن", "قيمة"}
_COL_DESC     = {"description", "desc", "وصف", "الوصف", "details", "تفاصيل", "ملاحظات"}
_COL_ICON     = {"icon", "emoji", "أيقونة", "رمز"}
_COL_AVAIL    = {"available", "متاح", "active", "نشط", "enabled"}


def _spreadsheet_direct(rows: List[List[str]]) -> List[Dict]:
    """
    Map spreadsheet rows to product dicts using column headers.
    Returns [] if no recognised 'name' column is found.
    """
    if len(rows) < 2:
        return []

    # Find header row (first non-empty row)
    header_idx = 0
    for i, row in enumerate(rows):
        if any(c.strip() for c in row):
            header_idx = i
            break

    headers = [h.strip().lower() for h in rows[header_idx]]

    # Map each header to a field key
    col_map: Dict[str, int] = {}  # field_key → col_index
    for idx, h in enumerate(headers):
        if h in _COL_NAME:
            col_map.setdefault("name", idx)
        elif h in _COL_CATEGORY:
            col_map.setdefault("category", idx)
        elif h in _COL_PRICE:
            col_map.setdefault("price", idx)
        elif h in _COL_DESC:
            col_map.setdefault("description", idx)
        elif h in _COL_ICON:
            col_map.setdefault("icon", idx)
        elif h in _COL_AVAIL:
            col_map.setdefault("available", idx)

    if "name" not in col_map:
        return []  # no recognised structure

    items: List[Dict] = []
    for row in rows[header_idx + 1:]:
        if not row or not any(c.strip() for c in row):
            continue  # skip blank rows

        def _cell(key: str, default: str = "") -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return default
            return str(row[idx]).strip()

        name = _cell("name")
        if not name or len(name) < 2:
            continue

        # Parse price
        price_raw = _cell("price")
        price: Optional[float] = None
        if price_raw:
            try:
                price = float(price_raw.replace(",", ".").replace(" ", "").replace("ر.س", "").replace("SAR", "").strip())
                if price < 0:
                    price = None
            except ValueError:
                price = None

        conf = 1.0 if price is not None else 0.55
        needs_review = price is None

        items.append({
            "name":         name,
            "category":     _cell("category") or "عام",
            "price":        price,
            "description":  _cell("description"),
            "variants":     [],
            "confidence":   conf,
            "needs_review": needs_review,
            "source_note":  "" if price is not None else "السعر مفقود",
        })

    return items


def _error_item(msg: str) -> List[Dict]:
    return [{
        "name": "خطأ في القراءة", "category": "", "price": None,
        "description": msg, "variants": [], "confidence": 0.0,
        "needs_review": True, "source_note": msg
    }]


# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize(raw_items: List[Dict], session_id: str) -> List[Dict]:
    """
    Normalize raw OpenAI output:
    - Add temp_id, session_id, action
    - Coerce types, cap confidence
    - Flag items needing review
    - Deduplicate by name (keep highest confidence)
    """
    by_name: Dict[str, Dict] = {}  # name_lower → item

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        name = str(raw.get("name") or "").strip()
        if not name or len(name) < 2:
            continue

        # Price coercion
        price = raw.get("price")
        if price is not None:
            try:
                price = float(str(price).replace(",", ".").replace(" ", ""))
                price = price if price >= 0 else None
            except (ValueError, TypeError):
                price = None

        # Confidence coercion
        try:
            conf = float(raw.get("confidence", 0.8))
            conf = max(0.0, min(1.0, conf))
        except (ValueError, TypeError):
            conf = 0.5

        # Lower confidence when price is missing
        if price is None:
            conf = min(conf, 0.55)

        needs_review = bool(raw.get("needs_review")) or conf < REVIEW_THRESHOLD or price is None

        # Normalize variants
        variants = raw.get("variants") or []
        clean_v = []
        for v in (variants if isinstance(variants, list) else []):
            if isinstance(v, dict) and str(v.get("name", "")).strip():
                vp = v.get("price")
                try:
                    vp = float(vp) if vp is not None else None
                except (ValueError, TypeError):
                    vp = None
                clean_v.append({"name": str(v["name"]).strip(), "price": vp})

        item = {
            "temp_id":      str(uuid.uuid4()),
            "session_id":   session_id,
            "name":         name,
            "category":     str(raw.get("category") or "عام").strip() or "عام",
            "price":        price,
            "description":  str(raw.get("description") or "").strip(),
            "variants":     clean_v,
            "confidence":   round(conf, 2),
            "needs_review": needs_review,
            "source_note":  str(raw.get("source_note") or "").strip(),
            "source_page":  int(raw.get("source_page") or 1),
            "action":       "create",   # create | skip | update_existing
            "existing_match": None,
            "image_url":    "",
        }

        name_key = name.lower()
        if name_key not in by_name or conf > by_name[name_key]["confidence"]:
            by_name[name_key] = item

    return list(by_name.values())


# ── Duplicate detection ────────────────────────────────────────────────────────

def _name_similarity(a: str, b: str) -> float:
    """Simple overlap similarity between two strings."""
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if len(a) > 3 and (a in b or b in a):
        return 0.90
    # Bigram overlap
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s) - 1))
    ab, bb = bigrams(a), bigrams(b)
    if not ab or not bb:
        return 0.0
    return 2 * len(ab & bb) / (len(ab) + len(bb))


def detect_duplicates(items: List[Dict], restaurant_id: str) -> List[Dict]:
    """
    For each extracted item, check against existing products in the DB.
    Sets item["existing_match"] and item["action"] = "merge_review" when
    a likely duplicate (similarity ≥ 0.80) is found.
    """
    import database
    conn = database.get_db()
    try:
        existing = conn.execute(
            "SELECT id, name, category, price FROM products WHERE restaurant_id=?",
            (restaurant_id,)
        ).fetchall()
    finally:
        conn.close()

    if not existing:
        return items

    ex_list = [dict(e) for e in existing]

    for item in items:
        best_score  = 0.0
        best_match  = None
        for ex in ex_list:
            score = _name_similarity(item["name"], ex["name"])
            if score > best_score:
                best_score = score
                best_match = ex

        if best_score >= 0.80 and best_match:
            item["existing_match"] = {
                "id":         best_match["id"],
                "name":       best_match["name"],
                "price":      best_match["price"],
                "similarity": round(best_score, 2),
            }
            item["action"] = "merge_review"  # user must decide

    return items


# ── Main entry points ─────────────────────────────────────────────────────────

def parse_files(file_paths: List[str], file_names: List[str], session_id: str) -> List[Dict]:
    """
    Parse one or more menu files. Images are batched together (up to 4 per call)
    for efficiency. Non-image files are processed individually.

    Returns a normalized, deduplicated list of menu items.
    """
    client = _get_client()

    image_batches: List[List[str]] = []
    cur_batch: List[str] = []
    non_image_items: List[Dict] = []

    for path, name in zip(file_paths, file_names):
        ext = Path(name).suffix.lower()
        if ext in SUPPORTED_IMAGES:
            cur_batch.append(path)
            if len(cur_batch) >= 4:
                image_batches.append(cur_batch)
                cur_batch = []
        else:
            # Process non-image file — quota errors propagate up
            raw = _dispatch_non_image(path, name, client)
            non_image_items.extend(raw)

    if cur_batch:
        image_batches.append(cur_batch)

    # Call Vision for each batch of images
    image_raw: List[Dict] = []
    for batch in image_batches:
        image_raw.extend(_call_vision(batch, client))

    all_raw = image_raw + non_image_items
    logger.info(f"session={session_id}: {len(all_raw)} raw items from {len(file_paths)} files")

    return _normalize(all_raw, session_id)


def _dispatch_non_image(path: str, name: str, client) -> List[Dict]:
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(path, client)
    elif ext == ".docx":
        return _parse_docx(path, client)
    elif ext in {".xlsx", ".xls", ".csv"}:
        return _parse_spreadsheet(path, client)
    elif ext == ".txt":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return _call_text(f.read(), client)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
