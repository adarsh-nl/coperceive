# SPDX-License-Identifier: Apache-2.0
"""One colour-blind-safe style for paper figures.

The palette is the eight colours from Wong, "Color blindness", Nature Methods
8, 441 (2011). Paper plots use these and no other series colours. The
bird's-eye and camera figures keep their own viewer theme.
"""

from __future__ import annotations

# Black, orange, sky blue, bluish green, yellow, blue, vermillion, reddish purple.
PALETTE: tuple[str, ...] = (
    "#000000",
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#F0E442",
    "#0072B2",
    "#D55E00",
    "#CC79A7",
)

# Series start on blue so the first line stays visible on white.
SERIES: tuple[str, ...] = (
    "#0072B2",
    "#E69F00",
    "#009E73",
    "#D55E00",
    "#CC79A7",
    "#56B4E9",
    "#000000",
    "#F0E442",
)


def series_color(index: int) -> str:
    """A palette colour. Indices wrap, so a ninth series repeats the first."""
    return SERIES[index % len(SERIES)]


def rc_params() -> dict[str, object]:
    """Matplotlib settings that keep paper figures stable across machines.

    ``svg.hashsalt`` stops SVG clip-path ids from changing between processes.
    Type 42 fonts embed as outlines-free TrueType so the PDF text stays text.
    """
    return {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.labelsize": 10,
        "axes.titlesize": 11,
        "axes.linewidth": 0.6,
        "axes.edgecolor": "#222222",
        "axes.labelcolor": "#222222",
        "xtick.color": "#222222",
        "ytick.color": "#222222",
        "text.color": "#222222",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "svg.hashsalt": "coperceive-paper",
        "axes.unicode_minus": False,
    }
