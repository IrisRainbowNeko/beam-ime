#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare one-stage translate adapters with the two-stage pipeline on the same keyed rows.

    python3 tools/models/onestage_compare.py --ime-cases ime.cases.jsonl --translator r4b/kd/epoch-3 \
        --onestage os-e3=os-e3.cases.jsonl --output compare/judge

The two-stage side translates the IME's top Chinese candidate (keys_llm_eval.py --mode ime cases) with a
zh_en_v1 checkpoint; the one-stage side is keys_llm_eval.py --mode translate_en cases. Writes one mt_judge.py
systems file per key spelling (<output>-<variant>.jsonl, gold Chinese as the fragment) and prints Chinese
accuracy per system. Rows are matched by id.
"""
import argparse
import collections
import json
from pathlib import Path

import torch

from mt_eval import generate


def read(path):
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ime-cases', required=True)
    parser.add_argument('--translator', type=Path, required=True)
    parser.add_argument('--onestage', action='append', default=[], help='name=cases.jsonl')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    ime = {r['id']: r for r in read(args.ime_cases)}
    systems = {name: {r['id']: r for r in read(path)} for name, _, path in (s.partition('=') for s in args.onestage)}
    ids = [i for i in ime if all(i in s for s in systems.values())]
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    todo = [{'chinese': ime[i]['displayCandidates'][0], 'context': ime[i]['context']} for i in ids if ime[i]['displayCandidates']]
    english = dict(zip([(r['chinese'], r['context']) for r in todo], generate(args.translator, todo, device, 64, 64)))

    stats = collections.defaultdict(lambda: collections.Counter())
    by_variant = collections.defaultdict(list)
    for i in ids:
        row = ime[i]
        out = {'chinese': row['source'] or row['target'], 'context': row['context']}
        gold = out['chinese']
        top = row['displayCandidates']
        out['two_stage'] = english.get((top[0], row['context']), '') if top else ''
        stats['two_stage']['zhP1'] += bool(top) and top[0] == gold
        stats['two_stage']['zhP5'] += gold in top[:5]
        stats['two_stage']['distinct'] += len(set(top[:5]))
        for name, cases in systems.items():
            case = cases[i]
            comments = [c for c in case['displayComments'] if c]
            out[name] = case['displayCandidates'][0] if case['displayCandidates'] else ''
            stats[name]['zhP1'] += bool(comments) and comments[0] == gold
            stats[name]['zhP5'] += gold in comments[:5]
            stats[name]['distinct'] += len(set(comments[:5]))
            stats[name]['empty'] += not case['displayCandidates']
        by_variant[row['variant']].append(out)
    for variant, rows in by_variant.items():
        with open(f'{args.output}-{variant}.jsonl', 'w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    n = len(ids)
    print(json.dumps({'rows': n, 'variants': {v: len(r) for v, r in by_variant.items()},
                      'systems': {s: {k: round(v / n, 3) for k, v in c.items()} for s, c in stats.items()}}, indent=2))


if __name__ == '__main__':
    main()
