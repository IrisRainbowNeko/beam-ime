import unittest

from mt_format import prompt


class MtFormatTest(unittest.TestCase):
    def test_matches_runtime_prefix(self):
        # engine.cpp: zh_en_prefix(context) + Chinese + "\n英文："
        self.assertEqual(prompt('明天见'), '任务：中译英\n中文：明天见\n英文：')
        self.assertEqual(prompt('一起吃饭', '我们明天'), '任务：中译英\n上文：我们明天\n中文：一起吃饭\n英文：')

    def test_context_keeps_last_64_chars(self):
        self.assertEqual(prompt('好', '甲' * 10 + '乙' * 64), f'任务：中译英\n上文：{"乙" * 64}\n中文：好\n英文：')


if __name__ == '__main__':
    unittest.main()
