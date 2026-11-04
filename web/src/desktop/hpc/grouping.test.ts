// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  groupScenes,
  isDatasetGrouping,
  recordingTime,
  type SceneRowState,
} from "./grouping.ts";

interface Row {
  scene_id: string;
  state: SceneRowState;
}

const rows: Row[] = [
  { scene_id: "2021_08_21_09_09_41", state: "failed" },
  { scene_id: "2021_08_18_23_23_19", state: "ready" },
  { scene_id: "scene_0010", state: "not_prepared" },
  { scene_id: "2021_08_18_19_11_02", state: "preparing" },
  { scene_id: "scene_0002", state: "stale" },
  { scene_id: "2021_08_21_15_41_04", state: "queued" },
];
const stateOf = (row: Row) => row.state;
const ids = (group: { rows: Row[] }) => group.rows.map((row) => row.scene_id);

test("no grouping: one flat list sorted by scene name, numbers in order", () => {
  const groups = groupScenes(rows, "none", stateOf);
  assert.strictEqual(groups.length, 1);
  assert.strictEqual(groups[0].label, "");
  assert.deepStrictEqual(ids(groups[0]), [
    "2021_08_18_19_11_02",
    "2021_08_18_23_23_19",
    "2021_08_21_09_09_41",
    "2021_08_21_15_41_04",
    "scene_0002",
    "scene_0010",
  ]);
  assert.deepStrictEqual(groupScenes([], "none", stateOf), []);
});

test("by state: failed, stale, preparing, queued, ready, not prepared; empty states left out", () => {
  const groups = groupScenes(rows, "state", stateOf);
  assert.deepStrictEqual(
    groups.map((group) => group.label),
    ["Failed", "Stale", "Preparing", "Queued", "Ready", "Not prepared"],
  );
  const onlyReady = groupScenes(
    rows.filter((row) => row.state === "ready"),
    "state",
    stateOf,
  );
  assert.deepStrictEqual(onlyReady.map((group) => group.key), ["ready"]);
});

test("by recording day: 'Recorded <day>' headers in date order; names without a date come last", () => {
  const groups = groupScenes(rows, "day", stateOf);
  assert.deepStrictEqual(
    groups.map((group) => group.label),
    ["Recorded Wed 18 Aug 2021", "Recorded Sat 21 Aug 2021", "Recording day unknown"],
  );
  assert.deepStrictEqual(ids(groups[0]), ["2021_08_18_19_11_02", "2021_08_18_23_23_19"]);
  assert.deepStrictEqual(ids(groups[2]), ["scene_0002", "scene_0010"]);
});

test("by recording day with no dated names: one 'Recording day unknown' group", () => {
  const undated = rows.filter((row) => row.scene_id.startsWith("scene_"));
  const groups = groupScenes(undated, "day", stateOf);
  assert.deepStrictEqual(groups.map((group) => group.key), ["unknown"]);
});

test("recordingTime reads only YYYY_MM_DD_HH_MM_SS with real calendar values", () => {
  assert.deepStrictEqual(recordingTime("2021_08_18_19_11_02"), {
    dayKey: "20210818",
    dayLabel: "Recorded Wed 18 Aug 2021",
    recorded: "Recorded 18 Aug 2021, 19:11",
    short: "08-18 19:11:02",
  });
  for (const name of [
    "scene_0001",
    "Town03-000",
    "2021_08_18",
    "2021_08_18_19_11",
    "2021_08_18_19_11_02_extra",
    "x2021_08_18_19_11_02",
    "2021_13_01_00_00_00",
    "2021_02_30_00_00_00",
    "2021_08_18_24_00_00",
    "2021_08_18_19_60_00",
    "2021-08-18-19-11-02",
    "",
  ]) {
    assert.strictEqual(recordingTime(name), null, name);
  }
});

test("isDatasetGrouping accepts only the three settings", () => {
  for (const value of ["none", "state", "day"]) assert.ok(isDatasetGrouping(value), value);
  for (const value of ["date", "", null, undefined, 1]) assert.ok(!isDatasetGrouping(value), String(value));
});
