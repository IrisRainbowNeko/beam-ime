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
import math
import os
import re
import socket
import statistics
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch

from keys_llm_format import CHINESE_FIRST_VERSIONS, DUAL_PROMPT_VERSIONS, PROMPT_VERSION, build_prompt, display_valid, parse_output, strict_valid, structural_valid
from translation_corpus import proportion

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


def ranked_results(keys: str, texts: list[str], name: str, mode: str = 'ime', version: str = PROMPT_VERSION) -> list[str]:
    seen, ranked = set(), []
    for text in texts:
        parsed = parse_output(text, mode, version)
        result = parsed["result"]
        if FILTERS[name](keys, parsed) and result not in seen:
            seen.add(result)
            ranked.append(result)
    return ranked


def summarize(cases: list[dict[str, Any]], filters=FILTERS) -> dict[str, Any]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        groups["all"].append(case)
        groups[case.get('mode', 'ime')].append(case)
        groups[f"{case.get('mode', 'ime')}:{case['variant']}"].append(case)
        groups[case["variant"]].append(case)
        seen = "seen" if case.get("seenTarget") else "unseen"
        groups[f"{case['variant']}:{seen}"].append(case)
        if any(char.isascii() and char.isalpha() for char in case["source"]):
            groups["withEnglish"].append(case)
            groups[f"{case['variant']}:withEnglish"].append(case)
            groups[f"{case.get('mode', 'ime')}:withEnglish"].append(case)
        if case.get("hasContext"):
            groups["withContext"].append(case)
            groups[f"{case['variant']}:withContext"].append(case)
            groups[f"{case.get('mode', 'ime')}:withContext"].append(case)
    metrics = {}
    for name, members in sorted(groups.items()):
        entry = {"rows": len(members)}
        for filter_name in filters:
            ranks = [case["ranks"][filter_name] for case in members]
            for k in TOP_K:
                entry[f"{filter_name}P@{k}"] = sum(rank is not None and rank <= k for rank in ranks) / len(members)
        for k in (1, 5):
            estimate = proportion(sum(case['target'] in case.get('displayCandidates', [])[:k] for case in members), len(members))
            entry[f'displayP@{k}'] = estimate['rate']
            entry[f'displayP@{k}Wilson95'] = estimate['wilson95']
        metrics[name] = entry
        bilingual = [case for case in members if case.get('mode') == 'translate_en']
        if bilingual:
            entry['pairedFormatRate'] = sum(case['paired'] for case in bilingual) / len(bilingual)
            entry['commentP@1'] = sum(case['comment'] == case['source'] for case in bilingual) / len(bilingual)
            cn = proportion(sum(case.get('displayComment') == case['source'] for case in bilingual), len(bilingual))
            entry['validatedChineseP@1'] = cn['rate']
            entry['validatedChineseP@1Wilson95'] = cn['wilson95']
            entry['displayCommentP@1'] = sum(case.get('displayComment') == case['source'] and case.get('translationStatus') != 'need_more_input'
                                              for case in bilingual) / len(bilingual)
            entry['englishContainsHanziRate'] = sum(bool(re.search('[一-鿿]', case['english'] or '')) for case in bilingual) / len(bilingual)
            entry['missingEnglishRate'] = sum(not case['english'] for case in bilingual) / len(bilingual)
            entry['waitingRate'] = sum(case.get('translationStatus') == 'need_more_input' for case in bilingual) / len(bilingual)
            expected_wait = [c for c in bilingual if c.get('expectedTranslationStatus') == 'need_more_input']
            ready = [c for c in bilingual if c.get('expectedTranslationStatus', 'ready') == 'ready']
            if expected_wait:
                recall = proportion(sum(c.get('translationStatus') == 'need_more_input' for c in expected_wait), len(expected_wait))
                entry.update(waitRecall=recall['rate'], waitRecallWilson95=recall['wilson95'], expectedWaitRows=len(expected_wait))
            if ready:
                false_wait = proportion(sum(c.get('translationStatus') == 'need_more_input' for c in ready), len(ready))
                entry.update(falseWaitRate=false_wait['rate'], falseWaitWilson95=false_wait['wilson95'], expectedReadyRows=len(ready))
            entry['noCandidateRate'] = sum(not c['displayCandidates'] for c in bilingual) / len(bilingual)
            entry['truncatedRate'] = sum(c.get('truncated', False) for c in bilingual) / len(bilingual)
        if any('timedOut' in case for case in members):
            entry['timeoutRate'] = sum(case.get('timedOut', False) for case in members) / len(members)
    return metrics


