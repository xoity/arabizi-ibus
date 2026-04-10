import unittest

from arabizi_ibus.key_processor import KeyProcessor


def run_stream(processor: KeyProcessor, events: list[str]) -> tuple[str, object]:
    committed: list[str] = []
    last_result = None
    for event in events:
        if event == "<TOGGLE>":
            result = processor.toggle_bypass_mode()
        elif event == "<ESC>":
            result = processor.handle_escape()
        elif event == "<BS>":
            result = processor.handle_backspace()
        elif event.startswith("<CAND:"):
            index = int(event.split(":", 1)[1].rstrip(">"))
            result = processor.select_candidate(index)
        else:
            result = processor.handle_char(event)
        last_result = result

        if result.commit_text:
            committed.append(result.commit_text)
    return "".join(committed), last_result


class KeyProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = KeyProcessor()

    def test_terminator_commits_transliterated_word(self) -> None:
        committed, _ = run_stream(self.processor, list("hala "))
        self.assertEqual(committed, "هلا ")

    def test_prefix_is_joined_with_next_word(self) -> None:
        committed, _ = run_stream(self.processor, list("al kitaab "))
        self.assertEqual(committed, "الكتاب ")

    def test_double_space_after_prefix_keeps_space(self) -> None:
        committed, _ = run_stream(self.processor, list("al  "))
        self.assertEqual(committed, "ال ")

    def test_bypass_toggle_keeps_literal_numbers(self) -> None:
        events = ["<TOGGLE>", "2", "0", "2", "6", " ", "<TOGGLE>"] + list("3alam ")
        committed, _ = run_stream(self.processor, events)
        self.assertEqual(committed, "2026 علم ")

    def test_backspace_reveals_latin_buffer_for_correction(self) -> None:
        run_stream(self.processor, list("hala"))
        result = self.processor.handle_backspace()
        self.assertEqual(result.preedit_text, "hal")

    def test_escape_reveals_latin_buffer(self) -> None:
        _, result = run_stream(self.processor, list("keef") + ["<ESC>"])
        self.assertTrue(result.consumed)
        self.assertEqual(result.preedit_text, "keef")

    def test_candidate_generation(self) -> None:
        _, result = run_stream(self.processor, list("keef"))
        self.assertEqual(result.candidates, ["كيف", "كيفك", "كيفكم"])

    def test_candidate_selection_commits(self) -> None:
        committed, _ = run_stream(self.processor, list("keef") + ["<CAND:1>"])
        self.assertEqual(committed, "كيفك")

    def test_focus_out_flushes_active_buffer(self) -> None:
        run_stream(self.processor, ["7", "b"])
        result = self.processor.focus_out()
        self.assertEqual(result.commit_text, "حب")

    def test_bigram_context_across_words(self) -> None:
        committed, _ = run_stream(self.processor, list("ya makan "))
        self.assertEqual(committed, "يا مكان ")


if __name__ == "__main__":
    unittest.main()
