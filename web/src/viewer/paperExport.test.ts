import assert from "node:assert/strict";
import test from "node:test";
import {
  clampDecimals,
  defaultPaperOptions,
  paperRequestBody,
  paperSavedNote,
  toggleMetric,
} from "./paperExport.ts";

test("paper options default to fixtures and three metrics", () => {
  const options = defaultPaperOptions();
  assert.deepEqual(options.metrics, ["precision", "recall", "ap"]);
  assert.equal(options.qualitative, false);
  assert.equal(options.allowGriffin, false);
  assert.equal(options.allowCarla, false);
  assert.equal(options.boldBest, true);
  assert.equal(options.includeCounts, true);
  const body = paperRequestBody(options, "det-a", {
    scene_id: "synth_001",
    time_us: 0,
  });
  assert.equal(body.run_id, "det-a");
  assert.deepEqual(body.allow_datasets, []);
  assert.deepEqual(body.frames, []);
  assert.equal(paperSavedNote("scene-paper.zip"), "Ready for Overleaf. Saved to scene-paper.zip.");
});

test("qualitative frames and dataset opt-in follow the checkboxes", () => {
  const options = {
    ...defaultPaperOptions(),
    qualitative: true,
    allowGriffin: true,
    decimals: 9,
  };
  const body = paperRequestBody(options, null, {
    scene_id: "synth_001",
    time_us: 100000,
    agent_id: "vehicle",
    sensor_id: "cam_front",
  });
  assert.equal(body.run_id, null);
  assert.equal(body.decimals, 6);
  assert.deepEqual(body.allow_datasets, ["griffin"]);
  assert.equal(body.frames.length, 1);
  assert.equal(body.frames[0]?.scene_id, "synth_001");
  assert.equal(clampDecimals(Number.NaN), 3);
});

test("metric toggles keep a stable order", () => {
  const without = toggleMetric(defaultPaperOptions(), "recall");
  assert.deepEqual(without.metrics, ["precision", "ap"]);
  const restored = toggleMetric(without, "recall");
  assert.deepEqual(restored.metrics, ["precision", "recall", "ap"]);
});
