# SPDX-License-Identifier: Apache-2.0
"""An independent reference evaluator for the 3D object-detection metrics.

This package was written from ``docs/eval-spec.md`` Part A ("The computed
definitions", sections A.0 to A.10; the breakdowns of A.11 and the labels of A.12
have no field in ``RunResult``) alone, without sight of the product evaluator, and
it imports nothing from ``coperceive``. Its only inputs are that text and the
plain-data types of ``tests.evalcheck.scenario``; its only dependencies are the
standard library and ``shapely`` (the footprint intersection is a GEOS overlay).

It exists to be an oracle: the same ``RunSpec`` is given to the product and to
:func:`evaluate`, and the two ``RunResult`` values are compared.

Where each part of Part A lives:

* ``geometry``: A.1 rotations, A.3 geometry, and the point-in-box test of A.4;
* ``matching``: A.2 config and A.4 matching one sample;
* ``gtset``: A.4 ground-truth set of a fused output (connected agents, observed
  annotations, the union);
* ``ap``: A.5 average precision under either ranking scope;
* ``cooperative``: A.9 pairing of other agents' boxes and the cooperative counts;
* ``evaluate``: A.0 outputs and samples, A.4 ground-truth sets per output, A.5 to
  A.7 per-scene tables, A.8 pooling, A.9 columns.
"""

from __future__ import annotations

from tests.reference_eval.ap import average_precision
from tests.reference_eval.evaluate import evaluate
from tests.reference_eval.geometry import (
    PlacedBox,
    bev_iou,
    centre_distance_m,
    footprint,
    horizontal_range_m,
    place_box,
    yaw_error_deg,
    yaw_rad,
)

__all__ = [
    "PlacedBox",
    "average_precision",
    "bev_iou",
    "centre_distance_m",
    "evaluate",
    "footprint",
    "horizontal_range_m",
    "place_box",
    "yaw_error_deg",
    "yaw_rad",
]
