# Netlify Deployment Guide

This guide explains how to deploy the Restaurant SaaS frontend to Netlify and connect it to your backend hosted on Railway or Render.

## Architecture

```
Netlify (Frontend)          Railway / Render (Backend)
┌────────────────┐          ┌──────────────────────────┐
│  public/       │  HTTPS   │  FastAPI (main.py)        │
│  app.html      │ ───────► │  /api/*                   │
│  login.html    │          │  /webhook/*               │
│  super.html    │          │  SQLite / PostgreSQL       │
│  config.js     │          │  Supabase Storage          │
└────────────────┘          └──────────────────────────┘
```

## Prerequisites

- Backend already deployed on Railway or Render (note the URL, e.g. `https://api.yourapp.railway.app`)
- Netlify account (free tier works)
- Git repository with this project

## Step 1 — Deploy Backend First

Make sure your backend is running and accessible. Test it:

```bash
curl https://api.yourapp.railway.app/health
# Should return {"status": "ok", ...}
```

## Step 2 — Deploy Frontend to Netlify

### Option A: Netlify CLI

```bash
npm install -g netlify-cli
netlify login
netlify deploy --dir=public --prod
```

### Option B: Netlify Dashboard (Drag & Drop)

1. Go to https://app.netlify.com
2. Drag the `public/` folder onto the deploy zone
3. Your site will be live immediately

### Option C: Connect Git Repository (Recommended)

1. Go to https://app.netlify.com → "Add new site" → "Import an existing project"
2. Connect your GitHub/GitLab repository
3. Build settings are read from `netlify.toml` automatically:
   - **Build command**: `bash scripts/build_config.sh`
   - **Publish directory**: `public`
4. Click "Deploy site"

> The build script generates `public/config.js` from environment variables at build time.

## Step 3 — Configure API_BASE Environment Variable

This is the most important step. Without it, the frontend will try to call `/api/*` on the Netlify domain instead of your backend.

### In Netlify Dashboard:

1. Go to **Site settings** → **Environment variables**
2. Add a new variable:
   - **Key**: `API_BASE`
   - **Value**: `https://api.yourapp.railway.app` (your actual backend URL, no trailing slash)
3. Click "Save"

### Using a `_headers` injection (alternative):

If you want to inject `API_BASE` dynamically via a script tag, you can use Netlify Edge Functions or add it directly to `public/config.js` before deploying.

### Redeploy after setting environment variables:

```bash
netlify deploy --prod
```

## Step 4 — Configure CORS on Backend

Make sure your backend allows requests from your Netlify domain.

In your `.env` (Railway/Render environment variables):

```
ALLOWED_ORIGINS=https://your-site.netlify.app,https://yourdomain.com
```

Or for development:

```
ALLOWED_ORIGINS=*
```

## Step 5 — Set Up Custom Domain (Optional)

1. Go to **Site settings** → **Domain management**
2. Add your custom domain
3. Netlify will provision an SSL certificate automatically
4. Update `ALLOWED_ORIGINS` on backend to include your custom domain

## Routing

The `public/_redirects` file and `netlify.toml` configure URL routing:

| URL | Served File |
|-----|------------|
| `/` or `/*` | `app.html` |
| `/login` | `login.html` |
| `/super` | `super.html` |
| `/super/login` | `super_login.html` |

## Troubleshooting

### "Failed to fetch" errors

- Check that `API_BASE` is set correctly in Netlify environment variables
- Verify the backend is running: `curl https://your-backend-url/health`
- Check CORS settings on the backend

### Login redirects to wrong URL

- The frontend redirects to `/app` after login — Netlify routing handles this via `_redirects`
- Make sure `public/_redirects` is present in your repository

### 404 on page refresh

- This is handled by the `[[redirects]]` rules in `netlify.toml`
- All routes fall back to `app.html` which handles client-side routing

### Environment variables not applied

- After setting environment variables in Netlify, you must trigger a new deploy
- In Netlify Dashboard → Deploys → "Trigger deploy" → "Deploy site"

## Security Headers

The `netlify.toml` configures security headers for all pages:

- `X-Frame-Options: DENY` — prevents clickjacking
- `X-Content-Type-Options: nosniff` — prevents MIME sniffing
- `Referrer-Policy: strict-origin-when-cross-origin` — controls referrer information

`config.js` is served with `Cache-Control: no-cache` so that environment-specific configuration is always fresh.
