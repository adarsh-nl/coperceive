# SPDX-License-Identifier: Apache-2.0
"""The subprocess runner: environment allow-list, hello, frames, timeouts, cancel, redaction."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from coperceive.models.protocol import (
    AgentRef,
    Configure,
    Configured,
    Error,
    Frame,
    Result,
)
from coperceive.models.runner import (
    PluginProcess,
    RunnerError,
    RunnerTimeout,
    child_environment,
    redact,
    resolve_interpreter,
)
from coperceive.models.spec import (
    Environment,
    ModelSpecError,
    load_manifest,
    parse_manifest,
    select_model,
)

from tests.fake_model_plugin import MANIFEST as FAKE

POSE = {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "tx": 10.0, "ty": 2.0, "tz": 0.5}


def _parent_env(**extra: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    env.update(extra)
    return env


def _manifest(secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    import yaml

    payload = cast(dict[str, Any], yaml.safe_load(FAKE.read_text(encoding="utf-8")))
    if secrets:
        payload["environment"]["secrets"] = list(secrets)
    return payload


def test_child_environment_allow_list(tmp_path: Path) -> None:
    manifest = parse_manifest(_manifest(("HF_TOKEN",)), source=FAKE)
    parent = {
        "PATH": "/p",
        "HOME": "/h",
        "PYTHONPATH": "/x",
        "SECRET_X": "1",
        "HF_TOKEN": "t",
        "LD_LIBRARY_PATH": "/l",
    }
    env = child_environment(manifest, plugin_dir=FAKE.parent, work_dir=tmp_path, parent=parent)
    assert env["PATH"] == "/p" and env["HOME"] == "/h"
    assert env["PYTHONPATH"] == str(FAKE.parent)
    assert env["HF_TOKEN"] == "t"
    assert "SECRET_X" not in env and "LD_LIBRARY_PATH" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["COPERCEIVE_MODEL_WORK"] == str(tmp_path)
    assert env["COPERCEIVE_MODEL_PROTOCOL"] == "1"


def test_ld_library_path_only_when_asked(tmp_path: Path) -> None:
    payload = _manifest()
    payload["environment"]["inherit_ld_library_path"] = True
    manifest = parse_manifest(payload, source=FAKE)
    env = child_environment(
        manifest, plugin_dir=FAKE.parent, work_dir=tmp_path, parent={"LD_LIBRARY_PATH": "/l"}
    )
    assert env["LD_LIBRARY_PATH"] == "/l"


def test_resolve_conda_name(tmp_path: Path) -> None:
    env = Environment("conda", "griffin", (), (), False)
    with pytest.raises(ModelSpecError, match="griffin"):
        resolve_interpreter(env, plugin_dir=tmp_path, conda_envs=(tmp_path / "envs",))
    python = tmp_path / "envs" / "griffin" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    assert resolve_interpreter(env, plugin_dir=tmp_path, conda_envs=(tmp_path / "envs",)) == python
    prefix = Environment("conda", str(tmp_path / "envs" / "griffin"), (), (), False)
    assert resolve_interpreter(prefix, plugin_dir=tmp_path, conda_envs=()) == python


def test_container_not_implemented(tmp_path: Path) -> None:
    env = Environment("container", "image.sif", (), (), False, "singularity")
    with pytest.raises(ModelSpecError, match="not implemented"):
        resolve_interpreter(env, plugin_dir=tmp_path)


def test_redact_replaces_every_value_of_four_or_more_characters() -> None:
    text = "token hunter22 and short ab and again hunter22"
    assert redact(text, {"A": "hunter22", "B": "ab"}) == (
        "token [redacted] and short ab and again [redacted]"
    )


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def _start(
    work: Path, *, parent_env: Mapping[str, str] | None = None, secrets: tuple[str, ...] = ()
) -> PluginProcess:
    manifest = parse_manifest(_manifest(secrets), source=FAKE) if secrets else load_manifest(FAKE)
    return PluginProcess.start(
        manifest,
        select_model(manifest, None),
        plugin_dir=FAKE.parent,
        work_dir=work,
        parent_env=parent_env or _parent_env(),
        hello_timeout_s=30.0,
    )


def _configure(scene_dir: Path, work: Path, misbehave: str = "") -> Configure:
    return Configure(
        model="default",
        scene_id="coop",
        agents=(AgentRef("vehicle", "vehicle"),),
        source="single",
        options={"extra": False, "misbehave": misbehave},
        work_dir=str(work),
        scene_dir=str(scene_dir),
        raw_root=None,
        artifacts={},
        device="cpu",
        sensors=(),
        samples=(100_000,),
    )


def _frame(frame_id: int = 1) -> Frame:
    return Frame(
        id=frame_id, scene_id="coop", time_us=100_000, poses={"vehicle": POSE}, images=(), points=()
    )


@pytest.fixture
def process(work: Path) -> Iterator[PluginProcess]:
    child = _start(work)
    yield child
    child.close(grace_s=2.0)


def test_start_reads_hello(process: PluginProcess) -> None:
    assert process.hello.plugin == "fake"
    assert process.hello.models == ("default", "known-answer", "a", "b")
    assert len(process.hello.environment["checkpoint_md5"]) == 32


def test_configure_and_one_frame(process: PluginProcess, tmp_path: Path) -> None:
    process.send(_configure(tmp_path, tmp_path / "work"))
    assert process.receive(timeout_s=10.0) == Configured(scene_id="coop")
    process.send(_frame())
    result = process.receive(timeout_s=10.0)
    assert isinstance(result, Result) and result.id == 1
    assert len(result.boxes) == 1
    box = result.boxes[0]
    assert box.agent_id == "vehicle" and box.source == "single" and box.category == "car"
    assert box.center == (15.0, 2.0, 0.5)  # the pose translation plus (5, 0, 0)
    assert process.close(grace_s=2.0) == 0
    assert (tmp_path / "work" / "parent.log").is_file()


def test_stray_stdout_is_a_protocol_violation(process: PluginProcess, tmp_path: Path) -> None:
    process.send(_configure(tmp_path, tmp_path / "work", misbehave="stray-stdout"))
    assert isinstance(process.receive(timeout_s=10.0), Configured)
    process.send(_frame())
    with pytest.raises(RunnerError, match="protocol"):
        process.receive(timeout_s=10.0)
    assert process.poll() is not None  # the runner closed the child


def test_no_exit_is_killed(work: Path, tmp_path: Path) -> None:
    child = _start(work)
    child.send(_configure(tmp_path, work, misbehave="no-exit"))
    assert isinstance(child.receive(timeout_s=10.0), Configured)
    started = time.monotonic()
    code = child.close(grace_s=0.3)
    assert code < 0, code  # killed by a signal
    assert time.monotonic() - started < 5.0
    assert child.poll() is not None


def test_frame_timeout(process: PluginProcess, tmp_path: Path) -> None:
    process.send(_configure(tmp_path, tmp_path / "work", misbehave="sleep"))
    assert isinstance(process.receive(timeout_s=10.0), Configured)
    process.send(_frame())
    with pytest.raises(RunnerTimeout):
        process.receive(timeout_s=0.5)


def test_cancel_is_answered(process: PluginProcess, tmp_path: Path) -> None:
    process.send(_configure(tmp_path, tmp_path / "work", misbehave="sleep"))
    assert isinstance(process.receive(timeout_s=10.0), Configured)
    process.send(_frame())
    time.sleep(0.05)
    process.cancel(1)
    started = time.monotonic()
    answer = process.receive(timeout_s=5.0)
    assert answer == Error(id=1, code="cancelled", message="cancelled by the parent")
    assert time.monotonic() - started < 2.0


def test_secret_values_are_redacted_from_logs(work: Path, tmp_path: Path) -> None:
    """A token reaches the child only by name; its value never shows up in the error the
    parent returns, in parent.log, in plugin.log or in the exception text."""
    child = _start(work, parent_env=_parent_env(FAKE_SECRET="hunter22"), secrets=("FAKE_SECRET",))
    assert child.secrets == {"FAKE_SECRET": "hunter22"}
    child.send(_configure(tmp_path, work, misbehave="secret"))
    assert isinstance(child.receive(timeout_s=10.0), Configured)
    child.send(_frame())
    error = child.receive(timeout_s=10.0)
    assert isinstance(error, Error) and error.code == "frame_failed"
    assert "[redacted]" in error.message and "hunter22" not in error.message
    with pytest.raises(RunnerError) as caught:  # the stray line after the error carries it too
        child.receive(timeout_s=10.0)
    assert "hunter22" not in str(caught.value) and "[redacted]" in str(caught.value)
    child.close(grace_s=2.0)
    for name in ("parent.log", "plugin.log"):
        text = (work / name).read_text(encoding="utf-8")
        assert "hunter22" not in text, name
        assert "[redacted]" in text, name
