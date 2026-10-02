import unittest

from keys_llm_format import (
    build_prompt,
    build_target,
    normalize_keys,
    parse_output,
    split_pyseg,
    strict_valid,
    structural_valid,
)


class KeysLlmFormatTest(unittest.TestCase):
    def test_keys_are_spaced_and_separators_collapse(self):
        self.assertEqual(normalize_keys(" N c  sh "), "n'c'sh")
        self.assertEqual(build_prompt("n c sh"), "按键：n ' c ' s h\n")
        self.assertEqual(build_prompt("wy", "你好"), "上文：你好\n按键：w y\n")
        with self.assertRaises(ValueError):
            normalize_keys("wo2")

    def test_pyseg_round_trips_through_target_and_parser(self):
        segments = split_pyseg("ZhebMBiY")
        self.assertEqual(segments, ["zheb", "m", "bi", "y"])
        parsed = parse_output(build_target("真没必要", segments))
        self.assertEqual(parsed, {"segments": segments, "result": "真没必要"})
        self.assertEqual(parse_output(build_target("真没必要", None)), {"segments": None, "result": "真没必要"})

    def test_structural_and_strict_validity(self):
        good = parse_output(build_target("没事你先去忙", list("msnxqm")))
        self.assertTrue(structural_valid("msnxqm", good))
        self.assertTrue(strict_valid("msnxqm", good))
        wrong_reading = parse_output(build_target("没事你先去玩", list("msnxqm")))
        self.assertTrue(structural_valid("msnxqm", wrong_reading))
        self.assertFalse(strict_valid("msnxqm", wrong_reading))
        short = parse_output(build_target("没事", ["ms", "nxqm"]))
        self.assertTrue(structural_valid("msnxqm", short))
        self.assertFalse(structural_valid("msnxqm", parse_output(build_target("没事你", ["ms", "nxqm"]))))
        self.assertFalse(structural_valid("wy", parse_output("结果：wo")))

    def test_mixed_english_units(self):
        self.assertTrue(structural_valid("yongcodexp", parse_output("结果：用codex跑")))
        self.assertTrue(structural_valid("ycodexp", parse_output("结果：用Codex跑")))
        self.assertFalse(structural_valid("ycodxp", parse_output("结果：用codex跑")))
        self.assertTrue(structural_valid("deeplearningh", parse_output("结果：deep learning好")))
        self.assertFalse(structural_valid("aib", parse_output("结果：AI 不")))
        self.assertTrue(structural_valid("codex", parse_output("结果：codex")))
        mixed = parse_output(build_target("跑AI实验", ["pao", "ai", "shi", "y"]))
        self.assertTrue(structural_valid("paoaishiy", mixed))
        self.assertTrue(strict_valid("paoaishiy", mixed))
        self.assertFalse(strict_valid("paoaishiy", parse_output(build_target("跑AI世验", ["pao", "ai", "shi", "x"]))))


if __name__ == "__main__":
    unittest.main()
