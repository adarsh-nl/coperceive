# SPDX-License-Identifier: Apache-2.0
"""The viewer page in a real (headless) browser.

These tests answer what the HTTP tests cannot: that Play moves the timeline, that
choosing a scene re-reads frames, timeline and map for that scene, that the
Predictions block renders an evaluated run, and that each pane says what is
missing instead of staying blank.

The page bundle comes from ``COPERCEIVE_WEB_DIST`` (a ``vite build`` output
directory) and otherwise from the packaged ``coperceive/app/static``. The
packaged bundle can be older than ``web/src``; CI builds a fresh one. Screenshots
go to ``COPERCEIVE_E2E_SHOTS`` when that is set. Without Playwright, or when no
Chromium can start on this machine, the module is skipped with the reason.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest
import uvicorn
from coperceive.app.server import create_app
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import write_run
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import write_map
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    Clock,
    ImageRef,
    Modality,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions
from tests.test_hdmap import _straight  # pyright: ignore[reportPrivateUsage]

REQUIRED = os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}


def _unavailable(reason: str) -> NoReturn:
    """Skip, or fail where the browser tests are the point (CI's e2e job sets the flag)."""
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


try:
    from playwright.sync_api import Browser, Page, sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ImportError:  # pragma: no cover - the dev extra installs it
    if REQUIRED:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)

# One valid 1x1 PNG (the same bytes as tests/test_app.py), so the strip can decode it.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_CHROMIUM_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]
# Scene ids sort so that the page opens on the cooperative scene first.
SCENE_FULL = "Town03-000"  # lidar, an image file, a Town03 map, an evaluated run
SCENE_BARE = "Town06-007"  # 30 samples, no lidar, image rows without files, no Town06 map
BARE_SAMPLES = 30
FILLER_SCENES = tuple(f"Town07-{index:03d}" for index in range(1, 10))  # list length only


def _bundle_dir() -> Path | None:
    """``COPERCEIVE_WEB_DIST`` when set (and it must hold a build), else the packaged page."""
    wanted = os.environ.get("COPERCEIVE_WEB_DIST")
    if wanted:
        chosen = Path(wanted).expanduser()
        if not (chosen / "index.html").is_file():
            pytest.fail(f"COPERCEIVE_WEB_DIST={wanted} has no index.html")
        return chosen
    packaged = Path(__file__).resolve().parents[1] / "coperceive" / "app" / "static"
    return packaged if (packaged / "index.html").is_file() else None


def _bare_scene() -> Scene:
    """A vehicle driving +x for three seconds at 10 Hz, one car ahead of it each sample."""
    poses: list[StampedPose] = []
    annotations: list[Annotation] = []
    images: list[ImageRef] = []
    box = SE3.identity()
    for index in range(BARE_SAMPLES):
        time_us = index * 100_000
        poses.append(StampedPose(time_us, SE3.identity().translation(float(index), 0.0, 0.0)))
        annotations.append(
            Annotation("vehicle", time_us, "1", "car", (8.0, 0.0, 0.8), (4.0, 2.0, 1.6), box, 1.0)
        )
        images.append(
            ImageRef("vehicle", "front", time_us, f"vehicle-side/camera/front/{index:06d}.png")
        )
    vehicle = Agent(
        "vehicle",
        AgentType.VEHICLE,
        tuple(poses),
        platform=(("map_name", "Carla/Maps/Town06"),),
    )
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        1920,
        1080,
        1000.0,
        1000.0,
        960.0,
        540.0,
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=SCENE_BARE,
        world_frame="ENU",
        clock=Clock("us", "browser fixture, 10 Hz"),
        agents=(vehicle,),
        sensors=(camera,),
        annotations=tuple(annotations),
        objects=(),
        images=tuple(images),
    )


def _full_scene() -> Scene:
    """The cooperative fixture in Town03 with a second vehicle camera, so the strip's
    one-camera-at-a-time loading is observable."""
    scene = cooperative_scene()
    agents = tuple(
        replace(agent, platform=(*agent.platform, ("map_name", "Carla/Maps/Town03")))
        for agent in scene.agents
    )
    rear = Sensor(
        "cam_rear",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        1280,
        720,
        1000.0,
        1000.0,
        640.0,
        360.0,
    )
    return replace(
        scene,
        scene_id=SCENE_FULL,
        agents=agents,
        sensors=(*scene.sensors, rear),
        images=(
            *scene.images,
            ImageRef("vehicle", "cam_rear", 100_000, "vehicle/cam_rear/0100000.png"),
        ),
    )


