# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-tenant Restaurant Management SaaS platform. Restaurant owners get a dashboard to manage products, orders, and customer conversations across Telegram, WhatsApp, Instagram, and Facebook. An AI chatbot handles customer messages and extracts orders automatically.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (http://localhost:8000)
uvicorn main:app --reload

# Run end-to-end smoke tests (22 tests)
bash scripts/test_e2e.sh

# Database backup
bash scripts/backup.sh
```

Set up environment first:
```bash
cp .env.example .env  # then fill in required values
```

Minimum required env vars: `JWT_SECRET`, `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

## Architecture

### Backend: `main.py` + `database.py` + `services/`

**`main.py`** (~2,900 lines) — all 67 FastAPI routes in one file. Key patterns:
- JWT auth via `get_current_user()` dependency (role: `owner`, `staff`, `super`)
- Subscription guard middleware blocks expired/suspended restaurants on protected endpoints
- Rate limiting is in-process (10/min per IP dict), no external dependency
- Background jobs: `asyncio.create_task()` in lifespan for hourly subscription cleanup
- Webhook processing uses `BackgroundTasks` to avoid blocking the HTTP response

**`database.py`** — direct SQL (no ORM). Supports SQLite (default) and PostgreSQL via a `_PgConnection` adapter that translates `?` placeholders to `%s` and normalizes row types. Write queries for SQLite — they run on PostgreSQL too.

**`services/`**:
- `bot.py` — processes messages with gpt-4o-mini, detects escalation keywords (Arabic), extracts orders
- `webhooks.py` — receives and routes incoming messages from all channels, handles voice via Whisper
- `menu_parser.py` — imports menus from PDF/DOCX/XLSX/images using OpenAI Vision with confidence scoring
- `storage.py` — Supabase Storage wrapper (uses `SUPABASE_SERVICE_ROLE_KEY`, backend only)

### Frontend: `public/`

Vanilla JS + Tailwind CSS CDN, no build step. Three main pages:
- `app.html` — restaurant owner dashboard (8 sections, RTL Arabic, Chart.js analytics)
- `super.html` — super admin dashboard (purple theme, platform-wide management)
- `config.js` — sets `API_BASE_URL` and Supabase credentials for frontend

Tokens stored in `localStorage`: `auth_token` (restaurant users), `sa_token` (super admins).

### Database Schema

Key tables: `users`, `restaurants`, `products`, `variants`, `customers`, `orders`, `order_items`, `channels`, `conversations`, `messages`, `bot_config`, `subscriptions`, `super_admins`.

### Auth & Multi-tenancy

Two separate auth hierarchies sharing the same JWT middleware:
- **Restaurant users** — `is_super=False` in JWT payload, scoped to their `restaurant_id`
- **Super admins** — `is_super=True`, platform-wide access, separate PIN recovery, audit log

### Subscription Plans & Enforcement

Plans: `trial` (10 products, 2 staff, 1 channel) → `starter` → `professional` → `enterprise` (unlimited). Limits checked at creation time; returns HTTP 402 with Arabic error message if exceeded.

## Deployment

- **Railway**: Git push, auto-detects `Procfile` (`uvicorn main:app --host 0.0.0.0 --port $PORT`)
- **Render**: `render.yaml` in repo configures the service
- **Docker**: `Dockerfile` builds Python 3.11 slim image; mount `/data` for SQLite persistence
- **Frontend**: Deploy `public/` to Netlify; update `config.js` with backend URL before deploying

`BASE_URL` env var must be set to the deployed backend URL for webhooks to register correctly.
