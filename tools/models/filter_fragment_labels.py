#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Rebalance teacher-labelled fragments: diversify unrelated contexts and cap waiting rows."""
import argparse
import collections
import json
import random
import re
from pathlib import Path

TEMPLATE = re.compile(r'^(我们正在讨论|we are discussing)', re.I)
PARTICLES = set('的了啊吗呢吧嘛么啦呀哦嗯哈哇')


def is_particle(row):
    return len(row['target']) == 1 and row['target'] in PARTICLES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-share', type=float, default=.10, help='waiting rows as a share of each split')
    parser.add_argument('--min-test-wait', type=int, default=210)
    parser.add_argument('--particle-share', type=float, default=.25, help='single-particle share of kept waits')
    parser.add_argument('--seed', type=int, default=20261014)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    rows = [json.loads(line) for line in args.input.read_text().splitlines()]
    stats = collections.Counter()

    # Unrelated contexts: replace the three fixed filler sentences by a related context of another row
    # of the same split and context kind, so "ignore irrelevant context" is learned from varied text.
    pools = collections.defaultdict(list)
    for row in rows:
        if row['context_relation'] == 'related':
            pools[(row['split'], row['context_kind'])].append(row['context'])
    for row in rows:
        if row['context_relation'] == 'unrelated' and TEMPLATE.match(row['context']):
            pool = pools[(row['split'], row['context_kind'])]
            row['context'] = rng.choice(pool)
            stats['contexts_replaced'] += 1

    kept = []
    for split in ('train', 'dev', 'test'):
        part = [r for r in rows if r['split'] == split]
        waits = [r for r in part if r.get('translation_status') == 'need_more_input']
        others = [r for r in part if r.get('translation_status') != 'need_more_input']
        total_after = len(others) / (1 - args.wait_share)
        quota = max(round(total_after * args.wait_share), args.min_test_wait if split == 'test' else 0)
        particles = [r for r in waits if is_particle(r)]
        words = [r for r in waits if not is_particle(r)]
        rng.shuffle(particles), rng.shuffle(words)
        n_particles = min(len(particles), round(quota * args.particle_share))
        chosen = particles[:n_particles] + words[:quota - n_particles]
        stats[split + '_waits_before'] += len(waits)
        stats[split + '_waits_kept'] += len(chosen)
        kept += others + chosen
    rng.shuffle(kept)
    args.output.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in kept))
    stats['rows_before'], stats['rows_after'] = len(rows), len(kept)
    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()
