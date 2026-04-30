# NUMBER 20D — Weak Reply List

> **Purpose:** Extract and diagnose every reply scoring ≤7/10 from the 20B/20C human taste review.
> No code changes in this file. Analysis only.
> Date: 2026-04-30

---

## Summary

| Metric | Value |
|---|---|
| Total scenarios reviewed | 155 |
| Weak replies (score ≤ 7) | 14 |
| يحتاج تحسين (5-6) | 5 |
| جيد but weak (7) | 9 |
| Weakest category | الرسائل الصوتية |
| Second weakest | الاشتراك / الأمان |
| Third weakest | الصور |

---

## Root Cause Distribution

| Root Cause | Count | Scenarios |
|---|---|---|
| GPT-level hallucination (unfixable by Elite Brain) | 3 | I15, I18, C25 |
| Wrong intent detection | 2 | V06, C21 |
| Banned phrase not in list (`يرجى`, `يمكنني`, `للاستفسار`) | 3 | T15, B07, I19 |
| Orphaned trailing punctuation `. .` | 1 | B02 |
| Robotic/cold GPT reply that passes all checks | 4 | V04, V17, S22, B06 |
| Missing emoji / no warmth | 1 | B04 |

---

## All Weak Scenarios

---

### W01 — T15 | أساسيات النصوص | Score: 5/10 | يحتاج تحسين

**Input type:** text
**Customer message:** `أرسل لي قائمة الأصناف`
**Context:** Menu request — GPT opens with "يرجى الانتظار"
**Detected intent:** `menu_request`

**Simulated bot input:**
```
يرجى الانتظار بينما أرتب لك القائمة. عندنا زينگر وشاورما وبرگر مشروم وكولا وبطاطا.
```

**Final reply (after Elite Brain):**
```
بينما أرتب لك القائمة. عندنا زينگر وشاورما وبرگر مشروم وكولا وبطاطا.
```

**Why not excellent:**
`يرجى الانتظار` was stripped correctly. But `بينما أرتب لك القائمة.` is still there — it reveals the bot is "processing" and sounds robotic. `بينما` is not in `BROKEN_START_PATTERNS`, so the broken first sentence is not dropped.

**Root cause:** Missing pattern `^بينما` in broken-start detector.

**Suggested reply:**
```
تفضل المنيو 🌷
زينگر 9,000 — شاورما 5,000 — برگر مشروم 8,000 — كولا 1,500 — بطاطا 2,000
شتريد؟
```

---

### W02 — B02 | الاشتراك / الأمان | Score: 5/10 | يحتاج تحسين

**Input type:** text
**Customer message:** `أريد أطلب`
**Context:** Subscription blocked — bot adds apology
**Detected intent:** `direct_order`

**Simulated bot input:**
```
الخدمة موقوفة حالياً. نعتذر على الإزعاج.
```

**Final reply (after Elite Brain):**
```
الخدمة موقوفة حالياً. .
```

**Why not excellent:**
`نعتذر على الإزعاج` was stripped correctly, but left a trailing orphaned `. ` — the reply ends with `حالياً. .` (double period). Post-strip cleanup removes leading punctuation but not trailing duplicate `.`.

**Root cause:** Missing trailing `. .` → `.` normalization in `_clean_leading_punctuation` (or a new `_clean_trailing_punctuation` step).

**Suggested reply:**
```
الخدمة موقوفة مؤقتاً 🌷 تواصل مع المطعم مباشرة.
```

---

### W03 — V17 | الرسائل الصوتية | Score: 6/10 | يحتاج تحسين

**Input type:** voice
**Customer message:** `[فويس] كيف أطلب؟`
**Context:** Customer asking how to order via voice message
**Detected intent:** `voice_order`

**Simulated bot input:**
```
للاستفسار عن الطلبات، تفضل بإرسال رسالتك النصية أو صوتية وسنخدمك.
```

**Final reply (after Elite Brain):**
```
للاستفسار عن الطلبات، تفضل بإرسال رسالتك النصية أو صوتية وسنخدمك.
```

**Why not excellent:**
No banned phrases, no broken start — passes all quality checks. But the reply is formal (`للاستفسار`), unhelpful (redirects to "send voice message" when the customer IS sending a voice message), and doesn't start the order flow.

**Root cause:** GPT-level bad logic that passes quality gate. `للاستفسار` is not in banned list.

**Suggested reply:**
```
وصلني 🌷 شتريد تطلب؟
```

**Fix type:** Add `للاستفسار` to `ELITE_BANNED_ADDITIONAL`. Also add template fallback for short unhelpful voice_order replies.

