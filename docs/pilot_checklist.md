# Pilot GO/NO-GO Checklist
> NUMBER 44C | Updated: 2026-05-15
> Complete this checklist before flipping the bot live for the pilot restaurant.

---

## Section 1 — Pre-Launch (GO/NO-GO Gate)

Mark each item ✅ PASS or ❌ FAIL.  
**All items must be PASS before proceeding.**

### 1.1 Environment Variables

| # | Check | Status | Notes |
|---|-------|--------|-------|
| E1 | `JWT_SECRET` is set and not a known default | ☐ | |
| E2 | `OPENAI_API_KEY` is set and has balance | ☐ | |
| E3 | `SUPABASE_URL` is set | ☐ | |
| E4 | `SUPABASE_SERVICE_ROLE_KEY` is set | ☐ | |
| E5 | `BASE_URL` is set to the deployed backend URL | ☐ | |

### 1.2 Service Health

| # | Check | Status | Notes |
|---|-------|--------|-------|
| H1 | `GET /api/health` returns `{"status": "ok"}` | ☐ | |
| H2 | No 5xx errors in logs in the last 10 minutes | ☐ | |
| H3 | Server memory < 80% | ☐ | |
| H4 | Response time < 2s on `/api/health` | ☐ | |

### 1.3 Database

| # | Check | Status | Notes |
|---|-------|--------|-------|
| D1 | Database connection succeeds | ☐ | |
| D2 | All required tables exist (products, orders, conversations, channels) | ☐ | |
| D3 | At least 1 restaurant record exists for pilot | ☐ | |
| D4 | Recent backup exists (SQLite `/data/*.db` or Supabase backup) | ☐ | |

### 1.4 Bot & Channel

| # | Check | Status | Notes |
|---|-------|--------|-------|
| B1 | Telegram bot token is saved in `channels` table | ☐ | |
| B2 | `GET channels/telegram/test` returns `connection_status: connected` | ☐ | |
| B3 | Webhook URL registered with Telegram (`getWebhookInfo`) | ☐ | |
| B4 | Webhook URL matches `$BASE_URL/api/webhook/telegram/...` | ☐ | |
| B5 | Bot can receive test message (send `/start` to bot) | ☐ | |

### 1.5 Menu & Configuration

| # | Check | Status | Notes |
|---|-------|--------|-------|
| M1 | At least 10 products seeded and available | ☐ | |
| M2 | Working hours configured (not all zeros) | ☐ | |
| M3 | Delivery fee set (even if 0) | ☐ | |
| M4 | Delivery time string set | ☐ | |
| M5 | At least one payment method configured | ☐ | |

### 1.6 Test Suite

| # | Check | Status | Notes |
|---|-------|--------|-------|
| T1 | `python3 scripts/day41a_order_flow_test.py` — 0 failures | ☐ | |
| T2 | `python3 scripts/day41b_critical_fixes_test.py` — 0 failures | ☐ | |
| T3 | `python3 scripts/day41c_final_reply_safety_test.py` — 0 failures | ☐ | |
| T4 | `python3 scripts/day42_data_integrity_phase3_test.py` — 0 failures | ☐ | |
| T5 | `python3 scripts/day42_reply_quality_phase1_test.py` — 0 failures | ☐ | |
| T6 | `python3 scripts/day43_backend_baseline_test.py` — 0 failures | ☐ | |
| T7 | `python3 scripts/day44a_reply_production_readiness_test.py` — 0 failures | ☐ | |
| T8 | `python3 scripts/day44b_real_restaurant_simulation_test.py` — 0 failures | ☐ | |
| T9 | `python3 scripts/day44c_pilot_readiness_test.py` — 0 failures | ☐ | |

### 1.7 Human Handoff

| # | Check | Status | Notes |
|---|-------|--------|-------|
| HH1 | Staff member has dashboard access and knows how to toggle manual mode | ☐ | |
| HH2 | Escalation notification delivers customer name + phone + basket | ☐ | |
| HH3 | Staff can reply manually from dashboard conversations view | ☐ | |
| HH4 | Staff knows rollback procedure (Runbook Section 9) | ☐ | |

