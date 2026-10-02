# SPDX-License-Identifier: Apache-2.0
"""Small, reversible changes to a user's Rime configuration."""
from pathlib import Path
import os
import tempfile


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".beam-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def schema_entry(path, enabled):
    import yaml
    path = Path(path)
    old = path.read_bytes() if path.exists() else b""
    data = yaml.safe_load(old) or {}
    if not isinstance(data, dict):
        raise ValueError("Rime configuration must be a mapping")
    patch = data.setdefault("patch", {})
    if not isinstance(patch, dict):
        raise ValueError("Rime patch must be a mapping")
    key = "schema_list/@before 0"
    if enabled:
        if key in patch and patch[key] != {"schema": "beam_ice"}:
            raise ValueError("schema_list/@before 0 is already used; add beam_ice to your schema_list manually")
        listing = patch.get("schema_list")
        if isinstance(listing, list):
            if not any(isinstance(x, dict) and x.get("schema") == "beam_ice" for x in listing):
                listing.insert(0, {"schema": "beam_ice"})
        else:
            patch[key] = {"schema": "beam_ice"}
    else:
        if patch.get(key) == {"schema": "beam_ice"}:
            del patch[key]
        if isinstance(patch.get("schema_list"), list):
            patch["schema_list"] = [x for x in patch["schema_list"] if not (isinstance(x, dict) and x.get("schema") == "beam_ice")]
    if enabled and old and not path.with_suffix(path.suffix + ".beam-backup").exists():
        atomic_write(path.with_suffix(path.suffix + ".beam-backup"), old)
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())
