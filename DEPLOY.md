# Production Deployment Guide

## Quick start (local)

```bash
cp .env.example .env          # fill in real values
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit: http://localhost:8000
Super Admin: http://localhost:8000/super/login

---

## 1. Railway (recommended)

### 1-a. Deploy

```bash
npm install -g @railway/cli
railway login
railway init          # or: railway link <existing-project>
railway up
```

Railway auto-detects the `Procfile`:
```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

### 1-b. Environment variables

In the Railway dashboard → your service → **Variables**, add:

| Variable | Value |
|---|---|
| `JWT_SECRET` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | `https://yourapp.up.railway.app` |
| `OPENAI_API_KEY` | `sk-...` |
| `SESSION_HOURS` | `24` |
| `ALLOWED_ORIGINS` | `https://yourapp.up.railway.app` |

### 1-c. PostgreSQL (optional but recommended for production)

1. Railway dashboard → **+ New** → **Database** → **PostgreSQL**
2. Railway automatically injects `DATABASE_URL` into your service
3. No code change needed — the app detects it automatically

### 1-d. Persistent SQLite volume (if not using PostgreSQL)

1. Railway dashboard → your service → **Volumes** → Add volume
2. Mount path: `/data`
3. Set env var: `DB_PATH=/data/restaurant.db`

### 1-e. Custom domain + SSL

1. Railway dashboard → your service → **Settings** → **Custom Domain**
2. Add your domain, copy the CNAME record
3. Add CNAME in your DNS provider
4. SSL is provisioned automatically (Let's Encrypt)
5. Update `BASE_URL` to your custom domain

---

## 2. Render

### 2-a. Deploy via render.yaml (already included)

```bash
git push origin main
```

Connect your GitHub repo in [render.com](https://render.com) → New Web Service → select repo.
`render.yaml` configures the service automatically.

### 2-b. Environment variables

In Render dashboard → your service → **Environment**, add same vars as Railway above.

### 2-c. PostgreSQL on Render

1. Render dashboard → **New** → **PostgreSQL** → create
2. Copy the **Internal Database URL**
3. Add as env var: `DATABASE_URL=<internal-url>`

### 2-d. Custom domain + SSL

1. Render dashboard → your service → **Settings** → **Custom Domains**
2. Add domain → copy the CNAME/A record
3. Add in DNS provider
4. SSL auto-provisioned

---

## 3. Docker (self-hosted / VPS)

```bash
# Build
docker build -t restaurant-saas .

# Run with SQLite (persistent volume)
docker run -d \
  -p 8000:8000 \
  -v /srv/restaurant-data:/data \
  -e JWT_SECRET="$(openssl rand -hex 32)" \
  -e BASE_URL="https://your-domain.com" \
  -e OPENAI_API_KEY="sk-..." \
  -e DB_PATH="/data/restaurant.db" \
  --name restaurant-saas \
  restaurant-saas
```

### Nginx reverse proxy + SSL (Certbot)

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
certbot --nginx -d your-domain.com
```

---

## 4. Channel Setup (per restaurant)

### Telegram

1. Message @BotFather → `/newbot` → get **TOKEN**
2. In Dashboard → Channels → Telegram → paste token → **Test**
3. Click **Register Webhook** — this calls Telegram's `setWebhook` automatically
4. Webhook URL set: `{BASE_URL}/webhook/telegram/{restaurant_id}`

> **Important**: `BASE_URL` must be `https://` for Telegram webhooks. Use ngrok locally:
> ```bash
> ngrok http 8000
> # Set BASE_URL=https://xxxx.ngrok-free.app
> ```

### WhatsApp Cloud API

1. [Meta Developer Console](https://developers.facebook.com) → Your App → WhatsApp → API Setup
2. Get: **Phone Number ID**, **Temporary Access Token** (or create permanent System User token)
3. In Dashboard → Channels → WhatsApp → fill all fields including a **Verify Token** (any string you choose)
4. In Meta Console → Webhooks → Configure:
   - Callback URL: `{BASE_URL}/webhook/whatsapp/{restaurant_id}`
   - Verify Token: (same string you saved in channel)
   - Subscribe fields: `messages`
5. Test by sending a WhatsApp message to the registered number

### Instagram Messaging

1. Meta App must have **Instagram Graph API** product added
2. Connect an Instagram Business/Creator account to a Facebook Page
3. Get a **Page Access Token** with `instagram_manage_messages` permission
4. In Dashboard → Channels → Instagram → fill token + verify_token + app_secret
5. In Meta Console → Webhooks → Instagram:
   - Callback URL: `{BASE_URL}/webhook/instagram/{restaurant_id}`
   - Verify Token: (same as saved)
   - Subscribe: `messages`, `messaging_postbacks`

### Facebook Messenger

1. Meta App → Messenger product → Settings
2. Get **Page Access Token** for your Facebook Page
3. In Dashboard → Channels → Facebook → fill token + verify_token + page_id
4. In Meta Console → Webhooks → Messenger:
   - Callback URL: `{BASE_URL}/webhook/facebook/{restaurant_id}`
   - Verify Token: (same as saved)
   - Subscribe: `messages`, `messaging_postbacks`

---

## 5. Security Checklist

- [ ] `JWT_SECRET` is at least 32 random characters
- [ ] `ALLOWED_ORIGINS` is restricted to your domain (not `*`)
- [ ] Super admin password changed from `super123`
- [ ] Demo restaurant password changed from `admin123`
- [ ] `BASE_URL` uses `https://`
- [ ] `DATABASE_URL` is set for PostgreSQL (not SQLite in production with multiple instances)
- [ ] Backups configured: `crontab -e` → `0 3 * * * /app/scripts/backup.sh`
- [ ] Health check configured: `GET /health` returns `{"status":"ok"}`

---

## 6. Subscription Plans

Plans are enforced automatically:

| Plan | Products | Staff | Channels |
|---|---|---|---|
| Trial | 10 | 2 | 1 |
| Starter | 50 | 5 | 2 |
| Professional | 200 | 15 | 4 |
| Enterprise | Unlimited | Unlimited | 10 |

- Subscriptions expire automatically (background job runs every hour)
- Expired/suspended restaurants get 402 on all API calls
- Manage via Super Admin → Subscriptions

---

## 7. Monitoring

```bash
# Health check
curl https://your-domain.com/health

# Run E2E tests
BASE_URL=https://your-domain.com bash scripts/test_e2e.sh

# Manual backup
bash scripts/backup.sh
```

### Logs

- Railway: `railway logs`
- Render: Dashboard → Logs
- Docker: `docker logs restaurant-saas -f`

---

## 8. First Login Checklist

1. Open `{BASE_URL}` → login with `admin@restaurant.com` / `admin123`
2. Dashboard → Settings → change email + password
3. Dashboard → Channels → configure at least one channel
4. Open `{BASE_URL}/super/login` → login with `superadmin@platform.com` / `super123`
5. Super Admin → Subscriptions → set plan + end date for the demo restaurant
6. Verify alerts in Super Admin → Alerts
