# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

import pytest
import yaml
from coperceive.adapters import registry
from coperceive.adapters.spec import builtin_manifest_path
from coperceive.cli import main
from coperceive.store import read_scene
from tests.griffin_release import write_release


def _snapshot(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*"))


def test_list_shows_the_builtin_griffin(capsys: pytest.CaptureFixture[str]) -> None:
    main(["adapters", "list"])
    out = capsys.readouterr().out
    assert "griffin" in out and "builtin" in out


def test_check_prints_counts_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1, 2, 3]})
    before = _snapshot(raw)
    main(["adapters", "check", "griffin", "--raw-root", str(raw), "--param", "subset=25m"])
    out = capsys.readouterr().out
    assert "scenes 1" in out
    assert "Town03-000" in out and "poses=4" in out
    assert _snapshot(raw) == before


def test_check_reports_an_error_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1, 2]}, drop_pose=("drone-side", "Town03-000", 2))
    with pytest.raises(SystemExit) as caught:
        main(["adapters", "check", "griffin", "--raw-root", str(raw), "--param", "subset=25m"])
    assert caught.value.code == 1
    assert "000002.json" in capsys.readouterr().out


def test_ingest_by_name_and_by_manifest_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1]})
    main(
        [
            "adapters", "ingest", "griffin",
            "--raw-root", str(raw), "--out", str(tmp_path / "a"),
            "--param", "subset=25m", "--workers", "1",
        ]
    )
    assert "wrote=1" in capsys.readouterr().out
    assert read_scene(tmp_path / "a" / "Town03-000").scene_id == "Town03-000"
    copy = tmp_path / "my-griffin.yaml"
    copy.write_text(builtin_manifest_path("griffin").read_text(encoding="utf-8"), encoding="utf-8")
    main(
        [
            "adapters", "ingest", str(copy),
            "--raw-root", str(raw), "--out", str(tmp_path / "b"),
            "--param", "subset=25m", "--workers", "1",
        ]
    )
    marker = json.loads((tmp_path / "b" / "Town03-000" / "COMPLETE.json").read_text(encoding="utf-8"))
    assert marker["adapter"] == "griffin"


def test_bad_param_and_unknown_adapter_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1]})
    with pytest.raises(SystemExit) as caught:
        main(["adapters", "ingest", "griffin", "--raw-root", str(raw), "--out", str(tmp_path / "o"), "--param", "subset=30m"])
    assert caught.value.code == 1
    assert "subset" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:
        main(["adapters", "check", "nope", "--raw-root", str(raw)])
    assert caught.value.code == 1
    assert "nope" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["adapters", "check", "griffin", "--raw-root", str(raw), "--param", "subset"])


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
    rows = [
        _FakeEntryPoint("griffin-copy", "coperceive.adapters", "tests.fake_plugin:make_adapter"),
        _FakeEntryPoint("txt_points", "coperceive.readers", "tests.fake_plugin:read_txt_points"),
    ]
    return [row for row in rows if group is None or row.group == group]


def test_entry_point_adapters_and_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)
    registry.clear_cache()
    main(["adapters", "list"])
    out = capsys.readouterr().out
    assert "griffin-copy" in out and "entry-point" in out
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1]})
    # A manifest that names the plugin reader for lidar.
    payload = yaml.safe_load(builtin_manifest_path("griffin").read_text(encoding="utf-8"))
    payload["adapter"] = "griffin-txt"
    payload["streams"]["lidar"]["reader"] = "txt_points"
    payload["streams"]["lidar"]["path"] = "{agent_root}/lidar/lidar_top/{frame:06d}.txt"
    manifest = tmp_path / "griffin-txt.yaml"
    manifest.write_text(yaml.safe_dump(payload), encoding="utf-8")
    (raw / "vehicle-side" / "lidar" / "lidar_top" / "000001.txt").write_text(
        "1 2 3 0.5\n4 5 6 0.25\n", encoding="utf-8"
    )
    main(
        [
            "adapters", "ingest", str(manifest),
            "--raw-root", str(raw), "--out", str(tmp_path / "out"),
            "--param", "subset=25m", "--workers", "1",
        ]
    )
    scene = read_scene(tmp_path / "out" / "Town03-000")
    assert scene.point_clouds[0].count == 2
    main(
        [
            "adapters", "ingest", "griffin-copy",
            "--raw-root", str(raw), "--out", str(tmp_path / "out2"),
            "--param", "subset=25m", "--workers", "1",
        ]
    )
    assert (tmp_path / "out2" / "Town03-000" / "manifest.json").is_file()
    registry.clear_cache()
