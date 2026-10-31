# SPDX-License-Identifier: Apache-2.0
"""``coperceive remote scan``: metadata-only folder and scene discovery."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from coperceive.cli import main
from coperceive.remote.scan import ScanError, ScanResult, scan_tree


def _scene(folder: Path, scene_id: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"scene_id": scene_id} if scene_id is not None else {}
    (folder / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "COMPLETE.json").write_text("{}", encoding="utf-8")
    return folder


def _paths(result: ScanResult) -> list[str]:
    return [item["path"] for item in result["folders"]]


def _scan(
    root: Path,
    path: str | Path = ".",
    *,
    depth: int = 2,
    timeout_s: float = 10.0,
    max_entries: int = 200,
    max_total: int = 2000,
    clock: Callable[[], float] | None = None,
) -> ScanResult:
    kwargs: dict[str, object] = {
        "depth": depth,
        "timeout_s": timeout_s,
        "max_entries": max_entries,
        "max_total": max_total,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return scan_tree(root, path, **kwargs)  # pyright: ignore[reportArgumentType]


def test_scan_finds_nested_scenes_and_datasets(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _scene(root / "dataset_a" / "scene_one", "one")
    _scene(root / "dataset_a" / "scene_two", "two")
    _scene(root / "deep" / "level1" / "scene_three", "three")
    (root / "plain").mkdir(parents=True)

    shallow = _scan(root, depth=2)
    assert shallow["status"] == "ok"
    assert shallow["truncated"] is False
    shallow_paths = _paths(shallow)
    assert "." in shallow_paths
    assert "dataset_a" in shallow_paths
    assert "dataset_a/scene_one" in shallow_paths
    assert "deep" in shallow_paths
    assert "deep/level1" in shallow_paths
    assert "deep/level1/scene_three" not in shallow_paths  # beyond depth 2
    scenes = shallow["scenes"]
    assert isinstance(scenes, list)
    assert {item["path"] for item in scenes} == {"dataset_a/scene_one", "dataset_a/scene_two"}
    assert {item["scene_id"] for item in scenes} == {"one", "two"}

    datasets = shallow["datasets"]
    assert isinstance(datasets, list)
    by_path = {item["path"]: item["scene_count"] for item in datasets}
    assert by_path == {"dataset_a": 2, ".": 2}

    deeper = _scan(root, depth=3)
    assert deeper["scene_count"] == 3
    scenes = deeper["scenes"]
    assert isinstance(scenes, list)
    assert {item["scene_id"] for item in scenes} == {"one", "two", "three"}
    datasets = deeper["datasets"]
    assert isinstance(datasets, list)
    by_path = {item["path"]: item["scene_count"] for item in datasets}
    assert by_path == {"dataset_a": 2, "deep": 1, "deep/level1": 1, ".": 3}


def test_scan_marks_a_scene_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    scene = _scene(root / "synth_001", "synth_001")
    result = _scan(root, path=scene, depth=1)
    assert result["relative_path"] == "synth_001"
    assert result["scenes"] == [
        {"path": "synth_001", "scene_id": "synth_001", "folder_name": "synth_001"}
    ]
    folders = result["folders"]
    assert isinstance(folders, list)
    assert folders[0]["is_scene"] is True
    assert folders[0]["has_manifest"] is True
    assert folders[0]["has_complete"] is True


def test_scan_falls_back_to_the_folder_name_for_a_bad_manifest(tmp_path: Path) -> None:
    root = tmp_path / "data"
    folder = root / "broken"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("not json", encoding="utf-8")
    (folder / "COMPLETE.json").write_text("{}", encoding="utf-8")
    result = _scan(root, depth=1)
    scenes = result["scenes"]
    assert isinstance(scenes, list)
    assert scenes[0]["path"] == "broken"
    assert scenes[0]["scene_id"] == "broken"


def test_scan_refuses_a_path_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ScanError, match="outside the data root"):
        scan_tree(root, outside)
    with pytest.raises(ScanError, match="must not contain '\\.\\.'"):
        scan_tree(root, root / ".." / "outside")
    with pytest.raises(ScanError, match="must not contain '\\.\\.'"):
        scan_tree(root, "../outside")


def test_scan_follows_a_readable_symlink_directory_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    _scene(outside / "hidden_scene", "hidden")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "inside").mkdir()

    result = _scan(root, depth=2)
    assert result["status"] == "ok"
    assert result["truncated"] is False
    assert "escape" in _paths(result)
    assert result["scene_count"] == 1
    assert result["scenes"][0]["path"].startswith("escape/")


def test_scan_still_refuses_an_absolute_path_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ScanError, match="outside the data root"):
        scan_tree(root, outside)


def test_a_listed_symlink_folder_can_be_opened_and_keeps_its_alias(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "shared"
    (outside / "child" / "leaf").mkdir(parents=True)
    (root / "griffin").symlink_to(outside, target_is_directory=True)

    listing = _scan(root, depth=2)
    assert "griffin" in _paths(listing)
    assert "griffin/child" in _paths(listing)
    opened = scan_tree(root, "griffin")
    assert opened["relative_path"] == "griffin"
    assert "griffin/child" in {folder["path"] for folder in opened["folders"]}


def test_scan_follows_a_symlink_that_stays_inside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _scene(root / "real" / "scene_one", "one")
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    result = _scan(root, depth=2)
    scenes = result["scenes"]
    assert isinstance(scenes, list)
    scene_paths = {item["path"] for item in scenes}
    # The alias is a second name for the same folder; it must not be scanned twice.
    assert scene_paths <= {"real/scene_one", "alias/scene_one"}
    assert len(scene_paths) == 1


def test_scan_depth_bound_is_not_truncation(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _scene(root / "a" / "b" / "scene", "s")
    level_zero = _scan(root, depth=0)
    assert _paths(level_zero) == ["."]
    assert level_zero["truncated"] is False
    level_one = _scan(root, depth=1)
    assert _paths(level_one) == [".", "a"]
    level_two = _scan(root, depth=2)
    assert "a/b" in _paths(level_two)
    assert "a/b/scene" not in _paths(level_two)


def test_scan_entry_limit_truncates(tmp_path: Path) -> None:
    root = tmp_path / "data"
    for index in range(6):
        (root / f"dir_{index}").mkdir(parents=True)
    result = _scan(root, depth=1, max_entries=3)
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["truncated_reason"] == "entry limit"
    # The path itself plus at most max_entries children were described.
    assert result["folder_count"] <= 1 + 3


def test_scan_total_limit_truncates(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "a" / "b" / "c" / "d").mkdir(parents=True)
    result = _scan(root, depth=4, max_total=2)
    assert result["truncated"] is True
    assert result["truncated_reason"] == "total limit"
    assert result["folder_count"] <= 2


def test_scan_timeout_returns_partial_results(tmp_path: Path) -> None:
    root = tmp_path / "data"
    for index in range(8):
        (root / f"level_{index}").mkdir(parents=True)
        for child in range(4):
            (root / f"level_{index}" / f"child_{child}").mkdir()

    class Clock:
        ticks = 0

        def __call__(self) -> float:
            self.ticks += 1
            return self.ticks * 0.4

    result = _scan(root, depth=2, timeout_s=1.0, clock=Clock())
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["truncated_reason"] == "timeout"
    assert result["folder_count"] >= 1


def test_scan_never_opens_data_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("permissions are not enforced for root")
    root = tmp_path / "data"
    scene = _scene(root / "scene_one", "one")
    points = scene / "points.bin"
    points.write_bytes(b"data that must not be read")
    points.chmod(0o000)
    try:
        result = _scan(root, depth=2)
    finally:
        points.chmod(0o644)
    assert result["scene_count"] == 1
    assert result["scenes"][0]["scene_id"] == "one"


def test_scan_handles_spaces_quotes_and_shell_metacharacters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "data dir"
    _scene(root / "a b" / "scene one", "one")
    _scene(root / 'quo"te' / "scene two", "two")
    weird = "a; touch pwned"
    _scene(root / weird / "scene three", "three")

    result = _scan(root, depth=2)
    paths = _paths(result)
    assert "a b/scene one" in paths
    assert 'quo"te/scene two' in paths
    assert f"{weird}/scene three" in paths
    json.dumps(result)  # the whole description stays JSON-serialisable

    # A --path that looks like a shell command is treated as a plain path: no
    # shell is involved, so the "touch" never runs and the path is just missing.
    with pytest.raises(SystemExit) as caught:
        main(
            ["remote", "scan", "--root", str(root), "--path", "x; touch /tmp/coperceive-scan-pwned"]
        )
    assert caught.value.code == 1
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "must not contain" not in error["error"]
    assert not Path("/tmp/coperceive-scan-pwned").exists()


def test_scan_cli_prints_json_and_uses_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "data"
    _scene(root / "scene_one", "one")

    main(["remote", "scan", "--root", str(root), "--path", ".", "--depth", "1"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["scenes"] == [
        {"path": "scene_one", "scene_id": "one", "folder_name": "scene_one"}
    ]

    with pytest.raises(SystemExit) as caught:
        main(["remote", "scan", "--root", str(root), "--path", "../outside"])
    assert caught.value.code == 1
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "must not contain '..'" in error["error"]


def test_scan_rejects_out_of_range_bounds(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    with pytest.raises(ScanError, match="depth must be between 0 and 4"):
        scan_tree(root, ".", depth=5)
    with pytest.raises(ScanError, match="timeout must be more than 0"):
        scan_tree(root, ".", timeout_s=0)
    with pytest.raises(ScanError, match="timeout must be more than 0"):
        scan_tree(root, ".", timeout_s=31)
    with pytest.raises(ScanError, match="max-entries must be between 1 and 1000"):
        scan_tree(root, ".", max_entries=0)
    with pytest.raises(ScanError, match="max-total must be between 1 and 10000"):
        scan_tree(root, ".", max_total=0)
