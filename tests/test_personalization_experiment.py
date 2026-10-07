import unittest

from personalization_experiment import PersonalMemory, replay_stream


class PersonalizationExperimentTest(unittest.TestCase):
    def test_current_target_is_not_inserted_before_feedback(self):
        row = {"keys": "sj", "target": "数据", "context": "代码", "category": "controlled"}
        cases = replay_stream([row] * 4, [["手机"]] * 4, PersonalMemory())
        self.assertEqual(cases[0]["top"], ["手机"])
        self.assertIn("数据", cases[1]["top"])
        self.assertEqual(cases[-1]["top"][0], "数据")

    def test_user_memories_are_isolated(self):
        row = {"keys": "sj", "target": "数据", "context": "代码"}
        first, second = PersonalMemory(), PersonalMemory()
        replay_stream([row] * 4, [["手机"]] * 4, first)
        self.assertEqual(first.rank(row, ["手机"])[0][0], "数据")
        self.assertEqual(second.rank(row, ["手机"])[0], ["手机"])

    def test_probe_does_not_teach_memory(self):
        memory = PersonalMemory()
        row = {"keys": "sj", "target": "数据", "context": "代码"}
        replay_stream([row] * 4, [["手机"]] * 4, memory, learn=False)
        self.assertEqual(memory.clock, 0)
        self.assertEqual(memory.rank(row, ["手机"])[0], ["手机"])

    def test_ranking_does_not_depend_on_current_target(self):
        memory = PersonalMemory()
        row = {"keys": "sj", "target": "数据", "context": "代码"}
        replay_stream([row], [["手机"]], memory)
        self.assertEqual(memory.rank(row, ["手机"]), memory.rank({**row, "target": "时间"}, ["手机"]))

    def test_memory_does_not_offer_a_different_key_sequence(self):
        memory = PersonalMemory()
        row = {"keys": "sj", "target": "数据", "context": "代码"}
        replay_stream([row] * 4, [["手机"]] * 4, memory)
        self.assertEqual(memory.rank({**row, "keys": "sjk"}, ["数据库"])[0], ["数据库"])

    def test_many_corrections_cannot_reverse_an_unfamiliar_input(self):
        memory = PersonalMemory()
        for index in range(100):
            row = {"keys": "cs", "target": f"selection-{index}", "context": ""}
            replay_stream([row], [["a", "b", "c", "d", row["target"]]], memory)
        row = {"keys": "xm", "target": "项目", "context": ""}
        self.assertEqual(memory.rank(row, ["小米", "项目", "姓名"])[0], ["小米", "项目", "姓名"])


if __name__ == "__main__":
    unittest.main()
