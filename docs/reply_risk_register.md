# Reply Risk Register — Restaurant SaaS Platform

> Last updated: 2026-05-14 | Based on NUMBER 41B audit
> Status key: Open / Fixed / Needs-Test / Postponed

---

## CRITICAL Risks

### RISK-C1 — GPT Free Text During Active Order
- **Area**: Core reply flow
- **File/Function**: bot.py `process_message` → post OpenAI call (~line 1601)
- **Example**: Customer has برجر in basket, says "بسرعة". GPT replies "وصلني الفويس 🌷 شنو تريد؟"
- **Bad Behavior**: Bot forgets active order, asks customer to start over
- **Root Cause**: If GPT doesn't trigger `update_order` tool, free text bypasses OrderBrain entirely
- **Severity**: Critical
- **Fix**: Force `_backend_next_reply()` when `_ob_session.is_active()` and no tool triggered
- **Files**: `services/bot.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B C1, bot.py:1601)

---

### RISK-C2 — Phone Never Saved via Tool
- **Area**: Order slot collection
- **File/Function**: `_ORDER_TOOLS` schema, `_populate_ob_session_from_tool()`
- **Example**: GPT calls `place_order` with all fields but no phone. Session has no phone. Confirmation sent without customer contact.
- **Bad Behavior**: Order confirmed with no phone number — restaurant can't call customer
- **Root Cause**: `phone` field was missing from both `place_order` and `update_order` tool schemas
- **Severity**: Critical
- **Fix**: Add `phone` as required field to `place_order`, optional to `update_order`
- **Files**: `services/bot.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B C2)

---

### RISK-C3 — Closed DB Connection Used
- **Area**: Data integrity
- **File/Function**: bot.py `process_message` line ~956 (repeat order), ~1679 (promo code)
- **Example**: Customer says "نفس الطلب السابق" → bot crashes silently; repeat order not loaded
- **Bad Behavior**: Repeat order not loaded. Promo code validation silently fails.
- **Root Cause**: `conn.close()` called at line 925. Later code at lines 956 and 1679 still uses closed `conn`
- **Severity**: Critical
- **Fix**: Open fresh connections (`_rep_conn`, `_promo_conn`) for each late DB access
- **Files**: `services/bot.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B C3)

---

### RISK-C4 — Items Replaced Instead of Merged
- **Area**: Order editing
- **File/Function**: bot.py `_populate_ob_session_from_tool()` line ~1465
- **Example**: Customer orders "برجر + كولا". Later says "اضيف بطاطا". GPT calls `update_order` with only `[بطاطا]`. Burger and cola are wiped.
- **Bad Behavior**: Previous items silently dropped
- **Root Cause**: `_ob_session.items = [new items]` replaced entire list
- **Severity**: Critical
- **Fix**: Merge: keep existing items not in new validated set, append new ones
- **Files**: `services/bot.py`
- **Phase**: 41A
- **Status**: Fixed (NUMBER 41A, verified in 41B)

---

### RISK-C5 — Sold-Out Items Pass Tool Validation
- **Area**: Order accuracy
- **File/Function**: `tool_safety.validate_tool_items()`
- **Example**: Menu has "برجر خاص — SOLD OUT". Customer orders it. GPT calls `place_order` with that item. Order confirmed for an unavailable product.
- **Bad Behavior**: Order created for sold-out item; restaurant can't fulfill
- **Root Cause**: `validate_tool_items()` didn't check `available=0` or `sold_out_date`
- **Severity**: Critical
- **Fix**: Check `available` and `sold_out_date` before validating item; move to unknown list if sold out
- **Files**: `services/tool_safety.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B C5)

---

## HIGH Risks

