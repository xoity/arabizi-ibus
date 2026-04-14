#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sqlite3
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator

SUPPORTED_DIALECTS = ("egyptian", "levantine", "gulf")
DIALECT_ALIASES = {
    "eg": "egyptian",
    "egypt": "egyptian",
    "egy": "egyptian",
    "egyptian": "egyptian",
    "lev": "levantine",
    "levant": "levantine",
    "levantine": "levantine",
    "sham": "levantine",
    "gulf": "gulf",
    "khaleeji": "gulf",
    "khaleej": "gulf",
    "gcc": "gulf",
}
TOKEN_RE = re.compile(r"[\u0600-\u06FFA-Za-z0-9']+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile Tarab corpus into compressed SQLite lexicon.")
    parser.add_argument("inputs", nargs="+", help="Input files or globs (.csv, .json, .jsonl)")
    parser.add_argument(
        "--output",
        default="arabizi_ibus/compiled_corpus.sqlite3",
        help="Output SQLite path (default: arabizi_ibus/compiled_corpus.sqlite3)",
    )
    parser.add_argument("--top-k", type=int, default=50000, help="Top words per dialect")
    parser.add_argument("--dialects", default=",".join(SUPPORTED_DIALECTS), help="Comma-separated dialect list")
    parser.add_argument("--min-bigram-count", type=int, default=2, help="Drop sparse bigrams below this count")
    parser.add_argument("--max-prefix-len", type=int, default=5, help="Max prefix depth for trie payloads")
    parser.add_argument(
        "--max-prefix-candidates",
        type=int,
        default=64,
        help="Max words stored per trie prefix bucket",
    )
    return parser.parse_args()


def normalize_dialect(value: str | None, *, filename_hint: str, allowed: set[str]) -> str | None:
    source = (value or "").strip().lower()
    if not source:
        source = filename_hint.lower()

    for key, dialect in DIALECT_ALIASES.items():
        if source == key or key in source:
            return dialect if dialect in allowed else None
    return None


def tokenize_text(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(text) if token]


def normalize_token(token: str) -> str:
    return token.strip()


