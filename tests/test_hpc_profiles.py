# SPDX-License-Identifier: Apache-2.0
"""Cluster profiles: generic, secret-free, validated, round-trippable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from coperceive.hpc import (
    EnvironmentKind,
    EnvironmentSetup,
    JobDefaults,
    ProfileError,
    SchedulerKind,
    ServerProfile,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hpc"


def _alpha() -> ServerProfile:
    """Fake cluster A: modules + venv, ProxyJump alias, gpu:a100 partitions."""
    return ServerProfile(
        name="Alpha lab",
        ssh_alias="alpha-cluster",
        data_roots=("/data/projects/alpha", "/data/shared"),
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        results_root="/data/projects/alpha/runs",
        code_roots=("/data/projects/alpha/code",),
        scheduler=SchedulerKind.SLURM,
        defaults=JobDefaults(
            partition="gpu-a100",
            gres="gpu:a100:1",
            time_limit="02:00:00",
            memory="16G",
            cpus_per_task=4,
        ),
        environment=EnvironmentSetup(
            kind=EnvironmentKind.MODULES,
            modules=("python/3.12", "cuda/12.4"),
            interpreter="/data/projects/alpha/venv/bin/python",
        ),
    )


def _beta() -> ServerProfile:
    """Fake cluster B: different partitions and GRES names, venv only."""
    return ServerProfile(
        name="Beta cluster",
        host="login.beta.example",
        user="bob",
        data_roots=("/mnt/beta/data",),
        scratch_template="/local/$USER/$SLURM_JOB_ID",
        results_root="/mnt/beta/runs",
        scheduler=SchedulerKind.SLURM,
        defaults=JobDefaults(partition="cpu-short", account="research"),
        environment=EnvironmentSetup(
            kind=EnvironmentKind.VENV,
            interpreter="/mnt/beta/venv/bin/python",
        ),
    )


def test_two_cluster_profiles_round_trip_through_json() -> None:
    for profile in (_alpha(), _beta()):
        text = profile.to_json_text()
        again = ServerProfile.from_json(json.loads(text))
        assert again == profile
        assert again.to_json_text() == text


def test_profile_json_has_no_site_values_or_credentials() -> None:
    text = _alpha().to_json_text().lower()
    for forbidden in ("hpc-node", "utwente", "/datasets/eemcs", "password", "token"):
        assert forbidden not in text
    # The only partition names that appear are the user's own choices.
    assert '"partition": "gpu-a100"' in _alpha().to_json_text()


def test_ssh_target_uses_alias_or_host_and_user() -> None:
    assert _alpha().ssh_target == "alpha-cluster"
    assert _beta().ssh_target == "bob@login.beta.example"


def test_a_server_needs_a_host_or_an_alias() -> None:
    with pytest.raises(ProfileError, match="ssh/config"):
        ServerProfile(name="x", data_roots=("/d",), scratch_template="/s").validate()


def test_alias_and_host_together_are_refused() -> None:
    with pytest.raises(ProfileError, match="either ssh_alias or host"):
        ServerProfile(
            name="x",
            ssh_alias="a",
            host="b",
            data_roots=("/d",),
            scratch_template="/s",
        ).validate()


def test_a_server_needs_a_data_root_scratch_and_results_root() -> None:
    with pytest.raises(ProfileError, match="data root"):
        ServerProfile(name="x", ssh_alias="a", scratch_template="/s").validate()
    with pytest.raises(ProfileError, match="scratch"):
        ServerProfile(name="x", ssh_alias="a", data_roots=("/d",)).validate()
    with pytest.raises(ProfileError, match="results root"):
        ServerProfile(
            name="x",
            ssh_alias="a",
            data_roots=("/d",),
            scratch_template="/s",
        ).validate()
    with pytest.raises(ProfileError, match="absolute or ~ path"):
        ServerProfile(
            name="x",
            ssh_alias="a",
            data_roots=("relative",),
            scratch_template="/s",
            results_root="/runs",
        ).validate()


def test_results_root_is_shared_storage_not_scratch() -> None:
    base = ServerProfile(
        name="x",
        ssh_alias="a",
        data_roots=("/d",),
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        results_root="/scratch/shared",
    )
    with pytest.raises(ProfileError, match="inside the scratch"):
        base.validate()
    good = ServerProfile(
        name="x",
        ssh_alias="a",
        data_roots=("/d",),
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        results_root="/shared/runs",
        code_roots=("/shared/code",),
    )
    good.validate()
    good.validate_output_dir("/shared/runs/run-1")
    with pytest.raises(ProfileError, match="outside results_root"):
        good.validate_output_dir("/elsewhere/run-1")
    with pytest.raises(ProfileError, match="under scratch|outside results_root"):
        good.validate_output_dir("/scratch/$USER/x")
    good.validate_pipeline_script("pipelines/bev.py")
    good.validate_pipeline_script("/shared/code/bev.py")
    with pytest.raises(ProfileError, match="outside code_roots and data_roots"):
        good.validate_pipeline_script("/etc/passwd")
    with pytest.raises(ProfileError, match=r"must not contain a '\.\.' segment"):
        good.validate_pipeline_script("../bev.py")


def test_unknown_json_fields_are_refused_so_credentials_cannot_ride_along() -> None:
    payload = _alpha().to_json()
    payload["password"] = "hunter2"
    with pytest.raises(ProfileError, match="Unknown ServerProfile field"):
        ServerProfile.from_json(payload)
    with pytest.raises(ProfileError, match="Unknown EnvironmentSetup field"):
        EnvironmentSetup.from_json({"kind": "venv", "token": "x"})


def test_environment_kinds_require_their_missing_pieces() -> None:
    with pytest.raises(ProfileError, match="module"):
        EnvironmentSetup(kind=EnvironmentKind.MODULES).validate()
    with pytest.raises(ProfileError, match="python"):
        EnvironmentSetup(kind=EnvironmentKind.CONDA).validate()
    with pytest.raises(ProfileError, match="python"):
        EnvironmentSetup(kind=EnvironmentKind.VENV).validate()
    with pytest.raises(ProfileError, match="image"):
        EnvironmentSetup(kind=EnvironmentKind.CONTAINER).validate()
    # A complete setup is accepted.
    EnvironmentSetup(kind=EnvironmentKind.CONTAINER, container_image="/data/app.sif").validate()


def test_defaults_are_generic_and_validated() -> None:
    with pytest.raises(ProfileError, match="time_limit"):
        JobDefaults(time_limit="two hours").validate()
    with pytest.raises(ProfileError, match="memory"):
        JobDefaults(memory="a lot").validate()
    with pytest.raises(ProfileError, match="whitespace"):
        JobDefaults(partition="gpu a100").validate()
    with pytest.raises(ProfileError, match="at least 1"):
        JobDefaults(cpus_per_task=0).validate()
    # Empty defaults are valid: the adapter asks for a partition instead of guessing.
    JobDefaults().validate()


def test_with_overrides_keeps_the_rest_of_the_profile_values() -> None:
    defaults = JobDefaults(partition="cpu-short", time_limit="01:00:00", cpus_per_task=2)
    merged = defaults.with_overrides(partition="gpu-h100", memory="8G")
    assert merged.partition == "gpu-h100"
    assert merged.time_limit == "01:00:00"
    assert merged.cpus_per_task == 2
    assert merged.memory == "8G"


def test_no_scheduler_profile_is_valid() -> None:
    profile = ServerProfile(
        name="Workstation",
        ssh_alias="workstation",
        data_roots=("/home/bob/coperceive-data",),
        scratch_template="/tmp/$USER/coperceive",
        results_root="/home/bob/coperceive-results",
        scheduler=SchedulerKind.NONE,
    )
    profile.validate()
    assert profile.scheduler is SchedulerKind.NONE


def test_path_traversal_is_refused_after_normalisation() -> None:
    profile = ServerProfile(
        name="Alpha lab",
        ssh_alias="alpha-cluster",
        data_roots=("/data/projects/alpha",),
        code_roots=("/shared/code",),
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        results_root="/shared/runs",
    )
    profile.validate()
    # results_root cannot be smuggled into scratch through ".."
    with pytest.raises(ProfileError, match=r"\.\."):
        ServerProfile(
            name="x",
            ssh_alias="a",
            data_roots=("/d",),
            scratch_template="/scratch/$USER/$SLURM_JOB_ID",
            results_root="/shared/../scratch/shared",
        ).validate()
    # results_root that normalises into scratch is still refused
    with pytest.raises(ProfileError, match="inside the scratch"):
        ServerProfile(
            name="x",
            ssh_alias="a",
            data_roots=("/d",),
            scratch_template="/scratch/$USER/$SLURM_JOB_ID",
            results_root="/scratch/./shared",
        ).validate()
    # A traversing output dir is refused; no lexical escape can fool containment.
    for path in (
        "/shared/runs/../scratch/user/job/out",
        "/shared/runs/../../etc/passwd",
        "/shared/../shared/runs/out",
    ):
        with pytest.raises(ProfileError, match=r"\.\."):
            profile.validate_output_dir(path)
    # Normal noise (duplicate slashes, ".", trailing slash) is normalised, not rejected.
    profile.validate_output_dir("/shared//runs/./run-1/")
    # Script containment: '..' is refused before any normalisation.
    for script in (
        "code/../outside.py",
        "/shared/code/../secret.py",
        "/data/./../etc/x.py",
        "a/../../b.py",
    ):
        with pytest.raises(ProfileError, match=r"\.\."):
            profile.validate_pipeline_script(script)
    profile.validate_pipeline_script("/shared/code/bev.py")
    profile.validate_pipeline_script("pipelines/bev.py")
