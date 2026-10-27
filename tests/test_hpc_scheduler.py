# SPDX-License-Identifier: Apache-2.0
"""Scheduler adapters: discovery parsing, job scripts, safe argv, no-scheduler."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

import pytest
from coperceive.hpc import (
    EnvironmentKind,
    EnvironmentSetup,
    JobDefaults,
    JobRequest,
    JobState,
    NullAdapter,
    SchedulerError,
    SchedulerKind,
    ServerProfile,
    SlurmAdapter,
    parse_scontrol_partition,
    parse_sinfo,
    quote_remote_argv,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hpc"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeRunner:
    """Records argv lists and returns canned stdout, like the cheap ssh seam."""

    def __init__(self, outputs: dict[str, str]) -> None:
        self.calls: list[list[str]] = []
        self._outputs = outputs

    def __call__(self, argv: Sequence[str], *, stdin: str | None = None) -> str:
        self.calls.append(list(argv))
        key = argv[0]
        return self._outputs.get(key, "")


def _profile(**changes: object) -> ServerProfile:
    values: dict[str, object] = {
        "name": "Alpha lab",
        "ssh_alias": "alpha-cluster",
        "data_roots": ("/data/projects/alpha",),
        "scratch_template": "/scratch/$USER/$SLURM_JOB_ID",
        "defaults": JobDefaults(partition="gpu-a100", gres="gpu:a100:1", memory="16G"),
        "environment": EnvironmentSetup(
            kind=EnvironmentKind.MODULES,
            modules=("python/3.12",),
        ),
    }
    values.update(changes)
    return ServerProfile(**values)  # type: ignore[arg-type]


def test_pipe_format_sinfo_is_parsed() -> None:
    rows = parse_sinfo(_text("sinfo_pipe.txt"))
    assert [row.name for row in rows] == ["compute", "gpu-a100"]
    gpu = rows[1]
    assert gpu.nodes == 2
    assert gpu.state == "mix"
    assert gpu.max_time == "2-00:00:00"
    assert gpu.gres == "gpu:a100:4"
    assert gpu.gpu_types == ("a100",)


def test_column_format_sinfo_is_parsed() -> None:
    rows = parse_sinfo(_text("sinfo_columns.txt"))
    assert [row.name for row in rows] == ["cpu-short", "gpu-h100"]
    assert rows[0].nodes == 8
    assert rows[0].max_time == "04:00:00"
    assert rows[1].state == "mix"
    assert rows[1].gres is None


def test_scontrol_one_line_and_multiline_forms() -> None:
    one = parse_scontrol_partition(_text("scontrol_oneline.txt"))
    assert set(one) == {"compute", "gpu-a100"}
    assert one["compute"]["Default"] == "YES"
    assert one["gpu-a100"]["MaxTime"] == "2-00:00:00"
    multi = parse_scontrol_partition(_text("scontrol_multiline.txt"))
    assert set(multi) == {"cpu-short", "gpu-h100"}
    assert multi["gpu-h100"]["TotalNodes"] == "4"
    assert "gres/gpu:h100=4" in multi["gpu-h100"]["TRES"]


def test_discovery_merges_sinfo_and_scontrol_and_lists_gpu_types() -> None:
    runner = FakeRunner(
        {"sinfo": _text("sinfo_pipe.txt"), "scontrol": _text("scontrol_oneline.txt")}
    )
    discovery = SlurmAdapter(runner).discover()
    assert discovery.scheduler is SchedulerKind.SLURM
    assert discovery.names() == ("compute", "gpu-a100")
    assert discovery.gpu_types == ("a100",)
    gpu = next(part for part in discovery.partitions if part.name == "gpu-a100")
    assert gpu.nodes == 2
    assert gpu.max_time == "2-00:00:00"
    assert gpu.gres == "gpu:a100:4"
    # Discovery is read-only: only sinfo and scontrol ran.
    assert [call[0] for call in runner.calls] == ["sinfo", "scontrol"]


def test_second_cluster_discovery_uses_its_own_partitions_and_gres() -> None:
    runner = FakeRunner(
        {"sinfo": _text("sinfo_columns.txt"), "scontrol": _text("scontrol_multiline.txt")}
    )
    discovery = SlurmAdapter(runner).discover()
    assert discovery.names() == ("cpu-short", "gpu-h100")
    assert discovery.gpu_types == ("h100",)
    gpu = next(part for part in discovery.partitions if part.name == "gpu-h100")
    assert gpu.gres == "gpu:h100:4"
    assert gpu.nodes == 4


def test_job_script_uses_only_profile_values() -> None:
    profile = _profile()
    request = JobRequest(
        job_name="coperceive-eval-1",
        command_argv=("coperceive", "eval", "run", "run-1"),
        partition="gpu-a100",
        gres="gpu:a100:1",
        time_limit="01:00:00",
        memory="16G",
        cpus_per_task=4,
        log_directory="/data/projects/alpha/logs",
    )
    script = SlurmAdapter(FakeRunner({})).render_job_script(profile, request)
    assert "#SBATCH --partition=gpu-a100" in script
    assert "#SBATCH --gres=gpu:a100:1" in script
    assert "#SBATCH --time=01:00:00" in script
    assert "#SBATCH --mem=16G" in script
    assert "#SBATCH --cpus-per-task=4" in script
    assert "#SBATCH --job-name=coperceive-eval-1" in script
    assert "module load python/3.12" in script
    assert "set -euo pipefail" in script
    for forbidden in ("hpc-node", "utwente", "/datasets/eemcs", "main-gpu", "ps,main"):
        assert forbidden not in script


def test_job_script_requires_a_partition_from_the_user() -> None:
    profile = _profile(defaults=JobDefaults())
    request = JobRequest(job_name="coperceive-x", command_argv=("coperceive", "--version"))
    with pytest.raises(SchedulerError, match="Choose a partition"):
        SlurmAdapter(FakeRunner({})).render_job_script(profile, request)


def test_app_job_names_must_be_prefixed() -> None:
    with pytest.raises(SchedulerError, match="coperceive-"):
        JobRequest(job_name="my-job", command_argv=("true",)).validate()


def test_job_command_arguments_are_quoted_as_data_not_shell_text() -> None:
    nasty = "data/with space/and'quote; touch /tmp/x"
    request = JobRequest(
        job_name="coperceive-x",
        command_argv=("python3", "run.py", "--scene", nasty),
        partition="compute",
    )
    script = SlurmAdapter(FakeRunner({})).render_job_script(_profile(), request)
    last_line = script.strip().splitlines()[-1]
    assert shlex.split(last_line) == list(request.command_argv)
    assert "touch /tmp/x" in last_line  # quoted, never executed as its own command


def test_quote_remote_argv_round_trips_hostile_elements() -> None:
    elements = ("python3", "run.py", "a b", "it's", "; touch /tmp/x", "$HOME", "*")
    quoted = quote_remote_argv(elements)
    assert shlex.split(quoted) == list(elements)
    with pytest.raises(SchedulerError, match="empty argv"):
        quote_remote_argv(())


def test_submit_status_cancel_and_list_own_jobs_use_argv_only() -> None:
    runner = FakeRunner(
        {
            "sbatch": "12345;cluster\n",
            "squeue": "RUNNING\n",
            "scontrol": "JobId=12345 JobName=coperceive-x\n",
            "scancel": "",
        }
    )
    adapter = SlurmAdapter(runner)
    assert adapter.submit("#!/bin/bash\necho hi\n") == "12345"
    assert adapter.status("12345") is JobState.RUNNING
    adapter.cancel("12345")
    assert "JobName=coperceive-x" in adapter.logs("12345")
    # Every call is an argv list, never a single shell string.
    for call in runner.calls:
        assert isinstance(call, list) and all(isinstance(item, str) for item in call)


def test_list_own_jobs_only_returns_app_jobs() -> None:
    runner = FakeRunner({"squeue": "11|coperceive-eval|RUNNING\n12|someone-else|PENDING\n"})
    jobs = SlurmAdapter(runner).list_own_jobs()
    assert jobs == (("11", JobState.RUNNING),)


def test_no_scheduler_profile_refuses_heavy_work_plainly() -> None:
    none = NullAdapter()
    assert none.discover().partitions == ()
    assert none.list_own_jobs() == ()
    with pytest.raises(SchedulerError, match="no scheduler"):
        none.submit("#!/bin/bash\n")
    with pytest.raises(SchedulerError, match="no scheduler"):
        none.cancel("1")
