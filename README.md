# Oye

A small installable web app (PWA) for Spanish listening practice in Mexican Spanish:
prices and numbers by ear, short real-life scenes, fix-your-mistake cards and "your reply" cards.
Plain HTML/CSS/JS with no framework and no build step. Dark theme from `../design/design-tokens.md`.

```
app/
├── index.html              app shell
├── styles.css              design tokens (Dark) + layout
├── manifest.webmanifest    name "Oye", standalone, #0E0E10, 4 icons, relative start_url/scope
├── sw.js                   service worker (offline shell + content + audio)
├── js/
│   ├── app.js              screens + card templates (picked by card `type`)
│   ├── content.js          version check, download, offline storage
│   ├── srs.js              Leitner-box spaced repetition, streak, stats (localStorage)
│   ├── audio.js            playback (normal + slow file)
│   └── check.js            answer normalisation (case, accents, punctuation)
├── content/                cards.json, scenes.json, vocab.json, version.json, audio.json
├── audio/                  pre-generated es-MX MP3s, named by a hash of voice+rate+text
├── icons/  fonts/          app icons (from design/icons), Inter subset (OFL)
├── tools/
│   ├── publish_content.py  validate -> copy content -> bump version -> build audio -> bump SW cache
│   ├── build_audio.py      edge-tts, voice es-MX-DaliaNeural, incremental
│   └── serve.py            local static server with the right MIME types
├── tests/e2e_test.py       Playwright end-to-end test (412x915 phone viewport)
└── screenshots/            screenshots from the last test run
```

## Run it locally

```bash
cd /workspace/spanish-app/app
python3 tools/serve.py 8765          # or: python3 -m http.server 8765
# open http://localhost:8765/
```

Service workers only run on `localhost` or HTTPS, so to try it on the phone over Wi-Fi you
need HTTPS (easiest: deploy to GitHub Pages, below). `python3 -m http.server` works too.
`serve.py` just adds the proper MIME type for the manifest and `no-cache` for `sw.js` / `version.json`.

Tooling (edge-tts, Playwright) is installed in a local virtualenv:

```bash
python3 -m venv .venv && .venv/bin/pip install edge-tts playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/e2e_test.py   # full end-to-end test, writes screenshots/
```

## How content works at runtime

- On launch the app fetches `content/version.json` with `cache: no-store`.
- If the version differs from the stored one, it downloads `cards.json`, `scenes.json`,
  `vocab.json` and `audio.json`, stores them in Cache Storage (`oye-content`), then downloads any
  audio files it doesn't have yet (and drops audio no card uses any more).
- If the version is the same, or the phone is offline, it uses the stored copy. After the first
  load everything (shell, content, audio) works offline.
- Home shows "N new cards this week" from `version.json` → `new_cards` (for 7 days after `published`).
- Each card's template is chosen by its `type`. Unknown types, or cards missing the fields their
  template needs, are skipped silently, so future content can't break the app.
- Progress (Leitner boxes, daily counts, streak, answer history) lives in `localStorage`
  (`oye.progress.v1`) on the phone.

## Publishing new content

The content team edits `/workspace/spanish-app/content/*.json`. To ship it into the app:

```bash
cd /workspace/spanish-app/app
.venv/bin/python tools/publish_content.py
```

It will:
1. run `/workspace/spanish-app/validate_content.py` and **abort unless it prints `CLEAN`**;
2. compare the new `cards.json` with `app/content/cards.json` to find new card ids;
3. copy `content/cards.json`, `scenes.json`, `vocab.json` into `app/content/`;
4. bump `app/content/version.json` (`version + 1`, `published` = today, `new_cards`, `new_card_ids`);
5. run `tools/build_audio.py`, which generates MP3s only for sentences without a file yet;
6. rewrite `CACHE_VERSION` in `sw.js` (content version + hash of the shell files).

If the content is unchanged the version is not bumped (`--force` bumps anyway), but audio and
`sw.js` are still refreshed, so it's also the command to run after editing app code.
Then deploy (commit + push, below). Installed apps pick up the new version on their next launch.

Audio only: `.venv/bin/python tools/build_audio.py [--prune]` (voice `es-MX-DaliaNeural`,
normal speed plus a slow file at rate −25% ≈ 0.75×; `--prune` deletes unused files).

## Deploy to GitHub Pages (later, not done)

All paths are relative, so the app works from a subpath such as `https://<user>.github.io/<repo>/`.

1. Create a new GitHub repository (e.g. `oye`).
2. Put the **contents** of `app/` at the repository root (or in `docs/`). Leave out `.venv/`
   (already in `.gitignore`); `tests/`, `tools/` and `screenshots/` are optional.
3. `git init && git add . && git commit -m "Oye v1"`, add the GitHub remote, `git push -u origin main`.
4. On GitHub: Settings → Pages → Build and deployment → Source "Deploy from a branch",
   branch `main`, folder `/ (root)` (or `/docs`). Save.
5. After a minute open `https://<user>.github.io/<repo>/` on the phone in Chrome, then
   menu ⋮ → "Add to home screen" / "Install app".
6. For each content update: run `tools/publish_content.py`, commit, push.
