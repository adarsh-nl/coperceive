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
import json
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

import pytest
import uvicorn
from coperceive.adapters.naming import display_names
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
    from playwright.sync_api import Browser, Page, Playwright, sync_playwright
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
    # Display names as ingest generates them (Vehicle 1, Drone 1, RSU 1); the bare scene has none.
    agents = display_names(
        tuple(
            replace(agent, platform=(*agent.platform, ("map_name", "Carla/Maps/Town03")))
            for agent in scene.agents
        )
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
            ImageRef("vehicle", "cam_front", 200_000, "vehicle/cam_front/0200000.png"),
            ImageRef("vehicle", "cam_rear", 200_000, "vehicle/cam_rear/0200000.png"),
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
    for child in scenes.iterdir():
        if child.is_dir() and (child / "manifest.json").is_file():
            (child / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
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


def _launch(playwright: Playwright, args: list[str]) -> Browser:
    try:
        return playwright.chromium.launch(headless=True, args=args)
    except PlaywrightError as exc:  # no browser build or missing system libraries
        _unavailable(f"chromium cannot start here: {str(exc).splitlines()[0]}")


@pytest.fixture(scope="module")
def playwright() -> Iterator[Playwright]:
    """One sync Playwright per module. Its event loop runs for as long as the context is
    open, so a second ``sync_playwright()`` (for another browser) is refused as nested."""
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="module")
def browser(playwright: Playwright) -> Iterator[Browser]:
    launched = _launch(playwright, _CHROMIUM_ARGS)
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
        page.add_init_script("window.__COPERCEIVE_TEST_ENV__ = true;")
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
    page.add_init_script("window.__COPERCEIVE_TEST_ENV__ = true;")
    session = _Session(page, viewer_url)
    session.open()
    yield session
    assert session.errors == [], session.errors
    page.close()


def test_a_stale_restored_scene_falls_back_and_layers_default_on(
    browser: Browser, viewer_url: str
) -> None:
    """A saved session from a removed scene must open an existing scene, not "not found";
    Points and Boxes default on when no explicit layer choice was stored."""
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        "window.__COPERCEIVE_INITIAL_STATE__ = {last_scene_id: 'synth_001', last_sample_index: 3};"
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{viewer_url}/#/viewer", wait_until="networkidle")
    page.wait_for_selector("#scene-id", timeout=30000)
    session = _Session(page, viewer_url)
    session.wait_for(
        lambda: session.page.locator("#scene-id").inner_text() == SCENE_FULL,
        "the first existing scene",
    )
    text = page.locator("body").inner_text().lower()
    assert "not found" not in text, text
    assert "synth_001" not in text, text
    for layer in ("Points", "Boxes"):
        assert page.get_by_role("switch", name=layer).get_attribute("aria-checked") == "true"
    assert errors == [], errors
    page.close()


def test_play_advances_the_timeline_at_the_recorded_pace(session: _Session, shots: Path) -> None:
    """Play on a 30-sample, 10 Hz scene: the sample index climbs with the clock and the
    button reads Pause meanwhile. Around 3 s the last sample pauses playback again."""
    session.choose_scene(SCENE_BARE)
    assert session.slider("aria-valuemax") == BARE_SAMPLES - 1
    assert session.slider("aria-valuenow") == 0
    # Turn loop OFF to test pausing at the last sample
    session.page.locator("button[aria-label=Loop]").click()
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
        lambda: (
            session.page.locator("button[aria-label=Replay]").count() == 1
            or session.page.locator("button[aria-label=Play]").count() == 1
        ),
        "playback to pause on the last sample",
        timeout_s=6.0,
    )
    assert session.slider("aria-valuenow") == BARE_SAMPLES - 1
    assert "sample 30 / 30" in session.page.locator(".clock").inner_text()


def test_playback_has_no_render_stall(session: _Session) -> None:
    """At 1x on the synthetic 10 Hz scene, no rendered-frame gap may exceed 350 ms.

    This catches a main-thread stall (shader recompilation, image decode, layout)
    while playing. The first two gaps after Play are ignored: one-off shader
    compilation/warm-up happens on the first frames, not mid-playback.
    """
    session.choose_scene(SCENE_BARE)
    session.wait_for(lambda: session.slider("aria-valuenow") == 0, "initial sample")
    page = session.page
    page.locator("button[aria-label=Play]").click()
    play_start = page.evaluate("performance.now()")
    page.wait_for_timeout(2200)
    stats = page.evaluate("window.__COPERCEIVE_FRAME_STATS__ || []")
    played = [stat for stat in stats if stat["at"] >= play_start]
    assert len(played) >= 8, played
    gaps = [played[index]["at"] - played[index - 1]["at"] for index in range(1, len(played))][2:]
    assert gaps, played
    assert max(gaps) <= 350, gaps


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
    assert any(request.startswith(f"/api/scenes/{SCENE_BARE}/map") for request in session.requests)
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
        session.page.locator(".cam-strip-note").inner_text() == "No image: no camera images staged."
    )
    assert session.page.locator(".cam-thumb canvas").count() == 0
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


