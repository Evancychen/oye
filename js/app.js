// Oye: app shell, screens and card templates.
// v1.1: one-screen cards (header / scrolling middle / pinned dock), hints, feedback sheet, SW update flow.
// v2 Levels: Easy (Quick session, Easy cards only), Medium (topic grid + topic sessions), Hard (missions),
// stars, and v2.1 voices (transcript labels coloured by voice: Alonso blue, Paloma green).
import { loadContent } from './content.js';
import { setAudioIndex, hasAudio, hasLine, play, stop, clipInfo, playClip, clipLoaded, audioEl } from './audio.js';
import * as levels from './levels.js';
import { isCorrect } from './check.js';
import * as srs from './srs.js';
import * as results from './results.js';

export const SHELL_VERSION = '2.0';
document.documentElement.dataset.shell = SHELL_VERSION;

const $app = document.getElementById('app');
const S = {
  content: null, playable: [], easy: [], medium: [], missions: [], scenes: new Map(), progress: srs.load(),
  plan: [], session: null, view: 'boot', audioStatus: null, keyHandler: null,
  shell: SHELL_VERSION, updatePending: false,
};
window.__oye = S; // handy for debugging / tests

// ---------- helpers ----------
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const svg = (inner, { size = 24, vb = '0 0 24 24' } = {}) =>
  `<svg width="${size}" height="${size}" viewBox="${vb}" fill="none" aria-hidden="true" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
const ICON = {
  speaker: svg('<path d="M3.5 9.5H7L12 5.5V18.5L7 14.5H3.5Z" fill="currentColor" stroke="currentColor" stroke-width="1.2"/><path d="M15.5 9.2A4 4 0 0 1 15.5 14.8" stroke="currentColor" stroke-width="1.6"/><path d="M18.2 6.6A7.6 7.6 0 0 1 18.2 17.4" stroke="currentColor" stroke-width="1.6"/>'),
  close: svg('<path d="M7 7L17 17M17 7L7 17" stroke="currentColor" stroke-width="1.8"/>'),
  check: (s = 14) => svg('<path d="M6 12.5L10 16.5L18 7.5" stroke="currentColor" stroke-width="2.6"/>', { size: s }),
  cross: (s = 14) => svg('<path d="M8 8L16 16M16 8L8 16" stroke="currentColor" stroke-width="2.6"/>', { size: s }),
  chevron: svg('<path d="M9.5 6L15.5 12L9.5 18" stroke="currentColor" stroke-width="1.8"/>', { size: 20 }),
  chevronDown: svg('<path d="M6 9.5L12 15.5L18 9.5" stroke="currentColor" stroke-width="1.8"/>', { size: 18 }),
  star: (s = 12) => svg('<path d="M12 2.8L14.7 8.9L21.3 9.5L16.3 13.9L17.8 20.4L12 17L6.2 20.4L7.7 13.9L2.7 9.5L9.3 8.9Z" fill="currentColor"/>', { size: s }),
  back: svg('<path d="M14.5 6L8.5 12L14.5 18" stroke="currentColor" stroke-width="1.8"/>', { size: 20 }),
  play: svg('<path d="M8 5.5V18.5L18.5 12Z" fill="currentColor" stroke="currentColor" stroke-width="1.2"/>'),
  pause: svg('<path d="M8 5.5V18.5M16 5.5V18.5" stroke="currentColor" stroke-width="3"/>'),
  backspace: svg('<path d="M9 5.5H19.5A1.5 1.5 0 0 1 21 7V17A1.5 1.5 0 0 1 19.5 18.5H9L2.5 12Z" stroke="currentColor" stroke-width="1.6"/><path d="M11.5 9.5L16.5 14.5M16.5 9.5L11.5 14.5" stroke="currentColor" stroke-width="1.6"/>', { size: 26 }),
};
const TYPE_LABEL = { listen_pick: 'Listen', listen_type: 'Listen and type', scene_question: 'Scene', fix_it: 'Fix it', reply: 'Your reply' };
const TAG_LABEL = { tu_vs_usted: 'tú vs usted', ser_estar: 'ser vs estar', pronoun_pairs: 'pronoun pairs' };
const tagLabel = (t) => TAG_LABEL[t] || String(t).replace(/_vs_/g, ' vs ').replace(/_/g, ' ');
const fmtDate = (d = new Date()) => new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
const $ = (sel, root = $app) => root.querySelector(sel);
const $$ = (sel, root = $app) => [...root.querySelectorAll(sel)];
function render(html) {
  closeSheet(false);
  disposeHints($app);
  $app.innerHTML = html;
  $app.scrollTop = 0;
}
function setKeyHandler(fn) {
  if (S.keyHandler) document.removeEventListener('keydown', S.keyHandler);
  S.keyHandler = fn || null;
  if (fn) document.addEventListener('keydown', fn);
}
const sceneOf = (card) => (card.scene_id ? S.scenes.get(card.scene_id) : null);
const cardLessons = (card) => [...new Set([...(card.lesson_tags || []), ...((sceneOf(card)?.lesson_tags) || [])])];

// ---------- "More below": fade + pill on any scrolling zone ----------
// Markup: <div class="zone"> <div class="scroller"><div class="scroll-inner">…</div></div> ${moreHint()} </div>
const moreHint = () => `<div class="fade" aria-hidden="true"></div><button type="button" class="more-pill" data-testid="more" tabindex="-1">More below ${ICON.chevronDown}</button>`;
const hintObservers = new Map(); // zone -> ResizeObserver
function setupScrollHint(zone) {
  if (!zone) return;
  const sc = zone.querySelector('.scroller');
  const inner = sc && sc.firstElementChild;
  const pill = zone.querySelector('.more-pill');
  if (!sc) return;
  const upd = () => {
    const overflow = sc.scrollHeight - sc.clientHeight > 2;
    const atEnd = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2;
    zone.classList.toggle('has-more', overflow && !atEnd);
    zone.classList.toggle('is-scrollable', overflow);
  };
  sc.addEventListener('scroll', upd, { passive: true });
  if (pill) pill.onclick = () => sc.scrollBy({ top: Math.max(80, sc.clientHeight * 0.75), behavior: 'smooth' });
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(upd);
    ro.observe(sc); if (inner) ro.observe(inner);
    hintObservers.set(zone, ro);
  }
  zone.__updHint = upd;
  upd();
}
function disposeHints(root) {
  for (const [zone, ro] of hintObservers) if (root.contains(zone)) { ro.disconnect(); hintObservers.delete(zone); }
}
const refreshHints = () => { for (const zone of hintObservers.keys()) zone.__updHint?.(); };

// ---------- card templates (picked by `type`; unknown types are skipped) ----------
const hasOptions = (c) => Array.isArray(c.options) && c.options.length >= 2 && c.options.includes(c.answer);
const hasAnswers = (c) => c.answer != null || (c.accepted_answers || []).length > 0;
const hasHint = (c) => typeof c.hint_en === 'string' && c.hint_en.trim() !== '';
const TEMPLATES = {
  listen_pick: { valid: (c) => !!c.audio_text && hasOptions(c), render: renderListenPick },
  listen_type: { valid: (c) => !!c.audio_text && hasAnswers(c), render: renderListenType },
  scene_question: { valid: (c) => hasOptions(c) && (!!c.audio_text || !!sceneOf(c)), render: renderSceneQuestion },
  fix_it: { valid: (c) => !!c.sentence && (hasAnswers(c) || hasOptions(c)), render: renderFixIt },
  reply: { valid: (c) => hasOptions(c), render: renderReply },
};
function isPlayable(c) {
  const t = c && TEMPLATES[c.type];
  if (!t) return false;
  try { return !!t.valid(c); } catch { return false; }
}

// ---------- service worker + updates ----------
// A new sw.js activates right away (skipWaiting + claim) and messages every open window.
// This page acks and reloads into the new shell as soon as it's safe: immediately on
// Home/summary, otherwise when the session ends. Pages that don't ack (v1) are reloaded by the SW.
function setupServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data?.type !== 'oye-update') return;
    try { e.source?.postMessage({ type: 'oye-update-ack' }); } catch {}
    S.updatePending = true;
    maybeApplyUpdate();
  });
  try { navigator.serviceWorker.startMessages(); } catch {}
  navigator.serviceWorker.register('sw.js').catch((e) => console.warn('[oye] sw', e));
  // Resuming the installed app from the background doesn't reload the page, so check for a new sw.js then too.
  let lastCheck = Date.now();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || Date.now() - lastCheck < 10000) return;
    lastCheck = Date.now();
    navigator.serviceWorker.getRegistration().then((r) => r && r.update()).catch(() => {});
  });
}
function maybeApplyUpdate() {
  if (!S.updatePending || S.view === 'session' || S.view === 'mission') return false;
  // Let a results upload finish first (a reload mid-request could make the retry send the rows twice).
  if (results.isSending()) { results.whenIdle().then(() => maybeApplyUpdate()); return false; }
  S.updatePending = false;
  location.reload();
  return true;
}

// ---------- on-screen keyboard (fallback when interactive-widget=resizes-content isn't honoured) ----------
function setupKeyboardInset() {
  if (!window.visualViewport) return;
  const upd = () => {
    const vv = window.visualViewport;
    const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty('--kb', kb > 80 ? `${Math.round(kb)}px` : '0px');
    document.documentElement.classList.toggle('kb-open', kb > 80);
    if (kb > 80 && (window.scrollY || document.documentElement.scrollTop)) window.scrollTo(0, 0);
    refreshHints();
  };
  visualViewport.addEventListener('resize', upd);
  visualViewport.addEventListener('scroll', upd);
  window.addEventListener('resize', () => refreshHints());
  S.updateKeyboardInset = upd;
}

// ---------- boot ----------
async function boot() {
  setupServiceWorker();
  setupKeyboardInset();
  if (history.state?.oye) history.replaceState(null, '');
  window.addEventListener('popstate', () => {
    if (S.sheet) { closeSheet(); if (S.view === 'session' || S.view === 'mission') history.pushState(S.lastState || { oye: S.view }, ''); return; }
    const st = history.state?.oye;
    if (st === 'topics') openTopics(false);
    else if (st === 'missions') openMissions(false);
    else if (S.view !== 'home') goHome(false);
  });
  try {
    S.content = await loadContent((p) => {
      S.audioStatus = p;
      const q = document.getElementById('quiet');
      if (q && S.view === 'home') q.textContent = quietLine();
    });
  } catch (e) {
    render(`<div class="screen page"><p class="eyebrow">Oye</p><h1 class="display">Can't load cards</h1><p class="body-copy" style="margin-top:16px">${esc(e.message)}</p></div>`);
    return;
  }
  setAudioIndex(S.content.audio);
  S.scenes = new Map(S.content.scenes.map((s) => [s.id, s]));
  S.playable = S.content.cards.filter(isPlayable);
  S.easy = S.playable.filter(levels.isEasy);                          // Quick session: Easy cards only
  S.medium = S.playable.filter((c) => levels.levelOf(c) === 'medium' && c.topic);
  S.missions = (S.content.missions || []).filter(isPlayableMission);
  const skipped = S.content.cards.length - S.playable.length;
  if (skipped) console.info(`[oye] skipped ${skipped} card(s) with unknown type or missing fields`);
  S.content.audioSync.then((r) => { S.audioStatus = { ...S.audioStatus, finished: true, ...r }; const q = document.getElementById('quiet'); if (q) q.textContent = quietLine(); });
  goHome(false);
  document.documentElement.dataset.ready = '1';
  setupResultsRetry();
}

