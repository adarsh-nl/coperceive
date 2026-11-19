# SPDX-License-Identifier: Apache-2.0
"""Seeded perturbations on synthetic agents. Ground truth is not modified."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.diagnostics import shared_objects
from coperceive.evaluation.predictions import read_run_meta
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    Clock,
    Scene,
    StampedPose,
)
from coperceive.models.batch import RunOptions, run_batch
from coperceive.models.inputs import build_frame
from coperceive.models.slurm import plan_sbatch
from coperceive.models.spec import (
    Inputs,
    ModelSpec,
    Outputs,
    Resources,
    load_manifest,
    select_model,
)
from coperceive.robustness.apply import (
    export_perturbed_inputs,
    frame_rng,
    perturb_sample,
    standard_normal,
)
from coperceive.robustness.spec import PerturbationSpec
from coperceive.se3 import SE3
from coperceive.store import read_scene, write_scene

from tests.fake_model_plugin import MANIFEST as FAKE


def _pose(time_us: int, x: float) -> StampedPose:
    return StampedPose(time_us, SE3.identity().translation(x, 0.0, 0.0))


def _scene() -> Scene:
    times = (0, 100_000, 200_000)
    agents = (
        Agent("vehicle", AgentType.VEHICLE, tuple(_pose(t, t / 10_000) for t in times)),
        Agent("uav", AgentType.UAV, tuple(_pose(t, 5.0 + t / 10_000) for t in times)),
        Agent("mast", AgentType.INFRASTRUCTURE, tuple(_pose(t, 100.0) for t in times)),
    )
    annotations = (
        Annotation("vehicle", 0, "1", "car", (1.0, 0.0, 0.0), (4.0, 2.0, 1.5), SE3.identity()),
        Annotation("uav", 0, "1", "car", (1.0, 0.0, 0.0), (4.0, 2.0, 1.5), SE3.identity()),
    )
    return Scene(
        SCHEMA_VERSION,
        "synth",
        "world",
        Clock("us", "perturbation fixture"),
        agents,
        (),
        annotations,
        shared_objects(annotations),
    )


def _at(scene: Scene, time_us: int, spec: PerturbationSpec):
    sample = scene.at(time_us, tolerance=0)
    return perturb_sample(scene, sample, spec, ego_agent_id="vehicle")


def test_parse_and_each_perturbation_has_an_exact_result() -> None:
    spec = PerturbationSpec.parse("latency_frames=2,pose_noise_xy=0.2,seed=7")
    assert spec.latency_frames == 2 and spec.pose_noise_xy == 0.2 and spec.seed == 7
    assert PerturbationSpec.parse(spec.canonical()) == spec

    scene = _scene()
    delayed = _at(scene, 200_000, PerturbationSpec(latency_frames=1, seed=1))
    uav = delayed.agent("uav")
    assert uav.time_us == 100_000
    assert uav.pose is not None and uav.pose.tx == 15.0

    earlier = _at(scene, 200_000, PerturbationSpec(latency_ms=50, seed=1))
    pose = earlier.agent("uav").pose
    assert pose is not None and pose.tx == 15.0  # 200 ms - 50 ms lands on 100 ms, x = 15

    both = _at(scene, 200_000, PerturbationSpec(latency_frames=1, latency_ms=150, seed=1))
    both_pose = both.agent("uav").pose
    # frames steps to 100 ms (x=15); 150 ms steps to 0 (x=5). The earlier one is kept.
    assert both_pose is not None and both_pose.tx == 5.0

    dropped = _at(scene, 200_000, PerturbationSpec(packet_loss=1, seed=1))
    assert dropped.agent("uav").matched is False and dropped.agent("uav").pose is None
    kept = _at(scene, 200_000, PerturbationSpec(packet_loss=0, seed=1))
    kept_pose = kept.agent("uav").pose
    assert kept_pose is not None and kept_pose.tx == 25.0

    noisy_spec = PerturbationSpec(pose_noise_xy=0.2, seed=7)
    rng = frame_rng(7, scene.scene_id, 200_000)
    dx = standard_normal(rng) * 0.2
    dy = standard_normal(rng) * 0.2
    noisy = _at(scene, 200_000, noisy_spec).agent("uav").pose
    assert noisy is not None
    assert noisy.tx == pytest.approx(25.0 + dx)
    assert noisy.ty == pytest.approx(dy)
    assert noisy.tz == 0.0

    only_ego = _at(scene, 200_000, PerturbationSpec(max_agents=1, seed=1))
    assert [row.agent_id for row in only_ego.agents] == ["vehicle"]


def test_dropout_is_applied_before_the_agent_cap() -> None:
    scene = _scene()
    seed = 0
    probability = 0.0
    for candidate in range(200):
        draw = frame_rng(candidate, scene.scene_id, 0)
        first = draw.random()
        second = draw.random()
        if first < second:
            seed = candidate
            probability = (first + second) / 2
            break
    sample = _at(scene, 0, PerturbationSpec(agent_dropout=probability, max_agents=2, seed=seed))
    assert [row.agent_id for row in sample.agents] == ["vehicle", "mast"]


def test_noise_is_applied_to_the_delayed_pose_and_is_deterministic() -> None:
    scene = _scene()
    spec = PerturbationSpec(latency_frames=1, pose_noise_xy=0.2, seed=3)
    rng = frame_rng(3, scene.scene_id, 200_000)
    dx = standard_normal(rng) * 0.2
    first = _at(scene, 200_000, spec)
    second = _at(scene, 200_000, spec)
    pose = first.agent("uav").pose
    assert pose is not None and second.agent("uav").pose == pose
    assert pose.tx == pytest.approx(15.0 + dx)


def test_ground_truth_is_not_perturbed(tmp_path: Path) -> None:
    scene = _scene()
    before = scene.annotations
    centers = [ann.center for ann in before]
    spec = PerturbationSpec(latency_frames=2, pose_noise_xy=1, packet_loss=0.5, seed=4)
    _at(scene, 200_000, spec)
    assert scene.annotations is before
    assert [ann.center for ann in scene.annotations] == centers
    export_perturbed_inputs(scene, PerturbationSpec(seed=4, max_agents=1), tmp_path / "out")
    document = json.loads((tmp_path / "out" / "frames.json").read_text(encoding="utf-8"))
    truth = cast(list[object], document["ground_truth"])
    assert len(truth) == 2
    assert cast(dict[str, object], truth[0])["center"] == [1.0, 0.0, 0.0]


def test_build_frame_delays_the_other_agents_camera(tmp_path: Path) -> None:
    from coperceive.models.synthetic import write_synthetic_scene

    spec = _model_spec()
    write_synthetic_scene(tmp_path, spec)
    scene = read_scene(tmp_path / "synthetic-check")
    frame, _files = build_frame(
        scene,
        tmp_path / "synthetic-check",
        spec,
        frame_id=1,
        time_us=300_000,
        agent_ids=("vehicle", "uav"),
        work_dir=tmp_path / "work",
        media_roots=(),
        raw_root=None,
        perturbation=PerturbationSpec(latency_frames=1, seed=1),
        ego_agent_id="vehicle",
    )
    paths = [image.path for image in frame.images]
    assert any(path.endswith("0300000.png") and "vehicle" in path for path in paths)
    assert any(path.endswith("0200000.png") and "uav" in path for path in paths)
    assert not any(path.endswith("0300000.png") and "uav" in path for path in paths)
    assert frame.poses["vehicle"]["tx"] == 3.0
    assert frame.poses["uav"]["tx"] == 5.0


def test_model_run_records_the_spec_and_leaves_the_scene_bytes(
    tmp_path: Path,
) -> None:
    from tests.cooperative import cooperative_scene

    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    original = (scenes / "coop" / "annotations.parquet").read_bytes()
    spec = PerturbationSpec.parse("max_agents=1,seed=7")
    report = run_batch(
        load_manifest(FAKE),
        scenes,
        tmp_path / "out",
        "r1",
        RunOptions(perturbation=spec, cache=False),
        created_utc="2026-11-02T00:00:00Z",
        parent_env={"PATH": "/usr/bin", "HOME": str(tmp_path)},
    )
    assert report.status == "complete"
    assert (scenes / "coop" / "annotations.parquet").read_bytes() == original
    meta = read_run_meta(tmp_path / "out", "r1")
    assert meta.provenance is not None
    stored = meta.provenance["perturbation"]
    assert isinstance(stored, dict) and stored["max_agents"] == 1 and stored["seed"] == 7
    assert (tmp_path / "out" / "r1" / "perturbation.json").is_file()
    frames = tmp_path / "out" / "r1" / "perturbed" / "coop" / "frames.json"
    assert frames.is_file()
    body = json.loads(frames.read_text(encoding="utf-8"))
    assert body["ground_truth"]
    manifest = load_manifest(FAKE)
    plan = plan_sbatch(
        manifest,
        select_model(manifest, "default"),
        scenes_root=scenes,
        scenes={"coop": read_scene(scenes / "coop")},
        run_id="r1",
        out_root=tmp_path / "out",
        options=RunOptions(perturbation=spec),
        python=Path("/opt/venv/bin/python"),
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="cpu",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
    )
    assert "--perturb" in plan.script
    assert "max_agents=1" in plan.script


def test_perturb_inputs_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene = _scene()
    root = tmp_path / "scenes"
    write_scene(scene, root / scene.scene_id)
    out = tmp_path / "export"
    main(
        [
            "model",
            "perturb-inputs",
            "--scenes",
            str(root),
            "--scene",
            scene.scene_id,
            "--perturb",
            "latency_frames=1,seed=2",
            "--out",
            str(out),
        ]
    )
    assert "wrote" in capsys.readouterr().out
    assert (out / "perturbation.json").is_file()
    document = json.loads((out / "frames.json").read_text(encoding="utf-8"))
    assert document["ego_agent_id"] == "vehicle"
    assert document["ground_truth"][0]["category"] == "car"


def _model_spec() -> ModelSpec:
    return ModelSpec(
        key="toy",
        description=None,
        checkpoint_md5=None,
        inputs=Inputs(
            modalities=("camera",),
            agents=None,
            sensors=("*",),
            frames="single",
            raw=(),
            raw_agent_roots={},
            raw_clock=None,
            other_runs=False,
        ),
        outputs=Outputs(
            categories=("car",),
            score_note="constant",
            sources=(("single", "own"),),
            agent="request",
            artifacts=(),
            deterministic=True,
        ),
        resources=Resources(
            gpu=False,
            memory_gb=1,
            seconds_per_sample=1.0,
            staging_seconds=0.0,
            conversion_seconds=0.0,
            frame_timeout_s=1.0,
            run_timeout_s=None,
            grace_s=1.0,
            slurm_constraint=None,
            max_inflight=1,
        ),
        depends_on=(),
        options={},
    )
