// Oye: app shell, screens and card templates.
import { loadContent } from './content.js';
import { setAudioIndex, hasAudio, play, stop } from './audio.js';
import { isCorrect } from './check.js';
import * as srs from './srs.js';

const $app = document.getElementById('app');
const S = {
  content: null, playable: [], scenes: new Map(), progress: srs.load(),
  plan: [], session: null, view: 'boot', audioStatus: null, keyHandler: null,
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
  backspace: svg('<path d="M9 5.5H19.5A1.5 1.5 0 0 1 21 7V17A1.5 1.5 0 0 1 19.5 18.5H9L2.5 12Z" stroke="currentColor" stroke-width="1.6"/><path d="M11.5 9.5L16.5 14.5M16.5 9.5L11.5 14.5" stroke="currentColor" stroke-width="1.6"/>', { size: 26 }),
};
const TYPE_LABEL = { listen_pick: 'Listen', listen_type: 'Listen and type', scene_question: 'Scene', fix_it: 'Fix it', reply: 'Your reply' };
const TAG_LABEL = { tu_vs_usted: 'tú vs usted', ser_estar: 'ser vs estar', pronoun_pairs: 'pronoun pairs' };
const tagLabel = (t) => TAG_LABEL[t] || String(t).replace(/_vs_/g, ' vs ').replace(/_/g, ' ');
const fmtDate = (d = new Date()) => new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
const render = (html) => { $app.innerHTML = html; $app.scrollTop = 0; };
const $ = (sel, root = $app) => root.querySelector(sel);
const $$ = (sel, root = $app) => [...root.querySelectorAll(sel)];
function setKeyHandler(fn) {
  if (S.keyHandler) document.removeEventListener('keydown', S.keyHandler);
  S.keyHandler = fn || null;
  if (fn) document.addEventListener('keydown', fn);
}
const sceneOf = (card) => (card.scene_id ? S.scenes.get(card.scene_id) : null);
const cardLessons = (card) => [...new Set([...(card.lesson_tags || []), ...((sceneOf(card)?.lesson_tags) || [])])];

// ---------- card templates (picked by `type`; unknown types are skipped) ----------
const hasOptions = (c) => Array.isArray(c.options) && c.options.length >= 2 && c.options.includes(c.answer);
const hasAnswers = (c) => c.answer != null || (c.accepted_answers || []).length > 0;
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

