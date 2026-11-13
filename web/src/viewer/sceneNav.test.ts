// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  camerasFoldCount,
  initialSampleIndex,
  neighbourScene,
  resolveActiveDataset,
} from "./sceneNav.ts";

const order = ["a", "b", "c", "d", "e"];

test("neighbourScene moves to the nearest prepared scene and names the skipped ones", () => {
  const ready = new Set(["a", "c", "e"]);
  assert.deepStrictEqual(neighbourScene(order, ready, "a", 1), { scene: "c", skipped: ["b"] });
  assert.deepStrictEqual(neighbourScene(order, ready, "c", -1), { scene: "a", skipped: ["b"] });
  assert.deepStrictEqual(neighbourScene(order, ready, "e", 1), { scene: null, skipped: [] });
  assert.deepStrictEqual(neighbourScene(order, new Set(["c"]), "c", 1), { scene: null, skipped: ["d", "e"] });
  assert.deepStrictEqual(neighbourScene(order, ready, "zz", 1), { scene: null, skipped: [] });
});

test("resolveActiveDataset ignores stale cluster dataset metadata when a local scene is loaded", () => {
  const staleCluster = {
    scenes: ["train_0000", "train_0001"],
    about: { label: "V2XSET train", server: "UT EEMCS HPC" },
    statuses: { train_0000: "ready", train_0001: "not_prepared" },
  };
  assert.deepStrictEqual(resolveActiveDataset("demo-junction", ["demo-junction"], staleCluster), {
    scenes: [],
    about: null,
    statuses: {},
  });
  assert.deepStrictEqual(resolveActiveDataset("Town03-000", ["Town03-000", "Town04-000"], staleCluster), {
    scenes: [],
    about: null,
    statuses: {},
  });
  assert.deepStrictEqual(resolveActiveDataset("train_0000", ["train_0000"], staleCluster), staleCluster);
});

test("initialSampleIndex starts at sample 0 when opening a different scene than last_scene_id", () => {
  const times = [0, 100_000, 200_000, 300_000];
  assert.equal(initialSampleIndex({ last_scene_id: "demo-junction", last_sample_index: 2 }, "Town03-000", times), 0);
  assert.equal(initialSampleIndex({ last_scene_id: "Town03-000", last_sample_index: 2 }, "Town03-000", times), 2);
  assert.equal(initialSampleIndex({ last_scene_id: "Town03-000", last_timestamp_us: 300_000 }, "Town03-000", times), 3);
  assert.equal(initialSampleIndex({ last_scene_id: "Town03-000", last_timestamp_us: 300_000 }, "train_0000", times), 0);
});

test("camerasFoldCount keeps total camera count when Frustum images is off", () => {
  const keys = ["cav-a|front", "cav-b|front", "cav-c|front", "cav-d|front"];
  const allOff = { frustumImages: false, frustumImageCameras: ["cav-a|front"] };
  assert.equal(camerasFoldCount(allOff, keys), "4");
  const allOn = { frustumImages: true, frustumImageCameras: null };
  assert.equal(camerasFoldCount(allOn, keys), "4");
  const twoOn = { frustumImages: true, frustumImageCameras: ["cav-a|front", "cav-c|front"] };
  assert.equal(camerasFoldCount(twoOn, keys), "2/4");
});
