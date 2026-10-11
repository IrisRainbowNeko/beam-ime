# SPDX-License-Identifier: Apache-2.0
import importlib.util
import unittest

from prepare_dual_task import expand, split_rows


class DualDataTest(unittest.TestCase):
    def test_sentence_and_session_stay_together_before_expansion(self):
        rows = [{'target': '你好', 'session_id': 'a'}, {'target': '再见', 'session_id': 'a'},
                {'target': '你好', 'session_id': 'b'}, {'target': '明天见', 'session_id': 'b'},
                {'target': '天气很好', 'session_id': 'c'}]
        for seed in range(20):
            splits = split_rows(rows, seed, .2, .2)
            locations = {row['target']: name for name, members in splits.items() for row in members}
            self.assertEqual(len({locations[t] for t in ('你好', '再见', '明天见')}), 1)

    def test_paired_variants_retain_english_and_original_polyphone(self):
        row = {'target': '重庆用Linux', 'keys': 'chongqingyonglinux', 'translation': 'Chongqing uses Linux.', 'context': ''}
        rows = list(expand(row))
        self.assertTrue(rows)
        for ordinary, translated in zip(rows[::2], rows[1::2]):
            self.assertEqual(ordinary['keys'], translated['keys'])
            self.assertTrue(ordinary['keys'].endswith('linux'))
            self.assertEqual(translated['mode'], 'translate_en')
            self.assertTrue(ordinary['keys'].startswith('c'))

    def test_noisy_original_keys_are_retained(self):
        rows = list(expand({'target': '你好', 'keys': 'nihaoo', 'translation': 'Hello.'}))
        self.assertTrue(any(row['variant'] == 'original' and row['keys'] == 'nihaoo' for row in rows))


@unittest.skipUnless(importlib.util.find_spec('torch'), 'offline training tests require torch')
class DualLossTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('transformers'), 'tiny full-parameter training requires transformers')
    def test_tiny_joint_training_exports_both_tasks(self):
        import json
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace
        import torch
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import WhitespaceSplit
        from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM
        from keys_llm_train import train
        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vocabulary = {'[UNK]': 0, '[EOS]': 1, '你好': 2, 'Hello.': 3}
            tokenizer = Tokenizer(WordLevel(vocabulary, unk_token='[UNK]'))
            tokenizer.pre_tokenizer = WhitespaceSplit()
            fast = PreTrainedTokenizerFast(tokenizer_object=tokenizer, eos_token='[EOS]', unk_token='[UNK]')
            base = root / 'base'
            fast.save_pretrained(base)
            model = Qwen3ForCausalLM(Qwen3Config(vocab_size=4, hidden_size=16, intermediate_size=32,
                num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=2, head_dim=8, tie_word_embeddings=True))
            model.save_pretrained(base)
            rows = [{'mode': 'ime', 'keys': 'nh', 'target': '你好'},
                    {'mode': 'translate_en', 'keys': 'nh', 'target': '你好', 'translation': 'Hello.'}]
            data = root / 'data.jsonl'
            data.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
            output = root / 'trained'
            train(SimpleNamespace(model=str(base), teacher=None, teacher_weight=.25, prompt_version='keys_llm_v2',
                train=[str(data)], dev=str(data), output=str(output), device='cpu', seed=17,
                gpu_memory_fraction=None, segments=False, max_length=512, limit=None,
                batch_size=4, micro_batch_size=1, epochs=1, learning_rate=.001, weight_decay=.01,
                warmup_ratio=.03, log_every=1, save_every=0))
            trained = Qwen3ForCausalLM.from_pretrained(output / 'epoch-1')
            self.assertEqual(trained.config.beam_prompt_version, 'keys_llm_v2')
            self.assertEqual(trained.config.beam_modes, ['ime', 'translate_en'])
            history = json.loads((output / 'run-config.json').read_text())['history']
            self.assertEqual(set(history[0]['devLoss']), {'ime', 'translate_en'})
            self.assertFalse(torch.equal(model.model.embed_tokens.weight, trained.model.embed_tokens.weight))

    def test_task_mix_is_three_to_one(self):
        from keys_llm_train import task_batches
        import random
        rows = [{'mode': 'ime', 'id': i} for i in range(19)] + [{'mode': 'translate_en', 'id': i} for i in range(3)]
        batches = task_batches(rows, 16, random.Random(3))
        for batch in batches:
            self.assertEqual(sum(r['mode'] == 'ime' for r in batch), 12)
            self.assertEqual(sum(r['mode'] == 'translate_en' for r in batch), 4)
        self.assertEqual({r['id'] for batch in batches for r in batch if r['mode'] == 'ime'}, set(range(19)))

    def test_token_normalized_microbatches_preserve_gradient(self):
        import torch
        from keys_llm_train import task_loss
        torch.manual_seed(7)
        original = torch.randn(7, 11)
        labels = torch.arange(7)
        teacher = torch.randn_like(original)
        whole = original.clone().requires_grad_()
        task_loss(whole, labels, 7, .75, teacher).backward()
        micro = original.clone().requires_grad_()
        for lo, hi in ((0, 2), (2, 7)):
            task_loss(micro[lo:hi], labels[lo:hi], 7, .75, teacher[lo:hi]).backward()
        torch.testing.assert_close(whole.grad, micro.grad)
        long = original.repeat(2, 1).requires_grad_()
        self.assertAlmostEqual(task_loss(original, labels, 7, .25).item(),
                               task_loss(long, labels.repeat(2), 14, .25).item(), places=6)


if __name__ == '__main__':
    unittest.main()
