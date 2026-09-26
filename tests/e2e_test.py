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


async def new_page(browser, errors, viewport=None, init_script=None):
    ctx = await browser.new_context(viewport=viewport or VIEWPORT, device_scale_factor=1, is_mobile=True, has_touch=True,
                                    locale='en-GB', timezone_id='America/Mexico_City')
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
    audio_files = sorted({f for e in audio_idx['items'].values() for f in e.values()})

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
        hint_used_ids = set()
        counts = {}
        autoplayed, listening = 0, 0
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
            counts[t] = counts.get(t, 0) + 1
            k = counts[t]
            # autoplay
            if card.get('audio_text'):
                listening += 1
                await page.wait_for_timeout(900)
                played = await page.evaluate('(() => { const a = window.__oyeAudioEl; return a.currentTime > 0 || !a.paused; })()')
                autoplayed += 1 if played else 0
                await wait_audio_idle(page)
            # Outcome plan: listen_pick #1 wrong, #2 correct; scene #2, fix_it #2 wrong; everything else correct.
            correct = not ((t == 'listen_pick' and k == 1) or (t in ('scene_question', 'fix_it') and k == 2))
            if t == 'listen_pick' and k == 1 and slow_checked is None:
                normal_dur = await page.evaluate('window.__oyeAudioEl.duration')
                await page.click('[data-play=slow]')
                await page.wait_for_timeout(700)
                slow = await page.evaluate('(() => { const a = window.__oyeAudioEl; return {t: a.currentTime, rate: a.playbackRate, d: a.duration}; })()')
                slow_checked = slow['t'] > 0 and slow['d'] > normal_dur
                check('slow replay plays the slower es-MX file', slow_checked, f"normal {normal_dur:.2f}s, slow {slow['d']:.2f}s")
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
        check('autoplay on listening cards', autoplayed == listening, f'{autoplayed}/{listening}')
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
        today = (await page.text_content('.stats .col:nth-of-type(3) .value, .stats .col:last-child .value')).strip()
        check(f'home cards today = {len(seen_types)}', today.startswith(str(len(seen_types))), today)
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
                await page.wait_for_timeout(1200)
                played = await page.evaluate('(() => { const a = window.__oyeAudioEl; return a.currentTime > 0 || !a.paused; })()')
                check('offline: card audio plays from the cache', played)
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
        server4.terminate(); server4.wait()

        # ---------- v1.1: service worker update path ----------
        os.makedirs(TMP, exist_ok=True)
        await update_path_test(browser)
        shutil.rmtree(TMP, ignore_errors=True)
        await browser.close()

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
        if (card.get('audio_text') or card.get('explanation_en')) and not row:
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
                if scene:  # colours follow the scene's speaker order: first speaker accent blue, second correct green
                    order = list(dict.fromkeys(d['speaker'] for d in scene['dialogue']))
                    want = {order[0].upper(): 'rgb(110, 139, 255)', order[1].upper(): 'rgb(76, 195, 138)'}
                    bad = [(n, col) for n, col in sg['spkPairs'] if n.upper() in want and want[n.upper()] != col]
                    if bad: sp.append(f'speaker colours {bad[:2]}')
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
        ok = await wait_for(page, "document.documentElement.dataset.shell === '1.1' && document.documentElement.dataset.ready === '1' && !!document.querySelector('[data-screen=home]')", 25000)
        info = await page.evaluate("""async () => ({ keys: await caches.keys(), cache: (await (await fetch('sw.js', {cache: 'no-store'})).text()).match(/CACHE_VERSION = '([^']+)'/)[1],
            ver: window.__oye && window.__oye.content.version.version, hints: window.__oye && window.__oye.content.cards.filter(c => c.hint_en).length })""")
        check('update test: on the next open the new SW activates and the page switches itself to the v1.1 shell (no reinstall)',
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
        check('live: page runs the v1.1 shell', await page.evaluate("document.documentElement.dataset.shell") == '1.1')
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
        await browser.close()
    passed = sum(1 for r in RESULTS if r[1])
    print(f'\n{passed}/{len(RESULTS)} live checks passed')
    return 0 if passed == len(RESULTS) else 1


TYPES = ['listen_pick', 'listen_type', 'scene_question', 'fix_it', 'reply']
if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--live':
        sys.exit(asyncio.run(live_check(sys.argv[2].rstrip('/') + '/')))
    sys.exit(asyncio.run(main()))