// ---------- boot ----------
async function boot() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch((e) => console.warn('[oye] sw', e));
  }
  if (window.visualViewport) {
    const upd = () => {
      const kb = Math.max(0, window.innerHeight - visualViewport.height - visualViewport.offsetTop);
      document.documentElement.style.setProperty('--kb', kb > 80 ? `${kb}px` : '0px');
    };
    visualViewport.addEventListener('resize', upd);
    visualViewport.addEventListener('scroll', upd);
  }
  if (history.state?.oye) history.replaceState(null, '');
  window.addEventListener('popstate', () => { if (S.view !== 'home') goHome(false); });
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
    <div class="card-body">${view.body}</div>
    ${view.action}
  </div>`);
  $('[data-act="close"]').onclick = () => goHome(true);
  // audio buttons
  $$('[data-play]').forEach((b) => { b.onclick = () => play(b.dataset.text || card.audio_text, { slow: b.dataset.play === 'slow', btn: b }); });
  const nextBtn = $('[data-act="next"]');
  if (nextBtn) { nextBtn.onclick = () => nextCard(); nextBtn.focus({ preventScroll: true }); }
  const dk = $('[data-act="dontknow"]');
  if (dk) dk.onclick = () => answer(card, false, null, true);
  view.bind?.();
  // Autoplay once when a listening card appears (allowed after the tap that started the session).
  if (!fb && card.audio_text && hasAudio(card.audio_text)) {
    const big = $('.replay');
    setTimeout(() => { if (S.session?.cards[S.session.i] === card && $('[data-state="question"]')) play(card.audio_text, { btn: big }); }, 350);
  }
}
function answer(card, ok, given, skipped = false) {
  if (!S.session || S.session.answered === S.session.i) return;
  S.session.answered = S.session.i;
  srs.record(S.progress, card, ok);
  S.session.results.push({ card, ok, given, skipped });
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
function audioControls(card, size) {
  if (!card.audio_text) return '';
  const lbl = size !== 'sm';
  const ctl = (inner, label) => lbl ? `<div class="ctl"><div class="ring-wrap">${inner}</div><span class="caption">${label}</span></div>` : inner;
  return `<div class="audio ${size === 'sm' ? 'compact' : size}">
    ${ctl(`<button class="replay ${size}" data-play="normal" aria-label="Replay">${ICON.speaker}</button>`, 'Replay')}
    ${ctl(`<button class="slow ${size}" data-play="slow" aria-label="Replay slowly">0.75×</button>`, 'Slow')}
  </div>`;
}
function resultRow(ok) {
  return `<div class="result ${ok ? 'ok' : 'bad'}" data-testid="result"><span class="dot">${ok ? ICON.check(19) : ICON.cross(19)}</span><span class="title">${ok ? 'Correct' : 'Not quite'}</span></div>`;
}
function heard(card) {
  const t = card.transcript || card.audio_text;
  if (!t) return '';
  return `<div class="heard"><p class="eyebrow">You heard</p><p class="transcript">${esc(t)}</p></div>${audioControls(card, 'sm')}`;
}
function explain(card) {
  return `${card.explanation_en ? `<p class="body-copy explain">${esc(card.explanation_en)}</p>` : ''}${regionNote(card)}`;
}
function regionNote(card) {
  return card.region_note ? `<div class="region"><p class="eyebrow">Mexico vs Spain</p><p class="caption">${esc(card.region_note)}</p></div>` : '';
}
function optionsBlock(card, fb, { grid } = {}) {
  const opts = card.options;
  const useGrid = grid ?? (opts.length === 4 && opts.every((o) => String(o).length <= 7));
  const items = opts.map((o, i) => {
    let cls = '', dot = '';
    if (fb) {
      if (o === card.answer) { cls = 'is-correct'; dot = `<span class="status-dot is-correct">${ICON.check(14)}</span>`; }
      else if (o === fb.given) { cls = 'is-wrong'; dot = `<span class="status-dot is-wrong">${ICON.cross(14)}</span>`; }
      else cls = 'is-dim';
    }
    return `<button class="opt ${cls}" data-opt="${i}" ${fb ? 'disabled' : ''}>${esc(o)}${dot}</button>`;
  }).join('');
  return `<div class="options ${useGrid ? 'grid' : ''}">${items}</div>`;
}
function bindOptions(card) {
  $$('[data-opt]').forEach((b) => {
    b.onclick = () => { const o = card.options[Number(b.dataset.opt)]; answer(card, o === card.answer, o); };
  });
}
const actionDontKnow = `<div class="action"><button class="btn-text" data-act="dontknow">Don’t know</button></div>`;
const actionNext = `<div class="action"><button class="btn-primary" data-act="next">Next</button></div>`;

// ---------- listen_pick ----------
function renderListenPick(card, fb) {
  const head = `${eyebrow('Listen')}<h2 class="title prompt">${esc(card.prompt_en || 'What did you hear?')}</h2>`;
  if (!fb) return { body: `${head}${audioControls(card, 'xl')}<div class="spacer"></div>${optionsBlock(card)}`, action: actionDontKnow, bind: () => bindOptions(card) };
  return { body: `${head}${resultRow(fb.ok)}${heard(card)}${explain(card)}<div class="spacer"></div>${optionsBlock(card, fb)}`, action: actionNext };
}

// ---------- listen_type ----------
function renderListenType(card, fb) {
  const all = [card.answer, ...(card.accepted_answers || [])].map(String);
  const money = all.some((a) => a.includes('$'));
  const head = `${eyebrow('Listen and type')}<h2 class="title prompt">${esc(card.prompt_en || 'Type what you hear.')}</h2>`;
  const show = (v) => `${money && !/[a-z]/i.test(v || '') ? '<span class="prefix">$</span>' : ''}<span class="value ${/[a-z]/i.test(v || '') ? 'words' : ''}">${esc(v || '')}</span>`;
  if (fb) {
    const answerDisplay = `${money && !String(card.answer).includes('$') ? '$' : ''}${card.answer}`;
    const yours = fb.skipped ? '' : `<div class="answer-row ${fb.ok ? 'is-correct' : 'is-wrong'}"><span class="caption">You typed</span><span class="val">${esc((money && /^\d/.test(fb.given) ? '$' : '') + fb.given)}</span><span class="status-dot ${fb.ok ? 'is-correct' : 'is-wrong'}">${fb.ok ? ICON.check(14) : ICON.cross(14)}</span></div>`;
    const right = fb.ok ? '' : `<div class="answer-row is-correct"><span class="caption">Answer</span><span class="val">${esc(answerDisplay)}</span><span class="status-dot is-correct">${ICON.check(14)}</span></div>`;
    return { body: `${head}${resultRow(fb.ok)}${heard(card)}${explain(card)}<div class="spacer"></div><div class="answer-rows">${yours}${right}</div>`, action: actionNext };
  }
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', ':', '0', 'del'];
  const keypad = keys.map((k) => k === 'del'
    ? `<button class="key" data-key="del" aria-label="Delete">${ICON.backspace}</button>`
    : `<button class="key ${k === ':' ? 'colon' : ''}" data-key="${k}">${k}</button>`).join('');
  return {
    body: `${head}${audioControls(card, 'lg')}<div class="spacer"></div>`,
    action: `<div class="type-zone">
      <div class="typed" data-testid="typed" aria-live="polite">${show('')}<span class="caret"></span></div>
      <button class="btn-primary" data-act="check" disabled>Check</button>
      <div class="keypad">${keypad}</div>
    </div>`,
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
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === 'Enter') { e.preventDefault(); check.click(); }
        else if (e.key === 'Backspace') { e.preventDefault(); press('del'); }
        else if (/^[0-9:.$a-zA-Záéíóúñü ]$/.test(e.key)) { e.preventDefault(); press(e.key); }
      });
    },
  };
}

// ---------- scene_question ----------
function parseTranscript(t) {
  return String(t || '').split(/\s+\/\s+/).filter(Boolean).map((line) => {
    const m = /^([^:]{1,24}):\s*(.+)$/.exec(line);
    return m ? { speaker: m[1], es: m[2] } : { speaker: '', es: line };
  });
}
function transcriptBox(lines, revealed) {
  const rows = lines.map((l) => {
    if (revealed) return `<div class="tline"><span class="spk">${esc(l.speaker)}</span><p class="es">${esc(l.es)}</p></div>`;
    let remaining = l.es.length * 6.4; const bars = [];
    while (remaining > 0 && bars.length < 3) { const w = Math.min(remaining, 230); bars.push(Math.max(30, Math.round((w / 230) * 100))); remaining -= 230; }
    return `<div class="tline"><span class="spk">${esc(l.speaker)}</span><span class="bars-r">${bars.map((w) => `<i style="width:${w}%"></i>`).join('')}</span></div>`;
  }).join('');
  return `<div class="tbox ${revealed ? 'revealed' : ''}" data-testid="transcript">
    <div class="tbox-head"><span class="eyebrow">Transcript</span>${revealed ? '' : '<span class="caption">Shown after you answer</span>'}</div>${rows}</div>`;
}
function renderSceneQuestion(card, fb) {
  const scene = sceneOf(card);
  const head = `${eyebrow('Scene', scene?.title_en)}<h2 class="title prompt">${esc(card.prompt_en)}</h2>`;
  let lines = parseTranscript(card.transcript || card.audio_text);
  const dialogueMode = !card.audio_text; // no audio: read the scene dialogue instead
  if (dialogueMode && scene) lines = scene.dialogue.map((d) => ({ speaker: d.speaker, es: d.es }));
  if (!fb) {
    return {
      body: `${head}${audioControls(card, 'lg')}${transcriptBox(lines, dialogueMode)}<div class="spacer"></div>${optionsBlock(card, null, { grid: false })}`,
      action: actionDontKnow, bind: () => bindOptions(card),
    };
  }
  return {
    body: `${head}${resultRow(fb.ok)}${transcriptBox(lines, true)}${audioControls(card, 'sm')}${explain(card)}<div class="spacer"></div>${optionsBlock(card, fb, { grid: false })}`,
    action: actionNext,
  };
}

// ---------- fix_it ----------
function sentenceHtml(card, fill, state) {
  const parts = String(card.sentence).split(/_{2,}/);
  if (parts.length < 2) return `<p class="sentence">${esc(card.sentence)}</p>`;
  const blank = `<span class="blank ${state || ''}" data-testid="blank">${fill ? esc(fill) : '&nbsp;'}</span>`;
  return `<p class="sentence">${esc(parts[0])}${blank}${parts.slice(1).map(esc).join(blank)}</p>`;
}
function renderFixIt(card, fb) {
  const src = card.source === 'mistake' ? 'from your mistakes' : card.source === 'class_quizlet' ? 'from class' : card.source === 'scene' ? 'from a scene' : '';
  const head = eyebrow('Fix it', src);
  const chosen = hasOptions(card);
  if (fb) {
    const wrote = !fb.ok && !fb.skipped && fb.given ? `<p class="caption you-wrote">You wrote: <span style="color:var(--wrong)">${esc(fb.given)}</span></p>` : '';
    return { body: `${head}${sentenceHtml(card, card.answer, 'ok')}${resultRow(fb.ok)}${wrote}${explain(card)}<div class="spacer"></div>`, action: actionNext };
  }
  if (chosen) {
    return { body: `${head}${sentenceHtml(card, '')}<p class="body-copy instruction">${esc(card.prompt_en)}</p><div class="spacer"></div>${optionsBlock(card, null, { grid: false })}`, action: actionDontKnow, bind: () => bindOptions(card) };
  }
  return {
    body: `${head}${sentenceHtml(card, '')}<p class="body-copy instruction">${esc(card.prompt_en)}</p><div class="spacer"></div>
      <div class="fix-zone"><label class="eyebrow" for="fix-input">Your answer</label>
      <input id="fix-input" class="text-input" type="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="done" lang="es"></div>`,
    action: `<div class="action" style="padding-top:24px"><button class="btn-primary" data-act="check" disabled>Check</button></div>`,
    bind: () => {
      const inp = $('#fix-input'), check = $('[data-act="check"]'), blank = $('.blank');
      inp.oninput = () => { if (blank) blank.innerHTML = inp.value ? esc(inp.value) : '&nbsp;'; check.disabled = !inp.value.trim(); };
      inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); check.click(); } };
      inp.onfocus = () => setTimeout(() => inp.scrollIntoView({ block: 'nearest' }), 300);
      setTimeout(() => inp.focus({ preventScroll: true }), 50);
      check.onclick = () => { const v = inp.value.trim(); if (v) { inp.blur(); answer(card, isCorrect(card, v), v); } };
    },
  };
}

