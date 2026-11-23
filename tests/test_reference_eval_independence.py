# SPDX-License-Identifier: Apache-2.0
"""The reference evaluator shares no code with the product (ADR 0016 §3).

An oracle that imports the thing it checks proves nothing. These tests read the
source of ``tests/reference_eval`` and of the plain-data modules it is fed with
and fail on any import of ``coperceive``, direct or dynamic. They also check
that every ``file::symbol`` anchor of ``docs/eval-spec.md`` names something that
exists, so the spec cannot point at code that has gone.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests" / "reference_eval"
# Modules that hold data or build inputs for both evaluators: they must stay neutral too.
NEUTRAL = (
    ROOT / "tests" / "evalcheck" / "scenario.py",
    ROOT / "tests" / "evalcheck" / "generate.py",
    ROOT / "tests" / "evalcheck" / "transform.py",
    ROOT / "tests" / "evalcheck" / "compare.py",
)
# What the reference may import besides the standard library.
REFERENCE_ALLOWED = ("shapely", "tests.evalcheck.scenario", "tests.reference_eval")
SPEC = ROOT / "docs" / "eval-spec.md"
_ANCHOR = re.compile(r"`([A-Za-z0-9_./-]+\.py)::([A-Za-z_][A-Za-z0-9_]*)`")
_DYNAMIC = {"__import__", "import_module", "exec", "eval"}


def _imports(path: Path) -> list[str]:
    """Every module name ``path`` imports, with relative imports resolved as written."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import; write the module out"
            names.append(node.module or "")
    return names


def _dynamic_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if name in _DYNAMIC:
            found.append(name)
    return found


def _reference_files() -> list[Path]:
    files = sorted(REFERENCE.glob("**/*.py"))
    assert len(files) >= 3, "tests/reference_eval is missing"
    return files


def _stdlib(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names or module == "__future__"


@pytest.mark.parametrize("path", [*_reference_files(), *NEUTRAL], ids=lambda path: path.name)
def test_nothing_from_the_product_is_imported(path: Path) -> None:
    offending = [name for name in _imports(path) if name.split(".")[0] == "coperceive"]
    assert not offending, f"{path.relative_to(ROOT)} imports {offending}"
    assert not _dynamic_calls(path), f"{path.relative_to(ROOT)} imports or runs code dynamically"


@pytest.mark.parametrize("path", _reference_files(), ids=lambda path: path.name)
def test_the_reference_imports_only_the_standard_library_shapely_and_the_scenario(
    path: Path,
) -> None:
    for name in _imports(path):
        allowed = _stdlib(name) or any(
            name == prefix or name.startswith(prefix + ".") for prefix in REFERENCE_ALLOWED
        )
        assert allowed, f"{path.relative_to(ROOT)} imports {name}"


def _defined(path: Path) -> set[str]:
    """Names bound at module level, and methods as their bare names."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
            names.update(
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_every_anchor_of_the_spec_names_code_that_exists() -> None:
    anchors = _ANCHOR.findall(SPEC.read_text(encoding="utf-8"))
    assert len(anchors) >= 25, "docs/eval-spec.md lost its anchors"
    missing: list[str] = []
    for relative, symbol in sorted(set(anchors)):
        path = ROOT / relative
        if not path.is_file():
            missing.append(f"{relative}: no such file")
        elif symbol not in _defined(path):
            missing.append(f"{relative}::{symbol}: not defined there")
    assert not missing, "\n".join(missing)
