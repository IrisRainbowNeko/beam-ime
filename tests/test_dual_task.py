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
    def test_validation_sampling_keeps_both_tasks_even_when_grouped(self):
        from keys_llm_train import validation_examples
        rows = [{'mode': 'ime', 'id': i} for i in range(4001)] + [{'mode': 'translate_en', 'id': 5000}]
        selected = validation_examples(rows, 17)
        self.assertEqual(sum(r['mode'] == 'ime' for r in selected), 2000)
        self.assertEqual(sum(r['mode'] == 'translate_en' for r in selected), 1)
        self.assertEqual(selected, validation_examples(rows, 17))

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
            for version, scheme in (('keys_llm_v2', 'task'), ('keys_llm_v2', 'field'), ('keys_llm_v3', 'field')):
                with self.subTest(version=version, scheme=scheme):
                    output = root / (version + '-' + scheme)
                    train(SimpleNamespace(model=str(base), teacher=str(base), teacher_weight=.25, prompt_version=version,
                        loss_scheme=scheme, train=[str(data)], dev=str(data), output=str(output), device='cpu', seed=17,
                        gpu_memory_fraction=None, segments=False, max_length=512, limit=None,
                        batch_size=4, micro_batch_size=1, epochs=1, learning_rate=.001, weight_decay=.01,
                        warmup_ratio=.03, log_every=1, save_every=0))
                    trained = Qwen3ForCausalLM.from_pretrained(output / 'epoch-1')
                    self.assertEqual(trained.config.beam_prompt_version, version)
                    self.assertEqual(trained.config.beam_modes, ['ime', 'translate_en'])
                    config = json.loads((output / 'run-config.json').read_text())
                    self.assertEqual(config['lossScheme'], scheme)
                    self.assertEqual(set(config['history'][0]['devLoss']),
                                     {'ime', 'translate_en', 'translate_en_english', 'translate_en_comment'})
                    self.assertFalse(torch.equal(model.model.embed_tokens.weight, trained.model.embed_tokens.weight))

    def test_field_loss_accumulation_and_length_normalization(self):
        import torch
        from keys_llm_train import ENGLISH, COMMENT, field_loss
        torch.manual_seed(9)
        logits = torch.randn(5, 9)
        labels = torch.arange(5)
        fields = torch.tensor([ENGLISH, ENGLISH, ENGLISH, COMMENT, COMMENT])
        totals = {ENGLISH: 3, COMMENT: 2}
        whole = logits.clone().requires_grad_()
        field_loss(whole, labels, fields, totals).backward()
        micro = logits.clone().requires_grad_()
        for start, stop in ((0, 2), (2, 4), (4, 5)):
            field_loss(micro[start:stop], labels[start:stop], fields[start:stop], totals).backward()
        torch.testing.assert_close(whole.grad, micro.grad)
        duplicated = torch.cat((logits[:3], logits[:3], logits[3:]))
        repeated_labels = torch.cat((labels[:3], labels[:3], labels[3:]))
        repeated_fields = torch.cat((fields[:3], fields[:3], fields[3:]))
        self.assertAlmostEqual(field_loss(logits, labels, fields, totals).item(),
            field_loss(duplicated, repeated_labels, repeated_fields, {ENGLISH: 6, COMMENT: 2}).item(), places=6)

    def test_field_labels_follow_content_when_order_changes(self):
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import WhitespaceSplit
        from transformers import PreTrainedTokenizerFast
        from keys_llm_train import encode, ENGLISH, COMMENT
        tokenizer = Tokenizer(WordLevel({'[UNK]': 0, '[EOS]': 1, '你好': 2, 'Hello.': 3}, unk_token='[UNK]'))
        tokenizer.pre_tokenizer = WhitespaceSplit()
        fast = PreTrainedTokenizerFast(tokenizer_object=tokenizer, eos_token='[EOS]', unk_token='[UNK]')
        row = {'mode': 'translate_en', 'keys': 'nh', 'target': '你好', 'translation': 'Hello.'}
        for version, expected in (('keys_llm_v2', [ENGLISH, COMMENT, COMMENT]),
                                  ('keys_llm_v3', [COMMENT, ENGLISH, COMMENT])):
            encoded = encode(fast, row, False, 512, version)
            actual = [field for field, label in zip(encoded['fields'], encoded['labels']) if label != -100]
            self.assertEqual(actual, expected)

    def test_evaluation_normalizes_chinese_first_candidates(self):
        from keys_llm_eval import ranked_results
        candidates = ['结果：你好\tHello.', '结果：你好\tHello.', '结果：你好\tHi.']
        self.assertEqual(ranked_results('nh', candidates, 'structural', 'translate_en', 'keys_llm_v3'),
                         ['Hello.', 'Hi.'])

    def test_translation_metrics_do_not_mistake_english_output_for_mixed_input(self):
        from keys_llm_eval import summarize
        case = {'mode': 'translate_en', 'variant': 'full', 'source': '你好', 'target': 'Hello.',
                'comment': '你好', 'english': 'Hello.', 'paired': True,
                'ranks': {'raw': 1, 'structural': 1, 'strict': None}}
        metrics = summarize([case])
        self.assertNotIn('withEnglish', metrics)
        self.assertEqual(metrics['translate_en:full']['commentP@1'], 1)

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
