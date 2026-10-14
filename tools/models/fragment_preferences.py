#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build reproducible English-only preference pairs from public training rows."""
import argparse
import json
from pathlib import Path
import random
import re

from translation_corpus import json_rows

ENTITY_PAIRS = (('Malaysia', 'Singapore'), ('China', 'Japan'), ('Linux', 'Windows'),
                ('CPU', 'GPU'), ('training', 'test'), ('parameters', 'files'),
                ('inference', 'training'), ('pretraining', 'training set'))


def negatives(text):
    result = []
    for left, right in ENTITY_PAIRS:
        for before, after in ((left, right), (right, left)):
            if re.search(r'\b' + re.escape(before) + r'\b', text):
                result.append(('entity', re.sub(r'\b' + re.escape(before) + r'\b', after, text)))
    if re.search(r'\d', text):
        result.append(('number', re.sub(r'\d', lambda m: str((int(m[0]) + 1) % 10), text, count=1)))
    if re.search(r"\b(?:not|never)\b|n't", text, re.I):
        result.append(('negation', re.sub(r"\b(?:not|never)\s*|n't", '', text, count=1, flags=re.I)))
    if len(text.split()) >= 4:
        result.append(('omission', ' '.join(text.split()[:-2])))
    result.append(('addition', text.rstrip('.!?') + ', because the train is late.'))
    result.append(('repetition', text + ' ' + text + ' ' + text))
    return [(kind, value) for kind, value in result if value and value != text]


def build(rows, count=8000, seed=20261014):
    rng = random.Random(seed)
    rows = [r for r in rows if r.get('mode') == 'translate_en' and r.get('translation_status', 'ready') == 'ready']
    rng.shuffle(rows)
    groups = {kind: [] for kind in ('entity', 'number', 'negation', 'omission', 'addition', 'repetition')}
    seen = set()
    for row in rows:
        key = (row['target'], row.get('context', ''), row['translation'])
        if key in seen:
            continue
        seen.add(key)
        for kind, rejected in negatives(row['translation']):
            groups[kind].append({**row, 'chosen': row['translation'], 'rejected': rejected, 'preference_type': kind})
    selected, used = [], set()
    while len(selected) < count:
        before = len(selected)
        for group in groups.values():
            while group:
                row = group.pop()
                key = (row['target'], row.get('context', ''), row['chosen'])
                if key not in used:
                    selected.append(row); used.add(key)
                    break
            if len(selected) == count:
                break
        if len(selected) == before:
            raise ValueError(f'not enough unique preference sources: {len(selected)} < {count}')
    rng.shuffle(selected)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = build(list(json_rows(args.train)))
    with args.output.open('x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(json.dumps({'pairs': len(rows), 'shorter_negative': sum(len(r['rejected']) < len(r['chosen']) for r in rows)}))


if __name__ == '__main__':
    main()
