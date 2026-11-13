#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
PATTERN_FILE="${2:-${ROOT}/scripts/ci/identifier_patterns.txt}"

if [ ! -f "${PATTERN_FILE}" ]; then
  echo "Pattern file not found: ${PATTERN_FILE}" >&2
  exit 2
fi

PATTERNS=$(grep -vE '^\s*(#|$)' "${PATTERN_FILE}" | paste -sd '|' -)
if [ -z "${PATTERNS}" ]; then
  echo "No patterns in ${PATTERN_FILE}" >&2
  exit 2
fi

TARGETS=()
for dir in web/src site coperceive src-tauri/src docs scripts plugins; do
  if [ -e "${ROOT}/${dir}" ]; then
    TARGETS+=("${ROOT}/${dir}")
  fi
done

if [ "${#TARGETS[@]}" -eq 0 ]; then
  exit 0
fi

HITS=$(grep -rnE "${PATTERNS}" "${TARGETS[@]}" \
  --exclude-dir=node_modules \
  --exclude-dir=.venv \
  --exclude-dir=target \
  --exclude="identifier_patterns.txt" \
  || true)

if [ -n "${HITS}" ]; then
  echo "Personal/cluster identifiers found:" >&2
  echo "${HITS}" >&2
  exit 1
fi
