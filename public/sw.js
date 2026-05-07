const CACHE = 'restaurant-v1';
const STATIC = [
  '/app.html',
  '/login.html',
  '/config.js',
  'https://cdn.tailwindcss.com',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC).catch(() => {}))
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const { request } = e;
  const url = new URL(request.url);

  // Never cache API calls or POST requests
  if (url.pathname.startsWith('/api/') || request.method !== 'GET') return;

  // Network-first for HTML pages (always fresh)
  if (request.destination === 'document') {
    e.respondWith(
      fetch(request)
        .then(r => { caches.open(CACHE).then(c => c.put(request, r.clone())); return r; })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Cache-first for static assets (CDN libs, fonts, icons)
  if (url.origin !== self.location.origin || request.destination === 'script' ||
      request.destination === 'style' || request.destination === 'font') {
    e.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(r => {
        caches.open(CACHE).then(c => c.put(request, r.clone()));
        return r;
      }))
    );
  }
});

// Push notification handler for order alerts
self.addEventListener('push', e => {
  if (!e.data) return;
  const { title = 'طلب جديد', body = '', icon = '/manifest.json' } = e.data.json();
  e.waitUntil(
    self.registration.showNotification(title, { body, icon, dir: 'rtl', lang: 'ar', badge: icon })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(cs => {
      const c = cs.find(x => x.url.includes('/app.html'));
      return c ? c.focus() : clients.openWindow('/app.html#orders');
    })
  );
});
