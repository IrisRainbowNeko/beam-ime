#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One-stage translate decoding that keeps several Chinese readings: beam over the Chinese field, then English.

    python3 tools/models/onestage_diverse.py --checkpoint merged-e3 --input judge.jsonl --output eval/judge-os-div.json

Plain beam search over 中文<tab>English spends its beams on English variants of one or two readings. Here the
beams stop at the tab, the distinct Chinese readings are kept in beam order (--readings of them), and each one
gets its English greedily, continuing from the beam's own tokens. Cases are written in keys_llm_eval.py's
layout (displayCandidates English, displayComments Chinese), so onestage_compare.py and the judges read them.
"""
import argparse
import json
import time
from pathlib import Path

import torch

from keys_llm_format import ENGLISH_PATTERN, FRAGMENT_VERSION, HAN_PATTERN, RESULT_PREFIX, build_prompt, display_valid, letters, \
    parse_output, readings, units


def one_edit(a, b):
    """a and b differ by one substitution, insertion, deletion or adjacent swap."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def tolerant_valid(keys, parsed, max_edits):
    """display_valid, or the keys spell the Chinese with up to max_edits slips, each inside a syllable typed with two or more letters."""
    if display_valid(keys, parsed) or not max_edits:
        return display_valid(keys, parsed)
    pieces = units(parsed.get('comment') or '') if parsed.get('comment') else None
    if not pieces or parsed.get('translation_status') == 'need_more_input' or not parsed['result'] or HAN_PATTERN.search(parsed['result']):
        return False
    code, states = letters(keys), {(0, 0)}
    for piece in pieces:
        following = set()
        for position, edits in states:
            if ENGLISH_PATTERN.fullmatch(piece):
                if code.startswith(piece.lower(), position):
                    following.add((position + len(piece), edits))
                continue
            for reading in readings(piece):
                for width in range(1, len(reading) + 1):
                    prefix = reading[:width]
                    if code.startswith(prefix, position):
                        following.add((position + width, edits))
                    if edits < max_edits:
                        for length in (width - 1, width, width + 1):
                            if length >= 2 and one_edit(prefix, code[position:position + length]) and position + length <= len(code):
                                following.add((position + length, edits + 1))
        states = following
        if not states:
            return False
    return any(position == len(code) for position, _ in states)


def stop_tokens(tokenizer):
    return [i for i in range(len(tokenizer)) if '\t' in tokenizer.decode([i])]


def trim(ids, stops):
    for n, token in enumerate(ids):
        if token in stops:
            return ids[:n + 1], token
    return ids, None


