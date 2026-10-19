#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Give mt_corpus.py / mt_merge.py rows simulated keys for the one-stage translate adapter (keys_llm_v4).

    python3 tools/models/onestage_data.py --corpus corpus-4b --output onestage-4b
    python3 tools/models/onestage_data.py --rows judge-rows.jsonl --all-spellings --output onestage-eval/judge.jsonl

Keys come from prepare_dual_task.expand (pypinyin full spelling, initials, a random mix). Training rows get one
spelling each (full .5, mixed .3, initials .2); --all-spellings keeps every spelling, for evaluation. Rows whose
Chinese is not Hanzi and English words only (punctuation, digits) cannot be typed as keys and are dropped.

--geneinput adds the 'real' spelling of key_noise.py (abbreviation habits and typos as GeneInput rows show them):
a training row takes it with probability --real-share instead of one of the above; --all-spellings adds it.
"""
import argparse
import collections
import json
import random
from pathlib import Path

from key_noise import Speller
from prepare_dual_task import expand

WEIGHTS = {'full': .5, 'mixed': .3, 'initials': .2}


def keyed(row, rng, every, speller=None, real_share=0.):
    source = {'target': row['chinese'], 'translation': row['english'], 'context': row['context'],
              'source': row.get('source', '')}
    try:
        options = [r for r in expand(source) if r['mode'] == 'translate_en']
    except ValueError:
        return []
    if speller and options and (every or rng.random() < real_share):
        keys = speller.spell(row['chinese'], rng)
        real = {**options[0], 'id': options[0]['id'].rsplit(':', 1)[0] + ':real', 'variant': 'real', 'keys': keys}
        if keys and not every:
            return [real]
        if keys and all(r['keys'] != keys for r in options):
            options.append(real)
    if every or not options:
        return options
    return [rng.choices(options, [WEIGHTS[r['variant']] for r in options])[0]]


def convert(rows, rng, every, speller=None, real_share=0.):
    out, counts = [], collections.Counter()
    for row in rows:
        items = keyed(row, rng, every, speller, real_share)
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
    parser.add_argument('--geneinput', help='GeneInput FK2C 26key.json for the realistic spelling')
    parser.add_argument('--real-share', type=float, default=.75)
    parser.add_argument('--seed', type=int, default=20261019)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    speller = Speller(args.geneinput) if args.geneinput else None
    read = lambda path: [json.loads(line) for line in path.open(encoding='utf-8') if line.strip()]
    if args.rows:
        rows, counts = convert(read(args.rows), rng, args.all_spellings, speller, args.real_share)
        write(args.output, rows)
        print(json.dumps({'rows': len(rows), **counts}), flush=True)
        return
    for name in ('train', 'dev'):
        rows, counts = convert(read(args.corpus / f'{name}.jsonl'), rng, args.all_spellings, speller, args.real_share)
        write(args.output / f'{name}.jsonl', rows)
        print(json.dumps({'split': name, 'rows': len(rows), **counts}), flush=True)


if __name__ == '__main__':
    main()
