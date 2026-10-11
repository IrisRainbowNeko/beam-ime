#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run teacher translation, dataset expansion, joint training, then evaluation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True, help='translation_corpus.py sample output')
    parser.add_argument('--teacher', required=True)
    parser.add_argument('--teacher-id', required=True)
    parser.add_argument('--teacher-revision', required=True)
    parser.add_argument('--model', required=True, help='full-precision released IME checkpoint')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--teacher-batch-size', type=int, default=16)
    parser.add_argument('--micro-batch-size', type=int, default=32)
    parser.add_argument('--gpu-memory-fraction', type=float, default=.7)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    tools = Path(__file__).resolve().parent
    dataset = args.output / 'data'
    translated = args.output / 'paired.jsonl'
    run = args.output / 'run'
    prefix = [sys.executable, '-u']
    steps = [
        ('teacher', prefix + [str(tools / 'translation_corpus.py'), 'translate', '--model', args.teacher,
            '--teacher-id', args.teacher_id, '--revision', args.teacher_revision,
            '--input', str(args.corpus / 'teacher-input.jsonl'), '--output', str(translated),
            '--batch-size', str(args.teacher_batch_size), '--gpu-memory-fraction', str(args.gpu_memory_fraction)]),
        ('dataset', prefix + [str(tools / 'prepare_dual_task.py'), '--input', str(args.corpus / 'ordinary.jsonl'),
            str(translated), '--output', str(dataset)]),
        ('train', prefix + [str(tools / 'keys_llm_train.py'), '--model', args.model, '--teacher', args.model,
            '--train', str(dataset / 'train.jsonl'), '--dev', str(dataset / 'dev.jsonl'), '--output', str(run),
            '--epochs', '2', '--batch-size', '256', '--micro-batch-size', str(args.micro_batch_size),
            '--learning-rate', '1e-5', '--max-length', '512', '--teacher-weight', '.25',
            '--gpu-memory-fraction', str(args.gpu_memory_fraction), '--save-every', '250', '--log-every', '10']),
        ('evaluate', prefix + [str(tools / 'keys_llm_eval.py'), '--checkpoint', str(run / 'epoch-2'),
            '--input', str(dataset / 'test.jsonl'), '--output', str(args.output / 'evaluation.json'),
            '--batch-size', '8', '--beams', '5']),
        ('baseline', prefix + [str(tools / 'keys_llm_eval.py'), '--checkpoint', args.model,
            '--input', str(dataset / 'test.jsonl'), '--output', str(args.output / 'baseline.json'),
            '--mode', 'ime', '--batch-size', '8', '--beams', '5']),
    ]
    status = {'pid': os.getpid(), 'started': time.time(), 'state': 'running',
              'teacher': args.teacher_id, 'teacher_revision': args.teacher_revision, 'model': args.model}
    def update():
        temporary = args.output / 'status.tmp'
        temporary.write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
        temporary.replace(args.output / 'status.json')
        print(json.dumps(status), flush=True)
    for phase, command in steps:
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
    status.update(state='complete', finished=time.time())
    update()
    return 0


if __name__ == '__main__':
    sys.exit(main())
