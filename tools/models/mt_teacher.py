#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Translate Chinese fragments with a teacher behind an OpenAI-compatible endpoint (vLLM, an API).

    python3 tools/models/mt_teacher.py --base-url http://127.0.0.1:8711 --model Qwen3.8-27B \
        --input rows.jsonl --output labels.jsonl --workers 64 [--no-thinking]

Input rows need "chinese" and "context"; each output row adds "english" (empty when the reply was unusable)
and "teacher". Rows already in --output are skipped, so an interrupted run resumes. The token comes from
$MT_TEACHER_TOKEN or --env-file (KEY=VALUE lines with AUTH_TOKEN).
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

SYSTEM = (
    'You translate Chinese text typed in an input method into English that the user will commit in place of it. '
    'Translate ONLY the current fragment. The context is text committed just before it (Chinese or English); use it '
    'only to pick the right sense, never translate, repeat or continue it. The fragment may be a whole sentence, a '
    'word or an unfinished piece of one: translate exactly the words given, as the matching English piece, without '
    'completing the sentence or inventing a subject, object, number or explanation. Keep names, terms, negation and '
    'conditions. Translate internet slang and chat particles by their meaning in chat (菜 = bad at something, '
    '大佬 = expert, 哈哈 = haha), and express sentence-final particles such as 哦/啊/呢/吧 by tone, not as words. '
    'Already-English text stays as it is. Reply with one JSON object {"translation": "..."} and nothing else; '
    'the translation must not contain Chinese characters.')


def messages(row):
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps({'context': row['context'], 'fragment': row['chinese']}, ensure_ascii=False)}]


def parse(text):
    if not text:
        return ''
    found = re.findall(r'\{[^{}]*"translation"[^{}]*\}', text)
    if not found:
        return ''
    try:
        english = json.loads(found[-1])['translation']
    except (json.JSONDecodeError, KeyError, TypeError):
        return ''
    if not isinstance(english, str) or re.search('[一-鿿\t\r\n]', english):
        return ''
    return english.strip()


class Client:
    def __init__(self, base_url, model, token, thinking, max_tokens, temperature):
        self.url = base_url.rstrip('/') + '/v1/chat/completions'
        self.model, self.token, self.thinking = model, token, thinking
        self.max_tokens, self.temperature = max_tokens, temperature

    def chat(self, msgs):
        body = {'model': self.model, 'messages': msgs, 'max_tokens': self.max_tokens, 'temperature': self.temperature}
        if not self.thinking:
            body['chat_template_kwargs'] = {'enable_thinking': False}
        headers = {'Content-Type': 'application/json', 'User-Agent': 'curl/8.10'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=600) as reply:
                    data = json.load(reply)
                message = data['choices'][0]['message']
                return message.get('content') or '', data.get('usage', {})
            except Exception as error:  # network or server hiccup: back off and retry
                if attempt == 4:
                    print(f'request failed: {error}', file=sys.stderr, flush=True)
                    return '', {}
                time.sleep(2 ** attempt)


def read_token(args):
    if os.environ.get('MT_TEACHER_TOKEN'):
        return os.environ['MT_TEACHER_TOKEN']
    if args.env_file:
        for line in Path(args.env_file).expanduser().read_text().splitlines():
            key, _, value = line.partition('=')
            if key.strip() == 'AUTH_TOKEN':
                return value.strip().strip('"')
    return ''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=32)
    parser.add_argument('--no-thinking', action='store_true')
    parser.add_argument('--max-tokens', type=int, default=4096)
    parser.add_argument('--temperature', type=float, default=0.0)
    parser.add_argument('--env-file')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()

    client = Client(args.base_url, args.model, read_token(args), not args.no_thinking, args.max_tokens, args.temperature)
    with args.input.open(encoding='utf-8') as handle:
        rows = [json.loads(line) for line in handle if line.strip()][:args.limit]
    done = set()
    if args.output.exists():
        with args.output.open(encoding='utf-8') as handle:
            done = {(r['chinese'], r['context']) for r in map(json.loads, handle)}
    todo = [r for r in rows if (r['chinese'], r['context']) not in done]
    lock, started, tokens, count = threading.Lock(), time.time(), 0, 0
    name = args.model + ('' if client.thinking else ':no-thinking')

    def work(row):
        text, usage = client.chat(messages(row))
        return {**row, 'english': parse(text), 'teacher': name}, usage.get('completion_tokens', 0)

    with args.output.open('a', encoding='utf-8') as out, concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for result, used in pool.map(work, todo):
            with lock:
                out.write(json.dumps(result, ensure_ascii=False) + '\n')
                tokens, count = tokens + used, count + 1
                if count % 200 == 0 or count == len(todo):
                    out.flush()
                    rate = count / (time.time() - started)
                    print(json.dumps({'done': count, 'of': len(todo), 'rowsPerSecond': round(rate, 2),
                                      'completionTokensPerRow': round(tokens / count, 1)}), flush=True)


if __name__ == '__main__':
    main()
