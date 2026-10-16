# SPDX-License-Identifier: Apache-2.0
"""FastAPI app: health, allow-listed jobs, SSE, and a path-confined file read."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coperceive.app.jobs import CSRF_HEADER, confine, list_jobs, open_db, run_job
from coperceive.hdmap.store import HdMap, find_map, load_map, scene_map_name
from coperceive.model import Scene
from coperceive.plot import PlotError, PlotNotFound, PlotResult, render_bev, render_overlay
from coperceive.rerun_export import RerunUnavailable, export_rerun
from coperceive.sequence_export import (
    FORMATS,
    SequenceExport,
    SequenceExportError,
    SequenceNotFound,
    export_camera_sequence,
    export_rendered_frames,
    samples_in_range,
)
from coperceive.store import read_scene
from coperceive.viewdata import (
    frame_document,
    frame_points,
    image_path,
    pose_times,
    scene_directories,
    summarize,
)

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_STATIC = Path(__file__).resolve().parent / "static"


class JobRequest(BaseModel):
    name: str = Field(examples=["echo"])
    args: list[str] = Field(default_factory=list)


class SequenceRequest(BaseModel):
    agent_id: str
    sensor_id: str
    start_us: int | None = None
    end_us: int | None = None
    fps: int = 10
    width: int = 0
    height: int = 0
    format: str


class PlotRequest(BaseModel):
    time_us: int | None = None
    format: str = "png"
    theme: str = "dark"
    color_by: str = "agent"
    points: bool = True
    boxes: bool = True
    map: bool = True
    box_style: str = "wire"
    hidden_classes: list[str] = Field(default_factory=list)
    agents: list[str] | None = None


class OverlayRequest(BaseModel):
    agent_id: str
    sensor_id: str
    time_us: int | None = None
    format: str = "png"
    theme: str = "dark"
    color_by: str = "agent"
    points: bool = True
    boxes: bool = True
    box_style: str = "wire"
    hidden_classes: list[str] = Field(default_factory=list)
    agents: list[str] | None = None


class ViewSequenceRequest(BaseModel):
    format: str
    fps: int
    start_us: int
    end_us: int
    frames_b64: list[str] = Field(default_factory=list)


_EXPORT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def create_app(
    *,
    root: str | Path,
    token: str | None,
    root_path: str = "",
    maps: str | Path | None = None,
) -> FastAPI:
    root_path_obj = Path(root).resolve()
    root_path_obj.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="coperceive",
        version="0.0.0",
        root_path=root_path,
        description="Local cooperative-perception data app.",
    )
    app.state.root = root_path_obj
    app.state.token = token
    app.state.db_path = root_path_obj / "jobs.sqlite"
    app.state.scene_cache = _SceneCache()
    app.state.maps = _maps_root(root_path_obj, maps)
    app.state.map_cache = {}

    @app.middleware("http")
    async def auth_and_csrf(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/"):
            expected = request.app.state.token
            if expected:
                header = request.headers.get("authorization", "")
                if header != f"Bearer {expected}":
                    return _json_error(401, "bearer token required")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                if request.headers.get(CSRF_HEADER) != "1":
                    return _json_error(403, f"missing {CSRF_HEADER}: 1")
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "root_path": root_path}

    @app.get("/api/jobs")
    def jobs() -> list[dict[str, object]]:
        conn = open_db(app.state.db_path)
        try:
            return list_jobs(conn)
        finally:
            conn.close()

    @app.post("/api/jobs")
    def create_job(body: JobRequest) -> dict[str, int]:
        conn = open_db(app.state.db_path)
        try:
            try:
                job_id = run_job(conn, app.state.root, body.name, body.args)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()
        return {"id": job_id}

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(_event_stream(app.state.db_path), media_type="text/event-stream")

    @app.get("/api/scenes")
    def scenes() -> list[dict[str, str]]:
        return [{"scene_id": path.name} for path in scene_directories(app.state.root)]

    @app.get("/api/scenes/{scene_id}")
    def scene_summary(scene_id: str) -> dict[str, object]:
        return summarize(_cached_scene(app, scene_id))

    @app.get("/api/scenes/{scene_id}/frame")
    def scene_frame(scene_id: str, time_us: int) -> dict[str, object]:
        return frame_document(_cached_scene(app, scene_id), time_us)

    @app.get("/api/scenes/{scene_id}/map")
    def scene_map(scene_id: str) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        return _map_summary(app, scene)

    @app.get("/api/scenes/{scene_id}/map/lines")
    def scene_map_lines(scene_id: str) -> Response:
        scene = _cached_scene(app, scene_id)
        hdmap = _scene_hdmap(app, scene)
        if hdmap is None:
            raise HTTPException(status_code=404, detail="map not found")
        return Response(content=hdmap.lines_blob(), media_type="application/octet-stream")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        svg = _STATIC / "favicon.svg"
        if not svg.is_file():
            raise HTTPException(status_code=404, detail="favicon not found")
        return FileResponse(svg, media_type="image/svg+xml")

    @app.get("/api/scenes/{scene_id}/image")
    def scene_image(scene_id: str, time_us: int, agent_id: str, sensor_id: str) -> FileResponse:
        scene = _cached_scene(app, scene_id)
        relative = image_path(scene, time_us, agent_id, sensor_id)
        if relative is None:
            raise HTTPException(status_code=404, detail="image not found")
        scene_dir = _scene_dir(app.state.root, scene_id)
        try:
            target = confine(scene_dir, relative)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="image not found")
        media = _IMAGE_TYPES.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(target, media_type=media)

    @app.get("/api/scenes/{scene_id}/points")
    def scene_points(scene_id: str, time_us: int, agent_id: str, sensor_id: str) -> Response:
        blob = frame_points(_cached_scene(app, scene_id), time_us, agent_id, sensor_id)
        return Response(content=blob, media_type="application/octet-stream")

    @app.post("/api/scenes/{scene_id}/export/rerun")
    def scene_rerun(scene_id: str) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        relative = Path("exports") / f"{scene.scene_id}.rrd"
        try:
            dest = confine(app.state.root, str(relative))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            samples = export_rerun(scene, dest)
        except RerunUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"path": relative.as_posix(), "samples": samples}

    @app.post("/api/scenes/{scene_id}/export/sequence")
    def scene_sequence(scene_id: str, body: SequenceRequest) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        scene_dir = _scene_dir(app.state.root, scene_id)
        try:
            times = samples_in_range(list(pose_times(scene)), body.start_us, body.end_us)
            label = _export_label(body.agent_id, body.sensor_id)
            relative = _export_relative(
                scene.scene_id, label, times[0], times[-1], body.format, len(times)
            )
            dest = confine(app.state.root, relative)
            result = export_camera_sequence(
                scene,
                scene_dir,
                dest,
                agent_id=body.agent_id,
                sensor_id=body.sensor_id,
                start_us=body.start_us,
                end_us=body.end_us,
                fps=body.fps,
                width=body.width,
                height=body.height,
                format=body.format,
            )
        except SequenceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SequenceExportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _export_body(app.state.root, result)

    @app.post("/api/scenes/{scene_id}/export/view")
    def scene_view(scene_id: str, body: ViewSequenceRequest) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        try:
            frames = [_decode_frame(item) for item in body.frames_b64]
            relative = _export_relative(
                scene.scene_id, "view", body.start_us, body.end_us, body.format, len(frames)
            )
            dest = confine(app.state.root, relative)
            result = export_rendered_frames(frames, dest, fps=body.fps, format=body.format)
        except SequenceExportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _export_body(app.state.root, result)

    @app.post("/api/scenes/{scene_id}/export/bev")
    def scene_bev(scene_id: str, body: PlotRequest) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        try:
            when = _plot_time(scene, body.time_us)
            relative = _plot_relative(scene.scene_id, "bev", when, body.format)
            dest = confine(app.state.root, relative)
            result = render_bev(
                scene,
                dest,
                time_us=when,
                hdmap=_scene_hdmap(app, scene),
                format=body.format,
                theme=body.theme,
                color_by=body.color_by,
                points=body.points,
                boxes=body.boxes,
                draw_map=body.map,
                box_style=body.box_style,
                hidden_classes=tuple(body.hidden_classes),
                agents=None if body.agents is None else tuple(body.agents),
            )
        except PlotNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlotError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _plot_body(app.state.root, result)

    @app.post("/api/scenes/{scene_id}/export/overlay")
    def scene_overlay(scene_id: str, body: OverlayRequest) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        scene_dir = _scene_dir(app.state.root, scene_id)
        try:
            when = _plot_time(scene, body.time_us)
            label = _export_label(body.agent_id, body.sensor_id)
            relative = _plot_relative(scene.scene_id, f"{label}-overlay", when, body.format)
            dest = confine(app.state.root, relative)
            result = render_overlay(
                scene,
                scene_dir,
                dest,
                agent_id=body.agent_id,
                sensor_id=body.sensor_id,
                time_us=when,
                format=body.format,
                theme=body.theme,
                color_by=body.color_by,
                points=body.points,
                boxes=body.boxes,
                box_style=body.box_style,
                hidden_classes=tuple(body.hidden_classes),
                agents=None if body.agents is None else tuple(body.agents),
            )
        except PlotNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PlotError, SequenceExportError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _plot_body(app.state.root, result)

    @app.get("/api/files")
    def files(path: str) -> FileResponse:
        try:
            target = confine(app.state.root, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        return FileResponse(target)

    if (_STATIC / "index.html").is_file():
        app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")
    return app


class _SceneCache:
    """One decoded scene. The viewer re-reads when the path changes."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.scene: Scene | None = None

    def load(self, path: Path) -> Scene:
        resolved = path.resolve()
        if self.scene is None or self.path != resolved:
            self.scene = read_scene(resolved)
            self.path = resolved
        if self.scene is None:
            raise FileNotFoundError(resolved)
        return self.scene


