# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false
"""Unit and integration tests for remote coperceive serve session management."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from coperceive.remote.manager import (
    generate_secure_token,
    get_session_token,
    get_status,
    list_sessions,
    map_stopped_reason,
    parse_slurm_time,
    record_heartbeat,
    start_session,
    stop_session,
    write_token_file,
)


def test_parse_slurm_time() -> None:
    assert parse_slurm_time("09:45") == 9 * 60 + 45
    assert parse_slurm_time("01:30:15") == 1 * 3600 + 30 * 60 + 15
    assert parse_slurm_time("1-02:00:00") == 86400 + 2 * 3600
    assert parse_slurm_time("2-00:30:00") == 2 * 86400 + 30 * 60
    assert parse_slurm_time("INVALID") == 0
    assert parse_slurm_time("") == 0
    assert parse_slurm_time("N/A") == 0


def test_token_file_strict_permissions(tmp_path: Path) -> None:
    token = generate_secure_token()
    token_file = tmp_path / "test.token"
    write_token_file(token_file, token)

    assert token_file.is_file()
    assert token_file.read_text(encoding="utf-8").strip() == token
    mode = token_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_remote_start_with_fake_sbatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Setup isolated ~/.coperceive directory in tmp_path
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )
    monkeypatch.setattr("coperceive.remote.manager.get_logs_dir", lambda: coperceive_dir / "logs")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_sbatch_script = tmp_path / "fake.sbatch"
    fake_sbatch_script.touch()

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "sbatch" or cmd[0].endswith("sbatch"):
            return subprocess.CompletedProcess(cmd, 0, stdout="778899\n", stderr="")
        elif cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout="RUNNING|None\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Mock health probe to succeed
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda prog: f"/usr/bin/{prog}")
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health", lambda ip, port, token, timeout: True
    )

    uuid_test = "test-uuid-1234"
    # Pre-populate session file simulating compute node output
    session_file = coperceive_dir / "sessions" / f"{uuid_test}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_test,
                "job_id": 778899,
                "node": "hpc-node11",
                "ip": "10.20.240.48",
                "port": 8731,
                "pid": 5544,
                "started_at": 1700000000,
                "data_root": str(data_dir),
                "token_file": str(coperceive_dir / "tokens" / f"{uuid_test}.token"),
            }
        ),
        encoding="utf-8",
    )

    res = start_session(
        data_root=data_dir,
        partition="test-partition",
        session_uuid=uuid_test,
        sbatch_script=fake_sbatch_script,
        wait_timeout=2,
    )

    assert res["status"] == "running"
    assert res["uuid"] == uuid_test
    assert res["job_id"] == 778899
    assert res["node"] == "hpc-node11"
    assert res["ip"] == "10.20.240.48"
    assert res["port"] == 8731
    assert len(res["token"]) == 32


def test_remote_start_uploads_static_dir_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop uploads its viewer page; the server must receive that dir."""
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )
    monkeypatch.setattr("coperceive.remote.manager.get_logs_dir", lambda: coperceive_dir / "logs")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_sbatch_script = tmp_path / "fake.sbatch"
    fake_sbatch_script.touch()
    static_dir = tmp_path / "remote-static"
    static_dir.mkdir()

    # Without a built page the desktop app would leave Remote mode with a 404;
    # refuse before submitting anything.
    missing = start_session(
        data_root=data_dir,
        partition="test-partition",
        sbatch_script=fake_sbatch_script,
        static_dir=static_dir,
        wait_timeout=1,
    )
    assert missing["status"] == "error"
    assert "no index.html" in missing["error"]

    (static_dir / "index.html").write_text('<script src="./a.js"></script>\n', encoding="utf-8")
    (static_dir / "a.js").write_text("console.log('viewer')\n", encoding="utf-8")
    captured: dict[str, str] = {}

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "sbatch" or cmd[0].endswith("sbatch"):
            captured.update(kwargs.get("env", {}))
            return subprocess.CompletedProcess(cmd, 0, stdout="778899\n", stderr="")
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout="RUNNING|None\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    uuid_test = "test-static-uuid"
    session_file = coperceive_dir / "sessions" / f"{uuid_test}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_test,
                "job_id": 778899,
                "node": "hpc-node11",
                "ip": "10.20.240.48",
                "port": 8731,
                "pid": 5544,
                "started_at": 1700000000,
                "data_root": str(data_dir),
                "token_file": str(coperceive_dir / "tokens" / f"{uuid_test}.token"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda prog: f"/usr/bin/{prog}")
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health", lambda ip, port, token, timeout: True
    )
    res = start_session(
        data_root=data_dir,
        partition="test-partition",
        session_uuid=uuid_test,
        sbatch_script=fake_sbatch_script,
        static_dir=static_dir,
        wait_timeout=2,
    )
    assert res["status"] == "running"
    assert captured.get("COPERCEIVE_REMOTE_STATIC") == str(static_dir.resolve())


