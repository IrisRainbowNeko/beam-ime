#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the pronunciation table shared by the native personal lexicon."""
import argparse
from pathlib import Path
from pypinyin import Style, pinyin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    lines = []
    for codepoint in range(0x4e00, 0xa000):
        ch = chr(codepoint)
        values = pinyin(ch, style=Style.NORMAL, heteronym=True, errors='ignore')
        readings = sorted({v.replace('ü', 'v') for group in values for v in group})
        if readings:
            lines.append(ch + '\t' + ' '.join(readings))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
