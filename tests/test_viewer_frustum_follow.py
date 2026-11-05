# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Camera frustums and their image planes follow their agent, frame by frame.

Over the SSH link a camera image takes longer than a 10 Hz sample gap. The
image plane must still be posed at every drawn sample as
``agent pose(t) x camera mount``, for every agent with a camera, during
playback, scrubbing and a scene change, in both camera-image modes. Where a
sample has no image or no pose for a camera, its plane is hidden, never left
where it was. Synthetic scenes only.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import uvicorn
from coperceive.app.server import create_app
from coperceive.camera_images import stage_images
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    ImageRef,
    Modality,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from PIL import Image
from playwright.sync_api import Browser, Page
from starlette.requests import Request
from starlette.responses import Response

from tests.test_viewer_browser import (
    _bundle_dir,  # pyright: ignore[reportPrivateUsage]
    _free_port,  # pyright: ignore[reportPrivateUsage]
    _Session,  # pyright: ignore[reportPrivateUsage]
    _unavailable,  # pyright: ignore[reportPrivateUsage]
    browser,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
    playwright,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
)

SCENES = ("Follow-000", "Follow-001")
SAMPLES = 20
IMAGE_DELAY_S = 0.25  # longer than the 100 ms sample gap, as over the SSH tunnel
MOUNT = SE3.identity().translation(0.5, 0.0, 1.6)
# The collaborator has no image at these samples, and no pose (so no frustum) at these.
NO_IMAGE = frozenset({6, 7, 8})
NO_POSE = frozenset({12, 13})
# The second scene is far away: a plane left over from the first is easy to see.
SCENE_OFFSET = {"Follow-000": 0.0, "Follow-001": 500.0}


def _camera(agent_id: str) -> Sensor:
    return Sensor(
        "camera0",
        agent_id,
        Modality.CAMERA,
        MOUNT,
        image_width_px=64,
        image_height_px=48,
        fx=40.0,
        fy=40.0,
        cx=32.0,
        cy=24.0,
    )


def _scene(scene_id: str, raw: Path) -> Scene:
    """The ego drives +x 1 m per sample, a collaborator +y, a third agent is parked."""
    offset = SCENE_OFFSET[scene_id]
    tracks: dict[str, list[StampedPose]] = {"ego": [], "cav": [], "parked": []}
    images: list[ImageRef] = []
    for index in range(SAMPLES):
        time_us = index * 100_000
        tracks["ego"].append(StampedPose(time_us, SE3.identity().translation(offset + index, 0, 0)))
        if index not in NO_POSE:
            tracks["cav"].append(
                StampedPose(time_us, SE3.identity().translation(offset, 10.0 + index, 0))
            )
        tracks["parked"].append(StampedPose(time_us, SE3.identity().translation(offset, -10, 0)))
        for agent_id in tracks:
            if agent_id == "cav" and index in NO_IMAGE | NO_POSE:
                continue
            relative = f"{scene_id}/{agent_id}/{index:06d}.png"
            (raw / relative).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 48), (40 + 10 * index, 90, 160)).save(raw / relative)
            images.append(ImageRef(agent_id, "camera0", time_us, relative))
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock("us", "frustum-follow fixture, 10 Hz"),
        agents=tuple(Agent(a, AgentType.VEHICLE, tuple(p)) for a, p in tracks.items()),
        sensors=tuple(_camera(a) for a in tracks),
        annotations=(),
        objects=(),
        images=tuple(images),
    )


def _expected(scene_id: str, agent_id: str, index: int) -> tuple[float, float, float] | None:
    """World position of a camera's mount at a sample: agent pose(t) x mount."""
    if agent_id == "cav" and index in NO_POSE:
        return None
    offset = SCENE_OFFSET[scene_id]
    base = {"ego": (offset + index, 0.0), "cav": (offset, 10.0 + index), "parked": (offset, -10.0)}
    x, y = base[agent_id]
    return (x + 0.5, y, 1.6)


