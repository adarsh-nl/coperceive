# SPDX-License-Identifier: Apache-2.0
"""Paper export: LaTeX tables, plots, qualitative allow list, manifest."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from coperceive.cli import main
from coperceive.paper_export import (
    NOTE_COMPARISON,
    NOTE_DEGRADATION,
    NOTE_METRICS,
    NOTE_REPRO,
    PaperExportError,
    PaperFrame,
    PaperInputs,
    PaperOptions,
    comparison_tex,
    dataset_allowed,
    export_paper,
    metrics_tex,
    resolve_dataset_id,
)
from coperceive.store import write_scene
from PIL import Image

from tests.cooperative import cooperative_scene

_METRICS = {
    "format": "coperceive-eval",
    "format_version": 1,
    "run_id": "det-a",
    "scene_id": "synth_001",
    "outputs": [
        {
            "agent_id": "vehicle",
            "source": "fused",
            "totals": {
                "gt": 9,
                "predictions": 10,
                "tp": 8,
                "fp": 2,
                "miss": 1,
                "precision": 0.8,
                "recall": 0.888888,
            },
            "ap": {"0.5": {"car": 0.75, "mean": 0.75}},
        },
        {
            "agent_id": "vehicle",
            "source": "single",
            "totals": {
                "gt": 9,
                "predictions": 10,
                "tp": 5,
                "fp": 5,
                "miss": 4,
                "precision": 0.5,
                "recall": 0.555555,
            },
            "ap": {"0.5": {"car": 0.4, "mean": 0.4}},
        },
    ],
}

_OPTIONS = PaperOptions()


def test_metrics_table_matches_the_golden_text() -> None:
    golden = Path("tests/fixtures/paper/metrics_golden.tex").read_text(encoding="utf-8")
    assert metrics_tex([_METRICS], _OPTIONS) == golden


def _with_config(**config: object) -> dict[str, object]:
    return {**_METRICS, "config": config, "iou": "bev"}


def test_metrics_caption_names_the_protocol_and_ap_columns_say_bev() -> None:
    """docs/eval-spec.md A.12: AP@<t> (BEV) and the protocol the scene was scored under."""
    current = _with_config(
        criterion="iou_bev",
        ranking_scope="per_frame",
        gt_scope="connected_union",
        comm_range_m=70.0,
    )
    text = metrics_tex([current], _OPTIONS)
    assert "{AP@0.5 (BEV)}" in text
    assert (
        "\\caption{Metrics. AP is bird's-eye-view (BEV) IoU AP; "
        "protocol: ranking per\\_frame, ground truth connected\\_union (70 m).}"
    ) in text
    ego = _with_config(criterion="iou_bev", ranking_scope="pooled", gt_scope="ego_only")
    both = metrics_tex([current, {**ego, "scene_id": "synth_002"}], _OPTIONS)
    assert "protocols differ between scenes: ranking per\\_frame" in both
    # Pooled with ego-only ground truth is the v0.1.1 rule set: it carries the legacy label.
    assert "; legacy (pooled, ego-only)." in both


def test_a_centre_distance_run_is_not_labelled_bev() -> None:
    centre = _with_config(criterion="centre", ranking_scope="pooled", gt_scope="ego_only")
    text = metrics_tex([centre], _OPTIONS)
    assert "{AP@0.5 m (centre)}" in text
    assert "(BEV)" not in text
    assert "AP is centre-distance AP" in text


def _compare_document(protocol: dict[str, object] | None) -> dict[str, object]:
    document: dict[str, object] = {
        "format": "coperceive-compare",
        "format_version": 1,
        "outputs": [
            {
                "agent_id": "vehicle",
                "source": "fused",
                "breakdowns": {
                    "distance": [
                        {
                            "bin": "0-30m",
                            "runs": {
                                "det-a": {"ap": {"0.5": {"mean": 0.5}}, "n_objects": 4},
                                "det-b": {"ap": {"0.5": {"mean": 0.75}}, "n_objects": 4},
                            },
                        }
                    ]
                },
            }
        ],
    }
    if protocol is not None:
        document["protocol"] = protocol
    return document


def test_breakdown_tables_name_the_threshold_and_the_protocol() -> None:
    text = comparison_tex(
        _compare_document(
            {
                "label": "ranking per_frame, ground truth connected_union (70 m)",
                "criterion": "iou_bev",
            }
        ),
        _OPTIONS,
    )
    assert "{det-a AP@0.5 (BEV)} & {det-b AP@0.5 (BEV)}" in text
    assert "protocol: ranking per\\_frame, ground truth connected\\_union (70 m).}" in text
    # A compare.json written before the protocol object says so instead of guessing.
    assert "protocol not recorded.}" in comparison_tex(_compare_document(None), _OPTIONS)


def test_missing_inputs_are_plain_notes(tmp_path: Path) -> None:
    dest = tmp_path / "paper.zip"
    manifest = export_paper(
        dest,
        _OPTIONS,
        PaperInputs(),
        created_utc="2026-11-02T00:00:00Z",
    )
    assert manifest["run_ids"] == []
    assert manifest["repro_card_ids"] == []
    notes = manifest["notes"]
    assert isinstance(notes, list)
    assert NOTE_METRICS in notes
    assert NOTE_COMPARISON in notes
    assert NOTE_DEGRADATION in notes
    assert NOTE_REPRO in notes
    with zipfile.ZipFile(dest) as archive:
        text = archive.read("tables/metrics.tex").decode("utf-8")
        assert "No scored run was found" in text
        assert "booktab" in archive.read("README.txt").decode(
            "utf-8"
        ) or "booktabs" in archive.read("README.txt").decode("utf-8")


def test_scored_run_lists_repro_card_and_draws_curves(tmp_path: Path) -> None:
    predictions = tmp_path / "predictions"
    scene = predictions / "det-a" / "synth_001"
    scene.mkdir(parents=True)
    (scene / "metrics.json").write_text(json.dumps(_METRICS) + "\n", encoding="utf-8")
    _write_matches(scene / "matches.parquet")
    card = {"schema": "coperceive-repro-card", "schema_version": 1, "id": "card-0123456789ab"}
    (predictions / "det-a" / "repro-card.json").write_text(json.dumps(card), encoding="utf-8")
    compare = {
        "format": "coperceive-compare",
        "format_version": 1,
        "runs": ["det-a", "det-b"],
        "outputs": [
            {
                "agent_id": "vehicle",
                "source": "fused",
                "breakdowns": {
                    "distance": [
                        {
                            "bin": "0-30 m",
                            "runs": {
                                "det-a": {"n_objects": 4, "ap": {"0.5": {"mean": 0.4}}},
                                "det-b": {"n_objects": 4, "ap": {"0.5": {"mean": 0.8}}},
                            },
                        },
                        {
                            "bin": "30-50 m",
                            "runs": {
                                "det-a": {"n_objects": 6, "ap": {"0.5": {"mean": 0.7}}},
                                "det-b": {"n_objects": 6, "ap": {"0.5": {"mean": 0.5}}},
                            },
                        },
                    ]
                },
            }
        ],
    }
    degradation = {
        "format": "coperceive-degradation",
        "format_version": 1,
        "x_label": "Latency (frames)",
        "y_label": "AP",
        "series": [
            {"name": "det-a", "points": [{"level": 0, "value": 0.75}, {"level": 2, "value": 0.5}]}
        ],
    }
    dest = tmp_path / "paper.zip"
    (tmp_path / "compare.json").write_text(json.dumps(compare), encoding="utf-8")
    (tmp_path / "degradation.json").write_text(json.dumps(degradation), encoding="utf-8")
    manifest = export_paper(
        dest,
        _OPTIONS,
        PaperInputs(
            run_id="det-a",
            predictions_root=predictions,
            compare_path=tmp_path / "compare.json",
            degradation_path=tmp_path / "degradation.json",
            repro_card_paths=(predictions / "det-a" / "repro-card.json",),
            explicit_compare=True,
            explicit_degradation=True,
        ),
        created_utc="2026-11-02T00:00:00Z",
    )
    assert manifest["repro_card_ids"] == ["card-0123456789ab"]
    assert manifest["run_ids"] == ["det-a"]
    inputs = manifest["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["metrics"] == "present"
    assert inputs["comparison"] == "present"
    assert inputs["degradation"] == "present"
    assert inputs["repro_cards"] == "present"
    with zipfile.ZipFile(dest) as archive:
        names = set(archive.namelist())
        assert "figures/pr.svg" in names
        assert "figures/pr.pdf" in names
        assert "figures/breakdown.svg" in names
        assert "figures/degradation.svg" in names
        table = archive.read("tables/comparison.tex").decode("utf-8")
        assert "\\bfseries 0.800" in table
        assert "\\bfseries 0.700" in table
        svg_a = archive.read("figures/pr.svg")
        svg_b = archive.read("figures/degradation.svg")
    again = tmp_path / "again.zip"
    export_paper(
        again,
        _OPTIONS,
        PaperInputs(
            run_id="det-a",
            predictions_root=predictions,
            compare_path=tmp_path / "compare.json",
            degradation_path=tmp_path / "degradation.json",
            repro_card_paths=(predictions / "det-a" / "repro-card.json",),
            explicit_compare=True,
            explicit_degradation=True,
        ),
        created_utc="2026-11-02T00:00:00Z",
    )
    with zipfile.ZipFile(again) as archive:
        assert archive.read("figures/pr.svg") == svg_a
        assert archive.read("figures/degradation.svg") == svg_b


def test_explicit_bad_comparison_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "compare.json"
    path.write_text('{"format":"nope","format_version":1}\n', encoding="utf-8")
    try:
        export_paper(
            tmp_path / "paper.zip",
            _OPTIONS,
            PaperInputs(compare_path=path, explicit_compare=True),
            created_utc="2026-11-02T00:00:00Z",
        )
    except PaperExportError as exc:
        assert "coperceive-compare" in str(exc)
    else:
        raise AssertionError("expected PaperExportError")


def test_forbidden_datasets_are_never_drawn(tmp_path: Path) -> None:
    allowed, message = dataset_allowed("OPV2V", ("opv2v", "griffin"))
    assert allowed is False
    assert "not exported" in message
    assert dataset_allowed("V2X-Set", ())[0] is False
    assert dataset_allowed("v2v4real", ("v2v4real",))[0] is False
    griffin_off, griffin_note = dataset_allowed("griffin", ())
    assert griffin_off is False
    assert "fixtures" in griffin_note
    assert dataset_allowed("Griffin", ("griffin",)) == (True, "")
    assert dataset_allowed("fixture", ()) == (True, "")
    frame = PaperFrame(
        scene_dir=tmp_path,
        scene_id="blocked",
        time_us=0,
        dataset_id="OPV2V",
    )
    dest = tmp_path / "paper.zip"
    manifest = export_paper(
        dest,
        PaperOptions(qualitative=True, allow_datasets=("opv2v", "griffin")),
        PaperInputs(frames=(frame,)),
        created_utc="2026-11-02T00:00:00Z",
    )
    inputs = manifest["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["qualitative"] == "refused"
    with zipfile.ZipFile(dest) as archive:
        assert not any(name.startswith("qualitative/") for name in archive.namelist())


def test_fixture_qualitative_frames_render(tmp_path: Path) -> None:
    scene = cooperative_scene()
    scene_dir = tmp_path / "scenes" / scene.scene_id
    write_scene(scene, scene_dir)
    image = scene_dir / "vehicle" / "cam_front" / "0100000.png"
    image.parent.mkdir(parents=True)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 40, 50)).save(buffer, format="PNG")
    image.write_bytes(buffer.getvalue())
    frame = PaperFrame(
        scene_dir=scene_dir,
        scene_id=scene.scene_id,
        time_us=100_000,
        agent_id="vehicle",
        sensor_id="cam_front",
        dataset_id="fixture",
    )
    dest = tmp_path / "paper.zip"
    manifest = export_paper(
        dest,
        PaperOptions(qualitative=True),
        PaperInputs(frames=(frame,)),
        created_utc="2026-11-02T00:00:00Z",
    )
    inputs = manifest["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["qualitative"] == "rendered"
    with zipfile.ZipFile(dest) as archive:
        png = archive.read(f"qualitative/{scene.scene_id}_100000_bev.png")
        assert png.startswith(b"\x89PNG")
        camera = archive.read(f"qualitative/{scene.scene_id}_100000_vehicle_cam_front.png")
        assert camera.startswith(b"\x89PNG")
        assert any(name.endswith("_bev.pdf") for name in archive.namelist())


def test_cli_writes_a_zip_without_a_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = tmp_path / "out.zip"
    main(["export", "paper", "--out", str(dest)])
    assert dest.is_file()
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    assert "No scored run was found" in captured.out


def test_resolve_dataset_id_only_matches_demo_junction_under_demo_path(tmp_path: Path) -> None:
    demo_scene = tmp_path / "demo" / "scenes" / "demo-junction"
    demo_scene.mkdir(parents=True)
    (demo_scene / "manifest.json").write_text(
        json.dumps({"scene_id": "demo-junction"}),
        encoding="utf-8",
    )
    assert resolve_dataset_id(demo_scene) == "Demo (synthetic)"

    # A scene named demo-* outside <state>/demo/scenes/demo-junction must not skip licence opt-in
    other_demo = tmp_path / "library" / "demo-custom"
    other_demo.mkdir(parents=True)
    (other_demo / "manifest.json").write_text(
        json.dumps({"scene_id": "demo-custom"}),
        encoding="utf-8",
    )
    assert resolve_dataset_id(other_demo) == "unknown"

    outside_junction = tmp_path / "library" / "demo-junction"
    outside_junction.mkdir(parents=True)
    (outside_junction / "manifest.json").write_text(
        json.dumps({"scene_id": "demo-junction"}),
        encoding="utf-8",
    )
    assert resolve_dataset_id(outside_junction) == "unknown"


def _write_matches(path: Path) -> None:
    rows: list[dict[str, object]] = [
        {
            "kind": kind,
            "agent_id": "vehicle",
            "source": "fused",
            "category": "car",
            "score": score,
            "status": status,
            "status_at_0": at,
        }
        for kind, score, status, at in (
            ("gt", None, "tp", None),
            ("gt", None, "miss", None),
            ("prediction", 0.9, "tp", "tp"),
            ("prediction", 0.2, "fp", "fp"),
        )
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
