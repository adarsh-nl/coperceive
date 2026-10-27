#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Build the macOS app and DMG for the desktop-macos CI job.
#
# Tauri's DMG bundler is a create-dmg fork that runs
#   hdiutil create -srcfolder Coperceive.app -volname Coperceive -fs HFS+ ...
# On the macOS runners that create intermittently fails with
#   hdiutil: create failed - Resource busy
# after about ten seconds of copying (the same command takes about 80 s when it
# succeeds), and the job is left with an orphaned "diskimages-helper" process;
# the next run of the same commit on the same runner image succeeds. The
# runner-images maintainers trace exactly this error on macOS 13/14 runners to
# XProtectBehaviorService locking a freshly created image and recommend a retry
# (https://github.com/actions/runner-images/issues/7522). That service is part
# of the OS security stack and cannot be disabled safely on a CI runner, so the
# cause cannot be removed from inside this repository.
#
# The retry here is bounded (two attempts by default, never more than three) and
# only fires when the attempt's own output contains that exact signature; any
# other failure is reported on the first attempt. Before each attempt the state
# this repository can control is reset: stale /Volumes/Coperceive* mounts are
# detached and rw.*.dmg images left behind by a failed create are removed.
# hdiutil's own view is logged so the next failure has evidence.
#
# COPERCEIVE_DMG_ATTEMPTS overrides the attempt count: a whole number, clamped to
# 1..3 (the clamp is logged); anything else is rejected before any build runs.
# COPERCEIVE_TAURI_BUILD_LOG overrides the log path (the CI failure-report step
# reads /tmp/tauri_build.log). tests/test_build_dmg.py exercises the retry and
# the cap with a fake npm, so both are proven on the Linux pytest runners too.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG="${COPERCEIVE_TAURI_BUILD_LOG:-/tmp/tauri_build.log}"
SIGNATURE="hdiutil: create failed - Resource busy"
MAX_ATTEMPTS=3
DEFAULT_ATTEMPTS=2

cd "$REPO_ROOT"
: > "$LOG"

log() {
  printf '%s\n' "$*" | tee -a "$LOG"
}

# Parse COPERCEIVE_DMG_ATTEMPTS: whole numbers only, clamped to 1..MAX_ATTEMPTS.
requested="${COPERCEIVE_DMG_ATTEMPTS:-$DEFAULT_ATTEMPTS}"
if ! [[ "$requested" =~ ^[-+]?[0-9]+$ ]]; then
  echo "COPERCEIVE_DMG_ATTEMPTS must be a whole number, got '${requested}'." >&2
  exit 64
fi
negative=""
case "$requested" in
  -*) negative=1 ;;
esac
digits="${requested#[+-]}"
digits="${digits#"${digits%%[!0]*}"}"
[ -n "$digits" ] || digits=0
ATTEMPTS=$((10#$digits))
if [ -n "$negative" ]; then
  ATTEMPTS=$((0 - ATTEMPTS))
fi
requested_number="$ATTEMPTS"
if [ "$ATTEMPTS" -lt 1 ]; then
  ATTEMPTS=1
fi
if [ "$ATTEMPTS" -gt "$MAX_ATTEMPTS" ]; then
  ATTEMPTS="$MAX_ATTEMPTS"
fi
if [ "$ATTEMPTS" -ne "$requested_number" ]; then
  log "COPERCEIVE_DMG_ATTEMPTS=${requested} clamped to ${ATTEMPTS} (allowed 1..${MAX_ATTEMPTS})"
fi

preflight() {
  log "--- preflight: hdiutil state before bundling ---"
  if command -v sw_vers >/dev/null 2>&1; then
    log "macOS: $(sw_vers -productVersion) ($(uname -m))"
  else
    log "macOS: not available on this host ($(uname -s) $(uname -m))"
  fi
  df -h . 2>&1 | tee -a "$LOG" || true
  if command -v hdiutil >/dev/null 2>&1; then
    hdiutil info 2>&1 | sed -n '1,25p' | tee -a "$LOG" || true
  else
    log "hdiutil: not available on this host"
  fi
  pgrep -lf "XProtect|diskimages" 2>/dev/null | tee -a "$LOG" || true
  # Only on CI: a developer's own mounted Coperceive DMG must not be ejected by
  # running this script locally.
  if [ "${GITHUB_ACTIONS:-}" = "true" ] && [ -d /Volumes ] &&
    command -v hdiutil >/dev/null 2>&1; then
    local volume
    for volume in /Volumes/Coperceive*; do
      [ -e "$volume" ] || continue
      log "Detaching stale mount ${volume}"
      hdiutil detach "$volume" -force >/dev/null 2>&1 ||
        diskutil unmount force "$volume" >/dev/null 2>&1 || true
    done
  fi
  find src-tauri/target -type f -name 'rw.*.dmg' -delete 2>/dev/null || true
}

build_once() {
  ( cd web && npm run tauri build -- --target aarch64-apple-darwin --verbose ) 2>&1 | tee -a "$LOG"
}

status=1
attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  log "=== Tauri build attempt ${attempt}/${ATTEMPTS} ==="
  preflight
  before=$(wc -l < "$LOG")
  if build_once; then
    status=0
  else
    status=$?
  fi
  if [ "$status" -eq 0 ]; then
    break
  fi
  if ! tail -n "+$((before + 1))" "$LOG" | grep -qF "$SIGNATURE"; then
    log "Not retrying: the failure is not the runner's transient hdiutil lock."
    break
  fi
  if [ "$attempt" -ge "$ATTEMPTS" ]; then
    break
  fi
  log "Retrying: hdiutil create hit the runner's transient Resource busy lock."
  sleep 5
  attempt=$((attempt + 1))
done
log "=== Tauri build finished with status ${status} ==="
exit "$status"
