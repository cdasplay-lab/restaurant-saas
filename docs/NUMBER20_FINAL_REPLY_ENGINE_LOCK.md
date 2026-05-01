# NUMBER 20 — Elite Reply Engine: Final Lock

**Status: LOCKED ✅**
**Date locked: 2026-05-01**
**Feature flag: `ELITE_REPLY_ENGINE=true` (default)**

---

## What It Does

A post-processing quality layer that runs after Algorithm 6 in `bot.py`.
It never blocks order flow, never raises exceptions, and always returns a valid reply string.
If the engine crashes, the original Algorithm 6 reply is returned unchanged.

---

## Iteration Summary

| Iteration | Focus | Avg | Rejected |
|-----------|-------|-----|----------|
| 20 | Initial architecture: intent detection, quality gate, template library | — | — |
| 20B | Human taste review baseline: 155-scenario scored dataset | 8.5 | 0 |
| 20C | Broken-start detector, orphaned punctuation cleanup, context-aware min length, 20 new banned phrases, factual memory preservation | 8.5 | 0 |
| 20D | AI-exposure in voice/image/story, subscription block intent override, voice readback strip, orphaned tail cleanup | **8.8** | **0** |

---

## Final Metrics (NUMBER 20D — 2026-05-01)

| Metric | Result | Target |
|--------|--------|--------|
| Overall avg | **8.8 / 10** | ≥ 8.8 |
| Rejected (1–4) | **0** | 0 |
| Voice avg | **8.8** | ≥ 8.8 |
| Image avg | **8.8** | ≥ 8.8 |
| Story/Reel avg | **8.8** | ≥ 8.8 |
| Elite brain suite | **847 / 849 (99.8%)** | ≥ 99% |
| Regression safety | **29 / 32 passed, 3 warnings** | 0 failures |

---

## Files Changed

| File | Role |
|------|------|
| `services/reply_brain.py` | Main engine: `elite_reply_pass()`, intent detection, context builder, subscription override |
| `services/reply_quality.py` | Extended quality gate: banned phrases, tone checks, `should_use_template`, `quality_score` |
| `services/reply_templates.py` | Iraqi Arabic template library with variable substitution |

---

## What the Engine Improves

**Text replies**
- Removes corporate openers (`بالتأكيد`, `من دواعي سروري`, etc.)
- Removes formal filler (`يرجى تزويدي`, `عزيزي العميل`, etc.)
- Enforces one question per reply

**Voice messages**
- Strips AI-process readback (`طلبت `, `وصلتني! `, `تم تحويل الصوت`)
- Responds naturally as if the order was heard directly

**Image messages**
- Removes AI-analysis exposure (`تم تحليل الصورة`, `الصورة تحتوي`, etc.)
- Falls back to appropriate image template when content is stripped

**Story / Reel / Post**
- Strips MSA filler (`نعم `, double CTAs like `تواصل معنا بالخاص`)
- Routes to warm story templates when content collapses

**Complaints**
- Blocks upsell phrases during complaint context
- Angry complaint → always offers human handoff
- Empathy before action, one action only

**Sales / Order flow**
- Enforces single-question rule in order slots
- Preserves factual data (price, address, last order) during cleanup

**Memory / Personalization**
- Protects `"آخر طلب"`, `"طلبك السابق"` from phrase-strip
- Customer name and address from memory flow through cleanly

**Banned phrases**
- 50+ phrases across 4 categories: corporate filler, formal openers, AI/system exposure, complaint upsell triggers

**Quality gate**
- `quality_score()` returns score 0–100 with issue list for logging
- Broken-start detector (19 patterns) → template fallback
- Orphaned punctuation cleanup after phrase stripping

---

## Feature Flag

```bash
# Enable (default)
ELITE_REPLY_ENGINE=true

# Disable — bypasses engine completely, Algorithm 6 reply returned as-is
ELITE_REPLY_ENGINE=false
```

Flag is read at module import time in `services/reply_brain.py:17`.

**Rollback:** set `ELITE_REPLY_ENGINE=false` in your environment and restart the process. No code change required.

---

## Known Limitations

- **GPT hallucination**: The engine cleans tone and strips banned phrases, but cannot fix factually incorrect GPT output (wrong prices, wrong items) in edge cases with unclear images.
- **Render production**: Not yet tested on live Render deployment. Local + test-suite verified only.
- **Meta live channels**: WhatsApp and Instagram live channel end-to-end not yet validated with real customer traffic.
- **`ELITE_ENABLED` is module-level**: Flag is read once at import. Changing the env var requires process restart (or module reload) to take effect.

---

## Tests Passed

| Suite | File | Result |
|-------|------|--------|
| Elite brain check | `scripts/day20_elite_reply_brain_check.py` | 847/849 ✅ |
| Regression safety | `scripts/day20_regression_safety_check.py` | 29/32, 0 failures ✅ |
| Human taste review | `scripts/day20b_taste_review.py` | avg 8.8, 0 rejected ✅ |

Known unfixable failures: J04/J05 — duplicate-order detection requires history context unavailable from message text alone.
Regression warnings (3): non-critical, OpenAI key absent in test env.

---

## Rollback Instruction

```bash
ELITE_REPLY_ENGINE=false
```

Restart the app. The engine is completely bypassed — `elite_reply_pass()` returns the input reply unchanged on line 276 of `reply_brain.py`.

See `docs/ROLLBACK_ELITE_REPLY_ENGINE.md` for full procedure.

---

## Golden Status

**NUMBER 20 FINAL LOCKED**

The Elite Reply Engine is stable, tested, and safe for production.
Do not add new reply features to this engine without a new numbered iteration.
Do not refactor. Do not touch Render/DATABASE_URL. Do not change bot order logic.
