# SPDX-License-Identifier: Apache-2.0
"""The OpenCOOD plugin: manifest, worker chain through the real runner, and its Slurm job."""

from __future__ import annotations

import ast
import hashlib
import shutil
import struct
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from coperceive.cli import main
from coperceive.evaluation.predictions import BoxFrame, read_boxes, read_run_meta
from coperceive.models.batch import RunOptions
from coperceive.models.slurm import plan_sbatch, write_plan
from coperceive.models.spec import load_manifest, render_argv, select_model
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.opencood_plugin import (
    FAKE_OPENCOOD_PARENT,
    MANIFEST,
    PLUGIN_DIR,
    config_bytes,
    copy_plugin_for_tests,
    load,
    torch_weights_bytes,
    write_checkpoint,
)

MODEL = "cobevt-lidar-v2xset"
PREDICTIONS = "opencood-fake"
SCENE_ID = "coop"


# -- the package and its manifest ------------------------------------------------------------


def test_manifest_declares_the_first_model_and_its_checkpoint_option() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest.name == "opencood" and manifest.version == "0.1.0"
    assert manifest.environment.kind == "conda"
    assert manifest.environment.value == "opencood"
    spec = select_model(manifest, None)
    assert spec.key == MODEL
    assert spec.checkpoint is not None and spec.checkpoint.option == "checkpoint"
    assert spec.options["checkpoint"].type == "str"
    assert spec.options["checkpoint"].default == ""
    assert spec.inputs.modalities == ("lidar",)
    assert spec.inputs.agents == ("vehicle", "infrastructure")
    assert spec.inputs.frames == "single"
    assert spec.outputs.categories == ("vehicle",)
    assert spec.outputs.agent == "vehicle"
    assert spec.resources.gpu is True
    assert spec.resources.max_inflight == 1
    argv = render_argv(
        manifest,
        "batch",
        python=Path("/usr/bin/python"),
        plugin_dir=PLUGIN_DIR,
        work_dir=Path("/tmp/work"),
        model=MODEL,
        options={"checkpoint": "/data/cobevt_lidar.zip"},
    )
    assert argv[-1] == "/data/cobevt_lidar.zip"
    assert argv[argv.index("--checkpoint") + 1] == "/data/cobevt_lidar.zip"


def test_manifest_states_the_open_cood_licence_truthfully() -> None:
    """Upstream OpenCOOD is the UCLA Academic Software License, not Apache-2.0."""
    manifest = load_manifest(MANIFEST)
    notice = manifest.licence.notice
    assert manifest.licence.spdx == "Apache-2.0"  # the plugin's own code only
    assert "OpenCOOD is NOT Apache-2.0" in notice
    assert "UCLA" in notice and "TDG-Attribution-NonCommercial-NoDistrib" in notice
    assert "github.com/DerrickXuNu/OpenCOOD/blob/main/LICENSE" in notice
    assert "checkpoint" in notice.lower() and "licence review" in notice


def test_plugin_code_imports_neither_coperceive_nor_opencood_statically() -> None:
    """The worker speaks the stdio protocol and imports OpenCOOD dynamically (opencood_engine)."""
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in ("coperceive", "opencood"), f"{path.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # The real package is ``opencood``; the plugin's own siblings are
                # ``opencood_convert``, ``opencood_engine``, … and are allowed.
                assert module != "opencood" and not module.startswith(
                    ("coperceive.", "opencood.")
                ), f"{path.name}: from {module}"
    # No vendored OpenCOOD package and no cluster path anywhere in the plugin.
    assert not list(PLUGIN_DIR.rglob("opencood/__init__.py"))
    for path in [*PLUGIN_DIR.glob("*.py"), MANIFEST, PLUGIN_DIR.parent / "README.md"]:
        text = path.read_text(encoding="utf-8")
        assert "/datasets/" not in text, path.name
        assert "eemcs" not in text, path.name


