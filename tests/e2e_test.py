#!/usr/bin/env python3
"""End-to-end test for Oye: Playwright + headless Chromium.

Starts its own throwaway servers (so it can really cut the network), then checks:
manifest + installability, service worker, content + audio download, a full Quick
session covering every card type (with screenshots), offline launch, a content
update with an unknown card type, and the answer normaliser (all at 412x915).

v1.1 adds: one-screen layout checks for EVERY card at 360x720, 412x915 and 384x854
(no vertical scrolling before answering, or a visible "More below" pill; replay buttons
fully visible and not covered by the input / number pad; audio row not squashed),
Hint hidden until tapped, the What-you-heard / Why sheet, a simulated phone keyboard on
fix-it (layout-viewport shrink = interactive-widget=resizes-content, and a shrunken
visualViewport = fallback), screenshots in screenshots/v1.1/, and the service-worker
update path (installed v1 -> v1.1 without reinstalling; v1.1 -> newer defers mid-session).

Run:  .venv/bin/python tests/e2e_test.py            full suite
      .venv/bin/python tests/e2e_test.py --live URL  layout check against the deployed site (412x915)
"""
import asyncio, json, os, re, shutil, socket, struct, subprocess, sys, tarfile, io, time
from playwright.async_api import async_playwright

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(APP, 'screenshots')
TMP = os.path.join(APP, 'tests', '.tmp')
SHOTS11 = os.path.join(SHOTS, 'v1.1')
VIEWPORT = {'width': 412, 'height': 915}
LAYOUT_SIZES = [(360, 720), (412, 915), (384, 854)]
SHOT_SIZES = {(360, 720), (412, 915)}
# Cards that match the design previews go first so they are the screenshotted ones.
SHOWCASE = ['c-0010', 'c-0018', 'c-0023', 'c-0029', 'c-0034']
RESULTS = []
SHELL_VERSION = re.search(r"SHELL_VERSION = '([^']+)'", open(os.path.join(APP, 'js', 'app.js'), encoding='utf-8').read()).group(1)
VOICE_COLOR = {'male': 'rgb(110, 139, 255)', 'female': 'rgb(76, 195, 138)'}   # accent blue (Alonso), correct green (Paloma)


def all_audio_files(idx):
    """Every MP3 audio.json points at: card clips (items), dialogue lines (lines) and mission clips (missions)."""
    return sorted({e[k] for sec in ('items', 'lines', 'missions') for e in (idx.get(sec) or {}).values() for k in ('normal', 'slow') if e.get(k)})
# Results upload: the real Apps Script endpoint must NEVER be hit by the tests. Every browser context
# routes it (and Google's redirect host) to a fake; ENDPOINT_SEEN logs every request the browser made to
# those hosts, ENDPOINT_ROUTED the ones the fake answered, and a final check compares the two.
RESULTS_ENDPOINT = 'https://script.google.com/macros/s/AKfycbwD3ECxaglquY6aadvYPN9jxkrtElkiKCdRo7_gD7yY6uZAHZGlbvcY4XKBivJNZ4U98w/exec'
ENDPOINT_HOSTS = re.compile(r'^https://(script\.google\.com|script\.googleusercontent\.com)/')
ENDPOINT_SEEN, ENDPOINT_ROUTED = [], []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -- ' + str(detail)) if detail else ''}", flush=True)


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def start_server(root, port):
    env = {**os.environ, 'OYE_QUIET': '1'}
    proc = subprocess.Popen([sys.executable, os.path.join(APP, 'tools', 'serve.py'), str(port), root],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for _ in range(50):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.2).close(); return proc
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('server did not start')


def png_size(path):
    with open(path, 'rb') as f:
        d = f.read(24)
    return struct.unpack('>II', d[16:24])


FAKE_MISSION_ID = re.compile(r'^m-[a-z0-9]+(?:-[a-z0-9]+){0,8}$')


def fake_accepts(r):
    """Same id rule as the v3 Apps Script (oye-notes/oye-results-script.gs): c-NNNN cards, or mission rows with
    mission_id m-... and card_id qN / <mission_id>:qN."""
    cid, mid = str(r.get('card_id') or '').strip(), str(r.get('mission_id') or '').strip()
    if re.match(r'^c-\d{4}$', cid): return True
    if not FAKE_MISSION_ID.match(mid): return False
    return bool(re.match(r'^q\d{1,2}$', cid)) or (cid.startswith(mid + ':') and bool(re.match(r'^q\d{1,2}$', cid[len(mid) + 1:])))


class FakeEndpoint:
    """Stands in for the Apps Script web app. mode: 'ok' | 'http500' | 'okfalse' | 'abort'."""
    def __init__(self, mode='ok', delay=0):
        self.mode, self.delay, self.calls = mode, delay, []   # calls: dicts {method, ctype, body (parsed JSON or raw), mode}

    async def handle(self, route):
        req = route.request
        ENDPOINT_ROUTED.append(req.url)
        raw = req.post_data or ''
        try: body = json.loads(raw)
        except Exception: body = raw
        self.calls.append({'method': req.method, 'ctype': req.headers.get('content-type', ''), 'body': body, 'mode': self.mode, 'url': req.url})
        cors = {'Access-Control-Allow-Origin': '*'}
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.mode == 'abort':
            return await route.abort('internetdisconnected')
        if self.mode == 'http500':
            return await route.fulfill(status=500, headers=cors, content_type='text/html', body='<html>Error</html>')
        if self.mode == 'okfalse':
            return await route.fulfill(status=200, headers=cors, content_type='application/json', body='{"ok":false}')
        n = len([r for r in (body.get('results') or []) if fake_accepts(r)][:200]) if isinstance(body, dict) else 0
        return await route.fulfill(status=200, headers=cors, content_type='application/json', body=json.dumps({'ok': True, 'saved': n}))

    def ok_rows(self):
        """Rows the fake 'saved' (only from requests it answered with ok: true)."""
        return [r for c in self.calls if c['mode'] == 'ok' and isinstance(c['body'], dict) for r in c['body'].get('results', [])]


async def new_page(browser, errors, viewport=None, init_script=None, endpoint=None):
    ctx = await browser.new_context(viewport=viewport or VIEWPORT, device_scale_factor=1, is_mobile=True, has_touch=True,
                                    locale='en-GB', timezone_id='America/Mexico_City')
    fake = endpoint or FakeEndpoint('ok')
    ctx.fake_endpoint = fake
    await ctx.route(ENDPOINT_HOSTS, fake.handle)
    ctx.on('request', lambda r: ENDPOINT_SEEN.append(r.url) if ENDPOINT_HOSTS.match(r.url) else None)
    if init_script:
        await ctx.add_init_script(init_script)
    page = await ctx.new_page()
    page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
    page.on('console', lambda m: errors.append(f'console.{m.type}: {m.text}') if m.type == 'error' else None)
    return ctx, page


async def wait_audio_idle(page, timeout=6000):
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout:
        if await page.evaluate('(() => { const a = window.__oyeAudioEl; return !a || a.paused || a.ended; })()'):
            break
        await page.wait_for_timeout(150)
    await page.wait_for_timeout(250)


async def wait_audio_synced(page, expected, timeout=30000):
    t0 = time.time()
    n = 0
    while (time.time() - t0) * 1000 < timeout:
        n = await page.evaluate("""caches.open('oye-content').then(c => c.keys()).then(k => k.filter(r => r.url.includes('/audio/')).length)""")
        if n >= expected:
            return n
        await page.wait_for_timeout(250)
    return n


async def current(page):
    el = await page.query_selector('#app > .screen')
    return {k: await el.get_attribute(f'data-{k}') for k in ('screen', 'type', 'card', 'state')}


