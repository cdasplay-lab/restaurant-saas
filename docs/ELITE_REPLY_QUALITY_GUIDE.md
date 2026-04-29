# Elite Reply Quality Guide — NUMBER 20

## What is the Elite Reply Engine?

An additive quality layer that runs **after** Algorithm 6 in `bot.py`. It never blocks order flow, never raises exceptions, and always returns a valid reply string. Feature flag: `ELITE_REPLY_ENGINE=true` (default).

---

## Files

| File | Role |
|------|------|
| `services/reply_brain.py` | Intent detection, context builder, main entry `elite_reply_pass()` |
| `services/reply_quality.py` | Extended quality gate, banned phrases, tone checks |
| `services/reply_templates.py` | Iraqi Arabic template library with variable substitution |

---

## Tone Rules

The bot must sound like a **friendly Iraqi restaurant employee**, not a corporate call center or a ChatGPT assistant.

### Always ✅
- Short, warm, direct Iraqi dialect
- Emoji: `🌷` only (one, at end of sentence)
- Acknowledge complaints with empathy, then act
- Ask at most **one question** per reply

### Never ❌
- Corporate openers: `بالتأكيد`, `بالطبع`, `بكل سرور`, `من دواعي سروري`, `بكل ترحيب`
- Formal filler: `يرجى تزويدي`, `كيف يمكنني مساعدتك`, `يسعدني مساعدتك`
- AI/system exposure: `تم تحليل الصورة`, `حسب قاعدة البيانات`, `النظام يشير`
- Upsell during complaints: never suggest products when a customer is upset

---

## Before / After Examples

### Greeting

| Before (bad) | After (elite) |
|---|---|
| بالتأكيد! أنا هنا لمساعدتك. هل تريد معرفة المنيو؟ | هلا بيك 🌷 شتحب أرتبلك؟ |
| من دواعي سروري خدمتك! كيف يمكنني مساعدتك؟ | هلا وغلا، تريد تشوف المنيو لو تطلب مباشرة؟ |

### Complaint

| Before (bad) | After (elite) |
|---|---|
| نعتذر عن الإزعاج! سيتم تحويل طلبك للقسم المختص. بالمناسبة عندنا عرض! | آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه. |
| بالتأكيد سنعالج المشكلة. هل ترغب في إضافة مشروب؟ | حقك علينا 🌷 أحولك لموظف هسه. |

### Voice/Image Message

| Before (bad) | After (elite) |
|---|---|
| تم تحويل الصوت إلى نص. طلبت زينگر. | وصلتني 🌷 تريد زينگر؟ |
| تم تحليل الصورة وإليك المعلومات. | شايف الصورة 🌷 تريد تطلب؟ |

### Order Confirmation

| Before (bad) | After (elite) |
|---|---|
| تم استلام طلبك بنجاح. يرجى الانتظار. | ✅ طلبك ثبت 🌷 |
| عزيزي العميل، طلبك قيد المعالجة. | حاضر 🌷 |

---

## Banned Phrases (Elite Gate)

In addition to Algorithm 6's list, the elite gate removes:

**Corporate filler:**
`بالتأكيد` · `بالطبع` · `بكل سرور` · `من دواعي سروري` · `بكل ترحيب` · `بكل تأكيد` · `لا تتردد في التواصل`

**Formal openers:**
`يرجى تزويدي` · `كيف يمكنني مساعدتك` · `يسعدني مساعدتك` · `عزيزي العميل` · `عميلنا العزيز` · `يرجى الانتظار` · `شكراً لاختيارك`

**AI/system exposure:**
`تم تحليل الصورة` · `تم تحويل الصوت إلى نص` · `حسب قاعدة البيانات` · `حسب السجل` · `النظام يشير` · `وفقاً للبيانات`

**Complaint upsell triggers (auto-removed in complaint context):**
`بالمناسبة` · `تريد تضيف` · `تحب تضيف` · `أضيفلك` · `عرض` · `تجرب` · `فرصة لا تفوتك`

