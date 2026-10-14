# SPDX-License-Identifier: Apache-2.0
import gzip
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from keys_llm_format import units
from translation_corpus import compact, focused_rows, lccc_rows, messages, reservoir
from run_dual_training import main as run_pipeline
from run_dual_ablation import commands as ablation_commands
from run_fragment_training import commands as fragment_commands


class TranslationCorpusTest(unittest.TestCase):
    def test_fragments_use_only_preceding_text(self):
        import random
        from fragment_corpus import fragments
        parent = {'parent_text': '我们今天使用Linux训练模型', 'source': 'test', 'session_id': 'a'}
        rows = list(fragments(parent, random.Random(17)))
        self.assertEqual(''.join(r['target'] for r in rows), parent['parent_text'])
        for row in rows:
            self.assertEqual(row['context'], parent['parent_text'][:row['fragment_start']][-64:])

    def test_teacher_wait_status_and_context_conversion(self):
        from fragment_corpus import teacher_result
        row = {'target': '的', 'context': '我们讨论', 'context_kind': 'english'}
        result = teacher_result(row, json.dumps({'translation_status': 'need_more_input', 'translation': '',
                                                'english_context': 'We discuss'}))
        self.assertEqual(result['context'], 'We discuss')
        self.assertEqual(result['translation_status'], 'need_more_input')
        fenced = '```json\n' + json.dumps({'translation_status': 'ready', 'translation': 'many', 'english_context': 'I think'}) + '\n```'
        self.assertEqual(teacher_result(row, fenced)['translation'], 'many')
        with self.assertRaises(ValueError):
            teacher_result(row, json.dumps({'translation_status': 'ready', 'translation': 'Do其他', 'english_context': ''}))

    def test_fragments_do_not_split_public_word_boundaries(self):
        import random
        from fragment_corpus import fragments
        text = '我们讨论涉及美国'
        boundaries = [(0, 2), (2, 4), (4, 6), (6, 8)]
        for seed in range(10):
            rows = list(fragments({'parent_text': text, 'word_boundaries': boundaries}, random.Random(seed)))
            self.assertEqual(''.join(row['target'] for row in rows), text)
            self.assertTrue(all(row['fragment_start'] % 2 == 0 and row['fragment_end'] % 2 == 0 for row in rows))

    def test_chinese_compaction_preserves_english_words(self):
        self.assertEqual(compact('我 在 学 deep learning 。'), '我在学deep learning。')

    def test_conversation_id_is_stable_across_duplicate_dialogs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'lccc.jsonl.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as stream:
                for _ in range(2):
                    stream.write(json.dumps(['我们 明天 再 聊', '请 不要 忘记 开会']) + '\n')
            rows = list(lccc_rows(path, 17))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['session_id'], rows[1]['session_id'])
            self.assertTrue(all(units(row['target']) for row in rows))

    def test_sampling_is_deduplicated_and_reproducible(self):
        rows = [{'target': '测试文本' + ch} for ch in '甲乙丙丁'] * 4
        first = reservoir(iter(rows), 3, 19)
        self.assertEqual(first, reservoir(iter(rows), 3, 19))
        self.assertEqual(len({row['target'] for row in first}), 3)

    def test_authored_rows_fit_ime_and_include_negation_mixed_and_time(self):
        rows = list(focused_rows())
        self.assertTrue(all(units(row['target']) for row in rows))
        for text in ('不是所有电脑都支持Vulkan', '明天上午九点开会', '并不是不想参加会议'):
            self.assertTrue(any(row['target'] == text for row in rows))

    def test_teacher_receives_separate_context_and_source(self):
        prompt = messages({'context': '讨论驱动', 'target': '不用安装CUDA'})
        self.assertEqual(json.loads(prompt[1]['content']), {'context': '讨论驱动', 'text': '不用安装CUDA'})

    def test_semantic_score_counts_empty_outputs_and_separates_chinese_errors(self):
        from translation_corpus import judgment_metrics, parse_judgment
        good = parse_judgment('{"usable":true,"errors":[],"reason":"Faithful phrase."}')
        wrong = parse_judgment('{"usable":false,"errors":["entity"],"reason":"Wrong country."}')
        rows = [dict(source='马来西亚', displayComment='马来西亚', displayEnglish='Malaysia', judgment=good),
                dict(source='马来西亚', displayComment='马来西亚', displayEnglish='Singapore', judgment=wrong),
                dict(source='马来西亚', displayComment='马来西亚', displayEnglish=None),
                dict(source='新加坡', displayComment='马来西亚', displayEnglish='Malaysia', judgment=good)]
        metrics = judgment_metrics(rows)
        self.assertEqual(metrics['endToEnd']['rate'], .25)
        self.assertEqual(metrics['conditionalEnglish']['rate'], 1/3)
        self.assertEqual(metrics['noCandidate'], 1)
        self.assertEqual(metrics['errors']['entity'], 1)
        self.assertLess(metrics['endToEnd']['wilson95'][0], .25)
        self.assertGreater(metrics['endToEnd']['wilson95'][1], .25)
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            parse_judgment('{"usable":true,"errors":["omission"],"reason":"missing object"}')