async def main():
    os.makedirs(SHOTS, exist_ok=True)
    cards = json.load(open(os.path.join(APP, 'content', 'cards.json'), encoding='utf-8'))
    by_id = {c['id']: c for c in cards}
    audio_idx = json.load(open(os.path.join(APP, 'content', 'audio.json'), encoding='utf-8'))
    version = json.load(open(os.path.join(APP, 'content', 'version.json'), encoding='utf-8'))
    audio_files = all_audio_files(audio_idx)

    # ---------- static checks ----------
    man = json.load(open(os.path.join(APP, 'manifest.webmanifest')))
    check('manifest: name/short_name = Oye', man.get('name') == 'Oye' and man.get('short_name') == 'Oye')
    check('manifest: display standalone, colours #0E0E10',
          man.get('display') == 'standalone' and man.get('background_color') == '#0E0E10' and man.get('theme_color') == '#0E0E10')
    check('manifest: start_url/scope relative (subpath safe)', man.get('start_url') == './' and man.get('scope') == './')
    purposes = {(i['src'], i['purpose']) for i in man['icons']}
    want = {('icons/icon-192.png', 'any'), ('icons/icon-512.png', 'any'),
            ('icons/icon-192-maskable.png', 'maskable'), ('icons/icon-512-maskable.png', 'maskable')}
    check('manifest: 4 icons with correct purposes', purposes == want, sorted(purposes))
    sizes_ok = all(png_size(os.path.join(APP, i['src'])) == tuple(map(int, i['sizes'].split('x'))) for i in man['icons'])
    check('manifest: icon files exist and match declared sizes', sizes_ok)
    import re
    pat = re.compile(r"""(src|href)="/|fetch\(['"`]/|register\(['"]/|url\(["']?/|(?<!includes\()['"]/(content|audio|js|icons|fonts)/""")
    files = ['index.html', 'manifest.webmanifest', 'sw.js', 'styles.css'] + [f'js/{x}' for x in os.listdir(os.path.join(APP, 'js'))]
    abs_refs = [f'{f}: {l.strip()}' for f in files for l in open(os.path.join(APP, f), encoding='utf-8') if pat.search(l)]
    check('no root-absolute paths in app files (works under a subpath)', not abs_refs, abs_refs[:3])
    audio_texts = {c['audio_text'] for c in cards if c.get('audio_text')}
    check('audio.json covers every card audio_text', audio_texts <= set(audio_idx['items']), f'{len(audio_texts)} sentences')
    NEW_LINE = f"{version.get('new_cards')} new card{'' if version.get('new_cards') == 1 else 's'} this week"
    check(f"version.json: version {version.get('version')}, new_cards {version.get('new_cards')} (= new_card_ids), card_count {len(cards)}",
          version.get('version', 0) >= 1 and version.get('new_cards') == len(version.get('new_card_ids', [])) and version.get('card_count') == len(cards))
    need_hint = [c['id'] for c in cards if c['type'] in ('fix_it', 'listen_type')]
    check('content: every fix_it / listen_type card has hint_en', all(by_id[i].get('hint_en') for i in need_hint),
          f"{sum(1 for c in cards if c.get('hint_en'))} cards with hint_en")
    sw_src = open(os.path.join(APP, 'sw.js'), encoding='utf-8').read()
    m_cv = re.search(r"const CACHE_VERSION = '([^']+)'", sw_src)
    check('sw.js CACHE_VERSION matches the content version', m_cv and m_cv.group(1).startswith(f"oye-v{version.get('version')}-"), m_cv and m_cv.group(1))
    check('viewport meta has interactive-widget=resizes-content (keyboard resizes the layout)',
          'interactive-widget=resizes-content' in open(os.path.join(APP, 'index.html'), encoding='utf-8').read())
    static_v2_checks(cards, audio_idx, version, sw_src)

    port = free_port()
    server = start_server(APP, port)
    base = f'http://localhost:{port}/'
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx, page = await new_page(browser, errors)

        # ---------- first load ----------
        await page.goto(base)
        await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
        quiet = (await page.text_content('#quiet')) or ''
        n_synced = await wait_audio_synced(page, len(audio_files))
        check('content + all audio stored for offline (Cache Storage)', n_synced == len(audio_files), f'{n_synced}/{len(audio_files)} audio files')
        await page.wait_for_timeout(300)
        quiet = (await page.text_content('#quiet')) or ''
        check(f'home shows "{NEW_LINE}"', quiet.strip() == NEW_LINE, quiet)

        scope = await page.evaluate('navigator.serviceWorker.ready.then(r => r.scope)')
        check('service worker registers (scope = app folder)', scope == base, scope)
        await page.reload()
        await page.wait_for_selector('html[data-ready="1"] [data-screen=home]')
        controlled = await page.evaluate('!!navigator.serviceWorker.controller')
        check('page is controlled by the service worker after reload', controlled)

        cdp = await ctx.new_cdp_session(page)
        m = await cdp.send('Page.getAppManifest')
        check('Chrome parses the manifest with no errors', m.get('url', '').endswith('manifest.webmanifest') and not m.get('errors'), m.get('errors'))
        try:
            inst = await cdp.send('Page.getInstallabilityErrors')
            check('Chrome reports no installability errors', not inst.get('installabilityErrors'), inst.get('installabilityErrors'))
        except Exception as e:
            check('Chrome installability check ran', False, e)

        # audio over HTTP
        bad = []
        for f in audio_files:
            r = await page.request.get(base + f)
            body = await r.body()
            if r.status != 200 or len(body) == 0 or 'audio/mpeg' not in r.headers.get('content-type', ''):
                bad.append((f, r.status, len(body)))
        check(f'all {len(audio_files)} audio files load (HTTP 200, non-zero, audio/mpeg)', not bad, bad[:3])

        # normaliser in the browser
        res = await page.evaluate("""import('./js/check.js').then(m => {
            const c = {answer: '$69', accepted_answers: ['69', '$69', 'sesenta y nueve']};
            const t = {answer: '10:30', accepted_answers: ['10:30', '10.30', 'diez y media']};
            return [m.isCorrect(c, '$69'), m.isCorrect(c, '69'), m.isCorrect(c, 'Sesenta y nueve'), m.isCorrect(c, 'sesenta y nueve pesos'),
                    !m.isCorrect(c, '79'), m.isCorrect(t, '10:30'), m.isCorrect(t, 'Diez y media'),
                    m.isCorrect({answer: 'estuve', accepted_answers: ['estuve']}, 'Estuvé.')];
        })""")
        check('answer check ignores case/accents/punctuation; $69, 69, "sesenta y nueve" accepted', all(res), res)

        # ---------- home screenshot ----------
        await page.screenshot(path=os.path.join(SHOTS, '01-home.png'))

        # ---------- full Quick session ----------
        await page.click('[data-act=start]')
        seen_types, first_q, first_fb = [], set(), set()
        quick_levels = []
        hint_used_ids = set()
        counts = {}
        autoplayed, listening, silent_bad = 0, 0, []
        slow_checked = None
        plan_len = None
        for step in range(12):
            await page.wait_for_timeout(200)
            cur = await current(page)
            if cur['screen'] != 'card':
                break
            text = await page.text_content('.count')
            plan_len = int(text.split(' of ')[1])
            card = by_id[cur['card']]
            t = card['type']
            seen_types.append(t)
            quick_levels.append(card.get('level', 'easy'))
            counts[t] = counts.get(t, 0) + 1
            k = counts[t]
            # no autoplay: the card opens silent ("Tap to listen"); audio only plays after tapping the play button
            if card.get('audio_text'):
                listening += 1
                await page.wait_for_timeout(900)
                st0 = await page.evaluate("(() => { const a = window.__oyeAudioEl; const c = document.querySelector('[data-testid=listen-cap]'); return {played: !a.paused && !a.ended, cap: c && c.textContent}; })()")
                autoplayed += 1 if st0['played'] else 0
                if st0['cap'] != 'Tap to listen': silent_bad.append((card['id'], st0['cap']))
                await page.click('[data-testid=replay]')
                await page.wait_for_timeout(300)
                if not await page.evaluate('(() => { const a = window.__oyeAudioEl; return !a.paused; })()'):
                    silent_bad.append((card['id'], 'tap did not play'))
                await wait_audio_idle(page)
            # Outcome plan: listen_pick #1 wrong, #2 correct; scene #2, fix_it #2 wrong; everything else correct.
            correct = not ((t == 'listen_pick' and k == 1) or (t in ('scene_question', 'fix_it') and k == 2))
            if t == 'listen_pick' and k == 1 and slow_checked is None:
                normal_dur = await page.evaluate('window.__oyeAudioEl.duration')
                await page.click('[data-play=slow]')
                await page.wait_for_timeout(700)
                slow = await page.evaluate('(() => { const a = window.__oyeAudioEl; return {t: a.currentTime, rate: a.playbackRate, d: a.duration}; })()')
                slow_checked = slow['t'] > 0 and slow['d'] > normal_dur
                check('slow replay plays the slower pre-generated file', slow_checked, f"normal {normal_dur:.2f}s, slow {slow['d']:.2f}s")
                await wait_audio_idle(page)

            if t in ('listen_type', 'fix_it') and card.get('hint_en'):
                box_hidden = not await page.is_visible('[data-testid=hint-box]')
                await page.click('[data-act=hint]')
                shown = await page.is_visible('[data-testid=hint-box]') and card['hint_en'] in (await page.text_content('[data-testid=hint-box]'))
                check(f'{card["id"]} hint hidden until tapped, then shows hint_en', box_hidden and shown)
                hint_used_ids.add(card['id'])
            shot_q = None if t in first_q else os.path.join(SHOTS, {
                'listen_pick': '02-listen-pick.png', 'listen_type': '03-listen-type.png', 'scene_question': '04a-scene-question.png',
                'fix_it': '04b-fix-it.png', 'reply': '04c-reply.png'}[t])
            first_q.add(t)

            if t in ('listen_pick', 'scene_question', 'reply') or (t == 'fix_it' and card.get('options')):
                opts = card['options']
                idx = opts.index(card['answer']) if correct else next(i for i, o in enumerate(opts) if o != card['answer'])
                if shot_q: await page.screenshot(path=shot_q)
                await page.click(f'[data-opt="{idx}"]')
            elif t == 'listen_type':
                if k == 1:   # on-screen keypad, digits of the answer
                    digits = card['answer'].replace('$', '') if correct else '1'
                    for ch in digits:
                        await page.click(f'[data-key="{ch}"]')
                else:        # hardware keyboard with a spoken-word or $ variant
                    words = [a for a in card['accepted_answers'] if any(c.isalpha() for c in a)]
                    variant = (words or [a for a in card['accepted_answers'] if a.startswith('$')] or [card['answer']])[0]
                    await page.keyboard.type(variant.upper() if words else variant)
                if shot_q: await page.screenshot(path=shot_q)
                await page.click('[data-act=check]')
            elif t == 'fix_it':
                await page.fill('#fix-input', card['answer'] if correct else 'xyz')
                await page.wait_for_timeout(100)
                if shot_q: await page.screenshot(path=shot_q)
                await page.click('[data-act=check]')
            await page.wait_for_selector('[data-state=correct], [data-state=wrong]')
            state = (await current(page))['state']
            check(f'{card["id"]} ({t}) answered {"right" if correct else "wrong"} -> {state} feedback', state == ('correct' if correct else 'wrong'))
            fb_text = await page.text_content('.card-body')
            fb_text += await sheet_text(page)
            if card.get('explanation_en'):
                check(f'{card["id"]} feedback shows explanation_en', card['explanation_en'] in fb_text)
            if card.get('region_note'):
                check(f'{card["id"]} feedback shows region_note', card['region_note'] in fb_text)
            if card.get('transcript') and t in ('listen_pick', 'listen_type', 'reply'):
                check(f'{card["id"]} feedback shows transcript', card['transcript'] in fb_text)
            if t == 'scene_question':
                lines = [l.split(': ', 1)[-1] for l in card['transcript'].split(' / ')]
                check(f'{card["id"]} transcript revealed after answering', all(l in fb_text for l in lines))
            if t == 'listen_pick' and k == 1:
                await page.screenshot(path=os.path.join(SHOTS, '02c-feedback-wrong.png'))
            elif t == 'listen_pick' and k == 2:
                await page.screenshot(path=os.path.join(SHOTS, '02b-feedback-correct.png'))
            elif t not in first_fb:
                name = {'listen_type': '03b-listen-type-feedback', 'scene_question': '04a2-scene-feedback',
                        'fix_it': '04b2-fix-it-feedback', 'reply': '04c2-reply-feedback'}.get(t)
                if name:
                    await page.screenshot(path=os.path.join(SHOTS, f'{name}-{state}.png'))
            first_fb.add(t)
            await page.click('[data-act=next]')

        cur = await current(page)
        check('session reaches the summary screen', cur['screen'] == 'summary')
        check('session has 8-10 cards', plan_len is not None and 8 <= plan_len <= 10, plan_len)
        check('session covered every card type', set(seen_types) == set(TYPES), seen_types)
        check('v2: the Quick session (Easy) showed only Easy cards (no Medium card)', quick_levels and all(l == 'easy' for l in quick_levels), quick_levels)
        check('no autoplay: every listening card opens silent with "Tap to listen"; tapping the play button plays it',
              autoplayed == 0 and listening > 0 and not silent_bad, {'autoplayed': f'{autoplayed}/{listening}', 'bad': silent_bad[:3]})
        score = (await page.text_content('[data-testid=score]')).strip()
        expect_right = sum(1 for i, t in enumerate(seen_types) if not ((t == 'listen_pick' and seen_types[:i + 1].count(t) == 1) or (t in ('scene_question', 'fix_it') and seen_types[:i + 1].count(t) == 2)))
        check('summary score matches answers', score == f'{expect_right}/{len(seen_types)}', score)
        rows = await page.query_selector_all('.review-row')
        check('summary lists the missed cards under Review again', len(rows) == len(seen_types) - expect_right, len(rows))
        await page.screenshot(path=os.path.join(SHOTS, '05-summary.png'))
        await page.click('[data-act=done]')
        await page.wait_for_selector('[data-screen=home]')
        streak = (await page.text_content('[data-testid=streak]')).strip()
        check('home streak = 1 after the first session', streak == '1', streak)
        # v2 design: the second home stat is "stars earned" (was "cards today"); cards answered today still live in progress.
        stars_home = (await page.text_content('[data-testid=stars-total]')).strip()
        today_n = await page.evaluate("(() => { const p = JSON.parse(localStorage.getItem('oye.progress.v1')); const d = new Date(); const k = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); return p.days[k].answered; })()")
        check(f'home: cards answered today = {len(seen_types)} (progress) and "stars earned" counts the session (1-3)',
              today_n == len(seen_types) and stars_home.isdigit() and 1 <= int(stars_home) <= 3, {'today': today_n, 'stars': stars_home})
        await page.screenshot(path=os.path.join(SHOTS, '01b-home-after-session.png'))
        prog = await page.evaluate("JSON.parse(localStorage.getItem('oye.progress.v1'))")
        wrong_ids = [cid for cid, s in prog['cards'].items() if s['lastOk'] is False]
        check('spaced repetition state saved in localStorage (wrong cards back to box 1)',
              len(prog['cards']) == len(seen_types) and all(prog['cards'][c]['box'] == 1 for c in wrong_ids))
        hinted = {h['id'] for h in prog['history'] if h.get('h')}
        check('hint use recorded in the answer history (optional field, old entries untouched)', hinted == hint_used_ids, sorted(hinted))
        # next session plan favours missed cards / introduces few new ones
        plan2 = await page.evaluate('window.__oye.plan.map(c => c.id)')
        check('second plan introduces new cards gradually (<=10) and is 8-10 long', 8 <= len(plan2) <= 10, plan2)

        # ---------- version unchanged -> stored copy ----------
        fresh_reqs = []
        page.on('request', lambda r: fresh_reqs.append(r.url) if 'fresh=' in r.url else None)
        await page.reload()
        await page.wait_for_selector('html[data-ready="1"] [data-screen=home]')
        check('same version: content not re-downloaded', not fresh_reqs, fresh_reqs[:2])

        # ---------- offline ----------
        server.terminate(); server.wait()
        await ctx.set_offline(True)
        await page.reload()
        try:
            await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=10000)
            ok = True
        except Exception:
            ok = False
        check('app loads with the network offline (server stopped too)', ok)
        if ok:
            await page.screenshot(path=os.path.join(SHOTS, '06-offline-home.png'))
            q = (await page.text_content('#quiet')).strip()
            check('offline home still shows the new-cards line (stored version.json)', q == NEW_LINE, q)
            await page.click('[data-act=start]')
            await page.wait_for_selector('[data-screen=card]')
            cur = await current(page)
            c = by_id[cur['card']]
            if c.get('audio_text'):
                await page.click('[data-testid=replay]')
                await page.wait_for_timeout(1200)
                played = await page.evaluate('(() => { const a = window.__oyeAudioEl; return a.currentTime > 0 || !a.paused; })()')
                check('offline: card audio plays from the cache (after tapping play)', played)
            r = await page.evaluate(f"fetch('{audio_files[0]}').then(r => r.status + ':' + r.headers.get('content-length')).catch(e => 'ERR ' + e)")
            check('offline: audio file served by the service worker', r.startswith('200'), r)
        await ctx.close()

        # ---------- hosted under a subpath (like GitHub Pages /repo-name/) ----------
        port3 = free_port()
        server3 = start_server(os.path.dirname(APP), port3)   # serves the parent, so the app lives at /app/
        errors3 = []
        ctx3, page3 = await new_page(browser, errors3)
        sub = f'http://localhost:{port3}/{os.path.basename(APP)}/'
        await page3.goto(sub)
        try:
            await page3.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=10000)
            sub_scope = await page3.evaluate('navigator.serviceWorker.ready.then(r => r.scope)')
            cdp3 = await ctx3.new_cdp_session(page3)
            m3 = await cdp3.send('Page.getAppManifest')
            ok3 = sub_scope == sub and not m3.get('errors')
        except Exception as e:
            ok3, sub_scope = False, str(e)
        check('works under a subpath (/app/): loads, SW scope and manifest OK', ok3 and not errors3, sub_scope)
        await ctx3.close(); server3.terminate(); server3.wait()

        # ---------- content update with an unknown card type (temp copy of the site) ----------
        shutil.rmtree(TMP, ignore_errors=True)
        site = os.path.join(TMP, 'site')
        shutil.copytree(APP, site, ignore=shutil.ignore_patterns('.venv', 'screenshots', 'tests', 'tools', '__pycache__'))
        port2 = free_port()
        server2 = start_server(site, port2)
        errors2 = []
        ctx2, page2 = await new_page(browser, errors2)
        base2 = f'http://localhost:{port2}/'
        await page2.goto(base2)
        await page2.wait_for_selector('html[data-ready="1"] [data-screen=home]')
        await wait_audio_synced(page2, len(audio_files))
        c2 = json.load(open(os.path.join(site, 'content', 'cards.json'), encoding='utf-8'))
        known_text = c2[3]['audio_text']
        c2 += [
            {'id': 'c-9001', 'type': 'speak_aloud', 'prompt_en': 'Say it', 'audio_text': None, 'options': [], 'answer': None, 'accepted_answers': []},
            dict(c2[3], id='c-9002', prompt_en='How much is it? (new)'),
            {k: v for k, v in next(c for c in c2 if c['type'] == 'listen_type').items() if k != 'hint_en'} | {'id': 'c-9004'},
            {'id': 'c-9003', 'type': 'listen_pick', 'audio_text': known_text, 'options': [], 'answer': '$1'},
        ]
        json.dump(c2, open(os.path.join(site, 'content', 'cards.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        v2 = dict(version, version=version['version'] + 1, new_cards=3, new_card_ids=['c-9001', 'c-9002', 'c-9003'])
        EXTRA = 4
        json.dump(v2, open(os.path.join(site, 'content', 'version.json'), 'w'))
        await page2.reload()
        await page2.wait_for_selector('html[data-ready="1"] [data-screen=home]')
        await page2.wait_for_timeout(500)
        info = await page2.evaluate('({n: window.__oye.content.cards.length, playable: window.__oye.playable.map(c => c.id), v: window.__oye.content.version.version, updated: window.__oye.content.updated})')
        q2 = (await page2.text_content('#quiet')).strip()
        check('new version.json -> app downloads the new content', info['v'] == version['version'] + 1 and info['updated'] and info['n'] == len(cards) + EXTRA, info['n'])
        check('unknown type (speak_aloud) and a broken card are skipped silently',
              'c-9001' not in info['playable'] and 'c-9003' not in info['playable'] and 'c-9002' in info['playable'] and 'c-9004' in info['playable'] and len(info['playable']) == len(cards) + 2)
        check('home new-cards line follows version.json', q2 == '3 new cards this week', q2)
        # a card without the hint_en field (old format) renders with no Hint tap
        await page2.evaluate("(() => { const S = window.__oye; S.plan = [S.playable.find(c => c.id === 'c-9004')]; })()")
        await page2.click('[data-act=start]')
        await page2.wait_for_selector('[data-card=c-9004]')
        check('card without hint_en (old format): renders, no Hint tap', not await page2.query_selector('[data-act=hint]') and await page2.is_visible('[data-testid=replay]'))
        await page2.click('[data-act=close]')
        await page2.wait_for_selector('[data-screen=home]')
        # play through a session on the updated content to be sure nothing breaks
        await page2.click('[data-act=start]')
        for _ in range(12):
            await page2.wait_for_timeout(150)
            cur = await current(page2)
            if cur['screen'] != 'card': break
            dk = await page2.query_selector('[data-act=dontknow]')
            if dk: await dk.click()
            elif await page2.query_selector('#fix-input'):
                await page2.fill('#fix-input', 'x'); await page2.click('[data-act=check]')
            else:
                await page2.click('[data-key="1"]'); await page2.click('[data-act=check]')
            await page2.click('[data-act=next]')
        check('session on updated content completes', (await current(page2))['screen'] == 'summary')
        check('no JS errors on updated content', not errors2, errors2[:3])
        await ctx2.close()
        server2.terminate(); server2.wait()
        shutil.rmtree(TMP, ignore_errors=True)

        # ---------- v1.1: one-screen layout at three sizes (fresh server so it's independent of the offline test) ----------
        port4 = free_port()
        server4 = start_server(APP, port4)
        base4 = f'http://localhost:{port4}/'
        shutil.rmtree(SHOTS11, ignore_errors=True)
        for (w, h) in LAYOUT_SIZES:
            await layout_suite(browser, base4, cards, w, h)
        await keyboard_visual_viewport_test(browser, base4)
        await results_upload_tests(browser, base4, version)
        await levels_suite(browser, base4, cards)
        await ux_replays_suite(browser, base4, cards)
        os.makedirs(TMP, exist_ok=True)
        await drill_read_suite(browser, base4, cards)
        server4.terminate(); server4.wait()

        # ---------- v1.1: service worker update path ----------
        os.makedirs(TMP, exist_ok=True)
        await update_path_test(browser)
        shutil.rmtree(TMP, ignore_errors=True)
        await browser.close()

    endpoint_guard_check()
    check('no JS errors or console errors during the main run',
          not [e for e in errors if 'ERR_INTERNET_DISCONNECTED' not in e and 'Failed to fetch' not in e and 'net::' not in e], errors[:5])
    if server.poll() is None:
        server.terminate()
    passed = sum(1 for r in RESULTS if r[1])
    print(f'\n{passed}/{len(RESULTS)} checks passed')
    json.dump([{'check': n, 'ok': o, 'detail': str(d)} for n, o, d in RESULTS],
              open(os.path.join(APP, 'tests', 'last-run.json'), 'w'), indent=1)
    return 0 if passed == len(RESULTS) else 1


# ---------------------------------------------------------------- v1.1 helpers
async def sheet_text(page):
    """Open the What-you-heard / Why sheet (if the card has one), return its text, close it."""
    row = await page.query_selector('[data-sheet]')
    if not row:
        return ''
    await row.click()
    await page.wait_for_selector('[data-testid=sheet]')
    txt = await page.text_content('[data-testid=sheet]')
    await page.click('[data-act=sheet-close]')
    await page.wait_for_selector('[data-testid=sheet]', state='detached')
    return txt


GEOM_JS = """() => {
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, h: b.height, top: b.top, bottom: b.bottom, left: b.left, right: b.right}; };
  const vis = (el) => !!el && getComputedStyle(el).display !== 'none' && !el.hidden && el.getClientRects().length > 0;
  const zone = document.querySelector('#app .card-zone, #app .summary-zone');
  const sc = zone && zone.querySelector('.scroller');
  const pill = zone && zone.querySelector('.more-pill');
  const dock = document.querySelector('#app .dock, #app .summary .action');
  const covered = (el) => {  // is the centre of el (or any corner inset 3px) hit by something that isn't el?
    const b = el.getBoundingClientRect();
    const pts = [[b.left + b.width / 2, b.top + b.height / 2], [b.left + 3, b.top + b.height / 2], [b.right - 3, b.top + b.height / 2], [b.left + b.width / 2, b.top + 3], [b.left + b.width / 2, b.bottom - 3]];
    return pts.some(([x, y]) => { const hit = document.elementFromPoint(x, y); return !hit || !(hit === el || el.contains(hit)); });
  };
  const btns = [...document.querySelectorAll('#app .card-zone [data-play]')].filter(vis).map((b) => ({
    kind: b.dataset.play, cls: b.className, box: r(b), covered: covered(b) }));
  return {
    vw: innerWidth, vh: innerHeight,
    docScroll: document.scrollingElement.scrollHeight - innerHeight,
    overflow: sc ? sc.scrollHeight - sc.clientHeight : 0,
    scrollTop: sc ? sc.scrollTop : 0,
    pill: vis(pill), zone: r(zone), dock: r(dock), topbar: r(document.querySelector('#app .topbar')),
    audioRow: r(document.querySelector('#app .card-zone [data-testid=audio-row]')),
    btns,
    input: r(document.querySelector('#app .typed, #app #fix-input')),
    keypad: r(document.querySelector('#app .keypad')),
    check: r(document.querySelector('#app [data-act=check]')),
    hintPill: vis(document.querySelector('#app [data-act=hint]')) ? r(document.querySelector('#app [data-act=hint]')) : null,
    hintBox: vis(document.querySelector('#app [data-testid=hint-box]')) ? r(document.querySelector('#app [data-testid=hint-box]')) : null,
    sentence: r(document.querySelector('#app [data-testid=sentence]')),
    instruction: r(document.querySelector('#app [data-testid=instruction]')),
    fixRow: r(document.querySelector('#app .fix-row')),
    next: r(document.querySelector('#app [data-act=next], #app [data-act=done]')),
  };
}"""
DESIGN_SIZES = {'r112': 112, 'r56': 56, 'r48': 48, 's64': 64, 's48': 48}


def fully_inside(b, top, bottom, left=0, right=10 ** 6, tol=0.5):
    return b and b['top'] >= top - tol and b['bottom'] <= bottom + tol and b['left'] >= left - tol and b['right'] <= right + tol


def layout_problems(g, label, allow_overflow_with_pill=True):
    """Return a list of problems with the current card screen."""
    p = []
    if g['docScroll'] > 1:
        p.append(f'page itself scrolls by {g["docScroll"]}px')
    if g['overflow'] > 1 and not (allow_overflow_with_pill and g['pill']):
        p.append(f'middle zone overflows by {g["overflow"]}px without a "More below" pill')
    if g['overflow'] <= 1 and g['pill']:
        p.append('"More below" pill shown but nothing to scroll')
    if g['dock'] and g['dock']['bottom'] > g['vh'] + 0.5:
        p.append(f'dock runs off screen ({g["dock"]["bottom"]} > {g["vh"]})')
    return p


def replay_problems(g):
    p = []
    top = g['topbar']['bottom'] if g['topbar'] else 0
    dock_top = g['dock']['top'] if g['dock'] else g['vh']
    zone_bottom = g['zone']['bottom'] if g['zone'] else g['vh']
    main = [b for b in g['btns'] if 'line-play' not in b['cls']]
    if not main:
        p.append('no replay buttons found')
    for b in main:
        box = b['box']
        size = next((v for k, v in DESIGN_SIZES.items() if k in b['cls'].split()), None)
        if size and (abs(box['h'] - size) > 0.5 or abs(box['w'] - size) > 0.5):
            p.append(f'{b["kind"]} button squashed: {box["w"]:.0f}x{box["h"]:.0f}, designed {size}')
        if not fully_inside(box, top, min(dock_top, zone_bottom), 0, g['vw']):
            p.append(f'{b["kind"]} button not fully visible: top {box["top"]:.0f} bottom {box["bottom"]:.0f}, zone/dock top {min(dock_top, zone_bottom):.0f}')
        for name in ('input', 'keypad', 'check'):
            o = g[name]
            if o and not (box['bottom'] <= o['top'] + 0.5 or box['top'] >= o['bottom'] - 0.5 or box['right'] <= o['left'] or box['left'] >= o['right']):
                p.append(f'{b["kind"]} button overlaps the {name}')
        if b['covered']:
            p.append(f'{b["kind"]} button is covered by another element')
    ar = g['audioRow']
    if ar and ar['h'] < 47.5:
        p.append(f'audio row squashed to {ar["h"]:.0f}px')
    return p


def shot_path(w, h, name):
    d = os.path.join(SHOTS11, f'{w}x{h}')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


async def layout_suite(browser, base, cards, w, h):
    """Every playable card at one screen size: question, hint, feedback, sheet, fix-it keyboard."""
    errors = []
    ctx, page = await new_page(browser, errors, viewport={'width': w, 'height': h})
    await page.goto(base)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
    tag = f'[{w}x{h}]'
    g = await page.evaluate(GEOM_JS)
    check(f'{tag} home fits without scrolling', g['docScroll'] <= 1, g['docScroll'])
    shots = (w, h) in SHOT_SIZES
    if shots:
        await page.screenshot(path=shot_path(w, h, '00-home.png'))
    ids = await page.evaluate('window.__oye.playable.map(c => c.id)')
    order = [i for i in SHOWCASE if i in ids] + [i for i in ids if i not in SHOWCASE]
    await page.evaluate(f"(() => {{ const S = window.__oye; S.plan = {json.dumps(order)}.map(id => S.playable.find(c => c.id === id)); }})()")
    await page.click('[data-act=start]')
    by_id = {c['id']: c for c in cards}
    scenes = {s['id']: s for s in json.load(open(os.path.join(APP, 'content', 'scenes.json'), encoding='utf-8'))}
    q_bad, fb_bad, rp_bad, hint_bad, sheet_bad, q_overflow, fb_overflow, kb_bad = [], [], [], [], [], [], [], []
    shot_types = set()
    n_hint = 0
    num = {'listen_pick': '01', 'listen_type': '02', 'scene_question': '03', 'fix_it': '04', 'reply': '05'}
    for idx, cid in enumerate(order):
        await page.wait_for_selector(f'[data-screen=card][data-card="{cid}"][data-state=question]')
        await page.wait_for_timeout(120)
        card = by_id[cid]
        t = card['type']
        first = shots and t not in shot_types
        shot_types.add(t)
        pre = f'{num[t]}-{t.replace("_", "-")}'
        g = await page.evaluate(GEOM_JS)
        probs = layout_problems(g, cid)
        if g['overflow'] > 1:
            q_overflow.append(f'{cid}:{g["overflow"]}px' + ('+pill' if g['pill'] else ''))
        if probs: q_bad.append(f'{cid}: {"; ".join(probs)}')
        if card.get('audio_text'):
            rp = replay_problems(g)
            if rp: rp_bad.append(f'{cid}: {"; ".join(rp)}')
        if first:
            await page.screenshot(path=shot_path(w, h, f'{pre}-a-question.png'))
        # ---- hint ----
        pill = await page.query_selector('[data-act=hint]')
        wants_hint = t in ('listen_type', 'fix_it') and bool(card.get('hint_en'))
        if wants_hint != bool(pill):
            hint_bad.append(f'{cid}: Hint tap {"missing" if wants_hint else "shown without hint_en"}')
        if pill:
            n_hint += 1
            if await page.is_visible('[data-testid=hint-box]'):
                hint_bad.append(f'{cid}: hint visible before tapping')
            elif card['hint_en'] in (await page.inner_text('[data-testid=card-body]')):
                hint_bad.append(f'{cid}: hint text readable before tapping')
            await pill.click()
            await page.wait_for_timeout(80)
            box_txt = (await page.text_content('[data-testid=hint-box]')) if await page.is_visible('[data-testid=hint-box]') else ''
            if card['hint_en'] not in box_txt.replace('\u200b', ''):
                hint_bad.append(f'{cid}: hint_en not shown after tapping')
            g2 = await page.evaluate(GEOM_JS)
            hp = layout_problems(g2, cid, allow_overflow_with_pill=False)
            if g2['hintBox'] and g2['dock'] and g2['hintBox']['bottom'] > g2['dock']['top'] + 0.5:
                hp.append('hint box runs under the dock')
            if card.get('audio_text'):
                hp += replay_problems(g2)
            if hp: hint_bad.append(f'{cid} (hint open): {"; ".join(hp)}')
            if first:
                await page.screenshot(path=shot_path(w, h, f'{pre}-b-hint-open.png'))
            await page.click('[data-act=hint-hide]')
            if await page.is_visible('[data-testid=hint-box]') or not await page.is_visible('[data-act=hint]'):
                hint_bad.append(f'{cid}: Hide did not close the hint')
        # ---- fix-it with a phone keyboard (layout viewport shrinks: interactive-widget=resizes-content) ----
        if t == 'fix_it' and await page.query_selector('#fix-input'):
            kb = 300 if h <= 760 else 330
            await page.click('#fix-input')
            await page.keyboard.type('estuv')
            await page.set_viewport_size({'width': w, 'height': h - kb})
            await page.wait_for_timeout(150)
            for hint_open in (False, True):
                if hint_open and await page.query_selector('[data-act=hint]'):
                    await page.click('[data-act=hint]'); await page.wait_for_timeout(80)
                gk = await page.evaluate(GEOM_JS)
                vh = h - kb
                kp = []
                if not gk['fixRow'] or abs(gk['fixRow']['bottom'] - (vh - 16)) > 1:
                    kp.append(f'answer row not pinned 16px above the keyboard (bottom {gk["fixRow"] and gk["fixRow"]["bottom"]}, want {vh - 16})')
                if gk['check'] and abs(gk['check']['w'] - 88) > 0.5: kp.append(f'Check is {gk["check"]["w"]}px wide, want 88')
                dock_top = gk['dock']['top']
                for nm in ('sentence', 'instruction') + (('hintBox',) if hint_open else ('hintPill',)):
                    if gk[nm] and not fully_inside(gk[nm], gk['topbar']['bottom'], dock_top):
                        kp.append(f'{nm} not fully visible above the answer row')
                if gk['overflow'] > 1: kp.append(f'middle zone overflows by {gk["overflow"]}px')
                focused = await page.evaluate("document.activeElement && document.activeElement.id")
                if focused != 'fix-input': kp.append('input lost focus')
                if kp: kb_bad.append(f'{cid} resizes-content{" +hint" if hint_open else ""}: {"; ".join(kp)}')
                if first and shots:
                    await page.screenshot(path=shot_path(w, h, f'{pre}-c-keyboard{"-hint" if hint_open else ""}.png'))
            await page.set_viewport_size({'width': w, 'height': h})
            await page.wait_for_timeout(100)
        # ---- answer (wrong, so the correct answer is shown) ----
        if await page.query_selector('[data-act=dontknow]'):
            wrong = next(i for i, o in enumerate(card['options']) if o != card['answer'])
            await page.click(f'[data-opt="{wrong}"]')
        elif await page.query_selector('#fix-input'):
            await page.fill('#fix-input', 'xyz'); await page.click('[data-act=check]')
        else:
            await page.click('[data-key="1"]'); await page.click('[data-act=check]')
        await page.wait_for_selector('[data-state=wrong]')
        await page.wait_for_timeout(80)
        g = await page.evaluate(GEOM_JS)
        probs = layout_problems(g, cid)
        body = await page.evaluate("(() => { const c = document.querySelector('.card-inner').cloneNode(true); c.querySelectorAll('.info-rows').forEach(e => e.remove()); return c.textContent; })()")
        is_drill = card.get('level') == 'drill'
        if is_drill:
            # Verb drill cards (Phase 4 design): the right form goes in the blank and the one-sentence why is shown inline, no sheet
            if card.get('explanation_en') and card['explanation_en'] not in body: probs.append('drill: explanation not shown inline')
            if card['answer'] not in body: probs.append('drill: right form not shown')
        else:
            if 'The answer is' not in body: probs.append('no "The answer is" line')
            if card.get('explanation_en') and card['explanation_en'] in body: probs.append('explanation shown inline instead of behind "Why"')
        if card.get('audio_text'):
            probs += replay_problems(g)
        if g['next'] and not fully_inside(g['next'], 0, g['vh']): probs.append('Next not fully visible')
        if g['overflow'] > 1: fb_overflow.append(f'{cid}:{g["overflow"]}px' + ('+pill' if g['pill'] else ''))
        if probs: fb_bad.append(f'{cid}: {"; ".join(probs)}')
        if first:
            await page.screenshot(path=shot_path(w, h, f'{pre}-d-feedback.png'))
        # ---- sheet ----
        row = await page.query_selector('[data-sheet=heard]') or await page.query_selector('[data-sheet=why]')
        if is_drill and row:
            sheet_bad.append(f'{cid}: drill card shows a What you heard / Why row')
        elif not is_drill and (card.get('audio_text') or card.get('explanation_en')) and not row:
            sheet_bad.append(f'{cid}: no What you heard / Why row')
        if row:
            await row.click()
            await page.wait_for_selector('[data-testid=sheet]')
            await page.wait_for_timeout(250)
            st = await page.text_content('[data-testid=sheet]')
            sp = []
            if card.get('explanation_en') and card['explanation_en'] not in st: sp.append('explanation missing')
            if card.get('region_note') and card['region_note'] not in st: sp.append('region note missing')
            if card.get('transcript'):
                for l in card['transcript'].split(' / '):
                    if l.split(': ', 1)[-1] not in st: sp.append('transcript line missing')
            sg = await page.evaluate("""() => { const z = document.querySelector('.sheet-zone'), sc = z.querySelector('.scroller'), s = document.querySelector('.fb-sheet').getBoundingClientRect();
                return {overflow: sc.scrollHeight - sc.clientHeight, pill: getComputedStyle(z.querySelector('.more-pill')).display !== 'none', top: s.top, bottom: s.bottom,
                        radius: getComputedStyle(document.querySelector('.fb-sheet')).borderTopLeftRadius, scrim: getComputedStyle(document.querySelector('.fb-backdrop')).backgroundColor,
                        lines: document.querySelectorAll('.fb-sheet .dline').length, linePlay: document.querySelectorAll('.fb-sheet [data-testid=line-play]').length,
                        spkPairs: [...document.querySelectorAll('.fb-sheet .spk')].map(e => [e.textContent.trim(), getComputedStyle(e).color])} }""")
            if sg['overflow'] > 1 and not sg['pill']: sp.append('sheet overflows without the pill')
            if sg['bottom'] > h + 0.5 or sg['top'] < 0: sp.append('sheet off screen')
            if sg['radius'] != '20px': sp.append(f'sheet radius {sg["radius"]}')
            if t == 'scene_question':
                if sg['lines'] == 0 or sg['linePlay'] != sg['lines']: sp.append(f'speaker lines {sg["lines"]}, per-line replay {sg["linePlay"]}')
                scene = scenes.get(card.get('scene_id'))
                # v2.1: colours follow the VOICE (content `voice`), not the order: male/Alonso accent blue, female/Paloma green
                src_lines = card.get('audio_lines') or (scene or {}).get('dialogue') or []
                want = {l['speaker'].upper(): VOICE_COLOR[l['voice']] for l in src_lines if l.get('voice') in VOICE_COLOR}
                if scene:
                    want.update({d['speaker'].upper(): VOICE_COLOR[d['voice']] for d in scene['dialogue'] if d.get('voice') in VOICE_COLOR})
                bad = [(n, col) for n, col in sg['spkPairs'] if n.upper() in want and want[n.upper()] != col]
                if bad or (src_lines and not sg['spkPairs']): sp.append(f'speaker colours {bad[:2] or "missing"}')
            if first:
                await page.screenshot(path=shot_path(w, h, f'{pre}-e-sheet.png'))
                if sg['overflow'] > 1:
                    await page.click('.sheet-zone .more-pill'); await page.wait_for_timeout(500)
                    await page.screenshot(path=shot_path(w, h, f'{pre}-f-sheet-scrolled.png'))
            if sp: sheet_bad.append(f'{cid}: {"; ".join(sp)}')
            await page.click('[data-act=sheet-close]')
        await page.click('[data-act=next]')
    await page.wait_for_selector('[data-screen=summary]')
    await page.wait_for_timeout(200)
    g = await page.evaluate(GEOM_JS)
    sprobs = layout_problems(g, 'summary')
    if not g['next'] or abs(g['next']['bottom'] - (h - 16)) > 1: sprobs.append(f'Done not pinned 16px from the bottom ({g["next"] and g["next"]["bottom"]})')
    check(f'{tag} summary fits: Done pinned 16px from the bottom, review list scrolls with "More below"', not sprobs, sprobs)
    if shots:
        await page.screenshot(path=shot_path(w, h, '06-summary.png'))
    n = len(order)
    check(f'{tag} {n} cards, before answering: no page scroll; middle zone overflow only with "More below"', not q_bad, q_bad[:4])
    check(f'{tag} before answering: no card needs vertical scrolling (hint closed)', not q_overflow, q_overflow)
    check(f'{tag} replay + slow fully visible, designed size, not overlapped by input/Check/number pad (question, hint open, feedback)', not rp_bad, rp_bad[:4])
    check(f'{tag} Hint: shown only for fix_it/listen_type with hint_en ({n_hint}), hidden until tapped, reveals hint_en, fits with the dock, Hide works', not hint_bad, hint_bad[:4])
    check(f'{tag} feedback: result + "The answer is" + replay; explanation only behind "Why"; overflow only with pill', not fb_bad, fb_bad[:4])
    if fb_overflow:
        print(f'      info {tag} feedback screens that scroll (with pill): {fb_overflow}')
    check(f'{tag} What you heard / Why sheet: content, 20px corners, pill when long, scene speaker labels + per-line replay', not sheet_bad, sheet_bad[:4])
    check(f'{tag} fix-it with keyboard (viewport shrunk): answer row + 88px Check pinned 16px above it; sentence, instruction, hint visible', not kb_bad, kb_bad[:4])
    check(f'{tag} no JS errors', not errors, errors[:3])
    await ctx.close()
    return {'q_overflow': q_overflow, 'fb_overflow': fb_overflow}


FAKE_VV = """(() => {
  const fake = new EventTarget(); let kb = 0;
  Object.defineProperty(fake, 'height', { get: () => window.innerHeight - kb });
  Object.defineProperty(fake, 'width', { get: () => window.innerWidth });
  Object.defineProperty(fake, 'offsetTop', { get: () => 0 });
  Object.defineProperty(fake, 'offsetLeft', { get: () => 0 });
  Object.defineProperty(fake, 'pageTop', { get: () => 0 });
  Object.defineProperty(fake, 'scale', { get: () => 1 });
  Object.defineProperty(window, 'visualViewport', { get: () => fake, configurable: true });
  window.__setKeyboard = (px) => { kb = px; fake.dispatchEvent(new Event('resize')); };
})();"""


async def keyboard_visual_viewport_test(browser, base, w=360, h=720, kb=300):
    """Fallback path: the keyboard only shrinks the visual viewport (no resizes-content)."""
    errors = []
    ctx, page = await new_page(browser, errors, viewport={'width': w, 'height': h}, init_script=FAKE_VV)
    await page.goto(base)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
    await page.evaluate("(() => { const S = window.__oye; S.plan = [S.playable.find(c => c.id === 'c-0029') || S.playable.find(c => c.type === 'fix_it')]; })()")
    await page.click('[data-act=start]')
    await page.wait_for_selector('#fix-input')
    await page.click('#fix-input'); await page.keyboard.type('estuve')
    await page.evaluate(f'window.__setKeyboard({kb})')
    await page.wait_for_timeout(150)
    if await page.query_selector('[data-act=hint]'):
        await page.click('[data-act=hint]'); await page.wait_for_timeout(80)
    g = await page.evaluate(GEOM_JS)
    want = h - kb - 16
    ok = g['fixRow'] and abs(g['fixRow']['bottom'] - want) <= 1 and all(
        g[n] and fully_inside(g[n], g['topbar']['bottom'], g['dock']['top']) for n in ('sentence', 'instruction', 'hintBox'))
    await page.screenshot(path=shot_path(w, h, '04-fix-it-c-keyboard-visualviewport.png'))
    check(f'[{w}x{h}] fix-it with a shrunken visualViewport ({kb}px keyboard): row pinned at {want}, sentence + instruction + hint visible',
          ok, {'row_bottom': g['fixRow'] and g['fixRow']['bottom'], 'dock_top': g['dock'] and g['dock']['top']})
    await page.evaluate('window.__setKeyboard(0)')
    await page.wait_for_timeout(100)
    g = await page.evaluate(GEOM_JS)
    check(f'[{w}x{h}] keyboard closed again: answer row back at the bottom (16px)', g['fixRow'] and abs(g['fixRow']['bottom'] - (h - 16)) <= 1, g['fixRow'] and g['fixRow']['bottom'])
    await ctx.close()


def export_v1(dest):
    """The deployed v1 app (the first commit on main) as a folder."""
    first = subprocess.run(['git', '-C', APP, 'rev-list', '--max-parents=0', 'HEAD'], capture_output=True, text=True).stdout.split()[0]
    data = subprocess.run(['git', '-C', APP, 'archive', '--format=tar', first], capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter='data')
    return first


def copy_current(dest):
    for name in os.listdir(dest):
        p = os.path.join(dest, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    shutil.copytree(APP, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.venv', 'screenshots', 'tests', 'tools', '__pycache__', '.git'))


async def wait_for(page, js, timeout=20000):
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout:
        try:
            if await page.evaluate(js):
                return True
        except Exception:
            pass  # page navigating
        await page.wait_for_timeout(200)
    return False


async def update_path_test(browser):
    """Installed v1 -> this build without reinstalling; then this build -> a newer one (deferred mid-session)."""
    site = os.path.join(TMP, 'upd')
    shutil.rmtree(site, ignore_errors=True); os.makedirs(site)
    first = export_v1(site)
    port = free_port()
    server = start_server(site, port)
    base = f'http://localhost:{port}/'
    errors = []
    ctx, page = await new_page(browser, errors)
    try:
        await page.goto(base)
        await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
        await page.evaluate('navigator.serviceWorker.ready')
        await page.reload()
        await page.wait_for_selector('html[data-ready="1"] [data-screen=home]')
        old = await page.evaluate("({shell: document.documentElement.dataset.shell || 'v1', ctl: !!navigator.serviceWorker.controller, caches: []})")
        # some progress in v1
        await page.click('[data-act=start]')
        for _ in range(3):
            await page.wait_for_selector('[data-state=question]')
            if await page.query_selector('[data-act=dontknow]'): await page.click('[data-act=dontknow]')
            elif await page.query_selector('#fix-input'): await page.fill('#fix-input', 'x'); await page.click('[data-act=check]')
            else: await page.click('[data-key="1"]'); await page.click('[data-act=check]')
            await page.click('[data-act=next]')
        await page.click('[data-act=close]')
        await page.wait_for_selector('[data-screen=home]')
        prog_before = await page.evaluate("localStorage.getItem('oye.progress.v1')")
        cv_before = await page.evaluate("localStorage.getItem('oye.contentVersion')")
        check(f'update test: v1 ({first[:7]}) installed and controlling the page, progress saved', old['shell'] == 'v1' and old['ctl'] and prog_before and len(json.loads(prog_before)['history']) == 3)
        await page.screenshot(path=os.path.join(SHOTS11, 'update-1-old-v1.png'))

        # deploy the new build to the same origin, then "open the app again"
        copy_current(site)
        await page.reload()   # next launch: the old SW still serves the v1 shell first
        ok = await wait_for(page, f"document.documentElement.dataset.shell === '{SHELL_VERSION}' && document.documentElement.dataset.ready === '1' && !!document.querySelector('[data-screen=home]')", 25000)
        info = await page.evaluate("""async () => ({ keys: await caches.keys(), cache: (await (await fetch('sw.js', {cache: 'no-store'})).text()).match(/CACHE_VERSION = '([^']+)'/)[1],
            ver: window.__oye && window.__oye.content.version.version, hints: window.__oye && window.__oye.content.cards.filter(c => c.hint_en).length })""")
        check(f'update test: on the next open the new SW activates and the page switches itself to the new shell ({SHELL_VERSION}, no reinstall)',
              ok and f"{info['cache']}-shell" in info['keys'] and not any(k.endswith('-shell') and k != f"{info['cache']}-shell" for k in info['keys']), info)
        check('update test: new content version downloaded, hints present', info['ver'] == json.load(open(os.path.join(APP, 'content', 'version.json')))['version'] and info['hints'] > 0, info)
        prog_after = await page.evaluate("localStorage.getItem('oye.progress.v1')")
        check("update test: Evan's progress (oye.progress.v1) is untouched by the update", prog_after == prog_before)
        st = (await page.text_content('[data-testid=streak]')).strip()
        check('update test: streak/stats read the old progress', st == '1', st)
        await page.screenshot(path=os.path.join(SHOTS11, 'update-2-after-v1.1.png'))

        # ---- v1.1 -> a newer build while mid-session: waits until the session ends ----
        await page.evaluate('window.__marker = 1')
        await page.click('[data-act=start]')
        await page.wait_for_selector('[data-screen=card]')
        sw = open(os.path.join(site, 'sw.js'), encoding='utf-8').read()
        open(os.path.join(site, 'sw.js'), 'w', encoding='utf-8').write(re.sub(r"(const CACHE_VERSION = ')([^']+)'", r"\1\2-next'", sw, count=1))
        idx = open(os.path.join(site, 'index.html'), encoding='utf-8').read()
        open(os.path.join(site, 'index.html'), 'w', encoding='utf-8').write(idx.replace('</body>', '<!-- build: next --></body>'))
        # resume from background -> visibilitychange -> registration.update()
        await page.wait_for_timeout(10500)
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        got = await wait_for(page, "caches.keys().then(k => k.some(x => x.endsWith('-next-shell')))", 15000)
        await page.wait_for_timeout(4000)
        still = await page.evaluate("window.__marker === 1 && !!document.querySelector('[data-screen=card]') && window.__oye.updatePending === true")
        check('update test: v1.1 -> newer while mid-session: new SW activates, page does NOT reload mid-card', got and still, {'activated': got, 'kept_session': still})
        await page.click('[data-act=close]')
        reloaded = await wait_for(page, "window.__marker === undefined && document.documentElement.dataset.ready === '1'", 15000)
        newer = await page.evaluate("fetch('./').then(r => r.text()).then(t => t.includes('build: next'))")
        check('update test: ...and reloads into the newer shell as soon as the session ends (Home)', reloaded and newer, {'reloaded': reloaded, 'new_shell': newer})
        check('update test: no JS errors', not [e for e in errors if 'net::' not in e], errors[:3])
    finally:
        await ctx.close()
        server.terminate(); server.wait()
        shutil.rmtree(site, ignore_errors=True)


async def live_check(url):
    """Headless run against the deployed site at 412x915."""
    os.makedirs(SHOTS11, exist_ok=True)
    local_ver = json.load(open(os.path.join(APP, 'content', 'version.json')))['version']
    local_cv = re.search(r"const CACHE_VERSION = '([^']+)'", open(os.path.join(APP, 'sw.js')).read()).group(1)
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx, page = await new_page(browser, errors)
        r = await page.request.get(url + 'sw.js', headers={'Cache-Control': 'no-cache'})
        live_cv = re.search(r"const CACHE_VERSION = '([^']+)'", await r.text())
        check('live: sw.js is the new build', live_cv and live_cv.group(1) == local_cv, live_cv and live_cv.group(1))
        r = await page.request.get(url + 'content/version.json', headers={'Cache-Control': 'no-cache'})
        v = await r.json()
        check(f'live: content/version.json is version {local_ver}', v.get('version') == local_ver, v.get('version'))
        await page.goto(url)
        await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=20000)
        check(f'live: page runs the {SHELL_VERSION} shell', await page.evaluate("document.documentElement.dataset.shell") == SHELL_VERSION)
        await page.screenshot(path=os.path.join(SHOTS11, 'live-412x915-home.png'))
        await page.evaluate("(() => { const S = window.__oye; S.plan = ['c-0018', 'c-0010', 'c-0029'].map(id => S.playable.find(c => c.id === id)).filter(Boolean); })()")
        await page.click('[data-act=start]')
        await page.wait_for_selector('[data-card=c-0018]')
        await page.wait_for_timeout(300)
        g = await page.evaluate(GEOM_JS)
        probs = layout_problems(g, 'live') + replay_problems(g)
        dock_h = g['dock']['h'] if g['dock'] else 0
        check('live: listen-and-type has the v1.1 layout (short audio row, 358px pinned dock, nothing overlapped, Hint tap)',
              not probs and abs(dock_h - 358) <= 1 and g['hintPill'] is not None, {'problems': probs, 'dock': dock_h})
        await page.screenshot(path=os.path.join(SHOTS11, 'live-412x915-listen-type.png'))
        await page.click('[data-act=hint]')
        ok = card_hint = await page.is_visible('[data-testid=hint-box]')
        check('live: Hint opens and shows hint_en', ok)
        await page.screenshot(path=os.path.join(SHOTS11, 'live-412x915-listen-type-hint.png'))
        await page.click('[data-key="1"]'); await page.click('[data-act=check]')
        await page.wait_for_selector('[data-state=wrong]')
        await page.screenshot(path=os.path.join(SHOTS11, 'live-412x915-listen-type-feedback.png'))
        await page.click('[data-sheet=heard]')
        await page.wait_for_selector('[data-testid=sheet]')
        await page.wait_for_timeout(300)
        await page.screenshot(path=os.path.join(SHOTS11, 'live-412x915-sheet.png'))
        check('live: no JS errors', not errors, errors[:3])
        await live_results_check(browser, url)
        await browser.close()
    endpoint_guard_check()
    passed = sum(1 for r in RESULTS if r[1])
    print(f'\n{passed}/{len(RESULTS)} live checks passed')
    return 0 if passed == len(RESULTS) else 1


# ---------------------------------------------------------------- results upload (Google Sheet)
QUEUE_JS = "JSON.parse(localStorage.getItem('oye.resultsQueue.v1') || '[]')"
# v2 adds level, topic, mission_id and the session's stars; ux-replays adds replays (plays before answering minus the
# first listen, 0-99) and skipped ("I don't know"). All additive: the v3 Apps Script ignores keys it doesn't know.
ROW_KEYS = ['answered_at', 'card_id', 'content_version', 'correct', 'answer_given', 'used_hint', 'used_slow', 'session_id', 'level', 'topic', 'mission_id', 'stars',
            'replays', 'skipped']
STATUS_JS = """() => {
  const el = document.querySelector('#app [data-testid=results-status]');
  if (!el) return null;
  const r = (e) => { const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, left: b.left, w: b.width, h: b.height}; };
  const dot = el.querySelector('.rs-dot'), cs = getComputedStyle(el), ds = getComputedStyle(dot);
  return { state: el.dataset.state, text: el.textContent.trim(), box: r(el), dial: r(document.querySelector('#app .dial')),
    stats: r(document.querySelector('#app .stats.small')), eyebrow: r(document.querySelector('#app .summary .eyebrow')),
    font: cs.fontSize + ' ' + cs.fontWeight, color: cs.color,
    dot: { ...r(dot), bg: ds.backgroundColor, ring: ds.boxShadow } };
}"""
GREEN, GRAY400, GRAY600 = 'rgb(76, 195, 138)', 'rgb(107, 107, 115)', 'rgb(155, 155, 163)'
SENT_TEXT, QUEUED_TEXT = 'Results sent to Gabriel', 'Saved · will send when you’re online'


def status_problems(st, state):
    """Spec: Caption Regular gray-600, directly under the dial, left-aligned, 12px above / 16px below; 8px dot."""
    if not st: return ['no status line']
    p = []
    if st['state'] != state: p.append(f"state {st['state']}")
    if st['text'] != (SENT_TEXT if state == 'sent' else QUEUED_TEXT): p.append(f"text {st['text']!r}")
    if st['font'] != '13px 400' or st['color'] != GRAY600: p.append(f"type {st['font']} {st['color']}")
    if abs(st['box']['top'] - st['dial']['bottom'] - 12) > 0.5: p.append(f"{st['box']['top'] - st['dial']['bottom']}px under the dial, want 12")
    if abs(st['stats']['top'] - st['box']['bottom'] - 16) > 0.5: p.append(f"{st['stats']['top'] - st['box']['bottom']}px above the stats, want 16")
    if abs(st['box']['left'] - st['eyebrow']['left']) > 0.5 or abs(st['dot']['left'] - st['box']['left']) > 0.5: p.append('not left-aligned')
    d = st['dot']
    if abs(d['w'] - 8) > 0.1 or abs(d['h'] - 8) > 0.1: p.append(f"dot {d['w']}x{d['h']}")
    if state == 'sent' and (d['bg'] != GREEN or d['ring'] != 'none'): p.append(f"sent dot {d['bg']} {d['ring']}")
    if state == 'queued' and (d['bg'] != 'rgba(0, 0, 0, 0)' or d['ring'] != f'{GRAY400} 0px 0px 0px 1.5px inset'): p.append(f"queued dot {d['bg']} {d['ring']}")
    return p


async def start_plan(page, ids):
    await page.evaluate(f"(() => {{ const S = window.__oye; S.plan = {json.dumps(ids)}.map(id => S.playable.find(c => c.id === id)); }})()")
    await page.click('[data-act=start]')


async def dont_know_all(page, n):
    for _ in range(n):
        await page.wait_for_selector('[data-state=question]')
        await page.click('[data-act=dontknow]')
        await page.wait_for_selector('[data-state=wrong]')
        await page.click('[data-act=next]')
    await page.wait_for_selector('[data-screen=summary]')


async def status(page):
    return await page.evaluate(STATUS_JS)


async def results_upload_tests(browser, base, version):
    cv = version['version']
    # ---- 1. success: one POST with the session's rows, text/plain JSON, green "Results sent to Gabriel" ----
    errors = []
    fake = FakeEndpoint('ok', delay=1.2)
    ctx, page = await new_page(browser, errors, viewport={'width': 360, 'height': 720}, endpoint=fake)
    await page.goto(base)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
    await page.wait_for_timeout(300)
    check('results: nothing is sent on open when the queue is empty', not fake.calls, len(fake.calls))
    await start_plan(page, ['c-0010', 'c-0018', 'c-0029', 'c-0031'])
    t_start = time.time()
    await page.wait_for_selector('[data-card=c-0010][data-state=question]')
    await page.click('[data-testid=replay]')                    # first listen
    await page.wait_for_timeout(200)
    await page.click('[data-testid=replay]')                    # tap again = stop
    await page.wait_for_timeout(100)
    await page.click('[data-play=slow]')                       # used_slow; a new play after stopping -> replays 1
    await page.click('[data-opt="0"]')                          # $38: wrong
    await page.wait_for_selector('[data-state=wrong]')
    await page.click('[data-testid=replay]')                    # plays on the feedback screen are not counted
    await page.wait_for_timeout(150)
    await page.click('[data-act=next]')
    await page.wait_for_selector('[data-card=c-0018][data-state=question]')
    await page.click('[data-act=hint]')                         # used_hint
    for ch in '8:45': await page.click(f'[data-key="{ch}"]')
    await page.click('[data-act=check]')
    await page.wait_for_selector('[data-state=correct]'); await page.click('[data-act=next]')
    await page.wait_for_selector('[data-card=c-0029][data-state=question]')
    await page.evaluate('window.__oye.session.plays = 150')    # cap: replays never go above 99
    await page.fill('#fix-input', 'Estuve'); await page.click('[data-act=check]')
    await page.wait_for_selector('[data-state=correct]'); await page.click('[data-act=next]')
    await page.wait_for_selector('[data-card=c-0031][data-state=question]')
    await page.click('[data-act=dontknow]')                     # I don't know
    await page.wait_for_selector('[data-state=wrong]'); await page.click('[data-act=next]')
    await page.wait_for_selector('[data-screen=summary]')
    t_end = time.time()
    st = await status(page)
    check('results: while sending, the summary shows the hollow dot + "Saved · will send when you’re online" (spec position/size)',
          not status_problems(st, 'queued'), status_problems(st, 'queued') or st['text'])
    await page.screenshot(path=shot_path(360, 720, '07-summary-results-sending.png'))
    sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 10000)
    st = await status(page)
    check('results: after { ok: true } the summary shows the green dot + "Results sent to Gabriel" (spec position/size)',
          sent and not status_problems(st, 'sent'), status_problems(st, 'sent') or st['text'])
    await page.screenshot(path=shot_path(360, 720, '07-summary-results-sent.png'))
    c0 = fake.calls[0] if fake.calls else {}
    body = c0.get('body')
    check('results: exactly one POST to the endpoint, Content-Type text/plain;charset=utf-8 (no preflight), body = JSON {results: [...]}',
          len(fake.calls) == 1 and c0['method'] == 'POST' and c0['url'] == RESULTS_ENDPOINT
          and c0['ctype'].replace(' ', '').lower() == 'text/plain;charset=utf-8' and isinstance(body, dict) and list(body) == ['results'],
          [(c['method'], c['ctype']) for c in fake.calls])
    rows = body.get('results', []) if isinstance(body, dict) else []
    want = [('c-0010', False, '$38', False, True), ('c-0018', True, '8:45', True, False),
            ('c-0029', True, 'Estuve', False, False), ('c-0031', False, '', False, False)]
    got = [(r.get('card_id'), r.get('correct'), r.get('answer_given'), r.get('used_hint'), r.get('used_slow')) for r in rows]
    check('results: one row per answer: card_id, correct, answer_given, used_hint, used_slow as answered', got == want, got)
    rs = [(r.get('card_id'), r.get('replays'), r.get('skipped')) for r in rows]
    check('results: replays = plays started before answering minus the first listen (play, stop, slow = 1; none = 0; '
          'feedback-screen plays not counted; capped at 99); skipped true only for "I don\u2019t know"',
          rs == [('c-0010', 1, False), ('c-0018', 0, False), ('c-0029', 99, False), ('c-0031', 0, True)], rs)
    sids = {r.get('session_id') for r in rows}
    def iso_ok(v):
        try:
            import datetime
            t = datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
            return t_start - 5 <= t <= t_end + 5 and len(str(v)) <= 30
        except Exception:
            return False
    check(f'results: rows have exactly the {len(ROW_KEYS)} fields (v2: + level easy, topic "", mission_id "", stars 1-3; + replays int, skipped bool); content_version {cv}; one session_id (<=40 chars); answered_at ISO time of each answer',
          all(list(r) == ROW_KEYS for r in rows) and all(r['content_version'] == cv for r in rows)
          and all(isinstance(r['replays'], int) and not isinstance(r['replays'], bool) and 0 <= r['replays'] <= 99 and isinstance(r['skipped'], bool) for r in rows)
          and all(r['level'] == 'easy' and r['topic'] == '' and r['mission_id'] == '' and r['stars'] in (1, 2, 3) for r in rows)
          and len(sids) == 1 and all(isinstance(x, str) and 0 < len(x) <= 40 for x in sids)
          and all(iso_ok(r['answered_at']) for r in rows) and [r['answered_at'] for r in rows] == sorted(r['answered_at'] for r in rows),
          rows[:1])
    q = await page.evaluate(QUEUE_JS)
    check('results: queue empty after the confirmed send', q == [], len(q))
    # grading untouched: srs history matches the answers
    hist = await page.evaluate("JSON.parse(localStorage.getItem('oye.progress.v1')).history.map(h => [h.id, h.ok])")
    check('results: grading unchanged (progress history matches the answers)', hist == [[w[0], 1 if w[1] else 0] for w in want], hist)
    await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')
    await page.reload(); await page.wait_for_selector('html[data-ready="1"] [data-screen=home]'); await page.wait_for_timeout(800)
    check('results: reopening the app sends nothing more (no duplicates)', len(fake.calls) == 1, len(fake.calls))
    check('results (success test): no JS errors', not errors, errors[:3])
    await ctx.close()

    # ---- 2. failure, then retry on resume / app open / online; never lost, never duplicated ----
    errors = []
    fake = FakeEndpoint('http500')
    ctx, page = await new_page(browser, errors, endpoint=fake)
    await page.goto(base)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
    await start_plan(page, ['c-0001', 'c-0031'])
    await dont_know_all(page, 2)
    got1 = await wait_for_calls(page, fake, 1)
    await page.wait_for_timeout(300)
    st = await status(page); q1 = await page.evaluate(QUEUE_JS)
    check('results: HTTP 500 -> rows stay queued, summary shows the hollow "Saved · will send when you’re online"',
          got1 and not status_problems(st, 'queued') and len(q1) == 2, {'status': status_problems(st, 'queued'), 'queue': len(q1)})
    fake.mode = 'okfalse'
    await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")   # app resumed
    got2 = await wait_for_calls(page, fake, 2)
    await page.wait_for_timeout(300)
    st = await status(page); q2 = await page.evaluate(QUEUE_JS)
    check('results: resume retries; a reply with ok: false is not "sent" (rows stay queued)',
          got2 and st['state'] == 'queued' and [r['_id'] for r in q2] == [r['_id'] for r in q1], {'calls': len(fake.calls), 'queue': len(q2)})
    fake.mode = 'ok'
    await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')
    await page.reload()                                                            # next app open
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]')
    got3 = await wait_for_calls(page, fake, 3)
    await wait_for(page, f"{QUEUE_JS}.length === 0", 5000)
    q3 = await page.evaluate(QUEUE_JS)
    check('results: next app open sends the queued rows and empties the queue', got3 and q3 == [], {'calls': len(fake.calls), 'queue': len(q3)})
    # second session: network error at the summary, then the phone comes back online while the summary is open
    fake.mode = 'abort'
    await start_plan(page, ['c-0002', 'c-0019', 'c-0032'])
    await dont_know_all(page, 3)
    got4 = await wait_for_calls(page, fake, 4)
    await page.wait_for_timeout(300)
    st = await status(page)
    queued_ok = got4 and st['state'] == 'queued' and len(await page.evaluate(QUEUE_JS)) == 3
    fake.mode = 'ok'
    await page.evaluate("window.dispatchEvent(new Event('online'))")
    sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 8000)
    st = await status(page)
    check('results: network error -> queued; the "online" event resends and the open summary turns green',
          queued_ok and sent and not status_problems(st, 'sent'), status_problems(st, 'sent'))
    rows = fake.ok_rows()
    keys = [(r['session_id'], r['card_id']) for r in rows]
    check('results: after failures + retries every row arrived exactly once (5 rows, 2 sessions)',
          len(rows) == 5 and len(set(keys)) == 5 and len({r['session_id'] for r in rows}) == 2, keys)
    await page.reload(); await page.wait_for_selector('html[data-ready="1"] [data-screen=home]'); await page.wait_for_timeout(800)
    check('results: nothing re-sent afterwards', len([c for c in fake.calls if c['mode'] == 'ok']) == 2, len(fake.calls))
    check('results (failure/retry test): no JS errors', not [e for e in errors if 'net::' not in e and 'Failed to load resource' not in e], errors[:3])
    await ctx.close()

    # ---- 3. offline: queued across sessions, invalid card ids dropped, sent in batches of <= 200 when back online ----
    errors = []
    fake = FakeEndpoint('ok')
    ctx, page = await new_page(browser, errors, endpoint=fake)
    await page.goto(base)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)
    await page.evaluate('navigator.serviceWorker.ready')
    await ctx.set_offline(True)
    await start_plan(page, ['c-0003', 'c-0020'])
    await dont_know_all(page, 2)
    await page.wait_for_timeout(500)
    st = await status(page)
    check('results: offline -> summary shows hollow dot + "Saved · will send when you’re online", no request made',
          not status_problems(st, 'queued') and not fake.calls, status_problems(st, 'queued') or len(fake.calls))
    await page.screenshot(path=os.path.join(SHOTS11, 'summary-results-offline-412x915.png'))
    await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')
    await page.reload()                                         # app reopened while offline
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=10000)
    await start_plan(page, ['c-0033'])
    await dont_know_all(page, 1)
    await page.wait_for_timeout(300)
    q = await page.evaluate(QUEUE_JS)
    check('results: offline across an app restart + a second session: all 3 rows kept in the queue', len(q) == 3 and not fake.calls, len(q))
    # queue a big backlog through the app's own enqueue (invalid ids filtered there), plus raw bad rows
    added = await page.evaluate("""import('./js/results.js').then(m => {
        const ids = window.__oye.content.cards.map(c => c.id), valid = new Set(ids), rows = [];
        for (let i = 0; i < 450; i++) rows.push({ answered_at: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString(), card_id: ids[i % ids.length],
            content_version: 3, correct: i % 2 === 0, answer_given: 'seed ' + i, used_hint: false, used_slow: false, session_id: 'e2e-seed' });
        rows.push({ answered_at: 'x', card_id: 'c-9999', content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false, session_id: 'e2e-seed' });
        rows.push({ answered_at: 'x', card_id: 'bad', content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false, session_id: 'e2e-seed' });
        // mission rows that must not be queued: unknown question, mission_id mismatch, not level hard, malformed mission id
        const mrow = (card_id, mission_id, level) => ({ answered_at: 'x', card_id, content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false,
            session_id: 'e2e-seed', level, topic: 'directions', mission_id, stars: 1 });
        rows.push(mrow('m-metro-01:q9', 'm-metro-01', 'hard'), mrow('m-metro-01:q1', 'm-voicemail-clinic-01', 'hard'), mrow('m-metro-01:q1', 'm-metro-01', 'medium'),
                  mrow('M-Metro-01:q1', 'M-Metro-01', 'hard'), mrow('q1', '', 'hard'));
        return m.enqueue(rows, valid.add ? new Set([...valid, 'm-metro-01:q1', 'm-voicemail-clinic-01:q1']) : valid).length;
    })""")
    await page.evaluate(f"""(() => {{ const q = {QUEUE_JS};
        q.push({{ _id: 'raw1', answered_at: 'x', card_id: 'c-8888', content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false, session_id: 'e2e-raw' }});
        q.push({{ _id: 'raw2', answered_at: 'x', card_id: 'C-0001 ', content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false, session_id: 'e2e-raw' }});
        q.push({{ _id: 'raw3', answered_at: 'x', card_id: 'm-metro-01:q9', content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false, session_id: 'e2e-raw', level: 'hard', topic: '', mission_id: 'm-metro-01', stars: 0 }});
        localStorage.setItem('oye.resultsQueue.v1', JSON.stringify(q)); }})()""")
    check('results: card ids not in the loaded content, and malformed or unknown mission rows, are dropped when queued (450 of 457 kept)', added == 450, added)
    await ctx.set_offline(False)                                # fires "online"
    sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 15000)
    sizes = [len(c['body']['results']) for c in fake.calls if isinstance(c['body'], dict)]
    rows = fake.ok_rows()
    ids_sent = {r['card_id'] for r in rows}
    valid = {c['id'] for c in json.load(open(os.path.join(APP, 'content', 'cards.json'), encoding='utf-8'))}
    keys = [(r['session_id'], r['card_id'], r['answered_at']) for r in rows]
    check('results: back online -> queue sent in batches of at most 200 (200, 200, 53), summary turns green',
          sent and sizes == [200, 200, 53], sizes)
    check('results: every queued row sent exactly once; invalid card ids never sent; queue empty',
          len(rows) == 453 and len(set(keys)) == 453 and ids_sent <= valid and await page.evaluate(QUEUE_JS) == [],
          {'rows': len(rows), 'unique': len(set(keys)), 'bad': sorted(ids_sent - valid)})
    seeded = [r for r in rows if r['session_id'] == 'e2e-seed']
    check('results: rows queued without replays/skipped (older app versions) still send, and no value is invented for them',
          len(seeded) == 450 and not any('replays' in r or 'skipped' in r for r in seeded), len(seeded))
    norm = await page.evaluate("""import('./js/results.js').then(m => {
        const base = { answered_at: 'x', content_version: 3, correct: false, answer_given: '', used_hint: false, used_slow: false, session_id: 'e2e-norm' };
        const ids = m.enqueue([{ ...base, card_id: 'c-0001', replays: 150, skipped: true }, { ...base, card_id: 'c-0002', replays: '3', skipped: 'yes' },
                               { ...base, card_id: 'c-0003', replays: -2, skipped: false }, { ...base, card_id: 'c-0004' }], null);
        const q = JSON.parse(localStorage.getItem('oye.resultsQueue.v1') || '[]').filter(r => ids.includes(r._id));
        localStorage.setItem('oye.resultsQueue.v1', JSON.stringify(JSON.parse(localStorage.getItem('oye.resultsQueue.v1')).filter(r => !ids.includes(r._id))));
        return [q.map(r => [r.card_id, r.replays, r.skipped]), m.clampReplays(99.7), m.clampReplays(null)];
    })""")
    check('results: the offline queue keeps replays as an integer 0-99 and skipped as a boolean (a row without them stays without them)',
          norm == [[['c-0001', 99, True], ['c-0002', 3, False], ['c-0003', 0, False], ['c-0004', None, None]], 99, 0], norm)
    check('results (offline test): no JS errors', not [e for e in errors if 'net::' not in e and 'Failed to load resource' not in e and 'Failed to fetch' not in e], errors[:3])
    await ctx.close()


