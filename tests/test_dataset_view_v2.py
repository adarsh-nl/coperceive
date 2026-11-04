# SPDX-License-Identifier: Apache-2.0
"""Dataset view v2 in the browser build, with synthetic fixtures.

The grouping setting regroups the table live and is read back after a reload
(through the controller's /state, served here by a Playwright route), and each
row action calls its backend command (the app's own Tauri client, with
`invoke` stubbed to record the calls).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import cast

import pytest
import uvicorn
from coperceive.app.server import create_app
from playwright.sync_api import Browser, Page, Route, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from tests import test_design_system_visual as visual

RICH_FIXTURE_INIT_SCRIPT = visual.RICH_FIXTURE_INIT_SCRIPT
SEED_FIXTURES_EVAL = visual.SEED_FIXTURES_EVAL
CTRL_PORT = 59231

# Synthetic scenes: made-up 2024 recording times, one undated name, every state.
DATASET_SEED = """() => {
  const ids = [
    "2024_03_05_09_00_00", "2024_03_05_11_17_29", "2024_03_05_14_34_58",
    "2024_03_05_16_51_27", "2024_03_07_08_16_52", "2024_03_07_10_33_21",
    "scene_extra_01",
  ];
  const states = {
    "2024_03_05_09_00_00": { state: "ready", images: "previews" },
    "2024_03_05_11_17_29": {
      state: "failed", reason: "ValueError: missing LiDAR sweep\\nTraceback ...",
    },
    "2024_03_05_14_34_58": { state: "stale", reason: "raw dataset fingerprint changed" },
    "2024_03_05_16_51_27": { state: "preparing", progress_percent: 40, job_id: "900002" },
    "2024_03_07_08_16_52": { state: "not_prepared" },
    "2024_03_07_10_33_21": { state: "not_prepared" },
    "scene_extra_01": { state: "ready", images: "originals" },
  };
  const scenes = ids.map((id, n) => ({
    scene_id: id, sample_count: 100 + n, duration_s: 10 + n, agent_count: 2,
    sensors: { sensors: 10, camera_samples: 800, lidar_samples: 200 },
  }));
  const statuses = ids.map((id) => ({
    scene_id: id, reason: null, job_id: null, prepared_unix: null, scene_dir: null,
    progress_percent: 0, images: null, ...states[id],
  }));
  const profile = (window.__SEEDED_HPC_PROFILES__ || [])[0];
  const dataset = {
    schema: "coperceive-cluster-detect-1", path: "/data/datasets/v2xset/train",
    adapter: "v2xset", raw_root: "/data/datasets/v2xset", split: "train",
    params: { split: "train" }, licence: null, scene_count: ids.length, status: "ok",
    reason: null,
  };
  const indexDoc = {
    schema: "coperceive-cluster-index-1", adapter: "v2xset",
    raw_root: "/data/datasets/v2xset", split: "train", params: {}, licence: null,
    fingerprint: { algorithm: "x", digest: "d", entries: 1, truncated: false },
    cache: { relative_path: "coperceive-cache/v2xset/d/train" },
    scene_count: ids.length, scenes, errors: [], truncated: false, elapsed_ms: 1,
  };
  // A prepare job this install submitted still covers one not-prepared scene: Queued.
  const submittedJobs = [{
    jobId: "900003", planId: "p", jobName: "j", scenes: ["2024_03_07_10_33_21"],
    state: "pending", reason: null,
  }];
  window.dispatchEvent(new CustomEvent("coperceive-open-dataset", { detail: {
    profile, dataset, indexDoc, statuses, submittedJobs,
    cacheRoot: "/home/alice/results/coperceive-cache/v2xset/d/train",
  } }));
}"""

# Record every Tauri command from here on; answer the two the row actions use.
RECORD_INVOKES = """() => {
  window.__INVOKES__ = [];
  window.__TAURI_INTERNALS__ = {
    transformCallback: () => 0,
    invoke: async (cmd, args) => {
      window.__INVOKES__.push({ cmd, args });
      if (cmd === "dataset_open") {
        return {
          origin: "http://127.0.0.1:1", token: "t", server_version: "0", data_root: "d",
          target_type: "remote",
        };
      }
      if (cmd === "dataset_prepare") {
        return {
          plan_id: "p", job_name: "j", partition: "cpu", walltime: "01:00:00",
          cpus_per_task: 1, array_tasks: 1, throttle: 1, estimated_seconds: 60,
          estimate_note: "", gres: null, cache_root: "/c", estimated_cache_bytes: 1,
          estimated_preview_bytes: 1, images: "originals", preview_hash: "h",
          script: "#!/bin/bash\\n", scenes: args.scenes,
        };
      }
      return null;
    },
  };
}"""


@pytest.fixture(scope="module")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = visual._bundle_dir()  # pyright: ignore[reportPrivateUsage]
    if bundle is None:
        visual._unavailable("no web bundle built in coperceive/app/static")  # pyright: ignore[reportPrivateUsage]
    app = create_app(root=tmp_path_factory.mktemp("dv_root"), token=None, static=bundle)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, name="dv-server", daemon=True)
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
def visual_browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=visual._CHROMIUM_ARGS,  # pyright: ignore[reportPrivateUsage]
            )
        except PlaywrightError as exc:
            visual._unavailable(f"chromium cannot start: {exc}")  # pyright: ignore[reportPrivateUsage]
        yield browser
        browser.close()


def _open(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/#/connect", wait_until="networkidle")
    page.wait_for_selector(".c-sidebar", timeout=15000)
    page.evaluate(SEED_FIXTURES_EVAL)
    page.locator(".c-sidebar button", has_text="Servers").first.click()
    page.wait_for_timeout(300)
    page.evaluate(DATASET_SEED)
    page.wait_for_selector("section[aria-label='Dataset view'] [role=grid]", timeout=5000)


def _group_labels(page: Page) -> list[str]:
    return cast(
        list[str],
        page.eval_on_selector_all(
            "section[aria-label='Dataset view'] [role=rowgroup]",
            "(groups) => groups.map((group) => group.getAttribute('aria-label'))",
        ),
    )


def _row_order(page: Page) -> list[str]:
    return cast(
        list[str],
        page.eval_on_selector_all(
            "section[aria-label='Dataset view'] [role=row][data-scene-id]",
            "(rows) => rows.map((row) => row.dataset.sceneId)",
        ),
    )


def _wait_saved(page: Page, saved: dict[str, object], value: str) -> None:
    """The POST to /state is sent without waiting; give it a moment to arrive."""
    for _ in range(40):
        if saved.get("dataset_grouping") == value:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"dataset_grouping {value!r} never reached /state: {saved}")


def test_group_setting_regroups_live_and_survives_a_reload(
    visual_browser: Browser, app_url: str
) -> None:
    saved: dict[str, object] = {}

    def state_route(route: Route) -> None:
        cors = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        }
        request = route.request
        if request.method == "POST":
            saved.update(json.loads(request.post_data or "{}"))
        route.fulfill(
            status=200, headers=cors, content_type="application/json", body=json.dumps(saved)
        )

    context = visual_browser.new_context(viewport={"width": 1600, "height": 1000})
    context.route(f"http://127.0.0.1:{CTRL_PORT}/state", state_route)
    context.add_init_script(RICH_FIXTURE_INIT_SCRIPT)
    context.add_init_script(
        f"window.__COPERCEIVE_CTRL_PORT__ = {CTRL_PORT};"
        " window.__COPERCEIVE_CTRL_SECRET__ = 'test';"
    )
    page = context.new_page()
    try:
        _open(page, app_url)
        # Default: one flat list by scene name, no group headers.
        assert page.locator("select[aria-label='Group scenes']").input_value() == "none"
        assert _group_labels(page) == ["Scenes"]
        assert _row_order(page)[-1] == "scene_extra_01"

        page.select_option("select[aria-label='Group scenes']", "state")
        page.wait_for_function(
            "() => document.querySelectorAll("
            "\"section[aria-label='Dataset view'] [role=rowgroup]\").length > 1"
        )
        assert _group_labels(page) == [
            "Failed",
            "Stale",
            "Preparing",
            "Queued",
            "Ready",
            "Not prepared",
        ]
        _wait_saved(page, saved, "state")

        page.select_option("select[aria-label='Group scenes']", "day")
        page.wait_for_timeout(200)
        assert _group_labels(page) == [
            "Recorded Tue 5 Mar 2024",
            "Recorded Thu 7 Mar 2024",
            "Recording day unknown",
        ]
        _wait_saved(page, saved, "day")

        # Read back from state.json (here: the routed /state) after a reload.
        page.reload(wait_until="networkidle")
        _open(page, app_url)
        page.wait_for_function(
            "() => document.querySelector(\"select[aria-label='Group scenes']\")?.value === 'day'"
        )
        assert _group_labels(page)[0] == "Recorded Tue 5 Mar 2024"
    finally:
        context.close()


def test_each_row_action_calls_its_backend_command(visual_browser: Browser, app_url: str) -> None:
    context = visual_browser.new_context(viewport={"width": 1600, "height": 1000})
    context.add_init_script(RICH_FIXTURE_INIT_SCRIPT)
    page = context.new_page()
    try:
        _open(page, app_url)
        page.evaluate(RECORD_INVOKES)

        def action(scene_id: str) -> str | None:
            button = page.locator(f"[data-scene-id='{scene_id}'] [role=gridcell] button")
            return button.inner_text() if button.count() else None

        assert action("2024_03_05_09_00_00") == "Open"
        assert action("2024_03_05_11_17_29") == "Retry"
        assert action("2024_03_05_14_34_58") == "Re-prepare"
        assert action("2024_03_07_08_16_52") == "Prepare"
        # No action while a prepare is running or queued.
        assert action("2024_03_05_16_51_27") is None
        assert action("2024_03_07_10_33_21") is None
        # Failed and stale rows say why, inline.
        assert (
            "ValueError: missing LiDAR sweep"
            in page.locator("[data-scene-id='2024_03_05_11_17_29']").inner_text()
        )
        assert "Traceback" not in page.locator("[data-scene-id='2024_03_05_11_17_29']").inner_text()

        page.locator("[data-scene-id='2024_03_05_09_00_00'] button", has_text="Open").click()
        page.wait_for_function("() => window.__INVOKES__.some((c) => c.cmd === 'dataset_open')")
        opened = page.evaluate(
            "() => window.__INVOKES__.find((c) => c.cmd === 'dataset_open').args"
        )
        assert opened["sceneId"] == "2024_03_05_09_00_00"

        for scene_id, label in [
            ("2024_03_05_11_17_29", "Retry"),
            ("2024_03_05_14_34_58", "Re-prepare"),
            ("2024_03_07_08_16_52", "Prepare"),
        ]:
            page.evaluate("() => { window.__INVOKES__ = []; }")
            page.locator(f"[data-scene-id='{scene_id}'] button", has_text=label).click()
            page.wait_for_function(
                "() => window.__INVOKES__.some((c) => c.cmd === 'dataset_prepare')"
            )
            prepared = page.evaluate(
                "() => window.__INVOKES__.find((c) => c.cmd === 'dataset_prepare').args"
            )
            assert prepared["scenes"] == [scene_id], (label, prepared)
            # The existing preview-and-confirm panel opens for that one scene; nothing is submitted.
            page.wait_for_selector(f"text=Prepare scene: {scene_id}", timeout=5000)
            assert not page.evaluate(
                "() => window.__INVOKES__.some((c) => c.cmd === 'dataset_prepare_submit')"
            )
            page.locator("button", has_text="Cancel").last.click()
    finally:
        context.close()


def test_keyboard_selects_opens_and_focuses_the_filter(
    visual_browser: Browser, app_url: str
) -> None:
    context = visual_browser.new_context(viewport={"width": 1600, "height": 1000})
    context.add_init_script(RICH_FIXTURE_INIT_SCRIPT)
    page = context.new_page()
    try:
        _open(page, app_url)
        page.evaluate(RECORD_INVOKES)
        grid = page.locator("section[aria-label='Dataset view'] [role=grid]")
        page.locator("[data-scene-id='2024_03_05_09_00_00']").click()
        grid.focus()
        page.keyboard.press("ArrowDown")
        assert page.locator(
            "section[aria-label='Dataset view'] [role=row][aria-selected=true]"
        ).get_attribute("data-scene-id") == ("2024_03_05_11_17_29")
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__INVOKES__.some((c) => c.cmd === 'dataset_open')")

        page.locator("body").click(position={"x": 5, "y": 5})
        page.keyboard.press("/")
        assert (
            page.evaluate("() => document.activeElement?.getAttribute('aria-label')")
            == "Filter scene ID"
        )
    finally:
        context.close()


def test_recorded_time_only_for_dated_names(visual_browser: Browser, app_url: str) -> None:
    """The inspector says "Recorded …" for a dated scene name and nothing for another."""
    context = visual_browser.new_context(viewport={"width": 1600, "height": 1000})
    context.add_init_script(RICH_FIXTURE_INIT_SCRIPT)
    page = context.new_page()
    try:
        _open(page, app_url)
        inspector = page.locator("aside[aria-label='Scene inspector']")
        page.locator("[data-scene-id='2024_03_05_09_00_00']").click()
        assert "Recorded 5 Mar 2024, 09:00" in inspector.inner_text()
        page.locator("[data-scene-id='scene_extra_01']").click()
        assert "Recorded" not in inspector.inner_text()
    finally:
        context.close()
