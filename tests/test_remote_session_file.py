# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false
"""The Remote viewer's session file cannot be rewritten by a folder name.

The Slurm job writes ``~/.coperceive/sessions/<uuid>.json``; the login-node
manager reads ``ip`` and ``port`` from it and sends the session's bearer token
there. These tests run the real ``remote_serve.sbatch`` with stand-ins for the
cluster tools (``hostname``, the server) and check that a data root, whatever
its name, is stored as data, and that values with control characters or a
session id that is not a plain file name are refused before anything is
written or submitted.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from coperceive.remote.manager import (
    RemoteSession,
    get_status,
    record_heartbeat,
    start_session,
    stop_session,
)

REPO = Path(__file__).resolve().parent.parent
SBATCH = REPO / "scripts" / "desktop" / "remote_serve.sbatch"

NODE_IP = "10.0.0.5"
JOB_ID = 4242
NODE = "node7"

# A folder name that, written into JSON without escaping, adds its own "ip" and
# "port" keys after the real ones (json.loads keeps the last duplicate).
HOSTILE = '.d", "ip": "127.0.0.1", "port": 40000, "z": "'

needs_bash = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="needs bash"
)

FAKE_SERVE = """\
import json, os, shutil, stat, sys, time
capture = os.environ["FAKE_CAPTURE_DIR"]
with open(os.path.join(capture, "serve_argv.json"), "w", encoding="utf-8") as out:
    json.dump(sys.argv[1:], out)
session = os.path.join(
    os.environ["HOME"], ".coperceive", "sessions", os.environ["COPERCEIVE_REMOTE_UUID"] + ".json"
)
deadline = time.time() + 20
while time.time() < deadline:
    if os.path.isfile(session) and os.path.getsize(session) > 0:
        time.sleep(0.3)
        mode = stat.S_IMODE(os.stat(session).st_mode)
        with open(os.path.join(capture, "session_mode"), "w", encoding="utf-8") as out:
            out.write(oct(mode))
        shutil.copyfile(session, os.path.join(capture, "session.json"))
        sys.exit(0)
    time.sleep(0.05)
sys.exit(3)
"""


def _write_exe(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _job_env(tmp_path: Path, data_root: str, session_id: str) -> tuple[dict[str, str], Path]:
    """Environment for one run of the real sbatch script, with fake cluster tools."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    capture = tmp_path / "capture"
    capture.mkdir(exist_ok=True)
    home = tmp_path / "home"
    (home / ".coperceive" / "tokens").mkdir(parents=True, exist_ok=True)
    token_file = home / ".coperceive" / "tokens" / "session.token"
    token_file.write_text("0" * 32 + "\n", encoding="utf-8")
    token_file.chmod(0o600)

    real_python = sys.executable
    fake_serve = tmp_path / "fake_serve.py"
    fake_serve.write_text(FAKE_SERVE, encoding="utf-8")
    _write_exe(
        fakebin / "hostname",
        '#!/bin/sh\nif [ "${1:-}" = "-I" ]; then echo "10.0.0.5 10.0.0.6"; else echo node-x; fi\n',
    )
    _write_exe(fakebin / "python3", f'#!/bin/sh\nexec "{real_python}" "$@"\n')
    interpreter = fakebin / "python-profile"
    _write_exe(
        interpreter,
        "#!/bin/sh\n"
        'if [ "${1:-}" = "-m" ] && [ "${2:-}" = "coperceive" ]; then\n'
        f'    touch "{capture}/serve_started"\n'
        f'    exec "{real_python}" "{fake_serve}" "$@"\n'
        "fi\n"
        f'exec "{real_python}" "$@"\n',
    )
    env = {
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "HOME": str(home),
        "COPERCEIVE_REMOTE_UUID": session_id,
        "COPERCEIVE_REMOTE_ROOT": data_root,
        "COPERCEIVE_REMOTE_TOKEN_FILE": str(token_file),
        "COPERCEIVE_REMOTE_PYTHON": str(interpreter),
        "COPERCEIVE_HEARTBEAT_TIMEOUT": "0",
        "SLURM_JOB_ID": str(JOB_ID),
        "SLURMD_NODENAME": NODE,
        "FAKE_CAPTURE_DIR": str(capture),
    }
    return env, capture


