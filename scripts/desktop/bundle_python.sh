#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Bundle reproducible python-build-standalone for the Coperceive Tauri desktop application.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD="python3"
if ! command -v python3 >/dev/null 2>&1 && command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

exec "${PYTHON_CMD}" "${SCRIPT_DIR}/bundle_python.py" "$@"
