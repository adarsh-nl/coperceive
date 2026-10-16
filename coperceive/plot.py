# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""Matplotlib figures for one recorded sample.

The bird's-eye figure uses the scene world: east is +X and points right,
north is +Y and points up. That is the same axis the HD map is aligned to.
The camera figure draws the stored image and projects boxes and lidar with
the pinhole in :mod:`coperceive.project`. Neither figure imports a web
framework. Image bytes stay in the scene directory.
"""

from __future__ import annotations

import array
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.patches import Polygon
from PIL import Image

from coperceive.errors import CoperceiveError
from coperceive.hdmap.opendrive import LAYER_LANE_CENTRE, LAYER_ROAD_EDGE, MapPolyline
from coperceive.hdmap.store import HdMap
from coperceive.model import Agent, Annotation, Modality, Scene, Sensor
from coperceive.project import (
    ProjectionStatus,
    box_world_points,
    project_box,
    project_camera_point,
)
from coperceive.transform import lookup_pose, sensor_frame, transform, world_frame
from coperceive.viewdata import image_path, pose_times, world_points

_FORMATS = frozenset({"png", "svg"})
_THEMES = frozenset({"dark", "light"})
_COLOR_BY = frozenset({"agent", "class"})
_BOX_STYLES = frozenset({"wire", "solid"})
_NEAR_M = 120.0
_AGENT_COLORS = {
    "vehicle": "#e67828",
    "uav": "#2878dc",
    "infrastructure": "#3d9a86",
    "other": "#b06a9a",
}
_CLASS_COLORS = {
    "car": "#e67828",
    "truck": "#c45c26",
    "bus": "#d4a017",
    "pedestrian": "#3d9a86",
    "cyclist": "#2878dc",
    "bicycle": "#2878dc",
    "motorcycle": "#6a5acd",
    "barrier": "#9aa7b2",
    "traffic_cone": "#e07a72",
}
_FALLBACK = ("#b06a9a", "#5aa0c4", "#c4a15a", "#7dcea0")
# Bottom face, wound so the polygon does not cross. Indices into the eight
# corners that follow the centre in box_world_points.
_FOOTPRINT = (0, 4, 6, 2)
_BOX_EDGES = (
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
    (0, 2),
    (1, 3),
    (4, 6),
    (5, 7),
    (0, 1),
    (2, 3),
    (4, 5),
    (6, 7),
)


class PlotError(Exception):
    """The figure cannot be written. The message is safe to show."""


class PlotNotFound(PlotError):
    """A sample, camera, or image file is missing."""


@dataclass(frozen=True)
class _BoxDraw:
    center: tuple[float, float, float]
    ring: tuple[tuple[float, float], ...]
    annotation: Annotation
    agent: Agent


@dataclass(frozen=True)
class PlotResult:
    dest: Path
    format: str
    time_us: int
    width: int
    height: int
    map_drawn: bool
    points: int
    boxes: int


@dataclass(frozen=True)
class _Theme:
    bg: str
    axes: str
    text: str
    muted: str
    spine: str
    lane: str
    edge: str
    grid: str


def render_bev(
    scene: Scene,
    dest: Path,
    *,
    time_us: int | None = None,
    hdmap: HdMap | None = None,
    format: str | None = None,
    theme: str = "dark",
    color_by: str = "agent",
    points: bool = True,
    boxes: bool = True,
    draw_map: bool = True,
    box_style: str = "wire",
    hidden_classes: tuple[str, ...] = (),
    agents: tuple[str, ...] | None = None,
) -> PlotResult:
    """Write a bird's-eye PNG or SVG for one recorded sample."""
    fmt = _file_format(dest, format)
    colors = _theme(theme)
    style = _box_style(box_style)
    mode = _color_mode(color_by)
    hidden = _class_filter(hidden_classes)
    chosen = _chosen_agents(scene, agents)
    when = _sample_time(scene, time_us)
    anchors = _anchors(chosen, when)
    trails = _trails(chosen)
    window_points: list[tuple[float, float]] = [point for point in anchors if point is not None]
    window_points.extend(point for trail in trails.values() for point in trail)
    drawn_boxes = _boxes(scene, when, chosen, hidden) if boxes else ()
    window_points.extend((item.center[0], item.center[1]) for item in drawn_boxes)
    sweeps = _lidar(scene, when, chosen, anchors) if points else ()
    window_points.extend((point[0], point[1]) for _agent, cloud in sweeps for point in cloud)
    if not window_points:
        raise PlotError("nothing to plot at this sample")
    bounds = _square(window_points)

    figure = Figure(figsize=(8.0, 8.0), dpi=120, facecolor=colors.bg)
    axes = _axes(figure)
    _style_axes(axes, colors)
    map_drawn = False
    if draw_map and hdmap is not None:
        map_drawn = _draw_map(axes, hdmap, bounds, colors)
    point_count = 0
    for agent, cloud in sweeps:
        if not cloud:
            continue
        xs = [point[0] for point in cloud]
        ys = [point[1] for point in cloud]
        axes.scatter(
            xs,
            ys,
            s=6,
            c=_agent_color(agent),
            linewidths=0,
            alpha=0.9,
            rasterized=True,
            zorder=2,
        )
        point_count += len(cloud)
    for agent, trail in trails.items():
        if len(trail) < 1:
            continue
        axes.plot(
            [point[0] for point in trail],
            [point[1] for point in trail],
            color=_agent_color(agent),
            linewidth=1.4,
            solid_capstyle="round",
            label=agent.agent_id,
            zorder=3,
        )
    for item in drawn_boxes:
        color = _box_color(item.agent, item.annotation, mode)
        face = color if style == "solid" else "none"
        axes.add_patch(
            Polygon(
                item.ring,
                closed=True,
                fill=style == "solid",
                facecolor=face,
                edgecolor=color,
                linewidth=1.1,
                alpha=0.95,
                zorder=4,
            )
        )
        front = (
            (item.ring[1][0] + item.ring[2][0]) / 2.0,
            (item.ring[1][1] + item.ring[2][1]) / 2.0,
        )
        axes.plot(
            [item.center[0], front[0]],
            [item.center[1], front[1]],
            color=color,
            linewidth=1.1,
            solid_capstyle="butt",
            zorder=4,
        )
    for agent, xy in zip(chosen, anchors, strict=True):
        if xy is None:
            continue
        axes.scatter(
            [xy[0]],
            [xy[1]],
            s=28,
            c=_agent_color(agent),
            edgecolors=colors.text,
            linewidths=0.6,
            zorder=5,
        )
    axes.set_xlim(bounds[0], bounds[1])
    axes.set_ylim(bounds[2], bounds[3])
    axes.set_aspect("equal", adjustable="box")
    axes.set_xlabel("east (m)")
    axes.set_ylabel("north (m)")
    axes.set_title(f"{scene.scene_id} at {when} µs")
    if any(trail for trail in trails.values()):
        legend = axes.legend(loc="lower left", frameon=True, fontsize=8)
        legend.get_frame().set_facecolor(colors.axes)
        legend.get_frame().set_edgecolor(colors.spine)
        for text in legend.get_texts():
            text.set_color(colors.text)
    width, height = _write(figure, dest, fmt)
    return PlotResult(dest, fmt, when, width, height, map_drawn, point_count, len(drawn_boxes))


