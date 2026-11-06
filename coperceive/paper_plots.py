# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""Vector paper figures: precision-recall, breakdown bars, degradation curves.

Every figure uses :mod:`coperceive.plot_style`. PDF and SVG are written with a
fixed timestamp so two exports of the same numbers are the same bytes.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from coperceive.plot_style import rc_params, series_color

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

_SOURCE_DATE = "0"


def _figure() -> Figure:
    os.environ.setdefault("SOURCE_DATE_EPOCH", _SOURCE_DATE)
    try:
        import matplotlib
        from matplotlib.figure import Figure as RuntimeFigure
    except ImportError as exc:
        raise RuntimeError("matplotlib is required for paper figures") from exc
    matplotlib.use("Agg", force=False)
    for key, value in rc_params().items():
        matplotlib.rcParams[key] = value  # pyright: ignore[reportArgumentType]
    figure: Figure = RuntimeFigure(figsize=(6.2, 3.6), dpi=120)
    return figure


def _axes(figure: Figure) -> Axes:
    axes: Axes = figure.subplots()
    return axes


def _save(figure: Figure, stem: Path) -> tuple[Path, Path]:
    """Write ``stem.pdf`` and ``stem.svg``. The SVG has no date metadata."""
    stem.parent.mkdir(parents=True, exist_ok=True)
    pdf = stem.with_suffix(".pdf")
    svg = stem.with_suffix(".svg")
    figure.savefig(
        pdf,
        format="pdf",
        bbox_inches="tight",
        metadata={"Creator": "coperceive", "Title": "coperceive", "CreationDate": None},
    )
    figure.savefig(svg, format="svg", bbox_inches="tight")
    return pdf, svg


def write_pr_curves(
    series: Sequence[tuple[str, Sequence[tuple[float, float]]]],
    stem: Path,
) -> tuple[Path, Path]:
    """Recall on x, precision on y. Each series is ``(name, [(recall, precision), ...])``."""
    figure = _figure()
    axes = _axes(figure)
    for index, (name, points) in enumerate(series):
        if not points:
            continue
        axes.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            color=series_color(index),
            linewidth=1.6,
            label=name,
        )
    axes.set_xlim(0.0, 1.0)
    axes.set_ylim(0.0, 1.05)
    axes.set_xlabel("Recall")
    axes.set_ylabel("Precision")
    axes.set_title("Precision-recall")
    axes.grid(True, linewidth=0.4, color="#cccccc")
    if series:
        axes.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    return _save(figure, stem)


def write_grouped_bars(
    bins: Sequence[str],
    series: Sequence[tuple[str, Sequence[float | None]]],
    stem: Path,
    *,
    ylabel: str,
    title: str,
) -> tuple[Path, Path]:
    """Grouped bars. A ``None`` value is a gap, not a zero."""
    figure = _figure()
    axes = _axes(figure)
    count = max(len(bins), 1)
    width = 0.8 / max(len(series), 1)
    positions = list(range(count))
    for index, (name, values) in enumerate(series):
        offset = (index - (len(series) - 1) / 2) * width
        xs: list[float] = []
        ys: list[float] = []
        for bin_index, value in enumerate(values):
            if value is None:
                continue
            xs.append(positions[bin_index] + offset)
            ys.append(value)
        axes.bar(xs, ys, width=width * 0.92, color=series_color(index), label=name)
    axes.set_xticks(positions)
    axes.set_xticklabels(list(bins), rotation=30 if count > 4 else 0, ha="right")
    axes.set_ylabel(ylabel)
    axes.set_title(title)
    axes.grid(True, axis="y", linewidth=0.4, color="#cccccc")
    if series:
        axes.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    return _save(figure, stem)


def write_degradation(
    series: Sequence[tuple[str, Sequence[tuple[float, float]]]],
    stem: Path,
    *,
    xlabel: str,
    ylabel: str,
) -> tuple[Path, Path]:
    """One line per series. Points are ``(level, value)`` and are sorted by level."""
    figure = _figure()
    axes = _axes(figure)
    for index, (name, points) in enumerate(series):
        ordered = sorted(points, key=lambda point: (point[0], point[1]))
        if not ordered:
            continue
        axes.plot(
            [point[0] for point in ordered],
            [point[1] for point in ordered],
            color=series_color(index),
            marker="o",
            linewidth=1.6,
            markersize=4.5,
            label=name,
        )
    axes.set_xlabel(xlabel)
    axes.set_ylabel(ylabel)
    axes.set_title("Degradation")
    axes.grid(True, linewidth=0.4, color="#cccccc")
    if series:
        axes.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    return _save(figure, stem)
