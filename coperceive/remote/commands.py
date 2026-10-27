# SPDX-License-Identifier: Apache-2.0
"""CLI commands for coperceive remote."""

from __future__ import annotations

import argparse
import json
import sys

from coperceive.remote.manager import (
    get_session_token,
    get_status,
    list_sessions,
    record_heartbeat,
    start_session,
    stop_session,
)


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
) -> None:
    remote_parser = sub.add_parser(
        "remote",
        help="Manage remote coperceive serve sessions on Slurm clusters.",
    )
    remote_sub = remote_parser.add_subparsers(dest="remote_command", required=True)

    # remote start
    start_parser = remote_sub.add_parser("start", help="Submit and start a remote serve Slurm job.")
    start_parser.add_argument("--root", required=True, help="Data directory root to serve.")
    start_parser.add_argument(
        "--partition",
        required=True,
        help="Slurm partition chosen in the server profile (there is no product default).",
    )
    start_parser.add_argument(
        "--time",
        default=None,
        help="Slurm walltime from the server profile; omitted means the partition default.",
    )
    start_parser.add_argument(
        "--cpus",
        type=int,
        default=None,
        help="CPUs allocated, from the server profile; omitted means the cluster default.",
    )
    start_parser.add_argument(
        "--mem",
        default=None,
        help="Memory allocated, from the server profile; omitted means the cluster default.",
    )
    start_parser.add_argument(
        "--python",
        default=None,
        help="Python interpreter on the compute node from the server profile.",
    )
    start_parser.add_argument(
        "--uuid", default=None, help="Explicit session UUID (auto-generated if omitted)."
    )
    start_parser.add_argument(
        "--sbatch-script", default=None, help="Path to remote_serve.sbatch script."
    )
    start_parser.add_argument(
        "--wait-timeout",
        type=int,
        default=45,
        help="Max seconds to wait on login node for RUNNING status (default: 45s).",
    )
    start_parser.add_argument(
        "--media-root",
        action="append",
        default=[],
        metavar="DIR",
        help="Additional media roots to allow. Repeatable.",
    )
    start_parser.set_defaults(func=_remote_start)

    # remote status
    status_parser = remote_sub.add_parser(
        "status", help="Get status and health of a remote session."
    )
    status_parser.add_argument("uuid", nargs="?", default=None, help="Session UUID.")
    status_parser.add_argument("--uuid", dest="opt_uuid", default=None, help="Session UUID.")
    status_parser.add_argument("--job-id", type=int, default=None, help="Slurm job ID.")
    status_parser.add_argument(
        "--health-timeout",
        type=float,
        default=3.0,
        help="Timeout in seconds for /api/health probe (default: 3.0).",
    )
    status_parser.set_defaults(func=_remote_status)

    # remote stop
    stop_parser = remote_sub.add_parser(
        "stop", help="Cancel and clean up a remote serve Slurm job."
    )
    stop_parser.add_argument("uuid", nargs="?", default=None, help="Session UUID.")
    stop_parser.add_argument("--uuid", dest="opt_uuid", default=None, help="Session UUID.")
    stop_parser.add_argument("--job-id", type=int, default=None, help="Slurm job ID.")
    stop_parser.set_defaults(func=_remote_stop)

    # remote token (read-only; used by the desktop app to reattach)
    token_parser = remote_sub.add_parser(
        "token", help="Print the running session's bearer token (desktop reattach only)."
    )
    token_parser.add_argument("--uuid", required=True, help="Session UUID.")
    token_parser.set_defaults(func=_remote_token)

    # remote list
    list_parser = remote_sub.add_parser(
        "list", help="List all active, pending, and orphan remote sessions."
    )
    list_parser.set_defaults(func=_remote_list)

    # remote heartbeat
    heartbeat_parser = remote_sub.add_parser(
        "heartbeat", help="Send heartbeat lease renewal for a remote session."
    )
    heartbeat_parser.add_argument("uuid", nargs="?", default=None, help="Session UUID.")
    heartbeat_parser.add_argument("--uuid", dest="opt_uuid", default=None, help="Session UUID.")
    heartbeat_parser.set_defaults(func=_remote_heartbeat)


def _remote_start(args: argparse.Namespace) -> None:
    extra_args: list[str] = []
    for mr in args.media_root:
        extra_args.extend(["--media-root", mr])

    result = start_session(
        data_root=args.root,
        partition=args.partition,
        walltime=args.time,
        cpus=args.cpus,
        mem=args.mem,
        python=args.python,
        session_uuid=args.uuid,
        sbatch_script=args.sbatch_script,
        wait_timeout=args.wait_timeout,
        extra_args=extra_args if extra_args else None,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") == "error":
        sys.exit(1)


def _remote_status(args: argparse.Namespace) -> None:
    uuid_val = args.uuid or args.opt_uuid
    result = get_status(
        session_uuid=uuid_val,
        job_id=args.job_id,
        health_timeout=args.health_timeout,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") == "error":
        sys.exit(1)


def _remote_stop(args: argparse.Namespace) -> None:
    uuid_val = args.uuid or args.opt_uuid
    result = stop_session(
        session_uuid=uuid_val,
        job_id=args.job_id,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") == "error":
        sys.exit(1)


def _remote_token(args: argparse.Namespace) -> None:
    result = get_session_token(session_uuid=args.uuid)
    print(json.dumps(result, indent=2))
    if result.get("status") == "error":
        sys.exit(1)


def _remote_list(args: argparse.Namespace) -> None:
    result = list_sessions()
    print(json.dumps(result, indent=2))


def _remote_heartbeat(args: argparse.Namespace) -> None:
    uuid_val = args.uuid or args.opt_uuid
    if not uuid_val:
        print(json.dumps({"status": "error", "error": "session uuid is required"}))
        sys.exit(1)
    result = record_heartbeat(uuid_val)
    print(json.dumps(result, indent=2))
    if result.get("status") == "error":
        sys.exit(1)
