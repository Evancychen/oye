"""Small offline regressions; all audio and content live in temporary directories."""
import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('build_audio', Path(__file__).resolve().parents[1] / 'tools/build_audio.py')
ba = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ba)


class AudioBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.content = self.root / 'content'
        self.audio = self.root / 'audio'
        self.content.mkdir()
        self.audio.mkdir()
        changes = dict(APP=str(self.root), CONTENT=str(self.content), AUDIO_DIR=str(self.audio),
                       PARTS_DIR=str(self.root / '.audio-parts'), INDEX=str(self.content / 'audio.json'),
                       MISSING_INDEX=str(self.content / 'missing-audio.json'), RETRIES=1)
        self.addCleanup(patch.stopall)
        patch.multiple(ba, **changes).start()
        patch.object(ba, 'committed_audio', return_value={'tracked.mp3'}).start()
        self.log = io.StringIO()
        self.cards = [{'id': 'c-0001', 'audio_text': 'Antes'}, {'id': 'c-0673', 'audio_text': 'Nuevo'}]
        self.write('cards.json', self.cards)

    def write(self, name, obj):
        (self.content / name).write_text(json.dumps(obj))

    def run_build(self, tolerate=False, dry=False):
        args = argparse.Namespace(dry_run=dry, prune=True, skip_missing_audio=tolerate)
        with contextlib.redirect_stdout(self.log), contextlib.redirect_stderr(self.log):
            return asyncio.run(ba.main_async(args))

    def seed_existing(self):
        for rate in ba.RATES.values():
            (self.audio / ba.file_name('Antes', rate)).write_bytes(b'existing mp3')
        (self.audio / 'tracked.mp3').write_bytes(b'tracked retired clip')
        (self.audio / 'referenced.mp3').write_bytes(b'previously indexed clip')
        (self.audio / 'stale.mp3').write_bytes(b'untracked orphan')
        self.write('audio.json', {'items': {'Previous': {'normal': 'audio/referenced.mp3'}}})

    def test_failures_then_retry_only_missing_and_prune_safely(self):
        self.seed_existing()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.audio.iterdir() if p.name != 'stale.mp3'}

        class FailingTTS:
            def __init__(self, *args, **kwargs): pass
            async def save(self, path):
                Path(path).write_bytes(b'partial')
                raise RuntimeError('HTTP 403')

        with patch.dict('sys.modules', {'edge_tts': types.SimpleNamespace(Communicate=FailingTTS)}):
            self.assertEqual(self.run_build(tolerate=True), 0)
            self.assertEqual(self.run_build(), 1)
        idx = json.loads((self.content / 'audio.json').read_text())
        self.assertIn('Antes', idx['items'])
        self.assertNotIn('Nuevo', idx['items'])
        skipped = json.loads((self.content / 'missing-audio.json').read_text())['items']
        self.assertEqual([(i['id'], i['text']) for i in skipped], [('c-0673', 'Nuevo')])
        self.assertIn('HTTP 403', self.log.getvalue())
        self.assertIn('c-0673', self.log.getvalue())
        self.assertFalse(list(self.root.rglob('*.part')))
        # The first run protects the previous index. Subsequent runs may prune its untracked orphan.
        for name in before.keys() - {'referenced.mp3'}:
            p = self.audio / name
            self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), before[name])
        self.assertFalse((self.audio / 'stale.mp3').exists())
        calls = []

        async def success(text, rate, voice, path, sem):
            calls.append((text, rate))
            Path(path).write_bytes(b'new mp3')

        with patch.object(ba, 'synth', side_effect=success):
            self.assertEqual(self.run_build(), 0)
            self.assertEqual(calls, [('Nuevo', '+0%'), ('Nuevo', '-25%')])
            calls.clear()
            self.assertEqual(self.run_build(), 0)
            self.assertEqual(calls, [])
        self.assertEqual(json.loads((self.content / 'missing-audio.json').read_text())['count'], 0)
        self.assertIn('Nuevo', json.loads((self.content / 'audio.json').read_text())['items'])

    def test_previous_reference_is_protected(self):
        self.seed_existing()
        with patch.object(ba, 'synth', side_effect=RuntimeError('offline')):
            self.assertEqual(self.run_build(tolerate=True), 0)
        self.assertEqual((self.audio / 'referenced.mp3').read_bytes(), b'previously indexed clip')

    def test_joined_outputs_do_not_regenerate_uncached_parts(self):
        lines = [{'voice': 'female', 'speaker': 'A', 'es': 'Hola'}]
        self.write('cards.json', [{'id': 'c-0673', 'audio_text': 'Hola', 'audio_lines': lines}])
        self.write('missions.json', [{'id': 'm-test', 'media': {'kind': 'audio', 'lines': lines}}])
        (self.audio / ba.file_name('Hola', '+0%')).write_bytes(b'line')
        for rate in ba.RATES.values():
            (self.audio / ba.joined_name([ba.file_name('Hola', rate)])).write_bytes(b'joined')
        with patch.object(ba, 'synth') as synth, patch.object(ba, 'join') as join, patch.object(ba, 'duration', return_value=12):
            self.assertEqual(self.run_build(), 0)
            synth.assert_not_called()
            join.assert_not_called()

    def test_failed_join_is_omitted_and_partial_removed(self):
        lines = [{'voice': 'female', 'speaker': 'A', 'es': 'Hola'}]
        self.write('cards.json', [{'id': 'c-0673', 'audio_text': 'Hola', 'audio_lines': lines}])
        for rate in ba.RATES.values():
            (self.audio / ba.file_name('Hola', rate)).write_bytes(b'line')

        def failing_ffmpeg(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'partial join')
            raise RuntimeError('ffmpeg failure')

        with patch.object(ba.subprocess, 'run', side_effect=failing_ffmpeg):
            self.assertEqual(self.run_build(tolerate=True), 0)
        idx = json.loads((self.content / 'audio.json').read_text())
        self.assertNotIn('Hola', idx['items'])
        self.assertIn('female|Hola', idx['lines'])
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_dry_run_writes_nothing(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        with patch.object(ba, 'synth') as synth:
            self.assertEqual(self.run_build(dry=True), 0)
            synth.assert_not_called()
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))


if __name__ == '__main__':
    unittest.main()