def _filler_scene(scene_id: str) -> Scene:
    poses = (
        StampedPose(0, SE3.identity()),
        StampedPose(100_000, SE3.identity().translation(1.0, 0.0, 0.0)),
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock("us", "filler"),
        agents=(Agent("vehicle", AgentType.VEHICLE, poses),),
        sensors=(),
        annotations=(),
        objects=(),
    )


def _write_roots(base: Path) -> tuple[Path, Path, Path]:
    """Scenes, maps and predictions roots: one scene with everything, one with little."""
    scenes = base / "scenes"
    full = _full_scene()
    write_scene(full, scenes / SCENE_FULL)
    for image in full.images:
        target = scenes / SCENE_FULL / image.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_PNG)
    write_scene(_bare_scene(), scenes / SCENE_BARE)
    for scene_id in FILLER_SCENES:
        write_scene(_filler_scene(scene_id), scenes / scene_id)
    xodr = base / "Town03.xodr"
    _straight(xodr)
    maps = base / "maps"
    write_map(
        parse_opendrive(xodr, sample_step_m=5),
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source={"licence": "MIT", "note": "browser test fixture"},
    )
    predictions = base / "predictions"
    meta, boxes = synthetic_predictions(full)
    write_run(predictions, meta.run_id, meta, tuple(replace(b, scene_id=SCENE_FULL) for b in boxes))
    report = evaluate_run(
        scenes, predictions, meta.run_id, DEFAULT_CONFIG, created_utc="2026-10-17T00:00:00Z"
    )
    assert report.evaluated == (SCENE_FULL,), report
    return scenes, maps, predictions


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def viewer_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle: set COPERCEIVE_WEB_DIST to a vite build directory")
    scenes, maps, predictions = _write_roots(tmp_path_factory.mktemp("viewer"))
    app = create_app(root=scenes, token=None, maps=maps, predictions=predictions, static=bundle)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="viewer-e2e", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
        except PlaywrightError as exc:  # no browser build or missing system libraries
            _unavailable(f"chromium cannot start here: {str(exc).splitlines()[0]}")
        yield launched
        launched.close()


@pytest.fixture
def shots(tmp_path_factory: pytest.TempPathFactory) -> Path:
    wanted = os.environ.get("COPERCEIVE_E2E_SHOTS")
    directory = Path(wanted) if wanted else tmp_path_factory.mktemp("shots")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class _Session:
    """One page on the viewer, with the API calls and page errors it produced."""

    def __init__(self, page: Page, base: str) -> None:
        self.page = page
        self.base = base
        self.requests: list[str] = []
        self.errors: list[str] = []
        page.on("request", lambda request: self._record(request.url))
        page.on("pageerror", lambda error: self.errors.append(str(error)))

    def _record(self, url: str) -> None:
        if "/api/" in url:
            self.requests.append(url.split(self.base, 1)[-1])

    def open(self) -> None:
        self.page.goto(f"{self.base}/#/viewer", wait_until="networkidle")
        self.wait_status(SCENE_FULL)

    def status(self) -> str:
        return self.page.locator(".status-line").inner_text()

    def wait_status(self, text: str, timeout_s: float = 10.0) -> str:
        return self.wait_for(
            lambda: text in self.status(), f"status to mention {text!r}", timeout_s
        )

    def wait_for(self, ready: Callable[[], bool], what: str, timeout_s: float = 10.0) -> str:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if ready():
                return self.status()
            self.page.wait_for_timeout(100)
        raise AssertionError(f"waited {timeout_s}s for {what}; status: {self.status()!r}")

    def choose_scene(self, scene_id: str) -> None:
        self.page.locator("#scene-id").click()
        self.page.locator("[role=option]", has_text=scene_id).click()
        self.wait_status(scene_id)

    def slider(self, name: str) -> int:
        value = self.page.locator("[role=slider][aria-label=Sample]").get_attribute(name)
        assert value is not None, name
        return int(value)

    def ticks(self) -> list[str]:
        ticks = self.page.locator(".tick")
        return [ticks.nth(i).inner_text() for i in range(ticks.count())]

    def missing(self) -> list[str]:
        items = self.page.locator(".empty-pill li")
        return [items.nth(i).inner_text() for i in range(items.count())]


