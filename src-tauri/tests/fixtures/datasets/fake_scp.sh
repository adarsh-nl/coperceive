#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fake scp for the packaged-app cluster-dataset e2e: `alias:path` maps to
# $FAKE_REMOTE_HOME/path (scp paths without a leading / are relative to the
# remote home). Handles the two shapes the app uses for the viewer page:
#   scp -q -p alias:rel/manifest local-file
#   scp -r -q -p local-dir/. alias:rel
set -u

home="${FAKE_REMOTE_HOME:?FAKE_REMOTE_HOME is required}"
paths=()
for arg in "$@"; do
    case "${arg}" in
        -*) ;;
        *) paths+=("${arg}") ;;
    esac
done
[ "${#paths[@]}" -eq 2 ] || { echo "fake_scp: expected source and destination" >&2; exit 2; }

map() {
    case "$1" in
        /*) printf '%s' "$1" ;;
        *:/*) printf '%s%s' "${home}" "${1#*:}" ;;
        *:*) printf '%s/%s' "${home}" "${1#*:}" ;;
        *) printf '%s' "$1" ;;
    esac
}

source_path="$(map "${paths[0]}")"
destination="$(map "${paths[1]}")"
if [ -n "${FAKE_SSH_LOG:-}" ]; then
    printf 'scp %s %s\n' "${paths[0]}" "${paths[1]}" >> "${FAKE_SSH_LOG}"
fi
case "${source_path}" in
    */.)
        mkdir -p "${destination}"
        cp -R "${source_path%/.}/" "${destination}/"
        ;;
    *)
        [ -e "${source_path}" ] || { echo "scp: ${paths[0]}: No such file or directory" >&2; exit 1; }
        cp -p "${source_path}" "${destination}"
        ;;
esac
