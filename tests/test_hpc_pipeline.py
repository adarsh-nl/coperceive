# SPDX-License-Identifier: Apache-2.0
"""User-registered pipelines: typed arguments, argv only, open output contract."""

from __future__ import annotations

import pytest
from coperceive.hpc import (
    ArgumentKind,
    ArgumentSpec,
    EnvironmentKind,
    EnvironmentSetup,
    PipelineSpec,
    PipelineValidationError,
)
from coperceive.hpc.pipeline import OUTPUT_CONTRACT, pipeline_from_json


def _pipeline() -> PipelineSpec:
    return PipelineSpec(
        name="bev-fusion",
        script="pipelines/bev_fusion.py",
        interpreter=("python3",),
        environment=EnvironmentSetup(
            kind=EnvironmentKind.VENV, interpreter="/data/alpha/venv/bin/python"
        ),
        arguments=(
            ArgumentSpec(name="checkpoint", kind=ArgumentKind.PATH, required=True),
            ArgumentSpec(name="split", kind=ArgumentKind.ENUM, choices=("train", "val", "test")),
            ArgumentSpec(name="score_threshold", kind=ArgumentKind.FLOAT, default=0.3),
            ArgumentSpec(name="frames", kind=ArgumentKind.INT, default=10),
            ArgumentSpec(name="cooperative", kind=ArgumentKind.BOOL, default=False),
            ArgumentSpec(name="categories", kind=ArgumentKind.STR, multiple=True),
        ),
        description="A user's own fusion model; coperceive does not import its code.",
    )


def test_spec_is_valid_and_has_the_open_contract() -> None:
    pipeline = _pipeline()
    pipeline.validate()
    assert pipeline.output_contract == OUTPUT_CONTRACT == "coperceive-pipeline-1"


def test_argument_names_are_checked() -> None:
    with pytest.raises(PipelineValidationError, match="name"):
        ArgumentSpec(name="Bad Name", kind=ArgumentKind.STR).validate()
    with pytest.raises(PipelineValidationError, match="credential"):
        ArgumentSpec(name="api_token", kind=ArgumentKind.STR).validate()
    with pytest.raises(PipelineValidationError, match="choices"):
        ArgumentSpec(name="mode", kind=ArgumentKind.ENUM).validate()
    with pytest.raises(PipelineValidationError, match="not an enum"):
        ArgumentSpec(name="mode", kind=ArgumentKind.STR, choices=("a",)).validate()
    with pytest.raises(PipelineValidationError, match="required and has a default"):
        ArgumentSpec(name="x", kind=ArgumentKind.STR, required=True, default="y").validate()


def test_values_are_typed_validated_and_complete() -> None:
    pipeline = _pipeline()
    typed = pipeline.validate_values({"checkpoint": "/data/ckpt.pt", "split": "val"})
    assert typed["checkpoint"] == "/data/ckpt.pt"
    assert typed["split"] == "val"
    assert typed["score_threshold"] == pytest.approx(0.3)
    assert typed["frames"] == 10
    assert typed["cooperative"] is False
    with pytest.raises(PipelineValidationError, match="required"):
        pipeline.validate_values({})
    with pytest.raises(PipelineValidationError, match="Unknown pipeline argument"):
        pipeline.validate_values({"checkpoint": "c", "split": "val", "nope": 1})
    with pytest.raises(PipelineValidationError, match="whole number"):
        pipeline.validate_values({"checkpoint": "c", "split": "val", "frames": "ten"})
    with pytest.raises(PipelineValidationError, match="one of"):
        pipeline.validate_values({"checkpoint": "c", "split": "holdout"})


def test_build_argv_passes_values_as_separate_elements_only() -> None:
    pipeline = _pipeline()
    nasty = "checkpoints/with space/and'; touch /tmp/x.sh"
    argv = pipeline.build_argv(
        {
            "checkpoint": nasty,
            "split": "test",
            "frames": 3,
            "cooperative": True,
            "categories": ["car", "truck"],
        },
        output_dir="/data/runs/out",
    )
    assert argv[0] == "python3"
    assert argv[1] == "pipelines/bev_fusion.py"
    assert nasty in argv
    # One element per value: the dangerous text is data, never its own command.
    assert not any(element.startswith(";") for element in argv)
    assert argv[-2:] == ("--output-dir", "/data/runs/out")
    index = argv.index(nasty)
    assert argv[index - 1] == "--checkpoint"


def test_bool_and_multiple_arguments_are_rendered_predictably() -> None:
    pipeline = _pipeline()
    argv = pipeline.build_argv(
        {
            "checkpoint": "c.pt",
            "split": "val",
            "cooperative": True,
            "categories": ["car", "bus"],
        },
        output_dir="/out",
    )
    assert "--cooperative" in argv
    assert "true" not in argv  # a true bool is a flag, not a value
    pairs = list(zip(argv, argv[1:], strict=False))
    assert ("--categories", "car") in pairs
    assert ("--categories", "bus") in pairs


def test_a_pipeline_cannot_point_outside_the_data_roots() -> None:
    for script in ("/etc/passwd", "../outside.py", "a/../../b.py"):
        with pytest.raises(PipelineValidationError, match="data roots"):
            PipelineSpec(name="p", script=script).validate()


def test_json_registration_round_trips_and_refuses_secrets() -> None:
    data = {
        "name": "bev-fusion",
        "script": "pipelines/bev_fusion.py",
        "interpreter": ["python3"],
        "environment": {"kind": "venv", "interpreter": "/data/alpha/venv/bin/python"},
        "arguments": [
            {"name": "checkpoint", "kind": "path", "required": True},
            {"name": "split", "kind": "enum", "choices": ["train", "val"]},
        ],
        "description": "user model",
    }
    pipeline = pipeline_from_json(data)
    assert pipeline.validate_values({"checkpoint": "c.pt", "split": "val"})["split"] == "val"
    with_secret = dict(data)
    with_secret["api_token"] = "x"
    with pytest.raises(PipelineValidationError, match="Unknown pipeline field"):
        pipeline_from_json(with_secret)
    credential_argument = dict(data)
    credential_argument["arguments"] = [{"name": "api_token", "kind": "str"}]
    with pytest.raises(PipelineValidationError, match="credential"):
        pipeline_from_json(credential_argument)


def test_unknown_output_contract_is_refused() -> None:
    with pytest.raises(PipelineValidationError, match="output contract"):
        PipelineSpec(name="p", script="p.py", output_contract="other").validate()
