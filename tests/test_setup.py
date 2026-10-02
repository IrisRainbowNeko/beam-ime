import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import beamctl


class SetupTests(unittest.TestCase):
    def test_upgrade_keeps_custom_model_and_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            state.mkdir()
            model = root / 'custom.gguf'
            model.write_bytes(b'local model')
            settings = {'model': str(model), 'cpu': True}
            (state / 'settings.json').write_text(json.dumps(settings))
            environment = {'BEAM_RIME_DIR': str(root / 'rime'), 'XDG_CONFIG_HOME': str(root / 'config')}
            with patch.dict(os.environ, environment), patch.object(os, 'geteuid', return_value=1000), \
                    patch.object(beamctl, 'data_dir', return_value=state), patch.object(beamctl, 'service'), \
                    patch('sys.argv', ['beamctl', 'setup']):
                self.assertEqual(beamctl.main(), 0)
            self.assertEqual(json.loads((state / 'settings.json').read_text()), settings)


if __name__ == '__main__':
    unittest.main()
