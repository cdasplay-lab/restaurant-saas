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


# ── Arabic + English column header synonyms ───────────────────────────────────
# Keys are normalised (lowercase, no spaces/underscores/dashes) — matched via _norm_header()
_COL_NAME     = {
    "name", "productname", "itemname", "product", "item", "title",
    "اسم", "الاسم", "المنتج", "اسمالمنتج", "اسمالصنف", "الصنف",
    "الوجبة", "اسمالوجبة", "الطبق", "اسمالطبق",
}
_COL_CATEGORY = {
    "category", "cat", "section", "group", "type",
    "قسم", "فئة", "القسم", "الفئة", "التصنيف", "النوع", "المجموعة",
}
_COL_PRICE    = {
    "price", "cost", "amount", "rate", "value", "unitprice",
    "سعر", "السعر", "الثمن", "ثمن", "قيمة", "التكلفة", "سعرالوحدة",
}
_COL_DESC     = {
    "description", "desc", "details", "notes", "note",
    "وصف", "الوصف", "تفاصيل", "ملاحظات", "ملاحظة",
}
_COL_ICON     = {"icon", "emoji", "أيقونة", "رمز", "صورة"}
_COL_AVAIL    = {"available", "active", "enabled", "status", "متاح", "نشط", "الحالة"}

# Normalised row-name patterns that indicate non-product rows (total/footer/etc.)
_SKIP_ROW_NAMES = {
    "total", "subtotal", "grandtotal", "discount", "tax", "vat",
    "note", "notes", "remark", "sum",
    "المجموع", "الإجمالي", "مجموع", "إجمالي", "ملاحظة", "ملاحظات",
    "خصم", "ضريبة",
}


def _norm_header(h: str) -> str:
    """Normalise a cell for column matching: strip BOM/control chars, lowercase,
    remove spaces / underscores / dashes / dots."""
    import unicodedata
    h = h.replace('\ufeff', '').replace('\xa0', ' ')
    h = ''.join(c for c in h if not unicodedata.category(c).startswith('C'))
    h = h.strip().lower()
    for sep in (' ', '_', '-', '.', '/'):
        h = h.replace(sep, '')
    return h


def _header_to_field(hn: str) -> Optional[str]:
    """Map a normalised header string to a field key, or None if unrecognised."""
    pairs = (
        (_COL_NAME,     "name"),
        (_COL_CATEGORY, "category"),
        (_COL_PRICE,    "price"),
        (_COL_DESC,     "description"),
        (_COL_ICON,     "icon"),
        (_COL_AVAIL,    "available"),
    )
    # Exact match first
    for col_set, field in pairs:
        if hn in col_set:
            return field
    # Substring / partial match for compound column names (e.g. "productname" ↔ "name")
    for col_set, field in pairs[:4]:
        for kw in col_set:
            if len(kw) >= 3 and len(hn) >= 3 and (kw in hn or hn in kw):
                return field
    return None


# ── Low-level file readers ─────────────────────────────────────────────────────

def _read_csv(path: str) -> List[List[str]]:
    """Read CSV with auto-encoding detection and delimiter sniffing."""
    import csv, io as _io

    raw: Optional[str] = None
    used_enc = "utf-8"
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                raw = fh.read()
            used_enc = enc
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if raw is None:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        used_enc = "utf-8(lossy)"

    # Sniff delimiter from first 4 KB
    snippet = raw[:4096]
    best_delim, best_score = ",", 0.0
    for d in (",", ";", "\t", "|"):
        reader = csv.reader(_io.StringIO(snippet), delimiter=d)
        sample = [r for _, r in zip(range(8), reader)]
        widths = [len(r) for r in sample if r]
        if not widths or max(widths) <= 1:
            continue
        mean_w = sum(widths) / len(widths)
        variance = sum((w - mean_w) ** 2 for w in widths) / len(widths)
        score = mean_w / (variance ** 0.5 + 1.0)
        if score > best_score:
            best_score = score
            best_delim = d

    rows = list(csv.reader(_io.StringIO(raw), delimiter=best_delim))
    logger.info(f"[csv] enc={used_enc} delim={best_delim!r} rows={len(rows)}")
    return [[c.strip() for c in r] for r in rows]


def _read_xlsx(path: str) -> List[List[List[str]]]:
    """Read all non-empty sheets from .xlsx; return list-of-sheets, each a list-of-rows."""
    import openpyxl
    wb  = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: List[List[List[str]]] = []
    for ws in wb.worksheets:
        rows: List[List[str]] = []
        for row in ws.iter_rows():
            cells = [str(c.value if c.value is not None else "").strip() for c in row]
            rows.append(cells)
        while rows and not any(c for c in rows[-1]):
            rows.pop()
        if rows:
            out.append(rows)
            logger.info(f"[xlsx] sheet='{ws.title}' rows={len(rows)}")
    wb.close()
    return out


