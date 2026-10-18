# SPDX-License-Identifier: Apache-2.0
"""``coperceive model ...`` through ``coperceive.cli.main`` with the fake plugin."""

from __future__ import annotations

from dataclasses import replace
from importlib import metadata
from pathlib import Path

import pytest
from coperceive.cli import main
from coperceive.evaluation.predictions import read_run_meta
from coperceive.models import registry
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE
from tests.test_model_spec import _fake_entry_points  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def scenes(tmp_path: Path) -> Path:
    root = tmp_path / "scenes"
    write_scene(replace(cooperative_scene(), scene_id="coop"), root / "coop")
    return root


def _run_args(scenes: Path, out: Path, run_id: str = "r1", *extra: str) -> list[str]:
    return [
        "model",
        "run",
        "--manifest",
        str(FAKE),
        "--scenes",
        str(scenes),
        "--out",
        str(out),
        "--run-id",
        run_id,
        *extra,
    ]


def test_model_list(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)
    registry.clear_cache()
    main(["model", "list"])
    out = capsys.readouterr().out
    assert "fake" in out and "entry-point" in out
    registry.clear_cache()


def test_model_inspect(capsys: pytest.CaptureFixture[str]) -> None:
    main(["model", "inspect", "--manifest", str(FAKE)])
    out = capsys.readouterr().out
    assert "environment:" in out and "kind: current" in out
    assert out.strip().splitlines()[-1].startswith("interpreter: /")


def test_model_check_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    main(["model", "check", "--manifest", str(FAKE), "--model", "default"])
    out = capsys.readouterr().out
    assert "check fake:" in out and " 0 fail" in out


def test_model_check_fails_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "model",
                "check",
                "--manifest",
                str(FAKE),
                "--model",
                "default",
                "--option",
                "misbehave=stray-stdout",
            ]
        )
    assert caught.value.code == 1
    assert "fail" in capsys.readouterr().out


def test_run_then_eval_chain(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "predictions"
    main(_run_args(scenes, out))
    text = capsys.readouterr().out
    assert f"wrote {out / 'r1'}" in text
    assert "scene coop: 7 frames, 0 cached, 10 boxes" in text
    assert "scenes 1 frames 7 cached 0 boxes 10" in text
    main(["eval", "run", "r1", "--root", str(scenes), "--predictions", str(out)])
    assert "evaluated 1" in capsys.readouterr().out
    main(["eval", "report", "r1", "--root", str(scenes), "--predictions", str(out)])
    assert "coop" in capsys.readouterr().out
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None and provenance["status"] == "complete"


def test_existing_run_needs_force(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "predictions"
    main(_run_args(scenes, out))
    capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        main(_run_args(scenes, out))
    assert caught.value.code == 1
    assert "use --force" in capsys.readouterr().err
    main(_run_args(scenes, out, "r1", "--force"))
    assert "1 cached" in capsys.readouterr().out


def test_unknown_plugin_lists_known(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)
    registry.clear_cache()
    with pytest.raises(SystemExit) as caught:
        main(["model", "inspect", "nope"])
    assert caught.value.code == 1
    assert "known:" in capsys.readouterr().err
    registry.clear_cache()


def test_bad_option_named(scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(_run_args(scenes, tmp_path / "p", "r1", "--option", "bogus=1"))
    assert caught.value.code == 1
    assert "bogus" in capsys.readouterr().err


def test_timeout_exits_4(scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "predictions"
    with pytest.raises(SystemExit) as caught:
        main(_run_args(scenes, out, "r1", "--option", "misbehave=sleep", "--run-timeout", "0.5"))
    assert caught.value.code == 4
    assert "timeout" in capsys.readouterr().err
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None and provenance["status"] == "partial"


def test_protocol_violation_exits_3(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(_run_args(scenes, tmp_path / "p", "r1", "--option", "misbehave=stray-stdout"))
    assert caught.value.code == 3
    assert "plugin_error" in capsys.readouterr().err


def test_cache_prune_prints_count(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "predictions"
    main(_run_args(scenes, out))
    capsys.readouterr()
    main(["model", "cache", "prune", "--out", str(out), "--run-id", "r1"])
    assert capsys.readouterr().out.strip() == "pruned 1"
    main(["model", "cache", "prune", "--out", str(out)])
    assert capsys.readouterr().out.strip() == "pruned 0"


def test_model_inspect_prints_raw_templates_as_text(capsys: pytest.CaptureFixture[str]) -> None:
    """A manifest with raw templates (the Griffin plugin) dumps cleanly: templates as text."""
    griffin = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "griffin"
        / "coperceive_griffin_model"
        / "model.yaml"
    )
    main(["model", "inspect", "--manifest", str(griffin)])
    out = capsys.readouterr().out
    assert "- vehicle-side/pose/{frame:06d}.json" in out
    assert "frozenset" not in out and "checkpoint_md5: 1201f5692e390a75e5b5ab0efa0cae19" in out
    assert out.strip().splitlines()[-1].startswith("interpreter:")
