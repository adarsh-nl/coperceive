# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Scene,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from coperceive.validate import Thresholds, altitude_phase, validate_root, yaw_deg


def _pose(time_us: int, x: float, y: float, z: float) -> StampedPose:
    return StampedPose(time_us, SE3.identity().translation(x, y, z))


def _agent(
    agent_id: str,
    kind: AgentType,
    poses: tuple[StampedPose, ...],
    *,
    subset: str | None = None,
) -> Agent:
    platform: list[tuple[str, str]] = []
    if subset is not None:
        platform.append(("subset", subset))
        platform.append(("cruise_altitude_m", "25"))
    return Agent(agent_id, kind, poses, tuple(platform))


def _scene(
    scene_id: str,
    agents: tuple[Agent, ...],
    annotations: tuple[Annotation, ...] = (),
) -> Scene:
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock("us", "validate fixture"),
        agents=agents,
        sensors=(),
        annotations=annotations,
        objects=(),
    )


def _box(
    agent_id: str,
    track_id: str | None,
    category: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    yaw: float,
) -> Annotation:
    return Annotation(
        agent_id,
        0,
        track_id,
        category,
        center,
        size,
        SE3.from_rpy_deg(0.0, 0.0, yaw),
        None,
        BoxFrame.WORLD,
    )


def test_yaw_matches_the_griffin_rotation() -> None:
    assert yaw_deg(SE3.from_rpy_deg(0.0, 0.0, 90.0)) == pytest.approx(90.0)
    turned = SE3.from_rpy_deg(0.0, 0.0, 90.0).compose(SE3.from_rpy_deg(0.0, 0.0, 0.0))
    assert yaw_deg(turned) == pytest.approx(90.0)


def test_altitude_phase_labels_a_descent_and_a_climb() -> None:
    limits = Thresholds()
    descent = [26.0] * 20 + [24.0, 22.0, 20.0, 18.0, 16.0]
    kind, frames = altitude_phase(descent, thresholds=limits)
    assert kind == "descent"
    # The longest mostly-descending suffix also takes the last cruise sample.
    assert frames == 7
    climb = [16.0, 18.0, 20.0, 22.0, 24.0] + [26.0] * 20
    kind, frames = altitude_phase(climb, thresholds=limits)
    assert kind == "climb"
    assert frames == 7
    assert altitude_phase([26.0] * 20, thresholds=limits) == ("none", 0)


