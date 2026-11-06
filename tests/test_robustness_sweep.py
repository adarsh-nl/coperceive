# SPDX-License-Identifier: Apache-2.0
"""``coperceive robustness sweep``: grid validation, the demo end-to-end sweep, Slurm.

The end-to-end test uses ``coperceive.demo.create`` and the sweep fixture plugin
(``tests/fake_model_plugin/sweep-model.yaml``, whose ``degrade`` model reports the
ground truth in the pose the frame received, so pose noise moves the boxes by exactly
the noise). Its numbers are demo/fixture numbers, not model results.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.demo import DemoScene, create
from coperceive.evaluation.schemautil import SchemaError, validate_schema
from coperceive.models.spec import Manifest, load_manifest, select_model
from coperceive.robustness.sweep import (
    DEFAULT_MAX_CELLS,
    EvalChoice,
    SweepError,
    SweepOptions,
    check_cell_limit,
    default_eval,
    load_grid,
    run_id_for,
    run_sweep,
)
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

ROOT = Path(__file__).parents[1]
SWEEP_MANIFEST = Path(__file__).parent / "fake_model_plugin" / "sweep-model.yaml"
SCHEMA = cast(
    dict[str, object],
    json.loads((ROOT / "docs/spec/robustness-sweep.schema.json").read_text(encoding="utf-8")),
)

DEMO_GRID = """
pose_noise_xy: [0.0, 1.0]
latency_frames: [0, 1]
seeds: [0, 1]
"""


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> DemoScene:
    """One demo scene for the whole module: it is deterministic and not cheap to write."""
    return create(tmp_path_factory.mktemp("demo-sweep") / "demo", seed=0)


@pytest.fixture
def coop(tmp_path: Path) -> Path:
    """A four-frame three-agent scene; enough for the grid and Slurm tests, cheap."""
    scenes = tmp_path / "scenes"
    write_scene(replace(cooperative_scene(), scene_id="coop"), scenes / "coop")
    return scenes


# --- helpers --------------------------------------------------------------------------


def _obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"expected a JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def _arr(value: object) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"expected a JSON array, got {type(value).__name__}")
    return list(cast(list[object], value))


def _document(out: Path) -> dict[str, object]:
    return _obj(json.loads((out / "sweep.json").read_text(encoding="utf-8")))


def _options(
    scenes: Path,
    out: Path,
    definition: str,
    *,
    force: bool = False,
    cache: bool = True,
    evals: tuple[EvalChoice, ...] = (default_eval(),),
    manifest: Manifest | None = None,
    run_id: str = "sweep",
) -> SweepOptions:
    return SweepOptions(
        manifest=load_manifest(SWEEP_MANIFEST) if manifest is None else manifest,
        scenes=scenes,
        out=out,
        run_id=run_id,
        grid=load_grid(definition),
        evals=evals,
        force=force,
        cache=cache,
    )


def _map_at(cells: list[object], *, eval_name: str = "default", **fields: object) -> float:
    for item in cells:
        cell = _obj(item)
        spec = _obj(cell["spec"])
        if all(spec[key] == value for key, value in fields.items()):
            display = _obj(_obj(_obj(cell["eval"])[eval_name])["mAP_display"])
            return float(cast(float, display["value"]))
    raise AssertionError(f"no cell with {fields}")


# --- the grid definition --------------------------------------------------------------


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ("", "empty"),
        ("[]", "must be a mapping"),
        ("nope: [1]", "unknown sweep key"),
        ("seedz: [0]", "unknown sweep key"),
        ("pose_noise_xy: []", "has no values"),
        ("pose_noise_xy: [0, 0.0]", "repeats the value"),
        ("pose_noise_xy: [-1]", "pose_noise_xy"),
        ("photon_density: [1]", "unknown sweep key"),
        ("packet_loss: [2]", "packet_loss"),
        ("agent_dropout: [1.5]", "agent_dropout"),
        ("latency_frames: [0.5]", "latency_frames"),
        ("latency_ms: [-2]", "latency_ms"),
        ("max_agents: [0]", "max_agents"),
        ("pose_noise_yaw: [-1]", "pose_noise_yaw"),
        ("seeds: [-1]\npose_noise_xy: [0]", "negative"),
        ("seeds: []\npose_noise_xy: [0]", "has no values"),
        ("seeds: [0, 0]\npose_noise_xy: [0]", "repeated"),
        ("seeds: [0.5]\npose_noise_xy: [0]", "not an integer"),
        ("seeds: [0]", "no perturbation axes"),
    ],
)
def test_a_bad_definition_is_refused(definition: str, message: str) -> None:
    with pytest.raises(SweepError, match=message):
        load_grid(definition)


def test_values_and_seeds_are_sorted_into_a_canonical_order() -> None:
    grid = load_grid("pose_noise_xy: [0.5, 0.2]\nlatency_frames: [1, 0]\nseeds: [2, 0]")
    # The axes follow the perturbation order, not the YAML order; values and seeds ascend.
    assert [axis.name for axis in grid.axes] == ["latency_frames", "pose_noise_xy"]
    assert grid.axes[0].values == (0, 1)
    assert grid.axes[1].values == (0.2, 0.5)
    assert grid.seeds == (0, 2)
    assert grid.combination_count == 4 and grid.cell_count == 8
    cells = grid.cells()
    assert [cell.index for cell in cells] == list(range(8))
    assert cells[0].spec.canonical() == "latency_frames=0,pose_noise_xy=0.2,seed=0"
    assert cells[1].spec.canonical() == "latency_frames=0,pose_noise_xy=0.2,seed=2"
    assert cells[-1].spec.canonical() == "latency_frames=1,pose_noise_xy=0.5,seed=2"


def test_the_cell_limit_counts_combinations_times_seeds() -> None:
    grid = load_grid("pose_noise_xy: [0, 0.5, 1]\nlatency_frames: [0, 1, 2]\nseeds: [0, 1]")
    assert grid.combination_count == 9 and grid.cell_count == 18
    with pytest.raises(SweepError, match=r"18 cells \(9 perturbation combinations x 2 seeds\)"):
        check_cell_limit(grid, 17)
    with pytest.raises(SweepError, match=r"\(2 perturbation combinations x 1 seed\)"):
        check_cell_limit(load_grid("pose_noise_xy: [0, 0.5]"), 1)
    check_cell_limit(grid, DEFAULT_MAX_CELLS)


def test_the_scene_split_is_part_of_the_cell_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_manifest(SWEEP_MANIFEST)
    model = select_model(manifest, None)
    grid = load_grid("pose_noise_xy: [0.5]")
    spec = grid.cells()[0].spec

    def options(scenes: Path) -> SweepOptions:
        return SweepOptions(
            manifest=manifest,
            scenes=scenes,
            out=tmp_path / "out",
            run_id="sweep",
            grid=grid,
            evals=(default_eval(),),
        )

    split_a = options(tmp_path / "split-a")
    assert run_id_for(split_a, model, spec) == run_id_for(split_a, model, spec)
    assert run_id_for(split_a, model, spec) != run_id_for(
        options(tmp_path / "split-b"), model, spec
    )
    # The resolved root is in the digest, so a relative spelling of the same split matches.
    monkeypatch.chdir(tmp_path)
    assert run_id_for(options(Path("split-a")), model, spec) == run_id_for(split_a, model, spec)


def test_cli_refuses_a_grid_over_the_cell_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    argv = [
        "robustness",
        "sweep",
        DEMO_GRID,
        "--manifest",
        str(SWEEP_MANIFEST),
        "--scenes",
        str(tmp_path / "missing"),
        "--out",
        str(out),
        "--max-cells",
        "4",
    ]
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 1
    error = capsys.readouterr().err
    assert "8 cells (4 perturbation combinations x 2 seeds)" in error
    assert "--max-cells" in error
    assert not out.exists()  # nothing is attempted for a refused grid


def test_a_bad_scene_split_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = [
        "robustness",
        "sweep",
        "pose_noise_xy: [0]",
        "--manifest",
        str(SWEEP_MANIFEST),
        "--scenes",
        str(tmp_path / "missing"),
        "--out",
        str(tmp_path / "out"),
    ]
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 1
    assert "not a directory" in capsys.readouterr().err


# --- end to end on the demo scene ----------------------------------------------------


def test_sweep_end_to_end_on_the_demo_scene(
    demo: DemoScene,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "out"
    argv = [
        "robustness",
        "sweep",
        DEMO_GRID,
        "--manifest",
        str(SWEEP_MANIFEST),
        "--scenes",
        str(demo.scenes),
        "--out",
        str(out),
        "--run-id",
        "demo",
    ]
    main(argv)
    printed = capsys.readouterr().out
    assert f"wrote {out / 'sweep.json'}" in printed
    assert "cells 8 (4 perturbation combinations x 2 seeds), eval 1" in printed

    document = _document(out)
    validate_schema(document, SCHEMA)
    assert _obj(document["model"])["model"] == "degrade"
    assert _obj(document["dataset"])["scenes_root"] == str(demo.scenes)
    grid = _obj(document["grid"])
    assert grid["cells"] == 8 and grid["combinations"] == 4
    cells = _arr(document["cells"])
    run_ids = [_obj(cell)["run_id"] for cell in cells]
    assert len(set(run_ids)) == 8
    assert [_obj(cell)["canonical"] for cell in cells] == [
        "latency_frames=0,pose_noise_xy=0,seed=0",
        "latency_frames=0,pose_noise_xy=0,seed=1",
        "latency_frames=0,pose_noise_xy=1,seed=0",
        "latency_frames=0,pose_noise_xy=1,seed=1",
        "latency_frames=1,pose_noise_xy=0,seed=0",
        "latency_frames=1,pose_noise_xy=0,seed=1",
        "latency_frames=1,pose_noise_xy=1,seed=0",
        "latency_frames=1,pose_noise_xy=1,seed=1",
    ]
    for item in cells:
        run_id = str(_obj(item)["run_id"])
        assert (out / run_id / "run.json").is_file()
        assert (out / run_id / "demo-junction" / "boxes.parquet").is_file()

    # The fixture is built to degrade deterministically with pose noise: exact ground
    # truth (mAP 1) with no noise, strictly lower with 1 m of noise, for both seeds.
    for seed in (0, 1):
        clean = _map_at(cells, latency_frames=0, pose_noise_xy=0, seed=seed)
        noisy = _map_at(cells, latency_frames=0, pose_noise_xy=1, seed=seed)
        assert clean == pytest.approx(1.0)
        assert noisy < clean

    # Aggregates are the mean and sample standard deviation over the two seeds.
    aggregates = _arr(document["aggregates"])
    assert len(aggregates) == 4
    clean_combo = next(
        _obj(item)
        for item in aggregates
        if _obj(_obj(item)["spec"])["latency_frames"] == 0
        and _obj(_obj(item)["spec"])["pose_noise_xy"] == 0.0
    )
    stats = _obj(_obj(_obj(_obj(clean_combo["eval"])["default"])["mAP_display"]))
    assert stats["n"] == 2
    assert float(cast(float, stats["mean"])) == pytest.approx(1.0)
    assert float(cast(float, stats["std"])) == pytest.approx(0.0)
    # Two seeds draw different noise, so the noisy combination has a non-zero sample std.
    noisy_combo = next(
        _obj(item)
        for item in aggregates
        if _obj(_obj(item)["spec"])["latency_frames"] == 0
        and _obj(_obj(item)["spec"])["pose_noise_xy"] == 1.0
    )
    noisy_stats = _obj(_obj(_obj(_obj(noisy_combo["eval"])["default"])["mAP_display"]))
    assert noisy_stats["n"] == 2
    assert float(cast(float, noisy_stats["mean"])) < 1.0
    assert float(cast(float, noisy_stats["std"])) > 0.0

    # Charts: mAP against each swept axis with the other axis at its neutral value 0.
    charts = [_obj(item) for item in _arr(document["charts"])]
    assert {chart["axis"] for chart in charts} == {"latency_frames", "pose_noise_xy"}
    pose = next(chart for chart in charts if chart["axis"] == "pose_noise_xy")
    assert pose["eval"] == "default" and pose["metric"] == "mAP@0.5 all"
    points = [_obj(point) for point in _arr(pose["points"])]
    assert [point["value"] for point in points] == [0.0, 1.0]
    baseline = _obj(pose["baseline"])
    assert float(cast(float, baseline["mean"])) == pytest.approx(1.0)

    # A plain re-run reuses each cell's run directory without model work: every scene is
    # already written, so the batch reports it skipped and runs no frame and no child.
    before = (out / "sweep.json").read_bytes()
    one_cell = "latency_frames: [0]\npose_noise_xy: [1.0]\nseeds: [0]"
    reused = run_sweep(_options(demo.scenes, out, one_cell, run_id="demo"))
    reused_report = reused.cells[0].report
    assert reused_report.status == "complete"
    assert reused_report.skipped == ("demo-junction",) and reused_report.completed == ()
    assert reused_report.frames_run == 0 and reused_report.cache_hits == 0

    # A forced re-run answers every scene from the per-scene cache (no frames, no fresh
    # child) and the document does not move.
    single = run_sweep(_options(demo.scenes, out, one_cell, force=True, run_id="demo"))
    assert single.cells[0].run_id == _obj(cells[2])["run_id"]
    report = single.cells[0].report
    assert report.status == "complete"
    assert report.cache_hits == 1 and report.frames_run == 0
    assert report.completed == ("demo-junction",)
    run_id = str(_obj(cells[2])["run_id"])
    provenance = _obj(_obj(json.loads((out / run_id / "run.json").read_text(encoding="utf-8"))))
    assert _obj(provenance["provenance"])["cache_hits"] == 1
    assert _obj(provenance["provenance"])["frames_run"] == 0
    assert _obj(_obj(provenance["provenance"])["perturbation"]) == _obj(_obj(cells[2])["spec"])
    assert json.loads(before) == document


# --- determinism, multiple eval configs and the schema --------------------------------


def test_the_same_spec_and_seed_give_the_same_numbers(coop: Path, tmp_path: Path) -> None:
    definition = "pose_noise_xy: [0.0, 1.0]\nseeds: [0, 1]"
    first = run_sweep(_options(coop, tmp_path / "a", definition, cache=False))
    second = run_sweep(_options(coop, tmp_path / "b", definition, cache=False))
    assert len(first.cells) == 4
    assert [cell.run_id for cell in first.cells] == [cell.run_id for cell in second.cells]
    assert [cell.evaluations for cell in first.cells] == [cell.evaluations for cell in second.cells]
    # The output has no timestamp and no output path: independent runs write the same bytes.
    assert first.path.read_bytes() == second.path.read_bytes()


def test_every_eval_config_is_recorded_under_its_name(
    coop: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fine = tmp_path / "fine.yaml"
    fine.write_text(
        "criterion: iou_bev\nthresholds: [0.5]\ndisplay_threshold: 0.5\n", encoding="utf-8"
    )
    centre = tmp_path / "centre.yaml"
    centre.write_text(
        "criterion: centre\nthresholds: [2.0]\ndisplay_threshold: 2.0\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    argv = [
        "robustness",
        "sweep",
        "pose_noise_xy: [0.0, 1.0]\nseeds: [0]",
        "--manifest",
        str(FAKE),
        "--scenes",
        str(coop),
        "--out",
        str(out),
        "--run-id",
        "eval",
        "--config",
        str(fine),
        "--config",
        f"by-centre={centre}",
    ]
    main(argv)
    printed = capsys.readouterr().out
    assert "cells 2 (2 perturbation combinations x 1 seed), eval 2" in printed
    document = _document(out)
    validate_schema(document, SCHEMA)
    choices = [_obj(item) for item in _arr(document["eval"])]
    assert [choice["name"] for choice in choices] == ["fine", "by-centre"]
    assert _obj(_obj(choices[0])["config"])["criterion"] == "iou_bev"
    assert _obj(_obj(choices[1])["config"])["criterion"] == "centre"
    cells = _arr(document["cells"])
    assert len(cells) == 2
    for item in cells:
        evaluations = _obj(_obj(item)["eval"])
        assert set(evaluations) == {"fine", "by-centre"}
        fine_eval = _obj(evaluations["fine"])
        assert fine_eval["criterion"] == "iou_bev"
        assert set(_obj(fine_eval["mAP"])) == {"mAP@0.5 all"}
        assert _obj(_obj(fine_eval["mAP_display"]))["field"] == "mAP@0.5 all"
        centre_eval = _obj(evaluations["by-centre"])
        assert centre_eval["criterion"] == "centre"
        assert set(_obj(centre_eval["mAP"])) == {"mAP@2.0 all"}

    # A repeated config name is refused before anything runs.
    with pytest.raises(SystemExit) as info:
        main(
            [
                "robustness",
                "sweep",
                "pose_noise_xy: [0]",
                "--manifest",
                str(FAKE),
                "--scenes",
                str(coop),
                "--out",
                str(tmp_path / "never"),
                "--config",
                f"same={fine}",
                "--config",
                f"same={centre}",
            ]
        )
    assert info.value.code == 1
    assert "names 'same' twice" in capsys.readouterr().err
    assert not (tmp_path / "never").exists()

    # The schema is stable: a mutated document is refused at the named path.
    with pytest.raises(SchemaError, match="format"):
        validate_schema({**document, "format": "something-else"}, SCHEMA)
    without_cells = dict(document)
    del without_cells["cells"]
    with pytest.raises(SchemaError, match="missing cells"):
        validate_schema(without_cells, SCHEMA)
    bad = cast(dict[str, object], json.loads(json.dumps(document)))
    bad_cell = cast(dict[str, object], cast(list[object], bad["cells"])[0])
    cast(dict[str, object], bad_cell["spec"])["packet_loss"] = 2
    with pytest.raises(SchemaError, match="packet_loss"):
        validate_schema(bad, SCHEMA)


def test_a_max_agents_axis_leaves_no_chart_slice(coop: Path, tmp_path: Path) -> None:
    # max_agents has no neutral 0, so no other axis has a 0 slice and max_agents itself is
    # not a chart axis: the complete grid stays in aggregates, charts stays empty.
    result = run_sweep(
        _options(coop, tmp_path / "out", "max_agents: [1, 2]\npose_noise_xy: [0, 1]")
    )
    assert len(result.cells) == 4
    assert len(result.aggregates) == 4
    assert _arr(result.document["charts"]) == []


# --- Slurm ----------------------------------------------------------------------------


def test_slurm_emits_one_sbatch_per_cell(
    coop: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    logs = tmp_path / "logs"
    results = tmp_path / "results"
    argv = [
        "robustness",
        "sweep",
        "pose_noise_xy: [0.0, 1.0]\nseeds: [0]",
        "--manifest",
        str(FAKE),
        "--scenes",
        str(coop),
        "--out",
        str(out),
        "--run-id",
        "job",
        "--slurm",
        "--slurm-partition",
        "test-partition",
        "--slurm-scratch-template",
        "/scratch/$USER/$SLURM_JOB_ID",
        "--slurm-python",
        "/opt/venv/bin/python",
        "--slurm-logs",
        str(logs),
        "--slurm-results",
        str(results),
    ]
    main(argv)
    printed = capsys.readouterr().out
    assert printed.count("wrote ") == 2
    assert "no job submitted" in printed
    assert not (out / "sweep.json").exists()  # a sweep is written when it is evaluated

    slurm = out / ".slurm"
    scripts = sorted(slurm.glob("job-*.sbatch"))
    assert len(scripts) == 2
    assert len(list(slurm.glob("job-*.scenes.txt"))) == 2
    texts = [path.read_text(encoding="utf-8") for path in scripts]
    script = texts[0]
    assert "#SBATCH --partition=test-partition" in script
    assert "#SBATCH --array" not in script  # one sbatch per cell, not an array job
    assert 'SCRATCH="/scratch/$USER/$SLURM_JOB_ID"' in script
    assert "rsync -a --files-from=" in script
    assert '"$SCRATCH/scenes"' in script
    assert "--perturb" in script
    assert "pose_noise_xy=" in script
    assert "seed=0" in script
    assert f"mkdir -p {out.resolve()}/job-" in script  # the run is copied home
    assert 'exit "$RC"' in script
    assert not any(line.startswith("sbatch ") for line in script.splitlines())

    # Emission is deterministic: the same command writes the same bytes.
    main(argv)
    capsys.readouterr()
    assert [path.read_text(encoding="utf-8") for path in scripts] == texts


def test_slurm_has_no_submit_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = [
        "robustness",
        "sweep",
        "pose_noise_xy: [0]",
        "--manifest",
        str(FAKE),
        "--scenes",
        str(tmp_path),
        "--out",
        str(tmp_path / "out"),
        "--submit",
    ]
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 2  # argparse: unknown option
    assert "--submit" in capsys.readouterr().err
