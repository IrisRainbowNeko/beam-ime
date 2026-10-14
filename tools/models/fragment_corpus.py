#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sample public commit fragments after assigning their parent conversations."""
import argparse
import collections
import gzip
import hashlib
import json
from pathlib import Path
import random
import re

from keys_llm_format import units
from prepare_dual_task import split_rows, sentence
from translation_corpus import compact, json_rows, focused_rows

LENGTH_WEIGHTS = (.5, .3, .2)
CONTEXT_WEIGHTS = {'chinese': .5, 'english': .25, 'mixed': .15, 'empty': .1}
TERMS = {
    '马来西亚': 'Malaysia', '新加坡': 'Singapore', '中国': 'China', '日本': 'Japan',
    '韩国': 'South Korea', '英国': 'the UK', '美国': 'the US', '法国': 'France',
    '德国': 'Germany', '加拿大': 'Canada', '澳大利亚': 'Australia', '印度': 'India',
    '参数量': 'parameter count', '参数': 'parameters', '预训练': 'pretraining',
    '训练集': 'training set', '测试集': 'test set', '验证集': 'validation set',
    '推理': 'inference', '训练': 'training', '量化': 'quantization', '词表': 'vocabulary',
    '词库': 'dictionary', '上下文': 'context', '输入法': 'input method', '候选': 'candidates',
    '模型': 'model', '权重': 'weights', '显存': 'GPU memory', '内存': 'memory',
    '学习率': 'learning rate', '梯度': 'gradients', '延迟': 'latency', '吞吐量': 'throughput',
    '处理器': 'processor', '驱动': 'driver', '编译': 'compilation', '部署': 'deployment',
}


def length_bucket(text):
    return 0 if len(text) <= 2 else 1 if len(text) <= 4 else 2


def partition(identity, seed):
    score = int(hashlib.sha256(f'{seed}:{identity}'.encode()).hexdigest()[:16], 16) / 2**64
    return 'test' if score < .05 else 'dev' if score < .1 else 'train'


def fragments(parent, rng):
    """All context ends before the current fragment; no other dialogue turn is used."""
    text = parent['parent_text']
    boundaries = parent.get('word_boundaries')
    spans = (boundaries if boundaries else [(m.start(), m.end()) for m in re.finditer(r'[一-鿿]|[A-Za-z]+', text)])
    start = 0
    while start < len(spans):
        width = rng.choice((1, 1, 2, 2, 3, 4, 5, 6, 8))
        end = start + 1
        while end < len(spans) and spans[end - 1][1] - spans[start][0] < width:
            end += 1
        lo, hi = spans[start][0], spans[end - 1][1]
        start = end
        target = text[lo:hi]
        if not units(target) or len(target) > 28:
            continue
        yield {**{k: v for k, v in parent.items() if k != 'word_boundaries'}, 'target': target, 'context': text[:lo][-64:],
               'fragment_start': lo, 'fragment_end': hi}


def authored_parents():
    prefixes = ('我想了解', '我们正在讨论', '这次需要介绍', '这里提到的是', '接下来说明',
                '今天研究的是', '刚才查到', '我还想问问', '大家正在关注', '这次的主题是',
                '请解释一下', '详细介绍一下', '我需要查询', '请帮我查找', '我们先看看',
                '有人问到了', '资料里写到了', '文章提到了', '报告涉及', '问题关系到',
                '今天想聊聊', '昨天讨论了', '下一步考虑', '这里需要注意', '现在请说说',
                '先简单说明', '我正在查阅', '我们刚才提到', '这部分关于', '我想再确认',
                '朋友提到了', '同事正在介绍', '老师说到了', '我刚刚搜到', '这次想了解',
                '接着讨论', '继续介绍', '请重点说明', '今天要研究', '我们的讨论涉及')
    for target, translation in TERMS.items():
        for prefix in prefixes:
            yield {'target': target, 'translation': translation, 'context': prefix,
                   'parent_text': prefix + target, 'source': 'beam-fragments',
                   'session_id': 'term:' + prefix, 'focused': True}
    for row in focused_rows():
        yield {**row, 'parent_text': row['context'] + row['target'], 'focused': True}


