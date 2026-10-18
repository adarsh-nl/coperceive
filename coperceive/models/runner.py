# SPDX-License-Identifier: Apache-2.0
"""Start a plugin as a subprocess and speak protocol version 1 with it (spec §Environment
of the child, §Ordering, §Working directory).

The child is started from an argv list with ``shell=False`` in an environment built from
an empty map (the allow-list below, the manifest's secret names, and four values the parent
sets). Secret values are redacted from everything the parent writes: ``parent.log``,
``plugin.log`` (the child's stderr, copied line by line), error messages and exceptions.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import IO, NoReturn

from coperceive.errors import CoperceiveError
from coperceive.models.protocol import (
    Bye,
    Cancel,
    Capabilities,
    Error,
    Hello,
    LineReader,
    Message,
    Progress,
    ProtocolError,
    decode,
    encode,
)
from coperceive.models.spec import (
    Environment,
    Manifest,
    ModelSpec,
    ModelSpecError,
    render_argv,
)

ALLOWED_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "CUDA_VISIBLE_DEVICES",
    "CUDA_HOME",
    "NVIDIA_VISIBLE_DEVICES",
)
REDACTED = "[redacted]"
_MIN_SECRET_LEN = 4


class RunnerError(CoperceiveError):
    """A protocol violation, or a child that died before ``bye``."""


class RunnerTimeout(RunnerError):
    """The child did not answer within the allowed time."""


def child_environment(
    manifest: Manifest, *, plugin_dir: Path, work_dir: Path, parent: Mapping[str, str]
) -> dict[str, str]:
    """§Environment of the child, built from an empty map."""
    env: dict[str, str] = {}
    for name in ALLOWED_ENV:
        if name in parent:
            env[name] = parent[name]
    if manifest.environment.inherit_ld_library_path and "LD_LIBRARY_PATH" in parent:
        env["LD_LIBRARY_PATH"] = parent["LD_LIBRARY_PATH"]
    for name in manifest.environment.secrets:
        if name in parent:
            env[name] = parent[name]
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(plugin_dir)
    env["COPERCEIVE_MODEL_WORK"] = str(work_dir)
    env["COPERCEIVE_MODEL_PROTOCOL"] = "1"
    return env


def secret_values(manifest: Manifest, parent: Mapping[str, str]) -> dict[str, str]:
    """The secrets the child receives, by name, for redaction. Never written anywhere."""
    return {name: parent[name] for name in manifest.environment.secrets if name in parent}


def redact(text: str, secrets: Mapping[str, str]) -> str:
    """Every secret value of four or more characters becomes ``[redacted]``."""
    for value in sorted(secrets.values(), key=len, reverse=True):
        if len(value) >= _MIN_SECRET_LEN:
            text = text.replace(value, REDACTED)
    return text


def default_conda_envs() -> tuple[Path, ...]:
    roots: list[Path] = []
    for entry in os.environ.get("CONDA_ENVS_PATH", "").split(os.pathsep):
        if entry:
            roots.append(Path(entry))
    roots.append(Path.home() / ".conda" / "envs")
    return tuple(roots)


def resolve_interpreter(
    env: Environment, *, plugin_dir: Path, conda_envs: Sequence[Path] | None = None
) -> Path:
    """The interpreter ``environment`` names; nothing is activated."""
    if env.kind == "current":
        return Path(sys.executable)
    if env.kind == "container":
        raise ModelSpecError("environment.container: not implemented in this version")
    if env.kind == "python":
        assert env.value is not None
        path = Path(env.value)
        if not path.is_absolute():
            path = plugin_dir / path
        if not path.is_file():
            raise ModelSpecError(f"environment.python: no interpreter at {path}")
        return path
    assert env.kind == "conda" and env.value is not None
    candidates: list[Path] = []
    named = Path(env.value)
    if named.is_absolute() or "/" in env.value:
        candidates.append(named)
    else:
        roots = default_conda_envs() if conda_envs is None else tuple(conda_envs)
        candidates.extend(root / env.value for root in roots)
    for prefix in candidates:
        python = prefix / "bin" / "python"
        if python.is_file():
            return python
    tried = ", ".join(str(c / "bin" / "python") for c in candidates) or "nothing"
    raise ModelSpecError(f"environment.conda: no interpreter for {env.value!r}; tried {tried}")


class PluginProcess:
    """One child process speaking protocol version 1. Use as a context manager or ``close``."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        work_dir: Path,
        secrets: Mapping[str, str],
        parent_log: IO[str],
        plugin_log: IO[str],
    ) -> None:
        self._process = process
        self.work_dir = work_dir
        self.secrets = dict(secrets)
        self._parent_log = parent_log
        self._plugin_log = plugin_log
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._closed = False
        self._broken = False
        assert process.stdout is not None and process.stderr is not None
        self._reader = threading.Thread(
            target=self._pump_stdout, args=(process.stdout,), daemon=True, name="plugin-stdout"
        )
        self._errors = threading.Thread(
            target=self._pump_stderr, args=(process.stderr,), daemon=True, name="plugin-stderr"
        )
        self._reader.start()
        self._errors.start()
        self.hello: Hello = Hello("", "", (), _NO_CAPS, {}, None)

    # -- lifecycle -----------------------------------------------------------------------

    @classmethod
    def start(
        cls,
        manifest: Manifest,
        spec: ModelSpec,
        *,
        plugin_dir: Path,
        work_dir: Path,
        parent_env: Mapping[str, str],
        hello_timeout_s: float,
    ) -> PluginProcess:
        """Start the child, read ``hello`` and check §Ordering 1."""
        python = resolve_interpreter(manifest.environment, plugin_dir=plugin_dir)
        argv = render_argv(
            manifest,
            "batch",
            python=python,
            plugin_dir=plugin_dir,
            work_dir=work_dir,
            model=spec.key,
        )
        env = child_environment(
            manifest, plugin_dir=plugin_dir, work_dir=work_dir, parent=parent_env
        )
        secrets = secret_values(manifest, parent_env)
        work_dir.mkdir(parents=True, exist_ok=True)
        parent_log = (work_dir / "parent.log").open("a", encoding="utf-8")
        plugin_log = (work_dir / "plugin.log").open("a", encoding="utf-8")
        parent_log.write(f"start {redact(' '.join(argv), secrets)}\n")
        parent_log.flush()
        process = subprocess.Popen(  # noqa: S603 - argv list, no shell, allow-listed env
            argv,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(work_dir),
            start_new_session=True,
        )
        child = cls(
            process,
            work_dir=work_dir,
            secrets=secrets,
            parent_log=parent_log,
            plugin_log=plugin_log,
        )
        try:
            first = child.receive(hello_timeout_s)
        except RunnerError:
            child.close(grace_s=1.0)
            raise
        if not isinstance(first, Hello):
            child._violation(f"first message was {type(first).__name__}, not hello")
        if spec.key not in first.models:
            child._violation(f"hello.models {list(first.models)} does not include {spec.key!r}")
        if first.plugin != manifest.name or first.version != manifest.version:
            child._violation(
                f"hello names {first.plugin} {first.version}, manifest says "
                f"{manifest.name} {manifest.version}"
            )
        pinned = spec.checkpoint_md5
        reported = first.environment["checkpoint_md5"]
        if pinned is not None and pinned != reported:
            child._violation(f"checkpoint_md5 {reported} differs from the manifest's {pinned}")
        child.hello = first
        return child

    def __enter__(self) -> PluginProcess:
        return self

    def __exit__(self, *_: object) -> None:
        self.close(grace_s=10.0)

    def poll(self) -> int | None:
        return self._process.poll()

    # -- messages ------------------------------------------------------------------------

    def send(self, message: Message) -> None:
        if self._closed:
            raise RunnerError("the child is closed")
        line = encode(message)
        self._log("> " + line.decode("utf-8").rstrip("\n"))
        stdin = self._process.stdin
        assert stdin is not None
        try:
            stdin.write(line)
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._broken = True
            raise RunnerError(f"the child closed its stdin: {exc}") from None

    def receive(self, timeout_s: float) -> Message:
        """The next message; a non-protocol line or an early exit closes the child."""
        if self._broken:
            raise RunnerError("the child is gone")
        try:
            raw = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            raise RunnerTimeout(f"no message from the child within {timeout_s:g} s") from None
        if raw is None:
            code = self._process.wait(timeout=5.0)
            self._broken = True
            raise RunnerError(f"the child exited with code {code} before bye")
        self._log("< " + raw.decode("utf-8", errors="replace").rstrip("\n"))
        try:
            message = decode(raw)
        except ProtocolError as exc:
            shown = redact(raw.decode("utf-8", errors="replace").rstrip("\n"), self.secrets)
            self._violation(f"{exc} (line: {shown[:200]!r})")
        if isinstance(message, Error):
            return replace(message, message=redact(message.message, self.secrets))
        if isinstance(message, Progress):
            return replace(message, text=redact(message.text, self.secrets))
        return message

    def cancel(self, frame_id: int | None) -> None:
        self.send(Cancel(id=frame_id))

    def close(self, grace_s: float) -> int:
        """``bye``, then SIGTERM after ``grace_s``, then SIGKILL; returns the exit code."""
        if self._closed:
            return self._process.returncode if self._process.returncode is not None else -1
        self._closed = True
        process = self._process
        if process.poll() is None and not self._broken:
            try:
                stdin = process.stdin
                assert stdin is not None
                stdin.write(encode(Bye()))
                stdin.flush()
                stdin.close()
                self._log("> bye")
            except (BrokenPipeError, OSError):
                pass
        code = _wait(process, grace_s)
        if code is None:
            self._log(f"sigterm after {grace_s:g} s")
            _signal(process, signal.SIGTERM)
            code = _wait(process, grace_s)
        if code is None:
            self._log(f"sigkill after {grace_s:g} s")
            _signal(process, signal.SIGKILL)
            code = _wait(process, max(grace_s, 5.0))
        if code is None:
            code = -9
        self._reader.join(timeout=2.0)
        self._errors.join(timeout=2.0)
        self._log(f"exit {code}")
        for handle in (self._parent_log, self._plugin_log):
            try:
                handle.close()
            except OSError:
                pass
        return code

    # -- internals -----------------------------------------------------------------------

    def _violation(self, reason: str) -> NoReturn:
        text = redact(reason, self.secrets)
        self._log(f"protocol violation: {text}")
        self._broken = True
        try:
            self.close(grace_s=1.0)
        finally:
            raise RunnerError(f"protocol violation: {text}")

    def _log(self, text: str) -> None:
        try:
            self._parent_log.write(redact(text, self.secrets) + "\n")
            self._parent_log.flush()
        except (OSError, ValueError):
            pass

    def _pump_stdout(self, stream: IO[bytes]) -> None:
        reader = LineReader(stream)
        try:
            while True:
                try:
                    line = reader.readline()
                except ProtocolError as exc:
                    self._lines.put(f"{exc}".encode() + b"\n")  # decode will reject it
                    continue
                if line is None:
                    break
                self._lines.put(line)
        finally:
            self._lines.put(None)

    def _pump_stderr(self, stream: IO[bytes]) -> None:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\n")
            try:
                self._plugin_log.write(redact(text, self.secrets) + "\n")
                self._plugin_log.flush()
            except (OSError, ValueError):
                break


_NO_CAPS = Capabilities(live=False, cancel=False, max_inflight=1)


def _wait(process: subprocess.Popen[bytes], seconds: float) -> int | None:
    deadline = time.monotonic() + seconds
    while True:
        code = process.poll()
        if code is not None:
            return code
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def _signal(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)  # the child's own session (start_new_session)
    except ProcessLookupError:
        pass
    except PermissionError:
        process.send_signal(sig)


__all__ = [
    "ALLOWED_ENV",
    "REDACTED",
    "PluginProcess",
    "RunnerError",
    "RunnerTimeout",
    "child_environment",
    "redact",
    "resolve_interpreter",
    "secret_values",
]
