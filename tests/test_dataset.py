# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pickle
from pathlib import Path
from typing import cast

import pytest
from coperceive.errors import TimeQueryError
from coperceive.model import SCHEMA_VERSION, Agent, AgentType, Clock, Scene, StampedPose
from coperceive.query import TimeMode
from coperceive.sample import CooperativeSample
from coperceive.se3 import SE3
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene


def _shifted(scene_id: str, tx: float) -> Scene:
    source = cooperative_scene()
    vehicle = source.agent("vehicle")
    poses = tuple(
        StampedPose(pose.time_us, pose.world_from_body.translation(tx, 0.0, 0.0))
        for pose in vehicle.poses
    )
    agents = tuple(
        Agent(agent.agent_id, agent.agent_type, poses, agent.platform)
        if agent.agent_id == "vehicle"
        else agent
        for agent in source.agents
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame=source.world_frame,
        clock=source.clock,
        agents=agents,
        sensors=source.sensors,
        annotations=source.annotations,
        objects=source.objects,
        point_clouds=source.point_clouds,
        images=source.images,
    )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "scenes"
    write_scene(cooperative_scene(), root / "a-coop")
    write_scene(_shifted("shifted", 3.0), root / "b-shift")
    (root / ".hidden").mkdir()
    write_scene(
        Scene(
            schema_version=SCHEMA_VERSION,
            scene_id="ignored",
            world_frame="ENU",
            clock=Clock("us", "hidden"),
            agents=(Agent("vehicle", AgentType.VEHICLE, (StampedPose(0, SE3.identity()),)),),
            sensors=(),
            annotations=(),
            objects=(),
        ),
        root / ".hidden" / "nope",
    )
    return root


def test_index_filters_and_seeds(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from coperceive.dataset import CooperativeDataset

    root = _root(tmp_path)
    ordered = CooperativeDataset(root, tolerance_us=0)
    assert len(ordered) == 8
    assert [Path(path).name for path, _ in ordered.index] == ["a-coop"] * 4 + ["b-shift"] * 4
    assert [time_us for _, time_us in ordered.index[:4]] == [0, 100_000, 200_000, 300_000]

    stepped = CooperativeDataset(root, tolerance_us=0, stride=2)
    assert [time_us for _, time_us in stepped.index] == [0, 200_000, 0, 200_000]

    uav = CooperativeDataset(root, tolerance_us=0, agents=("uav",))
    assert [time_us for _, time_us in uav.index[:4]] == [50_000, 100_000, 160_000, 200_000]
    assert [row.agent_id for row in uav[1].agents] == ["uav"]

    seeded = CooperativeDataset(root, tolerance_us=0, seed=1)
    again = CooperativeDataset(root, tolerance_us=0, seed=1)
    other = CooperativeDataset(root, tolerance_us=0, seed=2)
    assert seeded.index == again.index
    assert seeded.index != other.index
    assert seeded.index != ordered.index

    with pytest.raises(KeyError):
        CooperativeDataset(root, tolerance_us=0, agents=("missing",))
    with pytest.raises(TimeQueryError):
        CooperativeDataset(root, tolerance_us=-1)
    with pytest.raises(ValueError):
        CooperativeDataset(root, tolerance_us=0, stride=0)
    with pytest.raises(ValueError):
        CooperativeDataset(root, tolerance_us=0, seed=True)  # type: ignore[arg-type]


def test_sample_filters_drop_sensors_and_points(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from coperceive.dataset import CooperativeDataset

    root = _root(tmp_path)
    cameras = CooperativeDataset(
        root,
        tolerance_us=0,
        sensors=("cam_front",),
        include_points=False,
    )
    sample = cameras[1]
    vehicle = sample.agent("vehicle")
    assert vehicle.time_us == 100_000
    assert [sensor.sensor_id for sensor in vehicle.sensors] == ["cam_front"]
    assert len(vehicle.images) == 1
    assert vehicle.point_clouds == ()
    assert sample.agent("uav").sensors == ()
    assert sample.mode is TimeMode.NEAREST

    missed = cameras[0].agent("uav")
    assert missed.matched is False
    assert missed.dt_us is None
    assert missed.sensors == ()


def test_workers_match_and_pickle_drops_the_cache(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from coperceive.dataset import CooperativeDataset, identity_collate
    from torch.utils.data import DataLoader

    root = _root(tmp_path)
    dataset = CooperativeDataset(root, tolerance_us=0, seed=1)
    direct = [dataset[index] for index in range(len(dataset))]
    assert dataset.cached_scene_path is not None

    restored = pickle.loads(pickle.dumps(dataset))
    assert isinstance(restored, CooperativeDataset)
    assert restored.cached_scene_path is None
    assert [restored[index] for index in range(len(restored))] == direct

    def collect(workers: int) -> list[CooperativeSample]:
        loader = DataLoader(
            dataset,
            batch_size=2,
            num_workers=workers,
            shuffle=False,
            collate_fn=identity_collate,
        )
        rows: list[CooperativeSample] = []
        for batch in loader:
            assert isinstance(batch, list)
            for item in cast(list[object], batch):
                assert isinstance(item, CooperativeSample)
                rows.append(item)
        return rows

    assert collect(0) == direct
    assert collect(2) == direct
