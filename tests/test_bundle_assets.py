# SPDX-License-Identifier: Apache-2.0
"""The built viewer bundle checker used by CI and the packaging scripts."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "check_bundle_assets.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_static(static: Path, *, marker: str = "") -> Path:
    (static / "assets").mkdir(parents=True)
    (static / "assets" / "app.js").write_text(f"console.log('ok'){marker}\n", encoding="utf-8")
    (static / "assets" / "app.css").write_text("body{}\n", encoding="utf-8")
    (static / "index.html").write_text(
        '<link rel="stylesheet" href="./assets/app.css">'
        '<script type="module" src="./assets/app.js"></script>\n',
        encoding="utf-8",
    )
    return static


def _static(root: Path, *, marker: str = "") -> Path:
    return _write_static(root / "coperceive" / "app" / "static", marker=marker)


def test_a_complete_static_bundle_passes(tmp_path: Path) -> None:
    static = _static(tmp_path)
    result = run("--static", str(static))
    assert result.returncode == 0, result.stderr
    assert "viewer bundle check passed" in result.stdout


def test_a_missing_js_asset_fails(tmp_path: Path) -> None:
    static = _static(tmp_path)
    (static / "assets" / "app.js").unlink()
    result = run("--static", str(static))
    assert result.returncode == 1
    assert "missing files" in result.stderr


def test_the_test_stage_marker_fails(tmp_path: Path) -> None:
    static = _static(tmp_path, marker="// __COPERCEIVE_STAGE__")
    result = run("--static", str(static))
    assert result.returncode == 1
    assert "test-stage marker" in result.stderr


def test_a_wheel_with_the_bundle_passes_and_a_missing_asset_fails(tmp_path: Path) -> None:
    good = tmp_path / "good.whl"
    static = _static(tmp_path / "wheel-good")
    with zipfile.ZipFile(good, "w") as archive:
        for path in static.rglob("*"):
            if path.is_file():
                archive.write(path, f"coperceive/app/static/{path.relative_to(static)}")
    result = run("--wheel", str(good))
    assert result.returncode == 0, result.stderr

    bad = tmp_path / "bad.whl"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr(
            "coperceive/app/static/index.html",
            '<script src="./assets/app.js"></script>',
        )
    result = run("--wheel", str(bad))
    assert result.returncode == 1
    assert "missing from the wheel" in result.stderr


def test_the_packaged_app_bundle_is_checked(tmp_path: Path) -> None:
    app = tmp_path / "Coperceive.app"
    bundled = (
        app
        / "Contents"
        / "Resources"
        / "resources"
        / "python"
        / "lib"
        / "python3.13"
        / "site-packages"
        / "coperceive"
        / "app"
        / "static"
    )
    _write_static(bundled)
    result = run("--app", str(app))
    assert result.returncode == 0, result.stderr
    assert "viewer bundle check passed" in result.stdout
