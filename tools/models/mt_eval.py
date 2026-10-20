#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Greedy English from translate-stage checkpoints for gold Chinese, in mt_score.py's format.

    python3 tools/models/mt_eval.py --rows mt-corpus/test.jsonl --output gen.json \
        --system lora=mt-lora/epoch-3 --system full=mt-full/epoch-3

--rows takes mt_corpus.py rows or an earlier gen.json, whose systems are kept. A LoRA checkpoint
(adapter_config.json) is loaded on the base recorded in its beam-mt.json.
"""
import argparse
import json
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from mt_format import prompt


def load_rows(path: Path) -> dict:
    if path.suffix == '.json':
        return json.loads(path.read_text(encoding='utf-8'))
    with path.open(encoding='utf-8') as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return {'rows': [{'id': str(i), 'variant': r.get('source') or 'all', 'context': r['context'], 'chinese': r['chinese'],
                      'reference': r['english']} for i, r in enumerate(rows)], 'systems': {}}


def load_model(path: Path, device):
    meta = json.loads((path / 'beam-mt.json').read_text(encoding='utf-8'))
    if (path / 'adapter_config.json').exists():
        from peft import PeftModel
        model = AutoModelForCausalLM.from_pretrained(meta['base'], dtype=torch.bfloat16)
        model = PeftModel.from_pretrained(model, path).merge_and_unload()
    else:
        model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16)
    return model.to(device).eval()


@torch.no_grad()
def generate(path: Path, rows: list[dict], device, batch: int, max_new: int) -> list[str]:
    tokenizer = AutoTokenizer.from_pretrained(path)
    tokenizer.padding_side = 'left'
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = load_model(path, device)
    newline = [i for t, i in tokenizer.get_vocab().items() if '\n' in tokenizer.convert_tokens_to_string([t])]
    out = []
    for start in range(0, len(rows), batch):
        prompts = [prompt(r['chinese'], r['context']) for r in rows[start:start + batch]]
        encoded = tokenizer(prompts, return_tensors='pt', padding=True, add_special_tokens=False).to(device)
        generated = model.generate(**encoded, max_new_tokens=max_new, do_sample=False,
                                   eos_token_id=[tokenizer.eos_token_id, *newline], pad_token_id=tokenizer.pad_token_id)
        for row in generated[:, encoded['input_ids'].shape[1]:]:
            out.append(tokenizer.decode(row, skip_special_tokens=True).split('\n')[0].strip())
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rows', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--system', action='append', required=True, help='name=checkpoint')
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--max-new', type=int, default=48)
    args = parser.parse_args()
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    data = load_rows(args.rows)
    data.setdefault('eval_seconds', {})
    for spec in args.system:
        name, path = spec.split('=', 1)
        started = time.time()
        data['systems'][name] = generate(Path(path), data['rows'], device, args.batch_size, args.max_new)
        data['eval_seconds'][name] = round(time.time() - started, 1)
        print(f'{name}: {len(data["rows"])} rows in {data["eval_seconds"][name]} s', flush=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