async def wait_for_calls(page, fake, n, timeout=8000):
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout:
        if len(fake.calls) >= n:
            return True
        await page.wait_for_timeout(100)
    return False


def endpoint_guard_check():
    check('results: the real endpoint was never contacted (every request to script.google.com was answered by the test fake)',
          len(ENDPOINT_SEEN) == len(ENDPOINT_ROUTED) and sorted(ENDPOINT_SEEN) == sorted(ENDPOINT_ROUTED),
          f'{len(ENDPOINT_SEEN)} requests, {len(ENDPOINT_ROUTED)} faked')


async def live_results_check(browser, url):
    """Deployed site, mobile viewport, endpoint faked: the new build records a session and shows the status line."""
    errors = []
    fake = FakeEndpoint('ok', delay=0.8)
    ctx, page = await new_page(browser, errors, viewport={'width': 360, 'height': 720}, endpoint=fake)
    await page.goto(url)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=20000)
    has_mod = await page.evaluate("import('./js/results.js').then(m => m.RESULTS_ENDPOINT === " + json.dumps(RESULTS_ENDPOINT) + ").catch(() => false)")
    check('live: js/results.js is served and points at the Apps Script endpoint', has_mod)
    await start_plan(page, ['c-0001', 'c-0031'])
    await dont_know_all(page, 2)
    st1 = await status(page)
    sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 10000)
    st2 = await status(page)
    body = fake.calls[0]['body'] if fake.calls else {}
    rows = body.get('results', []) if isinstance(body, dict) else []
    check('live [360x720]: summary shows "Saved · will send…" while sending, then green "Results sent to Gabriel"; 1 faked POST with 2 rows',
          not status_problems(st1, 'queued') and sent and not status_problems(st2, 'sent') and len(fake.calls) == 1 and [r['card_id'] for r in rows] == ['c-0001', 'c-0031'],
          {'sending': status_problems(st1, 'queued'), 'sent': status_problems(st2, 'sent'), 'calls': len(fake.calls)})
    await page.screenshot(path=os.path.join(SHOTS11, 'live-360x720-summary-results.png'))
    check('live results: no JS errors', not errors, errors[:3])
    await ctx.close()


