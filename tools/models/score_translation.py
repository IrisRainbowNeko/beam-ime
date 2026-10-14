#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Score generated bilingual cases with COMET and chrF; no model training."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--model-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--display', action='store_true', help='score the actual filtered first candidate')
    args = parser.parse_args()
    import torch
    from comet import load_from_checkpoint
    from sacrebleu.metrics import CHRF
    torch.set_num_threads(8)
    rows = [json.loads(line) for line in args.cases.read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = [row for row in rows if row['mode'] == 'translate_en' and row.get('expectedTranslationStatus', 'ready') == 'ready']
    if args.display:
        rows = [{**row, 'english': row['displayEnglish'], 'comment': row['displayComment']} for row in rows]
    if not rows:
        raise ValueError('no translation cases to score')
    samples = [{'src': row['source'], 'mt': row['english'] or '', 'ref': row['target']} for row in rows]
    model = load_from_checkpoint(str(args.checkpoint)).eval()
    prediction = model.predict(samples, batch_size=args.batch_size, gpus=0, num_workers=0)
    scores = [float(value) for value in prediction.scores]
    groups = {'all': list(range(len(rows))),
              'correct_comment': [i for i, row in enumerate(rows) if row['comment'] == row['source']]}
    groups.update({variant: [i for i, row in enumerate(rows) if row['variant'] == variant]
                   for variant in sorted({row['variant'] for row in rows})})
    chrf = CHRF(word_order=2)
    metrics = {}
    for name, indices in groups.items():
        if indices:
            metrics[name] = {'rows': len(indices), 'comet': sum(scores[i] for i in indices) / len(indices),
                'chrf2++': chrf.corpus_score([samples[i]['mt'] for i in indices],
                                            [[samples[i]['ref'] for i in indices]]).score}
    digest = hashlib.sha256()
    with args.checkpoint.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    report = {'model': args.model_id, 'revision': args.model_revision, 'checkpointSha256': digest.hexdigest(),
              'cases': str(args.cases), 'metrics': metrics, 'chrfSignature': str(chrf.get_signature()),
              'packages': {name: importlib.metadata.version(name) for name in ('unbabel-comet', 'sacrebleu', 'torch', 'transformers')},
              'note': 'COMET and chrF are reference-based scores, not correctness percentages; correct_comment is a conditional subset.'}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with args.output.with_suffix('.cases.jsonl').open('w', encoding='utf-8') as stream:
        for row, score in zip(rows, scores):
            stream.write(json.dumps({'id': row['id'], 'keys': row['keys'], 'variant': row['variant'],
                                     'source': row['source'], 'comment': row['comment'], 'english': row['english'],
                                     'reference': row['target'], 'comet': score}, ensure_ascii=False) + '\n')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
