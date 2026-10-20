#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Have a strong LLM write chat messages from domains LCCC barely covers (technical terms, embedded English).

    python3 tools/models/mt_domain.py --base-url URL --model NAME --env-file glm.env --calls 1000 \
        --exclude terms.jsonl --output domain.jsonl.gz

The output has LCCC's layout (one JSON list of word-segmented messages per line), so mt_fragments.py turns it
into fragments with --rate 1. Messages containing a --exclude row's Chinese (four characters or more) are dropped
so a check set stays unseen.
"""
import argparse
import concurrent.futures
import gzip
import json
import random
import re
from pathlib import Path

from mt_teacher import Client, read_token

DOMAINS = (
    'programming and debugging', 'git, GitHub and code review', 'Docker, Kubernetes and servers', 'Linux desktop and terminal',
    'Python, conda and pip environments', 'machine learning training and GPUs', 'large language models and prompts',
    'input methods, keyboards and fonts', 'web front-end and APIs', 'databases and SQL', 'networking, Wi-Fi and VPN',
    'computer hardware and building a PC', 'phones, apps and system updates', 'office software, Excel and PPT',
    'online shopping and delivery', 'stocks, funds and personal finance', 'video games and Steam', 'anime, manga and fandom',
    'Bilibili, streaming and video editing', 'photography and cameras', 'music and concerts', 'movies and TV series',
    'school, exams and homework', 'academic papers and research', 'job hunting, interviews and HR', 'startups and product meetings',
    'travel, flights and hotels', 'food, cooking and restaurants', 'health, hospitals and medicine', 'fitness and sports',
    'cars and driving', 'home appliances and repairs', 'renting and real estate', 'pets', 'weather and daily plans',
    'social media and memes', 'security, passwords and accounts', 'design, Photoshop and Figma', 'math and physics homework',
    'electronics, Arduino and soldering')
STYLES = ('chatting with a close friend', 'talking to a coworker on a work chat', 'posting in a group chat',
          'asking for help online', 'complaining casually', 'giving a quick status update', 'replying to a family member')

SYSTEM = (
    'You write realistic Chinese messages that people type with a pinyin input method in chat apps. Write varied '
    'messages of 4 to 40 characters about the given topic and situation: questions, replies, complaints, plans, '
    'half-sentences people really send. Use the terms, product names, abbreviations and English words people in '
    'that field really type (some messages keep English like Docker, PR, bug, GPU, Excel inside the Chinese, many '
    'use the Chinese term such as 编译, 推理, 显存, 候选词), plus normal chat particles and slang where natural. No '
    'numbering, no emoji. Split every message into words with single spaces, the way a Chinese word segmenter '
    'would (English words stay whole). Reply with one JSON object {"messages": ["...", ...]} and nothing else.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--env-file')
    parser.add_argument('--calls', type=int, default=1000)
    parser.add_argument('--per-call', type=int, default=20)
    parser.add_argument('--workers', type=int, default=32)
    parser.add_argument('--max-tokens', type=int, default=8192)
    parser.add_argument('--exclude', type=Path, action='append', default=[])
    parser.add_argument('--seed', type=int, default=20261018)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    client = Client(args.base_url, args.model, read_token(args), True, args.max_tokens, 1.0)
    held = [r['chinese'] for path in args.exclude for r in map(json.loads, path.open(encoding='utf-8'))
            if len(r['chinese']) >= 4]
    rng = random.Random(args.seed)
    jobs = [(DOMAINS[i % len(DOMAINS)], rng.choice(STYLES)) for i in range(args.calls)]

    def work(job):
        domain, style = job
        reply, _ = client.chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps(
            {'topic': domain, 'situation': style, 'count': args.per_call}, ensure_ascii=False)}])
        found = re.findall(r'\{[^{}]*"messages"[^{}]*\}', reply or '', re.S)
        try:
            messages = json.loads(found[-1])['messages'] if found else []
        except (json.JSONDecodeError, KeyError, TypeError):
            messages = []
        return domain, [m.strip() for m in messages if isinstance(m, str) and re.search('[一-鿿]', m)]

    seen, kept, dropped = set(), 0, 0
    with gzip.open(args.output, 'xt', encoding='utf-8') as out, concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for n, (domain, messages) in enumerate(pool.map(work, jobs), 1):
            fresh = []
            for message in messages:
                plain = message.replace(' ', '')
                if plain in seen or any(h in plain for h in held):
                    dropped += 1
                    continue
                seen.add(plain)
                fresh.append(message)
            kept += len(fresh)
            if fresh:
                out.write(json.dumps(fresh, ensure_ascii=False) + '\n')
            if n % 50 == 0 or n == len(jobs):
                out.flush()
                print(json.dumps({'calls': n, 'of': len(jobs), 'messages': kept, 'dropped': dropped}), flush=True)


if __name__ == '__main__':
    main()
