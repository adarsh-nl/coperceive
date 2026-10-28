# SPDX-License-Identifier: Apache-2.0
"""Slurm job and session lifecycle manager for remote coperceive serve."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PARTITION = "ps"
DEFAULT_WALLTIME = "02:00:00"
DEFAULT_CPUS = 2
DEFAULT_MEM = "8G"


def get_base_dir() -> Path:
    base = Path.home() / ".coperceive"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


def get_sessions_dir() -> Path:
    d = get_base_dir() / "sessions"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def get_tokens_dir() -> Path:
    d = get_base_dir() / "tokens"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def get_logs_dir() -> Path:
    d = get_base_dir() / "logs"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def generate_secure_token() -> str:
    """Generates a 32-character hex secure bearer token."""
    return secrets.token_hex(16)


def write_token_file(token_path: Path, token: str) -> None:
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(token.strip() + "\n")
    except Exception:
        os.close(fd)
        raise


def parse_slurm_time(time_str: str) -> int:
    """Parses Slurm time format (D-HH:MM:SS, HH:MM:SS, or MM:SS) to total seconds."""
    s = time_str.strip()
    if not s or s in ("INVALID", "UNLIMITED", "NOT_SET", "N/A"):
        return 0
    days = 0
    if "-" in s:
        days_str, s = s.split("-", 1)
        try:
            days = int(days_str)
        except ValueError:
            return 0
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return days * 86400 + int(parts[0])
        elif len(parts) == 2:
            return days * 86400 + int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return 0
    return 0


def probe_health(ip: str, port: int, token: str, timeout: float = 3.0) -> bool:
    """Probes /api/health with the Bearer token."""
    url = f"http://{ip}:{port}/api/health"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("status") == "ok"
    except Exception:
        return False
    return False


@dataclass
class RemoteSession:
    uuid: str
    job_id: int
    node: str
    ip: str
    port: int
    pid: int
    started_at: int
    data_root: str
    token_file: str
    last_heartbeat: float | None = None

    @property
    def heartbeat_file(self) -> Path:
        return get_sessions_dir() / f"{self.uuid}.heartbeat"

    def get_heartbeat_age(self) -> float:
        if self.heartbeat_file.is_file():
            try:
                return max(0.0, round(time.time() - self.heartbeat_file.stat().st_mtime, 1))
            except OSError:
                pass
        if self.last_heartbeat is not None:
            return max(0.0, round(time.time() - self.last_heartbeat, 1))
        if self.started_at:
            return max(0.0, round(time.time() - float(self.started_at), 1))
        return 0.0

    @classmethod
    def from_file(cls, path: Path) -> RemoteSession | None:
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            last_hb = data.get("last_heartbeat")
            last_heartbeat = float(last_hb) if last_hb is not None else None
            return cls(
                uuid=str(data.get("uuid", "")),
                job_id=int(data.get("job_id", 0)),
                node=str(data.get("node", "")),
                ip=str(data.get("ip", "")),
                port=int(data.get("port", 0)),
                pid=int(data.get("pid", 0)),
                started_at=int(data.get("started_at", 0)),
                data_root=str(data.get("data_root", "")),
                token_file=str(data.get("token_file", "")),
                last_heartbeat=last_heartbeat,
            )
        except Exception:
            return None


def map_stopped_reason(sacct_state: str) -> str:
    """Maps a Slurm sacct state to a standardized stopped_reason."""
    s = sacct_state.upper()
    if "CANCELLED" in s:
        return "cancelled"
    if "TIMEOUT" in s or "TIME_LIMIT" in s:
        return "timeout"
    if "COMPLETED" in s:
        return "completed"
    return "failed"


def record_heartbeat(session_uuid: str) -> dict[str, Any]:
    """Records a heartbeat renewal for a session."""
    session_file = get_sessions_dir() / f"{session_uuid}.json"
    heartbeat_file = get_sessions_dir() / f"{session_uuid}.heartbeat"
    now_ts = time.time()

    if not session_file.is_file():
        return {
            "status": "error",
            "error": f"Session not found: {session_uuid}",
            "uuid": session_uuid,
        }

    # Touch heartbeat file mode 0600
    try:
        heartbeat_file.touch(mode=0o600, exist_ok=True)
        os.utime(heartbeat_file, (now_ts, now_ts))
    except OSError as exc:
        return {
            "status": "error",
            "error": f"Failed to update heartbeat file: {exc}",
            "uuid": session_uuid,
        }

    # Update session JSON
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        data["last_heartbeat"] = now_ts
        session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

    return {
        "status": "ok",
        "uuid": session_uuid,
        "timestamp": now_ts,
    }


def start_session(
    *,
    data_root: str | Path,
    partition: str = DEFAULT_PARTITION,
    walltime: str = DEFAULT_WALLTIME,
    cpus: int = DEFAULT_CPUS,
    mem: str = DEFAULT_MEM,
    session_uuid: str | None = None,
    sbatch_script: str | Path | None = None,
    wait_timeout: int = 45,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Submits a remote serve Slurm job and waits for RUNNING readiness."""
    data_root_path = Path(data_root).resolve()
    if not data_root_path.is_dir():
        return {
            "status": "error",
            "error": f"Data root directory does not exist: {data_root}",
        }

    uuid_val = session_uuid or secrets.token_hex(8)
    token = generate_secure_token()
    token_file = get_tokens_dir() / f"{uuid_val}.token"
    write_token_file(token_file, token)

    session_file = get_sessions_dir() / f"{uuid_val}.json"
    log_file = get_logs_dir() / f"{uuid_val}.log"

    # Default to repo sbatch script or caller override
    if sbatch_script is None:
        default_script = (
            Path(__file__).resolve().parent.parent.parent
            / "scripts"
            / "desktop"
            / "remote_serve.sbatch"
        )
        if default_script.is_file():
            script_path = default_script
        else:
            return {
                "status": "error",
                "error": f"sbatch script not found at {default_script}",
            }
    else:
        script_path = Path(sbatch_script).resolve()

    job_name = f"coperceive-desktop-{uuid_val}"

    # Build sbatch submission command
    sbatch_bin = shutil.which("sbatch")
    if not sbatch_bin:
        return {
            "status": "error",
            "error": "sbatch executable not found in PATH",
        }

    repo_root = Path(__file__).resolve().parent.parent.parent
    env = os.environ.copy()
    env["COPERCEIVE_REPO_ROOT"] = str(repo_root)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}:{existing_pythonpath}" if existing_pythonpath else str(repo_root)
    )
    env["COPERCEIVE_REMOTE_UUID"] = uuid_val
    env["COPERCEIVE_REMOTE_ROOT"] = str(data_root_path)
    env["COPERCEIVE_REMOTE_TOKEN_FILE"] = str(token_file)
    if extra_args:
        env["COPERCEIVE_EXTRA_ARGS"] = " ".join(extra_args)

    cmd = [
        sbatch_bin,
        "--parsable",
        f"--partition={partition}",
        f"--time={walltime}",
        f"--cpus-per-task={cpus}",
        f"--mem={mem}",
        f"--job-name={job_name}",
        f"--output={log_file}",
        str(script_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return {
            "status": "error",
            "error": f"sbatch failed (exit {exc.returncode}): {exc.stderr.strip()}",
        }
    except Exception as exc:
        return {"status": "error", "error": f"Failed to submit sbatch job: {exc}"}

    job_id_str = proc.stdout.strip().split(";")[0].strip()
    try:
        job_id = int(job_id_str)
    except ValueError:
        return {
            "status": "error",
            "error": f"Unexpected sbatch output: {proc.stdout.strip()}",
        }

    # Poll until job enters RUNNING and session file is written
    deadline = time.time() + max(1, wait_timeout)
    last_reason = "JobSubmitted"
    last_state = "PENDING"

    while time.time() < deadline:
        # Check squeue
        squeue_cmd = ["squeue", "-h", "-j", str(job_id), "-o", "%T|%r"]
        sq_proc = subprocess.run(squeue_cmd, capture_output=True, text=True)
        if sq_proc.returncode == 0 and sq_proc.stdout.strip():
            parts = sq_proc.stdout.strip().split("|", 1)
            last_state = parts[0].strip()
            last_reason = parts[1].strip() if len(parts) > 1 else "None"

            if last_state == "RUNNING":
                # Check if session file has been written
                session = RemoteSession.from_file(session_file)
                if session is not None and session.ip and session.port:
                    # Probe health
                    if probe_health(session.ip, session.port, token, timeout=2.0):
                        return {
                            "status": "running",
                            "uuid": uuid_val,
                            "job_id": job_id,
                            "node": session.node,
                            "ip": session.ip,
                            "port": session.port,
                            "token": token,
                            "started_at": session.started_at,
                            "data_root": session.data_root,
                            "log_file": str(log_file),
                        }
        elif sq_proc.returncode == 0 and not sq_proc.stdout.strip():
            # Job is no longer in queue (exited or failed)
            return {
                "status": "stopped",
                "uuid": uuid_val,
                "job_id": job_id,
                "error": "Slurm job exited prematurely",
            }
        time.sleep(1.0)

    # If wait timeout exceeded but job is PENDING in queue:
    return {
        "status": "pending",
        "state": last_state,
        "reason": last_reason,
        "uuid": uuid_val,
        "job_id": job_id,
        "token": token,
        "log_file": str(log_file),
    }


def get_status(
    *,
    session_uuid: str | None = None,
    job_id: int | None = None,
    health_timeout: float = 3.0,
) -> dict[str, Any]:
    """Inspects the state, elapsed time, walltime left, and health of a remote session."""
    session_file: Path | None = None
    session: RemoteSession | None = None

    if session_uuid:
        session_file = get_sessions_dir() / f"{session_uuid}.json"
        session = RemoteSession.from_file(session_file)
        if session and not job_id:
            job_id = session.job_id
    elif job_id:
        # Search session file matching job_id
        for candidate in get_sessions_dir().glob("*.json"):
            s = RemoteSession.from_file(candidate)
            if s and s.job_id == job_id:
                session = s
                session_file = candidate
                session_uuid = s.uuid
                break

    if not job_id and not session:
        if session_uuid:
            # Check sacct by job name for ended/cancelled sessions
            sa_cmd = [
                "sacct",
                "--name",
                f"coperceive-desktop-{session_uuid}",
                "-n",
                "-P",
                "-o",
                "JobID,State",
            ]
            sa_proc = subprocess.run(sa_cmd, capture_output=True, text=True)
            if sa_proc.returncode == 0 and sa_proc.stdout.strip():
                first = sa_proc.stdout.strip().split("\n")[0].split("|")
                resolved_id = None
                try:
                    resolved_id = int(first[0].split(".")[0])
                except ValueError:
                    pass
                sacct_state = first[1] if len(first) > 1 else "COMPLETED"
                stopped_reason = map_stopped_reason(sacct_state)
                # Clean up token and heartbeat files
                (get_tokens_dir() / f"{session_uuid}.token").unlink(missing_ok=True)
                (get_sessions_dir() / f"{session_uuid}.heartbeat").unlink(missing_ok=True)
                return {
                    "status": "stopped",
                    "state": sacct_state,
                    "stopped_reason": stopped_reason,
                    "uuid": session_uuid,
                    "job_id": resolved_id,
                    "healthy": False,
                    "last_heartbeat_age_s": None,
                    "message": f"Job ended ({sacct_state})",
                }
            return {
                "status": "stopped",
                "state": "STOPPED",
                "stopped_reason": "cancelled",
                "uuid": session_uuid,
                "job_id": None,
                "healthy": False,
                "last_heartbeat_age_s": None,
                "message": "Session ended or not found",
            }
        return {
            "status": "error",
            "error": "Either session_uuid or job_id must be provided",
        }

    # Query squeue for current state, elapsed, time left, reason, node
    sq_cmd = ["squeue", "-h", "-j", str(job_id), "-o", "%T|%M|%L|%r|%N"]
    sq_proc = subprocess.run(sq_cmd, capture_output=True, text=True)

    last_hb_age = session.get_heartbeat_age() if session else None
    if last_hb_age is None and session_uuid:
        hb_file = get_sessions_dir() / f"{session_uuid}.heartbeat"
        if hb_file.is_file():
            try:
                last_hb_age = max(0.0, round(time.time() - hb_file.stat().st_mtime, 1))
            except OSError:
                pass

    if sq_proc.returncode == 0 and sq_proc.stdout.strip():
        parts = [p.strip() for p in sq_proc.stdout.strip().split("|")]
        state = parts[0] if len(parts) > 0 else "UNKNOWN"
        elapsed = parts[1] if len(parts) > 1 else "00:00"
        time_left = parts[2] if len(parts) > 2 else "00:00"
        reason = parts[3] if len(parts) > 3 else "None"
        node = parts[4] if len(parts) > 4 else (session.node if session else "")

        time_left_sec = parse_slurm_time(time_left)
        warning = None
        if state == "RUNNING" and 0 < time_left_sec <= 600:
            warning = f"Walltime ending in less than 10 minutes ({time_left} remaining)"

        healthy = False
        token = ""
        ip = session.ip if session else ""
        port = session.port if session else 0

        if session_file and session:
            token_path = Path(session.token_file)
            if token_path.is_file():
                token = token_path.read_text(encoding="utf-8").strip()

        if state == "RUNNING" and ip and port and token:
            healthy = probe_health(ip, port, token, timeout=health_timeout)

        return {
            "status": "running" if state == "RUNNING" else "pending",
            "state": state,
            "uuid": session_uuid or (session.uuid if session else ""),
            "job_id": job_id,
            "node": node,
            "ip": ip,
            "port": port,
            "elapsed": elapsed,
            "time_left": time_left,
            "time_left_seconds": time_left_sec,
            "healthy": healthy,
            "last_heartbeat_age_s": last_hb_age,
            "reason": reason if state == "PENDING" else None,
            "warning": warning,
        }

    # Job is no longer in squeue; query sacct to determine exit state
    sacct_state = "COMPLETED"
    sacct_cmd = ["sacct", "-j", str(job_id), "-n", "-P", "-o", "State"]
    sa_proc = subprocess.run(sacct_cmd, capture_output=True, text=True)
    if sa_proc.returncode == 0 and sa_proc.stdout.strip():
        first_line = sa_proc.stdout.strip().split("\n")[0].strip()
        sacct_state = first_line.split("+")[0].strip() or "COMPLETED"

    # Clean up leftover session, token, and heartbeat files if they still exist
    if session_file and session_file.is_file():
        try:
            session_file.unlink()
        except OSError:
            pass
    if session and session.token_file:
        try:
            Path(session.token_file).unlink(missing_ok=True)
        except OSError:
            pass
    if session_uuid:
        token_path = get_tokens_dir() / f"{session_uuid}.token"
        if token_path.is_file():
            try:
                token_path.unlink()
            except OSError:
                pass
        hb_path = get_sessions_dir() / f"{session_uuid}.heartbeat"
        if hb_path.is_file():
            try:
                hb_path.unlink()
            except OSError:
                pass

    stopped_reason = map_stopped_reason(sacct_state)
    return {
        "status": "stopped",
        "state": sacct_state,
        "stopped_reason": stopped_reason,
        "uuid": session_uuid or (session.uuid if session else ""),
        "job_id": job_id,
        "healthy": False,
        "last_heartbeat_age_s": last_hb_age,
        "message": f"Job ended ({sacct_state})",
    }


def stop_session(
    *,
    session_uuid: str | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Cancels a remote serve Slurm job and cleans up session and token files."""
    session_file: Path | None = None
    token_file: Path | None = None

    if session_uuid:
        session_file = get_sessions_dir() / f"{session_uuid}.json"
        s = RemoteSession.from_file(session_file)
        if s:
            if not job_id:
                job_id = s.job_id
            if s.token_file:
                token_file = Path(s.token_file)

    if not token_file and session_uuid:
        token_file = get_tokens_dir() / f"{session_uuid}.token"

    if not job_id and session_uuid:
        # Check if squeue knows about coperceive-desktop-<uuid>
        sq_proc = subprocess.run(
            ["squeue", "-h", "--name", f"coperceive-desktop-{session_uuid}", "-o", "%i"],
            capture_output=True,
            text=True,
        )
        if sq_proc.returncode == 0 and sq_proc.stdout.strip():
            try:
                job_id = int(sq_proc.stdout.strip().split()[0])
            except ValueError:
                pass

    cancelled = False
    if job_id:
        scancel_bin = shutil.which("scancel") or "scancel"
        subprocess.run([scancel_bin, str(job_id)], capture_output=True, text=True)
        cancelled = True

    # Remove session, token, and heartbeat files
    if session_file and session_file.is_file():
        try:
            session_file.unlink()
        except OSError:
            pass

    if token_file and token_file.is_file():
        try:
            token_file.unlink()
        except OSError:
            pass

    if session_uuid:
        hb_file = get_sessions_dir() / f"{session_uuid}.heartbeat"
        if hb_file.is_file():
            try:
                hb_file.unlink()
            except OSError:
                pass

    return {
        "status": "stopped",
        "uuid": session_uuid or "",
        "job_id": job_id,
        "cancelled": cancelled,
    }


def list_sessions() -> dict[str, Any]:
    """Lists all active, pending, and orphan coperceive-desktop sessions."""
    # Query Slurm for user's coperceive-desktop jobs
    user = os.environ.get("USER", "")
    sq_cmd = ["squeue", "-h", "-o", "%i|%j|%T|%M|%L|%R|%N"]
    if user:
        sq_cmd.extend(["-u", user])

    sq_proc = subprocess.run(sq_cmd, capture_output=True, text=True)
    slurm_jobs: dict[str, dict[str, str]] = {}

    if sq_proc.returncode == 0 and sq_proc.stdout.strip():
        for line in sq_proc.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                j_id, j_name, state, elapsed, time_left, reason = parts[:6]
                node = parts[6] if len(parts) > 6 else ""
                if j_name.startswith("coperceive-desktop-"):
                    uuid_part = j_name.removeprefix("coperceive-desktop-")
                    slurm_jobs[uuid_part] = {
                        "job_id": j_id,
                        "job_name": j_name,
                        "state": state,
                        "elapsed": elapsed,
                        "time_left": time_left,
                        "reason": reason,
                        "node": node,
                    }

    sessions: list[dict[str, Any]] = []
    seen_uuids: set[str] = set()

    # Scan disk sessions
    for session_path in get_sessions_dir().glob("*.json"):
        s = RemoteSession.from_file(session_path)
        if not s:
            continue
        seen_uuids.add(s.uuid)
        slurm_info = slurm_jobs.get(s.uuid)
        hb_age = s.get_heartbeat_age()

        if slurm_info:
            time_left_sec = parse_slurm_time(slurm_info["time_left"])
            is_orphan = hb_age > 180.0
            sessions.append(
                {
                    "uuid": s.uuid,
                    "job_id": s.job_id,
                    "job_name": slurm_info["job_name"],
                    "state": slurm_info["state"],
                    "node": slurm_info["node"] or s.node,
                    "ip": s.ip,
                    "port": s.port,
                    "elapsed": slurm_info["elapsed"],
                    "time_left": slurm_info["time_left"],
                    "time_left_seconds": time_left_sec,
                    "data_root": s.data_root,
                    "last_heartbeat_age_s": hb_age,
                    "is_orphan": is_orphan,
                    "orphan_reason": "heartbeat_timeout" if is_orphan else None,
                }
            )
        else:
            # File exists on disk but job is not running in Slurm -> orphan/stale session
            sessions.append(
                {
                    "uuid": s.uuid,
                    "job_id": s.job_id,
                    "job_name": f"coperceive-desktop-{s.uuid}",
                    "state": "STOPPED",
                    "node": s.node,
                    "ip": s.ip,
                    "port": s.port,
                    "elapsed": "00:00",
                    "time_left": "00:00",
                    "time_left_seconds": 0,
                    "data_root": s.data_root,
                    "last_heartbeat_age_s": hb_age,
                    "is_orphan": True,
                    "orphan_reason": "job_not_found",
                }
            )

    # Any Slurm job running without a session file on disk is an orphan
    for uuid_val, slurm_info in slurm_jobs.items():
        if uuid_val not in seen_uuids:
            time_left_sec = parse_slurm_time(slurm_info["time_left"])
            sessions.append(
                {
                    "uuid": uuid_val,
                    "job_id": int(slurm_info["job_id"]),
                    "job_name": slurm_info["job_name"],
                    "state": slurm_info["state"],
                    "node": slurm_info["node"],
                    "ip": "",
                    "port": 0,
                    "elapsed": slurm_info["elapsed"],
                    "time_left": slurm_info["time_left"],
                    "time_left_seconds": time_left_sec,
                    "data_root": "",
                    "last_heartbeat_age_s": None,
                    "is_orphan": True,
                    "orphan_reason": "missing_session_file",
                }
            )

    return {"sessions": sessions}
