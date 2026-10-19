#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Give mt_corpus.py / mt_merge.py rows simulated keys for the one-stage translate adapter (keys_llm_v4).

    python3 tools/models/onestage_data.py --corpus corpus-4b --output onestage-4b
    python3 tools/models/onestage_data.py --rows judge-rows.jsonl --all-spellings --output onestage-eval/judge.jsonl

Keys come from prepare_dual_task.expand (pypinyin full spelling, initials, a random mix). Training rows get one
spelling each (full .5, mixed .3, initials .2); --all-spellings keeps every spelling, for evaluation. Rows whose
Chinese is not Hanzi and English words only (punctuation, digits) cannot be typed as keys and are dropped.
"""
import argparse
import collections
import json
import random
from pathlib import Path

from prepare_dual_task import expand

WEIGHTS = {'full': .5, 'mixed': .3, 'initials': .2}


def keyed(row, rng, every):
    source = {'target': row['chinese'], 'translation': row['english'], 'context': row['context'],
              'source': row.get('source', '')}
    try:
        options = [r for r in expand(source) if r['mode'] == 'translate_en']
    except ValueError:
        return []
    if every or not options:
        return options
    return [rng.choices(options, [WEIGHTS[r['variant']] for r in options])[0]]


def convert(rows, rng, every):
    out, counts = [], collections.Counter()
    for row in rows:
        items = keyed(row, rng, every)
        counts['dropped' if not items else 'kept'] += 1
        for item in items:
            counts[item['variant']] += 1
            out.append({**item, 'source': row.get('source', '')})
    return out, counts


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--corpus', type=Path, help='directory with train.jsonl and dev.jsonl')
    parser.add_argument('--rows', type=Path, help='a single rows file (chinese, context, english)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--all-spellings', action='store_true')
    parser.add_argument('--seed', type=int, default=20261019)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    read = lambda path: [json.loads(line) for line in path.open(encoding='utf-8') if line.strip()]
    if args.rows:
        rows, counts = convert(read(args.rows), rng, args.all_spellings)
        write(args.output, rows)
        print(json.dumps({'rows': len(rows), **counts}), flush=True)
        return
    for name in ('train', 'dev'):
        rows, counts = convert(read(args.corpus / f'{name}.jsonl'), rng, args.all_spellings)
        write(args.output / f'{name}.jsonl', rows)
        print(json.dumps({'split': name, 'rows': len(rows), **counts}), flush=True)


if __name__ == '__main__':
    main()
