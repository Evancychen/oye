// Audio playback. Files come from Cache Storage (offline) or the network.
let index = { items: {} };
const el = new Audio();
el.preload = 'auto';
window.__oyeAudioEl = el; // exposed for tests
const blobUrls = new Map();
let current = null; // { btn }

export function setAudioIndex(idx) { index = idx || { items: {} }; }
export function hasAudio(text) { return !!(text && index.items && index.items[text]); }

async function srcFor(url) {
  if (blobUrls.has(url)) return blobUrls.get(url);
  try {
    let r = typeof caches !== 'undefined' ? await caches.match(url) : null;
    if (!r) r = await fetch(url);
    if (!r.ok) throw new Error(r.status);
    const u = URL.createObjectURL(await r.blob());
    blobUrls.set(url, u);
    return u;
  } catch {
    return url;
  }
}

function clearPlaying() {
  document.querySelectorAll('.is-playing').forEach(b => b.classList.remove('is-playing'));
}
el.addEventListener('ended', clearPlaying);
el.addEventListener('pause', clearPlaying);

export function stop() { try { el.pause(); } catch {} clearPlaying(); }

/** Play the sentence. slow = the pre-generated slow file (or 0.75x playbackRate as a fallback). */
export async function play(text, { slow = false, btn = null } = {}) {
  const item = index.items && index.items[text];
  if (!item) return false;
  const url = slow ? (item.slow || item.normal) : item.normal;
  const stretch = slow && !item.slow;
  stop();
  el.src = await srcFor(url);
  el.preservesPitch = true;
  el.playbackRate = stretch ? 0.75 : 1;
  el.defaultPlaybackRate = el.playbackRate;
  if (btn) btn.classList.add('is-playing');
  try { await el.play(); return true; }
  catch { clearPlaying(); return false; } // autoplay blocked or file missing
}
