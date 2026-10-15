# SPDX-License-Identifier: Apache-2.0
"""Torch dataset over schema 1 scene directories.

Import this module from the ``torch`` extra. It is not imported by
``coperceive`` itself, so the base install does not require torch.

The index is built in the parent process. ``__getstate__`` drops the cached
scene, so a worker does not receive an open file or a loaded point blob.
Each worker calls :func:`coperceive.store.read_scene` on first use and keeps
only the last scene.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pyarrow.parquet as pq
from torch.utils.data import Dataset

from coperceive.errors import TimeQueryError
from coperceive.model import Scene
from coperceive.query import TimeMode
from coperceive.sample import AgentSample, CooperativeSample
from coperceive.store import read_scene


def identity_collate(batch: list[CooperativeSample]) -> list[CooperativeSample]:
    """Return the batch as a list.

    The default torch collate cannot stack point clouds of different lengths,
    or a miss whose pose is null.
    """
    return list(batch)


class CooperativeDataset(Dataset[CooperativeSample]):
    """One cooperative sample per reference-agent pose time.

    ``root`` contains scene directories (a ``manifest.json`` in each child).
    Names starting with ``.`` are skipped. The reference agent is the first
    entry of ``agents``, or the first agent in the manifest when ``agents``
    is omitted. Its pose times, after ``stride``, are the index. ``stride``
    1 keeps every time. ``seed`` shuffles that index with
    ``random.Random(seed)`` before any worker starts. ``seed`` omitted keeps
    scene-directory name order, then time.

    ``tolerance_us`` is required and is passed to :meth:`Scene.at`. ``mode``
    defaults to nearest. ``agents`` limits which agents are returned, in that
    order, including misses. ``sensors`` limits sensors, images, and clouds.
    ``include_points`` false drops clouds after the scene is read.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        tolerance_us: int,
        agents: tuple[str, ...] | list[str] | None = None,
        sensors: tuple[str, ...] | list[str] | None = None,
        mode: TimeMode = TimeMode.NEAREST,
        stride: int = 1,
        seed: int | None = None,
        include_points: bool = True,
    ) -> None:
        super().__init__()
        if type(tolerance_us) is not int or tolerance_us < 0:
            raise TimeQueryError("tolerance_us must be a Python int >= 0")
        if not isinstance(mode, TimeMode):
            raise ValueError("mode must be a TimeMode")
        if type(stride) is not int or stride < 1:
            raise ValueError("stride must be an int >= 1")
        if seed is not None and type(seed) is not int:
            raise ValueError("seed must be an int or None")
        if type(include_points) is not bool:
            raise ValueError("include_points must be a bool")
        self._root = Path(root)
        self._tolerance_us = tolerance_us
        self._mode = mode
        self._stride = stride
        self._seed = seed
        self._include_points = include_points
        self._agents = _names(agents, "agents", allow_empty=False)
        self._sensors = _names(sensors, "sensors", allow_empty=True)
        self._index = _build_index(self._root, self._agents, stride, seed)
        self._cached_path: str | None = None
        self._cached_scene: Scene | None = None

    def __len__(self) -> int:
        return len(self._index)

    @property
    def index(self) -> tuple[tuple[str, int], ...]:
        """``(absolute scene directory, reference time_us)`` in sample order."""
        return self._index

    def __getitem__(self, index: int) -> CooperativeSample:
        path_str, time_us = self._index[index]
        sample = self._load(path_str).at(time_us, tolerance=self._tolerance_us, mode=self._mode)
        return self._narrow(sample)

    def __getstate__(self) -> dict[str, object]:
        state = dict(self.__dict__)
        state["_cached_scene"] = None
        state["_cached_path"] = None
        return state

    def __setstate__(self, state: dict[str, object]) -> None:
        self.__dict__.update(state)

    def _load(self, path_str: str) -> Scene:
        if self._cached_path == path_str and self._cached_scene is not None:
            return self._cached_scene
        scene = read_scene(Path(path_str))
        self._cached_path = path_str
        self._cached_scene = scene
        return scene

    def _narrow(self, sample: CooperativeSample) -> CooperativeSample:
        rows = sample.agents
        if self._agents is not None:
            by_id = {row.agent_id: row for row in rows}
            rows = tuple(by_id[agent_id] for agent_id in self._agents)
        if self._sensors is None and self._include_points and self._agents is None:
            return sample
        narrowed: list[AgentSample] = []
        allowed = self._sensors
        for row in rows:
            sensors = row.sensors
            images = row.images
            clouds = row.point_clouds if self._include_points else ()
            if allowed is not None:
                sensors = tuple(sensor for sensor in sensors if sensor.sensor_id in allowed)
                images = tuple(image for image in images if image.sensor_id in allowed)
                clouds = tuple(cloud for cloud in clouds if cloud.sensor_id in allowed)
            narrowed.append(
                AgentSample(
                    agent_id=row.agent_id,
                    matched=row.matched,
                    time_us=row.time_us,
                    dt_us=row.dt_us,
                    pose=row.pose,
                    sensors=sensors,
                    annotations=row.annotations,
                    images=images,
                    point_clouds=clouds,
                )
            )
        return CooperativeSample(
            query_us=sample.query_us,
            tolerance_us=sample.tolerance_us,
            mode=sample.mode,
            agents=tuple(narrowed),
        )


