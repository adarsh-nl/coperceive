# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""Repro cards: schema, path roles, secret scrubbing, and tamper detection.

Fixtures are synthetic. Nothing here is a measured detector result.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path

import pytest
from coperceive.cli import main
from coperceive.evaluation.evaluate import read_metrics
from coperceive.evaluation.predictions import RunMeta
from coperceive.hpc.profile import (
    EnvironmentKind,
    EnvironmentSetup,
    SchedulerKind,
    ServerProfile,
)
from coperceive.hpc.scheduler import JobRequest, SlurmAdapter
from coperceive.repro.environment import CAPTURE_PROGRAM, environment_capture_lines
from coperceive.repro.export import ExportRequest, check_card, export_card, sha256_file
from coperceive.repro.schema import repo_schema_path, schema_text
from coperceive.repro.scrub import file_name, leaks, relativise, scrub_card
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions

TOKEN = "ghp_" + ("a" * 36)
PLAN_ID = "plan-1790000000-aabbccddeeff"
RUN_ID = "synthetic"
REPO = Path(__file__).resolve().parents[1]


def test_packaged_schema_matches_the_spec() -> None:
    assert schema_text() == repo_schema_path().read_text(encoding="utf-8")


def test_relativise_picks_the_longest_root_and_keeps_the_role() -> None:
    roots = {
        "data_roots": ("/data/alpha", "/data/alpha/nested"),
        "code_roots": ("/code/alpha",),
        "results_root": ("/results/alpha",),
    }
    assert relativise("/data/alpha/nested/weights/model.pt", roots) == {
        "role": "data_roots",
        "relative": "weights/model.pt",
    }
    assert relativise("/code/alpha/pipelines/run.py", roots) == {
        "role": "code_roots",
        "relative": "pipelines/run.py",
    }
    assert relativise("/results/alpha/runs/job", roots) == {
        "role": "results_root",
        "relative": "runs/job",
    }
    assert relativise("/other/cluster/model.pt", roots) is None
    assert relativise("/data/alpha/../outside/model.pt", roots) is None
    assert relativise("~/models/model.pt", roots) is None
    assert file_name("/data/alpha/nested/weights/model.pt") == "model.pt"


def test_local_export_matches_eval_metrics_and_checks(tmp_path: Path) -> None:
    root = _evaluated_run(tmp_path)
    card = export_card(
        ExportRequest(run_id=RUN_ID, scenes_root=root, created_utc="2026-11-02T12:00:00Z")
    )
    scene_dir = root / "predictions" / RUN_ID / "coop"
    assert card["metrics"]["by_scene"]["coop"] == read_metrics(scene_dir)  # type: ignore[index]
    predictions = (root / "predictions" / RUN_ID / "predictions.jsonl").read_bytes()
    assert card["predictions_sha256"] == hashlib.sha256(predictions).hexdigest()
    assert card["pipeline_report"]["authoritative"] is False  # type: ignore[index]
    assert card["pipeline_report"]["label"] == "Pipeline report. Not the coperceive score."  # type: ignore[index]
    assert card["perturbation"] == {"kind": "identity", "note": None}
    text = json.dumps(card)
    assert TOKEN not in text
    assert "example.edu" not in text
    assert "/home/" not in text
    problems_ok = check_card(
        card,
        run_dir=root / "predictions" / RUN_ID,
        predictions=root / "predictions",
        run_id=RUN_ID,
    )
    assert problems_ok == []
    problems = check_card(
        _tamper_predictions(card, root / "predictions" / RUN_ID),
        run_dir=root / "predictions" / RUN_ID,
    )
    assert problems and "predictions" in problems[0]


def test_remote_second_profile_is_portable_and_has_no_secrets(tmp_path: Path) -> None:
    first = _profile_tree(tmp_path / "first", "alpha")
    second = _profile_tree(tmp_path / "second", "beta")
    _remote_run(second, token=TOKEN, hostname="login.example.edu")
    card = export_card(
        ExportRequest(
            run_id=PLAN_ID,
            profile_path=second / "profile.json",
            created_utc="2026-11-02T12:00:00Z",
            time_limit="01:00:00",
            memory="8G",
        )
    )
    text = json.dumps(card)
    assert str(first) not in text
    assert "example.edu" not in text
    assert TOKEN not in text
    assert "/home/" not in text
    assert "beta-alias" not in text
    blob = json.dumps(card["paths"])
    assert '"role": "data_roots"' in blob
    assert '"role": "code_roots"' in blob
    assert '"role": "results_root"' in blob
    assert card["checkpoint"]["file_name"] == "model.pt"  # type: ignore[index]
    assert card["checkpoint"]["sha256"] == sha256_file(second / "data" / "weights" / "model.pt")  # type: ignore[index]
    assert card["pipeline"]["registration_sha256"] == sha256_file(  # type: ignore[index]
        second / "code" / "pipelines" / "coperceive-pipeline.json"
    )
    roles = {item["name"]: item["role"] for item in card["pipeline"]["arguments"]}  # type: ignore[index]
    assert roles["checkpoint"] == "checkpoint"
    assert roles["split"] == "test_data"
    assert card["resources"]["time"] == "01:00:00"  # type: ignore[index]
    assert card["resources"]["gpus"] == "gpu:1"  # type: ignore[index]
    assert check_card(card, run_dir=second / "results" / "runs" / PLAN_ID) == []
    for entry in card["paths"]:  # type: ignore[union-attr]
        assert not str(entry["relative"]).startswith("/")
        assert ".." not in str(entry["relative"]).split("/")