def render_overlay(
    scene: Scene,
    scene_dir: Path,
    dest: Path,
    *,
    agent_id: str,
    sensor_id: str,
    time_us: int | None = None,
    format: str | None = None,
    theme: str = "dark",
    color_by: str = "agent",
    points: bool = True,
    boxes: bool = True,
    box_style: str = "wire",
    hidden_classes: tuple[str, ...] = (),
    agents: tuple[str, ...] | None = None,
) -> PlotResult:
    """Write the camera image with projected boxes and lidar."""
    fmt = _file_format(dest, format)
    colors = _theme(theme)
    style = _box_style(box_style)
    mode = _color_mode(color_by)
    hidden = _class_filter(hidden_classes)
    chosen = _chosen_agents(scene, agents)
    when = _sample_time(scene, time_us)
    sensor = _camera(scene, agent_id, sensor_id)
    relative = image_path(scene, when, agent_id, sensor_id)
    if relative is None:
        raise PlotNotFound("image not found")
    image_file = _confine_file(scene_dir, relative)
    with Image.open(image_file) as opened:
        rgb = opened.convert("RGB")
        # Copy before the file closes. asarray can be a view of the Pillow buffer.
        pixels = np.array(rgb)
        image_size = rgb.size
    figure = Figure(
        figsize=(image_size[0] / 100.0, image_size[1] / 100.0),
        dpi=100,
        facecolor=colors.bg,
    )
    axes = _axes(figure)
    axes.set_position((0.0, 0.0, 1.0, 1.0))
    axes.imshow(pixels, origin="upper", interpolation="nearest", aspect="auto")
    axes.set_axis_off()
    scale_u = image_size[0] / sensor.image_width_px if sensor.image_width_px else 1.0
    scale_v = image_size[1] / sensor.image_height_px if sensor.image_height_px else 1.0
    box_count = 0
    if boxes:
        box_count = _draw_overlay_boxes(
            axes, scene, when, chosen, hidden, sensor, mode, style, scale_u, scale_v
        )
    point_count = 0
    if points:
        point_count = _draw_overlay_points(axes, scene, when, chosen, sensor, scale_u, scale_v)
    # imshow places each pixel centre on an integer. Limits of (0, width)
    # leave half a pixel of the figure colour on the far edge, and
    # matplotlib 3.10 rounds that into a full empty row.
    axes.set_xlim(-0.5, image_size[0] - 0.5)
    axes.set_ylim(image_size[1] - 0.5, -0.5)
    axes.set_aspect("auto")
    axes.set_position((0.0, 0.0, 1.0, 1.0))
    axes.text(
        0.01,
        0.98,
        f"{scene.scene_id} {agent_id}/{sensor_id} at {when} µs",
        transform=axes.transAxes,
        color="#f4f1ea",
        fontsize=11,
        verticalalignment="top",
    )
    width, height = _write(figure, dest, fmt)
    return PlotResult(dest, fmt, when, width, height, False, point_count, box_count)


