# SPDX-License-Identifier: Apache-2.0
"""CLI commands for coperceive remote."""

from __future__ import annotations

import argparse
import json
import sys

from coperceive.remote.manager import (
    DEFAULT_CPUS,
    DEFAULT_MEM,
    DEFAULT_PARTITION,
    DEFAULT_WALLTIME,
    get_status,
    list_sessions,
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
        default=DEFAULT_PARTITION,
        help=f"Slurm partition (default: {DEFAULT_PARTITION}).",
    )
    start_parser.add_argument(
        "--time", default=DEFAULT_WALLTIME, help=f"Slurm walltime (default: {DEFAULT_WALLTIME})."
    )
    start_parser.add_argument(
        "--cpus", type=int, default=DEFAULT_CPUS, help=f"CPUs allocated (default: {DEFAULT_CPUS})."
    )
    start_parser.add_argument(
        "--mem", default=DEFAULT_MEM, help=f"Memory allocated (default: {DEFAULT_MEM})."
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

    # remote list
    list_parser = remote_sub.add_parser(
        "list", help="List all active, pending, and orphan remote sessions."
    )
    list_parser.set_defaults(func=_remote_list)


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


def _remote_list(args: argparse.Namespace) -> None:
    result = list_sessions()
    print(json.dumps(result, indent=2))
