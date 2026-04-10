from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class LexiconRules:
    prefixes: Dict[str, str]
    mappings: Dict[str, str]
    exceptions: Dict[str, str]
    candidates: Dict[str, list[str]]
    dialects: Dict[str, Dict[str, str]]
    dialect_labels: Dict[str, str]
    vowels: str
    initial_vowels: Dict[str, str]
    terminal_vowels: Dict[str, str]
    shadda_enabled: bool
    shadda_mark: str
    collapse_double_consonants: bool
    shadda_consonants: str
    normalization_vowels: str
    dictionary_path: str
    fallback_distance: int
    bigram_overrides: Dict[str, str]


@dataclass(frozen=True)
class TokenState:
    is_prefix: bool = False
    has_guttural_digit: bool = False
    vowel_count: int = 0


def load_lexicon(lexicon_path: str | Path | None = None) -> LexiconRules:
    if lexicon_path is None:
        lexicon_path = Path(__file__).with_name("lexicon.json")
    path = Path(lexicon_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    vowels = payload.get("vowels", {})
    shadda = payload.get("shadda", {})
    normalization = payload.get("normalization", {})
    postprocessor = payload.get("postprocessor", {})
    return LexiconRules(
        prefixes=payload.get("prefixes", {}),
        mappings=payload.get("mappings", {}),
        exceptions=payload.get("exceptions", {}),
        candidates=payload.get("candidates", {}),
        dialects=payload.get("dialects", {"default": {}}),
        dialect_labels=payload.get("dialect_labels", {"default": "Default"}),
        vowels=vowels.get("letters", "aeiou"),
        initial_vowels=vowels.get("word_initial", {}),
        terminal_vowels=vowels.get("terminal", {}),
        shadda_enabled=shadda.get("enabled", False),
        shadda_mark=shadda.get("mark", "\u0651"),
        collapse_double_consonants=shadda.get("collapse_double_consonants", True),
        shadda_consonants=shadda.get("consonants", "bcdfghjklmnpqrstvwxyz"),
        normalization_vowels=normalization.get("long_vowel_letters", "aeiou"),
        dictionary_path=postprocessor.get("dictionary_path", "common_words_1000.json"),
        fallback_distance=postprocessor.get("fallback_max_distance", 1),
        bigram_overrides=postprocessor.get("bigram_overrides", {}),
    )


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        curr = [i]
        for j, char_b in enumerate(b, start=1):
            substitution = prev[j - 1] + (char_a != char_b)
            deletion = prev[j] + 1
            insertion = curr[j - 1] + 1
            curr.append(min(substitution, deletion, insertion))
        prev = curr
    return prev[-1]


class PostProcessor:
    def __init__(self, rules: LexiconRules, base_path: Path) -> None:
        self.rules = rules
        self.base_path = base_path
        self.dictionary_words = self._load_dictionary_words()
        self.words_by_initial: Dict[str, list[str]] = {}
        for word in self.dictionary_words:
            initial = word[:1]
            self.words_by_initial.setdefault(initial, []).append(word)

    def _load_dictionary_words(self) -> list[str]:
        dictionary_file = self.base_path / self.rules.dictionary_path
        if not dictionary_file.exists():
            return []
        with dictionary_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            words = payload.get("words", [])
        elif isinstance(payload, list):
            words = payload
        else:
            words = []

        seen: set[str] = set()
        unique: list[str] = []
        for word in words:
            if not isinstance(word, str):
                continue
            if word in seen:
                continue
            seen.add(word)
            unique.append(word)
        return unique

    def snap_word(self, previous_word: str, latin_word: str, arabic_word: str) -> str:
        bigram_key = f"{previous_word}|{latin_word.lower()}"
        if bigram_key in self.rules.bigram_overrides:
            return self.rules.bigram_overrides[bigram_key]

        if not self._should_dictionary_snap(latin_word, arabic_word):
            return arabic_word
        return self._dictionary_fallback(arabic_word)

    @staticmethod
    def _should_dictionary_snap(latin_word: str, arabic_word: str) -> bool:
        if len(arabic_word) < 3:
            return False
        if any(ch in arabic_word for ch in "اويىأإآؤئ"):
            return False
        if len(latin_word) < 3:
            return False
        return True

    def _dictionary_fallback(self, word: str) -> str:
        if not self.dictionary_words or not word:
            return word
        if word in self.dictionary_words:
            return word

        pool = self.words_by_initial.get(word[:1], self.dictionary_words)
        best_word = word
        best_distance = self.rules.fallback_distance + 1
        for candidate in pool:
            distance = _edit_distance(word, candidate)
            if distance < best_distance:
                best_distance = distance
                best_word = candidate
            if best_distance == 0:
                break

        if best_distance <= self.rules.fallback_distance:
            return best_word
        return word


class TranslitLogic:
    def __init__(self, rules: LexiconRules | None = None, dialect: str = "default") -> None:
        self.rules = rules or load_lexicon()
        self.dialect = dialect if dialect in self.rules.dialects else "default"
        self._base_path = Path(__file__).parent
        self.post_processor = PostProcessor(self.rules, self._base_path)

    def set_dialect(self, dialect: str) -> None:
        if dialect in self.rules.dialects:
            self.dialect = dialect

    def available_dialects(self) -> Dict[str, str]:
        return dict(self.rules.dialect_labels)

    def is_prefix_token(self, token: str) -> bool:
        return token.lower() in self.rules.prefixes

    def prefix_for(self, token: str) -> str:
        return self.rules.prefixes.get(token.lower(), "")

    def suggest_candidates(self, token: str, previous_word: str = "") -> list[str]:
        if not token:
            return []
        candidates = self.rules.candidates.get(token.lower())
        if candidates:
            return list(candidates)

        generated = self.transliterate_word(token, previous_word=previous_word)
        if generated and generated != token:
            return [generated]
        return []

    def transliterate(self, text: str) -> str:
        if not text:
            return ""

        words = text.split()
        if len(words) <= 1:
            return self.transliterate_word(text)

        previous = ""
        output: list[str] = []
        for word in words:
            arabic = self.transliterate_word(word, previous_word=previous)
            if arabic:
                previous = arabic
            output.append(arabic)
        return " ".join(output)

    def transliterate_word(self, word: str, apply_prefix: bool = True, previous_word: str = "") -> str:
        if not word:
            return ""

        lowered = self._normalize_latin(word)
        exception = self.rules.exceptions.get(lowered)
        if exception:
            return exception

        state = self._analyze_token_state(lowered)

        if apply_prefix:
            for prefix in sorted(self.rules.prefixes, key=len, reverse=True):
                if lowered.startswith(prefix) and len(word) > len(prefix):
                    tail = lowered[len(prefix) :]
                    return f"{self.rules.prefixes[prefix]}{self.transliterate_word(tail, apply_prefix=False, previous_word=previous_word)}"

        output: list[str] = []
        index = 0
        while index < len(lowered):
            if self._is_double_consonant(lowered, index):
                mapped = self._map_chunk(
                    lowered[index],
                    at_word_start=(index == 0),
                    at_word_end=(index + 2 == len(lowered)),
                    previous_char=lowered[index - 1] if index > 0 else "",
                    next_char=lowered[index + 2] if index + 2 < len(lowered) else "",
                    state=state,
                )
                if mapped is not None:
                    if mapped:
                        output.append(mapped)
                        if self.rules.shadda_enabled:
                            output.append(self.rules.shadda_mark)
                    index += 2
                    continue

            matched = False
            for window in (3, 2, 1):
                if index + window > len(lowered):
                    continue
                chunk = lowered[index : index + window]
                mapped = self._map_chunk(
                    chunk,
                    at_word_start=(index == 0),
                    at_word_end=(index + window == len(lowered)),
                    previous_char=lowered[index - 1] if index > 0 else "",
                    next_char=lowered[index + window] if index + window < len(lowered) else "",
                    state=state,
                )
                if mapped is None:
                    continue
                if mapped:
                    output.append(mapped)
                index += window
                matched = True
                break

            if matched:
                continue

            output.append(lowered[index])
            index += 1

        raw_word = "".join(output)
        return self.post_processor.snap_word(previous_word, lowered, raw_word)

    def _normalize_latin(self, word: str) -> str:
        lowered = word.lower().strip()
        if not lowered:
            return lowered

        normalized: list[str] = []
        last_char = ""
        run_length = 0
        for char in lowered:
            if char == last_char:
                run_length += 1
            else:
                run_length = 1
                last_char = char

            if char in self.rules.normalization_vowels and run_length > 2:
                continue
            normalized.append(char)
        return "".join(normalized)

    def _analyze_token_state(self, token: str) -> TokenState:
        return TokenState(
            is_prefix=token in self.rules.prefixes,
            has_guttural_digit=any(digit in token for digit in ("2", "3", "5", "7", "9")),
            vowel_count=sum(1 for char in token if char in self.rules.vowels),
        )

    def _map_chunk(
        self,
        chunk: str,
        *,
        at_word_start: bool,
        at_word_end: bool,
        previous_char: str,
        next_char: str,
        state: TokenState,
    ) -> str | None:
        dialect_map = self.rules.dialects.get(self.dialect, {})
        if chunk in dialect_map:
            return dialect_map[chunk]

        if len(chunk) == 1 and chunk in self.rules.vowels:
            if at_word_start:
                return self.rules.initial_vowels.get(chunk, "")
            if at_word_end:
                return self.rules.terminal_vowels.get(chunk, "")

            if chunk == "a" and previous_char and next_char:
                if (
                    state.vowel_count == 1
                    and previous_char not in self.rules.vowels
                    and next_char not in self.rules.vowels
                    and not previous_char.isdigit()
                    and not next_char.isdigit()
                ):
                    return "ا"

            return ""

        if chunk in self.rules.mappings:
            return self.rules.mappings[chunk]

        return None

    def _is_double_consonant(self, text: str, index: int) -> bool:
        if not self.rules.collapse_double_consonants:
            return False
        if index + 1 >= len(text):
            return False
        char = text[index]
        return char == text[index + 1] and char in self.rules.shadda_consonants


class ArabiziTransliterator(TranslitLogic):
    pass


MappingRules = LexiconRules
load_mapping = load_lexicon