// ---------- reply ----------
function renderReply(card, fb) {
  const head = `${eyebrow('Your reply', sceneOf(card)?.title_en)}<h2 class="title prompt">${esc(card.prompt_en)}</h2>`;
  if (!fb) return { body: `${head}${audioControls(card, 'lg')}<div class="spacer"></div>${optionsBlock(card, null, { grid: false })}`, action: actionDontKnow, bind: () => bindOptions(card) };
  return { body: `${head}${resultRow(fb.ok)}${card.audio_text ? heard(card) : ''}${explain(card)}<div class="spacer"></div>${optionsBlock(card, fb, { grid: false })}`, action: actionNext };
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
    <div class="scroll">
      <p class="eyebrow">${esc(fmtDate())}<span class="sub">${mins} min</span></p>
      <h1 class="display" style="margin-top:12px">Session done</h1>
      <div class="dial">${dialSvg(ses.results)}<div class="center"><span class="score" data-testid="score">${right}<span class="dim">/${ses.results.length}</span></span><span class="caption">correct</span></div></div>
      <section class="stats small">
        <div class="col"><div class="value">${srs.streak(S.progress)}</div><p class="caption">day streak</p></div>
        <div class="divider"></div>
        <div class="col"><div class="value">${today} <span class="dim">/ ${srs.DAILY_GOAL}</span></div><p class="caption">cards today</p></div>
      </section>
      ${misses.length ? `<div class="review-head"><span class="eyebrow">Review again</span><span class="caption">back tomorrow</span></div>
        <div class="review-list">${misses.map(reviewRow).join('')}</div>`
        : `<div class="review-head"><span class="eyebrow">Review again</span></div><p class="body-copy empty-note">Nothing to review. ¡Muy bien!</p>`}
    </div>
    <div class="action"><button class="btn-primary" data-act="done">Done</button></div>
  </div>`);
  $$('[data-play]').forEach((b) => { b.onclick = () => play(b.dataset.text, { btn: b }); });
  $('[data-act="done"]').onclick = () => goHome(true);
}

boot();