TYPES = ['listen_pick', 'listen_type', 'scene_question', 'fix_it', 'reply']


# ---------------------------------------------------------------- v2 Levels + v2.1 voices
SHOTS_V2 = os.environ.get('OYE_V2_SHOTS') or os.path.join(SHOTS, 'v2')
TOPIC_ORDER = ['numbers_prices', 'time_schedules', 'directions', 'verbs_past', 'verbs_present', 'verbs_commands', 'verbs_future']
TOPIC_NAMES = {'numbers_prices': 'Numbers & prices', 'time_schedules': 'Time & schedules', 'directions': 'Directions & places',
               'verbs_past': 'Past tense', 'verbs_present': 'Present tense', 'verbs_commands': 'Commands', 'verbs_future': 'Future & conditional',
               'weather_plans': 'Weather & plans', 'food_ordering': 'Food & ordering', 'shopping': 'Shopping & sizes',
               'health_pharmacy': 'Health & pharmacy', 'phone_reservations': 'Calls & bookings', 'home_errands': 'Home & errands', 'small_talk': 'Small talk'}


def load_build_audio():
    import importlib.util
    spec = importlib.util.spec_from_file_location('build_audio', os.path.join(APP, 'tools', 'build_audio.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def static_v2_checks(cards, idx, version, sw_src):
    """Content + audio index checks for Levels and the v2.1 voices (no browser)."""
    ba = load_build_audio()
    scenes = json.load(open(os.path.join(APP, 'content', 'scenes.json'), encoding='utf-8'))
    mpath = os.path.join(APP, 'content', 'missions.json')
    missions = json.load(open(mpath, encoding='utf-8')) if os.path.exists(mpath) else []
    src = os.path.join(os.path.dirname(APP), 'content', 'missions.json')
    if os.path.exists(src):
        check('publish: content/missions.json copied into the app (identical to the team\'s file)',
              os.path.exists(mpath) and open(src, 'rb').read() == open(mpath, 'rb').read(), f'{len(missions)} missions')
    check('publish: version.json card_count/mission_count match the app content',
          version.get('card_count') == len(cards) and version.get('mission_count', len(missions)) == len(missions), version.get('mission_count'))
    check('v2.1 voices: Alonso (male) and Paloma (female), no Dalia/Jorge left',
          ba.VOICES == {'male': 'es-US-AlonsoNeural', 'female': 'es-US-PalomaNeural'} and idx.get('voices') == ba.VOICES
          and 'Dalia' not in json.dumps(idx) and 'Jorge' not in json.dumps(idx))
    # Every dialogue line carries a voice (never defaulted from speaking order).
    dlines = [(f"scene {sc['id']}", l) for sc in scenes for l in sc.get('dialogue') or []]
    dlines += [(f"card {c['id']} audio_lines", l) for c in cards for l in c.get('audio_lines') or []]
    dlines += [(f"mission {m['id']}", l) for m in missions if (m.get('media') or {}).get('kind') == 'audio' for l in m['media'].get('lines') or []]
    untagged = [f"{w}: {l.get('speaker')} {l.get('es', '')[:30]!r}" for w, l in dlines if l.get('voice') not in ('male', 'female')]
    check(f'v2.1 voices: every dialogue line and audio_lines entry has voice male|female ({len(dlines)} lines)', not untagged, untagged[:4])
    # One voice per speaker; two speakers never share one (per dialogue).
    clash = []
    groups = [(sc['id'], sc.get('dialogue') or []) for sc in scenes] + [(c['id'], c.get('audio_lines') or []) for c in cards]
    groups += [(m['id'], (m.get('media') or {}).get('lines') or []) for m in missions]
    for gid, ls in groups:
        by_spk = {}
        for l in ls:
            by_spk.setdefault(l.get('speaker'), set()).add(l.get('voice'))
        if any(len(v) > 1 for v in by_spk.values()) or (len(by_spk) == 2 and len(set().union(*by_spk.values())) < 2):
            clash.append(gid)
    check('v2.1 voices: a speaker keeps one voice; the two speakers of a dialogue never share a voice', not clash, clash)
    # audio.json maps each line to a file generated in THAT voice (file name = sha1(voice name|rate|text)).
    wrong = []
    for w, l in dlines:
        e = (idx.get('lines') or {}).get(f"{l.get('voice')}|{l['es'].strip()}")
        if not e or e.get('voice') != l.get('voice') or os.path.basename(e['normal']) != ba.file_name(l['es'].strip(), ba.RATES['normal'], l['voice']):
            wrong.append(f"{w}: {l['es'][:30]!r}")
    check('v2.1 voices: audio.json maps every dialogue line to a file made in that line\'s voice', not wrong, wrong[:4])
    bad_cards = []
    for c in cards:
        t = (c.get('audio_text') or '').strip()
        if not t: continue
        e = idx['items'].get(t)
        if not e: bad_cards.append(f"{c['id']}: no entry"); continue
        if c.get('audio_lines'):
            want = [ba.file_name(l['es'].strip(), ba.RATES[k], l['voice']) for k in ('normal',) for l in c['audio_lines']]
            want_s = [ba.file_name(l['es'].strip(), ba.RATES['slow'], l['voice']) for l in c['audio_lines']]
            ok = e.get('voice') == 'dialogue' and e['parts']['normal'] == want and e['parts']['slow'] == want_s \
                and [(l['speaker'], l['voice']) for l in e['lines']] == [(l['speaker'], l['voice']) for l in c['audio_lines']] \
                and os.path.basename(e['normal']) == ba.joined_name(want)
        else:
            v = c.get('voice') or {'easy': 'female', 'medium': 'male', 'hard': 'male'}[c.get('level', 'easy')]
            ok = e.get('voice') == v and os.path.basename(e['normal']) == ba.file_name(t, ba.RATES['normal'], v) \
                and os.path.basename(e['slow']) == ba.file_name(t, ba.RATES['slow'], v)
        if not ok: bad_cards.append(c['id'])
    n_override = sum(1 for c in cards if c.get('voice'))
    check(f'v2.1 voices: card audio uses the card voice ({n_override} overrides, e.g. c-0003/c-0011 male), else Easy=Paloma, Medium/Hard=Alonso; audio_lines cards join each line in its own voice',
          not bad_cards, bad_cards[:5])
    bad_m = []
    for m in missions:
        md = m.get('media') or {}
        if md.get('kind') != 'audio': continue
        e = (idx.get('missions') or {}).get(m['id'])
        want = [ba.file_name(l['es'].strip(), ba.RATES['normal'], l['voice']) for l in md['lines']]
        if not e or e['parts']['normal'] != want or not (30 <= e.get('duration', 0) <= 120) or not e.get('slow_duration', 0) > e['duration']:
            bad_m.append(m['id'])
    check('v2.1 voices: each audio mission is one clip joined from its lines in their voices, with duration (slow clip longer)', not bad_m, bad_m)
    used = set(os.path.basename(f) for f in all_audio_files(idx))
    on_disk = {f for f in os.listdir(os.path.join(APP, 'audio')) if f.endswith('.mp3')}
    check(f'audio: every referenced file exists and no stale file is left over from the old voice ({len(used)} files)',
          used <= on_disk and not (on_disk - used), {'missing': sorted(used - on_disk)[:3], 'stale': len(on_disk - used)})
    js = sorted(f'js/{f}' for f in os.listdir(os.path.join(APP, 'js')) if f.endswith('.js'))
    missing_sw = [f for f in js if f"'{f}'" not in sw_src]
    check('sw.js precaches every js/*.js file (incl. js/levels.js)', not missing_sw, missing_sw)
    mc = [c['id'] for c in cards if c.get('level') == 'medium']
    check(f'content: {len(mc)} Medium cards, all with a topic; Easy cards have no level or level easy',
          all(c.get('topic') for c in cards if c.get('level') == 'medium') and len(mc) > 0)


async def shot_v2(page, name):
    os.makedirs(SHOTS_V2, exist_ok=True)
    await page.screenshot(path=os.path.join(SHOTS_V2, name))


async def home_fresh(page, base):
    await page.goto(base)
    await page.wait_for_selector('html[data-ready="1"] [data-screen=home]', timeout=15000)


async def answer_card_right(page, card):
    """Answer the card on screen correctly (any template)."""
    t = card['type']
    if await page.query_selector('#fix-input'):
        await page.fill('#fix-input', card['answer']); await page.click('[data-act=check]')
    elif t == 'listen_type':
        for ch in card['answer'].replace('$', ''):
            await page.click(f'[data-key="{ch}"]')
        await page.click('[data-act=check]')
    else:
        await click_option(page, card['answer'])   # by value: Medium topics shuffle the options
    await page.wait_for_selector('[data-state=correct], [data-state=wrong]')


async def click_option(page, value):
    """Tap the option button whose value is `value` (options can be shuffled, so never by content position)."""
    idx = await page.evaluate("(v) => { const b = [...document.querySelectorAll('#app [data-opt]')].find(x => x.dataset.value === v); return b ? b.dataset.opt : null; }", value)
    assert idx is not None, f'no option {value!r} on screen'
    await page.click(f'#app [data-opt="{idx}"]')


MQ_GEOM = """() => {
  const r = (e) => { const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, h: b.height, left: b.left, right: b.right}; };
  const sc = document.querySelector('.mq-zone .scroller');
  const opts = [...document.querySelectorAll('.mopt')].map((e) => ({ ...r(e), lines: Math.round((e.clientHeight - parseFloat(getComputedStyle(e).paddingTop) - parseFloat(getComputedStyle(e).paddingBottom)) / parseFloat(getComputedStyle(e).lineHeight)),
      hclip: e.scrollWidth > e.clientWidth + 1 }));
  const player = document.querySelector('.player');
  return { opts, check: r(document.querySelector('[data-act=check]')), overflow: sc.scrollHeight - sc.clientHeight, player: player && r(player),
           docScroll: document.scrollingElement.scrollHeight - innerHeight, vh: innerHeight, prompt: r(document.querySelector('.mq-prompt')) };
}"""


async def levels_suite(browser, base, cards):
    by_id = {c['id']: c for c in cards}
    missions = json.load(open(os.path.join(APP, 'content', 'missions.json'), encoding='utf-8'))
    mby = {m['id']: m for m in missions}
    audio_index = json.load(open(os.path.join(APP, 'content', 'audio.json'), encoding='utf-8'))
    medium = [c for c in cards if c.get('level') == 'medium']
    errors = []

    # ---- 1. Level filter: Quick session never contains a Medium card, whatever the progress looks like ----
    ctx, page = await new_page(browser, errors, viewport={'width': 360, 'height': 640})
    await home_fresh(page, base)
    info = await page.evaluate("(() => { const S = window.__oye; return { easy: S.easy.map(c => c.id), medium: S.medium.map(c => c.id), plan: S.plan.map(c => c.id) }; })()")
    easy_ids = {c['id'] for c in cards if c.get('level', 'easy') == 'easy'}
    med_ids = {c['id'] for c in medium}
    plans = [info['plan']]
    # Medium cards made maximally "urgent" (wrong, due, box 1) + every Easy card seen and not due -> early-review path
    today = time.strftime('%Y-%m-%d')
    for scenario in ('medium-urgent', 'easy-not-due', 'lesson'):
        prog = {'cards': {}, 'days': {}, 'history': [], 'lesson': 'all'}
        for cid in med_ids:
            prog['cards'][cid] = {'box': 1, 'seen': 3, 'right': 0, 'wrong': 3, 'due': '2000-01-01', 'last': today, 'lastOk': False}
        if scenario == 'easy-not-due':
            for cid in easy_ids:
                prog['cards'][cid] = {'box': 5, 'seen': 5, 'right': 5, 'wrong': 0, 'due': '2999-01-01', 'last': today, 'lastOk': True}
        if scenario == 'lesson':
            easy_tags = [t for e in cards if e['id'] in easy_ids for t in (e.get('lesson_tags') or [])]
            shared = [t for c in medium for t in c.get('lesson_tags', []) if t in easy_tags]
            prog['lesson'] = (shared or easy_tags)[0]
        await page.evaluate(f"localStorage.setItem('oye.progress.v1', {json.dumps(json.dumps(prog))})")
        for _ in range(8):
            await page.reload(); await page.wait_for_selector('html[data-ready="1"] [data-screen=home]')
            plans.append(await page.evaluate('window.__oye.plan.map(c => c.id)'))
    leaked = sorted({i for pl in plans for i in pl if i not in easy_ids})
    check(f'v2 level filter: {len(plans)} Quick-session plans (fresh, Medium cards overdue, Easy all not due, lesson filter) contain only Easy cards',
          not leaked and set(info['easy']) == easy_ids and set(info['medium']) == med_ids and all(len(pl) >= 1 for pl in plans), leaked[:5])
    await page.evaluate("localStorage.clear()")
    await ctx.close()

    # ---- 2. Home at small phones: two challenge cards above Quick session, fits without scrolling ----
    for (w, h) in [(360, 640), (375, 667), (360, 720)]:
        ctx, page = await new_page(browser, errors, viewport={'width': w, 'height': h})
        await home_fresh(page, base)
        g = await page.evaluate("""() => { const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, h: b.height, w: b.width}; };
            return { docScroll: document.scrollingElement.scrollHeight - innerHeight, med: r('[data-testid=challenge-medium]'), hard: r('[data-testid=challenge-hard]'),
                     start: r('[data-act=start]'), head: document.querySelector('.challenges-head')?.textContent, meta: document.querySelector('.meta-line')?.textContent.trim(),
                     medText: document.querySelector('[data-testid=challenge-medium]')?.textContent.replace(/\\s+/g, ' ').trim(),
                     hardText: document.querySelector('[data-testid=challenge-hard]')?.textContent.replace(/\\s+/g, ' ').trim() }; }""")
        ok = (g['docScroll'] <= 1 and g['med'] and g['hard'] and g['med']['bottom'] <= g['hard']['top'] and g['hard']['bottom'] < g['start']['top']
              and g['start']['bottom'] <= h - 15.5 and 'Topic challenge' in g['medText'] and 'Real-life mission' in g['hardText'] and g['meta'].startswith('EASY'))
        check(f'[{w}x{h}] v2 home: Medium then Hard challenge cards above the pinned Quick session, EASY caption, no scrolling', ok, g)
        if (w, h) == (360, 640):
            await shot_v2(page, '01-home-360x640.png')
        await ctx.close()

    # ---- 3. Topic grid + a Medium topic session (stars, results rows, letter keyboard) ----
    fake = FakeEndpoint('ok')
    ctx, page = await new_page(browser, errors, viewport={'width': 360, 'height': 640}, endpoint=fake)
    await home_fresh(page, base)
    await page.click('[data-act=medium]')
    await page.wait_for_selector('[data-screen=topics]')
    tiles = await page.evaluate("[...document.querySelectorAll('[data-topic]')].map(b => { const r = b.getBoundingClientRect(); return {code: b.dataset.topic, name: b.querySelector('.tname').textContent, w: r.width, h: r.height, stars: b.querySelector('.stars').dataset.stars}; })")
    soon = (await page.text_content('[data-testid=coming-soon]')).strip()
    have = [t for t in TOPIC_ORDER if any(c.get('topic') == t for c in medium)]
    check('v2 topic grid: one tile per topic that has cards, in design order, 152x84, 0 stars to start',
          [t['code'] for t in tiles] == have and all(t['name'] == TOPIC_NAMES[t['code']] and abs(t['w'] - 152) <= 1 and abs(t['h'] - 84) <= 0.5 and t['stars'] == '0' for t in tiles), tiles)
    rest = [TOPIC_NAMES[k] for k in TOPIC_NAMES if k not in have]
    check('v2 topic grid: the other topics are listed under "Coming soon" (not tappable)', soon.split(' · ') == rest and not await page.query_selector('.soon [data-topic]'), soon)
    await shot_v2(page, '02-topic-grid-360x640.png')
    await page.click('[data-topic=numbers_prices]')
    await page.wait_for_selector('[data-screen=card][data-state=question]')
    await page.wait_for_timeout(400)
    await shot_v2(page, '03-medium-topic-card-360x640.png')
    n = int((await page.text_content('.count')).split(' of ')[1])
    seen, hinted = [], False
    for i in range(n):
        await page.wait_for_selector('[data-screen=card][data-state=question]')
        cur = await current(page)
        card = by_id[cur['card']]
        seen.append(card)
        await answer_card_right(page, card)
        await page.click('[data-act=next]')
    await page.wait_for_selector('[data-screen=summary][data-level=medium]')
    check(f'v2 Medium session: {n} cards (8-10), all Medium cards from the chosen topic, existing card templates',
          8 <= n <= 10 and all(c.get('level') == 'medium' and c.get('topic') == 'numbers_prices' for c in seen), [c['id'] for c in seen])
    txt = await page.text_content('[data-screen=summary]')
    stars = await page.get_attribute('.msum .stars', 'data-stars')
    check('v2 Medium summary: "MEDIUM · Numbers & prices", 3 stars for all right with no hints, "New best", "N of N right", Another topic + Done',
          'Numbers & prices' in txt and stars == '3' and await page.query_selector('[data-testid=new-best]') and f'{n} of {n} right' in txt
          and await page.query_selector('[data-act=another]') and await page.query_selector('[data-act=done]'), (stars, txt[:120]))
    sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 8000)
    rows = fake.ok_rows()
    check('v2 results: Medium rows carry level "medium", topic, mission_id "" and the session stars',
          sent and len(rows) == n and all(r['level'] == 'medium' and r['topic'] == 'numbers_prices' and r['mission_id'] == '' and r['stars'] == 3 for r in rows), rows[:1])
    await shot_v2(page, '04-medium-summary-360x640.png')
    await page.click('[data-act=another]')
    await page.wait_for_selector('[data-screen=topics]')
    st = await page.get_attribute('[data-topic=numbers_prices] .stars', 'data-stars')
    check('v2 topic grid: "Another topic" goes back to the grid, which shows the best stars (3) for the topic', st == '3', st)
    # Hint caps a card at half: all right, one hint -> not 3 stars; best stays 3 (no "New best")
    await page.click('[data-topic=verbs_past]')
    await page.wait_for_selector('[data-screen=card][data-state=question]')
    n2 = int((await page.text_content('.count')).split(' of ')[1])
    fix_checked = None
    for i in range(n2):
        await page.wait_for_selector('[data-screen=card][data-state=question]')
        card = by_id[(await current(page))['card']]
        if not hinted and await page.query_selector('[data-act=hint]'):
            await page.click('[data-act=hint]'); hinted = True
        inp = await page.query_selector('#fix-input')
        if inp and fix_checked is None:
            attrs = await page.evaluate("(() => { const i = document.querySelector('#fix-input'); return ['autocapitalize', 'autocorrect', 'spellcheck', 'inputmode', 'autocomplete', 'type'].map(a => i.getAttribute(a)); })()")
            plain = card['answer'].lower()
            import unicodedata
            plain = ''.join(ch for ch in unicodedata.normalize('NFD', plain) if unicodedata.category(ch) != 'Mn').upper()
            await page.fill('#fix-input', plain); await page.click('[data-act=check]')
            await page.wait_for_selector('[data-state=correct], [data-state=wrong]')
            fix_checked = (attrs, plain, (await current(page))['state'])
        else:
            await answer_card_right(page, card)
        await page.click('[data-act=next]')
    await page.wait_for_selector('[data-screen=summary][data-level=medium]')
    check('v2 letter keyboard (Medium typed answers): text input has autocapitalize=off, autocorrect=off, spellcheck=false, inputmode=text; accent/case-insensitive',
          fix_checked and fix_checked[0] == ['off', 'off', 'false', 'text', 'off', 'text'] and fix_checked[2] == 'correct', fix_checked)
    st2 = await page.get_attribute('.msum .stars', 'data-stars')
    check('v2 stars: a hint caps that card at half, and 3 stars need no hints (all right + 1 hint -> 2 stars)', hinted and st2 == '2', {'hinted': hinted, 'stars': st2})
    stars_js = await page.evaluate("""import('./js/levels.js').then(L => [
        L.starsFor([{ok: true}], 'easy').stars, L.starsFor(Array(10).fill({ok: true}).map((a, i) => i < 7 ? a : {ok: false}), 'medium').stars,
        L.starsFor(Array(10).fill({ok: true}).map((a, i) => i < 6 ? a : {ok: false}), 'medium').stars,
        L.starsFor(Array(10).fill({ok: true}).map((a, i) => i < 9 ? a : {ok: false}), 'medium').stars,
        L.starsFor([{ok: true, hint: true}, {ok: true}, {ok: true}, {ok: true}], 'hard').stars,
        L.starsFor([{ok: false}, {ok: false}], 'hard').stars])""")
    check('v2 stars rule: 1 = finished, 2 = 70%+, 3 = 90%+ without hints', stars_js == [3, 2, 1, 3, 2, 1], stars_js)
    await page.click('[data-act=done]')
    await page.wait_for_selector('[data-screen=home]')
    tot = (await page.text_content('[data-testid=stars-total]')).strip()
    med = (await page.text_content('[data-testid=challenge-medium] .star-n')).strip()
    check('v2 home: stars earned = best per topic (3 + 2); Medium card shows ★ 5; Done returns straight Home', tot == '5' and med == '5', (tot, med))
    await ctx.close()

    # ---- 4. Hard missions: intro, player bar on every question, keypad, result + Why, transcript ----
    for (w, h) in [(360, 640), (375, 667), (360, 720)]:
        fake = FakeEndpoint('ok')
        ctx, page = await new_page(browser, errors, viewport={'width': w, 'height': h}, endpoint=fake)
        await home_fresh(page, base)
        tag = f'[{w}x{h}]'
        main = (w, h) == (360, 640)
        m = mby['m-metro-01']
        await page.click('[data-act=hard]'); await page.wait_for_selector('[data-screen=missions]')
        if main:
            rows_n = len(await page.query_selector_all('[data-testid=mission-row]'))
            check('v2 Hard: mission list shows every mission (3)', rows_n == len(missions), rows_n)
            ml_ = await page.evaluate("""(() => { const s = document.querySelector('[data-screen=missions]');
                return { back: s.querySelector('.back-link').textContent.trim(), eyebrow: s.querySelector('.lv-tag').textContent.trim(),
                  eyeTT: getComputedStyle(s.querySelector('.lv-tag')).textTransform, title: s.querySelector('.lv-title').textContent.trim(),
                  gridBack: !!s.querySelector('.back-link[data-act=home]'),
                  rows: [...s.querySelectorAll('[data-testid=mission-row]')].map(r => ({ id: r.dataset.mission, title: r.querySelector('.ch-title').textContent.trim(),
                     sub: r.querySelector('[data-testid=mission-sub]').textContent.trim(), stars: r.querySelector('.stars') ? r.querySelector('.stars').dataset.stars : null,
                     chev: !!r.querySelector('.ch-chev svg'), bg: getComputedStyle(r).backgroundColor, radius: getComputedStyle(r).borderRadius, h: r.getBoundingClientRect().height })),
                  surface: getComputedStyle(document.documentElement).getPropertyValue('--surface').trim(),
                  docScroll: document.scrollingElement.scrollHeight - innerHeight }; })()""")
            def want_sub(mm):
                if mm['media']['kind'] != 'audio': return 'Message'
                d = audio_index['missions'][mm['id']]['duration']
                return f'Audio · about {max(1, round(d / 60))} min'
            want_rows = [(mm['id'], mm['title_en'], want_sub(mm), '0') for mm in missions]
            got_rows = [(r['id'], r['title'], r['sub'], r['stars']) for r in ml_['rows']]
            check('v2 missions list (Picasso): "Home" back link, HARD eyebrow, title "Real-life missions"; one surface row per mission with title_en, '
                  '"Audio · about N min" (rounded from the clip) or "Message", best stars and a chevron',
                  ml_['back'] == 'Home' and ml_['gridBack'] and ml_['eyebrow'].lower() == 'hard' and ml_['eyeTT'] == 'uppercase' and ml_['title'] == 'Real-life missions'
                  and got_rows == want_rows and all(r['chev'] and r['bg'] not in ('rgba(0, 0, 0, 0)', 'transparent') and r['radius'] == '12px' for r in ml_['rows'])
                  and ml_['docScroll'] <= 1, {'got': got_rows, 'want': want_rows, 'hdr': (ml_['back'], ml_['eyebrow'], ml_['title'])})
            await shot_v2(page, '12-missions-list-360x640.png')
        await page.click('[data-mission=m-metro-01]')
        await page.wait_for_selector('[data-screen=mission-intro]')
        it = await page.text_content('[data-screen=mission-intro]')
        g = await page.evaluate("document.scrollingElement.scrollHeight - innerHeight")
        if main:
            check('v2 mission intro: HARD eyebrow, title, situation, audio length "about 1 min", 4 questions, "Read the questions first" + "Start mission" (no autoplay: nothing plays from the intro)',
                  'Real-life mission' in it and m['title_en'] in it and m['situation_en'] in it and 'about 1 min' in it and '4, one at a time' in it
                  and 'Read the questions first' in it and 'Start mission' in it and 'Play announcement' not in it and g <= 1, it[:200])
            await shot_v2(page, '05-mission-intro-360x640.png')
            await page.click('[data-act=preview]'); await page.wait_for_selector('[data-testid=sheet]')
            pv = await page.text_content('[data-testid=sheet]')
            check('v2 mission intro: "Read the questions first" shows the prompts only (no options)',
                  all(q['prompt_en'].split(' (type')[0] in pv for q in m['questions']) and m['questions'][1]['options'][2] not in pv)
            await page.click('[data-act=sheet-close]')
        await page.click('[data-act=begin]')
        await page.wait_for_selector('[data-screen=mission-q][data-q=q1]')
        await page.wait_for_timeout(1200)
        a = await page.evaluate("""(() => { const a = window.__oyeAudioEl, b = document.querySelector('[data-act=pl-play]');
            return {t: a.currentTime, clip: a.dataset.clip || '', paused: a.paused, time: document.querySelector('.pl-el').textContent,
                    label: b.getAttribute('aria-label'), pressed: b.getAttribute('aria-pressed'), anim: getComputedStyle(b).animationName}; })()""")
        check(f'{tag} no autoplay: mission question 1 opens with the player at 0:00, nothing playing, Play button not pressed',
              a['paused'] and a['clip'] == '' and a['time'] == '0:00' and a['label'] == 'Play' and a['pressed'] == 'false' and a['anim'] == 'none'
              and await page.is_visible('[data-testid=player]'), a)
        if main:
            await shot_v2(page, '16-mission-question-before-play-360x640.png')
            # Check never waits for audio: pick an answer without listening -> Check is enabled
            q1 = m['questions'][0]
            await page.click(f'[data-opt="{q1["options"].index(q1["answer"])}"]')
            check('v2 mission: Check is available without listening (enabled as soon as an answer is picked)', not await page.is_disabled('[data-act=check]'))
            await page.click('[data-act=pl-play]'); await page.wait_for_timeout(900)
            a = await page.evaluate("""(() => { const a = window.__oyeAudioEl, b = document.querySelector('[data-act=pl-play]');
                return {t: a.currentTime, clip: a.dataset.clip, paused: a.paused, label: b.getAttribute('aria-label'), pressed: b.getAttribute('aria-pressed')}; })()""")
            check('v2 mission: tapping Play starts the mission clip; the button shows the playing state (Pause, aria-pressed)',
                  a['clip'] == 'm-metro-01' and a['t'] > 0 and not a['paused'] and a['label'] == 'Pause' and a['pressed'] == 'true', a)
            await page.click('[data-act=pl-play]'); await page.wait_for_timeout(250)
            a2 = await page.evaluate("""(() => { const a = window.__oyeAudioEl, b = document.querySelector('[data-act=pl-play]');
                return {t: a.currentTime, paused: a.paused, label: b.getAttribute('aria-label'), pressed: b.getAttribute('aria-pressed')}; })()""")
            check('v2 mission: tapping it again stops the audio (button back to Play)', a2['paused'] and a2['label'] == 'Play' and a2['pressed'] == 'false' and a2['t'] > 0, a2)
            # slow toggles the slow file keeping the position (a new play after the stop); scrub seeks
            await page.click('[data-act=pl-slow]'); await page.wait_for_timeout(700)
            s1 = await page.evaluate("(() => { const a = window.__oyeAudioEl; return {slow: a.dataset.slow, d: a.duration, t: a.currentTime}; })()")
            box = await (await page.query_selector('.pl-track')).bounding_box()
            await page.mouse.click(box['x'] + box['width'] * 0.5, box['y'] + box['height'] / 2); await page.wait_for_timeout(400)
            s2 = await page.evaluate("(() => { const a = window.__oyeAudioEl; return {t: a.currentTime, d: a.duration, txt: document.querySelector('.pl-time').textContent}; })()")
            check('v2 player: slow (0.75×) switches to the slow clip; scrubbing seeks; elapsed / total shown',
                  s1['slow'] == '1' and s1['d'] > 60 and abs(s2['t'] / s2['d'] - 0.5) < 0.1 and '/ 1:1' in s2['txt'], (s1, s2))
            await page.click('[data-act=pl-slow]'); await page.wait_for_timeout(300)
            plays_q1 = await page.evaluate('window.__oye.mission.plays')
            check('v2 mission replays: play, stop, slow (a new play) = 2 plays on q1; seeking or switching speed while playing is not a new play',
                  plays_q1 == 2, plays_q1)
        players = []
        labels = []
        # q1 right, q2 wrong, q3 typed 5:00 on the keypad (right), q4 right
        for q in m['questions']:
            await page.wait_for_selector(f'[data-screen=mission-q][data-q={q["id"]}]')
            players.append(await page.is_visible('[data-testid=player]') and await page.is_visible('[data-act=pl-slow]'))
            labels.append((await page.text_content('[data-act=check]')).strip())
            if main and q is m['questions'][-1]:
                await page.click(f'[data-opt="{q["options"].index(q["answer"])}"]')
                await shot_v2(page, '14-last-question-see-results-360x640.png')
            if q['id'] == 'q2':
                await page.wait_for_timeout(100)
                await page.click('[data-opt="1"]')
                mg = await page.evaluate(MQ_GEOM)
                two_line = all(o['lines'] == 2 and o['h'] > 56 for o in mg['opts'])
                fits = mg['overflow'] <= 1 and mg['opts'][-1]['bottom'] <= mg['check']['top'] + 0.5 and mg['docScroll'] <= 1 and not any(o['hclip'] for o in mg['opts'])
                check(f'{tag} v2 fit: metro q2 options wrap onto two lines (hug height, not fixed 56px) and all four fit above Check, no scrolling',
                      two_line and fits, {'opts': [(round(o['top']), round(o['bottom']), o['lines']) for o in mg['opts']], 'check_top': mg['check']['top'], 'overflow': mg['overflow']})
                sel = await page.evaluate("getComputedStyle(document.querySelector('.mopt.is-selected')).borderColor")
                if main:
                    check('v2 mission: the selected option gets the accent border; Check enabled after choosing', sel == 'rgb(110, 139, 255)' and not await page.is_disabled('[data-act=check]'), sel)
                await shot_v2(page, f'06-metro-q2-wrapped-options-{w}x{h}.png')
                await page.click('[data-act=check]')
            elif q['kind'] == 'type':
                kp = await page.is_visible('[data-testid=keypad]') and await page.is_visible('[data-key=":"]')
                for ch in '5:00': await page.click(f'[data-key="{ch}"]')
                if main:
                    typed = (await page.text_content('[data-testid=typed]')).strip()
                    check('v2 mission typed answer uses the v1.1 keypad (numbers and colon)', kp and typed == '5:00', typed)
                    await shot_v2(page, '07-metro-q3-keypad-360x640.png')
                await page.click('[data-act=check]')
            elif main and q is m['questions'][0]:
                await page.click('[data-act=check]')           # answer already picked above
            else:
                await page.click(f'[data-opt="{q["options"].index(q["answer"])}"]'); await page.click('[data-act=check]')
        await page.wait_for_selector('[data-screen=mission-result]')
        if not main:
            await ctx.close(); continue
        check('v2 mission: the player bar (replay + slow) stays on every question', all(players) and len(players) == 4, players)
        check('v2 mission (Picasso): the button reads "Check" on every question and "See results" on the last one',
              labels == ['Check'] * (len(m['questions']) - 1) + ['See results'], labels)
        res = await page.evaluate("""(() => ({ stars: document.querySelector('.mission-result .stars').dataset.stars, score: document.querySelector('[data-testid=score]').textContent,
            rows: [...document.querySelectorAll('[data-testid=mr-row]')].map(r => ({ q: r.dataset.q, bad: r.classList.contains('bad'), open: !r.querySelector('.why-box').hidden,
                     why: r.querySelector('.why-box').textContent, prompt: r.querySelector('.mr-prompt').textContent })),
            last: document.querySelector('.mr-zone .scroll-inner').lastElementChild.textContent.trim(),
            wrongBg: getComputedStyle(document.querySelector('.mr-row.bad .why-box')).backgroundColor }))()""")
        q2 = next(r for r in res['rows'] if r['q'] == 'q2')
        others_closed = all(not r['open'] for r in res['rows'] if r['q'] != 'q2')
        check('v2 mission result: 3 of 4 right -> 2 stars; the wrong answer\'s "Why" starts open (wrongTint, "You picked …" + explanation); right ones closed; last row "Read the script"',
              res['stars'] == '2' and '3 of 4 right' in res['score'] and q2['bad'] and q2['open'] and 'You picked' in q2['why'] and m['questions'][1]['explanation_en'] in q2['why']
              and others_closed and res['last'] == 'Read the script' and res['wrongBg'] == 'rgb(45, 23, 20)'
              and '(type it like' not in ''.join(r['prompt'] for r in res['rows']), res)
        await shot_v2(page, '08-mission-result-why-open-360x640.png')
        await page.click('[data-q=q1] [data-act=why]')
        opened = await page.is_visible('[data-q=q1] [data-testid=why-box]')
        check('v2 mission result: tapping any row opens its "Why"', opened and m['questions'][0]['explanation_en'] in (await page.text_content('[data-q=q1] [data-testid=why-box]')))
        await page.get_by_role('button', name='Read the script', exact=True).click()
        await page.wait_for_selector('[data-screen=mission-read][data-from=result]')
        tr = await page.text_content('[data-testid=read-script-lines]')
        check('v2 mission result: "Read the script" opens every line of the announcement with speaker labels',
              all(l['es'] in tr for l in m['media']['lines']) and 'Anuncio' in tr and await page.query_selector('.read-screen .spk.v-male'))
        await page.click('[data-act=read-back]')
        await page.wait_for_selector('[data-screen=mission-result]')
        sent_ok = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 10000)
        q = await page.evaluate(QUEUE_JS)
        mrows = fake.ok_rows()
        want_ids = [f'm-metro-01:{qq["id"]}' for qq in m['questions']]
        sid = {r.get('session_id') for r in mrows}
        row_ok = (len(mrows) == 4 and [r['card_id'] for r in mrows] == want_ids and all(list(r.keys()) == ROW_KEYS for r in mrows)
                  and all(r['level'] == 'hard' and r['mission_id'] == 'm-metro-01' and r['topic'] == m['topic'] and r['stars'] == 2 for r in mrows)
                  and [r['correct'] for r in mrows] == [True, False, True, True] and mrows[2]['answer_given'] == '5:00'
                  and mrows[1]['answer_given'] == m['questions'][1]['options'][1] and len(sid) == 1 and all(fake_accepts(r) for r in mrows)
                  and all(isinstance(r['content_version'], int) and r['content_version'] > 0 for r in mrows) and not any(r['used_hint'] for r in mrows))
        check('v2 results (Apps Script v3): mission answers are queued and sent like card answers: card_id "<mission_id>:qN", level hard, topic, mission_id, stars (2); '
              'result screen shows "Results sent to Gabriel"; queue empty',
              row_ok and sent_ok and q == [] and all(c['ctype'].startswith('text/plain') for c in fake.calls),
              {'rows': [(r['card_id'], r['correct'], r['level'], r['mission_id'], r['stars'], r['used_slow']) for r in mrows], 'queue': len(q), 'sent': sent_ok})
        check('v2 results: slow replay used before answering q1 is recorded (used_slow) on that mission row only', mrows and mrows[0]['used_slow'] is True and not any(r['used_slow'] for r in mrows[1:]),
              [r.get('used_slow') for r in mrows])
        check('v2 results: mission rows carry replays per question (q1: 2 plays -> 1; questions with no new play -> 0) and skipped false',
              [r.get('replays') for r in mrows] == [1, 0, 0, 0] and all(r.get('skipped') is False for r in mrows),
              [(r.get('replays'), r.get('skipped')) for r in mrows])
        await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')
        hs = (await page.text_content('[data-testid=challenge-hard] .star-n')).strip()
        streak = (await page.text_content('[data-testid=streak]')).strip()
        check('v2 home after a mission: Hard card shows its stars (★ 2); the mission counts for the streak (1)', hs == '2' and streak == '1', (hs, streak))

        # ---- clinic voicemail: two voices, label colour follows the voice ----
        mc = mby['m-voicemail-clinic-01']
        await page.click('[data-act=hard]'); await page.click('[data-mission=m-voicemail-clinic-01]')
        it = await page.text_content('[data-screen=mission-intro]')
        await page.click('[data-act=begin]')
        for q in mc['questions']:
            await page.wait_for_selector(f'[data-screen=mission-q][data-q={q["id"]}]')
            if q['kind'] == 'pick':
                await page.click(f'[data-opt="{q["options"].index(q["answer"])}"]')
            else:
                for ch in q['answer']: await page.click(f'[data-key="{ch}"]')
            await page.click('[data-act=check]')
        await page.wait_for_selector('[data-screen=mission-result]')
        st = await page.get_attribute('.mission-result .stars', 'data-stars')
        await page.click('[data-act=transcript]'); await page.wait_for_selector('[data-testid=sheet]'); await page.wait_for_timeout(250)
        pairs = await page.evaluate("[...document.querySelectorAll('.fb-sheet .dline')].map(d => [d.querySelector('.spk').textContent, d.dataset.voice, getComputedStyle(d.querySelector('.spk')).color, !!d.querySelector('[data-testid=line-play]')])")
        want = [(l['speaker'], l['voice'], VOICE_COLOR[l['voice']]) for l in mc['media']['lines']]
        check('v2.1 two-voice mission transcript: each line labelled from content, colour follows its voice (male blue, female green), per-line replay',
              [tuple(p[:3]) for p in pairs] == want and all(p[3] for p in pairs) and 'Start mission' in it and st == '3', {'pairs': pairs[:3], 'stars': st})
        await shot_v2(page, '09-two-voice-transcript-clinic-360x640.png')
        await page.click('[data-act=sheet-close]')
        # Try again, this time opening the header Hint on one question: same v1.1 hint panel, and no 3 stars
        await page.click('[data-act=retry]'); await page.wait_for_selector('[data-screen=mission-intro]')
        await page.click('[data-act=begin]')
        hint_info = None
        for q in mc['questions']:
            await page.wait_for_selector(f'[data-screen=mission-q][data-q={q["id"]}]')
            if q is mc['questions'][0]:
                before = await page.evaluate("(() => ({ pill: !!document.querySelector('header [data-testid=hint-pill]'), boxHidden: document.querySelector('[data-testid=hint-box]').hidden }))()")
                await page.click('[data-act=hint]'); await page.wait_for_timeout(150)
                hint_info = await page.evaluate("""(() => { const b = document.querySelector('[data-testid=hint-box]'), cs = getComputedStyle(b);
                    const probe = document.createElement('div'); probe.style.background = 'var(--accent-tint)'; probe.style.borderRadius = 'var(--radius-l)';
                    document.body.appendChild(probe); const want = getComputedStyle(probe); const w = { bg: want.backgroundColor, radius: want.borderTopLeftRadius }; probe.remove();
                    const r = b.getBoundingClientRect(), dock = document.querySelector('[data-testid=dock]').getBoundingClientRect();
                    return { visible: !b.hidden && r.height > 0, cls: b.className, eyebrow: b.querySelector('.hint-head .eyebrow').textContent.trim(),
                             hide: !!b.querySelector('[data-act=hint-hide]'), text: b.querySelector('.hint-text').textContent,
                             bg: cs.backgroundColor, radius: cs.borderTopLeftRadius, want: w, pillHidden: document.querySelector('[data-act=hint]').hidden,
                             aboveDock: r.bottom <= dock.top + 0.5, used: window.__oye.mission.hintUsed }; })()""")
                await shot_v2(page, '13-mission-question-hint-open-360x640.png')
                await page.click('[data-act=hint-hide]')
                hint_info['pillBack'] = await page.is_visible('[data-act=hint]')
                hint_info['before'] = before
            if q['kind'] == 'pick':
                await page.click(f'[data-opt="{q["options"].index(q["answer"])}"]')
            else:
                for ch in q['answer']: await page.click(f'[data-key="{ch}"]')
            await page.click('[data-act=check]')
        await page.wait_for_selector('[data-screen=mission-result]')
        st2 = await page.get_attribute('.mission-result .stars', 'data-stars')
        cap2 = await page.text_content('.mr-cap')
        q1 = mc['questions'][0]
        check('v2 mission hint (Picasso): the header Hint opens the same v1.1 hint panel (hint-box, "Hint" eyebrow, Hide, accentTint, radius-l) with hint_en; Hide brings the pill back',
              hint_info['before']['pill'] and hint_info['before']['boxHidden'] and hint_info['visible'] and hint_info['cls'] == 'hint-box' and hint_info['eyebrow'].lower() == 'hint'
              and hint_info['hide'] and hint_info['text'] == q1['hint_en'] and hint_info['bg'] == hint_info['want']['bg'] and hint_info['radius'] == hint_info['want']['radius']
              and hint_info['pillHidden'] and hint_info['pillBack'] and hint_info['used'] is True, hint_info)
        best = await page.evaluate("JSON.parse(localStorage.getItem('oye.stars.v1')).missions['m-voicemail-clinic-01']")
        check('v2 mission hint (Picasso): all right but one hint used -> 2 stars, not 3 (no-hints rule, same as cards); the saved best stays 3',
              st2 == '2' and 'with no hints' in cap2 and best == 3, (st2, cap2, best))
        await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')

        # ---- landlord WhatsApp: long message scrolls with fade + "More below"; row reopens it ----
        ml = mby['m-landlord-whatsapp-01']
        await page.click('[data-act=hard]'); await page.click('[data-mission=m-landlord-whatsapp-01]')
        it = await page.text_content('[data-screen=mission-intro]')
        await page.click('[data-act=begin]')
        await page.wait_for_selector('[data-screen=mission-msg]'); await page.wait_for_timeout(250)
        mg = await page.evaluate("""(() => { const z = document.querySelector('.msg-zone'), sc = z.querySelector('.scroller'), b = document.querySelector('[data-testid=bubble]');
            return { overflow: sc.scrollHeight - sc.clientHeight, pill: getComputedStyle(z.querySelector('.more-pill')).display !== 'none', fade: getComputedStyle(z.querySelector('.fade')).opacity,
                     text: [...b.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(''), ws: getComputedStyle(b).whiteSpace, sender: document.querySelector('[data-testid=sender]').textContent,
                     docScroll: document.scrollingElement.scrollHeight - innerHeight }; })()""")
        check('v2 text mission: WhatsApp message (line breaks + emoji kept) is longer than the screen: it scrolls inside the bubble area with the fade + "More below" pill',
              mg['overflow'] > 40 and mg['pill'] and mg['fade'] == '1' and mg['text'].strip() == ml['media']['text_es'] and mg['ws'] == 'pre-wrap' and mg['docScroll'] <= 1
              and 'Read the message' in it, {k: v for k, v in mg.items() if k != 'text'})
        tm = await page.evaluate("""(() => { const b = document.querySelector('[data-testid=bubble]'), t = b.querySelector('[data-testid=msg-time]');
            if (!t) return null; const br = b.getBoundingClientRect(), tr = t.getBoundingClientRect(), range = document.createRange(); range.selectNodeContents(t);
            const txt = range.getBoundingClientRect();
            return { text: t.textContent, last: b.lastElementChild === t, align: getComputedStyle(t).textAlign, right: br.right - txt.right, inside: tr.left >= br.left && tr.right <= br.right,
                     sender: document.querySelector('[data-testid=sender]').textContent.trim() }; })()""")
        check('v2 text mission (content v2.2): sender line is media.sender_en ("Your landlord"); media.time ("16:35") sits bottom-right on the bubble',
              tm and tm['sender'] == ml['media']['sender_en'] == 'Your landlord' and tm['text'] == ml['media']['time'] == '16:35' and tm['last'] and tm['align'] == 'right'
              and 0 <= tm['right'] <= 16.5 and tm['inside'], tm)
        await shot_v2(page, '10-landlord-whatsapp-scroll-360x640.png')
        for _ in range(8):
            if not await page.is_visible('.msg-zone .more-pill'): break
            await page.click('.msg-zone .more-pill'); await page.wait_for_timeout(450)
        end = await page.evaluate("(() => { const z = document.querySelector('.msg-zone'); return getComputedStyle(z.querySelector('.more-pill')).display === 'none'; })()")
        check('v2 text mission: "More below" scrolls down and hides at the end of the message', end)
        await shot_v2(page, '10b-landlord-whatsapp-end-360x640.png')
        await shot_v2(page, '15-landlord-message-sender-time-360x640.png')
        await page.click('[data-act=to-questions]')
        await page.wait_for_selector('[data-screen=mission-q][data-q=q1]')
        row = await page.is_visible('[data-testid=msg-row]') and not await page.query_selector('[data-testid=player]')
        row_txt = (await page.text_content('[data-testid=msg-row]')).strip()
        await page.click('[data-testid=msg-row]'); await page.wait_for_selector('[data-screen=mission-msg]')
        back = (await page.text_content('[data-act=to-questions]')).strip()
        await page.click('[data-act=to-questions]'); await page.wait_for_selector('[data-screen=mission-q][data-q=q1]')
        check('v2 text mission: questions show "Message from your landlord" (no player) that reopens the message and comes back', row and back == 'Back to question 1' and row_txt == 'Message from your landlord', row_txt)
        ll = []
        for q in ml['questions']:
            await page.wait_for_selector(f'[data-screen=mission-q][data-q={q["id"]}]')
            ll.append((await page.text_content('[data-act=check]')).strip())
            if q['kind'] == 'pick':
                await page.click('[data-opt="0"]')
            else:
                for ch in '20': await page.click(f'[data-key="{ch}"]')   # an accepted alternate of "8:00"
            await page.click('[data-act=check]')
        await page.wait_for_selector('[data-screen=mission-result]')
        tq = next(q for q in ml['questions'] if q['kind'] == 'type')
        alt_txt = (await page.text_content(f'[data-q={tq["id"]}] .wb-given')).strip()
        check('mission typed accepted alternate: the result says “20” works. Also common: “8:00”. (main answer only)',
              alt_txt == f'“20” works. Also common: “{tq["answer"]}”.', alt_txt)
        sent_l = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 10000)
        lrows = [r for r in fake.ok_rows() if r.get('mission_id') == ml['id']]
        check('v2 results: message (WhatsApp) mission rows send replays 0 and skipped false',
              sent_l and len(lrows) == len(ml['questions']) and all(r.get('replays') == 0 and r.get('skipped') is False for r in lrows),
              [(r.get('replays'), r.get('skipped')) for r in lrows])
        await page.click('[data-act=transcript]'); await page.wait_for_selector('[data-testid=sheet]')
        tt = await page.text_content('[data-testid=sheet]')
        check('v2 text mission result: the transcript row shows the message and its English after answering', ml['media']['text_en'][:40] in tt and ml['media']['text_es'][:40] in tt)
        check('v2 text mission: the last question (keypad) also reads "See results"', ll == ['Check'] * (len(ml['questions']) - 1) + ['See results'], ll)
        await page.click('[data-act=sheet-close]')
        await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')
        # Fallbacks: no sender_en -> title_en; a time that isn't 24-hour HH:MM is not shown
        await page.evaluate("""(() => { const m = window.__oye.missions.find(x => x.id === 'm-landlord-whatsapp-01');
            window.__oyeSaved = { s: m.media.sender_en, t: m.media.time }; delete m.media.sender_en; m.media.time = '4:35 pm'; })()""")
        await page.click('[data-act=hard]'); await page.click('[data-mission=m-landlord-whatsapp-01]'); await page.click('[data-act=begin]')
        await page.wait_for_selector('[data-screen=mission-msg]')
        fb = await page.evaluate("(() => ({ sender: document.querySelector('[data-testid=sender]').textContent.trim(), time: !!document.querySelector('[data-testid=msg-time]') }))()")
        await page.click('[data-act=to-questions]'); await page.wait_for_selector('[data-screen=mission-q][data-q=q1]')
        fr = (await page.text_content('[data-testid=msg-row]')).strip()
        check('v2 text mission fallback: without sender_en the sender line is title_en; a non-HH:MM time is hidden; the reopen row still works',
              fb['sender'] == ml['title_en'] and not fb['time'] and fr == 'Read the message again', (fb, fr))
        await ctx.close()

    # ---- 5. Easy scene transcript: label colours follow the voice (Barista is female in s-cafe-01) ----
    ctx, page = await new_page(browser, errors, viewport={'width': 360, 'height': 640})
    await home_fresh(page, base)
    await start_plan(page, ['c-0019', 'c-0022'])
    for cid in ('c-0019', 'c-0022'):
        await page.wait_for_selector(f'[data-card={cid}][data-state=question]')
        await page.click('[data-act=dontknow]')
        await page.wait_for_selector('[data-state=wrong]')
        await page.click('[data-sheet=heard]'); await page.wait_for_selector('[data-testid=sheet]'); await page.wait_for_timeout(250)
        pairs = await page.evaluate("[...document.querySelectorAll('[data-sec=heard] .dline')].map(d => [d.querySelector('.spk').textContent, d.dataset.voice, getComputedStyle(d.querySelector('.spk')).color])")
        want = [(l['speaker'], l['voice'], VOICE_COLOR[l['voice']]) for l in by_id[cid]['audio_lines']]
        check(f'v2.1 Easy scene {cid}: "What you heard" labels come from content ({", ".join(l["speaker"] for l in by_id[cid]["audio_lines"])}) and colours follow the voice',
              [tuple(p) for p in pairs] == want, pairs)
        if cid == 'c-0019':
            await shot_v2(page, '11-two-voice-transcript-easy-scene-360x640.png')
        await page.click('[data-act=sheet-close]')
        await page.click('[data-act=next]')
    await ctx.close()
    check('v2 levels suite: no JS errors', not [e for e in errors if 'net::' not in e], errors[:3])