// ---------- results upload (Google Sheet via Apps Script; see js/results.js) ----------
const validCardIds = () => (S.content ? new Set(S.content.cards.map((c) => c.id)) : null);
const sendResults = () => results.flush(validCardIds());
function setupResultsRetry() {
  sendResults(); // anything left from an earlier offline session
  window.addEventListener('online', sendResults);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') sendResults(); });
  window.addEventListener('pageshow', (e) => { if (e.persisted) sendResults(); });
  results.onQueueChange(() => updateResultsStatus());
}
function resultsStatusHtml(sent) {
  return sent
    ? `<span class="rs-dot sent" aria-hidden="true"></span><span>Results sent to Gabriel</span>`
    : `<span class="rs-dot" aria-hidden="true"></span><span>Saved · will send when you’re online</span>`;
}
function updateResultsStatus() {
  const el = document.querySelector('#app [data-testid=results-status]');
  const ids = S.summaryRowIds;
  if (!el || !ids || !ids.length) return;
  const sent = !results.isQueued(ids);
  if (el.dataset.state === (sent ? 'sent' : 'queued')) return;
  el.dataset.state = sent ? 'sent' : 'queued';
  el.innerHTML = resultsStatusHtml(sent);
}
// ---------- home ----------
function quietLine() {
  const a = S.audioStatus;
  if (a && !a.finished && a.total > 0 && a.done < a.total) return `Saving audio for offline… ${a.done} of ${a.total}`;
  const v = S.content?.version;
  if (!v || !v.new_cards) return '';
  let recent = true;
  if (v.published) {
    const age = (Date.now() - new Date(`${v.published}T12:00:00`).getTime()) / 86400000;
    recent = age < 7.5;
  }
  return recent ? `${v.new_cards} new card${v.new_cards === 1 ? '' : 's'} this week` : '';
}
/** Quick session pool: Easy cards only (v2: Medium cards never appear here), optionally one lesson. */
function lessonPool() {
  const l = S.progress.lesson || 'all';
  return l === 'all' ? S.easy : S.easy.filter((c) => cardLessons(c).includes(l));
}
function lessonName(tag) {
  const m = /^(A\d)-L(\d+)$/.exec(tag || '');
  return m ? `${m[1]} · Lesson ${Number(m[2])}` : tag;
}
function sessionMeta(plan, lesson = 'all') {
  if (!plan.length) return `<span class="meta-txt">${['EASY', 'No cards for this lesson yet'].map(esc).join('<span class="sep">·</span>')}</span>`;
  const mins = Math.max(1, Math.round(plan.length * 0.3));
  const prices = plan.filter(srs.isPriceByEar).length;
  const focus = lesson !== 'all' ? lessonName(lesson) : prices >= plan.length * 0.6 ? 'mostly prices' : 'mixed review';
  return `<span class="meta-txt">${['EASY', `${plan.length} cards`, `about ${mins} min`, focus].map(esc).join('<span class="sep">·</span>')}</span>`;
}
const starsInline = (n, cls = '') => `<span class="star-n ${cls}">${ICON.star(12)}<span>${n}</span></span>`;
function starRow(n, size = 12, label = '') {
  return `<span class="stars s${size}" data-stars="${n}" role="img" aria-label="${label || `${n} of 3 stars`}">${[1, 2, 3].map((i) => `<i class="${i <= n ? 'on' : ''}">${ICON.star(size)}</i>`).join('')}</span>`;
}
/** History entries for the v2 screens: depth lets "Done" jump straight back to Home. */
function pushView(oye) {
  const depth = (history.state?.depth || 0) + 1;
  S.lastState = { oye, depth };
  history.pushState(S.lastState, '');
}
function goHome(pop = true) {
  stop(); setKeyHandler(null);
  if (pop && history.state?.oye) { history.go(-(history.state.depth || 1)); return; } // popstate will call goHome(false)
  S.view = 'home'; S.session = null; S.mission = null;
  if (maybeApplyUpdate()) return; // a new version was installed: reload into it now
  S.progress = srs.load();
  const newIds = new Set(S.content.version?.new_card_ids || []);
  S.plan = srs.planSession(S.progress, lessonPool(), { newIds });
  const st = srs.streak(S.progress);
  const pe = srs.pricesByEar(S.progress);
  let trendNote = 'Answer a few price cards to see this';
  if (pe.now != null && pe.twoWeeksAgo != null) {
    trendNote = pe.now > pe.twoWeeksAgo ? `up from ${pe.twoWeeksAgo}% two weeks ago` : pe.now < pe.twoWeeksAgo ? `down from ${pe.twoWeeksAgo}% two weeks ago` : 'same as two weeks ago';
  } else if (pe.now != null) trendNote = `last 7 days · ${pe.answers} answer${pe.answers === 1 ? '' : 's'}`;
  const maxH = 46;
  const bars = pe.bars.map((p, i) => {
    const cls = i === 6 ? 'now' : '';
    if (p == null) return `<i class="empty ${cls === 'now' ? 'now-empty' : ''}" style="height:4px"></i>`;
    return `<i class="${cls}" style="height:${Math.max(6, Math.round((p / 100) * maxH))}px"></i>`;
  }).join('');
  const lesson = S.progress.lesson || 'all';
  const tot = levels.totals();
  const challenge = (act, tag, title, sub, n, ok) => `
    <button class="challenge" data-act="${act}" data-testid="challenge-${act}" ${ok ? '' : 'disabled'}>
      <span class="ch-text"><span class="eyebrow ch-tag">${esc(tag)}</span><span class="ch-title">${esc(title)}</span><span class="caption">${esc(sub)}</span></span>
      ${starsInline(n, 'ch-stars')}<span class="ch-chev">${ICON.chevron}</span>
    </button>`;
  render(`
  <div class="screen page home" data-screen="home">
    <header class="home-header">
      <p class="eyebrow">${esc(fmtDate())}</p>
      <h1 class="display">Oye</h1>
    </header>
    <section class="stats" aria-label="Today">
      <div class="col">
        <div class="value" data-testid="streak">${st}</div>
        <p class="caption">day streak</p>
      </div>
      <div class="divider"></div>
      <div class="col">
        <div class="value star-total" data-testid="stars-total">${tot.all} ${ICON.star(24)}</div>
        <p class="caption">stars earned</p>
      </div>
    </section>
    <section class="trend" aria-label="Prices by ear">
      <div>
        <p class="eyebrow">Prices by ear</p>
        <p class="title">${pe.now == null ? '–' : `${pe.now}%`}</p>
        <p class="caption">${esc(trendNote)}</p>
      </div>
      <div class="bars" aria-hidden="true">${bars}</div>
    </section>
    <div class="spacer"></div>
    <p class="caption quiet" id="quiet" data-testid="quiet">${esc(quietLine())}</p>
    <p class="eyebrow challenges-head">Challenges</p>
    <div class="challenges">
      ${challenge('medium', 'Medium', 'Topic challenge', 'Pick a topic · about 4 min', tot.medium, S.medium.length > 0)}
      ${challenge('hard', 'Hard', 'Real-life mission', 'One longer situation · about 5 min', tot.hard, S.missions.length > 0)}
    </div>
    <button class="caption meta-line" data-act="lessons" aria-label="Quick session: choose a lesson">${sessionMeta(S.plan, lesson)}<span class="meta-chev">${ICON.chevron}</span></button>
    <div class="action">
      <button class="btn-primary" data-act="start" ${S.plan.length ? '' : 'disabled'}>Quick session</button>
    </div>
  </div>`);
  $('[data-act="start"]').onclick = startSession;
  $('[data-act="lessons"]').onclick = openLessons;
  $('[data-act="medium"]').onclick = () => openTopics(true);
  $('[data-act="hard"]').onclick = () => openMissions(true);
}

