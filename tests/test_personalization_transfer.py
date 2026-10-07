import unittest

from personalization_transfer import CompositionalMemory, PROFILES, align, curriculum, keys_for, probes, protect_personal_candidate, row


class PersonalizationTransferTest(unittest.TestCase):
    def test_unknown_words_cannot_be_injected(self):
        memory = CompositionalMemory()
        query = row("星澜引擎", "新组件", "initial")
        self.assertEqual(memory.candidates(query, ["心理预期"]), ["心理预期"])

    def test_learned_word_transfers_to_new_code(self):
        memory = CompositionalMemory()
        memory.learn(row("星澜引擎", "新组件"))
        query = row("星澜引擎", "运行项目", "mixed1")
        self.assertIn("星澜引擎", memory.candidates(query, []))

    def test_word_can_be_composed_inside_an_unseen_sentence(self):
        memory = CompositionalMemory()
        memory.learn(row("星澜引擎", "新组件"))
        query = row("检查星澜引擎的日志", "检查项目")
        self.assertIn(query["target"], memory.candidates(query, ["检查星蓝引擎的日志"]))

    def test_dictionary_does_not_read_target(self):
        memory = CompositionalMemory()
        memory.learn(row("星澜引擎", "新组件"))
        query = row("星澜引擎", "运行项目", "initial")
        self.assertEqual(memory.candidates(query, ["心理预期"]),
                         memory.candidates({**query, "target": "另一种答案"}, ["心理预期"]))

    def test_personal_candidate_stays_visible_without_forcing_top1(self):
        memory = CompositionalMemory()
        memory.learn(row("星澜引擎", "新组件"))
        query = row("星澜引擎", "开发环境", "initial")
        original = ["心理预期", "心理一起", "心里预期", "心理引起", "心里一起"]
        protected = protect_personal_candidate(memory, query, original, original)
        self.assertEqual(len(protected), 5)
        self.assertEqual(protected[0], original[0])
        self.assertIn("星澜引擎", protected)

    def test_personal_slot_does_not_duplicate_an_existing_candidate(self):
        memory = CompositionalMemory()
        memory.learn(row("星澜引擎", "新组件"))
        query = row("星澜引擎", "开发环境", "initial")
        original = ["心理预期", "星澜引擎"]
        self.assertEqual(protect_personal_candidate(memory, query, original, original), original)

    def test_test_sentences_and_slot_values_are_held_out(self):
        for profile in PROFILES.values():
            trained = {r["target"] for r in curriculum(profile)}
            for query in probes(profile):
                if query["group"] in ("sentence_transfer", "template_transfer"):
                    self.assertNotIn(query["target"], trained)

    def test_mixed_codes_align_and_differ_from_training_codes(self):
        for profile in PROFILES.values():
            for term in profile["terms"]:
                for mode in ("mixed0", "mixed1"):
                    keys = keys_for(term, mode)
                    self.assertNotIn(keys, (keys_for(term, "full"), keys_for(term, "initial")))
                    self.assertIsNotNone(align(term, keys))


if __name__ == "__main__":
    unittest.main()
