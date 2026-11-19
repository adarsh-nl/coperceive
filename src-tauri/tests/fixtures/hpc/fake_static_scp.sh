#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -u

here="$(dirname "$0")"
root="${here}/remote"
log="${here}/scp.log"

printf '%s\n' "$*" >> "${log}"
src="${@: -2:1}"
dst="${@: -1:1}"
case "$src" in
  *:*)
      rel="${src#*:}"
      cp "${root}/home/$rel" "$dst"
      ;;
  *)
      rel="${dst#*:}"
      mkdir -p "${root}/home/$rel"
      cp -R "$src" "${root}/home/$rel/"
      ;;
esac
