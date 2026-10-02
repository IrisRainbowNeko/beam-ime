#!/usr/bin/env python3
"""Materialize the pinned rime-ice resources for a binary package."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def stage(destination):
    lock = json.loads((ROOT / "dependencies.lock.json").read_text())["rime-ice"]
    cache = ROOT / "build/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "rime-ice.tar.gz"
    if not archive.exists():
        temporary = archive.with_suffix(".part")
        with urllib.request.urlopen(lock["url"], timeout=60) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["sha256"]:
        raise ValueError("rime-ice archive checksum mismatch")
    source = cache / ("rime-ice-" + lock["revision"])
    if not source.exists():
        with tarfile.open(archive) as package:
            package.extractall(cache, filter="data")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for directory in ("cn_dicts", "en_dicts", "lua", "opencc"):
        shutil.copytree(source / directory, destination / directory, dirs_exist_ok=True)
    for pattern in ("*.schema.yaml", "*.dict.yaml", "symbols_*.yaml"):
        for item in source.glob(pattern): shutil.copy2(item, destination / item.name)
    shutil.copy2(source / "default.yaml", destination / "rime_ice_suggestion.yaml")
    shutil.copy2(source / "LICENSE", destination / "rime-ice.LICENSE.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    stage(parser.parse_args().destination)
