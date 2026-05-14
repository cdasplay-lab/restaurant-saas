# Reply Test Matrix — Restaurant SaaS Platform

> Last updated: 2026-05-14 | Based on NUMBER 41B codebase scan
> Purpose: Define minimum test coverage for launch-ready reply system

---

## Test Setup (Shared)

```python
MENU = [
    {"id": "1", "name": "برجر لحم",   "price": 8000,  "available": 1},
    {"id": "2", "name": "برجر دجاج",  "price": 7000,  "available": 1},
    {"id": "3", "name": "برجر كلاسيك","price": 7500,  "available": 1},
    {"id": "4", "name": "بيبسي",      "price": 1500,  "available": 1},
    {"id": "5", "name": "بطاطا",      "price": 3000,  "available": 1},
    {"id": "6", "name": "زينجر",      "price": 8500,  "available": 1},
    {"id": "7", "name": "وجبة نافدة", "price": 5000,  "available": 0},
    {"id": "8", "name": "ماء",        "price": 500,   "available": 1, "sold_out_date": "2026-05-10"},
]
```

---

## Section A — Full Order Flows

### T-A1: Full order in one message
- **ID**: T-A1
- **Setup**: Fresh session, delivery restaurant
- **Messages**: "أريد برجر لحم اثنين مع توصيل للكرادة باسم علي رقمي 07901234567 وادفع كاش"
- **Expected**:
  - `session.items` = [برجر لحم × 2]
  - `session.order_type` = "delivery"
  - `session.address` = "الكرادة" (or similar)
  - `session.customer_name` = "علي"
  - `session.phone` = "07901234567"
  - `session.payment_method` = "كاش"
  - `session.is_complete()` = True
  - Reply contains order summary with total
- **Protects Against**: RISK-C4 (items replaced), RISK-H5 (name extraction)
- **Phase**: 41A

---

### T-A2: Multi-message order (one slot per turn)
- **ID**: T-A2
- **Setup**: Fresh session, delivery restaurant
- **Turn 1**: "أريد زينجر" → expect: asks توصيل لو استلام؟
- **Turn 2**: "توصيل" → expect: asks وين عنوان التوصيل؟
- **Turn 3**: "المنصور" → expect: asks شنو اسمك؟
- **Turn 4**: "اسمي سارة" → expect: asks شنو رقم هاتفك؟
- **Turn 5**: "07912345678" → expect: asks كاش لو كي كارد؟
- **Turn 6**: "كاش" → expect: order summary with total, "نثبت؟"
- **Turn 7**: "ثبت" → expect: order confirmation with order number
- **Expected per turn**: Exactly the right next question — no repeats, no skips
- **Protects Against**: RISK-H1 (repeated questions), RISK-C1 (GPT free text)
- **Phase**: 41B

---

### T-A3: Pickup order (address not required)
- **ID**: T-A3
- **Setup**: Fresh session, pickup allowed
- **Turn 1**: "أريد بطاطا واحدة" → asks توصيل لو استلام؟
- **Turn 2**: "استلام" → should NOT ask for address → asks شنو اسمك؟
- **Turn 3**: Fill name, phone, payment → is_complete() = True
- **Expected**: No address question at all
- **Protects Against**: RISK-H1 (address asked for pickup)
- **Phase**: 41A

---

### T-A4: Add item to existing order
- **ID**: T-A4
- **Setup**: Session with [برجر لحم × 1] already
- **Message**: "ضيف كولا"
- **Expected**:
  - `session.items` = [برجر لحم × 1, بيبسي × 1] (alias resolved)
  - Original item NOT dropped
  - Reply confirms both items present
- **Protects Against**: RISK-C4 (items replaced on update)
- **Phase**: 41A

---

## Section B — Alias & Product Matching