def test_engine_converts_the_injected_frame_to_numpy() -> None:
    """The real dataset needs ndarrays: lidar float32 (n, 4) and float64 4x4 matrices."""
    engine = load("engine")
    identity = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    frame = engine.dataset_frame(
        {
            "0": {
                "ego": True,
                "time_delay": 0.0,
                "lidar_np": [[1.0, 2.0, 3.0, 0.5], [4.0, 5.0, 6.0, 0.0]],
                "params": {
                    "lidar_pose": [1.0, 2.0, 3.0, 0.0, 0.0, 0.0],
                    "transformation_matrix": identity,
                    "gt_transformation_matrix": identity,
                    "spatial_correction_matrix": identity,
                    "vehicles": {},
                },
            }
        }
    )
    cav = cast(dict[str, object], frame["0"])
    lidar = cast(np.ndarray, cav["lidar_np"])
    params = cast(dict[str, object], cav["params"])
    assert isinstance(lidar, np.ndarray) and lidar.dtype == np.float32
    assert lidar.shape == (2, 4)
    for key in ("transformation_matrix", "gt_transformation_matrix", "spatial_correction_matrix"):
        matrix = cast(np.ndarray, params[key])
        assert isinstance(matrix, np.ndarray) and matrix.dtype == np.float64
        assert matrix.shape == (4, 4)
    assert params["lidar_pose"] == [1.0, 2.0, 3.0, 0.0, 0.0, 0.0]
    assert params["vehicles"] == {}
    with pytest.raises(engine.EngineError, match="lidar_np"):
        engine.dataset_frame({"0": {"lidar_np": [[1.0, 2.0]], "params": {"lidar_pose": [0.0] * 6}}})
    with pytest.raises(engine.EngineError, match="transformation_matrix"):
        engine.dataset_frame(
            {
                "0": {
                    "lidar_np": [[1.0, 2.0, 3.0, 0.0]],
                    "params": {
                        "lidar_pose": [0.0] * 6,
                        "transformation_matrix": [[1.0]],
                        "gt_transformation_matrix": identity,
                        "spatial_correction_matrix": identity,
                    },
                }
            }
        )


def test_worker_keeps_a_zero_score_threshold() -> None:
    """--option score_threshold=0 is a real override, not the falsy 'use the config' case."""
    worker = load("worker")
    assert worker._score_threshold({"options": {}}) == -1.0  # noqa: SLF001 - the parse helper
    assert worker._score_threshold({"options": {"score_threshold": 0.0}}) == 0.0  # noqa: SLF001
    assert worker._score_threshold({"options": {"score_threshold": 0.25}}) == 0.25  # noqa: SLF001
    assert worker._score_threshold({"options": {"score_threshold": -1.0}}) == -1.0  # noqa: SLF001
    assert worker._score_threshold({"options": {"score_threshold": None}}) == -1.0  # noqa: SLF001
    assert worker._score_threshold({"options": {"score_threshold": True}}) == -1.0  # noqa: SLF001


_SWEEP = struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)


def _sweep_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.bin"
    path.write_bytes(_SWEEP)
    return path


def _identity_pose(tx: float, ty: float = 0.0, tz: float = 0.0) -> dict[str, float]:
    return {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "tx": tx, "ty": ty, "tz": tz}


class _CapturingEngine:
    """Stands in for the OpenCOOD engine: records the injected frame and returns one box."""

    com_range = 70.0
    max_cav = 5

    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []

    def infer(self, base_data: dict[str, object], *, scene_id: str = "", time_us: int = 0):  # noqa: ANN201
        self.frames.append(base_data)
        return [
            {"center": [5.0, 0.0, -1.5], "size_lwh": [4.0, 2.0, 1.5], "heading": 0.0, "score": 0.9}
        ]


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast(dict[str, object], value)


def _capturing_emitter(emitted: list[dict[str, object]]):  # noqa: ANN201 - a monkeypatch value
    def capture(payload: dict[str, object]) -> None:
        emitted.append(payload)

    return capture


