#!/usr/bin/env python3
"""Collect the supplied license texts of the Windows static dependencies."""
from pathlib import Path
import shutil
import urllib.request
import base64
import json

root = Path(__file__).resolve().parents[1]
sources = root/"build/win/src/librime/deps"
destination = root/"licenses/dependencies"
destination.mkdir(parents=True, exist_ok=True)
for component in sorted(sources.iterdir()) if sources.exists() else []:
    if not component.is_dir(): continue
    for pattern in ("LICENSE*", "COPYING*", "COPYRIGHT*"):
        for source in component.glob(pattern):
            if source.is_file(): shutil.copy2(source, destination/(component.name+"-"+source.name))
extras = {
    'nlohmann-json-MIT': Path('/usr/share/licenses/nlohmann-json/LICENSE.MIT'),
    'Vulkan-Headers': root/'build/win/src/Vulkan-Headers/LICENSE.md',
    'Vulkan-Headers-MIT': root/'build/win/src/Vulkan-Headers/LICENSES/MIT.txt',
    'Vulkan-Loader': root/'build/win/src/Vulkan-Loader/LICENSE.txt',
    'NSIS-COPYING': root/'build/downloads/nsis-3.11/COPYING',
    'MinGW-runtime': Path('/usr/share/licenses/mingw-w64-crt/COPYING.MinGW-w64-runtime.txt'),
    'GCC-runtime-exception': Path('/usr/share/licenses/libgcc/RUNTIME.LIBRARY.EXCEPTION'),
}
for name, source in extras.items():
    if source.is_file(): shutil.copy2(source, destination/name)
lua = root/'build/win/src/librime-lua/thirdparty/lua5.4/lua.h'
if lua.exists():
    text = lua.read_text()
    start = text.index('Copyright (C)')
    end = text.index('*/', start)
    (destination/'Lua-MIT').write_text(text[start:end].rstrip('*\n')+'\n')
boost = destination/'Boost-BSL-1.0'
if not boost.exists():
    with urllib.request.urlopen('https://api.github.com/repos/boostorg/boost/contents/LICENSE_1_0.txt?ref=boost-1.89.0', timeout=30) as response:
        boost.write_bytes(base64.b64decode(json.load(response)['content']))
print(destination)