def _scene_dir(root: Path, scene_id: str) -> Path:
    if scene_id == "" or "/" in scene_id or "\\" in scene_id or ".." in scene_id:
        raise HTTPException(status_code=400, detail="bad scene id")
    path = (root / scene_id).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bad scene id") from exc
    if not (path / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="scene not found")
    return path


def _cached_scene(app: FastAPI, scene_id: str) -> Scene:
    path = _scene_dir(app.state.root, scene_id)
    cache = app.state.scene_cache
    return cache.load(path)  # type: ignore[no-any-return]


def _maps_root(root: Path, maps: str | Path | None) -> Path | None:
    if maps is None:
        candidate = (root / "maps").resolve()
        return candidate if candidate.is_dir() else None
    return Path(maps).resolve()


def _scene_hdmap(app: FastAPI, scene: Scene) -> HdMap | None:
    maps = app.state.maps
    if not isinstance(maps, Path):
        return None
    name = scene_map_name(scene)
    if name is None:
        return None
    cache = app.state.map_cache
    if isinstance(cache, dict) and name in cache:
        found = cache[name]
        return found if isinstance(found, HdMap) else None
    try:
        directory = find_map(maps, name)
    except ValueError:
        if isinstance(cache, dict):
            cache[name] = None
        return None
    if directory is None:
        if isinstance(cache, dict):
            cache[name] = None
        return None
    hdmap = load_map(directory)
    if isinstance(cache, dict):
        cache[name] = hdmap
    return hdmap