---

### W04 — I15 | الصور | Score: 6/10 | يحتاج تحسين

**Input type:** image
**Customer message:** `[صورة] الحلى`
**Context:** Customer sends image/asks about sweets — not in menu
**Detected intent:** `image_product`

**Simulated bot input:**
```
تم تحليل الصورة. يبدو أنها صورة مطعم. الحلى متوفرة.
```

**Final reply (after Elite Brain):**
```
يبدو أنها صورة مطعم. الحلى متوفرة.
```

**Why not excellent:**
`تم تحليل الصورة` stripped. Remaining first sentence `يبدو أنها صورة مطعم.` is not technically broken Arabic, so broken-start detector does not fire. The second sentence `الحلى متوفرة` is a factual hallucination — sweets are not in the product list.

**Root cause:** GPT hallucination + `يبدو أنها صورة مطعم` not detected as a broken/irrelevant opener.

**Suggested reply:**
```
وصلت الصورة 🌷 الحلى ما عندنا هسه — شتريد من المنيو؟
```

**Fix type:** Add `^يبدو أنها` to broken-start patterns. This sentence almost always means GPT is guessing from an image.

---

### W05 — I18 | الصور | Score: 6/10 | يحتاج تحسين

**Input type:** image
**Customer message:** `[صورة]`
**Context:** Generic image with no customer message
**Detected intent:** `image_product`

**Simulated bot input:**
```
تم تحليل الصورة. طلبك كان برگر مشروم. الحلى متوفرة.
```

**Final reply (after Elite Brain):**
```
طلبك كان برگر مشروم. الحلى متوفرة.
```

**Why not excellent:**
`تم تحليل الصورة` stripped. Remaining reply contains two fabrications: a previous order (`طلبك كان برگر مشروم`) and sweets availability. Not broken Arabic — passes quality gate.

**Root cause:** Pure GPT hallucination. Elite Brain cannot fact-check GPT's invented order history. `طلبك كان` is not a banned phrase.

**Suggested reply:**
```
وصلت الصورة 🌷 شتريد بالضبط؟
```

**Fix type:** `طلبك كان` references a previous order without using memory context — add to banned/suspicious phrases. When there is no `last_order` in memory, a GPT reply saying "طلبك كان..." is fabricated.

---

### W06 — C21 | الشكاوى | Score: 7/10 | جيد

**Input type:** text
**Customer message:** `اتصلوا بي`
**Context:** Customer requesting a callback
**Detected intent:** `casual_chat` ← wrong

**Simulated bot input:**
```
يمكنني تحويلك لموظف يتصل بك مباشرة إذا تريد.
```

**Final reply (after Elite Brain):**
```
يمكنني تحويلك لموظف يتصل بك مباشرة إذا تريد.
```

**Why not excellent:**
Intent detected as `casual_chat` instead of `human_handoff` (message is a callback request). Reply uses `يمكنني` which is formal and robotic — close to the banned `يمكنني مساعدتك` but not an exact match. The phrase `إذا تريد` sounds indifferent.

**Root cause:** `اتصلوا بي` not in `human_handoff` patterns. `يمكنني` alone (without `مساعدتك`) not in banned list.

**Suggested reply:**
```
حاضر 🌷 موظفنا يتواصل وياك هسه.
```

**Fix type:**
1. Add `"اتصلوا بي"`, `"تصلوا بي"`, `"اتصل بي"` to `human_handoff` patterns.
2. Add standalone `"يمكنني "` (with trailing space) to `ELITE_BANNED_ADDITIONAL`.

---

### W07 — V04 | الرسائل الصوتية | Score: 7/10 | جيد

**Input type:** voice
**Customer message:** `[فويس] كاش وتوصيل`
**Context:** Customer confirming payment and delivery via voice
**Detected intent:** `voice_order`

**Simulated bot input:**
```
تم تحويل الصوت إلى نص. الدفع كاش وتوصيل. تمام؟
```

**Final reply (after Elite Brain):**
```
الدفع كاش وتوصيل. تمام؟
```

**Why not excellent:**
`تم تحويل الصوت إلى نص.` stripped. Remaining `الدفع كاش وتوصيل.` is technically correct but starts with a noun phrase that reads like a bot statement, not a warm confirmation. The `تمام؟` at the end makes it acceptable but not warm.

**Root cause:** After stripping AI opener, the remaining reply doesn't start with a warm acknowledgment.

**Suggested reply:**
```
وصلني 🌷 كاش وتوصيل — صح؟
```

