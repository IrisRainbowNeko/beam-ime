#!/usr/bin/env python3
"""Evaluate a Beam v8 keys-conditioned checkpoint with batched beam search.

Candidates are deduplicated by Hanzi result in beam order. Three filters are
reported: ``raw`` (any parsed result), ``structural`` (segments consume the
keys and match the result length), and ``strict`` (each segment also prefixes
a pypinyin reading of its character; not meaningful for noisy input).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch

from keys_llm_format import PROMPT_VERSION, build_prompt, parse_output, strict_valid, structural_valid

FILTERS = {
    "raw": lambda keys, parsed: parsed["result"] is not None,
    "structural": structural_valid,
    "strict": strict_valid,
}
TOP_K = (1, 3, 5)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ranked_results(keys: str, texts: list[str], name: str) -> list[str]:
    seen, ranked = set(), []
    for text in texts:
        parsed = parse_output(text)
        result = parsed["result"]
        if FILTERS[name](keys, parsed) and result not in seen:
            seen.add(result)
            ranked.append(result)
    return ranked


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        groups["all"].append(case)
        groups[case["variant"]].append(case)
        seen = "seen" if case.get("seenTarget") else "unseen"
        groups[f"{case['variant']}:{seen}"].append(case)
        if any(char.isascii() and char.isalpha() for char in case["target"]):
            groups["withEnglish"].append(case)
            groups[f"{case['variant']}:withEnglish"].append(case)
        if case.get("hasContext"):
            groups["withContext"].append(case)
            groups[f"{case['variant']}:withContext"].append(case)
    metrics = {}
    for name, members in sorted(groups.items()):
        entry = {"rows": len(members)}
        for filter_name in FILTERS:
            ranks = [case["ranks"][filter_name] for case in members]
            for k in TOP_K:
                entry[f"{filter_name}P@{k}"] = sum(rank is not None and rank <= k for rank in ranks) / len(members)
        metrics[name] = entry
    return metrics


@torch.no_grad()
def evaluate(args: argparse.Namespace) -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = [json.loads(line) for line in Path(args.input).open(encoding="utf-8") if line.strip()]
    if args.variants:
        rows = [row for row in rows if row["variant"] in args.variants]
    if args.drop_context:
        rows = [{**row, "context": ""} for row in rows]
    if args.seen_from:
        # Recompute seen/unseen against every training file actually used.
        seen = set()
        for path in args.seen_from:
            with Path(path).open(encoding="utf-8") as handle:
                seen.update(json.loads(line)["target"] for line in handle if line.strip())
        for row in rows:
            row["seenTarget"] = row["target"] in seen
    if args.limit_per_variant:
        counts: dict[str, int] = defaultdict(int)
        kept = []
        for row in rows:
            if counts[row["variant"]] < args.limit_per_variant:
                counts[row["variant"]] += 1
                kept.append(row)
        rows = kept
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint, padding_side="left")
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    model = AutoModelForCausalLM.from_pretrained(args.checkpoint, dtype=torch.bfloat16, attn_implementation="sdpa")
    model.to("cuda").eval()

    order = sorted(range(len(rows)), key=lambda index: len(rows[index]["keys"]))
    cases: list[dict | None] = [None] * len(rows)
    started = time.time()
    for start in range(0, len(order), args.batch_size):
        indices = order[start:start + args.batch_size]
        prompts = [build_prompt(rows[i]["keys"], rows[i].get("context", "")) for i in indices]
        encoded = tokenizer(prompts, add_special_tokens=False, padding=True, return_tensors="pt").to("cuda")
        longest = max(len(rows[i]["keys"]) for i in indices)
        output = model.generate(
            **encoded,
            num_beams=args.beams,
            num_return_sequences=args.beams,
            do_sample=False,
            max_new_tokens=2 * longest + 16,
            pad_token_id=pad_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        generated = output[:, encoded["input_ids"].shape[1]:]
        texts = tokenizer.batch_decode(generated, skip_special_tokens=True)
        for offset, index in enumerate(indices):
            row = rows[index]
            beams = texts[offset * args.beams:(offset + 1) * args.beams]
            ranks, top = {}, {}
            for name in FILTERS:
                ranked = ranked_results(row["keys"], beams, name)
                top[name] = ranked[:5]
                ranks[name] = ranked.index(row["target"]) + 1 if row["target"] in ranked else None
            cases[index] = {
                "id": row["id"], "variant": row["variant"], "keys": row["keys"], "target": row["target"],
                "hasContext": bool(row.get("context")),
                "seenTarget": row.get("seenTarget"), "ranks": ranks, "top": top,
                "beam0": beams[0],
            }
        print(json.dumps({"done": min(start + args.batch_size, len(order)), "rows": len(order),
                          "elapsed": round(time.time() - started, 1)}), flush=True)
    finished = [case for case in cases if case is not None]
    report = {
        "kind": "beam-v8-keys-llm-evaluation",
        "promptVersion": PROMPT_VERSION,
        "checkpoint": args.checkpoint,
        "input": args.input,
        "inputSha256": sha256(Path(args.input)),
        "beams": args.beams,
        "dropContext": args.drop_context,
        "rows": len(finished),
        "elapsedSeconds": round(time.time() - started, 1),
        "metrics": summarize(finished),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with out.with_suffix(".cases.jsonl").open("w", encoding="utf-8") as handle:
        for case in finished:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(json.dumps(report["metrics"]["all"], indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--beams", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--variants", nargs="*")
    parser.add_argument("--limit-per-variant", type=int)
    parser.add_argument("--drop-context", action="store_true", help="evaluate the same rows without context")
    parser.add_argument("--seen-from", nargs="*", help="training JSONL files that define seen targets")
    evaluate(parser.parse_args())


if __name__ == "__main__":
    main()
