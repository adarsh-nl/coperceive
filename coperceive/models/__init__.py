# SPDX-License-Identifier: Apache-2.0
"""Model plugins (ADR 0011, ``docs/spec/model-plugin.md``).

A plugin is an installed package found through the entry point group ``coperceive.models``,
described by a YAML manifest and run as a subprocess in its own interpreter over
newline-delimited JSON. Batch mode writes a predictions format 1 run.
"""