def _captured_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ego_agent: str = ""
) -> tuple[object, _CapturingEngine, list[dict[str, object]], object]:
    """A Worker with a capturing engine, run through configure (+one frame) in-process."""
    worker_module = load("worker")
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(worker_module, "_emit", _capturing_emitter(emitted))
    worker = worker_module.Worker("cobevt-lidar-v2xset", "")
    capturer = _CapturingEngine()
    worker.engine = capturer
    agent_types = {"-1": "infrastructure", "650": "vehicle", "641": "vehicle"}
    agents = [{"agent_id": agent_id, "agent_type": kind} for agent_id, kind in agent_types.items()]
    sensors = [
        {
            "agent_id": agent_id,
            "sensor_id": "lidar",
            "modality": "lidar",
            "body_from_sensor": {
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "tx": 0.0,
                "ty": 0.0,
                "tz": 0.0,
            },
        }
        for agent_id in agent_types
    ]
    options = {"checkpoint": "ck.zip", "ego_agent": ego_agent}
    worker._configure(  # noqa: SLF001 - the worker's own protocol step
        {
            "type": "configure",
            "protocol": 1,
            "model": "cobevt-lidar-v2xset",
            "scene_id": "two-vehicles",
            "agents": agents,
            "source": "fused",
            "options": options,
            "work_dir": str(tmp_path),
            "scene_dir": str(tmp_path),
            "device": "cpu",
            "sensors": sensors,
            "samples": [100_000],
        }
    )
    positions = {"-1": -10.0, "650": 20.0, "641": 40.0}
    frame = {
        "type": "frame",
        "protocol": 1,
        "id": 1,
        "scene_id": "two-vehicles",
        "time_us": 100_000,
        "poses": {agent_id: _identity_pose(x) for agent_id, x in positions.items()},
        "images": [],
        "points": [
            {
                "agent_id": agent_id,
                "sensor_id": "lidar",
                "path": str(_sweep_file(tmp_path, agent_id)),
                "count": 1,
                "layout": "xyzi_f32le",
            }
            for agent_id in positions
        ],
    }
    worker._frame(frame)  # noqa: SLF001 - the worker's own protocol step
    return worker, capturer, emitted, frame


def _result_boxes(emitted: list[dict[str, object]]) -> list[dict[str, object]]:
    results = [payload for payload in emitted if payload.get("type") == "result"]
    assert len(results) == 1
    return cast(list[dict[str, object]], results[0]["boxes"])


def test_choose_ego_defaults_to_first_vehicle_and_respects_a_pin() -> None:
    worker = load("worker")
    agents = [
        {"agent_id": "-1", "agent_type": "infrastructure"},
        {"agent_id": "650", "agent_type": "vehicle"},
        {"agent_id": "641", "agent_type": "vehicle"},
    ]
    # OpenCOOD's own rule: the first non-negative CAV, i.e. the first vehicle in scene order.
    assert worker._choose_ego(agents, "") == "650"  # noqa: SLF001
    assert worker._choose_ego(agents, "641") == "641"  # noqa: SLF001
    with pytest.raises(ValueError, match="not a listed agent"):
        worker._choose_ego(agents, "nope")  # noqa: SLF001
    with pytest.raises(ValueError, match="ego_agent"):
        worker._choose_ego([{"agent_id": "-1", "agent_type": "infrastructure"}], "")  # noqa: SLF001


