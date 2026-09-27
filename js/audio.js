// Audio playback. Files come from Cache Storage (offline) or the network.
// v2.1: audio.json also has `lines` ("male|text" / "female|text": one dialogue line in the
// speaker's own voice) and `missions` (a Hard mission's whole script as one clip + duration).
let index = { items: {} };
const el = new Audio();
el.preload = 'auto';
window.__oyeAudioEl = el; // exposed for tests
const blobUrls = new Map();

export function setAudioIndex(idx) { index = idx || { items: {} }; }
export function hasAudio(text) { return !!(text && index.items && index.items[text]); }
const lineEntry = (text, voice) => (text && voice && index.lines ? index.lines[`${voice}|${text}`] : null);
/** A dialogue line in the given voice ('male' | 'female'); falls back to the sentence audio. */
export function hasLine(text, voice) { return !!lineEntry(text, voice) || hasAudio(text); }
export function clipInfo(id) { return (index.missions && index.missions[id]) || null; }
export function audioEl() { return el; }

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

/** Play the sentence. slow = the pre-generated slow file (or 0.75x playbackRate as a fallback).
 *  voice = play the dialogue-line file recorded in that voice, when there is one. */
export async function play(text, { slow = false, btn = null, voice = null } = {}) {
  const line = lineEntry(text, voice);
  const item = line || (index.items && index.items[text]);
  if (!item) return false;
  const url = slow ? (item.slow || item.normal) : item.normal;
  const stretch = slow && !item.slow;
  stop();
  el.dataset.clip = '';
  el.src = await srcFor(url);
  el.preservesPitch = true;
  el.playbackRate = stretch ? 0.75 : 1;
  el.defaultPlaybackRate = el.playbackRate;
  if (btn) btn.classList.add('is-playing');
  try { await el.play(); return true; }
  catch { clearPlaying(); return false; } // autoplay blocked or file missing
}

/** Mission player: play clip `id` (normal or slow file), starting at fraction `at` (0..1). */
export async function playClip(id, { slow = false, at = 0 } = {}) {
  const e = clipInfo(id);
  if (!e) return false;
  stop();
  const src = await srcFor(slow ? (e.slow || e.normal) : e.normal);
  el.dataset.clip = id;
  el.dataset.slow = slow && e.slow ? '1' : '';
  el.src = src;
  el.preservesPitch = true;
  el.playbackRate = slow && !e.slow ? 0.75 : 1;
  el.defaultPlaybackRate = el.playbackRate;
  if (at > 0) {
    await new Promise((res) => {
      if (el.readyState >= 1) return res();
      el.addEventListener('loadedmetadata', res, { once: true });
      setTimeout(res, 1500);
    });
    if (isFinite(el.duration)) el.currentTime = Math.min(el.duration - 0.05, at * el.duration);
  }
  try { await el.play(); return true; } catch { return false; }
}
/** Is clip `id` the one loaded in the player? */
export const clipLoaded = (id) => el.dataset.clip === id && !!el.src;