@pytest.fixture
def session(browser: Browser, viewer_url: str) -> Iterator[_Session]:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    session = _Session(page, viewer_url)
    session.open()
    yield session
    assert session.errors == [], session.errors
    page.close()


def test_play_advances_the_timeline_at_the_recorded_pace(session: _Session, shots: Path) -> None:
    """Play on a 30-sample, 10 Hz scene: the sample index climbs with the clock and the
    button reads Pause meanwhile. Around 3 s the last sample pauses playback again."""
    session.choose_scene(SCENE_BARE)
    assert session.slider("aria-valuemax") == BARE_SAMPLES - 1
    assert session.slider("aria-valuenow") == 0
    play = session.page.locator("button[aria-label=Play]")
    start = time.monotonic()  # before the click: the page starts its clock on the click
    play.click()
    assert session.page.locator("button[aria-label=Pause]").count() == 1
    seen: list[tuple[float, int]] = []
    for _ in range(6):
        session.page.wait_for_timeout(300)
        index = session.slider("aria-valuenow")
        seen.append((round(time.monotonic() - start, 2), index))  # elapsed after the read
    session.page.screenshot(path=str(shots / "play.png"))
    values = [index for _, index in seen]
    assert values == sorted(values), seen
    assert len(set(values)) >= 3, seen
    assert values[-1] > values[0], seen
    # 10 Hz at 1x: about ten samples per second, never ahead of the clock.
    for elapsed, index in seen:
        assert index <= int(elapsed * 10) + 2, seen
    session.wait_for(
        lambda: session.page.locator("button[aria-label=Play]").count() == 1,
        "playback to pause on the last sample",
        timeout_s=6.0,
    )
    assert session.slider("aria-valuenow") == BARE_SAMPLES - 1
    assert "sample 30 / 30" in session.page.locator(".clock").inner_text()


def test_choosing_a_scene_reloads_frames_timeline_and_map(session: _Session, shots: Path) -> None:
    """Both directions between two scenes in two towns: the frame API is asked for the
    chosen scene, the timeline takes its samples, and the map follows its own town."""
    session.wait_status("map drawn.")
    assert session.page.locator("#scene-id").inner_text() == SCENE_FULL
    full_ticks = session.ticks()
    assert session.slider("aria-valuemax") == 6  # the cooperative fixture's seven pose times

    session.requests.clear()
    session.choose_scene(SCENE_BARE)
    session.wait_status("no map for Town06.")
    assert f"/api/scenes/{SCENE_BARE}" in session.requests
    assert f"/api/scenes/{SCENE_BARE}/frame?time_us=0" in session.requests
    assert f"/api/scenes/{SCENE_BARE}/map" in session.requests
    assert session.page.locator("#scene-id").inner_text() == SCENE_BARE
    assert session.slider("aria-valuemax") == BARE_SAMPLES - 1
    assert session.slider("aria-valuenow") == 0
    bare_ticks = session.ticks()
    assert bare_ticks != full_ticks
    assert bare_ticks[-1] == "2.9 s"
    assert "Town06" in session.page.locator(".crumb-dataset").inner_text()
    session.page.screenshot(path=str(shots / "scene-switch.png"))

    session.requests.clear()
    session.choose_scene(SCENE_FULL)
    session.wait_status("map drawn.")
    assert f"/api/scenes/{SCENE_FULL}/frame?time_us=0" in session.requests
    assert f"/api/scenes/{SCENE_FULL}/map/lines" in session.requests
    assert session.slider("aria-valuemax") == 6
    assert session.ticks() == full_ticks


def test_predictions_block_lists_the_run_and_counts_its_matches(
    session: _Session, shots: Path
) -> None:
    """With a predictions root the block names the evaluated run, the status chips carry
    this sample's counts once the match rows are drawn, and the timeline grows the two
    evaluation sparklines."""
    assert session.page.locator("summary", has_text="Predictions").count() == 1
    run = session.page.locator("#prediction-run")
    session.wait_for(lambda: run.count() == 1 and run.inner_text() == "synthetic", "the run menu")
    chips = session.page.locator("[aria-label=Statuses] .chip .num")
    session.wait_for(lambda: chips.count() >= 3, "status chips with counts")
    counts = [int(chips.nth(i).inner_text()) for i in range(chips.count())]
    assert sum(counts) > 0, counts
    assert session.page.locator(".legend", has_text="TP").count() == 1
    assert session.page.locator(".spark-eval").count() == 2
    card = (
        session.page.locator(".eval-head").first.inner_text()
        + session.page.locator(".eval-grid").first.inner_text()
    )
    assert "map@0.5" in card.lower(), card  # CSS uppercases the labels
    assert session.missing() == []
    session.page.screenshot(path=str(shots / "predictions.png"))


