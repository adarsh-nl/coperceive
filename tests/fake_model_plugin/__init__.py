# SPDX-License-Identifier: Apache-2.0
"""The conformance fake: a model plugin that runs in coperceive's own interpreter."""

from pathlib import Path

MANIFEST = Path(__file__).with_name("model.yaml")
