# SPDX-License-Identifier: Apache-2.0
"""The Griffin plugin's manifest through the model-plugin loader."""

from __future__ import annotations

from pathlib import Path

from coperceive.models.spec import load_manifest, select_model

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
    assert tuple(manifest.models) == ("vehicle-side", "drone-side")
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
    assert vehicle.resources.slurm_constraint == "a40|a100"
    assert vehicle.resources.seconds_per_sample == 0.394
    assert vehicle.resources.staging_seconds == 700
    assert set(vehicle.options) == {"subset", "min_score", "det_json", "nusc_root"}
    drone = select_model(manifest, "drone-side")
    assert drone.inputs.agents == ("uav",) and "track_query" in drone.outputs.artifacts
    assert manifest.commands.batch == ("{python}", "{plugin_dir}/worker.py", "--model", "{model}")
