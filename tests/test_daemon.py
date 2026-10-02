import json
import os
from pathlib import Path
import socket
import subprocess
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
            "--socket", cls.endpoint, "--model", os.environ["BEAM_TEST_MODEL"], "--ngl", os.environ.get("BEAM_TEST_NGL", "0")],
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
