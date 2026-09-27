#!/usr/bin/env python3
"""Generate the Spanish text-to-speech audio for Oye (v2.1 voices).

Voices (content-format-v1.md, "v2.1 Voices"):
  male   = es-US-AlonsoNeural      female = es-US-PalomaNeural
  - Single-voice cards: the card's `voice` if set, else the level default
    (Easy -> female/Paloma, Medium and Hard -> male/Alonso).
  - Dialogues (scene lines, a card's `audio_lines`, mission `media.lines`) use the
    `voice` on each line. A dialogue line without a valid `voice` is an ERROR: the
    builder never guesses a voice from speaking order.

Makes, with edge-tts:
  items[audio_text]    card audio: normal (+0%) and slow (-25%, about 0.75x, made by the
                       voice itself). A card with `audio_lines` gets each line in its own
                       voice, joined into one clip (short pause between lines) with ffmpeg.
  lines["voice|text"]  one normal-speed file per dialogue line (per-line replay buttons in
                       transcripts), for scenes, audio_lines and missions.
  missions[id]         audio missions: the whole script as one clip (normal + slow), with
                       its duration in seconds, for the mission player bar.

Single-voice files are named sha1("<voice name>|<rate>|<text>")[:16].mp3, so a voice
change regenerates everything and a rerun only creates what's missing. Joined clips are
named from the hashes of their parts. Writes app/content/audio.json.

Usage:  python3 tools/build_audio.py [--prune] [--dry-run]
Needs:  pip install edge-tts, ffmpeg + ffprobe on PATH, network access to Microsoft's TTS.
"""
import argparse, asyncio, hashlib, json, os, shutil, subprocess, sys, tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(APP, 'content')
AUDIO_DIR = os.path.join(APP, 'audio')
INDEX = os.path.join(CONTENT, 'audio.json')
PARTS_DIR = os.path.join(APP, '.audio-parts')   # local cache of single lines used to build joined clips (gitignored)

VOICES = {'male': 'es-US-AlonsoNeural', 'female': 'es-US-PalomaNeural'}
LEVEL_VOICE = {'easy': 'female', 'medium': 'male', 'hard': 'male'}
RATES = {'normal': '+0%', 'slow': '-25%'}
PAUSE_S = 0.45            # silence between dialogue lines in a joined clip
CONCURRENCY = 4
RETRIES = 3


def file_name(text, rate, voice='female'):
    """voice = 'male' | 'female' (or a full voice name)."""
    name = VOICES.get(voice, voice)
    h = hashlib.sha1(f'{name}|{rate}|{text}'.encode('utf-8')).hexdigest()[:16]
    return f'{h}.mp3'


def joined_name(parts):
    h = hashlib.sha1(('join|%s|' % PAUSE_S + '|'.join(parts)).encode('utf-8')).hexdigest()[:16]
    return f'{h}.mp3'


def load(name, default=None):
    try:
        with open(os.path.join(CONTENT, name), encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def card_voice(c):
    v = c.get('voice')
    if v is not None:
        if v not in VOICES:
            raise SystemExit(f'ERROR {c.get("id")}: voice must be male or female, got {v!r}')
        return v
    return LEVEL_VOICE.get(c.get('level') or 'easy', 'female')


def check_line(where, line):
    v = line.get('voice')
    if v not in VOICES:
        raise SystemExit(f'ERROR {where}: dialogue line {line.get("speaker")!r} "{(line.get("es") or "")[:40]}" '
                         f'has no valid voice (got {v!r}); every dialogue line needs "voice": "male" | "female"')
    return v


def collect():
    """Returns (cards_plan, line_plan, mission_plan)."""
    cards = load('cards.json', [])
    scenes = load('scenes.json', [])
    missions = load('missions.json', [])
    card_plan = {}      # audio_text -> {'voice': v} | {'lines': [(voice, es, speaker)]}
    line_plan = {}      # (voice, es) -> True
    for sc in scenes:
        for d in sc.get('dialogue') or []:
            v = check_line(sc.get('id'), d)
            if (d.get('es') or '').strip():
                line_plan[(v, d['es'].strip())] = True
    for c in cards:
        t = (c.get('audio_text') or '').strip()
        if not t:
            continue
        if c.get('audio_lines'):
            lines = []
            for l in c['audio_lines']:
                v = check_line(c.get('id'), l)
                lines.append((v, l['es'].strip(), l.get('speaker') or ''))
                line_plan[(v, l['es'].strip())] = True
            entry = {'lines': lines}
        else:
            entry = {'voice': card_voice(c)}
        if t in card_plan and card_plan[t] != entry:
            raise SystemExit(f'ERROR {c.get("id")}: audio_text {t[:40]!r} is used by another card with a different voice')
        card_plan[t] = entry
    mission_plan = {}
    for m in missions:
        md = m.get('media') or {}
        if md.get('kind') != 'audio':
            continue
        lines = []
        for l in md.get('lines') or []:
            v = check_line(m.get('id'), l)
            lines.append((v, l['es'].strip(), l.get('speaker') or ''))
            line_plan[(v, l['es'].strip())] = True
        if lines:
            mission_plan[m['id']] = lines
    return card_plan, line_plan, mission_plan


async def synth(text, rate, voice, path, sem):
    import edge_tts
    async with sem:
        last = None
        for attempt in range(1, RETRIES + 1):
            try:
                tmp = path + '.part'
                await edge_tts.Communicate(text, VOICES[voice], rate=rate).save(tmp)
                if os.path.getsize(tmp) == 0:
                    raise RuntimeError('empty audio')
                os.replace(tmp, path)
                return
            except Exception as e:  # network hiccups: retry
                last = e
                await asyncio.sleep(1.5 * attempt)
        raise RuntimeError(f'failed after {RETRIES} tries: {text!r}: {last}')


def join(parts, out):
    """Concatenate MP3 parts with PAUSE_S of silence between them (re-encoded 24 kHz mono 48 kbps, like edge-tts)."""
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
    for p in parts:
        cmd += ['-i', os.path.join(PARTS_DIR, p)]
    filt = ''.join(f'[{i}:a]aresample=24000,apad=pad_dur={PAUSE_S}[a{i}];' if i < len(parts) - 1 else f'[{i}:a]aresample=24000[a{i}];'
                   for i in range(len(parts)))
    filt += ''.join(f'[a{i}]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=0:a=1[out]'
    tmp = out + '.part'
    cmd += ['-filter_complex', filt, '-map', '[out]', '-ac', '1', '-ar', '24000', '-c:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', tmp]
    subprocess.run(cmd, check=True)
    os.replace(tmp, out)


def duration(name):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                        os.path.join(AUDIO_DIR, name)], capture_output=True, text=True, check=True)
    return round(float(r.stdout.strip()), 2)


