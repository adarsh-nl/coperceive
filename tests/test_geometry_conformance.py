# SPDX-License-Identifier: Apache-2.0
"""Geometry conformance of the dataset adapters (``docs/adr/0017-geometry-conformance.md``).

For each case: the standard synthetic world is written in a dataset's native layout by an
exporter that was written from the dataset's documented conventions
(``docs/geometry/conventions.md``) and not from the adapter; the adapter ingests it through
the public ``ingest``; and the scene it produces is checked against what the generator
knows. See ``tests/geomworld/checks.py`` for the checks and their tolerances.

A case is one exporter with one set of readings. Where the documents leave a convention
open, the exporter takes a keyword per reading; the default is the reading the sources
support best. When an adapter refuses that default for a reason that is layout, not
geometry, a second case of the same dataset uses the layout the adapter reads, so the
geometry behind it is still tested. The refusal itself stays a finding.

An exporter reports the dataset's own names (folder names, sensor names, label ids). How
an adapter renames them is the adapter's business, not a convention, so each case maps
native names to the scene's names here.

A check that fails today is listed in ``KNOWN_FAILURES`` with its reason, and runs as
``xfail(strict=True)``: it turns red the day the adapter is fixed and the entry is
forgotten. Adapter fixes are routed by the PM; nothing under ``coperceive/adapters/`` is
changed here.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from coperceive.adapters.ingest import ingest
from coperceive.adapters.registry import resolve_adapter
from coperceive.store import read_scene

from tests.geomworld import (
    export_dair_v2x_c,
    export_generic,
    export_griffin,
    export_opv2v,
    export_v2v4real,
    export_v2xset,
)
from tests.geomworld.checks import CHECKS, Finding, run_checks
from tests.geomworld.expectation import Expectation, Export
from tests.geomworld.scenes import standard_world

Exporter = Callable[..., Export]


def _same(path: Path) -> Path:
    return path


def _griffin_release(raw_root: Path) -> Path:
    """The Griffin adapter reads the folder that holds ``vehicle-side/``."""
    return next(p.parent for p in sorted(raw_root.rglob("vehicle-side")) if p.is_dir())


@dataclass(frozen=True)
class Case:
    """One export of the standard world and how to read its names."""

    exporter: Exporter
    readings: Mapping[str, str] = field(default_factory=dict[str, str])
    reader_root: Callable[[Path], Path] = _same
    # Native name -> scene name. A name not listed is the same in the scene.
    agents: Mapping[str, str] = field(default_factory=dict[str, str])
    # The scene's sensor id of every agent's LiDAR, when the adapter fixes it.
    lidar: str | None = None
    categories: Mapping[str, str] = field(default_factory=dict[str, str])


# Readings the adapters implement where they differ from the reading the sources support
# best. Each such case passes; its source-reading twin carries the difference as a known
# failure. See docs/geometry/conventions.md for every row named below.
_OPENCOOD_ADAPTER = {"euler": "rzryrx", "camera_extrinsic": "camera_to_lidar"}
_V2V4REAL_ADAPTER = {
    "pose_encoding": "six",
    "handedness": "left",
    "box_frame": "world",
    "euler": "rzryrx",
}
# Layout, not geometry: each value is a valid release the adapter can read.
_DAIR_ADAPTER = {
    "frame_ids": "shared",
    "info_paths": "release",
    "matrix_layout": "rotation_nested",
    "novatel_nesting": "top_level",
}
_GRIFFIN_ADAPTER = {
    "scene_info_fields": "with_platform",
    "pose_timestamp": "frame",
    "lidar_file": "ply_ascii",
}
_DAIR_NAMES = {"vehicle-side": "vehicle", "infrastructure-side": "infrastructure"}
_GRIFFIN_NAMES = {"vehicle-side": "vehicle", "drone-side": "drone"}

CASES: dict[str, Case] = {
    # A box format with a heading only cannot hold a tilted object, so those objects
    # are left out of its annotations: writing them with the tilt dropped would fail the
    # checks for a reason that is not the adapter's.
    "generic": Case(export_generic.export, {"tilted_boxes": "omit"}),
    # docs/spec/generic-multiview.md: "`handedness: left` applies one y-reflection
    # (ADR 0012) to poses, extrinsics, points and boxes".
    "generic-left": Case(export_generic.export, {"tilted_boxes": "omit", "handedness": "left"}),
    "opv2v": Case(export_opv2v.export, lidar="lidar"),
    "opv2v-adapter-readings": Case(export_opv2v.export, _OPENCOOD_ADAPTER, lidar="lidar"),
    "v2xset": Case(export_v2xset.export, lidar="lidar"),
    "v2xset-adapter-readings": Case(export_v2xset.export, _OPENCOOD_ADAPTER, lidar="lidar"),
    "v2v4real": Case(export_v2v4real.export, lidar="lidar"),
    "v2v4real-flat-pose": Case(export_v2v4real.export, {"pose_encoding": "six"}, lidar="lidar"),
    # The adapter names every object "vehicle" (conventions V4R-10, a known failure of
    # v2v4real-flat-pose), so the classes are mapped here to test the geometry alone.
    "v2v4real-adapter-readings": Case(
        export_v2v4real.export,
        _V2V4REAL_ADAPTER,
        lidar="lidar",
        categories={"Car": "vehicle", "Truck": "vehicle"},
    ),
    "dair-v2x-c": Case(export_dair_v2x_c.export, agents=_DAIR_NAMES, lidar="lidar"),
    "dair-v2x-c-adapter-layout": Case(
        export_dair_v2x_c.export, _DAIR_ADAPTER, agents=_DAIR_NAMES, lidar="lidar"
    ),
    "griffin": Case(export_griffin.export, reader_root=_griffin_release, agents=_GRIFFIN_NAMES),
    "griffin-adapter-layout": Case(
        export_griffin.export,
        _GRIFFIN_ADAPTER,
        reader_root=_griffin_release,
        agents=_GRIFFIN_NAMES,
    ),
}

_ROUTED = "Routed to the adapter owner in PR #166."
_OPENCOOD_EULER = (
    "The adapter composes [roll, yaw, pitch] as Rz(yaw) Ry(pitch) Rx(roll) before the "
    "y-reflection; CARLA's GetMatrix and OpenCOOD's x_to_world (code, cite-checked) give "
    "Rz(yaw) Ry(-pitch) Rx(-roll), so roll and pitch come out mirrored: a tilted agent or "
    "box is off by twice its tilt (conventions OPV-6, OPV-13). Unverified in prose. "
)
_OPENCOOD_EXTRINSIC = (
    "Camera extrinsics are read camera-to-LiDAR, as the OpenCOOD docs label them; the "
    "docs' own example numbers and the OpenCDA dumper make them LiDAR-to-camera "
    "(conventions OPV-7), so every camera sits metres off. "
)
_V2V4REAL_POSE = (
    "lidar_pose written as the 4 x 4 matrix of the one published file excerpt (issue #5, "
    "partly supported) is refused; the adapter reads a flat [x, y, z, roll, yaw, pitch] "
    "(conventions V4R-4). "
)
_V2V4REAL_FLAT = (
    "With the flat pose: the adapter mirrors every pose, box and point in y, assuming "
    "CARLA's left-handed frame, where no source gives V2V4Real's handedness (V4R-1, "
    "unverified); it reads boxes as world coordinates, where the paper and the loader put "
    "them in each vehicle's own LiDAR frame (V4R-7); it has the roll/pitch sign of "
    "OPV-6 (V4R-6); and it names every object 'vehicle', ignoring obj_type (V4R-10). "
)

# (case, check) -> why it fails today. Each runs as xfail(strict=True).
KNOWN_FAILURES: dict[tuple[str, str], str] = {
    ("generic-left", "points"): (
        "coperceive/adapters/generic.py reflects poses, extrinsics and boxes of a "
        "handedness: left scene but reads its LiDAR files unchanged, so every return "
        "lands mirrored in y (0% inside its box). The spec reflects points too "
        "(conventions GEN-10). " + _ROUTED
    ),
    ("generic-left", "registration"): (
        "Same cause as generic-left points: the centroid of an object's returns is "
        "built from unreflected points, so two agents place it metres apart. " + _ROUTED
    ),
    **{
        (case, check): reason + _ROUTED
        for case in ("opv2v", "v2xset")
        for check, reason in (
            ("sensor-pose", _OPENCOOD_EULER + _OPENCOOD_EXTRINSIC),
            ("boxes", _OPENCOOD_EULER),
            ("points", _OPENCOOD_EULER),
            ("registration", _OPENCOOD_EULER),
            ("cameras", _OPENCOOD_EULER + _OPENCOOD_EXTRINSIC),
        )
    },
    **{("v2v4real", check): _V2V4REAL_POSE + _ROUTED for check in CHECKS},
    **{
        ("v2v4real-flat-pose", check): _V2V4REAL_FLAT + _ROUTED
        for check in ("sensor-pose", "boxes", "points", "registration")
    },
    **{
        ("dair-v2x-c", check): (
            "Refused at ingest: the adapter pairs a cooperative frame only when the "
            "vehicle, infrastructure and label files share one id, and ignores the pair "
            "paths of cooperative/data_info.json; the dataset's ids on the two sides are "
            "independent (conventions DAIR-1, supported). It also reads data_info paths "
            "from the release root, a calibration translation only as a flat list and "
            "lidar_to_novatel without its 'transform' level (DAIR-5); each is unverified "
            "in the documents. " + _ROUTED
        )
        for check in CHECKS
    },
    ("dair-v2x-c-adapter-layout", "sensor-pose"): (
        "The adapter ingests no camera (conventions DAIR-13). " + _ROUTED
    ),
    ("dair-v2x-c-adapter-layout", "boxes"): (
        "Every box turns 90 degrees with length and width swapped under each corner "
        "order the sources give for world_8_points: the adapter takes corner 0 -> 1 as "
        "the length edge (conventions DAIR-10; the stored order is unverified). " + _ROUTED
    ),
    **{
        ("griffin", check): (
            "Refused at ingest: the adapter requires info.map_name and info.cruise_altitude in "
            "scene_infos.json (and, behind it, a pose 'timestamp' key and .ply points), none of "
            "which the Griffin documents describe; they list name, info.frames, info.weather "
            "and .bin points (conventions GRF-3, GRF-8, GRF-14). The repository's notes say the "
            "released archive does carry .ply. " + _ROUTED
        )
        for check in CHECKS
    },
}


def _scene_names(case: Case, expectation: Expectation) -> Expectation:
    """``expectation`` with the dataset's native names replaced by the scene's."""
    return replace(
        expectation,
        agents={w: case.agents.get(n, n) for w, n in expectation.agents.items()},
        lidars={w: case.lidar or n for w, n in expectation.lidars.items()},
        categories={w: case.categories.get(n, n) for w, n in expectation.categories.items()},
    )


_RESULTS: dict[str, dict[str, list[Finding]]] = {}


def _results(name: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Finding]]:
    """Export, ingest and check once per case and test session."""
    if name not in _RESULTS:
        case = CASES[name]
        root = tmp_path_factory.mktemp(f"conformance-{name}")
        export = case.exporter(standard_world(), root / "raw", **case.readings)
        try:
            ingest(
                resolve_adapter(export.adapter),
                case.reader_root(export.raw_root),
                root / "out",
                params=dict(export.params),
                workers=1,
            )
        except Exception as error:  # noqa: BLE001 - a refusal is the finding
            refused = Finding("frames", "-", 0, "ingest", "the export is ingested", str(error))
            _RESULTS[name] = {"frames": [refused]}
            return _RESULTS[name]
        produced = [p for p in (root / "out").iterdir() if p.is_dir() and p.name[0] != "."]
        assert len(produced) == 1, f"{name}: expected one scene, got {produced}"
        scene = read_scene(produced[0])
        _RESULTS[name] = run_checks(export.world, scene, _scene_names(case, export.expectation))
    return _RESULTS[name]


def _cases() -> list[object]:
    cases: list[object] = []
    for name in CASES:
        for check in CHECKS:
            reason = KNOWN_FAILURES.get((name, check))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            cases.append(pytest.param(name, check, marks=marks, id=f"{name}-{check}"))
    return cases


@pytest.mark.parametrize(("case", "check"), _cases())
def test_adapter_conforms(case: str, check: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    results = _results(case, tmp_path_factory)
    if results.get("frames") and check != "frames":
        pytest.fail(f"{case}: the scene lacks agents or frames: {results['frames'][0]}")
    findings = results.get(check, [])
    shown = "\n".join(str(finding) for finding in findings[:12])
    assert not findings, f"{case}: {len(findings)} {check} findings\n{shown}"


def test_every_known_failure_names_a_case_and_a_check() -> None:
    for name, check in KNOWN_FAILURES:
        assert name in CASES and check in CHECKS, (name, check)


def test_no_exporter_imports_coperceive() -> None:
    """ADR 0017 §3: an exporter is a second description of the format, not the adapter
    inverted, so it may not reach the adapter's code even indirectly through an import."""
    folder = Path(__file__).parent / "geomworld"
    exporters = sorted(folder.glob("export_*.py")) + [folder / "opencood_format.py"]
    assert len(exporters) >= 6
    for path in exporters:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] != "coperceive", f"{path.name} imports {name}"
