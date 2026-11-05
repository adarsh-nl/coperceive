#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# A cluster interpreter whose `-m coperceive` is a build from before cluster
# datasets, for the "too old" case of e2e_cluster_datasets_app.sh: it has no
# `capabilities` command and no `dataset detect`, and answers both the way
# argparse does. `--version` prints what such builds print.
#
# Anything else (the desktop's install-location one-liner) runs on the real
# interpreter with a stand-in old checkout first on the path, so the probe sees
# "a git checkout at $FAKE_OLD_CHECKOUT". Required env: FAKE_OLD_CHECKOUT,
# FAKE_CLUSTER_PYTHON.
set -u

if [ "${1:-}" = "-m" ] && [ "${2:-}" = "coperceive" ]; then
    shift 2
    case "${1:-}" in
        --version)
            echo "coperceive 0.0.0"
            exit 0
            ;;
        dataset)
            echo "usage: coperceive dataset [-h] {catalog,dry-run,download} ..." >&2
            echo "coperceive dataset: error: argument dataset_command: invalid choice: '${2:-}' (choose from 'catalog', 'dry-run', 'download')" >&2
            exit 2
            ;;
        *)
            echo "usage: coperceive [-h] [--version] {schema,serve,dataset,remote} ..." >&2
            echo "coperceive: error: argument command: invalid choice: '${1:-}' (choose from 'schema', 'serve', 'dataset', 'remote')" >&2
            exit 2
            ;;
    esac
fi
PYTHONPATH="${FAKE_OLD_CHECKOUT:?FAKE_OLD_CHECKOUT is required}" \
    exec "${FAKE_CLUSTER_PYTHON:?FAKE_CLUSTER_PYTHON is required}" "$@"
