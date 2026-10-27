// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  browseCrumbs,
  browseRows,
  childBrowsePath,
  OPEN_BLOCKED_REASON,
  parentBrowsePath,
  relativeBrowsePath,
  scanSummary,
  selectionIsScene,
  selectionLabel,
  skippedMessages,
  truncatedMessage,
} from "./browse.ts";
import type { ScanDocument } from "./hpc.ts";

function document(overrides: Partial<ScanDocument> = {}): ScanDocument {
  return {
    status: "ok",
    root: "/data/scenes",
    path: "/data/scenes",
    relative_path: ".",
    depth: 2,
    timeout_s: 10,
    max_entries: 200,
    max_total: 2000,
    elapsed_ms: 6.4,
    truncated: false,
    truncated_reason: null,
    folder_count: 5,
    scene_count: 1,
    dataset_count: 1,
    folders: [
      {
        path: ".",
        depth: 0,
        is_scene: false,
        scene_id: null,
        dataset: true,
        scene_count: 1,
        has_manifest: false,
        has_complete: false,
        subfolder_count: 3,
        file_count: 0,
      },
      {
        path: "Town03-000",
        depth: 1,
        is_scene: true,
        scene_id: "Town03-000",
        dataset: false,
        scene_count: 0,
        has_manifest: true,
        has_complete: true,
        subfolder_count: 0,
        file_count: 7,
      },
      {
        path: "datasets",
        depth: 1,
        is_scene: false,
        scene_id: null,
        dataset: true,
        scene_count: 2,
        has_manifest: false,
        has_complete: false,
        subfolder_count: 3,
        file_count: 0,
      },
      {
        path: "loose-folder",
        depth: 1,
        is_scene: false,
        scene_id: null,
        dataset: false,
        scene_count: 0,
        has_manifest: false,
        has_complete: false,
        subfolder_count: 0,
        file_count: 2,
      },
    ],
    scenes: [
      { path: "Town03-000", scene_id: "Town03-000", folder_name: "Town03-000" },
    ],
    datasets: [{ path: "datasets", scene_count: 2 }],
    skipped: [],
    ...overrides,
  };
}

test("rows show scenes first, then datasets, then plain folders, never the current folder", () => {
  const rows = browseRows(document());
  assert.deepStrictEqual(
    rows.map((row) => [row.name, row.kind]),
    [
      ["Town03-000", "scene"],
      ["datasets", "dataset"],
      ["loose-folder", "folder"],
    ],
  );
  assert.strictEqual(rows[0]?.detail, "scene Town03-000");
  assert.strictEqual(rows[1]?.detail, "2 scenes");
  assert.strictEqual(rows[2]?.detail, "0 folders · 2 files");
  assert.deepStrictEqual(browseRows(null), []);
});

test("breadcrumbs, parent and child paths never climb above the data root", () => {
  assert.deepStrictEqual(browseCrumbs("/data/scenes", ""), [
    { label: "/data/scenes", path: "" },
  ]);
  assert.deepStrictEqual(browseCrumbs("/data/scenes", "datasets/Town03-007"), [
    { label: "/data/scenes", path: "" },
    { label: "datasets", path: "datasets" },
    { label: "Town03-007", path: "datasets/Town03-007" },
  ]);
  assert.deepStrictEqual(browseCrumbs("/data/scenes", "../etc"), [
    { label: "/data/scenes", path: "" },
  ]);
  assert.strictEqual(parentBrowsePath("datasets/Town03-007"), "datasets");
  assert.strictEqual(parentBrowsePath("datasets"), "");
  assert.strictEqual(parentBrowsePath("../etc"), "");
  assert.strictEqual(parentBrowsePath("a/../../etc"), "");
  assert.strictEqual(childBrowsePath("datasets", "Town03-007"), "datasets/Town03-007");
  assert.strictEqual(childBrowsePath("", "datasets"), "datasets");
  assert.strictEqual(childBrowsePath("datasets", "../escape"), "datasets");
  assert.strictEqual(childBrowsePath("datasets", "a/../../etc"), "datasets");
  assert.strictEqual(relativeBrowsePath(document()), "");
  assert.strictEqual(relativeBrowsePath(document({ relative_path: "datasets" })), "datasets");
});

test("a bound that stopped the scan is explained in plain words", () => {
  assert.strictEqual(truncatedMessage(null), null);
  assert.strictEqual(truncatedMessage(document()), null);
  const timeout = truncatedMessage(document({ truncated: true, truncated_reason: "timeout" }));
  assert.ok(timeout?.includes("stopped after 10 s"), timeout ?? "");
  assert.ok(timeout?.includes("login node stays free"), timeout ?? "");
  const entries = truncatedMessage(document({ truncated: true, truncated_reason: "entry limit" }));
  assert.ok(entries?.includes("200"), entries ?? "");
  const total = truncatedMessage(document({ truncated: true, truncated_reason: "total limit" }));
  assert.ok(total?.includes("2000"), total ?? "");
  const other = truncatedMessage(document({ truncated: true, truncated_reason: "weird" }));
  assert.ok(other?.includes("weird"), other ?? "");
});

test("skipped entries and the summary stay factual", () => {
  const skipped = skippedMessages(
    document({
      skipped: [{ path: "datasets/camera-link", reason: "symlink target is outside the data root" }],
    }),
  );
  assert.deepStrictEqual(skipped, [
    "datasets/camera-link: symlink target is outside the data root.",
  ]);
  assert.deepStrictEqual(skippedMessages(null), []);
  assert.strictEqual(scanSummary(document()), "5 folders · 1 scene · 1 dataset read in 6 ms.");
  assert.strictEqual(scanSummary(null), "No folder scanned yet.");
});

test("selection works but opening a scene stays blocked and says why", () => {
  assert.strictEqual(selectionLabel(document(), "Town03-000"), "Scene Town03-000");
  assert.strictEqual(selectionLabel(document(), "datasets"), "Dataset datasets (2 scenes)");
  assert.strictEqual(selectionLabel(document(), "loose-folder"), "Folder loose-folder");
  assert.strictEqual(selectionLabel(document(), null), null);
  assert.strictEqual(selectionLabel(document(), "../etc"), null);
  assert.strictEqual(selectionIsScene(document(), "Town03-000"), true);
  assert.strictEqual(selectionIsScene(document(), "datasets"), false);
  assert.ok(OPEN_BLOCKED_REASON.includes("confirmed job-submission step"), OPEN_BLOCKED_REASON);
  assert.ok(OPEN_BLOCKED_REASON.includes("Nothing was started"), OPEN_BLOCKED_REASON);
});
