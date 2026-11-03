# SPDX-License-Identifier: Apache-2.0
"""Playback over a slow point-cloud link, as through the SSH tunnel to a cluster.

The server here answers ``/points`` requests one at a time, 300 ms each, three
times the 10 Hz sample gap, like a bandwidth-limited tunnel. The boxes must
never move ahead of the point cloud: on every rendered frame during playback
both come from the same sample, and the timeline says "Buffering…" while it
waits instead of running on.
"""

from __future__ import annotations

import asyncio
import struct
import threading
import time
from collections.abc import Awaitable, Callable, Iterator

import pytest
import uvicorn
from coperceive.app.server import create_app
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from playwright.sync_api import Browser
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

SCENE = "Slow-000"
SAMPLES = 20
POINTS_PER_SWEEP = 64
POINTS_DELAY_S = 0.3


def _slow_scene() -> Scene:
    """A vehicle driving +x at 10 Hz with a lidar sweep and a car box on every sample."""
    poses: list[StampedPose] = []
    annotations: list[Annotation] = []
    clouds: list[PointCloud] = []
    for index in range(SAMPLES):
        time_us = index * 100_000
        poses.append(StampedPose(time_us, SE3.identity().translation(float(index), 0.0, 0.0)))
        annotations.append(
            Annotation(
                "vehicle",
                time_us,
                "1",
                "car",
                (8.0 + index, 0.0, 0.8),
                (4.0, 2.0, 1.6),
                SE3.identity(),
                1.0,
            )
        )
        xyz_i = b"".join(
            struct.pack("<4f", float(index) + point * 0.1, 1.0, 0.0, 1.0)
            for point in range(POINTS_PER_SWEEP)
        )
        clouds.append(PointCloud("vehicle", "lidar_top", time_us, POINTS_PER_SWEEP, xyz_i))
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=SCENE,
        world_frame="ENU",
        clock=Clock("us", "slow-points fixture, 10 Hz"),
        agents=(Agent("vehicle", AgentType.VEHICLE, tuple(poses)),),
        sensors=(
            Sensor("lidar_top", "vehicle", Modality.LIDAR, SE3.identity().translation(0, 0, 1.5)),
        ),
        annotations=tuple(annotations),
        objects=(),
        point_clouds=tuple(clouds),
    )


@pytest.fixture(scope="module")
def slow_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle: set COPERCEIVE_WEB_DIST to a vite build directory")
    scenes = tmp_path_factory.mktemp("slow") / "scenes"
    write_scene(_slow_scene(), scenes / SCENE)
    (scenes / SCENE / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
    app = create_app(root=scenes, token=None, static=bundle)
    # A narrow pipe, like the SSH tunnel: one sweep at a time, 300 ms each, so
    # prefetching several at once cannot make up for it.
    pipe = asyncio.Lock()

    @app.middleware("http")
    async def slow_points(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.endswith("/points"):
            async with pipe:
                await asyncio.sleep(POINTS_DELAY_S)
        return await call_next(request)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="viewer-slow-points", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_points_and_boxes_stay_on_the_same_sample_while_playing_over_a_slow_link(
    browser: Browser,  # noqa: F811 - the imported fixture
    slow_url: str,
) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script("window.__COPERCEIVE_TEST_ENV__ = true;")
    session = _Session(page, slow_url)
    page.goto(f"{slow_url}/#/viewer", wait_until="networkidle")
    session.wait_status(SCENE)
    session.wait_for(lambda: "64 points" in session.status(), "the first sweep to draw")
    page.locator("button[aria-label=Loop]").click()  # play once, stop on the last sample

    # The label can be up for well under a polling interval; watch for it in the page.
    page.evaluate("""() => {
        window.__SAW_BUFFERING__ = false;
        new MutationObserver(() => {
            if (document.querySelector('.clock .buffering')) window.__SAW_BUFFERING__ = true;
        }).observe(document.body, {childList: true, subtree: true});
    }""")
    play_start = page.evaluate("performance.now()")
    page.locator("button[aria-label=Play]").click()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (
            session.slider("aria-valuenow") == SAMPLES - 1
            and page.locator("button[aria-label=Play], button[aria-label=Replay]").count()
        ):
            break
        page.wait_for_timeout(50)
    else:
        raise AssertionError(f"playback did not reach the last sample: {session.status()!r}")
    page.wait_for_timeout(POINTS_DELAY_S * 1000 + 300)

    rendered = page.evaluate("window.__COPERCEIVE_RENDERED_TIMES__ || []")
    played = [row for row in rendered if row["at"] >= play_start]
    mismatched = [
        row
        for row in played
        if row["points"] is not None and row["boxes"] is not None and row["points"] != row["boxes"]
    ]
    assert not mismatched, mismatched[:5]
    shown = [row["points"] for row in played if row["points"] is not None]
    # Points only ever move forward, end on the last sample, and most samples were shown.
    assert shown == sorted(shown), shown
    assert shown and shown[-1] == (SAMPLES - 1) * 100_000, shown
    assert len(set(shown)) >= SAMPLES // 2, shown
    assert page.evaluate("window.__SAW_BUFFERING__"), (
        "the timeline never said Buffering… although every sweep took 300 ms"
    )

    stats = page.evaluate("window.__COPERCEIVE_FRAME_STATS__ || []")
    applied = [row for row in stats if row["at"] >= play_start and row["result"] == "applied"]
    # The viewer log times how long each drawn sample waited for its points; over this
    # link some waited longer than a whole sample gap (prefetch had started the rest).
    assert applied, stats[-5:]
    assert any((row["points_ms"] or 0) > 100 for row in applied), applied
    assert session.errors == [], session.errors
    page.close()
