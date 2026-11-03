#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Set the bundle version in tauri.conf.json, which the app reports and the
# updater compares (docs/release/v0.1.0-checklist.md, R2).
#
# - In a tag-triggered workflow: the tag that triggered it (GITHUB_REF_NAME),
#   without its "v". Never another tag on the same commit.
# - Elsewhere: the one v* tag on HEAD; several tags on HEAD is an error (we
#   do not guess which release this is).
# - No tag: pyproject.toml's version.

set -euo pipefail

VERSION=""
if [ "${GITHUB_REF_TYPE:-}" = "tag" ] && [ -n "${GITHUB_REF_NAME:-}" ]; then
    VERSION="${GITHUB_REF_NAME#v}"
else
    TAGS="$(git tag --points-at HEAD --list 'v*' 2>/dev/null || true)"
    COUNT="$(printf '%s' "${TAGS}" | grep -c . || true)"
    if [ "${COUNT}" -gt 1 ]; then
        echo "sync_version: several release tags on HEAD ($(echo ${TAGS})); set GITHUB_REF_NAME to the one being built" >&2
        exit 1
    elif [ "${COUNT}" -eq 1 ]; then
        VERSION="${TAGS#v}"
    elif [ -f "pyproject.toml" ]; then
        VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")
    fi
fi

if [ -z "${VERSION}" ] || [ "${VERSION}" = "0.0.0" ]; then
    VERSION="0.1.0"
fi
if ! [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$ ]]; then
    echo "sync_version: '${VERSION}' is not a release version (MAJOR.MINOR.PATCH)" >&2
    exit 1
fi

echo "Syncing Coperceive Desktop bundle version to: ${VERSION}"

for conf in "src-tauri/tauri.conf.json" "web/src-tauri/tauri.conf.json"; do
    if [ -f "${conf}" ]; then
        python3 - "${conf}" "${VERSION}" <<'PY'
import json
import sys

path, version = sys.argv[1:3]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["version"] = version
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PY
    fi
done