def field_score(log_probabilities, pieces, version):
    chinese, english = [], []
    after_tab = False
    for probability, piece in zip(log_probabilities, pieces):
        if '\t' in piece:
            if piece.count('\t') != 1 or after_tab:
                return float('-inf')
            before, after = piece.split('\t')
            # Separator tokens have no semantic weight; mixed tokens are shared by byte span.
            size = len(before.encode()) + len(after.encode())
            if before:
                (chinese if version in CHINESE_FIRST_VERSIONS else english).append(probability * len(before.encode()) / size)
            if after:
                (english if version in CHINESE_FIRST_VERSIONS else chinese).append(probability * len(after.encode()) / size)
            after_tab = True
        elif piece:
            is_chinese = (not after_tab) == (version in CHINESE_FIRST_VERSIONS)
            (chinese if is_chinese else english).append(probability)
    if not after_tab or not chinese or not english:
        return float('-inf')
    return .75 * sum(chinese) / len(chinese) + .25 * sum(english) / len(english) ** .6


def paired_mode_metrics(cases):
    ordinary = {row['id']: row for row in cases if row['mode'] == 'ime'}
    pairs = [(ordinary[row['id']], row) for row in cases if row['mode'] == 'translate_en' and row['id'] in ordinary]
    if not pairs:
        return {}
    ime = proportion(sum(a['target'] in a['displayCandidates'][:1] for a, _ in pairs), len(pairs))
    annotation = proportion(sum(b.get('displayComment') == b['source'] for _, b in pairs), len(pairs))
    return {'rows': len(pairs), 'imeP@1': ime, 'translationChineseP@1': annotation,
            'translationMinusIme': annotation['rate'] - ime['rate']}


