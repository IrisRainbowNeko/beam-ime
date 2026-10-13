#!/usr/bin/env python3
"""Full-parameter SFT for the Beam v8 keys-conditioned LLM experiment.

A deliberately small single-GPU loop: fp32 master weights with bf16 autocast,
loss only on target tokens, length-bucketed batches, cosine schedule, and
bf16 checkpoints at each epoch end.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path
from typing import Any

import torch

from keys_llm_format import CHINESE_FIRST_VERSION, DUAL_PROMPT_VERSIONS, PROMPT_VERSION, build_prompt, build_target, MODES

IME, ENGLISH, COMMENT = 1, 2, 3
FIELD_WEIGHTS = {ENGLISH: .3, COMMENT: .1}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_rows(path: Path, limit: int | None) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
                if limit is not None and len(rows) >= limit:
                    break
    return rows


def encode(tokenizer: Any, row: dict[str, Any], use_segments: bool, max_length: int,
           version: str = PROMPT_VERSION) -> dict | None:
    mode = row.get("mode", "ime")
    prefix = build_prompt(row["keys"], row.get("context", ""), mode, version)
    target = build_target(row["target"], row["segments"] if use_segments else None, mode, row.get("translation", ""), version)
    if not use_segments:
        prefix += "结果："
        target = target[len("结果："):]
    prompt_ids = tokenizer(prefix, add_special_tokens=False)["input_ids"]
    if mode == 'translate_en':
        encoded = tokenizer(target, add_special_tokens=False, return_offsets_mapping=True)
        start = len(row['target']) + 1 if version == CHINESE_FIRST_VERSION else 0
        end = start + len(row['translation'])
        fields = [ENGLISH if hi > start and lo < end else COMMENT for lo, hi in encoded['offset_mapping']]
        # Give EOS the same formatting weight in both output orders.
        fields.append(COMMENT)
        target_ids = encoded['input_ids'] + [tokenizer.eos_token_id]
    else:
        target_ids = tokenizer(target, add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
        fields = [IME] * len(target_ids)
    if len(prompt_ids) + len(target_ids) > max_length:
        return None
    return {"input_ids": prompt_ids + target_ids, "labels": [-100] * len(prompt_ids) + target_ids,
            "fields": [0] * len(prompt_ids) + fields, "mode": mode}


def batches(examples: list[dict], batch_size: int, rng: random.Random) -> list[list[dict]]:
    order = list(range(len(examples)))
    rng.shuffle(order)
    result = []
    chunk = batch_size * 64
    for start in range(0, len(order), chunk):
        group = sorted(order[start:start + chunk], key=lambda index: len(examples[index]["input_ids"]))
        result.extend([examples[i] for i in group[offset:offset + batch_size]]
                      for offset in range(0, len(group), batch_size))
    rng.shuffle(result)
    return result


def task_batches(examples: list[dict], batch_size: int, rng: random.Random) -> list[list[dict]]:
    if batch_size < 4 or batch_size % 4:
        raise ValueError("dual-task batch size must be a positive multiple of four")
    pools = {mode: [row for row in examples if row['mode'] == mode] for mode in MODES}
    if any(not pool for pool in pools.values()):
        raise ValueError("joint training requires both ime and translate_en examples")
    widths = {'ime': 3 * batch_size // 4, 'translate_en': batch_size // 4}
    count = max(math.ceil(len(pool) / widths[mode]) for mode, pool in pools.items())
    result = [[] for _ in range(count)]
    for mode, pool in pools.items():
        order = list(pool)
        rng.shuffle(order)
        cursor = 0
        for batch in result:
            for _ in range(widths[mode]):
                if cursor == len(order):
                    rng.shuffle(order)
                    cursor = 0
                batch.append(order[cursor]); cursor += 1
    return result


def validation_examples(examples: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    selected = []
    for mode in MODES:
        pool = [row for row in examples if row['mode'] == mode]
        selected.extend(rng.sample(pool, min(2000, len(pool))))
    return selected


def target_logits(model, batch):
    logits = model(input_ids=batch['input_ids'], attention_mask=batch['attention_mask']).logits[:, :-1]
    labels = batch['labels'][:, 1:]
    valid = labels != -100
    return logits[valid].float(), labels[valid]


def task_loss(logits, labels, total_tokens, weight, teacher_logits=None, teacher_weight=0.25):
    loss = torch.nn.functional.cross_entropy(logits, labels, reduction='sum')
    if teacher_logits is not None:
        loss += teacher_weight * torch.nn.functional.kl_div(
            logits.log_softmax(-1), teacher_logits.log_softmax(-1), log_target=True, reduction='sum')
    return loss * weight / total_tokens


def field_loss(logits, labels, fields, totals):
    losses = torch.nn.functional.cross_entropy(logits, labels, reduction='none')
    return sum(losses[fields == field].sum() * weight / totals[field]
               for field, weight in FIELD_WEIGHTS.items())


def target_fields(batch):
    return batch['fields'][:, 1:][batch['labels'][:, 1:] != -100]


def collate(batch: list[dict], pad_id: int, device: torch.device) -> dict[str, torch.Tensor]:
    width = max(len(item["input_ids"]) for item in batch)
    ids = torch.full((len(batch), width), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), width), -100, dtype=torch.long)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    fields = torch.zeros((len(batch), width), dtype=torch.long)
    for row, item in enumerate(batch):
        size = len(item["input_ids"])
        ids[row, :size] = torch.tensor(item["input_ids"])
        labels[row, :size] = torch.tensor(item["labels"])
        mask[row, :size] = 1
        fields[row, :size] = torch.tensor(item['fields'])
    return {"input_ids": ids.to(device), "labels": labels.to(device), "attention_mask": mask.to(device),
            "fields": fields.to(device)}


@torch.no_grad()
def dev_loss(model: Any, examples: list[dict], pad_id: int, device: torch.device, batch_size: int) -> dict[str, float]:
    model.eval()
    metrics = {}
    for mode in MODES:
        rows = [row for row in examples if row['mode'] == mode]
        total, count = 0.0, 0
        field_sums = {field: 0.0 for field in FIELD_WEIGHTS}
        field_counts = {field: 0 for field in FIELD_WEIGHTS}
        for start in range(0, len(rows), batch_size):
            batch = collate(rows[start:start + batch_size], pad_id, device)
            with torch.autocast(device.type, dtype=torch.bfloat16):
                logits, labels = target_logits(model, batch)
                losses = torch.nn.functional.cross_entropy(logits, labels, reduction='none')
            total += losses.sum().item(); count += labels.numel()
            if mode == 'translate_en':
                fields = target_fields(batch)
                for field in FIELD_WEIGHTS:
                    selected = fields == field
                    field_sums[field] += losses[selected].sum().item()
                    field_counts[field] += selected.sum().item()
        if count:
            metrics[mode] = total / count
        if mode == 'translate_en' and count:
            for field, name in ((ENGLISH, 'english'), (COMMENT, 'comment')):
                metrics['translate_en_' + name] = field_sums[field] / field_counts[field]
    model.train()
    return metrics


def save(model: Any, tokenizer: Any, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    state = {name: value.detach().to(torch.bfloat16) for name, value in model.state_dict().items()}
    model.save_pretrained(path, state_dict=state)
    tokenizer.save_pretrained(path)


def train(args: argparse.Namespace) -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = torch.device(args.device)
    if args.gpu_memory_fraction:
        # Shared GPUs: cap the caching allocator so neighbours keep their headroom.
        torch.cuda.set_per_process_memory_fraction(args.gpu_memory_fraction, torch.cuda.current_device())
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.eos_token_id is None:
        raise RuntimeError("tokenizer has no eos token")
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

    def prepare(path: Path, limit: int | None) -> tuple[list[dict], int]:
        encoded = []
        for row in read_rows(path, limit):
            item = encode(tokenizer, row, args.segments, args.max_length, args.prompt_version)
            if item is not None and item['mode'] == 'ime' and args.teacher_weight:
                item['teacher'] = encode(tokenizer, row, args.segments, args.max_length, teacher_version)
                if item['teacher'] is None:
                    item = None
            encoded.append(item)
        kept = [item for item in encoded if item is not None]
        return kept, len(encoded) - len(kept)

    teacher = None
    teacher_version = 'keys_llm_v1'
    if args.teacher_weight:
        teacher_path = args.teacher or args.model
        teacher_tokenizer = AutoTokenizer.from_pretrained(teacher_path)
        if teacher_tokenizer.get_vocab() != tokenizer.get_vocab():
            raise ValueError('teacher and student must use the same tokenizer')
        teacher = AutoModelForCausalLM.from_pretrained(teacher_path, torch_dtype=torch.bfloat16, attn_implementation='sdpa').to(device).eval()
        teacher.requires_grad_(False)
        teacher.config.use_cache = False
        teacher_version = getattr(teacher.config, 'beam_prompt_version', 'keys_llm_v1')
    train_examples, train_dropped = [], 0
    for path in args.train:
        kept, dropped = prepare(Path(path), args.limit)
        train_examples.extend(kept)
        train_dropped += dropped
    dev_examples, _ = prepare(Path(args.dev), None)
    dev_examples = validation_examples(dev_examples, args.seed + 1)

    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32, attn_implementation="sdpa")
    model.to(device)
    model.config.use_cache = False
    model.config.beam_prompt_version = args.prompt_version
    dual_task = args.prompt_version in DUAL_PROMPT_VERSIONS
    model.config.beam_modes = list(MODES) if dual_task else ['ime']
    model.train()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay, betas=(0.9, 0.95), fused=device.type == 'cuda',
    )
    make_batches = task_batches if dual_task else batches
    steps_per_epoch = len(make_batches(train_examples, args.batch_size, random.Random(args.seed)))
    total_steps = steps_per_epoch * args.epochs
    warmup = max(1, int(total_steps * args.warmup_ratio))

    def lr_at(step: int) -> float:
        if step < warmup:
            return args.learning_rate * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return args.learning_rate * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * progress)))

    config = {
        "kind": "beam-v8-keys-llm-train",
        "promptVersion": args.prompt_version,
        "teacherWeight": args.teacher_weight,
        "teacherPromptVersion": teacher_version,
        "lossScheme": args.loss_scheme,
        "taskWeights": ({'ime': .6, 'english': .3, 'comment': .1} if args.loss_scheme == 'field'
                        else {'ime': .75, 'translate_en': .25}) if dual_task else {'ime': 1},
        "translationOrder": 'chinese_first' if args.prompt_version == CHINESE_FIRST_VERSION else 'english_first',
        "teacher": args.teacher or args.model,
        "model": args.model,
        "segments": args.segments,
        "train": {path: sha256(Path(path)) for path in args.train},
        "dev": {args.dev: sha256(Path(args.dev))},
        "trainExamples": len(train_examples),
        "trainDroppedOverLength": train_dropped,
        "devExamples": len(dev_examples),
        "devByTask": {mode: sum(row['mode'] == mode for row in dev_examples) for mode in MODES},
        "epochs": args.epochs,
        "batchSize": args.batch_size,
        "microBatchSize": args.micro_batch_size,
        "learningRate": args.learning_rate,
        "warmupSteps": warmup,
        "totalSteps": total_steps,
        "maxLength": args.max_length,
        "seed": args.seed,
        "torch": torch.__version__,
    }
    (out / "run-config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    log = (out / "train-log.jsonl").open("a", encoding="utf-8")
    step, started = 0, time.time()
    history = []
    for epoch in range(args.epochs):
        running, running_steps = 0.0, 0
        for batch_items in make_batches(train_examples, args.batch_size, rng):
            for group in optimizer.param_groups:
                group["lr"] = lr_at(step)
            loss_value = 0.0
            # Normalize each task independently; bilingual output length must not set the task mix.
            for mode in MODES:
                items = [row for row in batch_items if row['mode'] == mode]
                target_tokens = sum(sum(label != -100 for label in item['labels'][1:]) for item in items)
                weight = (.75 if mode == 'ime' else .25) if dual_task else 1.0
                if args.loss_scheme == 'field' and dual_task:
                    weight = .6 if mode == 'ime' else .4
                totals = {field: sum(item['fields'].count(field) for item in items) for field in FIELD_WEIGHTS}
                for offset in range(0, len(items), args.micro_batch_size):
                    micro = items[offset:offset + args.micro_batch_size]
                    batch = collate(micro, pad_id, device)
                    teacher_logits = None
                    if teacher is not None and mode == 'ime':
                        with torch.no_grad(), torch.autocast(device.type, dtype=torch.bfloat16):
                            teacher_logits, _ = target_logits(teacher, collate([row['teacher'] for row in micro], pad_id, device))
                    with torch.autocast(device.type, dtype=torch.bfloat16):
                        logits, labels = target_logits(model, batch)
                        if mode == 'translate_en' and args.loss_scheme == 'field':
                            loss = field_loss(logits, labels, target_fields(batch), totals)
                        else:
                            loss = task_loss(logits, labels, target_tokens, weight, teacher_logits, args.teacher_weight)
                    if not torch.isfinite(loss):
                        raise RuntimeError('non-finite training loss')
                    loss.backward()
                    loss_value += loss.item()
                    del logits, loss, teacher_logits
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            running += loss_value
            running_steps += 1
            if args.save_every and step % args.save_every == 0:
                save(model, tokenizer, out / f"step-{step}")
            if step % args.log_every == 0:
                record = {"step": step, "epoch": epoch, "loss": running / running_steps,
                          "lr": lr_at(step), "elapsed": round(time.time() - started, 1)}
                if step % (args.log_every * 10) == 0:
                    record["devLoss"] = dev_loss(model, dev_examples, pad_id, device, args.micro_batch_size)
                log.write(json.dumps(record) + "\n")
                log.flush()
                print(json.dumps(record), flush=True)
                running, running_steps = 0.0, 0
        dev = dev_loss(model, dev_examples, pad_id, device, args.micro_batch_size)
        checkpoint = out / f"epoch-{epoch + 1}"
        save(model, tokenizer, checkpoint)
        history.append({"epoch": epoch + 1, "step": step, "devLoss": dev, "checkpoint": str(checkpoint)})
        print(json.dumps(history[-1]), flush=True)
    config["history"] = history
    log.close()
    config["elapsedSeconds"] = round(time.time() - started, 1)
    (out / "run-config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--train", required=True, nargs="+")
    parser.add_argument("--dev", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--segments", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--prompt-version", choices=('keys_llm_v1', *DUAL_PROMPT_VERSIONS), default=PROMPT_VERSION)
    parser.add_argument('--loss-scheme', choices=('task', 'field'), default='task',
                        help='field: separately normalize IME/English/comment CE with weights .6/.3/.1')
    parser.add_argument("--teacher", help="old checkpoint for ordinary-task teacher KL; defaults to --model")
    parser.add_argument("--teacher-weight", type=float, default=0.25)
    parser.add_argument("--device", choices=('cpu', 'cuda'), default='cuda')
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--micro-batch-size", type=int, default=4)
    parser.add_argument("--save-every", type=int, default=0, help="also save bf16 weights every N steps")
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--log-every", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--gpu-memory-fraction", type=float, help="cap this process's share of GPU memory")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
