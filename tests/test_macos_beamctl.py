# SPDX-License-Identifier: Apache-2.0
"""The macOS beamctl's Rime edits: schema_list patching and reversible managed copies."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'packaging/macos/beamctl'


@unittest.skipUnless(shutil.which('bash') and shutil.which('shasum'), 'needs bash and shasum (macOS has both)')
class MacBeamctlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.rime = self.home / 'Library/Rime'
        self.rime.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, script, check=True):
        result = subprocess.run(['bash', '-c', f'BEAM_BEAMCTL_SOURCE_ONLY=1 . "$0"; {script}', str(SCRIPT)],
                                env={**os.environ, 'HOME': str(self.home)}, text=True, capture_output=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_schema_entry_new_file_and_back(self):
        self.call('schema_entry 1')
        config = self.rime / 'default.custom.yaml'
        self.assertEqual(config.read_text(), 'patch:\n  "schema_list/@before 0": { schema: beam_ice }  # Beam IME\n')
        self.call('schema_entry 1')  # idempotent
        self.assertEqual(config.read_text().count('beam_ice'), 1)
        self.call('schema_entry 0')
        self.assertEqual(config.read_text(), 'patch:\n')

    def test_schema_entry_joins_an_existing_list(self):
        config = self.rime / 'default.custom.yaml'
        original = 'patch:\n  schema_list:\n    - schema: rime_ice\n  menu/page_size: 7\n'
        config.write_text(original)
        self.call('schema_entry 1')
        self.assertEqual(config.read_text(), 'patch:\n  schema_list:\n    - schema: beam_ice  # Beam IME\n'
                                             '    - schema: rime_ice\n  menu/page_size: 7\n')
        self.assertEqual((self.rime / 'default.custom.yaml.beam-backup').read_text(), original)
        self.call('schema_entry 0')
        self.assertEqual(config.read_text(), original)

    def test_schema_entry_after_other_patches(self):
        config = self.rime / 'default.custom.yaml'
        config.write_text('# mine\npatch:\n  menu/page_size: 7\n\nother: 1\n')
        self.call('schema_entry 1')
        self.assertEqual(config.read_text(), '# mine\npatch:\n  menu/page_size: 7\n\n'
                                             '  "schema_list/@before 0": { schema: beam_ice }  # Beam IME\nother: 1\n')

    def test_schema_entry_refuses_a_taken_slot(self):
        config = self.rime / 'default.custom.yaml'
        original = 'patch:\n  "schema_list/@before 0": { schema: luna_pinyin }\n'
        config.write_text(original)
        self.assertNotEqual(self.call('schema_entry 1', check=False).returncode, 0)
        self.assertEqual(config.read_text(), original)

    def test_managed_files_restore_and_keep_user_edits(self):
        source = self.home / 'source'
        source.mkdir()
        (source / 'a.yaml').write_text('beam a\n')
        (source / 'b.yaml').write_text('beam b\n')
        (source / 'c.yaml').write_text('beam c\n')
        (self.rime / 'b.yaml').write_text('user b\n')
        self.call(' && '.join(f'install_file "{source}/{n}" "{self.rime}/{n}"' for n in ('a.yaml', 'b.yaml', 'c.yaml')))
        self.assertEqual((self.rime / 'b.yaml').read_text(), 'beam b\n')
        (self.rime / 'c.yaml').write_text('edited c\n')
        # Reinstalling leaves the user's edit; the first backup of b stays the original.
        self.call(f'install_file "{source}/c.yaml" "{self.rime}/c.yaml" && install_file "{source}/b.yaml" "{self.rime}/b.yaml"')
        self.assertEqual((self.rime / 'c.yaml').read_text(), 'edited c\n')
        self.call('restore_files')
        self.assertFalse((self.rime / 'a.yaml').exists())
        self.assertEqual((self.rime / 'b.yaml').read_text(), 'user b\n')
        self.assertEqual((self.rime / 'c.yaml').read_text(), 'edited c\n')


if __name__ == '__main__':
    unittest.main()
