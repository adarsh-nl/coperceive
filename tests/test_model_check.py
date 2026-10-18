# SPDX-License-Identifier: Apache-2.0
"""``coperceive model check``: the ten conformance rules over the fake plugin."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import yaml
from coperceive.models.check import RULES, RuleResult, check_plugin, render_check
from coperceive.models.spec import load_manifest, select_model
from coperceive.models.synthetic import TIMES_US, synthetic_scene, write_synthetic_scene
from coperceive.store import read_scene

from tests.fake_model_plugin import MANIFEST as FAKE


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}


def _check(tmp_path: Path, **options: str) -> dict[int, RuleResult]:
    results = check_plugin(
        load_manifest(FAKE),
        model="default",
        work_root=tmp_path / "check",
        keep=False,
        raw_root=None,
        parent_env=_env(),
        options=options or None,
    )
    return {r.number: r for r in results}


def test_synthetic_scene_writes_media_per_modality(tmp_path: Path) -> None:
    spec = select_model(load_manifest(FAKE), "default")  # camera and lidar
    directory = write_synthetic_scene(tmp_path / "a", spec)
    scene = read_scene(directory)
    assert [a.agent_id for a in scene.agents] == ["vehicle", "uav"]
    assert len(scene.point_clouds) == len(TIMES_US)
    assert all((directory / image.path).is_file() for image in scene.images)
    camera_only = select_model(load_manifest(FAKE), "known-answer")
    directory = write_synthetic_scene(tmp_path / "b", camera_only)
    assert read_scene(directory).point_clouds == ()
    assert {a.category for a in synthetic_scene().annotations} == {"car", "pedestrian"}


def test_fake_plugin_passes(tmp_path: Path) -> None:
    results = _check(tmp_path)
    failed = [r for r in results.values() if r.status == "fail"]
    assert failed == [], failed
    assert results[1].status == "ok" and results[3].status == "ok" and results[4].status == "ok"
    assert results[7].status == "ok" and results[8].status == "ok" and results[9].status == "ok"
    assert results[6].status in {"ok", "not checked"}
    assert not (tmp_path / "check").exists()  # nothing kept without --keep


def test_stray_stdout_fails_rule_10(tmp_path: Path) -> None:
    results = _check(tmp_path, misbehave="stray-stdout")
    assert results[10].status == "fail"


def test_write_outside_fails_rule_5(tmp_path: Path) -> None:
    results = _check(tmp_path, misbehave="write-outside")
    assert results[5].status == "fail" and "escaped.txt" in str(results[5].reason)


def test_nondeterministic_fails_rule_8_unless_declared(tmp_path: Path) -> None:
    results = _check(tmp_path, misbehave="nondeterministic")
    assert results[8].status == "fail"
    payload = cast(dict[str, Any], yaml.safe_load(FAKE.read_text(encoding="utf-8")))
    payload["models"]["default"]["outputs"]["deterministic"] = False
    copy = tmp_path / "plugin" / "model.yaml"
    copy.parent.mkdir()
    copy.write_text(yaml.safe_dump(payload), encoding="utf-8")
    (copy.parent / "worker.py").write_bytes((FAKE.parent / "worker.py").read_bytes())
    declared = {
        r.number: r
        for r in check_plugin(
            load_manifest(copy),
            model="default",
            work_root=tmp_path / "check2",
            keep=False,
            raw_root=None,
            parent_env=_env(),
            options={"misbehave": "nondeterministic"},
        )
    }
    assert declared[8].status == "skipped"


def test_read_gt_fails_rule_6_when_enforced(tmp_path: Path) -> None:
    results = _check(tmp_path, misbehave="read-gt")
    if results[6].status == "not checked":
        return  # permissions are not enforced here (root, or a filesystem without modes)
    assert results[6].status == "fail"


def test_wrong_id_fails_rule_3(tmp_path: Path) -> None:
    results = _check(tmp_path, misbehave="wrong-id")
    assert results[3].status == "fail"


def test_keep_leaves_the_work_root(tmp_path: Path) -> None:
    check_plugin(
        load_manifest(FAKE),
        model="default",
        work_root=tmp_path / "kept",
        keep=True,
        raw_root=None,
        parent_env=_env(),
    )
    assert (tmp_path / "kept" / "work-default" / "parent.log").is_file()


def test_render_check_counts_add_up(tmp_path: Path) -> None:
    results = tuple(_check(tmp_path).values())
    text = render_check("fake", results)
    last = text.strip().splitlines()[-1]
    assert last.startswith("check fake: ")
    counts = {r.status for r in results}
    assert counts <= {"ok", "fail", "skipped", "not checked"}
    assert len(results) == len(RULES) == 10
    assert text.count("\n") == 11