def test_rsu_listed_first_still_picks_the_first_vehicle_as_ego(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reviewer's non-ego ordering failure cannot happen: the RSU sorts after the CAVs."""
    _, capturer, emitted, _ = _captured_worker(monkeypatch, tmp_path)
    configured = [payload for payload in emitted if payload.get("type") == "configured"]
    assert configured, emitted
    base_data = capturer.frames[0]
    assert list(base_data)[0] == "0"
    ego_content = _dict(base_data["0"])
    assert ego_content["ego"] is True
    params = _dict(ego_content["params"])
    assert cast(list[float], params["lidar_pose"])[0] == pytest.approx(20.0)
    # CAV ids: ego "0"; then the other vehicle "1" (641 at x=40); the RSU last, negative.
    assert list(base_data) == ["0", "1", "-1"]
    other = _dict(base_data["1"])
    assert other["ego"] is False
    other_params = _dict(other["params"])
    assert cast(list[float], other_params["lidar_pose"])[0] == pytest.approx(40.0)
    boxes = _result_boxes(emitted)
    assert boxes and boxes[0]["agent_id"] == "650"


def test_pinned_ego_agent_listed_second_is_used_and_attributed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, capturer, emitted, _ = _captured_worker(monkeypatch, tmp_path, ego_agent="641")
    base_data = capturer.frames[0]
    assert list(base_data)[0] == "0"
    ego_content = _dict(base_data["0"])
    assert ego_content["ego"] is True
    params = _dict(ego_content["params"])
    assert cast(list[float], params["lidar_pose"])[0] == pytest.approx(40.0)
    boxes = _result_boxes(emitted)
    assert boxes and boxes[0]["agent_id"] == "641"


def test_unknown_pinned_ego_is_an_unsupported_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker_module = load("worker")
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(worker_module, "_emit", _capturing_emitter(emitted))
    worker = worker_module.Worker("cobevt-lidar-v2xset", "")
    worker._configure(  # noqa: SLF001
        {
            "type": "configure",
            "protocol": 1,
            "model": "cobevt-lidar-v2xset",
            "scene_id": "two-vehicles",
            "agents": [{"agent_id": "650", "agent_type": "vehicle"}],
            "source": "fused",
            "options": {"checkpoint": "ck.zip", "ego_agent": "ghost"},
            "work_dir": str(tmp_path),
            "scene_dir": str(tmp_path),
            "device": "cpu",
            "sensors": [],
            "samples": [],
        }
    )
    errors = [payload for payload in emitted if payload.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "unsupported"
    assert "not a listed agent" in cast(str, errors[0]["message"])


def _new_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: dict[str, object], **kwargs: object
):  # noqa: ANN201 - the engine object, unittest style
    """An OpenCOODEngine over the fake OpenCOOD with ``state`` saved as its weights."""
    torch = pytest.importorskip("torch")
    monkeypatch.syspath_prepend(  # pyright: ignore[reportUnknownMemberType]
        str(FAKE_OPENCOOD_PARENT)
    )
    engine = load("engine")
    config_path = tmp_path / "config.yaml"
    config_path.write_bytes(config_bytes())
    weights_path = tmp_path / "weights.pth"
    torch.save({"model": state}, weights_path)
    return engine.OpenCOODEngine(
        str(config_path), str(weights_path), work_dir=str(tmp_path), **kwargs
    )


def test_engine_weights_are_strict_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing/unexpected keys must be a clear EngineError unless partial weights are opted in."""
    torch = pytest.importorskip("torch")
    engine = load("engine")
    with pytest.raises(engine.EngineError, match="allow_partial_weights"):
        _new_engine(monkeypatch, tmp_path, {"bogus.weight": torch.zeros(1)})
    logs: list[str] = []
    loaded = _new_engine(
        monkeypatch,
        tmp_path,
        {"fake.weight": torch.zeros(1), "bogus.weight": torch.zeros(1)},
        allow_partial_weights=True,
        log=logs.append,
    )
    assert loaded.partial_weights is True
    assert any("skipped" in line for line in logs)
    assert loaded.describe()["weights"] == "partial allowed"


def test_engine_pickle_fallback_is_opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The legacy pickle loader executes code: refused by default, named option to allow."""
    import pathlib

    torch = pytest.importorskip("torch")
    engine = load("engine")
    state: dict[str, object] = {"fake.weight": pathlib.PurePosixPath("/tmp/not-a-tensor")}
    with pytest.raises(engine.EngineError, match="trust_checkpoint_pickle"):
        _new_engine(monkeypatch, tmp_path, state)
    loaded = _new_engine(monkeypatch, tmp_path, state, trust_checkpoint_pickle=True)
    assert loaded.trust_checkpoint_pickle is True
    assert loaded.describe()["pickle_fallback"] == "allowed"
    assert torch is not None  # the import above is the real torch.load under test


def test_old_torch_without_weights_only_needs_the_pickle_opt_in(tmp_path: Path) -> None:
    """torch < 2.0 has no weights_only argument, so the legacy loader is the same opt-in."""
    engine = load("engine")
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"placeholder; the fake torch below never parses it")

    class _OldTorch:
        __version__: str = "1.9.1"

        def load(
            self, path: str, map_location: object = None, weights_only: object = None
        ) -> object:
            if weights_only is not None:
                raise TypeError("load() got an unexpected keyword argument 'weights_only'")
            return {"model": {"fake.weight": object()}}

    with pytest.raises(engine.EngineError, match="trust_checkpoint_pickle"):
        engine._load_weights(_OldTorch(), str(weights))  # noqa: SLF001 - the loader under test
    state = engine._load_weights(_OldTorch(), str(weights), trust_pickle=True)  # noqa: SLF001
    assert list(state) == ["fake.weight"]


