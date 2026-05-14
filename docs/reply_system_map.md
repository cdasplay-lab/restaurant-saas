# Reply System Map — Restaurant SaaS Platform

> Last updated: 2026-05-14 | Based on NUMBER 41B codebase scan
> Purpose: Reference for future Claude/dev runs — read this BEFORE scanning bot.py

---

## Overview

Customer message travels through **4 layers** before a reply is sent:

```
Webhook → _handle_incoming_message → bot.process_message → _send_reply
```

Each layer has early-exit paths that bypass the next layer entirely.

---

## Step-by-Step Flow

### LAYER 1 — Channel Adapter (webhooks.py)

| Step | Name | File:Line | What it does | Risks |
|------|------|-----------|-------------|-------|
| 1 | Channel webhook received | main.py:9637/9674/9729/9766 | POST routes for Telegram/WhatsApp/Instagram/Facebook. Adds to BackgroundTasks so HTTP response returns immediately | Late processing if task queue backs up |
| 2 | Rate limiter | main.py (route level) | 30 messages/min per sender IP | Legit high-volume customers can be throttled |
| 3 | Parse message | webhooks.py:87/831/964/1184 | Extract chat_id, text, sender name per platform | Platform-specific bugs (e.g. WhatsApp nested JSON) |
| 4 | Conversation lock | webhooks.py:1491-1500 | Per-conversation asyncio lock prevents double replies from concurrent messages | Lock leak if exception mid-processing |
| 5 | **EARLY EXIT: Voice error** | webhooks.py:1502-1531 | If voice transcription failed → send fallback "ما وضحت الرسالة" and return | Bypasses OrderBrain entirely |
| 6 | **EARLY EXIT: Story reply** | webhooks.py:1545-1557 | If message is a story reaction → `_build_deterministic_story_reply()` and return | Bypasses OrderBrain entirely |
| 7 | Bot input prep | webhooks.py:1576-1587 | Wraps voice as `[فويس]`, adds story context prefix | `[فويس]` prefix caps tokens at 60 in bot.py |
| 8 | **Call bot.process_message** | webhooks.py:1591 | Main AI processing. Returns `{"reply", "action", "extracted_order", "media"}` | All reply logic lives here |
| 9 | Save bot reply to DB | webhooks.py:1601-1606 | INSERT INTO messages (role=bot) | If crash before this step, reply is sent but not logged |
| 10 | Escalation check | webhooks.py:1614-1637 | If action==escalate → set conv to "human" mode, notify owner | No fallback if notification fails |
| 11 | **Send reply to customer** | webhooks.py:1642 `_send_reply()` | SINGLE dispatch point for all text replies | Network failure = lost reply, no retry |
| 12 | Send media/images | webhooks.py:1644-1652 | If `result["media"]` present, sends each image separately | Images sent after text; race condition possible on slow connections |
| 13 | Log outbound | webhooks.py:1666-1677 | Write to `outbound_messages` table | |

**Platform dispatch inside `_send_reply` (webhooks.py:1750):**
- Telegram → `_send_telegram()` → POST to `api.telegram.org` (httpx)
- WhatsApp → `_send_whatsapp()` → POST to WhatsApp Cloud API v19.0
- Instagram/Facebook → `_send_facebook_messenger()` → POST to Graph API v19.0

---

### LAYER 2 — bot.process_message Pre-Processing (bot.py:792)

All steps here are **deterministic** — no GPT call. Each is an early-exit if triggered.

