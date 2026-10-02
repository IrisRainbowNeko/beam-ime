#!/usr/bin/env python3
"""Write a checksum list for the files in a release directory."""
import argparse
from pathlib import Path
from beamlib.model import sha256

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
directory = parser.parse_args().directory
items = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS")
for item in items:
    if item.stat().st_size >= 2 * 1024**3: raise SystemExit(f"asset exceeds 2 GiB: {item.name}")
(directory/"SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in items))
