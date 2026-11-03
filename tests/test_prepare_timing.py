# SPDX-License-Identifier: Apache-2.0
"""The prepare timing diagnostic runs on a synthetic tree and reports every stage."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.opencood_release import two_cav_scene, write_release


def test_prepare_timing_reports_every_stage_and_leaves_raw_alone(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "opv2v"
    write_release(raw, "test", [two_cav_scene("scene_0001")])
    before = {
        str(path.relative_to(raw)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(raw.rglob("*"))
        if path.is_file()
    }
    out = tmp_path / "results" / "timing"
    script = Path(__file__).resolve().parents[1] / "scripts" / "hpc" / "prepare_timing.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--adapter",
            "opv2v",
            "--raw-root",
            str(raw),
            "--split",
            "test",
            "--scene",
            "scene_0001",
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "stage" in result.stdout and "raw read first" in result.stdout
    summary = json.loads((out / "prepare_timing.json").read_text(encoding="utf-8"))
    assert summary["schema"] == "coperceive-prepare-timing-1"
    assert summary["scene_id"] == "scene_0001"
    assert summary["counts"]["pcd"] == 6
    assert summary["counts"]["png"] == 24
    assert summary["read_first"]["bytes"] > 0
    assert summary["read_first"]["files_per_second"] > 0
    assert summary["read_warm"]["seconds"] >= 0
    assert summary["parse_pcd"]["files"] == 6
    assert summary["parse_yaml"]["files"] == 6
    assert summary["preview"]["frames"] == 24
    assert summary["preview"]["jpeg_bytes"] > 0
    assert summary["write"]["bytes"] > 0
    assert summary["environment"]["node"]
    assert summary["environment"]["filesystem_raw"]
    after = {
        str(path.relative_to(raw)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(raw.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_sbatch_wrapper_resolves_args_and_interpreter_from_slurm_env(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    sbatch = repo / "scripts" / "hpc" / "prepare_timing.sbatch"
    spool = tmp_path / "var" / "spool" / "slurmd" / "job123" / "slurm_script"
    spool.parent.mkdir(parents=True)
    spool.write_text(sbatch.read_text(encoding="utf-8"), encoding="utf-8")
    fake_python = tmp_path / "fake-python"
    log = tmp_path / "args.log"
    fake_python.write_text(
        '#!/bin/bash\nprintf \'%s\\n\' "$@" > "${FAKE_PY_LOG:?}"\n', encoding="utf-8"
    )
    fake_python.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    env = {
        "HOME": str(home),
        "SLURM_SUBMIT_DIR": str(repo),
        "SLURM_JOB_ID": "12345",
        "COPERCEIVE_PYTHON": str(fake_python),
        "FAKE_PY_LOG": str(log),
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        ["bash", str(spool), str(raw), "scene_0001"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert f"python={fake_python}" in result.stdout
    args = log.read_text(encoding="utf-8").splitlines()
    assert args[0] == str(repo / "scripts" / "hpc" / "prepare_timing.py")
    assert args[args.index("--raw-root") + 1] == str(raw)
    assert args[args.index("--scene") + 1] == "scene_0001"
    assert "--partition" not in args
    out = home / "results" / "coperceive-timing" / "12345-scene_0001"
    assert out.is_dir()