def evaluate_runtime(args):
    if os.name != 'posix':
        raise ValueError('isolated daemon replay currently requires a Unix socket')
    os.umask(0o077)
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding='utf-8').splitlines() if line.strip()]
    if args.mode:
        rows = [row for row in rows if row.get('mode', 'ime') == args.mode]
    if args.variants:
        rows = [row for row in rows if row.get('variant', 'original') in args.variants]
    if args.limit_per_variant:
        counts, limited = defaultdict(int), []
        for row in rows:
            group = (row.get('mode', 'ime'), row.get('variant', 'original'))
            if counts[group] < args.limit_per_variant:
                limited.append(row); counts[group] += 1
        rows = limited
    if not rows:
        raise ValueError('no runtime evaluation rows')
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cases, started = [], time.monotonic()
    with tempfile.TemporaryDirectory(prefix='beam-replay-') as temporary, out.with_suffix('.daemon.log').open('w') as log:
        endpoint = str(Path(temporary) / 'beamd.sock')
        def request(value):
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(30); client.connect(endpoint)
                client.sendall((json.dumps({'id': 1, **value}) + '\n').encode())
                with client.makefile('rb') as stream:
                    reply = json.loads(stream.readline(65537))
            if not reply['ok']:
                raise RuntimeError('daemon replay failed: ' + json.dumps(reply))
            return reply
        command = [args.runtime, '--model', args.checkpoint, '--socket', endpoint,
                   '--learning-dir', str(Path(temporary) / 'learning'), '--ngl', str(args.ngl), '--threads', str(args.threads)]
        if args.backend_dir:
            command += ['--backend-dir', args.backend_dir]
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError('daemon exited at startup; see ' + str(out.with_suffix('.daemon.log')))
                try:
                    health = request({'op': 'health'})
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('daemon startup exceeded 60 seconds')
                    time.sleep(.05)
            # Warm up before timing. Each measured query performs one normal generation/search.
            request({'op': 'query', 'keys': 'nihao', 'beam_ms': args.beam_ms})
            with out.with_suffix('.cases.jsonl').open('w', encoding='utf-8') as stream:
                for index, row in enumerate(rows):
                    mode, context = row.get('mode', 'ime'), '' if args.drop_context else row.get('context', '')
                    tick = time.perf_counter()
                    reply = request({'op': 'query', 'keys': row['keys'], 'context': context, 'mode': mode,
                                     'beam_ms': args.beam_ms, 'max': 5})
                    elapsed = (time.perf_counter() - tick) * 1000
                    candidates, comments = reply['candidates'], reply['comments']
                    timed_out = args.client_timeout_ms > 0 and elapsed > args.client_timeout_ms
                    if timed_out:
                        candidates, comments = [], []
                    first_comment = comments[0] if comments else None
                    case = {'id': row['id'], 'variant': row.get('variant', 'original'), 'keys': row['keys'],
                        'mode': mode, 'context': context, 'hasContext': bool(context), 'source': row['target'],
                        'target': row.get('translation', '') if mode == 'translate_en' else row['target'],
                        'comment': first_comment, 'displayComment': first_comment, 'paired': bool(comments),
                        'english': candidates[0] if candidates and mode == 'translate_en' else None,
                        'displayEnglish': candidates[0] if candidates and mode == 'translate_en' else None,
                        'displayCandidates': candidates, 'displayComments': comments,
                        'translationStatus': 'ready' if timed_out else reply.get('translation_status', 'ready'),
                        'expectedTranslationStatus': row.get('translation_status', 'ready'),
                        'requestMilliseconds': elapsed, 'greedyMilliseconds': reply['greedy_ms'],
                        'beamMilliseconds': reply['beam_ms'], 'beamComplete': reply['beam_complete'], 'timedOut': timed_out}
                    cases.append(case)
                    stream.write(json.dumps(case, ensure_ascii=False) + '\n'); stream.flush()
                    if (index + 1) % 50 == 0 or index + 1 == len(rows):
                        print(json.dumps({'done': index + 1, 'rows': len(rows), 'backend': health['backend']}), flush=True)
            proc_status = Path(f'/proc/{process.pid}/status')
            peak = re.search(r'^VmHWM:\s+(\d+)', proc_status.read_text(), re.M) if proc_status.exists() else None
            peak_rss = int(peak[1]) if peak else None
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
    latency = {}
    for mode in sorted({row['mode'] for row in cases}):
        members = [row for row in cases if row['mode'] == mode]
        latency[mode] = {'rows': len(members)}
        for field in ('requestMilliseconds', 'greedyMilliseconds', 'beamMilliseconds'):
            values = sorted(row[field] for row in members)
            latency[mode][field] = {'median': statistics.median(values), 'p95': values[math.ceil(.95 * len(values))-1]}
    report = {'kind': 'beam-native-replay', 'checkpoint': args.checkpoint, 'modelSha256': sha256(Path(args.checkpoint)),
        'input': args.input, 'inputSha256': sha256(Path(args.input)), 'promptVersion': health['prompt_version'],
        'backend': health['backend'], 'rows': len(cases), 'threads': args.threads, 'beamBudgetMs': args.beam_ms,
        'clientDeadlineMs': args.client_timeout_ms,
        'elapsedSeconds': time.monotonic()-started, 'peakResidentKiB': peak_rss,
        'metrics': summarize(cases, filters=()), 'latency': latency,
        'note': 'Actual daemon candidates with isolated learning data. WAIT replies do not expose the Chinese field; '
                'Chinese annotation accuracy for WAIT is measured separately by checkpoint evaluation.'}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'backend': report['backend'], 'rows': report['rows'], 'latency': latency}), flush=True)