### T-B1: Cola → Pepsi alias (tool validation path)
- **ID**: T-B1
- **Setup**: Menu has "بيبسي", no "كولا"
- **Input to validate_tool_items**: `[{"name": "كولا", "qty": 1, "unit_price": 1500}]`
- **Expected**: `validated = [{"name": "بيبسي", "qty": 1, ...}]`, `unknown = []`
- **Protects Against**: RISK-M3 (cola as unknown item)
- **Phase**: 41A

---

### T-B2: Burger alias variants
- **ID**: T-B2
- **Setup**: Menu has "برجر لحم"
- **Inputs to test (each separately)**: "برگر", "بركر", "بيرجر", "بوركر", "بورغر"
- **Expected**: Each resolves to "برجر لحم" (or triggers clarification if multiple burger types)
- **Protects Against**: RISK-H3 (ambiguous burger)
- **Phase**: 41A

---

### T-B3: Fries/Potato alias
- **ID**: T-B3
- **Inputs**: "بطاطس", "بطاطيس", "فرايز", "فريز", "فرايس"
- **Expected**: Each resolves to "بطاطا" (menu product)
- **Protects Against**: RISK-M3
- **Phase**: 41A

---

### T-B4: Ambiguous burger — clarification required
- **ID**: T-B4
- **Setup**: Menu has برجر لحم + برجر دجاج + برجر كلاسيك
- **Message**: "أريد برگر"
- **Expected**:
  - `session.items = []` (no random selection)
  - `session.clarification_needed` is not None
  - `clarification_needed` contains "لحم" and "دجاج"
  - Reply asks customer to specify type
- **Protects Against**: RISK-H3 (random burger selection)
- **Phase**: 41A

---

### T-B5: Specific burger — no clarification
- **ID**: T-B5
- **Setup**: Same as T-B4
- **Message**: "أريد برگر لحم"
- **Expected**:
  - `session.items = [OrderItem(name="برجر لحم", ...)]`
  - `session.clarification_needed = None`
- **Protects Against**: RISK-H3 (over-triggering clarification)
- **Phase**: 41A

---

## Section C — Item Modification

### T-C1: Remove item
- **ID**: T-C1
- **Setup**: Session with [برجر لحم × 1, بيبسي × 1]
- **Message**: "شيل الكولا"
- **Expected**: `session.items = [برجر لحم × 1]`
- **Protects Against**: Item edit bugs
- **Phase**: 41A

---

### T-C2: Decrease item quantity
- **ID**: T-C2
- **Setup**: Session with [برجر لحم × 3]
- **Message**: "خلي البرجر اثنين"
- **Expected**: `session.items = [برجر لحم × 2]`
- **Protects Against**: RISK-C4 partial
- **Phase**: 41A

---

### T-C3: Increase item quantity
- **ID**: T-C3
- **Setup**: Session with [برجر لحم × 1]
- **Message**: "ضيف برجر لحم ثاني"
- **Expected**: `session.items = [برجر لحم × 2]`
- **Protects Against**: Qty tracking bug
- **Phase**: 41A

---

### T-C4: Swap item
- **ID**: T-C4
- **Setup**: Session with [بيبسي × 1]
- **Message**: "بدل الكولا بماء"
- **Expected**: `session.items = [ماء × 1]` (if ماء in menu and not sold out)
- **Phase**: 41A

---

## Section D — Unknown & Sold-Out Items

### T-D1: Unknown item (GPT invention)
- **ID**: T-D1
- **Input to validate_tool_items**: `[{"name": "ساندويچ سري لانكا", "qty": 1, "unit_price": 5000}]`
- **Expected**:
  - `validated = []`
  - `unknown = ["ساندويچ سري لانكا"]`
- **Protects Against**: Invented products
- **Phase**: 41A

---

### T-D2: Sold-out item (available=0)
- **ID**: T-D2
- **Input**: `[{"name": "وجبة نافدة", "qty": 1, "unit_price": 5000}]` with MENU above
- **Expected**:
  - `validated = []`
  - `unknown` contains "[نافد] وجبة نافدة" or similar