async def main_async(args):
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(PARTS_DIR, exist_ok=True)
    card_plan, line_plan, mission_plan = collect()
    if (card_plan or mission_plan) and not (shutil.which('ffmpeg') and shutil.which('ffprobe')) and not args.dry_run:
        raise SystemExit('ERROR: ffmpeg and ffprobe are needed to join dialogue lines')
    todo = {}   # path -> (text, rate, voice)

    def need(text, rate, voice):
        name = file_name(text, rate, voice)
        p = os.path.join(AUDIO_DIR, name)
        if not (os.path.exists(p) and os.path.getsize(p) > 0):
            todo[p] = (text, rate, voice)
        return name

    def need_part(text, rate, voice):
        # Single lines that only exist to be joined live in a local cache (not shipped, not pruned),
        # so re-publishing doesn't re-download them. A published copy in audio/ is reused if present.
        name = file_name(text, rate, voice)
        p = os.path.join(PARTS_DIR, name)
        if not (os.path.exists(p) and os.path.getsize(p) > 0):
            shipped = os.path.join(AUDIO_DIR, name)
            if os.path.exists(shipped) and os.path.getsize(shipped) > 0:
                shutil.copyfile(shipped, p)
            else:
                todo[p] = (text, rate, voice)
        return name

    index = {'format': 2, 'voices': VOICES, 'level_voice': LEVEL_VOICE, 'rates': RATES, 'pause_s': PAUSE_S,
             'items': {}, 'lines': {}, 'missions': {}}
    joins = []  # (out_name, [parts])
    for t, e in card_plan.items():
        if 'voice' in e:
            index['items'][t] = {kind: f'audio/{need(t, rate, e["voice"])}' for kind, rate in RATES.items()}
            index['items'][t]['voice'] = e['voice']
        else:
            entry = {'voice': 'dialogue', 'lines': [{'speaker': s, 'voice': v, 'es': es} for v, es, s in e['lines']], 'parts': {}}
            for kind, rate in RATES.items():
                parts = [need_part(es, rate, v) for v, es, _ in e['lines']]
                out = joined_name(parts)
                joins.append((out, parts))
                entry[kind] = f'audio/{out}'
                entry['parts'][kind] = parts
            index['items'][t] = entry
    for (v, es) in line_plan:
        index['lines'][f'{v}|{es}'] = {'normal': f'audio/{need(es, RATES["normal"], v)}', 'voice': v}
    for mid, lines in mission_plan.items():
        entry = {'lines': [{'speaker': s, 'voice': v, 'es': es} for v, es, s in lines], 'parts': {}}
        for kind, rate in RATES.items():
            parts = [need_part(es, rate, v) for v, es, _ in lines]
            out = joined_name(parts)
            joins.append((out, parts))
            entry[kind] = f'audio/{out}'
            entry['parts'][kind] = parts
        index['missions'][mid] = entry
    print(f'{len(card_plan)} card clips, {len(line_plan)} dialogue lines, {len(mission_plan)} mission clips; '
          f'{len(todo)} TTS file(s) to generate, {len(joins)} joined clip(s) (voices {VOICES["male"]}, {VOICES["female"]})')
    if args.dry_run:
        return 0
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(synth(t, r, v, p, sem) for p, (t, r, v) in todo.items()), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    for e in errors:
        print('ERROR', e, file=sys.stderr)
    if errors:
        return 1
    for out, parts in joins:
        join(parts, os.path.join(AUDIO_DIR, out))
    for mid, entry in index['missions'].items():
        entry['duration'] = duration(entry['normal'].split('/')[-1])
        entry['slow_duration'] = duration(entry['slow'].split('/')[-1])
    wanted = set()
    for sec in ('items', 'lines', 'missions'):
        for e in index[sec].values():
            wanted.update(os.path.basename(e[k]) for k in ('normal', 'slow') if e.get(k))
    stale = [f for f in os.listdir(AUDIO_DIR) if f.endswith('.mp3') and f not in wanted]
    if args.prune:
        for f in stale:
            os.remove(os.path.join(AUDIO_DIR, f))
        print(f'pruned {len(stale)} unused file(s)')
    elif stale:
        print(f'{len(stale)} unused file(s) kept (run with --prune to delete)')
    with open(INDEX, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f'wrote {os.path.relpath(INDEX, APP)}; generated {len(todo)} new TTS file(s); {len(wanted)} audio files in use')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--prune', action='store_true', help='delete audio files nothing uses any more')
    ap.add_argument('--dry-run', action='store_true')
    sys.exit(asyncio.run(main_async(ap.parse_args())))
