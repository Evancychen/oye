#!/usr/bin/env python3
"""Publish new content into the Oye app.

1. Runs /workspace/spanish-app/validate_content.py and aborts unless it prints CLEAN.
2. Compares the new cards.json with the app's current app/content/cards.json to find
   new card ids.
3. Copies content/*.json (cards, scenes, vocab, and missions.json for the Hard level
   since v2; missions.json is optional) into app/content/.
4. Bumps app/content/version.json (version + 1, published date, new_cards, new_card_ids).
5. Runs tools/build_audio.py --prune (only missing audio is generated; files no card,
   dialogue line or mission uses any more are deleted).
6. Updates CACHE_VERSION in sw.js so installed apps pick up the new shell/content.

If the content is byte-identical to what the app already has, the version is not
bumped (use --force to bump anyway); audio and sw.js are still refreshed.

Usage:  python3 tools/publish_content.py [--force] [--source DIR] [--validator PATH]
"""
import argparse, datetime, glob, hashlib, json, os, re, shutil, subprocess, sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP)
APP_CONTENT = os.path.join(APP, 'content')
FILES = ['cards.json', 'scenes.json', 'vocab.json']
OPTIONAL_FILES = ['missions.json']   # v2 Levels: Hard missions
SHELL_FILES = ['index.html', 'styles.css', 'manifest.webmanifest', 'js/*.js',
               'fonts/*.woff2', 'icons/*.png']


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def load(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def validate(validator):
    print(f'> {validator}')
    p = subprocess.run([sys.executable, validator], capture_output=True, text=True)
    out = (p.stdout or '') + (p.stderr or '')
    print(out.rstrip())
    lines = [l.strip() for l in (p.stdout or '').splitlines() if l.strip()]
    if p.returncode != 0 or not lines or lines[-1] != 'CLEAN':
        sys.exit('ABORT: validator did not print CLEAN, nothing was published.')


def update_sw(content_version):
    sw = os.path.join(APP, 'sw.js')
    h = hashlib.sha256()
    for pat in SHELL_FILES:
        for p in sorted(glob.glob(os.path.join(APP, pat))):
            h.update(p.encode()); h.update(open(p, 'rb').read())
    tag = f'oye-v{content_version}-{h.hexdigest()[:8]}'
    src = open(sw, encoding='utf-8').read()
    new = re.sub(r"const CACHE_VERSION = '[^']*';", f"const CACHE_VERSION = '{tag}';", src, count=1)
    if new == src and tag not in src:
        sys.exit('ABORT: could not find CACHE_VERSION in sw.js')
    open(sw, 'w', encoding='utf-8').write(new)
    print(f'sw.js CACHE_VERSION = {tag}')


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=os.path.join(ROOT, 'content'))
    ap.add_argument('--validator', default=os.path.join(ROOT, 'validate_content.py'))
    ap.add_argument('--force', action='store_true', help='bump the version even if nothing changed')
    args = ap.parse_args()

    validate(args.validator)

    os.makedirs(APP_CONTENT, exist_ok=True)
    files = FILES + [f for f in OPTIONAL_FILES if os.path.exists(os.path.join(args.source, f))]
    changed = [f for f in files
               if not os.path.exists(os.path.join(APP_CONTENT, f))
               or sha(os.path.join(APP_CONTENT, f)) != sha(os.path.join(args.source, f))]
    old_version = load(os.path.join(APP_CONTENT, 'version.json'))

    if not changed and old_version and not args.force:
        print('Content unchanged, version stays', old_version.get('version'))
        version = old_version
    else:
        old_cards = load(os.path.join(APP_CONTENT, 'cards.json'), [])
        new_cards = load(os.path.join(args.source, 'cards.json'))
        old_ids = {c.get('id') for c in old_cards}
        new_ids = [c['id'] for c in new_cards if c.get('id') not in old_ids]
        # Same ISO week as the last publish (e.g. a mid-week fix): keep that week's
        # new cards so the Home line still counts them instead of dropping to 0.
        try:
            prev_day = datetime.date.fromisoformat((old_version or {}).get('published', ''))
        except ValueError:
            prev_day = None
        if prev_day and prev_day.isocalendar()[:2] == datetime.date.today().isocalendar()[:2]:
            current = {c.get('id') for c in new_cards}
            carried = [i for i in (old_version or {}).get('new_card_ids', []) if i in current and i not in new_ids]
            new_ids = carried + new_ids
        for f in files:
            shutil.copyfile(os.path.join(args.source, f), os.path.join(APP_CONTENT, f))
            print(f'copied {f}')
        version = {
            'version': (old_version or {}).get('version', 0) + 1,
            'published': datetime.date.today().isoformat(),
            'new_cards': len(new_ids),
            'new_card_ids': new_ids,
            'card_count': len(new_cards),
        }
        missions = load(os.path.join(args.source, 'missions.json'))
        if missions is not None:
            version['mission_count'] = len(missions)
        with open(os.path.join(APP_CONTENT, 'version.json'), 'w', encoding='utf-8') as f:
            json.dump(version, f, ensure_ascii=False, indent=1)
        print(f"version.json -> version {version['version']}, new_cards {version['new_cards']}")

    print('> tools/build_audio.py')
    r = subprocess.run([sys.executable, os.path.join(APP, 'tools', 'build_audio.py'), '--prune'])
    if r.returncode != 0:
        sys.exit('ABORT: audio build failed (content was copied; rerun after fixing).')

    update_sw(version['version'])
    print('Done.')


if __name__ == '__main__':
    main()