def test_remote_start_pending_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )
    monkeypatch.setattr("coperceive.remote.manager.get_logs_dir", lambda: coperceive_dir / "logs")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_sbatch_script = tmp_path / "fake.sbatch"
    fake_sbatch_script.touch()

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "sbatch" or cmd[0].endswith("sbatch"):
            return subprocess.CompletedProcess(cmd, 0, stdout="889900\n", stderr="")
        elif cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout="PENDING|Priority\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda prog: f"/usr/bin/{prog}")

    res = start_session(
        data_root=data_dir,
        partition="test-partition",
        session_uuid="pending-uuid-5678",
        sbatch_script=fake_sbatch_script,
        wait_timeout=1,
    )

    assert res["status"] == "pending"
    assert res["state"] == "PENDING"
    assert res["reason"] == "Priority"
    assert res["job_id"] == 889900


def test_remote_status_running_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )

    uuid_test = "warn-uuid-9999"
    token = generate_secure_token()
    token_file = coperceive_dir / "tokens" / f"{uuid_test}.token"
    write_token_file(token_file, token)

    session_file = coperceive_dir / "sessions" / f"{uuid_test}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_test,
                "job_id": 112233,
                "node": "hpc-node12",
                "ip": "10.20.240.49",
                "port": 8732,
                "pid": 6677,
                "started_at": 1700000000,
                "data_root": "/tmp/scenes",
                "token_file": str(token_file),
            }
        ),
        encoding="utf-8",
    )

    # Time left 08:30 (510s) <= 10m -> warning expected
    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="RUNNING|01:51:30|08:30|None|hpc-node12\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        "coperceive.remote.manager.probe_health", lambda ip, port, token, timeout: True
    )

    status = get_status(session_uuid=uuid_test)
    assert status["status"] == "running"
    assert status["state"] == "RUNNING"
    assert status["healthy"] is True
    assert status["time_left_seconds"] == 510
    assert status["warning"] is not None
    assert "less than 10 minutes" in status["warning"]


