// SPDX-License-Identifier: Apache-2.0
/**
 * Dataset view: a scene's row state, its recording time read from its name, and
 * the three ways of grouping the scene list (Settings → Datasets → "Group
 * scenes"). Pure functions, unit-tested in grouping.test.ts.
 */

/** "Group scenes": a flat list (default), by state, or by recording day. */
export type DatasetGrouping = "none" | "state" | "day";

export const DATASET_GROUPINGS: readonly DatasetGrouping[] = ["none", "state", "day"];

export function isDatasetGrouping(value: unknown): value is DatasetGrouping {
  return typeof value === "string" && (DATASET_GROUPINGS as readonly string[]).includes(value);
}

/** What a row shows; "queued" comes from a submitted job that has not reached the scene. */
export type SceneRowState = "ready" | "preparing" | "queued" | "stale" | "failed" | "not_prepared";

/** By state: what needs attention first. */
export const STATE_GROUP_ORDER: readonly SceneRowState[] = [
  "failed",
  "stale",
  "preparing",
  "queued",
  "ready",
  "not_prepared",
];

export const STATE_LABEL: Record<SceneRowState, string> = {
  ready: "Ready",
  preparing: "Preparing",
  queued: "Queued",
  stale: "Stale",
  failed: "Failed",
  not_prepared: "Not prepared",
};

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface RecordingTime {
  /** "20210818": sorts and groups by day. */
  dayKey: string;
  /** "Recorded Wed 18 Aug 2021": a day header that never reads as app activity. */
  dayLabel: string;
  /** "Recorded 18 Aug 2021, 19:11" for the inspector. */
  recorded: string;
  /** "08-18 19:11:02" for the row. */
  short: string;
}

/**
 * The recording time in a scene name of the form YYYY_MM_DD_HH_MM_SS (OPV2V and
 * V2XSet name scenes so), or null for any other name: nothing is guessed.
 */
export function recordingTime(sceneId: string): RecordingTime | null {
  const match = /^(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})$/.exec(sceneId);
  if (!match) return null;
  const [, yy, mm, dd, hh, mi, ss] = match;
  const year = Number(yy);
  const month = Number(mm);
  const day = Number(dd);
  if (Number(hh) > 23 || Number(mi) > 59 || Number(ss) > 59) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null;
  }
  const dow = DOW[date.getUTCDay()];
  const mon = MON[month - 1];
  return {
    dayKey: `${yy}${mm}${dd}`,
    dayLabel: `Recorded ${dow} ${day} ${mon} ${year}`,
    recorded: `Recorded ${day} ${mon} ${year}, ${hh}:${mi}`,
    short: `${mm}-${dd} ${hh}:${mi}:${ss}`,
  };
}

export interface SceneGroup<T> {
  key: string;
  /** Empty for the flat list: no header is drawn. */
  label: string;
  rows: T[];
}

const byName = <T extends { scene_id: string }>(left: T, right: T) =>
  left.scene_id.localeCompare(right.scene_id, "en", { numeric: true });

/**
 * The scene list in the chosen grouping. Every group is sorted by scene name;
 * empty groups are left out. By day, scenes whose names carry no recording time
 * come last, under "Recording day unknown".
 */
export function groupScenes<T extends { scene_id: string }>(
  rows: readonly T[],
  mode: DatasetGrouping,
  stateOf: (row: T) => SceneRowState,
): SceneGroup<T>[] {
  const sorted = [...rows].sort(byName);
  if (mode === "state") {
    return STATE_GROUP_ORDER.map((state) => ({
      key: state,
      label: STATE_LABEL[state],
      rows: sorted.filter((row) => stateOf(row) === state),
    })).filter((group) => group.rows.length > 0);
  }
  if (mode === "day") {
    const days = new Map<string, SceneGroup<T>>();
    const unknown: T[] = [];
    for (const row of sorted) {
      const time = recordingTime(row.scene_id);
      if (!time) {
        unknown.push(row);
        continue;
      }
      const group = days.get(time.dayKey) ?? { key: time.dayKey, label: time.dayLabel, rows: [] };
      group.rows.push(row);
      days.set(time.dayKey, group);
    }
    const groups = [...days.values()].sort((left, right) => left.key.localeCompare(right.key));
    if (unknown.length) groups.push({ key: "unknown", label: "Recording day unknown", rows: unknown });
    return groups;
  }
  return sorted.length ? [{ key: "all", label: "", rows: sorted }] : [];
}
