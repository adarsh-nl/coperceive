# SPDX-License-Identifier: Apache-2.0
"""Evaluation config: the defaults of ``docs/spec/predictions.md`` §Evaluation config,
key-path validation, and the copy written into ``metrics.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
import yaml
from coperceive.errors import CoperceiveError
from coperceive.evaluation.config import (
    DEFAULT_CONFIG,
    ConfigError,
    EvalConfig,
    config_json,
    display_index,
    load_config,
    parse_config,
    threshold_for,
    threshold_key,
)

SPEC_KEYS = (
    "criterion",
    "thresholds",
    "display_threshold",
    "class_thresholds",
    "ignore_gt_visibility_below",
    "max_range_m",
    "score_threshold",
)


def _centre() -> dict[str, object]:
    """The config of the spec's normative matching cases."""
    return {"criterion": "centre", "thresholds": [2.0, 1.0], "display_threshold": 2.0}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "eval.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- defaults ---------------------------------------------------------------


def test_defaults_are_the_spec_values() -> None:
    assert DEFAULT_CONFIG == EvalConfig(
        criterion="iou_bev",
        thresholds=(0.5, 0.7),
        display_threshold=0.5,
        class_thresholds={},
        ignore_gt_visibility_below=None,
        max_range_m=None,
        score_threshold=0.3,
    )


def test_no_path_gives_the_default() -> None:
    assert load_config(None) is DEFAULT_CONFIG


def test_empty_payload_gives_the_default() -> None:
    assert parse_config(None) == DEFAULT_CONFIG
    assert parse_config({}) == DEFAULT_CONFIG


def test_config_error_is_a_coperceive_error() -> None:
    assert issubclass(ConfigError, CoperceiveError)


# --- key-path validation ------------------------------------------------------


def test_unknown_key_is_an_error() -> None:
    with pytest.raises(ConfigError, match="colour"):
        parse_config({"colour": "red"})


def test_unknown_key_is_reported_before_its_consequences() -> None:
    # A typo in ``thresholds`` must be named, not the display_threshold it breaks.
    with pytest.raises(ConfigError, match="tresholds"):
        parse_config({"tresholds": [2.0, 1.0], "display_threshold": 2.0})


def test_payload_must_be_a_mapping() -> None:
    with pytest.raises(ConfigError, match="mapping"):
        parse_config(["criterion", "centre"])


def test_display_threshold_must_be_one_of_thresholds() -> None:
    with pytest.raises(ConfigError, match="display_threshold"):
        parse_config({"display_threshold": 0.6})


def test_class_thresholds_need_one_entry_per_threshold() -> None:
    with pytest.raises(ConfigError, match="pedestrian"):
        parse_config({"class_thresholds": {"pedestrian": [0.25]}})


def test_class_thresholds_follow_the_criterion_order() -> None:
    with pytest.raises(ConfigError, match="pedestrian"):
        parse_config({"class_thresholds": {"pedestrian": [0.5, 0.25]}})


def test_class_thresholds_must_be_a_mapping_of_lists() -> None:
    with pytest.raises(ConfigError, match="class_thresholds"):
        parse_config({"class_thresholds": [0.25, 0.5]})
    with pytest.raises(ConfigError, match=r"class_thresholds\.car"):
        parse_config({"class_thresholds": {"car": 0.5}})


def test_iou_thresholds_must_strictly_increase() -> None:
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"criterion": "iou_bev", "thresholds": [0.7, 0.5]})
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"criterion": "iou_bev", "thresholds": [0.5, 0.5]})


def test_centre_thresholds_must_strictly_decrease() -> None:
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"criterion": "centre", "thresholds": [1.0, 2.0], "display_threshold": 1.0})


def test_thresholds_must_be_a_non_empty_list_of_numbers() -> None:
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"thresholds": []})
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"thresholds": 0.5, "display_threshold": 0.5})
    with pytest.raises(ConfigError, match=r"thresholds\[1\]"):
        parse_config({"thresholds": [0.5, "0.7"]})
    with pytest.raises(ConfigError, match=r"thresholds\[1\]"):
        parse_config({"thresholds": [0.5, True]})


def test_thresholds_are_finite_and_in_range() -> None:
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"thresholds": [0.5, float("nan")]})
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"thresholds": [0.5, 1.5], "display_threshold": 0.5})
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({"thresholds": [0.0, 0.5], "display_threshold": 0.5})
    with pytest.raises(ConfigError, match="thresholds"):
        parse_config({**_centre(), "thresholds": [2.0, -1.0]})