### RISK-H1 — SlotTracker Overrides OrderBrain in System Prompt
- **Area**: Context injection
- **File/Function**: bot.py line 1268-1283
- **Example**: Customer gave phone, address, name. OrderBrain has them. SlotTracker misses address (regex didn't match format). System prompt says "no address yet" → GPT asks for address again.
- **Bad Behavior**: Repeated question for already-provided info
- **Root Cause**: SlotTracker was used as primary slot context even when OrderBrain had authoritative data
- **Severity**: High
- **Fix**: Prefer `_ob_session.to_prompt_section()` when session exists
- **Files**: `services/bot.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B H1)

---

### RISK-H2 — "زين" Matches "زينجر" in Payment Detection
- **Area**: Slot extraction
- **File/Function**: `SlotTracker._parse()` bot.py:~318, also `order_brain.py` PAYMENT_MAP
- **Example**: Customer orders "زينجر". Bot sets `payment = "زين كاش"` from SlotTracker.
- **Bad Behavior**: Wrong payment method saved; customer confused when asked for payment
- **Root Cause**: Simple substring check `if "زين" in text` matches inside product names
- **Severity**: High
- **Fix**: Word-boundary regex: `re.search(r'(?<![؀-ۿ])زين(?![؀-ۿ])', text)`
- **Files**: `services/bot.py` (SlotTracker), `services/order_brain.py` (PAYMENT_MAP — already fixed with word-boundary)
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B H2)

---

### RISK-H3 — Ambiguous Burger Added Before Clarification
- **Area**: Item matching
- **File/Function**: `order_brain._extract_items()` fuzzy fallback line ~1077
- **Example**: Customer says "أريد برگر". Menu has برجر لحم + برجر دجاج. `_fuzzy_product_match` returns first match (برجر لحم). Clarification flag set but item already in basket.
- **Bad Behavior**: Wrong item silently added; clarification question misleads customer
- **Root Cause**: Fuzzy fallback ran after ambiguity removal, re-adding the item via `_PRODUCT_ALIASES` match
- **Severity**: High
- **Fix**: After ambiguity removal, add `_ambig_ids` to `matched_ids` to block fuzzy fallback
- **Files**: `services/order_brain.py`
- **Phase**: 41A
- **Status**: Fixed (NUMBER 41B bonus fix)

---

### RISK-H4 — NUMBER 31 Appends to Tool-Generated Replies
- **Area**: Reply quality
- **File/Function**: bot.py NUMBER 31 block line ~1578
- **Example**: `update_order` tool fires, `_backend_next_reply` asks "توصيل لو استلام؟". NUMBER 31 appends another directive to the same reply.
- **Bad Behavior**: Double question, confusing reply
- **Root Cause**: NUMBER 31 condition didn't check `_tool_call_data["triggered"]`
- **Severity**: High
- **Fix**: Add `not _tool_call_data["triggered"]` to NUMBER 31 condition
- **Files**: `services/bot.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B M2)

---

### RISK-H5 — Name Extraction Captures Conjunction Words
- **Area**: Slot extraction
- **File/Function**: `order_brain._extract_name()` line ~1163
- **Example**: Customer says "اسمي علي ورقمي 07901234567". `[؀-ۿ]+` pattern captures "علي" but pattern `r'اسمي\s+([؀-ۿ]+)'` could theoretically capture "علي" + conjunction if no space boundary.
- **Bad Behavior**: `customer_name = "علي ورقمي"` saved to DB
- **Root Cause**: Pattern used `[؀-ۿ]+` without explicit stop at whitespace
- **Severity**: High
- **Fix**: Use `[^\s،؟?]{2,15}` to stop at whitespace/punctuation
- **Files**: `services/order_brain.py`
- **Phase**: 41B
- **Status**: Fixed (NUMBER 41B H5)

---

### RISK-H6 — Premature Confirmation Not Caught in All Cases
- **Area**: Order flow integrity
- **File/Function**: `tool_safety.has_premature_confirmation()`, `_PREMATURE_CONFIRM_PHRASES`
- **Example**: GPT replies "طلبك جاهز للتوصيل 🌷" (not in phrase list). Passes guard. Customer thinks order confirmed before `place_order` tool fires.
- **Bad Behavior**: Customer believes order is placed; restaurant has no record
- **Root Cause**: Phrase list is finite; GPT can generate synonyms not in list
- **Severity**: High
- **Fix**: Add more phrases; also gate on `_tool_call_data["triggered"]` (if not triggered, no confirmation possible)
- **Files**: `services/tool_safety.py`
- **Phase**: 42
- **Status**: Open

---

### RISK-H7 — Regex Order Extraction Runs After C1 Override
- **Area**: Conflicting reply paths
- **File/Function**: bot.py line 1613-1621 (regex extraction) vs line 1601 (C1 guard)
- **Example**: C1 forces next directive. Then regex extraction finds "برگر" in message, sets `extracted_order`. Two order paths active simultaneously.
- **Bad Behavior**: Duplicate order data; possible double-order on confirmation
- **Root Cause**: C1 guard overrides `reply_text` but doesn't set `_tool_call_data["triggered"] = True`, so regex extraction still runs
- **Severity**: High
- **Fix**: When C1 guard fires, set a flag to skip regex extraction
- **Files**: `services/bot.py`
- **Phase**: 42
- **Status**: Open

---

## MEDIUM Risks

