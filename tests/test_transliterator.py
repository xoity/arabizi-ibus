import unittest

from arabizi_ibus.transliterator import TranslitLogic


class TransliteratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logic = TranslitLogic()

    def test_greedy_cluster_sliding_window(self) -> None:
        self.assertEqual(self.logic.transliterate_word("kaan"), "كان")
        self.assertEqual(self.logic.transliterate_word("kan"), "كان")
        self.assertEqual(self.logic.transliterate_word("khaan"), "خان")

    def test_vowel_handling(self) -> None:
        self.assertEqual(self.logic.transliterate_word("ka"), "كا")
        self.assertEqual(self.logic.transliterate_word("umi"), "أُمي")

    def test_prefix_and_exception_handling(self) -> None:
        self.assertEqual(self.logic.transliterate_word("alkitaab"), "الكتاب")
        self.assertEqual(self.logic.transliterate_word("assalam"), "السلام")
        self.assertEqual(self.logic.transliterate_word("arragel"), "الرجل")
        self.assertEqual(self.logic.transliterate_word("allah"), "الله")
        self.assertEqual(self.logic.transliterate_word("alsalamaleykom"), "السلام عليكم")

    def test_modern_arabizi_numeric_substitutions(self) -> None:
        self.assertEqual(self.logic.transliterate_word("3arab"), "عرب")
        self.assertEqual(self.logic.transliterate_word("7'aleej"), "خليج")
        self.assertEqual(self.logic.transliterate_word("9a7"), "صح")

    def test_contextual_shadda_collapse(self) -> None:
        self.assertEqual(self.logic.transliterate_word("yalla"), "يلا")

    def test_bigram_postprocessor(self) -> None:
        self.assertEqual(self.logic.transliterate("ya makan"), "يا مكان")

    def test_dictionary_fallback(self) -> None:
        self.assertEqual(self.logic.transliterate_word("mkan"), "مكان")

    def test_candidate_suggestions(self) -> None:
        self.assertEqual(self.logic.suggest_candidates("keef"), ["كيف", "كيفك", "كيفكم"])

    def test_dialect_switch(self) -> None:
        self.assertEqual(self.logic.transliterate_word("g"), "غ")
        self.logic.set_dialect("egyptian")
        self.assertEqual(self.logic.transliterate_word("g"), "ج")


if __name__ == "__main__":
    unittest.main()
