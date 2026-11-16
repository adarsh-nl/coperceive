# SPDX-License-Identifier: Apache-2.0
"""Adapter scene ids must never become paths outside the library."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from coperceive.adapters.build import CheckReport, SceneRef
from coperceive.adapters.ingest import IngestError, ingest
from coperceive.model import Scene


class _EvilAdapter:
    name = "evil"

    def __init__(self, scene_id: str) -> None:
        self.scene_id = scene_id

    def scenes(self, raw_root: Path, params: Mapping[str, str]) -> Sequence[SceneRef]:
        return (SceneRef(self.scene_id, (0,), (), ()),)

    def load(self, raw_root: Path, ref: SceneRef) -> Scene:
        raise AssertionError("an unsafe scene id must be refused before load")

    def check(
        self,
        raw_root: Path,
        params: Mapping[str, str],
        *,
        scene_ids: Sequence[str] | None = None,
    ) -> CheckReport:
        raise AssertionError("not used")


@pytest.mark.parametrize(
    "scene_id",
    ["../escaped", "/tmp/escaped", "C:\\escaped", "\\\\server\\share\\escaped", "bad.", "bad "],
)
def test_unsafe_scene_ids_are_refused_before_load(tmp_path: Path, scene_id: str) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "library"
    out.mkdir()
    sentinel = tmp_path / "escaped"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(IngestError, match="unsafe scene id"):
        ingest(_EvilAdapter(scene_id), raw, out, params={}, workers=1)

    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (out / "escaped").exists()


def test_symlink_scene_directory_is_refused_without_deleting_outside(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "library"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    (out / "scene").symlink_to(outside, target_is_directory=True)

    with pytest.raises(IngestError, match="symlink|unsafe"):
        ingest(_EvilAdapter("scene"), raw, out, params={}, workers=1)

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
