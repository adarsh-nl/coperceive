# SPDX-License-Identifier: Apache-2.0
"""The Griffin worker is Python 3.8 code: it parses with the 3.8 grammar, subscripts no
builtin generic outside an annotation, imports only the standard library and its siblings,
and, where the Griffin conda environment exists, imports under its interpreter."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "griffin" / "coperceive_griffin_model"
SIBLINGS = {"convert", "stage", "toolkit", "worker"}
RUNTIME_GENERICS = {"dict", "list", "tuple", "set", "frozenset", "type"}
GRIFFIN_PYTHON = Path.home() / ".conda" / "envs" / "griffin" / "bin" / "python"


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """ids of every node that sits inside an annotation (not evaluated under the future
    import) so the subscript check can skip them."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        parts: list[ast.AST | None] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(node.returns)
            for arg in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
                parts.append(arg.annotation)
            if node.args.vararg is not None:
                parts.append(node.args.vararg.annotation)
            if node.args.kwarg is not None:
                parts.append(node.args.kwarg.annotation)
        elif isinstance(node, ast.AnnAssign):
            parts.append(node.annotation)
        for part in parts:
            if part is not None:
                inside.update(id(sub) for sub in ast.walk(part))
    return inside


@pytest.mark.parametrize("path", sorted(PLUGIN.glob("*.py")), ids=lambda p: p.name)
def test_worker_is_python_38(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path), feature_version=(3, 8))
    annotated = _annotation_nodes(tree)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Match), f"{path.name}: match statement"
        if isinstance(node, ast.Subscript) and id(node) not in annotated:
            value = node.value
            if isinstance(value, ast.Name) and value.id in RUNTIME_GENERICS:
                raise AssertionError(
                    f"{path.name}:{node.lineno}: {value.id}[...] evaluated at runtime"
                )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                top = name.split(".")[0]
                assert top not in {"coperceive", "numpy", "pyarrow", "yaml"}, (
                    f"{path.name} imports {name}"
                )
                if top in SIBLINGS:
                    assert (PLUGIN / f"{top}.py").is_file()
    for needle in ("strict=True", ".removeprefix(", ".removesuffix("):
        assert needle not in source, f"{path.name} uses {needle}"


def test_worker_imports_under_the_griffin_interpreter() -> None:
    """On the HPC the real Python 3.8 imports every module; elsewhere this is skipped."""
    if not GRIFFIN_PYTHON.is_file():
        pytest.skip(f"no Griffin environment at {GRIFFIN_PYTHON}")
    done = subprocess.run(  # noqa: S603 - argv list, the env's own interpreter
        [
            str(GRIFFIN_PYTHON),
            "-c",
            "import convert, stage, toolkit, worker; print(worker.VERSION)",
        ],
        cwd=str(PLUGIN),
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(Path.home()),
            "PYTHONNOUSERSITE": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "0.1.0"
    assert sys.version_info >= (3, 10)  # the parent's interpreter is not the worker's
