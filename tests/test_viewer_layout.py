# SPDX-License-Identifier: Apache-2.0
"""Nothing in the viewer overlaps or clips, at the window sizes people use; and
the camera strip's two modes (Settings → Viewer → "Camera strip").

A populated scene streamed "over SSH" (the controller is faked with Playwright
routes) with real-length strings: a 19-character scene id, the 31st of 33 scenes,
and a synthetic server name as long as a real cluster's. For each window size,
with the camera viewer closed and open and with the side panels shown, narrow
and hidden (full screen), every pair of sibling elements in the top bar, the
stage chip row, the left-panel rows, the camera strip header and the timeline
row must not intersect, nothing may stick out of the top bar, and no text in the
left panel may start left of the panel's edge (the trackpad-scrolled clipping).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from coperceive.app.server import create_app
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import write_map
from coperceive.model import Modality, Sensor
from coperceive.se3 import SE3
from coperceive.store import write_scene
from playwright.sync_api import Browser, Page, Route, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from tests import test_viewer_browser as vb
from tests.test_hdmap import _straight  # pyright: ignore[reportPrivateUsage]

SCENE = "demo_crossing_00031"  # 19 characters, like 2021_08_24_12_19_30
SCENE_COUNT = 33
SERVER = "Example University HPC cluster"
CTRL_PORT = 47917
SIZES = ((1280, 800), (1440, 900), (1600, 1000), (2560, 1440))
MINIMUM = (900, 600)  # the desktop window's minimum size


def _scene_ids() -> list[str]:
    return [f"demo_crossing_{index:05d}" for index in range(1, SCENE_COUNT + 1)]


# Cameras added to the fixture so the filmstrip holds more than fits: two agents
# with cameras (no image files for the extra ones: their thumbnails say so).
EXTRA_CAMERAS = (
    ("vehicle", "cam_left"),
    ("vehicle", "cam_right"),
    ("uav", "cam_down"),
    ("uav", "cam_fwd"),
)
LAST_CAMERA = "uav/cam_fwd"


def _scene_with_cameras() -> Any:
    full = replace(vb._full_scene(), scene_id=SCENE)  # pyright: ignore[reportPrivateUsage]
    extra = tuple(
        Sensor(
            sensor, agent, Modality.CAMERA, SE3.identity(), 1280, 720, 1000.0, 1000.0, 640.0, 360.0
        )
        for agent, sensor in EXTRA_CAMERAS
    )
    return replace(full, sensors=(*full.sensors, *extra))


def _write_root(base: Path) -> tuple[Path, Path]:
    scenes = base / "scenes"
    full = _scene_with_cameras()
    write_scene(full, scenes / SCENE)
    for image in full.images:
        target = scenes / SCENE / image.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(vb._PNG)  # pyright: ignore[reportPrivateUsage]
    for scene_id in _scene_ids():
        if scene_id != SCENE:
            write_scene(vb._filler_scene(scene_id), scenes / scene_id)  # pyright: ignore[reportPrivateUsage]
    for child in scenes.iterdir():
        (child / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
    xodr = base / "Town03.xodr"
    _straight(xodr)
    maps = base / "maps"
    write_map(
        parse_opendrive(xodr, sample_step_m=5),
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source={"licence": "MIT", "note": "layout test fixture"},
    )
    return scenes, maps


@pytest.fixture(scope="module")
def layout_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = vb._bundle_dir()  # pyright: ignore[reportPrivateUsage]
    if bundle is None:
        vb._unavailable("no web bundle: set COPERCEIVE_WEB_DIST to a vite build directory")  # pyright: ignore[reportPrivateUsage]
    scenes, maps = _write_root(tmp_path_factory.mktemp("layout"))
    app = create_app(root=scenes, token=None, maps=maps, static=bundle)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, name="viewer-layout", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True, args=vb._CHROMIUM_ARGS)  # pyright: ignore[reportPrivateUsage]
        except PlaywrightError as exc:
            vb._unavailable(f"chromium cannot start: {exc}")  # pyright: ignore[reportPrivateUsage]
        yield launched
        launched.close()


def _controller(saved: dict[str, object]) -> Any:
    """The desktop controller's answers: a cluster dataset on a named server, a job."""

    def handle(route: Route) -> None:
        url = route.request.url
        body: object = {}
        if url.endswith("/dataset-scenes"):
            body = {
                "dataset": {
                    "scenes": [
                        {"scene_id": scene_id, "state": "ready"} for scene_id in _scene_ids()
                    ],
                    "label": "Example dataset train",
                    "server": SERVER,
                }
            }
        elif url.endswith("/viewer-session"):
            body = {
                "session": {
                    "job_id": 4242,
                    "status": "running",
                    "state": "RUNNING",
                    "time_left": "1:52:10",
                }
            }
        elif url.endswith("/state"):
            if route.request.method == "POST":
                saved.update(json.loads(route.request.post_data or "{}"))
            body = saved
        route.fulfill(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            },
            content_type="application/json",
            body=json.dumps(body),
        )

    return handle