def _read_xls(path: str) -> List[List[List[str]]]:
    """Read all sheets from legacy .xls (uses xlrd if installed, else openpyxl fallback)."""
    try:
        import xlrd
    except ImportError:
        logger.warning("[xls] xlrd not installed — trying openpyxl (may fail for old .xls format)")
        return _read_xlsx(path)

    wb  = xlrd.open_workbook(path)
    out: List[List[List[str]]] = []
    for ws in wb.sheets():
        rows: List[List[str]] = []
        for ri in range(ws.nrows):
            row: List[str] = []
            for ci in range(ws.ncols):
                ctype = ws.cell_type(ri, ci)
                val   = ws.cell_value(ri, ci)
                if ctype == xlrd.XL_CELL_NUMBER:
                    row.append(str(int(val)) if float(val) == int(val) else str(val))
                else:
                    row.append(str(val).strip())
            rows.append(row)
        while rows and not any(c for c in rows[-1]):
            rows.pop()
        if rows:
            out.append(rows)
            logger.info(f"[xls] sheet='{ws.name}' rows={len(rows)}")
    return out


def _parse_spreadsheet(path: str, client, file_name: str = "") -> List[Dict]:
    """Parse .xlsx / .xls / .csv → product list.
    • Reads ALL sheets (multi-sheet Excel supported).
    • Tries direct column mapping on each sheet (no OpenAI cost).
    • Falls back to OpenAI text parse if no recognised headers found anywhere.
    """
    ext = Path(file_name).suffix.lower() if file_name else Path(path).suffix.lower()
    logger.info(f"[spreadsheet] START file='{file_name or path}' ext='{ext}'")

    try:
        if ext == ".csv":
            sheets: List[List[List[str]]] = [_read_csv(path)]
        elif ext == ".xlsx":
            sheets = _read_xlsx(path)
        elif ext == ".xls":
            sheets = _read_xls(path)
        else:
            try:
                sheets = _read_xlsx(path)
            except Exception:
                sheets = [_read_csv(path)]
    except Exception as e:
        logger.error(f"[spreadsheet] read error: {e}", exc_info=True)
        return []

    sheets = [s for s in sheets if s]
    if not sheets:
        logger.error("[spreadsheet] no data read from file")
        return []

    total_rows = sum(len(s) for s in sheets)
    logger.info(f"[spreadsheet] {len(sheets)} sheet(s) loaded, {total_rows} total rows")

    all_items: List[Dict] = []
    for idx, rows in enumerate(sheets):
        label = f"sheet{idx + 1}"
        items = _spreadsheet_direct(rows, sheet_label=label)
        if items:
            all_items.extend(items)

    if all_items:
        logger.info(f"[spreadsheet] direct parse total: {len(all_items)} items (no OpenAI)")
        return all_items

    logger.info("[spreadsheet] no recognised headers in any sheet — falling back to OpenAI")
    text_parts: List[str] = []
    for idx, rows in enumerate(sheets):
        if len(sheets) > 1:
            text_parts.append(f"=== Sheet {idx + 1} ===")
        text_parts.extend(" | ".join(r) for r in rows[:300])
    text = "\n".join(text_parts)[:12000]
    return _call_text(text, client)


# ── Direct spreadsheet → product mapping ──────────────────────────────────────

