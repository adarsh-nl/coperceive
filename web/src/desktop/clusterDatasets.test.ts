// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  formatDetectBannerTitle,
  formatDuration,
  formatLicenceInfo,
  formatSensorsSummary,
  formatStorageEstimate,
  generatePrepareScriptText,
  datasetDetect,
  datasetIndex,
  datasetStatus,
  datasetPrepare,
  datasetPrepareSubmit,
  datasetOpen,
  _resetStubStore,
  _setStubSceneState,
  type ClusterDetectDocument,
  type ClusterIndexDocument,
} from "./clusterDatasets.ts";

test("formatDetectBannerTitle formats adapter, split, and scene count plainly", () => {
  const docOpv2v: ClusterDetectDocument = {
    schema: "coperceive-cluster-detect-1",
    path: "/datasets/opv2v/test",
    adapter: "opv2v",
    raw_root: "/datasets/opv2v",
    split: "test",
    params: { split: "test" },
    licence: null,
    scene_count: 16,
    status: "ok",
    reason: null,
  };
  assert.strictEqual(formatDetectBannerTitle(docOpv2v), "OPV2V · test · 16 scenes");

  const docGriffin: ClusterDetectDocument = {
    schema: "coperceive-cluster-detect-1",
    path: "/datasets/griffin",
    adapter: "griffin",
    raw_root: "/datasets/griffin",
    split: null,
    params: {},
    licence: null,
    scene_count: 1,
    status: "ok",
    reason: null,
  };
  assert.strictEqual(formatDetectBannerTitle(docGriffin), "GRIFFIN · 1 scene");
});

test("formatLicenceInfo formats known and unknown licence metadata with links", () => {
  const unknownLicence = {
    status: "unknown",
    spdx: null,
    notice: "No licence published.",
    links: ["https://mobility-lab.seas.ucla.edu/opv2v/"],
  };
  const resUnknown = formatLicenceInfo(unknownLicence);
  assert.strictEqual(
    resUnknown.statusText,
    "licence not published — research use on this cluster only",
  );
  assert.deepStrictEqual(resUnknown.links, ["https://mobility-lab.seas.ucla.edu/opv2v/"]);

  const knownLicence = {
    status: "known",
    spdx: "Apache-2.0",
    notice: "Apache License 2.0",
    links: ["https://example.com/license"],
  };
  const resKnown = formatLicenceInfo(knownLicence);
  assert.strictEqual(resKnown.statusText, "SPDX: Apache-2.0");
  assert.deepStrictEqual(resKnown.links, ["https://example.com/license"]);

  const nullLicence = formatLicenceInfo(null);
  assert.strictEqual(nullLicence.statusText, "Licence not specified.");
  assert.deepStrictEqual(nullLicence.links, []);
});

test("formatDuration and formatSensorsSummary handle missing and populated data", () => {
  assert.strictEqual(formatDuration(null), "—");
  assert.strictEqual(formatDuration(undefined), "—");
  assert.strictEqual(formatDuration(6), "6.0s");
  assert.strictEqual(formatDuration(3.24), "3.2s");

  assert.strictEqual(formatSensorsSummary(null), "—");
  assert.strictEqual(
    formatSensorsSummary({
      sensors: 2,
      camera_samples: 240,
      lidar_samples: 60,
    }),
    "240 cam / 60 lidar (2 sensors)",
  );
});

test("formatStorageEstimate gives sensible bounds from byte sizes or scene counts", () => {
  const mockDoc: ClusterIndexDocument = {
    schema: "coperceive-cluster-index-1",
    adapter: "opv2v",
    raw_root: "/data/opv2v",
    split: "test",
    params: {},
    licence: null,
    fingerprint: { algorithm: "sha1-mtime-size-v1", digest: "abc", entries: 10, truncated: false },
    cache: { relative_path: "coperceive-cache/opv2v/abc/test" },
    scene_count: 16,
    scenes: [],
    errors: [],
    truncated: false,
    elapsed_ms: 100,
    storage_estimate_bytes: 2.5 * 1024 * 1024 * 1024,
  };
  assert.strictEqual(formatStorageEstimate(mockDoc), "~2.5 GB");

  delete mockDoc.storage_estimate_bytes;
  assert.strictEqual(formatStorageEstimate(mockDoc), "~2.3 GB");
});