def _open(
    browser: Browser,
    url: str,
    size: tuple[int, int],
    initial: dict[str, object],
    saved: dict[str, object] | None = None,
) -> Page:
    """The viewer as the desktop shows it; ``saved`` is the controller's state.json."""
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    context.route(
        f"http://127.0.0.1:{CTRL_PORT}/**",
        _controller(saved if saved is not None else dict(initial)),
    )
    page = context.new_page()
    page.add_init_script(
        "window.__COPERCEIVE_TEST_ENV__ = true; window.__COPERCEIVE_DESKTOP__ = true;"
        f"window.__COPERCEIVE_CTRL_PORT__ = {CTRL_PORT}; window.__COPERCEIVE_CTRL_SECRET__ = 's';"
        f"window.__COPERCEIVE_INITIAL_STATE__ = {json.dumps(initial)};"
    )
    page.goto(f"{url}/#/viewer", wait_until="networkidle")
    page.wait_for_function(
        "(scene) => (document.querySelector('.status-line') || {}).textContent?.includes(scene)",
        arg=SCENE,
        timeout=15000,
    )
    page.wait_for_function(
        "() => (document.querySelector('.crumb-count') || {}).textContent?.includes('31 of 33')",
        timeout=10000,
    )
    page.wait_for_selector(".conn-state", state="attached", timeout=10000)
    page.wait_for_function(
        f"() => document.querySelector('.conn-state').textContent.includes({json.dumps(SERVER)})",
        timeout=10000,
    )
    _to_sample(page)
    return page


def _to_sample(page: Page) -> None:
    """A sample with boxes, an image and both camera agents posed, so every chip,
    count and thumbnail has its real text."""
    page.locator(".viewer-canvas").click(position={"x": 5, "y": 5})
    page.keyboard.press("Home")
    for _ in range(12):
        if "at 200000 " in page.locator(".status-line").inner_text():
            break
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(150)
    page.wait_for_function(
        "() => document.querySelector('.status-line').textContent.includes('at 200000 ')",
        timeout=10000,
    )
    page.wait_for_timeout(400)


# Sibling groups that must not intersect, by area. Each selector lists elements;
# every pair across the whole group (not an element and its own ancestor) is checked.
GROUPS: dict[str, list[str]] = {
    "top bar": [
        ".topbar .brand",
        ".topbar .crumb > *",
        ".topbar .top-actions > *",
        ".topbar .top-end > details.settings",
    ],
    "stage chips": [
        ".viewport .stage-chips .status-pill",
        ".viewport .stage-chips .view-row",
        ".viewport .stage-chips .chip-end > *",
        ".viewport .stage-chips .empty-pill",
    ],
    "stage bottom": [".viewport .legend", ".viewport .scale-bar"],
    "camera strip head": [
        ".cam-strip-toggle",
        ".cam-strip .cam-group-head > *",
        ".cam-strip-toggle > *",
    ],
    "camera strip thumbs": [
        ".cam-strip-head",
        ".cam-strip .cam-group",
        ".cam-strip .cam-thumb-wrap",
    ],
    "timeline row": [".timeline-tools > *", ".timeline-tools .speed-seg > *"],
    "status line": [".status-line", ".cam-strip", ".timeline"],
}

OVERLAP_JS = (Path(__file__).with_name("viewer_layout_overlap.js")).read_text(encoding="utf-8")


def _problems(page: Page, case: str) -> list[str]:
    found = cast(list[str], page.evaluate(OVERLAP_JS, GROUPS))
    return [f"{case}: {item}" for item in found]