# ---------------------------------------------------------------- ux-replays: no autoplay, replays, accepted alternates, focus, topic stars
SHOTS_UX = os.environ.get('OYE_UX_SHOTS') or os.path.join(SHOTS, 'ux-replays')
PLAYBTN_JS = """() => { const b = document.querySelector('#app .card-zone [data-testid=replay]'), a = window.__oyeAudioEl, cap = document.querySelector('#app [data-testid=listen-cap]');
  const vis = (e) => !!e && getComputedStyle(e).display !== 'none';
  const slow = document.querySelector('#app .card-zone [data-play=slow]');
  return { playing: !a.paused && !a.ended, rate: a.playbackRate, dur: a.duration, cls: b.className, pressed: b.getAttribute('aria-pressed'), label: b.getAttribute('aria-label'),
           stopIcon: vis(b.querySelector('.ic-stop')), playIcon: vis(b.querySelector('.ic-play')), cap: cap && cap.textContent,
           anim: [b, slow].map((e) => getComputedStyle(e).animationName), slowCls: slow.className }; }"""
TILES_JS = """() => [...document.querySelectorAll('.topic-tile')].map((t) => { const r = t.getBoundingClientRect(), n = t.querySelector('.tname'), s = t.querySelector('.stars').getBoundingClientRect();
  const lines = Math.round(n.getBoundingClientRect().height / parseFloat(getComputedStyle(n).lineHeight));
  return { name: n.textContent, lines, h: r.height, gap: s.top - n.getBoundingClientRect().bottom, fromBottom: r.bottom - s.bottom, starsTop: s.top - r.top }; })"""