def test_remote_status_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )

    uuid_test = "stopped-uuid-4444"
    session_file = coperceive_dir / "sessions" / f"{uuid_test}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_test,
                "job_id": 998877,
                "node": "hpc-node11",
                "ip": "10.20.240.48",
                "port": 8731,
                "pid": 1122,
                "started_at": 1700000000,
                "data_root": "/tmp/scenes",
                "token_file": "",
            }
        ),
        encoding="utf-8",
    )

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        elif cmd[0] == "sacct":
            return subprocess.CompletedProcess(cmd, 0, stdout="CANCELLED by 1001\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    status = get_status(session_uuid=uuid_test)
    assert status["status"] == "stopped"
    assert status["state"] == "CANCELLED by 1001"
    assert status["healthy"] is False
    # Verifies session file was cleaned up
    assert not session_file.exists()


def test_remote_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )

    uuid_test = "stop-uuid-1111"
    token_file = coperceive_dir / "tokens" / f"{uuid_test}.token"
    write_token_file(token_file, "secret")

    session_file = coperceive_dir / "sessions" / f"{uuid_test}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_test,
                "job_id": 445566,
                "node": "hpc-node14",
                "ip": "10.20.240.41",
                "port": 8731,
                "pid": 3344,
                "started_at": 1700000000,
                "data_root": "/tmp/scenes",
                "token_file": str(token_file),
            }
        ),
        encoding="utf-8",
    )

    scancel_called = []

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "scancel" or cmd[0].endswith("scancel"):
            scancel_called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    res = stop_session(session_uuid=uuid_test)
    assert res["status"] == "stopped"
    assert res["job_id"] == 445566
    assert res["cancelled"] is True
    assert len(scancel_called) == 1
    assert "445566" in scancel_called[0]
    assert not session_file.exists()
    assert not token_file.exists()


def test_remote_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )

    import time

    now = time.time()
    # Session 1: valid running session
    s1_file = coperceive_dir / "sessions" / "uuid-1.json"
    s1_file.parent.mkdir(parents=True, exist_ok=True)
    s1_file.write_text(
        json.dumps(
            {
                "uuid": "uuid-1",
                "job_id": 1001,
                "node": "hpc-node11",
                "ip": "10.20.240.48",
                "port": 8731,
                "pid": 111,
                "started_at": int(now),
                "last_heartbeat": now,
                "data_root": "/data/1",
                "token_file": "",
            }
        ),
        encoding="utf-8",
    )

    # Session 2: orphan session on disk (job not in squeue)
    s2_file = coperceive_dir / "sessions" / "uuid-2.json"
    s2_file.write_text(
        json.dumps(
            {
                "uuid": "uuid-2",
                "job_id": 1002,
                "node": "hpc-node12",
                "ip": "10.20.240.49",
                "port": 8732,
                "pid": 222,
                "started_at": 1700000000,
                "data_root": "/data/2",
                "token_file": "",
            }
        ),
        encoding="utf-8",
    )

    # Slurm output has job 1001 (uuid-1) and job 1003 (uuid-3 orphan with no file)
    slurm_output = (
        "1001|coperceive-desktop-uuid-1|RUNNING|00:10:00|01:50:00|None|hpc-node11\n"
        "1003|coperceive-desktop-uuid-3|RUNNING|00:05:00|01:55:00|None|hpc-node14\n"
    )

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout=slurm_output, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    res = list_sessions()
    sessions = res["sessions"]
    assert len(sessions) == 3

    by_uuid = {s["uuid"]: s for s in sessions}
    assert by_uuid["uuid-1"]["is_orphan"] is False
    assert by_uuid["uuid-1"]["state"] == "RUNNING"

    assert by_uuid["uuid-2"]["is_orphan"] is True
    assert by_uuid["uuid-2"]["state"] == "STOPPED"

    assert by_uuid["uuid-3"]["is_orphan"] is True
    assert by_uuid["uuid-3"]["state"] == "RUNNING"


def test_stopped_reason_mapping() -> None:
    assert map_stopped_reason("TIMEOUT") == "timeout"
    assert map_stopped_reason("TIME_LIMIT") == "timeout"
    assert map_stopped_reason("CANCELLED by 17764406") == "cancelled"
    assert map_stopped_reason("CANCELLED") == "cancelled"
    assert map_stopped_reason("COMPLETED") == "completed"
    assert map_stopped_reason("FAILED") == "failed"
    assert map_stopped_reason("NODE_FAIL") == "failed"