def _file_format(dest: Path, fmt: str | None) -> str:
    chosen = fmt
    if chosen is None:
        suffix = dest.suffix.lower().lstrip(".")
        chosen = suffix if suffix in _FORMATS else ""
    if chosen not in _FORMATS:
        raise PlotError("format must be png or svg")
    if dest.suffix.lower() != f".{chosen}":
        raise PlotError("the output path must end in .png or .svg and match the format")
    return chosen


def _theme(name: str) -> _Theme:
    if name not in _THEMES:
        raise PlotError("theme must be dark or light")
    if name == "light":
        return _Theme(
            "#f4f1ea", "#fffcf7", "#1c1f1a", "#5c584e", "#d3cec2", "#1d6b52", "#5c584e", "#d3cec2"
        )
    return _Theme(
        "#121410", "#0e100d", "#e7e4da", "#9a9588", "#2e332c", "#7dcea0", "#9aa7b2", "#2e332c"
    )


def _box_style(name: str) -> str:
    if name not in _BOX_STYLES:
        raise PlotError("box style must be wire or solid")
    return name


def _color_mode(name: str) -> str:
    if name not in _COLOR_BY:
        raise PlotError("color must be agent or class")
    return name


def _class_filter(names: tuple[str, ...]) -> frozenset[str]:
    if len(names) > 40:
        raise PlotError("at most 40 hidden classes")
    for name in names:
        if name == "" or len(name) > 64:
            raise PlotError("bad class name")
    return frozenset(names)


def _chosen_agents(scene: Scene, agents: tuple[str, ...] | None) -> tuple[Agent, ...]:
    if agents is None:
        return scene.agents
    if len(agents) > 32:
        raise PlotError("at most 32 agents")
    known = {agent.agent_id: agent for agent in scene.agents}
    chosen: list[Agent] = []
    for agent_id in agents:
        if agent_id == "" or len(agent_id) > 64:
            raise PlotError("bad agent id")
        agent = known.get(agent_id)
        if agent is None:
            raise PlotError(f"unknown agent {agent_id}")
        chosen.append(agent)
    if not chosen:
        raise PlotError("no agents selected")
    return tuple(chosen)


def _sample_time(scene: Scene, time_us: int | None) -> int:
    times = pose_times(scene)
    if not times:
        raise PlotError("scene has no pose times")
    if time_us is None:
        return times[0]
    if time_us not in times:
        raise PlotNotFound(f"{time_us} is not a recorded sample")
    return time_us


def _camera(scene: Scene, agent_id: str, sensor_id: str) -> Sensor:
    try:
        sensor = scene.sensor(agent_id, sensor_id)
    except KeyError as exc:
        raise PlotNotFound("camera not found") from exc
    if sensor.modality is not Modality.CAMERA:
        raise PlotError("that sensor is not a camera")
    if None in (sensor.fx, sensor.fy, sensor.cx, sensor.cy):
        raise PlotError("that camera has no pinhole")
    return sensor


def _confine_file(scene_dir: Path, relative: str) -> Path:
    root = scene_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise PlotError("image path leaves the scene")
    if not target.is_file():
        raise PlotNotFound("image not found")
    return target


