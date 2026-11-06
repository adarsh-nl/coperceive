# pyright: basic
# This package doubles for an untyped third-party dependency in tests (the real OpenCOOD
# has no annotations either); strict-mode Unknown-type diagnostics do not apply here.
# SPDX-License-Identifier: Apache-2.0
"""``opencood.tools.train_utils`` stand-in: a model object with the calls the engine makes."""

from __future__ import annotations


class _IncompatibleKeys:
    def __init__(self, missing: list[str], unexpected: list[str]) -> None:
        self.missing_keys = missing
        self.unexpected_keys = unexpected


_EXPECTED_KEYS = frozenset({"fake.weight"})


class FakeModel:
    """Records what the engine asked of it; forwards like an OpenCOOD model would.

    ``load_state_dict`` behaves like ``torch.nn.Module``: with ``strict=True`` a state dict
    whose keys do not match the expected parameter set raises, with ``strict=False`` the
    skipped keys are reported. The expected set is the one fixture checkpoint the chain test
    saves.
    """

    def __init__(self, config):  # noqa: ANN001 - an untyped config dict
        self.config = config
        self.loaded_state = None
        self.device = None
        self.evaluated = False
        self.forwarded = None

    def load_state_dict(self, state, strict=False):  # noqa: ANN001, ANN201
        self.loaded_state = state
        missing = sorted(_EXPECTED_KEYS - set(state))
        unexpected = sorted(set(state) - _EXPECTED_KEYS)
        if strict and (missing or unexpected):
            raise RuntimeError(
                "Error(s) in loading state_dict: missing keys "
                f"{missing}; unexpected keys {unexpected}"
            )
        return _IncompatibleKeys(missing, unexpected)

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
