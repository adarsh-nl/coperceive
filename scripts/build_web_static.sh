#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the viewer bundle into coperceive/app/static.
#
# The built files are NOT committed (see .gitignore): CI, the wheel build and
# scripts/desktop/bundle_python.sh all run this before packaging the app, so a
# source change can never leave a stale committed bundle behind.
#
# Node.js and npm are required. node_modules is reused when it matches
# web/package-lock.json, otherwise `npm ci --legacy-peer-deps` runs first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WEB_DIR="${REPO_ROOT}/web"
STATIC_DIR="${REPO_ROOT}/coperceive/app/static"

if ! command -v npm >/dev/null 2>&1; then
    echo "build_web_static.sh: npm is required to build the viewer bundle" >&2
    exit 1
fi

cd "${WEB_DIR}"
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    echo "build_web_static.sh: installing web dependencies"
    # --legacy-peer-deps: openapi-typescript's peer range predates TypeScript 6
    # (see web/package.json); npm 10+ refuses the install without this.
    npm ci --legacy-peer-deps
fi
echo "build_web_static.sh: building coperceive/app/static"
npm run build

if [ ! -f "${STATIC_DIR}/index.html" ]; then
    echo "build_web_static.sh: the build did not write ${STATIC_DIR}/index.html" >&2
    exit 1
fi
python3 "${REPO_ROOT}/scripts/check_bundle_assets.py" --static "${STATIC_DIR}"