def _open_split(page: Page) -> None:
    opener = page.locator(".cam-strip button[aria-label^='Open ']").first
    opener.click()
    page.wait_for_selector(".camera-split", timeout=5000)
    page.wait_for_timeout(300)


def _check_size(browser: Browser, url: str, size: tuple[int, int]) -> list[str]:
    problems: list[str] = []
    label = f"{size[0]}x{size[1]}"
    page = _open(browser, url, size, {"last_scene_id": SCENE})
    try:
        title = page.locator(".conn-state").get_attribute("title") or ""
        if f"Streaming over SSH from {SERVER}" not in title:
            problems.append(
                f"{label}: the connection text's tooltip lacks the full text: {title!r}"
            )
        problems += _problems(page, f"{label} panels on, camera viewer closed")
        _open_split(page)
        problems += _problems(page, f"{label} panels on, camera viewer open")
        page.keyboard.press("f")
        page.wait_for_timeout(300)
        assert page.locator(".workspace.is-fullscreen").count() == 1
        problems += _problems(page, f"{label} panels off, camera viewer open")
        page.locator(".camera-split-close").click()
        page.wait_for_timeout(300)
        problems += _problems(page, f"{label} panels off, camera viewer closed")
    finally:
        page.context.close()
    # The narrowest side panels the resizers allow, and the widest.
    for name, left, right in (("narrow", 160, 200), ("wide", 520, 520)):
        page = _open(
            browser,
            url,
            size,
            {
                "last_scene_id": SCENE,
                "layout": {"left_px": left, "right_px": right, "strip_px": 150},
            },
        )
        try:
            problems += _problems(page, f"{label} {name} panels, camera viewer closed")
            _open_split(page)
            problems += _problems(page, f"{label} {name} panels, camera viewer open")
        finally:
            page.context.close()
    return problems


@pytest.mark.parametrize("size", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_nothing_overlaps_in_the_viewer(
    browser: Browser, layout_url: str, size: tuple[int, int]
) -> None:
    problems = _check_size(browser, layout_url, size)
    assert problems == [], "\n".join(problems)


def test_nothing_overlaps_at_the_minimum_window_size(browser: Browser, layout_url: str) -> None:
    problems = _check_size(browser, layout_url, MINIMUM)
    assert problems == [], "\n".join(problems)


# --- Camera strip: the filmstrip of every camera, or one agent at a time ------------


def _strip_mode(page: Page) -> str:
    return page.locator(".cam-strip").get_attribute("data-mode") or ""


def _thumb_agents(page: Page) -> set[str]:
    keys = cast(
        list[str],
        page.eval_on_selector_all(
            ".cam-strip .cam-thumb", "(els) => els.map((el) => el.dataset.camera)"
        ),
    )
    return {key.split("/", 1)[0] for key in keys}


def _wait_saved(page: Page, saved: dict[str, object], value: str) -> None:
    for _ in range(60):
        if saved.get("camera_strip") == value:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"camera_strip {value!r} never reached /state: {saved}")


def test_camera_strip_modes_render_switch_live_and_survive_a_reload(
    browser: Browser, layout_url: str
) -> None:
    saved: dict[str, object] = {"last_scene_id": SCENE}
    page = _open(browser, layout_url, (1600, 1000), {"last_scene_id": SCENE}, saved)
    try:
        # The default is the filmstrip: every agent's cameras, each thumbnail named
        # by its agent (colour swatch and name) and its camera, with its box count.
        assert _strip_mode(page) == "all"
        assert _thumb_agents(page) == {"vehicle", "uav"}
        first = page.locator(".cam-strip .cam-thumb-wrap").first
        assert first.locator(".cam-thumb-swatch").count() == 1
        assert first.locator(".cam-thumb-agent").inner_text() == "Vehicle 1"
        assert first.locator(".cam-thumb-sensor").inner_text() == "cam_front"
        assert page.locator(".cam-strip .cam-group-head").count() == 0

        # The strip's quick toggle writes the same setting and applies at once.
        page.locator(
            ".cam-strip [role=radiogroup][aria-label='Camera strip'] [role=radio]", has_text="Agent"
        ).click()
        page.wait_for_function(
            "() => document.querySelector('.cam-strip').dataset.mode === 'agent'"
        )
        _wait_saved(page, saved, "agent")
        assert _thumb_agents(page) == {"vehicle"}
        assert page.locator(".cam-strip .cam-switch").inner_text() == "Switch agent"
        page.locator(".cam-strip .cam-switch").click()
        page.wait_for_function(
            "() => [...document.querySelectorAll('.cam-strip .cam-thumb')]"
            ".every((el) => el.dataset.camera.startsWith('uav/'))"
        )

        # Read back from state.json after a reload.
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".cam-strip[data-mode='agent']", timeout=15000)
        _to_sample(page)

        # Settings → Viewer → Camera strip switches it back, live, and saves it.
        page.locator(".topbar details.settings > summary").click()
        group = page.locator(".settings-pop [role=radiogroup][aria-label='Camera strip']")
        group.wait_for(timeout=5000)
        assert group.locator("input[type=radio]:checked").get_attribute("value") == "agent"
        group.get_by_text("All cameras (scroll)").click()
        page.wait_for_selector(".cam-strip[data-mode='all']", timeout=5000)
        _wait_saved(page, saved, "all")
        assert _thumb_agents(page) == {"vehicle", "uav"}
    finally:
        page.context.close()


