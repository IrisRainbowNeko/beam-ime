# SPDX-License-Identifier: Apache-2.0
"""Native tiny-Qwen regression; NumPy and gguf-py only, no PyTorch."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


def tiny_model(path):
    import gguf
    import numpy as np
    rng = np.random.default_rng(17)
    writer = gguf.GGUFWriter(str(path), 'qwen3')
    writer.add_context_length(256)
    writer.add_embedding_length(32)
    writer.add_block_count(1)
    writer.add_feed_forward_length(64)
    writer.add_head_count(2)
    writer.add_head_count_kv(2)
    writer.add_rope_dimension_count(16)
    writer.add_layer_norm_rms_eps(1e-6)
    writer.add_tokenizer_model('llama')
    tokens = ['<unk>', '<s>', '</s>', '▁'] + [f'<0x{i:02X}>' for i in range(256)]
    writer.add_token_list(tokens)
    writer.add_token_scores([0.0] * len(tokens))
    writer.add_token_types([2, 3, 3, 1] + [6] * 256)
    writer.add_unk_token_id(0)
    writer.add_bos_token_id(1)
    writer.add_eos_token_id(2)
    writer.add_add_bos_token(False)
    writer.add_add_eos_token(False)
    def weight(name, shape):
        data = np.ones(shape, dtype=np.float32) if len(shape) == 1 else rng.normal(0, .03, shape).astype(np.float32)
        writer.add_tensor(name, data)
    weight('token_embd.weight', (len(tokens), 32))
    weight('output_norm.weight', (32,))
    for name in ('attn_norm', 'ffn_norm'):
        weight('blk.0.' + name + '.weight', (32,))
    for name in ('attn_q_norm', 'attn_k_norm'):
        weight('blk.0.' + name + '.weight', (16,))
    for name in ('attn_q', 'attn_k', 'attn_v', 'attn_output'):
        weight('blk.0.' + name + '.weight', (32, 32))
    for name in ('ffn_gate', 'ffn_up'):
        weight('blk.0.' + name + '.weight', (64, 32))
    weight('blk.0.ffn_down.weight', (32, 64))
    writer.write_header_to_file(); writer.write_kv_data_to_file(); writer.write_tensors_to_file(); writer.close()


@unittest.skipUnless(os.environ.get('BEAM_TEST_NATIVE_TRAINER'), 'native trainer regression runs in standalone trainer CTest')
class NativeTrainerTest(unittest.TestCase):
    def test_microbatch_resume_and_completed_reexport(self):
        import gguf
        import numpy as np
        executable = os.environ['BEAM_TEST_NATIVE_TRAINER']
        with tempfile.TemporaryDirectory(prefix='beam-训练 test-') as temporary:
            root = Path(temporary)
            model = root / 'tiny.gguf'; tiny_model(model)
            fingerprint = hashlib.sha256(model.read_bytes()).hexdigest()
            pinyin = root / 'pinyin.tsv'; pinyin.write_text('星\txing\n好\thao\n', encoding='utf-8')
            replay = root / 'replay.json'
            replay.write_text(json.dumps([{'keys': 'hao', 'target': '好', 'context': ''}] * 16), encoding='utf-8')
            component = {'schemaVersion': 2, 'recipe': 'beam-personal-r8-qvac-v1', 'prompt_version': 'keys_llm_v1',
                         'backend': 'cpu', 'base_sha256': fingerprint, 'pinyin': str(pinyin), 'replay': str(replay)}
            def job(directory):
                directory.mkdir(parents=True)
                value = {'id': 'fixed-seed', 'directory': str(directory), 'clock': 1, 'base_sha256': fingerprint,
                         'model': str(model), 'component': component, 'history': [], 'previous': {},
                         'recent': [{'event_id': '1', 'samples': [{'keys': 'xing', 'target': '星', 'context': ''}]}]}
                path = directory / 'job.json'; path.write_text(json.dumps(value), encoding='utf-8'); return path
            baseline = job(root / 'baseline/jobs/fixed-seed')
            resumed = job(root / 'resumed/jobs/fixed-seed')
            log = root / 'training.log'
            with log.open('w') as output:
                def run(path):
                    result = subprocess.run([executable, '--job', str(path)], stdout=output, stderr=output, timeout=180)
                    self.assertEqual(result.returncode, 0, log.read_text(encoding='utf-8')[-6000:])
                run(baseline)
                process = subprocess.Popen([executable, '--job', str(resumed)], stdout=output, stderr=output)
                try:
                    deadline = time.monotonic() + 120
                    while process.poll() is None and time.monotonic() < deadline:
                        progress = resumed.parent / 'progress.jsonl'
                        if progress.exists():
                            lines = progress.read_bytes().split(b'\n')
                            status = json.loads(lines[-2]) if len(lines) > 1 else {}
                            if status.get('phase') == 'training' and 1 <= status['microbatch'] <= 10:
                                (resumed.parent / 'pause.json').write_text('true'); break
                        time.sleep(.002)
                    self.assertEqual(process.wait(timeout=60), 75, log.read_text(encoding='utf-8')[-6000:])
                finally:
                    if process.poll() is None:
                        process.terminate(); process.wait(timeout=15)
                pointer = json.loads((resumed.parent / 'resume.json').read_text())
                state = json.loads((resumed.parent / pointer['slot'] / 'state.json').read_text())
                self.assertNotEqual(state['completed'] % 16, 0)
                saved = gguf.GGUFReader(str(resumed.parent / pointer['slot'] / 'optimizer.gguf'))
                self.assertTrue(any(t.name.startswith('Gradient for') for t in saved.tensors))
                del saved
                (resumed.parent / 'pause.json').unlink()
                run(resumed)
                def tensors(path):
                    manifest = json.loads((path.parent / 'result.json').read_text(encoding='utf-8'))
                    return {t.name: t.data.copy() for t in gguf.GGUFReader(manifest['path']).tensors}
                expected, actual = tensors(baseline), tensors(resumed)
                self.assertEqual(set(expected), set(actual))
                self.assertEqual(len(actual), 14)
                self.assertTrue(all(np.any(value) for name, value in actual.items() if name.endswith('.lora_b')))
                for name in expected:
                    np.testing.assert_array_equal(expected[name], actual[name], err_msg=name)
                (resumed.parent / 'result.json').unlink()
                run(resumed)
                for name, value in tensors(resumed).items():
                    np.testing.assert_array_equal(actual[name], value)


if __name__ == '__main__':
    unittest.main()