def test_record_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )

    sessions_dir = coperceive_dir / "sessions"
    sessions_dir.mkdir(parents=True)

    uuid_test = "hb-uuid-123"
    session_file = sessions_dir / f"{uuid_test}.json"
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_test,
                "job_id": 9999,
                "node": "hpc-node11",
                "ip": "10.20.240.11",
                "port": 50000,
                "pid": 1234,
                "started_at": 1000,
                "last_heartbeat": 1000,
                "data_root": "/data",
                "token_file": "/tokens/test.token",
            }
        )
    )

    # Valid heartbeat
    res = record_heartbeat(uuid_test)
    assert res["status"] == "ok"
    assert res["uuid"] == uuid_test
    hb_file = sessions_dir / f"{uuid_test}.heartbeat"
    assert hb_file.is_file()
    assert (hb_file.stat().st_mode & 0o777) == 0o600

    # Non-existent session
    bad_res = record_heartbeat("unknown-uuid")
    assert bad_res["status"] == "error"


def test_remote_list_orphan_on_heartbeat_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )

    sessions_dir = coperceive_dir / "sessions"
    sessions_dir.mkdir(parents=True)

    # Active session with recent heartbeat (30s ago)
    import time

    now = time.time()
    s1_file = sessions_dir / "fresh-uuid.json"
    s1_file.write_text(
        json.dumps(
            {
                "uuid": "fresh-uuid",
                "job_id": 2001,
                "node": "hpc-node11",
                "ip": "10.20.240.11",
                "port": 50001,
                "pid": 2001,
                "started_at": int(now - 100),
                "last_heartbeat": now - 30.0,
                "data_root": "/data",
                "token_file": "",
            }
        )
    )

    # Stale session with expired heartbeat (200s ago > 180s)
    s2_file = sessions_dir / "stale-uuid.json"
    s2_file.write_text(
        json.dumps(
            {
                "uuid": "stale-uuid",
                "job_id": 2002,
                "node": "hpc-node12",
                "ip": "10.20.240.12",
                "port": 50002,
                "pid": 2002,
                "started_at": int(now - 300),
                "last_heartbeat": now - 200.0,
                "data_root": "/data",
                "token_file": "",
            }
        )
    )

    slurm_output = (
        "2001|coperceive-desktop-fresh-uuid|RUNNING|00:01:40|01:58:20|None|hpc-node11\n"
        "2002|coperceive-desktop-stale-uuid|RUNNING|00:05:00|01:55:00|None|hpc-node12\n"
    )

    def fake_subprocess_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout=slurm_output, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    res = list_sessions()
    sessions = res["sessions"]
    by_uuid = {s["uuid"]: s for s in sessions}

    assert by_uuid["fresh-uuid"]["is_orphan"] is False
    assert by_uuid["fresh-uuid"]["orphan_reason"] is None
    assert by_uuid["fresh-uuid"]["last_heartbeat_age_s"] < 60.0

    assert by_uuid["stale-uuid"]["is_orphan"] is True
    assert by_uuid["stale-uuid"]["orphan_reason"] == "heartbeat_timeout"
    assert by_uuid["stale-uuid"]["last_heartbeat_age_s"] >= 199.0


@pytest.mark.slow
def test_cluster_remote_session_integration(tmp_path: Path) -> None:
    """End-to-end integration test on real cluster Slurm (requires sbatch)."""
    import shutil

    if not shutil.which("sbatch"):
        pytest.skip("sbatch not available on this system")

    data_dir = tmp_path / "scenes"
    data_dir.mkdir()
    (data_dir / "scene-01").mkdir()

    res = start_session(
        data_root=data_dir,
        partition="integration-test",
        walltime="00:03:00",
        wait_timeout=45,
    )

    if res.get("status") == "pending":
        job_id = res["job_id"]
        stop_session(job_id=job_id)
        return

    assert res["status"] == "running"
    session_uuid = res["uuid"]
    job_id = res["job_id"]

    try:
        status = get_status(session_uuid=session_uuid)
        assert status["status"] == "running"
        assert status["healthy"] is True

        listing = list_sessions()
        active = [s for s in listing["sessions"] if s["uuid"] == session_uuid]
        assert len(active) == 1
        assert active[0]["is_orphan"] is False
    finally:
        stop_res = stop_session(session_uuid=session_uuid, job_id=job_id)
        assert stop_res["status"] == "stopped"

    post_status = get_status(session_uuid=session_uuid, job_id=job_id)
    assert post_status["status"] == "stopped"