FOCUS_JS = """() => { const e = document.activeElement, cs = getComputedStyle(e); return { act: e && e.dataset.act, fv: e.matches(':focus-visible'), outline: cs.outlineStyle, width: cs.outlineWidth, bg: cs.backgroundColor }; }"""


async def ux_shot(page, name):
    os.makedirs(SHOTS_UX, exist_ok=True)
    await page.screenshot(path=os.path.join(SHOTS_UX, name))


async def ux_replays_suite(browser, base, cards):
    errors = []
    by_id = {c['id']: c for c in cards}
    fake = FakeEndpoint('ok')
    ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844}, endpoint=fake)
    await home_fresh(page, base)

    # ---- play button: silent start, playing state + tap to stop, "Play again", slow, no animation (hero and row layouts) ----
    await start_plan(page, ['c-0010', 'c-0018', 'c-0061', 'c-0055', 'c-0061', 'c-0061', 'c-0011', 'c-0061'])
    for cid, layout in (('c-0010', 'hero'), ('c-0018', 'row')):
        await page.wait_for_selector(f'[data-card={cid}][data-state=question]')
        await page.wait_for_timeout(700)
        s0 = await page.evaluate(PLAYBTN_JS)
        if cid == 'c-0010': await ux_shot(page, '01-card-before-play-tap-to-listen.png')
        await page.click('#app .card-zone [data-testid=replay]'); await page.wait_for_timeout(350)
        s1 = await page.evaluate(PLAYBTN_JS)
        if cid == 'c-0010': await ux_shot(page, '02-card-playing-stop-state.png')
        await page.click('#app .card-zone [data-testid=replay]'); await page.wait_for_timeout(250)
        s2 = await page.evaluate(PLAYBTN_JS)
        if cid == 'c-0010': await ux_shot(page, '03-card-after-play-play-again.png')
        await page.click('#app .card-zone [data-play=slow]'); await page.wait_for_timeout(350)
        s3 = await page.evaluate(PLAYBTN_JS)
        await page.click('#app .card-zone [data-testid=replay]'); await page.wait_for_timeout(250)   # stops the slow clip too
        s4 = await page.evaluate(PLAYBTN_JS)
        plays = await page.evaluate('window.__oye.session.plays')
        ok = (not s0['playing'] and s0['cap'] == 'Tap to listen' and s0['pressed'] == 'false' and s0['playIcon'] and not s0['stopIcon']
              and s1['playing'] and 'is-playing' in s1['cls'] and s1['pressed'] == 'true' and s1['label'] == 'Stop' and s1['stopIcon'] and not s1['playIcon'] and s1['cap'] == 'Play again'
              and not s2['playing'] and s2['pressed'] == 'false' and s2['label'] == 'Replay' and s2['playIcon'] and s2['cap'] == 'Play again'
              and s3['playing'] and s3['dur'] > s1['dur'] and 'is-playing' in s3['slowCls'] and s3['pressed'] == 'true'
              and not s4['playing'] and s4['pressed'] == 'false' and plays == 2
              and all(x == 'none' for st in (s0, s1, s2, s3) for x in st['anim']))
        check(f'no autoplay ({layout} play button, {cid}): opens silent with "Tap to listen"; tap plays and shows a stop icon (aria-pressed, "Stop"), caption "Play again"; '
              'tap again stops; slow still starts slow playback on tap (play button shows playing, can stop it); no pulse/animation; 2 plays counted',
              ok, {'before': s0, 'playing': s1, 'stopped': s2, 'slow': s3, 'stopped2': s4, 'plays': plays})
        if cid == 'c-0010':
            await page.click('[data-opt="1"]')
            await page.wait_for_selector('[data-state=correct], [data-state=wrong]')
            f = await page.evaluate(FOCUS_JS)
            check('focus: after tapping an option, Next is focused (keyboard) but shows no focus ring; flat blue button',
                  f['act'] == 'next' and f['outline'] == 'none' and f['bg'] == 'rgb(110, 139, 255)', f)
        else:
            for ch in '8:45': await page.click(f'[data-key="{ch}"]')
            await page.click('[data-act=check]')
            await page.wait_for_selector('[data-state=correct]')
        await page.click('[data-act=next]')

    # ---- accepted alternate: keep the user's word, "“gira” works. Also common: “dobla”." ----
    async def fix(cid, typed):
        await page.wait_for_selector(f'[data-card={cid}][data-state=question]')
        await page.click('#fix-input'); await page.keyboard.type(typed)
        await page.click('[data-act=check]')
        await page.wait_for_selector('[data-state=correct], [data-state=wrong]')
        return await page.evaluate("""(() => { const b = document.querySelector('[data-testid=blank]'), l = document.querySelector('[data-testid=answer-line]'), y = document.querySelector('[data-testid=you-wrote]');
            return { state: document.querySelector('#app > .screen').dataset.state, blank: b.textContent, blankCls: b.className, line: l.textContent, you: y && y.textContent,
                     head: document.querySelector('[data-testid=result] .title').textContent, why: !!document.querySelector('[data-sheet=why]'),
                     underline: getComputedStyle(b).borderBottomColor, lineColor: getComputedStyle(l).color }; })()""")
    g = await fix('c-0061', 'gira')
    await ux_shot(page, '04-accepted-alternate-c-0061-gira.png')
    f = await page.evaluate(FOCUS_JS)
    check('focus: after typing an answer and tapping Check, Next shows no focus ring even though Chrome treats it as :focus-visible',
          f['act'] == 'next' and f['outline'] == 'none', f)
    await page.click('[data-act=next]')
    s_ = await fix('c-0055', 'sobre'); await page.click('[data-act=next]')
    main_ = await fix('c-0061', ' Dobla '); await page.click('[data-act=next]')
    dv = await fix('c-0061', 'da vuelta'); await page.click('[data-act=next]')
    # listen_type with a word alternate: money formatting of the main answer is kept ($90)
    await page.wait_for_selector('[data-card=c-0011][data-state=question]')
    await page.keyboard.type('noventa'); await page.click('[data-act=check]')
    await page.wait_for_selector('[data-state=correct]')
    lt = (await page.text_content('[data-testid=answer-line]')).strip()
    await page.click('[data-act=next]')
    wrong = await fix('c-0061', 'xyz'); await page.click('[data-act=next]')
    ok_green = 'rgb(76, 195, 138)'
    check('accepted alternate (c-0061 typed "gira"): the blank keeps "gira" (green, ok style), line is exactly “gira” works. Also common: “dobla”.; "Correct" heading and Why row stay',
          g['state'] == 'correct' and g['blank'] == 'gira' and 'ok' in g['blankCls'].split() and g['line'] == '“gira” works. Also common: “dobla”.'
          and g['head'] == 'Correct' and g['why'] and g['you'] is None and g['underline'] == ok_green, g)
    check('accepted alternate (c-0055 typed "sobre"): “sobre” works. Also common: “encima de”., blank keeps "sobre"',
          s_['blank'] == 'sobre' and s_['line'] == '“sobre” works. Also common: “encima de”.', s_)
    check('main answer typed (" Dobla ", normalised equal): unchanged display, blank "dobla", "The answer is “dobla”."',
          main_['state'] == 'correct' and main_['blank'] == 'dobla' and main_['line'] == 'The answer is “dobla”.', main_)
    check('content sync: c-0061 now also accepts "da vuelta" (Gabriel\'s change) and shows “da vuelta” works. Also common: “dobla”.',
          dv['state'] == 'correct' and dv['blank'] == 'da vuelta' and dv['line'] == '“da vuelta” works. Also common: “dobla”.', dv)
    check('accepted alternate on listen_type (c-0011 typed "noventa"): “noventa” works. Also common: $90. (money format kept)',
          lt == '“noventa” works. Also common: $90.', lt)
    check('wrong answer unchanged: "The answer is “dobla”." + "You wrote: xyz", blank shows the answer',
          wrong['state'] == 'wrong' and wrong['line'] == 'The answer is “dobla”.' and wrong['you'] == 'You wrote: xyz' and wrong['blank'] == 'dobla', wrong)
    await page.wait_for_selector('[data-screen=summary]')
    sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 10000)
    rows = fake.ok_rows()
    check('results: this session\'s rows carry replays (2 plays -> 1 on c-0010 and c-0018, 0 elsewhere) and skipped false',
          sent and [(r['card_id'], r['replays'], r['skipped']) for r in rows][:3] == [('c-0010', 1, False), ('c-0018', 1, False), ('c-0061', 0, False)]
          and all(r['replays'] == 0 for r in rows[2:]), [(r['card_id'], r['replays'], r['skipped']) for r in rows])
    # keyboard users still get the ring: Tab moves focus and the outline shows
    await page.keyboard.press('Tab'); await page.keyboard.press('Tab')
    f = await page.evaluate(FOCUS_JS)
    check('focus: keyboard (Tab) focus still shows the focus ring', f['fv'] and f['outline'] == 'solid' and f['width'] == '2px', f)
    await page.click('[data-act=done]'); await page.wait_for_selector('[data-screen=home]')

    # ---- Medium topic grid: stars pinned to the bottom, 12px+ under the name, all lined up ----
    await page.click('[data-act=medium]'); await page.wait_for_selector('[data-screen=topics]')
    await page.wait_for_timeout(200)
    tiles = await page.evaluate(TILES_JS)
    await ux_shot(page, '05-medium-topic-grid.png')
    two = [t for t in tiles if t['lines'] == 2]
    ok = (tiles and len({round(t['h'], 1) for t in tiles}) == 1 and len({round(t['starsTop'], 1) for t in tiles}) == 1 and len({round(t['fromBottom'], 1) for t in tiles}) == 1
          and all(t['gap'] >= 11.5 for t in tiles) and any(t['name'] in ('Directions & places', 'Future & conditional') for t in two))
    check('[390x844] topic grid: two-line names ("Future & conditional") keep >= 12px above the stars; stars pinned to the bottom and lined up on every tile', ok, tiles)
    for (w, h) in ((360, 640), (320, 640)):
        await page.set_viewport_size({'width': w, 'height': h}); await page.wait_for_timeout(150)
        tl = await page.evaluate(TILES_JS)
        okw = tl and len({round(t['h'], 1) for t in tl}) == 1 and len({round(t['starsTop'], 1) for t in tl}) == 1 and all(t['gap'] >= 11.5 for t in tl) and any(t['lines'] == 2 for t in tl)
        check(f'[{w}x{h}] topic grid: stars 12px+ below one- and two-line names, bottom-aligned, equal tile heights (grow if needed)', okw, tl)
    await page.set_viewport_size({'width': 390, 'height': 844})
    await page.click('[data-act=home]'); await page.wait_for_selector('[data-screen=home]')

    # ---- mission question before play (screenshot) ----
    await page.click('[data-act=hard]'); await page.click('[data-mission=m-metro-01]')
    await page.wait_for_selector('[data-screen=mission-intro]')
    await page.click('[data-act=begin]'); await page.wait_for_selector('[data-screen=mission-q][data-q=q1]')
    await page.wait_for_timeout(800)
    mq = await page.evaluate("(() => ({ paused: window.__oyeAudioEl.paused, time: document.querySelector('.pl-el').textContent }))()")
    await ux_shot(page, '06-mission-question-before-play.png')
    check('[390x844] mission question opens silent: player at 0:00, nothing playing', mq['paused'] and mq['time'] == '0:00', mq)
    check('ux-replays suite: no JS errors', not errors, errors[:3])
    await ctx.close()


