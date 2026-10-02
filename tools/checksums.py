#!/usr/bin/env python3
"""Write a checksum list for the files in a release directory."""
import argparse
from pathlib import Path
from beamlib.model import sha256, read_manifest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
parser.add_argument('--model-manifest', type=Path, help='also list the independently distributed model')
args = parser.parse_args()
directory = args.directory
items = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS")
for item in items:
    if item.stat().st_size >= 2 * 1024**3: raise SystemExit(f"asset exceeds 2 GiB: {item.name}")
checksums = {p.name: sha256(p) for p in items}
if args.model_manifest:
    model = read_manifest(args.model_manifest)
    checksums.setdefault(model['filename'], model['sha256'])
(directory/"SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())))
