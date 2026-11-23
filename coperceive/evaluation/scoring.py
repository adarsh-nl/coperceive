# SPDX-License-Identifier: Apache-2.0
"""Score one prediction run, the one way ``eval run`` and ``model run --evaluate`` share.

:func:`score_run` resolves the evaluation config exactly as ``coperceive eval run`` does
(``--protocol``, else the config file's ``protocol``, else the ``eval_protocol`` the run
recorded in ``run.json``, else the default), evaluates every scene, and writes
``<predictions>/<run_id>/evaluation.json``: that scoring ran, under which protocol and
config (its SHA-256), with what result (docs/spec/predictions.md, §``evaluation.json``).
The record is written on failure as well, so an older record never outlives a rerun.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from coperceive.evaluation.config import EvalConfig, config_json, load_config
from coperceive.evaluation.evaluate import RunReport, evaluate_run
from coperceive.evaluation.predictions import (
    RUN_FILE,
    PredictionError,
    check_run_id,
    read_run_meta,
)
from coperceive.evaluation.report import config_protocol_label

EVALUATION_FILE = "evaluation.json"
RECORD_FORMAT = "coperceive-evaluation"
RECORD_FORMAT_VERSION = 1


@dataclass(frozen=True)
class Scoring:
    """What :func:`score_run` did: the report, and the config it scored under."""

    report: RunReport
    config: EvalConfig

    @property
    def ok(self) -> bool:
        return not self.report.errors


def recorded_protocol(predictions_root: Path, run_id: str) -> str | None:
    """The ``eval_protocol`` that ``run_id``'s ``run.json`` records, if any."""
    try:
        provenance = read_run_meta(predictions_root, run_id).provenance or {}
    except PredictionError:
        # A missing or malformed run.json gets evaluate_run's own message.
        return None
    value = provenance.get("eval_protocol")
    return value if isinstance(value, str) else None


def resolve_config(
    config_path: Path | None,
    protocol: str | None,
    *,
    predictions_root: Path | None = None,
    run_id: str | None = None,
) -> EvalConfig:
    """The config ``eval run`` would use. Without a run to read, the recorded protocol is
    unknown: the config is checked (file, and its agreement with ``--protocol``) only."""
    recorded = None
    if predictions_root is not None and run_id is not None:
        recorded = recorded_protocol(predictions_root, run_id)
    return load_config(config_path, protocol, recorded=recorded)


def config_sha256(config: EvalConfig) -> str:
    """SHA-256 of the resolved config as ``metrics.json`` writes it under ``config``
    (canonical JSON: sorted keys, no spaces), so a reader can check one against the other."""
    text = json.dumps(config_json(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def score_run(
    scenes_root: Path,
    predictions_root: Path,
    run_id: str,
    *,
    config_path: Path | None,
    protocol: str | None,
    scene_ids: Sequence[str] | None,
    created_utc: str,
    command: str,
) -> Scoring:
    """Evaluate ``run_id`` and write its ``evaluation.json``.

    A user error (bad config, no ``run.json``, ...) is written into the record as
    ``status: failed`` when the run directory exists, then raised unchanged.
    """
    check_run_id(run_id)
    try:
        config = resolve_config(
            config_path, protocol, predictions_root=predictions_root, run_id=run_id
        )
        report = evaluate_run(
            scenes_root,
            predictions_root,
            run_id,
            config,
            scene_ids=scene_ids,
            created_utc=created_utc,
        )
    except Exception as exc:
        _write_record(
            predictions_root,
            run_id,
            _record(
                run_id,
                command=command,
                created_utc=created_utc,
                config=None,
                config_path=config_path,
                report=None,
                error=str(exc),
            ),
        )
        raise
    _write_record(
        predictions_root,
        run_id,
        _record(
            run_id,
            command=command,
            created_utc=created_utc,
            config=config,
            config_path=config_path,
            report=report,
            error=None,
        ),
    )
    return Scoring(report=report, config=config)


def read_record(predictions_root: Path, run_id: str) -> dict[str, object] | None:
    """The run's ``evaluation.json``, or ``None`` when it was never scored."""
    path = predictions_root / check_run_id(run_id) / EVALUATION_FILE
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _record(
    run_id: str,
    *,
    command: str,
    created_utc: str,
    config: EvalConfig | None,
    config_path: Path | None,
    report: RunReport | None,
    error: str | None,
) -> dict[str, object]:
    if error is not None:
        status = "failed"
    elif report is not None and report.errors:
        status = "errors"
    else:
        status = "ok"
    file_digest = None
    if config_path is not None and config_path.is_file():
        file_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    return {
        "format": RECORD_FORMAT,
        "format_version": RECORD_FORMAT_VERSION,
        "run_id": run_id,
        "command": command,
        "created_utc": created_utc,
        "status": status,
        "error": error,
        "protocol": None if config is None else config_protocol_label(config_json(config)),
        "ranking_scope": None if config is None else config.ranking_scope,
        "gt_scope": None if config is None else config.gt_scope,
        "config_sha256": None if config is None else config_sha256(config),
        "config_file": None if config_path is None else str(config_path),
        "config_file_sha256": file_digest,
        "evaluated": [] if report is None else list(report.evaluated),
        "skipped": [] if report is None else list(report.skipped),
        "errors": [] if report is None else list(report.errors),
    }


def _write_record(predictions_root: Path, run_id: str, record: dict[str, object]) -> None:
    directory = predictions_root / run_id
    if not (directory / RUN_FILE).is_file():
        return
    (directory / EVALUATION_FILE).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


__all__ = [
    "EVALUATION_FILE",
    "RECORD_FORMAT",
    "RECORD_FORMAT_VERSION",
    "Scoring",
    "config_sha256",
    "read_record",
    "recorded_protocol",
    "resolve_config",
    "score_run",
]
