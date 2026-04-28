#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from arabizi_ibus.linguistic_engine import ArabiziEngine


DEFAULT_DATASET_PATH = Path(__file__).parent / "tests" / "corpora" / "arabizi_parallel_corpus.jsonl"
FALLBACK_DATASET_PATH = Path(__file__).parent / "tests" / "test_dataset.json"
LATIN_RE = re.compile(r"[A-Za-z0-9']")
ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run strict transliteration regression on corpus dataset.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"Dataset path (.json or .jsonl). Default: {DEFAULT_DATASET_PATH}",
    )
    parser.add_argument("--max-cases", type=int, default=0, help="Limit number of cases (0 = all).")
    parser.add_argument(
        "--checked-only",
        action="store_true",
        help="For JSONL corpus, use only rows where checked=true.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.0,
        help="Fail if pass rate is below this percentage.",
    )
    return parser.parse_args()


def load_dataset(path: Path, *, checked_only: bool, max_cases: int) -> list[tuple[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    cases: list[tuple[str, str]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if checked_only and not bool(row.get("checked", False)):
                    continue
                latin = str(row.get("latin", "")).strip()
                arabic = str(row.get("arabic", "")).strip()
                if not latin or not arabic:
                    continue
                cases.append((latin, arabic))
                if max_cases > 0 and len(cases) >= max_cases:
                    break
        return cases

    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload.get("word_cases", []):
        cases.append((item["latin"], item["arabic"]))
    for item in payload.get("sentence_cases", []):
        cases.append((item["latin"], item["arabic"]))
    if max_cases > 0:
        return cases[:max_cases]
    return cases


def corpus_quality(dataset: list[tuple[str, str]]) -> dict[str, float]:
    total = len(dataset)
    latin_good = sum(1 for latin, _ in dataset if LATIN_RE.search(latin))
    arabic_good = sum(1 for _, arabic in dataset if ARABIC_RE.search(arabic))
    if total == 0:
        return {"latin_ratio": 0.0, "arabic_ratio": 0.0}
    return {
        "latin_ratio": (latin_good / total) * 100.0,
        "arabic_ratio": (arabic_good / total) * 100.0,
    }


def print_report(title: str, report_total: int, report_passed: int, failed_cases: list[tuple[str, str, str]]) -> None:
    failed = report_total - report_passed
    pass_rate = 100.0 if report_total == 0 else (report_passed / report_total) * 100.0
    print(f"\n{title}")
    print(f"Total: {report_total}")
    print(f"Passed: {report_passed}")
    print(f"Failed: {failed}")
    print(f"Pass rate: {pass_rate:.2f}%")
    if failed_cases:
        print("Sample failures:")
        for latin, expected, actual in failed_cases[:10]:
            print(f"  - {latin} -> expected: {expected} | actual: {actual}")


def main() -> int:
    args = parse_args()
    dataset_path = args.dataset if args.dataset.exists() else FALLBACK_DATASET_PATH
    dataset = load_dataset(dataset_path, checked_only=args.checked_only, max_cases=args.max_cases)
    quality = corpus_quality(dataset)

    print(f"Dataset: {dataset_path}")
    print(f"Cases: {len(dataset)}")
    print(f"Latin source ratio: {quality['latin_ratio']:.2f}%")
    print(f"Arabic target ratio: {quality['arabic_ratio']:.2f}%")

    if quality["latin_ratio"] < 98.0 or quality["arabic_ratio"] < 98.0:
        print("Corpus quality guard failed.")
        return 1

    engine = ArabiziEngine()
    final_report = engine.validate(dataset)
    print_report(
        title="Corpus Regression Report",
        report_total=final_report.total,
        report_passed=final_report.passed,
        failed_cases=final_report.failed_cases,
    )
    if final_report.pass_rate < args.min_pass_rate:
        print(f"Pass rate below threshold ({args.min_pass_rate:.2f}%).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