def test_missing_facts_are_unknown_instead_of_guessed(tmp_path: Path) -> None:
    root = tmp_path / "scenes"
    run = root / "predictions" / RUN_ID
    (run / "scene-a").mkdir(parents=True)
    meta = RunMeta(
        run_id=RUN_ID,
        model="fixture",
        score_note="constant",
        categories=("car",),
        sources=(("single", "own sensors"),),
        created_utc="2026-11-02T00:00:00Z",
    )
    (run / "run.json").write_text(json.dumps(meta.to_json()) + "\n", encoding="utf-8")
    card = export_card(
        ExportRequest(run_id=RUN_ID, scenes_root=root, created_utc="2026-11-02T12:00:00Z")
    )
    assert card["dataset"]["id"] is None  # type: ignore[index]
    assert card["checkpoint"]["sha256"] is None  # type: ignore[index]
    assert card["metrics"] is None
    assert card["environment"]["python"] is None  # type: ignore[index]
    unknown = set(card["unknown"])  # type: ignore[arg-type]
    assert "dataset.id" in unknown
    assert "checkpoint.sha256" in unknown
    assert "metrics" in unknown
    assert "environment.python" in unknown
    assert "not recorded" not in json.dumps(card["checkpoint"])


def test_cli_export_and_check_round_trip(tmp_path: Path) -> None:
    root = _evaluated_run(tmp_path)
    dest = tmp_path / "card.json"
    markdown = tmp_path / "card.md"
    main(
        [
            "repro",
            "export",
            RUN_ID,
            "--root",
            str(root),
            "--out",
            str(dest),
            "--markdown",
            str(markdown),
        ]
    )
    card = json.loads(dest.read_text(encoding="utf-8"))
    assert card["schema"] == "coperceive-repro-card"
    assert card["schema_version"] == 1
    assert "Authoritative scores" in markdown.read_text(encoding="utf-8")
    main(
        [
            "repro",
            "check",
            str(dest),
            "--run-dir",
            str(root / "predictions" / RUN_ID),
            "--predictions",
            str(root / "predictions"),
            "--run-id",
            RUN_ID,
        ]
    )
    tampered = root / "predictions" / RUN_ID / "predictions.jsonl"
    tampered.write_bytes(tampered.read_bytes() + b"\n")
    with pytest.raises(SystemExit) as caught:
        main(["repro", "check", str(dest), "--run-dir", str(root / "predictions" / RUN_ID)])
    assert caught.value.code == 1


def test_check_accepts_a_newer_envelope_and_still_rejects_a_secret() -> None:
    envelope = {
        "schema": "coperceive-repro-card",
        "schema_version": 2,
        "id": "card-0123456789ab",
        "created_utc": "2026-11-02T12:00:00Z",
        "extra": "a later field",
    }
    assert check_card(envelope) == []
    leaked = dict(envelope)
    leaked["extra"] = TOKEN
    problems = check_card(leaked)
    assert problems and "credential" in problems[0]


def test_capture_script_writes_environment_without_secrets(tmp_path: Path) -> None:
    dest = tmp_path / "environment.json"
    script = "\n".join(environment_capture_lines(shlex.quote(str(dest)))) + "\n"
    subprocess.run(["bash", "-c", script], check=True)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["schema"] == "coperceive-environment"
    assert payload["schema_version"] == 1
    assert isinstance(payload["python"], str) and payload["python"]
    text = dest.read_text(encoding="utf-8").lower()
    for forbidden in ("ghp_", "password", "secret", "/home/"):
        assert forbidden not in text
    for forbidden in ("token", "password", "secret"):
        assert forbidden not in CAPTURE_PROGRAM.lower()