def test_each_pane_says_what_is_missing(session: _Session, shots: Path) -> None:
    """A scene without lidar, image files, map or predictions names each gap in the
    viewport pill, the layers pane and the camera panel, and asks for no image files."""
    session.requests.clear()
    session.choose_scene(SCENE_BARE)
    session.wait_for(lambda: len(session.missing()) >= 4, "the missing-data pill")
    assert session.missing() == [
        "Points: no lidar staged.",
        "Camera: no camera images staged.",
        "Map: no map for Town06.",
        "Predictions: no prediction runs for this scene.",
    ]
    layer_notes = session.page.locator(".panel .empty-state")
    assert [layer_notes.nth(i).inner_text() for i in range(layer_notes.count())] == [
        "Points: no lidar staged.",
        "Map: no map for Town06.",
    ]
    assert (
        session.page.locator(".camera-missing").inner_text() == "No image: no camera images staged."
    )
    assert (
        "no prediction runs for this scene."
        in session.page.locator("details", has_text="Predictions").inner_text()
    )
    assert session.status().endswith("no map for Town06.")
    assert not [url for url in session.requests if "/image?" in url], session.requests
    session.page.screenshot(path=str(shots / "empty-states.png"))


def test_scene_list_shows_every_scene_without_scrolling(session: _Session) -> None:
    """Eleven scenes: the last option sits inside the open list's box and is clickable.
    (A 240 px list showed eight rows and hid the rest behind an overlay scrollbar.)"""
    session.page.locator("#scene-id").click()
    options = session.page.locator("[role=option]")
    assert options.count() == 2 + len(FILLER_SCENES)
    last = options.nth(options.count() - 1)
    assert last.inner_text() == FILLER_SCENES[-1]
    list_box = session.page.locator(".menu-list").bounding_box()
    last_box = last.bounding_box()
    assert list_box is not None and last_box is not None
    assert last_box["y"] + last_box["height"] <= list_box["y"] + list_box["height"] + 1
    last.click()
    session.wait_status(FILLER_SCENES[-1])
    assert session.page.locator("#scene-id").inner_text() == FILLER_SCENES[-1]


def test_camera_strip_requests_only_the_focused_camera(session: _Session) -> None:
    """At the sample with two camera files, one image request (the focused camera); the
    second camera's file is fetched when it is selected."""
    step = session.page.locator("button[aria-label='Step forward']")
    step.click()
    step.click()
    session.wait_for(
        lambda: "sample 3 / 7" in session.page.locator(".clock").inner_text(), "sample 3"
    )

    def at_sample() -> set[str]:
        return {
            url.split("sensor_id=")[-1]
            for url in session.requests
            if "/image?" in url and "time_us=100000" in url
        }

    session.wait_for(lambda: at_sample() == {"cam_front"}, "the focused camera's image")
    session.wait_for(lambda: session.page.locator(".pip canvas").count() == 1, "the decoded image")
    assert at_sample() == {"cam_front"}
    session.page.locator("button.pip-name", has_text="cam_rear").click()
    session.wait_for(
        lambda: at_sample() == {"cam_front", "cam_rear"}, "the selected camera's image"
    )


def test_choosing_a_scene_while_playing_pauses_at_once(session: _Session) -> None:
    """The old scene stops prefetching when another scene is chosen, so the chosen
    scene's summary is not queued behind it."""
    session.choose_scene(SCENE_BARE)
    session.page.locator("button[aria-label=Play]").click()
    session.page.wait_for_timeout(700)
    assert session.page.locator("button[aria-label=Pause]").count() == 1
    session.requests.clear()
    session.page.locator("#scene-id").click()
    session.page.locator("[role=option]", has_text=SCENE_FULL).click()
    assert session.page.locator("button[aria-label=Play]").count() == 1
    session.wait_status(SCENE_FULL, timeout_s=3.0)
    old = [url for url in session.requests if f"/api/scenes/{SCENE_BARE}/" in url]
    assert len(old) <= 10, old
    assert session.slider("aria-valuenow") == 0
