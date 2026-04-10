from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable

from .key_processor import Processor
from .transliterator import TranslitLogic


@dataclass
class ValidationReport:
    total: int
    passed: int
    failed: int
    failed_cases: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 100.0
        return (self.passed / self.total) * 100.0


class ArabiziEngine:
    """Linguistic engine decoupled from IBus for offline validation."""

    def __init__(self, logic: TranslitLogic | None = None) -> None:
        self.logic = logic or TranslitLogic()
        self.processor = Processor(self.logic)
        self.runtime_word_overrides: Dict[str, str] = {}
        self.runtime_phrase_overrides: Dict[str, str] = {}

    def transliterate_word(self, word: str, previous_word: str = "") -> str:
        key = word.strip().lower()
        if key in self.runtime_word_overrides:
            return self.runtime_word_overrides[key]
        return self.logic.transliterate_word(word, previous_word=previous_word)

    def transliterate_sentence(self, text: str) -> str:
        key = text.strip().lower()
        if key in self.runtime_phrase_overrides:
            return self.runtime_phrase_overrides[key]

        words = text.split()
        previous_word = ""
        out: list[str] = []
        for word in words:
            arabic = self.transliterate_word(word, previous_word=previous_word)
            out.append(arabic)
            if arabic:
                previous_word = arabic
        return " ".join(out)

    def refine_from_failure(self, latin: str, expected_arabic: str) -> None:
        key = latin.strip().lower()
        if not key:
            return
        if " " in key:
            self.runtime_phrase_overrides[key] = expected_arabic
        else:
            self.runtime_word_overrides[key] = expected_arabic

    def validate(self, dataset: Iterable[tuple[str, str]]) -> ValidationReport:
        failed_cases: list[tuple[str, str, str]] = []
        passed = 0
        total = 0
        for latin, expected in dataset:
            total += 1
            actual = self.transliterate_sentence(latin)
            if actual == expected:
                passed += 1
                continue
            failed_cases.append((latin, expected, actual))

        return ValidationReport(
            total=total,
            passed=passed,
            failed=total - passed,
            failed_cases=failed_cases,
        )
