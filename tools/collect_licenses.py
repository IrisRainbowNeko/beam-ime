#!/usr/bin/env python3
"""Collect the supplied license texts of the Windows static dependencies."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
sources = root/"build/win/src/librime/deps"
destination = root/"licenses/dependencies"
destination.mkdir(parents=True, exist_ok=True)
for component in sorted(sources.iterdir()) if sources.exists() else []:
    if not component.is_dir(): continue
    for pattern in ("LICENSE*", "COPYING*", "COPYRIGHT*"):
        for source in component.glob(pattern):
            if source.is_file(): shutil.copy2(source, destination/(component.name+"-"+source.name))
print(destination)
