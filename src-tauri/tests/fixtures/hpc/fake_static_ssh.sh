#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -u

here="$(dirname "$0")"
root="${here}/remote"
log="${here}/ssh.log"

remote="${!#}"
printf 'call\n' >> "${log}"
version="${remote##* }"; version="${version#\'}"; version="${version%\'}"
case "$remote" in
  *"coperceive-static-verify"*)
      HOME="${root}/home" bash -c "$remote"
      exit $?
      ;;
  *"mkdir -p -- \"\$HOME/.cache/coperceive/static/\$1\""*)
      mkdir -p "${root}/home/.cache/coperceive/static/$version"
      exit 0
      ;;
  *"cat > \"\$HOME/.cache/coperceive/static/\$1/.coperceive-static-manifest\""*)
      mkdir -p "${root}/home/.cache/coperceive/static/$version"
      cat > "${root}/home/.cache/coperceive/static/$version/.coperceive-static-manifest"
      exit 0
      ;;
esac
echo "fake ssh: unsupported: $remote" >&2
exit 1
