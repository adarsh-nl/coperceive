// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { buildPredictionObjects, cooperativeViews } from "./predictionLayer.ts";
import type { PredictionRow } from "./predictionTypes.ts";

function makeRow(overrides: Partial<PredictionRow> = {}): PredictionRow {
  return {
    kind: "gt",
    agent_id: "cav-a",
    source: "fused",
    time_us: 100_000,
    category: "Car",
    row: 0,
    score: null,
    track_id: "trk-1",
    visibility: 1,
    status: "miss",
    pair_id: null,
    iou_bev: null,
    centre_error_m: null,
    yaw_error_deg: null,
    cx: 10,
    cy: 2,
    cz: 0,
    length: 4,
    width: 2,
    height: 1.6,
    qw: 1,
    qx: 0,
    qy: 0,
    qz: 0,
    status_single: "miss",
    status_fused: "tp",
    other_kind: null,
    seen_by_other_gt: false,
    other_agent_tp: null,
    center: [10, 2, 0],
    size_lwh: [4, 2, 1.6],
    pose: { qw: 1, qx: 0, qy: 0, qz: 0, tx: 10, ty: 2, tz: 0 },
    lines: [0, 0, 0, 1, 0, 0],
    ...overrides,
  };
}

test("cooperativeViews matches docs/eval-spec.md A.9 for own and connected-agent added boxes", () => {
  // Own box missed alone, found by fusion, and also TP by other agent.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: "miss",
        status_fused: "tp",
        seen_by_other_gt: true,
        other_kind: "computed",
        other_agent_tp: true,
      }),
    ),
    ["recovered", "only_other_agent"],
  );

  // Connected-agent added box recovered by fusion and TP on other agent's single output.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: null,
        status_fused: "tp",
        seen_by_other_gt: true,
        other_kind: null,
        other_agent_tp: true,
      }),
    ),
    ["recovered", "only_other_agent"],
  );

  // Connected-agent added box missed by fusion and not TP on other agent.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: null,
        status_fused: "miss",
        seen_by_other_gt: true,
        other_kind: null,
        other_agent_tp: false,
      }),
    ),
    ["nobody"],
  );

  // Connected-agent added box missed by fusion, but TP on other agent.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: null,
        status_fused: "miss",
        seen_by_other_gt: true,
        other_kind: null,
        other_agent_tp: true,
      }),
    ),
    ["only_other_agent"],
  );

  // Own box missed by both and not TP on other agent.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: "miss",
        status_fused: "miss",
        seen_by_other_gt: false,
        other_kind: null,
        other_agent_tp: null,
      }),
    ),
    ["nobody"],
  );

  // Box lost by fusion.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: "tp",
        status_fused: "miss",
        seen_by_other_gt: false,
        other_kind: null,
        other_agent_tp: null,
      }),
    ),
    ["lost_by_fusion"],
  );

  // Ignored in either output falls in none of the cooperative views.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: "miss",
        status_fused: "ignored",
        seen_by_other_gt: true,
        other_kind: "computed",
        other_agent_tp: true,
      }),
    ),
    [],
  );
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: "ignored",
        status_fused: "miss",
        seen_by_other_gt: false,
        other_kind: null,
        other_agent_tp: false,
      }),
    ),
    [],
  );

  // Own box when single output is absent does not enter any view.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: null,
        status_fused: "miss",
        seen_by_other_gt: false,
        other_kind: null,
        other_agent_tp: null,
      }),
    ),
    [],
  );

  // No fused output -> no cooperative views.
  assert.deepEqual(
    cooperativeViews(
      makeRow({
        status_single: "miss",
        status_fused: null,
        seen_by_other_gt: true,
        other_kind: "computed",
        other_agent_tp: true,
      }),
    ),
    [],
  );
});

test("buildPredictionObjects includes connected-agent added boxes under recovered and only_other_agent filters", () => {
  const addedGt = makeRow({
    kind: "gt",
    track_id: "added-gt",
    status: "tp",
    pair_id: 42,
    status_single: null,
    status_fused: "tp",
    seen_by_other_gt: true,
    other_kind: null,
    other_agent_tp: true,
  });
  const matchedPred = makeRow({
    kind: "prediction",
    track_id: "pred-42",
    score: 0.9,
    status: "tp",
    pair_id: 42,
    status_single: null,
    status_fused: null,
    other_kind: null,
    seen_by_other_gt: null,
    other_agent_tp: null,
  });
  const baseOptions = {
    showStatuses: new Set(["tp", "fp", "miss", "ignored"] as const),
    showSources: new Set(["fused"]),
    minScore: 0,
    runTag: "A" as const,
    runId: "run-1",
  };

  const recovered = buildPredictionObjects([addedGt, matchedPred], {
    ...baseOptions,
    cooperative: new Set(["recovered"]),
  });
  assert.equal(recovered.shown, 2);
  assert.equal(recovered.counts.tp, 1);
  assert.equal(recovered.dimmedGtKeys.size, 1);

  const onlyOther = buildPredictionObjects([addedGt, matchedPred], {
    ...baseOptions,
    cooperative: new Set(["only_other_agent"]),
  });
  assert.equal(onlyOther.shown, 2);
  assert.equal(onlyOther.counts.tp, 1);
  assert.equal(onlyOther.dimmedGtKeys.size, 1);
});
