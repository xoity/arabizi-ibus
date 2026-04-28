#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DATASET_ID = "bashartalafha/Arabizi-Transliteration"
CORPUS_XLSX_URL = (
    "https://raw.githubusercontent.com/bashartalafha/Arabizi-Transliteration/master/"
    "Arabizi-Arabic%20Parallel%20corpora.xlsx"
)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class CorpusRow:
    latin: str
    arabic: str
    checked: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download high-quality Arabizi->Arabic corpus for regression testing."
    )
    parser.add_argument(
        "--output",
        default="tests/corpora/arabizi_parallel_corpus.jsonl",
        help="Output JSONL path (default: tests/corpora/arabizi_parallel_corpus.jsonl)",
    )
    parser.add_argument(
        "--metadata-output",
        default="tests/corpora/arabizi_parallel_corpus.meta.json",
        help="Metadata JSON path (default: tests/corpora/arabizi_parallel_corpus.meta.json)",
    )
    parser.add_argument("--download-url", default=CORPUS_XLSX_URL, help="Corpus XLSX URL override.")
    return parser.parse_args()


def fetch_binary(url: str, *, max_retries: int = 6) -> bytes:
    headers = {"User-Agent": "arabizi-ibus-corpus-downloader/1.0"}
    for attempt in range(max_retries):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == max_retries - 1:
                raise
            delay = 2.0 * (attempt + 1)
            print(f"Retrying after HTTP {exc.code} in {delay:.1f}s...")
            time.sleep(delay)
        except urllib.error.URLError:
            if attempt == max_retries - 1:
                raise
            delay = 2.0 * (attempt + 1)
            print(f"Retrying after network error in {delay:.1f}s...")
            time.sleep(delay)
    raise RuntimeError("Failed to download corpus file.")


def _si_to_text(si: ET.Element) -> str:
    parts: list[str] = []
    for node in si.iter():
        if node.tag.endswith("}t") and node.text:
            parts.append(node.text)
    return "".join(parts)


def _parse_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_si_to_text(si) for si in root.findall("x:si", XML_NS)]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value_node = cell.find("x:v", XML_NS)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        index = int(raw)
        return shared[index] if 0 <= index < len(shared) else ""
    return raw


def parse_corpus_rows(xlsx_bytes: bytes) -> list[CorpusRow]:
    rows: list[CorpusRow] = []
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as archive:
        shared = _parse_shared_strings(archive)
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        sheet_data = root.find("x:sheetData", XML_NS)
        if sheet_data is None:
            return rows

        for row in sheet_data.findall("x:row", XML_NS):
            values: dict[str, str] = {}
            for cell in row.findall("x:c", XML_NS):
                ref = cell.attrib.get("r", "")
                col = "".join(char for char in ref if char.isalpha())
                if col in {"A", "B", "C"}:
                    values[col] = _cell_value(cell, shared).strip()

            latin = values.get("A", "")
            arabic = values.get("B", "")
            checked_raw = values.get("C", "")
            if latin.lower() == "arabize" and arabic.lower() == "arabic":
                continue
            if not latin or not arabic:
                continue
            rows.append(CorpusRow(latin=latin, arabic=arabic, checked=checked_raw in {"1", "1.0"}))
    return rows


def write_jsonl(path: Path, rows: list[CorpusRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = {
                "latin": row.latin,
                "arabic": row.arabic,
                "checked": row.checked,
                "source_dataset": DATASET_ID,
            }
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_metadata(path: Path, rows: list[CorpusRow], *, download_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_rows = sum(1 for row in rows if row.checked)
    payload = {
        "dataset_id": DATASET_ID,
        "download_url": download_url,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows_total_raw": len(rows),
        "rows_kept": len(rows),
        "rows_checked": checked_rows,
        "rows_unchecked": len(rows) - checked_rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    metadata_path = Path(args.metadata_output)

    xlsx_bytes = fetch_binary(args.download_url)
    rows = parse_corpus_rows(xlsx_bytes)
    if not rows:
        print("No rows parsed from corpus workbook.")
        return 1

    write_jsonl(output_path, rows)
    write_metadata(metadata_path, rows, download_url=args.download_url)

    checked_rows = sum(1 for row in rows if row.checked)
    print(f"Downloaded dataset: {DATASET_ID}")
    print(f"Saved rows: {len(rows)}")
    print(f"Checked rows: {checked_rows}")
    print(f"JSONL: {output_path}")
    print(f"Metadata: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
