#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate public fragment supervision, run D0-D3, and evaluate held-out sets."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def commands(args):
    tools, root = Path(__file__).resolve().parent, args.output
    py = [sys.executable, '-u']
    data = root / 'data'
    paired = root / 'paired.jsonl'
    yield 'teacher', py + [str(tools / 'translation_corpus.py'), 'translate', '--fragments', '--model', args.teacher,
        '--teacher-id', 'Qwen/Qwen3-32B', '--revision', '9216db5781bf21249d130ec9da846c4624c16137',
        '--input', str(args.corpus / 'teacher-input.jsonl'), '--output', str(paired), '--batch-size', '4',
        '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
    yield 'dataset', py + [str(tools / 'prepare_dual_task.py'), '--input', str(paired),
        *[str(args.corpus / (split + '-base.jsonl')) for split in ('train', 'dev', 'test')], '--output', str(data)]
    yield 'benchmarks', None
    for strategy in ('beam', 'greedy_first', 'field'):
        yield 'D0-' + strategy, py + [str(tools / 'keys_llm_eval.py'), '--checkpoint', args.baseline,
            '--input', str(data / 'dev.jsonl'), '--output', str(root / ('D0-' + strategy + '.json')),
            '--batch-size', '2', '--beams', '5', '--decoding', strategy, '--limit-per-variant', '128',
            '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
        yield 'D0-' + strategy + '-semantic', [args.scorer_python, str(tools / 'score_translation.py'),
            '--cases', str(root / ('D0-' + strategy + '.cases.jsonl')), '--checkpoint', args.comet_checkpoint,
            '--model-id', 'Unbabel/wmt22-comet-da', '--model-revision', '2760a223ac957f30acfb18c8aa649b01cf1d75f2',
            '--display', '--output', str(root / ('D0-' + strategy + '-semantic.json'))]
    judge = py + [str(tools / 'translation_corpus.py'), 'judge', '--model', args.teacher,
        '--teacher-id', 'Qwen/Qwen3-32B', '--revision', '9216db5781bf21249d130ec9da846c4624c16137',
        '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
    yield 'D0-quality', judge + ['--cases', *[str(root / ('D0-' + s + '.cases.jsonl'))
        for s in ('beam', 'greedy_first', 'field')], '--output', str(root / 'D0-quality.json')]
    yield 'select-decoding', None
    for label, scheme in (('D1', 'field'), ('D2', 'sample_field')):
        yield label + '-train', py + [str(tools / 'keys_llm_train.py'), '--model', args.ime, '--teacher', args.ime,
            '--prompt-version', 'keys_llm_v4', '--loss-scheme', scheme, '--fragment-sampling',
            '--train', str(data / 'train.jsonl'), '--dev', str(data / 'dev.jsonl'), '--output', str(root / label),
            '--epochs', '2', '--batch-size', '256', '--micro-batch-size', '8', '--learning-rate', '5e-6',
            '--max-length', '512', '--teacher-weight', '.25', '--seed', '20261014', '--save-every', '250',
            '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
    yield 'preferences', py + [str(tools / 'fragment_preferences.py'), '--train', str(data / 'train.jsonl'),
                               '--output', str(root / 'preferences.jsonl')]
    yield 'D3-train', py + [str(tools / 'keys_llm_train.py'), '--model', str(root / 'D2/epoch-2'), '--teacher', args.ime,
        '--prompt-version', 'keys_llm_v4', '--loss-scheme', 'sample_field', '--fragment-sampling',
        '--train', str(data / 'train.jsonl'), '--dev', str(data / 'dev.jsonl'), '--output', str(root / 'D3'),
        '--epochs', '1', '--max-steps', '200', '--preferences', str(root / 'preferences.jsonl'), '--batch-size', '256',
        '--micro-batch-size', '4', '--learning-rate', '1e-6', '--max-length', '512', '--teacher-weight', '.25',
        '--seed', '20261014', '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
    for label, checkpoint in [('original', args.ime), ('B', args.baseline),
                             ('D1', str(root / 'D1/epoch-2')), ('D2', str(root / 'D2/epoch-2')), ('D3', str(root / 'D3/epoch-1'))]:
        selected = json.loads((root / 'decoding.json').read_text())['strategy']
        for split in ('dev', 'test'):
            yield label + '-' + split, py + [str(tools / 'keys_llm_eval.py'), '--checkpoint', checkpoint,
                '--input', str(data / (split + '.jsonl')), '--output', str(root / (label + '-' + split + '.json')),
                '--batch-size', '4', '--beams', '5', '--gpu-memory-fraction', str(args.gpu_memory_fraction),
                '--decoding', selected,
                *(['--mode', 'ime'] if label == 'original' else [])]
        if label != 'original':
            for benchmark in ('semantic-eval', 'wait-200'):
                yield label + '-' + benchmark, py + [str(tools / 'keys_llm_eval.py'), '--checkpoint', checkpoint,
                    '--input', str(root / (benchmark + '.jsonl')), '--output', str(root / (label + '-' + benchmark + '.json')),
                    '--batch-size', '2', '--beams', '5', '--decoding', selected,
                    '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
            yield label + '-semantic', [args.scorer_python, str(tools / 'score_translation.py'),
                '--cases', str(root / (label + '-dev.cases.jsonl')), '--checkpoint', args.comet_checkpoint,
                '--model-id', 'Unbabel/wmt22-comet-da', '--model-revision', '2760a223ac957f30acfb18c8aa649b01cf1d75f2',
                '--display', '--output', str(root / (label + '-semantic.json'))]
    yield 'model-quality', judge + ['--cases', *[str(root / (label + '-' + split + '.cases.jsonl'))
        for label in ('B', 'D1', 'D2', 'D3') for split in ('dev', 'semantic-eval')],
        '--output', str(root / 'model-quality.json')]
    yield 'select-model', None
    winner = json.loads((root / 'comparison.json').read_text())
    yield 'export-q8', py + [str(Path(args.llama_source) / 'convert_hf_to_gguf.py'), winner['checkpoint'],
                             '--outfile', str(root / 'model-q8.gguf'), '--outtype', 'q8_0']
    yield 'export-metadata', [sys.executable, '-u', str(tools / 'tie_embeddings.py'), str(root / 'model-q8.gguf'),
        str(root / 'beam-fragments-q8.gguf'), '--prompt-version', 'keys_llm_v4', '--decoding-strategy', winner['decoding']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--teacher', required=True)
    parser.add_argument('--ime', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--scorer-python', required=True)
    parser.add_argument('--comet-checkpoint', required=True)
    parser.add_argument('--llama-source', required=True)
    parser.add_argument('--gpu-memory-fraction', type=float, default=.65)
    parser.add_argument('--start-phase', default='teacher', help='continue from this phase using existing earlier outputs')
    args = parser.parse_args()
    os.environ['PYTHONPATH'] = str(Path(args.llama_source) / 'gguf-py') + os.pathsep + os.environ.get('PYTHONPATH', '')
    args.output.mkdir(parents=True, exist_ok=args.start_phase != 'teacher')
    state = {'pid': os.getpid(), 'started': time.time(), 'state': 'running', 'completed': []}
    def update():
        temporary = args.output / 'status.tmp'
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(args.output / 'status.json')
        print(json.dumps(state), flush=True)
    active = False
    for phase, command in commands(args):
        active |= phase == args.start_phase
        if not active:
            state['completed'].append(phase)
            continue
        state.update(phase=phase, command=command, phase_started=time.time()); update()
        if phase == 'benchmarks':
            import random
            from prepare_dual_task import expand
            rows = [json.loads(line) for line in (args.output / 'paired.jsonl').read_text().splitlines()]
            ready = [r for r in rows if r['split'] == 'test' and not r.get('translation_error') and r['translation_status'] == 'ready']
            unique = {(r['parent_id'], r['target']): r for r in ready}
            selected = random.Random(20261014).sample(list(unique.values()), 500)
            wait_pool = {(r['parent_id'], r['target']): r for r in rows if r['split'] == 'test'
                         and not r.get('translation_error') and r['translation_status'] == 'need_more_input'}
            waiting = random.Random(20261014).sample(list(wait_pool.values()), min(200, len(wait_pool)))
            state['benchmark_counts'] = {'semantic': len(selected), 'waiting': len(waiting), 'waiting_target': 200}
            with (args.output / 'semantic-500.jsonl').open('x') as stream:
                for row in selected:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            with (args.output / 'semantic-eval.jsonl').open('x') as stream:
                for index, row in enumerate(selected):
                    variants = [r for r in expand(row) if r['mode'] == 'translate_en']
                    stream.write(json.dumps(variants[index % len(variants)], ensure_ascii=False) + '\n')
            with (args.output / 'wait-200.jsonl').open('x') as stream:
                for row in waiting:
                    stream.write(json.dumps(next(r for r in expand(row) if r['mode'] == 'translate_en'), ensure_ascii=False) + '\n')
            state['completed'].append(phase)
            continue
        if phase == 'select-decoding':
            candidates = []
            quality = json.loads((args.output / 'D0-quality.json').read_text())['reports']
            for strategy in ('beam', 'greedy_first', 'field'):
                report = json.loads((args.output / ('D0-' + strategy + '.json')).read_text())
                semantic = json.loads((args.output / ('D0-' + strategy + '-semantic.json')).read_text())
                candidates.append({'strategy': strategy, 'chinese': report['metrics']['translate_en']['validatedChineseP@1'],
                    'semantic': quality['D0-' + strategy + '.cases.jsonl']['metrics'],
                    'comet': semantic['metrics']['all']['comet'], 'elapsed': report['elapsedSeconds']})
            top = max(row['chinese'] for row in candidates)
            eligible = [row for row in candidates if row['chinese'] >= top - .005]
            selected = max(eligible, key=lambda row: (row['semantic']['endToEnd']['rate'], -row['elapsed']))
            (args.output / 'decoding.json').write_text(json.dumps({**selected, 'comparisons': candidates}, indent=2) + '\n')
            state['completed'].append(phase)
            continue
        if phase == 'select-model':
            baseline = json.loads((args.output / 'original-dev.json').read_text())['metrics']['ime']
            quality = json.loads((args.output / 'model-quality.json').read_text())['reports']
            candidates = []
            for label, epoch in (('D1', 'epoch-2'), ('D2', 'epoch-2'), ('D3', 'epoch-1')):
                report = json.loads((args.output / (label + '-dev.json')).read_text())
                semantic = json.loads((args.output / (label + '-semantic.json')).read_text())
                ime = report['metrics']['ime']
                candidates.append({'label': label, 'checkpoint': str(args.output / label / epoch),
                    'ime': ime, 'translation': report['metrics']['translate_en'],
                    'chinese_target_met': all(ime[f'displayP@{k}'] >= baseline[f'displayP@{k}'] - .005 for k in (1, 5)),
                    'semantic': quality[label + '-dev.cases.jsonl']['metrics'],
                    'comet': semantic['metrics']['all']['comet']})
            eligible = [r for r in candidates if r['chinese_target_met']]
            selected = max(eligible, key=lambda row: row['semantic']['endToEnd']['rate']) if eligible else max(
                candidates, key=lambda row: (row['ime']['displayP@1'], row['semantic']['endToEnd']['rate']))
            report = {**selected, 'candidates': candidates, 'baseline': baseline,
                      'decoding': json.loads((args.output / 'decoding.json').read_text())['strategy'],
                      'semantic_judge': 'Qwen/Qwen3-32B; automated, not human verification',
                      'human_semantic_accuracy': None, 'private_holdout_evaluated': False, 'runtime_benchmarked': False}
            (args.output / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
            state['completed'].append(phase)
            continue
        try:
            with (args.output / (phase + '.log')).open('x') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        except OSError as error:
            state.update(state='failed', error=str(error), finished=time.time()); update()
            raise
        if result.returncode:
            state.update(state='failed', exit_code=result.returncode, finished=time.time()); update()
            return result.returncode
        state['completed'].append(phase)
    if not active:
        raise ValueError('unknown start phase: ' + args.start_phase)
    state.update(state='complete', finished=time.time()); update()
    return 0


if __name__ == '__main__':
    sys.exit(main())
