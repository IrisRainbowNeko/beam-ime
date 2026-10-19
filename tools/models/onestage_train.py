#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Train the one-stage translate adapter: keys -> "Chinese<TAB>English" in one pass (keys_llm_v4, translate_en).

    python3 tools/models/onestage_train.py --model runs/r5-v2ex/epoch-1 --corpus onestage-4b \
        --mt-teacher r4b/full/epoch-2 --output onestage --lora-rank 128

LoRA on the frozen IME checkpoint. The loss covers the Chinese, the tab, the English and the end token. Two
token-level distillation terms, KL(teacher || student):
  * Chinese tokens from the IME itself (the same weights with the adapter disabled, keys_llm_v1 input prompt),
    so the Chinese of translate mode stays as close as possible to the ordinary candidates;
  * English tokens from a zh_en_v1 translator (mt_train.py checkpoint, same tokenizer) given the gold Chinese.
Tokens are matched by position inside each field; a field whose token ids differ between the student and a
teacher (tokenisation at a field boundary) is matched from its end and the unmatched head is left to the CE term.
"""
import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from keys_llm_format import FRAGMENT_VERSION, RESULT_PREFIX, build_prompt
from mt_format import prompt as zh_en_prompt
from mt_train import batches, read


def spans(tokenizer, text, regions):
    """Token ids of text and, per (start, end) character region, the token positions that lie inside it."""
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = encoded['input_ids'], encoded['offset_mapping']
    # A token belongs to the region holding its last character (so "\tThe" counts as English).
    return ids, [[i for i, (a, b) in enumerate(offsets) if start < b <= end] for start, end in regions]


def matched(student_ids, student_positions, teacher_ids, teacher_positions):
    """Pairs of (student position, teacher position) for the common suffix of a field's token ids."""
    pairs = []
    for s, t in zip(reversed(student_positions), reversed(teacher_positions)):
        if student_ids[s] != teacher_ids[t]:
            break
        pairs.append((s, t))
    return pairs[::-1]


def encode(tokenizer, row, max_length):
    head = build_prompt(row['keys'], row['context'], 'translate_en', FRAGMENT_VERSION) + RESULT_PREFIX
    chinese, english = row['target'], row['translation']
    text = head + chinese + '\t' + english
    a, b = len(head), len(head) + len(chinese)
    ids, (zh, en) = spans(tokenizer, text, [(a, b), (b, len(text))])
    eos = tokenizer.eos_token_id
    ids = ids + [eos]
    first = min(zh + en) if zh else None
    if first is None or not en or len(ids) > max_length:
        return None
    labels = [-100] * first + ids[first:]

    ime_head = build_prompt(row['keys'], row['context'], 'ime', 'keys_llm_v1') + RESULT_PREFIX
    ime_ids, (ime_zh,) = spans(tokenizer, ime_head + chinese, [(len(ime_head), len(ime_head) + len(chinese))])
    mt_head = zh_en_prompt(chinese, row['context'])
    mt_ids, (mt_en,) = spans(tokenizer, mt_head + english, [(len(mt_head), len(mt_head) + len(english))])
    mt_ids = mt_ids + [eos]
    zh_pairs = matched(ids, zh, ime_ids, ime_zh)
    en_pairs = matched(ids, en + [len(ids) - 1], mt_ids, mt_en + [len(mt_ids) - 1])
    return {'ids': ids, 'labels': labels, 'ime': ime_ids, 'mt': mt_ids, 'zh': zh_pairs, 'en': en_pairs}


def pad(rows, field, pad_id, device):
    width = max(len(r[field]) for r in rows)
    ids = torch.full((len(rows), width), pad_id, dtype=torch.long)
    mask = torch.zeros((len(rows), width), dtype=torch.long)
    for i, r in enumerate(rows):
        ids[i, :len(r[field])] = torch.tensor(r[field])
        mask[i, :len(r[field])] = 1
    return ids.to(device), mask.to(device)


def gather(logits, rows, pairs_field, side):
    """Logits that predict each matched token: the position before it, for the student (side 0) or teacher (1)."""
    index = [(i, p[side] - 1) for i, r in enumerate(rows) for p in r[pairs_field] if p[0] > 0 and p[1] > 0]
    if not index:
        return None
    rows_index, cols = zip(*index)
    return logits[list(rows_index), list(cols)].float()


def targets(rows, pairs_field, device):
    return torch.tensor([r['ids'][p[0]] for r in rows for p in r[pairs_field] if p[0] > 0 and p[1] > 0], device=device)


def kl(student, teacher):
    return torch.nn.functional.kl_div(torch.log_softmax(student, -1), torch.log_softmax(teacher, -1),
                                      log_target=True, reduction='sum')


