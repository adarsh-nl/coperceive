#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Derive bundle version from git describe or pyproject.toml and sync to tauri.conf.json

set -euo pipefail

VERSION=""
if GIT_TAG=$(git describe --tags --exact-match 2>/dev/null); then
    VERSION="${GIT_TAG#v}"
elif [ -f "pyproject.toml" ]; then
    VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")
else
    VERSION="0.1.0"
fi

if [ -z "${VERSION}" ] || [ "${VERSION}" = "0.0.0" ]; then
    VERSION="0.1.0"
fi

echo "Syncing Coperceive Desktop bundle version to: ${VERSION}"

for conf in "src-tauri/tauri.conf.json" "web/src-tauri/tauri.conf.json"; do
    if [ -f "${conf}" ]; then
        python3 -c "
import json
with open('${conf}', 'r') as f:
    data = json.load(f)
data['version'] = '${VERSION}'
with open('${conf}', 'w') as f:
    json.dump(data, f, indent=2)
"
    fi
done