def _run_job(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SBATCH)],
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )


@needs_bash
@pytest.mark.parametrize(
    "name",
    [
        HOSTILE,
        'back\\slash "quoted" dir',
        "plain scenes",
        "données-ünïcode",
    ],
    ids=["injected-keys", "quote-and-backslash", "space", "unicode"],
)
def test_job_writes_the_data_root_into_the_session_file_as_data(tmp_path: Path, name: str) -> None:
    data_root = tmp_path / "lab" / name
    data_root.mkdir(parents=True)
    env, capture = _job_env(tmp_path, str(data_root), "55bef61ccf2f60b8")

    done = _run_job(env)

    assert done.returncode == 0, done.stderr
    raw = (capture / "session.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    serve_argv: list[str] = json.loads((capture / "serve_argv.json").read_text(encoding="utf-8"))
    port = int(serve_argv[serve_argv.index("--port") + 1])
    assert serve_argv[serve_argv.index("--root") + 1] == str(data_root)
    assert (data["ip"], data["port"]) == (NODE_IP, port), "the folder name set ip/port"
    assert set(data) == {
        "uuid",
        "job_id",
        "node",
        "ip",
        "port",
        "pid",
        "started_at",
        "last_heartbeat",
        "data_root",
        "token_file",
    }
    assert data["ip"] == NODE_IP
    assert data["port"] == port
    assert data["data_root"] == str(data_root)
    assert data["token_file"] == env["COPERCEIVE_REMOTE_TOKEN_FILE"]
    assert (data["uuid"], data["job_id"], data["node"]) == ("55bef61ccf2f60b8", JOB_ID, NODE)
    assert (capture / "session_mode").read_text(encoding="utf-8") == oct(0o600)

    # What the login-node manager does with it: the token goes to this address.
    session = RemoteSession.from_file(capture / "session.json")
    assert session is not None
    assert (session.ip, session.port, session.data_root) == (NODE_IP, port, str(data_root))


@needs_bash
@pytest.mark.parametrize(
    "session_id", ["55bef61ccf2f60b8", str(uuid.uuid4()), "test-uuid-1234", "Run_2.v1"]
)
def test_job_accepts_ordinary_session_ids(tmp_path: Path, session_id: str) -> None:
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    env, capture = _job_env(tmp_path, str(data_root), session_id)
    done = _run_job(env)
    assert done.returncode == 0, done.stderr
    data = json.loads((capture / "session.json").read_text(encoding="utf-8"))
    assert data["uuid"] == session_id


@needs_bash
@pytest.mark.parametrize("bad", ["\n", "\r", "\x1b", "\t"], ids=["LF", "CR", "ESC", "TAB"])
def test_job_refuses_a_data_root_with_control_characters(tmp_path: Path, bad: str) -> None:
    data_root = tmp_path / f"scenes{bad}ip"
    data_root.mkdir()
    env, capture = _job_env(tmp_path, str(data_root), "55bef61ccf2f60b8")

    done = _run_job(env)

    assert done.returncode != 0, done.stdout
    assert "control character" in done.stderr
    assert not (capture / "serve_started").exists(), "the server must not start"
    assert not (capture / "session.json").exists()


@needs_bash
@pytest.mark.parametrize(
    "session_id",
    ["../escape", "a/b", "a b", "x\ny", 'x"y', "a\\b", "x" * 129],
    ids=["dotdot", "slash", "space", "newline", "quote", "backslash", "too-long"],
)
def test_job_refuses_a_session_id_that_is_not_a_plain_name(tmp_path: Path, session_id: str) -> None:
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    env, capture = _job_env(tmp_path, str(data_root), session_id)

    done = _run_job(env)

    assert done.returncode != 0, done.stdout
    assert "session id" in done.stderr
    assert not (capture / "serve_started").exists(), "the server must not start"
    coperceive_dir = Path(env["HOME"]) / ".coperceive"
    assert not (coperceive_dir / "escape.json").exists()
    assert not (coperceive_dir / "escape.heartbeat").exists()


@needs_bash
def test_job_passes_media_roots_and_viewer_page_as_separate_arguments(tmp_path: Path) -> None:
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    media_a = tmp_path / "raw a"
    media_b = tmp_path / 'raw "b" \\ c'
    static = tmp_path / "static page"
    for folder in (media_a, media_b, static):
        folder.mkdir()
    (static / "index.html").write_text("<html></html>\n", encoding="utf-8")
    env, capture = _job_env(tmp_path, str(data_root), "55bef61ccf2f60b8")
    env["COPERCEIVE_REMOTE_MEDIA_ROOTS"] = f"{media_a}\n{media_b}"
    env["COPERCEIVE_REMOTE_STATIC"] = str(static)

    done = _run_job(env)

    assert done.returncode == 0, done.stderr
    argv: list[str] = json.loads((capture / "serve_argv.json").read_text(encoding="utf-8"))
    assert argv[argv.index("--static") + 1] == str(static)
    roots = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--media-root"]
    assert roots == [str(media_a), str(media_b)]


@needs_bash
@pytest.mark.parametrize("variable", ["COPERCEIVE_REMOTE_MEDIA_ROOTS", "COPERCEIVE_REMOTE_STATIC"])
def test_job_refuses_media_and_page_paths_with_control_characters(
    tmp_path: Path, variable: str
) -> None:
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    bad = tmp_path / "dir\rx\x1b"
    bad.mkdir()
    (bad / "index.html").write_text("<html></html>\n", encoding="utf-8")
    env, capture = _job_env(tmp_path, str(data_root), "55bef61ccf2f60b8")
    env[variable] = str(bad)

    done = _run_job(env)

    assert done.returncode != 0, done.stdout
    assert "control character" in done.stderr
    assert not (capture / "serve_started").exists(), "the server must not start"


# --- coperceive remote start (login node) ---------------------------------------


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )
    monkeypatch.setattr("coperceive.remote.manager.get_logs_dir", lambda: coperceive_dir / "logs")
    monkeypatch.setattr("shutil.which", lambda prog: f"/usr/bin/{prog}")
    return coperceive_dir


