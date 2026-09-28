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
- Listen and type: replay 48 + slow 48 + "Tap to listen" / "Play again" + Hint pill in one row; pinned 358px dock
  (input 56, Check 56, number pad 4 x 48).
- Fix it: answer field + 88px Check in one 56px row, 16px above the phone keyboard
  (`interactive-widget=resizes-content`, plus a `visualViewport` fallback that sets `--kb`).
- After answering: result, "The answer is …" (or, for a right typed answer that is an accepted alternate, see
  "ux-replays" below), replay + slow, then "What you heard" / "Why" rows that
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

## ux-replays: no autoplay, replays / skipped, accepted alternates

Spec: `../design/levels-v2-spec.md` → "No autoplay".

- **No autoplay anywhere.** Every card opens silent with the question and answers visible. Under / next to the play
  button a caption reads "Tap to listen" until the first play, then "Play again". While a clip plays the play button
  shows a stop icon (`aria-pressed="true"`, label "Stop") and tapping it stops the audio. No pulse or other animation
  (the old `.is-playing` pulse is gone). The slow button still starts slow playback on tap; the play button shows the
  playing state during slow playback too, so it can stop it.
- **Missions**: the audio intro's primary button is now "Go to questions" (it used to be "Play announcement" /
  "Play voicemail" and started the clip). Question 1 opens with the player bar at 0:00 and nothing playing; the bar's
  button plays, and tapping it again stops (pauses; the next tap goes on from there). A clip that was started keeps
  playing when Check moves to the next question (that is the user's own playback, not autoplay). Check never waits
  for the audio.
- **`replays`** (results rows, 0-99): audio plays started on the card / mission question **before the answer is
  submitted**, minus the first listen, i.e. `max(0, plays - 1)`. Normal and slow both count; stop + tap again is a
  new play. In missions every play started from the player bar counts (play, resume after a stop, the slow toggle
  or a seek while stopped); switching speed or seeking while it plays does not. Plays on the mission intro screen
  count toward question 1 (the intro has no audio button today). Plays on the feedback screen, in the What-you-heard
  sheet and on the result/summary screens are **not** counted. Message (WhatsApp) missions send 0.
- **`skipped`** (results rows, boolean): true when the card was answered with "I don't know" (`answer_given` stays
  empty, `correct` false). Missions have no skip path: always false.
- **Accepted alternates**: when a right typed answer (fix_it, listen_type, mission `type` questions) is an accepted
  alternate that differs from the card's main answer after `normalize()` (e.g. "gira" on c-0061, "sobre" on c-0055),
  the blank keeps the user's own word (same green style) and the line reads `“gira” works. Also common: “dobla”.`
  (only the main answer, never the whole list; prices keep their `$` format). Typing the main answer, and wrong
  answers, look as before. On the mission result the right row's Why box uses the same sentence.
- **Focus ring**: buttons show the ring only for keyboard focus (`:focus-visible`). Because Chrome can still treat
  the script-focused Next button as `:focus-visible` after a tap (e.g. right after the text field), `html.pointer`
  (set on pointerdown, cleared by Tab / arrow keys) hides it until the keyboard is used.
- **Topic grid**: tiles are flex columns (name at the top, stars pinned to the bottom with at least 12px above them,
  `grid-auto-rows: 1fr`), so two-line names ("Future & conditional") no longer crowd the stars and every tile lines up.

## v2: levels, topic challenges and missions

- **Levels** come from each card's `level` (`easy` default, `medium`, `hard`); `js/levels.js` has the topic
  names, `levelOf()` and the star rule. Lessons, Quick session and the SRS pool use **Easy** cards only.
- **Home**: streak + stars earned, price trend, then CHALLENGES: *Medium · Topic challenge* (topic grid →
  10-card session drawn from that topic, `planTopic` in `js/srs.js`) and *Hard · Real-life mission*
  (missions list → intro → message/player → questions → result). The EASY caption above Quick session opens
  the Lessons sheet (it replaces the old Lessons row).
- **Stars** (`oye.stars.v1` in localStorage): 1 = finished, 2 = 70%+, 3 = 90%+ with no hints; in Medium a correct
  answer after a hint counts 0.5. Medium topics and missions keep their best score; Easy sessions add their stars
  to the total. Opening the Hint on any mission question also rules out 3 stars (same as cards).
- **Missions** (`content/missions.json`): one long joined audio clip (or a WhatsApp-style text message) plus
  questions (`pick` with wrapping options, keypad `type` answers like `5:00`, letter input for word answers).
  Check moves on without feedback (the last question's button reads "See results"); the result screen shows every
  answer with an open "Why" box for mistakes and the full transcript. Missions count toward the streak. The
  missions list (Home → Hard) shows each mission's title, "Audio · about N min" or "Message", and best stars.
  Message missions show `media.sender_en` as the sender line (falls back to `title_en`) and `media.time`
  (24-hour HH:MM) bottom-right on the bubble.
- Text answers are accent- and case-insensitive; text inputs use `autocapitalize/autocorrect=off`,
  `spellcheck=false`.
- Short phones (≤760 px / ≤700 px tall) get tighter spacing so the home screen and mission questions fit
  without scrolling; Medium cards with long two-line options use the compact audio row, and after answering
  they keep only the right answer and your pick.

### v2: voices and audio.json

Every dialogue line in content carries `voice` (`male` = `es-US-AlonsoNeural`, `female` = `es-US-PalomaNeural`).
`build_audio.py` **aborts** if a dialogue line has no valid voice; it never falls back to speaking order.
Single cards use the card's `voice` override, else the level default (Easy → Paloma, Medium/Hard → Alonso).
Cards with `audio_lines` and missions get each line synthesised in its own voice, then joined (0.45 s pause)
into one clip. File names are `sha1("<voice>|<rate>|<text>")[:16].mp3`, so a voice change always makes a new
file. Single lines used only for joining are cached in `.audio-parts/` (gitignored), not shipped.

`content/audio.json` (format 2):
- `items[text]` → `{normal, slow, voice}`; dialogue cards: `voice: "dialogue"`, `lines` (speaker, voice, es), `parts`;
- `lines["<voice>|<es>"]` → `{normal, voice}` (per-line replay in transcripts; colour follows the voice:
  Alonso blue, Paloma green; labels come from content);
- `missions[id]` → `{normal, slow, parts, lines, duration, slow_duration}`.

The e2e test fails if any dialogue line or `audio_lines` entry lacks a voice, or if `audio.json` maps a line or
card to the wrong voice.

## Results upload (Google Sheet for Gabriel)

`js/results.js`. Every answer in a session is recorded (`answered_at`, `card_id`, `content_version`, `correct`,
`answer_given` (empty for "I don't know"), `used_hint`, `used_slow` (slow replay tapped before answering), `session_id`,
and since ux-replays `replays` (plays before answering minus the first listen, 0-99) and `skipped` (true for "I don't know")).
When the session reaches the Summary, its rows go into a localStorage queue (`oye.resultsQueue.v1`) and are
POSTed to the Apps Script web app as `text/plain;charset=utf-8` JSON `{results: [...]}` (no CORS preflight),
at most 200 rows per request. Rows leave the queue only after a reply with `ok: true`; otherwise they are retried
on the next app open, on resume and on the `online` event. Rows whose card id isn't in the loaded content (or
doesn't match `c-NNNN`) are dropped before sending. The Summary shows "Results sent to Gabriel" (green dot) once
confirmed, "Saved · will send when you're online" (hollow dot) while sending or queued. Sessions closed with ✕
before the Summary are not uploaded. v2 rows also carry `level`, `topic`, `mission_id` (empty for now) and
`stars` (the Apps Script v3 stores them in their own columns). **Hard mission answers are uploaded too**, through the
same offline queue, when the mission result screen opens: one row per question with `card_id` `<mission_id>:qN`
(e.g. `m-metro-01:q2`), `level` `hard`, the mission's `topic`, `mission_id` and the attempt's `stars`; the result
screen shows the same "Results sent to Gabriel" / "Saved · will send…" line. `js/results.js` keeps only rows the
script accepts: `c-NNNN` card ids, or mission rows (`mission_id` `m-…`, level `hard`, `card_id` `<mission_id>:qN`;
a bare `qN` is rewritten to that form), and only ids in the loaded content. The e2e tests route the endpoint to a fake and fail if a request ever reaches it.

`replays` / `skipped` (ux-replays) are stored by the Apps Script **v4** in two new columns at the end
(`…, stars, replays, skipped`). The v3 script builds each Sheet row from the fields it knows and ignores any others,
so rows with the two new fields are still accepted (and simply not stored) while v3 is live. The queue coerces the
two fields when present (replays integer 0-99, skipped boolean); rows queued by an older app version without them
still send unchanged (v4 stores replays blank and skipped false for them). Mission rows send `skipped: false`.

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

Publishing also copies the optional `missions.json`, writes `mission_count` into `version.json`, and runs
`build_audio.py --prune`.

Audio only: `.venv/bin/python tools/build_audio.py [--prune] [--dry-run]` (normal speed plus a slow file at
rate −25% ≈ 0.75×; `--prune` deletes files nothing uses). Needs `ffmpeg`/`ffprobe` to join dialogue lines.
See "v2: voices and audio.json" below.

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