function openLessons() {
  const counts = new Map();
  for (const c of S.easy) for (const t of cardLessons(c)) counts.set(t, (counts.get(t) || 0) + 1);
  const tags = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'en', { numeric: true }));
  const cur = S.progress.lesson || 'all';
  const row = (val, label, n) => `<button class="sheet-row ${cur === val ? 'on' : ''}" data-lesson="${esc(val)}"><span>${esc(label)}<span class="caption">${n} cards</span></span>${cur === val ? `<span class="check-mark">${ICON.check(18)}</span>` : ''}</button>`;
  const el = document.createElement('div');
  el.className = 'sheet-backdrop';
  el.innerHTML = `<div class="sheet" role="dialog" aria-label="Lessons"><span class="eyebrow">Lessons</span>
    ${row('all', 'All lessons', S.easy.length)}${tags.map((t) => row(t, lessonName(t), counts.get(t))).join('')}</div>`;
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-lesson]');
    if (b) { const p = srs.load(); p.lesson = b.dataset.lesson; srs.save(p); el.remove(); goHome(false); }
    else if (e.target === el) el.remove();
  });
  document.body.appendChild(el);
}

// ---------- session ----------
function startSession() {
  if (!S.plan.length) return;
  S.session = { cards: S.plan, i: 0, results: [], start: Date.now(), id: results.newSessionId(), level: 'easy', topic: null };
  S.view = 'session';
  pushView('session');
  showCard();
}
function showCard(fb = null) {
  const ses = S.session;
  const card = ses.cards[ses.i];
  stop(); setKeyHandler(null);
  if (!fb) { ses.hintUsed = false; ses.slowUsed = false; }
  let view;
  try { view = TEMPLATES[card.type].render(card, fb); }
  catch (e) { console.warn('[oye] template failed, skipping', card.id, e); return nextCard(true); }
  const n = ses.cards.length;
  render(`
  <div class="screen card-screen" data-screen="card" data-type="${esc(card.type)}" data-card="${esc(card.id)}" data-state="${fb ? (fb.ok ? 'correct' : 'wrong') : 'question'}">
    <header class="topbar">
      <button class="close" data-act="close" aria-label="End session">${ICON.close}</button>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${n}" aria-valuenow="${ses.i + 1}"><i style="width:${((ses.i + 1) / n) * 100}%"></i></div>
      <span class="count">${ses.i + 1} of ${n}</span>
    </header>
    <div class="zone card-zone"><div class="card-body scroller" data-testid="card-body"><div class="card-inner">${view.body}</div></div>${moreHint()}</div>
    <div class="dock ${view.dockClass || ''}" data-testid="dock">${view.dock}</div>
  </div>`);
  $('[data-act="close"]').onclick = () => goHome(true);
  bindAudio(card);
  bindHint(card);
  bindFeedbackRows(card, fb);
  const nextBtn = $('[data-act="next"]');
  if (nextBtn) { nextBtn.onclick = () => nextCard(); nextBtn.focus({ preventScroll: true }); }
  const dk = $('[data-act="dontknow"]');
  if (dk) dk.onclick = () => answer(card, false, null, true);
  view.bind?.();
  setupScrollHint($('.card-zone'));
  // Autoplay once when a listening card appears (allowed after the tap that started the session).
  if (!fb && card.audio_text && hasAudio(card.audio_text)) {
    const big = $('.replay');
    setTimeout(() => { if (S.session?.cards[S.session.i] === card && $('[data-state="question"]')) play(card.audio_text, { btn: big }); }, 350);
  }
}
function bindAudio(card, root = $app) {
  $$('[data-play]', root).forEach((b) => {
    b.onclick = () => {
      const slow = b.dataset.play === 'slow';
      // Slow replay before answering is recorded with the answer (used_slow in the results upload).
      if (slow && S.session && S.view === 'session' && S.session.answered !== S.session.i) S.session.slowUsed = true;
      play(b.dataset.text || card?.audio_text, { slow, btn: b, voice: b.dataset.voice || null });
    };
  });
}
function answer(card, ok, given, skipped = false) {
  if (!S.session || S.session.answered === S.session.i) return;
  S.session.answered = S.session.i;
  const hint = !!S.session.hintUsed;
  srs.record(S.progress, card, ok, { hint });
  S.session.results.push({ card, ok, given, skipped, hint, slow: !!S.session.slowUsed, at: new Date().toISOString() });
  showCard({ ok, given, skipped });
}
function nextCard(skipBroken = false) {
  const ses = S.session;
  if (skipBroken) { ses.cards.splice(ses.i, 1); if (!ses.cards.length) return goHome(true); }
  else ses.i++;
  if (ses.i >= ses.cards.length) return showSummary();
  showCard();
}

// ---------- shared bits ----------
function eyebrow(label, sub) {
  return `<p class="eyebrow">${esc(label)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</p>`;
}
const replayBtn = (size, text) => `<button class="replay r${size}" data-play="normal" ${text ? `data-text="${esc(text)}"` : ''} data-testid="replay" aria-label="Replay">${ICON.speaker}</button>`;
const slowBtn = (size, text) => `<button class="slow s${size}" data-play="slow" ${text ? `data-text="${esc(text)}"` : ''} data-testid="slow" aria-label="Replay slowly">0.75×</button>`;
/** Big centred replay + slow (listen_pick question). */
function audioHero(card) {
  return `<div class="audio-hero" data-testid="audio-row">
    <div class="ctl"><div class="ring-wrap">${replayBtn(112)}</div><span class="caption">Replay</span></div>
    <div class="ctl"><div class="ring-wrap">${slowBtn(64)}</div><span class="caption">Slow</span></div>
  </div>`;
}
/** One short row: replay, slow, caption, optional thing on the right (Hint pill). Never shrinks. */
function audioRow(card, { size = 48, slow = 48, caption = 'Replay · Slow', right = '', text = null, cls = '' } = {}) {
  if (!card.audio_text && !text) return right ? `<div class="audio-row ${cls}">${right}</div>` : '';
  return `<div class="audio-row ${cls}" data-testid="audio-row">${replayBtn(size, text)}${slowBtn(slow, text)}<span class="caption">${esc(caption)}</span>${right}</div>`;
}
function hintPill(card) {
  return hasHint(card) ? `<button type="button" class="hint-pill" data-act="hint" data-testid="hint-pill" aria-expanded="false" aria-controls="hint-box">Hint</button>` : '';
}
function hintBox(card) {
  if (!hasHint(card)) return '';
  return `<div class="hint-box" id="hint-box" data-testid="hint-box" hidden>
    <div class="hint-head"><span class="eyebrow">Hint</span><button type="button" class="hint-hide" data-act="hint-hide">Hide</button></div>
    <p class="hint-text">${esc(card.hint_en).replace(/\//g, '/<wbr>')}</p></div>`;
}
function bindHint(card) {
  const pill = $('[data-act="hint"]'), box = $('#hint-box'), hide = $('[data-act="hint-hide"]');
  if (!pill || !box) return;
  // Keep focus (and the phone keyboard) on the answer field while toggling the hint.
  [pill, hide].forEach((b) => b && b.addEventListener('mousedown', (e) => e.preventDefault()));
  pill.onclick = () => {
    box.hidden = false; pill.hidden = true; pill.setAttribute('aria-expanded', 'true');
    if (S.session && S.view === 'session') S.session.hintUsed = true;
    if (S.mission && S.view === 'mission') S.mission.hintUsed = true;
    refreshHints();
  };
  if (hide) hide.onclick = () => { box.hidden = true; pill.hidden = false; pill.setAttribute('aria-expanded', 'false'); refreshHints(); };
}
function resultRow(ok) {
  return `<div class="result ${ok ? 'ok' : 'bad'}" data-testid="result"><span class="dot">${ok ? ICON.check(19) : ICON.cross(19)}</span><span class="title">${ok ? 'Correct' : 'Not quite'}</span></div>`;
}
const isMoney = (card) => [card.answer, ...(card.accepted_answers || [])].some((a) => String(a).includes('$'));
function answerText(card) {
  let a = String(card.answer ?? (card.accepted_answers || [])[0] ?? '');
  if (card.type === 'listen_type' && isMoney(card) && /^\d/.test(a)) a = `$${a}`;
  const plain = /^[$\d][\d.,:\s]*$/.test(a); // prices, numbers, times
  return plain ? a : `“${a}”`;
}
function answerLine(card, fb) {
  let you = '';
  if (!fb.ok && !fb.skipped && fb.given != null && card.type !== 'listen_pick' && !hasOptions(card)) {
    const g = card.type === 'listen_type' ? fmtAnswer(card, fb.given) : fb.given;
    you = `<p class="caption you-wrote" data-testid="you-wrote">You ${card.type === 'listen_type' ? 'typed' : 'wrote'}: <span class="given">${esc(g)}</span></p>`;
  }
  return `<p class="answer-line" data-testid="answer-line">The answer is ${esc(answerText(card))}.</p>${you}`;
}
function parseTranscript(t) {
  return String(t || '').split(/\s+\/\s+/).filter(Boolean).map((line) => {
    const m = /^([^:]{1,24}):\s*(.+)$/.exec(line);
    return m ? { speaker: m[1].trim(), es: m[2].trim() } : { speaker: '', es: line.trim() };
  });
}
/** Lines for "What you heard": scene cards get speakers (+ English from the scene where it matches). */
const VOICES = new Set(['male', 'female']);
/** The voice a single-voice card is spoken in (v2.1): card `voice`, else Easy = female, Medium/Hard = male. */
const cardVoice = (card) => (VOICES.has(card?.voice) ? card.voice : levels.isEasy(card) ? 'female' : 'male');
function heardLines(card) {
  const scene = sceneOf(card);
  let lines = [];
  if (Array.isArray(card.audio_lines) && card.audio_lines.length) lines = card.audio_lines.map((l) => ({ ...l }));
  else if (card.type === 'scene_question' && !card.audio_text && scene) lines = scene.dialogue.map((d) => ({ ...d }));
  else lines = parseTranscript(card.transcript || card.audio_text);
  if (scene) {
    const en = new Map(scene.dialogue.map((d) => [d.es, d.en]));
    const voiceOf = new Map(scene.dialogue.filter((d) => VOICES.has(d.voice)).map((d) => [d.speaker, d.voice]));
    lines = lines.map((l) => ({ ...l, en: l.en || en.get(l.es) || '', voice: l.voice || voiceOf.get(l.speaker) }));
  }
  // A one-speaker clip is read in the card's own voice.
  if (!lines.some((l) => l.voice) && new Set(lines.map((l) => l.speaker)).size <= 1) lines = lines.map((l) => ({ ...l, voice: cardVoice(card) }));
  return lines;
}
/** Speaker label colour follows the VOICE (v2.1): male (Alonso) accent blue, female (Paloma) green.
 *  Only lines with no voice at all fall back to speaking order. */
