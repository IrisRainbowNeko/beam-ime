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


class TranslationCorpusTest(unittest.TestCase):
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


class TrainingPipelineTest(unittest.TestCase):
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