| Step | Name | File:Line | What it does | Risks |
|------|------|-----------|-------------|-------|
| 14 | Load context | bot.py:801-928 | Load conv, customer, bot_config, settings, products, history (last 15 msgs), memory, shift_commands, corrections, knowledge, exception_playbook | Single DB connection opened, closed at line 925 — anything using `conn` after line 925 is a bug (C3) |
| 15 | Restore OrderBrain session | bot.py:937-1008 | `OrderBrain.get_or_create()` then restore from DB JSON state if fresh. Then `update_from_message()` | Session expired (TTL 7200s) = fresh start mid-order |
| 16 | Sold-out / payment validation | bot.py:970-1000 | Check invalid payment method, set `_ob_soldout_reply` / `_ob_invalid_pm_reply` overrides | These override GPT reply later — highest priority |
| 17 | Order edit flow | bot.py:1009-1044 | Detects "شيل/غيّر/بدّل" — deterministic item removal/swap, returns early | Returns before GPT call — bypasses OpenAI entirely |
| 18 | Escalation detection | bot.py:1046-1086 | Arabic keyword match + complaint triggers → returns escalate action | Bypasses OpenAI |
| 19 | Bot turn limit | bot.py:1088-1097 | If turns ≥ max_bot_turns (default 15) → escalate | |
| 20 | Menu image intent | bot.py:1099-1122 | Detects "المنيو/صور" → returns images list, no text reply | Bypasses OpenAI |
| 21 | Exception playbook | bot.py:1133-1138 | Hard-coded trigger→reply pairs from owner config | Bypasses OpenAI |
| 22 | Owner corrections | bot.py:1140-1160 | DB lookup: if trigger text in message → return exact correction | Bypasses OpenAI; uses separate `_corr_conn` |
| 23 | FAQ cache | bot.py:1162-1168 | `_faq_reply()` — answers hours/location/prices without GPT | Bypasses OpenAI |
| 24 | Price lookup | bot.py:1170-1200 | "بكم X؟" → direct menu lookup | Bypasses OpenAI |
| 25 | Product disambiguation | bot.py:1202-1216 | If ambiguous product → ask clarification without GPT | Bypasses OpenAI |
| 26 | Budget suggestion | bot.py:1218-1246 | "عندي X دينار" → suggest fitting items | Bypasses OpenAI |
| 27 | Off-hours guard | bot.py:1248-1264 | If closed + order intent → block with "المطعم مسكّر هسه" | Bypasses OpenAI |

---

### LAYER 3 — GPT Call & Tool Handling (bot.py:1266-1557)

| Step | Name | File:Line | What it does | Risks |
|------|------|-----------|-------------|-------|
| 28 | Build slot context | bot.py:1268-1283 | If `_ob_session` present → `to_prompt_section()`. Else → `SlotTracker.known_slots_section()` | SlotTracker is regex-only; can miss fields OrderBrain has |
| 29 | Compress history | bot.py:1272 | Keep last 6 messages, summarize older | Summarizer may drop critical context |
| 30 | Detect mood | bot.py:1275 | `_detect_mood()` → urgent/enthusiastic/cold | Urgent mood reduces max_tokens by 40% |
| 31 | Build system prompt | bot.py:1278-1297 | `_build_system_prompt()` — injects menu, hours, memory, slot_context, corrections, knowledge, mood, shift commands | Large prompt; expensive for small restaurants |
| 32 | Set token budget | bot.py:1309-1319 | Intent-aware budget (220 default, 60 for voice) | Low budget → reply truncated |
| 33 | **Call OpenAI** | bot.py:1370 `_call_openai()` | `gpt-4o-mini`, `tool_choice=auto`, temperature 0.3. Returns text or `"__FC_ORDER__"` | Network failure → exception, returns error reply |
| 34 | Detect tool call | bot.py:1380-1406 | If GPT called `place_order` or `update_order` → store in `_tool_call_data` | If GPT hallucinated tool args → silent validation failure |
| 35 | Validate GPT reply | bot.py:1424-1431 | Algorithm 6: `_validate_reply()` — checks banned phrases, formal openers, double questions | Only runs if no tool triggered |
| 36 | Retry if critical | bot.py:1436-1440 | One retry on critical issues with reduced token budget | Retry can also trigger tool call |
| 37 | Elite reply pass | bot.py:1423-1434 | `elite_reply_pass()` from reply_brain.py — tone/phrase cleanup | Only runs if no tool triggered |
| 38 | Handle `update_order` | bot.py:1541-1557 | `_populate_ob_session_from_tool()` + `_backend_next_reply()` | Unknown items → clarification reply |
| 39 | Handle `place_order` | bot.py:1508-1538 | Populate session + finalize confirmation | Unknown items → block and ask clarification |

---

### LAYER 4 — Reply Post-Processing (bot.py:1558-1810)