@pytest.fixture(scope="module", params=["originals", "previews"])
def follow_url(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle: set COPERCEIVE_WEB_DIST to a vite build directory")
    root = tmp_path_factory.mktemp(f"follow-{request.param}")
    raw, scenes = root / "raw", root / "scenes"
    for scene_id in SCENES:
        write_scene(_scene(scene_id, raw), scenes / scene_id)
        (scenes / scene_id / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
        # The same staging a cluster prepare does: originals through the raw link,
        # or 640 px previews.
        stage_images(scenes / scene_id, raw, request.param)
    app = create_app(root=scenes, token=None, static=bundle, media_roots=(raw,))

    @app.middleware("http")
    async def slow_images(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.endswith("/image"):
            await asyncio.sleep(IMAGE_DELAY_S)
        return await call_next(request)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="viewer-frustum-follow", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


SNAPSHOT = """() => {
    const graph = window.__COPERCEIVE_SCENE_GRAPH__ && window.__COPERCEIVE_SCENE_GRAPH__();
    const state = window.__COPERCEIVE_PLAYBACK_STATE__ && window.__COPERCEIVE_PLAYBACK_STATE__();
    const drawn = window.__LAST_DRAW__ || null;
    return { graph, drawn, frameIndex: state ? state.frameIndex : null };
}"""


def _snapshots(page: Page, seconds: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rows.append(page.evaluate(SNAPSHOT))
        page.wait_for_timeout(40)
    return rows


def _check(rows: list[dict[str, object]], scene_id: str) -> dict[str, set[tuple[float, ...]]]:
    """Every visible image plane sits where its camera is at the drawn sample."""
    seen: dict[str, set[tuple[float, ...]]] = {"ego": set(), "cav": set(), "parked": set()}
    for row in rows:
        graph = row["graph"]
        drawn = row["drawn"]
        if not isinstance(graph, dict) or not isinstance(drawn, dict):
            continue
        if drawn.get("sceneId") != scene_id:
            continue
        index = int(drawn["timeUs"]) // 100_000  # type: ignore[call-overload]
        lines = {f["key"]: f["position"] for f in graph["frustums"]}  # type: ignore[index]
        for plane in graph["imagePlanes"]:  # type: ignore[index]
            if not plane["visible"]:
                continue
            agent_id = plane["key"].split("/")[0]
            expected = _expected(scene_id, agent_id, index)
            where = f"{plane['key']} at sample {index} ({scene_id})"
            assert expected is not None, f"{where}: shown although the camera has no pose"
            assert not (agent_id == "cav" and index in NO_IMAGE), (
                f"{where}: shown although this sample has no image"
            )
            got = plane["position"]
            assert max(abs(a - b) for a, b in zip(got, expected, strict=True)) < 1e-3, (
                f"{where}: plane at {got}, camera at {expected}"
            )
            assert lines.get(plane["key"]) == got, (
                f"{where}: plane {got} != frustum {lines.get(plane['key'])}"
            )
            seen[agent_id].add(tuple(round(v, 3) for v in got))
    return seen


def test_image_planes_follow_their_agents_in_playback_scrubbing_and_scene_changes(
    browser: Browser,  # noqa: F811 - the imported fixture
    follow_url: str,
) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        "window.__COPERCEIVE_TEST_ENV__ = true;"
        "localStorage.setItem('coperceive-view', JSON.stringify("
        "{frustums: true, frustumImages: true, frustumImageCameras: null}));"
    )
    session = _Session(page, follow_url)
    page.goto(f"{follow_url}/#/viewer", wait_until="networkidle")
    session.wait_status(SCENES[0])
    page.locator("button[aria-label=Loop]").click()  # play once, stop on the last sample

    # Playback: the ego's and collaborator's planes move with them, the parked one stays.
    page.locator("button[aria-label=Play]").click()
    rows = _snapshots(page, SAMPLES * 0.1 + 2.5)
    seen = _check(rows, SCENES[0])
    assert len(seen["ego"]) >= SAMPLES // 2, sorted(seen["ego"])
    assert len(seen["cav"]) >= 4, sorted(seen["cav"])
    assert len(seen["parked"]) == 1, sorted(seen["parked"])

    # Scrubbing, onto samples with and without the collaborator's image and pose.
    for index in (15, 7, 12, 3):
        page.evaluate("(i) => window.__COPERCEIVE_PLAYBACK__('seek', i)", index)
        rows = _snapshots(page, IMAGE_DELAY_S + 0.8)
        _check(rows, SCENES[0])
        last = rows[-1]["graph"]
        assert isinstance(last, dict)
        planes = {p["key"]: p for p in last["imagePlanes"]}  # type: ignore[index]
        ego = planes.get("ego/camera0")
        assert ego is not None and ego["visible"], f"sample {index}: the ego's image is not shown"
        cav = planes.get("cav/camera0")
        cav_shown = cav is not None and cav["visible"]
        assert cav_shown == (index not in NO_IMAGE | NO_POSE), (index, cav)

    # A scene change: no plane is left behind in the first scene's place.
    page.keyboard.press("n")
    session.wait_status(SCENES[1])
    rows = _snapshots(page, IMAGE_DELAY_S + 1.0)
    seen = _check(rows, SCENES[1])
    assert seen["ego"] and seen["parked"], seen
    for row in rows:
        graph = row["graph"]
        drawn = row["drawn"]
        # Once the second scene is drawn, nothing from the first may still show.
        if (
            isinstance(graph, dict)
            and isinstance(drawn, dict)
            and drawn.get("sceneId") == SCENES[1]
        ):
            for plane in graph["imagePlanes"]:  # type: ignore[index]
                if plane["visible"]:
                    assert plane["position"][0] > 400, (
                        f"a first-scene plane is still shown: {plane}"
                    )
    assert session.errors == [], session.errors
    page.close()