def test_capture_program_matches_the_rust_job_script() -> None:
    rust = (REPO / "src-tauri" / "src" / "slurm_adapter.rs").read_text(encoding="utf-8")
    start = rust.index('ENVIRONMENT_CAPTURE_PROGRAM: &str = r#"')
    body = rust[start + len('ENVIRONMENT_CAPTURE_PROGRAM: &str = r#"') :]
    body = body[: body.index('"#;')]
    assert body.strip() == CAPTURE_PROGRAM.strip()
    lowered = CAPTURE_PROGRAM.lower()
    for forbidden in ("token", "password", "secret", "pip install"):
        assert forbidden not in lowered


def test_job_scripts_record_the_environment(tmp_path: Path) -> None:
    profile = ServerProfile(
        name="Alpha lab",
        ssh_alias="alpha-cluster",
        data_roots=("/data/projects/alpha",),
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        results_root="/data/projects/alpha/runs",
        environment=EnvironmentSetup(kind=EnvironmentKind.MODULES, modules=("python/3.12",)),
    )
    script = SlurmAdapter(lambda *_args, **_kwargs: "").render_job_script(
        profile,
        JobRequest(
            job_name="coperceive-eval",
            command_argv=("coperceive", "eval", "run", "run-1"),
            partition="gpu-a100",
        ),
    )
    assert "coperceive-environment" in script
    assert script.strip().splitlines()[-1] == "coperceive eval run run-1"
    lowered = script.lower()
    for forbidden in ("token", "password", "secret"):
        assert forbidden not in lowered

    # The model-run sbatch also writes environment.json into the run directory.
    from dataclasses import replace

    from tests.test_model_slurm import _manifest, _plan

    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    plan = _plan((scenes, tmp_path / "predictions", scene), _manifest(), tmp_path)
    assert "environment.json" in plan.script
    assert "coperceive-environment" in plan.script