def _names(
    value: tuple[str, ...] | list[str] | None,
    what: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ValueError(f"{what} must be a sequence of strings")
    names = tuple(value)
    if not allow_empty and len(names) == 0:
        raise ValueError(f"{what} must name at least one entry")
    if any(type(name) is not str or name == "" for name in names):
        raise ValueError(f"{what} must be non-empty strings")
    if len(set(names)) != len(names):
        raise ValueError(f"{what} has a duplicate")
    return names


def _build_index(
    root: Path,
    agents: tuple[str, ...] | None,
    stride: int,
    seed: int | None,
) -> tuple[tuple[str, int], ...]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    order: list[tuple[str, int]] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if not (child / "manifest.json").is_file():
            continue
        times = _reference_times(child, agents)[::stride]
        directory = str(child.resolve())
        order.extend((directory, time_us) for time_us in times)
    if seed is not None:
        random.Random(seed).shuffle(order)
    return tuple(order)


def _reference_times(scene_dir: Path, agents: tuple[str, ...] | None) -> tuple[int, ...]:
    manifest_obj = json.loads((scene_dir / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest_obj, dict):
        raise ValueError(f"{scene_dir} manifest is not an object")
    rows = manifest_obj.get("agents")
    if not isinstance(rows, list) or len(rows) == 0:
        raise ValueError(f"{scene_dir} has no agents")
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{scene_dir} manifest agent is not an object")
        agent_id = row.get("agent_id")
        if type(agent_id) is not str or agent_id == "":
            raise ValueError(f"{scene_dir} manifest agent_id is missing")
        names.append(agent_id)
    if agents is not None:
        for agent_id in agents:
            if agent_id not in names:
                raise KeyError(f"{scene_dir.name} has no agent {agent_id}")
        reference = agents[0]
    else:
        reference = names[0]
    table = pq.read_table(scene_dir / "poses.parquet", columns=["agent_id", "time_us"])
    times: list[int] = []
    agent_col = table.column("agent_id").to_pylist()
    time_col = table.column("time_us").to_pylist()
    for agent_id, time_us in zip(agent_col, time_col, strict=True):
        if agent_id != reference:
            continue
        if type(time_us) is not int:
            raise ValueError(f"{scene_dir} pose time_us is not an int")
        times.append(time_us)
    if not times:
        raise ValueError(f"{scene_dir} reference agent {reference} has no poses")
    return tuple(sorted(set(times)))