def _record_submissions(
    monkeypatch: pytest.MonkeyPatch, *, job_id: int = 4242, state: str = ""
) -> list[dict[str, Any]]:
    """Fakes sbatch/squeue and returns the list of submissions (argv and env).

    By default squeue no longer lists the job, so start returns at once.
    """
    submitted: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0].endswith("sbatch"):
            submitted.append({"cmd": list(cmd), "env": dict(kwargs.get("env") or {})})
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{job_id}\n", stderr="")
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{state}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return submitted


@pytest.mark.parametrize(
    "session_id",
    ["../escape", "a/b", "a\\b", "x\x00y", "x\ny", "a b", 'x"y', "x" * 129],
    ids=["dotdot", "slash", "backslash", "nul", "newline", "space", "quote", "too-long"],
)
def test_start_refuses_a_session_id_that_is_not_a_plain_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> None:
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    submitted = _record_submissions(monkeypatch)
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    script = tmp_path / "fake.sbatch"
    script.touch()

    res = start_session(
        data_root=data_root,
        partition="main-cpu",
        session_uuid=session_id,
        sbatch_script=script,
        wait_timeout=0,
    )

    assert res["status"] == "error", res
    assert "session id" in res["error"]
    assert submitted == [], "nothing may be submitted"
    # No token file was written anywhere, in particular not outside tokens/.
    assert not (coperceive_dir / "escape.token").exists()
    written = list(coperceive_dir.rglob("*.token")) if coperceive_dir.exists() else []
    assert written == []


@pytest.mark.parametrize(
    "session_id", ["55bef61ccf2f60b8", str(uuid.uuid4()), "test-uuid-1234", "Run_2.v1"]
)
def test_start_accepts_ordinary_session_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> None:
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    submitted = _record_submissions(monkeypatch)
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    script = tmp_path / "fake.sbatch"
    script.touch()

    res = start_session(
        data_root=data_root,
        partition="main-cpu",
        session_uuid=session_id,
        sbatch_script=script,
        wait_timeout=0,
    )

    assert res["status"] != "error", res
    assert res["uuid"] == session_id
    assert submitted[0]["env"]["COPERCEIVE_REMOTE_UUID"] == session_id
    assert (coperceive_dir / "tokens" / f"{session_id}.token").is_file()


