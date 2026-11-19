# SPDX-License-Identifier: Apache-2.0
"""The perturbation spec recorded with a run.

A spec is one JSON object. ``seed`` fixes every random draw. The other fields
are the four perturbation types; a null field is off. Parsing accepts
``latency_frames=2,pose_noise_xy=0.2,seed=7``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from coperceive.errors import CoperceiveError

_KEYS = (
    "latency_frames",
    "latency_ms",
    "packet_loss",
    "pose_noise_xy",
    "pose_noise_yaw",
    "max_agents",
    "agent_dropout",
    "seed",
)


class PerturbationError(CoperceiveError):
    """The perturbation spec is malformed. The message names the field."""


@dataclass(frozen=True)
class PerturbationSpec:
    """One seeded perturbation of the cooperating agents. The ego is never changed."""

    seed: int = 0
    latency_frames: int | None = None
    latency_ms: float | None = None
    packet_loss: float | None = None
    pose_noise_xy: float | None = None
    pose_noise_yaw: float | None = None
    max_agents: int | None = None
    agent_dropout: float | None = None

    def __post_init__(self) -> None:
        _seed(self.seed)
        _optional_int(self.latency_frames, "latency_frames", minimum=0)
        _optional_number(self.latency_ms, "latency_ms", minimum=0.0)
        _optional_number(self.packet_loss, "packet_loss", minimum=0.0, maximum=1.0)
        _optional_number(self.pose_noise_xy, "pose_noise_xy", minimum=0.0)
        _optional_number(self.pose_noise_yaw, "pose_noise_yaw", minimum=0.0)
        _optional_int(self.max_agents, "max_agents", minimum=1)
        _optional_number(self.agent_dropout, "agent_dropout", minimum=0.0, maximum=1.0)

    def to_json(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "latency_frames": self.latency_frames,
            "latency_ms": self.latency_ms,
            "packet_loss": self.packet_loss,
            "pose_noise_xy": self.pose_noise_xy,
            "pose_noise_yaw": self.pose_noise_yaw,
            "max_agents": self.max_agents,
            "agent_dropout": self.agent_dropout,
        }

    def canonical(self) -> str:
        """Stable ``key=value`` text, null fields omitted, ``seed`` always present."""
        parts: list[str] = []
        for key in _KEYS:
            value = self.to_json()[key]
            if value is None:
                continue
            parts.append(f"{key}={_format(value)}")
        return ",".join(parts)

    @staticmethod
    def parse(text: str) -> PerturbationSpec:
        """``key=value`` pairs separated by commas. An unknown key is an error."""
        if text.strip() == "":
            raise PerturbationError("perturbation spec is empty")
        raw: dict[str, str] = {}
        for part in text.split(","):
            key, sep, value = part.strip().partition("=")
            if sep == "" or key == "" or value == "":
                raise PerturbationError(f"perturbation entry {part!r} is not key=value")
            if key in raw:
                raise PerturbationError(f"perturbation key {key!r} is repeated")
            if key not in _KEYS:
                raise PerturbationError(f"unknown perturbation key {key!r}")
            raw[key] = value
        return PerturbationSpec(
            seed=_int_text(raw, "seed", 0),
            latency_frames=_optional_int_text(raw, "latency_frames"),
            latency_ms=_optional_float_text(raw, "latency_ms"),
            packet_loss=_optional_float_text(raw, "packet_loss"),
            pose_noise_xy=_optional_float_text(raw, "pose_noise_xy"),
            pose_noise_yaw=_optional_float_text(raw, "pose_noise_yaw"),
            max_agents=_optional_int_text(raw, "max_agents"),
            agent_dropout=_optional_float_text(raw, "agent_dropout"),
        )

    @staticmethod
    def from_json(payload: Mapping[str, object]) -> PerturbationSpec:
        unexpected = sorted(set(payload) - set(_KEYS))
        if unexpected:
            raise PerturbationError(f"unknown perturbation key {unexpected[0]!r}")
        return PerturbationSpec(
            seed=_int_value(payload.get("seed", 0), "seed"),
            latency_frames=_optional_int_value(payload.get("latency_frames"), "latency_frames"),
            latency_ms=_optional_float_value(payload.get("latency_ms"), "latency_ms"),
            packet_loss=_optional_float_value(payload.get("packet_loss"), "packet_loss"),
            pose_noise_xy=_optional_float_value(payload.get("pose_noise_xy"), "pose_noise_xy"),
            pose_noise_yaw=_optional_float_value(payload.get("pose_noise_yaw"), "pose_noise_yaw"),
            max_agents=_optional_int_value(payload.get("max_agents"), "max_agents"),
            agent_dropout=_optional_float_value(payload.get("agent_dropout"), "agent_dropout"),
        )


def load_perturbation(text: str) -> PerturbationSpec:
    """JSON object or ``key=value`` text."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            loaded: object = json.loads(stripped)
        except ValueError as error:
            raise PerturbationError(f"perturbation JSON: {error}") from error
        if not isinstance(loaded, dict):
            raise PerturbationError("perturbation JSON must be an object")
        return PerturbationSpec.from_json(cast(dict[str, object], loaded))
    return PerturbationSpec.parse(stripped)


def _format(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number == int(number):
        return str(int(number))
    return format(number, "g")


def _seed(value: int) -> None:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise PerturbationError("seed must be an integer >= 0")


def _optional_int(value: int | None, name: str, *, minimum: int) -> None:
    if value is None:
        return
    if isinstance(value, bool) or type(value) is not int or value < minimum:
        raise PerturbationError(f"{name} must be an integer >= {minimum}")


def _optional_number(
    value: float | None, name: str, *, minimum: float, maximum: float | None = None
) -> None:
    if value is None:
        return
    number = float(value)
    if number != number or number < minimum or (maximum is not None and number > maximum):
        limit = f" <= {maximum}" if maximum is not None else ""
        raise PerturbationError(f"{name} must be >= {minimum}{limit}")


def _int_text(raw: Mapping[str, str], name: str, default: int) -> int:
    if name not in raw:
        return default
    return _int_value(raw[name], name)


def _optional_int_text(raw: Mapping[str, str], name: str) -> int | None:
    if name not in raw:
        return None
    return _int_value(raw[name], name)


def _optional_float_text(raw: Mapping[str, str], name: str) -> float | None:
    if name not in raw:
        return None
    return _float_value(raw[name], name)


def _int_value(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise PerturbationError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    raise PerturbationError(f"{name} must be an integer")


def _optional_int_value(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _int_value(value, name)


def _optional_float_value(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _float_value(value, name)


def _float_value(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise PerturbationError(f"{name} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise PerturbationError(f"{name} must be a number")
