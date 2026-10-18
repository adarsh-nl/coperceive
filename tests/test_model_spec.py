# SPDX-License-Identifier: Apache-2.0
"""Model plugin manifests (spec §Manifest) and the entry-point registry (§Discovery)."""

from __future__ import annotations

import hashlib
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from coperceive.models import registry
from coperceive.models.registry import RegistryError, list_plugins, resolve_manifest
from coperceive.models.spec import (
    ModelSpecError,
    check_options,
    load_manifest,
    manifest_sha256,
    parse_manifest,
    render_argv,
    select_model,
)

from tests.fake_model_plugin import MANIFEST as FAKE


def _payload() -> dict[str, Any]:
    loaded = yaml.safe_load(FAKE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def test_fake_manifest_loads() -> None:
    manifest = load_manifest(FAKE)
    assert manifest.name == "fake"
    assert manifest.version == "0.0.1"
    assert manifest.environment.kind == "current"
    assert tuple(manifest.models) == ("default",)
    assert manifest.models["default"].inputs.modalities == ("camera", "lidar")
    assert manifest.models["default"].outputs.sources == (("single", "fake"),)
    assert manifest.models["default"].resources.frame_timeout_s == 600.0
    assert manifest.licence.notice == "fixture, no licence"
    assert manifest.source == FAKE


def test_unknown_key_names_its_path() -> None:
    payload = _payload()
    payload["models"]["default"]["colour"] = "red"
    with pytest.raises(ModelSpecError, match="models.default.colour"):
        parse_manifest(payload, source=None)


def test_two_environment_kinds_are_an_error() -> None:
    payload = _payload()
    payload["environment"] = {"current": True, "python": "/usr/bin/python3"}
    with pytest.raises(ModelSpecError, match="environment"):
        parse_manifest(payload, source=None)


def test_unknown_placeholder_in_commands() -> None:
    payload = _payload()
    payload["commands"]["batch"] = ["{shell}", "-c", "true"]
    with pytest.raises(ModelSpecError, match="commands.batch"):
        parse_manifest(payload, source=None)


def test_dependency_must_name_a_model() -> None:
    payload = _payload()
    payload["models"]["default"]["depends_on"] = [{"model": "nope", "artifact": "x"}]
    with pytest.raises(ModelSpecError, match="nope"):
        parse_manifest(payload, source=None)


def test_frame_placeholder_needs_raw_clock() -> None:
    payload = _payload()
    payload["models"]["default"]["inputs"]["raw"] = ["vehicle-side/pose/{frame:06d}.json"]
    with pytest.raises(ModelSpecError, match="raw_clock"):
        parse_manifest(payload, source=None)


def test_render_argv() -> None:
    manifest = load_manifest(FAKE)
    argv = render_argv(
        manifest,
        "batch",
        python=Path(sys.executable),
        plugin_dir=FAKE.parent,
        work_dir=Path("/tmp/work"),
        model="default",
    )
    assert argv == [sys.executable, f"{FAKE.parent}/worker.py", "--model", "default"]


def test_manifest_sha256_is_the_file_hash() -> None:
    assert manifest_sha256(load_manifest(FAKE)) == hashlib.sha256(FAKE.read_bytes()).hexdigest()
    with pytest.raises(ModelSpecError, match="source"):
        manifest_sha256(parse_manifest(_payload(), source=None))


def test_check_options_types_and_rejects_unknown() -> None:
    spec = select_model(load_manifest(FAKE), None)
    given = check_options(spec, {"extra": "true"})
    assert given["extra"] is True
    assert given["misbehave"] == ""
    assert check_options(spec, {})["extra"] is False
    with pytest.raises(ModelSpecError, match="bogus"):
        check_options(spec, {"bogus": "1"})
    with pytest.raises(ModelSpecError, match="extra"):
        check_options(spec, {"extra": "maybe"})


def test_select_model_needs_a_name_when_ambiguous() -> None:
    payload = _payload()
    payload["models"]["second"] = payload["models"]["default"]
    manifest = parse_manifest(payload, source=None)
    with pytest.raises(ModelSpecError, match="default, second"):
        select_model(manifest, None)
    assert select_model(manifest, "second").key == "second"
    with pytest.raises(ModelSpecError, match="third"):
        select_model(manifest, "third")


class _FakeEntryPoint:
    def __init__(self, name: str, group: str, value: str) -> None:
        self.name = name
        self.group = group
        self.value = value

    def load(self) -> object:
        module_name, _, attr = self.value.partition(":")
        import importlib

        return getattr(importlib.import_module(module_name), attr)


def _fake_entry_points(**kwargs: object) -> list[_FakeEntryPoint]:
    group = kwargs.get("group")
    rows = [_FakeEntryPoint("fake", "coperceive.models", "tests.fake_model_plugin:MANIFEST")]
    return [row for row in rows if group is None or row.group == group]


def test_list_plugins_from_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)
    registry.clear_cache()
    rows = list_plugins()
    assert [(row.name, row.source) for row in rows] == [("fake", "entry-point")]
    assert resolve_manifest("fake", manifest=None).name == "fake"
    registry.clear_cache()


def test_resolve_unknown_lists_known(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)
    registry.clear_cache()
    with pytest.raises(RegistryError, match="known:"):
        resolve_manifest("nope", manifest=None)
    registry.clear_cache()


def test_resolve_manifest_file() -> None:
    assert resolve_manifest(None, manifest=FAKE).name == "fake"
    with pytest.raises(RegistryError, match="manifest"):
        resolve_manifest(None, manifest=None)