### RISK-M1 — Session TTL Expires Mid-Conversation
- **Area**: State persistence
- **File/Function**: `order_brain.OrderSession` TTL = 7200s (2h), `_ob_save_state()` to DB
- **Example**: Customer starts order, leaves for 2+ hours, comes back. Session cleared. Bot says "هلا شنو تطلب؟" while customer expects to continue.
- **Bad Behavior**: Customer must restart order
- **Root Cause**: In-memory TTL + DB state not always restored correctly if conv record stale
- **Severity**: Medium
- **Fix**: Extend TTL to 12h, add user-facing message "طلبك انتهت مدته — ابدأ طلب جديد؟"
- **Files**: `services/order_brain.py`
- **Phase**: 42
- **Status**: Open

---

### RISK-M2 — NUMBER 31 Dead Code When C1 Active
- **Area**: Code quality / maintenance
- **File/Function**: bot.py NUMBER 31 block line 1578
- **Example**: N/A — both blocks exist but C1 always overrides first
- **Bad Behavior**: Confusing codebase; NUMBER 31 may fire in edge cases where C1 doesn't (e.g. no OrderBrain)
- **Root Cause**: C1 condition: `is_active()` (has items + collecting). NUMBER 31 condition: `has_items()`. If session has items but status != collecting, NUMBER 31 can still fire.
- **Severity**: Medium
- **Fix**: Remove NUMBER 31 entirely or narrow its scope to SlotTracker fallback only
- **Files**: `services/bot.py`
- **Phase**: 42
- **Status**: Open

---

### RISK-M3 — Cola/Pepsi Alias Works in Normalize but Not PAYMENT_MAP
- **Area**: Alias matching
- **File/Function**: `arabic_normalize.py` `_PRODUCT_ALIASES_NORMALIZED`
- **Example**: Menu has "بيبسي". Customer says "كولا". `find_product_by_alias` works. But if GPT sends `update_order` with `name="كولا"`, `validate_tool_items` may or may not catch the alias depending on normalize tier.
- **Bad Behavior**: Cola becomes unknown item; customer sees "ما لقيت «كولا» بالمنيو"
- **Root Cause**: Two alias systems: `_PRODUCT_ALIASES` in order_brain.py and `_PRODUCT_ALIASES_NORMALIZED` in arabic_normalize.py. Tool validation uses arabic_normalize path (tier 0), which should work — but only if product name in DB contains "بيبسي".
- **Severity**: Medium
- **Fix**: Ensure alias test coverage exists for cola→pepsi in `validate_tool_items` path
- **Files**: `services/arabic_normalize.py`, `services/tool_safety.py`
- **Phase**: 42
- **Status**: Needs-Test

---

### RISK-M4 — Double Order on Webhook Retry
- **Area**: Idempotency
- **File/Function**: webhooks.py + main.py order insert
- **Example**: Network timeout causes platform to retry webhook. Second message arrives. Conversation lock (per asyncio, not per request) may not guard DB insert.
- **Bad Behavior**: Customer gets two orders created
- **Root Cause**: No idempotency key on webhook processing; `message_id` dedup only applies to messages table
- **Severity**: Medium
- **Fix**: Check `IF NOT EXISTS` on order insert using conversation_id + approximate timestamp
- **Files**: `services/webhooks.py`, `main.py`
- **Phase**: 42
- **Status**: Open

---

### RISK-M5 — Token Budget Too Low Causes Truncated Reply
- **Area**: Reply quality
- **File/Function**: bot.py `_intent_fast` → `_INTENT_MAX_TOKENS`, urgent mood reduces by 40%
- **Example**: Customer says urgent "بسرعة عندي ضيوف". Bot in urgent mode, budget = 60 tokens. Order summary requires 150 tokens. Reply truncated mid-sentence.
- **Bad Behavior**: Incomplete reply; looks broken
- **Root Cause**: Urgent mood reduces token budget to 60% of intent budget
- **Severity**: Medium
- **Fix**: Set minimum floor: `max(min_floor, int(budget * 0.6))` where `min_floor = 80`
- **Files**: `services/bot.py`
- **Phase**: 42
- **Status**: Open

---

### RISK-M6 — Customer Name Asked Again After Memory Save
- **Area**: Memory / repeated questions
- **File/Function**: bot.py memory injection in `_build_system_prompt()`, `_FIELD_QUESTION["customer_name"]`
- **Example**: Customer ordered 3 times before. Name is in memory. OrderBrain has no name yet (fresh session). System prompt shows name in memory section but `_FIELD_QUESTION["customer_name"]` still fires.
- **Bad Behavior**: "شنو اسمك؟" asked to a returning customer
- **Root Cause**: OrderBrain checks `session.customer_name is None`, but memory has the name. No pre-fill from memory.
- **Severity**: Medium
- **Fix**: In `_ob_save_state` or session restore, pre-fill `customer_name` / `phone` / `address` from memory if not set
- **Files**: `services/bot.py`, `services/order_brain.py`
- **Phase**: 42
- **Status**: Open

