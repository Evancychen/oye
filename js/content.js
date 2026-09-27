// Runtime content: version check, download, offline storage (Cache Storage).
const CONTENT_CACHE = 'oye-content';           // shared with sw.js; survives app-shell updates
const JSON_FILES = ['cards.json', 'scenes.json', 'vocab.json', 'audio.json', 'missions.json'];
const OPTIONAL = { 'audio.json': '{"items":{}}', 'missions.json': '[]' }; // v2: missions.json is new, older content has none
const LS_VERSION = 'oye.contentVersion';

const hasCaches = typeof caches !== 'undefined';
const readLS = (k) => { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch { return null; } };
const writeLS = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
const jsonResponse = (text) => new Response(text, { headers: { 'Content-Type': 'application/json' } });

async function fetchVersion() {
  try {
    const r = await fetch('content/version.json', { cache: 'no-store' });
    if (r.ok) return await r.json();
  } catch {}
  return null; // offline or server down
}

async function readCachedJson(cache, file) {
  if (!cache) return null;
  const r = await cache.match(`content/${file}`);
  if (!r) return null;
  try { return await r.json(); } catch { return null; }
}

async function downloadAll(version) {
  // Fetch everything first, then store, so a failed download never leaves a half-updated set.
  const texts = {};
  for (const f of JSON_FILES) {
    const r = await fetch(`content/${f}?fresh=${encodeURIComponent(version.version)}`, { cache: 'no-store' });
    if (!r.ok) {
      if (OPTIONAL[f]) { texts[f] = OPTIONAL[f]; continue; }
      throw new Error(`${f}: HTTP ${r.status}`);
    }
    texts[f] = await r.text();
    JSON.parse(texts[f]); // throws on a broken file
  }
  return texts;
}

/**
 * Load content. Online + new version -> download and store. Otherwise use the stored copy.
 * Returns { cards, scenes, vocab, missions, audio, version, updated, audioSync }.
 */
export async function loadContent(onAudioProgress) {
  const cache = hasCaches ? await caches.open(CONTENT_CACHE) : null;
  const remote = await fetchVersion();
  const local = readLS(LS_VERSION) || await readCachedJson(cache, 'version.json');
  let data = null, updated = false, online = !!remote;

  let haveAll = true;
  for (const f of JSON_FILES) if (!cache || !(await cache.match(`content/${f}`))) { haveAll = false; break; }

  if (remote && (!local || local.version !== remote.version || !haveAll)) {
    try {
      const texts = await downloadAll(remote);
      if (cache) {
        for (const f of JSON_FILES) await cache.put(`content/${f}`, jsonResponse(texts[f]));
        await cache.put('content/version.json', jsonResponse(JSON.stringify(remote)));
      }
      writeLS(LS_VERSION, remote);
      data = Object.fromEntries(JSON_FILES.map(f => [f, JSON.parse(texts[f])]));
      updated = true;
    } catch (e) {
      console.warn('[oye] content download failed, using stored copy', e);
    }
  }
  if (!data && cache) {
    data = {};
    for (const f of JSON_FILES) data[f] = await readCachedJson(cache, f);
    if (!data['cards.json']) data = null;
  }
  if (!data) {
    // No Cache Storage (insecure context) or nothing stored yet: plain network.
    data = {};
    for (const f of JSON_FILES) {
      try { const r = await fetch(`content/${f}`); data[f] = r.ok ? await r.json() : null; } catch { data[f] = null; }
    }
    if (!data['cards.json']) throw new Error('No content available. Connect to the internet once to download it.');
  }
  const audio = data['audio.json'] || { items: {} };
  const version = remote || local || { version: 0, new_cards: 0 };
  const audioSync = online && cache ? syncAudio(cache, audio, onAudioProgress) : Promise.resolve({ total: 0, missing: 0 });
  return {
    cards: Array.isArray(data['cards.json']) ? data['cards.json'] : [],
    scenes: Array.isArray(data['scenes.json']) ? data['scenes.json'] : [],
    vocab: Array.isArray(data['vocab.json']) ? data['vocab.json'] : [],
    missions: Array.isArray(data['missions.json']) ? data['missions.json'] : [],
    audio, version, updated, online, audioSync,
  };
}

/** Download audio files that aren't stored yet; drop stored audio no card uses any more. */
async function syncAudio(cache, audio, onProgress) {
  const entries = ['items', 'lines', 'missions'].flatMap(k => Object.values(audio[k] || {}));
  const urls = [...new Set(entries.flatMap(e => [e.normal, e.slow]).filter(Boolean))];
  const abs = new Set(urls.map(u => new URL(u, location.href).href));
  const missing = [];
  for (const u of urls) if (!(await cache.match(u))) missing.push(u);
  let done = 0, failed = 0;
  onProgress?.({ done, total: missing.length, failed });
  const queue = [...missing];
  const worker = async () => {
    while (queue.length) {
      const u = queue.shift();
      try {
        const r = await fetch(u, { cache: 'no-store' });
        if (!r.ok) throw new Error(r.status);
        await cache.put(u, r);
      } catch { failed++; }
      done++;
      onProgress?.({ done, total: missing.length, failed });
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  for (const req of await cache.keys()) {
    if (req.url.includes('/audio/') && !abs.has(req.url)) await cache.delete(req);
  }
  return { total: urls.length, missing: missing.length, failed };
}
