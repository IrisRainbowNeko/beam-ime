#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Simulate how people really type pinyin, from GeneInput FK2C rows (input, pyseg, target).

    python3 tools/models/key_noise.py --geneinput FK2C/train/26key.json --demo 你请假了 好像都拿了

Each FK2C row gives, per character, what was typed for it: the whole syllable, a prefix (initials and the like)
or a slip of the finger (one substituted, inserted, deleted or swapped letter, measured against the nearest
prefix of the character's reading). A row's sequence of such edits is a template; spell() takes a template of
the same length (or a neighbouring one) and replays it on another text's syllables, so row-level habits
(all initials, full first word then initials, a typo in a long sentence) carry over.
"""
import argparse
import bisect
import collections
import json
import random
import re
from functools import lru_cache

from keys_llm_format import ENGLISH_PATTERN, units

KEYBOARD = ('qwertyuiop', 'asdfghjkl', 'zxcvbnm')
NEIGHBOURS = {}
for r, line in enumerate(KEYBOARD):
    for c, key in enumerate(line):
        near = set()
        for dr in (-1, 0, 1):
            if 0 <= r + dr < 3:
                row = KEYBOARD[r + dr]
                for dc in (-1, 0, 1):
                    if (dr or dc) and 0 <= c + dc + (0 if dr == 0 else 0) < len(row):
                        near.add(row[c + dc])
        NEIGHBOURS[key] = ''.join(sorted(near - {key}))


@lru_cache(maxsize=65536)
def readings(character):
    from pypinyin import Style, pinyin
    values = pinyin(character, style=Style.NORMAL, heteronym=True, errors='ignore')
    return tuple(dict.fromkeys(v.replace('ü', 'v') for group in values for v in group))


def default_reading(text):
    from pypinyin import Style, pinyin
    return [v[0].replace('ü', 'v') for v in pinyin(text, style=Style.NORMAL, errors='ignore')]


def edit(a, b):
    """The single edit turning a into b ('sub', 'ins', 'del', 'swap'), or None when more than one is needed."""
    if a == b:
        return 'same'
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return 'sub'
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return 'swap'
        return None
    if len(b) == len(a) + 1:
        return 'ins' if any(b[:i] + b[i + 1:] == a for i in range(len(b))) else None
    if len(a) == len(b) + 1:
        return 'del' if any(a[:i] + a[i + 1:] == b for i in range(len(a))) else None
    return None


def classify(segment, character):
    """('full'|'prefix'|typo kind, prefix length or None) for what was typed for one character."""
    options = readings(character)
    if not options:
        return None
    if segment in options:
        return ('full', None)
    for reading in options:
        if reading.startswith(segment):
            return ('prefix', len(segment))
    for reading in options:
        for base, length in ((reading, None), *((reading[:k], k) for k in range(1, len(reading)))):
            kind = edit(base, segment)
            if kind and kind != 'same':
                return (kind, length)
    return None


class Speller:
    def __init__(self, path, limit=None):
        self.templates = collections.defaultdict(list)
        with open(path, encoding='utf-8') as handle:
            for n, line in enumerate(handle):
                if limit and n >= limit:
                    break
                row = json.loads(line)
                segments = [s.lower() for s in re.findall(r'[A-Z][a-z]*', row.get('pyseg', ''))]
                target = row['target']
                if len(segments) != len(target) or not re.fullmatch('[一-鿿]+', target):
                    continue
                ops = [classify(s, c) for s, c in zip(segments, target)]
                if all(ops):
                    self.templates[len(ops)].append(tuple(ops))
        self.lengths = sorted(self.templates)

    def stats(self):
        kinds = collections.Counter(op[0] for group in self.templates.values() for t in group for op in t)
        total = sum(kinds.values())
        return {'templates': sum(map(len, self.templates.values())), 'perCharacter': {k: round(v / total, 4) for k, v in kinds.items()}}

    def template(self, n, rng):
        if n in self.templates:
            return rng.choice(self.templates[n])
        # Longer than any template: chain shorter ones.
        index = min(bisect.bisect_left(self.lengths, n), len(self.lengths) - 1)
        out = list(rng.choice(self.templates[self.lengths[index]]))
        while len(out) < n:
            out += rng.choice(self.templates[rng.choice(self.lengths[:index + 1])])
        return tuple(out[:n])

    def spell(self, target, rng):
        """Keys for target (Hanzi and English words), or None when it cannot be typed."""
        pieces = units(target)
        if not pieces:
            return None
        syllables = iter(default_reading(''.join(p for p in pieces if not ENGLISH_PATTERN.fullmatch(p))))
        hanzi = [p for p in pieces if not ENGLISH_PATTERN.fullmatch(p)]
        ops = iter(self.template(len(hanzi), rng)) if hanzi else iter(())
        keys = []
        for piece in pieces:
            if ENGLISH_PATTERN.fullmatch(piece):
                keys.append(piece.lower())
                continue
            reading, (kind, length) = next(syllables), next(ops)
            if kind == 'full':
                keys.append(reading)
                continue
            base = reading if length is None else reading[:max(1, min(length, len(reading) - 1 if len(reading) > 1 else 1))]
            keys.append(base if kind == 'prefix' else typo(base, kind, rng))
        value = ''.join(keys)
        return value if 0 < len(value) <= 40 and re.fullmatch('[a-z]+', value) else None


def typo(base, kind, rng):
    i = rng.randrange(len(base))
    if kind == 'sub':
        return base[:i] + rng.choice(NEIGHBOURS.get(base[i], base[i])) + base[i + 1:]
    if kind == 'ins':
        extra = rng.choice((base[i], *NEIGHBOURS.get(base[i], '')))
        return base[:i + 1] + extra + base[i + 1:]
    if kind == 'del':
        return base if len(base) == 1 else base[:i] + base[i + 1:]
    if kind == 'swap' and len(base) > 1:
        i = min(i, len(base) - 2)
        return base[:i] + base[i + 1] + base[i] + base[i + 2:]
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--geneinput', required=True)
    parser.add_argument('--demo', nargs='*', default=[])
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()
    speller = Speller(args.geneinput)
    print(json.dumps(speller.stats(), ensure_ascii=False))
    rng = random.Random(args.seed)
    for text in args.demo:
        print(text, [speller.spell(text, rng) for _ in range(6)])


if __name__ == '__main__':
    main()