---

### W08 — V06 | الرسائل الصوتية | Score: 7/10 | جيد

**Input type:** voice
**Customer message:** `[فويس] أسوأ مطعم`
**Context:** Angry complaint sent via voice message
**Detected intent:** `voice_order` ← wrong

**Simulated bot input:**
```
بالتأكيد! من دواعي سروري خدمتك. كيف يمكنني مساعدتك؟
```

**Final reply (after Elite Brain):**
```
وصلني 🌷 تريد زينگر؟
```

**Why not excellent:**
The original corporate reply was correctly replaced by a `voice_order` template. But the customer said "أسوأ مطعم" (worst restaurant) — this is a complaint, not an order. The bot asking "do you want زينگر?" to an angry customer is contextually wrong.

**Root cause:** Intent detection checks `[فويس]` tag first (matches `voice_order`) before reaching `angry_complaint` pattern (`أسوأ`). The complaint content inside the voice message is never checked.

**Suggested reply:**
```
وصلتني 🌷 شنو المشكلة؟
```

**Fix type:** After matching `voice_order` via tag, do a secondary complaint scan on the remaining message text. If complaint keywords found, escalate to `voice_complaint` sub-intent.

---

### W09 — I19 | الصور | Score: 7/10 | جيد

**Input type:** image
**Customer message:** `[صورة] كيف أطلب؟`
**Context:** Customer asking how to order while sending an image
**Detected intent:** `image_product`

**Simulated bot input:**
```
لا أستطيع معالجة الطلب من الصور فقط. يرجى كتابة طلبك نصياً.
```

**Final reply (after Elite Brain):**
```
لا أستطيع معالجة الطلب من الصور فقط. يرجى كتابة طلبك نصياً.
```

**Why not excellent:**
`يرجى كتابة طلبك نصياً` — `يرجى` alone is NOT in `ELITE_BANNED_ADDITIONAL` (only `يرجى الانتظار`, `يرجى تزويدي`, `يرجى العلم` are). `لا أستطيع` (I cannot) exposes bot limitations. The reply redirects the customer unnecessarily — they can just type their order.

**Root cause:** `يرجى` standalone phrase not banned. `لا أستطيع` not banned.

**Suggested reply:**
```
وصلت الصورة 🌷 شتريد تطلب؟
```

**Fix type:** Add bare `"يرجى "` (with trailing space) to `ELITE_BANNED_ADDITIONAL`. Add `"لا أستطيع"` as a soft ban (bot should never say "I cannot").

---

### W10 — S22 | الستوري / الريل / البوست | Score: 7/10 | جيد

**Input type:** story
**Customer message:** `[ستوري] هل هذا للبيع؟`
**Context:** Customer asking if story product is available for sale
**Detected intent:** `story_reply`

**Simulated bot input:**
```
نعم هذا متوفر 🌷 تريد تطلب؟ تواصل معنا بالخاص.
```

**Final reply (after Elite Brain):**
```
نعم هذا متوفر 🌷 تريد تطلب؟ تواصل معنا بالخاص.
```

**Why not excellent:**
Two calls to action in one reply: `تريد تطلب؟` (want to order?) then `تواصل معنا بالخاص` (DM us). The question `تريد تطلب؟` is orphaned before the DM redirect — mixed signals. Also `نعم` is a formal MSA opener.

**Root cause:** Multi-question check: `تريد تطلب؟` is treated as the question; `تواصل معنا بالخاص` doesn't end with `؟` so multi-question detector misses this second call-to-action.

**Suggested reply:**
```
أيه متوفر 🌷 راسلنا بالخاص ونثبت الطلب.
```

---

### W11 — B04 | الاشتراك / الأمان | Score: 7/10 | جيد

**Input type:** text
**Customer message:** `هلا`
**Context:** Greeting while subscription is blocked
**Detected intent:** `greeting`

**Simulated bot input:**
```
هلا 🌷 هسه الخدمة موقوفة مؤقتاً.
```

**Final reply (after Elite Brain):**
```
هلا 🌷 هسه الخدمة موقوفة مؤقتاً.
```

**Why not excellent:**
The reply is clean but lacks direction. Customer greeted and gets a dead end. No guidance on what to do next.

**Root cause:** GPT-generated reply passes all checks. Missing a "what to do" direction.

**Suggested reply:**
```
هلا بيك 🌷 هسه الخدمة موقوفة مؤقتاً — تواصل مع المطعم مباشرة.
```

---

