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

Usage:  python3 tools/build_audio.py [--prune] [--dry-run] [--skip-missing-audio]
Failures are logged and listed by content id/text in content/missing-audio.json.
The index contains successful clips only. Failures exit 1 unless --skip-missing-audio
is supplied; a later normal run fills missing clips without rewriting existing MP3s.
--prune preserves both referenced clips and all tracked MP3s. --dry-run writes nothing.

Needs:  pip install edge-tts, ffmpeg + ffprobe on PATH, network access to Microsoft's TTS.
"""
import argparse, asyncio, hashlib, json, os, shutil, subprocess, sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(APP, 'content')
AUDIO_DIR = os.path.join(APP, 'audio')
INDEX = os.path.join(CONTENT, 'audio.json')
MISSING_INDEX = os.path.join(CONTENT, 'missing-audio.json')
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
    if os.path.exists(path):
        if exists(path):
            return
        raise RuntimeError(f'refusing to overwrite existing empty file: {path}')
    async with sem:
        last = None
        for attempt in range(1, RETRIES + 1):
            try:
                import edge_tts
                tmp = path + '.part'
                await edge_tts.Communicate(text, VOICES[voice], rate=rate).save(tmp)
                if os.path.getsize(tmp) == 0:
                    raise RuntimeError('empty audio')
                os.replace(tmp, path)
                return
            except Exception as e:  # network hiccups: retry
                last = e
                if os.path.exists(path + '.part'):
                    os.remove(path + '.part')
                if attempt < RETRIES:
                    await asyncio.sleep(1.5 * attempt)
        raise RuntimeError(f'failed after {RETRIES} tries: {text!r}: {last}')


def join(parts, out):
    """Concatenate MP3 parts with PAUSE_S of silence between them (re-encoded 24 kHz mono 48 kbps, like edge-tts)."""
    if os.path.exists(out):
        if exists(out):
            return
        raise RuntimeError(f'refusing to overwrite existing empty file: {out}')
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
    for p in parts:
        cmd += ['-i', os.path.join(PARTS_DIR, p)]
    filt = ''.join(f'[{i}:a]aresample=24000,apad=pad_dur={PAUSE_S}[a{i}];' if i < len(parts) - 1 else f'[{i}:a]aresample=24000[a{i}];'
                   for i in range(len(parts)))
    filt += ''.join(f'[a{i}]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=0:a=1[out]'
    tmp = out + '.part'
    cmd += ['-filter_complex', filt, '-map', '[out]', '-ac', '1', '-ar', '24000', '-c:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', tmp]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        if not exists(tmp):
            raise RuntimeError('empty joined audio')
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def duration(name):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                        os.path.join(AUDIO_DIR, name)], capture_output=True, text=True, check=True)
    return round(float(r.stdout.strip()), 2)


def exists(path):
    return os.path.isfile(path) and os.path.getsize(path) > 0


def references(index):
    return {os.path.basename(e[k]) for sec in ('items', 'lines', 'missions')
            for e in index.get(sec, {}).values() for k in RATES if e.get(k)}


def write_missing(index):
    """Report missing rates by content owner, including lines shared by multiple owners."""
    skipped = []

    def record(kind, owner, section, key, text, rates):
        entry = index.get(section, {}).get(key, {})
        missing = [rate for rate in rates if not entry.get(rate) or not exists(os.path.join(APP, entry[rate]))]
        if missing:
            skipped.append({'kind': kind, 'id': owner['id'], 'section': section, 'key': key,
                            'text': text, 'missing_rates': missing})

    for card in load('cards.json', []):
        text = (card.get('audio_text') or '').strip()
        if text:
            record('card', card, 'items', text, text, RATES)
    for kind, filename, field in [('card', 'cards.json', 'audio_lines'),
                                   ('scene', 'scenes.json', 'dialogue'),
                                   ('mission', 'missions.json', 'lines')]:
        for owner in load(filename, []):
            media = owner.get('media') or {}
            lines = media.get(field, []) if kind == 'mission' else owner.get(field, [])
            if kind == 'mission':
                if media.get('kind') != 'audio':
                    continue
                record(kind, owner, 'missions', owner['id'],
                       '\n'.join(l['es'].strip() for l in lines), RATES)
            for line in lines or []:
                text = (line.get('es') or '').strip()
                if text:
                    record(kind, owner, 'lines', f"{line['voice']}|{text}", text, ('normal',))
    with open(MISSING_INDEX, 'w', encoding='utf-8') as f:
        json.dump({'count': len(skipped), 'items': skipped}, f, ensure_ascii=False, indent=1)
        f.write('\n')
    for item in skipped:
        print(f"SKIPPED {item['kind']} {item['id']} {item['section']}[{item['key']!r}] "
              f"({', '.join(item['missing_rates'])})", file=sys.stderr)
    print(f'{len(skipped)} missing audio item(s); wrote {os.path.relpath(MISSING_INDEX, APP)}')
    return skipped


def committed_audio():
    """Pruning must also preserve tracked MP3s, including retired content."""
    result = subprocess.run(['git', '-C', APP, 'ls-files', '-z', '--', 'audio/*.mp3'],
                            check=True, capture_output=True, text=True)
    return {os.path.basename(p) for p in result.stdout.split('\0') if p}


async def main_async(args):
    if not args.dry_run:
        os.makedirs(AUDIO_DIR, exist_ok=True)
        os.makedirs(PARTS_DIR, exist_ok=True)
    card_plan, line_plan, mission_plan = collect()
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
                if not args.dry_run:
                    shutil.copyfile(shipped, p)
            else:
                todo[p] = (text, rate, voice)
        return name

    def plan_join(lines, rate):
        parts = [file_name(es, rate, v) for v, es, _ in lines]
        out = joined_name(parts)
        if not exists(os.path.join(AUDIO_DIR, out)):
            for v, es, _ in lines:
                need_part(es, rate, v)
            joins.append((out, parts))
        return out, parts

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
                out, parts = plan_join(e['lines'], rate)
                entry[kind] = f'audio/{out}'
                entry['parts'][kind] = parts
            index['items'][t] = entry
    for (v, es) in line_plan:
        index['lines'][f'{v}|{es}'] = {'normal': f'audio/{need(es, RATES["normal"], v)}', 'voice': v}
    for mid, lines in mission_plan.items():
        entry = {'lines': [{'speaker': s, 'voice': v, 'es': es} for v, es, s in lines], 'parts': {}}
        for kind, rate in RATES.items():
            out, parts = plan_join(lines, rate)
            entry[kind] = f'audio/{out}'
            entry['parts'][kind] = parts
        index['missions'][mid] = entry
    # A line needed both as a shipped replay and as a join part is synthesized once.
    copies = {}
    for path in list(todo):
        shipped = os.path.join(AUDIO_DIR, os.path.basename(path))
        if os.path.dirname(path) == PARTS_DIR and shipped in todo:
            copies[path] = shipped
            del todo[path]
    print(f'{len(card_plan)} card clips, {len(line_plan)} dialogue lines, {len(mission_plan)} mission clips; '
          f'{len(todo)} TTS file(s) to generate, {len(dict(joins))} joined clip(s) (voices {VOICES["male"]}, {VOICES["female"]})')
    if args.dry_run:
        return 0
    planned = references(index)
    previous = load('audio.json', {})
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(synth(t, r, v, p, sem) for p, (t, r, v) in todo.items()), return_exceptions=True)
    errors = []
    for path, result in zip(todo, results):
        if isinstance(result, Exception):
            errors.append(result)
            # Also clean up when synth itself fails before its retry loop.
            if os.path.exists(path + '.part'):
                os.remove(path + '.part')
            print(f'ERROR TTS {os.path.basename(path)} {todo[path]!r}: {result}', file=sys.stderr)
    for target, source in copies.items():
        if exists(source):
            shutil.copyfile(source, target)
    for out, parts in dict(joins).items():
        try:
            if not all(exists(os.path.join(PARTS_DIR, p)) for p in parts):
                raise RuntimeError('missing TTS parts')
            join(parts, os.path.join(AUDIO_DIR, out))
        except Exception as e:
            errors.append(e)
            print(f'ERROR join {out}: {e}', file=sys.stderr)
    # Only successful clips are published. A missing slow clip can use normal at 0.75x.
    for section in ('items', 'lines', 'missions'):
        for key, entry in list(index[section].items()):
            for kind in RATES:
                if not entry.get(kind):
                    continue
                if not exists(os.path.join(APP, entry[kind])):
                    del entry[kind]
                    continue
                if section == 'missions':
                    try:
                        entry['duration' if kind == 'normal' else 'slow_duration'] = duration(os.path.basename(entry[kind]))
                    except Exception as e:
                        errors.append(e)
                        print(f'ERROR duration missions[{key!r}] {kind}: {e}', file=sys.stderr)
                        del entry[kind]
            if not any(entry.get(k) for k in RATES):
                del index[section][key]
    skipped = write_missing(index)
    with open(INDEX, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
        f.write('\n')
    wanted = references(index)
    # Protect planned outputs, previous references and tracked files as well as the new index.
    # In particular, a failed slow/join/duration step must never prune an existing clip.
    protected = wanted | planned | references(previous)
    if args.prune:
        protected |= committed_audio()  # fail closed if Git cannot establish which files are tracked
    stale = [f for f in os.listdir(AUDIO_DIR) if f.endswith('.mp3') and f not in protected]
    if args.prune:
        for f in stale:
            os.remove(os.path.join(AUDIO_DIR, f))
        print(f'pruned {len(stale)} unused, untracked file(s)')
    elif stale:
        print(f'{len(stale)} unused file(s) kept (run with --prune to delete untracked files)')
    print(f'wrote {os.path.relpath(INDEX, APP)}; generated {len(todo) - sum(isinstance(r, Exception) for r in results)} '
          f'new TTS file(s); {len(wanted)} audio files in use; {len(errors)} failure(s)')
    return 1 if (errors or skipped) and not args.skip_missing_audio else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--prune', action='store_true', help='delete only unused, unreferenced, untracked audio files')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-missing-audio', action='store_true',
                    help='publish successful clips and report missing audio without failing the build')
    sys.exit(asyncio.run(main_async(ap.parse_args())))