def sample(args):
    rng = random.Random(args.seed)
    old = [row for path in args.base_corpus for row in json_rows(path)]
    original_splits = split_rows(old, args.base_seed)
    sessions, targets = {}, {}
    for name, rows in original_splits.items():
        for row in rows:
            targets[sentence(row['target'])] = name
            if row.get('session_id'):
                sessions[(row.get('source', ''), row['session_id'])] = name
    pools = collections.defaultdict(list)
    seen, counts = set(), collections.Counter()
    assigned_parents = dict(targets)
    limits = {'train': args.count, 'dev': 2000, 'test': 2000}

    def add(row):
        identity = row.get('session_id', row['parent_text'])
        by_session = sessions.get((row['source'], identity))
        by_text = targets.get(sentence(row['parent_text']))
        if by_session and by_text and by_session != by_text:
            return
        split = by_session or by_text or partition(row['source'] + ':' + identity, args.seed)
        parent_key = sentence(row['parent_text'])
        if parent_key in assigned_parents and assigned_parents[parent_key] != split:
            return
        assigned_parents[parent_key] = split
        row = {**row, 'split': split, 'parent_id': hashlib.sha256(
            (row['source'] + ':' + identity + ':' + row['parent_text']).encode()).hexdigest()[:24]}
        base_context = row['context']
        for relation in ('related', 'unrelated', 'empty'):
            context = base_context if relation == 'related' else '我们正在讨论另外一个话题' if relation == 'unrelated' else ''
            for kind in CONTEXT_WEIGHTS:
                if (kind == 'empty') != (relation == 'empty') or (kind != 'empty' and not context):
                    continue
                key = (row['target'], context, kind)
                if key in seen:
                    continue
                seen.add(key)
                group = (split, length_bucket(row['target']), kind, bool(row.get('focused')))
                counts[group] += 1
                value = {**row, 'context': context, 'context_kind': kind, 'context_relation': relation}
                pool = pools[group]
                capacity = max(64, int(limits[split] * LENGTH_WEIGHTS[group[1]] * CONTEXT_WEIGHTS[kind]))
                if len(pool) < capacity:
                    pool.append(value)
                else:
                    index = rng.randrange(counts[group])
                    if index < capacity:
                        pool[index] = value

    for row in authored_parents():
        add(row)
    with gzip.open(args.lccc, 'rt', encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            raw_turns = [value for value in json.loads(line) if isinstance(value, str)]
            turns = [compact(value) for value in raw_turns]
            session = hashlib.sha256('\n'.join(turns).encode()).hexdigest()[:24]
            for raw, turn in zip(raw_turns, turns):
                words, cursor = [], 0
                for word in raw.split():
                    start = turn.find(word, cursor)
                    if start < 0:
                        raise ValueError('public corpus segmentation does not match compact text')
                    if units(word):
                        words.append((start, start + len(word)))
                    cursor = start + len(word)
                parent = {'parent_text': turn, 'source': 'lccc-base', 'session_id': session,
                          'focused': False, 'word_boundaries': words}
                for row in fragments(parent, rng):
                    add(row)
            if index + 1 >= args.parent_limit:
                break
    selected = []
    for split, count in limits.items():
        for bucket, weight in enumerate(LENGTH_WEIGHTS):
            for kind, fraction in CONTEXT_WEIGHTS.items():
                n = round(count * weight * fraction)
                focused = min(round(n * .2), len(pools[(split, bucket, kind, True)]))
                for special, required in ((True, focused), (False, n - focused)):
                    pool = pools[(split, bucket, kind, special)]
                    if len(pool) < required:
                        raise ValueError(f'insufficient public fragments: {split}/{bucket}/{kind}/{special}: {len(pool)} < {required}')
                    selected.extend(rng.sample(pool, required))
        needed = round(count * .2) - sum(bool(r['focused']) for r in selected if r['split'] == split)
        used = {(r['target'], r['context'], r['context_kind']) for r in selected}
        for index, row in enumerate(selected):
            if needed <= 0:
                break
            if row['split'] != split or row['focused']:
                continue
            pool = pools[(split, length_bucket(row['target']), row['context_kind'], True)]
            while pool:
                candidate = pool.pop()
                key = (candidate['target'], candidate['context'], candidate['context_kind'])
                if key not in used:
                    selected[index] = candidate; used.add(key); needed -= 1
                    break
        if needed > 0 and split == 'train':
            raise ValueError(f'insufficient focused public fragments in {split}: missing {needed}')
    rng.shuffle(selected)
    args.output.mkdir(parents=True)
    with (args.output / 'teacher-input.jsonl').open('x', encoding='utf-8') as stream:
        for row in selected:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    for name, rows in original_splits.items():
        with (args.output / (name + '-base.jsonl')).open('x', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps({**row, 'split': name}, ensure_ascii=False) + '\n')
    print(json.dumps({'rows': len(selected), 'splits': dict(collections.Counter(r['split'] for r in selected)),
                      'lengths': dict(collections.Counter(length_bucket(r['target']) for r in selected)),
                      'contexts': dict(collections.Counter(r['context_kind'] for r in selected)),
                      'focused': {split: sum(bool(r['focused']) for r in selected if r['split'] == split)
                                  for split in limits}}), flush=True)


def teacher_messages(row):
    return [{'role': 'system', 'content':
        'Translate ONLY the current fragment into concise English suitable for committing in an input method. '
        'Context is previously committed text, used only for disambiguation. Never translate, repeat, or continue it. '
        'Never invent a subject, object, purpose, location, number, or explanation. Preserve names, terminology, '
        'negation, and conditions. Default to ready: dictionary words and unfinished phrases can be committed. '
        'Missing sentence arguments, an unknown subject, unrelated context, or several possible senses are NOT '
        'reasons to wait. Translate just the supplied words, choosing the common sense if context does not help. '
        'Examples: 水笔 -> gel pen; 需要 -> need; 这个 -> this; 来形容 -> to describe; '
        '刚躺到床上 -> just lay down on the bed; 我不知道哪里 -> I do not know where; 我要是 -> if I. '
        'Use need_more_input ONLY when no meaningful English fragment preserves the current text, '
        'such as an isolated grammatical particle 的, 了, or 吗 that needs restructuring earlier words. '
        'For need_more_input the translation must be empty. Punctuation alone is not a translation. '
        'Preserve already-English text exactly. Return one JSON object with translation_status (ready or '
        'need_more_input), translation, and english_context (a translation of ONLY the preceding context). '
        'No Chinese characters are allowed in either English field.'},
        {'role': 'user', 'content': json.dumps({'context': row['context'], 'fragment': row['target'],
            **({'preferred_term_translation': row['translation']} if row.get('translation') else {})}, ensure_ascii=False)}]


def teacher_result(row, text):
    fenced = re.fullmatch(r'```json\s*\n(.*)\n```', text.strip(), flags=re.S)
    if fenced:
        text = fenced.group(1)
    value = json.loads(text)
    status, translation = value['translation_status'], value['translation']
    if row['target'].isascii() and units(row['target']):
        status, translation = 'ready', row['target']
    if status not in ('ready', 'need_more_input') or not isinstance(translation, str):
        raise ValueError('invalid teacher status')
    if (status == 'ready' and not re.search('[A-Za-z0-9]', translation)) or (status == 'need_more_input' and translation):
        raise ValueError('teacher text conflicts with status')
    if re.search('[一-鿿\t\r\n]', translation):
        raise ValueError('invalid English translation')
    context = row['context']
    if row['context_kind'] in ('english', 'mixed'):
        english_context = value['english_context']
        if not isinstance(english_context, str) or not english_context.strip() or re.search('[一-鿿\t\r\n]', english_context):
            raise ValueError('invalid English context')
        context = english_context if row['context_kind'] == 'english' else context[-24:] + ' ' + english_context[-32:]
    return {**row, 'context': context[-64:], 'translation': translation.strip(), 'translation_status': status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lccc', type=Path, required=True)
    parser.add_argument('--base-corpus', nargs='+', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=30000)
    parser.add_argument('--parent-limit', type=int, default=100000)
    parser.add_argument('--seed', type=int, default=20261014)
    parser.add_argument('--base-seed', type=int, default=20261012)
    sample(parser.parse_args())


if __name__ == '__main__':
    main()
