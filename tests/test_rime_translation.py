# SPDX-License-Identifier: Apache-2.0
"""Drive the real Rime plugin against an isolated bilingual fixture daemon."""
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.environ.get('BEAM_TEST_RIME_BUILD'), 'set BEAM_TEST_RIME_BUILD for real Rime mode tests')
class RimeTranslationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='beam-双模式 test-')
        self.root = Path(self.temporary.name)
        self.events = []
        self.failure = ''
        owner = self
        status = {'enabled': True, 'paused': False, 'installation': {'state': 'ready'}, 'last_error': ''}

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    request = json.loads(line)
                    owner.events.append(request)
                    reply = {'id': request['id'], 'ok': True}
                    if request['op'] == 'query':
                        mode = request.get('mode', 'ime')
                        reply.update(mode=mode, candidates=['Hello.'] if mode == 'translate_en' else ['你好'],
                                     comments=['你好'] if mode == 'translate_en' else [''], sources=['model'])
                        if mode == 'translate_en' and owner.failure:
                            if owner.failure == 'waiting' and request['keys'] != 'nihao':
                                reply.update(candidates=[], comments=[], sources=[], translation_status='need_more_input')
                            elif owner.failure == 'waiting':
                                reply['translation_status'] = 'ready'
                            elif owner.failure == 'timeout':
                                time.sleep(.5)
                            elif owner.failure == 'malformed':
                                reply['comments'] = 5
                            else:
                                reply.update(ok=False, error=owner.failure)
                    elif request['op'] == 'learning':
                        reply['learning'] = status
                    try:
                        self.wfile.write((json.dumps(reply) + '\n').encode())
                    except (BrokenPipeError, ConnectionResetError):
                        return

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        endpoint = self.root / 'beamd.sock'
        self.server = Server(str(endpoint), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        build = Path(os.environ['BEAM_TEST_RIME_BUILD'])
        libdir = Path(subprocess.check_output(['pkg-config', '--variable=libdir', 'rime'], text=True).strip())
        plugins = self.root / 'plugins'; plugins.mkdir()
        shutil.copy2(build / 'bin/librime-beam.so', plugins)
        user = self.root / 'rime'; user.mkdir()
        (user / 'default.yaml').write_text('config_version: "1"\nschema_list:\n  - schema: beam_test\n', encoding='utf-8')
        (user / 'beam_test.schema.yaml').write_text('''schema:
  schema_id: beam_test
  name: Beam test
  version: '1'
switches:
  - {name: beam_off, reset: 0, states: [智能, 经典]}
  - {name: beam_translate, reset: 0, states: [普通输入, 英文翻译]}
engine:
  processors: [beam_processor, speller, selector, express_editor]
  segmentors: [abc_segmentor]
  translators: [beam_translator, table_translator]
  filters: [beam_filter]
speller:
  alphabet: abcdefghijklmnopqrstuvwxyz
translator:
  dictionary: beam_test
  enable_user_dict: false
beam:
  timeout_ms: 400
  beam_ms: 0
''', encoding='utf-8')
        (user / 'beam_test.dict.yaml').write_text('---\nname: beam_test\nversion: "1"\nsort: original\n...\n你\tni\t100\n好\thao\t100\n你好\tni hao\t100\n', encoding='utf-8')
        self.command = ['bwrap', '--ro-bind', '/', '/', '--bind', str(self.root), str(self.root),
                        '--ro-bind', str(plugins), str(libdir / 'rime-plugins'),
                        str(build / 'bin/rime_smoke'), str(user), 'beam_test']
        self.env = {**os.environ, 'BEAM_SOCKET': str(endpoint), 'GLOG_log_dir': str(self.root)}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temporary.cleanup()

    def drive(self, *keys):
        result = subprocess.run(self.command + list(keys), env=self.env, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_switch_retains_keys_and_only_commits_english(self):
        output = self.drive('nihao', '@beam_translate', '{space}')
        self.assertIn('Hello.  你好', output)
        self.assertIn('commit: Hello.', output)
        self.assertNotIn('commit: 你好', output)
        self.assertTrue(any(r.get('mode') == 'translate_en' and r.get('keys') == 'nihao' for r in self.events))
        self.assertFalse(any(r['op'] == 'feedback' for r in self.events))
        self.assertTrue(any(r['op'] == 'composition' and r['state'] == 'end' for r in self.events))

    def test_switch_back_and_classic(self):
        output = self.drive('@beam_translate', 'nihao', '@!beam_translate', '{space}')
        self.assertIn('commit: 你好', output)
        self.assertTrue(any(r['op'] == 'feedback' for r in self.events))
        self.events.clear()
        output = self.drive('@beam_translate', 'nihao', '@beam_off', '{space}')
        self.assertIn('commit: 你好', output)

    def test_wait_keeps_keys_until_a_translatable_fragment_is_ready(self):
        self.failure = 'waiting'
        output = self.drive('@beam_translate', 'ni', '{space}', 'hao', '{space}')
        self.assertNotIn('commit: ni', output)
        self.assertIn('commit: Hello.', output)
        self.assertTrue(any(r.get('keys') == 'nihao' for r in self.events))

    def test_translation_errors_do_not_offer_chinese_candidates(self):
        for failure in ('unsupported_mode', 'timeout', 'malformed'):
            with self.subTest(failure=failure):
                self.failure = failure
                output = self.drive('@beam_translate', 'nihao', '{space}')
                self.assertNotIn('1. 你好', output)
                self.assertNotIn('Hello.', output)
                self.assertIn('nihao', output)
                self.assertNotIn('commit:', output)


if __name__ == '__main__':
    unittest.main()
