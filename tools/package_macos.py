#!/usr/bin/env python3
"""Create the macOS (Squirrel) light/offline installer packages from an existing build.

    python3 tools/package_macos.py --build build/macos/build --squirrel-pkg build/macos/downloads/Squirrel-1.1.2.pkg \
        --squirrel-executable build/macos/squirrel-src/Squirrel [--model beam-0.6b-q8_0.gguf --adapter beam-translate-en-v1.gguf]

Runs on macOS (pkgbuild, productbuild, codesign). The payload goes to /Library/Application Support/Beam;
postinstall puts the Rime plugin and the patched Squirrel executable (skin images) into Squirrel.app, re-signs
it ad hoc, and runs `beamctl setup` for the logged-in user. The product also carries the official Squirrel package, installed when Squirrel is missing
or older than the pinned version.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape
from fetch_rime_data import stage as stage_rime
from beamlib.model import verify, sha256

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/Library/Application Support/Beam'
IDENTIFIER = 'io.github.irisrainbowneko.beam-ime'
MACHO = {b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
# Personal learning needs the trainer, which is not built for macOS yet.
LEARNING_SWITCHES = ('beam_learning_install', 'beam_learning,', 'beam_learning_paused')


def macho(path):
    with path.open('rb') as stream:
        return stream.read(4) in MACHO


def distribution(version, squirrel, offline):
    note = ('离线版已包含模型（约 610 MiB）和译英 adapter（约 154 MiB）。' if offline else
            '安装后会在后台下载模型（约 610 MiB）和译英 adapter（约 154 MiB），下载完成前鼠须管照常显示雾凇候选。')
    return f'''<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Beam {escape(version)}</title>
  <options customize="never" require-scripts="true" hostArchitectures="arm64"/>
  <domains enable_localSystem="true"/>
  <volume-check><allowed-os-versions><os-version min="13.0"/></allowed-os-versions></volume-check>
  <installation-check script="beam_check()"/>
  <welcome file="welcome.html" mime-type="text/html"/>
  <script><![CDATA[
function squirrel_version() {{
  var bundle = system.files.bundleAtPath('/Library/Input Methods/Squirrel.app');
  return bundle ? (bundle.CFBundleVersion || '') : '';
}}
function need_squirrel() {{ return squirrel_version() != '{squirrel['version']}'; }}
function beam_check() {{
  if (system.sysctl('hw.optional.arm64') != 1) {{
    my.result.type = 'Fatal'; my.result.title = '需要 Apple 芯片';
    my.result.message = 'Beam 目前只支持 Apple 芯片（M1 或更新）的 Mac。'; return false;
  }}
  var installed = squirrel_version();
  if (installed && system.compareVersions(installed, '{squirrel['version']}') > 0) {{
    my.result.type = 'Fatal'; my.result.title = '鼠须管版本不兼容';
    my.result.message = '已安装鼠须管 ' + installed + '；这一版 Beam 只支持鼠须管 {squirrel['version']}，请等待 Beam 更新。'; return false;
  }}
  return true;
}}
  ]]></script>
  <choices-outline>
    <line choice="squirrel"/>
    <line choice="beam"/>
  </choices-outline>
  <choice id="squirrel" visible="false" selected="need_squirrel()" title="鼠须管 {squirrel['version']}">
    <pkg-ref id="im.rime.inputmethod.Squirrel"/>
  </choice>
  <choice id="beam" visible="false" title="Beam">
    <pkg-ref id="{IDENTIFIER}"/>
  </choice>
  <pkg-ref id="im.rime.inputmethod.Squirrel" version="{squirrel['version']}" onConclusion="RequireLogout">Squirrel-{squirrel['version']}.pkg</pkg-ref>
  <pkg-ref id="{IDENTIFIER}" version="{escape(version)}">beam.pkg</pkg-ref>
</installer-gui-script>
''', note


def welcome(version, squirrel, note):
    return f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<style>body{{font-family:-apple-system,sans-serif;font-size:13px;line-height:1.5}}</style></head><body>
<h3>Beam {escape(version)}：鼠须管上的本地大模型输入法</h3>
<p>Beam 在本机运行 0.6B 模型，为鼠须管的「Beam 雾凇」方案提供整句候选和英文翻译。不联网推理，不上传输入内容。</p>
<ul>
<li>需要 Apple 芯片的 Mac 和 macOS 13 或更新版本。</li>
<li>未安装鼠须管或版本较旧时，会一并安装官方鼠须管 {squirrel['version']}，结束后需要注销并重新登录。</li>
<li>Beam 会把插件放进鼠须管并重新做本机签名；卸载时恢复原版鼠须管。</li>
<li>{note}</li>
</ul>
<p>安装完成后，在鼠须管的方案选单（默认 Ctrl+`）中选择「Beam 雾凇」。卸载：在终端运行
<code>sudo "{PREFIX}/bin/beamctl" uninstall</code>。</p>
</body></html>
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', type=Path, default=ROOT / 'build/macos/build')
    parser.add_argument('--squirrel-pkg', type=Path, required=True)
    parser.add_argument('--squirrel-executable', type=Path, required=True,
                        help='Squirrel built from the pinned source with packaging/macos/squirrel/back-image.patch')
    parser.add_argument('--model', type=Path, help='also make the offline package')
    parser.add_argument('--adapter', type=Path, help='translation adapter for the offline package')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if bool(args.model) != bool(args.adapter):
        parser.error('the offline package needs both --model and --adapter')
    version = (ROOT / 'VERSION').read_text().strip()
    lock = json.loads((ROOT / 'dependencies.lock.json').read_text())
    squirrel = lock['squirrel']
    if sha256(args.squirrel_pkg) != squirrel['sha256']:
        raise ValueError('Squirrel package checksum mismatch')
    manifest = json.loads((ROOT / 'models/default.json').read_text())
    temporary = tempfile.TemporaryDirectory(prefix='beam-macos-package-')
    work = Path(temporary.name)
    payload = work / 'root'
    subprocess.run(['cmake', '--install', str(args.build.resolve()), '--prefix', PREFIX],
                   env={**os.environ, 'DESTDIR': str(payload)}, check=True)
    base = payload / PREFIX.lstrip('/')
    share = base / 'share/beam-ime'
    if not (base / 'bin/beamd').exists() or not (base / 'lib/beam-ime/rime-plugins/librime-beam.dylib').exists():
        raise ValueError('build beamd and the Rime plugin (BEAM_RIME_LIBRARY) first')
    (base / 'lib/squirrel').mkdir()
    shutil.copy2(args.squirrel_executable, base / 'lib/squirrel/Squirrel')
    stage_rime(share / 'rime-data')
    schema = (ROOT / 'src/rime/beam_ice.schema.yaml').read_text(encoding='utf-8').splitlines(keepends=True)
    (share / 'rime/beam_ice.schema.yaml').write_text(
        ''.join(line for line in schema if not any(f'name: {s}' in line for s in LEARNING_SWITCHES)), encoding='utf-8')
    shutil.copy2(ROOT / 'dependencies.lock.json', share / 'dependencies.lock.json')
    (share / 'VERSION').write_text(version + '\n')
    for path in sorted(base.rglob('*')):
        if path.is_file() and not path.is_symlink() and macho(path):
            subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(path)], check=True)
    scripts = work / 'scripts'
    shutil.copytree(ROOT / 'packaging/macos/scripts', scripts)
    for script in scripts.iterdir():
        script.chmod(0o755)
    (base / 'bin/beamctl').chmod(0o755)
    packages = work / 'packages'
    packages.mkdir()
    shutil.copy2(args.squirrel_pkg, packages / f"Squirrel-{squirrel['version']}.pkg")
    resources = work / 'resources'
    resources.mkdir()
    dist = args.output_dir.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    built = []

    def build(offline):
        models = share / 'models'
        for name in (manifest['filename'], manifest['translateAdapter']['installAs']):
            (models / name).unlink(missing_ok=True)
        if offline:
            verify(args.model, manifest)
            verify(args.adapter, manifest['translateAdapter'])
            shutil.copy2(args.model, models / manifest['filename'])
            shutil.copy2(args.adapter, models / manifest['translateAdapter']['installAs'])
        subprocess.run(['pkgbuild', '--root', str(payload), '--scripts', str(scripts), '--identifier', IDENTIFIER,
                        '--version', version, '--install-location', '/', '--ownership', 'recommended',
                        str(packages / 'beam.pkg')], check=True)
        text, note = distribution(version, squirrel, offline)
        (work / 'distribution.xml').write_text(text, encoding='utf-8')
        (resources / 'welcome.html').write_text(welcome(version, squirrel, note), encoding='utf-8')
        artifact = dist / f"Beam-{version}-macos-arm64{'-offline' if offline else ''}.pkg"
        subprocess.run(['productbuild', '--distribution', str(work / 'distribution.xml'), '--package-path', str(packages),
                        '--resources', str(resources), str(artifact)], check=True)
        built.append(artifact)

    build(False)
    if args.model:
        build(True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    for artifact in built:
        record = {'version': version, 'platform': 'macos-arm64', 'squirrel': squirrel['version'],
                  'rimeAbi': (args.build / 'rime-abi.txt').read_text().strip(), 'artifact': artifact.name,
                  'sha256': sha256(artifact), 'sourceCommit': commit, 'sourceDirty': dirty,
                  'compiler': subprocess.check_output(['c++', '--version'], text=True).splitlines()[0],
                  'dependencies': lock}
        (dist / (artifact.name + '.build.json')).write_text(json.dumps(record, indent=2) + '\n')
        print(artifact)


if __name__ == '__main__':
    main()
