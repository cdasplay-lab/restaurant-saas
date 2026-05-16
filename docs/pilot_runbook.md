# Pilot Runbook — Controlled Restaurant Launch
> NUMBER 44C | Updated: 2026-05-15
> Scope: One restaurant, Telegram only, controlled test environment

---

## Overview

This runbook walks an operator through setting up and running one controlled pilot restaurant. It is not a general deployment guide — it covers only what is needed to safely test the bot with a real (or simulated) restaurant before a wider rollout.

**Time estimate:** 45–90 minutes end-to-end  
**Channel:** Telegram (first and only channel for pilot)  
**Rollback window:** Always available — see Section 9

---

## Section 1 — Prerequisites

Before starting, verify the following are available:

| Item | Value |
|------|-------|
| Backend URL | Railway/Render deployed URL |
| JWT_SECRET | Generated strong secret |
| OPENAI_API_KEY | Valid key with credits |
| SUPABASE_URL | Your Supabase project URL |
| SUPABASE_SERVICE_ROLE_KEY | Supabase service role key |
| Telegram Bot Token | From @BotFather |
| Staff phone number | For human handoff test |

---

## Section 2 — Environment Check

Run on server / Railway console:

```bash
# Minimum required vars
echo "JWT_SECRET set:         $([ -n "$JWT_SECRET" ] && echo YES || echo MISSING)"
echo "OPENAI_API_KEY set:     $([ -n "$OPENAI_API_KEY" ] && echo YES || echo MISSING)"
echo "SUPABASE_URL set:       $([ -n "$SUPABASE_URL" ] && echo YES || echo MISSING)"
echo "SUPABASE_SERVICE_ROLE_KEY set: $([ -n "$SUPABASE_SERVICE_ROLE_KEY" ] && echo YES || echo MISSING)"
echo "BASE_URL set:           $([ -n "$BASE_URL" ] && echo YES || echo MISSING)"
```

All five must print YES. If any prints MISSING — STOP, do not proceed.

Health endpoint must return `{"status": "ok"}`:

```bash
curl -s $BASE_URL/api/health | python3 -m json.tool
```

---

## Section 3 — Test Restaurant Setup

### 3.1 Create Owner Account

```bash
curl -s -X POST $BASE_URL/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "مطعم التجربة",
    "email": "pilot@test.com",
    "password": "PilotTest2026!",
    "phone": "07901234567"
  }'
```

Save the returned `token` as `$PILOT_TOKEN`.

### 3.2 Verify Login

```bash
curl -s -X POST $BASE_URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "pilot@test.com", "password": "PilotTest2026!"}'
```

---

## Section 4 — Menu Seed (10–15 Items)

Seed via API with a realistic Iraqi fast-food menu. Post each item:

```bash
BASE="$BASE_URL/api/products"
H="Authorization: Bearer $PILOT_TOKEN"
CT="Content-Type: application/json"

# Burgers
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"برجر لحم","price":8000,"available":true,"category":"برجر"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"برجر دجاج","price":7500,"available":true,"category":"برجر"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"برجر مزدوج","price":11000,"available":true,"category":"برجر"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"زينجر","price":8500,"available":true,"category":"دجاج"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"وجبة تشيكن","price":10000,"available":true,"category":"دجاج"}'

# Sides
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"بطاطا صغير","price":2500,"available":true,"category":"جانبية"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"بطاطا وسط","price":3000,"available":true,"category":"جانبية"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"بطاطا كبير","price":4000,"available":true,"category":"جانبية"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"صلصة إضافية","price":500,"available":true,"category":"إضافات"}'

# Drinks
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"بيبسي","price":1500,"available":true,"category":"مشروبات"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"ماء","price":500,"available":true,"category":"مشروبات"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"عصير برتقال","price":2000,"available":true,"category":"مشروبات"}'

# Combos
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"وجبة عائلية","price":28000,"available":true,"category":"وجبات"}'
curl -s -X POST $BASE -H "$H" -H "$CT" -d '{"name":"وجبة فردية","price":12000,"available":true,"category":"وجبات"}'
```

**Verify:** `curl -s $BASE_URL/api/products -H "Authorization: Bearer $PILOT_TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} items seeded')"`

Expected: 14 items

---

## Section 5 — Working Hours

Set realistic Iraqi restaurant hours (12:00–24:00):

```bash
curl -s -X PUT $BASE_URL/api/bot-config \
  -H "Authorization: Bearer $PILOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "working_hours": {
      "saturday":  {"open": "12:00", "close": "24:00"},
      "sunday":    {"open": "12:00", "close": "24:00"},
      "monday":    {"open": "12:00", "close": "24:00"},
      "tuesday":   {"open": "12:00", "close": "24:00"},
      "wednesday": {"open": "12:00", "close": "24:00"},
      "thursday":  {"open": "12:00", "close": "24:00"},
      "friday":    {"open": "14:00", "close": "24:00"}
    }
  }'
```

---

## Section 6 — Delivery & Pickup Settings

