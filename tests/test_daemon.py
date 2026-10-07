import json
import os
from pathlib import Path
import socket
import subprocess
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get("BEAM_TEST_MODEL") and os.name == "posix", "set BEAM_TEST_MODEL for real model tests")
class DaemonIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.endpoint = str(Path(cls.temp.name)/"runtime/beamd.sock")
        cls.process = subprocess.Popen([os.environ.get("BEAM_TEST_BINARY", "build/release/bin/beamd"),
            "--socket", cls.endpoint, "--learning-dir", str(Path(cls.temp.name)/'learning'),
            "--model", os.environ["BEAM_TEST_MODEL"], "--ngl", os.environ.get("BEAM_TEST_NGL", "0")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for attempt in range(200):
            if cls.process.poll() is not None: raise RuntimeError("beamd exited during startup")
            try:
                if cls.call({"id": 1, "op": "health"})["ok"]: return
            except (OSError, ValueError): time.sleep(.1)
        cls.process.terminate();cls.process.wait(timeout=10)
        raise RuntimeError("beamd startup timed out")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try: cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired: cls.process.kill();cls.process.wait()
        cls.temp.cleanup()

    @classmethod
    def call(cls, request):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(10);client.connect(cls.endpoint)
            client.sendall(json.dumps(request).encode()+b"\n")
            with client.makefile("rb") as stream: return json.loads(stream.readline())

    def test_bad_requests_do_not_kill_service(self):
        for request in ({"id":1,"op":2},{"id":1,"op":"query","keys":[]},
                        {"id":1,"op":"query","keys":"nh","max":"five"}):
            self.assertFalse(self.call(request)["ok"])
        self.assertTrue(self.call({"id":2,"op":"health"})["ok"])

    def test_generation_and_context_switch(self):
        for context in ("", "今天讨论软件", ""):
            answer=self.call({"id":3,"op":"query","keys":"nihao","context":context,"beam_ms":0})
            self.assertTrue(answer["ok"])
            self.assertTrue(answer["candidates"])

    def test_learning_feedback_lifecycle(self):
        event = {'op': 'feedback', 'session': 'test', 'composition': '1', 'event_id': 'test:1',
                 'keys': 'xinglanyinqing', 'text': '星澜引擎', 'context': '开发组件', 'first': '星蓝引擎'}
        self.call({'id': 1, 'op': 'learning', 'action': 'reset', 'confirm': True})
        self.assertFalse(self.call({'id': 2, **event})['learned'])
        self.call({'id': 3, 'op': 'learning', 'action': 'enable'})
        self.assertTrue(self.call({'id': 4, **event})['learned'])
        self.assertFalse(self.call({'id': 5, **event})['learned'])
        answer = self.call({'id': 6, 'op': 'query', 'keys': 'xlyq', 'context': '开发组件', 'beam_ms': 0})
        self.assertIn('星澜引擎', answer['candidates'])
        self.assertIn('personal', answer['sources'])
        self.call({'id': 7, 'op': 'learning', 'action': 'pause'})
        self.assertFalse(self.call({'id': 8, **event, 'event_id': 'test:2'})['learned'])
        self.assertIn('星澜引擎', self.call({'id': 9, 'op': 'query', 'keys': 'xlyq', 'beam_ms': 0})['candidates'])
        self.assertFalse(self.call({'id': 10, 'op': 'learning', 'action': 'reset'})['ok'])
        reset = self.call({'id': 11, 'op': 'learning', 'action': 'reset', 'confirm': True})
        self.assertEqual(reset['learning']['words'], 0)
        self.assertFalse(reset['learning']['enabled'])

    def test_feedback_is_not_coalesced_with_queries(self):
        self.call({'id': 1, 'op': 'learning', 'action': 'enable'})
        requests = [{'id': 2, 'op': 'feedback', 'session': 'batch', 'composition': '1', 'event_id': 'batch:1',
                     'keys': 'jichuanwangguan', 'text': '霁川网关'},
                    {'id': 3, 'op': 'feedback', 'session': 'batch', 'composition': '2', 'event_id': 'batch:2',
                     'keys': 'wuqiaokuangjia', 'text': '雾桥框架'},
                    {'id': 4, 'op': 'query', 'keys': 'wqkj', 'beam_ms': 0}]
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(20); client.connect(self.endpoint)
            client.sendall(('\n'.join(json.dumps(r) for r in requests) + '\n').encode())
            with client.makefile('rb') as stream:
                answers = [json.loads(stream.readline()) for _ in range(3)]
        self.assertTrue(answers[0]['learned']); self.assertTrue(answers[1]['learned'])
        self.assertIn('雾桥框架', answers[2]['candidates'])
        self.call({'id': 5, 'op': 'learning', 'action': 'reset', 'confirm': True})

    def test_training_progress_pause_and_resume(self):
        root = Path(self.temp.name)
        script = root/'trainer.py'
        script.write_text('''#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
job=json.loads(Path(sys.argv[-1]).read_text(encoding="utf-8"))
root=Path(job["directory"])
with (root/"progress.jsonl").open("a",encoding="utf-8") as output:
    output.write('{"state":"running","phase":"training",'); output.flush()
    time.sleep(.5)
    output.write('"step":1}\\n'); output.flush()
while not (root/"pause.json").exists(): time.sleep(.02)
sys.exit(75)
''', encoding='utf-8')
        script.chmod(0o755)
        def command(action, **extra):
            reply=self.call({'id':1,'op':'learning','action':action,**extra})
            self.assertTrue(reply['ok'],reply)
            return reply['learning']
        def wait(predicate):
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                value=command('status')
                self.assertFalse(value['last_error'],value)
                if predicate(value['training']): return
                time.sleep(.03)
            self.fail('training progress did not converge')
        command('reset',confirm=True); command('enable')
        base=command('status')['base_sha256']
        manifest=root/'component.json'
        manifest.write_text(json.dumps({'schemaVersion':2,'base_sha256':base,'tokenizer':'gguf-embedded',
            'recipe':'beam-personal-r8-qvac-v1','prompt_version':'keys_llm_v1','backend':'vulkan',
            'executable':str(script),'pinyin':str(script),'replay':str(script)}))
        command('install',manifest=str(manifest))
        self.call({'id':2,'op':'feedback','session':'progress','composition':'1','event_id':'progress:1',
                   'keys':'nihao','text':'你好'})
        try:
            for _ in range(2):
                command('train')
                wait(lambda s:s.get('step')==1)
                self.call({'id':3,'op':'query','keys':'nihao','beam_ms':0})
                wait(lambda s:s.get('state')=='paused')
        finally:
            command('pause')
            wait(lambda s:s.get('state')=='paused')
            command('reset',confirm=True)

    def test_reused_buffers_after_editing_and_context_switch(self):
        cases = [("nihao", ""), ("nhsj", ""), ("yonglinux", ""),
                 ("nihao", "今天讨论软件"), ("ni'hao", "")]
        expected = {}
        for keys, context in cases:
            answer = self.call({"id": 4, "op": "query", "keys": keys,
                                "context": context, "beam_ms": 2000})
            self.assertTrue(answer["beam_complete"])
            expected[keys, context] = answer["candidates"]
        for keys, context in reversed(cases):
            with self.subTest(keys=keys, context=context):
                # Exercise draft acceptance/rejection, backspace, and switching out of Top-1 mode.
                for edited in (keys[:1], keys, keys[:-1]):
                    self.call({"id": 5, "op": "query", "keys": edited,
                               "context": context, "beam_ms": 0})
                answer = self.call({"id": 6, "op": "query", "keys": keys,
                                    "context": context, "beam_ms": 2000})
                self.assertTrue(answer["beam_complete"])
                self.assertEqual(answer["candidates"], expected[keys, context])

    def test_reused_batch_survives_beam_timeout(self):
        request = {"id": 7, "op": "query", "keys": "nihao", "beam_ms": 2000}
        expected = self.call(request)
        self.assertTrue(expected["beam_complete"])
        interrupted = self.call({"id": 8, "op": "query", "keys": "nhsjwsny", "beam_ms": 1})
        self.assertTrue(interrupted["ok"])
        self.assertFalse(interrupted["beam_complete"])
        top = self.call({"id": 9, "op": "query", "keys": "nihao", "max": 1, "beam_ms": 2000})
        self.assertFalse(top["beam_complete"])
        self.assertTrue(top["candidates"])
        answer = self.call(request)
        self.assertTrue(answer["beam_complete"])
        self.assertEqual(answer["candidates"], expected["candidates"])
        self.assertIn(top["candidates"][0], answer["candidates"])


@unittest.skipUnless(os.environ.get('BEAM_TEST_ADAPTER') and os.environ.get('BEAM_TEST_MODEL') and os.name == 'posix',
                     'set BEAM_TEST_ADAPTER to a real adapter manifest for lifecycle tests')
class AdapterLifecycle(unittest.TestCase):
    def test_activation_disconnect_failure_rollback_and_model_change(self):
        with tempfile.TemporaryDirectory(prefix='beam-adapter-test-') as temporary:
            root = Path(temporary)
            endpoint = str(root/'beamd.sock')
            learning = root/'learning'
            model = Path(os.environ['BEAM_TEST_MODEL'])
            manifest = json.loads(Path(os.environ['BEAM_TEST_ADAPTER']).read_text())
            process = None
            def call(request, client=None):
                owned = client is None
                if owned:
                    client = socket.socket(socket.AF_UNIX); client.settimeout(30)
                try:
                    if owned: client.connect(endpoint)
                    client.sendall(json.dumps({'id': 1, **request}).encode()+b'\n')
                    with client.makefile('rb') as stream:
                        result = json.loads(stream.readline())
                    self.assertTrue(result['ok'], result)
                    return result
                finally:
                    if owned: client.close()
            def status(): return call({'op': 'health'})['learning']
            def wait(predicate):
                deadline = time.monotonic()+30
                while time.monotonic() < deadline:
                    value = status()
                    if predicate(value): return value
                    time.sleep(.1)
                self.fail('adapter state did not converge: '+json.dumps(value))
            def start():
                nonlocal process
                process = subprocess.Popen([os.environ['BEAM_TEST_BINARY'], '--model', str(model),
                    '--socket', endpoint, '--learning-dir', str(learning),
                    '--ngl', os.environ.get('BEAM_TEST_NGL', '0'), '--threads', '4'],
                    stdout=subprocess.DEVNULL, stderr=log)
                for _ in range(300):
                    self.assertIsNone(process.poll(), 'daemon exited; inspect model/backend support')
                    try:
                        return call({'op': 'health'})
                    except (OSError, ValueError): time.sleep(.1)
                self.fail('daemon startup timed out')
            def stop():
                process.terminate(); process.wait(timeout=20)
            def latency():
                times=[]
                for i in range(12):
                    started=time.perf_counter()
                    answer=call({'op':'query','keys':'nihao' if i%2 else 'shijie','beam_ms':0})
                    self.assertTrue(answer['candidates'])
                    if i>=2: times.append(1000*(time.perf_counter()-started))
                return sorted(times)[int(.95*len(times))]
            with (root/'daemon.log').open('w') as log:
                try:
                    start(); stop()
                    versions=[]
                    for name, clock in [('previous', 32), ('current', 64)]:
                        directory=learning/'generations'/name; directory.mkdir(parents=True)
                        shutil.copy2(manifest['path'], directory/'adapter.gguf')
                        value={**manifest, 'id':name, 'clock':clock, 'path':str(directory/'adapter.gguf'),
                               'checkpoint':str(directory/'checkpoint')}
                        (directory/'manifest.json').write_text(json.dumps(value)); versions.append(value)
                    with sqlite3.connect(learning/'learning.sqlite3') as db:
                        for name,value in {'enabled':True, 'active_adapter':versions[1],
                            'adapter_'+manifest['base_sha256']:versions[1],
                            'previous_'+manifest['base_sha256']:versions[0]}.items():
                            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(name,json.dumps(value)))
                    health=start(); wait(lambda s:s['adapter_loaded'])
                    clients=[]
                    for index in (1,2):
                        client=socket.socket(socket.AF_UNIX); client.settimeout(30); client.connect(endpoint)
                        clients.append(client)
                        call({'op':'composition','session':str(index),'composition':'1','state':'begin'},client)
                    call({'op':'learning','action':'disable'})
                    self.assertTrue(status()['adapter_loaded'])
                    clients[0].close()
                    wait(lambda s:s['active_compositions']==1)
                    self.assertTrue(status()['adapter_loaded'])
                    call({'op':'composition','session':'2','composition':'1','state':'cancel'},clients[1])
                    clients[1].close()
                    wait(lambda s:not s['adapter_loaded'])
                    baseline=latency()
                    call({'op':'learning','action':'enable'}); wait(lambda s:s['adapter_loaded'])
                    adapted=latency()
                    client=socket.socket(socket.AF_UNIX); client.settimeout(30); client.connect(endpoint)
                    with client:
                        call({'op':'composition','session':'3','composition':'1','state':'begin'},client)
                        call({'op':'learning','action':'rollback'})
                        self.assertEqual(status()['adapter']['id'],'current')
                        call({'op':'composition','session':'3','composition':'1','state':'end'},client)
                    wait(lambda s:s['adapter'].get('id')=='previous')
                    with sqlite3.connect(learning/'learning.sqlite3') as db:
                        active=json.loads(db.execute("SELECT value FROM settings WHERE name='active_adapter'").fetchone()[0])
                        self.assertEqual(active['checkpoint'],versions[0]['checkpoint'])
                    Path(versions[1]['path']).write_bytes(b'invalid adapter')
                    call({'op':'learning','action':'rollback'})
                    state=wait(lambda s:bool(s['last_error']))
                    self.assertTrue(state['adapter_loaded']); self.assertEqual(state['adapter']['id'],'previous')
                    self.assertTrue(call({'op':'query','keys':'nihao','beam_ms':0})['candidates'])
                    stop()
                    changed=root/'new-base.gguf'; shutil.copyfile(model,changed)
                    with changed.open('ab') as output: output.write(b'beam base revision test')
                    model=changed
                    state=start()['learning']
                    self.assertNotEqual(state['base_sha256'],manifest['base_sha256'])
                    self.assertFalse(state['adapter_loaded']); self.assertEqual(state['adapter'],{})
                    print(f"adapter backend={health['backend']} baseline_p95_ms={baseline:.1f} adapted_p95_ms={adapted:.1f}")
                finally:
                    if process and process.poll() is None: stop()