- **Protects Against**: RISK-C5
- **Phase**: 41B

---

### T-D3: Sold-out via sold_out_date
- **ID**: T-D3
- **Input**: `[{"name": "ماء", "qty": 1, "unit_price": 500}]` with sold_out_date set
- **Expected**: Item goes to unknown, not validated
- **Protects Against**: RISK-C5
- **Phase**: 41B

---

## Section E — Payment & Phone Extraction

### T-E1: Payment — كاش
- **ID**: T-E1
- **Message**: "ادفع كاش"
- **Expected**: `session.payment_method = "كاش"`
- **Phase**: 41A

---

### T-E2: Payment — ZainCash vs Zinger (critical)
- **ID**: T-E2a
- **Message**: "زينجر واحد"
- **Expected**: `session.payment_method = None` (no payment match)
- **ID**: T-E2b
- **Message**: "زين كاش"
- **Expected**: `session.payment_method = "زين كاش"`
- **ID**: T-E2c
- **Message**: "بدفع زين"
- **Expected**: `session.payment_method = "زين كاش"`
- **Protects Against**: RISK-H2
- **Phase**: 41B

---

### T-E3: Phone extraction
- **ID**: T-E3
- **Messages to test**:
  - "رقمي 07901234567" → phone = "07901234567"
  - "٠٧٩٠١٢٣٤٥٦٧" (Arabic-Indic digits) → phone = "07901234567"
  - "اتصل بي على 07901234567" → phone = "07901234567"
- **Expected**: Correct normalized phone in all cases
- **Protects Against**: Phone not saved (RISK-C2)
- **Phase**: 41B

---

### T-E4: Name extraction stops at first word
- **ID**: T-E4
- **Messages**:
  - "اسمي علي ورقمي 07901234567" → name = "علي"
  - "أنا محمد وعنواني الكرادة" → name = "محمد"
  - "اسمي 07901234567" → name = None (phone-only, not a name)
- **Protects Against**: RISK-H5
- **Phase**: 41B

---

## Section F — Safety Guards

### T-F1: Premature confirmation blocked
- **ID**: T-F1
- **Input to has_premature_confirmation**: "تم تأكيد الطلب، الشباب يجهزون هسه"
- **Expected**: Returns True (blocked)
- **Input**: "توصيل لو استلام؟"
- **Expected**: Returns False (not blocked)
- **Protects Against**: RISK-H6
- **Phase**: 41A

---

### T-F2: Price stripped from update_order reply
- **ID**: T-F2
- **Input to strip_prices_from_reply**: "وصلت 🌷 برجر لحم — 8,000 د.ع وعندك توصيل"
- **Expected**: "وصلت 🌷 برجر لحم — وعندك توصيل" (price removed)
- **Protects Against**: Price leakage mid-conversation
- **Phase**: 41A

---

### T-F3: GPT free text during active order blocked
- **ID**: T-F3
- **Setup**: Session with [زينجر × 1], status=collecting
- **Simulate**: GPT returns "وصلني الفويس 🌷 شنو تريد؟" without triggering tool
- **Expected**: C1 guard overrides reply with next directive (asks order_type or address etc.)
- **Protects Against**: RISK-C1
- **Phase**: 41B

---

## Section G — Context & Memory

### T-G1: Saved fields not asked again
- **ID**: T-G1
- **Setup**: Session already has customer_name="علي", phone="07901234567"
- **Action**: Call `_backend_next_reply(session, products, [])`
- **Expected**: Reply does NOT contain "شنو اسمك" or "رقم هاتفك"
- **Protects Against**: RISK-H1
- **Phase**: 41B

---

### T-G2: No greeting in active order session
- **ID**: T-G2
- **Setup**: Session with items in basket
- **Simulate**: C1 guard fires
- **Expected**: Reply does NOT start with "هلا", "مرحبا", "أهلاً"
- **Protects Against**: Repeated greetings during order
- **Phase**: 42

