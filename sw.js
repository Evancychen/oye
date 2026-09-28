// Oye service worker: offline app shell + content + audio.
// CACHE_VERSION is rewritten by tools/publish_content.py (content version + hash of the shell files).
const CACHE_VERSION = 'oye-v6-9c1d433a';
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
  'js/results.js',
  'js/levels.js',
  'fonts/inter-latin.woff2',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-192-maskable.png',
  'icons/icon-512-maskable.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // Fetch each file with a version query so no HTTP/CDN cache can hand back an old copy,
    // then store it under its plain URL (what the page asks for).
    await Promise.all(SHELL.map(async (u) => {
      const res = await fetch(new Request(`${u}${u.includes('?') ? '&' : '?'}v=${CACHE_VERSION}`, { cache: 'reload' }));
      if (!res.ok) throw new Error(`${u}: HTTP ${res.status}`);
      await cache.put(u, res);
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  const ready = (async () => {
    const keys = await caches.keys();
    const isUpdate = keys.some((k) => k.startsWith('oye-') && k.endsWith('-shell') && k !== SHELL_CACHE);
    for (const key of keys) {
      if (key.startsWith('oye-') && key !== SHELL_CACHE && key !== CONTENT_CACHE) await caches.delete(key);
    }
    await self.clients.claim();
    return isUpdate;
  })();
  event.waitUntil(ready);
  // After activation (not inside waitUntil, so navigations aren't held up): move open windows to the new shell.
  ready.then((isUpdate) => { if (isUpdate) setTimeout(refreshClients, 0); }).catch(() => {});
});

// Open windows running v1.1+ ack 'oye-update' and reload themselves when it's safe (not mid-session).
// Windows that don't ack within 3 s (the v1 shell has no handler) are reloaded here.
const acks = new Map();
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'oye-update-ack' && event.source) {
    const done = acks.get(event.source.id);
    if (done) done();
  }
});
async function refreshClients() {
  const wins = await self.clients.matchAll({ type: 'window' });
  await Promise.all(wins.map(async (client) => {
    const acked = new Promise((resolve) => { acks.set(client.id, () => resolve(true)); setTimeout(() => resolve(false), 3000); });
    try { client.postMessage({ type: 'oye-update', cache: CACHE_VERSION }); } catch (e) {}
    const ok = await acked;
    acks.delete(client.id);
    if (!ok && 'navigate' in client) {
      try { await client.navigate(client.url); } catch (e) {}
    }
  }));
}

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
