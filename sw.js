const CACHE = 'hechos-bvl-v1';
const ASSETS = ['./index.html', './manifest.json'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});

// Placeholder for future push notifications once connected to a real backend
self.addEventListener('push', (e) => {
  const data = e.data ? e.data.json() : { title: 'Hechos BVL', body: 'Nuevo hecho de importancia publicado.' };
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: 'icon-192.png',
    })
  );
});
