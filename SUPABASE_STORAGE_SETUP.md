# Supabase Storage Setup Guide

This guide explains how to set up Supabase Storage for the Restaurant SaaS platform to store menu files and product images.

## Overview

Supabase Storage is used for:
- **Menu files** — PDFs and images uploaded for AI parsing (`menus` bucket)
- **Product images** — Photos for menu items (`products` bucket)

The `services/storage.py` module handles all upload/delete operations using the Supabase Storage REST API.

## Step 1 — Create a Supabase Project

1. Go to https://supabase.com and sign in
2. Click "New Project"
3. Choose your organization, give the project a name, set a database password
4. Select a region close to your backend (e.g. if backend is on Railway US, choose US East)
5. Wait for the project to be provisioned (~2 minutes)

## Step 2 — Get Your API Keys

1. Go to **Project Settings** (gear icon) → **API**
2. Note the following values:
   - **Project URL** — e.g. `https://abcdefghijklmnop.supabase.co`
   - **anon / public** key — safe to use in frontend
   - **service_role** key — KEEP SECRET, only use on backend

## Step 3 — Create Storage Buckets

### Create the `menus` bucket

1. Go to **Storage** in the left sidebar
2. Click "New bucket"
3. Name: `menus`
4. Toggle **Public bucket** ON (so uploaded menu files have public URLs)
5. Click "Create bucket"

### Create the `products` bucket

1. Click "New bucket" again
2. Name: `products`
3. Toggle **Public bucket** ON
4. Click "Create bucket"

## Step 4 — Configure Bucket Policies (Optional but Recommended)

By default, public buckets allow anyone to read files. The service role key is used for uploads (server-side only), so this is safe.

If you want to restrict reads to authenticated users only, set the bucket to **private** and generate signed URLs instead. The current `storage.py` implementation uses public buckets.

### Recommended RLS policy for service role uploads:

The service role key bypasses RLS by default, so no additional policies are needed for server-side uploads.

## Step 5 — Add Environment Variables to Backend

Add these to your `.env` file (locally) or Railway/Render environment variables (production):

```env
SUPABASE_URL=https://abcdefghijklmnop.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_STORAGE_BUCKET_MENUS=menus
SUPABASE_STORAGE_BUCKET_PRODUCTS=products
```

IMPORTANT:
- Never expose `SUPABASE_SERVICE_ROLE_KEY` to the frontend
- The `SUPABASE_ANON_KEY` can be exposed to the frontend via `config.js` if needed for direct uploads
- The backend uses `SUPABASE_SERVICE_ROLE_KEY` for all upload operations

## Step 6 — Verify Setup

After configuring environment variables and restarting your backend:

```bash
# Get a token first
TOKEN=$(curl -s -X POST https://your-backend/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@restaurant.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Upload a product image (saves URL to products.image_url if product_id provided)
curl -s -X POST https://your-backend/api/upload/product-image \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/test-image.jpg" \
  -F "product_id=<optional-product-uuid>" \
  | python3 -m json.tool

# Upload a gallery image (appends URL to products.gallery_images)
curl -s -X POST https://your-backend/api/upload/gallery-image \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/gallery.jpg" \
  -F "product_id=<product-uuid>" \
  | python3 -m json.tool
```

Expected response:
```json
{
  "url": "https://abcdefghijklmnop.supabase.co/storage/v1/object/public/products/restaurants/.../image.jpg",
  "product_id": "uuid-of-product"
}
```

If Supabase is not configured, you will get:
```json
{
  "url": "",
  "message": "Supabase not configured — configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
}
```

## Storage Structure

Files are organized with the following path structure:

```
menus bucket:
  restaurants/{restaurant_id}/menus/{session_id}/{filename}

products bucket:
  restaurants/{restaurant_id}/products/{product_id}/{uuid}.ext   ← main image
  restaurants/{restaurant_id}/gallery/{product_id}/{uuid}.ext    ← gallery images
```

This structure makes it easy to:
- List all files for a specific restaurant
- Clean up files when a restaurant is deleted
- Organize files by feature (menus vs products)

## File Size Limits

The backend enforces:
- Product images: max 10 MB
- Accepted formats: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`

Supabase free tier storage limits:
- 1 GB total storage
- 50 MB per file (default)
- Unlimited file count

## Costs

Supabase free tier includes:
- 1 GB storage
- 2 GB bandwidth per month
- Suitable for development and small production deployments

Paid plans start at $25/month for 100 GB storage.

## Troubleshooting

### Upload returns 400 or 401

- Check that `SUPABASE_SERVICE_ROLE_KEY` is set correctly
- Verify the bucket exists in Supabase Dashboard → Storage
- Make sure the bucket name matches the environment variable

### Files upload but URLs return 403

- The bucket must be set to **Public** for public URL access
- Go to Storage → Buckets → click your bucket → toggle "Public" ON

### "Supabase not configured" message

- Verify `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set in your environment
- Restart the backend after setting environment variables
- Check backend logs for any startup errors

### httpx not installed

If you get `ModuleNotFoundError: No module named 'httpx'`, add it to requirements:

```bash
pip install httpx
echo "httpx" >> requirements.txt
```
