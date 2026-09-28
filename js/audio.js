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

// Buttons of the clip that is playing get .is-playing (static playing state, no animation) and, when they
// have data-stop-label, aria-pressed="true" + that label ("Stop"): tapping them again stops the audio.
function setPlaying(b, on) {
  b.classList.toggle('is-playing', on);
  if (b.dataset.stopLabel) {
    b.setAttribute('aria-pressed', String(on));
    b.setAttribute('aria-label', on ? b.dataset.stopLabel : (b.dataset.label || b.getAttribute('aria-label')));
  }
}
function clearPlaying() {
  document.querySelectorAll('.is-playing').forEach(b => setPlaying(b, false));
}
el.addEventListener('ended', clearPlaying);
// A pause queued by stop() can arrive after the next clip has started; only clear if we're really paused.
el.addEventListener('pause', () => { if (el.paused) clearPlaying(); });
/** Is the player playing right now (not paused, not ended)? */
export const isPlaying = () => !el.paused && !el.ended;

export function stop() { try { el.pause(); } catch {} clearPlaying(); }

/** Play the sentence. slow = the pre-generated slow file (or 0.75x playbackRate as a fallback).
 *  voice = play the dialogue-line file recorded in that voice, when there is one.
 *  btn = the button (or buttons) that show the playing state while this clip plays. */
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
  const btns = (Array.isArray(btn) ? btn : [btn]).filter(Boolean);
  btns.forEach((b) => setPlaying(b, true));
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
