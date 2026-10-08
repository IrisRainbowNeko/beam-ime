# SPDX-License-Identifier: Apache-2.0
"""Install the separately distributed, relocatable learning component."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import zipfile

from .model import sha256


def install_component(manifest_path, destination, source=None, download=False):
    destination = Path(destination)
    if str(manifest_path).startswith('https://'):
        if not download:
            raise ValueError('use --download to allow learning component downloads')
        with urllib.request.urlopen(str(manifest_path), timeout=60) as remote:
            manifest_bytes = remote.read(65537)
        if len(manifest_bytes) > 65536:
            raise ValueError('learning manifest is too large')
    else:
        manifest_bytes = Path(manifest_path).read_bytes()
    spec = json.loads(manifest_bytes.decode('utf-8-sig'))
    platform = 'windows-x86_64' if os.name == 'nt' else 'linux-x86_64'
    if spec.get('schemaVersion') != 2 or spec.get('platform') != platform or spec.get('backend') != 'vulkan':
        raise ValueError('incompatible learning component manifest')
    assets = spec.get('assets')
    if not isinstance(assets, list) or not assets:
        raise ValueError('learning manifest has no assets')
    destination.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(manifest_bytes).hexdigest()[:16]
    final = destination / identity
    if (final / 'component.json').exists():
        return final / 'component.json'
    with tempfile.TemporaryDirectory(prefix='.install-', dir=destination) as temporary:
        temporary = Path(temporary)
        unpacked = temporary / 'component'
        unpacked.mkdir()
        for asset in assets:
            filename = asset['filename']
            if Path(filename).name != filename or not filename.endswith('.zip') or asset['size'] <= 0:
                raise ValueError('invalid learning asset')
            archive = temporary / filename
            if source:
                shutil.copyfile(Path(source) / filename, archive)
            elif download:
                if not asset['url'].startswith('https://'):
                    raise ValueError('learning downloads require HTTPS')
                with urllib.request.urlopen(asset['url'], timeout=60) as remote, archive.open('wb') as output:
                    shutil.copyfileobj(remote, output)
            else:
                raise ValueError('use --source for offline installation or --download to allow downloads')
            if archive.stat().st_size != asset['size'] or sha256(archive) != asset['sha256']:
                raise ValueError('learning asset checksum mismatch: ' + filename)
            with zipfile.ZipFile(archive) as package:
                for member in package.infolist():
                    name = PurePosixPath(member.filename)
                    if name.is_absolute() or '..' in name.parts or '\\' in member.filename or ':' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('unsafe learning archive entry')
                    target = unpacked.joinpath(*name.parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if target.exists():
                        raise ValueError('overlapping learning assets')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as incoming, target.open('wb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    if os.name != 'nt':
                        target.chmod(0o755 if member.external_attr >> 16 & 0o111 else 0o644)
        component = json.loads((unpacked / 'component.json').read_text(encoding='utf-8'))
        if component['base_sha256'] != spec['base_sha256'] or component['backend'] != spec['backend']:
            raise ValueError('learning asset metadata mismatch')
        os.replace(unpacked, final)
    return final / 'component.json'