def test_criterion_must_be_known() -> None:
    with pytest.raises(ConfigError, match="criterion"):
        parse_config({"criterion": "iou_3d"})


def test_optional_numbers_are_null_or_finite() -> None:
    cfg = parse_config({"ignore_gt_visibility_below": 0.5, "max_range_m": 50})
    assert cfg.ignore_gt_visibility_below == 0.5
    assert cfg.max_range_m == 50.0
    with pytest.raises(ConfigError, match="ignore_gt_visibility_below"):
        parse_config({"ignore_gt_visibility_below": "low"})
    with pytest.raises(ConfigError, match="max_range_m"):
        parse_config({"max_range_m": -1})
    with pytest.raises(ConfigError, match="max_range_m"):
        parse_config({"max_range_m": float("inf")})


def test_score_threshold_is_a_finite_number() -> None:
    assert parse_config({"score_threshold": -2}).score_threshold == -2.0  # a logit is allowed
    with pytest.raises(ConfigError, match="score_threshold"):
        parse_config({"score_threshold": "0.3"})
    with pytest.raises(ConfigError, match="score_threshold"):
        parse_config({"score_threshold": True})


# --- lookups ---------------------------------------------------------------------


def test_centre_config_loads_and_indexes_thresholds() -> None:
    cfg = parse_config({**_centre(), "class_thresholds": {"pedestrian": [1.0, 0.5]}})
    assert cfg.criterion == "centre"
    assert cfg.thresholds == (2.0, 1.0)
    assert threshold_for(cfg, "car", 1) == 1.0
    assert threshold_for(cfg, "car", 0) == 2.0
    assert threshold_for(cfg, "pedestrian", 0) == 1.0
    assert threshold_for(cfg, "pedestrian", 1) == 0.5
    assert display_index(cfg) == 0


def test_display_index_follows_display_threshold() -> None:
    assert display_index(DEFAULT_CONFIG) == 0
    assert display_index(parse_config({"display_threshold": 0.7})) == 1


def test_threshold_key_is_the_shortest_repr() -> None:
    assert threshold_key(0.5) == "0.5"
    assert threshold_key(2.0) == "2.0"
    assert threshold_key(2) == "2.0"
    assert threshold_key(0.7) == "0.7"
    assert threshold_key(0.25) == "0.25"


# --- metrics.json copy -------------------------------------------------------


def test_config_json_has_every_spec_key() -> None:
    payload = config_json(DEFAULT_CONFIG)
    assert tuple(payload) == SPEC_KEYS
    assert payload == {
        "criterion": "iou_bev",
        "thresholds": [0.5, 0.7],
        "display_threshold": 0.5,
        "class_thresholds": {},
        "ignore_gt_visibility_below": None,
        "max_range_m": None,
        "score_threshold": 0.3,
    }


def test_config_json_round_trips_through_parse_config() -> None:
    assert parse_config(config_json(DEFAULT_CONFIG)) == DEFAULT_CONFIG
    cfg = parse_config(
        {
            **_centre(),
            "class_thresholds": {"pedestrian": [1.0, 0.5], "car": [3.0, 1.5]},
            "ignore_gt_visibility_below": 0.25,
            "max_range_m": 50,
            "score_threshold": 0.4,
        }
    )
    text = json.dumps(config_json(cfg))
    assert "NaN" not in text and "Infinity" not in text
    assert parse_config(json.loads(text)) == cfg
    classes = cast(dict[str, object], config_json(cfg)["class_thresholds"])
    assert tuple(classes) == ("car", "pedestrian")  # sorted, so metrics.json is deterministic


# --- YAML file --------------------------------------------------------------------


def test_load_config_reads_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, yaml.safe_dump({**_centre(), "score_threshold": 0.5}))
    cfg = load_config(path)
    assert cfg.criterion == "centre"
    assert cfg.thresholds == (2.0, 1.0)
    assert cfg.display_threshold == 2.0
    assert cfg.score_threshold == 0.5
    assert cfg.class_thresholds == {}


def test_load_config_of_an_empty_file_is_the_default(tmp_path: Path) -> None:
    assert load_config(_write(tmp_path, "")) == DEFAULT_CONFIG


def test_load_config_names_the_file_on_bad_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "thresholds: [0.5, 0.7\n")
    with pytest.raises(ConfigError, match="eval.yaml"):
        load_config(path)


def test_load_config_rejects_an_unknown_key_from_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="colour"):
        load_config(_write(tmp_path, "colour: red\n"))
