#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Configure a Linux Beam installation, query it, or install its model."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys

HERE = Path(__file__).resolve()
SHARE = Path(os.environ.get("BEAM_SHARE_DIR", "/usr/share/beam-ime"))
if (HERE.parent / "beamlib").exists():
    SHARE = HERE.parent.parent
else:
    sys.path.insert(0, str(SHARE))
from beamlib.model import read_manifest, install
from beamlib.config import atomic_write, schema_entry
from beamlib.files import install_files, restore_files
from beamlib.learning import install_component


def data_dir():
    if os.name == 'nt':
        return Path(os.environ['LOCALAPPDATA']) / 'beam-ime'
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "beam-ime"


def query(request):
    if os.name == 'nt':
        endpoint = json.loads((data_dir() / 'beamd.endpoint').read_text(encoding='utf-8-sig'))
        with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=10) as connection:
            connection.sendall((json.dumps({'id': 1, 'token': endpoint['token'], **request}) + '\n').encode())
            with connection.makefile('rb') as stream:
                return json.loads(stream.readline(65537))
    endpoint = os.environ.get("BEAM_SOCKET", f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp/beam-ime-' + str(os.getuid()))}/beam-ime/beamd.sock")
    if not os.environ.get("XDG_RUNTIME_DIR") and not os.environ.get("BEAM_SOCKET"):
        endpoint = f"/tmp/beam-ime-{os.getuid()}/beamd.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(endpoint)
        connection.sendall((json.dumps({"id": 1, **request}) + "\n").encode())
        with connection.makefile("rb") as stream:
            return json.loads(stream.readline(65537))


def service(*args):
    subprocess.run(["systemctl", "--user", *args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("disable")
    run = commands.add_parser("run")
    run.add_argument("--cpu", action="store_true")
    setup = commands.add_parser("setup")
    setup.add_argument("--model", type=Path, help="use an existing compatible GGUF without copying it")
    setup.add_argument("--cpu", action="store_true")
    model = commands.add_parser("model-install")
    model.add_argument("--manifest", type=Path, default=SHARE / "models/default.json")
    model.add_argument("--source", type=Path)
    model.add_argument("--download", action="store_true")
    candidate = commands.add_parser("query")
    candidate.add_argument("keys")
    candidate.add_argument("--context", default="")
    learn = commands.add_parser('learn')
    learn.add_argument('action', choices=('enable', 'disable', 'pause', 'resume', 'status', 'train', 'rollback', 'reset', 'install'))
    learn.add_argument('--yes', action='store_true', help='confirm erasing learning data')
    learn.add_argument('--manifest', type=Path)
    learn.add_argument('--source', type=Path, help='directory containing offline learning assets')
    learn.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.command == 'learn':
        request = {'op': 'learning', 'action': args.action}
        if args.action == 'reset':
            if not args.yes:
                parser.error('reset deletes personal learning data; repeat with --yes to confirm')
            request['confirm'] = True
        if args.action == 'install':
            if not args.manifest:
                if not args.download:
                    parser.error('use --download for automatic installation, or --manifest with --source for offline assets')
                platform = 'windows-x86_64' if os.name == 'nt' else 'linux-x86_64'
                args.manifest = read_manifest(SHARE / 'models/default.json')['learning'][platform]
            component = install_component(args.manifest, data_dir() / 'trainer', args.source, args.download)
            request['manifest'] = str(component)
        result = query(request)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok') else 1
    if args.command in {"setup", "disable", "model-install"} and os.geteuid() == 0:
        parser.error("run this command as your desktop user, without sudo")
    if args.command == "model-install":
        spec = read_manifest(args.manifest)
        print(install(spec, data_dir() / "models" / spec["filename"], args.source, args.download))
    elif args.command == "doctor":
        result = {"version": "0.2.1", "platform": sys.platform, "modelInstalled": (data_dir() / "models/beam-0.6b-q8_0.gguf").exists()}
        try:
            result["service"] = query({"op": "health"})
        except (OSError, ValueError):
            result["service"] = {"ok": False, "error": "unavailable"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "query":
        print(json.dumps(query({"op": "query", "keys": args.keys, "context": args.context, "beam_ms": 100}), ensure_ascii=False))
    elif args.command == "run":
        settings = json.loads((data_dir() / "settings.json").read_text())
        executable = shutil.which("beamd")
        if not executable:
            raise ValueError("beamd is not installed")
        cpu = args.cpu or settings.get("cpu", False)
        child = None
        stopped = False
        def stop(signum, frame):
            nonlocal stopped
            stopped = True
            if child: child.terminate()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        for attempt in range(3):
            child = subprocess.Popen([executable, "--model", settings["model"], "--ngl", "0" if cpu else "99"])
            code = child.wait()
            if stopped or code in (0, 2, 4):
                return code
            cpu = True
        return 1
    else:
        rime = Path(os.environ.get("BEAM_RIME_DIR", str(Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))) + "/fcitx5/rime"))
        unit_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
        if args.command == "disable":
            subprocess.run(["systemctl", "--user", "disable", "--now", "beam-ime.service"], check=False)
            schema_entry(rime / "default.custom.yaml", False)
            restore_files(data_dir() / "installed-files")
            (unit_dir / "beam-ime.service").unlink(missing_ok=True)
            service("daemon-reload")
            print("Beam disabled. Model and user dictionaries were kept. Redeploy Rime once.")
            return 0
        settings_path = data_dir() / "settings.json"
        previous = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        saved_model = Path(previous["model"]) if previous.get("model") else None
        model_path = args.model or saved_model or data_dir() / "models/beam-0.6b-q8_0.gguf"
        legacy = data_dir() / "models/beam.gguf"
        if not args.model and not model_path.exists() and legacy.exists(): model_path = legacy
        if not model_path.is_file():
            raise ValueError("install the model first: beamctl model-install --download")
        schema_source = SHARE / "rime/beam_ice.schema.yaml"
        if not schema_source.exists(): schema_source = SHARE / "src/rime/beam_ice.schema.yaml"
        schema_target = rime / "beam_ice.schema.yaml"
        resource_dir = SHARE / "rime-data"
        pairs = [(p, rime / p.relative_to(resource_dir)) for p in resource_dir.rglob("*") if p.is_file()]
        pairs.append((schema_source, schema_target))
        install_files(pairs, data_dir() / "installed-files")
        try:
            schema_entry(rime / "default.custom.yaml", True)
        except Exception:
            restore_files(data_dir() / "installed-files")
            raise
        atomic_write(settings_path, json.dumps({**previous, "model": str(model_path.resolve()), "cpu": args.cpu or previous.get("cpu", False)}).encode())
        unit = "[Unit]\nDescription=Beam IME\nAfter=graphical-session.target\n\n[Service]\nExecStart=/usr/bin/beamctl run\nRestart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n"
        atomic_write(unit_dir / "beam-ime.service", unit.encode())
        service("daemon-reload")
        service("enable", "--now", "beam-ime.service")
        service("restart", "beam-ime.service")
        print("Beam enabled. Redeploy Rime, then choose Beam in its schema menu.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
