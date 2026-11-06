# SPDX-License-Identifier: Apache-2.0
"""``opencood.tools.train_utils`` stand-in: a model object with the calls the engine makes."""

from __future__ import annotations


class _IncompatibleKeys:
    missing_keys: list[str] = []
    unexpected_keys: list[str] = []


class FakeModel:
    """Records what the engine asked of it; forwards like an OpenCOOD model would."""

    def __init__(self, config):  # noqa: ANN001 - an untyped config dict
        self.config = config
        self.loaded_state = None
        self.device = None
        self.evaluated = False
        self.forwarded = None

    def load_state_dict(self, state, strict=False):  # noqa: ANN001, ANN201
        self.loaded_state = state
        return _IncompatibleKeys()

    def to(self, device):  # noqa: ANN001, ANN201
        self.device = device
        return self

    def eval(self):  # noqa: ANN201
        self.evaluated = True
        return self

    def __call__(self, data):  # noqa: ANN001, ANN201
        self.forwarded = data
        return {"psm": None, "rm": None, "fake": True}


def create_model(config):  # noqa: ANN001, ANN201
    return FakeModel(config)


def to_device(inputs, device):  # noqa: ANN001, ANN201 - nested dict/list move, as upstream
    if isinstance(inputs, list):
        return [to_device(item, device) for item in inputs]
    if isinstance(inputs, dict):
        return {key: to_device(value, device) for key, value in inputs.items()}
    return inputs
