#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run matched loss/order/initialization experiments, then evaluate both epochs."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def commands(args):
    tools = Path(__file__).resolve().parent
    python = [sys.executable, '-u']
    evaluations = []
    # All three runs see the same batches and loss weights. Only these two axes vary.
    for group, model, version in (('A', args.ime, 'keys_llm_v2'), ('B', args.ime, 'keys_llm_v3'),
                                  ('C', args.base, 'keys_llm_v3')):
        root = args.output / group
        yield group + '-train', python + [str(tools / 'keys_llm_train.py'), '--model', model, '--teacher', args.ime,
            '--prompt-version', version, '--loss-scheme', 'field', '--train', str(args.data / 'train.jsonl'),
            '--dev', str(args.data / 'dev.jsonl'), '--output', str(root / 'run'), '--epochs', '2', '--batch-size', '256',
            '--micro-batch-size', str(args.micro_batch_size), '--learning-rate', '1e-5', '--max-length', '512',
            '--teacher-weight', '.25', '--gpu-memory-fraction', str(args.gpu_memory_fraction),
            '--seed', '20260930', '--save-every', '250', '--log-every', '10']
        evaluations.extend((group + '-epoch-' + str(epoch), str(root / 'run' / ('epoch-' + str(epoch))), None)
                           for epoch in (1, 2))
    evaluations.extend((('reference-ime', args.ime, 'ime'), ('reference-joint', args.reference, None)))
    for name, checkpoint, mode in evaluations:
        output = args.output / (name + '-evaluation.json')
        command = python + [str(tools / 'keys_llm_eval.py'), '--checkpoint', checkpoint,
            '--input', str(args.data / 'test.jsonl'), '--output', str(output), '--batch-size', '8', '--beams', '5',
            '--gpu-memory-fraction', str(args.gpu_memory_fraction)]
        if mode:
            command += ['--mode', mode]
        yield name + '-evaluate', command
    # CPU scoring follows all GPU work, so these runs do not hold up the next training group.
    for name, checkpoint, mode in evaluations:
        if mode != 'ime':
            yield name + '-semantic', [args.scorer_python, '-u', str(tools / 'score_translation.py'),
                '--cases', str(args.output / (name + '-evaluation.cases.jsonl')),
                '--checkpoint', args.comet_checkpoint, '--model-id', args.comet_model,
                '--model-revision', args.comet_revision, '--output', str(args.output / (name + '-semantic.json'))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--ime', required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--reference', required=True, help='previous joint-training epoch-2 checkpoint')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--micro-batch-size', type=int, default=8)
    parser.add_argument('--gpu-memory-fraction', type=float, default=.25)
    parser.add_argument('--scorer-python', required=True)
    parser.add_argument('--comet-checkpoint', required=True)
    parser.add_argument('--comet-model', required=True)
    parser.add_argument('--comet-revision', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    status = {'pid': os.getpid(), 'host': socket.gethostname(), 'state': 'running', 'started': time.time(),
              'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES'),
              'codeRevision': os.environ.get('BEAM_EXPERIMENT_REVISION'), 'completed': []}

    def update():
        temporary = args.output / 'status.tmp'
        temporary.write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
        temporary.replace(args.output / 'status.json')
        print(json.dumps(status), flush=True)

    for phase, command in commands(args):
        status.update(phase=phase, phase_started=time.time(), command=command)
        update()
        try:
            with (args.output / (phase + '.log')).open('x', encoding='utf-8') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        except OSError as error:
            status.update(state='failed', error=str(error), finished=time.time())
            update()
            raise
        if result.returncode:
            status.update(state='failed', exit_code=result.returncode, finished=time.time())
            update()
            return result.returncode
        status['completed'].append(phase)
    summary = {}
    for path in sorted(args.output.glob('*-evaluation.json')):
        report = json.loads(path.read_text(encoding='utf-8'))
        entry = {'promptVersion': report['promptVersion'], 'inputSha256': report['inputSha256'], 'metrics': report['metrics']}
        semantic = path.with_name(path.name.replace('-evaluation.json', '-semantic.json'))
        if semantic.exists():
            entry['semantic'] = json.loads(semantic.read_text(encoding='utf-8'))['metrics']
        summary[path.name.removesuffix('-evaluation.json')] = entry
    (args.output / 'comparison.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    status.update(state='complete', finished=time.time())
    update()
    return 0


if __name__ == '__main__':
    sys.exit(main())