def _isolate_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    coperceive_dir = tmp_path / ".coperceive"
    monkeypatch.setattr("coperceive.remote.manager.get_base_dir", lambda: coperceive_dir)
    monkeypatch.setattr(
        "coperceive.remote.manager.get_sessions_dir", lambda: coperceive_dir / "sessions"
    )
    monkeypatch.setattr(
        "coperceive.remote.manager.get_tokens_dir", lambda: coperceive_dir / "tokens"
    )
    monkeypatch.setattr("coperceive.remote.manager.get_logs_dir", lambda: coperceive_dir / "logs")
    return coperceive_dir


def test_start_refuses_missing_partition_with_plain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No product partition default: without a profile choice, start explains it."""
    _isolate_dirs(tmp_path, monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    script = tmp_path / "fake.sbatch"
    script.touch()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = start_session(data_root=data_dir, sbatch_script=script)
    assert res["status"] == "error"
    assert "No partition was chosen" in res["error"]
    assert calls == [], "nothing may be submitted without a chosen partition"


def test_start_uses_profile_values_and_omits_absent_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_dirs(tmp_path, monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    script = tmp_path / "fake.sbatch"
    script.touch()
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(list(cmd))
        if cmd[0].endswith("sbatch"):
            return subprocess.CompletedProcess(cmd, 0, stdout="4242\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="PENDING|Priority\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda prog: f"/usr/bin/{prog}")

    start_session(
        data_root=data_dir,
        partition="gpu-h100",
        walltime="01:00:00",
        cpus=4,
        mem="16G",
        session_uuid="profile-resources-uuid",
        sbatch_script=script,
        wait_timeout=0,
    )
    sbatch_cmd = next(cmd for cmd in seen if cmd[0].endswith("sbatch"))
    assert "--partition=gpu-h100" in sbatch_cmd
    assert "--time=01:00:00" in sbatch_cmd
    assert "--cpus-per-task=4" in sbatch_cmd
    assert "--mem=16G" in sbatch_cmd

    seen.clear()
    start_session(
        data_root=data_dir,
        partition="main-cpu",
        session_uuid="profile-minimal-uuid",
        sbatch_script=script,
        wait_timeout=0,
    )
    sbatch_cmd = next(cmd for cmd in seen if cmd[0].endswith("sbatch"))
    assert "--partition=main-cpu" in sbatch_cmd
    assert not any(flag.startswith("--time=") for flag in sbatch_cmd)
    assert not any(flag.startswith("--cpus-per-task=") for flag in sbatch_cmd)
    assert not any(flag.startswith("--mem=") for flag in sbatch_cmd)


def test_session_token_is_read_over_ssh_and_stays_a_remote_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coperceive_dir = _isolate_dirs(tmp_path, monkeypatch)
    uuid_val = "token-uuid-42"
    token = "0123456789abcdef0123456789abcdef"
    token_file = coperceive_dir / "tokens" / f"{uuid_val}.token"
    write_token_file(token_file, token)
    session_file = coperceive_dir / "sessions" / f"{uuid_val}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "uuid": uuid_val,
                "job_id": 7,
                "node": "compute-1",
                "ip": "192.0.2.5",
                "port": 9000,
                "pid": 10,
                "started_at": 1,
                "data_root": "/data",
                "token_file": str(token_file),
            }
        ),
        encoding="utf-8",
    )

    res = get_session_token(session_uuid=uuid_val)
    assert res["status"] == "ok"
    assert res["token"] == token
    # The token is only read, never copied to another local file.
    assert sorted(p.name for p in (coperceive_dir / "tokens").iterdir()) == [token_file.name]

    assert get_session_token(session_uuid="unknown-uuid")["status"] == "error"
    assert get_session_token(session_uuid="../escape")["status"] == "error"


def test_session_token_refuses_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coperceive_dir = _isolate_dirs(tmp_path, monkeypatch)
    outside = tmp_path / "outside.token"
    outside.write_text("stolen", encoding="utf-8")
    tokens_dir = coperceive_dir / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    (tokens_dir / "escape-uuid.token").symlink_to(outside)
    res = get_session_token(session_uuid="escape-uuid")
    assert res["status"] == "error"


def test_no_site_defaults_in_remote_code_or_sbatch_script() -> None:
    root = Path(__file__).resolve().parent.parent
    product_sources = {
        "coperceive/remote/manager.py": root / "coperceive" / "remote" / "manager.py",
        "coperceive/remote/commands.py": root / "coperceive" / "remote" / "commands.py",
        "coperceive/models/slurm.py": root / "coperceive" / "models" / "slurm.py",
        "coperceive/models/commands.py": root / "coperceive" / "models" / "commands.py",
        "scripts/desktop/remote_serve.sbatch": root / "scripts" / "desktop" / "remote_serve.sbatch",
    }
    sources = {name: path.read_text(encoding="utf-8") for name, path in product_sources.items()}
    all_text = "\n".join(sources.values())
    for site_value in [
        "DEFAULT_PARTITION",
        "ps,main-gpu",
        "ps,main-cpu",
        "utwente",
        "hpc-node",
        "hpc-head",
        "/datasets/eemcs",
        "main-gpu",
    ]:
        assert site_value not in all_text, f"site-specific value {site_value!r} in product code"
    assert "--partition=ps" not in all_text
    sbatch = sources["scripts/desktop/remote_serve.sbatch"]
    for flag in ["--partition=", "--cpus-per-task=", "--mem=", "--time="]:
        assert f"#SBATCH {flag}" not in sbatch, f"{flag} must come from the profile, not the script"
    # The model-run sbatch takes its partition from the CLI/profile too.
    models = sources["coperceive/models/slurm.py"]
    assert "--slurm-partition" in models or "COPERCEIVE_SLURM_PARTITION" in models
    assert "partition=partition" in models or "#SBATCH --partition={partition}" in models


def test_committed_portability_fixtures_are_generic_and_distinct() -> None:
    """The same fixtures drive the Rust portability tests and this check."""
    fixtures_dir = (
        Path(__file__).resolve().parent.parent / "src-tauri" / "tests" / "fixtures" / "hpc"
    )
    names = sorted(p.name for p in fixtures_dir.glob("cluster-*.json"))
    assert names == ["cluster-a.json", "cluster-b.json", "cluster-none.json"]

    profiles = []
    for name in names:
        data = json.loads((fixtures_dir / name).read_text(encoding="utf-8"))
        profile = data["profile"]
        profiles.append(profile)
        serialised = json.dumps(data).lower()
        for forbidden in [
            "utwente",
            "hpc-node",
            " hpc-head",
            '"ps"',
            "partition=ps",
            "password",
            "token",
        ]:
            assert forbidden not in serialised, f"{name} contains site/credential value {forbidden}"
        assert profile["defaults"]["partition"] is None or profile["scheduler"] == "slurm"

    partitions = {profile["defaults"]["partition"] for profile in profiles}
    assert partitions == {"gpu-a100", "main-cpu", None}
    assert [p["scheduler"] for p in profiles].count("none") == 1
    assert "gpu:a100:1" in json.dumps(profiles[0])
    assert "gpu:h100:2" not in json.dumps(profiles[0])

    fake_ssh = (fixtures_dir / "fake_ssh.sh").read_text(encoding="utf-8")
    assert "gpu:a100:4" in fake_ssh and "gpu:h100:2" in fake_ssh
    assert "proxyjump jump.example.org" in fake_ssh
