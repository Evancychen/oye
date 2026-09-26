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
├── tests/e2e_test.py       Playwright end-to-end test (412x915 + v1.1 layout at 360x720 / 412x915 / 384x854)
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

## v1.1: one-screen cards and hints

Spec: `../design/v1.1-one-screen-spec.md` (designed at 360 x 720; taller phones only get more empty middle).

- Every card screen is three zones: header (48px), a middle zone (the only thing that may scroll) and a
  dock pinned to the bottom (16px bottom padding) with the options / input and the main button.
  Everything is `flex: none`, so nothing is squashed. If the middle still overflows, a 120px fade and a
  "More below" pill appear (same on the feedback sheet and the summary's review list).
- Listen and type: replay 48 + slow 48 + "Replay · Slow" + Hint pill in one row; pinned 358px dock
  (input 56, Check 56, number pad 4 x 48).
- Fix it: answer field + 88px Check in one 56px row, 16px above the phone keyboard
  (`interactive-widget=resizes-content`, plus a `visualViewport` fallback that sets `--kb`).
- After answering: result, "The answer is …", replay + slow, then "What you heard" / "Why" rows that
  open a bottom sheet. Scene transcripts show speaker labels (first speaker blue, second green), the
  English line from the scene, and a replay button per line.
- `hint_en` (fix_it, listen_type) is only shown behind a "Hint" tap; no field / `null` = no Hint tap.
  Opening it is recorded as `h: 1` on that answer in `oye.progress.v1` history (optional field).
- `tools/build_audio.py` also makes one normal-speed MP3 per scene dialogue line (index entries with
  only `normal`) for the per-line replay buttons.

### How an installed app picks up a new version

`sw.js` installs the new shell (fetched with `?v=CACHE_VERSION`, so no HTTP/CDN cache can serve an old
file), calls `skipWaiting()` + `clients.claim()`, and messages every open window. A v1.1+ page acks
and reloads itself into the new shell right away on Home/summary, or as soon as the current session
ends (never mid-card). Windows that don't ack within 3s (the v1 shell) are reloaded by the SW. The
page checks for a new `sw.js` on every launch and when the app comes back from the background.
Progress (`localStorage`) and stored content/audio (`oye-content`) are never touched by an update.

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
Then deploy (commit + push, below). Installed apps pick up the new version on their next launch
(see "How an installed app picks up a new version").

Audio only: `.venv/bin/python tools/build_audio.py [--prune]` (voice `es-MX-DaliaNeural`,
normal speed plus a slow file at rate −25% ≈ 0.75×; `--prune` deletes unused files).

## Deploy to GitHub Pages

Live at https://evancychen.github.io/oye/ (repo `Evancychen/oye`, Pages from `main` /root).
After pushing: `.venv/bin/python tests/e2e_test.py --live https://evancychen.github.io/oye/`.

First-time setup, for reference:

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