---

### RISK-M7 — Promo Code Increments Even If Order Fails
- **Area**: Data integrity
- **File/Function**: bot.py promo code block line ~1693, `_promo_conn.execute("UPDATE promo_codes SET uses_count=uses_count+1")`
- **Example**: Promo code validated and incremented. Then `place_order` DB insert fails. `uses_count` already incremented but order not created.
- **Bad Behavior**: Promo code use wasted; customer loses discount
- **Root Cause**: Promo increment not inside same DB transaction as order insert
- **Severity**: Medium
- **Fix**: Wrap promo increment + order insert in single transaction
- **Files**: `services/bot.py`
- **Phase**: 42
- **Status**: Open

---

## LOW Risks

### RISK-L1 — SlotTracker Still Running When OrderBrain Active (CPU waste)
- **Area**: Performance
- **File/Function**: bot.py line 1268 `SlotTracker().ingest()`
- **Example**: Every message runs SlotTracker regex even though result discarded when OrderBrain active
- **Bad Behavior**: Wasted CPU; negligible latency
- **Root Cause**: SlotTracker always instantiated and run
- **Severity**: Low
- **Fix**: `if _ob_session is None: _slot_tracker = SlotTracker().ingest(...)`
- **Phase**: Later
- **Status**: Postponed

---

### RISK-L2 — Quantity Cap (20) Not Validated in Tool Path
- **Area**: Order data
- **File/Function**: `validate_tool_items()` in tool_safety.py
- **Example**: GPT sends `update_order` with `qty=99`. Gets validated as 99 in session. No cap applied.
- **Root Cause**: `MAX_QTY = 20` enforced in OrderBrain `_extract_items` but not in `validate_tool_items`
- **Severity**: Low
- **Fix**: Add `qty = min(qty, MAX_QTY)` in `validate_tool_items`
- **Files**: `services/tool_safety.py`
- **Phase**: Later
- **Status**: Open

---

### RISK-L3 — Weak Iraqi Arabic Tone in Safety Fallbacks
- **Area**: Customer experience
- **File/Function**: `tool_safety.validate_update_order_reply()` fallback messages
- **Example**: Unknown item triggers: "ما لقيت «X» بالمنيو 🌷 — تكدر تشوف المنيو وتكلني شنو بالضبط تريد؟"
- **Bad Behavior**: Reply is OK but generic; not personalized for Iraqi dialect
- **Root Cause**: Hard-coded strings in tool_safety.py
- **Severity**: Low
- **Fix**: Move fallback strings to configurable system constant with Iraqi-dialect variants
- **Phase**: Later
- **Status**: Postponed

---

### RISK-L4 — Story Reply Bypasses OrderBrain Entirely
- **Area**: Multi-channel order flow
- **File/Function**: webhooks.py:1545-1557 `_build_deterministic_story_reply()`
- **Example**: Customer reacts to story with "أريد". Bot sends generic story reply. OrderBrain session not updated. Customer must repeat themselves.
- **Bad Behavior**: Order intent from story reactions lost
- **Root Cause**: Story path returns before `bot.process_message()` is called
- **Severity**: Low
- **Fix**: Pass story reactions through bot.process_message with story context flag
- **Phase**: Later
- **Status**: Postponed

---

### RISK-L5 — Voice Token Cap May Cut Off Order Summary
- **Area**: Voice orders
- **File/Function**: bot.py line 1327 `if customer_message.startswith("[فويس]"): max_tokens = min(max_tokens, 60)`
- **Example**: Customer voice-orders "برجر لحم وكولا وبطاطا مع توصيل لعنوان المنصور وادفع كاش". Bot needs 120 tokens for confirmation summary. Gets 60. Reply cut.
- **Root Cause**: 60-token cap is too aggressive for complex voice orders
- **Severity**: Low
- **Fix**: Raise voice cap to 100 tokens; or only cap for short voice messages
- **Phase**: Later
- **Status**: Open

---

## Risk Summary by Status

| Status | Count |
|--------|-------|
| Fixed | 8 |
| Open | 9 |
| Needs-Test | 1 |
| Postponed | 4 |

## Risk Summary by Severity

| Severity | Total | Fixed | Open |
|----------|-------|-------|------|
| Critical | 5 | 5 | 0 |
| High | 7 | 4 | 3 |
| Medium | 7 | 0 | 7 |
| Low | 5 | 0 | 5 |
