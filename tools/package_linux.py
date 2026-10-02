#!/usr/bin/env python3
"""Build a distribution-specific package from an existing CMake build."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from fetch_rime_data import stage as stage_rime
from beamlib.model import read_manifest, verify, sha256

ROOT = Path(__file__).resolve().parents[1]


def normalize_permissions(stage):
    for path in stage.rglob('*'):
        if path.is_symlink():
            continue
        executable = path.is_dir() or path.parent == stage / 'usr/bin'
        path.chmod(0o755 if executable else 0o644)


def bundle_metadata(info):
    info.uid = info.gid = 0
    info.uname = info.gname = 'root'
    info.mode = 0o755 if info.isdir() or info.name == 'install.sh' else 0o644
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("deb", "arch"), required=True)
    parser.add_argument("--build", type=Path, default=ROOT / "build/release")
    parser.add_argument("--model", type=Path, help="also make the offline bundle")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    # Stage on the native temporary filesystem, including when the checkout is on NTFS.
    temporary = tempfile.TemporaryDirectory(prefix='beam-package-')
    stage = Path(temporary.name)
    subprocess.run(["cmake", "--install", str(args.build.resolve()), "--prefix", "/usr"],
                   env={**os.environ, "DESTDIR": str(stage)}, check=True)
    stage_rime(stage / "usr/share/beam-ime/rime-data")
    shutil.copy2(ROOT / "dependencies.lock.json", stage / "usr/share/beam-ime/dependencies.lock.json")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    if args.format == "deb":
        abi_package = subprocess.check_output(["dpkg-query", "-W", "-f=${Package}", "librime1"], text=True).strip()
        abi_version = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", "librime1"], text=True).strip()
        metadata = stage / "DEBIAN"
        metadata.mkdir()
        (metadata / "control").write_text(
            f"Package: beam-ime\nVersion: {version.replace('-beta.', '~beta.')}\nArchitecture: amd64\n"
            "Maintainer: Beam contributors\nSection: utils\nPriority: optional\n"
            f"Depends: fcitx5-rime, {abi_package} (= {abi_version}), python3, python3-yaml, libstdc++6, libc6, libgomp1\n"
            "Recommends: mesa-vulkan-drivers\nDescription: Local keys-conditioned language model input method\n")
        artifact = dist / f"beam-ime-{version}-ubuntu24.04-amd64.deb"
        normalize_permissions(stage)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(artifact)], check=True)
    else:
        abi = subprocess.check_output(["pacman", "-Q", "librime"], text=True).split()[1]
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
        (stage / ".PKGINFO").write_text(
            f"pkgname = beam-ime\npkgbase = beam-ime\npkgver = {version.replace('-beta.', 'beta')}-1\n"
            "pkgdesc = Local keys-conditioned language model input method\n"
            "url = https://github.com/IrisRainbowNeko/beam-ime\narch = x86_64\nlicense = Apache-2.0\n"
            f"size = {size}\ndepend = librime={abi}\ndepend = fcitx5-rime\ndepend = python\ndepend = python-yaml\n"
            "depend = gcc-libs\ndepend = glibc\noptdepend = vulkan-icd-loader: GPU inference\n")
        artifact = dist / f"beam-ime-{version}-arch-x86_64.pkg.tar.zst"
        normalize_permissions(stage)
        subprocess.run(["tar", "--zstd", "--owner=0", "--group=0", "-cf", str(artifact), "-C", str(stage), ".PKGINFO", "usr"], check=True)
    if args.model:
        spec = read_manifest(ROOT / "models/default.json")
        verify(args.model, spec)
        bundle = dist / f"beam-ime-{version}-{args.format}-x86_64-offline.tar"
        with tarfile.open(bundle, "w") as archive:
            archive.add(artifact, arcname=artifact.name, filter=bundle_metadata)
            archive.add(args.model, arcname=spec["filename"], filter=bundle_metadata)
            archive.add(ROOT / "packaging/linux/install-offline.sh", arcname="install.sh", filter=bundle_metadata)
            archive.add(ROOT / "docs/install-linux.md", arcname="README.md", filter=bundle_metadata)
    manifest = {"version": version, "platform": args.format, "rimeAbi": (args.build / "rime-abi.txt").read_text().strip(),
                "artifact": artifact.name, "sha256": sha256(artifact),
                "sourceCommit": subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                "compiler": subprocess.check_output(['c++', '--version'], text=True).splitlines()[0],
                "dependencies": json.loads((ROOT / 'dependencies.lock.json').read_text())}
    (dist / (artifact.name + ".build.json")).write_text(json.dumps(manifest, indent=2)+"\n")
    print(artifact)


if __name__ == "__main__": main()
