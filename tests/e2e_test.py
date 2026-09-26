#!/usr/bin/env python3
"""End-to-end test for Oye: Playwright + headless Chromium at a 412x915 phone viewport.

Starts its own throwaway servers (so it can really cut the network), then checks:
manifest + installability, service worker, content + audio download, a full Quick
session covering every card type (with screenshots), offline launch, a content
update with an unknown card type, and the answer normaliser.

Run:  .venv/bin/python tests/e2e_test.py      (needs: pip install playwright; playwright install chromium)
"""
import asyncio, json, os, shutil, socket, struct, subprocess, sys, time
from playwright.async_api import async_playwright

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(APP, 'screenshots')
TMP = os.path.join(APP, 'tests', '.tmp')
VIEWPORT = {'width': 412, 'height': 915}
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


async def new_page(browser, errors):
    ctx = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1, is_mobile=True, has_touch=True,
                                    locale='en-GB', timezone_id='America/Mexico_City')
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
    check('version.json: version 1, new_cards 34', version.get('version') == 1 and version.get('new_cards') == 34 and len(version.get('new_card_ids', [])) == 34)

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
        check('home shows "34 new cards this week"', quiet.strip() == '34 new cards this week', quiet)

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
            check('offline home still shows the new-cards line (stored version.json)', q == '34 new cards this week', q)
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
            {'id': 'c-9003', 'type': 'listen_pick', 'audio_text': known_text, 'options': [], 'answer': '$1'},
        ]
        json.dump(c2, open(os.path.join(site, 'content', 'cards.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        v2 = dict(version, version=2, new_cards=3, new_card_ids=['c-9001', 'c-9002', 'c-9003'])
        json.dump(v2, open(os.path.join(site, 'content', 'version.json'), 'w'))
        await page2.reload()
        await page2.wait_for_selector('html[data-ready="1"] [data-screen=home]')
        await page2.wait_for_timeout(500)
        info = await page2.evaluate('({n: window.__oye.content.cards.length, playable: window.__oye.playable.map(c => c.id), v: window.__oye.content.version.version, updated: window.__oye.content.updated})')
        q2 = (await page2.text_content('#quiet')).strip()
        check('new version.json -> app downloads the new content', info['v'] == 2 and info['updated'] and info['n'] == 37, info['n'])
        check('unknown type (speak_aloud) and a broken card are skipped silently',
              'c-9001' not in info['playable'] and 'c-9003' not in info['playable'] and 'c-9002' in info['playable'] and len(info['playable']) == 35)
        check('home new-cards line follows version.json', q2 == '3 new cards this week', q2)
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


TYPES = ['listen_pick', 'listen_type', 'scene_question', 'fix_it', 'reply']
if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
