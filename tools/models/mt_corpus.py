#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the Chinese(+context) -> English corpus for the translate stage from teacher-paired rows.

    python3 tools/models/mt_corpus.py --paired experiment-1/paired.jsonl --paired experiment-4/paired.jsonl \
        --exclude experiment-4/semantic-eval.jsonl --output mt-corpus

Rows with a split field keep it; rows without one go to train. Test-split rows, rows of an excluded parent
and rows whose Chinese is an excluded target never reach train or dev. Writes train/dev/test.jsonl with
{"chinese", "context", "english", "source"}.
"""
import argparse
import json
from pathlib import Path

from mt_format import CONTEXT_CHARS


def read(path: Path) -> list[dict]:
    with path.open(encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--paired', type=Path, action='append', required=True)
    parser.add_argument('--exclude', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    excluded = [r for path in args.exclude for r in read(path)]
    parents = {r['parent_id'] for r in excluded if r.get('parent_id')}
    targets = {r['target'] for r in excluded}
    rows = [r for path in args.paired for r in read(path)]
    # Whole test sentences are held out too, so a train fragment never shares a sentence with a test fragment.
    test_text = {r.get('parent_text') or r['target'] for r in rows if r.get('split') == 'test'} | targets

    splits = {'train': [], 'dev': [], 'test': []}
    seen = set()
    dropped = {'not_ready': 0, 'excluded': 0, 'duplicate': 0}
    for r in rows:
        english = (r.get('translation') or '').strip()
        if r.get('translation_status') not in (None, 'ready') or not english or '\n' in english:
            dropped['not_ready'] += 1
            continue
        split = r.get('split') or 'train'
        chinese, context = r['target'].strip(), (r.get('context') or '').strip()[-CONTEXT_CHARS:]
        if split != 'test' and (r.get('parent_id') in parents or chinese in test_text
                                or (r.get('parent_text') or chinese) in test_text):
            dropped['excluded'] += 1
            continue
        if (chinese, context) in seen:
            dropped['duplicate'] += 1
            continue
        seen.add((chinese, context))
        splits[split].append({'chinese': chinese, 'context': context, 'english': english, 'source': r.get('source', '')})

    args.output.mkdir(parents=True, exist_ok=True)
    for name, items in splits.items():
        with (args.output / f'{name}.jsonl').open('w', encoding='utf-8') as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + '\n')
    print(json.dumps({name: len(items) for name, items in splits.items()} | {'dropped': dropped}, ensure_ascii=False))


if __name__ == '__main__':
    main()
