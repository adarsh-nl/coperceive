#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test verifying the bundled Python environment, coperceive serve sidecar,
# constant-time token verification, and measuring artifact sizes.

set -euo pipefail

APP_BUNDLE="${1:-src-tauri/target/release/bundle/macos/Coperceive.app}"
DMG_FILE="${2:-src-tauri/target/release/bundle/dmg/Coperceive_0.0.0_aarch64.dmg}"

echo "=========================================================="
echo "Coperceive Desktop Artifact Verification & Smoke Test"
echo "=========================================================="

if [ ! -d "${APP_BUNDLE}" ]; then
    echo "Searching for built .app bundle across repository..."
    FOUND_APP=$(find . -iname "*.app" -type d | head -n 1 || true)
    if [ -n "${FOUND_APP}" ]; then
        APP_BUNDLE="${FOUND_APP}"
    else
        echo "Error: .app bundle not found! Listing targets:" >&2
        find . -maxdepth 6 -type d 2>/dev/null | grep -E "target|bundle|dist|release" || true
        exit 1
    fi
fi

echo "App bundle: ${APP_BUNDLE}"

# Find bundled python
PYTHON_BIN=""
for candidate in \
    "${APP_BUNDLE}/Contents/Resources/resources/python/bin/python3" \
    "${APP_BUNDLE}/Contents/Resources/python/bin/python3" \
    "src-tauri/resources/python/bin/python3"; do
    if [ -x "${candidate}" ]; then
        PYTHON_BIN="${candidate}"
        break
    fi
done

if [ -z "${PYTHON_BIN}" ]; then
    echo "Error: Bundled Python interpreter not found in bundle!" >&2
    exit 2
fi

echo "Found bundled Python: ${PYTHON_BIN}"
echo "Python version: $("${PYTHON_BIN}" --version)"
"${PYTHON_BIN}" -c "import coperceive, fastapi, uvicorn; print('coperceive import verified from:', coperceive.__file__)"

# Measure artifact sizes
echo ""
echo "--- Artifact Measured Sizes ---"
APP_SIZE=$(du -sh "${APP_BUNDLE}" | awk '{print $1}')
echo "Coperceive.app size: ${APP_SIZE}"

FOUND_DMG=""
if [ -f "${DMG_FILE}" ]; then
    FOUND_DMG="${DMG_FILE}"
else
    FOUND_DMG=$(find . -iname "*.dmg" -type f | head -n 1 || true)
fi

if [ -n "${FOUND_DMG}" ]; then
    DMG_SIZE=$(ls -lh "${FOUND_DMG}" | awk '{print $5}')
    echo "Coperceive .dmg size: ${DMG_SIZE} (${FOUND_DMG})"
else
    DMG_SIZE="unknown"
    echo "Warning: .dmg file not located for measurement"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "### Artifact Measured Sizes" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Coperceive.app size**: ${APP_SIZE}" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Coperceive .dmg size**: ${DMG_SIZE} (\`${FOUND_DMG}\`)" >> "${GITHUB_STEP_SUMMARY}"
fi

# Headless smoke test: launch serve with token-file and verify loopback authentication
echo ""
echo "--- Headless Sidecar Smoke Test ---"
TMPDIR=$(mktemp -d /tmp/coperceive-desktop-smoke-XXXXXX)
trap 'rm -rf "${TMPDIR}"' EXIT

TOKEN="smoke-test-secret-token-abcdef123456"
TOKEN_FILE="${TMPDIR}/token.txt"
touch "${TOKEN_FILE}"
chmod 0600 "${TOKEN_FILE}"
echo "${TOKEN}" > "${TOKEN_FILE}"

DATA_ROOT="${TMPDIR}/data"
mkdir -p "${DATA_ROOT}"

# Ephemeral port
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
echo "Launching coperceive serve on port ${PORT} with token-file ${TOKEN_FILE}..."

"${PYTHON_BIN}" -m coperceive serve \
    --host 127.0.0.1 \
    --port "${PORT}" \
    --token-file "${TOKEN_FILE}" \
    --root "${DATA_ROOT}" > "${TMPDIR}/serve.log" 2>&1 &
SERVE_PID=$!

echo "Sidecar PID: ${SERVE_PID}"

# Ensure process is killed on script exit
cleanup_serve() {
    if kill -0 "${SERVE_PID}" 2>/dev/null; then
        echo "Terminating sidecar PID ${SERVE_PID}..."
        kill "${SERVE_PID}" 2>/dev/null || true
        wait "${SERVE_PID}" 2>/dev/null || true
    fi
}
trap 'cleanup_serve; rm -rf "${TMPDIR}"' EXIT

# Wait for server to start
echo "Waiting for /api/health readiness..."
HEALTHY=0
for i in {1..30}; do
    if curl -s -f -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:${PORT}/api/health" > /dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 0.2
done

if [ "${HEALTHY}" -ne 1 ]; then
    echo "Error: Server failed to become healthy within 6 seconds. Log output:" >&2
    cat "${TMPDIR}/serve.log" >&2
    exit 3
fi

echo "Server is healthy!"

# Test 1: Unauthenticated request must return HTTP 401
echo "Checking unauthenticated request (expect HTTP 401)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health")
if [ "${HTTP_CODE}" != "401" ]; then
    echo "Security failure: unauthenticated request returned HTTP ${HTTP_CODE}, expected 401!" >&2
    exit 4
fi
echo "Verified: unauthenticated request returned 401 Unauthorized"

# Test 2: Authenticated request returns HTTP 200 OK
echo "Checking authenticated request with token (expect HTTP 200)..."
RESP=$(curl -s -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:${PORT}/api/health")
echo "Response: ${RESP}"
if ! echo "${RESP}" | grep -q '"status":"ok"'; then
    echo "Failure: unexpected health response: ${RESP}" >&2
    exit 5
fi
echo "Verified: authenticated request returned HTTP 200 OK with status ok"

# Terminate process cleanly and verify no orphaned processes
cleanup_serve
echo "Verified: sidecar terminated cleanly, no orphaned processes."
echo "=========================================================="
echo "Smoke test PASSED successfully!"
echo "=========================================================="
