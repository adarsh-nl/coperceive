# SPDX-License-Identifier: Apache-2.0
"""Name a scene's CARLA town by the ADR 0008 alignment residual (ADR 0012, decision 6).

OPV2V and V2XSet files do not name their town. For every ingested map the vehicle
poses of a scene are scored at the identity transform (every CARLA town so far aligned
at identity); the best town is accepted only when its median lane-centre distance is at
most ``accept_m`` and the runner-up is at least ``margin_m`` worse. Otherwise the scene
stays map-less and the table says why. The result is a platform table that
``coperceive adapters ingest --platform-table`` applies: ``{scene_id: {map_name: ...}}``
plus ``_``-prefixed notes that the loader ignores.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from coperceive.hdmap.align import score_identity, vehicle_poses
from coperceive.hdmap.store import HdMap, load_map
from coperceive.model import Scene
from coperceive.store import read_scene

DEFAULT_ACCEPT_M = 0.5
DEFAULT_MARGIN_M = 2.0


@dataclass(frozen=True)
class TownGuess:
    scene_id: str
    map_name: str | None
    status: str  # identified | unidentified | no-poses | no-maps
    reason: str
    poses: int
    best: str | None
    best_m: float | None
    best_heading_deg: float | None
    runner_up: str | None
    runner_up_m: float | None
    residuals: tuple[tuple[str, float], ...]

    def table_row(self) -> dict[str, object]:
        row: dict[str, object] = {}
        if self.map_name is not None:
            row["map_name"] = self.map_name
        row["_status"] = self.status
        row["_reason"] = self.reason
        row["_poses"] = self.poses
        row["_best"] = self.best
        row["_best_m"] = self.best_m
        row["_best_heading_deg"] = self.best_heading_deg
        row["_runner_up"] = self.runner_up
        row["_runner_up_m"] = self.runner_up_m
        return row


def identify_town(
    scene: Scene,
    maps: Sequence[HdMap],
    *,
    accept_m: float = DEFAULT_ACCEPT_M,
    margin_m: float = DEFAULT_MARGIN_M,
) -> TownGuess:
    poses = vehicle_poses([scene])
    if not maps:
        return _guess(scene.scene_id, None, "no-maps", "no map directories", len(poses), ())
    if not poses:
        return _guess(scene.scene_id, None, "no-poses", "no vehicle poses", 0, ())
    scored: list[tuple[str, float, float]] = []
    for hdmap in maps:
        median, heading, count = score_identity(hdmap, poses)
        if median is None or count == 0:
            continue
        scored.append((hdmap.map_name, median, heading if heading is not None else float("nan")))
    scored.sort(key=lambda item: item[1])
    residuals = tuple((name, median) for name, median, _ in scored)
    if not scored:
        return _guess(
            scene.scene_id,
            None,
            "unidentified",
            "no lane centre near any pose",
            len(poses),
            residuals,
        )
    best_name, best_m, _best_heading = scored[0]
    runner = scored[1] if len(scored) > 1 else None
    if best_m > accept_m:
        reason = f"best residual {best_m:.3f} m is above {accept_m} m"
        return _guess(
            scene.scene_id, None, "unidentified", reason, len(poses), residuals, scored[0], runner
        )
    if runner is not None and runner[1] - best_m < margin_m:
        reason = (
            f"runner-up {runner[0]} at {runner[1]:.3f} m is within {margin_m} m of {best_m:.3f} m"
        )
        return _guess(
            scene.scene_id, None, "unidentified", reason, len(poses), residuals, scored[0], runner
        )
    reason = f"{best_name} at {best_m:.3f} m" + (
        f", runner-up {runner[0]} at {runner[1]:.3f} m" if runner else ""
    )
    return _guess(
        scene.scene_id, best_name, "identified", reason, len(poses), residuals, scored[0], runner
    )


def _guess(
    scene_id: str,
    map_name: str | None,
    status: str,
    reason: str,
    poses: int,
    residuals: tuple[tuple[str, float], ...],
    best: tuple[str, float, float] | None = None,
    runner: tuple[str, float, float] | None = None,
) -> TownGuess:
    return TownGuess(
        scene_id=scene_id,
        map_name=map_name,
        status=status,
        reason=reason,
        poses=poses,
        best=best[0] if best else None,
        best_m=best[1] if best else None,
        best_heading_deg=best[2] if best else None,
        runner_up=runner[0] if runner else None,
        runner_up_m=runner[1] if runner else None,
        residuals=residuals,
    )


def load_maps(maps_root: Path) -> list[HdMap]:
    if not maps_root.is_dir():
        raise FileNotFoundError(maps_root)
    out: list[HdMap] = []
    for path in sorted(maps_root.iterdir(), key=lambda item: item.name):
        if path.is_dir() and (path / "manifest.json").is_file():
            out.append(load_map(path))
    return out


def identify_towns(
    maps_root: Path,
    scenes_root: Path,
    *,
    accept_m: float = DEFAULT_ACCEPT_M,
    margin_m: float = DEFAULT_MARGIN_M,
) -> list[TownGuess]:
    if not scenes_root.is_dir():
        raise FileNotFoundError(scenes_root)
    maps = load_maps(maps_root)
    guesses: list[TownGuess] = []
    for path in sorted(scenes_root.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or not (path / "manifest.json").is_file():
            continue
        guesses.append(identify_town(read_scene(path), maps, accept_m=accept_m, margin_m=margin_m))
    return guesses


def write_town_table(guesses: Sequence[TownGuess], path: Path) -> None:
    """A platform table ``scene_id -> {map_name, _notes...}``; no ``map_name`` when unidentified."""
    table = {guess.scene_id: guess.table_row() for guess in guesses}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(table, sort_keys=True), encoding="utf-8")