def test_configure_without_a_work_dir_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No "." fallback: the worker refuses to extract/scratch into an arbitrary directory."""
    worker_module = load("worker")
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(worker_module, "_emit", _capturing_emitter(emitted))
    monkeypatch.delenv("COPERCEIVE_MODEL_WORK", raising=False)
    worker = worker_module.Worker("cobevt-lidar-v2xset", "")
    worker._configure(  # noqa: SLF001 - the worker's protocol step
        {
            "type": "configure",
            "protocol": 1,
            "model": "cobevt-lidar-v2xset",
            "scene_id": "no-work-dir",
            "agents": [{"agent_id": "650", "agent_type": "vehicle"}],
            "source": "fused",
            "options": {"checkpoint": "ck.zip"},
            "dev": "no work_dir here",
            "sensors": [
                {
                    "agent_id": "650",
                    "sensor_id": "lidar",
                    "modality": "lidar",
                    "body_from_sensor": {
                        "qw": 1.0,
                        "qx": 0.0,
                        "qy": 0.0,
                        "qz": 0.0,
                        "tx": 0.0,
                        "ty": 0.0,
                        "tz": 0.0,
                    },
                }
            ],
            "samples": [],
        }
    )
    errors = [payload for payload in emitted if payload.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "configure_failed"
    message = cast(str, errors[0]["message"])
    assert "work_dir" in message and "COPERCEIVE_MODEL_WORK" in message


def test_frame_seed_is_stable_per_scene_and_time() -> None:
    """Two model check passes reseed the same shuffle; different frames do not collide."""
    engine = load("engine")
    assert engine.frame_seed("coop", 100_000) == engine.frame_seed("coop", 100_000)
    assert engine.frame_seed("coop", 100_000) != engine.frame_seed("coop", 100_001)
    assert engine.frame_seed("coop", 100_000) != engine.frame_seed("other", 100_000)
    assert 0 <= engine.frame_seed("", 0) < 2**32


def test_worker_without_a_checkpoint_is_a_clean_skip_in_model_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Conformance for the real OpenCOOD needs a checkpoint; without one the model skips."""
    manifest = copy_plugin_for_tests(tmp_path)
    main(["model", "check", "--manifest", str(manifest)])
    out = capsys.readouterr().out
    assert "check opencood:" in out
    assert " 0 fail" in out
    assert "skipped" in out
    assert "--option checkpoint=FILE is required" in out  # the worker's actionable message
    assert "Traceback" not in out


# -- the full chain with the fake OpenCOOD ---------------------------------------------------


