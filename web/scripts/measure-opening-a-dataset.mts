// SPDX-License-Identifier: Apache-2.0
/**
 * Measures what `Open dataset` costs before and after the cached scene index.
 *
 * The index command is the one the app runs on the login node
 * (`coperceive dataset index`), executed here through the same fake-cluster
 * setup the desktop e2e uses: the local machine is the cluster, with the
 * repository's own `.venv` Python. No network, no Slurm, no real dataset.
 *
 *   scripts/desktop/measure_workspace_open.sh [--scenes N]
 *
 * Prints one JSON document:
 *   index_ms_before      cold `dataset index` on this machine (what the view blocked on)
 *   open_ms_before       time to the scene list before (the index call)
 *   open_ms_after        the cached document lookup that runs first on the next
 *                        open. This is NOT time-to-screen: React's first render
 *                        of the already-fetched row data is not measured, and on
 *                        a real cluster the background command still runs.
 *   background_ms_after  the check-for-changes index that runs after the cache
 *
 * These are fake-cluster, local numbers: no SSH, Slurm, network or real dataset
 * is involved, and the real-cluster time is unverified.
 */
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

import { _setInvokeForTesting, datasetIndex } from "../src/desktop/clusterDatasets.ts";
import {
  _resetIndexCache,
  readCachedIndex,
  writeCachedIndex,
} from "../src/desktop/indexCache.ts";

const rawRoot = process.env.COPERCEIVE_MEASURE_RAW_ROOT;
const split = process.env.COPERCEIVE_MEASURE_SPLIT || "train";
const python = process.env.COPERCEIVE_MEASURE_PYTHON || "../.venv/bin/python";
const reportPath = process.env.COPERCEIVE_MEASURE_REPORT || "";
if (!rawRoot) {
  console.error("COPERCEIVE_MEASURE_RAW_ROOT is required (the synthetic OPV2V dataset root)");
  process.exit(2);
}

// The app window: Node has neither a Tauri bridge nor storage. The bridge is
// empty because the index command goes through the test invoke below.
(globalThis as unknown as { window: unknown }).window = { __TAURI_INTERNALS__: {} };
const values = new Map<string, string>();
(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => {
    values.set(key, value);
  },
  removeItem: (key: string) => {
    values.delete(key);
  },
};

let indexCommands = 0;
_setInvokeForTesting(async <T,>(command: string, args?: Record<string, unknown>): Promise<T> => {
  if (command !== "dataset_index") throw new Error(`unexpected command ${command}`);
  indexCommands += 1;
  const argv = [
    "-m",
    "coperceive.cli",
    "dataset",
    "index",
    "--adapter",
    String(args?.adapter ?? "opv2v"),
    "--raw-root",
    String(args?.rawRoot ?? ""),
  ];
  if (args?.split) argv.push("--split", String(args.split));
  const stdout = execFileSync(python, argv, { encoding: "utf-8", maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(stdout) as T;
});

function ms(start: number): number {
  return Math.round((performance.now() - start) * 10) / 10;
}

const profile = "measure-fake-cluster";
const adapter = "opv2v";
// The view's memoised rows are cheap; what it waits on is the index answer.
_resetIndexCache();
const coldStart = performance.now();
const cold = await datasetIndex(profile, adapter, rawRoot, split);
const coldMs = ms(coldStart);
// The view caches under the index's own root (the server's real path).
const servedRoot = cold.raw_root || rawRoot;
writeCachedIndex(profile, adapter, servedRoot, split, cold);

const hitStart = performance.now();
const cached = readCachedIndex(profile, adapter, servedRoot, split);
const hitMs = ms(hitStart);
if (!cached) throw new Error("the cached index was not readable right after writing it");

// What the background "checking for changes…" index costs after the cache.
const backgroundStart = performance.now();
await datasetIndex(profile, adapter, servedRoot, split);
const backgroundMs = ms(backgroundStart);

const result = {
  dataset: `${adapter} ${split}`,
  raw_root: rawRoot,
  scenes: cold.scene_count,
  fingerprint: cold.fingerprint,
  cluster: "fake (this machine runs the login-node command; no SSH, Slurm or network)",
  note: "open_ms_after is the cached lookup only, not time-to-screen; real-cluster times are unverified",
  index_ms_before: coldMs,
  open_ms_before: coldMs,
  open_ms_after: hitMs,
  background_ms_after: backgroundMs,
  index_commands: indexCommands,
};
if (reportPath) writeFileSync(reportPath, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