def step_loss(model, rows, pad_id, device, mt_teacher, zh_weight, en_weight):
    ids, mask = pad(rows, 'ids', pad_id, device)
    labels = torch.full_like(ids, -100)
    for i, r in enumerate(rows):
        labels[i, :len(r['labels'])] = torch.tensor(r['labels'], device=device)
    logits = model(input_ids=ids, attention_mask=mask).logits
    shifted = logits[:, :-1].float()
    target = labels[:, 1:]
    loss = torch.nn.functional.cross_entropy(shifted.reshape(-1, shifted.size(-1)), target.reshape(-1),
                                             ignore_index=-100, reduction='sum')
    total = loss
    if zh_weight:
        with torch.no_grad(), model.disable_adapter():
            ime_ids, ime_mask = pad(rows, 'ime', pad_id, device)
            reference = model(input_ids=ime_ids, attention_mask=ime_mask).logits
        s, t = gather(logits, rows, 'zh', 0), gather(reference, rows, 'zh', 1)
        if s is not None:
            total = total + zh_weight * (kl(s, t) - torch.nn.functional.cross_entropy(s, targets(rows, 'zh', device), reduction='sum'))
    if en_weight and mt_teacher is not None:
        with torch.no_grad():
            mt_ids, mt_mask = pad(rows, 'mt', pad_id, device)
            reference = mt_teacher(input_ids=mt_ids, attention_mask=mt_mask).logits
        s, t = gather(logits, rows, 'en', 0), gather(reference, rows, 'en', 1)
        if s is not None:
            total = total + en_weight * (kl(s, t) - torch.nn.functional.cross_entropy(s, targets(rows, 'en', device), reduction='sum'))
    return total, loss, int((target != -100).sum())


@torch.no_grad()
def dev_loss(model, examples, pad_id, device, size):
    model.eval()
    total, tokens = 0.0, 0
    for i in range(0, len(examples), size):
        _, loss, n = step_loss(model, examples[i:i + size], pad_id, device, None, 0, 0)
        total, tokens = total + float(loss), tokens + n
    model.train()
    return total / max(tokens, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lora-rank', type=int, default=128)
    parser.add_argument('--mt-teacher')
    parser.add_argument('--zh-kd', type=float, default=.5, help='share of the Chinese loss taken by IME distillation')
    parser.add_argument('--en-kd', type=float, default=.5, help='share of the English loss taken by translator distillation')
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--micro-batch-size', type=int, default=32)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--warmup', type=float, default=.03)
    parser.add_argument('--max-length', type=int, default=256)
    parser.add_argument('--dev-limit', type=int, default=2000)
    parser.add_argument('--seed', type=int, default=20261019)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    train = [e for r in read(args.corpus / 'train.jsonl') if (e := encode(tokenizer, r, args.max_length))]
    dev = [e for r in read(args.corpus / 'dev.jsonl')[:args.dev_limit] if (e := encode(tokenizer, r, args.max_length))]
    dev.sort(key=lambda e: len(e['ids']))
    coverage = {f: sum(len(e[f]) for e in train) / max(1, sum(len([t for t in e['labels'] if t != -100]) for e in train))
                for f in ('zh', 'en')}

    from peft import LoraConfig, get_peft_model
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to(device)
    model = get_peft_model(model, LoraConfig(
        r=args.lora_rank, lora_alpha=2 * args.lora_rank, lora_dropout=0.0, task_type='CAUSAL_LM',
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']))
    model.print_trainable_parameters()
    model.config.use_cache = False
    mt_teacher = None
    if args.mt_teacher:
        mt_teacher = AutoModelForCausalLM.from_pretrained(args.mt_teacher, dtype=torch.bfloat16).to(device).eval()
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.learning_rate, betas=(.9, .95), weight_decay=0.0)
    total = math.ceil(len(train) / args.batch_size) * args.epochs
    warmup = max(1, int(total * args.warmup))

    def lr_at(step):
        if step < warmup:
            return args.learning_rate * (step + 1) / warmup
        return args.learning_rate * .5 * (1 + math.cos(math.pi * (step - warmup) / max(1, total - warmup)))

    args.output.mkdir(parents=True, exist_ok=True)
    log = (args.output / 'train-log.jsonl').open('a', encoding='utf-8')
    history, step, started = [], 0, time.time()
    print(json.dumps({'train': len(train), 'dev': len(dev), 'steps': total, 'kdCoverage': coverage}), flush=True)
    for epoch in range(1, args.epochs + 1):
        for batch in batches(train, args.batch_size, rng):
            for group in optimizer.param_groups:
                group['lr'] = lr_at(step)
            tokens = sum(sum(1 for t in e['labels'] if t != -100) for e in batch)
            running = 0.0
            for i in range(0, len(batch), args.micro_batch_size):
                loss, _, _ = step_loss(model, batch[i:i + args.micro_batch_size], pad_id, device, mt_teacher,
                                       args.zh_kd, args.en_kd)
                (loss / tokens).backward()
                running += float(loss)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % 20 == 0:
                log.write(json.dumps({'step': step, 'loss': running / tokens, 'lr': lr_at(step)}) + '\n')
                log.flush()
        loss = dev_loss(model, dev, pad_id, device, args.micro_batch_size)
        checkpoint = args.output / f'epoch-{epoch}'
        model.save_pretrained(checkpoint)
        tokenizer.save_pretrained(checkpoint)
        (checkpoint / 'beam-mt.json').write_text(json.dumps({'prompt_version': FRAGMENT_VERSION, 'base': args.model}) + '\n')
        history.append({'epoch': epoch, 'step': step, 'devLoss': loss, 'checkpoint': str(checkpoint)})
        print(json.dumps(history[-1]), flush=True)
    config = {'kind': 'beam-onestage-train', 'promptVersion': FRAGMENT_VERSION, 'model': args.model,
              'loraRank': args.lora_rank, 'mtTeacher': args.mt_teacher, 'zhKd': args.zh_kd, 'enKd': args.en_kd,
              'epochs': args.epochs, 'batchSize': args.batch_size, 'learningRate': args.learning_rate,
              'trainExamples': len(train), 'devExamples': len(dev), 'kdCoverage': coverage, 'seed': args.seed,
              'history': history, 'elapsedSeconds': round(time.time() - started, 1)}
    (args.output / 'run-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