def _anchors(agents: tuple[Agent, ...], time_us: int) -> tuple[tuple[float, float] | None, ...]:
    found: list[tuple[float, float] | None] = []
    for agent in agents:
        try:
            pose = lookup_pose(agent, time_us, max_gap_us=0)
        except CoperceiveError:
            found.append(None)
            continue
        found.append((pose.tx, pose.ty))
    return tuple(found)


def _trails(agents: tuple[Agent, ...]) -> dict[Agent, tuple[tuple[float, float], ...]]:
    return {
        agent: tuple((pose.world_from_body.tx, pose.world_from_body.ty) for pose in agent.poses)
        for agent in agents
    }


def _boxes(
    scene: Scene,
    time_us: int,
    agents: tuple[Agent, ...],
    hidden: frozenset[str],
) -> tuple[_BoxDraw, ...]:
    shown = {agent.agent_id: agent for agent in agents}
    rows: list[_BoxDraw] = []
    for annotation in scene.annotations:
        agent = shown.get(annotation.agent_id)
        if annotation.time_us != time_us or agent is None or annotation.category in hidden:
            continue
        try:
            world = box_world_points(scene, annotation, max_gap_us=0)
        except CoperceiveError:
            continue
        ring = tuple((world[index + 1][0], world[index + 1][1]) for index in _FOOTPRINT)
        rows.append(_BoxDraw(world[0], ring, annotation, agent))
    return tuple(rows)


def _lidar(
    scene: Scene,
    time_us: int,
    agents: tuple[Agent, ...],
    anchors: tuple[tuple[float, float] | None, ...],
) -> tuple[tuple[Agent, tuple[tuple[float, float, float], ...]], ...]:
    shown = {agent.agent_id: agent for agent in agents}
    centres = [point for point in anchors if point is not None]
    rows: list[tuple[Agent, tuple[tuple[float, float, float], ...]]] = []
    for cloud in scene.point_clouds:
        if cloud.time_us != time_us:
            continue
        agent = shown.get(cloud.agent_id)
        if agent is None:
            continue
        try:
            sensor = scene.sensor(cloud.agent_id, cloud.sensor_id)
        except KeyError:
            continue
        if sensor.modality is not Modality.LIDAR:
            continue
        try:
            decoded = _xyz(world_points(scene, cloud))
        except CoperceiveError:
            continue
        kept = tuple(point for point in decoded if _near(point, centres))
        rows.append((agent, kept))
    return tuple(rows)


def _xyz(blob: bytes) -> tuple[tuple[float, float, float], ...]:
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    points: list[tuple[float, float, float]] = []
    for index in range(0, len(values), 3):
        points.append((float(values[index]), float(values[index + 1]), float(values[index + 2])))
    return tuple(points)


def _near(point: tuple[float, float, float], anchors: list[tuple[float, float]]) -> bool:
    if not anchors:
        return True
    limit = _NEAR_M * _NEAR_M
    return any((point[0] - x) ** 2 + (point[1] - y) ** 2 <= limit for x, y in anchors)


def _square(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 8.0)
    pad = max(12.0, span * 0.12)
    half = span / 2.0 + pad
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    return (cx - half, cx + half, cy - half, cy + half)


def _overlaps(polyline: MapPolyline, bounds: tuple[float, float, float, float]) -> bool:
    if len(polyline.xyz) < 2:
        return False
    xs = [point[0] for point in polyline.xyz]
    ys = [point[1] for point in polyline.xyz]
    min_x, max_x, min_y, max_y = bounds
    return not (max(xs) < min_x or min(xs) > max_x or max(ys) < min_y or min(ys) > max_y)


def _draw_map(
    axes: Axes, hdmap: HdMap, bounds: tuple[float, float, float, float], colors: _Theme
) -> bool:
    drawn = False
    for item in hdmap.world_polylines(frozenset({LAYER_LANE_CENTRE, LAYER_ROAD_EDGE})):
        if not _overlaps(item, bounds):
            continue
        color = colors.lane if item.layer == LAYER_LANE_CENTRE else colors.edge
        axes.plot(
            [point[0] for point in item.xyz],
            [point[1] for point in item.xyz],
            color=color,
            linewidth=0.9 if item.layer == LAYER_LANE_CENTRE else 0.7,
            solid_capstyle="round",
            zorder=1,
        )
        drawn = True
    return drawn


def _axes(figure: Figure) -> Axes:
    return figure.add_subplot(1, 1, 1)


