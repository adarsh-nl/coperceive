# SPDX-License-Identifier: Apache-2.0
"""`coperceive remote start|stop|status|token` for the dataset e2e fake cluster.

The real command submits a Slurm serve job; here the "job" is a real
`coperceive serve` on 127.0.0.1 started with the same coperceive argv the app
sent, the same --root and --static, and a 0600 token file. The reply has the
shape `coperceive remote start` prints, so the app's tunnel and viewer code run
unchanged. Usage: fake_remote.py '<the remote command line the app sent>'
"""

import json
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _option(argv: list[str], name: str) -> str | None:
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def _reply(**fields: object) -> int:
    print(json.dumps(fields))
    return 0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _healthy(port: int, token: str) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return response.status == 200
    except OSError:
        return False


def main() -> int:
    argv = shlex.split(sys.argv[1])
    split = argv.index("remote")
    coperceive, verb, args = argv[:split], argv[split + 1], argv[split + 2 :]
    home = Path(os.environ["FAKE_REMOTE_HOME"])
    sessions = home / ".fake-slurm" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)

    if verb == "start":
        root = _option(args, "--root")
        if not root or not Path(root).is_dir():
            return _reply(status="error", error=f"Data root directory does not exist: {root}")
        uuid = _option(args, "--uuid") or secrets.token_hex(8)
        token = secrets.token_urlsafe(24)
        token_file = sessions / f"{uuid}.token"
        token_file.write_text(token, encoding="utf-8")
        token_file.chmod(0o600)
        port = _free_port()
        command = [*coperceive, "serve", "--host", "127.0.0.1", "--port", str(port)]
        command += ["--token-file", str(token_file), "--root", root]
        static = _option(args, "--static")
        if static:
            static_dir = static.replace("~", str(home), 1) if static.startswith("~") else static
            if not (Path(static_dir) / "index.html").is_file():
                return _reply(status="error", error=f"Viewer page has no index.html: {static_dir}")
            command += ["--static", static_dir]
        log = open(sessions / f"{uuid}.log", "wb")
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "HOME": str(home)},
        )
        (sessions / f"{uuid}.pid").write_text(str(process.pid), encoding="utf-8")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print(json.dumps({"status": "stopped", "uuid": uuid, "error": "serve exited"}))
                return 0
            if _healthy(port, token):
                print(
                    json.dumps(
                        {
                            "status": "running",
                            "uuid": uuid,
                            "job_id": 799999,
                            "node": "fake-node",
                            "ip": "127.0.0.1",
                            "port": port,
                            "token": token,
                            "data_root": root,
                        }
                    )
                )
                return 0
            time.sleep(0.2)
        return _reply(status="pending", uuid=uuid, state="PENDING", reason="serve not healthy")

    uuid = _option(args, "--uuid") or ""
    if verb == "stop":
        pid_file = sessions / f"{uuid}.pid"
        if pid_file.is_file():
            try:
                os.killpg(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        print(json.dumps({"status": "stopped", "uuid": uuid}))
        return 0
    if verb == "token":
        token_file = sessions / f"{uuid}.token"
        if not token_file.is_file():
            print(json.dumps({"status": "error", "error": "no such session"}))
            return 0
        return _reply(status="ok", uuid=uuid, token=token_file.read_text(encoding="utf-8"))
    print(json.dumps({"status": "error", "error": f"fake remote does not support {verb}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
