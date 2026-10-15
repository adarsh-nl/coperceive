# SPDX-License-Identifier: Apache-2.0
"""Check a directory of schema 1 scenes and write a report.

The altitude bands and the official scene counts are the figures in the
Griffin toolkit ``Dataset_Preparation.md``, as already recorded in
``docs/design/griffin-verification.md``: 25 m is 47 scenes at 25±2 m, 40 m is
54 scenes at 40±2 m, 55 m is 50 scenes at 55±2 m, and random is 104 scenes at
20–60 m. That note says each scene is about 150 frames. It does not give an
exact frame total. ``cruise_altitude`` in the release is a per-scene value,
copied into ``platform`` as ``cruise_altitude_m``. It is not a per-frame
drone-minus-vehicle height.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.assoc import AssociationKind, associate
from coperceive.errors import CoperceiveError
from coperceive.model import Agent, AgentType, Annotation, Scene
from coperceive.query import TimeMode, query_agents
from coperceive.se3 import SE3
from coperceive.store import read_scene
from coperceive.transform import lookup_pose

# Bands and scene counts from Dataset_Preparation.md via griffin-verification.md.
# (official scene count, altitude low m, altitude high m)
_EXPECTATIONS: dict[str, tuple[int, float, float]] = {
    "25m": (47, 23.0, 27.0),
    "40m": (54, 38.0, 42.0),
    "55m": (50, 53.0, 57.0),
    "random": (104, 20.0, 60.0),
}
_FRAMES_ABOUT = 150


@dataclass(frozen=True)
class Thresholds:
    """Limits the report uses. They are stated in the output, not hidden."""

    max_speed_m_s: float = 40.0
    max_accel_m_s2: float = 20.0
    smooth_window_s: float = 0.5
    gap_factor: float = 1.5
    sync_tolerance_us: int = 0
    nearest_tolerance_us: int = 1_000_000
    computed_max_center_m: float = 2.0
    computed_size_fraction: float = 0.5
    phase_delta_m: float = 2.0
    phase_min_frames: int = 5
    phase_max_fraction: float = 0.4
    phase_step_m: float = 0.05
    phase_step_fraction: float = 0.8


@dataclass(frozen=True)
class Distribution:
    count: int
    minimum: float | None
    p05: float | None
    median: float | None
    p95: float | None
    maximum: float | None

    def as_json(self) -> dict[str, object]:
        return {
            "count": self.count,
            "min": self.minimum,
            "p05": self.p05,
            "median": self.median,
            "p95": self.p95,
            "max": self.maximum,
        }


def distribution(values: list[float]) -> Distribution:
    if not values:
        return Distribution(0, None, None, None, None, None)
    ordered = sorted(values)
    return Distribution(
        count=len(ordered),
        minimum=ordered[0],
        p05=_percentile(ordered, 0.05),
        median=_percentile(ordered, 0.5),
        p95=_percentile(ordered, 0.95),
        maximum=ordered[-1],
    )


def yaw_deg(pose: SE3) -> float:
    """Yaw of ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``, in degrees, range (-180, 180]."""
    r00 = 1.0 - 2.0 * (pose.qy * pose.qy + pose.qz * pose.qz)
    r10 = 2.0 * (pose.qx * pose.qy + pose.qw * pose.qz)
    return math.degrees(math.atan2(r10, r00))


def altitude_phase(
    heights_m: list[float],
    *,
    thresholds: Thresholds,
) -> tuple[str, int]:
    """Return ``(none|climb|descent, frame count)``.

    A climb is the longest prefix, and a descent the longest suffix, whose
    median is at least ``phase_delta_m`` below the median of the other frames
    and whose steps mostly move toward that other part. Both matching is left
    as ``none`` so a mixed scene is not given a single label.
    """
    count = len(heights_m)
    if count < thresholds.phase_min_frames * 2:
        return ("none", 0)
    max_k = int(math.floor(count * thresholds.phase_max_fraction))
    if max_k < thresholds.phase_min_frames:
        return ("none", 0)
    climb = _longest_end(
        heights_m,
        prefix=True,
        max_k=max_k,
        thresholds=thresholds,
    )
    descent = _longest_end(
        heights_m,
        prefix=False,
        max_k=max_k,
        thresholds=thresholds,
    )
    if climb and descent:
        return ("none", 0)
    if climb:
        return ("climb", climb)
    if descent:
        return ("descent", descent)
    return ("none", 0)


def validate_root(
    root: Path,
    thresholds: Thresholds | None = None,
    maps: Path | None = None,
) -> dict[str, object]:
    """Read every scene directory under ``root`` and return a JSON-ready report."""
    limits = Thresholds() if thresholds is None else thresholds
    if not root.is_dir():
        raise FileNotFoundError(root)
    maps_root = _maps_root(root, maps)
    paths = _scene_dirs(root)
    scenes: list[dict[str, object]] = []
    pooled = _Pools()
    centreline_pool = _CentrelinePool()
    map_cache: dict[str, object] = {}
    for path in paths:
        try:
            scene = read_scene(path)
        except (OSError, ValueError, CoperceiveError) as exc:
            scenes.append(
                {
                    "path": str(path),
                    "scene_id": path.name,
                    "error": str(exc),
                }
            )
            continue
        row = _scene_report(scene, path, limits, pooled)
        row["centreline"] = _centreline_row(scene, maps_root, map_cache, centreline_pool)
        scenes.append(row)
    report: dict[str, object] = {
        "root": str(root.resolve()),
        "thresholds": _thresholds_json(limits),
        "sources": (
            "Altitude bands and official scene counts are the Dataset_Preparation.md "
            "figures recorded in docs/design/griffin-verification.md: "
            "25m is 47 scenes at 25±2 m, 40m is 54 at 40±2 m, 55m is 50 at 55±2 m, "
            "random is 104 at 20–60 m. That note says about 150 frames and about 15 s "
            "per scene. cruise_altitude_m is the release's per-scene cruise value."
        ),
        "scenes": scenes,
        "official": _official(scenes),
    }
    aggregate = pooled.aggregate(scenes, limits)
    aggregate["centreline"] = centreline_pool.aggregate(maps_root)
    hard = _hard_failures(scenes)
    aggregate["hard_failure_count"] = len(hard)
    aggregate["hard_failures"] = hard[:50]
    report["aggregate"] = aggregate
    report["checks"] = {
        "warnings": (
            "Instantaneous speed above max_speed_m_s, instantaneous acceleration "
            "above max_accel_m_s2, and smoothed acceleration above the same "
            "acceleration threshold are warnings. Smoothed speed is the trailing "
            "mean of instantaneous speed over smooth_window_s. They do not change "
            "the exit code."
        ),
        "hard_failures": (
            "An unreadable scene, a missing frame, or a cross-agent pose miss at "
            "sync_tolerance_us. The default exit code is 0 after the report is "
            "written. --strict exits 1 when hard_failure_count is not zero."
        ),
        "computed_gate": (
            "max(computed_max_center_m, computed_size_fraction * the longer box "
            "length). The pair stays labelled computed and is not ground truth."
        ),
    }
    report["altitude_finding"] = _altitude_finding(report)
    return report


def render_markdown(report: dict[str, object]) -> str:
    """Render the JSON-ready report as Markdown."""
    lines: list[str] = ["# coperceive validate", ""]
    lines.append(f"Root: `{report['root']}`")
    lines.append("")
    lines.append(str(report["sources"]))
    lines.append("")
    lines.append("## Thresholds")
    lines.append("")
    thresholds = _as_dict(report["thresholds"], "thresholds")
    for key in sorted(thresholds):
        lines.append(f"- `{key}`: {thresholds[key]}")
    lines.append("")
    lines.append("## Official counts")
    lines.append("")
    lines.extend(_official_markdown(_as_list(report["official"], "official")))
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(str(report["altitude_finding"]))
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report["aggregate"], indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## Scenes")
    lines.append("")
    for item in _as_list(report["scenes"], "scenes"):
        row = _as_dict(item, "scene")
        title = row.get("scene_id", row.get("path", "scene"))
        lines.append(f"### {title}")
        lines.append("")
        if "error" in row:
            lines.append(f"Read failed: {row['error']}")
            lines.append("")
            continue
        lines.extend(_bullet_block(row))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(report: dict[str, object], json_path: Path, markdown_path: Path) -> None:
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


class _Pools:
    def __init__(self) -> None:
        self.dz: list[float] = []
        self.cruise_dz: list[float] = []
        self.phase_dz: list[float] = []
        self.bias: list[float] = []
        self.shared_center: list[float] = []
        self.shared_yaw: list[float] = []
        self.shared_length: list[float] = []
        self.shared_width: list[float] = []
        self.shared_height: list[float] = []
        self.computed_center: list[float] = []
        self.computed_yaw: list[float] = []
        self.speeds: list[float] = []
        self.accels: list[float] = []
        self.smoothed_accels: list[float] = []
        self.missing_frames = 0
        self.cross_queries = 0
        self.cross_misses = 0
        self.nearest_abs_dt: list[float] = []
        self.vehicle_only = 0
        self.uav_only = 0
        self.both = 0
        self.speed_flags = 0
        self.accel_flags = 0
        self.smoothed_accel_flags = 0
        self.phases: Counter[str] = Counter()

    def aggregate(self, scenes: list[dict[str, object]], limits: Thresholds) -> dict[str, object]:
        readable = [row for row in scenes if "error" not in row]
        return {
            "scene_directories": len(scenes),
            "scenes_read": len(readable),
            "scenes_failed": len(scenes) - len(readable),
            "missing_frames": self.missing_frames,
            "cross_agent_queries": self.cross_queries,
            "cross_agent_misses_at_sync_tolerance": self.cross_misses,
            "nearest_abs_dt_us": distribution(self.nearest_abs_dt).as_json(),
            "max_speed_m_s": None if not self.speeds else max(self.speeds),
            "max_accel_m_s2": None if not self.accels else max(self.accels),
            "max_smoothed_accel_m_s2": (
                None if not self.smoothed_accels else max(self.smoothed_accels)
            ),
            "speed_warnings": self.speed_flags,
            "acceleration_warnings": self.accel_flags,
            "smoothed_acceleration_warnings": self.smoothed_accel_flags,
            "speed_threshold_m_s": limits.max_speed_m_s,
            "acceleration_threshold_m_s2": limits.max_accel_m_s2,
            "smooth_window_s": limits.smooth_window_s,
            "altitude_dz_m": distribution(self.dz).as_json(),
            "altitude_cruise_dz_m": distribution(self.cruise_dz).as_json(),
            "altitude_phase_dz_m": distribution(self.phase_dz).as_json(),
            "altitude_bias_median_dz_minus_cruise_m": distribution(self.bias).as_json(),
            "altitude_phases": dict(sorted(self.phases.items())),
            "shared_track_pairs": len(self.shared_center),
            "shared_center_error_m": distribution(self.shared_center).as_json(),
            "shared_yaw_error_deg": distribution(self.shared_yaw).as_json(),
            "shared_length_error_m": distribution(self.shared_length).as_json(),
            "shared_width_error_m": distribution(self.shared_width).as_json(),
            "shared_height_error_m": distribution(self.shared_height).as_json(),
            "computed_pairs": len(self.computed_center),
            "computed_center_error_m": distribution(self.computed_center).as_json(),
            "computed_yaw_error_deg": distribution(self.computed_yaw).as_json(),
            "computed_is_ground_truth": False,
            "vehicle_only_tracks": self.vehicle_only,
            "uav_only_tracks": self.uav_only,
            "both_tracks": self.both,
        }


class _CentrelinePool:
    def __init__(self) -> None:
        self.pose_lane: list[float] = []
        self.pose_reference: list[float] = []
        self.box_lane: list[float] = []
        self.heading: list[float] = []
        self.scenes_measured = 0
        self.scenes_skipped = 0

    def aggregate(self, maps_root: Path | None) -> dict[str, object]:
        lane = distribution(self.pose_lane)
        payload: dict[str, object] = {
            "maps_root": None if maps_root is None else str(maps_root),
            "scenes_measured": self.scenes_measured,
            "scenes_skipped": self.scenes_skipped,
            "vehicle_pose_to_lane_centre_m": lane.as_json(),
            "vehicle_pose_to_reference_m": distribution(self.pose_reference).as_json(),
            "vehicle_box_to_lane_centre_m": distribution(self.box_lane).as_json(),
            "vehicle_pose_heading_error_deg": distribution(self.heading).as_json(),
            "claim_m": 0.24,
            "lane_centre_median_minus_claim_m": (
                None if lane.median is None else lane.median - 0.24
            ),
            "claim_note": (
                "0.24 m is the Town03 road-centreline figure recorded in "
                "docs/design/griffin-verification.md before this map check. "
                "lane_centre_median_minus_claim_m is the measured lane-centre median "
                "minus that figure, not a pass/fail gate."
            ),
        }
        if maps_root is None:
            payload["status"] = "not measured"
            payload["reason"] = "no maps directory"
        elif self.scenes_measured == 0:
            payload["status"] = "not measured"
            payload["reason"] = "no scene linked to a map in the maps directory"
        else:
            payload["status"] = "measured"
        return payload


def _maps_root(root: Path, maps: Path | None) -> Path | None:
    if maps is not None:
        if not maps.is_dir():
            raise FileNotFoundError(maps)
        return maps
    candidate = root / "maps"
    if not candidate.is_dir():
        return None
    for child in candidate.iterdir():
        if child.is_dir() and (child / "manifest.json").is_file():
            return candidate
    return None


def _centreline_row(
    scene: Scene,
    maps_root: Path | None,
    cache: dict[str, object],
    pool: _CentrelinePool,
) -> dict[str, object]:
    if maps_root is None:
        pool.scenes_skipped += 1
        return {"status": "not measured", "reason": "no maps directory"}
    from coperceive.hdmap.measure import collect_centreline, indexes_for, sample_as_json
    from coperceive.hdmap.store import find_map, load_map, scene_map_name

    map_name = scene_map_name(scene)
    if map_name is None:
        pool.scenes_skipped += 1
        return {"status": "not measured", "reason": "scene has no single map_name"}
    cached = cache.get(map_name)
    if cached is None:
        try:
            directory = find_map(maps_root, map_name)
        except ValueError as exc:
            pool.scenes_skipped += 1
            return {"status": "not measured", "map_name": map_name, "reason": str(exc)}
        if directory is None:
            cache[map_name] = None
        else:
            hdmap = load_map(directory)
            cache[map_name] = (hdmap, indexes_for(hdmap))
        cached = cache[map_name]
    if cached is None:
        pool.scenes_skipped += 1
        return {
            "status": "not measured",
            "map_name": map_name,
            "reason": "map asset is not in the maps directory",
        }
    from coperceive.hdmap.index import SegmentIndex
    from coperceive.hdmap.store import HdMap

    pair = cast("tuple[HdMap, tuple[SegmentIndex, SegmentIndex]]", cached)
    hdmap, indexes = pair
    lanes, reference = indexes
    sample = collect_centreline(scene, lanes, reference)
    pool.pose_lane.extend(sample.pose_lane_m)
    pool.pose_reference.extend(sample.pose_reference_m)
    pool.box_lane.extend(sample.box_lane_m)
    pool.heading.extend(sample.heading_error_deg)
    pool.scenes_measured += 1
    return sample_as_json(hdmap, sample)


def _scene_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if (child / "manifest.json").is_file():
            found.append(child)
    return found


def _scene_report(
    scene: Scene,
    path: Path,
    limits: Thresholds,
    pooled: _Pools,
) -> dict[str, object]:
    subset = _subset(scene)
    sync_rows, missing = _time_sync(scene, limits)
    pooled.missing_frames += missing
    cross_rows = _cross_sync(scene, limits, pooled)
    pose = _poses(scene, limits, pooled)
    altitude = _altitude(scene, subset, limits, pooled)
    agreement = _agreement(scene, limits, pooled)
    coverage = _coverage(scene, pooled)
    frames = {agent.agent_id: len(agent.poses) for agent in scene.agents}
    return {
        "path": str(path),
        "scene_id": scene.scene_id,
        "subset": subset,
        "frames": frames,
        "time_sync": sync_rows,
        "missing_frames": missing,
        "cross_sync": cross_rows,
        "pose": pose,
        "altitude": altitude,
        "agreement": agreement,
        "coverage": coverage,
    }


def _subset(scene: Scene) -> str | None:
    values: set[str] = set()
    for agent in scene.agents:
        for key, value in agent.platform:
            if key == "subset" and value != "":
                values.add(value)
    if len(values) == 1:
        return next(iter(values))
    return None


def _time_sync(scene: Scene, limits: Thresholds) -> tuple[list[dict[str, object]], int]:
    rows: list[dict[str, object]] = []
    missing_total = 0
    for agent in scene.agents:
        deltas = [
            agent.poses[index + 1].time_us - agent.poses[index].time_us
            for index in range(len(agent.poses) - 1)
        ]
        nominal = _nominal_dt(deltas)
        missing = 0
        wide_gaps = 0
        if nominal is not None:
            for delta in deltas:
                if delta > limits.gap_factor * nominal:
                    wide_gaps += 1
                    missing += max(0, _half_up(delta / nominal) - 1)
        missing_total += missing
        rate = None if nominal is None or nominal == 0 else 1_000_000.0 / nominal
        rows.append(
            {
                "agent_id": agent.agent_id,
                "samples": len(agent.poses),
                "nominal_dt_us": nominal,
                "rate_hz": rate,
                "dt_us": distribution([float(delta) for delta in deltas]).as_json(),
                "gaps_over_nominal": wide_gaps,
                "missing_frames": missing,
            }
        )
    return rows, missing_total


def _cross_sync(scene: Scene, limits: Thresholds, pooled: _Pools) -> list[dict[str, object]]:
    reference = _reference_agent(scene)
    rows: list[dict[str, object]] = []
    if reference is None:
        return rows
    others = [agent for agent in scene.agents if agent.agent_id != reference.agent_id]
    for other in others:
        matched = 0
        missed = 0
        nearest: list[float] = []
        for pose in reference.poses:
            exact = query_agents(
                scene,
                pose.time_us,
                TimeMode.NEAREST,
                tolerance_us=limits.sync_tolerance_us,
            )
            wide = query_agents(
                scene,
                pose.time_us,
                TimeMode.NEAREST,
                tolerance_us=limits.nearest_tolerance_us,
            )
            exact_hit = next(hit for hit in exact if hit.agent_id == other.agent_id)
            wide_hit = next(hit for hit in wide if hit.agent_id == other.agent_id)
            pooled.cross_queries += 1
            if exact_hit.matched:
                matched += 1
            else:
                missed += 1
                pooled.cross_misses += 1
            if wide_hit.matched and wide_hit.dt_us is not None:
                nearest.append(float(abs(wide_hit.dt_us)))
                pooled.nearest_abs_dt.append(float(abs(wide_hit.dt_us)))
        rows.append(
            {
                "reference_agent": reference.agent_id,
                "other_agent": other.agent_id,
                "queries": len(reference.poses),
                "matched_at_sync_tolerance": matched,
                "missed_at_sync_tolerance": missed,
                "nearest_abs_dt_us": distribution(nearest).as_json(),
            }
        )
    return rows


def _poses(scene: Scene, limits: Thresholds, pooled: _Pools) -> dict[str, object]:
    warnings: list[dict[str, object]] = []
    max_speed: float | None = None
    max_accel: float | None = None
    max_smoothed: float | None = None
    window_us = int(round(limits.smooth_window_s * 1_000_000.0))
    if window_us < 1:
        window_us = 1
    for agent in scene.agents:
        speeds: list[float] = []
        durations: list[float] = []
        stamps: list[int] = []
        for index in range(len(agent.poses) - 1):
            start = agent.poses[index]
            end = agent.poses[index + 1]
            dt_s = (end.time_us - start.time_us) / 1_000_000.0
            dist = math.dist(
                (start.world_from_body.tx, start.world_from_body.ty, start.world_from_body.tz),
                (end.world_from_body.tx, end.world_from_body.ty, end.world_from_body.tz),
            )
            speed = math.inf if dt_s <= 0.0 else dist / dt_s
            speeds.append(speed)
            durations.append(dt_s)
            stamps.append(end.time_us)
            pooled.speeds.append(speed)
            if max_speed is None or speed > max_speed:
                max_speed = speed
            if speed > limits.max_speed_m_s:
                pooled.speed_flags += 1
                warnings.append(
                    {
                        "agent_id": agent.agent_id,
                        "time_us": end.time_us,
                        "kind": "speed",
                        "severity": "warning",
                        "value": speed,
                        "threshold": limits.max_speed_m_s,
                    }
                )
        for index in range(1, len(speeds)):
            magnitude = _accel_magnitude(speeds, durations, index)
            pooled.accels.append(magnitude)
            if max_accel is None or magnitude > max_accel:
                max_accel = magnitude
            if magnitude > limits.max_accel_m_s2:
                pooled.accel_flags += 1
                warnings.append(
                    {
                        "agent_id": agent.agent_id,
                        "time_us": stamps[index],
                        "kind": "acceleration",
                        "severity": "warning",
                        "value": magnitude,
                        "threshold": limits.max_accel_m_s2,
                    }
                )
        smoothed = _trailing_mean(speeds, stamps, window_us)
        for index in range(1, len(smoothed)):
            magnitude = _accel_magnitude(smoothed, durations, index)
            pooled.smoothed_accels.append(magnitude)
            if max_smoothed is None or magnitude > max_smoothed:
                max_smoothed = magnitude
            if magnitude > limits.max_accel_m_s2:
                pooled.smoothed_accel_flags += 1
                warnings.append(
                    {
                        "agent_id": agent.agent_id,
                        "time_us": stamps[index],
                        "kind": "smoothed_acceleration",
                        "severity": "warning",
                        "value": magnitude,
                        "threshold": limits.max_accel_m_s2,
                        "window_s": limits.smooth_window_s,
                    }
                )
    warnings.sort(key=lambda row: float(_number(row["value"])), reverse=True)
    return {
        "max_speed_m_s": max_speed,
        "max_accel_m_s2": max_accel,
        "max_smoothed_accel_m_s2": max_smoothed,
        "smooth_window_s": limits.smooth_window_s,
        "warning_count": len(warnings),
        "flag_count": len(warnings),
        "warnings": warnings[:20],
    }


def _altitude(
    scene: Scene,
    subset: str | None,
    limits: Thresholds,
    pooled: _Pools,
) -> dict[str, object] | None:
    vehicle = _first_of_type(scene, AgentType.VEHICLE)
    uav = _first_of_type(scene, AgentType.UAV)
    if vehicle is None or uav is None:
        return None
    vehicle_z = {pose.time_us: pose.world_from_body.tz for pose in vehicle.poses}
    uav_z = {pose.time_us: pose.world_from_body.tz for pose in uav.poses}
    times = sorted(set(vehicle_z) & set(uav_z))
    heights = [uav_z[time_us] - vehicle_z[time_us] for time_us in times]
    pooled.dz.extend(heights)
    phase, phase_frames = altitude_phase(heights, thresholds=limits)
    pooled.phases[phase] += 1
    if phase == "climb":
        phase_values = heights[:phase_frames]
        cruise_values = heights[phase_frames:]
    elif phase == "descent":
        phase_values = heights[-phase_frames:]
        cruise_values = heights[:-phase_frames]
    else:
        phase_values = []
        cruise_values = heights
    pooled.phase_dz.extend(phase_values)
    pooled.cruise_dz.extend(cruise_values)
    low, high = _expected_band(subset)
    inside = 0
    outside = 0
    if low is not None and high is not None:
        for height in heights:
            if low <= height <= high:
                inside += 1
            else:
                outside += 1
    cruise_value = _cruise_altitude(uav)
    if cruise_value is None:
        cruise_value = _cruise_altitude(vehicle)
    if cruise_value is not None and heights:
        pooled.bias.append(_percentile(sorted(heights), 0.5) - cruise_value)
    return {
        "vehicle_id": vehicle.agent_id,
        "uav_id": uav.agent_id,
        "samples": len(heights),
        "cruise_altitude_m": cruise_value,
        "expected_low_m": low,
        "expected_high_m": high,
        "dz_m": distribution(heights).as_json(),
        "inside_expected": inside,
        "outside_expected": outside,
        "phase": phase,
        "phase_frames": phase_frames,
        "cruise_dz_m": distribution(cruise_values).as_json(),
        "phase_dz_m": distribution(phase_values).as_json(),
    }


def _agreement(scene: Scene, limits: Thresholds, pooled: _Pools) -> dict[str, object]:
    shared_center: list[float] = []
    shared_yaw: list[float] = []
    shared_length: list[float] = []
    shared_width: list[float] = []
    shared_height: list[float] = []
    computed_center: list[float] = []
    computed_yaw: list[float] = []
    times = sorted({ann.time_us for ann in scene.annotations})
    agent_ids = [agent.agent_id for agent in scene.agents]
    for time_us in times:
        for index, left_id in enumerate(agent_ids):
            for right_id in agent_ids[index + 1 :]:
                try:
                    pairs = associate(
                        scene,
                        left_id,
                        right_id,
                        time_us=time_us,
                        max_gap_us=0,
                        max_center_m=limits.computed_max_center_m,
                        size_fraction=limits.computed_size_fraction,
                    )
                except CoperceiveError:
                    continue
                for pair in pairs:
                    yaw_error = _yaw_error(scene, pair.left, pair.right)
                    length = abs(pair.left.size_lwh[0] - pair.right.size_lwh[0])
                    width = abs(pair.left.size_lwh[1] - pair.right.size_lwh[1])
                    height = abs(pair.left.size_lwh[2] - pair.right.size_lwh[2])
                    if pair.kind is AssociationKind.SHARED_TRACK:
                        shared_center.append(pair.center_distance_m)
                        shared_length.append(length)
                        shared_width.append(width)
                        shared_height.append(height)
                        if yaw_error is not None:
                            shared_yaw.append(yaw_error)
                    else:
                        computed_center.append(pair.center_distance_m)
                        if yaw_error is not None:
                            computed_yaw.append(yaw_error)
    pooled.shared_center.extend(shared_center)
    pooled.shared_yaw.extend(shared_yaw)
    pooled.shared_length.extend(shared_length)
    pooled.shared_width.extend(shared_width)
    pooled.shared_height.extend(shared_height)
    pooled.computed_center.extend(computed_center)
    pooled.computed_yaw.extend(computed_yaw)
    return {
        "shared_track": {
            "ground_truth": True,
            "pairs": len(shared_center),
            "center_error_m": distribution(shared_center).as_json(),
            "yaw_error_deg": distribution(shared_yaw).as_json(),
            "length_error_m": distribution(shared_length).as_json(),
            "width_error_m": distribution(shared_width).as_json(),
            "height_error_m": distribution(shared_height).as_json(),
        },
        "computed": {
            "ground_truth": False,
            "pairs": len(computed_center),
            "max_center_m": limits.computed_max_center_m,
            "size_fraction": limits.computed_size_fraction,
            "gate": "max(max_center_m, size_fraction * longer box length)",
            "center_error_m": distribution(computed_center).as_json(),
            "yaw_error_deg": distribution(computed_yaw).as_json(),
            "label": "computed",
        },
    }


def _coverage(scene: Scene, pooled: _Pools) -> dict[str, object]:
    vehicle_ids = {
        agent.agent_id for agent in scene.agents if agent.agent_type is AgentType.VEHICLE
    }
    uav_ids = {agent.agent_id for agent in scene.agents if agent.agent_type is AgentType.UAV}
    by_track: dict[str, set[str]] = {}
    unlabeled_vehicle = 0
    unlabeled_uav = 0
    for ann in scene.annotations:
        if ann.track_id is None:
            if ann.agent_id in vehicle_ids:
                unlabeled_vehicle += 1
            elif ann.agent_id in uav_ids:
                unlabeled_uav += 1
            continue
        by_track.setdefault(ann.track_id, set()).add(ann.agent_id)
    vehicle_only = 0
    uav_only = 0
    both = 0
    for users in by_track.values():
        has_vehicle = bool(users & vehicle_ids)
        has_uav = bool(users & uav_ids)
        if has_vehicle and has_uav:
            both += 1
        elif has_vehicle:
            vehicle_only += 1
        elif has_uav:
            uav_only += 1
    pooled.vehicle_only += vehicle_only
    pooled.uav_only += uav_only
    pooled.both += both
    return {
        "vehicle_only_tracks": vehicle_only,
        "uav_only_tracks": uav_only,
        "both_tracks": both,
        "unlabeled_vehicle_boxes": unlabeled_vehicle,
        "unlabeled_uav_boxes": unlabeled_uav,
    }


def _official(scenes: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in scenes:
        if "error" in row:
            continue
        subset = row.get("subset")
        if type(subset) is not str:
            continue
        grouped.setdefault(subset, []).append(row)
    rows: list[dict[str, object]] = []
    for subset in sorted(grouped):
        expectation = _EXPECTATIONS.get(subset)
        measured = len(grouped[subset])
        frame_counts: list[int] = []
        for row in grouped[subset]:
            frames = row.get("frames")
            if isinstance(frames, dict) and frames:
                raw_frames = cast(dict[object, object], frames)
                counts = [value for value in raw_frames.values() if type(value) is int]
                if counts:
                    frame_counts.append(max(counts))
        not_about = sum(1 for count in frame_counts if count != _FRAMES_ABOUT)
        rows.append(
            {
                "subset": subset,
                "expected_scenes": None if expectation is None else expectation[0],
                "measured_scenes": measured,
                "scenes_match": expectation is not None and measured == expectation[0],
                "frames_about": _FRAMES_ABOUT,
                "measured_frames": sum(frame_counts),
                "scenes_whose_frame_count_is_not_about": not_about,
                "frame_count": distribution([float(count) for count in frame_counts]).as_json(),
            }
        )
    return rows


def _altitude_finding(report: dict[str, object]) -> str:
    aggregate = _as_dict(report["aggregate"], "aggregate")
    dz = _as_dict(aggregate["altitude_dz_m"], "altitude_dz_m")
    bias = _as_dict(
        aggregate["altitude_bias_median_dz_minus_cruise_m"],
        "altitude_bias",
    )
    phases = _as_dict(aggregate["altitude_phases"], "altitude_phases")
    return (
        "Per-frame altitude is drone z minus vehicle z at shared timestamps. "
        f"Samples {dz.get('count')}, min {dz.get('min')}, median {dz.get('median')}, "
        f"max {dz.get('max')}. "
        "The toolkit band applies to the recorded cruise altitude. "
        f"Scene median height minus cruise_altitude_m has median {bias.get('median')} m "
        f"(min {bias.get('min')}, max {bias.get('max')}). "
        f"Phase labels: {phases}. "
        "A phase is a prefix or suffix at least 2 m below the rest of that scene, "
        "not a dataset-wide takeoff."
    )


def _longest_end(
    heights: list[float],
    *,
    prefix: bool,
    max_k: int,
    thresholds: Thresholds,
) -> int:
    best = 0
    for length in range(thresholds.phase_min_frames, max_k + 1):
        if prefix:
            phase = heights[:length]
            rest = heights[length:]
            moving = _mostly(phase, ascending=True, thresholds=thresholds)
        else:
            phase = heights[-length:]
            rest = heights[:-length]
            moving = _mostly(phase, ascending=False, thresholds=thresholds)
        if not rest or not moving:
            continue
        rest_median = _percentile(sorted(rest), 0.5)
        phase_median = _percentile(sorted(phase), 0.5)
        if rest_median - phase_median >= thresholds.phase_delta_m:
            best = length
    return best


def _mostly(values: list[float], *, ascending: bool, thresholds: Thresholds) -> bool:
    if len(values) < 2:
        return False
    hits = 0
    steps = len(values) - 1
    for index in range(steps):
        delta = values[index + 1] - values[index]
        if ascending and delta >= thresholds.phase_step_m:
            hits += 1
        if not ascending and delta <= -thresholds.phase_step_m:
            hits += 1
    return hits / steps >= thresholds.phase_step_fraction


def _yaw_error(scene: Scene, left: Annotation, right: Annotation) -> float | None:
    try:
        left_yaw = _box_yaw(scene, left)
        right_yaw = _box_yaw(scene, right)
    except CoperceiveError:
        return None
    delta = (left_yaw - right_yaw) % 360.0
    if delta > 180.0:
        delta -= 360.0
    return abs(delta)


def _box_yaw(scene: Scene, ann: Annotation) -> float:
    if ann.frame.value == "world":
        return yaw_deg(ann.box_from_rpy)
    pose = lookup_pose(scene.agent(ann.agent_id), ann.time_us, max_gap_us=0)
    return yaw_deg(pose.compose(ann.box_from_rpy))


def _reference_agent(scene: Scene) -> Agent | None:
    for agent in scene.agents:
        if agent.agent_type is AgentType.VEHICLE and agent.poses:
            return agent
    for agent in scene.agents:
        if agent.poses:
            return agent
    return None


def _first_of_type(scene: Scene, kind: AgentType) -> Agent | None:
    for agent in scene.agents:
        if agent.agent_type is kind:
            return agent
    return None


def _cruise_altitude(agent: Agent) -> float | None:
    for key, value in agent.platform:
        if key != "cruise_altitude_m":
            continue
        try:
            number = float(value)
        except ValueError:
            return None
        if math.isfinite(number):
            return number
    return None


def _expected_band(subset: str | None) -> tuple[float | None, float | None]:
    if subset is None:
        return (None, None)
    expectation = _EXPECTATIONS.get(subset)
    if expectation is None:
        return (None, None)
    return (expectation[1], expectation[2])


def _nominal_dt(deltas: list[int]) -> int | None:
    if not deltas:
        return None
    counts = Counter(deltas)
    return sorted(counts, key=lambda delta: (-counts[delta], delta))[0]


def _hard_failures(scenes: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in scenes:
        scene_id = row.get("scene_id", row.get("path", ""))
        if "error" in row:
            rows.append(
                {
                    "kind": "unreadable_scene",
                    "scene_id": scene_id,
                    "detail": row["error"],
                }
            )
            continue
        missing = row.get("missing_frames")
        if type(missing) is int and missing > 0:
            rows.append(
                {
                    "kind": "missing_frames",
                    "scene_id": scene_id,
                    "count": missing,
                }
            )
        cross = row.get("cross_sync")
        if isinstance(cross, list):
            for item in cast(list[object], cross):
                if not isinstance(item, dict):
                    continue
                pair = cast(dict[object, object], item)
                missed = pair.get("missed_at_sync_tolerance")
                if type(missed) is int and missed > 0:
                    rows.append(
                        {
                            "kind": "cross_agent_sync_miss",
                            "scene_id": scene_id,
                            "reference_agent": pair.get("reference_agent"),
                            "other_agent": pair.get("other_agent"),
                            "count": missed,
                        }
                    )
    return rows


def _accel_magnitude(series: list[float], durations: list[float], index: int) -> float:
    span = 0.5 * (durations[index] + durations[index - 1])
    if span <= 0.0:
        return math.inf
    return abs((series[index] - series[index - 1]) / span)


def _trailing_mean(values: list[float], stamps: list[int], window_us: int) -> list[float]:
    """Mean of samples whose stamp is in ``(stamp - window_us, stamp]``."""
    means: list[float] = []
    start = 0
    total = 0.0
    for index, stamp in enumerate(stamps):
        total += values[index]
        cutoff = stamp - window_us
        while start < index and stamps[start] <= cutoff:
            total -= values[start]
            start += 1
        means.append(total / (index - start + 1))
    return means


def _half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        raise ValueError("empty")
    index = int(round((len(ordered) - 1) * fraction))
    if index < 0:
        index = 0
    if index >= len(ordered):
        index = len(ordered) - 1
    return ordered[index]


def _thresholds_json(limits: Thresholds) -> dict[str, object]:
    return {
        "max_speed_m_s": limits.max_speed_m_s,
        "max_accel_m_s2": limits.max_accel_m_s2,
        "gap_factor": limits.gap_factor,
        "sync_tolerance_us": limits.sync_tolerance_us,
        "nearest_tolerance_us": limits.nearest_tolerance_us,
        "computed_max_center_m": limits.computed_max_center_m,
        "computed_size_fraction": limits.computed_size_fraction,
        "smooth_window_s": limits.smooth_window_s,
        "phase_delta_m": limits.phase_delta_m,
        "phase_min_frames": limits.phase_min_frames,
        "phase_max_fraction": limits.phase_max_fraction,
        "phase_step_m": limits.phase_step_m,
        "phase_step_fraction": limits.phase_step_fraction,
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        result[str(key)] = item
    return result


def _as_list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list")
    return list(cast(list[object], value))


def _official_markdown(rows: list[object]) -> list[str]:
    if not rows:
        return ["No scene declared a known subset."]
    lines = [
        "| Subset | Measured scenes | Official scenes | Match | Frames | Scenes not about 150 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in rows:
        row = _as_dict(item, "official row")
        lines.append(
            "| {subset} | {measured_scenes} | {expected_scenes} | {scenes_match} | "
            "{measured_frames} | {scenes_whose_frame_count_is_not_about} |".format(
                subset=row.get("subset"),
                measured_scenes=row.get("measured_scenes"),
                expected_scenes=row.get("expected_scenes"),
                scenes_match=row.get("scenes_match"),
                measured_frames=row.get("measured_frames"),
                scenes_whose_frame_count_is_not_about=row.get(
                    "scenes_whose_frame_count_is_not_about"
                ),
            )
        )
    return lines


def _bullet_block(row: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for key in (
        "subset",
        "frames",
        "missing_frames",
        "pose",
        "altitude",
        "agreement",
        "coverage",
        "centreline",
        "time_sync",
        "cross_sync",
    ):
        if key not in row:
            continue
        lines.append(f"- `{key}`: `{json.dumps(row[key], sort_keys=True)}`")
    return lines
