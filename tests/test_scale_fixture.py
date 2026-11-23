# SPDX-License-Identifier: Apache-2.0
"""The real-size fixture generator and the viewer bench (ADR 0020, phase 0).

The fixture is checked at a tiny size: it reads back through the store with the
counts asked for, the same seed writes the same bytes, and a target inside the
repository is refused. The bench runs ``--quick`` at a tiny size when Playwright,
Chromium and a web bundle are present, and is skipped (or failed, where
``COPERCEIVE_E2E_REQUIRED`` says the browser is the point) otherwise. No timing
assertion: the numbers depend on the machine.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn, cast

import pytest
from coperceive.model import Modality
from coperceive.store import read_scene

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "scripts" / "bench" / "scale_fixture.py"
BENCH = REPO / "scripts" / "bench" / "viewer_bench.py"
SMALL = ["--agents", "2", "--points", "2000", "--cameras", "2", "--samples", "3"]
REQUIRED = os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}


def _bench_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(sys, "path", [str(BENCH.parent), *sys.path])  # for bench_machine
    spec = importlib.util.spec_from_file_location("viewer_bench_under_test", BENCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(out: Path, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), str(out), *SMALL, *extra],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    summary = cast(dict[str, Any], json.loads(completed.stdout.strip().splitlines()[-1]))
    return completed, summary


def _files(scene_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(scene_dir)): path.read_bytes()
        for path in sorted(scene_dir.rglob("*"))
        if path.is_file()
    }


def test_a_small_fixture_reads_back_with_the_counts_asked_for(tmp_path: Path) -> None:
    completed, summary = _write(tmp_path / "scenes")
    scene_dir = Path(summary["scene_dir"])
    assert scene_dir.parent == (tmp_path / "scenes").resolve()
    assert (scene_dir / "COMPLETE.json").is_file()
    assert summary["bytes"] == sum(len(data) for data in _files(scene_dir).values())
    assert f"wrote {summary['bytes']} bytes" in completed.stderr

    scene = read_scene(scene_dir)
    assert [agent.agent_id for agent in scene.agents] == ["cav-0", "cav-1"]
    assert all(len(agent.poses) == 3 for agent in scene.agents)
    lidars = [s for s in scene.sensors if s.modality is Modality.LIDAR]
    cameras = [s for s in scene.sensors if s.modality is Modality.CAMERA]
    assert len(lidars) == 2 and len(cameras) == 2
    assert all(c.image_width_px == 160 and c.image_height_px == 120 for c in cameras)
    assert len(scene.point_clouds) == 2 * 3
    assert all(cloud.count == 2000 for cloud in scene.point_clouds)
    assert len(scene.images) == 2 * 3
    for image in scene.images:
        assert (scene_dir / image.path).read_bytes().startswith(b"\x89PNG")
    times = sorted({cloud.time_us for cloud in scene.point_clouds})
    assert times == [0, 100_000, 200_000]
    # A few boxes per agent per sample, with track ids shared between the agents.
    assert len(scene.annotations) == 2 * 3 * 6
    assert {obj.track_id for obj in scene.objects} == {a.track_id for a in scene.annotations}
    # Real poses: the vehicles turn, so the world transform is not a translation.
    assert any(abs(pose.world_from_body.qz) > 0.1 for pose in scene.agents[0].poses)


def test_the_same_seed_writes_the_same_bytes(tmp_path: Path) -> None:
    _, first = _write(tmp_path / "a")
    _, second = _write(tmp_path / "b")
    _, other = _write(tmp_path / "c", "--seed", "7")
    a = _files(Path(first["scene_dir"]))
    assert a == _files(Path(second["scene_dir"]))
    assert a["points.bin"] != _files(Path(other["scene_dir"]))["points.bin"]


def test_a_target_inside_the_repository_is_refused() -> None:
    target = REPO / "scale-fixture-must-not-exist"
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), str(target), *SMALL],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
        check=False,
    )
    assert completed.returncode == 2, completed.stderr
    assert "inside the repository" in completed.stderr
    assert not target.exists()


def _stat(at: float, result: str, draw: float) -> dict[str, Any]:
    return {
        "at": at,
        "index": 0,
        "load_ms": 5.0,
        "points_ms": 20.0 if result == "applied" else None,
        "draw_ms": draw,
        "render_ms": draw / 10,
        "result": result,
    }


def test_the_summary_times_applied_samples_and_splits_io_by_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench = _bench_module(monkeypatch)
    raw: dict[str, Any] = {
        "measure_from": 1000.0,
        "measure_to": 2000.0,
        "raf": [1000.0 + 16.0 * i for i in range(60)],
        "frame_stats": [
            _stat(1100.0, "applied", 40.0),
            _stat(1200.0, "dropped", 0.0),
            _stat(1300.0, "applied", 60.0),
            _stat(500.0, "applied", 999.0),  # before the window
        ],
        "io": [
            {"kind": "frame", "op": "wait", "at": 1100.0, "ms": 3.0},
            {"kind": "frame", "op": "read", "at": 1101.0, "ms": 1.0},
            {"kind": "frame", "op": "parse", "at": 1102.0, "ms": 2.0},
            {"kind": "points", "op": "read", "at": 1150.0, "ms": 8.0, "bytes": 1920000},
            {"kind": "image", "op": "decode", "at": 1160.0, "ms": 4.0},
            {"kind": "image", "op": "decode", "at": 2500.0, "ms": 400.0},  # after it
        ],
        "polls": [],
        "network": [],
        "no_webgl": False,
        "first_draw_s": 1.0,
    }
    report = bench.summarize(raw, scene_rate_hz=10.0, samples=30)
    phases = report["phases_ms"]
    assert phases["draw"]["count"] == 2 and phases["draw"]["max"] == 60.0  # the drop is not timed
    assert phases["points"]["count"] == 2
    assert phases["load"]["count"] == 3  # dropped samples still loaded their frame document
    assert report["samples"]["drawn"] == 2 and report["samples"]["dropped"] == 1
    io = report["io_ms"]
    assert set(io) == {"frame", "points", "image"}
    assert set(io["frame"]) == {"wait", "read", "parse"}
    assert io["image"]["decode"]["count"] == 1 and io["image"]["decode"]["max"] == 4.0
    decode = report["decode_ms"]
    assert decode["frame_json_parse"]["p50"] == 2.0
    assert decode["image_decode"]["p50"] == 4.0
    assert decode["points"] is None  # raw float32: nothing to decode
    assert report["io_all_ms"]["image"]["decode"]["count"] == 2  # the whole run, not the window


def test_request_kinds_are_the_scene_routes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _bench_module(monkeypatch)
    base = "http://127.0.0.1:9/api/scenes/s1"
    assert bench.url_kind(f"{base}/points?time_us=1&agent_id=a") == "points"
    assert bench.url_kind(f"{base}/frame?time_us=1") == "frame"
    assert bench.url_kind(f"{base}/image?path=x&width=96") == "image"
    assert bench.url_kind(f"{base}/predictions/run1/frame?time_us=1") == "other"
    assert bench.url_kind(f"{base}") == "other"


def test_the_machine_record_names_the_cpu_and_no_host(monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _bench_module(monkeypatch)
    info = cast(dict[str, object], bench.machine_info())
    assert {"platform", "machine", "cpu", "cpu_count", "memory_bytes", "python"} <= set(info)
    assert os.uname().nodename not in json.dumps(info)


def _unavailable(reason: str) -> NoReturn:
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


def test_the_viewer_bench_quick_run_reports_its_numbers(tmp_path: Path) -> None:
    try:
        import playwright.sync_api  # noqa: F401  # pyright: ignore[reportUnusedImport]
    except ImportError:
        _unavailable("playwright is not installed")
    out = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(BENCH),
            "--quick",
            *["--agents", "2", "--points", "2000", "--cameras", "2", "--samples", "20"],
            *["--warmup-s", "0.5", "--measure-s", "1", "--json-out", str(out)],
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
        check=False,
    )
    if completed.returncode == 3:  # no Chromium or no web bundle on this machine
        _unavailable(completed.stderr.strip().splitlines()[-1])
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = cast(dict[str, Any], json.loads(completed.stdout.strip().splitlines()[-1]))
    assert json.loads(out.read_text(encoding="utf-8")) == result
    assert result["schema"] == "coperceive-viewer-bench-1"
    assert result["quick"] is True
    assert {"machine", "browser", "fixture", "server", "config", "measured"} <= set(result)
    assert result["fixture"]["generated"] is True
    assert result["fixture"]["agents"] == 2 and result["fixture"]["samples"] == 20
    assert {"name", "version", "renderer", "webgl"} <= set(result["browser"])
    measured = result["measured"]
    for key in ("window_s", "rendering_skipped", "phases_ms", "buffering", "playhead", "memory"):
        assert key in measured
    assert {"load", "points"} <= set(measured["phases_ms"])
    assert {"frame_json_parse", "image_decode", "points"} <= set(measured["decode_ms"])
    assert "frame" in measured["io_all_ms"], measured["io_all_ms"]
    assert {"server_rss_peak_bytes", "server_rss_end_bytes"} & set(measured["memory"])
    assert result["machine"]["cpu_count"]
    assert {"events", "ms", "stalled_events"} <= set(measured["buffering"])
    assert {"bytes", "mb_per_s", "by_kind_bytes"} <= set(measured["network"])
    if measured["rendering_skipped"]:
        assert "rendering skipped: no WebGL" in completed.stderr
        assert "frame_time_ms" not in measured and "samples" not in measured
        assert {"draw", "render"}.isdisjoint(measured["phases_ms"])
    else:
        assert {"count", "p50", "p95", "max"} <= set(measured["frame_time_ms"])
        assert measured["frame_time_ms"]["count"] > 0
        assert {"drawn", "drawn_per_s", "scene_rate_hz", "ratio"} <= set(measured["samples"])
        assert measured["samples"]["scene_rate_hz"] == 10.0
        assert {"draw", "render"} <= set(measured["phases_ms"])
        assert "gpu_points_estimate_bytes" in measured["memory"]
    assert measured["page_errors"] == [], measured["page_errors"]
