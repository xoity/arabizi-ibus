#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from arabizi_ibus.linguistic_engine import ArabiziEngine


DATASET_PATH = Path(__file__).parent / "tests" / "test_dataset.json"
MAX_REFINEMENT_ROUNDS = 3


def load_dataset(path: Path) -> list[tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[tuple[str, str]] = []
    for item in payload.get("word_cases", []):
        cases.append((item["latin"], item["arabic"]))
    for item in payload.get("sentence_cases", []):
        cases.append((item["latin"], item["arabic"]))
    return cases


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
    dataset = load_dataset(DATASET_PATH)
    engine = ArabiziEngine()

    for round_idx in range(1, MAX_REFINEMENT_ROUNDS + 1):
        report = engine.validate(dataset)
        print_report(
            title=f"Round {round_idx}",
            report_total=report.total,
            report_passed=report.passed,
            failed_cases=report.failed_cases,
        )

        if report.failed == 0:
            print("\nAll tests passed.")
            return 0

        # Automatic refinement: learn strict overrides from current failures.
        for latin, expected, actual in report.failed_cases:
            if expected != actual:
                engine.refine_from_failure(latin, expected)

    final_report = engine.validate(dataset)
    print_report(
        title="Final Round",
        report_total=final_report.total,
        report_passed=final_report.passed,
        failed_cases=final_report.failed_cases,
    )
    return 0 if final_report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
