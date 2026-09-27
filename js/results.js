// Results upload: each finished session's answers go to the team's Google Sheet (Apps Script web app).
// Rows are written to a localStorage queue first and removed only after the endpoint answers { ok: true },
// so nothing is lost offline. The queue is retried on app open, on resume and when the phone comes online.
export const RESULTS_ENDPOINT = 'https://script.google.com/macros/s/AKfycbwD3ECxaglquY6aadvYPN9jxkrtElkiKCdRo7_gD7yY6uZAHZGlbvcY4XKBivJNZ4U98w/exec';
const QUEUE_KEY = 'oye.resultsQueue.v1';
const BATCH = 200;                       // the Apps Script keeps at most 200 rows per request
const TIMEOUT_MS = 30000;
// Ids the Apps Script (v3) accepts: card answers "c-NNNN", and Hard mission answers with mission_id "m-..."
// and card_id "<mission_id>:qN" (it also takes a bare "qN", which we normalise before queueing).
const CARD_ID = /^c-\d{4}$/;
const MISSION_ID = /^m-[a-z0-9]+(?:-[a-z0-9]+){0,8}$/;
const QUESTION_ID = /^q\d{1,2}$/;

let flushing = null;                     // one send at a time (per tab); navigator.locks covers other tabs
const listeners = new Set();

const rid = () => (globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`);
export const newSessionId = () => rid();

export function readQueue() {
  try { const q = JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]'); return Array.isArray(q) ? q : []; } catch { return []; }
}
function writeQueue(q) {
  try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); return true; } catch { return false; }
}
const notify = () => { const n = readQueue().length; listeners.forEach((fn) => { try { fn(n); } catch {} }); };
/** fn(queueLength) after every send attempt. Returns an unsubscribe function. */
export function onQueueChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

/** Mission rows: "qN" + mission_id -> "<mission_id>:qN" (the form the Sheet stores). Card rows are left alone. */
function normalise(r) {
  const cid = String(r.card_id ?? ''), mid = String(r.mission_id ?? '');
  return MISSION_ID.test(mid) && QUESTION_ID.test(cid) ? { ...r, card_id: `${mid}:${cid}` } : r;
}
/** Would the endpoint accept this row's ids? Card: c-NNNN. Mission: level hard, mission_id m-..., card_id "<mission_id>:qN". */
function acceptable(r) {
  const cid = String(r.card_id ?? '');
  if (CARD_ID.test(cid)) return true;
  const mid = String(r.mission_id ?? '');
  if (r.level !== 'hard' || !MISSION_ID.test(mid)) return false;
  const i = cid.lastIndexOf(':');
  return i > 0 && cid.slice(0, i) === mid && QUESTION_ID.test(cid.slice(i + 1));
}
/** Only ids the endpoint accepts and that exist in the loaded content (validIds = Set of card ids and
 *  "<mission_id>:qN" question ids, or null = don't know yet). */
const keep = (validIds) => (r) => acceptable(r) && (!validIds || validIds.has(r.card_id));

/** Add a finished session's rows to the queue. Returns the local ids of the rows that were queued. */
export function enqueue(rows, validIds) {
  const add = rows.map(normalise).filter(keep(validIds)).map((r) => ({ ...r, _id: rid() }));
  if (!add.length) return [];
  if (!writeQueue([...readQueue(), ...add])) return [];
  return add.map((r) => r._id);
}
export function isQueued(ids) {
  const q = new Set(readQueue().map((r) => r._id));
  return ids.some((id) => q.has(id));
}

async function postBatch(rows) {
  const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctl && setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    // text/plain keeps this a "simple" request (no CORS preflight, which Apps Script can't answer).
    // Apps Script replies with a redirect to the JSON; fetch follows it.
    const res = await fetch(RESULTS_ENDPOINT, {
      method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: JSON.stringify({ results: rows.map(({ _id, ...r }) => r) }),
      redirect: 'follow', credentials: 'omit', cache: 'no-store', signal: ctl?.signal,
    });
    if (!res.ok) return false;
    const data = await res.json();
    return data?.ok === true;
  } catch {
    return false;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function flushNow(validIds) {
  // Drop rows the endpoint would reject or whose card isn't in the loaded content any more.
  if (validIds) {
    const q = readQueue(), ok = q.filter(keep(validIds));
    if (ok.length !== q.length) writeQueue(ok);
  }
  while (true) {
    const batch = readQueue().slice(0, BATCH);
    if (!batch.length) return true;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return false;
    if (!(await postBatch(batch))) return false;
    // Remove exactly the rows that were sent (new rows may have been queued meanwhile).
    const sent = new Set(batch.map((r) => r._id));
    writeQueue(readQueue().filter((r) => !sent.has(r._id)));
  }
}

/** A send is in flight; whenIdle() resolves once it has finished. */
export const isSending = () => !!flushing;
export const whenIdle = () => (flushing ? flushing.then(whenIdle) : Promise.resolve());

/** Send everything in the queue (batches of 200). Resolves true when the queue is empty. */
export function flush(validIds = null) {
  if (flushing) return flushing.then(() => flush(validIds)); // pick up rows queued while a send was running
  const run = () => flushNow(validIds);
  const p = (typeof navigator !== 'undefined' && navigator.locks?.request)
    ? navigator.locks.request('oye-results-send', run)
    : run();
  flushing = Promise.resolve(p).catch(() => false).finally(() => { flushing = null; notify(); });
  return flushing;
}
