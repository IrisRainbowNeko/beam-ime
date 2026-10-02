# SPDX-License-Identifier: Apache-2.0
"""Restore only files still owned by the installer; retain user modifications."""
import json
from pathlib import Path
import uuid
from .config import atomic_write
from .model import sha256


def install_files(pairs, state):
    state = Path(state)
    manifest = state / "files.json"
    before = manifest.read_bytes() if manifest.exists() else None
    records = json.loads(before) if before else {}
    changed = []
    try:
        for source, target in pairs:
            target = Path(target).absolute()
            key = str(target)
            record = records.get(key)
            current = target.read_bytes() if target.exists() else None
            if record and current is not None and sha256(target) != record["installed"]:
                continue
            changed.append((target, current))
            if record is None:
                backup = state / (uuid.uuid4().hex + ".bak")
                if current is not None: atomic_write(backup, current)
                record = {"backup": str(backup), "existed": current is not None}
            record["installed"] = sha256(source)
            records[key] = record
            atomic_write(manifest, json.dumps(records, indent=2).encode())
            atomic_write(target, Path(source).read_bytes())
    except Exception:
        for target, current in reversed(changed):
            if current is None: target.unlink(missing_ok=True)
            else: atomic_write(target, current)
        if before is None: manifest.unlink(missing_ok=True)
        else: atomic_write(manifest, before)
        raise


def restore_files(state):
    state = Path(state)
    manifest = state / "files.json"
    if not manifest.exists(): return
    for name, record in json.loads(manifest.read_text()).items():
        target = Path(name)
        if target.exists() and sha256(target) != record["installed"]:
            continue
        if record["existed"]: atomic_write(target, Path(record["backup"]).read_bytes())
        else: target.unlink(missing_ok=True)
    manifest.replace(state / "restored-files.json")