def _map_summary(app: FastAPI, scene: Scene) -> dict[str, object]:
    name = scene_map_name(scene)
    hdmap = _scene_hdmap(app, scene)
    if hdmap is None:
        return {"map_name": name, "available": False}
    counts: dict[str, int] = {}
    for item in hdmap.polylines:
        counts[item.layer] = counts.get(item.layer, 0) + 1
    return {
        "map_name": hdmap.map_name,
        "available": True,
        "alignment_measured": hdmap.world_from_opendrive.measured,
        "sample_step_m": hdmap.sample_step_m,
        "layers": counts,
    }


def _export_label(agent_id: str, sensor_id: str) -> str:
    if _EXPORT_NAME.fullmatch(agent_id) is None or _EXPORT_NAME.fullmatch(sensor_id) is None:
        raise SequenceExportError("agent and sensor ids must be letters, digits, '_' or '-'")
    return f"{agent_id}-{sensor_id}"


def _export_relative(
    scene_id: str,
    label: str,
    start_us: int,
    end_us: int,
    fmt: str,
    count: int,
) -> str:
    if fmt not in FORMATS:
        raise SequenceExportError("format must be mp4, gif, or png")
    if _EXPORT_NAME.fullmatch(scene_id) is None or _EXPORT_NAME.fullmatch(label) is None:
        raise SequenceExportError("bad export name")
    stem = f"{scene_id}-{label}-{start_us}-{end_us}"
    if fmt == "png" and count != 1:
        return f"exports/{stem}"
    return f"exports/{stem}.{fmt}"


def _decode_frame(item: str) -> bytes:
    try:
        return base64.b64decode(item, validate=True)
    except Exception as exc:
        raise SequenceExportError("a frame is not base64") from exc


def _plot_time(scene: Scene, time_us: int | None) -> int:
    times = pose_times(scene)
    if time_us is None:
        if not times:
            raise PlotError("scene has no pose times")
        return times[0]
    return time_us


def _plot_relative(scene_id: str, label: str, time_us: int, fmt: str) -> str:
    if fmt not in {"png", "svg"}:
        raise PlotError("format must be png or svg")
    if time_us < 0:
        raise PlotError("bad sample time")
    if _EXPORT_NAME.fullmatch(scene_id) is None or _EXPORT_NAME.fullmatch(label) is None:
        raise PlotError("bad export name")
    return f"exports/{scene_id}-{label}-{time_us}.{fmt}"


def _plot_body(root: Path, result: PlotResult) -> dict[str, object]:
    relative = result.dest.resolve().relative_to(root.resolve())
    return {
        "path": relative.as_posix(),
        "format": result.format,
        "time_us": result.time_us,
        "width": result.width,
        "height": result.height,
        "map_drawn": result.map_drawn,
        "points": result.points,
        "boxes": result.boxes,
    }


def _export_body(root: Path, result: SequenceExport) -> dict[str, object]:
    relative = result.dest.resolve().relative_to(root.resolve())
    return {
        "path": relative.as_posix(),
        "frames": result.frames,
        "width": result.width,
        "height": result.height,
        "fps": result.fps,
        "format": result.format,
    }


def _json_error(status: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"detail": detail})


async def _event_stream(db_path: Path) -> AsyncIterator[str]:
    seen = 0
    for _ in range(3):
        conn = open_db(db_path)
        try:
            rows = list_jobs(conn)
        finally:
            conn.close()
        if len(rows) != seen:
            seen = len(rows)
            yield f"data: {json.dumps(rows)}\n\n"
        else:
            yield "data: []\n\n"
        await asyncio.sleep(0.01)
