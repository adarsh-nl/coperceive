# SPDX-License-Identifier: Apache-2.0
"""``coperceive predictions import`` and ``coperceive eval run|report|compare`` through
``coperceive.cli.main`` (``docs/spec/predictions.md`` §Import, §``metrics.json``) on the
cooperative fixture with the synthetic predictions of ``tests/eval_fixture.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from coperceive.cli import main
from coperceive.evaluation.predictions import PredictionBox, read_run_meta
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions

SCENE_ID = "coop"


def _jsonl_row(box: PredictionBox, *, with_scene: bool) -> dict[str, object]:
    rotation = box.box_from_rpy
    row: dict[str, object] = {
        "agent_id": box.agent_id,
        "source": box.source,
        "time_us": box.time_us,
        "category": box.category,
        "score": box.score,
        "track_id": box.track_id,
        "cx": box.center[0],
        "cy": box.center[1],
        "cz": box.center[2],
        "length": box.size_lwh[0],
        "width": box.size_lwh[1],
        "height": box.size_lwh[2],
        "qw": rotation.qw,
        "qx": rotation.qx,
        "qy": rotation.qy,
        "qz": rotation.qz,
        "frame": box.frame.value,
    }
    if with_scene:
        row["scene_id"] = SCENE_ID
    return row


def _prepare(tmp_path: Path, *, with_scene: bool = True) -> tuple[Path, Path]:
    """Write the fixture scene to ``<tmp>/scenes/coop`` and the predictions to a JSONL file."""
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    root = tmp_path / "scenes"
    write_scene(scene, root / SCENE_ID)
    _, boxes = synthetic_predictions(scene)
    jsonl = tmp_path / "boxes.jsonl"
    jsonl.write_text(
        "".join(json.dumps(_jsonl_row(box, with_scene=with_scene)) + "\n" for box in boxes),
        encoding="utf-8",
    )
    return root, jsonl


def _import(root: Path, jsonl: Path, *extra: str) -> None:
    main(
        [
            "predictions",
            "import",
            str(jsonl),
            "--run",
            "synthetic",
            "--root",
            str(root),
            "--score-note",
            "constant",
            *extra,
        ]
    )


def test_import_writes_boxes_and_run_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    _import(root, jsonl)
    run_dir = root / "predictions" / "synthetic"
    assert (run_dir / SCENE_ID / "boxes.parquet").is_file()
    assert (run_dir / "run.json").is_file()
    meta = read_run_meta(root / "predictions", "synthetic")
    assert meta.score_note == "constant"
    assert set(meta.source_names) == {"single", "fused"}
    assert dict(meta.sources)["single"] == "imported"
    assert "car" in meta.categories and "truck" in meta.categories
    out = capsys.readouterr().out
    assert "wrote" in out and "scenes 1" in out


def test_import_takes_scene_from_flag_and_source_notes(tmp_path: Path) -> None:
    root, jsonl = _prepare(tmp_path, with_scene=False)
    _import(
        root,
        jsonl,
        "--scene",
        SCENE_ID,
        "--model",
        "fixture",
        "--categories",
        "car,truck,pedestrian",
        "--source-note",
        "single=own sensors",
    )
    meta = read_run_meta(root / "predictions", "synthetic")
    assert meta.model == "fixture"
    assert meta.categories == ("car", "truck", "pedestrian")
    assert dict(meta.sources) == {"single": "own sensors", "fused": "imported"}


def test_import_without_scene_column_needs_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path, with_scene=False)
    with pytest.raises(SystemExit) as caught:
        _import(root, jsonl)
    assert caught.value.code == 1
    assert "--scene" in capsys.readouterr().err


def test_import_refuses_to_overwrite_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    _import(root, jsonl)
    with pytest.raises(SystemExit) as caught:
        _import(root, jsonl)
    assert caught.value.code == 1
    assert "force" in capsys.readouterr().err
    _import(root, jsonl, "--force")


def test_import_rejects_a_row_of_an_unknown_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    _, boxes = synthetic_predictions(scene)
    rows = [_jsonl_row(box, with_scene=True) for box in boxes]
    rows[3]["agent_id"] = "ghost"
    jsonl.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        _import(root, jsonl)
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "row 3" in err and "ghost" in err
    assert not (root / "predictions").exists()


def test_import_without_score_note_exits_2(tmp_path: Path) -> None:
    root, jsonl = _prepare(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["predictions", "import", str(jsonl), "--run", "synthetic", "--root", str(root)])
    assert caught.value.code == 2


def test_eval_run_report_and_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, jsonl = _prepare(tmp_path)
    # The fixture's run.json list: the cone in the ground truth is not on it.
    _import(root, jsonl, "--categories", "car,truck,pedestrian,sign,pole")
    capsys.readouterr()

    main(["eval", "run", "synthetic", "--root", str(root)])
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "evaluated 1 skipped 0 errors 0",
        f"warning\t{SCENE_ID}: category 'cone' is not in run.json categories",
    ]
    scene_out = root / "predictions" / "synthetic" / SCENE_ID
    assert (scene_out / "matches.parquet").is_file()
    assert (scene_out / "metrics.json").is_file()
    metrics = json.loads((scene_out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["created_utc"].endswith("Z") and len(metrics["created_utc"]) == 20

    main(["eval", "report", "synthetic", "--root", str(root)])
    out = capsys.readouterr().out
    assert "| scene |" in out and "AP@0.5" in out
    assert f"| {SCENE_ID} | vehicle | single |" in out
    assert "| all |" in out

    main(["eval", "compare", "synthetic", "synthetic", "--root", str(root)])
    out = capsys.readouterr().out
    assert f"| {SCENE_ID} | vehicle | single |" in out
    assert "+0.000" in out


def test_eval_run_with_explicit_predictions_root_config_and_scenes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    predictions = tmp_path / "elsewhere"
    _import(root, jsonl, "--predictions", str(predictions))
    assert (predictions / "synthetic" / SCENE_ID / "boxes.parquet").is_file()
    config = tmp_path / "eval.yaml"
    config.write_text("criterion: centre\nthresholds: [2.0, 1.0]\ndisplay_threshold: 2.0\n")
    capsys.readouterr()
    main(
        [
            "eval",
            "run",
            "synthetic",
            "--root",
            str(root),
            "--predictions",
            str(predictions),
            "--config",
            str(config),
            "--scenes",
            SCENE_ID,
        ]
    )
    assert capsys.readouterr().out.splitlines()[0] == "evaluated 1 skipped 0 errors 0"
    metrics = json.loads(
        (predictions / "synthetic" / SCENE_ID / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["config"]["criterion"] == "centre"
    main(["eval", "report", "synthetic", "--root", str(root), "--predictions", str(predictions)])
    assert "AP@2.0" in capsys.readouterr().out


def test_eval_run_reports_a_scene_error_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    _import(root, jsonl)
    capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        main(["eval", "run", "synthetic", "--root", str(root), "--scenes", f"{SCENE_ID},nope"])
    assert caught.value.code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "evaluated 1 skipped 0 errors 1"
    assert lines[1].startswith("error\tnope: ")


def test_eval_run_unknown_run_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, jsonl = _prepare(tmp_path)
    _import(root, jsonl)
    capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        main(["eval", "run", "nope", "--root", str(root)])
    assert caught.value.code == 1
    assert "nope" in capsys.readouterr().err


def test_eval_run_without_predictions_root_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = _prepare(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["eval", "run", "synthetic", "--root", str(root)])
    assert caught.value.code == 1
    assert "--predictions" in capsys.readouterr().err


def test_eval_run_missing_config_file_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    _import(root, jsonl)
    capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "eval",
                "run",
                "synthetic",
                "--root",
                str(root),
                "--config",
                str(tmp_path / "absent.yaml"),
            ]
        )
    assert caught.value.code == 1
    assert "absent.yaml" in capsys.readouterr().err


def test_eval_report_of_an_unevaluated_run_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    _import(root, jsonl)
    capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        main(["eval", "report", "synthetic", "--root", str(root)])
    assert caught.value.code == 1
    assert "eval run" in capsys.readouterr().err


def test_import_refuses_a_scene_flag_that_breaks_the_scene_id_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path, with_scene=False)
    outside = tmp_path / "x"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")  # must never be opened
    with pytest.raises(SystemExit) as caught:
        _import(root, jsonl, "--scene", "../x")
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "../x" in err
    assert not (root / "predictions").exists()


def test_import_refuses_a_scene_id_column_that_breaks_the_scene_id_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    outside = tmp_path / "x"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")  # must never be opened
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    _, boxes = synthetic_predictions(scene)
    rows = [_jsonl_row(box, with_scene=True) for box in boxes]
    rows[2]["scene_id"] = "../x"
    jsonl.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        _import(root, jsonl)
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "row 2" in err and "../x" in err
    assert not (root / "predictions").exists()


def test_import_reports_a_malformed_scene_manifest_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, jsonl = _prepare(tmp_path)
    (root / SCENE_ID / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        _import(root, jsonl)
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "row 0" in err and SCENE_ID in err
    assert not (root / "predictions").exists()