def test_trackpad_and_wheel_scroll_the_filmstrip_sideways_only(
    browser: Browser, layout_url: str
) -> None:
    initial: dict[str, object] = {
        "last_scene_id": SCENE,
        "layout": {"left_px": 248, "right_px": 268, "strip_px": 200},
    }
    page = _open(browser, layout_url, (1280, 800), initial)
    try:
        strip = page.locator(".cam-strip .cam-film")
        sizes = cast(
            dict[str, float],
            strip.evaluate(
                "(el) => ({sw: el.scrollWidth, cw: el.clientWidth,"
                " sh: el.scrollHeight, ch: el.clientHeight})"
            ),
        )
        assert sizes["sw"] > sizes["cw"] + 100, sizes  # more cameras than fit
        assert sizes["sh"] <= sizes["ch"] + 1, sizes  # never a vertical scroll
        # Thumbnails keep the camera's 16:9.
        box = page.locator(".cam-strip .cam-thumb-wrap").first.bounding_box()
        assert box and abs(box["width"] / box["height"] - 1280 / 720) < 0.05, box
        # Agents entering the scene add thumbnails without moving the strip.
        assert cast(float, strip.evaluate("(el) => el.scrollLeft")) == 0
        middle = strip.bounding_box()
        assert middle
        page.mouse.move(middle["x"] + middle["width"] / 2, middle["y"] + middle["height"] / 2)
        # A trackpad swipe: horizontal deltas.
        page.mouse.wheel(260, 0)
        page.wait_for_timeout(400)
        after_swipe = cast(float, strip.evaluate("(el) => el.scrollLeft"))
        assert after_swipe > 0, after_swipe
        # A mouse wheel (and Shift with it) steps a thumbnail along, never vertically.
        page.mouse.wheel(0, 200)
        page.wait_for_timeout(400)
        after_wheel = cast(float, strip.evaluate("(el) => el.scrollLeft"))
        assert after_wheel > after_swipe, (after_swipe, after_wheel)
        page.keyboard.down("Shift")
        page.mouse.wheel(0, -200)
        page.keyboard.up("Shift")
        page.wait_for_timeout(400)
        assert cast(float, strip.evaluate("(el) => el.scrollLeft")) < after_wheel
        assert cast(float, strip.evaluate("(el) => el.scrollTop")) == 0
    finally:
        page.context.close()


def test_the_selected_camera_is_highlighted_and_scrolled_into_view(
    browser: Browser, layout_url: str
) -> None:
    """Chosen elsewhere (the Camera view mode), the last camera is outlined and in view."""
    initial: dict[str, object] = {
        "last_scene_id": SCENE,
        "selected_camera": LAST_CAMERA,
        "layout": {"left_px": 248, "right_px": 268, "strip_px": 200},
    }
    page = _open(browser, layout_url, (1280, 800), initial)
    try:
        page.locator(".viewer-canvas").click(position={"x": 5, "y": 5})
        page.keyboard.press("5")
        selected = page.locator(f".cam-strip .cam-thumb[data-camera='{LAST_CAMERA}']")
        page.wait_for_function(
            "(key) => [...document.querySelectorAll('.cam-strip .cam-thumb')]"
            ".some((el) => el.dataset.camera === key"
            " && el.getAttribute('aria-current') === 'true')",
            arg=LAST_CAMERA,
            timeout=5000,
        )
        page.wait_for_timeout(300)
        strip_box = page.locator(".cam-strip .cam-film").bounding_box()
        thumb_box = selected.bounding_box()
        assert strip_box and thumb_box
        assert thumb_box["x"] >= strip_box["x"] - 1, (thumb_box, strip_box)
        assert thumb_box["x"] + thumb_box["width"] <= strip_box["x"] + strip_box["width"] + 1, (
            thumb_box,
            strip_box,
        )
    finally:
        page.context.close()