```bash
curl -s -X PUT $BASE_URL/api/bot-config \
  -H "Authorization: Bearer $PILOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "delivery_fee": 2000,
    "delivery_time": "30-45 دقيقة",
    "delivery_areas": "بغداد — الكرخ والرصافة",
    "pickup_available": true,
    "payment_methods": ["cash", "online"]
  }'
```

---

## Section 7 — Telegram Channel Setup

### 7.1 Create Bot via @BotFather

1. Message @BotFather → `/newbot`
2. Name: `مطعم التجربة`
3. Username: `PilotRestaurant2026Bot`
4. Save token

### 7.2 Register Channel

```bash
curl -s -X POST $BASE_URL/api/channels \
  -H "Authorization: Bearer $PILOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type": "telegram", "token": "YOUR_BOT_TOKEN_HERE"}'
```

### 7.3 Register Webhook

```bash
curl -s -X POST $BASE_URL/api/channels/telegram/webhook \
  -H "Authorization: Bearer $PILOT_TOKEN"
```

Expected: `{"status": "ok", "webhook_url": "..."}`

### 7.4 Verify Webhook with Telegram

```bash
curl -s "https://api.telegram.org/botYOUR_TOKEN/getWebhookInfo" | python3 -m json.tool
```

`url` field must point to your `$BASE_URL/api/webhook/telegram/YOUR_RESTAURANT_ID`.

---

## Section 8 — Functional Tests

Run in order. Each test requires observing the bot reply in Telegram.

### 8.1 Basic Order Test

Send to bot:
1. `أريد برجر لحم` → expect menu item recognition + slot question (توصيل أو استلام؟)
2. `توصيل` → expect address question
3. `شارع المتنبي، الكرخ` → expect name question
4. `محمد` → expect phone question
5. `07901234567` → expect order summary + confirmation

**Pass:** confirmation message shows item, total = 8000 + 2000 (delivery), correct phone.

### 8.2 Human Handoff Test

Send: `أريد أكلم موظف` or `مشكلة في طلبي`

**Pass:** Bot sends handoff reply. Dashboard shows escalation notification with customer name, phone, active basket.

### 8.3 Fallback Test (GPT Down Simulation)

Temporarily set `OPENAI_API_KEY=invalid_key_test` in env.  
Send: `أريد برجر` (start order), then `أضيف بيبسي` (while order active)

**Pass:** Bot replies with deterministic Iraqi Arabic question (not "OpenAI error" in English).

Restore `OPENAI_API_KEY` immediately after.

### 8.4 Spam Test

Send 4+ messages within 10 seconds:
1. `هلو`
2. `شنو عندكم`
3. `أريد طلب`
4. `سريع`

**Pass:** 4th message gets `لحظة 🌷` throttle reply. No crash.

### 8.5 Duplicate Webhook Test

Resend the same Telegram `update_id` twice (use curl to POST same payload twice to webhook endpoint).

**Pass:** Second delivery returns `{"status": "ok"}` immediately (idempotent), no duplicate order.

### 8.6 Order Confirmation Test

Complete a full order (see 8.1). Check dashboard Orders tab.

**Pass:** Order appears with correct items, total, customer phone, status = `pending`.

### 8.7 Closed Hours Test

Temporarily set all working hours to past times (e.g., "08:00"–"09:00").  
Send: `أريد طلب`

**Pass:** Bot replies with closed message in Arabic. No order started.

Restore working hours after.

---

## Section 9 — Rollback Plan

If anything goes wrong during pilot:

### Immediate Steps

1. **Disconnect Telegram webhook:**
   ```bash
   curl -s -X DELETE $BASE_URL/api/channels/telegram/webhook \
     -H "Authorization: Bearer $PILOT_TOKEN"
   ```

2. **Switch conversations to human mode:**  
   Dashboard → Conversations → each active conv → toggle "وضع يدوي"

3. **Notify staff** to handle messages manually via Telegram app.

### If Database Corruption Suspected

```bash
# SQLite only — take a snapshot before any fix
cp /data/restaurant.db /data/restaurant.db.pilot-backup-$(date +%Y%m%d-%H%M%S)
```

For PostgreSQL: restore from Supabase backup (Dashboard → Database → Backups).

### Git Rollback to Last Stable Tag

```bash
# On server: pull the last known good tag
git fetch --tags
git checkout number44b-real-restaurant-simulation-complete
# Restart service
```

### Full Teardown (Nuclear Option)

Delete the pilot restaurant record from DB. All associated data (products, orders, conversations, channels) cascades via FK.

```sql
-- PostgreSQL / SQLite
DELETE FROM restaurants WHERE email = 'pilot@test.com';
```

---

## Section 10 — Post-Pilot Review

After pilot session (min 2 hours, ideally 4):

1. Export orders CSV → review for missing phones or incorrect totals
2. Export conversations → check for bot confusion patterns
3. Review server logs → verify no PII leaks (phone numbers should appear as `079****`)
4. Check escalation log → were all handoffs handled correctly?
5. Fill out `docs/pilot_checklist.md` Section 4 (Post-Pilot Review) completely

---

## Emergency Contacts

| Role | Contact |
|------|---------|
| Dev lead | On-call phone (add before pilot) |
| Restaurant owner | Add before pilot |
| Supabase support | support@supabase.com |
