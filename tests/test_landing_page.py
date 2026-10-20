# SPDX-License-Identifier: MIT
"""End-to-end and accessibility verification for the coperceive landing page."""

from __future__ import annotations

import functools
import http.server
import socket
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

DIST_DIR = Path(__file__).resolve().parent.parent / "site" / "dist"
AXE_PATH = (
    Path(__file__).resolve().parent.parent / "site" / "node_modules" / "axe-core" / "axe.min.js"
)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def site_server():
    assert DIST_DIR.exists(), f"site/dist does not exist at {DIST_DIR}. Run 'npm run build' first."
    port = find_free_port()
    handler = functools.partial(QuietHandler, directory=str(DIST_DIR))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.2)
    yield base_url
    httpd.shutdown()


def test_landing_page_e2e_and_a11y(site_server: str, tmp_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # Navigate
        response = page.goto(site_server, wait_until="networkidle")
        assert response is not None and response.status == 200

        # 1. Header / Navbar
        assert page.locator("header").is_visible()
        assert page.locator("header").get_by_text("coperceive").is_visible()
        assert (
            page.locator("header")
            .get_by_role("link", name="GitHub repository", exact=True)
            .is_visible()
        )

        # 2. Hero Headline & Section
        heading = page.locator("h1")
        assert heading.is_visible()
        assert "Cooperative Autonomous Perception" in heading.inner_text()

        # 3. 3D Canvas / Region
        hero_region = page.locator('div[role="region"]')
        assert hero_region.is_visible()

        # Check telemetry HUD loaded
        assert page.get_by_text("CAV (Ground)").is_visible()
        assert page.get_by_text("UAV (Aerial 27m)").is_visible()

        # 4. Interactive controls: Jump to Occlusion
        jump_btn = page.get_by_role("button", name="Jump to Occlusion")
        assert jump_btn.is_visible()
        jump_btn.click()
        page.wait_for_timeout(300)

        # Occlusion badge should now be visible
        assert page.get_by_text("Occluded Pedestrian #265 Detected").is_visible()

        # 5. Viewpoint switcher
        chase_btn = page.get_by_role("button", name="CHASE")
        assert chase_btn.is_visible()
        chase_btn.click()
        page.wait_for_timeout(200)

        # 6. Occlusion Section
        occlusion_section = page.locator("#occlusion")
        assert occlusion_section.is_visible()
        assert page.get_by_text("Pedestrian #265 at Blind Corner").is_visible()
        assert page.get_by_text("17.5 m").is_visible()
        assert page.get_by_text("0.0137 m").first.is_visible()

        # Switch perspective tab to UAV
        uav_tab = page.get_by_role("button", name="2. Aerial UAV View")
        assert uav_tab.is_visible()
        uav_tab.click()
        page.wait_for_timeout(200)
        assert page.get_by_text("Aerial Drone Perspective").is_visible()

        # 7. Architecture / How It Works
        arch_section = page.locator("#architecture")
        assert arch_section.is_visible()
        assert arch_section.get_by_text("Unified Spatial Calibration").is_visible()
        assert arch_section.get_by_role(
            "heading", name="Subprocess Plugin Protocol", exact=True
        ).is_visible()

        # 8. Desktop Section
        desktop_section = page.locator("#desktop")
        assert desktop_section.is_visible()
        assert desktop_section.get_by_text("coperceive Desktop").is_visible()
        assert desktop_section.get_by_text("[TODO: Native app screenshot").is_visible()

        # 9. Datasets Table
        datasets_section = page.locator("#datasets")
        assert datasets_section.is_visible()
        assert datasets_section.get_by_text("Griffin", exact=True).is_visible()
        assert datasets_section.get_by_text("CARLA–AirSim Simulation").is_visible()
        assert datasets_section.get_by_text("OPV2V", exact=True).is_visible()
        assert datasets_section.get_by_text(
            "Licence not published; local research use only"
        ).first.is_visible()
        assert datasets_section.get_by_text("CARLA OpenDRIVE Maps", exact=True).is_visible()
        assert datasets_section.get_by_text("CC-BY License").first.is_visible()

        # 10. Quick Start Commands & Copy Interaction
        quickstart = page.locator("#quickstart")
        assert quickstart.is_visible()
        copy_btn = quickstart.get_by_role("button", name="Copy").first
        assert copy_btn.is_visible()
        copy_btn.click()
        page.wait_for_timeout(200)
        assert page.get_by_text("Copied").first.is_visible()

        # 11. Citation Section
        assert page.get_by_text("Citation & Attribution").is_visible()
        assert page.get_by_text("@software{coperceive2026").is_visible()
        assert page.get_by_text("@article{wang2025griffin").is_visible()

        # 12. Footer
        footer = page.locator("footer")
        assert footer.is_visible()
        assert "Adarsh van Ee" in footer.inner_text()
        assert "CC-BY" in footer.inner_text()

        # 13. Accessibility Audit with axe-core
        assert AXE_PATH.exists(), f"axe.min.js not found at {AXE_PATH}"
        axe_script = AXE_PATH.read_text()
        page.evaluate(axe_script)

        axe_results = page.evaluate("""
            axe.run({
                runOnly: {
                    type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']
                }
            })
        """)

        violations = axe_results.get("violations", [])
        if violations:
            print("AXE VIOLATIONS FOUND:")
            for v in violations:
                print(f"[{v['id']}] {v['help']}: {v['helpUrl']}")
                for node in v["nodes"]:
                    print(f"  Target: {node['target']}")
                    print(f"  HTML: {node['html']}")

        assert len(violations) == 0, (
            f"Found {len(violations)} accessibility violations: {[v['id'] for v in violations]}"
        )

        # Save screenshot
        screenshot_path = tmp_path / "landing_page.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"Full-page screenshot saved to {screenshot_path}")

        browser.close()


def test_reduced_motion_behavior(site_server: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader"])
        # Emulate reduced-motion
        page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page.goto(site_server, wait_until="networkidle")

        # Reduced motion banner should appear
        banner = page.get_by_text("Reduced motion active (paused at frame 52)")
        assert banner.is_visible()

        browser.close()
