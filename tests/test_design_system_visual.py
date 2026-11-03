# SPDX-License-Identifier: Apache-2.0
"""Visual regression, font assertions, and traffic-lights layout tests for the v0.1.0 revamp."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
import uvicorn
from coperceive.app.server import create_app

REQUIRED = os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}


def _unavailable(reason: str) -> NoReturn:
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


try:
    from playwright.sync_api import Browser, Playwright, sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ImportError:  # pragma: no cover
    if REQUIRED:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)

_CHROMIUM_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]

VIEWPORTS = [
    {"width": 1280, "height": 800},
    {"width": 1600, "height": 1000},
]

THEMES = ["dark", "light"]

SCREENS = ["library", "get_data", "local", "remote", "dataset_view", "settings"]

RICH_FIXTURE_INIT_SCRIPT = """
(() => {
  window.__COPERCEIVE_TEST_ENV__ = true;
  window.__COPERCEIVE_DESKTOP__ = true;
})();
"""

SEED_FIXTURES_EVAL = """
() => {
  const scenes = [
    {
      scene_id: "2021_08_18_23_23_19",
      path: "/Users/researcher/coperceive-data/v2xset/2021_08_18_23_23_19",
      dataset_name: "V2XSet · train",
      town: "Town05",
      duration_s: 19.6,
      agent_count: 4,
      sample_count: 196,
      size_bytes: 342884352,
      has_images: "full",
      has_thumbnail: false,
      map_name: "Carla/Maps/Town05",
      has_map: true,
      is_complete: true,
      is_valid: true
    },
    {
      scene_id: "2021_08_16_22_26_54",
      path: "/Users/researcher/coperceive-data/opv2v/2021_08_16_22_26_54",
      dataset_name: "OPV2V · train",
      town: "Town03",
      duration_s: 16.0,
      agent_count: 3,
      sample_count: 160,
      size_bytes: 289406976,
      has_images: "full",
      has_thumbnail: false,
      map_name: "Carla/Maps/Town03",
      has_map: true,
      is_complete: true,
      is_valid: true
    },
    {
      scene_id: "griffin-drone-01",
      path: "/Users/researcher/coperceive-data/griffin/griffin-drone-01",
      dataset_name: "Griffin · aerial-ground",
      town: "Campus",
      duration_s: 24.2,
      agent_count: 5,
      sample_count: 242,
      size_bytes: 512753664,
      has_images: "full",
      has_thumbnail: false,
      map_name: null,
      has_map: true,
      is_complete: true,
      is_valid: true
    },
    {
      scene_id: "Town05-intersection",
      path: "/Users/researcher/coperceive-data/carla/Town05-intersection",
      dataset_name: "CARLA · synthetic",
      town: "Town05",
      duration_s: 12.8,
      agent_count: 2,
      sample_count: 128,
      size_bytes: 148897792,
      has_images: "preview",
      has_thumbnail: false,
      map_name: "Carla/Maps/Town05",
      has_map: false,
      is_complete: true,
      is_valid: true
    }
  ];

  const storage = {
    library_root: "~/coperceive-data",
    is_configured: true,
    used_bytes: 1503942784,
    free_bytes: 440401920000,
    total_bytes: 1000000000000,
    volume_name: "Macintosh HD",
    scene_count: 4
  };

  window.dispatchEvent(
    new CustomEvent("coperceive-test-library-seed", {
      detail: { scenes, storage }
    })
  );

  const catalog = [
    {
      id: "griffin",
      name: "Griffin (Aerial-Ground Cooperative)",
      description:
        "Multi-agent aerial + ground cooperative perception benchmark " +
        "with synchronized LiDAR and 4 cameras per agent.",
      modalities: ["LiDAR", "RGB", "3D Boxes"],
      agents: "1 UAV + 2–4 vehicles",
      licence: "CC BY-NC-SA 4.0",
      licence_url: "https://griffin-dataset.github.io",
      status: "ready",
      presets: [
        {
          id: "dev",
          name: "Dev sample (2 scenes)",
          description: "Quick local test split with 2 aerial-ground scenes",
          estimated_download_bytes: 412000000,
          estimated_disk_bytes: 680000000
        },
        {
          id: "full",
          name: "Full benchmark split (28 scenes)",
          description: "Complete train + validation cooperative split",
          estimated_download_bytes: 6840000000,
          estimated_disk_bytes: 11200000000
        }
      ]
    },
    {
      id: "v2xset",
      name: "V2XSet (V2X Cooperative Perception)",
      description:
        "55 V2X cooperative driving scenes with roadside infrastructure " +
        "units and connected vehicles.",
      modalities: ["LiDAR", "RGB", "HD Map"],
      agents: "2–5 CAVs + RSU",
      licence: "MIT",
      source_url: "https://github.com/DerrickXuNu/v2xvit",
      status: "local-import",
      notice: "Import from a local V2XSet directory or prepare directly on your HPC cluster."
    },
    {
      id: "opv2v",
      name: "OPV2V (OpenPack Vehicle-to-Vehicle)",
      description:
        "70+ multi-vehicle cooperative LiDAR + RGB sequences collected " +
        "across 8 CARLA towns and Culver City.",
      modalities: ["LiDAR", "RGB", "HD Map"],
      agents: "2–7 vehicles",
      licence: "MIT",
      source_url: "https://mobility-lab.seas.ucla.edu/opv2v/",
      status: "local-import",
      notice: "Import from a local OPV2V folder or prepare scenes in the HPC workspace."
    },
    {
      id: "v2v4real",
      name: "V2V4Real (Real-World V2V Benchmark)",
      description:
        "Real-world multi-vehicle 3D detection and tracking sequences " +
        "across highways and urban corridors.",
      modalities: ["LiDAR", "3D Boxes"],
      agents: "2 vehicles",
      licence: "Apache-2.0",
      source_url: "https://research.ece.osu.edu/carmen/v2v4real/",
      status: "planned",
      notice: "Direct adapter scheduled for v0.2.0."
    }
  ];

  window.dispatchEvent(
    new CustomEvent("coperceive-test-catalog-seed", {
      detail: {
        catalog,
        presetBytes: {
          "griffin:dev": 412000000,
          "griffin:full": 6840000000
        }
      }
    })
  );

  const recentRoots = [
    "/Users/researcher/coperceive-data/v2xset/2021_08_18_23_23_19",
    "/Users/researcher/coperceive-data/opv2v/2021_08_16_22_26_54",
    "/Users/researcher/coperceive-data/griffin/drone-intersection-01",
    "/Users/researcher/coperceive-data/carla/Town05_intersection_coop"
  ];

  window.dispatchEvent(
    new CustomEvent("coperceive-test-open-folder-seed", {
      detail: {
        dataRoot: "/Users/researcher/coperceive-data/v2xset/2021_08_18_23_23_19",
        recentRoots
      }
    })
  );

  const hpcProfiles = [
    {
      id: "ut-eemcs-hpc",
      schema_version: 1,
      profile: {
        name: "UT EEMCS HPC",
        ssh_alias: "ut-hpc",
        host: "hpc-head1.ewi.utwente.nl",
        user: "nanjaiyalathaa",
        data_roots: ["/datasets/eemcs/ps/cv/opencood/v2xset", "/datasets/eemcs/ps/cv/opv2v"],
        scratch_template: "/scratch/$USER/coperceive",
        results_root: "/home/nanjaiyalathaa/results",
        code_roots: ["/home/nanjaiyalathaa/code/opencood"],
        scheduler: "slurm",
        defaults: {
          partition: "ps",
          account: "eemcs-cv",
          qos: "normal",
          gres: "gpu:a100:1",
          time_limit: "02:00:00",
          memory: "32G",
          cpus_per_task: 8
        },
        environment: {
          kind: "conda",
          modules: ["cuda/12.1"],
          interpreter: "/home/nanjaiyalathaa/miniconda3/envs/coperceive/bin/python",
          container_image: null,
          coperceive_command: ["coperceive"],
          wheelhouse: null,
          compute_has_internet: false
        },
        notes: "Primary EEMCS computer-vision cluster."
      },
      discovered: {
        partitions: [
          {
            name: "ps",
            nodes: 14,
            state: "up",
            max_time: "2-00:00:00",
            gres: "gpu:a100:4",
            gpu_types: ["a100"]
          },
          {
            name: "gpu",
            nodes: 8,
            state: "up",
            max_time: "1-00:00:00",
            gres: "gpu:l40s:4",
            gpu_types: ["l40s"]
          },
          { name: "short", nodes: 6, state: "up", max_time: "04:00:00", gres: null, gpu_types: [] }
        ],
        gpu_types: ["a100", "l40s"],
        environment: {
          module_system: true,
          conda: "/home/nanjaiyalathaa/miniconda3/bin/conda",
          virtualenv: null,
          python: "/home/nanjaiyalathaa/miniconda3/envs/coperceive/bin/python",
          container_runtime: "apptainer",
          coperceive_path: "/home/nanjaiyalathaa/.local/bin/coperceive",
          coperceive_version: "0.1.0",
          scratch_candidates: ["/scratch/nanjaiyalathaa"]
        },
        checked_unix: 1730660000
      },
      ssh: {
        alias: "ut-hpc",
        hostname: "hpc-head1.ewi.utwente.nl",
        user: "nanjaiyalathaa",
        port: 22,
        proxy_jump: "bastion.utwente.nl",
        proxy_command: null
      }
    },
    {
      id: "tudelft-daic",
      schema_version: 1,
      profile: {
        name: "TU Delft DAIC",
        ssh_alias: "daic",
        host: "login.daic.tudelft.nl",
        user: "researcher",
        data_roots: ["/tudelft.net/staff-umbrella/coperceive/datasets"],
        scratch_template: "/tmp/$USER/coperceive",
        results_root: "/tudelft.net/staff-umbrella/coperceive/results",
        code_roots: [],
        scheduler: "slurm",
        defaults: {
          partition: "general",
          account: null,
          qos: "short",
          gres: "gpu:a40:1",
          time_limit: "04:00:00",
          memory: "32G",
          cpus_per_task: 8
        },
        environment: {
          kind: "container",
          modules: [],
          interpreter: null,
          container_image: "/tudelft.net/staff-umbrella/coperceive/coperceive.sif",
          coperceive_command: ["coperceive"],
          wheelhouse: null,
          compute_has_internet: false
        },
        notes: null
      },
      discovered: {
        partitions: [
          {
            name: "general",
            nodes: 32,
            state: "up",
            max_time: "7-00:00:00",
            gres: "gpu:a40:2",
            gpu_types: ["a40"]
          },
          {
            name: "insy",
            nodes: 12,
            state: "up",
            max_time: "3-00:00:00",
            gres: "gpu:v100:4",
            gpu_types: ["v100"]
          }
        ],
        gpu_types: ["a40", "v100"],
        environment: {
          module_system: true,
          conda: null,
          virtualenv: null,
          python: "/usr/bin/python3",
          container_runtime: "apptainer",
          coperceive_path: "/usr/local/bin/coperceive",
          coperceive_version: "0.1.0",
          scratch_candidates: ["/tmp"]
        },
        checked_unix: 1730658000
      },
      ssh: {
        alias: "daic",
        hostname: "login.daic.tudelft.nl",
        user: "researcher",
        port: 22,
        proxy_jump: null,
        proxy_command: null
      }
    }
  ];

  window.__SEEDED_HPC_PROFILES__ = hpcProfiles;
  window.dispatchEvent(
    new CustomEvent("coperceive-test-hpc-servers-seed", {
      detail: {
        profiles: hpcProfiles,
        hosts: ["ut-hpc", "daic"]
      }
    })
  );
}
"""


def _bundle_dir() -> Path | None:
    wanted = os.environ.get("COPERCEIVE_WEB_DIST")
    if wanted:
        chosen = Path(wanted).expanduser()
        if (chosen / "index.html").is_file():
            return chosen
    packaged = Path(__file__).resolve().parents[1] / "coperceive" / "app" / "static"
    return packaged if (packaged / "index.html").is_file() else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle built in coperceive/app/static")
    root = tmp_path_factory.mktemp("visual_root")
    app = create_app(root=root, token=None, static=bundle)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="visual-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="module")
def visual_browser(playwright_instance: Playwright) -> Iterator[Browser]:
    try:
        launched = playwright_instance.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
    except PlaywrightError as exc:
        _unavailable(f"chromium cannot start: {exc}")
    yield launched
    launched.close()


@pytest.fixture(scope="module")
def screenshots_dir() -> Path:
    shots = Path(__file__).resolve().parents[1] / "docs" / "design" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    return shots


def test_visual_revamp_screens_and_traffic_lights_no_overlap(
    visual_browser: Browser, app_url: str, screenshots_dir: Path
) -> None:
    """Verifies each screen with realistic fixture data in dark and light themes at 1280x800

    and 1600x1000, asserts bundled Geist & Geist Mono fonts, captures empty states, and
    verifies zero overlap with macOS traffic lights (x < 74px) in expanded & collapsed states.
    """
    for vp in VIEWPORTS:
        w, h = vp["width"], vp["height"]
        context = visual_browser.new_context(viewport={"width": w, "height": h})
        page = context.new_page()

        try:
            page.add_init_script(RICH_FIXTURE_INIT_SCRIPT)
            page.goto(f"{app_url}/#/connect", wait_until="networkidle")
            page.wait_for_selector(".c-sidebar", timeout=15000)

            page.evaluate("document.documentElement.dataset.theme = 'dark';")
            page.evaluate(SEED_FIXTURES_EVAL)
            page.wait_for_timeout(150)

            # 1. Assert computed font-family includes Geist and Geist Mono
            body_font = page.evaluate("window.getComputedStyle(document.body).fontFamily")
            assert "Geist" in body_font, f"Expected 'Geist' in body font-family, got: {body_font}"
            mono_font = page.evaluate(
                "window.getComputedStyle(document.querySelector('.brand-wordmark')).fontFamily"
            )
            assert "Geist Mono" in mono_font, (
                f"Expected 'Geist Mono' in .brand-wordmark font-family, got: {mono_font}"
            )

            # 2. Check traffic light safe zone: traffic light spacer exists and spans >= 68px
            spacer = page.locator(".traffic-lights-spacer")
            assert spacer.count() > 0, "Traffic lights spacer must exist"
            box = spacer.first.bounding_box()
            assert box is not None, "Spacer must be rendered"
            assert box["width"] >= 68, (
                f"Traffic lights spacer width is {box['width']}, expected >= 68"
            )
            assert box["x"] <= 1, f"Traffic lights spacer must start at left edge, got x={box['x']}"

            # Check wordmark position in expanded sidebar: right of traffic lights (x >= 74)
            wordmark = page.locator(".c-sidebar .brand-wordmark")
            assert wordmark.count() > 0, "Brand wordmark must exist in sidebar"
            wm_box = wordmark.first.bounding_box()
            assert wm_box is not None, "Wordmark must have a bounding box"
            assert wm_box["x"] >= 74, (
                f"Brand wordmark x={wm_box['x']} overlaps with traffic lights safe zone (0..74px)!"
            )

            # Check hide-sidebar button is on the right of the wordmark (also x >= 74)
            hide_btn = page.locator(".c-sidebar button[aria-label*='Toggle sidebar']")
            assert hide_btn.count() > 0
            hb_box = hide_btn.first.bounding_box()
            assert hb_box is not None
            assert hb_box["x"] > wm_box["x"], (
                f"Hide sidebar button x={hb_box['x']} should be right of wordmark x={wm_box['x']}"
            )

            if w == 1280 and h == 800:
                top_left_exp = screenshots_dir / "top_left_expanded.png"
                page.screenshot(
                    path=str(top_left_exp),
                    clip={"x": 0, "y": 0, "width": 320, "height": 56},
                )
                assert top_left_exp.is_file()

            # 3. Test every screen with realistic fixture data in dark and light themes
            for theme in THEMES:
                page.evaluate(f"document.documentElement.dataset.theme = '{theme}';")
                page.wait_for_timeout(120)

                for screen in SCREENS:
                    if screen == "library":
                        page.locator("button[role=tab]", has_text="Library").click()
                        page.evaluate(SEED_FIXTURES_EVAL)
                        page.wait_for_timeout(180)
                    elif screen == "get_data":
                        page.locator("button[role=tab]", has_text="Get Data").click()
                        page.evaluate(SEED_FIXTURES_EVAL)
                        page.wait_for_timeout(180)
                    elif screen == "local":
                        page.locator("button[role=tab]", has_text="Open Folder").click()
                        page.evaluate(SEED_FIXTURES_EVAL)
                        page.wait_for_timeout(180)
                    elif screen == "remote":
                        page.locator(".c-sidebar button", has_text="Servers").first.click()
                        page.evaluate(SEED_FIXTURES_EVAL)
                        page.wait_for_timeout(180)
                    elif screen == "dataset_view":
                        page.evaluate(SEED_FIXTURES_EVAL)
                        page.evaluate(
                            """() => {
                              const srvs = window.__SEEDED_HPC_PROFILES__ || [];
                              const profile = srvs[0];
                              window.dispatchEvent(
                                new CustomEvent("coperceive-open-dataset", {
                                  detail: { profile, useMockV2xset: true }
                                })
                              );
                            }"""
                        )
                        page.wait_for_timeout(220)
                    elif screen == "settings":
                        page.locator("button[aria-label='Settings (⌘,)']").click()
                        page.wait_for_selector(
                            "div[role=dialog][aria-label='Settings']", timeout=5000
                        )
                        page.wait_for_timeout(150)

                    shot_path = screenshots_dir / f"{screen}_{theme}_{w}x{h}.png"
                    page.screenshot(path=str(shot_path), full_page=False)
                    assert shot_path.is_file() and shot_path.stat().st_size > 0

                    if screen == "settings":
                        page.locator("button[aria-label='Close settings (Esc)']").click()
                        page.wait_for_timeout(150)

            # 4. Test collapsing sidebar and traffic-lights clearance in collapsed mode
            page.evaluate("document.documentElement.dataset.theme = 'dark';")
            page.locator("button[role=tab]", has_text="Library").click()
            page.evaluate(SEED_FIXTURES_EVAL)
            page.wait_for_timeout(150)

            toggle_btn = page.locator("button[aria-label*='Toggle sidebar']")
            toggle_btn.first.click()
            page.wait_for_timeout(250)

            sidebar = page.locator(".c-sidebar")
            s_box = sidebar.bounding_box()
            assert s_box is not None
            assert s_box["width"] <= 1, f"Sidebar should be collapsed, width={s_box['width']}"

            collapsed_wm = page.locator("header .brand-wordmark")
            assert collapsed_wm.count() > 0
            c_box = collapsed_wm.first.bounding_box()
            assert c_box is not None
            assert c_box["x"] >= 74, (
                f"Collapsed header wordmark x={c_box['x']} overlaps with traffic lights (0..74px)!"
            )

            expand_btn = page.locator(
                "header button[aria-label*='Open sidebar'], "
                "header button[aria-label*='Toggle sidebar']"
            )
            assert expand_btn.count() > 0
            eb_box = expand_btn.first.bounding_box()
            assert eb_box is not None
            assert eb_box["x"] >= 74, (
                f"Collapsed expand button x={eb_box['x']} overlaps with traffic lights (0..74px)!"
            )

            shot_collapsed = screenshots_dir / f"sidebar_collapsed_dark_{w}x{h}.png"
            page.screenshot(path=str(shot_collapsed))
            assert shot_collapsed.is_file()

            if w == 1280 and h == 800:
                top_left_col = screenshots_dir / "top_left_collapsed.png"
                page.screenshot(
                    path=str(top_left_col),
                    clip={"x": 0, "y": 0, "width": 320, "height": 56},
                )
                assert top_left_col.is_file()

            # 5. Capture empty state screenshots at 1280x800
            if w == 1280 and h == 800:
                expand_btn.first.click()
                page.wait_for_timeout(200)
                page.evaluate(
                    """() => {
                      window.dispatchEvent(
                        new CustomEvent("coperceive-test-library-seed", {
                          detail: { scenes: [], storage: null }
                        })
                      );
                      window.dispatchEvent(
                        new CustomEvent("coperceive-test-catalog-seed", {
                          detail: { catalog: [] }
                        })
                      );
                      window.dispatchEvent(
                        new CustomEvent("coperceive-test-open-folder-seed", {
                          detail: { dataRoot: "", recentRoots: [] }
                        })
                      );
                      window.dispatchEvent(
                        new CustomEvent("coperceive-test-hpc-servers-seed", {
                          detail: { profiles: [], hosts: [] }
                        })
                      );
                    }"""
                )
                for empty_screen, tab_label in [
                    ("library", "Library"),
                    ("get_data", "Get Data"),
                    ("local", "Open Folder"),
                    ("remote", "HPC Workspace"),
                ]:
                    if empty_screen == "remote":
                        page.locator(".c-sidebar button", has_text="Servers").first.click()
                    else:
                        page.locator("button[role=tab]", has_text=tab_label).click()
                    page.wait_for_timeout(180)
                    empty_shot = screenshots_dir / f"empty_{empty_screen}_dark_1280x800.png"
                    page.screenshot(path=str(empty_shot), full_page=False)
                    assert empty_shot.is_file()

        finally:
            context.close()

    # 6. Render reference Claude Design mock at 1600x1000 for side-by-side comparison
    mock_html = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "design"
        / "hpc-dataset-view"
        / "HPC Dataset View.dc.html"
    )
    if mock_html.is_file():
        ctx = visual_browser.new_context(viewport={"width": 1600, "height": 1000})
        mpage = ctx.new_page()
        try:
            mpage.goto(mock_html.as_uri(), wait_until="domcontentloaded")
            mpage.wait_for_timeout(300)
            mock_shot = screenshots_dir / "claude_design_mock_1600x1000.png"
            mpage.screenshot(path=str(mock_shot), full_page=False)
            assert mock_shot.is_file()
        finally:
            ctx.close()