def _spreadsheet_direct(rows: List[List[str]], sheet_label: str = "") -> List[Dict]:
    """Map rows to products via column headers.

    1. Scan first 15 rows for the best header row (must include a 'name' column).
    2. If no header found, use a no-header heuristic for 2–4 column sheets.
    3. Delegate actual row conversion to _extract_data_rows.
    """
    prefix = f"[direct/{sheet_label}]" if sheet_label else "[direct]"
    if not rows:
        return []

    # ── Find best header row ───────────────────────────────────────────────────
    best_idx     = -1
    best_col_map: Dict[str, int] = {}

    for i, row in enumerate(rows[:15]):
        if not any(c.strip() for c in row):
            continue
        col_map: Dict[str, int] = {}
        for ci, cell in enumerate(row):
            field = _header_to_field(_norm_header(cell))
            if field and field not in col_map:
                col_map[field] = ci
        if "name" in col_map and len(col_map) >= len(best_col_map):
            best_col_map = col_map
            best_idx = i

    logger.info(f"{prefix} header_idx={best_idx} col_map={best_col_map}")

    if best_idx >= 0:
        return _extract_data_rows(rows, best_idx + 1, best_col_map, prefix, rows[best_idx])

    # ── No-header heuristic ────────────────────────────────────────────────────
    # Works for simple sheets: Name|Price or Name|Category|Price
    sample = [r for r in rows[:20] if any(c.strip() for c in r)]
    if not sample:
        return []

    col_widths = [len([c for c in r if c.strip()]) for r in sample]
    min_w, max_w = min(col_widths), max(col_widths)

    if min_w < 2 or max_w > 6:
        logger.warning(f"{prefix} no recognised headers, sheet width {min_w}–{max_w} cols is inconclusive")
        return []

    # Detect which column is most likely the price (highest fraction of numeric values)
    _ea = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    price_hits = [0] * (max_w + 1)
    for r in sample:
        for ci, val in enumerate(r[:max_w + 1]):
            cleaned = val.translate(_ea).replace(",", ".").replace(" ", "")
            for sym in ("ر.س", "ريال", "SAR", "$", "€"):
                cleaned = cleaned.replace(sym, "")
            try:
                float(cleaned)
                price_hits[ci] += 1
            except ValueError:
                pass

    price_ci = max(range(len(price_hits)), key=lambda i: price_hits[i]) if max(price_hits) > 0 else -1
    name_ci  = 1 if price_ci == 0 else 0
    cat_ci: Optional[int] = None
    if max_w >= 3:
        rem = [i for i in range(max_w) if i not in (name_ci, price_ci)]
        if rem:
            cat_ci = rem[0]

    heuristic_map: Dict[str, int] = {"name": name_ci}
    if price_ci >= 0:
        heuristic_map["price"] = price_ci
    if cat_ci is not None:
        heuristic_map["category"] = cat_ci

    logger.info(f"{prefix} no-header heuristic col_map={heuristic_map}")
    return _extract_data_rows(rows, 0, heuristic_map, prefix, header_row=None)


def _extract_data_rows(
    rows: List[List[str]],
    start_idx: int,
    col_map: Dict[str, int],
    prefix: str,
    header_row: Optional[List[str]],
) -> List[Dict]:
    """Convert spreadsheet data rows to product dicts, skipping blanks/totals/repeated headers."""
    items:   List[Dict] = []
    skipped: int = 0

    # Normalised header cells, used to detect repeated header rows in data
    header_norm: set = set()
    if header_row:
        for c in header_row:
            hn = _norm_header(c)
            if hn:
                header_norm.add(hn)

    _ea_trans = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

    for row in rows[start_idx:]:
        if not row or not any(c.strip() for c in row):
            skipped += 1
            continue

        def _cell(key: str, default: str = "", _cm=col_map, _r=row) -> str:
            i = _cm.get(key)
            if i is None or i >= len(_r):
                return default
            return str(_r[i]).strip()

        name = _cell("name")
        if not name or len(name) < 2:
            skipped += 1
            continue

        # Skip repeated header rows
        if header_norm and _norm_header(name) in header_norm:
            skipped += 1
            continue

        # Skip total / footer / metadata rows
        if _norm_header(name) in _SKIP_ROW_NAMES:
            skipped += 1
            continue

        # Parse price — handles Eastern Arabic numerals, comma-thousands, currency symbols
        price_raw = _cell("price")
        price: Optional[float] = None
        if price_raw:
            cleaned = price_raw.translate(_ea_trans).replace(",", ".").replace(" ", "")
            for sym in ("ر.س", "ريال", "SAR", "$", "€", "£", "﷼"):
                cleaned = cleaned.replace(sym, "")
            try:
                val = float(cleaned.strip())
                price = val if val >= 0 else None
            except ValueError:
                price = None

        conf         = 1.0 if price is not None else 0.55
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

    logger.info(f"{prefix} extracted={len(items)} skipped={skipped}")
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

    result = list(by_name.values())
    logger.info(f"_normalize: input={len(raw_items)} raw → output={len(result)} unique items")
    return result


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
    logger.info(f"[dispatch] file='{name}' ext='{ext}' path='{path}'")
    if ext == ".pdf":
        return _parse_pdf(path, client)
    elif ext == ".docx":
        return _parse_docx(path, client)
    elif ext in {".xlsx", ".xls", ".csv"}:
        logger.info(f"[dispatch] → _parse_spreadsheet (direct column mapping, no OpenAI)")
        return _parse_spreadsheet(path, client, file_name=name)
    elif ext == ".txt":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return _call_text(f.read(), client)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
