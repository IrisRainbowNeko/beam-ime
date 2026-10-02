#!/usr/bin/env python3
"""Exercise the Windows CPU binary under isolated Wine with Unicode/space paths."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', type=Path, default=Path('build/win/out'))
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='beam-wine-') as temporary:
        root = Path(temporary)
        binary = root / '\u6d4b\u8bd5 install'
        binary.mkdir()
        for source in args.bin.iterdir():
            if source.name in ('beamd.exe', 'ggml-base.dll', 'ggml.dll', 'ggml-cpu.dll', 'llama.dll',
                               'rime.dll', 'beam-rime-host-test.exe'):
                shutil.copy2(source, binary / source.name)
        model = root / '\u6a21\u578b test.gguf'
        model.symlink_to(args.model.resolve())
        env = {**os.environ, 'WINEPREFIX': str(root / 'prefix'), 'WINEDEBUG': '-all',
               'WINEDLLOVERRIDES': 'vulkan-1=d;winemenubuilder.exe=d'}
        process = None
        try:
            subprocess.run(['wineboot', '-u'], env=env, check=True, timeout=90,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            host_test = binary / 'beam-rime-host-test.exe'
            if host_test.exists():
                subprocess.run(['wine', str(host_test)], env=env, cwd=binary, check=True, timeout=30)
                print('Unicode GUI-host Rime initialization passed.')
            with (root / 'daemon.log').open('w') as log:
                process = subprocess.Popen(['wine', str(binary / 'beamd.exe'), '--model',
                    # Preserve the Unicode symlink name instead of resolving it.
                    'Z:' + str(model).replace('/', '\\'), '--ngl', '0', '--threads', '2'],
                    env=env, stdout=log, stderr=log)
                endpoint = None
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError((root / 'daemon.log').read_text(errors='replace'))
                    files = list((root / 'prefix/drive_c/users').glob('*/AppData/Local/beam-ime/beamd.endpoint'))
                    if files:
                        endpoint = json.loads(files[0].read_text())
                        try:
                            answer = query(endpoint, {'op': 'health'})
                            if answer.get('ok'):
                                break
                        except (OSError, ValueError):
                            pass
                    time.sleep(.1)
                else:
                    raise TimeoutError((root / 'daemon.log').read_text(errors='replace'))
                assert answer['backend'] == 'cpu', answer
                for request in ({'op': 7}, {'op': 'query', 'keys': []}):
                    assert not query(endpoint, request)['ok']
                result = query(endpoint, {'op': 'query', 'keys': 'nihao', 'beam_ms': 0})
                assert result['ok'] and result['candidates'], result
                print(json.dumps({'health': answer, 'query': result}, ensure_ascii=False))
        finally:
            subprocess.run(['wineserver', '-k'], env=env, check=False)
            if process:
                process.wait(timeout=15)
            subprocess.run(['wineserver', '-w'], env=env, check=False, timeout=30)


def query(endpoint, request):
    with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=15) as connection:
        connection.sendall((json.dumps({'id': 1, 'token': endpoint['token'], **request}) + '\n').encode())
        with connection.makefile('rb') as stream:
            return json.loads(stream.readline())


if __name__ == '__main__':
    main()
