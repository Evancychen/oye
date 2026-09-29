// Spaced repetition (Leitner boxes) + stats, stored in localStorage.
const KEY = 'oye.progress.v1';
export const SESSION_SIZE = 10;
export const MIN_SESSION = 8;
export const DAILY_GOAL = 20;
const DAILY_NEW_CAP = 15;          // at most this many never-seen cards per day
const NEW_WHEN_REVIEWS = 3;        // new cards per session when plenty of reviews are due
const INTERVAL_DAYS = [0, 1, 2, 4, 8, 16]; // index = box (1..5)
const TYPE_ORDER = ['listen_pick', 'listen_type', 'scene_question', 'fix_it', 'reply'];

export function todayKey(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function addDays(key, n) {
  const [y, m, d] = key.split('-').map(Number);
  return todayKey(new Date(y, m - 1, d + n));
}
function daysBetween(a, b) {
  const [y1, m1, d1] = a.split('-').map(Number), [y2, m2, d2] = b.split('-').map(Number);
  return Math.round((new Date(y2, m2 - 1, d2) - new Date(y1, m1 - 1, d1)) / 86400000);
}

export function load() {
  let s = null;
  try { s = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch {}
  s = s && typeof s === 'object' ? s : {};
  s.cards ||= {}; s.days ||= {}; s.history ||= []; s.lesson ||= 'all';
  return s;
}
export function save(s) {
  if (s.history.length > 3000) s.history = s.history.slice(-3000);
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {}
}

/** Record one answer. ok = first-try correct. hint = the Hint was opened (v1.1; optional, stats only). */
export function record(s, card, ok, { hint = false } = {}) {
  const t = todayKey();
  const c = s.cards[card.id] || { box: 0, seen: 0, right: 0, wrong: 0 };
  const isNew = !c.seen;
  c.seen++; ok ? c.right++ : c.wrong++;
  c.box = ok ? Math.min(5, Math.max(1, c.box) + 1) : 1;
  c.due = addDays(t, INTERVAL_DAYS[c.box]);
  c.last = t; c.lastOk = ok;
  s.cards[card.id] = c;
  const d = s.days[t] || { answered: 0, correct: 0, introduced: 0, sessions: 0 };
  d.answered++; if (ok) d.correct++; if (isNew) d.introduced++;
  s.days[t] = d;
  const h = { d: t, id: card.id, ok: ok ? 1 : 0, p: isPriceByEar(card) ? 1 : 0 };
  if (hint) { h.h = 1; c.hints = (c.hints || 0) + 1; }
  s.history.push(h);
  save(s);
}
export function finishSession(s) {
  const t = todayKey();
  const d = s.days[t] || { answered: 0, correct: 0, introduced: 0, sessions: 0 };
  d.sessions++; s.days[t] = d; save(s);
}

/** v2: a finished Hard mission counts for the streak (n = questions answered). */
export function recordActivity(s, n) {
  const t = todayKey();
  const d = s.days[t] || { answered: 0, correct: 0, introduced: 0, sessions: 0 };
  d.answered += Math.max(1, n || 0); d.sessions++;
  s.days[t] = d; save(s);
}

export function isPriceByEar(card) {
  const tags = card.grammar_tags || [];
  return !!card.audio_text && (tags.includes('prices') || tags.includes('numbers'));
}

// ---------- session planning ----------
function weight(c) {
  // Weighted toward cards answered wrong and low boxes.
  const wrongRate = c.seen ? c.wrong / c.seen : 0;
  return 1 + (c.lastOk === false ? 6 : 0) + 4 * wrongRate + (5 - (c.box || 1));
}
function weightedSample(items, wfn, n, rnd = Math.random) {
  const pool = items.map(x => ({ x, w: Math.max(0.01, wfn(x)) }));
  const out = [];
  while (out.length < n && pool.length) {
    const total = pool.reduce((a, b) => a + b.w, 0);
    let r = rnd() * total, i = 0;
    for (; i < pool.length - 1; i++) { r -= pool[i].w; if (r <= 0) break; }
    out.push(pool.splice(i, 1)[0].x);
  }
  return out;
}
function pickNew(cards, n, newIds) {
  // Round-robin across types so a session mixes templates; easier + newest first within a type.
  const byType = new Map();
  const sorted = [...cards].sort((a, b) =>
    (newIds.has(b.id) - newIds.has(a.id)) || ((a.difficulty || 2) - (b.difficulty || 2)) || String(a.id).localeCompare(String(b.id)));
  for (const c of sorted) { if (!byType.has(c.type)) byType.set(c.type, []); byType.get(c.type).push(c); }
  const types = [...byType.keys()].sort((a, b) => (TYPE_ORDER.indexOf(a) + 99) % 99 - (TYPE_ORDER.indexOf(b) + 99) % 99);
  const out = [];
  while (out.length < n && types.some(t => byType.get(t).length)) {
    for (const t of types) { const q = byType.get(t); if (q.length && out.length < n) out.push(q.shift()); }
  }
  return out;
}
function interleave(list) {
  // Avoid the same template twice in a row where possible; start with a listening card.
  const rest = [...list];
  const out = [];
  const firstIdx = rest.findIndex(c => c.type === 'listen_pick');
  if (firstIdx > 0) out.push(rest.splice(firstIdx, 1)[0]);
  while (rest.length) {
    const prev = out[out.length - 1];
    let i = rest.findIndex(c => !prev || c.type !== prev.type);
    if (i < 0) i = 0;
    out.push(rest.splice(i, 1)[0]);
  }
  return out;
}

/** Plan a Quick session from the playable cards. */
export function planSession(s, cards, { newIds = new Set(), size = SESSION_SIZE } = {}) {
  const t = todayKey();
  const seen = cards.filter(c => s.cards[c.id]?.seen);
  const fresh = cards.filter(c => !s.cards[c.id]?.seen);
  const due = seen.filter(c => (s.cards[c.id].due || t) <= t);
  const introducedToday = s.days[t]?.introduced || 0;
  const newCap = Math.max(0, DAILY_NEW_CAP - introducedToday);
  let newCount = due.length >= size - NEW_WHEN_REVIEWS ? NEW_WHEN_REVIEWS : size - due.length;
  newCount = Math.min(newCount, newCap, fresh.length);

  const picked = pickNew(fresh, newCount, newIds);
  picked.push(...weightedSample(due, c => weight(s.cards[c.id]), size - picked.length));
  if (picked.length < size) {
    // Early review: not due yet, lowest box / most recently wrong first.
    const notDue = seen.filter(c => !picked.includes(c));
    picked.push(...weightedSample(notDue, c => weight(s.cards[c.id]) * 2 / (1 + daysBetween(t, s.cards[c.id].due || t)), size - picked.length));
  }
  if (picked.length < MIN_SESSION) {
    picked.push(...pickNew(fresh.filter(c => !picked.includes(c)), MIN_SESSION - picked.length, newIds));
  }
  return interleave(picked.slice(0, size));
}

/** Fisher-Yates shuffle (a new array). */
export function shuffle(list, rnd = Math.random) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}
/** How likely a card is to be picked for a topic session or the Verb drill: never seen = 8; seen cards use
 *  weight() (wrong last time +6, wrong rate, low box), +3 when due today or overdue. A missed card (box 1,
 *  wrong last time) weighs 14-18, a card that is always right and not due weighs 1. */
export function pickWeight(s, c, t = todayKey()) {
  const st = s.cards[c.id];
  if (!st?.seen) return 8;
  return weight(st) + ((st.due || t) <= t ? 3 : 0);
}
/** A session copy of the card with its options in a random order (cards without options are returned as they are).
 *  The answer check compares option values (card.answer), never positions, so this is safe. */
export const withShuffledOptions = (c) => (Array.isArray(c.options) && c.options.length > 1 ? { ...c, options: shuffle(c.options) } : c);
/**
 * v2 Medium: a topic session. Up to `size` cards: all of them when the topic has `size` or fewer, else `size`
 * picked at random weighted toward missed and due cards (pickWeight). Every start shuffles the card order and
 * the order of each card's options. No daily cap, no due dates.
 */
export function planTopic(s, cards, { size = SESSION_SIZE, rnd = Math.random } = {}) {
  const t = todayKey();
  const picked = cards.length <= size ? [...cards] : weightedSample(cards, (c) => pickWeight(s, c, t), size, rnd);
  return shuffle(picked, rnd).map(withShuffledOptions);
}
/** v2.2 Verb drill: `size` (20) drill cards in a row, weighted toward missed and due cards, shuffled, options shuffled. */
export const DRILL_SIZE = 20;
export function planDrill(s, cards, { size = DRILL_SIZE, rnd = Math.random } = {}) {
  return planTopic(s, cards, { size, rnd });
}

// ---------- stats ----------
export function streak(s) {
  const t = todayKey();
  let day = s.days[t]?.answered ? t : addDays(t, -1);
  let n = 0;
  while (s.days[day]?.answered) { n++; day = addDays(day, -1); }
  return n;
}
export function last7(s) {
  const t = todayKey();
  return Array.from({ length: 7 }, (_, i) => !!s.days[addDays(t, i - 6)]?.answered);
}
export function answeredToday(s) { return s.days[todayKey()]?.answered || 0; }

/** Accuracy on prices/numbers by ear: last 7 days, the 7 days ending two weeks ago, and 7 weekly bars. */
export function pricesByEar(s) {
  const t = todayKey();
  const weeks = Array.from({ length: 7 }, () => ({ n: 0, ok: 0 }));
  for (const h of s.history) {
    if (!h.p) continue;
    const age = daysBetween(h.d, t);
    const w = Math.floor(age / 7);
    if (w >= 0 && w < 7) { weeks[6 - w].n++; weeks[6 - w].ok += h.ok; }
  }
  const pct = (w) => (w.n ? Math.round(100 * w.ok / w.n) : null);
  return { now: pct(weeks[6]), twoWeeksAgo: pct(weeks[4]), bars: weeks.map(pct), answers: weeks[6].n };
}
