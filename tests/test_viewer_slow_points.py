# SPDX-License-Identifier: Apache-2.0
"""Playback over a slow point-cloud link, as through the SSH tunnel to a cluster.

The server here answers ``/points`` requests one at a time, 600 ms each, six
times the 10 Hz sample gap, like a bandwidth-limited tunnel. The scene is long
enough (40 samples) that even a slow page load cannot prefetch it all before
playback starts, so the underrun is deterministic rather than load-dependent. The boxes must
never move ahead of the point cloud: on every rendered frame during playback
both come from the same sample, and the timeline says "Buffering…" while it
waits instead of running on.

The second test covers the time-based stream buffer: the timeline's buffer bar
fills ahead of the playhead, and playback that runs dry pauses with
"Buffering…" and resumes by itself once a playback second is cached again.
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
SAMPLES = 40
POINTS_PER_SWEEP = 64
POINTS_DELAY_S = 0.6


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
    page.add_init_script(
        "window.__COPERCEIVE_TEST_ENV__ = true;"
        "window.__COPERCEIVE_INITIAL_STATE__ = {buffer_target_seconds: 1};"
    )
    session = _Session(page, slow_url)
    # Not "networkidle": the background buffer keeps fetching sweeps, and this
    # test needs playback to start while the buffer is still filling -- that is
    # what makes the link slow enough to hold on a sample.
    page.goto(f"{slow_url}/#/viewer", wait_until="domcontentloaded")
    session.wait_status(SCENE)
    # The scheduler asks for the current sweep before prefetching ahead (see
    # streamBuffer.test.ts), so on the serialized 600 ms link the first draw
    # waits a little over one sweep; the default 10 s is ample.
    session.wait_for(lambda: "64 points" in session.status(), "the first sweep to draw")
    page.locator("button[aria-label=Loop]").click()  # play once, stop on the last sample

    # The slow link (~1.7 sweeps/s against 10 samples/s playback) must run the
    # buffer dry soon after Play, so the timeline says "Buffering…" while it
    # holds. Poll the label directly: it stays up while the refill happens.
    play_start = page.evaluate("performance.now()")
    play_started = time.monotonic()
    page.locator("button[aria-label=Play]").click()
    session.wait_for(
        lambda: page.locator(".clock .buffering").count() > 0,
        'the timeline to say "Buffering…"',
        timeout_s=20.0,
    )
    deadline = time.monotonic() + 60
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
    # Points only ever move forward and end on the last sample. The buffer pauses
    # and resumes (and a long main-thread stall may catch the wall clock up), so
    # the count of distinct drawn samples is not fixed; the invariants are the
    # sample consistency, the monotonic order and ending on the last sample.
    assert shown == sorted(shown), shown
    assert shown and shown[-1] == (SAMPLES - 1) * 100_000, shown
    assert len(set(shown)) >= 2, shown

    stats = page.evaluate("window.__COPERCEIVE_FRAME_STATS__ || []")
    applied = [row for row in stats if row["at"] >= play_start and row["result"] == "applied"]
    assert applied, stats[-5:]
    # The time-based buffer pauses for data instead of stuttering, so the wall
    # clock still shows the slow link: 1.9 s of recorded 10 Hz playback cannot
    # have finished in under the scene's own 1.9 s plus a buffer refill.
    play_wall_s = time.monotonic() - play_started
    assert play_wall_s > 2.5, f"playback did not appear to wait: {play_wall_s:.2f} s"
    assert session.errors == [], session.errors
    page.close()


def test_the_buffer_bar_fills_and_playback_buffers_then_resumes(
    browser: Browser,  # noqa: F811 - the imported fixture
    slow_url: str,
) -> None:
    """The time-based buffer on a slow link: the timeline shows a buffer bar that
    fills ahead of the playhead, playback that runs dry pauses instead of
    stuttering and says "Buffering…", and it resumes by itself once enough is
    cached (the 1 s resume threshold).

    The 1 s target is reachable, and while not playing the scheduler stops there,
    so at Play time at most ~1 s is buffered; the slow link (one serialized sweep
    request per 600 ms = ~1.7 samples/s against 10 samples/s playback) drains that
    buffer quickly, and the 4 s scene is long enough that a slow page load cannot
    have prefetched it all first, so the underrun and auto-resume are deterministic.
    """
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        "window.__COPERCEIVE_TEST_ENV__ = true;"
        "window.__COPERCEIVE_INITIAL_STATE__ = {buffer_target_seconds: 1, show_stream_stats: true};"
    )
    session = _Session(page, slow_url)
    # "domcontentloaded" rather than "networkidle": the buffer fills in the
    # background, so the network stays busy for as long as the prefetch runs.
    page.goto(f"{slow_url}/#/viewer", wait_until="domcontentloaded")
    session.wait_status(SCENE)
    # The scheduler asks for the current sweep before prefetching ahead (see
    # streamBuffer.test.ts), so on the serialized 600 ms link the first draw
    # waits a little over one sweep; the default 10 s is ample.
    session.wait_for(lambda: "64 points" in session.status(), "the first sweep to draw")

    # The stats line is on through the initial state, and the buffer bar is part
    # of the timeline track (it starts empty and fills as sweeps arrive).
    assert page.locator("[data-testid=stream-stats]").count() == 1
    stats_line = page.locator("[data-testid=stream-stats]").inner_text()
    assert "buffer" in stats_line and "p50" in stats_line, stats_line
    assert page.locator(".track-buffered").count() == 1
    assert page.locator(".track-buffered").get_attribute("data-target-us") == "1000000"
    fraction = float(page.locator(".track-buffered").get_attribute("data-buffered-fraction") or 0)
    assert 0.0 <= fraction <= 1.0

    def buffered_us() -> int:
        value = page.locator(".track-buffered").get_attribute("data-buffered-us")
        return int(value) if value else 0

    # The bar fills ahead of the playhead as sweeps arrive.
    session.wait_for(
        lambda: buffered_us() >= 100_000, "the buffer bar to fill ahead", timeout_s=20.0
    )

    # Play right away: while paused the scheduler stops at the 1 s target, and the
    # slow link cannot keep 10 samples/s, so playback must run dry. The label is polled
    # directly (it stays up for as long as the refill takes) instead of relying
    # on a mutation observer noticing a transient class change.
    page.locator("button[aria-label=Play]").click()
    session.wait_for(
        lambda: page.locator(".clock .buffering").count() > 0,
        'the timeline to say "Buffering…"',
        timeout_s=20.0,
    )
    start = session.slider("aria-valuenow")
    # Auto-resume: no click, the label clears, playback runs again.
    session.wait_for(
        lambda: (
            page.locator(".clock .buffering").count() == 0
            and page.locator("button[aria-label=Pause]").count() == 1
            and session.slider("aria-valuenow") > start
        ),
        "playback to resume by itself after the buffer refills",
        timeout_s=30.0,
    )
    page.wait_for_timeout(300)
    buffer_stats = page.evaluate("window.__COPERCEIVE_BUFFER_STATS__ || null")
    assert buffer_stats is not None, "the buffer stats were never exposed"
    assert buffer_stats["target_seconds"] == 1, buffer_stats
    assert buffer_stats["buffered_us"] > 0, buffer_stats
    assert buffer_stats["ready_frames"] > 1, buffer_stats
    assert buffer_stats["cache_bytes"] > 0, buffer_stats
    # The stats line is still there once the buffer recovered.
    assert "buffer" in page.locator("[data-testid=stream-stats]").inner_text()
    assert session.errors == [], session.errors
    page.close()
