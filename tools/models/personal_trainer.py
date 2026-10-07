#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run one local Beam personalization job; exit 75 after a cooperative pause."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

from keys_llm_format import build_prompt, letters, normalize_keys, readings, units

RECIPE = 'beam-personal-r8-v1'
TARGETS = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def phonetic_segments(target, keys):
    pieces = units(target)
    raw = letters(normalize_keys(keys))
    if not pieces:
        return None
    states = {0: []}
    for piece in pieces:
        next_states = {}
        options = [piece.lower()] if piece.isascii() else sorted(readings(piece))
        for start, segments in states.items():
            for reading in options:
                widths = [len(reading)] if piece.isascii() else range(1, len(reading) + 1)
                for width in widths:
                    if raw.startswith(reading[:width], start):
                        next_states[start + width] = segments + [(reading, piece.isascii())]
        states = next_states
    return states.get(len(raw))


def variants(sample):
    original = normalize_keys(sample['keys'])
    result = [{**sample, 'keys': original}]
    segments = phonetic_segments(sample['target'], original)
    if segments:
        full = ''.join(text for text, _ in segments)
        initials = ''.join(text if english else text[0] for text, english in segments)
        for keys in (full, initials):
            if len(keys) <= 40 and keys not in {r['keys'] for r in result}:
                result.append({**sample, 'keys': keys})
    return result


def encode(tokenizer, sample):
    prompt = build_prompt(sample['keys'], sample.get('context', '')[-64:]) + '结果：'
    prefix = tokenizer(prompt, add_special_tokens=False)['input_ids']
    suffix = tokenizer(sample['target'], add_special_tokens=False)['input_ids'] + [tokenizer.eos_token_id]
    return {'input_ids': prefix + suffix, 'labels': [-100] * len(prefix) + suffix}


def select_step(recent, history, references, seed, step):
    rng = random.Random(seed + step)
    chosen = rng.choices(recent, k=8) + rng.choices(history or recent, k=4)
    # Sample events first, so a commit with many segments gets no extra weight.
    personal = [rng.choice(rng.choice(event['samples'])['variants']) for event in chosen]
    return personal + rng.choices(references, k=4)


