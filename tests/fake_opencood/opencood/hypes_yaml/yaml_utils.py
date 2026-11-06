# pyright: basic
# This package doubles for an untyped third-party dependency in tests (the real OpenCOOD
# has no annotations either); strict-mode Unknown-type diagnostics do not apply here.
# SPDX-License-Identifier: Apache-2.0
"""``opencood.hypes_yaml.yaml_utils.load_yaml`` as the plugin's engine calls it."""

from __future__ import annotations

import os

import yaml


def load_yaml(file, opt=None):  # noqa: ANN001, ANN201 - the real signature is untyped
    """Load a config YAML (the real loader also applies ``yaml_parser``; fixtures are plain)."""
    path = file
    model_dir = getattr(opt, "model_dir", None) if opt is not None else None
    if model_dir:
        path = os.path.join(model_dir, "config.yaml")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)
