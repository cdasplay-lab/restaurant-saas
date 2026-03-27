# نشر المشروع على Render

## متطلبات مسبقة
- حساب على [render.com](https://render.com) (الخطة المجانية تكفي للبداية)
- الكود مرفوع على GitHub أو GitLab

---

## Build Command
```
pip install -r requirements.txt
```

## Start Command
```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## خطوات النشر اليدوية على Render

### 1. إنشاء الخدمة
1. اذهب إلى [dashboard.render.com](https://dashboard.render.com) → **New** → **Web Service**
2. اختر مستودعك من GitHub/GitLab
3. اضبط الإعدادات:
   - **Name**: `restaurant-saas` (أو أي اسم)
   - **Region**: أقرب منطقة لك
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### 2. إضافة متغيرات البيئة
في تبويب **Environment** أضف المتغيرات التالية:

| المتغير | القيمة | ملاحظة |
|---------|--------|--------|
| `JWT_SECRET` | *(اضغط Generate)* | Render يولده تلقائياً |
| `OPENAI_API_KEY` | `sk-...` | من platform.openai.com |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `BASE_URL` | `https://YOUR-APP.onrender.com` | بعد أول deploy |
| `ALLOWED_ORIGINS` | `https://YOUR-NETLIFY.netlify.app` | دومين الواجهة الأمامية |
| `SESSION_HOURS` | `24` | |
| `DB_PATH` | `/opt/render/project/src/restaurant.db` | |
| `SUPABASE_URL` | `https://xxxx.supabase.co` | |
| `SUPABASE_ANON_KEY` | `sb_publishable_...` | |
| `SUPABASE_SERVICE_ROLE_KEY` | `sb_secret_...` | ⚠️ سري جداً |
| `SUPABASE_STORAGE_BUCKET_MENUS` | `menus` | |
| `SUPABASE_STORAGE_BUCKET_PRODUCTS` | `products` | |

### 3. أول Deploy
1. اضغط **Create Web Service**
2. انتظر حتى يكتمل البناء (3-5 دقائق)
3. تحقق من السجلات — يجب أن ترى:
   ```
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://0.0.0.0:10000
   ```

### 4. تحديث BASE_URL
بعد ظهور URL الخدمة (مثل `https://restaurant-saas.onrender.com`):
1. اذهب إلى **Environment** → عدّل `BASE_URL` ليكون هذا الرابط
2. اضغط **Save Changes** — سيُعيد Render النشر تلقائياً

### 5. التحقق من الصحة
```bash
# Health check
curl https://YOUR-APP.onrender.com/health

# يجب أن يرجع:
# {"status":"ok","version":"3.0.0","db":"sqlite"}
```

---

## ⚠️ تحذير: SQLite على Render (خطة مجانية)

الخطة المجانية على Render تستخدم **Ephemeral Storage** — أي أن البيانات **تُمسح عند كل restart**.

**الحلول:**

### الخيار أ: PostgreSQL على Render (موصى به)
1. **New** → **PostgreSQL** → أنشئ قاعدة بيانات
2. انسخ الـ `Internal Database URL`
3. أضف متغير بيئة: `DATABASE_URL=postgresql://...`
4. في `render.yaml` فك تعليق قسم `databases`

### الخيار ب: Render Disk (مدفوع)
1. في إعدادات الخدمة → **Disks** → أضف disk
2. **Mount Path**: `/data`
3. عدّل `DB_PATH` إلى `/data/restaurant.db`

---

## CORS للإنتاج

إذا الواجهة الأمامية على Netlify:
```
ALLOWED_ORIGINS=https://your-app.netlify.app
```

إذا عندك دومين مخصص أيضاً:
```
ALLOWED_ORIGINS=https://your-app.netlify.app,https://yourdomain.com
```

> **تحذير**: لا تترك `ALLOWED_ORIGINS=*` في الإنتاج أبداً.

---

## Supabase Storage

الرفع يمر عبر الـ backend فقط:
- `POST /api/upload/menu-pdf` — رفع PDF القائمة
- `POST /api/upload/product-image` — رفع صورة المنتج الرئيسية
- `POST /api/upload/gallery-image` — رفع صورة لمعرض المنتج

**لا تضع `SUPABASE_SERVICE_ROLE_KEY` في الواجهة الأمامية (Netlify) أبداً.**

---

## نشر الواجهة الأمامية على Netlify

الملفات الثابتة موجودة في `public/`:
- `app.html` → صفحة لوحة تحكم المطعم
- `login.html` → صفحة تسجيل الدخول
- `super.html` → لوحة Super Admin
- `super_login.html` → تسجيل دخول Super Admin

### إعداد `netlify.toml`
```toml
[[redirects]]
  from = "/"
  to = "/app.html"
  status = 200

[[redirects]]
  from = "/login"
  to = "/login.html"
  status = 200

[[redirects]]
  from = "/super"
  to = "/super.html"
  status = 200

[[redirects]]
  from = "/super/login"
  to = "/super_login.html"
  status = 200
```

### متغيرات بيئة Netlify
```
VITE_API_BASE=https://YOUR-APP.onrender.com
```
أو استخدم ملف `public/config.js`:
```js
window.API_BASE = "https://YOUR-APP.onrender.com";
```

---

## روابط المنصة بعد النشر

| الصفحة | الرابط |
|--------|--------|
| لوحة تحكم المطعم | `https://YOUR-NETLIFY.netlify.app/` |
| تسجيل دخول المطعم | `https://YOUR-NETLIFY.netlify.app/login` |
| لوحة Super Admin | `https://YOUR-NETLIFY.netlify.app/super` |
| تسجيل دخول Super Admin | `https://YOUR-NETLIFY.netlify.app/super/login` |
| API Health Check | `https://YOUR-APP.onrender.com/health` |

---

## بيانات الدخول الافتراضية (غيّرها فوراً!)

| الحساب | البريد | كلمة المرور |
|--------|--------|------------|
| مطعم | admin@restaurant.com | admin123 |
| Super Admin | superadmin@platform.com | super123 |

> **مهم**: غيّر كلمتَي المرور فور أول تسجيل دخول.
