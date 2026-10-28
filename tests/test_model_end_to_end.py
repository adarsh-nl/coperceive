# SPDX-License-Identifier: Apache-2.0
"""End-to-end step B batch chain on the cooperative fixture (plan, Task 13).

Writes the cooperative fixture scene, runs the fake plugin over it through
``coperceive model run``, evaluates with ``coperceive eval run``, checks the
``/api/scenes/{id}/predictions`` route via ``TestClient`` (verifying ``provenance``
in ``run.json``), re-runs from the per-scene cache into a second run directory and
compares the two runs with ``coperceive eval compare`` (``+0.000`` across shared
rows), and verifies that ``coperceive model run --slurm`` emits an sbatch script
accepted by ``bash -n``.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast

import pytest
from coperceive.app.server import create_app
from coperceive.cli import main
from coperceive.store import write_scene
from fastapi.testclient import TestClient

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

SCENE_ID = "coop"
RUN_A = "fake-batch-a"
RUN_B = "fake-batch-b"

Row = dict[str, object]


class _Response(Protocol):
    """Typed response subset so strict pyright does not depend on starlette's httpx2 base."""

    status_code: int

    def json(self) -> object: ...


def _get(client: TestClient, path: str) -> _Response:
    return cast(_Response, client.get(path))  # pyright: ignore[reportUnknownMemberType]


def test_model_run_eval_serve_compare_and_slurm_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    predictions = scenes / "predictions"
    write_scene(scene, scenes / SCENE_ID)
    (scenes / SCENE_ID / "COMPLETE.json").write_text("{}\n", encoding="utf-8")

    # 1. Batch run of the fake plugin's known-answer model -> complete format 1 run.
    main(
        [
            "model",
            "run",
            "--manifest",
            str(FAKE),
            "--model",
            "known-answer",
            "--scenes",
            str(scenes),
            "--out",
            str(predictions),
            "--run-id",
            RUN_A,
        ]
    )
    out_a = capsys.readouterr().out
    assert f"wrote {predictions / RUN_A}" in out_a

    # 2. Second run (deterministic) -> evaluate both and compare.
    main(
        [
            "model",
            "run",
            "--manifest",
            str(FAKE),
            "--model",
            "known-answer",
            "--scenes",
            str(scenes),
            "--out",
            str(predictions),
            "--run-id",
            RUN_B,
        ]
    )
    capsys.readouterr()

    for run_id in (RUN_A, RUN_B):
        main(["eval", "run", run_id, "--root", str(scenes)])
        assert capsys.readouterr().out.splitlines()[-1] == "evaluated 1 skipped 0 errors 0"

    # 3. Server route exposes the evaluated plugin run with its provenance.
    client: TestClient = TestClient(create_app(root=scenes, token=None))
    listed = _get(client, f"/api/scenes/{SCENE_ID}/predictions")
    assert listed.status_code == 200
    runs = cast(list[Row], cast(Row, listed.json())["runs"])
    by_id = {cast(str, r["run_id"]): r for r in runs}
    assert set(by_id) == {RUN_A, RUN_B}
    assert by_id[RUN_A]["evaluated"] is True
    run_meta = cast(Row, by_id[RUN_A]["run"])
    provenance = cast(Row, run_meta["provenance"])
    assert provenance["plugin"] == "fake"
    assert provenance["model"] == "known-answer"
    assert provenance["status"] == "complete"
    assert provenance["completed_scenes"] == [SCENE_ID]

    # 4. eval compare between the two deterministic runs prints +0.000 across shared rows.
    main(["eval", "compare", RUN_A, RUN_B, "--root", str(scenes)])
    compare_out = capsys.readouterr().out
    assert f"| {SCENE_ID} | vehicle | single |" in compare_out
    assert "| all |" in compare_out
    assert "+0.000" in compare_out

    # 5. Slurm mode emits an sbatch script that bash -n accepts without running the child.
    main(
        [
            "model",
            "run",
            "--manifest",
            str(FAKE),
            "--model",
            "known-answer",
            "--scenes",
            str(scenes),
            "--out",
            str(predictions),
            "--run-id",
            "fake-slurm",
            "--slurm",
            "--slurm-logs",
            str(tmp_path / "logs"),
            "--slurm-results",
            str(tmp_path / "results"),
        ]
    )
    sbatch_path = predictions / ".slurm" / "fake-slurm.sbatch"
    assert sbatch_path.is_file()
    assert not (predictions / "fake-slurm").exists()
    if shutil.which("bash") is not None:
        done = subprocess.run(["bash", "-n", str(sbatch_path)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
