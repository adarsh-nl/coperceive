# SPDX-License-Identifier: Apache-2.0
"""FastAPI app: health, allow-listed jobs, SSE, and a path-confined file read."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coperceive.app.jobs import CSRF_HEADER, confine, list_jobs, open_db, run_job
from coperceive.hdmap.store import HdMap, find_map, load_map, scene_map_name
from coperceive.model import Scene
from coperceive.rerun_export import RerunUnavailable, export_rerun
from coperceive.store import read_scene
from coperceive.viewdata import (
    frame_document,
    frame_points,
    image_path,
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
