# SPDX-License-Identifier: Apache-2.0
"""The Griffin model plugin: the entry point value is the manifest path."""

from pathlib import Path

MANIFEST = Path(__file__).with_name("model.yaml")
