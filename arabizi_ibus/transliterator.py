from __future__ import annotations

import json
import math
import sqlite3
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class LexiconRules:
    prefixes: Dict[str, str]
    mappings: Dict[str, Any]
    exceptions: Dict[str, str]
    candidates: Dict[str, list[str]]
    names_lexicon: Dict[str, str]
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


@dataclass(frozen=True)
class DecodePath:
    index: int
    output: str
    score: float


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
        names_lexicon=payload.get("names_lexicon", {}),
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
    SQL_PREFIX_LIMIT = 96
    BIGRAM_ROW_LIMIT = 256

    def __init__(self, rules: LexiconRules, base_path: Path) -> None:
        self.rules = rules
        self.base_path = base_path
        self._sqlite_conn: sqlite3.Connection | None = None
        self._sqlite_enabled = False
        self._freq_cache: Dict[str, float] = {}
        self._trie_cache: Dict[str, list[tuple[str, float]]] = {}
        self._bigram_cache: Dict[str, Dict[str, float]] = {}

        db_path = self._resolve_dictionary_db_path()
        if db_path is not None:
            self._sqlite_enabled = self._open_sqlite_backend(db_path)

        self.dictionary_words: list[str] = []
        self.word_frequencies: Dict[str, float] = {}
        self.words_by_initial: Dict[str, list[str]] = {}
        if not self._sqlite_enabled:
            self.dictionary_words, self.word_frequencies = self._load_dictionary_words()
            for word in self.dictionary_words:
                initial = word[:1]
                self.words_by_initial.setdefault(initial, []).append(word)

    def _resolve_dictionary_db_path(self) -> Path | None:
        configured = (self.base_path / self.rules.dictionary_path).resolve()
        candidates: list[Path] = [configured]

        if configured.suffix.lower() == ".json":
            candidates.append(configured.with_suffix(".sqlite3"))
            candidates.append(configured.with_suffix(".db"))

        candidates.extend(
            (
                (self.base_path / "compiled_corpus.sqlite3").resolve(),
                (self.base_path / "tarab_corpus.sqlite3").resolve(),
                (self.base_path / "corpus.sqlite3").resolve(),
            )
        )

        seen: set[Path] = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            if path.exists() and path.is_file():
                return path
        return None

    def _open_sqlite_backend(self, path: Path) -> bool:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA temp_store=MEMORY")
            if not self._sqlite_has_table(conn, "words"):
                conn.close()
                return False
            self._sqlite_conn = conn
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _sqlite_has_table(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _load_dictionary_words(self) -> tuple[list[str], Dict[str, float]]:
        dictionary_file = self.base_path / self.rules.dictionary_path
        if not dictionary_file.exists():
            return [], {}
        with dictionary_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            words = payload.get("words", [])
        elif isinstance(payload, list):
            words = payload
        else:
            words = []

        extracted: list[tuple[str, float | None]] = []
        for entry in words:
            if isinstance(entry, str):
                extracted.append((entry, None))
                continue
            if not isinstance(entry, dict):
                continue
            token = entry.get("word") or entry.get("token") or entry.get("text")
            if not isinstance(token, str):
                continue
            extracted.append((token, self._extract_explicit_frequency(entry)))

        seen: set[str] = set()
        unique: list[str] = []
        frequencies: Dict[str, float] = {}
        total = max(len(extracted), 1)
        for index, (word, explicit_frequency) in enumerate(extracted):
            if not isinstance(word, str):
                continue
            word = word.strip()
            if not word:
                continue
            if word in seen:
                continue
            seen.add(word)
            unique.append(word)
            if explicit_frequency is not None and explicit_frequency > 0:
                weight = 1.0 + math.log1p(explicit_frequency)
            else:
                rank = index + 1
                weight = (total - rank + 1) / total
            frequencies[word] = max(weight, 0.0)
        return unique, frequencies

    @staticmethod
    def _extract_explicit_frequency(entry: Dict[str, object]) -> float | None:
        for key in ("frequency", "freq", "count", "weight"):
            value = entry.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)

        rank = entry.get("rank")
        if isinstance(rank, (int, float)) and rank > 0:
            return 1.0 / float(rank)
        return None

    def frequency_score(self, word: str) -> float:
        if self._sqlite_enabled and self._sqlite_conn is not None:
            cached = self._freq_cache.get(word)
            if cached is not None:
                return cached

            row = self._sqlite_conn.execute(
                "SELECT prob FROM words WHERE word=? ORDER BY prob DESC LIMIT 1",
                (word,),
            ).fetchone()
            score = float(row[0]) if row else 0.0
            self._freq_cache[word] = score
            return score
        return self.word_frequencies.get(word, 0.0)

    def snap_word(self, previous_word: str, latin_word: str, arabic_word: str) -> str:
        bigram_key = f"{previous_word}|{latin_word.lower()}"
        if bigram_key in self.rules.bigram_overrides:
            return self.rules.bigram_overrides[bigram_key]

        if not self._should_dictionary_snap(latin_word, arabic_word):
            return arabic_word
        if self._sqlite_enabled and self._sqlite_conn is not None:
            return self._sqlite_dictionary_fallback(previous_word, arabic_word)
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

    def _sqlite_dictionary_fallback(self, previous_word: str, word: str) -> str:
        conn = self._sqlite_conn
        if conn is None or not word:
            return word

        exact = conn.execute("SELECT 1 FROM words WHERE word=? LIMIT 1", (word,)).fetchone()
        if exact is not None:
            return word

        candidates = self._sqlite_prefix_candidates(word)
        if not candidates:
            return word

        previous_scores = self._sqlite_bigram_scores(previous_word)
        best = word
        best_score = float("-inf")
        best_distance = self.rules.fallback_distance + 2
        distance_cap = self.rules.fallback_distance + 1

        for candidate, probability in candidates:
            distance = _edit_distance(word, candidate)
            if distance > distance_cap:
                continue

            score = (probability * 1.35) + (previous_scores.get(candidate, 0.0) * 2.0) - (distance * 1.2)
            if score > best_score or (score == best_score and distance < best_distance):
                best = candidate
                best_score = score
                best_distance = distance

        if best_distance <= self.rules.fallback_distance:
            return best
        return word

    def _sqlite_prefix_candidates(self, word: str) -> list[tuple[str, float]]:
        conn = self._sqlite_conn
        if conn is None:
            return []

        max_prefix = min(5, len(word))
        for length in range(max_prefix, 0, -1):
            prefix = word[:length]
            if prefix in self._trie_cache:
                return self._trie_cache[prefix]

            row = conn.execute(
                "SELECT payload FROM trie_prefix WHERE dialect=? AND prefix=? LIMIT 1",
                ("all", prefix),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT payload FROM trie_prefix WHERE prefix=? ORDER BY dialect='all' DESC LIMIT 1",
                    (prefix,),
                ).fetchone()
            if row is None:
                continue

            unpacked = self._decode_prefix_payload(row[0])
            if unpacked:
                self._trie_cache[prefix] = unpacked
                return unpacked

        prefix = word[:2] if len(word) >= 2 else word
        rows = conn.execute(
            "SELECT word, prob FROM words WHERE prefix=? ORDER BY prob DESC LIMIT ?",
            (prefix, self.SQL_PREFIX_LIMIT),
        ).fetchall()
        return [(str(item[0]), float(item[1])) for item in rows]

    @staticmethod
    def _decode_prefix_payload(payload: bytes) -> list[tuple[str, float]]:
        try:
            decoded = zlib.decompress(payload)
            data = json.loads(decoded.decode("utf-8"))
        except (TypeError, ValueError, zlib.error):
            return []

        if not isinstance(data, list):
            return []

        candidates: list[tuple[str, float]] = []
        for entry in data:
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            token, probability = entry
            if not isinstance(token, str):
                continue
            try:
                numeric_probability = float(probability)
            except (TypeError, ValueError):
                continue
            candidates.append((token, numeric_probability))
        return candidates

    def _sqlite_bigram_scores(self, previous_word: str) -> Dict[str, float]:
        prev = previous_word.strip()
        if not prev:
            return {}
        if prev in self._bigram_cache:
            return self._bigram_cache[prev]

        conn = self._sqlite_conn
        if conn is None:
            return {}

        rows = conn.execute(
            "SELECT curr_word, prob FROM bigrams WHERE prev_word=? ORDER BY prob DESC LIMIT ?",
            (prev, self.BIGRAM_ROW_LIMIT),
        ).fetchall()
        scores = {str(word): float(probability) for word, probability in rows}
        self._bigram_cache[prev] = scores
        return scores

    def __del__(self) -> None:
        if self._sqlite_conn is not None:
            try:
                self._sqlite_conn.close()
            except sqlite3.Error:
                pass