@pytest.mark.parametrize(
    "name",
    [HOSTILE, 'back\\slash "quoted" dir', "plain scenes", "données-ünïcode"],
    ids=["injected-keys", "quote-and-backslash", "space", "unicode"],
)
def test_start_passes_an_unusual_but_valid_data_root_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Quotes, backslashes, spaces and non-ASCII letters are valid folder names."""
    _isolate(tmp_path, monkeypatch)
    submitted = _record_submissions(monkeypatch)
    data_root = tmp_path / "lab" / name
    data_root.mkdir(parents=True)
    link = tmp_path / "data"
    link.symlink_to(data_root)
    script = tmp_path / "fake.sbatch"
    script.touch()

    res = start_session(data_root=link, partition="main-cpu", sbatch_script=script, wait_timeout=0)

    assert res["status"] != "error", res
    assert submitted[0]["env"]["COPERCEIVE_REMOTE_ROOT"] == str(data_root.resolve())


@pytest.mark.parametrize(
    "bad", ["\n", "\r", "\x1b", "\t", "\x85"], ids=["LF", "CR", "ESC", "TAB", "NEL"]
)
def test_start_refuses_control_characters_in_values_sent_to_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _isolate(tmp_path, monkeypatch)
    submitted = _record_submissions(monkeypatch)
    script = tmp_path / "fake.sbatch"
    script.touch()
    good_root = tmp_path / "scenes"
    good_root.mkdir()
    bad_dir = tmp_path / f"dir{bad}x"
    bad_dir.mkdir()
    (bad_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
    link = tmp_path / "innocent"
    link.symlink_to(bad_dir)

    def start(**kwargs: Any) -> dict[str, Any]:
        options: dict[str, Any] = {
            "data_root": good_root,
            "partition": "main-cpu",
            "sbatch_script": script,
            "wait_timeout": 0,
        }
        options.update(kwargs)
        return start_session(**options)

    for label, res in [
        ("data root", start(data_root=bad_dir)),
        ("data root behind a symlink", start(data_root=link)),
        ("viewer page directory", start(static_dir=bad_dir)),
        ("media root", start(media_roots=[str(bad_dir)])),
        ("python", start(python=f"/usr/bin/python3{bad}")),
        ("extra args", start(extra_args=[f"--flag{bad}"])),
    ]:
        assert res["status"] == "error", (label, res)
        assert "control character" in res["error"], (label, res)
    assert submitted == [], "nothing may be submitted"

    # The same call with ordinary values is submitted.
    assert start()["status"] != "error"
    assert len(submitted) == 1


def test_start_ignores_a_session_file_left_by_another_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is only sent to an address the job just started wrote."""
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    _record_submissions(monkeypatch, job_id=5000, state="RUNNING|None")
    probed: list[tuple[str, int]] = []

    def fake_probe(ip: str, port: int, token: str, timeout: float = 3.0) -> bool:
        probed.append((ip, port))
        return True

    monkeypatch.setattr("coperceive.remote.manager.probe_health", fake_probe)
    data_root = tmp_path / "scenes"
    data_root.mkdir()
    script = tmp_path / "fake.sbatch"
    script.touch()
    stale = coperceive_dir / "sessions" / "reused-id.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        json.dumps(
            {
                "uuid": "reused-id",
                "job_id": 4999,
                "node": "other",
                "ip": "192.0.2.66",
                "port": 40000,
                "pid": 1,
                "started_at": 1,
                "data_root": str(data_root),
                "token_file": str(coperceive_dir / "tokens" / "reused-id.token"),
            }
        ),
        encoding="utf-8",
    )

    res = start_session(
        data_root=data_root,
        partition="main-cpu",
        session_uuid="reused-id",
        sbatch_script=script,
        wait_timeout=1,
    )

    assert probed == [], "the token went to an address from another job's session file"
    assert res["status"] == "pending", res
    assert stat.S_IMODE((coperceive_dir / "tokens" / "reused-id.token").stat().st_mode) == 0o600


# --- non-ASCII folder names are not control characters ---------------------------

# Their UTF-8 bytes include 0x80-0x9F (C1 control code points if read as Latin-1):
# Ä = c3 84, ß = c3 9f, … = e2 80 a6, デ = e3 83 87, 数 = e6 95 b0. The check must
# look at decoded characters (Unicode category Cc), not at raw bytes.
NON_ASCII_NAMES = ["Äpfel", "Grüße", "naïve…", "データ", "数据集", "Ünïcödé — 日本"]