def _image_requests(session: _Session, time_us: int) -> list[str]:
    return [
        url.split("sensor_id=")[-1].split("&")[0]
        for url in session.requests
        if "/image?" in url and f"time_us={time_us}&" in url
    ]


def _at_sample(session: _Session, label: str) -> None:
    session.wait_for(lambda: label in session.page.locator(".clock").inner_text(), label)


def _stored_view(session: _Session) -> dict[str, object]:
    """The page's `coperceive-view` settings."""
    raw: object = session.page.evaluate("() => JSON.parse(localStorage.getItem('coperceive-view'))")
    assert isinstance(raw, dict)
    rows = cast(dict[object, object], raw)
    return {str(key): value for key, value in rows.items()}


def _strip_open(session: _Session) -> bool:
    return session.page.locator(".cam-strip").get_attribute("data-open") == "true"


def test_camera_strip_fetches_visible_thumbnails_once_and_nothing_when_collapsed(
    session: _Session,
) -> None:
    """Open strip at the sample with two camera files: one thumbnail request per camera;
    opening the split pane asks for that camera at the split width (a second, larger thumbnail).
    Collapsed (C), the next sample with files is not requested; opening the strip again fetches
    it."""
    step = session.page.locator("button[aria-label='Step forward']")
    step.click()
    step.click()
    _at_sample(session, "sample 3 / 7")
    session.wait_for(
        lambda: sorted(_image_requests(session, 100000)) == ["cam_front", "cam_rear"],
        "both thumbnails",
    )
    session.wait_for(lambda: session.page.locator(".cam-thumb canvas").count() == 2, "thumbnails")
    session.page.locator(".cam-thumb[data-camera='vehicle/cam_front']").click()
    session.wait_for(lambda: session.page.locator(".camera-split canvas").count() == 1, "split")
    # The split pane asks for the same camera at its display width; the strip itself did not
    # refetch either thumbnail (no duplicate 160 px requests).
    assert sorted(_image_requests(session, 100000)) == ["cam_front", "cam_front", "cam_rear"]
    session.page.keyboard.press("Escape")
    session.page.keyboard.press("c")
    session.wait_for(lambda: not _strip_open(session), "collapsed")
    step.click()
    step.click()
    _at_sample(session, "sample 5 / 7")
    session.page.wait_for_timeout(600)
    assert _image_requests(session, 200000) == []
    session.page.keyboard.press("c")
    session.wait_for(
        lambda: sorted(_image_requests(session, 200000)) == ["cam_front", "cam_rear"],
        "thumbnails after reopening",
    )


def _hit_targets(session: _Session) -> dict[str, str]:
    """What elementFromPoint finds at the 3D canvas centre and its four corners, 14 px in
    (the viewport's 12 px rounded corners clip the canvas itself). The non-interactive
    HUD pills are pointer-transparent; the viewpoint switch at the top right is not."""
    found: object = session.page.evaluate(
        """() => {
          const canvas = document.querySelector('.viewer-canvas');
          const b = canvas.getBoundingClientRect();
          const pts = {
            centre: [b.left + b.width / 2, b.top + b.height / 2],
            top_left: [b.left + 14, b.top + 14],
            top_right: [b.right - 14, b.top + 14],
            bottom_left: [b.left + 14, b.bottom - 14],
            bottom_right: [b.right - 14, b.bottom - 14],
          };
          const out = {};
          for (const [name, [x, y]] of Object.entries(pts)) {
            const el = document.elementFromPoint(x, y);
            const pill = el && el.closest('.view-pill');
            out[name] = el === canvas ? 'canvas' : pill ? 'view-pill' : el ? el.tagName : 'none';
          }
          return out;
        }"""
    )
    assert isinstance(found, dict)
    rows = cast(dict[object, object], found)
    return {str(key): str(value) for key, value in rows.items()}


def _assert_canvas_uncovered(session: _Session) -> None:
    hits = _hit_targets(session)
    expected = {
        "centre": "canvas",
        "top_left": "canvas",
        "bottom_left": "canvas",
        "bottom_right": "canvas",
    }
    assert {k: hits[k] for k in expected} == expected, hits
    assert hits["top_right"] in {"canvas", "view-pill"}, hits  # the viewpoint switch lives there


