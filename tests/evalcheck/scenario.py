# SPDX-License-Identifier: Apache-2.0
"""Plain-data description of an evaluation problem and of its result.

This module is the only thing the product adapter (``tests/evalcheck/product.py``)
and the reference evaluator (``tests/reference_eval``) have in common. It holds
data and no logic: numbers, strings and tuples. It imports nothing from
``coperceive`` and nothing from the reference, so neither side can borrow the
other's arithmetic through it (``docs/adr/0016-evaluator-verification.md`` §3).

The meaning of every field is in ``docs/eval-spec.md`` Part A; the section
numbers are quoted below.

Conventions (A.1):

* lengths in metres, times in integer microseconds;
* a rotation is a unit quaternion ``(w, x, y, z)`` of a right-handed frame;
* a pose is ``world_from_body``: ``p_world = R(q) @ p_body + t``;
* ``size_lwh`` is ``(length, width, height)``: length along the box x axis,
  width along the box y axis, height along the box z axis.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

FRAME_BODY = "body"
FRAME_WORLD = "world"

STATUS_TP = "tp"
STATUS_FP = "fp"
STATUS_MISS = "miss"
STATUS_IGNORED = "ignored"
STATUS_BELOW_THRESHOLD = "below_threshold"

CRITERION_IOU_BEV = "iou_bev"
CRITERION_CENTRE = "centre"

SOURCE_SINGLE = "single"
SOURCE_FUSED = "fused"

OTHER_KIND_SHARED_TRACK = "shared_track"
OTHER_KIND_COMPUTED = "computed"


# --- the problem ------------------------------------------------------------------


@dataclass(frozen=True)
class PoseSample:
    """``world_from_body`` of an agent at one pose time."""

    time_us: int
    rotation: Quat
    translation: Vec3


@dataclass(frozen=True)
class AgentSpec:
    """An agent and its pose samples, strictly increasing in time."""

    agent_id: str
    poses: tuple[PoseSample, ...]


@dataclass(frozen=True)
class BoxSpec:
    """A 3D box in ``frame``: ``body`` (the agent body at the row's time) or ``world``."""

    center: Vec3
    size_lwh: Vec3
    rotation: Quat
    frame: str


@dataclass(frozen=True)
class GtSpec:
    """One ground-truth annotation. Its row index is its position in ``SceneSpec.ground_truth``."""

    agent_id: str
    time_us: int
    category: str
    track_id: str | None
    visibility: float | None
    box: BoxSpec


@dataclass(frozen=True)
class PredSpec:
    """One predicted box. Its row index is its position in ``SceneSpec.predictions``."""

    agent_id: str
    source: str
    time_us: int
    category: str
    score: float
    box: BoxSpec


@dataclass(frozen=True)
class SceneSpec:
    """One scene: agents in scene order, annotations and predictions in file row order.

    ``shared_tracks`` are the track ids the dataset declares to be one object for
    more than one agent, as ``(track_id, category)`` (A.9).
    """

    scene_id: str
    agents: tuple[AgentSpec, ...]
    ground_truth: tuple[GtSpec, ...]
    predictions: tuple[PredSpec, ...]
    shared_tracks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ConfigSpec:
    """The evaluation config (A.2). ``class_thresholds`` and ``gt_class_map`` may be empty."""

    criterion: str = CRITERION_IOU_BEV
    thresholds: tuple[float, ...] = (0.5, 0.7)
    display_threshold: float = 0.5
    class_thresholds: Mapping[str, tuple[float, ...]] = field(
        default_factory=dict[str, tuple[float, ...]]
    )
    ignore_gt_visibility_below: float | None = None
    max_range_m: float | None = None
    score_threshold: float = 0.3
    gt_class_map: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class RunSpec:
    """A run: the classes the model declares, the config and the scenes.

    ``scenes`` may be given in any order; pooling orders them by ``scene_id`` (A.8).
    """

    categories: tuple[str, ...]
    config: ConfigSpec
    scenes: tuple[SceneSpec, ...]


# --- the result -------------------------------------------------------------------


@dataclass(frozen=True)
class PairResult:
    """A true positive at the operating point and the errors of the pair (A.4, A.7)."""

    pred_row: int
    gt_row: int
    iou_bev: float
    centre_error_m: float
    yaw_error_deg: float


@dataclass(frozen=True)
class SampleResult:
    """Counts of one pose time of the output's agent at the operating point (A.6)."""

    time_us: int
    tp: int
    fp: int
    miss: int
    ignored_gt: int
    precision: float | None
    recall: float | None


@dataclass(frozen=True)
class TotalsResult:
    """Sums over the samples of one output (A.6)."""

    tp: int
    fp: int
    miss: int
    ignored_gt: int
    gt: int
    predictions: int
    precision: float | None
    recall: float | None


@dataclass(frozen=True)
class TpErrorsResult:
    """Error summary over the true positives at the operating point (A.7)."""

    centre_mean_m: float | None
    centre_median_m: float | None
    yaw_mean_abs_deg: float | None
    size_length_m: float | None
    size_width_m: float | None
    size_height_m: float | None
    iou_bev_mean: float | None


@dataclass(frozen=True)
class GtCooperativeResult:
    """The cooperative columns of one ground-truth box (A.9)."""

    status_single: str | None
    status_fused: str | None
    seen_by_other_gt: bool
    other_kind: str | None
    other_agent_tp: bool | None


@dataclass(frozen=True)
class CooperativeResult:
    """The cooperative counts of one output (A.9)."""

    recovered: int
    lost_by_fusion: int
    only_other_agent: int
    nobody: int
    recovery_rate: float | None


@dataclass(frozen=True)
class ApResult:
    """AP at one threshold: per category, and the two means (A.5, A.8)."""

    per_category: Mapping[str, float | None]
    mean: float | None
    mean_model: float | None


@dataclass(frozen=True)
class OutputResult:
    """Everything computed for one ``(agent_id, source)`` output of one scene.

    ``pred_status`` and ``gt_status`` are at the operating point (display threshold,
    score threshold applied), keyed by row. ``pred_status_at[i]`` is the AP-pass
    status at ``thresholds[i]`` (score threshold not applied). ``ap[i]`` is the
    per-scene AP at ``thresholds[i]``. ``gt_cooperative`` is keyed by ground-truth row.
    """

    agent_id: str
    source: str
    pred_status: Mapping[int, str]
    gt_status: Mapping[int, str]
    pred_status_at: tuple[Mapping[int, str], ...]
    pairs: tuple[PairResult, ...]
    samples: tuple[SampleResult, ...]
    totals: TotalsResult
    ap: tuple[ApResult, ...]
    tp_errors: TpErrorsResult
    cooperative: CooperativeResult
    gt_cooperative: Mapping[int, GtCooperativeResult]


@dataclass(frozen=True)
class SceneResult:
    """The outputs of one scene, in the order they first appear in its predictions."""

    scene_id: str
    outputs: tuple[OutputResult, ...]


@dataclass(frozen=True)
class PooledResult:
    """One headline row (A.8): an output pooled over its scenes, or every output pooled.

    ``agent_id`` and ``source`` are both ``None`` for the row over every output.
    ``ap[i]`` is the pooled AP at ``thresholds[i]``.
    """

    agent_id: str | None
    source: str | None
    gt: int
    predictions: int
    tp: int
    fp: int
    miss: int
    precision: float | None
    recall: float | None
    recovered: int
    lost_by_fusion: int
    ap: tuple[ApResult, ...]


@dataclass(frozen=True)
class RunResult:
    """Per-scene results in ``scene_id`` order, then the headline rows.

    ``scenes`` covers the evaluated scenes (A.8): the scenes of the run that have at
    least one prediction row. A scene without one has no prediction file, is not
    evaluated and appears nowhere in the result.

    ``pooled`` holds one row per output in first-appearance order over the evaluated
    scenes taken in ``scene_id`` order, followed by the row over every output.
    """

    scenes: tuple[SceneResult, ...]
    pooled: tuple[PooledResult, ...]
