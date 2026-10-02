# SPDX-License-Identifier: Apache-2.0
"""Model transfer with bounded, verified, atomic replacement."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request


def read_manifest(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or value.get("promptVersion") != "keys_llm_v1":
        raise ValueError("unsupported model manifest or prompt version")
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.gguf", value.get("filename", "")):
        raise ValueError("invalid model filename")
    if not re.fullmatch(r"[a-f0-9]{64}", value.get("sha256", "")):
        raise ValueError("invalid SHA-256")
    if type(value.get("size")) is not int or value["size"] <= 0:
        raise ValueError("invalid model size")
    if not value.get("url", "").startswith("https://"):
        raise ValueError("model URL must use HTTPS")
    return value


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path, manifest):
    path = Path(path)
    if path.stat().st_size != manifest["size"] or sha256(path) != manifest["sha256"]:
        raise ValueError("model size or SHA-256 mismatch")


def install(manifest, destination, source=None, download=False):
    destination = Path(destination)
    if destination.exists():
        try:
            verify(destination, manifest)
            return destination
        except ValueError:
            pass
    if source is None and not download:
        raise ValueError("pass --download to fetch the model, or --source FILE for an offline install")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".beam-", suffix=".part", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            stream = open(source, "rb") if source else urllib.request.urlopen(manifest["url"], timeout=60)
            with stream:
                if not source and not stream.geturl().startswith("https://"):
                    raise ValueError("refusing an insecure download redirect")
                total = 0
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    total += len(block)
                    if total > manifest["size"]:
                        raise ValueError("model exceeds the declared size")
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())
        verify(name, manifest)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return destination