# ---------------------------------------------------------------- drill-read: shuffled topics, Verb drill, mission Read view
SHOTS_DR = os.environ.get('OYE_DRILL_SHOTS') or os.path.join(SHOTS, 'drill-read')
TEAM_CARDS = os.path.join(os.path.dirname(APP), 'content', 'cards.json')
DRILL_FIXTURE = os.path.join(APP, 'tests', 'fixtures', 'drill_cards.json')


async def dr_shot(page, name):
    os.makedirs(SHOTS_DR, exist_ok=True)
    await page.screenshot(path=os.path.join(SHOTS_DR, name))


def drill_source():
    """Drill cards for the tests: the app's own content if it has them, else the team's content file
    (Gabriel's batch, not published yet), else the small fixture in tests/fixtures."""
    for path in (os.path.join(APP, 'content', 'cards.json'), TEAM_CARDS, DRILL_FIXTURE):
        try:
            d = [c for c in json.load(open(path, encoding='utf-8')) if c.get('level') == 'drill']
        except Exception:
            continue
        if d:
            return d, path
    return [], None


def make_drill_site(dest):
    """A copy of the app whose cards.json also has the drill cards (a content fixture; the real content is untouched)."""
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    copy_current(dest)
    drill, src = drill_source()
    cpath = os.path.join(dest, 'content', 'cards.json')
    cs = json.load(open(cpath, encoding='utf-8'))
    have = {c['id'] for c in cs}
    cs += [c for c in drill if c['id'] not in have]
    json.dump(cs, open(cpath, 'w', encoding='utf-8'), ensure_ascii=False)
    return cs, drill, src


DRILL_CARD_JS = """() => { const q = (s) => document.querySelector('#app ' + s), r = (e) => e && e.getBoundingClientRect();
  const lab = q('[data-testid=drill-label]'), sen = q('[data-testid=sentence]'), opts = [...document.querySelectorAll('#app [data-opt]')];
  const ls = lab && getComputedStyle(lab), ss = sen && getComputedStyle(sen);
  const cols = new Set(opts.map((o) => Math.round(r(o).left))).size, rows = new Set(opts.map((o) => Math.round(r(o).top))).size;
  return { card: q('.screen').dataset.card, count: q('.count').textContent, label: lab && lab.textContent, labFont: ls && [ls.fontSize, ls.fontWeight, ls.textTransform, ls.color],
           eyebrow: !!q('.card-inner > .eyebrow'), senFont: ss && [ss.fontSize, ss.fontWeight, ss.lineHeight], senLines: sen && Math.round(r(sen).height / parseFloat(ss.lineHeight)),
           opts: opts.map((o) => o.dataset.value), cols, rows, grid: !!q('.options.grid'), input: !!q('#fix-input'), audio: !!q('[data-testid=audio-row]'), replay: !!q('[data-testid=replay]'),
           hint: !!q('[data-act=hint]'), dontknow: !!q('[data-act=dontknow]'), docScroll: document.scrollingElement.scrollHeight - innerHeight }; }"""


