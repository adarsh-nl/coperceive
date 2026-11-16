# SPDX-License-Identifier: Apache-2.0
"""Geometry conformance of the dataset adapters (``docs/adr/0017-geometry-conformance.md``).

For each adapter: the standard synthetic world is written in the dataset's native
layout by an exporter that was written from the dataset's documented conventions
(``docs/geometry/conventions.md``) and not from the adapter; the adapter ingests
it through the public ``ingest``; and the scene it produces is checked against
what the generator knows. See ``tests/geomworld/checks.py`` for the checks and
their tolerances.

A check that fails today is listed in ``KNOWN_FAILURES`` with its reason and its
issue, and runs as ``xfail(strict=True)``: it turns red the day the adapter is
fixed and the entry is forgotten. Adapter fixes are routed by the PM; nothing
under ``coperceive/adapters/`` is changed here.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest
from coperceive.adapters.ingest import ingest
from coperceive.adapters.registry import resolve_adapter
from coperceive.store import read_scene

from tests.geomworld import export_generic
from tests.geomworld.checks import CHECKS, Finding, run_checks
from tests.geomworld.expectation import Export
from tests.geomworld.scenes import standard_world
from tests.geomworld.world import World

# Each entry is one export of the standard world: the adapter it is for, and the readings
# of the dataset's conventions it uses. A box format with a heading only cannot hold a
# tilted object, so those objects are left out of its annotations ("omit"); writing them
# with the tilt dropped would fail the checks for a reason that is not the adapter's.
EXPORTERS: dict[str, Callable[[World, Path], Export]] = {
    "generic": partial(export_generic.export, tilted_boxes="omit"),
    # docs/spec/generic-multiview.md: "`handedness: left` applies one y-reflection
    # (ADR 0012) to poses, extrinsics, points and boxes".
    "generic-left": partial(export_generic.export, tilted_boxes="omit", handedness="left"),
}

# (export, check) -> why it fails today and where the fix is tracked. Each runs as
# xfail(strict=True): it turns red the day the adapter is fixed.
KNOWN_FAILURES: dict[tuple[str, str], str] = {
    ("generic-left", "points"): (
        "coperceive/adapters/generic.py reflects poses, extrinsics and boxes of a "
        "handedness: left scene but reads its LiDAR files unchanged, so every return "
        "lands mirrored in y (0% inside its box). The spec reflects points too. "
        "Routed to the adapter owner in PR #166."
    ),
    ("generic-left", "registration"): (
        "Same cause as generic-left points: the centroid of an object's returns is "
        "built from unreflected points, so two agents place it metres apart."
    ),
}

_RESULTS: dict[str, dict[str, list[Finding]]] = {}


def _results(adapter: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Finding]]:
    """Export, ingest and check once per adapter and test session."""
    if adapter not in _RESULTS:
        root = tmp_path_factory.mktemp(f"conformance-{adapter}")
        export = EXPORTERS[adapter](standard_world(), root / "raw")
        ingest(
            resolve_adapter(export.adapter),
            export.raw_root,
            root / "out",
            params=dict(export.params),
            workers=1,
        )
        scene = read_scene(root / "out" / export.scene_id)
        _RESULTS[adapter] = run_checks(export.world, scene, export.expectation)
    return _RESULTS[adapter]


def _cases() -> list[object]:
    cases: list[object] = []
    for adapter in sorted(EXPORTERS):
        for check in CHECKS:
            reason = KNOWN_FAILURES.get((adapter, check))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            cases.append(pytest.param(adapter, check, marks=marks, id=f"{adapter}-{check}"))
    return cases


@pytest.mark.parametrize(("adapter", "check"), _cases())
def test_adapter_conforms(
    adapter: str, check: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    results = _results(adapter, tmp_path_factory)
    if "frames" in results and results["frames"] and check != "frames":
        pytest.fail(f"{adapter}: the scene lacks agents or frames: {results['frames'][0]}")
    findings = results.get(check, [])
    shown = "\n".join(str(finding) for finding in findings[:12])
    assert not findings, f"{adapter}: {len(findings)} {check} findings\n{shown}"


def test_every_known_failure_names_an_adapter_and_a_check() -> None:
    for adapter, check in KNOWN_FAILURES:
        assert adapter in EXPORTERS and check in CHECKS


def test_no_exporter_imports_coperceive() -> None:
    """ADR 0017 §3: an exporter is a second description of the format, not the adapter
    inverted, so it may not reach the adapter's code even indirectly through an import."""
    import ast

    exporters = sorted(Path(__file__).parent.joinpath("geomworld").glob("export_*.py"))
    assert exporters
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
