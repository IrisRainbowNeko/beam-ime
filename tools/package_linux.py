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


def offline_bundle(artifact, model, platform, output_dir=None):
    spec = read_manifest(ROOT / 'models/default.json')
    verify(model, spec)
    version = (ROOT / 'VERSION').read_text().strip()
    bundle = (output_dir or ROOT / 'dist') / f'beam-ime-{version}-{platform}-x86_64-offline.tar'
    bundle.parent.mkdir(exist_ok=True)
    with tarfile.open(bundle, 'w') as archive:
        for source, name in ((artifact, artifact.name), (model, spec['filename']),
                             (ROOT / 'packaging/linux/install-offline.sh', 'install.sh'),
                             (ROOT / 'docs/install-linux.md', 'README.md')):
            archive.add(source, arcname=name, filter=bundle_metadata)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("deb", "arch", "rpm"), required=True)
    parser.add_argument("--build", type=Path, default=ROOT / "build/release")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--model", type=Path, help="also make the offline bundle")
    parser.add_argument('--existing-package', type=Path, help='bundle an already built distribution package')
    args = parser.parse_args()
    platform = {"deb": "ubuntu24.04", "arch": "arch", "rpm": "fedora"}[args.format]
    if args.format == "rpm":
        platform += subprocess.check_output(["rpm", "--eval", "%{fedora}"], text=True).strip()
    if args.existing_package:
        if not args.model:
            parser.error('--existing-package requires --model')
        print(offline_bundle(args.existing_package, args.model, platform, args.output_dir))
        return
    version = (ROOT / "VERSION").read_text().strip()
    # Stage on the native temporary filesystem, including when the checkout is on NTFS.
    temporary = tempfile.TemporaryDirectory(prefix='beam-package-')
    stage = Path(temporary.name)
    subprocess.run(["cmake", "--install", str(args.build.resolve()), "--prefix", "/usr"],
                   env={**os.environ, "DESTDIR": str(stage)}, check=True)
    stage_rime(stage / "usr/share/beam-ime/rime-data")
    shutil.copy2(ROOT / "dependencies.lock.json", stage / "usr/share/beam-ime/dependencies.lock.json")
    dist = args.output_dir.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    if args.format == "deb":
        installed = subprocess.check_output(['dpkg-query', '-W',
            '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n', 'librime1*'], text=True)
        abi_package, abi_version = next((row[0].split(':')[0], row[1])
            for line in installed.splitlines() if len(row := line.split('\t')) == 3 and row[2] == 'installed')
        metadata = stage / "DEBIAN"
        metadata.mkdir()
        (metadata / "control").write_text(
            f"Package: beam-ime\nVersion: {version.replace('-beta.', '~beta.')}\nArchitecture: amd64\n"
            "Maintainer: Beam contributors\nSection: utils\nPriority: optional\n"
            f"Depends: fcitx5-rime, librime-plugin-lua, {abi_package} (= {abi_version}), python3, python3-yaml, libstdc++6, libc6, libsqlite3-0, libssl3t64\n"
            "Recommends: mesa-vulkan-drivers\nDescription: Local keys-conditioned language model input method\n")
        artifact = dist / f"beam-ime-{version}-ubuntu24.04-amd64.deb"
        normalize_permissions(stage)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(artifact)], check=True)
    elif args.format == "rpm":
        abi = subprocess.check_output(["rpm", "-q", "--qf", "%{EPOCHNUM}:%{VERSION}-%{RELEASE}",
                                       "librime"], text=True).strip()
        private_libs = next((stage / "usr").glob("lib*/beam-ime")).relative_to(stage)
        plugin = next((stage / "usr").glob("lib*/rime-plugins/librime-beam.so")).relative_to(stage)
        artifact = dist / f"beam-ime-{version}-{platform}-x86_64.rpm"
        normalize_permissions(stage)
        with tempfile.TemporaryDirectory(prefix="beam-rpm-") as rpm_dir:
            spec = Path(rpm_dir) / "beam-ime.spec"
            spec.write_text(
                "%global debug_package %{nil}\n"
                "%global _build_id_links none\n"
                "%global __provides_exclude_from ^/usr/lib(64)?/(beam-ime|rime-plugins)/.*$\n"
                "%global __requires_exclude ^lib(ggml|llama).*\\.so.*$\n"
                f"Name: beam-ime\nVersion: {version.replace('-beta.', '~beta.')}\nRelease: 1%{{?dist}}\n"
                "Summary: Local keys-conditioned language model input method\n"
                "License: Apache-2.0 AND MIT AND BSD-3-Clause AND BSL-1.0 AND GPL-3.0-only\n"
                "URL: https://github.com/IrisRainbowNeko/beam-ime\nBuildArch: x86_64\n"
                f"Requires: librime%{{?_isa}} = {abi}\n"
                "Requires: fcitx5-rime, librime-lua, python3, python3-pyyaml\n"
                "Recommends: mesa-vulkan-drivers\n\n"
                "%description\nBeam runs a local language model and supplies candidates to Fcitx5-Rime.\n\n"
                "%install\nmkdir -p \"%{buildroot}\"\n"
                f'cp -a "{stage}/usr" "%{{buildroot}}/"\n\n'
                "%files\n%defattr(-,root,root,-)\n/usr/bin/beamd\n/usr/bin/beamctl\n"
                f"/{private_libs}\n/{plugin}\n/usr/share/beam-ime\n"
                "%license /usr/share/licenses/beam-ime\n")
            subprocess.run(["rpmbuild", "-bb", "--define", f"_topdir {rpm_dir}",
                            "--define", f"_rpmdir {dist}", "--define", f"_rpmfilename {artifact.name}",
                            str(spec)], check=True)
    else:
        abi = subprocess.check_output(["pacman", "-Q", "librime"], text=True).split()[1]
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
        (stage / ".PKGINFO").write_text(
            f"pkgname = beam-ime\npkgbase = beam-ime\npkgver = {version.replace('-beta.', 'beta')}-1\n"
            "pkgdesc = Local keys-conditioned language model input method\n"
            "url = https://github.com/IrisRainbowNeko/beam-ime\narch = x86_64\nlicense = Apache-2.0\n"
            f"size = {size}\ndepend = librime={abi}\ndepend = fcitx5-rime\ndepend = python\ndepend = python-yaml\n"
            "depend = gcc-libs\ndepend = glibc\ndepend = sqlite\ndepend = openssl\noptdepend = vulkan-icd-loader: GPU inference\n")
        artifact = dist / f"beam-ime-{version}-arch-x86_64.pkg.tar.zst"
        normalize_permissions(stage)
        subprocess.run(["tar", "--zstd", "--owner=0", "--group=0", "-cf", str(artifact), "-C", str(stage), ".PKGINFO", "usr"], check=True)
    if args.model:
        offline_bundle(artifact, args.model, platform, dist)
    manifest = {"version": version, "platform": args.format, "rimeAbi": (args.build / "rime-abi.txt").read_text().strip(),
                "artifact": artifact.name, "sha256": sha256(artifact),
                "sourceCommit": subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                "sourceDirty": bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
                "compiler": subprocess.check_output(['c++', '--version'], text=True).splitlines()[0],
                "dependencies": json.loads((ROOT / 'dependencies.lock.json').read_text())}
    (dist / (artifact.name + ".build.json")).write_text(json.dumps(manifest, indent=2)+"\n")
    print(artifact)


if __name__ == "__main__": main()