def test_non_ascii_names_hold_c1_bytes_in_utf8() -> None:
    """The guard below is meaningful: each name has a byte in 0x80-0x9F."""
    for name in NON_ASCII_NAMES:
        assert any(0x80 <= byte <= 0x9F for byte in name.encode("utf-8")), name


@pytest.mark.parametrize("name", NON_ASCII_NAMES)
def test_start_accepts_non_ascii_folder_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _isolate(tmp_path, monkeypatch)
    submitted = _record_submissions(monkeypatch)
    data_root = tmp_path / name
    data_root.mkdir()
    static = tmp_path / f"{name} page"
    static.mkdir()
    (static / "index.html").write_text("<html></html>\n", encoding="utf-8")
    script = tmp_path / "fake.sbatch"
    script.touch()

    res = start_session(
        data_root=data_root,
        partition="main-cpu",
        sbatch_script=script,
        wait_timeout=0,
        static_dir=static,
        media_roots=[str(data_root)],
        extra_args=[f"--label={name}"],
    )

    assert res["status"] != "error", res
    env = submitted[0]["env"]
    assert env["COPERCEIVE_REMOTE_ROOT"] == str(data_root.resolve())
    assert env["COPERCEIVE_REMOTE_STATIC"] == str(static.resolve())


@needs_bash
@pytest.mark.parametrize("name", NON_ASCII_NAMES)
def test_job_accepts_non_ascii_folder_names(tmp_path: Path, name: str) -> None:
    data_root = tmp_path / name
    data_root.mkdir()
    env, capture = _job_env(tmp_path, str(data_root), "55bef61ccf2f60b8")
    env["COPERCEIVE_REMOTE_MEDIA_ROOTS"] = str(data_root)

    done = _run_job(env)

    assert done.returncode == 0, done.stderr
    data = json.loads((capture / "session.json").read_text(encoding="utf-8"))
    assert data["data_root"] == str(data_root)


# --- status, stop and heartbeat take the same session id as start ------------------

BAD_IDS = ["../escape", "../../escape", "a/b", "a\\b", "x\x00y", "x\ny", "a b", "x" * 129]
BAD_ID_NAMES = ["dotdot", "dotdot2", "slash", "backslash", "nul", "newline", "space", "too-long"]