def extract_tokens(record: dict) -> list[str]:
    if "tokens" in record and isinstance(record["tokens"], list):
        return [normalize_token(str(item)) for item in record["tokens"] if str(item).strip()]

    for key in ("text", "sentence", "utterance", "content"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return tokenize_text(value)

    for key in ("words", "lemmas"):
        value = record.get(key)
        if isinstance(value, list):
            return [normalize_token(str(item)) for item in value if str(item).strip()]

    word = record.get("word") or record.get("token") or record.get("current_word")
    if isinstance(word, str) and word.strip():
        return [normalize_token(word)]

    return []


def extract_explicit_bigram(record: dict) -> tuple[str, str] | None:
    prev = record.get("previous_word") or record.get("prev_word")
    curr = record.get("word") or record.get("current_word")
    if isinstance(prev, str) and isinstance(curr, str):
        prev = normalize_token(prev)
        curr = normalize_token(curr)
        if prev and curr:
            return prev, curr
    return None


def iter_input_files(inputs: Iterable[str]) -> Iterator[Path]:
    for raw in inputs:
        expanded = glob.glob(raw, recursive=True)
        if not expanded:
            expanded = [raw]
        for candidate in expanded:
            path = Path(candidate)
            if path.is_dir():
                for nested in sorted(path.rglob("*")):
                    if nested.suffix.lower() in {".csv", ".json", ".jsonl"}:
                        yield nested
            elif path.is_file() and path.suffix.lower() in {".csv", ".json", ".jsonl"}:
                yield path


def iter_records(path: Path) -> Iterator[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                yield dict(row)
        return

    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        return

    if suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    yield item
        elif isinstance(payload, dict):
            rows = payload.get("rows") or payload.get("data")
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, dict):
                        yield item
            else:
                yield payload


def pass_one_word_counts(files: list[Path], dialects: set[str]) -> tuple[dict[str, Counter[str]], Counter[str]]:
    per_dialect = {dialect: Counter() for dialect in dialects}
    totals = Counter()

    for path in files:
        hint = path.stem
        for record in iter_records(path):
            dialect = normalize_dialect(str(record.get("dialect", "")), filename_hint=hint, allowed=dialects)
            if dialect is None:
                continue

            tokens = extract_tokens(record)
            for token in tokens:
                per_dialect[dialect][token] += 1
                totals[dialect] += 1

            explicit = extract_explicit_bigram(record)
            if explicit is not None:
                prev, curr = explicit
                per_dialect[dialect][prev] += 1
                per_dialect[dialect][curr] += 1
                totals[dialect] += 2

    return per_dialect, totals


def pass_two_bigram_counts(
    files: list[Path],
    dialects: set[str],
    vocab_by_dialect: dict[str, set[str]],
) -> dict[str, Counter[tuple[str, str]]]:
    bigrams = {dialect: Counter() for dialect in dialects}

    for path in files:
        hint = path.stem
        for record in iter_records(path):
            dialect = normalize_dialect(str(record.get("dialect", "")), filename_hint=hint, allowed=dialects)
            if dialect is None:
                continue

            vocab = vocab_by_dialect[dialect]
            tokens = [token for token in extract_tokens(record) if token in vocab]
            for prev, curr in zip(tokens, tokens[1:]):
                bigrams[dialect][(prev, curr)] += 1

            explicit = extract_explicit_bigram(record)
            if explicit is not None:
                prev, curr = explicit
                if prev in vocab and curr in vocab:
                    bigrams[dialect][(prev, curr)] += 1

    return bigrams


def prepare_word_rows(
    word_counts: dict[str, Counter[str]],
    totals: Counter[str],
    *,
    top_k: int,
) -> tuple[list[tuple[str, str, str, int, float]], dict[str, set[str]], list[tuple[str, str, str, int, float]]]:
    rows: list[tuple[str, str, str, int, float]] = []
    all_counter: Counter[str] = Counter()
    vocab_by_dialect: dict[str, set[str]] = {}

    for dialect, counts in word_counts.items():
        selected = counts.most_common(top_k)
        vocab = {word for word, _ in selected}
        vocab_by_dialect[dialect] = vocab

        denominator = max(totals[dialect], 1)
        for word, count in selected:
            prefix = word[:2] if len(word) >= 2 else word
            probability = count / denominator
            rows.append((dialect, word, prefix, count, probability))
            all_counter[word] += count

    all_rows: list[tuple[str, str, str, int, float]] = []
    all_total = max(sum(all_counter.values()), 1)
    for word, count in all_counter.most_common(top_k):
        prefix = word[:2] if len(word) >= 2 else word
        all_rows.append(("all", word, prefix, count, count / all_total))

    return rows, vocab_by_dialect, all_rows


def prepare_bigram_rows(
    bigrams_by_dialect: dict[str, Counter[tuple[str, str]]],
    *,
    min_count: int,
) -> list[tuple[str, str, str, int, float]]:
    rows: list[tuple[str, str, str, int, float]] = []
    merged: Counter[tuple[str, str]] = Counter()

    for dialect, counter in bigrams_by_dialect.items():
        prev_totals: Counter[str] = Counter()
        for (prev, _curr), count in counter.items():
            if count >= min_count:
                prev_totals[prev] += count

        for (prev, curr), count in counter.items():
            if count < min_count:
                continue
            probability = count / max(prev_totals[prev], 1)
            rows.append((dialect, prev, curr, count, probability))
            merged[(prev, curr)] += count

    merged_prev_totals: Counter[str] = Counter()
    for (prev, _curr), count in merged.items():
        if count >= min_count:
            merged_prev_totals[prev] += count

    for (prev, curr), count in merged.items():
        if count < min_count:
            continue
        probability = count / max(merged_prev_totals[prev], 1)
        rows.append(("all", prev, curr, count, probability))

    return rows


def build_trie_payload_rows(
    word_rows: list[tuple[str, str, str, int, float]],
    *,
    max_prefix_len: int,
    max_prefix_candidates: int,
) -> list[tuple[str, str, bytes]]:
    buckets: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)

    for dialect, word, _prefix, _count, probability in word_rows:
        upto = min(max_prefix_len, len(word))
        for depth in range(1, upto + 1):
            buckets[(dialect, word[:depth])].append((word, probability))

    rows: list[tuple[str, str, bytes]] = []
    for (dialect, prefix), entries in buckets.items():
        entries.sort(key=lambda item: item[1], reverse=True)
        compact = entries[:max_prefix_candidates]
        payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        rows.append((dialect, prefix, zlib.compress(payload, level=9)))

    return rows


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS words (
            dialect TEXT NOT NULL,
            word TEXT NOT NULL,
            prefix TEXT NOT NULL,
            freq INTEGER NOT NULL,
            prob REAL NOT NULL,
            PRIMARY KEY (dialect, word)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_words_word ON words(word);
        CREATE INDEX IF NOT EXISTS idx_words_prefix ON words(prefix, dialect);

        CREATE TABLE IF NOT EXISTS bigrams (
            dialect TEXT NOT NULL,
            prev_word TEXT NOT NULL,
            curr_word TEXT NOT NULL,
            count INTEGER NOT NULL,
            prob REAL NOT NULL,
            PRIMARY KEY (dialect, prev_word, curr_word)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_bigrams_prev ON bigrams(prev_word, dialect);

        CREATE TABLE IF NOT EXISTS trie_prefix (
            dialect TEXT NOT NULL,
            prefix TEXT NOT NULL,
            payload BLOB NOT NULL,
            PRIMARY KEY (dialect, prefix)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID;
        """
    )


def compile_corpus(args: argparse.Namespace) -> Path:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(set(iter_input_files(args.inputs)))
    if not files:
        raise ValueError("No input files found.")

    dialects = {part.strip().lower() for part in args.dialects.split(",") if part.strip()}
    unsupported = dialects.difference(SUPPORTED_DIALECTS)
    if unsupported:
        raise ValueError(f"Unsupported dialects: {', '.join(sorted(unsupported))}")

    word_counts, totals = pass_one_word_counts(files, dialects)
    words_rows, vocab_by_dialect, all_word_rows = prepare_word_rows(word_counts, totals, top_k=args.top_k)

    bigrams_by_dialect = pass_two_bigram_counts(files, dialects, vocab_by_dialect)
    bigram_rows = prepare_bigram_rows(bigrams_by_dialect, min_count=args.min_bigram_count)

    merged_word_rows = words_rows + all_word_rows
    trie_rows = build_trie_payload_rows(
        merged_word_rows,
        max_prefix_len=args.max_prefix_len,
        max_prefix_candidates=args.max_prefix_candidates,
    )

    if output_path.exists():
        output_path.unlink()

    conn = sqlite3.connect(output_path)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-20000")
        conn.execute("PRAGMA page_size=4096")

        create_schema(conn)

        conn.executemany(
            "INSERT INTO words(dialect, word, prefix, freq, prob) VALUES (?, ?, ?, ?, ?)",
            merged_word_rows,
        )
        conn.executemany(
            "INSERT INTO bigrams(dialect, prev_word, curr_word, count, prob) VALUES (?, ?, ?, ?, ?)",
            bigram_rows,
        )
        conn.executemany(
            "INSERT INTO trie_prefix(dialect, prefix, payload) VALUES (?, ?, ?)",
            trie_rows,
        )

        metadata = [
            ("top_k", str(args.top_k)),
            ("dialects", ",".join(sorted(dialects))),
            ("min_bigram_count", str(args.min_bigram_count)),
            ("max_prefix_len", str(args.max_prefix_len)),
            ("word_rows", str(len(merged_word_rows))),
            ("bigram_rows", str(len(bigram_rows))),
            ("trie_rows", str(len(trie_rows))),
        ]
        conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", metadata)

        conn.commit()
        conn.execute("VACUUM")
        conn.execute("PRAGMA optimize")
    finally:
        conn.close()

    return output_path


def main() -> None:
    args = parse_args()
    database_path = compile_corpus(args)
    size_mb = database_path.stat().st_size / (1024 * 1024)

    print(f"Compiled SQLite corpus: {database_path}")
    print(f"Size: {size_mb:.2f} MB")
    if size_mb > 15.0:
        print("Warning: output is larger than 15MB. Increase --min-bigram-count or lower --top-k.")


if __name__ == "__main__":
    main()
