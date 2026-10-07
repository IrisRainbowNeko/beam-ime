#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Package the native Vulkan learning component, reusing the installed GGUF."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from beamlib.model import sha256

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True, help='tools/build_learning.py install prefix')
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--pinyin', type=Path, required=True)
    parser.add_argument('--replay', type=Path, default=ROOT / 'src/trainer/replay.json')
    parser.add_argument('--platform', choices=('linux-x86_64', 'windows-x86_64'), required=True)
    parser.add_argument('--release-url', required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text().strip()
    name = f'beam-learning-{version}-{args.platform}-vulkan'
    windows = args.platform.startswith('windows')
    executable = 'beam-trainer.exe' if windows else 'beam-trainer'
    libraries = (['llama.dll', 'ggml.dll', 'ggml-base.dll', 'qvac-ggml-cpu.dll', 'qvac-ggml-vulkan.dll'] if windows else
                 ['libllama.so.0', 'libggml.so.0', 'libggml-base.so.0', 'libqvac-ggml-cpu.so', 'libqvac-ggml-vulkan.so'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='beam-learning-package-') as temporary:
        stage = Path(temporary)
        (stage / 'bin').mkdir()
        for filename in [executable, *libraries]:
            # Copy SONAME contents once; ZIP installation does not need symlinks.
            shutil.copy2(args.runtime / 'bin' / filename, stage / 'bin' / filename)
        shutil.copy2(args.pinyin, stage / 'pinyin.tsv')
        replay = json.loads(args.replay.read_text(encoding='utf-8'))
        if not isinstance(replay, list) or not 16 <= len(replay) <= 256:
            raise ValueError('replay must contain 16 to 256 rows')
        shutil.copy2(args.replay, stage / 'replay.json')
        shutil.copytree(args.runtime / 'licenses', stage / 'licenses')
        shutil.copy2(ROOT / 'LICENSE', stage / 'licenses/Beam-Apache-2.0.txt')
        shutil.copy2(ROOT / 'licenses/dependencies/nlohmann-json-MIT', stage / 'licenses')
        shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', stage)
        if windows:
            for filename in ('GCC-runtime-exception', 'MinGW-runtime'):
                shutil.copy2(ROOT / 'licenses/dependencies' / filename, stage / 'licenses')
        component = {'schemaVersion': 2, 'version': version, 'base_sha256': sha256(args.model),
                     'tokenizer': 'gguf-embedded', 'prompt_version': 'keys_llm_v1',
                     'recipe': 'beam-personal-r8-qvac-v1', 'backend': 'vulkan',
                     'executable': 'bin/' + executable, 'pinyin': 'pinyin.tsv', 'replay': 'replay.json'}
        (stage / 'component.json').write_text(json.dumps(component, indent=2), encoding='utf-8')
        archive = args.output_dir / (name + '.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for item in sorted(stage.rglob('*')):
                if item.is_file():
                    output.write(item, item.relative_to(stage).as_posix())
        manifest = {'schemaVersion': 2, 'version': version, 'platform': args.platform, 'backend': 'vulkan',
                    'base_sha256': component['base_sha256'], 'assets': [{'filename': archive.name,
                    'size': archive.stat().st_size, 'sha256': sha256(archive),
                    'url': args.release_url.rstrip('/') + '/' + archive.name}]}
        path = args.output_dir / (name + '.json')
        path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(path)
        print(f'{archive.stat().st_size / 1024**2:.2f} MiB compressed; no Python or additional base weights')


if __name__ == '__main__':
    main()