# --- Viewer styles: the same overlap rule, at the four sizes the style work names ------

STYLE_SIZES = ((1280, 800), (1440, 900), (1920, 1080), (2560, 1440))
STYLES = ("classic", "studio", "instrument", "cinema")

# Panels float over the stage in Instrument and Cinema. They may cover the
# canvas. They may not cover each other, the top bar, the timeline or the strip.
_CHROME_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity) > 0;
  };
  const sels = [
    '.topbar',
    '.workspace > .panel:not(.panel-right)',
    '.workspace > .panel-right',
    '.timeline',
    '.cam-strip',
  ];
  const nodes = sels.map((sel) => document.querySelector(sel)).filter((el) => el && visible(el));
  const out = [];
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    if (a.contains(b) || b.contains(a)) continue;
    const p = a.getBoundingClientRect(), q = b.getBoundingClientRect();
    const w = Math.min(p.right, q.right) - Math.max(p.left, q.left);
    const h = Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
    if (w > 0.5 && h > 0.5) {
      const pair = a.className + ' overlaps ' + b.className;
      out.push(pair + ' by ' + w.toFixed(0) + 'x' + h.toFixed(0));
    }
  }
  return out;
}
"""


def _style_case(
    browser: Browser, url: str, size: tuple[int, int], style: str, theme: str
) -> list[str]:
    label = f"{style} {theme} {size[0]}x{size[1]}"
    page = _open(
        browser,
        url,
        size,
        {
            "last_scene_id": SCENE,
            "viewer_style": style,
            "theme_override": theme,
            "cinema_drift": True,
        },
    )
    problems: list[str] = []
    try:
        page.wait_for_function(
            "(name) => document.querySelector('.viewer')?.dataset.viewerStyle === name",
            arg=style,
            timeout=5000,
        )
        if style == "classic":
            theme_ok = page.evaluate(
                "(theme) => document.documentElement.getAttribute('data-theme') === theme",
                theme,
            )
            if not theme_ok:
                problems.append(f"{label}: theme is not {theme}")
        views = page.locator(".view-row").inner_text()
        for word in ("Orbit", "Bird", "Chase", "Drone", "Camera"):
            if word not in views:
                problems.append(f"{label}: the {word} view is missing ({views!r})")
        if page.locator(".cam-strip").count() != 1:
            problems.append(f"{label}: the camera strip is missing on a scene that has cameras")
        problems += _problems(page, f"{label} camera viewer closed")
        chrome = cast(list[str], page.evaluate(_CHROME_JS))
        problems += [f"{label}: {item}" for item in chrome]
        if os.environ.get("COPERCEIVE_STYLE_SHOTS") == "1" and size == (1440, 900):
            shot = (
                Path(__file__).resolve().parents[1]
                / "docs"
                / "design"
                / "scene-viewer-v2"
                / "screenshots"
            )
            shot.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot / f"{style}-{theme}-1440x900.png"))
        _open_split(page)
        problems += _problems(page, f"{label} camera viewer open")
        chrome = cast(list[str], page.evaluate(_CHROME_JS))
        problems += [f"{label} split: {item}" for item in chrome]
    finally:
        page.context.close()
    return problems


@pytest.mark.parametrize("size", STYLE_SIZES, ids=[f"{w}x{h}" for w, h in STYLE_SIZES])
@pytest.mark.parametrize("style", STYLES)
def test_viewer_styles_do_not_overlap(
    browser: Browser, layout_url: str, size: tuple[int, int], style: str
) -> None:
    themes = ("light", "dark") if style == "classic" else ("dark",)
    problems: list[str] = []
    for theme in themes:
        problems += _style_case(browser, layout_url, size, style, theme)
    assert problems == [], "\n".join(problems)