---

## Media Message Rules

Webhook tags identify media type. The bot must **not expose AI processing**:

| Tag | Intent | Correct reply style |
|-----|--------|---------------------|
| `[فويس]` | voice_order | Respond to the order as if you heard it naturally |
| `[صورة-شكوى]` | image_complaint | Acknowledge the complaint, ask for order number |
| `[صورة-منيو]` | image_menu | Confirm the item and ask for delivery details |
| `[صورة]` | image_product | Ask if they want to order |
| `[ستوري]` | story_reply | Engage warmly, offer to order |

---

## Complaint Rules

1. **No upsell** — ever, in any complaint context
2. **Empathy first** — acknowledge before asking for info
3. **One action** — either escalate or ask for order number, not both
4. **Angry complaint** → always offer human handoff

---

## Template System

Templates are used for **simple intents** that don't need factual data (greeting, thanks, emoji, human handoff). They are **never** used for:
- Replies containing order summaries (`✅ طلبك`, `المجموع`, `د.ع`)
- Factual intents: `price_question`, `menu_request`, `voice_order`, `memory_same_order`

Variable substitution: `{item}`, `{price}`, `{name}`, `{address}`, `{menu}`, `{last_order}`

---

## Quality Score

`quality_score(reply, ctx)` returns a dict with `score` (0-100), `is_acceptable`, `issues`, and `intent`. Each issue deducts 10 points. Used for logging/review hooks.

---

## Test Results

Script: `scripts/day20_elite_reply_brain_check.py`

| Category | Scenarios | Pass |
|----------|-----------|------|
| A — Greetings | 70 | 70 ✅ |
| B — Menu/Price | 80 | 80 ✅ |
| C — Order slots | 111 | 111 ✅ |
| D — Confirm/Cancel/Modify | 80 | 80 ✅ |
| E — Complaints | 100 | 100 ✅ |
| F — Story/Reel | 79 | 79 ✅ |
| G — Image | 60 | 60 ✅ |
| H — Voice | 50 | 50 ✅ |
| I — Memory | 50 | 50 ✅ |
| J — Stability | 50 | 48 (2 unfixable*) |
| K — Blocked subscription | 60 | 60 ✅ |
| T — Templates | 59 | 59 ✅ |
| **TOTAL** | **849** | **847 (99.8%)** |

*J04/J05: test expects duplicate order detection via history context, impossible from message text alone.

**Critical safety checks: 0 failures.**

---

## NUMBER 20C Fixes (applied to `reply_quality.py`)

| Fix | Description |
|-----|-------------|
| Orphaned leading punctuation | After phrase strip, `_clean_leading_punctuation()` removes leading `! . ، — :` |
| Broken sentence drop | If first sentence is a broken fragment and remainder ≥10 chars, drop first sentence |
| Broken-start detector | 19 patterns for `وهي/وتحتوي/الصورة/في معرفة/بناءً/وفقاً...` → triggers template |
| Context-aware min length | Complaint intents: ≥12 chars; media/order intents: ≥8 chars |
| Multi-question in order flow | `STRICT_ONE_QUESTION_INTENTS` now includes `direct_order`, `voice_order`, `story_reply` etc. |
| Best-question stripping | Strips leading `و/ف/أ` conjunctions from selected question |
| Factual memory preservation | `"آخر طلب"`, `"طلبك السابق"` added to never-replace markers |
| 20 new banned phrases | `وفقاً للسجلات`, `بناءً على سجلاتنا`, `استقبلنا رسالتك الصوتية`, `تم معالجة طلبك الصوتي`, `من خلال الصورة`, `حسب الصورة`, `يسرنا`, `تم رصد`, `تم تحديد`, `تم التعرف على` and 10 more |

**NUMBER 20C result:** 155 scenarios, avg 8.5/10, **0 rejected**, 0 regressions.
