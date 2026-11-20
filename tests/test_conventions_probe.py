# SPDX-License-Identifier: Apache-2.0
"""``scripts/verify/conventions_probe.py`` on the synthetic exports of ``tests/geomworld``.

Each exporter writes the standard world under one reading of an open convention; the
probe, run on one exported frame, must name that reading by a clear margin. The probe
must also keep its promises: no path in its output and no file written.

The exports hold the standard world's first frame only: the probe reads one frame, and
three frames would triple the ray casting without adding a case.
"""

from __future__ import annotations

import getpass
import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.geomworld import export_dair_v2x_c, export_opv2v, export_v2v4real, export_v2xset
from tests.geomworld.scenes import standard_world
from tests.geomworld.world import World

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify" / "conventions_probe.py"
# A reading wins "by a clear margin" when its score beats the next one by this much.
CLEAR_MARGIN = 0.3

OPENCOOD_SCENE = "train/2026_11_16_00_00_00"
V2V4REAL_SCENE = "train/testoutput_CAV_data_2026-11-16-00-00-00_0"
FIRST_STEM = "000000"
DAIR_FRAME = f"{export_dair_v2x_c.VEHICLE_FIRST_ID:06d}"

_VERDICT = re.compile(r"^verdict (\S+) (\S+): (\S+)(?: margin (\d+\.\d+))?")


