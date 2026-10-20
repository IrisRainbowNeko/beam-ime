#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Merge a bulk teacher run with the strong teacher's re-translations into mt_train.py corpus files.

    python3 tools/models/mt_merge.py --bulk q38.jsonl --strong glm.jsonl --base-corpus mt-corpus --output mt-corpus-v2

A strong-teacher row replaces the bulk row of the same fragment when it has English (its context replaces the
bulk context too, since English contexts were translated again). Rows without English are dropped. The dev split
holds out whole messages by hash; test.jsonl is copied from --base-corpus so scores stay comparable.
--keep-base-train adds the base corpus's training rows (an older teacher) as well, taking the strong teacher's
translation where it has one. --extra adds teacher-labelled rows of other sources (mt_domain.py messages), split
the same way.
"""
import argparse
import collections
import hashlib
import json
import shutil
from pathlib import Path


def read(path):
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def key(row):
    return row['chinese'], row.get('context_chinese', row['context'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bulk', type=Path, required=True)
    parser.add_argument('--strong', type=Path, action='append', default=[])
    parser.add_argument('--base-corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dev-share', type=float, default=.005)
    parser.add_argument('--keep-base-train', action='store_true')
    parser.add_argument('--extra', type=Path, action='append', default=[])
    args = parser.parse_args()

    strong = {}
    for path in args.strong:
        for row in read(path):
            if row['english']:
                # The strong teacher saw the Chinese context; its own English context goes with its translation.
                strong[(row['chinese'], row.get('context_chinese', row['context']))] = row
    splits, teachers = collections.defaultdict(list), collections.Counter()
    for row in read(args.bulk) + [r for path in args.extra for r in read(path)]:
        chosen = strong.get(key(row), row)
        if not chosen['english']:
            teachers['dropped'] += 1
            continue
        teachers[chosen['teacher']] += 1
        message = row.get('parent_text') or row['chinese']
        held = int(hashlib.sha256(message.encode()).hexdigest()[:8], 16) / 2**32 < args.dev_share
        splits['dev' if held else 'train'].append({'chinese': chosen['chinese'], 'context': chosen['context'],
                                                   'english': chosen['english'], 'source': row.get('source', ''),
                                                   'teacher': chosen['teacher']})
    if args.keep_base_train:
        for row in read(args.base_corpus / 'train.jsonl'):
            chosen = strong.get(key(row), {**row, 'teacher': 'Qwen3-32B'})
            teachers[chosen['teacher'] + ':base'] += 1
            splits['train'].append({'chinese': row['chinese'], 'context': chosen['context'], 'english': chosen['english'],
                                    'source': row.get('source', ''), 'teacher': chosen['teacher']})
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        with (args.output / f'{name}.jsonl').open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    shutil.copy(args.base_corpus / 'test.jsonl', args.output / 'test.jsonl')
    print(json.dumps({'train': len(splits['train']), 'dev': len(splits['dev']), 'teachers': teachers}), flush=True)


if __name__ == '__main__':
    main()