test("generatePrepareScriptText generates CPU-only Slurm job with exact arguments", () => {
  const scriptAll = generatePrepareScriptText({
    adapter: "opv2v",
    rawRoot: "/datasets/opv2v",
    cacheRoot: "/scratch/results/coperceive-cache/opv2v/1234/test",
    scenes: "all",
    workers: 4,
    partition: "cpu-short",
  });

  assert.ok(scriptAll.includes("#SBATCH --job-name=coperceive-prepare-opv2v"));
  assert.ok(scriptAll.includes("#SBATCH --partition=cpu-short"));
  assert.ok(scriptAll.includes("#SBATCH --cpus-per-task=4"));
  assert.ok(scriptAll.includes("# No GPU requested (CPU only)"));
  assert.ok(!scriptAll.includes("--gres="));
  assert.ok(scriptAll.includes("--adapter \"opv2v\""));
  assert.ok(scriptAll.includes("--all"));
  assert.ok(scriptAll.includes("--workers 4"));

  const scriptSingle = generatePrepareScriptText({
    adapter: "griffin",
    rawRoot: "/datasets/griffin",
    cacheRoot: "/scratch/results/coperceive-cache/griffin/1234/all",
    scenes: ["scene_001"],
    workers: 2,
  });

  assert.ok(scriptSingle.includes("#SBATCH --job-name=coperceive-prepare-griffin"));
  assert.ok(scriptSingle.includes("#SBATCH --cpus-per-task=2"));
  assert.ok(scriptSingle.includes("--scenes scene_001"));
  assert.ok(scriptSingle.includes("--workers 2"));
});

test("datasetDetect and datasetIndex provide typed cluster contract shapes", async () => {
  const detectRes = await datasetDetect("profile-1", "/datasets/opv2v/test");
  assert.strictEqual(detectRes.schema, "coperceive-cluster-detect-1");
  assert.strictEqual(detectRes.status, "ok");
  assert.strictEqual(detectRes.adapter, "opv2v");
  assert.strictEqual(detectRes.scene_count, 16);

  const indexRes = await datasetIndex("profile-1", "opv2v", "/datasets/opv2v");
  assert.strictEqual(indexRes.schema, "coperceive-cluster-index-1");
  assert.strictEqual(indexRes.adapter, "opv2v");
  assert.strictEqual(indexRes.scene_count, 16);
  assert.strictEqual(indexRes.scenes.length, 16);
  assert.strictEqual(indexRes.scenes[0]?.scene_id, "scene_0001");
  assert.strictEqual(indexRes.scenes[0]?.sample_count, 60);
  assert.strictEqual(indexRes.scenes[0]?.sensors.camera_samples, 240);
  assert.strictEqual(indexRes.scenes[0]?.sensors.lidar_samples, 60);
});

test("datasetStatus tracks scene status states and polling transitions", async () => {
  _resetStubStore();
  const cacheRoot = "/scratch/results/coperceive-cache/opv2v/test";

  // Initially empty
  const initialStatus = await datasetStatus("profile-1", cacheRoot);
  assert.strictEqual(initialStatus.schema, "coperceive-cluster-scene-status-1");
  assert.strictEqual(initialStatus.scenes.length, 0);

  // Mark scenes in different states
  _setStubSceneState(cacheRoot, "scene_0001", "ready");
  _setStubSceneState(cacheRoot, "scene_0002", "preparing", null, "651911");
  _setStubSceneState(cacheRoot, "scene_0003", "failed", "vehicle-side/pose/000000.json: missing");
  _setStubSceneState(cacheRoot, "scene_0004", "stale");

  const statusDoc = await datasetStatus("profile-1", cacheRoot);
  assert.strictEqual(statusDoc.scenes.length, 4);

  const s1 = statusDoc.scenes.find((s) => s.scene_id === "scene_0001");
  assert.strictEqual(s1?.state, "ready");
  assert.strictEqual(s1?.reason, null);

  const s2 = statusDoc.scenes.find((s) => s.scene_id === "scene_0002");
  assert.strictEqual(s2?.state, "preparing");
  assert.strictEqual(s2?.job_id, "651911");

  const s3 = statusDoc.scenes.find((s) => s.scene_id === "scene_0003");
  assert.strictEqual(s3?.state, "failed");
  assert.strictEqual(s3?.reason, "vehicle-side/pose/000000.json: missing");

  const s4 = statusDoc.scenes.find((s) => s.scene_id === "scene_0004");
  assert.strictEqual(s4?.state, "stale");
});