def _plant_outside_session(coperceive_dir: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A session-shaped file outside sessions/, naming a file the user cares about."""
    victim = tmp_path / "precious.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    sessions = coperceive_dir / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    planted = coperceive_dir / "escape.json"
    planted.write_text(
        json.dumps(
            {
                "uuid": "escape",
                "job_id": 777,
                "node": "n",
                "ip": "192.0.2.66",
                "port": 40000,
                "pid": 1,
                "started_at": 1,
                "data_root": str(tmp_path),
                "token_file": str(victim),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "escape.json").write_text(planted.read_text(encoding="utf-8"), encoding="utf-8")
    return planted, victim


def _record_calls(monkeypatch: pytest.MonkeyPatch, squeue_out: str = "") -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout=squeue_out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("session_id", BAD_IDS, ids=BAD_ID_NAMES)
def test_status_stop_and_heartbeat_refuse_a_session_id_that_is_not_a_plain_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> None:
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    planted, victim = _plant_outside_session(coperceive_dir, tmp_path)
    calls = _record_calls(monkeypatch)
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health",
        lambda ip, port, token, timeout=3.0: probed.append((ip, port)) or True,
    )

    for label, res in [
        ("status", get_status(session_uuid=session_id)),
        ("stop", stop_session(session_uuid=session_id)),
        ("heartbeat", record_heartbeat(session_id)),
    ]:
        assert res["status"] == "error", (label, res)
        assert "session id" in res["error"], (label, res)

    assert calls == [], "nothing may be asked of Slurm (or cancelled) for a bad id"
    assert probed == []
    assert planted.is_file(), "a file outside sessions/ was removed"
    assert (tmp_path / "escape.json").is_file()
    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert not (coperceive_dir / "escape.heartbeat").exists()
    assert not (tmp_path / "escape.heartbeat").exists()
    assert json.loads(planted.read_text(encoding="utf-8")).get("last_heartbeat") is None


@pytest.mark.parametrize(
    "session_id", ["55bef61ccf2f60b8", str(uuid.uuid4()), "test-uuid-1234", "Run_2.v1"]
)
def test_status_stop_and_heartbeat_accept_ordinary_session_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> None:
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    sessions = coperceive_dir / "sessions"
    sessions.mkdir(parents=True)
    tokens = coperceive_dir / "tokens"
    tokens.mkdir()
    token = tokens / f"{session_id}.token"
    token.write_text("t" * 32 + "\n", encoding="utf-8")
    (sessions / f"{session_id}.json").write_text(
        json.dumps(
            {
                "uuid": session_id,
                "job_id": 4242,
                "node": NODE,
                "ip": NODE_IP,
                "port": 40001,
                "pid": 1,
                "started_at": 1,
                "data_root": str(tmp_path),
                "token_file": str(token),
            }
        ),
        encoding="utf-8",
    )
    calls = _record_calls(monkeypatch, squeue_out=f"RUNNING|00:01|01:00:00|None|{NODE}\n")
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health",
        lambda ip, port, token, timeout=3.0: probed.append((ip, port)) or True,
    )

    beat = record_heartbeat(session_id)
    assert beat["status"] == "ok", beat
    status = get_status(session_uuid=session_id)
    assert status["status"] == "running", status
    assert (status["ip"], status["port"], status["healthy"]) == (NODE_IP, 40001, True)
    assert probed == [(NODE_IP, 40001)]
    stopped = stop_session(session_uuid=session_id)
    assert stopped["cancelled"] is True, stopped
    assert ["/usr/bin/scancel", "4242"] in calls
    assert not token.exists()


def test_status_ignores_a_session_file_left_by_another_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reused id: the old file's address must not receive the new job's token."""
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    sessions = coperceive_dir / "sessions"
    sessions.mkdir(parents=True)
    tokens = coperceive_dir / "tokens"
    tokens.mkdir()
    token = tokens / "reused-id.token"
    token.write_text("n" * 32 + "\n", encoding="utf-8")
    stale = sessions / "reused-id.json"
    stale.write_text(
        json.dumps(
            {
                "uuid": "reused-id",
                "job_id": 4999,
                "node": "other",
                "ip": "192.0.2.66",
                "port": 40000,
                "pid": 1,
                "started_at": 1,
                "data_root": str(tmp_path),
                "token_file": str(token),
            }
        ),
        encoding="utf-8",
    )
    _record_calls(monkeypatch, squeue_out="RUNNING|00:01|01:00:00|None|node7\n")
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health",
        lambda ip, port, token, timeout=3.0: probed.append((ip, port)) or True,
    )

    res = get_status(session_uuid="reused-id", job_id=5000)

    assert probed == [], "the token went to an address from another job's session file"
    assert res["status"] == "pending", res
    assert res["ip"] != "192.0.2.66"
    assert res["healthy"] is False
    assert res["job_id"] == 5000

    # When the new job has ended, the other job's session file is left alone.
    _record_calls(monkeypatch, squeue_out="")
    ended = get_status(session_uuid="reused-id", job_id=5000)
    assert ended["status"] == "stopped", ended
    assert stale.is_file(), "the session file of job 4999 was deleted"


def test_status_ignores_a_session_file_whose_uuid_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coperceive_dir = _isolate(tmp_path, monkeypatch)
    sessions = coperceive_dir / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "mine.json").write_text(
        json.dumps(
            {
                "uuid": "someone-else",
                "job_id": 4242,
                "node": "other",
                "ip": "192.0.2.66",
                "port": 40000,
                "pid": 1,
                "started_at": 1,
                "data_root": str(tmp_path),
                "token_file": str(tmp_path / "t.token"),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "t.token").write_text("x" * 32 + "\n", encoding="utf-8")
    _record_calls(monkeypatch, squeue_out="RUNNING|00:01|01:00:00|None|node7\n")
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health",
        lambda ip, port, token, timeout=3.0: probed.append((ip, port)) or True,
    )

    res = get_status(session_uuid="mine", job_id=4242)

    assert probed == []
    assert res["ip"] != "192.0.2.66", res
