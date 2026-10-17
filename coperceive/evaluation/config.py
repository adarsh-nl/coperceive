# SPDX-License-Identifier: Apache-2.0
"""Evaluation config: defaults, YAML loader, validation and the ``metrics.json`` copy.

The config is the §Evaluation config block of ``docs/spec/predictions.md``. Every
key is checked and an unknown key is an error that names it, so a typo cannot
fall back to a default. The parsed object goes into ``metrics.json`` under
``config`` with every key present, so a reader of that file can say which rules
produced a number.

Beyond the spec's ordering rules, a threshold has to be finite and positive, and
an IoU threshold at most 1: an IoU threshold of 0 would make every prediction
that has any candidate box a true positive.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from coperceive.errors import CoperceiveError

CRITERIA = ("iou_bev", "centre")
KEYS = (
    "criterion",
    "thresholds",
    "display_threshold",
    "class_thresholds",
    "ignore_gt_visibility_below",
    "max_range_m",
    "score_threshold",
)


class ConfigError(CoperceiveError):
    """The evaluation config is malformed. The message starts with the key path."""


@dataclass(frozen=True)
class EvalConfig:
    criterion: str  # iou_bev | centre
    thresholds: tuple[float, ...]  # IoU or metres; AP is computed at each
    display_threshold: float  # one of thresholds; statuses are stored at it
    class_thresholds: Mapping[str, tuple[float, ...]]  # per class, same length
    ignore_gt_visibility_below: float | None
    max_range_m: float | None
    score_threshold: float  # the operating point


DEFAULT_CONFIG = EvalConfig(
    criterion="iou_bev",
    thresholds=(0.5, 0.7),
    display_threshold=0.5,
    class_thresholds=MappingProxyType({}),
    ignore_gt_visibility_below=None,
    max_range_m=None,
    score_threshold=0.3,
)


def load_config(path: Path | None) -> EvalConfig:
    """Read a YAML config; ``None`` is the default config. An empty file is too."""
    if path is None:
        return DEFAULT_CONFIG
    import yaml

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse_config(payload)


def parse_config(payload: object) -> EvalConfig:
    """Validate a decoded YAML or JSON object. Absent keys take the spec defaults."""
    node = _mapping("", payload)
    unknown = sorted(set(node) - set(KEYS))
    if unknown:
        raise ConfigError(f"unknown key {unknown[0]!r}")
    criterion = _choice("criterion", node.get("criterion", DEFAULT_CONFIG.criterion), CRITERIA)
    if "thresholds" in node:
        thresholds = _thresholds("thresholds", node["thresholds"], criterion, count=None)
    else:
        thresholds = DEFAULT_CONFIG.thresholds
    display = _number(
        "display_threshold", node.get("display_threshold", DEFAULT_CONFIG.display_threshold)
    )
    if display not in thresholds:
        raise ConfigError(
            f"display_threshold: must be one of thresholds {list(thresholds)}, got {display!r}"
        )
    classes: dict[str, tuple[float, ...]] = {}
    for name, value in sorted(_mapping("class_thresholds", node.get("class_thresholds")).items()):
        path = f"class_thresholds.{name}"
        classes[name] = _thresholds(path, value, criterion, count=len(thresholds))
    visibility = _optional_number(
        "ignore_gt_visibility_below", node.get("ignore_gt_visibility_below")
    )
    max_range = _optional_number("max_range_m", node.get("max_range_m"))
    if max_range is not None and max_range <= 0:
        raise ConfigError(f"max_range_m: expected a positive number of metres, got {max_range!r}")
    score = _number("score_threshold", node.get("score_threshold", DEFAULT_CONFIG.score_threshold))
    return EvalConfig(
        criterion=criterion,
        thresholds=thresholds,
        display_threshold=display,
        class_thresholds=MappingProxyType(classes),
        ignore_gt_visibility_below=visibility,
        max_range_m=max_range,
        score_threshold=score,
    )


def config_json(config: EvalConfig) -> dict[str, object]:
    """The object written into ``metrics.json`` under ``config``: every key, plain JSON."""
    return {
        "criterion": config.criterion,
        "thresholds": [float(t) for t in config.thresholds],
        "display_threshold": float(config.display_threshold),
        "class_thresholds": {
            name: [float(t) for t in values] for name, values in config.class_thresholds.items()
        },
        "ignore_gt_visibility_below": config.ignore_gt_visibility_below,
        "max_range_m": config.max_range_m,
        "score_threshold": float(config.score_threshold),
    }


def threshold_for(config: EvalConfig, category: str, index: int) -> float:
    """The threshold at ``index`` for ``category``: its class entry, else the global one."""
    return config.class_thresholds.get(category, config.thresholds)[index]


def display_index(config: EvalConfig) -> int:
    """The index of ``display_threshold`` in ``thresholds``."""
    return config.thresholds.index(config.display_threshold)


def threshold_key(value: float) -> str:
    """The ``metrics.json`` key of a threshold: the shortest repr, ``"0.5"`` or ``"2.0"``."""
    return repr(float(value))


# --- validation helpers ----------------------------------------------------------


def _mapping(path: str, value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path or 'config'}: expected a mapping, got {type(value).__name__}")
    out: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str) or key == "":
            raise ConfigError(f"{path or 'config'}: keys are non-empty strings, got {key!r}")
        out[key] = item
    return out


def _choice(path: str, value: object, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ConfigError(f"{path}: expected one of {', '.join(choices)}, got {value!r}")
    return value


def _number(path: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: expected a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{path}: expected a finite number, got {value!r}")
    return number


def _optional_number(path: str, value: object) -> float | None:
    if value is None:
        return None
    return _number(path, value)


def _thresholds(
    path: str, value: object, criterion: str, *, count: int | None
) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected a list of numbers, got {value!r}")
    items = tuple(_number(f"{path}[{i}]", v) for i, v in enumerate(cast(list[object], value)))
    if not items:
        raise ConfigError(f"{path}: expected at least one threshold")
    if count is not None and len(items) != count:
        raise ConfigError(
            f"{path}: expected {count} thresholds, one per entry of thresholds, got {len(items)}"
        )
    for i, item in enumerate(items):
        if item <= 0:
            raise ConfigError(f"{path}[{i}]: a threshold is positive, got {item!r}")
        if criterion == "iou_bev" and item > 1:
            raise ConfigError(f"{path}[{i}]: an IoU threshold is at most 1, got {item!r}")
    steps = tuple(zip(items[:-1], items[1:], strict=True))
    if criterion == "iou_bev" and not all(a < b for a, b in steps):
        raise ConfigError(f"{path}: iou_bev thresholds are strictly increasing, got {list(items)}")
    if criterion == "centre" and not all(a > b for a, b in steps):
        raise ConfigError(f"{path}: centre thresholds are strictly decreasing, got {list(items)}")
    return items