async def drill_read_suite(browser, base, cards):
    errors = []
    by_id = {c['id']: c for c in cards}
    missions = json.load(open(os.path.join(APP, 'content', 'missions.json'), encoding='utf-8'))
    mby = {m['id']: m for m in missions}
    medium = [c for c in cards if c.get('level') == 'medium']

    # ================= 1. Shuffled Medium topics (real app content) =================
    ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844})
    await home_fresh(page, base)
    topic = 'numbers_prices'
    pool = {c['id'] for c in medium if c.get('topic') == topic}
    sessions = []
    for _ in range(6):
        await page.click('[data-act=medium]'); await page.wait_for_selector('[data-screen=topics]')
        await page.click(f'[data-topic={topic}]'); await page.wait_for_selector('[data-screen=card][data-state=question]')
        sessions.append(await page.evaluate("window.__oye.session.cards.map(c => ({ id: c.id, options: c.options || [] }))"))
        await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
    orders = {tuple(c['id'] for c in s) for s in sessions}
    # <= 10 cards: every card once per start; > 10 cards: 10 distinct cards from the topic (weighted pick, tested below)
    want_n = min(len(pool), 10)
    same_set = all(len(s) == want_n and len({c['id'] for c in s}) == want_n and
                   ({c['id'] for c in s} == pool if len(pool) <= 10 else {c['id'] for c in s} <= pool) for s in sessions)
    opt_cards = [(c, by_id[c['id']]) for s in sessions for c in s if by_id[c['id']].get('options')]
    opt_same_values = all(sorted(c['options']) == sorted(o['options']) for c, o in opt_cards)
    opt_moved = sum(1 for c, o in opt_cards if c['options'] != o['options'])
    check(f'shuffled topics: 6 starts of "{topic}" ({len(pool)} cards) each hold ' + ('every playable card once' if len(pool) <= 10 else f'{want_n} distinct cards of the topic') + f', in {len(orders)} different orders; '
          f'option order shuffled ({opt_moved} of {len(opt_cards)} option cards moved), same option values',
          same_set and len(orders) >= 3 and opt_cards and opt_same_values and opt_moved >= len(opt_cards) // 3, {'orders': len(orders), 'moved': opt_moved})
    # The check uses option values, not positions: find a start where an options card's answer moved, tap by value -> right;
    # tap the button at the answer's CONTENT position (now a different option) -> wrong.
    proof = None
    for attempt in range(12):
        await page.click('[data-act=medium]'); await page.wait_for_selector('[data-screen=topics]')
        await page.click(f'[data-topic={topic}]'); await page.wait_for_selector('[data-screen=card][data-state=question]')
        info = await page.evaluate("(() => { const s = window.__oye.session; return s.cards.map((c, i) => ({ i, id: c.id, options: c.options || [] })); })()")
        target = next((c for c in info if c['options'] and c['options'].index(by_id[c['id']]['answer']) != by_id[c['id']]['options'].index(by_id[c['id']]['answer'])), None)
        if not target:
            await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]'); continue
        # skip ahead to that card with "I don't know"
        for _ in range(target['i']):
            await page.click('[data-act=dontknow]') if await page.query_selector('[data-act=dontknow]') else None
            if not await page.query_selector('[data-act=next]'):
                if await page.query_selector('#fix-input'):
                    await page.fill('#fix-input', 'x'); await page.click('[data-act=check]')
                else:
                    await page.click('[data-key="1"]'); await page.click('[data-act=check]')
            await page.click('[data-act=next]'); await page.wait_for_selector('[data-screen=card][data-state=question]')
        card = by_id[target['id']]
        content_pos = card['options'].index(card['answer'])
        shown_at = await page.evaluate(f"document.querySelector('#app [data-opt=\"{content_pos}\"]').dataset.value")
        await click_option(page, card['answer'])
        await page.wait_for_selector('[data-state]:not([data-state=question])')
        st1 = (await current(page))['state']
        row = await page.evaluate("(() => { const r = window.__oye.session.results.at(-1); return { ok: r.ok, given: r.given }; })()")
        proof = {'card': card['id'], 'content_pos': content_pos, 'shown_at_that_pos': shown_at, 'state_by_value': st1, 'row': row}
        await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
        break
    check('shuffled topics: the answer check uses the option VALUE (right answer tapped at its new position = correct, recorded as the value)',
          proof and proof['state_by_value'] == 'correct' and proof['row'] == {'ok': True, 'given': by_id[proof['card']]['answer']} and proof['shown_at_that_pos'] != by_id[proof['card']]['answer'], proof)
    # Easy (Quick session) keeps its options in content order (different code path: planSession, no shuffle)
    easy_opts = []
    await home_fresh(page, base)
    for _ in range(3):
        await start_plan(page, ['c-0010', 'c-0018']); await page.wait_for_selector('[data-card=c-0010]')
        easy_opts.append(await page.evaluate("[...document.querySelectorAll('#app [data-opt]')].map(b => b.dataset.value)"))
        await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
    check('Easy options are NOT shuffled (unchanged behaviour): c-0010 shows its options in content order every time',
          all(o == by_id['c-0010']['options'] for o in easy_opts), easy_opts[:1])
    # > 10 playable cards: 10 picked at random, weighted toward missed / due cards (srs.planTopic on 25 cards)
    w = await page.evaluate("""import('./js/srs.js').then((srs) => {
        const cards = Array.from({ length: 25 }, (_, i) => ({ id: 'x' + i, options: ['a', 'b', 'c', 'd'], answer: 'a' }));
        const today = srs.todayKey(), cardsP = {};
        for (let i = 0; i < 5; i++) cardsP['x' + i] = { box: 1, seen: 3, right: 1, wrong: 2, due: '2000-01-01', lastOk: false };      // missed + due
        for (let i = 5; i < 10; i++) cardsP['x' + i] = { box: 3, seen: 3, right: 3, wrong: 0, due: '2000-01-01', lastOk: true };      // right, due
        for (let i = 10; i < 25; i++) cardsP['x' + i] = { box: 5, seen: 6, right: 6, wrong: 0, due: '2999-01-01', lastOk: true };     // right, not due
        const s = { cards: cardsP, days: {}, history: [] };
        const cnt = {}; let sizes = new Set(), dupes = 0, firsts = new Set();
        for (let k = 0; k < 400; k++) {
          const p = srs.planTopic(s, cards);
          sizes.add(p.length); if (new Set(p.map((c) => c.id)).size !== p.length) dupes++;
          firsts.add(p[0].id); p.forEach((c) => { cnt[c.id] = (cnt[c.id] || 0) + 1; });
        }
        const rate = (a, b) => { let t = 0; for (let i = a; i < b; i++) t += (cnt['x' + i] || 0); return t / (400 * (b - a)); };
        return { sizes: [...sizes], dupes, missed: rate(0, 5), due: rate(5, 10), rest: rate(10, 25), firsts: firsts.size };
    })""")
    check('shuffled topics: more than 10 cards -> 10 distinct picked at random, missed cards most likely, then due, then known (400 plans on 25 cards)',
          w['sizes'] == [10] and w['dupes'] == 0 and w['missed'] > w['due'] > w['rest'] and w['missed'] > 1.5 * w['rest'] and w['firsts'] >= 10, w)
    await ctx.close()

    # ================= 2. Verb drill (content fixture: the app plus the drill cards) =================
    site = os.path.join(TMP, 'drill-site')
    all_cards, drill, src = make_drill_site(site)
    dby = {c['id']: c for c in drill}
    check(f'drill fixture: {len(drill)} level "drill" cards from {os.path.relpath(src, os.path.dirname(APP)) if src else "-"} (fix_it, 3-4 options, answer among them, no audio, verbs_* topic)',
          drill and all(c['type'] == 'fix_it' and 3 <= len(c['options']) <= 4 and c['answer'] in c['options'] and not c.get('audio_text')
                        and c['topic'] in ('verbs_past', 'verbs_present', 'verbs_commands', 'verbs_future') for c in drill), len(drill))
    port = free_port()
    server = start_server(site, port)
    dbase = f'http://localhost:{port}/'
    try:
        # ---- 2a. drill cards never enter Easy or Medium ----
        ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844})
        await home_fresh(page, dbase)
        pools = await page.evaluate("""import('./js/levels.js').then((L) => { const S = window.__oye;
            return { easy: S.easy.map(c => c.id), medium: S.medium.map(c => c.id), drill: S.drill.map(c => c.id),
                     lv: [L.levelOf({ level: 'drill' }), L.isEasy({ level: 'drill' }), L.isDrill({ level: 'drill' }), L.levelOf({}), L.levelOf({ level: 'weird' })] }; })""")
        dids = set(dby)
        check('drill filter: levelOf("drill") = "drill" (not Easy); S.easy and S.medium hold no drill card; S.drill = every drill card',
              pools['lv'] == ['drill', False, True, 'easy', 'easy'] and not dids & set(pools['easy']) and not dids & set(pools['medium']) and set(pools['drill']) == dids, pools['lv'])
        today = time.strftime('%Y-%m-%d')
        lesson_tags = sorted({t for c in drill for t in (c.get('lesson_tags') or [])})
        plans, topic_sessions = [], []
        for scenario in ['drill-urgent', 'drill-lesson']:
            prog = {'cards': {cid: {'box': 1, 'seen': 4, 'right': 0, 'wrong': 4, 'due': '2000-01-01', 'last': today, 'lastOk': False} for cid in dids},
                    'days': {}, 'history': [], 'lesson': lesson_tags[0] if (scenario == 'drill-lesson' and lesson_tags) else 'all'}
            if scenario == 'drill-urgent':   # every Easy card known and not due, so the planner has to look further
                for c in all_cards:
                    if c.get('level', 'easy') == 'easy':
                        prog['cards'][c['id']] = {'box': 5, 'seen': 5, 'right': 5, 'wrong': 0, 'due': '2999-01-01', 'last': today, 'lastOk': True}
            await page.evaluate(f"localStorage.setItem('oye.progress.v1', {json.dumps(json.dumps(prog))})")
            for _ in range(6):
                await page.reload(); await page.wait_for_selector('html[data-ready="1"] [data-screen=home]')
                plans.append(await page.evaluate('window.__oye.plan.map(c => c.id)'))
            for t in ('verbs_past', 'verbs_present', 'verbs_commands', 'verbs_future'):
                if not await page.query_selector('[data-act=medium]:not([disabled])'): break
                await page.click('[data-act=medium]'); await page.wait_for_selector('[data-screen=topics]')
                if await page.query_selector(f'[data-topic={t}]'):
                    await page.click(f'[data-topic={t}]'); await page.wait_for_selector('[data-screen=card]')
                    topic_sessions.append(await page.evaluate('window.__oye.session.cards.map(c => c.id)'))
                    await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
                else:
                    await page.click('[data-act=home]'); await page.wait_for_selector('[data-screen=home]')
        leaked = sorted({i for pl in plans + topic_sessions for i in pl if i in dids})
        lessons_txt = ''
        await page.click('[data-act=lessons]'); await page.wait_for_selector('.sheet')
        lessons_total = await page.evaluate("document.querySelector('[data-lesson=all] .caption').textContent")
        await page.evaluate("document.querySelector('.sheet-backdrop').remove()")
        n_easy = len([c for c in all_cards if c.get('level', 'easy') == 'easy'])
        check(f'drill filter: no drill card in {len(plans)} Quick-session plans (drill cards all missed + overdue, Easy all known, drill lesson picked) '
              f'or {len(topic_sessions)} verbs_* topic sessions; the lessons sheet counts Easy cards only',
              not leaked and len(plans) == 12 and len(topic_sessions) >= 4 and lessons_total == f'{len(pools["easy"])} cards', {'leaked': leaked[:5], 'lessons': lessons_total})
        await page.evaluate("localStorage.clear()")
        await ctx.close()

        # ---- 2b. home: Verb drill row under the Medium and Hard cards ----
        ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844}, endpoint=(fake := FakeEndpoint('ok')))
        await home_fresh(page, dbase)
        g = await page.evaluate("""() => { const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, h: b.height, left: b.left, right: b.right}; };
            const d = document.querySelector('[data-testid=drill-row]');
            return { med: r('[data-testid=challenge-medium]'), hard: r('[data-testid=challenge-hard]'), drill: r('[data-testid=drill-row]'), start: r('[data-act=start]'), meta: r('.meta-line'),
                     text: d && [d.querySelector('.ch-title').textContent, d.querySelector('.caption').textContent].join(' | '), chev: !!(d && d.querySelector('.ch-chev svg')), bg: d && getComputedStyle(d).backgroundColor, radius: d && getComputedStyle(d).borderRadius,
                     docScroll: document.scrollingElement.scrollHeight - innerHeight }; }""")
        n20 = min(20, len(drill))
        check(f'[390x844] home: slim "Verb drill" row right under the Hard card ("{n20} quick questions · no audio", chevron, surface card), above the EASY line and Quick session, no scrolling',
              g['drill'] and g['hard']['bottom'] <= g['drill']['top'] <= g['hard']['bottom'] + 8.5 and g['drill']['h'] < g['hard']['h'] and g['drill']['bottom'] <= g['meta']['top']
              and g['text'] == f'Verb drill | {n20} quick questions · no audio' and g['chev'] and g['radius'] == '12px' and g['docScroll'] <= 1, g)
        await page.wait_for_timeout(300)
        await dr_shot(page, '01-home-verb-drill.png')
        for (w_, h_) in ((360, 640), (375, 667), (360, 720)):
            await page.set_viewport_size({'width': w_, 'height': h_}); await page.wait_for_timeout(150)
            gs = await page.evaluate("() => { const s = document.querySelector('[data-act=start]').getBoundingClientRect(), d = document.querySelector('[data-testid=drill-row]').getBoundingClientRect(); return { docScroll: document.scrollingElement.scrollHeight - innerHeight, start: s.bottom, drill: d.bottom, vh: innerHeight }; }")
            check(f'[{w_}x{h_}] home with Verb drill still fits (no scrolling, Quick session pinned)', gs['docScroll'] <= 1 and gs['start'] <= h_ - 15.5 and gs['drill'] < gs['start'], gs)
        await page.set_viewport_size({'width': 390, 'height': 844})

        # ---- 2c. a drill session ----
        await page.click('[data-act=drill]'); await page.wait_for_selector('[data-screen=card][data-state=question]')
        await page.wait_for_timeout(200)
        c1 = await page.evaluate(DRILL_CARD_JS)
        card = dby.get(c1['card'])
        check(f'drill card: "1 of {n20}"; prompt_en as the small label above the sentence (13px/500, normal case, gray-600, no uppercase eyebrow); sentence in title style; '
              'one tap per answer: options in a 2 x 2 grid (4) or stacked (3), no text field, no audio, Hint + "I don\'t know"',
              card and c1['count'] == f'1 of {n20}' and c1['label'] == card['prompt_en'] and c1['labFont'] == ['13px', '500', 'none', 'rgb(155, 155, 163)'] and not c1['eyebrow']
              and c1['senFont'] == ['24px', '600', '32px'] and sorted(c1['opts']) == sorted(card['options'])
              and ((len(card['options']) == 4 and c1['grid'] and c1['cols'] == 2 and c1['rows'] == 2) or (len(card['options']) == 3 and not c1['grid'] and c1['cols'] == 1))
              and not c1['input'] and not c1['audio'] and not c1['replay'] and c1['hint'] == bool(card.get('hint_en')) and c1['dontknow'] and c1['docScroll'] <= 1, c1)
        await dr_shot(page, '02-drill-card.png')
        if c1['hint']:
            await page.click('[data-act=hint]')
            ht = await page.text_content('[data-testid=hint-box]')
            check('drill card: hint_en behind the same Hint button as other cards', card['hint_en'].split('/')[0].strip()[:20] in ht, ht)
        # wrong answer first (feedback screenshot), then right answers, one "I don't know"
        wrong = next(o for o in c1['opts'] if o != card['answer'])
        await click_option(page, wrong)
        await page.wait_for_selector('[data-state=wrong]')
        fb = await page.evaluate("""() => { const q = (s) => document.querySelector('#app ' + s); const why = q('[data-testid=drill-why]');
            const opt = (v) => [...document.querySelectorAll('#app [data-opt]')].find(b => b.dataset.value === v);
            return { blank: q('[data-testid=blank]').textContent.trim(), result: q('[data-testid=result]').textContent.trim(), why: why && why.textContent,
                     whyColor: why && getComputedStyle(why).color, whyLines: why && Math.round(why.getBoundingClientRect().height / parseFloat(getComputedStyle(why).lineHeight)),
                     next: !!q('[data-act=next]'), focus: document.activeElement && document.activeElement.dataset.act }; }""")
        cls = await page.evaluate(f"(() => {{ const m = {{}}; document.querySelectorAll('#app [data-opt]').forEach(b => m[b.dataset.value] = b.className); return m; }})()")
        check('drill feedback (wrong): right form in the blank, "Not quite", the one-sentence why in gray-600, your pick red, right answer green, Next',
              fb['blank'] == card['answer'] and fb['result'] == 'Not quite' and fb['why'] == card['explanation_en'] and fb['whyColor'] == 'rgb(155, 155, 163)' and fb['whyLines'] <= 3
              and 'is-wrong' in cls[wrong] and 'is-correct' in cls[card['answer']] and fb['next'] and fb['focus'] == 'next', {'fb': fb, 'cls': cls})
        await dr_shot(page, '03-drill-feedback.png')
        seen = [card['id']]
        skipped_id = None
        opt_orders_moved = int(c1['opts'] != card['options'])
        for i in range(1, n20):
            await page.click('[data-act=next]'); await page.wait_for_selector('[data-screen=card][data-state=question]')
            cur = await current(page)
            c = dby[cur['card']]; seen.append(c['id'])
            shown = await page.evaluate("[...document.querySelectorAll('#app [data-opt]')].map(b => b.dataset.value)")
            opt_orders_moved += int(shown != c['options'])
            if i == 3:
                skipped_id = c['id']; await page.click('[data-act=dontknow]')
            else:
                await click_option(page, c['answer'])
            await page.wait_for_selector('[data-state=correct], [data-state=wrong]')
            if i == 1:
                st = await page.evaluate("(() => ({ state: document.querySelector('#app .screen').dataset.state, result: document.querySelector('#app [data-testid=result]').textContent.trim(), blank: document.querySelector('#app [data-testid=blank]').className }))()")
                check('drill feedback (right): "Correct", the right form in a green blank, Next (same flow as other option cards)', st['state'] == 'correct' and st['result'] == 'Correct' and 'ok' in st['blank'], st)
        check(f'drill session: {n20} cards in a row, all level "drill", no repeats; options shuffled ({opt_orders_moved} of {n20} not in content order)',
              len(seen) == n20 and len(set(seen)) == n20 and all(s in dby for s in seen) and opt_orders_moved >= n20 // 4, opt_orders_moved)
        await page.click('[data-act=next]')
        await page.wait_for_selector('[data-screen=summary][data-level=drill]')
        sent = await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 8000)
        rows = fake.ok_rows()
        rows_ok = sent and len(rows) == n20 and [r['card_id'] for r in rows] == seen and all(
            r['level'] == 'drill' and r['topic'] == dby[r['card_id']]['topic'] and r['replays'] == 0 and r['mission_id'] == '' and r['stars'] == ''
            and r['used_slow'] is False and r['skipped'] is (r['card_id'] == skipped_id) for r in rows)
        check('drill results rows: level "drill", topic = the card\'s own verbs_* topic, replays 0, no stars (blank), skipped only on the "I don\'t know" card',
              rows_ok and rows[0]['correct'] is False and rows[0]['answer_given'] == wrong, rows[:2])
        sm = await page.evaluate("""() => { const q = (s) => document.querySelector('#app ' + s);
            return { score: q('[data-testid=score]').textContent, misses: [...document.querySelectorAll('#app [data-testid=drill-miss]')].map(m => ({ id: m.dataset.card,
                       given: m.querySelector('.dm-given').textContent, deco: getComputedStyle(m.querySelector('.dm-given')).textDecorationLine, gc: getComputedStyle(m.querySelector('.dm-given')).color,
                       right: m.querySelector('.dm-right').textContent, rc: getComputedStyle(m.querySelector('.dm-right')).color })),
                     another: q('[data-act=another-drill]') && q('[data-act=another-drill]').textContent, done: !!q('[data-act=done]'), stars: !!q('.stars') }; }""")
        m0 = sm['misses'][0] if sm['misses'] else {}
        check(f'drill end screen: "{n20 - 2} of {n20} right", missed pairs side by side (your form struck through in red, right form green; skipped shown as "skipped"), '
              f'primary "Another {n20}" + Done, no stars',
              sm['score'] == f'{n20 - 2} of {n20} right' and len(sm['misses']) == 2 and m0.get('given') == wrong and m0.get('deco') == 'line-through' and m0.get('gc') == 'rgb(240, 122, 106)'
              and m0.get('right') == card['answer'] and m0.get('rc') == 'rgb(76, 195, 138)' and sm['misses'][1]['given'] == 'skipped' and sm['another'] == f'Another {n20}' and sm['done'] and not sm['stars'], sm)
        await dr_shot(page, '08-drill-end-screen.png')
        # missed cards are weighted up: the two just missed come back far more often than the average drill card
        wr = await page.evaluate(f"""import('./js/srs.js').then((srs) => {{ const S = window.__oye; const p = srs.load(); const miss = {json.dumps([card['id'], skipped_id])};
            let hit = 0, all = 0; for (let k = 0; k < 300; k++) {{ const pl = srs.planDrill(p, S.drill); hit += pl.filter(c => miss.includes(c.id)).length; all += pl.length; }}
            return {{ missRate: hit / (300 * miss.length), avg: all / (300 * S.drill.length) }}; }})""")
        check('drill weighting: cards missed in the last drill are much more likely in the next one (300 plans)', wr['missRate'] > 1.5 * wr['avg'], wr)
        await page.click('[data-act=another-drill]')
        await page.wait_for_selector('[data-screen=card][data-state=question]')
        again = await page.evaluate("({ count: document.querySelector('#app .count').textContent, level: window.__oye.session.level })")
        check(f'drill end screen: "Another {n20}" starts a new drill', again == {'count': f'1 of {n20}', 'level': 'drill'}, again)
        await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
        tot = (await page.text_content('[data-testid=stars-total]')).strip()
        check('drill: no stars (home total unchanged at 0)', tot == '0', tot)
        # every drill card: sentence max 3 lines (title style), options short enough for the 2 x 2 grid; the explanation
        # should be max 2 lines (Picasso) - cards over that are listed as a content NOTE, and the longest one must still fit.
        fit = await page.evaluate("""(cards) => { const box = document.createElement('div'); box.className = 'card-inner'; box.style.cssText = 'position:absolute;left:0;top:0;visibility:hidden';
            document.getElementById('app').appendChild(box); const out = {};
            const lines = (e) => Math.round(e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight));
            for (const w of [342, 312]) {
              box.style.width = w + 'px'; const bad = { sentence: [], why: [], opt: [] };
              for (const c of cards) {
                box.innerHTML = `<p class="sentence">${c.sentence.replace(/_{2,}/, '<span class="blank">' + c.answer + '</span>')}</p><p class="drill-why">${c.explanation_en || ''}</p>`;
                if (lines(box.children[0]) > 3) bad.sentence.push(c.id); if (lines(box.children[1]) > 2) bad.why.push(c.id);
                if (!c.options.every((o) => o.length <= 14)) bad.opt.push(c.id);
              }
              out[w] = bad;
            }
            box.remove(); return out; }""", drill)
        check(f'drill layout (all {len(drill)} drill cards, 390 wide): sentence max 3 lines (title style), options short enough for the 2 x 2 grid',
              not fit['342']['sentence'] and not fit['342']['opt'] and not fit['312']['opt'], fit)
        if fit['342']['why'] or fit['312']['why'] or fit['312']['sentence']:
            print(f"NOTE  drill content over the line limits: explanation > 2 lines at 390 wide {fit['342']['why']}, at 360 wide {fit['312']['why']}; "
                  f"sentence > 3 lines at 360 wide {fit['312']['sentence']}", flush=True)
        longest = max(drill, key=lambda c: len(c.get('explanation_en') or '') + len(c['sentence']))
        await page.evaluate(f"(() => {{ const S = window.__oye; S.drill = [S.drill.find(c => c.id === {json.dumps(longest['id'])})]; }})()")
        await page.click('[data-act=drill]'); await page.wait_for_selector(f'[data-card={longest["id"]}]')
        await click_option(page, next(o for o in longest['options'] if o != longest['answer']))
        await page.wait_for_selector('[data-state=wrong]')
        lf = await page.evaluate("(() => { const sc = document.querySelector('#app .card-zone .scroller'), n = document.querySelector('#app [data-act=next]').getBoundingClientRect(); return { overflow: sc.scrollHeight - sc.clientHeight, next: n.bottom, vh: innerHeight, doc: document.scrollingElement.scrollHeight - innerHeight }; })()")
        check(f'[390x844] drill feedback for the longest sentence + explanation ({longest["id"]}) fits: nothing scrolls, Next on screen', lf['overflow'] <= 1 and lf['doc'] <= 1 and lf['next'] <= lf['vh'], lf)
        await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
        # a 3-option drill card (allowed by the format; the fixture has some) is stacked full width
        three = next(c for c in json.load(open(DRILL_FIXTURE, encoding='utf-8')) if len(c['options']) == 3)
        await page.evaluate(f"(() => {{ window.__oye.drill = [{json.dumps(three, ensure_ascii=False)}]; }})()")
        await page.click('[data-act=drill]'); await page.wait_for_selector(f'[data-card={three["id"]}]')
        c3 = await page.evaluate(DRILL_CARD_JS)
        check('drill card with 3 options: stacked full width (1 column, 3 rows), one tap per answer',
              not c3['grid'] and c3['cols'] == 1 and c3['rows'] == 3 and sorted(c3['opts']) == sorted(three['options']) and not c3['input'], c3)
        await click_option(page, three['answer']); await page.wait_for_selector('[data-state=correct]')
        await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
        check('drill suite: no JS errors', not errors, errors[:3])
        await ctx.close()
    finally:
        server.terminate(); server.wait()
        shutil.rmtree(site, ignore_errors=True)

    # the published app content: the Verb drill row shows exactly when the content has drill cards
    want_drill = len([c for c in cards if c.get('level') == 'drill'])
    ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844})
    await home_fresh(page, base)
    n_drill = await page.evaluate("window.__oye.drill.length")
    has_row = bool(await page.query_selector('[data-testid=drill-row]'))
    check(f'home on the current content ({want_drill} drill cards): Verb drill row ' + ('shown' if want_drill else 'hidden'),
          n_drill == want_drill and has_row == bool(want_drill), {'drill': n_drill, 'row': has_row})
    await ctx.close()
    # content without drill cards: no Verb drill row
    site = os.path.join(TMP, 'nodrill-site')
    shutil.rmtree(site, ignore_errors=True); os.makedirs(site)
    copy_current(site)
    cpath = os.path.join(site, 'content', 'cards.json')
    json.dump([c for c in json.load(open(cpath, encoding='utf-8')) if c.get('level') != 'drill'], open(cpath, 'w', encoding='utf-8'), ensure_ascii=False)
    port = free_port()
    server = start_server(site, port)
    try:
        ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844})
        await home_fresh(page, f'http://localhost:{port}/')
        n_drill = await page.evaluate("window.__oye.drill.length")
        check('home without drill cards: no Verb drill row', not await page.query_selector('[data-testid=drill-row]') and n_drill == 0, n_drill)
        await ctx.close()
    finally:
        server.terminate(); server.wait()
        shutil.rmtree(site, ignore_errors=True)

    # ================= 3. Mission Read view (real app content) =================
    fake = FakeEndpoint('ok')
    ctx, page = await new_page(browser, errors, viewport={'width': 390, 'height': 844}, endpoint=fake)
    await home_fresh(page, base)
    await page.click('[data-act=hard]'); await page.wait_for_selector('[data-screen=missions]')
    check('missions list: unfinished missions show no Read link', not await page.query_selector('[data-testid=read-link]'))
    await page.evaluate("localStorage.setItem('oye.stars.v1', JSON.stringify({ topics: {}, missions: { 'm-metro-01': 2, 'm-landlord-whatsapp-01': 1 }, easy: 0, log: [] }))")
    await home_fresh(page, base)
    await page.click('[data-act=hard]'); await page.wait_for_selector('[data-screen=missions]')
    ml = await page.evaluate("""() => [...document.querySelectorAll('[data-testid=mission-row]')].map((row) => {
        const r = (e) => { const b = e.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, w: b.width, h: b.height }; };
        const link = row.querySelector('[data-testid=read-link]'), st = row.querySelector('.stars'), text = row.querySelector('.ch-text');
        if (!link) return { id: row.dataset.mission, link: false };
        const cs = getComputedStyle(link), lr = r(link), sr = r(st), rr = r(row), tr = r(text);
        const label = document.createRange(); label.selectNodeContents(link); const lb = label.getBoundingClientRect();
        return { id: row.dataset.mission, link: true, text: link.textContent, color: cs.color, font: [cs.fontSize, cs.fontWeight], hit: [lr.w, lr.h],
                 inRow: lr.left >= rr.left && lr.right <= rr.right && lr.top >= rr.top - 0.5 && lr.bottom <= rr.bottom + 0.5,
                 sameLine: Math.abs((lb.top + lb.bottom) / 2 - (sr.top + sr.bottom) / 2) <= 4, rightAligned: Math.abs(lb.right - tr.right) <= 1, oppositeStars: lb.left > sr.right + 100,
                 topAtCenter: document.elementFromPoint(lr.left + lr.w / 2, lr.top + lr.h / 2) === link }; })""")
    fin = {r['id']: r for r in ml}
    ok_links = (not fin['m-voicemail-clinic-01']['link'] and all(fin[i]['link'] and fin[i]['text'] == 'Read' and fin[i]['color'] == 'rgb(110, 139, 255)' and fin[i]['font'] == ['13px', '500']
                and fin[i]['hit'][0] >= 44 and fin[i]['hit'][1] >= 44 and fin[i]['inRow'] and fin[i]['sameLine'] and fin[i]['rightAligned'] and fin[i]['oppositeStars'] and fin[i]['topAtCenter']
                for i in ('m-metro-01', 'm-landlord-whatsapp-01')))
    check('missions list: finished missions show "Read" on the bottom line, right-aligned opposite the stars, accent blue capMed (13/500), 44x44+ tap area inside the card; unfinished: none', ok_links, ml)
    await dr_shot(page, '04-missions-list-read.png')
    # tapping the card anywhere else still opens the mission
    tbox = await (await page.query_selector('[data-mission=m-metro-01] .ch-title')).bounding_box()
    await page.mouse.click(tbox['x'] + 20, tbox['y'] + tbox['height'] / 2)
    await page.wait_for_selector('[data-screen=mission-intro][data-mission=m-metro-01]')
    await page.go_back(); await page.wait_for_selector('[data-screen=missions]')
    box = await (await page.query_selector('[data-mission=m-metro-01] .stars')).bounding_box()
    await page.mouse.click(box['x'] + 4, box['y'] + 4)
    await page.wait_for_selector('[data-screen=mission-intro][data-mission=m-metro-01]')
    check('missions list: a tap on the title or the stars (anywhere but Read) still opens the mission', True)
    await page.go_back(); await page.wait_for_selector('[data-screen=missions]')
    queue0 = await page.evaluate("localStorage.getItem('oye.resultsQueue.v1') || '[]'")
    stars0 = await page.evaluate("localStorage.getItem('oye.stars.v1')")
    calls0 = len(fake.calls)
    # ---- Read view, audio mission ----
    await page.click('[data-mission=m-metro-01] [data-testid=read-link]')
    await page.wait_for_selector('[data-screen=mission-read][data-mission=m-metro-01]')
    await page.wait_for_timeout(1000)
    READ_JS = """() => { const q = (s) => document.querySelector('#app ' + s), a = window.__oyeAudioEl, pl = q('[data-testid=player]'), zone = q('.rd-zone');
      return { header: q('header').textContent.trim(), headerCount: !!q('header .count'), eyebrow: q('.lv-tag').textContent, from: q('.screen').dataset.from, back: q('[data-testid=read-back]').textContent.trim(), player: !!pl, playerAbove: pl ? pl.getBoundingClientRect().bottom <= zone.getBoundingClientRect().top : null,
               time: q('.pl-el') && q('.pl-el').textContent, paused: a.paused, clip: a.dataset.clip || '', label: q('[data-act=pl-play]') && q('[data-act=pl-play]').getAttribute('aria-label'),
               lines: [...document.querySelectorAll('#app .dline')].map((d) => ({ spk: d.querySelector('.spk').textContent, voice: d.dataset.voice, color: getComputedStyle(d.querySelector('.spk')).color,
                  es: d.querySelector('.es').textContent, en: d.querySelector('.en') && d.querySelector('.en').textContent, enColor: d.querySelector('.en') && getComputedStyle(d.querySelector('.en')).color })),
               questions: !!document.querySelector('#app [data-opt], #app .mopt, #app [data-act=check], #app .stars'),
               bubble: q('[data-testid=bubble]') && q('[data-testid=bubble]').childNodes[0].textContent, en: q('[data-testid=read-en]') && q('[data-testid=read-en]').textContent }; }"""
    rd = await page.evaluate(READ_JS)
    m = mby['m-metro-01']
    want = [(l['speaker'], l['voice'], VOICE_COLOR[l['voice']], l['es'], l['en'], 'rgb(155, 155, 163)') for l in m['media']['lines']]
    got = [(l['spk'], l['voice'], l['color'], l['es'], l['en'], l['enColor']) for l in rd['lines']]
    check('Read view (audio): player bar at the top at 0:00, nothing playing (no autoplay), the full script with speaker labels in the voice colour and each line\'s English under it in gray-600; no questions or stars; Back = "Missions"',
          rd['from'] == 'missions' and rd['back'] == 'Missions' and rd['player'] and rd['playerAbove'] and rd['time'] == '0:00' and rd['paused'] and rd['clip'] == '' and rd['label'] == 'Play'
          and got == want and not rd['questions'], {'rd': {k: v for k, v in rd.items() if k != 'lines'}, 'first': got[:1]})
    check('Read view (audio): no Script/Message header labels; Hard · Script line remains',
          rd['header'] == 'Missions' and not rd['headerCount'] and rd['eyebrow'] == 'Hard · Script', rd['header'])
    await dr_shot(page, '05-read-view-audio.png')
    await page.click('[data-act=pl-play]'); await page.wait_for_timeout(700)
    playing = await page.evaluate("(() => { const a = window.__oyeAudioEl; return { playing: !a.paused, clip: a.dataset.clip, label: document.querySelector('[data-act=pl-play]').getAttribute('aria-label') }; })()")
    await page.click('[data-act=pl-play]'); await page.wait_for_timeout(200)
    stopped = await page.evaluate("window.__oyeAudioEl.paused")
    check('Read view: the player plays the mission clip on a tap and a second tap stops it', playing == {'playing': True, 'clip': 'm-metro-01', 'label': 'Pause'} and stopped, playing)
    await page.click('[data-act=pl-play]'); await page.wait_for_timeout(300)
    await page.click('[data-act=read-back]'); await page.wait_for_selector('[data-screen=missions]')
    after = await page.evaluate("(() => ({ paused: window.__oyeAudioEl.paused, queue: localStorage.getItem('oye.resultsQueue.v1') || '[]', stars: localStorage.getItem('oye.stars.v1') }))()")
    check('Read view: Back returns to the missions list and stops the audio; nothing written to results, stars unchanged',
          after['paused'] and after['queue'] == queue0 and after['stars'] == stars0 and len(fake.calls) == calls0, after)
    # ---- Read view, message mission ----
    await page.click('[data-mission=m-landlord-whatsapp-01] [data-testid=read-link]')
    await page.wait_for_selector('[data-screen=mission-read][data-mission=m-landlord-whatsapp-01]')
    rm = await page.evaluate(READ_JS)
    lm = mby['m-landlord-whatsapp-01']['media']
    check('Read view (message): the message text and its English, no player, no questions',
          not rm['player'] and rm['bubble'] == lm['text_es'] and rm['en'] == lm['text_en'] and not rm['questions'], {k: rm[k] for k in ('player', 'questions')})
    check('Read view (message): no Script/Message header labels; Hard · The message line remains',
          rm['header'] == 'Missions' and not rm['headerCount'] and rm['eyebrow'] == 'Hard · The message', rm['header'])
    await dr_shot(page, '06-read-view-message.png')
    await page.go_back(); await page.wait_for_selector('[data-screen=missions]')
    # ---- intro of a finished mission: "Read script" under "Start mission"; the Read view's plays never count ----
    await page.click('[data-mission=m-metro-01]'); await page.wait_for_selector('[data-screen=mission-intro]')
    ib = await page.evaluate("(() => { const r = (s) => document.querySelector(s)?.getBoundingClientRect(); const a = r('[data-act=begin]'), b = r('[data-testid=read-script]'); return b ? { below: b.top >= a.bottom - 0.5, text: document.querySelector('[data-testid=read-script]').textContent, cls: document.querySelector('[data-testid=read-script]').className } : null; })()")
    check('finished mission intro: a "Read script" text button under "Start mission"', ib and ib['below'] and ib['text'] == 'Read script' and 'btn-text' in ib['cls'], ib)
    await page.click('[data-testid=read-script]'); await page.wait_for_selector('[data-screen=mission-read][data-from=intro]')
    back_lbl = (await page.text_content('[data-testid=read-back]')).strip()
    await page.click('[data-act=pl-play]'); await page.wait_for_timeout(500)
    await page.click('[data-act=read-back]'); await page.wait_for_selector('[data-screen=mission-intro][data-mission=m-metro-01]')
    await page.click('[data-act=begin]'); await page.wait_for_selector('[data-screen=mission-q][data-q=q1]')
    await page.wait_for_timeout(400)
    q1 = await page.evaluate("(() => ({ plays: window.__oye.mission.plays, time: document.querySelector('.pl-el').textContent, paused: window.__oyeAudioEl.paused }))()")
    check('Read view from the intro: Back ("Mission") returns to the intro; plays in the Read view do not count toward question 1 (player back at 0:00, plays 0)',
          back_lbl == 'Mission' and q1 == {'plays': 0, 'time': '0:00', 'paused': True}, {'back': back_lbl, 'q1': q1})
    await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
    # unfinished mission intro: no Read script
    await page.click('[data-act=hard]'); await page.click('[data-mission=m-voicemail-clinic-01]'); await page.wait_for_selector('[data-screen=mission-intro]')
    check('unfinished mission intro: no "Read script"', not await page.query_selector('[data-testid=read-script]'))
    await page.click('[data-act=close]'); await page.wait_for_selector('[data-screen=home]')
    # ---- result screen: "Read the script" row opens the Read view ----
    await page.click('[data-act=hard]'); await page.click('[data-mission=m-landlord-whatsapp-01]'); await page.wait_for_selector('[data-screen=mission-intro]')
    await page.click('[data-act=begin]'); await page.wait_for_selector('[data-screen=mission-msg]')
    await page.click('[data-act=to-questions]')
    for q in mby['m-landlord-whatsapp-01']['questions']:
        await page.wait_for_selector(f'[data-screen=mission-q][data-q={q["id"]}]')
        if q['kind'] == 'pick':
            await page.click(f'[data-opt="{q["options"].index(q["answer"])}"]'); await page.click('[data-act=check]')
        elif await page.query_selector('#fix-input'):
            await page.fill('#fix-input', q['answer']); await page.click('[data-act=check]')
        else:
            for ch in q['answer']:
                await page.click(f'[data-key="{ch}"]')
            await page.click('[data-act=check]')
    await page.wait_for_selector('[data-screen=mission-result]')
    await wait_for(page, "document.querySelector('[data-testid=results-status]')?.dataset.state === 'sent'", 8000)
    n_rows = len(fake.ok_rows())
    score = await page.text_content('[data-testid=score]')
    check('mission result: row reads "Read the script"; no "Read script" button',
          await page.text_content('[data-testid=transcript-row]') == 'Read the script'
          and not await page.query_selector('[data-testid=read-script]')
          and await page.get_by_role('button', name='Read script', exact=True).count() == 0)
    await page.wait_for_timeout(200)
    await dr_shot(page, '07-mission-result-read-script.png')
    await page.get_by_role('button', name='Read the script', exact=True).click()
    await page.wait_for_selector('[data-screen=mission-read][data-from=result]')
    rl = (await page.text_content('[data-testid=read-back]')).strip()
    await page.click('[data-act=read-back]'); await page.wait_for_selector('[data-screen=mission-result]')
    await page.wait_for_timeout(500)
    score2 = await page.text_content('[data-testid=score]')
    check('Read view from the result: Back ("Results") returns to the same result; no extra results rows sent',
          rl == 'Results' and score2 == score and len(fake.ok_rows()) == n_rows, {'back': rl, 'rows': (n_rows, len(fake.ok_rows()))})
    check('drill-read mission suite: no JS errors', not [e for e in errors if 'net::' not in e], errors[:3])
    await ctx.close()


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--live':
        sys.exit(asyncio.run(live_check(sys.argv[2].rstrip('/') + '/')))
    sys.exit(asyncio.run(main()))
