#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pick the rows of a bulk teacher run that a stronger teacher should translate again.

    python3 tools/models/mt_hard.py --labels q38.jsonl --output hard.jsonl --limit 75000 --calibration 5000

Reasons, most important first: no usable English; chat slang; sentence-final particles; a rare English word
(seen at most twice in the whole run, often a made-up one); English much longer than the Chinese (added content);
one or two Hanzi. A random calibration sample is added on top so the two teachers can be compared.
"""
import argparse
import collections
import json
import random
import re
from pathlib import Path

SLANG = ('菜', '大佬', '大神', '触', '萌', '坑', '吐槽', '槽', '怼', '撩', '逗比', '屌', '牛逼', '卧槽', '666', '233',
         '么么', '酱紫', '神马', '肿么', '稀饭', '童鞋', '有木有', '给力', '土豪', '高富帅', '白富美', '卖萌', '刷屏',
         '水贴', '灌水', '粉丝', '黑粉', '喷', '翻车', '安利', '打call', '狗带', '傲娇', '腹黑', '呆萌', '逆袭', '屌丝',
         '小鲜肉', '然并卵', '尼玛', '妈蛋', '我去', '靠谱', '奇葩', '二货', '蛋疼', '躺枪', '围观', '亲', '赞', '顶',
         '沙发', '楼主', '霸气', '鸭梨', '杯具', '洗洗睡', '你懂的', '伤不起', 'hold住', '有爱', '囧', '雷', '汗',
         '晕', '醉了', '跪了', '扎心', '戳', '基友', '闺蜜', '男神', '女神', '颜值', '撒娇', '作死', '打脸')
PARTICLES = re.compile('[哦啊呢吧呀嘛啦咯嘞噢呗哇喔诶哈嘿呐咩]')
WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=75000)
    parser.add_argument('--calibration', type=int, default=5000)
    parser.add_argument('--seed', type=int, default=20261017)
    args = parser.parse_args()
    with args.labels.open(encoding='utf-8') as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    frequency = collections.Counter(w for r in rows for w in set(WORD.findall(r['english'].lower())))

    def reason(row):
        chinese, english = row['chinese'], row['english']
        hanzi = len(re.findall('[一-鿿]', chinese))
        if not english:
            return 0, 'empty'
        if any(term in chinese for term in SLANG):
            return 1, 'slang'
        if PARTICLES.search(chinese):
            return 2, 'particle'
        if any(frequency[w] <= 2 for w in WORD.findall(english.lower())):
            return 3, 'rare_word'
        if len(english.split()) > 3 * max(hanzi, 1) + 3:
            return 4, 'long'
        if hanzi <= 2:
            return 5, 'short'
        return None

    rng = random.Random(args.seed)
    scored = [(reason(r), rng.random(), i) for i, r in enumerate(rows)]
    picked = sorted((s for s in scored if s[0]), key=lambda s: (s[0][0], s[1]))[:args.limit]
    chosen = {i: why[1] for why, _, i in picked}
    rest = [i for i in range(len(rows)) if i not in chosen]
    for i in rng.sample(rest, min(args.calibration, len(rest))):
        chosen[i] = 'calibration'
    with args.output.open('w', encoding='utf-8') as handle:
        for i, why in chosen.items():
            row = rows[i]
            source = {k: v for k, v in row.items() if k not in ('english', 'teacher')}
            if 'context_chinese' in row:  # the strong teacher translates the Chinese context again
                source['context'] = row['context_chinese']
                del source['context_chinese']
            handle.write(json.dumps({**source, 'hard_reason': why, 'bulk_english': row['english'],
                                     'bulk_context': row['context']}, ensure_ascii=False) + '\n')
    print(json.dumps({'rows': len(rows), 'picked': len(chosen),
                      'reasons': collections.Counter(chosen.values()),
                      'eligible': collections.Counter(s[0][1] for s in scored if s[0])}), flush=True)


if __name__ == '__main__':
    main()
