#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Blind 1-5 scoring of several English translations of the same fragments by an LLM judge.

    python3 tools/models/mt_judge.py --base-url URL --model NAME --input sample.jsonl \
        --system qwen3_32b=sample.jsonl:qwen3_32b --system glm=glm.jsonl:english --output judged.jsonl

--system name=file:field reads that field from rows matched by (chinese, context). Candidates are shuffled per
row under letters, identical texts are scored once, and empty ones get 0 without asking.
"""
import argparse
import collections
import concurrent.futures
import json
import random
import re
from pathlib import Path

from mt_teacher import Client, read_token

SYSTEM = (
    'You grade English translations of Chinese text typed in an input method. The user commits the English in place '
    'of the Chinese fragment, right after the given context. Score every candidate from 1 to 5: 5 = exact meaning of '
    'the whole fragment and nothing more, natural English that fits after the context; 4 = right meaning, slightly '
    'unnatural or a small nuance lost; 3 = partly right or awkward; 2 = mostly wrong, or adds/drops important content; '
    '1 = wrong or nonsense. Translating the context too, completing the sentence, or keeping Chinese is a fault. '
    'Reply with one JSON object mapping each letter to its score, e.g. {"A": 5, "B": 3}, and nothing else.')


def load(spec):
    name, _, rest = spec.partition('=')
    path, _, field = rest.rpartition(':')
    with open(path, encoding='utf-8') as handle:
        return name, {(r['chinese'], r['context']): r.get(field) or '' for r in map(json.loads, handle)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--system', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=32)
    parser.add_argument('--no-thinking', action='store_true')
    parser.add_argument('--env-file')
    parser.add_argument('--seed', type=int, default=20261017)
    args = parser.parse_args()
    client = Client(args.base_url, args.model, read_token(args), not args.no_thinking, 4096, 0.0)
    systems = dict(load(spec) for spec in args.system)
    with args.input.open(encoding='utf-8') as handle:
        rows = [json.loads(line) for line in handle if line.strip()]

    def judge(row):
        key = (row['chinese'], row['context'])
        texts = sorted({systems[s].get(key, '') for s in systems} - {''})
        rng_row = random.Random(f'{args.seed}:{row["chinese"]}:{row["context"]}')
        rng_row.shuffle(texts)
        letters = {chr(65 + i): t for i, t in enumerate(texts)}
        scores = {}
        if letters:
            prompt = json.dumps({'context': row['context'], 'fragment': row['chinese'], 'candidates': letters},
                                ensure_ascii=False)
            reply, _ = client.chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}])
            found = re.findall(r'\{[^{}]*\}', reply or '')
            try:
                graded = json.loads(found[-1]) if found else {}
                scores = {letters[k]: int(v) for k, v in graded.items() if k in letters}
            except (json.JSONDecodeError, ValueError, TypeError):
                scores = {}
        judged = {s: (scores.get(systems[s].get(key, '')) if systems[s].get(key) else 0) for s in systems}
        return {'chinese': row['chinese'], 'context': row['context'], 'judge': args.model,
                'english': {s: systems[s].get(key, '') for s in systems}, 'scores': judged}

    totals = collections.defaultdict(list)
    with args.output.open('w', encoding='utf-8') as out, concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for result in pool.map(judge, rows):
            out.write(json.dumps(result, ensure_ascii=False) + '\n')
            for s, v in result['scores'].items():
                if v is not None:
                    totals[s].append(v)
    for s, values in totals.items():
        print(f'{s:24s} mean {sum(values) / len(values):.3f}  >=4 {sum(v >= 4 for v in values) / len(values):.3f}  '
              f'<=2 {sum(v <= 2 for v in values) / len(values):.3f}  n {len(values)}', flush=True)


if __name__ == '__main__':
    main()
