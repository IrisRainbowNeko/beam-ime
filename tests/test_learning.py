# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from beamlib.learning import install_component
from personal_trainer import phonetic_segments, select_step, variants


class TrainerDataTest(unittest.TestCase):
    def test_mixed_english_remains_spelled_out(self):
        rows = variants({'keys': 'shiyonglinux', 'target': '使用Linux', 'context': ''})
        self.assertIn('sylinux', [r['keys'] for r in rows])
        self.assertTrue(all('linux' in r['keys'] for r in rows))

    def test_polyphonic_reading_matches_actual_keys(self):
        rows = variants({'keys': 'zhongqing', 'target': '重庆'})
        self.assertIn('zq', [r['keys'] for r in rows])
        self.assertNotIn('cq', [r['keys'] for r in rows])

    def test_original_variant_is_not_duplicated(self):
        rows = variants({'keys': 'xinglanyinqing', 'target': '星澜引擎'})
        self.assertEqual(len({r['keys'] for r in rows}), len(rows))
        self.assertEqual(rows[0]['keys'], 'xinglanyinqing')

    def test_batch_selection_is_resumable(self):
        events = [{'samples': [{'variants': [{'input_ids': [i], 'labels': [i]}]}]} for i in range(4)]
        references = [{'teacher': i} for i in range(8)]
        first = select_step(events, [], references, 11, 3)
        self.assertEqual(first, select_step(events, [], references, 11, 3))
        self.assertEqual(len(first), 16)

    def test_unknown_character_has_no_invented_pronunciation(self):
        self.assertIsNone(phonetic_segments('🙂', 'x'))


class LearningInstallTest(unittest.TestCase):
    def package(self, root, name='component.json'):
        component = {'schemaVersion': 2, 'base_sha256': 'a' * 64, 'backend': 'vulkan',
                     'executable': 'bin/beam-trainer', 'pinyin': 'pinyin.tsv', 'replay': 'replay.json'}
        path = root / 'runtime.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(name, json.dumps(component))
            entry = zipfile.ZipInfo('bin/beam-trainer')
            entry.create_system = 3
            entry.external_attr = 0o100755 << 16
            archive.writestr(entry, b'native trainer fixture')
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'schemaVersion': 2, 'platform': 'windows-x86_64' if os.name == 'nt' else 'linux-x86_64',
            'backend': 'vulkan', 'base_sha256': 'a' * 64, 'assets': [{'filename': path.name, 'size': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}))
        return manifest

    def test_offline_install_and_repeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); manifest = self.package(root)
            installed = install_component(manifest, root / 'installed', source=root)
            self.assertTrue(installed.exists())
            self.assertEqual(install_component(manifest, root / 'installed', source=root), installed)
            if os.name != 'nt':
                self.assertTrue(os.access(installed.parent / 'bin/beam-trainer', os.X_OK))

    def test_corruption_cannot_replace_an_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); manifest = self.package(root)
            (root / 'runtime.zip').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                install_component(manifest, root / 'installed', source=root)
            self.assertEqual(list((root / 'installed').iterdir()), [])

    def test_archive_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); manifest = self.package(root, '../escaped')
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                install_component(manifest, root / 'installed', source=root)
            self.assertFalse((root / 'escaped').exists())

    def test_old_python_component_requests_native_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); manifest = self.package(root)
            spec = json.loads(manifest.read_text()); spec['schemaVersion'] = 1
            manifest.write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError, 'incompatible'):
                install_component(manifest, root / 'installed', source=root)