def test_the_3d_canvas_is_never_covered_and_keeps_its_height(
    session: _Session, shots: Path
) -> None:
    """With the strip open, collapsed and in the split view, the canvas centre and corners
    hit the canvas itself, and the split view takes width, not height, from the 3D view."""
    canvas = session.page.locator(".viewer-canvas")
    before = canvas.bounding_box()
    assert before is not None
    _assert_canvas_uncovered(session)
    session.page.keyboard.press("c")
    session.wait_for(lambda: not _strip_open(session), "collapsed")
    _assert_canvas_uncovered(session)
    session.page.screenshot(path=str(shots / "strip-collapsed.png"))
    session.page.keyboard.press("c")
    session.wait_for(lambda: session.page.locator(".cam-thumb").count() == 2, "thumbnails")
    session.page.locator(".cam-thumb[data-camera='vehicle/cam_rear']").click()
    session.wait_for(lambda: session.page.locator(".camera-split").count() == 1, "the split view")
    split = canvas.bounding_box()
    assert split is not None
    assert split["height"] == pytest.approx(before["height"], abs=1)
    assert split["width"] < before["width"] * 0.85
    _assert_canvas_uncovered(session)
    assert session.page.locator(".split-divider").count() == 1
    session.page.screenshot(path=str(shots / "split-view.png"))


def test_c_toggles_the_strip_and_a_thumbnail_opens_and_closes_the_split(
    session: _Session,
) -> None:
    assert _strip_open(session)
    assert session.page.locator(".cam-group").count() == 1  # one agent with cameras here
    session.page.keyboard.press("C")
    assert not _strip_open(session)
    assert session.page.locator(".cam-thumb").count() == 0
    session.page.keyboard.press("C")
    assert _strip_open(session)
    thumb = session.page.locator(".cam-thumb[data-camera='vehicle/cam_front']")
    thumb.click()
    session.wait_for(lambda: session.page.locator(".camera-split").count() == 1, "the split view")
    assert thumb.get_attribute("aria-pressed") == "true"
    toggles = session.page.locator(".camera-split .pip-toggle")
    assert [toggles.nth(i).get_attribute("aria-pressed") for i in range(toggles.count())] == [
        "true",
        "true",
    ]
    thumb.click()  # the same click closes it
    session.wait_for(lambda: session.page.locator(".camera-split").count() == 0, "closed")
    thumb.click()
    session.wait_for(lambda: session.page.locator(".camera-split").count() == 1, "split again")
    stored = _stored_view(session)
    assert stored.get("splitCamera") == "vehicle/cam_front"
    assert stored.get("stripOpen") is True
    session.page.keyboard.press("Escape")
    session.wait_for(lambda: session.page.locator(".camera-split").count() == 0, "closed by Esc")
    stored = _stored_view(session)
    assert stored.get("splitCamera") is None
    assert session.page.locator(".pip").count() == 0  # the floating panel is gone


REPEAT = int(os.environ.get("COPERCEIVE_E2E_REPEAT", "1"))  # the gates job can run a test N times


@pytest.mark.parametrize("_repeat", range(REPEAT))
def test_choosing_a_scene_while_playing_pauses_at_once(session: _Session, _repeat: int) -> None:
    """The old scene stops prefetching when another scene is chosen, so the chosen
    scene's summary is not queued behind it. Every wait is on a state or an event, not
    on wall time: playing with a sample advanced, the option present, paused, the new
    status, network idle. Adarsh's bound: at most 10 old-scene requests after the choice."""
    page = session.page
    session.choose_scene(SCENE_BARE)
    page.locator("button[aria-label=Play]").click()
    session.wait_for(lambda: page.locator("button[aria-label=Pause]").count() == 1, "playing")
    session.wait_for(lambda: session.slider("aria-valuenow") >= 1, "a sample advanced")
    page.locator("#scene-id").click()
    option = page.locator("[role=option]", has_text=SCENE_FULL)
    session.wait_for(lambda: option.count() == 1, "the scene option")
    # The click itself is the choice. Requests that were already in flight when it was
    # dispatched are not "after the choice": the old scene's prefetch may legitimately have
    # queued one window of them just before the event. Measure from after the click
    # returns, not from a snapshot taken before it; the waits below are the #49 rule
    # (state or event, never wall time).
    option.click()
    after_click = len(session.requests)
    session.wait_for(lambda: page.locator("button[aria-label=Play]").count() == 1, "paused")
    session.wait_status(SCENE_FULL)
    old_scene_after_click = [
        url for url in session.requests[after_click:] if f"/api/scenes/{SCENE_BARE}/" in url
    ]
    assert len(old_scene_after_click) <= 10, old_scene_after_click
    page.wait_for_load_state("networkidle")
    settled = [url for url in session.requests[after_click:] if f"/api/scenes/{SCENE_BARE}/" in url]
    assert settled == old_scene_after_click, settled[len(old_scene_after_click) :]
    assert session.slider("aria-valuenow") == 0


@pytest.fixture(scope="module")
def browser_without_webgl(playwright: Playwright) -> Iterator[Browser]:
    """Chromium with WebGL switched off, the situation of a blocked GPU or a remote desktop.
    Launched from the module's Playwright, next to the ordinary browser."""
    launched = _launch(playwright, ["--disable-webgl", "--disable-webgl2", "--disable-3d-apis"])
    yield launched
    launched.close()


