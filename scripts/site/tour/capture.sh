#!/usr/bin/env bash
# One command: build the app, generate synthetic scenes, capture, encode.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -e ".[app]" imageio-ffmpeg
fi
if ! .venv/bin/python -c "import imageio_ffmpeg" >/dev/null 2>&1; then
  .venv/bin/pip install imageio-ffmpeg
fi

if [[ ! -f coperceive/app/static/index.html ]]; then
  (cd web && npm ci --legacy-peer-deps && npx vite build)
fi

if [[ ! -d scripts/site/tour/node_modules/playwright ]]; then
  (cd scripts/site/tour && npm install)
fi
# Playwright's browser cache, not the sandbox override. Download only when the binary is missing.
CHROME="$(env -u PLAYWRIGHT_BROWSERS_PATH node -e "console.log(require('./scripts/site/tour/node_modules/playwright').chromium.executablePath())")"
if [[ ! -x "$CHROME" ]]; then
  env -u PLAYWRIGHT_BROWSERS_PATH scripts/site/tour/node_modules/.bin/playwright install chromium
fi

exec env -u PLAYWRIGHT_BROWSERS_PATH .venv/bin/python scripts/site/tour/run_capture.py