function speakerClasses(lines, scene) {
  const order = [];
  for (const l of [...(scene?.dialogue || []), ...lines]) if (l.speaker && !order.includes(l.speaker)) order.push(l.speaker);
  return (l) => {
    if (VOICES.has(l.voice)) return `v-${l.voice}`;
    const i = order.indexOf(l.speaker); return i === 0 ? 'spk-1' : i === 1 ? 'spk-2' : 'spk-n';
  };
}
function dialogueHtml(lines, scene, { play = true } = {}) {
  const cls = speakerClasses(lines, scene);
  return `<div class="dialogue">${lines.map((l) => `
    <div class="dline" ${l.voice ? `data-voice="${esc(l.voice)}"` : ''}>
      ${play && hasLine(l.es, l.voice) ? `<button class="line-play" data-play="normal" data-text="${esc(l.es)}" ${l.voice ? `data-voice="${esc(l.voice)}"` : ''} data-testid="line-play" aria-label="Play this line">${ICON.speaker}</button>` : '<span class="line-play placeholder" aria-hidden="true"></span>'}
      <div class="dtext">${l.speaker ? `<p class="eyebrow spk ${cls(l)}" data-testid="spk">${esc(l.speaker)}</p>` : ''}<p class="es">${esc(l.es)}</p>${l.en ? `<p class="caption en">${esc(l.en)}</p>` : ''}</div>
    </div>`).join('')}</div>`;
}
/** After answering: "What you heard" and "Why" rows (each opens the sheet). */
function feedbackRows(card) {
  const lines = heardLines(card);
  const heard = lines.length ? lines.map((l) => l.es).join(' ') : '';
  const why = card.explanation_en || card.region_note || '';
  if (!heard && !why) return '';
  return `<div class="info-rows">
    ${heard ? `<button class="info-row" data-sheet="heard" data-testid="row-heard"><span class="lbl">What you heard</span><span class="pv">${esc(heard)}</span>${ICON.chevron}</button>` : ''}
    ${why ? `<button class="info-row" data-sheet="why" data-testid="row-why"><span class="lbl">Why</span><span class="pv">${esc(why)}</span>${ICON.chevron}</button>` : ''}
  </div>`;
}
function feedbackBody(card, fb, head) {
  return `${head}${resultRow(fb.ok)}${answerLine(card, fb)}${card.audio_text ? audioRow(card, { caption: 'Hear it again', cls: 'fb-audio' }) : ''}${feedbackRows(card)}`;
}
function bindFeedbackRows(card, fb) {
  if (!fb) return;
  $$('[data-sheet]').forEach((b) => { b.onclick = () => openFeedbackSheet(card, b.dataset.sheet); });
}

// ---------- feedback sheet ----------
function closeSheet(restoreKeys = true) {
  const el = S.sheet;
  if (!el) return;
  S.sheet = null;
  disposeHints(el);
  el.remove();
  if (restoreKeys) setKeyHandler(S.sheetPrevKey || null);
  S.sheetPrevKey = null;
}
function openFeedbackSheet(card, focus = 'heard') {
  closeSheet();
  const scene = sceneOf(card);
  const lines = heardLines(card);
  const isScene = !!scene && (card.type === 'scene_question' || lines.some((l) => l.speaker));
  const isDialogue = isScene || new Set(lines.filter((l) => l.speaker).map((l) => l.speaker)).size > 1;
  const secs = [];
  if (lines.length) {
    secs.push(`<section class="sheet-sec" data-sec="heard" data-testid="sheet-heard">
      ${eyebrow('What you heard', isScene ? scene.title_en : '')}
      ${isDialogue ? dialogueHtml(lines, scene) : `<p class="heard-text">${esc(lines.map((l) => l.es).join(' '))}</p>`}
      ${card.audio_text ? audioRow(card, { caption: isDialogue ? 'Whole clip' : '', cls: 'sheet-audio' }) : ''}
    </section>`);
  }
  if (card.explanation_en || card.region_note) {
    secs.push(`<section class="sheet-sec" data-sec="why" data-testid="sheet-why">${eyebrow('Why')}
      ${card.explanation_en ? `<p class="why-text">${esc(card.explanation_en)}</p>` : ''}
      ${card.region_note ? `<div class="region"><p class="eyebrow">Mexico vs Spain</p><p class="caption">${esc(card.region_note)}</p></div>` : ''}
    </section>`);
  }
  if (isScene && scene.dialogue?.length > lines.length) {
    secs.push(`<section class="sheet-sec" data-sec="scene" data-testid="sheet-scene">${eyebrow('Whole scene', scene.title_en)}${dialogueHtml(scene.dialogue, scene)}</section>`);
  }
  const el = document.createElement('div');
  el.className = 'sheet-backdrop fb-backdrop';
  el.innerHTML = `<div class="sheet fb-sheet" role="dialog" aria-modal="true" aria-label="What you heard and why" data-testid="sheet">
    <span class="grab" aria-hidden="true"></span>
    <div class="zone sheet-zone"><div class="sheet-scroll scroller"><div class="scroll-inner">${secs.join('')}</div></div>${moreHint()}</div>
    <button class="btn-text sheet-close" data-act="sheet-close">Close</button>
  </div>`;
  el.addEventListener('click', (e) => { if (e.target === el) closeSheet(); });
  el.querySelector('.grab').addEventListener('click', () => closeSheet());
  el.querySelector('[data-act="sheet-close"]').onclick = () => closeSheet();
  document.body.appendChild(el);
  S.sheet = el;
  S.sheetPrevKey = S.keyHandler; setKeyHandler((e) => { if (e.key === 'Escape') closeSheet(); });
  bindAudio(card, el);
  setupScrollHint(el.querySelector('.sheet-zone'));
  const target = el.querySelector(`[data-sec="${focus}"]`);
  if (target && focus !== 'heard') el.querySelector('.sheet-scroll').scrollTop = target.offsetTop - 8;
  el.querySelector('.sheet-close').focus({ preventScroll: true });
}

// ---------- options ----------
/** v2: Medium cards can have long, two-line text options. */
const longOptions = (card) => (card.options || []).some((o) => String(o).length > 32);
function optionsBlock(card, fb, { grid } = {}) {
  const opts = card.options;
  const long = !grid && longOptions(card);
  const priceLike = opts.length === 4 && opts.every((o) => String(o).length <= 7);
  // Price options always use the 2 x 2 grid of 64px cells (spec rule 4), even on scene cards.
  const useGrid = (grid ?? priceLike) || (priceLike && opts.every((o) => /^\$?\d[\d.,:]*$/.test(String(o))));
  const items = opts.map((o, i) => {
    let cls = '', dot = '';
    if (fb) {
      if (o === card.answer) { cls = 'is-correct'; dot = `<span class="status-dot is-correct">${ICON.check(14)}</span>`; }
      else if (o === fb.given) { cls = 'is-wrong'; dot = `<span class="status-dot is-wrong">${ICON.cross(14)}</span>`; }
      else cls = 'is-dim';
    }
    // Long two-line options: after answering, keep only the right answer and your pick so the feedback still fits.
    if (fb && long && cls === 'is-dim') return '';
    return `<button class="opt ${cls}" data-opt="${i}" ${fb ? 'disabled' : ''}>${esc(o)}${dot}</button>`;
  }).join('');
  return `<div class="options ${useGrid ? 'grid' : ''} ${long ? 'long' : ''}" data-testid="options">${items}</div>`;
}
function bindOptions(card) {
  $$('[data-opt]').forEach((b) => {
    b.onclick = () => { const o = card.options[Number(b.dataset.opt)]; answer(card, o === card.answer, o); };
  });
}
const btnDontKnow = `<button class="btn-text dontknow" data-act="dontknow">I don’t know</button>`;
const btnNext = `<button class="btn-primary" data-act="next">Next</button>`;
const optionsDock = (card, fb, o) => `${optionsBlock(card, fb, o)}${fb ? btnNext : btnDontKnow}`;

// ---------- listen_pick ----------
function renderListenPick(card, fb) {
  const head = `${eyebrow('Listen')}<h2 class="title prompt">${esc(card.prompt_en || 'What did you hear?')}</h2>`;
  // Long text options (Medium) take the room of the big replay: use the compact audio row instead.
  const hero = longOptions(card) ? audioRow(card, { size: 56 }) : audioHero(card);
  if (!fb) return { body: `${head}${hero}`, dock: optionsDock(card, null), dockClass: 'opts-dock', bind: () => bindOptions(card) };
  return { body: feedbackBody(card, fb, head), dock: optionsDock(card, fb), dockClass: 'opts-dock' };
}