test("datasetPrepare and submit workflow tracks scenes as preparing", async () => {
  _resetStubStore();
  const cacheRoot = "/scratch/results/coperceive-cache/opv2v/test";

  const preview = await datasetPrepare("profile-1", {
    adapter: "opv2v",
    rawRoot: "/datasets/opv2v",
    cacheRoot,
    all: true,
    workers: 4,
  });

  assert.strictEqual(preview.workers, 4);
  assert.ok(preview.script.includes("# No GPU requested (CPU only)"));

  const submitRes = await datasetPrepareSubmit("profile-1", preview, ["scene_0001", "scene_0002"]);
  assert.strictEqual(submitRes.status, "submitted");
  assert.ok(submitRes.job_id.length > 0);

  const statusAfter = await datasetStatus("profile-1", cacheRoot);
  assert.strictEqual(statusAfter.scenes.length, 2);
  assert.strictEqual(statusAfter.scenes[0]?.state, "preparing");
  assert.strictEqual(statusAfter.scenes[1]?.state, "preparing");
});

test("opening a ready scene completes and emits open event", async () => {
  _resetStubStore();
  const cacheRoot = "/scratch/results/coperceive-cache/opv2v/test";
  _setStubSceneState(cacheRoot, "scene_0001", "ready");

  const openRes = await datasetOpen("profile-1", "scene_0001", cacheRoot);
  assert.strictEqual(openRes.scene_id, "scene_0001");
  assert.strictEqual(openRes.cache_root, cacheRoot);

  // Test object-param overload matching frozen contract
  const openResContract = await datasetOpen("profile-1", "scene_0001", {
    adapter: "opv2v",
    rawRoot: "/datasets/opv2v",
    split: "test",
    cacheRoot,
  });
  assert.strictEqual(openResContract.scene_id, "scene_0001");
  assert.strictEqual(openResContract.cache_root, cacheRoot);
});

test("datasetStatus and datasetPrepare accept frozen contract parameter objects", async () => {
  _resetStubStore();
  const cacheRoot = "/scratch/results/coperceive-cache/opv2v/test";
  _setStubSceneState(cacheRoot, "scene_0001", "ready");

  const statusDoc = await datasetStatus("profile-1", {
    adapter: "opv2v",
    rawRoot: "/datasets/opv2v",
    split: "test",
    cacheRoot,
  });
  assert.strictEqual(statusDoc.schema, "coperceive-cluster-scene-status-1");
  assert.strictEqual(statusDoc.scenes.length, 1);
  assert.strictEqual(statusDoc.scenes[0]?.scene_id, "scene_0001");

  const preview = await datasetPrepare("profile-1", {
    adapter: "opv2v",
    rawRoot: "/datasets/opv2v",
    split: "test",
    cacheRoot,
    scenes: ["scene_0001"],
    workers: 2,
  });
  assert.strictEqual(preview.workers, 2);
  assert.ok(preview.script.includes("# No GPU requested (CPU only)"));
  assert.ok(preview.preview_hash?.length);

  const submitRes = await datasetPrepareSubmit("profile-1", preview, ["scene_0001"], true);
  assert.strictEqual(submitRes.status, "submitted");
  assert.strictEqual(submitRes.state, "pending");
});
