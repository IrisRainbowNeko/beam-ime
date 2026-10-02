#!/usr/bin/env python3
"""Create Windows light/offline NSIS installers from the cross-build outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from fetch_rime_data import stage as stage_rime
from beamlib.model import read_manifest, verify, sha256

ROOT = Path(__file__).resolve().parents[1]


def fetch(url, path, digest):
    if not path.exists():
        with urllib.request.urlopen(url, timeout=60) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"checksum mismatch: {path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT/"models/beam-0.6b-q8_0.gguf")
    parser.add_argument("--light-only", action="store_true")
    args = parser.parse_args()
    version = (ROOT/"VERSION").read_text().strip()
    lock = json.loads((ROOT/"dependencies.lock.json").read_text())
    stage = ROOT/"build/windows-package"
    if stage.exists(): shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage/"payload").mkdir()
    (stage/"models").mkdir()
    output = ROOT/"build/win/out"
    for file in output.glob("*"):
        if file.suffix.lower() in (".dll", ".exe") and not file.name.endswith('-test.exe'):
            shutil.copy2(file, stage/("payload/rime.dll" if file.name == "rime.dll" else file.name))
    if not (stage/"beamd.exe").exists() or not (stage/"payload/rime.dll").exists():
        raise ValueError("build the rime and beamd targets first")
    for name in ("beam-setup.ps1", "beam-stop.ps1", "Beam.Files.psm1"):
        shutil.copy2(ROOT/"packaging/windows"/name, stage/name)
    shutil.copy2(ROOT/"models/default.json", stage/"models/default.json")
    shutil.copy2(ROOT/"dependencies.lock.json", stage/"dependencies.lock.json")
    shutil.copytree(ROOT/"licenses", stage/"licenses")
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT/name, stage/"licenses"/name)
    stage_rime(stage/"payload/rime-data")
    shutil.copy2(ROOT/"src/rime/beam_ice.schema.yaml", stage/"payload/rime-data/beam_ice.schema.yaml")
    downloads = ROOT/"build/downloads"
    downloads.mkdir(exist_ok=True)
    maker = shutil.which("makensis")
    wine = not bool(maker)
    if wine:
        nsis = lock["nsis"]
        archive = downloads/f"nsis-{nsis['version']}.zip"
        fetch(f"https://downloads.sourceforge.net/project/nsis/NSIS%203/{nsis['version']}/{archive.name}", archive, nsis["sha256"])
        with zipfile.ZipFile(archive) as package: package.extractall(downloads)
        maker = str(downloads/f"nsis-{nsis['version']}/makensis.exe")
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    wine_prefix = tempfile.TemporaryDirectory(prefix='beam-nsis-') if wine else None
    environment = {**os.environ, 'WINEDEBUG': '-all'}
    if wine_prefix:
        environment['WINEPREFIX'] = wine_prefix.name
        environment['HOME'] = wine_prefix.name
        environment['WINEDLLOVERRIDES'] = 'winemenubuilder.exe=d;mscoree,mshtml=d'
    def build(offline):
        artifact = dist/f"Beam-{version}-windows-x64-{'offline' if offline else 'setup'}.exe"
        def path(p): return "Z:" + str(p.resolve()).replace("/", "\\") if wine else str(p.resolve())
        prefix = "/" if wine or os.name == "nt" else "-"
        command = (["wine", maker] if wine else [maker]) + [prefix+"V2", prefix+"INPUTCHARSET", "UTF8",
            prefix+f"DVERSION={version}", prefix+f"DSTAGE={path(stage)}", prefix+f"DOUTFILE={path(artifact)}",
            prefix+f"DSETUP_FLAGS={' ' if offline else '-Download'}"]
        if offline: command.append(prefix+"DOFFLINE")
        command.append(path(ROOT/"packaging/windows/installer.nsi"))
        subprocess.run(command, check=True, env=environment)
        info = {"version": version, "platform": "windows-x64", "artifact": artifact.name,
                "sha256": sha256(artifact), "dependencies": lock,
                "sourceCommit": subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                "compiler": subprocess.check_output(['x86_64-w64-mingw32-g++', '--version'], text=True).splitlines()[0]}
        (dist / (artifact.name + '.build.json')).write_text(json.dumps(info, indent=2) + '\n')
        print(artifact)
    try:
        if wine_prefix:
            subprocess.run(['wineboot', '-u'], env=environment, check=True, timeout=120)
        build(False)
        if not args.light_only:
            spec = read_manifest(ROOT/"models/default.json")
            verify(args.model, spec)
            shutil.copy2(args.model, stage/"models"/spec["filename"])
            weasel = downloads/"weasel-0.17.4.0-installer.exe"
            fetch(lock["weasel"]["url"], weasel, lock["weasel"]["sha256"])
            shutil.copy2(weasel, stage/"payload/weasel-installer.exe")
            build(True)
        symbols = ROOT / 'build/win/symbols'
        if symbols.exists():
            with zipfile.ZipFile(dist/f'Beam-{version}-windows-x64-symbols.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(symbols.glob('*.debug')):
                    archive.write(file, file.name)
    finally:
        if wine_prefix:
            subprocess.run(['wineserver', '-k'], env=environment, check=False)
            subprocess.run(['wineserver', '-w'], env=environment, check=False, timeout=30)
            wine_prefix.cleanup()


if __name__ == "__main__": main()
