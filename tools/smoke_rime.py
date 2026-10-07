#!/usr/bin/env python3
"""Test the packaged Linux Rime resources in an isolated user directory."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from fetch_rime_data import stage

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=ROOT / 'build/release')
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    binary = args.build.resolve() / 'bin'
    libdir = subprocess.check_output(['pkg-config', '--variable=libdir', 'rime'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='beam-rime-') as temporary:
        root = Path(temporary)
        user = root / 'rime'
        stage(user)
        shutil.copy2(ROOT / 'src/rime/beam_ice.schema.yaml', user / 'beam_ice.schema.yaml')
        (user / 'default.custom.yaml').write_text('patch:\n  schema_list:\n    - schema: beam_ice\n')
        # A CPU query can exceed the normal interactive budget on a CI host.
        (user / 'beam_ice.custom.yaml').write_text('patch:\n  beam/timeout_ms: 3000\n  beam/beam_ms: 0\n')
        plugins = root / 'plugins'
        plugins.mkdir()
        for plugin in (Path(libdir) / 'rime-plugins').glob('*.so'):
            if plugin.name != 'librime-beam.so':
                shutil.copy2(plugin, plugins / plugin.name)
        shutil.copy2(binary / 'librime-beam.so', plugins / 'librime-beam.so')
        endpoint = root / 'runtime/beamd.sock'
        env = {**os.environ, 'BEAM_SOCKET': str(endpoint), 'GLOG_log_dir': str(root)}
        command = ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                   '--ro-bind', str(plugins), str(Path(libdir) / 'rime-plugins'),
                   str(binary / 'rime_smoke'), str(user), 'beam_ice']
        daemon = None
        try:
            daemon = subprocess.Popen([str(binary / 'beamd'), '--model', str(args.model.resolve()),
                '--ngl', '0', '--threads', '2', '--socket', str(endpoint), '--learning-dir', str(root / 'learning')], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(300):
                if daemon.poll() is not None:
                    raise RuntimeError('Daemon exited during startup')
                if endpoint.exists():
                    with socket.socket(socket.AF_UNIX) as client:
                        client.settimeout(15)
                        client.connect(str(endpoint))
                        client.sendall(b'{"id":1,"op":"health"}\n')
                        with client.makefile('rb') as stream:
                            assert json.loads(stream.readline())['ok']
                    break
                time.sleep(.1)
            result = subprocess.run(command + ['@beam_learning', 'nihao', '{space}', 'shijie', '{Escape}'], env=env,
                                    capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stderr)
            print(result.stdout)
            if 'commit: ' not in result.stdout:
                raise RuntimeError(result.stderr + '\nNo committed candidate')
            import sqlite3
            with sqlite3.connect(root / 'learning/learning.sqlite3') as db:
                learned = db.execute('SELECT COUNT(*) FROM recent').fetchone()[0]
            if learned != 1:
                raise RuntimeError(f'Expected exactly one committed learning event, got {learned}')
            daemon.terminate()
            daemon.wait(timeout=15)
            fallback = subprocess.run(command + ['nihao', '{space}'], env=env, check=True,
                                      capture_output=True, text=True, timeout=60)
            print('Without daemon:\n' + fallback.stdout)
            if 'commit: ' not in fallback.stdout:
                raise RuntimeError(fallback.stderr + '\nDictionary fallback did not commit')
        finally:
            if daemon and daemon.poll() is None:
                daemon.terminate()
                try:
                    daemon.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait()


if __name__ == '__main__':
    main()
