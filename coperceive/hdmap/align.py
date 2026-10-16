# SPDX-License-Identifier: Apache-2.0
"""Choose the OpenDRIVE-to-world map by measuring vehicle poses.

Candidates are horizontal rotations and reflections that keep z up. Translation
is added only when it reduces the median horizontal distance by at least one
metre. A small residual is left in the measurement instead of being absorbed
into the origin. ``tz`` stays 0. Only a horizontal shift is considered.
"""

from __future__ import annotations

from pathlib import Path

from coperceive.hdmap.frame import FrameMap, Matrix3
from coperceive.hdmap.index import SegmentIndex
from coperceive.hdmap.measure import heading_error_deg, yaw_deg
from coperceive.hdmap.opendrive import LAYER_LANE_CENTRE, MapPolyline
from coperceive.hdmap.store import HdMap, load_map, scene_map_name, write_alignment
from coperceive.model import AgentType, Scene
from coperceive.store import read_scene

_TRANSLATION_GAIN_M = 1.0

_CANDIDATES: tuple[tuple[str, Matrix3], ...] = (
    ("identity", ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    ("reflect_y", ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))),
    ("reflect_x", ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    ("rot180", ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))),
    ("rot90", ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
    ("rot270", ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
    ("swap", ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
    ("swap_neg", ((0.0, -1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
)

_AXIS_NOTE = {
    "identity": "Griffin +X equals OpenDRIVE +X, and Griffin +Y equals OpenDRIVE +Y.",
    "reflect_y": "Griffin +X equals OpenDRIVE +X, and Griffin +Y equals OpenDRIVE −Y.",
    "reflect_x": "Griffin +X equals OpenDRIVE −X, and Griffin +Y equals OpenDRIVE +Y.",
    "rot180": "Griffin +X equals OpenDRIVE −X, and Griffin +Y equals OpenDRIVE −Y.",
    "rot90": "Griffin +X equals OpenDRIVE −Y, and Griffin +Y equals OpenDRIVE +X.",
    "rot270": "Griffin +X equals OpenDRIVE +Y, and Griffin +Y equals OpenDRIVE −X.",
    "swap": "Griffin +X equals OpenDRIVE +Y, and Griffin +Y equals OpenDRIVE +X.",
    "swap_neg": "Griffin +X equals OpenDRIVE −Y, and Griffin +Y equals OpenDRIVE −X.",
}


def align_maps(maps_root: Path, scenes_root: Path) -> list[dict[str, object]]:
    """Measure each map against scenes that name it, and store the transform."""
    if not maps_root.is_dir():
        raise FileNotFoundError(maps_root)
    if not scenes_root.is_dir():
        raise FileNotFoundError(scenes_root)
    grouped: dict[str, list[Scene]] = {}
    for path in sorted(scenes_root.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or not path.is_dir():
            continue
        if not (path / "manifest.json").is_file():
            continue
        scene = read_scene(path)
        name = scene_map_name(scene)
        if name is None:
            continue
        grouped.setdefault(name, []).append(scene)
    reports: list[dict[str, object]] = []
    for path in sorted(maps_root.iterdir(), key=lambda item: item.name):
        if not (path / "manifest.json").is_file():
            continue
        hdmap = load_map(path)
        scenes = grouped.get(hdmap.map_name, [])
        if not scenes:
            reports.append(
                {
                    "map_name": hdmap.map_name,
                    "measured": False,
                    "reason": "no scene with this map_name",
                }
            )
            continue
        report = _align_one(hdmap, scenes)
        frame = report.pop("_frame")
        if not isinstance(frame, FrameMap):
            raise TypeError("alignment frame missing")
        alignment = {key: value for key, value in report.items() if key != "_frame"}
        write_alignment(hdmap.directory, frame, alignment)
        reports.append(alignment)
    return reports


def _align_one(hdmap: HdMap, scenes: list[Scene]) -> dict[str, object]:
    poses = _vehicle_poses(scenes)
    lanes = tuple(item for item in hdmap.polylines if item.layer == LAYER_LANE_CENTRE)
    candidates: list[dict[str, object]] = []
    best_name = ""
    best_key: tuple[float, float] | None = None
    best_r: Matrix3 = _CANDIDATES[0][1]
    for name, matrix in _CANDIDATES:
        stats = _score(lanes, matrix, (0.0, 0.0, 0.0), poses)
        row: dict[str, object] = {
            "name": name,
            "median_m": stats[0],
            "heading_median_deg": stats[1],
            "poses": stats[2],
        }
        candidates.append(row)
        if stats[0] is None or stats[1] is None:
            continue
        key = (stats[0], stats[1])
        if best_key is None or key < best_key:
            best_key = key
            best_name = name
            best_r = matrix
    if best_key is None:
        raise ValueError(f"{hdmap.map_name} produced no lane-centre distance")
    unshifted = _score(lanes, best_r, (0.0, 0.0, 0.0), poses)
    translation = _median_offset(lanes, best_r, poses)
    shifted = _score(lanes, best_r, translation, poses)
    use_translation = (
        unshifted[0] is not None
        and shifted[0] is not None
        and unshifted[0] - shifted[0] >= _TRANSLATION_GAIN_M
    )
    t = translation if use_translation else (0.0, 0.0, 0.0)
    final = _score(lanes, best_r, t, poses)
    frame = FrameMap(best_r, t, True)
    median = final[0]
    heading = final[1]
    return {
        "_frame": frame,
        "measured": True,
        "map_name": hdmap.map_name,
        "scenes": len(scenes),
        "vehicle_poses": len(poses),
        "chosen": best_name,
        "translation_applied": use_translation,
        "translation_m": [t[0], t[1], t[2]],
        "unshifted_median_m": unshifted[0],
        "shifted_median_m": shifted[0],
        "residual_median_m": median,
        "heading_median_deg": heading,
        "candidates": candidates,
        "geo_reference": hdmap.geo_reference,
        "note": _note(hdmap.geo_reference, best_name, use_translation, median, heading),
    }


def _vehicle_poses(scenes: list[Scene]) -> list[tuple[float, float, float]]:
    poses: list[tuple[float, float, float]] = []
    for scene in scenes:
        for agent in scene.agents:
            if agent.agent_type is not AgentType.VEHICLE:
                continue
            for stamped in agent.poses:
                pose = stamped.world_from_body
                poses.append((pose.tx, pose.ty, yaw_deg(pose)))
    return poses


def _score(
    lanes: tuple[MapPolyline, ...],
    matrix: Matrix3,
    translation: tuple[float, float, float],
    poses: list[tuple[float, float, float]],
) -> tuple[float | None, float | None, int]:
    frame = FrameMap(matrix, translation, True)
    mapped = tuple(
        MapPolyline(
            layer=item.layer,
            road_id=item.road_id,
            lane_id=item.lane_id,
            side=item.side,
            lane_type=item.lane_type,
            junction_id=item.junction_id,
            xyz=tuple(frame.apply(point) for point in item.xyz),
        )
        for item in lanes
    )
    index = SegmentIndex(mapped)
    distances: list[float] = []
    headings: list[float] = []
    for x, y, yaw in poses:
        hit = index.nearest(x, y)
        if hit is None:
            continue
        distances.append(hit.distance_m)
        headings.append(heading_error_deg(yaw, hit.travel_heading_rad))
    return (_median(distances), _median(headings), len(distances))


def _median_offset(
    lanes: tuple[MapPolyline, ...],
    matrix: Matrix3,
    poses: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    frame = FrameMap(matrix, (0.0, 0.0, 0.0), True)
    mapped = tuple(
        MapPolyline(
            layer=item.layer,
            road_id=item.road_id,
            lane_id=item.lane_id,
            side=item.side,
            lane_type=item.lane_type,
            junction_id=item.junction_id,
            xyz=tuple(frame.apply(point) for point in item.xyz),
        )
        for item in lanes
    )
    index = SegmentIndex(mapped)
    dx: list[float] = []
    dy: list[float] = []
    for x, y, _yaw in poses:
        hit = index.nearest(x, y)
        if hit is None:
            continue
        dx.append(x - hit.closest_x)
        dy.append(y - hit.closest_y)
    return (_median(dx) or 0.0, _median(dy) or 0.0, 0.0)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _note(
    geo_reference: str,
    chosen: str,
    translation: bool,
    median: float | None,
    heading: float | None,
) -> str:
    axis = _AXIS_NOTE.get(chosen, chosen)
    projected = any(token in geo_reference for token in ("+proj=tmerc", "+proj=utm", "+proj=merc"))
    if projected:
        crs = (
            " The header geoReference is a projected metre CRS. "
            "In PROJ, +X is easting and +Y is northing."
        )
    else:
        crs = (
            " The header geoReference does not name a PROJ projected CRS, "
            "so east is not taken from it."
        )
    if translation:
        shift = (
            " A horizontal translation is stored because it cut "
            "the median residual by at least 1 m. tz stays 0."
        )
    else:
        shift = " No horizontal translation is stored. tz stays 0."
    return (
        f"{axis}{crs}{shift} "
        f"Measured vehicle-pose median distance to the driving-lane centre is {median} m "
        f"and the median heading error is {heading} degrees. "
        "This is the Griffin world against the OpenDRIVE file, not a compass survey."
    )