def _style_axes(axes: Axes, colors: _Theme) -> None:
    axes.set_facecolor(colors.axes)
    axes.grid(True, color=colors.grid, linewidth=0.4)
    axes.set_axisbelow(True)
    axes.tick_params(colors=colors.muted, labelsize=8)
    axes.title.set_color(colors.text)
    axes.xaxis.label.set_color(colors.text)
    axes.yaxis.label.set_color(colors.text)
    for spine in axes.spines.values():
        spine.set_color(colors.spine)


def _agent_color(agent: Agent) -> str:
    return _AGENT_COLORS.get(agent.agent_type.value, _AGENT_COLORS["other"])


def _class_color(category: str) -> str:
    known = _CLASS_COLORS.get(category)
    if known is not None:
        return known
    total = sum(ord(char) for char in category) % len(_FALLBACK)
    return _FALLBACK[total]


def _box_color(agent: Agent, annotation: Annotation, mode: str) -> str:
    if mode == "class":
        return _class_color(annotation.category)
    return _agent_color(agent)


def _draw_overlay_boxes(
    axes: Axes,
    scene: Scene,
    time_us: int,
    agents: tuple[Agent, ...],
    hidden: frozenset[str],
    sensor: Sensor,
    mode: str,
    style: str,
    scale_u: float,
    scale_v: float,
) -> int:
    shown = {agent.agent_id: agent for agent in agents}
    count = 0
    for annotation in scene.annotations:
        agent = shown.get(annotation.agent_id)
        if annotation.time_us != time_us or agent is None or annotation.category in hidden:
            continue
        try:
            projected = project_box(
                scene,
                annotation,
                sensor.agent_id,
                sensor.sensor_id,
                max_gap_us=0,
                camera_time_us=time_us,
            )
        except CoperceiveError:
            continue
        color = _box_color(agent, annotation, mode)
        if style == "solid":
            face = [
                (corner.u_px * scale_u, corner.v_px * scale_v)
                for corner in projected.corners
                if corner.u_px is not None
                and corner.v_px is not None
                and corner.status is ProjectionStatus.IN_IMAGE
            ]
            if len(face) >= 3:
                axes.add_patch(
                    Polygon(
                        face,
                        closed=True,
                        facecolor=color,
                        edgecolor="none",
                        alpha=0.28,
                        zorder=2,
                    )
                )
        for start, end in _BOX_EDGES:
            first = projected.corners[start]
            second = projected.corners[end]
            if (
                first.u_px is None
                or first.v_px is None
                or second.u_px is None
                or second.v_px is None
            ):
                continue
            axes.plot(
                [first.u_px * scale_u, second.u_px * scale_u],
                [first.v_px * scale_v, second.v_px * scale_v],
                color=color,
                linewidth=1.2,
                solid_capstyle="round",
                zorder=3,
            )
        count += 1
    return count


def _draw_overlay_points(
    axes: Axes,
    scene: Scene,
    time_us: int,
    agents: tuple[Agent, ...],
    sensor: Sensor,
    scale_u: float,
    scale_v: float,
) -> int:
    try:
        camera_from_world = transform(
            scene,
            sensor_frame(sensor.agent_id, sensor.sensor_id),
            world_frame(),
            time_us,
            max_gap_us=0,
        )
    except CoperceiveError as exc:
        raise PlotError("that camera has no pose at this sample") from exc
    shown = {agent.agent_id: agent for agent in agents}
    total = 0
    for cloud in scene.point_clouds:
        if cloud.time_us != time_us:
            continue
        agent = shown.get(cloud.agent_id)
        if agent is None:
            continue
        try:
            owner = scene.sensor(cloud.agent_id, cloud.sensor_id)
        except KeyError:
            continue
        if owner.modality is not Modality.LIDAR:
            continue
        try:
            decoded = _xyz(world_points(scene, cloud))
        except CoperceiveError:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for point in decoded:
            projected = project_camera_point(camera_from_world.apply(point), sensor)
            if projected.status is not ProjectionStatus.IN_IMAGE:
                continue
            if projected.u_px is None or projected.v_px is None:
                continue
            xs.append(projected.u_px * scale_u)
            ys.append(projected.v_px * scale_v)
        if not xs:
            continue
        axes.scatter(xs, ys, s=8, c=_agent_color(agent), linewidths=0, alpha=0.9, zorder=3)
        total += len(xs)
    return total


def _write(figure: Figure, dest: Path, fmt: str) -> tuple[int, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "svg":
        FigureCanvasSVG(figure)
    else:
        FigureCanvasAgg(figure)
    figure.savefig(dest, format=fmt)
    width, height = figure.canvas.get_width_height()
    return int(width), int(height)