def run_job(job_path):
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    job_path = Path(job_path)
    job = json.loads(job_path.read_text(encoding='utf-8'))
    root = Path(job['directory'])
    component = job['component']
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    if os.name == 'posix':
        os.nice(10)
    torch.set_num_threads(4)
    device = component['backend']
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('The CUDA learning component requires a working CUDA device.')
    seed = int(hashlib.sha256(job['id'].encode()).hexdigest()[:8], 16)
    torch.manual_seed(seed)
    checkpoint = Path(component['checkpoint'])
    actual_tokenizer = hashlib.sha256((checkpoint / 'tokenizer.json').read_bytes()).hexdigest()
    if actual_tokenizer != component['tokenizer_sha256']:
        raise ValueError('Training tokenizer does not match the component manifest.')

    def paused():
        return (root / 'pause.json').exists()

    def progress(phase, step=0, microbatch=0):
        # Appending avoids Windows rename conflicts with concurrent progress readers.
        with (root / 'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'state': 'running', 'phase': phase, 'step': step,
                                     'microbatch': microbatch, 'total': 24, 'backend': device}) + '\n')

    if paused():
        return 75
    progress('loading')
    tokenizer = AutoTokenizer.from_pretrained(checkpoint, extra_special_tokens={}, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(checkpoint, torch_dtype=torch.bfloat16,
                                               attn_implementation='sdpa', local_files_only=True).to(device)
    for parameter in base.parameters():
        parameter.requires_grad_(False)
    base.config.use_cache = False

    def batch(sample):
        return {key: torch.tensor([value], dtype=torch.long, device=device) for key, value in sample.items()
                if key in ('input_ids', 'labels')}

    recent, history = job['recent'], job['history']
    for event in recent + history:
        for sample in event['samples']:
            sample['variants'] = [encode(tokenizer, item) for item in variants(sample)]

    replay_rows = json.loads(Path(component['replay']).read_text(encoding='utf-8'))
    if not isinstance(replay_rows, list) or len(replay_rows) < 16:
        raise ValueError('The component must contain at least 16 generic replay examples.')
    replay_rows = replay_rows[:256]
    progress('collision_replay')
    anchor_path = root / 'anchors.json'
    saved_anchors = json.loads(anchor_path.read_text(encoding='utf-8')) if anchor_path.exists() else {'cursor': 0, 'rows': []}
    contexts = list(dict.fromkeys(r.get('context', '')[-64:] for r in replay_rows if len(r.get('context', '')) >= 8))[:12]
    terms, codes = set(), []
    for event in recent:
        for sample in event['samples']:
            target = sample['target']
            if not 2 <= len(target) <= 8 or not all('\u4e00' <= c <= '\u9fff' for c in target):
                continue
            segments = phonetic_segments(target, sample['keys'])
            if segments:
                terms.add(target)
                code = ''.join(text[0] for text, _ in segments)
                if code not in codes and len(codes) < 12:
                    codes.append(code)
    queries = [(code, context) for code in codes for context in contexts]
    base.eval()
    for index in range(saved_anchors['cursor'], len(queries)):
        if paused():
            return 75
        keys, context = queries[index]
        prefix = tokenizer(build_prompt(keys, context) + '结果：', add_special_tokens=False, return_tensors='pt').to(device)
        with torch.no_grad():
            output = base.generate(**prefix, num_beams=5, num_return_sequences=5, do_sample=False,
                                   max_new_tokens=2 * len(keys) + 8, use_cache=True,
                                   pad_token_id=tokenizer.eos_token_id)
        for target in tokenizer.batch_decode(output[:, prefix['input_ids'].shape[1]:], skip_special_tokens=True):
            target = target.split('\n', 1)[0].strip()
            if target not in terms and phonetic_segments(target, keys):
                saved_anchors['rows'].append({'keys': keys, 'context': context, 'target': target})
                break
        saved_anchors['cursor'] = index + 1
        atomic_json(anchor_path, saved_anchors)

    progress('teacher_cache')
    cache_root = root.parent.parent / 'teacher-cache' / job['base_sha256']
    cache_root.mkdir(parents=True, exist_ok=True)
    generic, anchors = [], []
    for rows, encoded in ((replay_rows, generic), (saved_anchors['rows'], anchors)):
        for sample in rows:
            if paused():
                return 75
            value = encode(tokenizer, sample)
            digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            path = cache_root / (digest + '.pt')
            if path.exists():
                teacher = torch.load(path, map_location='cpu', weights_only=True)
            else:
                tensors = batch(value)
                with torch.no_grad():
                    logits = base(input_ids=tensors['input_ids']).logits[0, :-1]
                    selected = logits[tensors['labels'][0, 1:] != -100].float().softmax(-1)
                    probabilities, ids = selected.topk(min(32, selected.shape[-1]), dim=-1)
                    teacher = {'teacher_ids': ids.cpu(), 'teacher_probs': torch.cat((probabilities,
                               (1 - probabilities.sum(-1, keepdim=True)).clamp_min(1e-8)), -1).cpu()}
                temporary = path.with_suffix('.tmp')
                torch.save(teacher, temporary)
                os.replace(temporary, path)
            encoded.append({**value, **teacher})
    references = generic + (anchors * max(1, round(len(generic) / len(anchors))) if anchors else [])

    state_pointer = root / 'resume.json'
    saved = json.loads(state_pointer.read_text(encoding='utf-8')) if state_pointer.exists() else None
    previous = job.get('previous', {})
    adapter = Path(saved['directory']) / 'adapter' if saved else Path(previous['checkpoint']) / 'adapter' if previous else None
    if adapter:
        model = PeftModel.from_pretrained(base, adapter, is_trainable=True, local_files_only=True)
    else:
        model = get_peft_model(base, LoraConfig(r=8, lora_alpha=16, target_modules=TARGETS,
                                               lora_dropout=0.0, bias='none', task_type='CAUSAL_LM'))
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=2e-4)
    step, micro = 0, 0
    state_path = Path(saved['directory']) / 'state.pt' if saved else Path(previous['checkpoint']) / 'state.pt' if previous else None
    if state_path:
        state = torch.load(state_path, map_location='cpu', weights_only=True)
        optimizer.load_state_dict(state['optimizer'])
        if saved:
            step, micro = state['step'], state['micro']
            torch.set_rng_state(state['torch_rng'])
            if device == 'cuda':
                torch.cuda.set_rng_state(state['cuda_rng'])
            for name, parameter in model.named_parameters():
                if name in state['gradients']:
                    parameter.grad = state['gradients'][name].to(parameter.device)

    def save_state(next_step, next_micro):
        pointer = json.loads(state_pointer.read_text(encoding='utf-8')) if state_pointer.exists() else None
        slot = 'resume-b' if pointer and Path(pointer['directory']).name == 'resume-a' else 'resume-a'
        destination = root / slot
        destination.mkdir(exist_ok=True)
        model.save_pretrained(destination / 'adapter')
        state = {'optimizer': optimizer.state_dict(), 'step': next_step, 'micro': next_micro,
                 'torch_rng': torch.get_rng_state(), 'gradients': {name: p.grad.cpu() for name, p in model.named_parameters()
                                                                    if p.requires_grad and p.grad is not None}}
        if device == 'cuda':
            state['cuda_rng'] = torch.cuda.get_rng_state()
        torch.save(state, destination / 'state.pt')
        atomic_json(state_pointer, {'directory': str(destination)})
        return destination

    model.train()
    started = time.monotonic()
    while step < 24:
        selected = select_step(recent, history, references, seed, step)
        target_tokens = sum(sum(label != -100 for label in row['labels'][1:]) for row in selected[:12])
        if micro == 0:
            optimizer.zero_grad(set_to_none=True)
        while micro < 16:
            if paused():
                save_state(step, micro)
                return 75
            tensors = batch(selected[micro])
            with torch.autocast(device, dtype=torch.bfloat16):
                logits = model(input_ids=tensors['input_ids']).logits[0, :-1]
            labels = tensors['labels'][0, 1:]
            valid = labels != -100
            if micro < 12:
                loss = torch.nn.functional.cross_entropy(logits[valid].float(), labels[valid], reduction='sum') / target_tokens
            else:
                reference = selected[micro]
                log_probs = logits[valid].float().log_softmax(-1)
                picked = log_probs.gather(-1, reference['teacher_ids'].to(device)).exp()
                student = torch.cat((picked, (1 - picked.sum(-1, keepdim=True)).clamp_min(1e-8)), -1)
                teacher = reference['teacher_probs'].to(device)
                kl = (teacher * (teacher.clamp_min(1e-8).log() - student.clamp_min(1e-8).log())).sum(-1).mean()
                loss = (0.25 * torch.nn.functional.nll_loss(log_probs, labels[valid]) + kl) / 4
            if not torch.isfinite(loss):
                raise RuntimeError('Non-finite personalization loss.')
            loss.backward()
            micro += 1
            progress('training', step, micro)
        torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.0)
        optimizer.step()
        step += 1
        micro = 0
        save_state(step, micro)
    saved_dir = save_state(24, 0)
    if paused():
        return 75
    progress('export', 24)
    destination = root.parent.parent / 'generations' / job['id']
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(saved_dir, destination / 'checkpoint', dirs_exist_ok=True)
    output = destination / 'adapter.gguf'
    subprocess.run([sys.executable, '-X', 'utf8', component['converter'], '--base', str(checkpoint), '--outtype', 'f16',
                    '--outfile', str(output.with_suffix('.tmp')), str(destination / 'checkpoint/adapter')], check=True)
    os.replace(output.with_suffix('.tmp'), output)
    manifest = {'id': job['id'], 'path': str(output), 'checkpoint': str(destination / 'checkpoint'),
                'base_sha256': job['base_sha256'], 'tokenizer_sha256': actual_tokenizer,
                'prompt_version': 'keys_llm_v1', 'rank': 8, 'alpha': 16, 'recipe': RECIPE,
                'clock': job['clock'], 'training_seconds': time.monotonic() - started}
    atomic_json(destination / 'manifest.json', manifest)
    atomic_json(root / 'result.json', manifest)
    cached = sorted(cache_root.glob('*.pt'), key=lambda path: path.stat().st_mtime, reverse=True)
    for expired in cached[4096:]:
        expired.unlink()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    args = parser.parse_args()
    return run_job(args.job)


if __name__ == '__main__':
    sys.exit(main())
