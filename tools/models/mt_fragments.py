#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sample Chinese commit fragments from LCCC for teacher translation (mt_teacher.py input).

    python3 tools/models/mt_fragments.py --lccc lccc_base_train.jsonl.gz --exclude-paired experiment-4/paired.jsonl \
        --exclude-corpus mt-corpus --exclude semantic-eval.jsonl --count 500000 --output fragments.jsonl

A fragment's context is the text of the same message before it, as the input box holds it. A quarter of the rows
are marked context_kind "english": the teacher also translates the context, which then stands for English the
user already committed. A fifth are whole messages without context. Messages that are the parent or target of a
dev/test/eval row are skipped.
"""
import argparse
import gzip
import hashlib
import json
import random
from pathlib import Path

from fragment_corpus import fragments
from keys_llm_format import units
from translation_corpus import compact


def read(path):
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lccc', type=Path, required=True)
    parser.add_argument('--exclude-paired', type=Path, action='append', default=[], help='rows with split and parent_text')
    parser.add_argument('--exclude-corpus', type=Path, action='append', default=[], help='mt_corpus.py output directory')
    parser.add_argument('--exclude', type=Path, action='append', default=[], help='rows with target / parent_text')
    parser.add_argument('--count', type=int, default=500000)
    parser.add_argument('--rate', type=float, default=.02, help='share of messages considered')
    parser.add_argument('--seed', type=int, default=20261017)
    parser.add_argument('--source', default='lccc-base')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    held = set()
    for path in args.exclude_paired:
        held |= {r.get('parent_text') or r['target'] for r in read(path) if r.get('split') in ('dev', 'test')}
    for path in args.exclude:
        held |= {v for r in read(path) for v in (r.get('target'), r.get('parent_text')) if v}
    for directory in args.exclude_corpus:
        held |= {r['chinese'] for name in ('dev.jsonl', 'test.jsonl') for r in read(directory / name)}

    rng = random.Random(args.seed)
    seen, out, skipped = set(), [], 0
    with gzip.open(args.lccc, 'rt', encoding='utf-8') as stream:
        for line in stream:
            for raw in json.loads(line):
                if not isinstance(raw, str):
                    continue
                if int(hashlib.sha256(f'{args.seed}:{raw}'.encode()).hexdigest()[:8], 16) / 2**32 >= args.rate:
                    continue
                text = compact(raw)
                if not units(text) or text in held:
                    skipped += text in held
                    continue
                words, cursor = [], 0
                for word in raw.split():
                    start = text.find(word, cursor)
                    if start < 0:
                        break
                    if units(word):
                        words.append((start, start + len(word)))
                    cursor = start + len(word)
                if rng.random() < .2 and len(text) <= 28:
                    candidates = [{'target': text, 'context': ''}]
                else:
                    candidates = list(fragments({'parent_text': text, 'word_boundaries': words}, rng))
                for row in candidates:
                    chinese, context = row['target'], row['context']
                    if chinese in held or (chinese, context) in seen:
                        continue
                    seen.add((chinese, context))
                    kind = 'empty' if not context else 'english' if rng.random() < .25 else 'chinese'
                    out.append({'chinese': chinese, 'context': context, 'context_kind': kind, 'parent_text': text,
                                'source': args.source})
            if len(out) >= args.count:
                break
    rng.shuffle(out)
    with args.output.open('x', encoding='utf-8') as handle:
        for row in out[:args.count]:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    kinds = {k: sum(r['context_kind'] == k for r in out[:args.count]) for k in ('empty', 'chinese', 'english')}
    print(json.dumps({'rows': min(len(out), args.count), 'kinds': kinds, 'heldOutSkipped': skipped}), flush=True)


if __name__ == '__main__':
    main()
