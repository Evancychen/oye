// Oye: app shell, screens and card templates.
// v1.1: one-screen cards (header / scrolling middle / pinned dock), hints, feedback sheet, SW update flow.
import { loadContent } from './content.js';
import { setAudioIndex, hasAudio, play, stop } from './audio.js';
import { isCorrect } from './check.js';
import * as srs from './srs.js';

export const SHELL_VERSION = '1.1';
document.documentElement.dataset.shell = SHELL_VERSION;

const $app = document.getElementById('app');
const S = {
  content: null, playable: [], scenes: new Map(), progress: srs.load(),
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
  if (!S.updatePending || S.view === 'session') return false;
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
    if (S.sheet) { closeSheet(); if (S.view === 'session') history.pushState({ oye: 'session' }, ''); return; }
    if (S.view !== 'home') goHome(false);
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
  const skipped = S.content.cards.length - S.playable.length;
  if (skipped) console.info(`[oye] skipped ${skipped} card(s) with unknown type or missing fields`);
  S.content.audioSync.then((r) => { S.audioStatus = { ...S.audioStatus, finished: true, ...r }; const q = document.getElementById('quiet'); if (q) q.textContent = quietLine(); });
  goHome(false);
  document.documentElement.dataset.ready = '1';
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
function lessonPool() {
  const l = S.progress.lesson || 'all';
  return l === 'all' ? S.playable : S.playable.filter((c) => cardLessons(c).includes(l));
}
function lessonName(tag) {
  const m = /^(A\d)-L(\d+)$/.exec(tag || '');
  return m ? `${m[1]} · Lesson ${Number(m[2])}` : tag;
}
function sessionMeta(plan) {
  if (!plan.length) return 'No cards for this lesson yet';
  const mins = Math.max(1, Math.round(plan.length * 0.4));
  const prices = plan.filter(srs.isPriceByEar).length;
  const listening = plan.filter((c) => c.audio_text).length;
  const focus = prices >= plan.length / 2 ? 'mostly prices' : listening >= plan.length / 2 ? 'mostly listening' : 'mixed';
  return [`${plan.length} cards`, `about ${mins} min`, focus].map(esc).join('<span class="sep">·</span>');
}
function goHome(pop = true) {
  stop(); setKeyHandler(null);
  if (pop && history.state?.oye) { history.back(); return; } // popstate will call goHome(false)
  S.view = 'home'; S.session = null;
  if (maybeApplyUpdate()) return; // a new version was installed: reload into it now
  S.progress = srs.load();
  const newIds = new Set(S.content.version?.new_card_ids || []);
  S.plan = srs.planSession(S.progress, lessonPool(), { newIds });
  const st = srs.streak(S.progress), dots = srs.last7(S.progress);
  const today = srs.answeredToday(S.progress);
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
        <div class="dots" aria-label="Last 7 days">${dots.map((d) => `<i class="${d ? 'on' : ''}"></i>`).join('')}</div>
      </div>
      <div class="divider"></div>
      <div class="col">
        <div class="value">${today} <span class="dim">/ ${srs.DAILY_GOAL}</span></div>
        <p class="caption">cards today</p>
        <div class="bar"><i style="width:${Math.min(100, (today / srs.DAILY_GOAL) * 100)}%"></i></div>
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
    <button class="row-btn" data-act="lessons"><span class="grow">Lessons</span><span class="val">${esc(lesson === 'all' ? 'All' : lessonName(lesson))} ${ICON.chevron}</span></button>
    <p class="caption meta-line">${sessionMeta(S.plan)}</p>
    <div class="action" style="padding-top:16px">
      <button class="btn-primary" data-act="start" ${S.plan.length ? '' : 'disabled'}>Quick session</button>
    </div>
  </div>`);
  $('[data-act="start"]').onclick = startSession;
  $('[data-act="lessons"]').onclick = openLessons;
}

function openLessons() {
  const counts = new Map();
  for (const c of S.playable) for (const t of cardLessons(c)) counts.set(t, (counts.get(t) || 0) + 1);
  const tags = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'en', { numeric: true }));
  const cur = S.progress.lesson || 'all';
  const row = (val, label, n) => `<button class="sheet-row ${cur === val ? 'on' : ''}" data-lesson="${esc(val)}"><span>${esc(label)}<span class="caption">${n} cards</span></span>${cur === val ? `<span class="check-mark">${ICON.check(18)}</span>` : ''}</button>`;
  const el = document.createElement('div');
  el.className = 'sheet-backdrop';
  el.innerHTML = `<div class="sheet" role="dialog" aria-label="Lessons"><span class="eyebrow">Lessons</span>
    ${row('all', 'All lessons', S.playable.length)}${tags.map((t) => row(t, lessonName(t), counts.get(t))).join('')}</div>`;
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
  S.session = { cards: S.plan, i: 0, results: [], start: Date.now() };
  S.view = 'session';
  history.pushState({ oye: 'session' }, '');
  showCard();
}
function showCard(fb = null) {
  const ses = S.session;
  const card = ses.cards[ses.i];
  stop(); setKeyHandler(null);
  if (!fb) ses.hintUsed = false;
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
  $$('[data-play]', root).forEach((b) => { b.onclick = () => play(b.dataset.text || card.audio_text, { slow: b.dataset.play === 'slow', btn: b }); });
}
function answer(card, ok, given, skipped = false) {
  if (!S.session || S.session.answered === S.session.i) return;
  S.session.answered = S.session.i;
  const hint = !!S.session.hintUsed;
  srs.record(S.progress, card, ok, { hint });
  S.session.results.push({ card, ok, given, skipped, hint });
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
    if (S.session) S.session.hintUsed = true;
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
function heardLines(card) {
  const scene = sceneOf(card);
  let lines = [];
  if (card.type === 'scene_question' && !card.audio_text && scene) lines = scene.dialogue.map((d) => ({ ...d }));
  else lines = parseTranscript(card.transcript || card.audio_text);
  if (scene) {
    const en = new Map(scene.dialogue.map((d) => [d.es, d.en]));
    lines = lines.map((l) => ({ ...l, en: l.en || en.get(l.es) || '' }));
  }
  return lines;
}
function speakerClasses(lines, scene) {
  const order = [];
  for (const l of [...(scene?.dialogue || []), ...lines]) if (l.speaker && !order.includes(l.speaker)) order.push(l.speaker);
  return (spk) => { const i = order.indexOf(spk); return i === 0 ? 'spk-1' : i === 1 ? 'spk-2' : 'spk-n'; };
}
function dialogueHtml(lines, scene, { play = true } = {}) {
  const cls = speakerClasses(lines, scene);
  return `<div class="dialogue">${lines.map((l) => `
    <div class="dline">
      ${play && hasAudio(l.es) ? `<button class="line-play" data-play="normal" data-text="${esc(l.es)}" data-testid="line-play" aria-label="Play this line">${ICON.speaker}</button>` : '<span class="line-play placeholder" aria-hidden="true"></span>'}
      <div class="dtext">${l.speaker ? `<p class="eyebrow spk ${cls(l.speaker)}">${esc(l.speaker)}</p>` : ''}<p class="es">${esc(l.es)}</p>${l.en ? `<p class="caption en">${esc(l.en)}</p>` : ''}</div>
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
  const secs = [];
  if (lines.length) {
    secs.push(`<section class="sheet-sec" data-sec="heard" data-testid="sheet-heard">
      ${eyebrow('What you heard', isScene ? scene.title_en : '')}
      ${isScene ? dialogueHtml(lines, scene) : `<p class="heard-text">${esc(lines.map((l) => l.es).join(' '))}</p>`}
      ${card.audio_text ? audioRow(card, { caption: isScene ? 'Whole clip' : '', cls: 'sheet-audio' }) : ''}
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
function optionsBlock(card, fb, { grid } = {}) {
  const opts = card.options;
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
    return `<button class="opt ${cls}" data-opt="${i}" ${fb ? 'disabled' : ''}>${esc(o)}${dot}</button>`;
  }).join('');
  return `<div class="options ${useGrid ? 'grid' : ''}" data-testid="options">${items}</div>`;
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
  if (!fb) return { body: `${head}${audioHero(card)}`, dock: optionsDock(card, null), dockClass: 'opts-dock', bind: () => bindOptions(card) };
  return { body: feedbackBody(card, fb, head), dock: optionsDock(card, fb), dockClass: 'opts-dock' };
}

// ---------- listen_type ----------
function renderListenType(card, fb) {
  const money = isMoney(card);
  const head = `${eyebrow('Listen and type')}<h2 class="title prompt">${esc(card.prompt_en || 'Type what you hear.')}</h2>`;
  const show = (v) => `${money && !/[a-z]/i.test(v || '') ? '<span class="prefix">$</span>' : ''}<span class="value ${/[a-z]/i.test(v || '') ? 'words' : ''}">${esc(v || '')}</span>`;
  if (fb) return { body: feedbackBody(card, fb, head), dock: btnNext };
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
    dock: `<div class="fix-row"><input id="fix-input" class="text-input" type="text" aria-label="Your answer" placeholder="Your answer" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="done" lang="es">
      <button class="btn-primary check-compact" data-act="check" disabled>Check</button></div>`,
    bind: () => {
      const inp = $('#fix-input'), check = $('[data-act="check"]'), blank = $('.blank');
      check.addEventListener('mousedown', (e) => e.preventDefault()); // don't drop the keyboard before the tap lands
      inp.oninput = () => { if (blank) blank.innerHTML = inp.value ? esc(inp.value) : '&nbsp;'; check.disabled = !inp.value.trim(); };
      inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); check.click(); } };
      inp.onfocus = () => setTimeout(() => { S.updateKeyboardInset?.(); refreshHints(); }, 350);
      setTimeout(() => inp.focus({ preventScroll: true }), 50);
      check.onclick = () => { const v = inp.value.trim(); if (v) { inp.blur(); answer(card, isCorrect(card, v), v); } };
    },
  };
}

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
  const right = ses.results.filter((r) => r.ok).length;
  const mins = Math.max(1, Math.round((Date.now() - ses.start) / 60000));
  const misses = ses.results.filter((r) => !r.ok);
  const today = srs.answeredToday(S.progress);
  render(`
  <div class="screen page summary" data-screen="summary">
    <div class="zone summary-zone"><div class="scroll scroller"><div class="scroll-inner">
      <p class="eyebrow">${esc(fmtDate())}<span class="sub">${mins} min</span></p>
      <h1 class="display">Session done</h1>
      <div class="dial">${dialSvg(ses.results)}<div class="center"><span class="score" data-testid="score">${right}<span class="dim">/${ses.results.length}</span></span><span class="caption">correct</span></div></div>
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
}

boot();
