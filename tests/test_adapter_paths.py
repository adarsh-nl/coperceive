# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from coperceive.adapters.paths import (
    PathError,
    Template,
    confine,
)


def test_expand_pads_an_int_placeholder() -> None:
    template = Template("pose/{frame:06d}.json")
    assert template.expand({"frame": 42}) == "pose/000042.json"
    assert template.placeholders == ("frame",)


def test_expand_requires_every_placeholder() -> None:
    template = Template("{agent_root}/camera/{sensor}/{frame:06d}.png")
    with pytest.raises(PathError, match="sensor"):
        template.expand({"agent_root": "vehicle-side", "frame": 1})


def test_only_known_placeholders_and_specs_are_allowed() -> None:
    with pytest.raises(PathError, match="unknown placeholder"):
        Template("{nope}/x.json")
    with pytest.raises(PathError, match="format"):
        Template("{frame:x}.json")
    with pytest.raises(PathError, match="'..'"):
        Template("../{frame}.json")
    with pytest.raises(PathError, match="absolute"):
        Template("/etc/{frame}.json")


def test_invert_recovers_the_frame_from_a_file_name(tmp_path: Path) -> None:
    (tmp_path / "pose").mkdir()
    for frame in (3, 12):
        (tmp_path / "pose" / f"{frame:06d}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pose" / "notes.txt").write_text("", encoding="utf-8")
    template = Template("pose/{frame:06d}.json")
    found = template.discover(tmp_path, {}, "frame")
    assert found == ({"frame": 3}, {"frame": 12})


def test_invert_rejects_a_name_that_does_not_round_trip(tmp_path: Path) -> None:
    (tmp_path / "pose").mkdir()
    (tmp_path / "pose" / "7.json").write_text("{}", encoding="utf-8")
    template = Template("pose/{frame:06d}.json")
    assert template.discover(tmp_path, {}, "frame") == ()


def test_discover_a_string_placeholder_sorts_by_name(tmp_path: Path) -> None:
    (tmp_path / "calib").mkdir()
    for stem in ("lidar_top", "front", "back"):
        (tmp_path / "calib" / f"{stem}.json").write_text("{}", encoding="utf-8")
    template = Template("calib/{sensor}.json")
    found = template.discover(tmp_path, {}, "sensor")
    assert [row["sensor"] for row in found] == ["back", "front", "lidar_top"]


def test_confine_keeps_paths_under_the_root(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("", encoding="utf-8")
    assert confine(root, "a/b.json") == (root / "a" / "b.json").resolve()
    with pytest.raises(PathError, match="leaves"):
        confine(root, "../outside.txt")
    link = root / "link"
    link.symlink_to(tmp_path / "outside.txt")
    with pytest.raises(PathError, match="leaves"):
        confine(root, "link")
