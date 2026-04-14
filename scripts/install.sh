#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${1:-/usr}"
COMPONENT_DIR="${2:-/usr/share/ibus/component}"
ENGINE_LIB_DIR="${PREFIX}/lib/ibus-arabizi"
PACKAGE_DIR="${ENGINE_LIB_DIR}/arabizi_ibus"
BIN_DIR="${PREFIX}/bin"

install -d "${PACKAGE_DIR}" "${BIN_DIR}" "${COMPONENT_DIR}"
install -m 644 "${ROOT_DIR}/arabizi_ibus/"*.py "${PACKAGE_DIR}/"
install -m 644 "${ROOT_DIR}/arabizi_ibus/lexicon.json" "${PACKAGE_DIR}/lexicon.json"

# Build optimized corpus DB during install if raw corpus sources are available.
shopt -s nullglob
CORPUS_INPUTS=("${ROOT_DIR}/data/"*.csv "${ROOT_DIR}/data/"*.json "${ROOT_DIR}/data/"*.jsonl)
shopt -u nullglob
if [[ ${#CORPUS_INPUTS[@]} -gt 0 ]]; then
  python3 "${ROOT_DIR}/scripts/compile_corpus.py" "${CORPUS_INPUTS[@]}" \
    --output "${ROOT_DIR}/arabizi_ibus/common_words_1000.sqlite3" \
    --top-k 50000 --dialects egyptian,levantine,gulf --min-bigram-count 2
fi

if [[ -f "${ROOT_DIR}/arabizi_ibus/common_words_1000.sqlite3" ]]; then
  install -m 644 "${ROOT_DIR}/arabizi_ibus/common_words_1000.sqlite3" "${PACKAGE_DIR}/common_words_1000.sqlite3"
else
  install -m 644 "${ROOT_DIR}/arabizi_ibus/common_words_1000.json" "${PACKAGE_DIR}/common_words_1000.json"
fi
install -m 755 "${ROOT_DIR}/bin/arabizi-ibus-engine" "${BIN_DIR}/arabizi-ibus-engine"

sed "s|/usr/bin/arabizi-ibus-engine|${BIN_DIR}/arabizi-ibus-engine|g" \
  "${ROOT_DIR}/data/arabizi.xml" > "${COMPONENT_DIR}/arabizi.xml"

if ! "${BIN_DIR}/arabizi-ibus-engine" --help >/dev/null 2>&1; then
  echo "ERROR: installed launcher failed sanity check: ${BIN_DIR}/arabizi-ibus-engine --help" >&2
  exit 1
fi

echo "Installed Arabizi IBus engine files."
echo "1) Run: ibus restart"
echo "2) Open GNOME Settings -> Keyboard -> Input Sources"
echo "3) Add: Arabizi Transliteration"
