#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Split sentence/session groups, then expand paired IME/English examples.

Input JSONL: target (Chinese/mixed source), translation (teacher English),
optional keys (actual user spelling), context, session_id and source.
Rows without a translation contribute only ordinary IME examples.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import unicodedata

from keys_llm_format import FRAGMENT_VERSION, build_target, normalize_keys, units
from personal_trainer import phonetic_segments


def sentence(text):
    return unicodedata.normalize('NFKC', text).strip().casefold()


def split_rows(rows, seed=20261012, dev_fraction=.05, test_fraction=.05):
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    # Repeated sentences connect sessions; neither may straddle a split.
    for row in rows:
        text = 'text:' + sentence(row.get('parent_text', row['target']))
        find(text)
        if row.get('session_id'):
            session = 'session:' + str(row.get('source', '')) + ':' + str(row['session_id'])
            a, b = sorted((find(text), find(session)))
            parents[b] = a
    result = {'train': [], 'dev': [], 'test': []}
    for row in rows:
        group = find('text:' + sentence(row.get('parent_text', row['target'])))
        score = int(hashlib.sha256(f'{seed}:{group}'.encode()).hexdigest()[:16], 16) / 2**64
        name = row.get('split') or ('test' if score < test_fraction else 'dev' if score < test_fraction + dev_fraction else 'train')
        if name not in result:
            raise ValueError('invalid parent split')
        result[name].append(row)
    return result


def expand(row, seed=20261012):
    from pypinyin import Style, pinyin

    pieces = units(row['target'])
    if not pieces:
        raise ValueError('target must contain Hanzi and English words in the IME output alphabet')
    original = normalize_keys(row['keys']) if row.get('keys') else None
    aligned = phonetic_segments(row['target'], original) if original else None
    if aligned is None:
        readings = iter(value[0].replace('ü', 'v') for value in pinyin(row['target'], style=Style.NORMAL, errors='ignore'))
        aligned = [(piece.lower(), True) if piece.isascii() else (next(readings), False) for piece in pieces]
    rng = random.Random(str(seed) + ':' + row['target'])
    spellings = [
        ('full', ''.join(text for text, _ in aligned)),
        ('initials', ''.join(text if english else text[0] for text, english in aligned)),
        ('mixed', ''.join(text if english else text[:rng.randint(1, len(text))] for text, english in aligned)),
    ]
    if original:
        spellings.insert(0, ('original', original))
    seen = set()
    identifier = hashlib.sha256((str(row.get('source', '')) + ':' + row.get('parent_id', '') + ':' +
                                 row.get('context', '') + ':' + row['target']).encode()).hexdigest()[:20]
    for variant, keys in spellings:
        if keys in seen or len(keys.replace("'", '')) > 40:
            continue
        seen.add(keys)
        shared = {'id': identifier + ':' + variant, 'variant': variant, 'keys': keys,
                  'target': row['target'], 'context': row.get('context', '')[-64:]}
        for field in ('parent_id', 'context_kind', 'focused'):
            if field in row:
                shared[field] = row[field]
        yield {**shared, 'mode': 'ime'}
        if row.get('translation') or row.get('translation_status') == 'need_more_input':
            status = row.get('translation_status', 'ready')
            build_target(row['target'], mode='translate_en', translation=row.get('translation', ''),
                         version=FRAGMENT_VERSION, translation_status=status)
            yield {**shared, 'mode': 'translate_en', 'translation': row.get('translation', ''), 'translation_status': status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261012)
    parser.add_argument('--dev-fraction', type=float, default=.05)
    parser.add_argument('--test-fraction', type=float, default=.05)
    args = parser.parse_args()
    if min(args.dev_fraction, args.test_fraction) < 0 or args.dev_fraction + args.test_fraction >= 1:
        parser.error('split fractions must be nonnegative and sum to less than one')
    rows = []
    rejected = 0
    for path in args.input:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    if row.get('translation_error'):
                        rejected += 1
                    else:
                        rows.append(row)
    args.output.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, members in split_rows(rows, args.seed, args.dev_fraction, args.test_fraction).items():
        counts[name] = {'sentences': len(members), 'ime': 0, 'translate_en': 0}
        with (args.output / (name + '.jsonl')).open('w', encoding='utf-8') as output:
            seen = set()
            for row in members:
                for item in expand(row, args.seed):
                    key = (item['keys'], item['context'], item['target'], item['mode'], item.get('translation'))
                    if key in seen:
                        continue
                    seen.add(key)
                    output.write(json.dumps(item, ensure_ascii=False) + '\n')
                    counts[name][item['mode']] += 1
    print(json.dumps({'splits': counts, 'teacher_errors_excluded': rejected}, indent=2))


if __name__ == '__main__':
    main()