### W12 — B06 | الاشتراك / الأمان | Score: 7/10 | جيد

**Input type:** text
**Customer message:** `أريد زينگر`
**Context:** Order attempt — subscription expired with technical message
**Detected intent:** `direct_order`

**Simulated bot input:**
```
عذراً، الخدمة متوقفة حالياً بسبب صيانة تقنية.
```

**Final reply (after Elite Brain):**
```
عذراً، الخدمة متوقفة حالياً بسبب صيانة تقنية.
```

**Why not excellent:**
`صيانة تقنية` (technical maintenance) exposes system internals — customers should not know the reason. Also `عذراً` starts the reply without warmth. Reply passes all quality checks — no banned phrases.

**Root cause:** `صيانة تقنية` not in banned list. Technical reason phrases need to be hidden.

**Suggested reply:**
```
الخدمة موقوفة مؤقتاً 🌷 تواصل مع المطعم مباشرة.
```

**Fix type:** Add `"صيانة تقنية"`, `"مشكلة تقنية"`, `"خطأ تقني"` to banned/replacement phrases.

---

### W13 — B07 | الاشتراك / الأمان | Score: 7/10 | جيد

**Input type:** text
**Customer message:** `شنو عندكم؟`
**Context:** Menu request — subscription not active
**Detected intent:** `menu_request`

**Simulated bot input:**
```
الخدمة موقوفة حالياً. يرجى التواصل مع المطعم لتفعيل الخدمة.
```

**Final reply (after Elite Brain):**
```
الخدمة موقوفة حالياً. يرجى التواصل مع المطعم لتفعيل الخدمة.
```

**Why not excellent:**
`يرجى التواصل مع المطعم لتفعيل الخدمة` — `يرجى` alone not banned. `لتفعيل الخدمة` reveals subscription management to the customer — they don't need to know what "تفعيل" means.

**Root cause:** `يرجى` standalone not in `ELITE_BANNED_ADDITIONAL`. Subscription internals exposed.

**Suggested reply:**
```
الخدمة موقوفة مؤقتاً 🌷 تواصل مع المطعم مباشرة.
```

**Fix type:** Add `"يرجى "` (bare, with trailing space) to `ELITE_BANNED_ADDITIONAL`.

---

### W14 — V09 | الرسائل الصوتية | Score: 7/10 (borderline) | جيد

**Input type:** voice
**Customer message:** `[فويس] شنو عندكم؟`
**Context:** Menu request via voice message
**Detected intent:** `voice_order`

**Simulated bot input:**
```
وصلتني! هلا بيك 🌷 شتريد؟
```

**Final reply (after Elite Brain):**
```
وصلتني! هلا بيك 🌷 شتريد؟
```

**Why not excellent:**
Reply passes all checks. `وصلتني!` with exclamation mark is slightly over-excited. The double acknowledgment (`وصلتني!` then `هلا بيك 🌷`) is mildly redundant. Not a serious issue but can be tightened.

**Root cause:** GPT-generated reply with minor stylistic issue. No quality gate action needed.

**Suggested reply:**
```
هلا بيك 🌷 شتريد؟
```

or keep `وصلتني 🌷` without the exclamation.

---

## Categorized Summary

### الرسائل الصوتية (Voice) — 3 weak scenarios

| ID | Score | Root Cause | Fix Type |
|---|---|---|---|
| V04 | 7 | Cold start after AI strip | Add warm opener after strip |
| V06 | 7 | Wrong intent: voice tag hides complaint | Secondary complaint scan inside voice messages |
| V09 | 7 | Minor style (wصلتني!) | Template refinement |
| V17 | 6 | `للاستفسار` formal + unhelpful | Add to banned phrases |

### الصور (Images) — 3 weak scenarios

| ID | Score | Root Cause | Fix Type |
|---|---|---|---|
| I15 | 6 | GPT hallucinates sweets available | Add `^يبدو أنها` to broken-start patterns |
| I18 | 6 | GPT invents previous order | Ban `طلبك كان` without memory context |
| I19 | 7 | `يرجى` not banned; `لا أستطيع` exposed | Add `يرجى ` + `لا أستطيع` to banned list |

### الاشتراك / الأمان (Subscription) — 4 weak scenarios

| ID | Score | Root Cause | Fix Type |
|---|---|---|---|
| B02 | 5 | Trailing `. .` orphan after strip | Add trailing punctuation cleanup |
| B04 | 7 | No direction after "service off" | Template improvement |
| B06 | 7 | `صيانة تقنية` exposes internals | Add technical phrases to banned list |
| B07 | 7 | `يرجى` standalone not banned | Add `يرجى ` to banned list |

