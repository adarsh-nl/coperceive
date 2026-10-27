# SPDX-License-Identifier: Apache-2.0
"""The HPC workspace layer carries no site-specific values or defaults."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from coperceive.hpc import (
    EnvironmentKind,
    EnvironmentSetup,
    JobDefaults,
    JobRequest,
    ServerProfile,
    SlurmAdapter,
)

REPO = Path(__file__).resolve().parents[1]
HPC_PYTHON = sorted((REPO / "coperceive" / "hpc").glob("*.py"))
FORBIDDEN = (
    "hpc-node",
    "hpc-head",
    "utwente",
    "/datasets/eemcs",
    "main-gpu",
    "main-cpu",
    "partition=ps",
    'partition="ps"',
    "'ps,main",
)


def _rendered_scripts() -> list[str]:
    alpha = ServerProfile(
        name="Alpha lab",
        ssh_alias="alpha-cluster",
        data_roots=("/data/projects/alpha",),
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        defaults=JobDefaults(partition="gpu-a100", gres="gpu:a100:1", time_limit="01:00:00"),
        environment=EnvironmentSetup(kind=EnvironmentKind.MODULES, modules=("python/3.12",)),
    )
    beta = ServerProfile(
        name="Beta cluster",
        host="login.beta.example",
        user="bob",
        data_roots=("/mnt/beta/data",),
        scratch_template="/local/$USER/$SLURM_JOB_ID",
        defaults=JobDefaults(partition="cpu-short"),
        environment=EnvironmentSetup(
            kind=EnvironmentKind.VENV, interpreter="/mnt/beta/venv/bin/python"
        ),
    )

    def _no_runner(argv: Sequence[str], *, stdin: str | None = None) -> str:
        return ""

    adapter = SlurmAdapter(_no_runner)
    scripts: list[str] = []
    for profile in (alpha, beta):
        request = JobRequest(
            job_name="coperceive-eval",
            command_argv=("coperceive", "eval", "run", "run-1"),
            partition=profile.defaults.partition,
        )
        scripts.append(adapter.render_job_script(profile, request))
    return scripts


def test_no_site_value_in_the_hpc_python_layer() -> None:
    assert HPC_PYTHON, "expected coperceive/hpc modules"
    for path in HPC_PYTHON:
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN:
            assert forbidden not in text, f"{path.name} contains site value {forbidden!r}"


def test_no_site_value_in_rendered_job_scripts() -> None:
    for script in _rendered_scripts():
        lowered = script.lower()
        for forbidden in FORBIDDEN:
            assert forbidden not in lowered, f"job script contains site value {forbidden!r}"
        # Every partition in a rendered script came from the profile above.
        assert "partition=gpu-a100" in script or "partition=cpu-short" in script


def test_no_default_partition_or_cluster_host_constant_exists() -> None:
    for path in HPC_PYTHON:
        text = path.read_text(encoding="utf-8")
        assert "DEFAULT_PARTITION" not in text
        assert "DEFAULT_HOST" not in text
        assert "DEFAULT_SCRATCH" not in text


def test_profile_json_and_schema_hold_no_site_defaults() -> None:
    schema_path = REPO / "docs" / "spec" / "cluster-profile.schema.json"
    pipeline_schema_path = REPO / "docs" / "spec" / "pipeline.schema.json"
    for path in (schema_path, pipeline_schema_path):
        assert path.is_file(), f"missing JSON schema {path}"
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN:
            assert forbidden not in text, f"{path.name} contains site value {forbidden!r}"
    profile_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    defaults = profile_schema["$defs"]["job_defaults"]["properties"]
    assert defaults["partition"]["type"] == ["string", "null"]
    assert "default" not in defaults["partition"]
    assert "default" not in profile_schema["properties"]["host"]
    assert "default" not in profile_schema["properties"]["ssh_alias"]


def test_rust_mirror_exists_and_has_no_site_defaults() -> None:
    rust = REPO / "src-tauri" / "src" / "hpc.rs"
    assert rust.is_file(), "expected the Rust serde mirrors in src-tauri/src/hpc.rs"
    text = rust.read_text(encoding="utf-8").lower()
    for forbidden in FORBIDDEN:
        assert forbidden not in text, f"hpc.rs contains site value {forbidden!r}"
    assert "default_partition" not in text
    assert "ps,main" not in text
