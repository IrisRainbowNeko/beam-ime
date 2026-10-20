#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Score every system of an mt_eval.py output: exact match, COMET-22 and unknown-word rate.

    python3 tools/models/mt_score.py gen.json --comet models/wmt22-comet-da --vocab mt-corpus/train.jsonl

Unknown words are words found in neither the training English nor the row's reference; outputs such as
"adients" or "peps" show up there. Writes <gen>.report.json.
"""
import argparse
import collections
import json
import re
from pathlib import Path

WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', text.lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('gen', type=Path)
    parser.add_argument('--comet', type=Path)
    parser.add_argument('--vocab', type=Path, required=True)
    parser.add_argument('--gpus', type=int, default=0)
    args = parser.parse_args()
    data = json.loads(args.gen.read_text(encoding='utf-8'))
    rows = data['rows']
    vocab = set()
    with args.vocab.open(encoding='utf-8') as handle:
        for line in handle:
            vocab.update(WORD.findall(json.loads(line)['english'].lower()))
    model = None
    if args.comet:
        from comet import load_from_checkpoint
        model = load_from_checkpoint(str(args.comet / 'checkpoints' / 'model.ckpt'))

    report = {}
    for name, hyps in data['systems'].items():
        if model:
            scores = model.predict([{'src': r['chinese'], 'mt': h, 'ref': r['reference']} for r, h in zip(rows, hyps)],
                                   batch_size=32, gpus=args.gpus, progress_bar=False).scores
        else:
            scores = [0.0] * len(rows)
        groups = collections.defaultdict(list)
        for r, h, s in zip(rows, hyps, scores):
            known = vocab | set(WORD.findall(r['reference'].lower()))
            unknown = [w for w in WORD.findall(h.lower()) if w not in known]
            for key in ('all', r['variant']):
                groups[key].append((norm(h) == norm(r['reference']), s, not h, bool(unknown)))
        report[name] = {k: {'n': len(v), 'exact': sum(x[0] for x in v) / len(v), 'comet': sum(x[1] for x in v) / len(v),
                            'empty': sum(x[2] for x in v) / len(v), 'unknown_word': sum(x[3] for x in v) / len(v)}
                        for k, v in groups.items()}
        a = report[name]['all']
        print(f"{name:26s} exact {a['exact']:.3f}  comet {a['comet']:.4f}  unknown-word {a['unknown_word']:.3f}  "
              f"empty {a['empty']:.3f}", flush=True)
    args.gen.with_suffix('.report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