// ---------- listen_type ----------
function renderListenType(card, fb) {
  const money = isMoney(card);
  const head = `${eyebrow('Listen and type')}<h2 class="title prompt">${esc(card.prompt_en || 'Type what you hear.')}</h2>`;
  const show = (v) => `${money && !/[a-z]/i.test(v || '') ? '<span class="prefix">$</span>' : ''}<span class="value ${/[a-z]/i.test(v || '') ? 'words' : ''}">${esc(v || '')}</span>`;
  if (fb) return { body: feedbackBody(card, fb, head), dock: btnNext };
  if (wordAnswer(card)) {
    return { body: `${head}${audioRow(card, { right: hintPill(card) })}${hintBox(card)}`, dockClass: 'fix-dock', dock: textInputRow(),
      bind: () => bindTextInput(card, (v) => answer(card, isCorrect(card, v), v)) };
  }
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', ':', '0', 'del'];
  const keypad = keys.map((k) => k === 'del'
    ? `<button class="key" data-key="del" aria-label="Delete">${ICON.backspace}</button>`
    : `<button class="key ${k === ':' ? 'colon' : ''}" data-key="${k}">${k}</button>`).join('');
  return {
    body: `${head}${audioRow(card, { right: hintPill(card) })}${hintBox(card)}`,
    dockClass: 'type-dock',
    dock: `<div class="typed" data-testid="typed" aria-live="polite">${show('')}<span class="caret"></span></div>
      <button class="btn-primary" data-act="check" disabled>Check</button>
      <div class="keypad" data-testid="keypad">${keypad}</div>`,
    bind: () => {
      let val = '';
      const box = $('.typed'), check = $('[data-act="check"]');
      const upd = () => { box.innerHTML = `${show(val)}<span class="caret"></span>`; check.disabled = !val.trim(); };
      const press = (k) => {
        if (k === 'del') val = val.slice(0, -1);
        else if (val.length < 24) val += k;
        upd();
      };
      $$('[data-key]').forEach((b) => { b.onclick = () => press(b.dataset.key); });
      check.onclick = () => { if (val.trim()) answer(card, isCorrect(card, val), val.trim()); };
      // Hardware keyboard (desktop / testing): digits, letters for number words, Enter to check.
      setKeyHandler((e) => {
        if (e.metaKey || e.ctrlKey || e.altKey || S.sheet) return;
        if (e.key === 'Enter') { e.preventDefault(); check.click(); }
        else if (e.key === 'Backspace') { e.preventDefault(); press('del'); }
        else if (/^[0-9:.$a-zA-Záéíóúñü ]$/.test(e.key)) { e.preventDefault(); press(e.key); }
      });
    },
  };
}

// ---------- scene_question ----------
function renderSceneQuestion(card, fb) {
  const scene = sceneOf(card);
  const head = `${eyebrow('Scene', scene?.title_en)}<h2 class="title prompt">${esc(card.prompt_en)}</h2>`;
  if (fb) return { body: feedbackBody(card, fb, head), dock: optionsDock(card, fb, { grid: false }), dockClass: 'opts-dock' };
  const dialogueMode = !card.audio_text; // no audio: read the scene dialogue instead
  const transcript = dialogueMode
    ? `<div class="read-dialogue" data-testid="transcript">${dialogueHtml(heardLines(card), scene, { play: true })}</div>`
    : `<div class="locked-row" data-testid="transcript"><span class="lbl">Transcript</span><span class="caption">after you answer</span></div>`;
  return {
    body: `${head}${audioRow(card, { size: 56 })}${transcript}`,
    dock: optionsDock(card, null, { grid: false }), dockClass: 'opts-dock', bind: () => bindOptions(card),
  };
}

// ---------- fix_it ----------
function sentenceHtml(card, fill, state) {
  const parts = String(card.sentence).split(/_{2,}/);
  if (parts.length < 2) return `<p class="sentence" data-testid="sentence">${esc(card.sentence)}</p>`;
  const blank = `<span class="blank ${state || ''}" data-testid="blank">${fill ? esc(fill) : '&nbsp;'}</span>`;
  return `<p class="sentence" data-testid="sentence">${esc(parts[0])}${blank}${parts.slice(1).map(esc).join(blank)}</p>`;
}
function renderFixIt(card, fb) {
  const src = card.source === 'mistake' ? 'from your mistakes' : card.source === 'class_quizlet' ? 'from class' : card.source === 'scene' ? 'from a scene' : '';
  const head = eyebrow('Fix it', src);
  const chosen = hasOptions(card);
  const instruction = card.prompt_en ? `<p class="body-copy instruction" data-testid="instruction">${esc(card.prompt_en)}</p>` : '';
  if (fb) {
    return { body: feedbackBody(card, fb, `${head}${sentenceHtml(card, card.answer, 'ok')}`), dock: chosen ? optionsDock(card, fb, { grid: false }) : btnNext, dockClass: chosen ? 'opts-dock' : '' };
  }
  const hint = hasHint(card) ? `<div class="hint-slot">${hintPill(card)}${hintBox(card)}</div>` : '';
  if (chosen) {
    return { body: `${head}${sentenceHtml(card, '')}${instruction}${hint}`, dock: optionsDock(card, null, { grid: false }), dockClass: 'opts-dock', bind: () => bindOptions(card) };
  }
  return {
    body: `${head}${sentenceHtml(card, '')}${instruction}${hint}`,
    dockClass: 'fix-dock',
    dock: textInputRow(),
    bind: () => bindTextInput(card, (v) => answer(card, isCorrect(card, v), v)),
  };
}
/** The phone's letter keyboard (fix-it, and Medium typed answers with words). Checking is accent/case-insensitive (check.js).
 *  No autocapitalize / autocorrect / spellcheck, so the keyboard doesn't "fix" Spanish into English. */
const textInputRow = () => `<div class="fix-row"><input id="fix-input" class="text-input" type="text" inputmode="text" aria-label="Your answer" placeholder="Your answer" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="done" lang="es">
      <button class="btn-primary check-compact" data-act="check" disabled>Check</button></div>`;
function bindTextInput(card, onCheck) {
  const inp = $('#fix-input'), check = $('[data-act="check"]'), blank = $('.blank');
  check.addEventListener('mousedown', (e) => e.preventDefault()); // don't drop the keyboard before the tap lands
  inp.oninput = () => { if (blank) blank.innerHTML = inp.value ? esc(inp.value) : '&nbsp;'; check.disabled = !inp.value.trim(); };
  inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); check.click(); } };
  inp.onfocus = () => setTimeout(() => { S.updateKeyboardInset?.(); refreshHints(); }, 350);
  setTimeout(() => inp.focus({ preventScroll: true }), 50);
  check.onclick = () => { const v = inp.value.trim(); if (v) { inp.blur(); onCheck(v); } };
}
/** Typed answer that is a word, not a number/price/time: use the letter keyboard instead of the number pad. */
const wordAnswer = (c) => /[a-zñáéíóúü]/i.test(String(c.answer ?? (c.accepted_answers || [])[0] ?? '').replace(/\$/g, ''));

// ---------- reply ----------
function renderReply(card, fb) {
  const head = `${eyebrow('Your reply', sceneOf(card)?.title_en)}<h2 class="title prompt">${esc(card.prompt_en)}</h2>`;
  if (!fb) return { body: `${head}${audioRow(card, { size: 56 })}`, dock: optionsDock(card, null, { grid: false }), dockClass: 'opts-dock', bind: () => bindOptions(card) };
  return { body: feedbackBody(card, fb, head), dock: optionsDock(card, fb, { grid: false }), dockClass: 'opts-dock' };
}

// ---------- summary ----------
function dialSvg(results) {
  const n = results.length, r = 72, c = 80;
  const gap = Math.min(6, (360 / n) * 0.2);
  const seg = 360 / n;
  const pt = (deg) => { const a = ((deg - 90) * Math.PI) / 180; return [c + r * Math.cos(a), c + r * Math.sin(a)]; };
  const paths = results.map((res, i) => {
    const a0 = i * seg + gap / 2, a1 = (i + 1) * seg - gap / 2;
    const [x0, y0] = pt(a0), [x1, y1] = pt(a1);
    return `<path d="M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}" stroke="${res.ok ? 'var(--ink)' : 'var(--gray-300)'}" stroke-width="8" fill="none"/>`;
  }).join('');
  return `<svg width="160" height="160" viewBox="0 0 160 160" aria-hidden="true">${paths}</svg>`;
}
function fmtAnswer(card, v) {
  const money = [card.answer, ...(card.accepted_answers || [])].some((a) => String(a).includes('$'));
  return money && /^\d/.test(String(v)) ? `$${v}` : String(v ?? '');
}
function reviewRow(res) {
  const c = res.card;
  let title = c.audio_text || '';
  if (!title && c.sentence) title = String(c.sentence).replace(/_{2,}/, c.answer);
  if (!title) title = c.prompt_en;
  const meta = [TYPE_LABEL[c.type] || c.type];
  if (c.type === 'fix_it') meta.push(tagLabel((c.grammar_tags || [])[0] || ''));
  else meta.push(fmtAnswer(c, c.answer));
  if (res.skipped) meta.push('skipped');
  else if (c.type === 'listen_type') meta.push(`you typed ${fmtAnswer(c, res.given)}`);
  else if (c.type !== 'fix_it') meta.push(`you picked ${res.given}`);
  else meta.push(`you wrote ${res.given}`);
  const mini = c.audio_text && hasAudio(c.audio_text) ? `<button class="mini" data-play="normal" data-text="${esc(c.audio_text)}" aria-label="Play">${ICON.speaker}</button>` : '';
  return `<div class="review-row"><div class="grow"><p class="es">${esc(title)}</p><p class="caption">${meta.filter(Boolean).map(esc).join('<span class="sep" style="margin:0 7px">·</span>')}</p></div>${mini}</div>`;
}
function showSummary() {
  const ses = S.session;
  stop(); setKeyHandler(null);
  srs.finishSession(S.progress);
  S.progress = srs.load();
  S.view = 'summary';
  // Stars (v2): computed and saved once per session.
  if (!ses.starsResult) {
    ses.starsResult = levels.starsFor(ses.results, ses.level);
    ses.starsSaved = levels.record(ses.level, ses.topic, ses.starsResult.stars);
  }
  const right = ses.results.filter((r) => r.ok).length;
  const mins = Math.max(1, Math.round((Date.now() - ses.start) / 60000));
  const misses = ses.results.filter((r) => !r.ok);
  const today = srs.answeredToday(S.progress);
  // Queue this session's answers for the results sheet (stored first, so nothing is lost offline), then send.
  const cv = Number(S.content?.version?.version) || 0;
  if (!ses.rowIds) ses.rowIds = results.enqueue(ses.results.map((r) => ({
    answered_at: r.at, card_id: r.card.id, content_version: cv, correct: !!r.ok,
    answer_given: r.given == null ? '' : String(r.given), used_hint: !!r.hint, used_slow: !!r.slow, session_id: ses.id,
    level: ses.level || 'easy', topic: ses.topic || '', mission_id: '', stars: ses.starsResult.stars,
  })), validCardIds());
  S.summaryRowIds = ses.rowIds;
  if (ses.level === 'medium') return showTopicSummary(ses, mins);
  const statusLine = S.summaryRowIds.length
    ? `<p class="caption results-status" data-testid="results-status" data-state="queued" role="status">${resultsStatusHtml(false)}</p>` : '';
  render(`
  <div class="screen page summary" data-screen="summary">
    <div class="zone summary-zone"><div class="scroll scroller"><div class="scroll-inner">
      <p class="eyebrow">${esc(fmtDate())}<span class="sub">${mins} min</span><span class="sub" data-testid="session-stars">${ICON.star(11)} ${ses.starsResult.stars}</span></p>
      <h1 class="display">Session done</h1>
      <div class="dial">${dialSvg(ses.results)}<div class="center"><span class="score" data-testid="score">${right}<span class="dim">/${ses.results.length}</span></span><span class="caption">correct</span></div></div>
      ${statusLine}
      <section class="stats small">
        <div class="col"><div class="value">${srs.streak(S.progress)}</div><p class="caption">day streak</p></div>
        <div class="divider"></div>
        <div class="col"><div class="value">${today} <span class="dim">/ ${srs.DAILY_GOAL}</span></div><p class="caption">cards today</p></div>
      </section>
      ${misses.length ? `<div class="review-head"><span class="eyebrow">Review again</span><span class="caption">back tomorrow</span></div>
        <div class="review-list">${misses.map(reviewRow).join('')}</div>`
        : `<div class="review-head"><span class="eyebrow">Review again</span></div><p class="body-copy empty-note">Nothing to review. ¡Muy bien!</p>`}
    </div></div>${moreHint()}</div>
    <div class="action"><button class="btn-primary" data-act="done">Done</button></div>
  </div>`);
  $$('[data-play]').forEach((b) => { b.onclick = () => play(b.dataset.text, { btn: b }); });
  $('[data-act="done"]').onclick = () => goHome(true);
  setupScrollHint($('.summary-zone'));
  if (S.summaryRowIds.length) sendResults();
}


