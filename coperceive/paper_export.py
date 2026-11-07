# SPDX-License-Identifier: Apache-2.0
"""Export one scored run as a zip a paper can drop into Overleaf.

Tables are booktabs and use siunitx-friendly numbers. Figures are PDF and SVG
in one colour-blind-safe style. Qualitative frames are drawn only for datasets
on the allow list. The list defaults to synthetic fixtures. Griffin and
CARLA-derived scenes can be opted in. OPV2V, V2XSet and V2V4Real are never
drawn.

Comparison breakdowns (``coperceive-compare``), failure rankings
(``coperceive-failures``) and repro cards (``coperceive-repro-card``) are read
when a file is passed or sits in the run directory. A missing file becomes a
plain note in the zip, not a failed export. Degradation curves use
``coperceive-degradation`` JSON, which a robustness sweep can write later.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import cast

from coperceive.adapters.paths import PathError, confine
from coperceive.errors import CoperceiveError
from coperceive.evaluation.evaluate import read_metrics
from coperceive.evaluation.predictions import PredictionError, check_run_id, check_scene_id
from coperceive.plot import PredictionDraw

FORMAT = "coperceive-paper-export"
FORMAT_VERSION = 1
COMPARE_FORMAT = "coperceive-compare"
FAILURES_FORMAT = "coperceive-failures"
DEGRADATION_FORMAT = "coperceive-degradation"
REPRO_SCHEMA = "coperceive-repro-card"
CARD_ID = re.compile(r"^card-[a-f0-9]{12}$")

# Alphanumeric keys. Hyphens and spaces are stripped before the check, so
# "V2X-Set" and "v2xset" are the same refusal.
FORBIDDEN_DATASETS = frozenset({"opv2v", "v2xset", "v2v4real"})
DEFAULT_ALLOW = frozenset({"fixture", "synthetic", "demosynthetic"})
OPT_IN_DATASETS = frozenset({"griffin", "carla"})

METRIC_NAMES = ("precision", "recall", "ap", "recovered", "lost_by_fusion")
LOWER_IS_BETTER = frozenset({"lost_by_fusion"})
_JSON_LIMIT = 20_000_000
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

NOTE_METRICS = (
    "No scored run was found, so the metric table and precision-recall curve were left out."
)
NOTE_COMPARISON = (
    "Comparison was not provided, so the comparison table and breakdown chart were left out."
)
NOTE_DEGRADATION = "Degradation curve was not provided, so that figure was left out."
NOTE_FAILURES = "Failure ranking was not provided, so that table was left out."
NOTE_REPRO = "No repro card was provided, so the manifest lists no card ids."
NOTE_QUALITATIVE = "No bookmarked frames were provided, so qualitative figures were left out."
NOTE_MATPLOTLIB = "Plots were left out because matplotlib is not installed."


class PaperExportError(CoperceiveError):
    """The export cannot be written. The message is safe to show."""


@dataclass(frozen=True)
class PaperOptions:
    """What the tables include. Decimals apply to every ratio."""

    decimals: int = 3
    bold_best: bool = True
    include_counts: bool = True
    metrics: tuple[str, ...] = ("precision", "recall", "ap")
    qualitative: bool = False
    allow_datasets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.decimals < 0 or self.decimals > 6:
            raise PaperExportError("decimals must be from 0 to 6")
        unknown = [name for name in self.metrics if name not in METRIC_NAMES]
        if unknown:
            raise PaperExportError(f"unknown metric {unknown[0]!r}")


@dataclass(frozen=True)
class PaperFrame:
    """One frame to draw, already confined to a scene directory."""

    scene_dir: Path
    scene_id: str
    time_us: int
    agent_id: str | None = None
    sensor_id: str | None = None
    dataset_id: str = "unknown"


@dataclass(frozen=True)
class PaperInputs:
    """Files the exporter may read. Missing paths stay ``None``."""

    run_id: str | None = None
    predictions_root: Path | None = None
    scenes_root: Path | None = None
    compare_path: Path | None = None
    degradation_path: Path | None = None
    failures_path: Path | None = None
    repro_card_paths: tuple[Path, ...] = ()
    bookmarks_path: Path | None = None
    frames: tuple[PaperFrame, ...] = ()
    explicit_compare: bool = False
    explicit_degradation: bool = False
    explicit_failures: bool = False


@dataclass(frozen=True)
class MetricRow:
    scene: str
    agent: str
    source: str
    values: dict[str, float | int | None]


def _str_dict() -> dict[str, str]:
    return {}


def _str_list() -> list[str]:
    return []


@dataclass
class _Built:
    files: dict[str, str] = field(default_factory=_str_dict)
    notes: list[str] = field(default_factory=_str_list)
    inputs: dict[str, str] = field(default_factory=_str_dict)
    run_ids: list[str] = field(default_factory=_str_list)
    repro_card_ids: list[str] = field(default_factory=_str_list)


def dataset_key(value: str) -> str:
    """Lowercase letters and digits only, so ``V2X-Set`` matches ``v2xset``."""
    return "".join(character for character in value.strip().lower() if character.isalnum())


def dataset_allowed(dataset_id: str, extra: Sequence[str]) -> tuple[bool, str]:
    """Whether qualitative frames of this dataset may be drawn.

    The default allow list is fixtures. Griffin and CARLA are off until named
    in ``extra``. The three refused datasets stay refused even if named.
    """
    key = dataset_key(dataset_id)
    if not key:
        return False, "Qualitative frames were left out because the dataset is unknown."
    if _forbidden(key):
        return False, f"Qualitative frames for {dataset_id} are not exported."
    allow = set(DEFAULT_ALLOW)
    for item in extra:
        name = dataset_key(item)
        if not name or _forbidden(name):
            continue
        if name in OPT_IN_DATASETS or name in DEFAULT_ALLOW:
            allow.add(name)
    if key in allow:
        return True, ""
    if key in OPT_IN_DATASETS:
        return (
            False,
            f"Qualitative export for {dataset_id} is off. "
            "Only fixtures are exported unless that dataset is allowed.",
        )
    return (
        False,
        f"Qualitative export for {dataset_id} is not on the allow list. "
        "Only fixtures are exported unless a dataset is allowed.",
    )


def _forbidden(key: str) -> bool:
    return any(name in key for name in FORBIDDEN_DATASETS)


def resolve_dataset_id(scene_dir: Path) -> str:
    """Dataset id from the scene manifest, else ``fixture`` when the clock says so.

    A missing id is ``unknown``, which the allow list refuses. The map name is
    not used: a synthetic fixture can name a CARLA town and stay a fixture.
    """
    path = scene_dir / "manifest.json"
    if not path.is_file():
        return "fixture" if scene_dir.name.lower().startswith("synth") else "unknown"
    try:
        loaded = _read_json(path)
    except (OSError, PaperExportError, json.JSONDecodeError):
        return "unknown"
    if not isinstance(loaded, dict):
        return "unknown"
    document = cast(dict[object, object], loaded)
    dataset = document.get("dataset")
    if isinstance(dataset, str) and dataset.strip():
        return dataset.strip()
    if isinstance(dataset, dict):
        raw_id = cast(dict[object, object], dataset).get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            return raw_id.strip()
    clock = document.get("clock")
    note = ""
    if isinstance(clock, dict):
        raw_note = cast(dict[object, object], clock).get("scale_note")
        if isinstance(raw_note, str):
            note = raw_note.lower()
    if "fixture" in note or "synthetic" in note:
        return "fixture"
    scene_id = document.get("scene_id")
    if isinstance(scene_id, str):
        resolved_dir = scene_dir.resolve()
        if (
            scene_id == "demo-junction"
            and resolved_dir.name == "demo-junction"
            and resolved_dir.parent.name == "scenes"
            and resolved_dir.parent.parent.name == "demo"
        ):
            return "Demo (synthetic)"
        if scene_id.lower().startswith("synth"):
            return "fixture"
    return "unknown"


def discover_sidecars(predictions_root: Path, run_id: str) -> dict[str, Path | None]:
    """Optional files a later feature can drop next to a run.

    ``compare.json``, ``degradation.json``, ``failures.json``,
    ``bookmarks.json`` and ``repro-card.json`` are the names this export reads.
    """
    try:
        run_dir = confine(predictions_root, check_run_id(run_id))
    except (PathError, PredictionError) as exc:
        raise PaperExportError(str(exc)) from exc
    found: dict[str, Path | None] = {}
    for name in ("compare.json", "degradation.json", "failures.json", "bookmarks.json"):
        path = run_dir / name
        found[name] = path if path.is_file() else None
    card = run_dir / "repro-card.json"
    found["repro-card.json"] = card if card.is_file() else None
    return found


def repro_card_directory(predictions_root: Path, run_id: str) -> Path | None:
    """``<run>/repro-cards`` when that directory exists and stays under the root."""
    try:
        path = confine(predictions_root, f"{check_run_id(run_id)}/repro-cards")
    except (PathError, PredictionError) as exc:
        raise PaperExportError(str(exc)) from exc
    return path if path.is_dir() else None


def export_paper(
    dest: Path,
    options: PaperOptions,
    inputs: PaperInputs,
    *,
    created_utc: str | None = None,
) -> dict[str, object]:
    """Write a zip at ``dest`` and return the manifest object.

    ``dest`` is replaced. Work files are removed. The zip stores a fixed
    timestamp so the archive does not change when the numbers do not.
    """
    if dest.suffix.lower() != ".zip":
        raise PaperExportError("the paper export path must end in .zip")
    stamp = created_utc if created_utc else _now()
    built = _Built()
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.stem}-paper"
    if work.exists():
        _remove_tree(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        _fill(work, options, inputs, built)
        manifest = _manifest(built, stamp)
        (work / "manifest.json").write_text(
            json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        (work / "notes.txt").write_text("\n".join(built.notes) + "\n", encoding="utf-8")
        (work / "README.txt").write_text(_README, encoding="utf-8")
        names = sorted(
            path.relative_to(work).as_posix() for path in work.rglob("*") if path.is_file()
        )
        manifest["files"] = names
        (work / "manifest.json").write_text(
            json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_zip(work, dest)
    finally:
        _remove_tree(work)
    return manifest


def metrics_tex(documents: Sequence[Mapping[str, object]], options: PaperOptions) -> str:
    """The metric table for one or more ``metrics.json`` objects."""
    rows = _metric_rows(documents)
    if not rows:
        return _plain_tex(NOTE_METRICS)
    thresholds = _threshold_keys(rows)
    columns = _metric_columns(options, thresholds)
    header = ["Scene", "Agent", "Source", *[label for _key, label, _kind in columns]]
    body: list[list[str]] = []
    numeric: list[list[float | None]] = []
    for row in rows:
        cells: list[str] = [_tex_text(row.scene), _tex_text(row.agent), _tex_text(row.source)]
        numbers: list[float | None] = []
        for key, _label, kind in columns:
            value = row.values.get(key)
            if kind == "count" or isinstance(value, bool) or not isinstance(value, (int, float)):
                numbers.append(None)
            else:
                numbers.append(float(value))
            cells.append(
                _number_cell(value if isinstance(value, (int, float)) else None, kind, options)
            )
        body.append(cells)
        numeric.append(numbers)
    if options.bold_best and len(body) > 1:
        _bold_best(body, numeric, [kind == "lower" for _key, _label, kind in columns])
    pieces: list[str] = []
    for _key, _label, kind in columns:
        if kind == "count":
            pieces.append(" S[table-format=3.0]")
        else:
            pieces.append(f" S[table-format=1.{options.decimals},detect-weight]")
    spec = "lll" + "".join(pieces)
    return _tabular("Metrics", spec, header, body)


def comparison_tex(document: Mapping[str, object], options: PaperOptions) -> str:
    """Breakdown tables from one ``coperceive-compare`` document."""
    blocks = _breakdown_blocks(document)
    if not blocks:
        return _plain_tex(NOTE_COMPARISON)
    parts = [_PREAMBLE, "% comparison breakdowns", ""]
    for block in blocks:
        parts.append(_breakdown_table(block, options))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def failures_tex(document: Mapping[str, object], options: PaperOptions) -> str:
    """A short table of the worst frames from ``coperceive-failures``."""
    frames = document.get("frames")
    parsed = _dicts(frames)
    if not parsed:
        return _plain_tex(NOTE_FAILURES)
    header = ["Rank", "Scene", "Time", "Agent", "Source", "Badness", "Tags"]
    body: list[list[str]] = []
    for frame in parsed:
        tags = frame.get("tags")
        names: list[str] = []
        if isinstance(tags, list):
            for tag in cast(list[object], tags):
                if isinstance(tag, str):
                    names.append(tag)
        tag_text = ", ".join(names)
        badness = frame.get("badness")
        body.append(
            [
                _tex_text(_as_text(frame.get("rank"))),
                _tex_text(_as_text(frame.get("scene_id"))),
                _tex_text(_as_text(frame.get("time_us"))),
                _tex_text(_as_text(frame.get("agent_id"))),
                _tex_text(_as_text(frame.get("source"))),
                _number_cell(
                    badness if isinstance(badness, (int, float)) else None, "ratio", options
                ),
                _tex_text(tag_text),
            ]
        )
    if not body:
        return _plain_tex(NOTE_FAILURES)
    spec = "lllll S[table-format=3." + str(options.decimals) + "] l"
    return _tabular("Worst frames", spec, header, body)


def _fill(work: Path, options: PaperOptions, inputs: PaperInputs, built: _Built) -> None:
    documents = _load_metrics(inputs, built)
    if documents:
        built.inputs["metrics"] = "present"
        text = metrics_tex(documents, options)
        _write_text(work, "tables/metrics.tex", text, built)
        _try_pr(work, inputs, documents, built)
    else:
        built.inputs["metrics"] = "missing"
        built.notes.append(NOTE_METRICS)
        _write_text(work, "tables/metrics.tex", _plain_tex(NOTE_METRICS), built)

    compare = _load_named(
        inputs.compare_path, COMPARE_FORMAT, "comparison", inputs.explicit_compare
    )
    if compare is None:
        built.inputs["comparison"] = "missing"
        built.inputs["breakdowns"] = "missing"
        built.notes.append(NOTE_COMPARISON)
        _write_text(work, "tables/comparison.tex", _plain_tex(NOTE_COMPARISON), built)
        _write_text(work, "tables/breakdowns.tex", _plain_tex(NOTE_COMPARISON), built)
    else:
        built.inputs["comparison"] = "present"
        built.inputs["breakdowns"] = "present"
        _write_text(work, "tables/comparison.tex", comparison_tex(compare, options), built)
        _write_text(work, "tables/breakdowns.tex", comparison_tex(compare, options), built)
        _try_bars(work, compare, built)

    degradation = _load_named(
        inputs.degradation_path, DEGRADATION_FORMAT, "degradation", inputs.explicit_degradation
    )
    if degradation is None:
        built.inputs["degradation"] = "missing"
        built.notes.append(NOTE_DEGRADATION)
    else:
        built.inputs["degradation"] = "present"
        _try_degradation(work, degradation, built)

    failures = _load_named(
        inputs.failures_path, FAILURES_FORMAT, "failures", inputs.explicit_failures
    )
    if failures is None:
        built.inputs["failures"] = "missing"
        built.notes.append(NOTE_FAILURES)
        _write_text(work, "tables/failures.tex", _plain_tex(NOTE_FAILURES), built)
    else:
        built.inputs["failures"] = "present"
        _write_text(work, "tables/failures.tex", failures_tex(failures, options), built)

    ids, repro_notes = _repro_ids(inputs.repro_card_paths)
    built.repro_card_ids.extend(ids)
    if ids:
        built.inputs["repro_cards"] = "present"
    else:
        built.inputs["repro_cards"] = "missing"
        built.notes.append(NOTE_REPRO)
    built.notes.extend(repro_notes)
    _qualitative(work, options, inputs, built)


def _load_metrics(inputs: PaperInputs, built: _Built) -> list[Mapping[str, object]]:
    if inputs.predictions_root is None or inputs.run_id is None:
        return []
    try:
        run_dir = confine(inputs.predictions_root, check_run_id(inputs.run_id))
    except (PathError, PredictionError) as exc:
        raise PaperExportError(str(exc)) from exc
    if not run_dir.is_dir():
        return []
    documents: list[Mapping[str, object]] = []
    for child in sorted(run_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == "cache":
            continue
        if not (child / "metrics.json").is_file():
            continue
        try:
            check_scene_id(child.name)
            documents.append(read_metrics(child))
        except (CoperceiveError, OSError, ValueError):
            built.notes.append(f"Skipped metrics for {child.name}.")
    if documents:
        built.run_ids.append(inputs.run_id)
    return documents


def _load_named(
    path: Path | None, expected: str, label: str, explicit: bool
) -> dict[str, object] | None:
    if path is None:
        return None
    if not path.is_file():
        if explicit:
            raise PaperExportError(f"{label} file was not found")
        return None
    try:
        loaded = _read_json(path)
    except (OSError, json.JSONDecodeError, PaperExportError) as exc:
        if explicit:
            raise PaperExportError(f"{label} file could not be read") from exc
        return None
    if not isinstance(loaded, dict):
        if explicit:
            raise PaperExportError(f"{label} file must be a JSON object")
        return None
    document = {
        key: value
        for key, value in cast(dict[object, object], loaded).items()
        if isinstance(key, str)
    }
    if document.get("format") != expected:
        if explicit:
            raise PaperExportError(f"{label} file format must be {expected}")
        return None
    version = document.get("format_version")
    if type(version) is not int or version != 1:
        if explicit:
            raise PaperExportError(f"{label} file format_version must be 1")
        return None
    return document


def _metric_rows(documents: Sequence[Mapping[str, object]]) -> list[MetricRow]:
    rows: list[MetricRow] = []
    for document in documents:
        scene_id = document.get("scene_id")
        scene = scene_id if isinstance(scene_id, str) else ""
        outputs = document.get("outputs")
        for item in _dicts(outputs):
            output = item
            agent = output.get("agent_id")
            source = output.get("source")
            totals = output.get("totals")
            values: dict[str, float | int | None] = {}
            if isinstance(totals, dict):
                total_map = cast(dict[object, object], totals)
                for key in ("gt", "predictions", "tp", "fp", "miss", "precision", "recall"):
                    values[key] = _num(total_map.get(key))
                cooperative = output.get("cooperative")
                if isinstance(cooperative, dict):
                    coop = cast(dict[object, object], cooperative)
                    values["recovered"] = _num(coop.get("recovered"))
                    values["lost_by_fusion"] = _num(coop.get("lost_by_fusion"))
            ap = output.get("ap")
            if isinstance(ap, dict):
                for key, cell in cast(dict[object, object], ap).items():
                    if not isinstance(key, str) or not isinstance(cell, dict):
                        continue
                    mean = cast(dict[object, object], cell).get("mean")
                    values["ap@" + key] = _num(mean)
            rows.append(
                MetricRow(
                    scene=scene,
                    agent=agent if isinstance(agent, str) else "",
                    source=source if isinstance(source, str) else "",
                    values=values,
                )
            )
    rows.sort(key=lambda row: (row.scene, row.agent, row.source))
    return rows


def _threshold_keys(rows: Sequence[MetricRow]) -> list[str]:
    found: set[str] = set()
    for row in rows:
        for key in row.values:
            if key.startswith("ap@"):
                found.add(key.removeprefix("ap@"))
    return sorted(found, key=lambda key: float(key) if _float_key(key) else 0.0)


def _float_key(key: str) -> bool:
    try:
        float(key)
    except ValueError:
        return False
    return True


def _metric_columns(options: PaperOptions, thresholds: Sequence[str]) -> list[tuple[str, str, str]]:
    columns: list[tuple[str, str, str]] = []
    if options.include_counts:
        columns.append(("gt", "Objects", "count"))
        columns.append(("predictions", "Predictions", "count"))
    selected = set(options.metrics)
    if "precision" in selected:
        columns.append(("precision", "Precision", "higher"))
    if "recall" in selected:
        columns.append(("recall", "Recall", "higher"))
    if "ap" in selected:
        for key in thresholds:
            columns.append((f"ap@{key}", f"AP@{key}", "higher"))
    if "recovered" in selected:
        columns.append(("recovered", "Recovered", "count"))
    if "lost_by_fusion" in selected:
        columns.append(("lost_by_fusion", "Lost by fusion", "lower"))
    return columns


def _bold_best(
    body: list[list[str]], numeric: list[list[float | None]], lower: Sequence[bool]
) -> None:
    if not numeric:
        return
    width = len(numeric[0])
    for column in range(width):
        present: list[float] = []
        for row in numeric:
            value = row[column]
            if value is not None:
                present.append(value)
        if len(present) < 2:
            continue
        target = min(present) if lower[column] else max(present)
        for row_index, row in enumerate(numeric):
            if row[column] is None or row[column] != target:
                continue
            # Scene, agent and source occupy the first three cells.
            cell = body[row_index][column + 3]
            if cell.startswith("{") or cell.startswith("\\bfseries"):
                continue
            body[row_index][column + 3] = "\\bfseries " + cell


def _bold_best_in_row(
    body: list[list[str]], numeric: list[list[float | None]], n_scores: int
) -> None:
    """Bold the highest score in each row. Count columns after ``n_scores`` stay plain."""
    for row_index, numbers in enumerate(numeric):
        scores = [value for value in numbers[:n_scores] if value is not None]
        if len(scores) < 2:
            continue
        target = max(scores)
        for column, value in enumerate(numbers[:n_scores]):
            if value != target:
                continue
            cell = body[row_index][column + 1]
            if cell.startswith("{") or cell.startswith("\\bfseries"):
                continue
            body[row_index][column + 1] = "\\bfseries " + cell


def _number_cell(value: object, kind: str, options: PaperOptions) -> str:
    if value is None or not isinstance(value, (int, float)):
        return "{---}"
    if kind == "count" and float(value).is_integer():
        return str(int(value))
    return _format_decimal(float(value), options.decimals)


def _format_decimal(value: float, decimals: int) -> str:
    quant = Decimal(1).scaleb(-decimals)
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise PaperExportError("a metric was not a finite number") from exc
    if not number.is_finite():
        return "{---}"
    return format(number.quantize(quant, rounding=ROUND_HALF_UP), "f")


def _breakdown_blocks(document: Mapping[str, object]) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for output in _dicts(document.get("outputs")):
        breakdowns = output.get("breakdowns")
        if not isinstance(breakdowns, dict):
            continue
        agent_raw = output.get("agent_id")
        source_raw = output.get("source")
        agent = agent_raw if isinstance(agent_raw, str) else ""
        source = source_raw if isinstance(source_raw, str) else ""
        named = cast(dict[object, object], breakdowns)
        for name, bins in named.items():
            if not isinstance(name, str):
                continue
            parsed = [row for row in (_bin_row(entry) for entry in _dicts(bins)) if row is not None]
            if parsed:
                blocks.append({"name": name, "agent": agent, "source": source, "bins": parsed})
    return blocks


def _bin_row(entry: Mapping[str, object]) -> dict[str, object] | None:
    label = entry.get("bin")
    runs = entry.get("runs")
    if not isinstance(label, str) or not isinstance(runs, dict):
        return None
    cells: dict[str, tuple[float | None, int | None]] = {}
    for run_id, payload in cast(dict[object, object], runs).items():
        if not isinstance(run_id, str) or not isinstance(payload, dict):
            continue
        body = cast(dict[object, object], payload)
        count = body.get("n_objects")
        mean = _first_mean(body.get("ap"))
        cells[run_id] = (mean, int(count) if isinstance(count, int) else None)
    if not cells:
        return None
    return {"bin": label, "runs": cells}


def _first_mean(ap: object) -> float | None:
    if not isinstance(ap, dict):
        return None
    pairs = sorted(
        (
            (key, value)
            for key, value in cast(dict[object, object], ap).items()
            if isinstance(key, str)
        ),
        key=lambda item: item[0],
    )
    for _key, cell in pairs:
        if isinstance(cell, dict):
            mean = cast(dict[object, object], cell).get("mean")
            if isinstance(mean, (int, float)):
                return float(mean)
    return None


def _breakdown_table(block: Mapping[str, object], options: PaperOptions) -> str:
    bins = _dicts(block.get("bins"))
    if not bins:
        return _plain_tex(NOTE_COMPARISON)
    run_ids: list[str] = []
    for row in bins:
        runs = row.get("runs")
        if isinstance(runs, dict):
            for run_id in cast(dict[object, object], runs):
                if isinstance(run_id, str) and run_id not in run_ids:
                    run_ids.append(run_id)
    header = ["Bin", *[f"{run_id} AP" for run_id in run_ids]]
    if options.include_counts:
        header.extend(f"{run_id} objects" for run_id in run_ids)
    body: list[list[str]] = []
    numeric: list[list[float | None]] = []
    for row in bins:
        runs = row.get("runs")
        if not isinstance(runs, dict):
            continue
        run_map = cast(dict[str, tuple[float | None, int | None]], runs)
        cells = [_tex_text(str(row.get("bin", "")))]
        numbers: list[float | None] = []
        for run_id in run_ids:
            mean, _count = run_map.get(run_id, (None, None))
            numbers.append(mean)
            cells.append(_number_cell(mean, "ratio", options))
        if options.include_counts:
            for run_id in run_ids:
                _mean, count = run_map.get(run_id, (None, None))
                numbers.append(float(count) if count is not None else None)
                cells.append(_number_cell(count, "count", options))
        body.append(cells)
        numeric.append(numbers)
    if options.bold_best:
        _bold_best_in_row(body, numeric, len(run_ids))
    spec = "l" + f" S[table-format=1.{options.decimals},detect-weight]" * len(run_ids)
    if options.include_counts:
        spec += " S[table-format=4.0]" * len(run_ids)
    title = f"{block.get('agent', '')} {block.get('source', '')} by {block.get('name', '')}".strip()
    return _tabular(title, spec, header, body)


def _try_pr(
    work: Path,
    inputs: PaperInputs,
    documents: Sequence[Mapping[str, object]],
    built: _Built,
) -> None:
    if inputs.predictions_root is None or inputs.run_id is None:
        return
    try:
        series = _pr_series(inputs.predictions_root, inputs.run_id, documents)
    except PaperExportError as exc:
        built.notes.append(str(exc))
        return
    if not series:
        built.notes.append(
            "Precision-recall curve was left out because matches.parquet has no status columns."
        )
        return
    _draw(work, "figures/pr", built, lambda stem: _pr(series, stem))


def _pr(
    series: Sequence[tuple[str, Sequence[tuple[float, float]]]], stem: Path
) -> tuple[Path, Path]:
    from coperceive.paper_plots import write_pr_curves

    return write_pr_curves(series, stem)


def _pr_series(
    predictions_root: Path,
    run_id: str,
    documents: Sequence[Mapping[str, object]],
) -> list[tuple[str, list[tuple[float, float]]]]:
    try:
        run_dir = confine(predictions_root, check_run_id(run_id))
    except (PathError, PredictionError) as exc:
        raise PaperExportError(str(exc)) from exc
    grouped: dict[tuple[str, str, str], list[tuple[float, str]]] = {}
    n_gt: dict[tuple[str, str, str], int] = {}
    found_column = False
    for child in sorted(run_dir.iterdir(), key=lambda path: path.name):
        path = child / "matches.parquet"
        if not path.is_file():
            continue
        rows, has_column = _match_rows(path)
        found_column = found_column or has_column
        if not has_column:
            continue
        for row in rows:
            kind = row.get("kind")
            category = row.get("category")
            agent = row.get("agent_id")
            source = row.get("source")
            if not all(isinstance(item, str) for item in (category, agent, source)):
                continue
            key = (str(agent), str(source), str(category))
            if kind == "gt":
                status = row.get("status")
                if status != "ignored":
                    n_gt[key] = n_gt.get(key, 0) + 1
            elif kind == "prediction":
                score = row.get("score")
                status = row.get("status_at_0")
                if isinstance(score, (int, float)) and isinstance(status, str):
                    grouped.setdefault(key, []).append((float(score), status))
    if not found_column:
        return []
    del documents
    series: list[tuple[str, list[tuple[float, float]]]] = []
    for key in sorted(grouped):
        agent, source, category = key
        points = _pr_points(grouped[key], n_gt.get(key, 0))
        if points:
            series.append((f"{agent}/{source} {category}", points))
    return series[:12]


def _match_rows(path: Path) -> tuple[list[dict[str, object]], bool]:
    import pyarrow.parquet as pq

    try:
        names = set(pq.read_schema(path).names)
    except Exception as exc:
        raise PaperExportError(f"{path.name} could not be read") from exc
    if "status_at_0" not in names:
        return [], False
    wanted = [
        name
        for name in ("kind", "agent_id", "source", "category", "score", "status", "status_at_0")
        if name in names
    ]
    try:
        table = pq.read_table(path, columns=wanted)
    except Exception as exc:
        raise PaperExportError(f"{path.name} could not be read") from exc
    raw = cast(list[object], table.to_pylist())
    return _dicts(raw), True


def _pr_points(records: Sequence[tuple[float, str]], n_gt: int) -> list[tuple[float, float]]:
    """The precision-recall walk of :func:`coperceive.evaluation.metrics.average_precision`."""
    if n_gt <= 0:
        return []
    ordered = sorted(records, key=lambda record: -record[0])
    points: list[tuple[float, float]] = [(0.0, 1.0)]
    tp = 0
    fp = 0
    for _score, status in ordered:
        if status == "tp":
            tp += 1
        elif status == "fp":
            fp += 1
        else:
            continue
        points.append((tp / n_gt, tp / (tp + fp)))
    return points


def _try_bars(work: Path, document: Mapping[str, object], built: _Built) -> None:
    blocks = _breakdown_blocks(document)
    if not blocks:
        return
    block = blocks[0]
    rows = _dicts(block.get("bins"))
    if not rows:
        return
    labels: list[str] = []
    run_ids: list[str] = []
    for row in rows:
        labels.append(str(row.get("bin", "")))
        runs = row.get("runs")
        if isinstance(runs, dict):
            for run_id in cast(dict[object, object], runs):
                if isinstance(run_id, str) and run_id not in run_ids:
                    run_ids.append(run_id)
    series: list[tuple[str, list[float | None]]] = []
    for run_id in run_ids:
        values: list[float | None] = []
        for row in rows:
            runs = row.get("runs")
            if not isinstance(runs, dict):
                values.append(None)
                continue
            cell = cast(dict[str, tuple[float | None, int | None]], runs).get(run_id)
            values.append(cell[0] if cell else None)
        series.append((run_id, values))
    title = f"{block.get('name', 'breakdown')}"

    def draw(stem: Path) -> tuple[Path, Path]:
        from coperceive.paper_plots import write_grouped_bars

        return write_grouped_bars(
            labels, series, stem, ylabel="Average precision", title=str(title)
        )

    _draw(work, "figures/breakdown", built, draw)


def _try_degradation(work: Path, document: Mapping[str, object], built: _Built) -> None:
    series = _degradation_series(document)
    if not series:
        built.notes.append("Degradation file had no points, so that figure was left out.")
        return
    xlabel = document.get("x_label")
    ylabel = document.get("y_label")

    def draw(stem: Path) -> tuple[Path, Path]:
        from coperceive.paper_plots import write_degradation

        return write_degradation(
            series,
            stem,
            xlabel=xlabel if isinstance(xlabel, str) and xlabel else "Level",
            ylabel=ylabel if isinstance(ylabel, str) and ylabel else "Score",
        )

    _draw(work, "figures/degradation", built, draw)


def _degradation_series(
    document: Mapping[str, object],
) -> list[tuple[str, list[tuple[float, float]]]]:
    grouped: list[tuple[str, list[tuple[float, float]]]] = []
    flat: dict[str, list[tuple[float, float]]] = {}
    flat_order: list[str] = []
    for entry in _dicts(document.get("series")):
        points = entry.get("points")
        name = entry.get("name")
        if isinstance(name, str) and _level_points(points):
            parsed = _level_points(points)
            grouped.append((name, parsed))
            continue
        label = name if isinstance(name, str) else entry.get("label")
        level = entry.get("level")
        value = entry.get("value")
        if (
            isinstance(label, str)
            and isinstance(level, (int, float))
            and isinstance(value, (int, float))
        ):
            if label not in flat:
                flat_order.append(label)
                flat[label] = []
            flat[label].append((float(level), float(value)))
    for label in flat_order:
        grouped.append((label, flat[label]))
    return grouped


def _level_points(points: object) -> list[tuple[float, float]]:
    parsed: list[tuple[float, float]] = []
    for point in _dicts(points):
        level = point.get("level")
        value = point.get("value")
        if isinstance(level, (int, float)) and not isinstance(level, bool):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parsed.append((float(level), float(value)))
    return parsed


def _repro_ids(paths: Sequence[Path]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    notes: list[str] = []
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(item for item in path.glob("*.json") if item.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            notes.append(f"Repro card {path.name} was not found, so it was left out.")
    for path in files:
        try:
            loaded = _read_json(path)
        except (OSError, json.JSONDecodeError, PaperExportError):
            notes.append(f"Repro card {path.name} could not be read, so it was left out.")
            continue
        if not isinstance(loaded, dict):
            notes.append(f"Repro card {path.name} was left out.")
            continue
        document = cast(dict[object, object], loaded)
        schema = document.get("schema")
        if isinstance(schema, str) and schema != REPRO_SCHEMA:
            notes.append(f"Repro card {path.name} was left out.")
            continue
        card_id = document.get("id")
        if isinstance(card_id, str) and CARD_ID.fullmatch(card_id):
            if card_id not in ids:
                ids.append(card_id)
        else:
            notes.append(f"Repro card {path.name} has no card id, so it was left out.")
    return ids, notes


def _qualitative(work: Path, options: PaperOptions, inputs: PaperInputs, built: _Built) -> None:
    if not options.qualitative:
        built.inputs["qualitative"] = "skipped"
        return
    frames = list(inputs.frames)
    if not frames and inputs.bookmarks_path is not None and inputs.scenes_root is not None:
        frames.extend(_frames_from_bookmarks(inputs.bookmarks_path, inputs.scenes_root, built))
    if not frames:
        built.inputs["qualitative"] = "skipped"
        built.notes.append(NOTE_QUALITATIVE)
        return
    rendered = 0
    refused = 0
    for frame in frames:
        allowed, message = dataset_allowed(frame.dataset_id, options.allow_datasets)
        if not allowed:
            refused += 1
            if message and message not in built.notes:
                built.notes.append(message)
            continue
        if _draw_frame(work, frame, inputs, built):
            rendered += 1
    if rendered:
        built.inputs["qualitative"] = "rendered"
    elif refused:
        built.inputs["qualitative"] = "refused"
    else:
        built.inputs["qualitative"] = "skipped"


def _frames_from_bookmarks(path: Path, scenes_root: Path, built: _Built) -> list[PaperFrame]:
    try:
        loaded = _read_json(path)
    except (OSError, json.JSONDecodeError, PaperExportError):
        built.notes.append("Bookmarks could not be read, so qualitative figures were left out.")
        return []
    loaded_object: object = loaded
    if isinstance(loaded, dict):
        loaded_object = cast(dict[object, object], loaded).get("frames")
    frames: list[PaperFrame] = []
    for frame in _dicts(loaded_object):
        scene_id = frame.get("scene_id")
        time_us = frame.get("time_us")
        if not isinstance(scene_id, str) or not isinstance(time_us, int):
            continue
        try:
            check_scene_id(scene_id)
            scene_dir = confine(scenes_root, scene_id)
        except (PathError, PredictionError):
            built.notes.append(f"Bookmark {scene_id} was left out.")
            continue
        if not (scene_dir / "manifest.json").is_file():
            built.notes.append(f"Bookmark {scene_id} was left out.")
            continue
        agent = frame.get("agent_id")
        sensor = frame.get("sensor_id")
        frames.append(
            PaperFrame(
                scene_dir=scene_dir,
                scene_id=scene_id,
                time_us=time_us,
                agent_id=agent if isinstance(agent, str) else None,
                sensor_id=sensor if isinstance(sensor, str) else None,
                dataset_id=resolve_dataset_id(scene_dir),
            )
        )
    return frames


def _draw_frame(work: Path, frame: PaperFrame, inputs: PaperInputs, built: _Built) -> bool:
    from coperceive.plot import PlotError, render_bev, render_overlay
    from coperceive.store import read_scene

    try:
        scene = read_scene(frame.scene_dir)
    except (OSError, CoperceiveError, ValueError):
        built.notes.append(f"Qualitative frame {frame.scene_id} could not be read.")
        return False
    stem = f"{frame.scene_id}_{frame.time_us}"
    drew = False
    predictions: tuple[PredictionDraw, ...] = ()
    if inputs.predictions_root is not None and inputs.run_id is not None:
        predictions = _prediction_draws(inputs.predictions_root, inputs.run_id, frame)
    for fmt in ("png", "pdf"):
        dest = work / "qualitative" / f"{stem}_bev.{fmt}"
        try:
            render_bev(
                scene,
                dest,
                time_us=frame.time_us,
                theme="light",
                predictions=predictions,
            )
            built.files[dest.relative_to(work).as_posix()] = "figure"
            drew = True
        except (PlotError, OSError, CoperceiveError, ValueError):
            built.notes.append(
                f"Bird's-eye figure for {frame.scene_id} at {frame.time_us} was left out."
            )
            break
    if frame.agent_id and frame.sensor_id:
        for fmt in ("png", "pdf"):
            dest = work / "qualitative" / f"{stem}_{frame.agent_id}_{frame.sensor_id}.{fmt}"
            try:
                render_overlay(
                    scene,
                    frame.scene_dir,
                    dest,
                    agent_id=frame.agent_id,
                    sensor_id=frame.sensor_id,
                    time_us=frame.time_us,
                    theme="light",
                )
                built.files[dest.relative_to(work).as_posix()] = "figure"
                drew = True
            except (PlotError, OSError, CoperceiveError, ValueError):
                built.notes.append(
                    f"Camera figure for {frame.scene_id} at {frame.time_us} was left out."
                )
                break
    return drew


def _prediction_draws(
    predictions_root: Path, run_id: str, frame: PaperFrame
) -> tuple[PredictionDraw, ...]:
    try:
        from coperceive.evalview import frame_rows
        from coperceive.plot import PlotError, prediction_draws

        body = frame_rows(predictions_root, run_id, frame.scene_id, frame.time_us, compare=None)
    except (CoperceiveError, OSError, ValueError):
        return ()
    rows = _dicts(body.get("rows"))
    try:
        return prediction_draws(rows)
    except (PlotError, CoperceiveError, OSError, ValueError):
        return ()


def _draw(work: Path, stem: str, built: _Built, draw: Callable[[Path], tuple[Path, Path]]) -> None:
    try:
        pdf, svg = draw(work / stem)
    except RuntimeError:
        if NOTE_MATPLOTLIB not in built.notes:
            built.notes.append(NOTE_MATPLOTLIB)
        return
    except (OSError, ValueError) as exc:
        built.notes.append(f"A figure was left out ({exc.__class__.__name__}).")
        return
    built.files[pdf.relative_to(work).as_posix()] = "figure"
    built.files[svg.relative_to(work).as_posix()] = "figure"


def _manifest(built: _Built, created_utc: str) -> dict[str, object]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_utc": created_utc,
        "run_ids": list(built.run_ids),
        "repro_card_ids": list(built.repro_card_ids),
        "inputs": dict(built.inputs),
        "notes": list(built.notes),
        "files": [],
    }


def _write_text(work: Path, relative: str, text: str, built: _Built) -> None:
    dest = work / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    built.files[relative] = "text"


def _write_zip(source: Path, dest: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    with zipfile.ZipFile(dest, "w") as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), _ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def _read_json(path: Path) -> object:
    data = path.read_bytes()
    if len(data) > _JSON_LIMIT:
        raise PaperExportError(f"{path.name} is too large")
    return json.loads(data.decode("utf-8"))


def _dicts(value: object) -> list[dict[str, object]]:
    """JSON arrays as string-keyed objects. Anything else is dropped."""
    if not isinstance(value, list):
        return []
    found: list[dict[str, object]] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            continue
        raw = cast(dict[object, object], item)
        found.append({key: raw[key] for key in raw if isinstance(key, str)})
    return found


def _num(value: object) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return None


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _tex_text(value: str) -> str:
    pieces: list[str] = []
    for character in value:
        if character == "\\":
            pieces.append("\\textbackslash{}")
        elif character in "%&_#${}":
            pieces.append("\\" + character)
        elif character == "~":
            pieces.append("\\textasciitilde{}")
        elif character == "^":
            pieces.append("\\textasciicircum{}")
        else:
            pieces.append(character)
    return "{" + "".join(pieces) + "}"


_PREAMBLE = (
    "% coperceive paper export. Load booktabs and siunitx.\n"
    "% Bold values need \\sisetup{detect-weight=true,detect-inline-weight=math}."
)


def _plain_tex(message: str) -> str:
    return _PREAMBLE + "\n\\noindent " + _tex_text(message)[1:-1] + "\n"


def _tabular(caption: str, spec: str, header: Sequence[str], body: Sequence[Sequence[str]]) -> str:
    lines = [
        _PREAMBLE,
        "\\begin{table}",
        "\\centering",
        "\\caption{" + _tex_text(caption)[1:-1] + "}",
        "\\begin{tabular}{" + spec + "}",
        "\\toprule",
        " & ".join("{" + item + "}" for item in header) + " \\\\",
        "\\midrule",
    ]
    for row in body:
        lines.append(" & ".join(row) + " \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}", "\\end{table}", ""])
    return "\n".join(lines)


def paper_inputs_from_cli(
    *,
    run_id: str | None,
    predictions: Path | None,
    scenes: Path | None,
    compare: Path | None,
    degradation: Path | None,
    failures: Path | None,
    repro_cards: tuple[Path, ...],
    bookmarks: Path | None,
    frames_json: str | None,
    decimals: int,
    bold_best: bool,
    include_counts: bool,
    metrics: tuple[str, ...],
    qualitative: bool,
    allow_datasets: tuple[str, ...],
) -> tuple[PaperOptions, PaperInputs]:
    """Shared by ``coperceive export paper``. Explicit files win over sidecars."""
    options = PaperOptions(
        decimals=decimals,
        bold_best=bold_best,
        include_counts=include_counts,
        metrics=metrics,
        qualitative=qualitative,
        allow_datasets=allow_datasets,
    )
    cards = list(repro_cards)
    explicit_compare = compare is not None
    explicit_degradation = degradation is not None
    explicit_failures = failures is not None
    if predictions is not None and run_id is not None:
        found = discover_sidecars(predictions, run_id)
        if compare is None:
            compare = found.get("compare.json")
        if degradation is None:
            degradation = found.get("degradation.json")
        if failures is None:
            failures = found.get("failures.json")
        if bookmarks is None:
            bookmarks = found.get("bookmarks.json")
        card = found.get("repro-card.json")
        if card is not None:
            cards.append(card)
        try:
            extra = confine(predictions, f"{check_run_id(run_id)}/repro-cards")
        except (PathError, PredictionError) as exc:
            raise PaperExportError(str(exc)) from exc
        if extra.is_dir():
            cards.append(extra)
    frames = _cli_frames(frames_json, scenes) if frames_json else ()
    paper_inputs = PaperInputs(
        run_id=run_id,
        predictions_root=predictions,
        scenes_root=scenes,
        compare_path=compare,
        degradation_path=degradation,
        failures_path=failures,
        repro_card_paths=tuple(cards),
        bookmarks_path=bookmarks,
        frames=frames,
        explicit_compare=explicit_compare,
        explicit_degradation=explicit_degradation,
        explicit_failures=explicit_failures,
    )
    return options, paper_inputs


def _cli_frames(payload: str, scenes: Path | None) -> tuple[PaperFrame, ...]:
    if scenes is None:
        raise PaperExportError("qualitative frames need --scenes")
    try:
        loaded: object = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PaperExportError("frames JSON could not be read") from exc
    if not isinstance(loaded, list):
        raise PaperExportError("frames JSON must be a list")
    frames: list[PaperFrame] = []
    for frame in _dicts(cast(object, loaded)):
        scene_id = frame.get("scene_id")
        time_us = frame.get("time_us")
        if not isinstance(scene_id, str) or not isinstance(time_us, int):
            raise PaperExportError("a frame needs scene_id and time_us")
        try:
            check_scene_id(scene_id)
            scene_dir = confine(scenes, scene_id)
        except (PathError, PredictionError) as exc:
            raise PaperExportError(str(exc)) from exc
        if not (scene_dir / "manifest.json").is_file():
            # A library that is itself one scene keeps the manifest on the root.
            if (scenes / "manifest.json").is_file() and scenes.name == scene_id:
                scene_dir = scenes
            else:
                raise PaperExportError(f"no scene {scene_id}")
        agent = frame.get("agent_id")
        sensor = frame.get("sensor_id")
        frames.append(
            PaperFrame(
                scene_dir=scene_dir,
                scene_id=scene_id,
                time_us=time_us,
                agent_id=agent if isinstance(agent, str) else None,
                sensor_id=sensor if isinstance(sensor, str) else None,
                dataset_id=resolve_dataset_id(scene_dir),
            )
        )
    return tuple(frames)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_README = """coperceive paper export
Drop tables/*.tex into an Overleaf project that loads booktabs and siunitx.
Figures are PDF and SVG and share one colour-blind-safe palette.
manifest.json lists the repro card ids for the numbers in this zip.
notes.txt says which pieces were left out.
"""