def _evaluated_run(tmp_path: Path) -> Path:
    from dataclasses import replace

    root = tmp_path / "scenes"
    scene = replace(cooperative_scene(), scene_id="coop")
    write_scene(scene, root / "coop")
    boxes = synthetic_predictions(scene)[1]
    rows = []
    for box in boxes:
        rows.append(
            {
                "scene_id": "coop",
                "agent_id": box.agent_id,
                "source": box.source,
                "time_us": box.time_us,
                "category": box.category,
                "score": box.score,
                "track_id": box.track_id,
                "cx": box.center[0],
                "cy": box.center[1],
                "cz": box.center[2],
                "length": box.size_lwh[0],
                "width": box.size_lwh[1],
                "height": box.size_lwh[2],
                "qw": box.box_from_rpy.qw,
                "qx": box.box_from_rpy.qx,
                "qy": box.box_from_rpy.qy,
                "qz": box.box_from_rpy.qz,
                "frame": box.frame.value,
            }
        )
    jsonl = tmp_path / "boxes.jsonl"
    jsonl.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    main(
        [
            "predictions",
            "import",
            str(jsonl),
            "--run",
            RUN_ID,
            "--root",
            str(root),
            "--score-note",
            "constant",
            "--model",
            "fixture",
        ]
    )
    main(["eval", "run", RUN_ID, "--root", str(root)])
    run = root / "predictions" / RUN_ID
    (run / "predictions.jsonl").write_text(jsonl.read_text(encoding="utf-8"), encoding="utf-8")
    (run / "metrics.json").write_text(
        json.dumps(
            {
                "schema": "coperceive-pipeline-metrics",
                "version": 1,
                "metrics": {"latency_ms": 3, "note": TOKEN},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stored = json.loads((run / "run.json").read_text(encoding="utf-8"))
    stored["provenance"] = {
        "git_commit": "ab" * 20,
        "host": "login.example.edu",
        "perturbation": {"kind": "identity", "note": TOKEN},
        "checkpoint": {"path": "/home/alice/weights/model.pt", "size": 4, "md5": "ab" * 16},
    }
    (run / "run.json").write_text(json.dumps(stored) + "\n", encoding="utf-8")
    (root / "dataset.json").write_text(
        json.dumps({"id": "synthetic-set", "version": "v1"}) + "\n",
        encoding="utf-8",
    )
    return root


def _tamper_predictions(card: dict[str, object], run_dir: Path) -> dict[str, object]:
    path = run_dir / "predictions.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    return card


def _profile_tree(root: Path, name: str) -> Path:
    data = root / "data"
    code = root / "code" / "pipelines"
    results = root / "results"
    for path in (data / "weights", data / "splits", code, results):
        path.mkdir(parents=True)
    (data / "weights" / "model.pt").write_bytes(b"weights")
    (data / "splits" / "val.txt").write_text("scene-a\nscene-b\n", encoding="utf-8")
    (code / "run.py").write_text("print('synthetic pipeline')\n", encoding="utf-8")
    registration = {
        "name": "synthetic",
        "script": "pipelines/run.py",
        "interpreter": ["python3"],
        "environment": {"kind": "none"},
        "arguments": [
            {"name": "checkpoint", "kind": "path", "role": "checkpoint"},
            {"name": "split", "kind": "path", "role": "test_data"},
            {"name": "output-dir", "kind": "path", "role": "output"},
        ],
    }
    (code / "coperceive-pipeline.json").write_text(
        json.dumps(registration) + "\n", encoding="utf-8"
    )
    profile = ServerProfile(
        name=f"{name} lab",
        ssh_alias=f"{name}-alias",
        data_roots=(str(data),),
        code_roots=(str(root / "code"),),
        scratch_template=str(root / "scratch") + "/$USER/$SLURM_JOB_ID",
        results_root=str(results),
        scheduler=SchedulerKind.SLURM,
        environment=EnvironmentSetup(kind=EnvironmentKind.MODULES, modules=("python/3.12",)),
    )
    profile.validate()
    (root / "profile.json").write_text(profile.to_json_text(), encoding="utf-8")
    return root


def _remote_run(root: Path, *, token: str, hostname: str) -> None:
    results = root / "results"
    run = results / "runs" / PLAN_ID
    plans = results / "plans"
    plans.mkdir(parents=True)
    run.mkdir(parents=True)
    data = root / "data"
    code = root / "code"
    weights = data / "weights" / "model.pt"
    split = data / "splits" / "val.txt"
    script = code / "pipelines" / "run.py"
    (run / "predictions.jsonl").write_text(
        json.dumps({"scene_id": "scene-a", "score": 0.2}) + "\n",
        encoding="utf-8",
    )
    (run / "metrics.json").write_text(
        json.dumps(
            {
                "schema": "coperceive-pipeline-metrics",
                "version": 1,
                "metrics": {"host": hostname, "note": token},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run / "environment.json").write_text(
        json.dumps(
            {
                "schema": "coperceive-environment",
                "schema_version": 1,
                "python": "3.12.1",
                "key_packages": {"numpy": "2.1.0"},
                "pip_freeze_sha256": "cd" * 32,
                "modules": [],
                "container_image_digest": None,
                "cuda": "12.4",
                "driver": "550.54",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    plan = {
        "schema": "coperceive-run-plan-1",
        "id": PLAN_ID,
        "pipeline": {"name": "synthetic", "source": "custom"},
        "argv": [
            "python3",
            str(script),
            "--checkpoint",
            str(weights),
            "--split",
            str(split),
            "--output-dir",
            str(run),
            "--note",
            token,
        ],
        "environment": {"kind": "modules", "modules": ["python/3.12"]},
        "checkpoint": {"path": str(weights), "sha256": sha256_file(weights), "kind": "file"},
        "split": {"path": str(split), "sha256": sha256_file(split), "kind": "file"},
        "output_dir": str(run),
        "gres": "gpu:1",
        "nothing_submitted": True,
    }
    (plans / f"{PLAN_ID}.json").write_text(json.dumps(plan) + "\n", encoding="utf-8")

def test_scrubber_removes_personal_identifiers_hosts_homes_tokens_and_emails() -> None:
    card: dict[str, object] = {
        "environment": {
            "user": "nanjaiyalathaa",
            "host": "ewi.utwente.nl",
            "home": "/home/nanjaiyalathaa",
            "mac_home": "/Users/nanjaiyalathaa/data",
            "email": "nanjaiyalathaa@example.org",
            "token": TOKEN,
            "ssh": "ssh -i /home/nanjaiyalathaa/.ssh/id_ed25519 ewi.utwente.nl",
        },
        "pipeline": {"name": "nanjaiyalathaa-checkpoint"},
    }
    cleaned = scrub_card(card)
    text = json.dumps(cleaned)
    assert leaks(cleaned) == []
    for forbidden in (
        "nanjaiyalathaa",
        "ewi.utwente.nl",
        "/home/",
        "/Users/",
        "@example.org",
        TOKEN,
    ):
        assert forbidden not in text
    assert "environment.user" in cleaned["unknown"]  # type: ignore[operator]
    assert "environment.host" in cleaned["unknown"]  # type: ignore[operator]
    assert "environment.home" in cleaned["unknown"]  # type: ignore[operator]
    assert "environment.token" in cleaned["unknown"]  # type: ignore[operator]
    for forbidden_key in ("updater", "ssh_key", "app_token", "token"):
        assert forbidden_key not in {key for key in cleaned if key != "unknown"}