class NameProcessor:
    COMMON_NAME_PATTERNS = {
        "mohammad": "محمد",
        "mohamed": "محمد",
        "muhammad": "محمد",
        "mohammed": "محمد",
        "abu": "أبو",
    }

    def __init__(self, rules: LexiconRules) -> None:
        self.rules = rules
        self.names = {key.lower(): value for key, value in self.rules.names_lexicon.items()}

    def override_name(self, token: str) -> str | None:
        normalized = token.lower().strip()
        if not normalized:
            return None
        override = self.names.get(normalized)
        if override:
            return override
        if normalized in self.COMMON_NAME_PATTERNS:
            return self.COMMON_NAME_PATTERNS[normalized]
        return None

    def candidate_bonus(self, latin_word: str, candidate: str) -> float:
        token = latin_word.lower().strip()
        if not token or not candidate:
            return 0.0
        override = self.override_name(token)
        if override and candidate == override:
            return 3.25
        if token.startswith("abu") and candidate.startswith("أبو"):
            return 1.4
        if token.startswith("moh") or token.startswith("muh"):
            if candidate == "محمد":
                return 2.25
        return 0.0

    def special_letter_bias(
        self,
        chunk: str,
        target: str,
        previous_char: str,
        next_char: str,
        token: str,
    ) -> float:
        if chunk != "d" or target != "ض":
            return 0.0

        bonus = 0.0
        if next_char in self.rules.vowels or next_char in {"o", "u", "a"}:
            bonus += 0.8
        if token.lower().startswith(("moh", "muh", "mu")):
            bonus += 0.6
        if token.lower().startswith("abu") and target == "ض":
            bonus += 0.3
        return bonus


