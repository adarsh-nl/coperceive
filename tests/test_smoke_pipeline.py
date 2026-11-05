# SPDX-License-Identifier: Apache-2.0
"""The examples/pipelines/smoke pipeline is loadable and honours the contract."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from coperceive.hpc import load_pipeline_registration

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "examples" / "pipelines" / "smoke"


def test_smoke_registration_declares_a_path_and_an_int() -> None:
    pipeline = load_pipeline_registration(SMOKE / "coperceive-pipeline.json")
    assert pipeline.name == "smoke"
    assert pipeline.interpreter == ("python3",)
    assert pipeline.environment.kind.value == "none"
    assert [(argument.name, argument.kind.value) for argument in pipeline.arguments] == [
        ("split", "path"),
        ("sleep-seconds", "int"),
    ]
    assert pipeline.output_contract == "coperceive-pipeline-1"


def test_smoke_writes_the_contract_and_caps_the_contract_check(tmp_path: Path) -> None:
    split = tmp_path / "split.txt"
    split.write_text("Town03-000\n", encoding="utf-8")
    output = tmp_path / "out"
    started = time.monotonic()
    subprocess.run(
        [
            sys.executable,
            str(SMOKE / "smoke_pipeline.py"),
            "--split",
            str(split),
            "--sleep-seconds",
            "5",
            "--max-frames",
            "3",
            "--output-dir",
            str(output),
        ],
        check=True,
    )
    assert time.monotonic() - started < 3

    lines = (output / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    progress = json.loads(lines[0])
    assert progress["type"] == "progress"
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["schema"] == "coperceive-pipeline-metrics"
    assert metrics["version"] == 1
    assert metrics["run"]["scenes"] == ["Town03-000"]
    assert metrics["outputs"] == []


def test_smoke_has_no_cluster_paths() -> None:
    text = "\n".join(
        (SMOKE / name).read_text(encoding="utf-8")
        for name in ("smoke_pipeline.py", "coperceive-pipeline.json")
    ).lower()
    for forbidden in ("/datasets", "/scratch", "/local/", "eemcs", "hpc-"):
        assert forbidden not in text