### 1.8 PII & Log Safety

| # | Check | Status | Notes |
|---|-------|--------|-------|
| P1 | Iraqi phone numbers (07x / 09x) are masked as `07x****` in logs | ☐ | |
| P2 | Order confirmation does NOT log raw phone to application log | ☐ | |
| P3 | Escalation notification in dashboard hides phone from log (masked) | ☐ | |
| P4 | Supabase RLS is enabled on `customers` table | ☐ | |

---

## Section 2 — GO / NO-GO Decision

Fill this block at the time of launch decision.

```
Date/Time:       ___________________
Operator:        ___________________
Environment:     ___________________  (staging / production)

Section 1.1 (Env Vars):      [ ] GO   [ ] NO-GO   Failures: ___
Section 1.2 (Health):        [ ] GO   [ ] NO-GO   Failures: ___
Section 1.3 (Database):      [ ] GO   [ ] NO-GO   Failures: ___
Section 1.4 (Bot/Channel):   [ ] GO   [ ] NO-GO   Failures: ___
Section 1.5 (Menu/Config):   [ ] GO   [ ] NO-GO   Failures: ___
Section 1.6 (Test Suite):    [ ] GO   [ ] NO-GO   Failures: ___
Section 1.7 (Handoff):       [ ] GO   [ ] NO-GO   Failures: ___
Section 1.8 (PII/Logs):      [ ] GO   [ ] NO-GO   Failures: ___

OVERALL DECISION:  [ ] 🟢 GO — all sections pass, pilot may proceed
                   [ ] 🔴 NO-GO — fix failures before proceeding
```

---

## Section 3 — Pilot Monitoring (During Pilot)

Check these every 30 minutes during the pilot session.

| # | Check | 30min | 60min | 90min | 120min |
|---|-------|-------|-------|-------|--------|
| W1 | No 5xx errors in logs | ☐ | ☐ | ☐ | ☐ |
| W2 | Spam guard not firing excessively | ☐ | ☐ | ☐ | ☐ |
| W3 | Orders appearing in dashboard | ☐ | ☐ | ☐ | ☐ |
| W4 | Bot replies in < 5s (manual test) | ☐ | ☐ | ☐ | ☐ |
| W5 | No unexpected escalations (customer confusion) | ☐ | ☐ | ☐ | ☐ |
| W6 | Human handoffs resolved within 10 min | ☐ | ☐ | ☐ | ☐ |

---

## Section 4 — Post-Pilot Review

Complete within 24 hours of pilot end.

| # | Question | Answer |
|---|---------|--------|
| R1 | How many orders were placed? | |
| R2 | How many completed successfully (confirmation sent)? | |
| R3 | How many required human handoff? | |
| R4 | How many spam throttle events? | |
| R5 | Were any phone numbers visible unmasked in logs? | |
| R6 | Did any order miss customer phone? | |
| R7 | Were any duplicate orders created? | |
| R8 | Any Arabic parsing failures (bot confused by dialect)? | |
| R9 | Any GPT fallback events (OpenAI down)? | |
| R10 | Any crashes or 500 errors? | |

**Top weakness observed:**
```
___________________________________________________________________
```

**Recommendation for full rollout:**
```
[ ] READY — no critical issues
[ ] NOT YET — fix these first: _______________________________
```

---

## Section 5 — Staff Review Checklist

Brief the restaurant staff member before pilot starts:

- [ ] Show them the Conversations tab in the dashboard
- [ ] Show them how to toggle a conversation to manual mode
- [ ] Show them how to send a manual reply
- [ ] Show them the Orders tab and order status buttons
- [ ] Explain what happens on human escalation (they get a notification)
- [ ] Give them the emergency rollback step (Section 9 of runbook)
- [ ] Confirm they have the backend URL and their login credentials saved
- [ ] Confirm they have the developer's contact for emergencies
