# SPDX-License-Identifier: Apache-2.0
"""FastAPI app: health, allow-listed jobs, SSE, and a path-confined file read."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coperceive.app.jobs import CSRF_HEADER, confine, confine_media, list_jobs, open_db, run_job
from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import predictions_root
from coperceive.evalview import EvalViewError, frame_rows, runs_for_scene, series
from coperceive.hdmap.store import HdMap, find_map, load_map, scene_map_name
from coperceive.model import Scene
from coperceive.plot import (
    PlotError,
    PlotNotFound,
    PlotResult,
    prediction_draws,
    render_bev,
    render_overlay,
)
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
    run_id: str | None = None


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
    predictions: str | Path | None = None,
    static: str | Path | None = None,
    media_roots: Sequence[str | Path] = (),
    allow_origin: str | None = None,
) -> FastAPI:
    """The app over ``root``.

    ``predictions`` is the predictions root (``docs/spec/predictions.md`` §Layout);
    without it, ``<root>/predictions`` is used when it holds a run, else the
    prediction routes answer 404 ``no predictions root``. ``static`` replaces the
    packaged page with another build directory. ``media_roots`` are directories a
    scene's image links may resolve into (a staged scene links ``<side>/camera``
    to the dataset copy); without them an image outside the scene is 400.
    """
    root_path_obj = Path(root).resolve()
    root_path_obj.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="coperceive",
        version="0.0.0",
        root_path=root_path,
        description="Local cooperative-perception data app.",
    )
    if allow_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[allow_origin],
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Coperceive-Request"],
        )
    app.state.root = root_path_obj
    app.state.token = token
    app.state.db_path = root_path_obj / "jobs.sqlite"
    app.state.scene_cache = _SceneCache()
    app.state.maps = _maps_root(root_path_obj, maps)
    app.state.map_cache = {}
    app.state.predictions = predictions_root(
        root_path_obj, None if predictions is None else Path(predictions).resolve()
    )
    app.state.media_roots = tuple(Path(item).resolve() for item in media_roots)
    static_dir = _STATIC if static is None else Path(static).resolve()

    @app.middleware("http")
    async def auth_and_csrf(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            expected = request.app.state.token
            if expected:
                header = request.headers.get("authorization", "")
                prefix = "Bearer "
                if not (
                    header.startswith(prefix)
                    and hmac.compare_digest(header[len(prefix) :], expected)
                ):
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
        return [
            {"scene_id": path.name}
            for path in scene_directories(app.state.root, require_complete=True)
        ]

    @app.get("/api/scenes/{scene_id}")
    def scene_summary(scene_id: str) -> dict[str, object]:
        scene = _cached_scene(app, scene_id)
        body = summarize(scene)
        body["images_staged"] = _images_staged(app, scene, _scene_dir(app.state.root, scene_id))
        return body

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

    @app.get("/api/scenes/{scene_id}/predictions")
    def scene_predictions(scene_id: str) -> dict[str, object]:
        _scene_dir(app.state.root, scene_id)
        root = _predictions_root(app)
        return _eval_view(lambda: runs_for_scene(root, scene_id))

    @app.get("/api/scenes/{scene_id}/predictions/{run_id}/frame")
    def scene_prediction_frame(
        scene_id: str, run_id: str, time_us: int, compare: str | None = None
    ) -> dict[str, object]:
        _scene_dir(app.state.root, scene_id)
        root = _predictions_root(app)
        return _eval_view(lambda: frame_rows(root, run_id, scene_id, time_us, compare=compare))

    @app.get("/api/scenes/{scene_id}/predictions/{run_id}/series")
    def scene_prediction_series(
        scene_id: str, run_id: str, compare: str | None = None
    ) -> dict[str, object]:
        _scene_dir(app.state.root, scene_id)
        root = _predictions_root(app)
        return _eval_view(lambda: series(root, run_id, scene_id, compare=compare))

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        svg = static_dir / "favicon.svg"
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
            target = confine_media(scene_dir, relative, app.state.media_roots)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            present = target.is_file()
        except OSError:
            # An over-long component or an unsearchable parent: the file is not there.
            present = False
        if not present:
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
            predictions = ()
            run_id = body.run_id
            if run_id is not None:
                root = _predictions_root(app)
                rows = _eval_view(lambda: frame_rows(root, run_id, scene_id, when, compare=None))
                predictions = prediction_draws(_frame_row_list(rows))
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
                predictions=predictions,
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

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


def _images_staged(app: FastAPI, scene: Scene, scene_dir: Path) -> bool | None:
    """Whether any recorded image file is present. ``None`` when the scene records no
    images. The table is sensor-major, so the sample is the first, middle and last row
    of each camera (at most 48 rows in all); a path the OS refuses to stat counts as
    absent rather than failing the summary."""
    rows = scene.images
    if not rows:
        return None
    by_camera: dict[tuple[str, str], list[int]] = {}
    for index, image in enumerate(rows):
        by_camera.setdefault((image.agent_id, image.sensor_id), []).append(index)
    picks: list[int] = []
    for indices in by_camera.values():
        picks.extend({indices[0], indices[len(indices) // 2], indices[-1]})
    for index in sorted(set(picks))[:48]:
        try:
            if confine_media(scene_dir, rows[index].path, app.state.media_roots).is_file():
                return True
        except (ValueError, OSError):
            continue
    return False


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
    if (root / "manifest.json").is_file() and (root / "COMPLETE.json").is_file():
        if scene_id in (root.name, ".", ""):
            return root
        try:
            loaded = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("scene_id") == scene_id:
                return root
        except Exception:
            pass
    try:
        # A NUL byte makes resolve() raise ValueError; an over-long name makes
        # the stat raise OSError. Both are a bad id, not a server error.
        path = (root / scene_id).resolve()
        path.relative_to(root)
        present = (path / "manifest.json").is_file() and (path / "COMPLETE.json").is_file()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="bad scene id") from exc
    if not present:
        raise HTTPException(status_code=404, detail="scene not found")
    return path


def _cached_scene(app: FastAPI, scene_id: str) -> Scene:
    path = _scene_dir(app.state.root, scene_id)
    cache = app.state.scene_cache
    return cache.load(path)  # type: ignore[no-any-return]


def _predictions_root(app: FastAPI) -> Path:
    root = app.state.predictions
    if not isinstance(root, Path):
        raise HTTPException(status_code=404, detail="no predictions root")
    return root


def _eval_view(read: Callable[[], dict[str, object]]) -> dict[str, object]:
    """Run one ``coperceive.evalview`` read; its errors become HTTP statuses."""
    try:
        return read()
    except EvalViewError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except CoperceiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        # An id that passed the name rule but not the OS (NUL byte, over-long).
        raise HTTPException(status_code=400, detail=f"bad id: {exc}") from exc


def _frame_row_list(body: dict[str, object]) -> list[dict[str, object]]:
    rows = body.get("rows")
    return rows if isinstance(rows, list) else []


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
        "predictions": result.predictions,
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