def test_injected_faults_are_reported(tmp_path: Path) -> None:
    dropped = _agent(
        "vehicle",
        AgentType.VEHICLE,
        (
            _pose(0, 0.0, 0.0, 0.0),
            _pose(100_000, 1.0, 0.0, 0.0),
            _pose(200_000, 2.0, 0.0, 0.0),
            _pose(400_000, 4.0, 0.0, 0.0),
        ),
        subset="25m",
    )
    jumper = _agent(
        "vehicle",
        AgentType.VEHICLE,
        (
            _pose(0, 0.0, 0.0, 0.0),
            _pose(100_000, 0.0, 0.0, 0.0),
            _pose(200_000, 100.0, 0.0, 0.0),
        ),
    )
    shifted_vehicle = _agent(
        "vehicle",
        AgentType.VEHICLE,
        (_pose(0, 0.0, 0.0, 0.0), _pose(100_000, 1.0, 0.0, 0.0), _pose(200_000, 2.0, 0.0, 0.0)),
    )
    shifted_uav = _agent(
        "uav",
        AgentType.UAV,
        (
            _pose(50_000, 0.0, 0.0, 25.0),
            _pose(150_000, 1.0, 0.0, 25.0),
            _pose(250_000, 2.0, 0.0, 25.0),
        ),
    )
    both_vehicle = _agent("vehicle", AgentType.VEHICLE, (_pose(0, 0.0, 0.0, 0.0),))
    both_uav = _agent("uav", AgentType.UAV, (_pose(0, 0.0, 0.0, 25.0),))
    boxes = (
        _box("vehicle", "1", "car", (0.0, 0.0, 0.0), (4.0, 2.0, 1.5), 0.0),
        _box("uav", "1", "car", (3.0, 0.0, 0.0), (6.0, 2.0, 1.5), 90.0),
        _box("vehicle", "9", "pedestrian", (10.0, 0.0, 0.0), (0.6, 0.6, 1.7), 0.0),
        _box("uav", "8", "sign", (11.0, 0.0, 0.0), (0.2, 0.8, 1.2), 0.0),
        _box("uav", "4", "truck", (100.0, 0.0, 0.0), (6.0, 2.5, 2.0), 0.0),
    )
    # Shared objects are required by the writer. Track 1 agrees. 9, 8, 4 do not.
    from coperceive.diagnostics import shared_objects

    agreed = _scene(
        "boxes",
        (both_vehicle, both_uav),
        boxes,
    )
    agreed = Scene(
        schema_version=agreed.schema_version,
        scene_id=agreed.scene_id,
        world_frame=agreed.world_frame,
        clock=agreed.clock,
        agents=agreed.agents,
        sensors=agreed.sensors,
        annotations=agreed.annotations,
        objects=shared_objects(agreed.annotations),
    )
    root = tmp_path / "scenes"
    write_scene(_scene("dropped", (dropped,)), root / "a-dropped")
    write_scene(_scene("jump", (jumper,)), root / "b-jump")
    write_scene(_scene("clock", (shifted_vehicle, shifted_uav)), root / "c-clock")
    write_scene(agreed, root / "d-boxes")

    report = validate_root(root)
    by_id = _by_scene(report)
    assert by_id["dropped"]["missing_frames"] == 1
    pose = _mapping(by_id["jump"]["pose"])
    flag_count = pose["flag_count"]
    assert isinstance(flag_count, int)
    assert flag_count >= 1
    assert float(_number(pose["max_speed_m_s"])) > 40.0
    cross = by_id["clock"]["cross_sync"]
    assert isinstance(cross, list)
    first = _mapping(cast(list[object], cross)[0])
    assert first["missed_at_sync_tolerance"] == 3
    nearest = _mapping(first["nearest_abs_dt_us"])
    assert nearest["median"] == 50_000.0
    agreement = _mapping(by_id["boxes"]["agreement"])
    shared = _mapping(agreement["shared_track"])
    computed = _mapping(agreement["computed"])
    assert shared["ground_truth"] is True
    assert shared["pairs"] == 1
    center = _mapping(shared["center_error_m"])
    yaw = _mapping(shared["yaw_error_deg"])
    assert center["median"] == pytest.approx(3.0)
    assert yaw["median"] == pytest.approx(90.0)
    assert computed["ground_truth"] is False
    assert computed["label"] == "computed"
    assert computed["pairs"] == 1
    coverage = _mapping(by_id["boxes"]["coverage"])
    assert coverage["both_tracks"] == 1
    assert coverage["vehicle_only_tracks"] == 1
    assert coverage["uav_only_tracks"] == 2
    official = report["official"]
    assert isinstance(official, list)
    subsets: dict[str, dict[str, object]] = {}
    for item in cast(list[object], official):
        row = _mapping(item)
        subset = row.get("subset")
        if isinstance(subset, str):
            subsets[subset] = row
    assert subsets["25m"]["measured_scenes"] == 1
    assert subsets["25m"]["expected_scenes"] == 47
    assert subsets["25m"]["scenes_match"] is False


def test_cli_writes_json_and_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "scenes"
    write_scene(
        _scene(
            "flat",
            (
                _agent(
                    "vehicle",
                    AgentType.VEHICLE,
                    tuple(_pose(index * 100_000, float(index), 0.0, 0.0) for index in range(6)),
                    subset="25m",
                ),
                _agent(
                    "uav",
                    AgentType.UAV,
                    tuple(_pose(index * 100_000, float(index), 0.0, 26.0) for index in range(6)),
                    subset="25m",
                ),
            ),
        ),
        root / "flat",
    )
    json_path = tmp_path / "out.json"
    markdown_path = tmp_path / "out.md"
    main(
        [
            "validate",
            str(root),
            "--json",
            str(json_path),
            "--markdown",
            str(markdown_path),
        ]
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["aggregate"]["missing_frames"] == 0
    assert payload["aggregate"]["altitude_dz_m"]["median"] == pytest.approx(26.0)
    text = markdown_path.read_text(encoding="utf-8")
    assert "coperceive validate" in text
    assert "computed_is_ground_truth" in text
    captured = capsys.readouterr()
    assert "scenes 1" in captured.out
    with pytest.raises(SystemExit) as raised:
        main(["validate", str(tmp_path / "missing")])
    assert raised.value.code == 2


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        assert isinstance(key, str)
        result[key] = item
    return result


def _by_scene(report: dict[str, object]) -> dict[str, dict[str, object]]:
    scenes = report["scenes"]
    assert isinstance(scenes, list)
    found: dict[str, dict[str, object]] = {}
    for item in cast(list[object], scenes):
        row = _mapping(item)
        scene_id = row["scene_id"]
        assert isinstance(scene_id, str)
        found[scene_id] = row
    return found


def _number(value: object) -> float:
    assert isinstance(value, (int, float))
    assert not isinstance(value, bool)
    return float(value)