class TrainingPipelineTest(unittest.TestCase):
    def test_fragment_runs_keep_training_budget_equal_and_english_dpo_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'decoding.json').write_text('{"strategy":"greedy_first"}')
            (root / 'comparison.json').write_text('{"checkpoint":"/winner","decoding":"greedy_first"}')
            args = SimpleNamespace(output=root, corpus=Path('/corpus'), teacher='/teacher', baseline='/B', ime='/ime',
                                   gpu_memory_fraction=.65, scorer_python='/scorer', comet_checkpoint='/comet', llama_source='/llama')
            phases = dict(fragment_commands(args))
            for label, scheme in (('D1', 'field'), ('D2', 'sample_field')):
                command = phases[label + '-train']
                for flag, value in (('--model', '/ime'), ('--teacher', '/ime'), ('--learning-rate', '5e-6'),
                                    ('--epochs', '2'), ('--batch-size', '256'), ('--loss-scheme', scheme)):
                    self.assertEqual(command[command.index(flag) + 1], value)
                self.assertIn('--fragment-sampling', command)
            dpo = phases['D3-train']
            self.assertEqual(dpo[dpo.index('--max-steps') + 1], '200')
            self.assertEqual(dpo[dpo.index('--model') + 1], str(root / 'D2/epoch-2'))

    def test_preferences_include_both_longer_and_shorter_errors(self):
        from fragment_preferences import negatives, build
        values = negatives('We do not use Linux for training.')
        self.assertTrue(any(len(text) < len('We do not use Linux for training.') for _, text in values))
        self.assertTrue(any(kind == 'entity' and 'Windows' in text for kind, text in values))
        rows = [{'mode': 'translate_en', 'keys': 'test', 'target': f'测试{i}', 'translation': f'The value is {i}.'} for i in range(8)]
        result = build(rows, count=8)
        self.assertEqual(len({row['target'] for row in result}), 8)
        self.assertTrue(all(row['chosen'] != row['rejected'] for row in result))

    def test_ablation_controls_teacher_data_budget_and_loss(self):
        args = SimpleNamespace(ime='/ime', base='/base', reference='/reference', data=Path('/data'),
            output=Path('/output'), micro_batch_size=8, gpu_memory_fraction=.25,
            scorer_python='/scorer/python', comet_checkpoint='/comet/model.ckpt',
            comet_model='test-comet', comet_revision='test-revision')
        phases = list(ablation_commands(args))
        training = phases[:3]
        self.assertEqual([name for name, command in training], ['A-train', 'B-train', 'C-train'])
        for (name, command), model, version in zip(training, ('/ime', '/ime', '/base'),
                ('keys_llm_v2', 'keys_llm_v3', 'keys_llm_v3')):
            values = dict(zip(command[3::2], command[4::2]))
            self.assertEqual(values['--model'], model)
            self.assertEqual(values['--prompt-version'], version)
            self.assertEqual(values['--teacher'], '/ime')
            self.assertEqual(values['--train'], '/data/train.jsonl')
            self.assertEqual(values['--loss-scheme'], 'field')
            self.assertEqual((values['--epochs'], values['--batch-size'], values['--seed']), ('2', '256', '20260930'))
        evaluations = [name for name, command in phases if name.endswith('-evaluate')]
        self.assertEqual(len(evaluations), 8)
        self.assertEqual(sum(name.endswith('-semantic') for name, command in phases), 7)

    def run_case(self, output, results):
        args = ['run_dual_training.py', '--corpus', '/corpus', '--teacher', '/teacher',
                '--teacher-id', 'test-teacher', '--teacher-revision', 'test-revision',
                '--model', '/model', '--output', str(output)]
        with patch('sys.argv', args), patch('run_dual_training.subprocess.run', side_effect=results) as run:
            code = run_pipeline()
        return code, run

    def test_failure_stops_later_stages_and_records_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'run'
            code, run = self.run_case(output, [SimpleNamespace(returncode=0), SimpleNamespace(returncode=7)])
            self.assertEqual(code, 7)
            self.assertEqual(run.call_count, 2)
            state = json.loads((output / 'status.json').read_text())
            self.assertEqual((state['state'], state['phase'], state['exit_code']), ('failed', 'dataset', 7))
            self.assertFalse((output / 'train.log').exists())

    def test_completion_includes_joint_and_old_model_evaluation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'run'
            code, run = self.run_case(output, [SimpleNamespace(returncode=0)] * 5)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((output / 'status.json').read_text())['state'], 'complete')
            calls = [call.args[0] for call in run.call_args_list]
            evaluations = [call for call in calls if any(value.endswith('keys_llm_eval.py') for value in call)]
            self.assertEqual(len(evaluations), 2)
            self.assertEqual({call[call.index('--checkpoint') + 1] for call in evaluations},
                             {'/model', str(output / 'run' / 'epoch-2')})

    def test_launch_error_is_visible_in_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'run'
            with self.assertRaisesRegex(OSError, 'test launch failure'):
                self.run_case(output, OSError('test launch failure'))
            state = json.loads((output / 'status.json').read_text())
            self.assertEqual((state['state'], state['error']), ('failed', 'test launch failure'))


if __name__ == '__main__':
    unittest.main()