### أساسيات النصوص (Text basics) — 1 weak scenario

| ID | Score | Root Cause | Fix Type |
|---|---|---|---|
| T15 | 5 | `بينما أرتب` not in broken-start patterns | Add `^بينما` pattern |

### الشكاوى (Complaints) — 1 weak scenario

| ID | Score | Root Cause | Fix Type |
|---|---|---|---|
| C21 | 7 | `اتصلوا بي` not in handoff patterns; `يمكنني` not banned | Add to patterns + ban `يمكنني ` |

### الستوري / الريل (Story) — 1 weak scenario

| ID | Score | Root Cause | Fix Type |
|---|---|---|---|
| S22 | 7 | Two calls-to-action; `نعم` formal opener | Template improvement |

---

## Improvement Blueprint for NUMBER 20D

### Group A — Banned phrase additions (high impact, low risk)

Add to `ELITE_BANNED_ADDITIONAL` in [reply_quality.py](../services/reply_quality.py):

```python
# NUMBER 20D additions
"يرجى ",               # standalone يرجى (with space) — B07, I19
"يمكنني ",             # standalone يمكنني (with space) — C21
"للاستفسار",           # formal deflection opener — V17
"لا أستطيع",           # bot exposing its own limitations — I19
"صيانة تقنية",         # exposes technical internals — B06
"مشكلة تقنية",         # same
"لتفعيل الخدمة",       # exposes subscription system — B07
```

### Group B — Broken-start pattern additions (medium impact, low risk)

Add to `BROKEN_START_PATTERNS` in [reply_quality.py](../services/reply_quality.py):

```python
r"^بينما\s+(أرتب|أجهز|أحضر|أرسل)",   # T15: "بينما أرتب لك القائمة."
r"^يبدو\s+أنها",                       # I15: "يبدو أنها صورة مطعم."
```

### Group C — Trailing punctuation cleanup (medium impact, low risk)

In `extended_quality_gate`, after step 9 (dangling punctuation), add:

```python
# Remove duplicate trailing periods caused by phrase stripping
fixed = re.sub(r'\.\s*\.+', '.', fixed).strip()
```

This fixes B02: `"الخدمة موقوفة حالياً. ."` → `"الخدمة موقوفة حالياً."`

### Group D — Intent pattern additions (medium impact)

In `INTENT_PATTERNS` in [reply_brain.py](../services/reply_brain.py):

```python
# Human handoff — add callback requests
("human_handoff", [..., "اتصلوا بي", "اتصل بي", "تصلوا بي", "أبي تتصلون"]),
```

### Group E — Secondary complaint scan in voice messages (higher complexity)

After detecting `voice_order` intent from `[فويس]` tag, do a fast secondary check:
if complaint keywords (`أسوأ`, `مشكلة`, `شكوى`, `خراء`, `ما زبط`) appear in the message body, override intent to `voice_complaint` → use complaint templates.

This fixes V06 where `[فويس] أسوأ مطعم` gets a sales response instead of empathy.

### Group F — Template refinements (low impact)

- `voice_order` templates: add `"وصلني 🌷 كاش وتوصيل — صح؟"` variant for payment confirmations
- `story_reply_available` templates: replace `"نعم هذا متوفر"` with `"أيه متوفر 🌷"` (Iraqi dialect)
- `blocked_subscription` templates: always end with direction `تواصل مع المطعم مباشرة`

---

## Unfixable Cases (GPT-level, not Elite Brain)

| ID | Issue | Why unfixable |
|---|---|---|
| I15 | GPT claims sweets available when not in menu | Elite Brain can detect AI exposure, but can't fact-check GPT's hallucinations against product list at reply time |
| I18 | GPT invents previous order history | Same — no memory passed to this scenario in test |
| C25 | GPT deflects delay complaint by questioning customer's address | GPT generates a defensively wrong response; no banned phrase, no broken start |

---

## Final Statement

**Count of weak replies: 14** (5 × يحتاج تحسين, 9 × جيد/weak)

**Weakest categories:**
1. الرسائل الصوتية — 4 weak (voice tag hides complaint intent; cold replies after AI strip)
2. الاشتراك / الأمان — 4 weak (يرجى not banned; technical internals exposed)
3. الصور — 3 weak (GPT hallucination; يبدو أنها not detected as broken start)

**Fixable in 20D: 11 of 14**
**Unfixable (GPT-level): 3 of 14**

---

## NUMBER 20D READY
