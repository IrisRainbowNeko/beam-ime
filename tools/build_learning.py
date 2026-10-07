#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the separate native Vulkan trainer; no Python training dependencies."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/learning')
    parser.add_argument('--prefix', type=Path, default=ROOT / 'build/learning-runtime')
    parser.add_argument('--qvac-source', type=Path, help='already patched QVAC checkout')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--cross', action='store_true', help='skip running target tests when cross compiling')
    parser.add_argument('cmake_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    extra = args.cmake_args
    if extra[:1] == ['--']:
        extra = extra[1:]
    if args.qvac_source:
        extra.append(f'-DBEAM_QVAC_SOURCE={args.qvac_source.resolve()}')
    build = str(args.build_dir.resolve())
    subprocess.run(['cmake', '-S', str(ROOT / 'src/trainer'), '-B', build, '-G', 'Ninja',
                    '-DCMAKE_BUILD_TYPE=Release', f'-DCMAKE_INSTALL_PREFIX={args.prefix.resolve()}', *extra], check=True)
    subprocess.run(['cmake', '--build', build, '--parallel', str(args.jobs)], check=True)
    if not args.cross:
        subprocess.run(['ctest', '--test-dir', build, '--output-on-failure'], check=True)
    subprocess.run(['cmake', '--install', build, '--strip'], check=True)


if __name__ == '__main__':
    main()
