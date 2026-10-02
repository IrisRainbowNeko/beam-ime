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

from keys_llm_format import PROMPT_VERSION, build_prompt, build_target


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


def encode(tokenizer: Any, row: dict[str, Any], use_segments: bool, max_length: int) -> dict | None:
    prompt_ids = tokenizer(build_prompt(row["keys"], row.get("context", "")), add_special_tokens=False)["input_ids"]
    target = build_target(row["target"], row["segments"] if use_segments else None)
    target_ids = tokenizer(target, add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
    if len(prompt_ids) + len(target_ids) > max_length:
        return None
    return {"input_ids": prompt_ids + target_ids, "labels": [-100] * len(prompt_ids) + target_ids}


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


def collate(batch: list[dict], pad_id: int, device: torch.device) -> dict[str, torch.Tensor]:
    width = max(len(item["input_ids"]) for item in batch)
    ids = torch.full((len(batch), width), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), width), -100, dtype=torch.long)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    for row, item in enumerate(batch):
        size = len(item["input_ids"])
        ids[row, :size] = torch.tensor(item["input_ids"])
        labels[row, :size] = torch.tensor(item["labels"])
        mask[row, :size] = 1
    return {"input_ids": ids.to(device), "labels": labels.to(device), "attention_mask": mask.to(device)}


@torch.no_grad()
def dev_loss(model: Any, examples: list[dict], pad_id: int, device: torch.device, batch_size: int) -> float:
    model.eval()
    total, count = 0.0, 0
    for start in range(0, len(examples), batch_size):
        batch = collate(examples[start:start + batch_size], pad_id, device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits
        shift_logits = logits[:, :-1].float()
        shift_labels = batch["labels"][:, 1:]
        loss = torch.nn.functional.cross_entropy(
            shift_logits.reshape(-1, shift_logits.size(-1)), shift_labels.reshape(-1),
            ignore_index=-100, reduction="sum",
        )
        total += loss.item()
        count += int((shift_labels != -100).sum())
    model.train()
    return total / max(count, 1)


def save(model: Any, tokenizer: Any, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    state = {name: value.detach().to(torch.bfloat16) for name, value in model.state_dict().items()}
    model.save_pretrained(path, state_dict=state)
    tokenizer.save_pretrained(path)


def train(args: argparse.Namespace) -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = torch.device("cuda")
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
        encoded = [encode(tokenizer, row, args.segments, args.max_length) for row in read_rows(path, limit)]
        kept = [item for item in encoded if item is not None]
        return kept, len(encoded) - len(kept)

    train_examples, train_dropped = [], 0
    for path in args.train:
        kept, dropped = prepare(Path(path), args.limit)
        train_examples.extend(kept)
        train_dropped += dropped
    dev_examples, _ = prepare(Path(args.dev), 4000)

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32, attn_implementation="sdpa")
    model.to(device)
    model.config.use_cache = False
    model.train()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay, betas=(0.9, 0.95), fused=True,
    )
    steps_per_epoch = math.ceil(len(train_examples) / args.batch_size)
    total_steps = steps_per_epoch * args.epochs
    warmup = max(1, int(total_steps * args.warmup_ratio))

    def lr_at(step: int) -> float:
        if step < warmup:
            return args.learning_rate * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return args.learning_rate * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * progress)))

    config = {
        "kind": "beam-v8-keys-llm-train",
        "promptVersion": PROMPT_VERSION,
        "model": args.model,
        "segments": args.segments,
        "train": {path: sha256(Path(path)) for path in args.train},
        "dev": {args.dev: sha256(Path(args.dev))},
        "trainExamples": len(train_examples),
        "trainDroppedOverLength": train_dropped,
        "devExamples": len(dev_examples),
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
        for batch_items in batches(train_examples, args.batch_size, rng):
            for group in optimizer.param_groups:
                group["lr"] = lr_at(step)
            # Weight micro-batches by target tokens so accumulation matches one large batch.
            target_tokens = sum(sum(label != -100 for label in item["labels"]) for item in batch_items)
            loss_value = 0.0
            for offset in range(0, len(batch_items), args.micro_batch_size):
                micro = batch_items[offset:offset + args.micro_batch_size]
                batch = collate(micro, pad_id, device)
                weight = sum(sum(label != -100 for label in item["labels"]) for item in micro) / target_tokens
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    loss = model(**batch).loss * weight
                loss.backward()
                loss_value += loss.item()
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
                    record["devLoss"] = dev_loss(model, dev_examples, pad_id, device, 128)
                log.write(json.dumps(record) + "\n")
                log.flush()
                print(json.dumps(record), flush=True)
                running, running_steps = 0.0, 0
        dev = dev_loss(model, dev_examples, pad_id, device, 128)
        checkpoint = out / f"epoch-{epoch + 1}"
        save(model, tokenizer, checkpoint)
        history.append({"epoch": epoch + 1, "step": step, "devLoss": dev, "checkpoint": str(checkpoint)})
        print(json.dumps(history[-1]), flush=True)
    config["history"] = history
    config["elapsedSeconds"] = round(time.time() - started, 1)
    (out / "run-config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--train", required=True, nargs="+")
    parser.add_argument("--dev", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--segments", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--micro-batch-size", type=int, default=128)
    parser.add_argument("--save-every", type=int, default=0, help="also save bf16 weights every N steps")
    parser.add_argument("--learning-rate", type=float, default=3e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--log-every", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--gpu-memory-fraction", type=float, help="cap this process's share of GPU memory")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