def left_pad(sequences, pad_id, device):
    width = max(map(len, sequences))
    ids = torch.full((len(sequences), width), pad_id, dtype=torch.long)
    mask = torch.zeros((len(sequences), width), dtype=torch.long)
    for n, sequence in enumerate(sequences):
        ids[n, width - len(sequence):] = torch.tensor(sequence)
        mask[n, width - len(sequence):] = 1
    return ids.to(device), mask.to(device)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--beams', type=int, default=8)
    parser.add_argument('--readings', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--max-edits', type=int, default=0, help='accept readings the keys spell with this many slips')
    args = parser.parse_args()
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = [json.loads(line) for line in open(args.input, encoding='utf-8') if line.strip()]
    rows = [r for r in rows if r.get('mode', 'translate_en') == 'translate_en']
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint, padding_side='left')
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    model = AutoModelForCausalLM.from_pretrained(args.checkpoint, torch_dtype=torch.bfloat16, attn_implementation='sdpa')
    model.to(args.device).eval()
    eos = tokenizer.eos_token_id
    tabs = set(stop_tokens(tokenizer))
    order = sorted(range(len(rows)), key=lambda i: len(rows[i]['keys']))
    cases, started = [None] * len(rows), time.time()
    for start in range(0, len(order), args.batch_size):
        indices = order[start:start + args.batch_size]
        prompts = [tokenizer(build_prompt(rows[i]['keys'], rows[i].get('context', ''), 'translate_en', FRAGMENT_VERSION)
                             + RESULT_PREFIX, add_special_tokens=False)['input_ids'] for i in indices]
        ids, mask = left_pad(prompts, pad_id, args.device)
        with torch.no_grad():
            output = model.generate(input_ids=ids, attention_mask=mask, num_beams=args.beams, num_return_sequences=args.beams,
                                    do_sample=False, max_new_tokens=max(2 * len(rows[i]['keys']) + 8 for i in indices),
                                    pad_token_id=pad_id, eos_token_id=[eos, *tabs], use_cache=True, length_penalty=1.0)
        generated = output[:, ids.shape[1]:].tolist()
        pending = []
        for offset, index in enumerate(indices):
            seen = []
            for j in range(args.beams):
                head, stop = trim(generated[offset * args.beams + j], tabs | {eos})
                if stop is None or stop == eos:
                    continue
                chinese = tokenizer.decode(head).split('\t')[0]
                if chinese and chinese not in [c for c, _ in seen]:
                    seen.append((chinese, head))
            for chinese, head in seen[:args.readings]:
                pending.append((offset, prompts[offset] + head, head))
        english = []
        for chunk in range(0, len(pending), args.batch_size * 2):
            part = pending[chunk:chunk + args.batch_size * 2]
            ids2, mask2 = left_pad([p for _, p, _ in part], pad_id, args.device)
            budget = max(min(192, 4 * len(rows[indices[o]]['keys']) + 32) for o, _, _ in part)
            with torch.no_grad():
                tails = model.generate(input_ids=ids2, attention_mask=mask2, num_beams=1, do_sample=False,
                                       max_new_tokens=budget, pad_token_id=pad_id, eos_token_id=eos, use_cache=True)
            for (offset, _, head), tail in zip(part, tails[:, ids2.shape[1]:].tolist()):
                body, stop = trim(tail, {eos})
                english.append((offset, RESULT_PREFIX + tokenizer.decode(head + (body[:-1] if stop else body)), stop is not None))
        for offset, index in enumerate(indices):
            row = rows[index]
            displayed = []
            for _, text, finished in (e for e in english if e[0] == offset):
                candidate = parse_output(text, 'translate_en', FRAGMENT_VERSION)
                if finished and tolerant_valid(row['keys'], candidate, args.max_edits):
                    if candidate.get('translation_status') == 'need_more_input':
                        if not displayed:
                            displayed.append(candidate)
                        continue
                    if candidate['result'] not in [c['result'] for c in displayed]:
                        displayed.append(candidate)
            waiting = bool(displayed and displayed[0].get('translation_status') == 'need_more_input')
            cases[index] = {
                'id': row['id'], 'variant': row['variant'], 'keys': row['keys'], 'target': row.get('translation', ''),
                'mode': 'translate_en', 'source': row['target'], 'context': row.get('context', ''),
                'displayCandidates': [] if waiting else [c['result'] for c in displayed[:5]],
                'displayComments': [] if waiting else [c.get('comment') for c in displayed[:5]],
                'translationStatus': 'need_more_input' if waiting else 'ready',
            }
        print(json.dumps({'done': min(start + args.batch_size, len(order)), 'rows': len(order),
                          'elapsed': round(time.time() - started, 1)}), flush=True)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    finished = [c for c in cases if c]
    with out.with_suffix('.cases.jsonl').open('w', encoding='utf-8') as handle:
        for case in finished:
            handle.write(json.dumps(case, ensure_ascii=False) + '\n')
    n = len(finished)
    report = {'checkpoint': args.checkpoint, 'input': args.input, 'rows': n, 'beams': args.beams, 'readings': args.readings,
              'zhP1': sum(bool(c['displayComments']) and c['displayComments'][0] == c['source'] for c in finished) / n,
              'zhP5': sum(c['source'] in c['displayComments'] for c in finished) / n,
              'distinct': sum(len(set(c['displayComments'])) for c in finished) / n,
              'empty': sum(not c['displayCandidates'] for c in finished) / n, 'elapsedSeconds': round(time.time() - started, 1)}
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