---

## Section H — Edge Cases & Error Paths

### T-H1: Empty message
- **ID**: T-H1
- **Message**: "" or whitespace only
- **Expected**: No crash; returns generic "شنو تحب تطلب؟" or similar
- **Phase**: 42

---

### T-H2: Customer frustration keywords
- **ID**: T-H2
- **Messages**: "ما تفهم", "غبي", "تعبتني"
- **Expected**: `session.customer_frustrated = True`; reply acknowledges frustration without repeating question
- **Phase**: 41A

---

### T-H3: Repeat last order
- **ID**: T-H3
- **Setup**: Customer has a previous order in DB with [زينجر × 1]
- **Message**: "نفس الطلب السابق"
- **Expected**: `session.items = [زينجر × 1]` (loaded from DB)
- **Protects Against**: RISK-C3 (conn after close)
- **Phase**: 41B

---

### T-H4: Promo code validation
- **ID**: T-H4
- **Setup**: Promo code "SAVE10" exists in DB (10% discount), min_order=5000, active=1
- **Session**: items_total = 8000, promo_code="SAVE10", promo_discount=0
- **Expected**: After place_order block, `promo_discount = 800` (10% of 8000)
- **Protects Against**: RISK-C3 (promo conn), RISK-M7 (promo not rolled back)
- **Phase**: 41B

---

### T-H5: Qty cap at MAX_QTY=20
- **ID**: T-H5
- **Message**: "أريد ١٠٠ برجر لحم"
- **Expected**: `session.items = [برجر لحم × 20]` (capped), `session.qty_capped` not empty
- **Phase**: 41A

---

## Section I — Channel & Voice

### T-I1: Voice message with "[فويس]" prefix
- **ID**: T-I1
- **Message**: "[فويس] أريد برجر لحم"
- **Expected**:
  - Token budget capped at 60
  - Order still extracted correctly
  - No "[فويس]" visible in reply
- **Phase**: 41A

---

### T-I2: Story reply (deterministic path)
- **ID**: T-I2
- **Setup**: Message has `replied_story_id` set
- **Expected**:
  - `_build_deterministic_story_reply()` called
  - `bot.process_message()` NOT called
  - Reply is simple (no order extraction)
- **Phase**: Later

---

### T-I3: Order summary confirmation format
- **ID**: T-I3
- **Setup**: Complete session (all fields set)
- **Action**: Call `order_summary_for_confirmation(delivery_fee=2000)`
- **Expected**:
  - Contains item names with qty
  - Contains total (items + delivery fee)
  - Contains name, phone, address, payment
  - Ends with "نثبت؟" or similar
  - "رسوم التوصيل" shown if fee > 0
- **Phase**: 36C

---

## Test Coverage Summary

| Section | Count | Status |
|---------|-------|--------|
| A — Full order flows | 4 | 3 exist (T-A3 missing) |
| B — Alias & matching | 5 | 3 exist in 41a tests |
| C — Item modification | 4 | 2 exist |
| D — Unknown & sold-out | 3 | 2 exist (T-D2/3 new in 41B) |
| E — Payment & phone | 4 | T-E2 new in 41B |
| F — Safety guards | 3 | 2 exist in tool_safety tests |
| G — Context & memory | 2 | T-G1 new in 41B |
| H — Edge cases | 5 | T-H3/H4 new in 41B |
| I — Channel & voice | 3 | 0 exist |
| **Total** | **33** | **~20 existing, 13 needed** |

---

## Priority — Next Tests to Write (NUMBER 42)

1. **T-A2** — Multi-message order (6-turn slot progression)
2. **T-I3** — Order summary format (complete confirmation)
3. **T-G2** — No greeting during active order
4. **T-H2** — Customer frustration handling
5. **T-I1** — Voice order extraction
6. **T-I2** — Story reply deterministic path
7. **T-H4** — Promo code end-to-end
8. **T-H1** — Empty/whitespace message