| Step | Name | File:Line | What it does | Risks |
|------|------|-----------|-------------|-------|
| 40 | Payment override | bot.py:1558-1560 | If invalid payment method detected earlier → override reply | |
| 41 | Sold-out override | bot.py:1561-1563 | If sold-out items detected → override reply | |
| 42 | **NUMBER 31 Persona** | bot.py:1578-1599 | If active order + reply ≤100 chars + no question mark + no tool triggered → append next directive | Risk: appends to good replies if ≤100 chars |
| 43 | **NUMBER 41B C1 Guard** | bot.py:1601-1617 | If active order + no tool triggered → FORCE `_backend_next_reply()` (overrides GPT entirely) | Aggressive — may override legitimate side replies |
| 44 | Regex order extraction | bot.py:1613-1621 | If no tool and order keywords → keyword regex extraction (legacy) | Conflicts with OrderBrain if both fire |
| 45 | Auto-detect confirmation | bot.py:1623-1626 | Parse "✅" summary blocks from reply | |
| 46 | Fallback template | bot.py:1628-1644 | If `repeated_confirmation` intent + no ✅ + no OrderBrain → SlotTracker summary | Edge case only |
| 47 | Update customer memory | bot.py:1646-1648 | Extract name/preferences from conversation | |
| 48 | OrderBrain confirmation | bot.py:1651-1770 | If `confirmation_status == "confirmed"` → validate promo, compute total, insert DB order, clear session | Uses fresh `_promo_conn` (C3 fix) |
| 49 | Delivery time injection | bot.py:1773-1777 | Append delivery time to ✅ confirmation if missing | |
| 50 | Quality metrics log | bot.py:1779-1808 | Log to `ai_quality_logs` table (fire-and-forget) | |

---

## Voice / Story / Image Special Paths

```
Voice message:
  → webhooks.py: transcribe via Whisper → wrap as "[فويس] <text>"
  → bot.py: if starts with "[فويس]" → max_tokens capped at 60
  → Error case: webhooks.py:1502-1531 early exit with fallback

Story reaction:
  → webhooks.py:1545-1557 early exit
  → _build_deterministic_story_reply() → hardcoded reply
  → Never reaches bot.process_message

Menu images:
  → bot.py:1099-1122 early exit with media list
  → webhooks.py:1644-1652 sends images after text reply
```

---

## Known Duplicate / Conflicting Reply Paths

| Conflict | Location | Description |
|----------|----------|-------------|
| NUMBER 31 vs C1 Guard | bot.py:1578 vs 1601 | Both fire for active order + no tool. C1 comes after 31, so C1 wins. 31 is effectively dead code when C1 is active. |
| SlotTracker vs OrderBrain slot_context | bot.py:1268-1283 | Both produce slot context. OrderBrain preferred since 41B H1 fix. SlotTracker still runs (wasted CPU) even when OrderBrain active. |
| Regex extraction vs tool call | bot.py:1613-1621 | Regex only skipped if `_tool_call_data["triggered"]`. But C1 guard overrides GPT reply without setting triggered=True. Old regex could still run. |
| Fallback template vs OrderBrain | bot.py:1628-1644 | Fallback only fires if `_ob_session is None`. Safe but redundant path. |
| Legacy order extraction vs place_order tool | bot.py:1623-1690 | `_parse_confirmed_order()` parses ✅ from reply AND OrderBrain writes confirmed order. Double-write risk if both fire. |

---

## Where Each Key Decision is Made

| Decision | Location |
|----------|----------|
| Reply language (Arabic) | system prompt in `_build_system_prompt()` |
| Next question to ask | `_backend_next_reply()` → `ob.next_missing_field()` → `_FIELD_QUESTION` |
| Order confirmed to DB | bot.py:~1700 `place_order` handler |
| Session persisted | `_ob_save_state()` → `conversations.order_brain_state` JSON |
| Session cleared | `OrderBrain.clear_session()` after confirmed order |
| Customer memory saved | `_update_memory_from_conversation()` |
| Escalation triggered | `_detect_escalation()` or complaint keywords |

---

## Files That Affect Reply Quality

| File | Role |
|------|------|
| `services/bot.py` | Core orchestrator — all reply logic |
| `services/order_brain.py` | Order state machine + slot extraction |
| `services/tool_safety.py` | Tool arg validation + price/confirm guards |
| `services/arabic_normalize.py` | Alias matching (برگر→برجر, كولا→بيبسي) |
| `services/reply_brain.py` | Elite tone pass + intent detection |
| `services/webhooks.py` | Channel dispatch + voice/story paths |
| `database.py` | Conn management (SQLite↔PostgreSQL) |