class TranslitLogic:
    BEAM_WIDTH = 24
    MAX_FINAL_CANDIDATES = 12
    PREFIX_SCORE_BONUS = 2.4
    LITERAL_WORD_SCORE = -2.4
    LITERAL_CHAR_SCORE = -0.85
    AMBIGUITY_PENALTY = 0.35
    VOCATIVE_CONTEXT_WORDS = {"ya", "yaa"}
    SUN_LETTER_CLUSTERS = ("sh", "th", "dh", "t", "d", "r", "z", "s", "n", "l")
    AMBIGUOUS_CHUNK_ALTERNATIVES = {
        "7": ("ه",),
        "5": ("ه",),
        "8": ("ك",),
        "9": ("س",),
    }

    def __init__(self, rules: LexiconRules | None = None, dialect: str = "default", user_adapter: Any | None = None) -> None:
        self.rules = rules or load_lexicon()
        self.dialect = dialect if dialect in self.rules.dialects else "default"
        self._base_path = Path(__file__).parent
        self.user_adapter = user_adapter
        self.post_processor = PostProcessor(self.rules, self._base_path)
        self.name_processor = NameProcessor(self.rules)

    def set_dialect(self, dialect: str) -> None:
        if dialect in self.rules.dialects:
            self.dialect = dialect

    def available_dialects(self) -> Dict[str, str]:
        return dict(self.rules.dialect_labels)

    def is_prefix_token(self, token: str) -> bool:
        return token.lower() in self.rules.prefixes

    def prefix_for(self, token: str) -> str:
        return self.rules.prefixes.get(token.lower(), "")

    def suggest_candidates(self, token: str, previous_word: str = "", beam_width: int | None = None) -> list[str]:
        if not token:
            return []
        candidates = self.rules.candidates.get(token.lower())
        if candidates:
            return list(candidates)

        generated = self._generate_ranked_candidates(token, apply_prefix=True, previous_word=previous_word, beam_width=beam_width)
        if not generated:
            return []

        lowered = self._normalize_latin(token)
        filtered = [candidate for candidate in generated if candidate != lowered]
        if filtered:
            return filtered[:5]
        return generated[:1]

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

    def transliterate_word(self, word: str, apply_prefix: bool = True, previous_word: str = "", beam_width: int | None = None) -> str:
        if not word:
            return ""

        generated = self._generate_ranked_candidates(
            word,
            apply_prefix=apply_prefix,
            previous_word=previous_word,
            beam_width=beam_width,
        )
        if not generated:
            return self._normalize_latin(word)
        return generated[0]

    def _generate_ranked_candidates(self, word: str, apply_prefix: bool, previous_word: str, beam_width: int | None = None) -> list[str]:
        lowered = self._normalize_latin(word)
        if not lowered:
            return []

        override = self.name_processor.override_name(lowered)
        if override:
            return [override]

        scored: list[tuple[str, float]] = []
        for variant, variant_bonus in self._expand_word_variants(lowered):
            exception = self.rules.exceptions.get(variant)
            if exception:
                scored.append((exception, 4.5 + variant_bonus))
                continue

            token_state = self._analyze_token_state(variant)
            scored.extend(
                self._decode_variant_paths(
                    variant,
                    state=token_state,
                    apply_prefix=apply_prefix,
                    previous_word=previous_word,
                    variant_bonus=variant_bonus,
                    beam_width=beam_width,
                )
            )

        scored.append((lowered, self.LITERAL_WORD_SCORE))
        return self._rank_candidates(latin_word=lowered, previous_word=previous_word, raw_candidates=scored)

    def _expand_word_variants(self, token: str) -> list[tuple[str, float]]:
        scored_variants: Dict[str, float] = {token: 0.0}

        compact = token.replace("-", "")
        if compact:
            scored_variants[compact] = max(scored_variants.get(compact, float("-inf")), 0.1)

        normalized_article = self._normalize_solar_article(compact)
        if normalized_article:
            current_score = scored_variants.get(normalized_article, float("-inf"))
            scored_variants[normalized_article] = max(current_score, 0.6)

        ranked = sorted(scored_variants.items(), key=lambda item: item[1], reverse=True)
        return ranked

    def _normalize_solar_article(self, token: str) -> str:
        if len(token) < 4:
            return token
        if token.startswith(("al", "el")):
            return token
        if token[:1] not in {"a", "e"}:
            return token

        after_initial = token[1:]
        for cluster in sorted(self.SUN_LETTER_CLUSTERS, key=len, reverse=True):
            if not after_initial.startswith(cluster):
                continue
            remainder = after_initial[len(cluster) :]
            if remainder.startswith(cluster):
                return f"al{remainder}"
        return token

    def _decode_variant_paths(
        self,
        token: str,
        *,
        state: TokenState,
        apply_prefix: bool,
        previous_word: str,
        variant_bonus: float,
        beam_width: int | None = None,
    ) -> list[tuple[str, float]]:
        scored: list[tuple[str, float]] = []

        if apply_prefix:
            for prefix in sorted(self.rules.prefixes, key=len, reverse=True):
                if not token.startswith(prefix):
                    continue
                if len(token) <= len(prefix):
                    continue
                tail = token[len(prefix) :]
                tail_state = self._analyze_token_state(tail)
                for candidate, score in self._decode_with_beam(tail, tail_state, beam_width=beam_width):
                    snapped_tail = self.post_processor.snap_word(previous_word, tail, candidate)
                    prefixed = f"{self.rules.prefixes[prefix]}{snapped_tail}"
                    scored.append((prefixed, score + self.PREFIX_SCORE_BONUS + variant_bonus))

        for candidate, score in self._decode_with_beam(token, state, beam_width=beam_width):
            scored.append((candidate, score + variant_bonus))
        return scored

    def _decode_with_beam(self, token: str, state: TokenState, beam_width: int | None = None) -> list[tuple[str, float]]:
        if not token:
            return [("", 0.0)]

        beam: list[DecodePath] = [DecodePath(index=0, output="", score=0.0)]
        completed: list[DecodePath] = []
        max_steps = max(1, len(token) * 2)

        for _ in range(max_steps):
            if not beam:
                break

            next_paths: Dict[tuple[int, str], float] = {}
            active = False

            for path in beam:
                if path.index >= len(token):
                    completed.append(path)
                    continue

                active = True
                index = path.index
                previous_char = token[index - 1] if index > 0 else ""

                if self._is_double_consonant(token, index):
                    next_char = token[index + 2] if index + 2 < len(token) else ""
                    options = self._map_chunk_candidates(
                        token[index],
                        at_word_start=(index == 0),
                        at_word_end=(index + 2 == len(token)),
                        previous_char=previous_char,
                        next_char=next_char,
                        state=state,
                        token=token,
                    )
                    for mapped, score_bonus in options:
                        rendered = mapped
                        if rendered and self.rules.shadda_enabled:
                            rendered = f"{rendered}{self.rules.shadda_mark}"
                        self._merge_path(
                            next_paths,
                            index=index + 2,
                            output=f"{path.output}{rendered}",
                            score=path.score + score_bonus + 0.9,
                        )

                for window in (3, 2, 1):
                    if index + window > len(token):
                        continue
                    chunk = token[index : index + window]
                    next_char = token[index + window] if index + window < len(token) else ""
                    options = self._map_chunk_candidates(
                        chunk,
                        at_word_start=(index == 0),
                        at_word_end=(index + window == len(token)),
                        previous_char=previous_char,
                        next_char=next_char,
                        state=state,
                        token=token,
                    )
                    if not options:
                        continue

                    for mapped, score_bonus in options:
                        self._merge_path(
                            next_paths,
                            index=index + window,
                            output=f"{path.output}{mapped}",
                            score=path.score + score_bonus,
                        )

                self._merge_path(
                    next_paths,
                    index=index + 1,
                    output=f"{path.output}{token[index]}",
                    score=path.score + self.LITERAL_CHAR_SCORE,
                )

            if not active:
                break
            beam = self._prune_paths(next_paths, beam_width)

        for path in beam:
            if path.index >= len(token):
                completed.append(path)

        if not completed:
            return [(token, self.LITERAL_WORD_SCORE)]

        best_per_output: Dict[str, float] = {}
        for path in completed:
            current = best_per_output.get(path.output)
            if current is None or path.score > current:
                best_per_output[path.output] = path.score

        ranked = sorted(best_per_output.items(), key=lambda item: item[1], reverse=True)
        return ranked[: self.MAX_FINAL_CANDIDATES]

    @staticmethod
    def _merge_path(paths: Dict[tuple[int, str], float], *, index: int, output: str, score: float) -> None:
        key = (index, output)
        existing = paths.get(key)
        if existing is None or score > existing:
            paths[key] = score

    def _prune_paths(self, paths: Dict[tuple[int, str], float], beam_width: int | None = None) -> list[DecodePath]:
        if not paths:
            return []
        beam_limit = beam_width if beam_width is not None else self.BEAM_WIDTH
        ranked = sorted(paths.items(), key=lambda item: item[1], reverse=True)
        return [
            DecodePath(index=index, output=output, score=score)
            for (index, output), score in ranked[: beam_limit]
        ]

    def _rank_candidates(
        self,
        *,
        latin_word: str,
        previous_word: str,
        raw_candidates: list[tuple[str, float]],
    ) -> list[str]:
        if not raw_candidates:
            return [latin_word]

        best_scores: Dict[str, float] = {}
        for candidate, decode_score in raw_candidates:
            if not candidate:
                continue

            snapped = self.post_processor.snap_word(previous_word, latin_word, candidate)
            final_score = decode_score + self._candidate_score(
                latin_word=latin_word,
                previous_word=previous_word,
                candidate=snapped,
            )
            existing = best_scores.get(snapped)
            if existing is None or final_score > existing:
                best_scores[snapped] = final_score

        if not best_scores:
            return [latin_word]

        ranked = sorted(best_scores.items(), key=lambda item: (-item[1], len(item[0])))
        return [word for word, _ in ranked[: self.MAX_FINAL_CANDIDATES]]

    def predict_ghost_suffix(
        self,
        token: str,
        *,
        previous_word: str = "",
        current_preview: str = "",
        candidates: list[str] | None = None,
        beam_width: int | None = None,
    ) -> str:
        if not token or not current_preview:
            return ""

        if candidates is None:
            candidates = self.suggest_candidates(token, previous_word=previous_word, beam_width=beam_width)
        if len(candidates) != 1:
            return ""

        top = candidates[0]
        if top.startswith(current_preview) and len(top) > len(current_preview):
            return top[len(current_preview) :]
        return ""

    def _candidate_score(self, *, latin_word: str, previous_word: str, candidate: str) -> float:
        score = self.post_processor.frequency_score(candidate) * 1.3
        score += self._vocative_context_bonus(previous_word, candidate)
        if self.user_adapter is not None:
            score += self.user_adapter.get_weight(candidate) * 0.9
        score += self.name_processor.candidate_bonus(latin_word, candidate)

        if self.rules.collapse_double_consonants and not self.rules.shadda_enabled:
            duplicate_run_count = sum(
                1
                for index in range(1, len(candidate))
                if candidate[index] == candidate[index - 1] and candidate[index] not in "اويى"
            )
            if duplicate_run_count:
                score -= 0.45 * duplicate_run_count

        latin_char_count = sum(1 for char in candidate if char.isascii() and (char.isalpha() or char.isdigit()))
        if latin_char_count:
            score -= 0.65 * latin_char_count

        if candidate == latin_word:
            score -= 0.8
        return score

    def _vocative_context_bonus(self, previous_word: str, candidate: str) -> float:
        prev = previous_word.strip()
        if not prev:
            return 0.0
        if prev != "يا" and prev.lower() not in self.VOCATIVE_CONTEXT_WORDS:
            return 0.0

        bonus = 0.3
        if candidate.startswith("ال"):
            bonus += 0.45
        if self.post_processor.frequency_score(candidate) > 0:
            bonus += 0.5
        if all(not (char.isascii() and (char.isalpha() or char.isdigit())) for char in candidate):
            bonus += 0.2
        return bonus

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
        token: str = "",
    ) -> str | None:
        options = self._map_chunk_candidates(
            chunk,
            at_word_start=at_word_start,
            at_word_end=at_word_end,
            previous_char=previous_char,
            next_char=next_char,
            state=state,
            token=token,
        )
        if not options:
            return None
        return options[0][0]

    def _map_chunk_candidates(
        self,
        chunk: str,
        *,
        at_word_start: bool,
        at_word_end: bool,
        previous_char: str,
        next_char: str,
        state: TokenState,
        token: str,
    ) -> list[tuple[str, float]]:
        dialect_map = self.rules.dialects.get(self.dialect, {})
        if chunk in dialect_map:
            base = 1.8 + (len(chunk) - 1) * 0.25
            return [(dialect_map[chunk], base)]

        if len(chunk) == 1 and chunk in self.rules.vowels:
            if at_word_start:
                mapped = self.rules.initial_vowels.get(chunk, "")
                return [(mapped, 0.65 if mapped else -0.2)]
            if at_word_end:
                mapped = self.rules.terminal_vowels.get(chunk, "")
                return [(mapped, 0.55 if mapped else -0.25)]

            if chunk == "a" and previous_char and next_char:
                if (
                    state.vowel_count == 1
                    and previous_char not in self.rules.vowels
                    and next_char not in self.rules.vowels
                    and not previous_char.isdigit()
                    and not next_char.isdigit()
                ):
                    return [("ا", 0.35), ("", -0.1)]

            return [("", -0.1)]

        if chunk in self.rules.mappings:
            mapped = self.rules.mappings[chunk]
            base_score = 1.2 + (len(chunk) - 1) * 1.8
            options: list[tuple[str, float]] = []
            if isinstance(mapped, list):
                for index, item in enumerate(mapped):
                    bias = self.name_processor.special_letter_bias(chunk, item, previous_char, next_char, token)
                    penalty = 0.0 if index == 0 else 0.85
                    options.append((item, base_score + bias - penalty))
            elif isinstance(mapped, str):
                options.append((mapped, base_score))
            else:
                options.append((str(mapped), base_score))

            for alt in self.AMBIGUOUS_CHUNK_ALTERNATIVES.get(chunk, ()):
                if any(opt[0] == alt for opt in options):
                    continue
                options.append((alt, base_score - self.AMBIGUITY_PENALTY))
            return options

        return []

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