def _load_probe() -> ModuleType:
    """The script as a module, without leaving bytecode or a changed flag behind."""
    previous = sys.dont_write_bytecode
    try:
        spec = importlib.util.spec_from_file_location("conventions_probe", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["conventions_probe"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


PROBE = _load_probe()


@pytest.fixture(scope="module")
def world() -> World:
    """The standard world, first frame only (one shared ray-cast cache)."""
    full = standard_world()
    return replace(
        full,
        objects=full.objects[:1],
        agents=tuple(replace(a, world_from_body=a.world_from_body[:1]) for a in full.agents),
    )


Exporter = Callable[..., Any]


@pytest.fixture(scope="module")
def exported(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Callable[..., Path]]:
    """``exported(exporter, **readings)``: the export's root, written once per module."""
    cache: dict[tuple[str, tuple[tuple[str, str], ...]], Path] = {}

    def get(exporter: Exporter, **readings: str) -> Path:
        key = (exporter.__module__, tuple(sorted(readings.items())))
        if key not in cache:
            root = tmp_path_factory.mktemp("export")
            exporter(world, root, **readings)
            cache[key] = root
        return cache[key]

    yield get


def _run(argv: Sequence[str]) -> list[str]:
    lines: list[str] = PROBE.run(list(argv))
    text = "\n".join(lines)
    for value in argv[1:]:
        if "/" in value:
            assert value not in text
    assert getpass.getuser() not in text
    return lines


def _verdict(lines: Sequence[str], row: str) -> tuple[str, float | None]:
    for line in lines:
        match = _VERDICT.match(line)
        if match and match.group(1) == row:
            margin = match.group(4)
            return match.group(3), (float(margin) if margin is not None else None)
    raise AssertionError(f"no verdict for {row} in:\n" + "\n".join(lines))


def _assert_picks(lines: Sequence[str], row: str, expected: str) -> None:
    reading, margin = _verdict(lines, row)
    report = "\n".join(lines)
    assert reading == expected, report
    assert margin is not None and margin >= CLEAR_MARGIN, report


# --- OPV2V / V2XSet: OPV-7 and OPV-6 --------------------------------------------------------


@pytest.mark.parametrize("extrinsic", export_opv2v.READINGS["camera_extrinsic"])
def test_opv2v_camera_extrinsic(exported: Callable[..., Path], extrinsic: str) -> None:
    root = exported(export_opv2v.export, camera_extrinsic=extrinsic)
    lines = _run(["opv2v", str(root / OPENCOOD_SCENE), "650", FIRST_STEM])
    _assert_picks(lines, "OPV-7", extrinsic)
    # The exporter's default Euler reading is CARLA's, which flips roll and pitch.
    _assert_picks(lines, "OPV-6", "flipped")


@pytest.mark.parametrize("extrinsic", export_v2xset.READINGS["camera_extrinsic"])
@pytest.mark.parametrize("agent", ["650", "-1"])
def test_v2xset_camera_extrinsic(exported: Callable[..., Path], extrinsic: str, agent: str) -> None:
    root = exported(export_v2xset.export, camera_extrinsic=extrinsic)
    lines = _run(["v2xset", str(root / OPENCOOD_SCENE), agent, FIRST_STEM])
    _assert_picks(lines, "OPV-7", extrinsic)


def test_opv2v_euler_as_the_adapter_reads_it(exported: Callable[..., Path]) -> None:
    root = exported(export_opv2v.export, euler="rzryrx")
    lines = _run(["opv2v", str(root / OPENCOOD_SCENE), "651", FIRST_STEM])
    _assert_picks(lines, "OPV-6", "adapter")
    _assert_picks(lines, "OPV-7", "lidar_to_camera")


# --- V2V4Real: V4R-7 and V4R-1 ----------------------------------------------------------------


@pytest.mark.parametrize("pose_encoding", ["numpy", "six"])
@pytest.mark.parametrize("box_frame", export_v2v4real.READINGS["box_frame"])
@pytest.mark.parametrize("handedness", export_v2v4real.READINGS["handedness"])
def test_v2v4real_box_frame_and_handedness(
    exported: Callable[..., Path], handedness: str, box_frame: str, pose_encoding: str
) -> None:
    root = exported(
        export_v2v4real.export,
        handedness=handedness,
        box_frame=box_frame,
        pose_encoding=pose_encoding,
    )
    lines = _run(["v2v4real", str(root / V2V4REAL_SCENE), "1", FIRST_STEM])
    _assert_picks(lines, "V4R-7", box_frame)
    # Containment cannot pick a handedness: the files of the two readings are mirror
    # images of each other, so every point lands in the same box under both. The probe
    # must say so instead of naming one.
    reading, _ = _verdict(lines, "V4R-1")
    assert reading == "undecided", "\n".join(lines)
    rows = {
        tuple(line.split()[:2]): line.split()[-1]
        for line in lines
        if line.startswith(("  lidar ", "  world "))
    }
    assert rows[(box_frame, "right")] == rows[(box_frame, "left")]


# --- DAIR-V2X-C: DAIR-10 --------------------------------------------------------------------

# Each order is exported with one of the layouts the probe must also accept: per-side
# paths from the release root, flat calibration matrices and a top-level lidar_to_novatel.
_DAIR_CASES = {
    "dair_lidar": {"label_numbers": "string"},
    "turned_pi": {"info_paths": "release"},
    "reversed_ring": {"matrix_layout": "flat", "novatel_nesting": "top_level"},
}


@pytest.mark.parametrize("corner_order", export_dair_v2x_c.READINGS["corner_order"])
def test_dair_corner_order(exported: Callable[..., Path], corner_order: str) -> None:
    root = exported(
        export_dair_v2x_c.export, corner_order=corner_order, **_DAIR_CASES[corner_order]
    )
    lines = _run(["dair", str(root), DAIR_FRAME])
    _assert_picks(lines, "DAIR-10", corner_order)
    # The pairing went through cooperative/data_info.json: the two sides' ids differ.
    assert export_dair_v2x_c.VEHICLE_FIRST_ID != export_dair_v2x_c.INFRA_FIRST_ID
    containment = [line.split() for line in lines if line.startswith(("  vehicle", "  infra"))]
    assert len(containment) == 2
    assert all(float(row[-1]) > 0.95 for row in containment), "\n".join(lines)


# --- promises: no path in the output, nothing written ---------------------------------------


def _snapshot(*folders: Path) -> dict[str, tuple[int, int]]:
    """Every file and folder under ``folders`` with its mtime and size."""
    listing: dict[str, tuple[int, int]] = {}
    for folder in folders:
        for current, dirs, files in os.walk(folder):
            for name in [*dirs, *files]:
                path = Path(current) / name
                stat = path.stat()
                listing[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return listing


def _subprocess(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # No -B: the probe itself must keep Python from writing bytecode.
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _path_words(path: Path) -> set[str]:
    return {part for part in path.parts if len(part) > 1 and part != os.sep}


def _assert_no_path(text: str, root: Path) -> None:
    assert str(root) not in text
    assert getpass.getuser() not in text
    tokens = set(re.split(r"[\s/\\:;,()=]+", text))
    leaked = _path_words(root) & tokens
    assert not leaked, f"path parts in the output: {sorted(leaked)}"


@pytest.mark.parametrize(
    ("exporter", "argv"),
    [
        (export_opv2v.export, ["opv2v", f"{{root}}/{OPENCOOD_SCENE}", "650", FIRST_STEM]),
        (export_v2v4real.export, ["v2v4real", f"{{root}}/{V2V4REAL_SCENE}", "0", FIRST_STEM]),
        (export_dair_v2x_c.export, ["dair", "{root}", DAIR_FRAME]),
    ],
)
def test_output_has_no_path_and_nothing_is_written(
    exported: Callable[..., Path],
    tmp_path: Path,
    exporter: Exporter,
    argv: list[str],
) -> None:
    root = exported(exporter)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    args = [value.replace("{root}", str(root)) for value in argv]
    watched = (root, workdir, SCRIPT.parent, REPO / "coperceive" / "adapters" / "readers")
    before = _snapshot(*watched)
    result = _subprocess(args, workdir)
    after = _snapshot(*watched)
    assert result.returncode == 0, result.stderr
    assert "verdict" in result.stdout
    _assert_no_path(result.stdout + result.stderr, root)
    assert after == before


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["opv2v", "{root}/" + OPENCOOD_SCENE, "650", "999999"], "the agent YAML is missing"),
        (["opv2v", "{root}/" + OPENCOOD_SCENE, "a/b", FIRST_STEM], "plain name"),
    ],
)
def test_errors_name_no_path(
    exported: Callable[..., Path], tmp_path: Path, argv: list[str], message: str
) -> None:
    root = exported(export_opv2v.export)
    args = [value.replace("{root}", str(root)) for value in argv]
    result = _subprocess(args, tmp_path)
    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""
    _assert_no_path(result.stderr, root)


# --- the PCD reader's compressed form (no exporter writes it) -------------------------------


def _lzf_literals(data: bytes) -> bytes:
    """A valid LZF stream of literal runs only."""
    out = bytearray()
    for start in range(0, len(data), 32):
        chunk = data[start : start + 32]
        out.append(len(chunk) - 1)
        out += chunk
    return bytes(out)


def test_lzf_back_reference() -> None:
    # "abc" as a literal, then 6 bytes copied from 3 back.
    stream = b"\x02abc" + bytes([(6 - 2) << 5, 3 - 1])
    assert PROBE.lzf_decompress(stream, 9, "a test stream") == b"abcabcabc"


def test_pcd_binary_compressed() -> None:
    import numpy as np

    points = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [-1.5, 0.25, 9.0]], dtype="<f4")
    intensity = np.array([7.0, 8.0, 9.0], dtype="<f4")
    columns = points.T.tobytes() + intensity.tobytes()
    compressed = _lzf_literals(columns)
    header = (
        "VERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\n"
        "WIDTH 3\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS 3\nDATA binary_compressed\n"
    ).encode("ascii")
    sizes = len(compressed).to_bytes(4, "little") + len(columns).to_bytes(4, "little")
    xyz = PROBE.read_pcd_xyz(header + sizes + compressed, "a test cloud")
    assert xyz.tolist() == points.astype(np.float64).tolist()
