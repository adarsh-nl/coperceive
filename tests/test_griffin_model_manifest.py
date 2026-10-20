# SPDX-License-Identifier: Apache-2.0
"""The Griffin plugin's manifest through the model-plugin loader."""

from __future__ import annotations

from pathlib import Path

from coperceive.models.spec import Dependency, load_manifest, select_model

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "griffin"
    / "coperceive_griffin_model"
    / "model.yaml"
)


def test_griffin_manifest_loads() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest.name == "griffin" and manifest.version == "0.1.0"
    assert tuple(manifest.models) == (
        "vehicle-side",
        "drone-side",
        "instance-fusion",
        "early-fusion",
    )
    assert manifest.environment.kind == "conda" and manifest.environment.value == "griffin"
    assert manifest.environment.modules == ("nvidia/cuda-11.8", "nvidia/cuda-11.x_cudnn-8.6")
    assert manifest.environment.inherit_ld_library_path is True
    assert manifest.licence.spdx == "MIT"
    assert manifest.licence.citation is not None and "arXiv:2503.06983" in manifest.licence.citation
    vehicle = select_model(manifest, "vehicle-side")
    assert vehicle.checkpoint_md5 == "1201f5692e390a75e5b5ab0efa0cae19"
    assert vehicle.inputs.agents == ("vehicle",) and vehicle.inputs.frames == "sequence"
    assert vehicle.inputs.sensors == ("front", "back", "left", "right")
    assert vehicle.inputs.raw_clock == (100000, 0)
    assert any("camera/{sensor}/{frame:06d}.png" in t.text for t in vehicle.inputs.raw)
    assert vehicle.outputs.categories == ("car", "bicycle", "pedestrian")
    assert vehicle.outputs.sources == (
        ("single", "vehicle-side camera-only Agile model, no fusion"),
    )
    assert (
        vehicle.resources.gpu and vehicle.resources.cpus == 8 and vehicle.resources.memory_gb == 64
    )
    assert vehicle.resources.conversion_seconds == 100
    assert vehicle.resources.slurm_constraint == "a40"
    assert vehicle.resources.seconds_per_sample == 0.394
    assert vehicle.resources.staging_seconds == 700
    assert set(vehicle.options) == {"subset", "min_score", "det_json", "nusc_root"}
    drone = select_model(manifest, "drone-side")
    assert drone.inputs.agents == ("uav",) and "track_query" in drone.outputs.artifacts
    assert drone.resources.slurm_constraint == "a40"
    assert manifest.commands.batch == ("{python}", "{plugin_dir}/worker.py", "--model", "{model}")


def test_griffin_manifest_instance_fusion_depends_on_the_drone_pass() -> None:
    manifest = load_manifest(MANIFEST)
    fusion = select_model(manifest, "instance-fusion")
    assert fusion.depends_on == (Dependency("drone-side", "track_query"),)
    assert fusion.checkpoint_md5 == "7e1448188b6e99ca6303575c3466b97f"
    assert fusion.inputs.agents == ("vehicle",)
    assert fusion.outputs.sources == (
        ("fused", "instance fusion of vehicle queries with drone track queries"),
    )
    assert fusion.resources.seconds_per_sample == 0.421
    assert fusion.resources.slurm_constraint == "a40"
    drone = select_model(manifest, "drone-side")
    assert drone.outputs.transient_artifacts == ("track_query",)
    assert drone.resources.seconds_per_sample == 0.461 and drone.resources.staging_seconds == 930
    # No drone camera is needed by instance fusion itself: the drone pass stages its own.
    assert not any("drone-side/camera" in t.text for t in fusion.inputs.raw)
    assert any("drone-side/camera" in t.text for t in drone.inputs.raw)


def test_griffin_manifest_early_fusion_stages_both_camera_sets() -> None:
    manifest = load_manifest(MANIFEST)
    early = select_model(manifest, "early-fusion")
    assert early.checkpoint_md5 == "ccf39651d3e4381ec06e4d0709821949"
    assert early.depends_on == ()
    assert early.inputs.agents == ("vehicle",)
    assert early.outputs.sources == (("fused", "early fusion of vehicle and drone cameras"),)
    assert early.outputs.agent == "vehicle"
    texts = [t.text for t in early.inputs.raw]
    assert "vehicle-side/camera/{sensor}/{frame:06d}.png" in texts
    for sensor in ("front", "back", "left", "right", "bottom"):
        assert f"drone-side/camera/{sensor}/{{frame:06d}}.png" in texts
    assert early.resources.seconds_per_sample == 0.538
    assert early.resources.staging_seconds == 1611
    assert early.resources.conversion_seconds == 112
    assert early.resources.slurm_constraint == "a40"
