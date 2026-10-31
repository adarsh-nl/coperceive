#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install git hooks for Coperceive development.
# Sets core.hooksPath to scripts/hooks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

chmod +x "${REPO_ROOT}/scripts/hooks/pre-push"
git config core.hooksPath scripts/hooks

echo "[install-hooks] Configured git core.hooksPath -> scripts/hooks"
echo "[install-hooks] Pre-push hook is active. It runs fast checks on changed files before each push."
