#!/usr/bin/env python3
"""Build a distribution-specific package from an existing CMake build."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from fetch_rime_data import stage as stage_rime
from beamlib.model import read_manifest, verify, sha256

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("deb", "arch"), required=True)
    parser.add_argument("--build", type=Path, default=ROOT / "build/release")
    parser.add_argument("--model", type=Path, help="also make the offline bundle")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    stage = ROOT / "build/package" / args.format
    if stage.exists(): shutil.rmtree(stage)
    stage.mkdir(parents=True)
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
        subprocess.run(["tar", "--zstd", "--owner=0", "--group=0", "-cf", str(artifact), "-C", str(stage), ".PKGINFO", "usr"], check=True)
    if args.model:
        spec = read_manifest(ROOT / "models/default.json")
        verify(args.model, spec)
        bundle = dist / f"beam-ime-{version}-{args.format}-x86_64-offline.tar"
        with tarfile.open(bundle, "w") as archive:
            archive.add(artifact, arcname=artifact.name)
            archive.add(args.model, arcname=spec["filename"])
            archive.add(ROOT / "packaging/linux/install-offline.sh", arcname="install.sh")
            archive.add(ROOT / "docs/install-linux.md", arcname="README.md")
    manifest = {"version": version, "platform": args.format, "rimeAbi": (args.build / "rime-abi.txt").read_text().strip(),
                "artifact": artifact.name, "sha256": sha256(artifact)}
    (dist / (artifact.name + ".build.json")).write_text(json.dumps(manifest, indent=2)+"\n")
    print(artifact)


if __name__ == "__main__": main()
