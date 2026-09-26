// Oye service worker: offline app shell + content + audio.
// CACHE_VERSION is rewritten by tools/publish_content.py (content version + hash of the shell files).
const CACHE_VERSION = 'oye-v2-76258145';
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const CONTENT_CACHE = 'oye-content'; // filled by js/content.js (JSON + audio); kept across shell updates

const SHELL = [
  './',
  'index.html',
  'styles.css',
  'manifest.webmanifest',
  'js/app.js',
  'js/content.js',
  'js/audio.js',
  'js/check.js',
  'js/srs.js',
  'fonts/inter-latin.woff2',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-192-maskable.png',
  'icons/icon-512-maskable.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await cache.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('oye-') && key !== SHELL_CACHE && key !== CONTENT_CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const scope = self.registration.scope;
  const rel = req.url.startsWith(scope) ? req.url.slice(scope.length) : '';

  // Always ask the network for the version file; fall back to the stored copy offline.
  if (rel.startsWith('content/version.json')) {
    event.respondWith(fetch(req, { cache: 'no-store' }).catch(async () =>
      (await caches.match('content/version.json', { ignoreSearch: true })) || new Response('null', { status: 503, headers: { 'Content-Type': 'application/json' } })));
    return;
  }
  // The app downloads a new content version with ?fresh=N and stores it itself.
  if (url.searchParams.has('fresh')) return;

  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      const shell = await caches.open(SHELL_CACHE);
      return (await shell.match('index.html')) || (await shell.match('./')) || fetch(req);
    })().catch(() => fetch(req)));
    return;
  }

  event.respondWith((async () => {
    const hit = await caches.match(req, { ignoreVary: true });
    if (hit) return hit;
    const res = await fetch(req);
    if (res.ok && res.status === 200 && (rel.startsWith('audio/') || rel.startsWith('content/'))) {
      const cache = await caches.open(CONTENT_CACHE);
      cache.put(req, res.clone());
    }
    return res;
  })());
});