// ======================================================================= v2 Levels
// ---------- Medium: topic grid ----------
const topicCards = (code) => S.medium.filter((c) => c.topic === code);
function openTopics(push = true) {
  stop(); setKeyHandler(null);
  if (push) pushView('topics');
  S.view = 'topics'; S.session = null;
  const best = levels.load().topics;
  const withCards = levels.TOPICS.filter((t) => topicCards(t.code).length);
  const soon = levels.TOPICS.filter((t) => !topicCards(t.code).length);
  render(`
  <div class="screen page topics" data-screen="topics">
    <button class="back-link" data-act="home">${ICON.back}<span>Home</span></button>
    <p class="eyebrow lv-tag">Medium</p>
    <h1 class="title lv-title">Topic challenge</h1>
    <p class="caption lv-cap">Pick a topic: 10 cards, normal-speed audio.</p>
    <div class="zone topics-zone"><div class="scroller"><div class="scroll-inner">
      <div class="topic-grid" data-testid="topic-grid">${withCards.map((t) => `
        <button class="topic-tile" data-topic="${esc(t.code)}" data-testid="topic-tile">
          <span class="tname">${esc(t.name)}</span>${starRow(best[t.code] || 0, 12, `Best: ${best[t.code] || 0} of 3 stars`)}
        </button>`).join('')}</div>
      ${soon.length ? `<p class="eyebrow soon-head">Coming soon</p><p class="caption soon" data-testid="coming-soon">${soon.map((t) => esc(t.name)).join(' · ')}</p>` : ''}
    </div></div>${moreHint()}</div>
  </div>`);
  $('[data-act="home"]').onclick = () => goHome(true);
  $$('[data-topic]').forEach((b) => { b.onclick = () => startTopic(b.dataset.topic); });
  setupScrollHint($('.topics-zone'));
}
function startTopic(code) {
  const cards = srs.planTopic(S.progress, topicCards(code));
  if (!cards.length) return;
  S.session = { cards, i: 0, results: [], start: Date.now(), id: results.newSessionId(), level: 'medium', topic: code };
  S.view = 'session';
  pushView('session');
  showCard();
}
function fmtDuration(ms) {
  const s = Math.max(1, Math.round(ms / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}
function starsSentence(r, level) {
  const bits = [];
  if (r.stars === 3) bits.push(`3 stars: ${r.need3} or more right with no hints.`);
  else if (r.stars === 2) bits.push(r.hints && r.right >= r.need3 ? `2 stars: 3 stars needs ${r.need3} right with no hints.` : `2 stars: ${r.need2} or more right. ${r.need3} right with no hints earns 3.`);
  else bits.push(`1 star for finishing. ${r.need2} right earns 2 stars.`);
  if (level === 'medium' && r.hints && r.stars < 3) bits.push('A right answer after a hint counts as half.');
  return bits.join(' ');
}
function showTopicSummary(ses, mins) {
  const r = ses.starsResult, saved = ses.starsSaved, name = levels.topicName(ses.topic);
  const typed = (c) => c.type === 'listen_type' || (c.type === 'fix_it' && !hasOptions(c));
  const cnt = (f) => { const rs = ses.results.filter((x) => f(x.card)); return { n: rs.length, ok: rs.filter((x) => x.ok).length }; };
  const typing = cnt(typed), listening = cnt((c) => !typed(c) && !!c.audio_text), choosing = cnt((c) => !typed(c) && !c.audio_text);
  const row = (k, v) => `<div class="bd-row"><span>${esc(k)}</span><span class="bd-val">${esc(v)}</span></div>`;
  const statusLine = S.summaryRowIds.length
    ? `<p class="caption results-status" data-testid="results-status" data-state="queued" role="status">${resultsStatusHtml(false)}</p>` : '';
  const before = saved.prev ? ` Your best before was ${saved.prev}.` : '';
  render(`
  <div class="screen page summary msum" data-screen="summary" data-level="medium" data-topic="${esc(ses.topic)}">
    <div class="zone summary-zone"><div class="scroll scroller"><div class="scroll-inner">
      <p class="eyebrow lv-tag">Medium<span class="sub-dot">·</span>${esc(name)}</p>
      <div class="stars-line">${starRow(r.stars, 40)}${saved.newBest ? '<span class="new-best" data-testid="new-best">New best</span>' : ''}</div>
      <h1 class="display msum-score" data-testid="score">${r.right} of ${r.n} right</h1>
      <p class="body-copy msum-why">${esc(starsSentence(r, 'medium') + before)}</p>
      ${statusLine}
      <div class="breakdown">
        ${typing.n ? row('Typing', `${typing.ok} of ${typing.n}`) : ''}
        ${listening.n ? row('Listening', `${listening.ok} of ${listening.n}`) : ''}
        ${choosing.n ? row('Choosing', `${choosing.ok} of ${choosing.n}`) : ''}
        ${row('Hints used', String(r.hints))}
        ${row('Time', fmtDuration(Date.now() - ses.start))}
      </div>
      <p class="caption topic-note">Topic stars: ${esc(name)} is now at ${saved.best} of 3.</p>
    </div></div>${moreHint()}</div>
    <div class="action two"><button class="btn-text" data-act="another">Another topic</button><button class="btn-primary" data-act="done">Done</button></div>
  </div>`);
  $('[data-act="done"]').onclick = () => goHome(true);
  $('[data-act="another"]').onclick = () => { S.view = 'summary-back'; history.back(); }; // back to the grid
  setupScrollHint($('.summary-zone'));
  if (S.summaryRowIds.length) sendResults();
}

// ---------- Hard: missions ----------
function isPlayableMission(m) {
  try {
    const md = m.media || {};
    const okMedia = md.kind === 'text' ? !!md.text_es : md.kind === 'audio' && (md.lines || []).length > 0;
    const qs = (m.questions || []).filter((q) => (q.kind === 'pick' && hasOptions(q)) || (q.kind === 'type' && hasAnswers(q)));
    return !!m.id && okMedia && qs.length > 0;
  } catch { return false; }
}
const mQuestions = (m) => m.questions.filter((q) => (q.kind === 'pick' && hasOptions(q)) || (q.kind === 'type' && hasAnswers(q)));
const shortTitle = (m) => String(m.title_en || '').split(':')[0].trim();
const cleanPrompt = (p) => String(p || '').replace(/\s*\(type it like [^)]*\)\s*$/i, '');
function clipMinutes(m) {
  const d = clipInfo(m.id)?.duration;
  if (!d) return 'about 1 min';
  return d < 40 ? 'under a minute' : `about ${Math.max(1, Math.round(d / 60))} min`;
}
/** "WhatsApp from your landlord: …" -> "Your landlord". Content may set media.sender_en instead. */
function senderOf(m) {
  if (m.media?.sender_en) return m.media.sender_en;
  const x = /from (?:the |your |a )?([^:]+?)(?::|$)/i.exec(m.title_en || '');
  const who = x ? x[1].trim() : '';
  if (!who) return 'The message';
  return /^(your|the)\b/i.test(who) ? who.replace(/^./, (c) => c.toUpperCase()) : (/from your /i.test(m.title_en) ? `Your ${who}` : who);
}
const FORMAT_LABEL = { whatsapp: 'WhatsApp', sign: 'Sign', menu: 'Menu', receipt: 'Receipt', email: 'Email', label: 'Label' };
function openMissions(push = true) {
  stop(); setKeyHandler(null);
  if (push) pushView('missions');
  S.view = 'missions'; S.mission = null;
  const best = levels.load().missions;
  render(`
  <div class="screen page topics missions" data-screen="missions">
    <button class="back-link" data-act="home">${ICON.back}<span>Home</span></button>
    <p class="eyebrow lv-tag">Hard</p>
    <h1 class="title lv-title">Real-life mission</h1>
    <p class="caption lv-cap">One longer situation: listen or read, then answer.</p>
    <div class="zone topics-zone"><div class="scroller"><div class="scroll-inner"><div class="mission-list">${S.missions.map((m) => `
      <button class="mission-row" data-mission="${esc(m.id)}" data-testid="mission-row">
        <span class="ch-text"><span class="ch-title">${esc(m.title_en)}</span>
          <span class="caption">${m.media.kind === 'audio' ? `Audio · ${esc(clipMinutes(m))}` : `Message · ${esc(FORMAT_LABEL[m.media.text_format] || 'text')}`} · ${mQuestions(m).length} questions</span>
          ${starRow(best[m.id] || 0, 12)}</span>
        <span class="ch-chev">${ICON.chevron}</span>
      </button>`).join('')}</div></div></div>${moreHint()}</div>
  </div>`);
  $('[data-act="home"]').onclick = () => goHome(true);
  $$('[data-mission]').forEach((b) => { b.onclick = () => startMission(b.dataset.mission); });
  setupScrollHint($('.topics-zone'));
}
function startMission(id) {
  const m = S.missions.find((x) => x.id === id);
  if (!m) return;
  S.mission = { m, qs: mQuestions(m), i: 0, answers: [], start: Date.now(), slow: false, hintUsed: false, id: results.newSessionId() };
  S.view = 'mission';
  pushView('mission');
  missionIntro();
}
function missionTop(right = '') {
  return `<header class="topbar mtop"><button class="close" data-act="close" aria-label="End mission">${ICON.close}</button><span class="grow"></span>${right}</header>`;
}
function bindMissionClose() { $('[data-act="close"]').onclick = () => goHome(true); }
function missionIntro() {
  const { m, qs } = S.mission;
  const audio = m.media.kind === 'audio';
  const primary = audio ? (/voicemail/i.test(m.title_en) ? 'Play voicemail' : /call/i.test(m.title_en) ? 'Play the call' : 'Play announcement') : 'Read the message';
  const fact = (k, v) => `<div class="fact"><span>${esc(k)}</span><span class="fv">${esc(v)}</span></div>`;
  render(`
  <div class="screen mission-screen" data-screen="mission-intro" data-mission="${esc(m.id)}">
    ${missionTop()}
    <div class="zone intro-zone"><div class="scroller"><div class="scroll-inner">
      <p class="eyebrow lv-tag">Hard · Real-life mission</p>
      <h1 class="title mi-title">${esc(m.title_en)}</h1>
      <p class="body-copy mi-sit">${esc(m.situation_en)}</p>
      <div class="facts">
        ${audio ? fact('Audio', clipMinutes(m)) : fact('Message', FORMAT_LABEL[m.media.text_format] || 'Text')}
        ${fact('Questions', `${qs.length}, one at a time`)}
        ${fact(audio ? 'Replay' : 'Reread', audio ? 'Normal or slow, any time' : 'Any time')}
      </div>
      <p class="caption mi-note">The transcript and a “Why” for each question open after you answer.</p>
    </div></div>${moreHint()}</div>
    <div class="dock">
      <button class="btn-text" data-act="preview">Read the questions first</button>
      <button class="btn-primary" data-act="begin">${esc(primary)}</button>
    </div>
  </div>`);
  bindMissionClose();
  setupScrollHint($('.intro-zone'));
  $('[data-act="preview"]').onclick = () => openSimpleSheet('Questions', `<ol class="q-preview">${qs.map((q) => `<li>${esc(cleanPrompt(q.prompt_en))}</li>`).join('')}</ol>`);
  $('[data-act="begin"]').onclick = () => {
    if (audio) { missionQuestion(); playClip(m.id, { slow: S.mission.slow }); }
    else missionMessage();
  };
}
function openSimpleSheet(title, html, { sub = '', bindWith = null } = {}) {
  closeSheet();
  const el = document.createElement('div');
  el.className = 'sheet-backdrop fb-backdrop';
  el.innerHTML = `<div class="sheet fb-sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}" data-testid="sheet">
    <span class="grab" aria-hidden="true"></span>
    <div class="zone sheet-zone"><div class="sheet-scroll scroller"><div class="scroll-inner"><section class="sheet-sec">${eyebrow(title, sub)}${html}</section></div></div>${moreHint()}</div>
    <button class="btn-text sheet-close" data-act="sheet-close">Close</button>
  </div>`;
  el.addEventListener('click', (e) => { if (e.target === el) closeSheet(); });
  el.querySelector('.grab').addEventListener('click', () => closeSheet());
  el.querySelector('[data-act="sheet-close"]').onclick = () => closeSheet();
  document.body.appendChild(el);
  S.sheet = el;
  S.sheetPrevKey = S.keyHandler; setKeyHandler((e) => { if (e.key === 'Escape') closeSheet(); });
  if (bindWith) bindAudio(null, el);
  setupScrollHint(el.querySelector('.sheet-zone'));
  el.querySelector('.sheet-close').focus({ preventScroll: true });
}
/** Text missions: the message in one WhatsApp-style bubble, scrolling with the "More below" fade. */
function missionMessage(backTo = null) {
  const { m } = S.mission;
  const md = m.media;
  const fmt = FORMAT_LABEL[md.text_format] || '';
  render(`
  <div class="screen mission-screen" data-screen="mission-msg" data-mission="${esc(m.id)}">
    ${missionTop('<span class="count">Read, then answer</span>')}
    <p class="caption sender" data-testid="sender">${esc([senderOf(m), fmt, md.time].filter(Boolean).join(' · '))}</p>
    <div class="zone msg-zone"><div class="scroller" data-testid="msg-scroll"><div class="scroll-inner">
      <div class="bubble ${md.text_format === 'whatsapp' ? 'wa' : ''}" data-testid="bubble" lang="es">${esc(md.text_es)}${md.time ? `<span class="btime">${esc(md.time)}</span>` : ''}</div>
    </div></div>${moreHint()}</div>
    <div class="dock"><button class="btn-primary" data-act="to-questions">${backTo == null ? 'Go to questions' : `Back to question ${backTo + 1}`}</button></div>
  </div>`);
  bindMissionClose();
  setupScrollHint($('.msg-zone'));
  $('[data-act="to-questions"]').onclick = () => missionQuestion();
}
const fmtClock = (sec) => { sec = Math.max(0, Math.floor(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; };
function playerBar(m) {
  const info = clipInfo(m.id) || {};
  const tot = S.mission.slow ? info.slow_duration : info.duration;
  return `<div class="player" data-testid="player">
    <button class="pl-play" data-act="pl-play" aria-label="Play">${ICON.speaker}</button>
    <div class="pl-mid">
      <div class="pl-track" data-testid="scrub" role="slider" aria-label="Position" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" tabindex="0"><i class="pl-fill"></i><b class="pl-knob"></b></div>
      <div class="caption pl-time"><span class="pl-el">0:00</span> / <span class="pl-tot">${fmtClock(tot)}</span></div>
    </div>
    <button class="slow s48 pl-slow ${S.mission.slow ? 'is-on' : ''}" data-act="pl-slow" data-testid="slow" aria-pressed="${S.mission.slow}" aria-label="Slow (0.75×)">0.75×</button>
  </div>`;
}
function bindPlayer(m) {
  const el = audioEl();
  const bar = $('.player');
  if (!bar) return;
  const btn = $('[data-act="pl-play"]', bar), fill = $('.pl-fill', bar), knob = $('.pl-knob', bar), track = $('.pl-track', bar);
  const elT = $('.pl-el', bar), totT = $('.pl-tot', bar);
  const upd = () => {
    if (!document.body.contains(bar)) return;
    const mine = clipLoaded(m.id);
    const d = mine && isFinite(el.duration) ? el.duration : (S.mission?.slow ? clipInfo(m.id)?.slow_duration : clipInfo(m.id)?.duration) || 0;
    const t = mine ? el.currentTime : 0;
    const f = d ? Math.min(1, t / d) : 0;
    fill.style.width = `${f * 100}%`; knob.style.left = `${f * 100}%`;
    track.setAttribute('aria-valuenow', String(Math.round(f * 100)));
    elT.textContent = fmtClock(t); totT.textContent = fmtClock(d);
    const playing = mine && !el.paused && !el.ended;
    bar.classList.toggle('is-active', playing);
    btn.innerHTML = playing ? ICON.pause : ICON.speaker;
    btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  };
  if (S.playerOff) S.playerOff();
  const evs = ['timeupdate', 'play', 'pause', 'ended', 'loadedmetadata', 'seeked'];
  evs.forEach((e) => el.addEventListener(e, upd));
  S.playerOff = () => evs.forEach((e) => el.removeEventListener(e, upd));
  const frac = () => (clipLoaded(m.id) && isFinite(el.duration) && el.duration ? el.currentTime / el.duration : 0);
  btn.onclick = () => {
    if (clipLoaded(m.id) && !el.paused && !el.ended) el.pause();
    else if (clipLoaded(m.id) && !el.ended && el.currentTime > 0) el.play().catch(() => {});
    else playClip(m.id, { slow: S.mission.slow });
  };
  $('[data-act="pl-slow"]', bar).onclick = (e) => {
    S.mission.slow = !S.mission.slow;
    const b = e.currentTarget; b.classList.toggle('is-on', S.mission.slow); b.setAttribute('aria-pressed', String(S.mission.slow));
    const f = frac(), wasPlaying = clipLoaded(m.id) && !el.paused && !el.ended;
    if (wasPlaying || f > 0) playClip(m.id, { slow: S.mission.slow, at: f });
    else playClip(m.id, { slow: S.mission.slow });
  };
  const seek = (ev) => {
    const r = track.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
    if (clipLoaded(m.id) && isFinite(el.duration)) { el.currentTime = f * el.duration; if (el.paused) el.play().catch(() => {}); }
    else playClip(m.id, { slow: S.mission.slow, at: f });
  };
  track.addEventListener('pointerdown', (ev) => { seek(ev); track.setPointerCapture?.(ev.pointerId); track.onpointermove = seek; });
  track.addEventListener('pointerup', () => { track.onpointermove = null; });
  upd();
}
function keypadHtml() {
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', ':', '0', 'del'];
  return keys.map((k) => k === 'del'
    ? `<button class="key" data-key="del" aria-label="Delete">${ICON.backspace}</button>`
    : `<button class="key ${k === ':' ? 'colon' : ''}" data-key="${k}">${k}</button>`).join('');
}
function missionQuestion() {
  const ms = S.mission;
  const { m, qs } = ms;
  const q = qs[ms.i];
  ms.hintUsed = false; ms.selected = null;
  setKeyHandler(null);
  const audio = m.media.kind === 'audio';
  const media = audio ? playerBar(m)
    : `<button class="msg-row" data-act="reopen" data-testid="msg-row"><span>Message from ${esc(senderOf(m).replace(/^Your /, 'your ').replace(/^The /, 'the '))}</span>${ICON.chevron}</button>`;
  const pick = q.kind === 'pick';
  const letters = !pick && wordAnswer(q);
  let dock, dockClass = '';
  if (pick) dock = `<button class="btn-primary" data-act="check" disabled>Check</button>`;
  else if (letters) { dock = textInputRow(); dockClass = 'fix-dock'; }
  else { dock = `<div class="typed" data-testid="typed" aria-live="polite"><span class="value"></span><span class="caret"></span></div>
      <button class="btn-primary" data-act="check" disabled>Check</button><div class="keypad" data-testid="keypad">${keypadHtml()}</div>`; dockClass = 'type-dock'; }
  const opts = pick ? `<div class="moptions" data-testid="options" role="radiogroup">${q.options.map((o, i) => `<button class="mopt" data-opt="${i}" role="radio" aria-checked="false">${esc(o)}</button>`).join('')}</div>` : '';
  render(`
  <div class="screen card-screen mission-screen" data-screen="mission-q" data-mission="${esc(m.id)}" data-q="${esc(q.id)}" data-kind="${esc(q.kind)}">
    ${missionTop(`${hintPill(q)}<span class="count mcount">Question ${ms.i + 1} of ${qs.length}</span>`)}
    ${media}
    <div class="zone card-zone mq-zone"><div class="card-body scroller" data-testid="card-body"><div class="card-inner">
      <h2 class="title prompt mq-prompt">${esc(q.prompt_en)}</h2>
      ${hintBox(q)}
      ${opts}
    </div></div>${moreHint()}</div>
    <div class="dock ${dockClass}" data-testid="dock">${dock}</div>
  </div>`);
  bindMissionClose();
  bindHint(q);
  if (audio) bindPlayer(m);
  else $('[data-act="reopen"]').onclick = () => missionMessage(ms.i);
  const done = (ok, given) => {
    ms.answers.push({ q, ok, given, hint: !!ms.hintUsed, at: new Date().toISOString() });
    ms.i++;
    if (ms.i >= qs.length) missionResult(); else missionQuestion();
  };
  const check = $('[data-act="check"]');
  if (pick) {
    $$('[data-opt]').forEach((b) => {
      b.onclick = () => {
        $$('[data-opt]').forEach((x) => { x.classList.remove('is-selected'); x.setAttribute('aria-checked', 'false'); });
        b.classList.add('is-selected'); b.setAttribute('aria-checked', 'true');
        ms.selected = Number(b.dataset.opt); check.disabled = false;
      };
    });
    check.onclick = () => { if (ms.selected == null) return; const o = q.options[ms.selected]; done(o === q.answer, o); };
  } else if (letters) {
    bindTextInput(q, (v) => done(isCorrect(q, v), v));
  } else {
    let val = '';
    const box = $('.typed');
    const upd = () => { box.innerHTML = `<span class="value ${/[a-z]/i.test(val) ? 'words' : ''}">${esc(val)}</span><span class="caret"></span>`; check.disabled = !val.trim(); };
    const press = (k) => { if (k === 'del') val = val.slice(0, -1); else if (val.length < 24) val += k; upd(); };
    $$('[data-key]').forEach((b) => { b.onclick = () => press(b.dataset.key); });
    check.onclick = () => { if (val.trim()) done(isCorrect(q, val), val.trim()); };
    setKeyHandler((e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || S.sheet) return;
      if (e.key === 'Enter') { e.preventDefault(); check.click(); }
      else if (e.key === 'Backspace') { e.preventDefault(); press('del'); }
      else if (/^[0-9:.a-zA-Záéíóúñü ]$/.test(e.key)) { e.preventDefault(); press(e.key); }
    });
  }
  setupScrollHint($('.mq-zone'));
}
function missionResult() {
  const ms = S.mission;
  const { m } = ms;
  stop(); setKeyHandler(null);
  if (S.playerOff) { S.playerOff(); S.playerOff = null; }
  if (!ms.starsResult) {
    ms.starsResult = levels.starsFor(ms.answers, 'hard');
    ms.starsSaved = levels.record('hard', m.id, ms.starsResult.stars);
    srs.recordActivity(S.progress, ms.answers.length);   // a finished mission counts for the streak
    S.progress = srs.load();
    ms.end = Date.now();
    // Results upload: the Apps Script only accepts card ids (c-NNNN) for now, so mission answers are kept
    // on the phone (oye.stars.v1 log) and not queued. See README "Results upload".
  }
  const r = ms.starsResult;
  const rows = ms.answers.map((a, i) => {
    const given = a.q.kind === 'pick' ? `You picked “${a.given}”` : `You typed ${a.given}`;
    const open = !a.ok;
    return `<div class="mr-row ${a.ok ? 'ok' : 'bad'} ${open ? 'open' : ''}" data-q="${esc(a.q.id)}" data-testid="mr-row">
      <button class="mr-head" data-act="why" aria-expanded="${open}">
        <span class="mr-ico">${a.ok ? ICON.check(18) : ICON.cross(18)}</span>
        <span class="mr-prompt">${esc(cleanPrompt(a.q.prompt_en))}</span>
        <span class="mr-chev">${open ? ICON.chevronDown : ICON.chevron}</span>
      </button>
      <div class="why-box ${a.ok ? 'is-ok' : ''}" data-testid="why-box" ${open ? '' : 'hidden'}>
        <p class="caption wb-given">${esc(given)}${a.ok ? '' : ` · The answer is “${esc(a.q.answer)}”`}</p>
        <p class="caption wb-why">${esc(a.q.explanation_en || '')}</p>
      </div>
    </div>`;
  }).join('');
  render(`
  <div class="screen page mission-result" data-screen="mission-result" data-mission="${esc(m.id)}">
    ${missionTop()}
    <div class="mr-head-block">
      ${starRow(r.stars, 32)}
      <h1 class="display mr-score" data-testid="score">${r.right} of ${r.n} right</h1>
      <p class="caption mr-cap">${esc(shortTitle(m))} · ${r.need3} of ${r.n} right${r.hints ? ' with no hints' : ''} earns 3 stars${ms.starsSaved.newBest && ms.starsSaved.prev ? ' · New best' : ''}</p>
    </div>
    <div class="zone mr-zone"><div class="scroller"><div class="scroll-inner">
      ${rows}
      <button class="mr-row transcript-row" data-act="transcript" data-testid="transcript-row"><span>Read the transcript</span>${ICON.chevron}</button>
    </div></div>${moreHint()}</div>
    <div class="action two"><button class="btn-text" data-act="retry">Try again</button><button class="btn-primary" data-act="done">Done</button></div>
  </div>`);
  bindMissionClose();
  $$('[data-act="why"]').forEach((b) => {
    b.onclick = () => {
      const row = b.closest('.mr-row'), box = row.querySelector('.why-box');
      const open = box.hidden;
      box.hidden = !open; row.classList.toggle('open', open); b.setAttribute('aria-expanded', String(open));
      row.querySelector('.mr-chev').innerHTML = open ? ICON.chevronDown : ICON.chevron;
      refreshHints();
    };
  });
  $('[data-act="transcript"]').onclick = () => openMissionTranscript(m);
  $('[data-act="done"]').onclick = () => goHome(true);
  $('[data-act="retry"]').onclick = () => {
    Object.assign(ms, { i: 0, answers: [], start: Date.now(), starsResult: null, starsSaved: null, id: results.newSessionId() });
    missionIntro();
  };
  setupScrollHint($('.mr-zone'));
}
function openMissionTranscript(m) {
  const md = m.media;
  let html;
  if (md.kind === 'audio') {
    const lines = md.lines.map((l) => ({ ...l }));
    html = `${dialogueHtml(lines, null)}`;
  } else {
    html = `<p class="heard-text msg-text" lang="es">${esc(md.text_es)}</p>${md.text_en ? `<p class="eyebrow en-head">In English</p><p class="caption msg-en">${esc(md.text_en)}</p>` : ''}`;
  }
  if (m.region_note) html += `<div class="region"><p class="eyebrow">Mexico vs Spain</p><p class="caption">${esc(m.region_note)}</p></div>`;
  openSimpleSheet(md.kind === 'audio' ? 'Transcript' : 'The message', html, { sub: shortTitle(m), bindWith: true });
}


boot();