@torch.no_grad()
def evaluate(args: argparse.Namespace) -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if args.gpu_memory_fraction:
        torch.cuda.set_per_process_memory_fraction(args.gpu_memory_fraction)
    rows = [json.loads(line) for line in Path(args.input).open(encoding="utf-8") if line.strip()]
    if args.mode:
        rows = [row for row in rows if row.get('mode', 'ime') == args.mode]
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
        counts: dict[tuple[str, str], int] = defaultdict(int)
        kept = []
        for row in rows:
            group = (row.get('mode', 'ime'), row['variant'])
            if counts[group] < args.limit_per_variant:
                counts[group] += 1
                kept.append(row)
        rows = kept
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint, padding_side="left")
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    model = AutoModelForCausalLM.from_pretrained(args.checkpoint, torch_dtype=torch.bfloat16, attn_implementation="sdpa")
    model.to("cuda").eval()
    version = args.prompt_version or getattr(model.config, 'beam_prompt_version', 'keys_llm_v1')

    order = sorted(range(len(rows)), key=lambda index: len(rows[index]["keys"]))
    cases: list[dict | None] = [None] * len(rows)
    started = time.time()
    for start in range(0, len(order), args.batch_size):
        indices = order[start:start + args.batch_size]
        prompts = [build_prompt(rows[i]["keys"], rows[i].get("context", ""), rows[i].get('mode', 'ime'), version) + '结果：' for i in indices]
        encoded = tokenizer(prompts, add_special_tokens=False, padding=True, return_tensors="pt").to("cuda")
        strategy = getattr(args, 'decoding', 'beam')
        output = model.generate(
            **encoded,
            num_beams=args.beams,
            num_return_sequences=args.beams,
            do_sample=False,
            max_new_tokens=max(min(192, 4 * len(rows[i]['keys'].replace("'", '')) + 32) if rows[i].get('mode') == 'translate_en'
                               else 2 * len(rows[i]['keys'].replace("'", '')) + 8 for i in indices),
            pad_token_id=pad_id,
            eos_token_id=tokenizer.eos_token_id,
            use_cache=True,
            length_penalty=1.0,
            repetition_penalty=1.0,
            return_dict_in_generate=True,
            output_scores=strategy == 'field',
        )
        generated = output.sequences[:, encoded["input_ids"].shape[1]:]
        transitions = model.compute_transition_scores(output.sequences, output.scores, output.beam_indices, normalize_logits=True) if strategy == 'field' else None
        greedy_texts = None
        greedy_tokens = None
        if strategy == 'greedy_first' and any(rows[i].get('mode') == 'translate_en' for i in indices):
            budget = max(min(192, 4 * len(rows[i]['keys'].replace("'", '')) + 32) if rows[i].get('mode') == 'translate_en'
                         else 2 * len(rows[i]['keys'].replace("'", '')) + 8 for i in indices)
            greedy_tokens = model.generate(**encoded, num_beams=1, do_sample=False, max_new_tokens=budget,
                                           pad_token_id=pad_id, eos_token_id=tokenizer.eos_token_id, use_cache=True)
            greedy_tokens = greedy_tokens[:, encoded['input_ids'].shape[1]:]
            greedy_texts = ['结果：' + text for text in tokenizer.batch_decode(greedy_tokens, skip_special_tokens=True)]
        texts = ['结果：' + text for text in tokenizer.batch_decode(generated, skip_special_tokens=True)]
        for offset, index in enumerate(indices):
            row = rows[index]
            mode = row.get('mode', 'ime')
            target = row['translation'] if mode == 'translate_en' else row['target']
            beams = texts[offset * args.beams:(offset + 1) * args.beams]
            complete = [tokenizer.eos_token_id in tokens.tolist() for tokens in generated[offset * args.beams:(offset + 1) * args.beams]]
            if strategy == 'field' and mode == 'translate_en':
                scores = []
                for j in range(args.beams):
                    ids = generated[offset * args.beams + j].tolist()
                    end = ids.index(tokenizer.eos_token_id) if tokenizer.eos_token_id in ids else len(ids)
                    pieces = [tokenizer.decode([token], skip_special_tokens=True) for token in ids[:end]]
                    scores.append(field_score(transitions[offset * args.beams + j, :end].tolist(), pieces, version))
                ranking = sorted(range(args.beams), key=scores.__getitem__, reverse=True)
                beams, complete = [beams[j] for j in ranking], [complete[j] for j in ranking]
            if greedy_texts and mode == 'translate_en':
                beams.insert(0, greedy_texts[offset])
                complete.insert(0, tokenizer.eos_token_id in greedy_tokens[offset].tolist())
            ranks, top = {}, {}
            for name in FILTERS:
                ranked = ranked_results(row["keys"], beams, name, mode, version)
                top[name] = ranked[:5]
                ranks[name] = ranked.index(target) + 1 if target in ranked else None
            parsed = parse_output(beams[0], mode, version)
            displayed = []
            for text, finished in zip(beams, complete):
                candidate = parse_output(text, mode, version)
                valid = structural_valid(row['keys'], candidate) if mode == 'ime' else display_valid(row['keys'], candidate)
                if finished and valid:
                    if candidate.get('translation_status') == 'need_more_input':
                        if not displayed:
                            displayed.append(candidate)
                        continue
                    identity = candidate['result'].lower() if mode == 'ime' else candidate['result']
                    if identity not in [c['result'].lower() if mode == 'ime' else c['result'] for c in displayed if c['result'] is not None]:
                        displayed.append(candidate)
            waiting = bool(displayed and displayed[0].get('translation_status') == 'need_more_input')
            cases[index] = {
                "id": row["id"], "variant": row["variant"], "keys": row["keys"], "target": target, 'mode': mode,
                'source': row['target'], 'comment': parsed.get('comment'), 'paired': structural_valid(row['keys'], parsed),
                'english': parsed['result'] if mode == 'translate_en' else None,
                'context': row.get('context', ''),
                "hasContext": bool(row.get("context")),
                "seenTarget": row.get("seenTarget"), "ranks": ranks, "top": top,
                "beam0": beams[0],
                'displayCandidates': [] if waiting else [c['result'] for c in displayed[:5]],
                'displayComments': [] if waiting else [c.get('comment') for c in displayed[:5]],
                'displayComment': displayed[0].get('comment') if displayed else None,
                'displayEnglish': displayed[0]['result'] if displayed and not waiting and mode == 'translate_en' else None,
                'translationStatus': 'need_more_input' if waiting else 'ready',
                'expectedTranslationStatus': row.get('translation_status', 'ready'),
                'truncated': not complete[0],
            }
        print(json.dumps({"done": min(start + args.batch_size, len(order)), "rows": len(order),
                          "elapsed": round(time.time() - started, 1)}), flush=True)
    finished = [case for case in cases if case is not None]
    report = {
        "kind": "beam-v8-keys-llm-evaluation",
        "promptVersion": version,
        "translationMetric": "reference exact match, not semantic translation quality",
        "checkpoint": args.checkpoint,
        "input": args.input,
        "inputSha256": sha256(Path(args.input)),
        "beams": args.beams,
        'decodingStrategy': getattr(args, 'decoding', 'beam'),
        "decoding": {'useCache': True, 'lengthPenalty': 1.0, 'repetitionPenalty': 1.0},
        "strictMetricsApplicable": any(case['beam0'].startswith('切分：') for case in finished),
        "dropContext": args.drop_context,
        "rows": len(finished),
        "elapsedSeconds": round(time.time() - started, 1),
        "metrics": summarize(finished),
        'pairedModeChinese': paired_mode_metrics(finished),
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
    parser.add_argument('--mode', choices=('ime', 'translate_en'))
    parser.add_argument('--prompt-version', choices=('keys_llm_v1', *DUAL_PROMPT_VERSIONS))
    parser.add_argument("--output", required=True)
    parser.add_argument("--beams", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--variants", nargs="*")
    parser.add_argument("--limit-per-variant", type=int)
    parser.add_argument("--drop-context", action="store_true", help="evaluate the same rows without context")
    parser.add_argument("--seen-from", nargs="*", help="training JSONL files that define seen targets")
    parser.add_argument('--gpu-memory-fraction', type=float)
    parser.add_argument('--decoding', choices=('beam', 'greedy_first', 'field'), default='beam')
    parser.add_argument('--runtime', help='beamd executable for isolated native GGUF replay on this machine')
    parser.add_argument('--ngl', type=int, default=0)
    parser.add_argument('--backend-dir')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--beam-ms', type=int, default=2000)
    parser.add_argument('--client-timeout-ms', type=int, default=0,
                        help='count replies later than this deadline as empty candidates; zero measures unrestricted quality')
    args = parser.parse_args()
    (evaluate_runtime if args.runtime else evaluate)(args)


if __name__ == "__main__":
    main()