def test_model_run_eval_run_chain_with_the_fake_open_cood(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("torch")
    plugin_manifest = copy_plugin_for_tests(tmp_path)
    checkpoint = write_checkpoint(tmp_path / "cobevt_lidar.zip", weights=torch_weights_bytes())
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    predictions = tmp_path / "predictions"

    main(
        [
            "model",
            "run",
            "--manifest",
            str(plugin_manifest),
            "--scenes",
            str(scenes),
            "--out",
            str(predictions),
            "--run-id",
            PREDICTIONS,
            "--option",
            f"checkpoint={checkpoint}",
        ]
    )
    rows = read_boxes(predictions, PREDICTIONS, SCENE_ID)
    assert len(rows) == 1
    box = rows[0]
    # The fake OpenCOOD predicts a box at CARLA-ego (8, 2, -1.5), yaw 90, size lwh
    # (4, 2, 1.5), score 0.9; the ego is at schema world (10, 0, 0) heading 0 at 100_000 us.
    assert box.center == pytest.approx((18.0, -2.0, -1.5), abs=1e-9)
    assert box.size_lwh == pytest.approx((4.0, 2.0, 1.5), abs=1e-9)
    assert box.box_from_rpy.qw == pytest.approx(2**-0.5, abs=1e-9)  # schema yaw -90
    assert box.box_from_rpy.qz == pytest.approx(-(2**-0.5), abs=1e-9)
    assert box.category == "vehicle" and box.source == "fused"
    assert box.agent_id == "vehicle" and box.time_us == 100_000
    assert box.score == pytest.approx(0.9)
    assert box.frame is BoxFrame.WORLD

    meta = read_run_meta(predictions, PREDICTIONS)
    provenance = meta.provenance
    assert provenance is not None
    assert provenance["plugin"] == "opencood"
    assert provenance["model"] == MODEL
    assert provenance["status"] == "complete"
    environment = cast(dict[str, str], provenance["environment"])
    assert environment["checkpoint_md5"] == hashlib.md5(checkpoint.read_bytes()).hexdigest()  # noqa: S324
    assert environment["checkpoint_path"] == str(checkpoint.resolve())
    assert str(FAKE_OPENCOOD_PARENT) in environment["opencood_path"]
    record = cast(dict[str, object], provenance["checkpoint"])
    assert record["md5"] == environment["checkpoint_md5"]
    cache = (
        predictions
        / PREDICTIONS
        / "cache"
        / "opencood"
        / MODEL
        / environment["checkpoint_md5"]
        / SCENE_ID
    )
    assert (cache / "result.json").is_file()
    # The extracted checkpoint is a temporary tree: nothing large is left in the work dir.
    work_dir = predictions / ".work" / PREDICTIONS
    leftovers = [path.name for path in work_dir.iterdir() if "checkpoint" in path.name]
    assert leftovers == []
    log = (work_dir / "plugin.log").read_text(encoding="utf-8")
    assert "cobevt-lidar/config.yaml" in log
    assert "cobevt-lidar/net_epoch_bestval_at_10.pth" in log

    # The run is an ordinary predictions run: eval reads it unchanged.
    capsys.readouterr()
    main(
        [
            "eval",
            "run",
            PREDICTIONS,
            "--root",
            str(scenes),
            "--predictions",
            str(predictions),
        ]
    )
    out = capsys.readouterr().out
    # The synthetic ground truth has a pedestrian, which this run's categories do not; a
    # warning is fine, the evaluation must still score the scene.
    assert "evaluated 1 skipped 0 errors 0" in out


# -- Slurm -----------------------------------------------------------------------------------


def _plan(tmp_path: Path, checkpoint: Path):  # noqa: ANN202 - an slurm plan object
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    manifest = load_manifest(MANIFEST)
    return plan_sbatch(
        manifest,
        select_model(manifest, MODEL),
        scenes_root=scenes,
        scenes={SCENE_ID: scene},
        run_id="r1",
        out_root=tmp_path / "predictions",
        options=RunOptions(options={"checkpoint": str(checkpoint)}),
        python=Path("/opt/venv/bin/python"),
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
    )


def test_slurm_golden_stages_the_checkpoint_and_rewrites_the_option(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "cobevt_lidar.zip")
    plan = _plan(tmp_path, checkpoint)
    staged = f"$SCRATCH/checkpoints/checkpoint/{checkpoint.name}"
    assert plan.checkpoints == (("checkpoint", str(checkpoint.resolve()), staged),)
    script = plan.script
    # GPU job for the plugin, with the manifest's resources and no hard-coded cluster path.
    for needle in (
        "#SBATCH --partition=test-partition",
        "#SBATCH --gres=gpu:1",
        "#SBATCH --cpus-per-task=8",
        "#SBATCH --mem=16G",
        "#SBATCH --time=",
        'SCRATCH="/scratch/$USER/$SLURM_JOB_ID"',
        "module purge",
        "export PYTHONPATH=",
        "--manifest",
        f"--model {MODEL}",
        '--scenes "$SCRATCH/scenes"',
        '--out "$SCRATCH/out"',
        '--work "$SCRATCH/work"',
        "STAGING=$((SECONDS - STAGE_START))",
        '--staging-seconds "$STAGING"',
    ):
        assert needle in script, needle
    # The checkpoint is staged before the run, and the inner command reads the staged copy.
    stage_line = f'rsync -a {checkpoint.resolve()} "$SCRATCH/checkpoints/checkpoint/"'
    assert stage_line in script
    command = " ".join(plan.command)
    assert f'--option "checkpoint={staged}"' in command
    assert str(checkpoint.resolve()) not in command
    # Staged inside the timed rsync section, before the inner model run starts.
    assert script.index("STAGE_START=$SECONDS") < script.index(stage_line)
    assert script.index(stage_line) < script.index(" ".join(plan.command))
    # Never staged back home: only the staging lines mention the source path.
    assert script.count(str(checkpoint.resolve())) == 1
    copy_home = [line for line in script.splitlines() if 'rsync -a "$SCRATCH/out/' in line]
    assert copy_home and all("checkpoints" not in line for line in copy_home)
    # The scenes list is the lidar plugin's: points.bin travels, camera bytes never exist.
    sbatch_path = write_plan(plan, tmp_path / "predictions", "r1")
    scenes_list = sbatch_path.with_suffix(".scenes.txt")
    staged_files = scenes_list.read_text(encoding="utf-8").split()
    assert f"{SCENE_ID}/points.bin" in staged_files
    assert f"{SCENE_ID}/manifest.json" in staged_files
    assert not any(name.endswith(".png") for name in staged_files)
    if shutil.which("bash") is not None:
        done = subprocess.run(["bash", "-n", str(sbatch_path)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


# -- full-script golden (normalised machine paths) --------------------------------------------

GOLDEN_SBATCH = Path(__file__).parent / "fixtures" / "opencood" / "cobevt-lidar-v2xset-r1.sbatch"


def _normalised_sbatch(script: str, tmp_path: Path) -> str:
    """Replace machine/temp specifics so the whole script can be a committed golden.

    Same idea as the repo's other script goldens: substitute the temp root, the chosen
    interpreter, the checkout path and the home directory with placeholders, then compare
    the entire script, not fragments.
    """
    normalised = script.replace(str(tmp_path.resolve()), "<tmp>")
    normalised = normalised.replace(str(tmp_path), "<tmp>")
    normalised = normalised.replace("/opt/venv/bin/python", "<python>")
    normalised = normalised.replace("/opt/coperceive-checkout", "<repo>")
    # The inner command carries the manifest's own resolved path, which is this checkout:
    # normalise the plugin directory before the home prefix so the golden is machine-free.
    normalised = normalised.replace(str(MANIFEST.parent.resolve()), "<plugin>")
    normalised = normalised.replace(str(MANIFEST.parent), "<plugin>")
    normalised = normalised.replace(str(Path.home()), "<home>")
    return normalised.replace("\r\n", "\n")


def _golden_plan(tmp_path: Path):  # noqa: ANN201 - a coperceive SlurmPlan
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    checkpoint = write_checkpoint(tmp_path / "cobevt_lidar.zip")
    manifest = load_manifest(MANIFEST)
    return plan_sbatch(
        manifest,
        select_model(manifest, MODEL),
        scenes_root=scenes,
        scenes={SCENE_ID: scene},
        run_id="r1",
        out_root=tmp_path / "predictions",
        options=RunOptions(options={"checkpoint": str(checkpoint)}),
        python=Path("/opt/venv/bin/python"),
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        repo_root=Path("/opt/coperceive-checkout"),
    )


def test_full_sbatch_matches_the_normalised_golden(tmp_path: Path) -> None:
    """The whole emitted script equals the committed golden after path normalisation."""
    plan = _golden_plan(tmp_path)
    actual = _normalised_sbatch(plan.script, tmp_path)
    expected = GOLDEN_SBATCH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert actual == expected


def test_slurm_run_requires_the_checkpoint_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "model",
                "run",
                "--manifest",
                str(MANIFEST),
                "--scenes",
                str(scenes),
                "--out",
                str(tmp_path / "predictions"),
                "--run-id",
                "r1",
                "--slurm",
                "--slurm-partition",
                "test-partition",
                "--slurm-scratch-template",
                "/scratch/$USER/$SLURM_JOB_ID",
            ]
        )
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "--option checkpoint=FILE" in captured.err + captured.out
    assert not (tmp_path / "predictions" / ".slurm" / "r1.sbatch").exists()