def test_a_browser_without_webgl_gets_a_message_not_a_blank_page(
    browser_without_webgl: Browser, viewer_url: str
) -> None:
    page = browser_without_webgl.new_page(viewport={"width": 1400, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{viewer_url}/#/viewer", wait_until="networkidle")
    notice = page.locator(".webgl-missing")
    deadline = time.monotonic() + 10
    while notice.count() == 0 and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert notice.count() == 1, "no WebGL notice"
    assert "needs WebGL" in notice.inner_text()
    # The rest of the page is still there and usable.
    assert page.locator("#scene-id").inner_text() == SCENE_FULL
    assert page.locator("button[aria-label=Play]").count() == 1
    assert "sample 1 / 7" in page.locator(".clock").inner_text()
    assert errors == [], errors
    page.close()


def test_agent_display_names_label_the_panels_and_raw_ids_stay_in_tooltips(
    session: _Session,
) -> None:
    """A scene with display names shows them in the agent list, the telemetry cards, the
    camera strip and the split view, with the raw agent id as the hover text; a scene
    without names shows the raw id."""
    page = session.page
    expected = [(agent.display_name, agent.agent_id) for agent in _full_scene().agents]
    assert [name for name, _ in expected] == ["Vehicle 1", "Drone 1", "RSU 1"]
    swatches = page.locator(".agent-list .swatch")
    listed = [
        (
            swatches.nth(i).locator("span").first.text_content(),
            swatches.nth(i).get_attribute("title"),
        )
        for i in range(swatches.count())
    ]
    assert listed == expected
    cards = page.locator(".tele header strong")
    shown = [
        (cards.nth(i).text_content(), cards.nth(i).get_attribute("title"))
        for i in range(cards.count())
    ]
    assert shown == expected
    if not _strip_open(session):
        page.keyboard.press("C")
    group = page.locator(".cam-group-name[title='vehicle']")
    session.wait_for(lambda: group.count() == 1, "the vehicle's camera group")
    assert group.text_content() == "Vehicle 1"
    thumb = page.locator(".cam-thumb[data-camera='vehicle/cam_front']")
    assert thumb.get_attribute("title") == "Vehicle 1 cam_front (vehicle)"
    thumb.click()
    session.wait_for(lambda: page.locator(".camera-split").count() == 1, "the split view")
    bar = page.locator(".camera-split-bar strong span")
    assert bar.text_content() == "Vehicle 1" and bar.get_attribute("title") == "vehicle"
    assert page.locator(".camera-panel figcaption").text_content() == "cam_front · Vehicle 1"
    page.keyboard.press("Escape")
    session.choose_scene(SCENE_BARE)
    assert page.locator(".tele header strong").all_text_contents() == ["vehicle"]
    assert page.locator(".agent-list .swatch span").first.text_content() == "vehicle"


def test_legend_names_the_agent_types_of_the_scene(session: _Session) -> None:
    """The 3D legend lists the types present: Vehicle, Drone, RSU and Lane for the
    cooperative scene; Vehicle and Lane for the single-vehicle scene."""

    # The status chips of an evaluated run follow "Lane"; the agent types come first.
    def through_lane() -> list[str]:
        texts = [text.strip() for text in session.page.locator(".legend span").all_text_contents()]
        return texts[: texts.index("Lane") + 1]

    assert through_lane() == ["Vehicle", "Drone", "RSU", "Lane"]
    session.choose_scene(SCENE_BARE)
    assert through_lane() == ["Vehicle", "Lane"]


def test_agent_visibility_toggles_remove_objects_from_scene_graph(session: _Session) -> None:
    """Toggling agent visibility removes points, frustums, and bounding boxes directly
    from the Three.js scene graph and updates object counts immediately."""
    session.open()
    session.wait_status(f"{SCENE_FULL} at", timeout_s=10.0)

    # Advance to sample 2 (100000 µs) where vehicle has points and both agents have boxes
    session.page.keyboard.press("ArrowRight")
    session.page.keyboard.press("ArrowRight")
    session.wait_status("100000 µs", timeout_s=10.0)

    # Initial counts from Three.js scene graph
    initial = session.page.evaluate("() => window.__COPERCEIVE_SCENE_COUNTS__()")
    assert initial is not None
    assert initial["frustums"] == 2  # cam_front and cam_rear on vehicle
    assert initial["boxes"] == 6  # 3 vehicle boxes + 3 drone boxes
    assert initial["points"] == 1  # 1 point in vehicle lidar sweep
    assert initial["agents"] == 2  # vehicle and drone have poses at 100000 µs

    # Also verify direct stage reference if available in development mode
    has_stage = session.page.evaluate("() => typeof window.__COPERCEIVE_STAGE__ !== 'undefined'")
    if has_stage:
        stage_frustums = session.page.evaluate(
            "() => window.__COPERCEIVE_STAGE__.frustums.children.length"
        )
        assert stage_frustums == initial["frustums"]

    # 1. Hide Vehicle: vehicle's points and frustums must immediately disappear from scene graph
    vehicle_item = (
        session.page.locator(".agent-list li").filter(has_text="Vehicle 1").locator("button.swatch")
    )
    vehicle_item.click()

    after_vehicle = session.page.evaluate("() => window.__COPERCEIVE_SCENE_COUNTS__()")
    assert after_vehicle["points"] == 0, "LiDAR points must be removed when vehicle is hidden"
    assert after_vehicle["frustums"] == 0, "Camera frustums must be removed when vehicle is hidden"
    assert after_vehicle["boxes"] == 3, "Only drone's 3 boxes should remain"
    assert after_vehicle["agents"] == 1  # Only Drone 1 remains visible

    # 2. Hide Drone: drone's annotations/boxes and axis helper removed
    uav_item = (
        session.page.locator(".agent-list li").filter(has_text="Drone 1").locator("button.swatch")
    )
    uav_item.click()

    after_all = session.page.evaluate("() => window.__COPERCEIVE_SCENE_COUNTS__()")
    assert after_all["points"] == 0
    assert after_all["frustums"] == 0
    assert after_all["boxes"] == 0
    assert after_all["agents"] == 0

    # 3. Unhide Vehicle: vehicle's points, frustums, and boxes restored immediately
    vehicle_item.click()
    restored_veh = session.page.evaluate("() => window.__COPERCEIVE_SCENE_COUNTS__()")
    assert restored_veh["points"] == 1
    assert restored_veh["frustums"] == 2
    assert restored_veh["boxes"] == 3
    assert restored_veh["agents"] == 1

    # 4. Unhide Drone: all 6 boxes and 2 agents restored
    uav_item.click()
    restored_all = session.page.evaluate("() => window.__COPERCEIVE_SCENE_COUNTS__()")
    assert restored_all["points"] == 1
    assert restored_all["frustums"] == 2
    assert restored_all["boxes"] == 6
    assert restored_all["agents"] == 2


def test_playback_updates_status_line_every_frame(session: _Session) -> None:
    """The status line updates its sample time on every frame while playback is running."""
    session.open()
    session.wait_status(f"{SCENE_FULL} at 0 µs", timeout_s=10.0)

    # Ensure loop is active so playback does not immediately end after 400ms on short clip
    if session.page.locator("button[aria-label=Loop][aria-pressed=true]").count() == 0:
        session.page.keyboard.press("l")

    play = session.page.locator("button[aria-label=Play]")
    play.click()

    # Wait for the status line to advance beyond 0 µs while playback is running.
    # Capture the status line that satisfied the condition so a short 400ms looping
    # fixture does not wrap around to 0 µs before the post-wait assertion executes.
    advanced_status: str | None = None

    def _status_advanced() -> bool:
        nonlocal advanced_status
        if session.page.locator("button[aria-label=Pause]").count() != 1:
            return False
        st = session.status()
        if "at 0 µs" not in st and "µs." in st:
            advanced_status = st
            return True
        return False

    session.wait_for(
        _status_advanced,
        "status line to advance beyond 0 µs during active playback",
        timeout_s=5.0,
    )

    assert advanced_status is not None
    assert f"{SCENE_FULL} at " in advanced_status
    assert "at 0 µs" not in advanced_status

    # Pause playback so background loop does not consume CPU during subsequent tests
    pause = session.page.locator("button[aria-label=Pause]")
    if pause.count() == 1:
        pause.click()


def test_connect_screen_accessibility_and_navigation(session: _Session) -> None:
    """The Connect screen has no animated canvas backdrop and passes a11y across tabs."""
    session.page.goto(f"{session.base}/#/connect", wait_until="networkidle")
    assert session.page.locator("canvas").count() == 0, "Expected no canvas backdrop on Connect"

    # Accessibility audit with axe-core
    session.page.emulate_media(reduced_motion="no-preference")
    repo_root = Path(__file__).resolve().parents[1]
    axe_path = repo_root / "web" / "node_modules" / "axe-core" / "axe.min.js"
    if axe_path.exists():
        session.page.evaluate(axe_path.read_text(encoding="utf-8"))
        # Run axe on Connect / Library
        results = session.page.evaluate("""
            axe.run({
                runOnly: {
                    type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
                }
            })
        """)
        violations = results.get("violations", [])
        serious = [v for v in violations if v.get("impact") in ("serious", "critical")]
        assert len(serious) == 0, f"Library tab axe violations: {serious}"

        # Run axe on Get Data
        session.page.click("button:has-text('Get Data')")
        results_gd = session.page.evaluate("""
            axe.run({
                runOnly: {
                    type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
                }
            })
        """)
        violations_gd = results_gd.get("violations", [])
        serious_gd = [v for v in violations_gd if v.get("impact") in ("serious", "critical")]
        assert len(serious_gd) == 0, f"Get Data tab axe violations: {serious_gd}"

        # Run axe on Open Folder (Local Connect)
        session.page.click("button:has-text('Open Folder')")
        results_local = session.page.evaluate("""
            axe.run({
                runOnly: {
                    type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
                }
            })
        """)
        violations_local = results_local.get("violations", [])
        serious_local = [v for v in violations_local if v.get("impact") in ("serious", "critical")]
        assert len(serious_local) == 0, f"Open Folder tab axe violations: {serious_local}"


def test_panel_states_empty_loading_error(session: _Session, shots: Path) -> None:
    """Verify empty, loading, and error states adhere to title, 1 sentence, and 1 button."""
    # 1. Empty state: In Viewer's Selected Object (Inspector) panel
    session.page.goto(f"{session.base}/#/viewer", wait_until="networkidle")
    empty_sel = ".side-card[aria-label='Selected object'] [data-testid='panel-state-empty']"
    empty_inspector = session.page.locator(empty_sel)
    empty_inspector.wait_for(state="visible", timeout=10000)
    title = (empty_inspector.locator(".panel-state-title").text_content() or "").strip()
    desc = (empty_inspector.locator(".panel-state-description").text_content() or "").strip()
    btn_count = empty_inspector.locator(".panel-state-action").count()
    assert btn_count == 0, "No dead action on empty inspector"

    assert title == "No object selected", f"Unexpected empty state title: {title}"
    assert desc.endswith("."), f"Description must be a sentence ending with period: {desc}"
    sentences = [s for s in desc.split(".") if s.strip()]
    assert len(sentences) == 1, f"Expected exactly 1 sentence, got: {desc}"
    assert "pip" not in (title + desc).lower(), "No CLI jargon permitted"

    # 2. Loading state: Dispatch test state to ConnectScreen in local tab
    session.page.goto(f"{session.base}/#/connect", wait_until="networkidle")
    session.page.evaluate("""() => {
        window.dispatchEvent(new CustomEvent('coperceive-test-panel-state', {
            detail: { tab: 'local', busy: true }
        }));
    }""")
    loading_state = session.page.locator("[data-testid='panel-state-loading']")
    loading_state.wait_for(state="visible", timeout=5000)
    load_title = (loading_state.locator(".panel-state-title").text_content() or "").strip()
    load_desc = (loading_state.locator(".panel-state-description").text_content() or "").strip()
    load_action = (loading_state.locator(".panel-state-action").text_content() or "").strip()

    assert load_title == "Connecting to dataset", f"Unexpected loading title: {load_title}"
    assert load_desc.endswith("."), f"Loading description must be a sentence: {load_desc}"
    load_sentences = [s for s in load_desc.split(".") if s.strip()]
    assert len(load_sentences) == 1, f"Expected 1 sentence: {load_desc}"
    assert load_action == "Cancel", f"Unexpected loading action button: {load_action}"
    jargon_free = (load_title + load_desc + load_action).lower()
    assert "coperceive" not in jargon_free, "No CLI jargon permitted"

    # 3. Error state: Dispatch error state to ConnectScreen
    err_detail = "Unable to start the local service with the chosen folder."
    session.page.evaluate(f"""() => {{
        window.dispatchEvent(new CustomEvent('coperceive-test-panel-state', {{
            detail: {{ busy: false, error: '{err_detail}' }}
        }}));
    }}""")
    error_state = session.page.locator("[data-testid='panel-state-error']")
    error_state.wait_for(state="visible", timeout=5000)
    err_title = (error_state.locator(".panel-state-title").text_content() or "").strip()
    err_desc = (error_state.locator(".panel-state-description").text_content() or "").strip()
    err_action = (error_state.locator(".panel-state-action").text_content() or "").strip()

    assert err_title == "Connection failed", f"Unexpected error title: {err_title}"
    assert err_desc.endswith("."), f"Error description must be a sentence: {err_desc}"
    err_sentences = [s for s in err_desc.split(".") if s.strip()]
    assert len(err_sentences) == 1, f"Expected 1 sentence: {err_desc}"
    assert err_action == "Choose Another Folder", f"Unexpected error action button: {err_action}"
    assert "bash" not in (err_title + err_desc + err_action).lower(), "No CLI jargon permitted"

    session.page.screenshot(path=str(shots / "panel-states.png"))


def test_system_theme_follows_prefers_color_scheme(session: _Session) -> None:
    """Default theme follows system appearance (prefers-color-scheme)."""
    # 1. Dark scheme
    session.page.emulate_media(color_scheme="dark")
    session.page.reload(wait_until="networkidle")
    info = session.page.evaluate("""() => ({
        matches: window.matchMedia('(prefers-color-scheme: dark)').matches,
        theme: document.documentElement.dataset.theme,
        override: localStorage.getItem('coperceive-theme-override'),
        view: localStorage.getItem('coperceive-view')
    })""")
    assert info["theme"] == "dark", f"Expected dark theme under dark scheme, got: {info}"

    # 2. Light scheme
    session.page.emulate_media(color_scheme="light")
    session.page.reload(wait_until="networkidle")
    theme_light = session.page.evaluate("() => document.documentElement.dataset.theme")
    assert theme_light == "light", f"Expected light theme under light scheme, got: {theme_light}"

    # 3. User override locks theme
    session.page.click("button[aria-label='Use dark theme']")
    theme_override = session.page.evaluate("() => document.documentElement.dataset.theme")
    assert theme_override == "dark", f"Expected dark theme after override, got: {theme_override}"


def test_keyboard_navigation_flow(session: _Session) -> None:
    """Keyboard-only path: Connect -> Library -> open scene -> play with visible focus."""
    session.page.goto(f"{session.base}/#/connect", wait_until="networkidle")

    # Focus and activate Library tab via keyboard
    library_tab = session.page.locator("button[role='tab']:has-text('Library')")
    library_tab.focus()
    session.page.keyboard.press("Enter")

    # Tab to the open button for a scene
    open_btn = session.page.locator("button[aria-label^='Open scene']").first
    open_btn.wait_for(state="visible", timeout=10000)
    open_btn.focus()
    session.page.keyboard.press("Enter")

    # Wait for viewer to load or navigate
    session.page.wait_for_selector(".timeline-tools", timeout=10000)
    play_btn = session.page.locator("button[aria-label='Play'], button[aria-label='Pause']").first
    play_btn.wait_for(state="visible", timeout=10000)

    # Press Space or click Play button via keyboard
    session.page.keyboard.press("Space")
    time.sleep(0.3)
    play_state = session.page.evaluate("""() => {
        return window.__COPERCEIVE_PLAYBACK_STATE__ ? window.__COPERCEIVE_PLAYBACK_STATE__() : null;
    }""")
    assert play_state is not None, "Playback state must be available"


def test_export_dialog_native_flow(session: _Session, tmp_path: Path) -> None:
    """Export dialog opens, saves still via native save dialog in desktop mode,
    and surfaces 'Show in Finder'.
    """
    dest_file = tmp_path / "saved_still.png"
    session.page.add_init_script(
        f"""
        window.__COPERCEIVE_DESKTOP__ = true;
        window.__TAURI_INTERNALS__ = {{
            invocations: [],
            pickedPath: {json.dumps(str(dest_file))},
            transformCallback: function(cb) {{ return 0; }},
            invoke: async function(cmd, args) {{
                this.invocations.push({{ cmd, args }});
                if (cmd === 'pick_save_path') {{
                    return this.pickedPath;
                }}
                if (cmd === 'save_exported_file') {{
                    return this.pickedPath;
                }}
                if (cmd === 'reveal_in_finder') {{
                    return null;
                }}
                return null;
            }}
        }};
        """
    )
    session.page.reload(wait_until="networkidle")
    session.wait_status(SCENE_FULL)
    session.page.wait_for_selector(".top-actions button:has-text('Export')", timeout=5000)

    # Open Export dialog
    session.page.click(".top-actions button:has-text('Export')")
    session.page.wait_for_selector("div[role='dialog'] h2:has-text('Export')", timeout=5000)

    # Click 'Save still'
    session.page.click("div[role='dialog'] button:has-text('Save still')")

    # Verify status updates and 'Show in Finder' button appears
    session.page.wait_for_selector("button:has-text('Show in Finder')", timeout=10000)
    note_locator = session.page.locator("div[role='dialog'] p.muted:not(#export-detail)")
    note_text = note_locator.text_content() or ""
    assert "saved_still.png" in note_text
    assert "Saved to" in note_text

    # Click 'Show in Finder' and verify IPC was invoked
    session.page.click("button:has-text('Show in Finder')")
    session.page.wait_for_function(
        "() => window.__TAURI_INTERNALS__.invocations.some(i => i.cmd === 'reveal_in_finder')"
    )
    invocations = session.page.evaluate("window.__TAURI_INTERNALS__.invocations")
    cmds = [inv["cmd"] for inv in invocations]
    assert "pick_save_path" in cmds
    assert "save_exported_file" in cmds
    assert "reveal_in_finder" in cmds


def test_identifier_inputs_have_autocapitalize_and_autocorrect_disabled(
    session: _Session,
) -> None:
    """Identifier, host, user, path and partition inputs disable auto-capitalisation."""
    session.page.goto(f"{session.base}/#/connect", wait_until="networkidle")

    # 1. Connect screen storage path input (under Open Folder tab)
    session.page.click("button:has-text('Open Folder')")
    data_root = session.page.locator("input[aria-label='Selected dataset folder']")
    assert data_root.count() == 1
    assert data_root.get_attribute("autocapitalize") == "off"
    assert data_root.get_attribute("autocorrect") == "off"
    assert data_root.get_attribute("spellcheck") == "false"
    assert data_root.get_attribute("autocomplete") == "off"

    # 2. HPC Workspace Add server inputs
    session.page.click("button:has-text('HPC Workspace')")
    add_btn = session.page.locator("button:has-text('Add server')").first
    add_btn.click()

    expected_ids = [
        "hpc-name",
        "hpc-host",
        "hpc-user",
        "hpc-data-root",
        "hpc-scratch",
        "hpc-results-root",
        "hpc-code-roots",
        "hpc-partition",
        "hpc-time",
        "hpc-cpus",
        "hpc-mem",
        "hpc-interpreter",
        "hpc-wheelhouse",
    ]
    for field_id in expected_ids:
        inp = session.page.locator(f"#{field_id}")
        assert inp.count() == 1, f"Missing #{field_id}"
        assert inp.get_attribute("autocapitalize") == "off", f"{field_id} autocapitalize"
        assert inp.get_attribute("autocorrect") == "off", f"{field_id} autocorrect"
        assert inp.get_attribute("spellcheck") == "false", f"{field_id} spellcheck"
        assert inp.get_attribute("autocomplete") == "off", f"{field_id} autocomplete"

    # Conditional environment inputs
    session.page.select_option("#hpc-env-kind", "modules")
    modules_inp = session.page.locator("#hpc-modules")
    assert modules_inp.count() == 1
    assert modules_inp.get_attribute("autocapitalize") == "off"
    assert modules_inp.get_attribute("autocorrect") == "off"
    assert modules_inp.get_attribute("spellcheck") == "false"
    assert modules_inp.get_attribute("autocomplete") == "off"

    session.page.select_option("#hpc-env-kind", "container")
    img_inp = session.page.locator("#hpc-image")
    assert img_inp.count() == 1
    assert img_inp.get_attribute("autocapitalize") == "off"
    assert img_inp.get_attribute("autocorrect") == "off"
    assert img_inp.get_attribute("spellcheck") == "false"
    assert img_inp.get_attribute("autocomplete") == "off"

    # 3. Typing lowercase values retains them without transformation
    user_input = session.page.locator("#hpc-user")
    user_input.fill("")
    user_input.press_sequentially("nanjaiyalatha")
    assert user_input.input_value() == "nanjaiyalatha"

    session.page.select_option("#hpc-scheduler", "slurm")
    partition_input = session.page.locator("#hpc-partition")
    partition_input.fill("")
    partition_input.press_sequentially("ps")
    assert partition_input.input_value() == "ps"


def test_updater_quiet_on_404_launch_and_shows_uptodate_on_manual_check(
    browser: Browser,
    viewer_url: str,
) -> None:
    """A 404 on latest.json is quiet on launch (no error toast, no banner), but
    shows 'You're up to date' on a manual check."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), _H)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.add_init_script(
        f"""
        window.__COPERCEIVE_DESKTOP__ = true;
        window.__COPERCEIVE_TEST_ENV__ = true;
        window.__COPERCEIVE_UPDATE_URL__ = "http://127.0.0.1:{port}/latest.json";
        """
    )
    session = _Session(page, viewer_url)

    try:
        session.open()
        session.wait_status(SCENE_FULL)

        # On launch, wait 1.5s for initial background check to finish
        time.sleep(1.5)
        # Verify no error toast and no update banner
        body_text = session.page.locator("body").inner_text() or ""
        assert "Could not check for updates" not in body_text
        assert "is ready" not in body_text

        # Trigger manual check
        session.page.evaluate("() => window.__COPERCEIVE_CHECK_UPDATES__(true)")
        session.page.wait_for_selector(".update-status", timeout=5000)
        status_text = session.page.locator(".update-status").inner_text() or ""
        assert "You're up to date" in status_text
    finally:
        context.close()
        server.shutdown()


def test_updater_bad_signature_is_rejected_with_error(
    browser: Browser,
    viewer_url: str,
) -> None:
    """A manifest with a bad signature is rejected with an error on install and
    never marked installed."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    body = json.dumps(
        {
            "version": "0.2.0",
            "notes": "Bad signature test",
            "platforms": {
                "darwin-aarch64": {
                    "signature": "bad-signature",
                    "url": "http://127.0.0.1:9/Coperceive.app.tar.gz",
                }
            },
        }
    ).encode("utf-8")

    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), _H)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.add_init_script(
        f"""
        window.__COPERCEIVE_DESKTOP__ = true;
        window.__COPERCEIVE_TEST_ENV__ = true;
        window.__COPERCEIVE_UPDATE_URL__ = "http://127.0.0.1:{port}/latest.json";
        """
    )
    session = _Session(page, viewer_url)

    try:
        session.open()
        session.wait_status(SCENE_FULL)

        # Update banner appears
        session.page.wait_for_selector("div[role='banner']", timeout=10000)
        banner_text = session.page.locator("div[role='banner']").inner_text() or ""
        assert "coperceive 0.2.0 is ready" in banner_text

        # Click Restart now
        session.page.click("button:has-text('Restart now')")

        # Expect error alert to appear with Retry button
        session.page.wait_for_selector("div[role='alert']", timeout=5000)
        alert_text = session.page.locator("div[role='alert']").inner_text() or ""
        assert "Retry" in alert_text

        # Assert restart flag was NOT recorded
        restarted = session.page.evaluate("() => Boolean(window.__COPERCEIVE_UPDATE_RESTART__)")
        assert not restarted, "Update with bad signature must not be installed"
    finally:
        context.close()
        server.shutdown()
