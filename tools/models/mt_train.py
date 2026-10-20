#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Train the Chinese -> English translate stage on mt_corpus.py output.

LoRA on the frozen IME checkpoint (runs as an adapter on the IME model's weights):
    python3 tools/models/mt_train.py --model runs/r5-v2ex/epoch-1 --lora-rank 64 --corpus mt-corpus --output mt-lora
All weights of a separate model:
    python3 tools/models/mt_train.py --model models/Qwen3-0.6B-Base --learning-rate 3e-5 --corpus mt-corpus --output mt-full

The loss covers the English and the end token only.
"""
import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from mt_format import PROMPT_VERSION, prompt


def read(path: Path) -> list[dict]:
    with path.open(encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def encode(tokenizer, row: dict, max_length: int) -> dict | None:
    head = tokenizer(prompt(row['chinese'], row['context']), add_special_tokens=False)['input_ids']
    tail = tokenizer(row['english'], add_special_tokens=False)['input_ids'] + [tokenizer.eos_token_id]
    if len(head) + len(tail) > max_length:
        return None
    return {'ids': head + tail, 'labels': [-100] * len(head) + tail}


def batches(examples: list[dict], size: int, rng: random.Random) -> list[list[dict]]:
    # Similar lengths share a batch; batch order is random.
    order = sorted(examples, key=lambda e: len(e['ids']) + rng.random())
    out = [order[i:i + size] for i in range(0, len(order), size)]
    rng.shuffle(out)
    return out


def collate(batch: list[dict], pad_id: int, device) -> dict:
    width = max(len(e['ids']) for e in batch)
    ids = torch.full((len(batch), width), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), width), -100, dtype=torch.long)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    for i, e in enumerate(batch):
        n = len(e['ids'])
        ids[i, :n] = torch.tensor(e['ids'])
        labels[i, :n] = torch.tensor(e['labels'])
        mask[i, :n] = 1
    return {'input_ids': ids.to(device), 'labels': labels.to(device), 'attention_mask': mask.to(device)}


def token_loss(model, batch, teacher=None, kd_weight=0.0) -> tuple[torch.Tensor, int]:
    logits = model(input_ids=batch['input_ids'], attention_mask=batch['attention_mask']).logits[:, :-1].float()
    labels = batch['labels'][:, 1:]
    loss = torch.nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)), labels.reshape(-1),
                                             ignore_index=-100, reduction='sum')
    if teacher is not None:
        # Token-level distillation on the English positions: KL(teacher || student).
        with torch.no_grad():
            reference = teacher(input_ids=batch['input_ids'], attention_mask=batch['attention_mask']).logits[:, :-1].float()
        keep = labels != -100
        target = torch.log_softmax(reference[keep], -1)
        kl = torch.nn.functional.kl_div(torch.log_softmax(logits[keep], -1), target, log_target=True, reduction='sum')
        loss = (1 - kd_weight) * loss + kd_weight * kl
    return loss, int((labels != -100).sum())


@torch.no_grad()
def dev_loss(model, examples, pad_id, device, size) -> float:
    model.eval()
    total, tokens = 0.0, 0
    for i in range(0, len(examples), size):
        loss, n = token_loss(model, collate(examples[i:i + size], pad_id, device))
        total, tokens = total + float(loss), tokens + n
    model.train()
    return total / max(tokens, 1)


def save(model, tokenizer, path: Path, base: str | None):
    model.save_pretrained(path)
    tokenizer.save_pretrained(path)
    meta = {'prompt_version': PROMPT_VERSION}
    if base:
        meta['base'] = base
    (path / 'beam-mt.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lora-rank', type=int, default=0)
    parser.add_argument('--kd-teacher', help='a translate checkpoint with the same tokenizer to distil from')
    parser.add_argument('--kd-weight', type=float, default=.5)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--micro-batch-size', type=int, default=32)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--warmup', type=float, default=.03)
    parser.add_argument('--max-length', type=int, default=192)
    parser.add_argument('--dev-limit', type=int, default=2000)
    parser.add_argument('--seed', type=int, default=20261015)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    train = [e for r in read(args.corpus / 'train.jsonl') if (e := encode(tokenizer, r, args.max_length))]
    dev = [e for r in read(args.corpus / 'dev.jsonl')[:args.dev_limit] if (e := encode(tokenizer, r, args.max_length))]
    dev.sort(key=lambda e: len(e['ids']))

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to(device)
    if args.lora_rank:
        from peft import LoraConfig, get_peft_model
        model = get_peft_model(model, LoraConfig(
            r=args.lora_rank, lora_alpha=2 * args.lora_rank, lora_dropout=0.0, task_type='CAUSAL_LM',
            target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']))
        model.print_trainable_parameters()
    model.config.use_cache = False
    teacher = None
    if args.kd_teacher:
        teacher = AutoModelForCausalLM.from_pretrained(args.kd_teacher, dtype=torch.bfloat16).to(device).eval()
    if not args.lora_rank:
        model.gradient_checkpointing_enable()
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.learning_rate, betas=(.9, .95), weight_decay=0.0 if args.lora_rank else .01)

    steps_per_epoch = math.ceil(len(train) / args.batch_size)
    total = steps_per_epoch * args.epochs
    warmup = max(1, int(total * args.warmup))

    def lr_at(step):
        if step < warmup:
            return args.learning_rate * (step + 1) / warmup
        return args.learning_rate * .5 * (1 + math.cos(math.pi * (step - warmup) / max(1, total - warmup)))

    args.output.mkdir(parents=True, exist_ok=True)
    log = (args.output / 'train-log.jsonl').open('a', encoding='utf-8')
    history, step, started = [], 0, time.time()
    print(json.dumps({'train': len(train), 'dev': len(dev), 'steps': total}), flush=True)
    for epoch in range(1, args.epochs + 1):
        for batch in batches(train, args.batch_size, rng):
            for group in optimizer.param_groups:
                group['lr'] = lr_at(step)
            tokens = sum(sum(1 for t in e['labels'] if t != -100) for e in batch)
            running = 0.0
            for i in range(0, len(batch), args.micro_batch_size):
                loss, _ = token_loss(model, collate(batch[i:i + args.micro_batch_size], pad_id, device), teacher, args.kd_weight)
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
        save(model, tokenizer, checkpoint, args.model if args.lora_rank else None)
        history.append({'epoch': epoch, 'step': step, 'devLoss': loss, 'checkpoint': str(checkpoint)})
        print(json.dumps(history[-1]), flush=True)
    config = {'kind': 'beam-mt-train', 'promptVersion': PROMPT_VERSION, 'model': args.model, 'loraRank': args.lora_rank,
              'kdTeacher': args.kd_teacher, 'kdWeight': args.kd_weight if args.kd_teacher else 0.0,
              'epochs': args.epochs, 'batchSize': args.batch_size, 'learningRate': args.learning_rate,
              'trainExamples': len(train), 'devExamples': len(dev), 'seed': args.seed, 'torch': torch.__version__,
              'history': history, 'elapsedSeconds': round(time.time() - started, 1)}
    (args.output / 'run-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
