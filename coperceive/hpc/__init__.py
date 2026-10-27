# SPDX-License-Identifier: Apache-2.0
"""Cluster profiles, scheduler adapters and pipeline registration.

This package is the product-neutral HPC workspace layer. Nothing here knows a
specific cluster: hosts, partitions, accounts, QoS, GRES names, scratch paths and
environment setup all come from a :class:`ServerProfile` the user creates, and
partitions/GPU types are discovered from the scheduler read-only.

The Slurm adapter is the first implementation; PBS, LSF and a plain-SSH "no
scheduler" target are documented extension points. See
``docs/adr/0015-cluster-profiles-scheduler-adapter.md``.
"""

from coperceive.hpc.pipeline import (
    ArgumentKind,
    ArgumentSpec,
    PipelineSpec,
    PipelineValidationError,
)
from coperceive.hpc.profile import (
    EnvironmentKind,
    EnvironmentSetup,
    JobDefaults,
    ProfileError,
    SchedulerKind,
    ServerProfile,
)
from coperceive.hpc.scheduler import (
    Discovery,
    JobRequest,
    JobState,
    NullAdapter,
    Partition,
    SchedulerAdapter,
    SchedulerError,
    SlurmAdapter,
    parse_scontrol_partition,
    parse_sinfo,
    quote_remote_argv,
)

__all__ = [
    "ArgumentKind",
    "ArgumentSpec",
    "Discovery",
    "EnvironmentKind",
    "EnvironmentSetup",
    "JobDefaults",
    "JobRequest",
    "JobState",
    "NullAdapter",
    "Partition",
    "PipelineSpec",
    "PipelineValidationError",
    "ProfileError",
    "SchedulerAdapter",
    "SchedulerError",
    "SchedulerKind",
    "ServerProfile",
    "SlurmAdapter",
    "parse_scontrol_partition",
    "parse_sinfo",
    "quote_remote_argv",
]
