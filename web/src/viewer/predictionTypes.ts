import type { Pose } from "./types";
import { isPose } from "./types";

/** Bodies of the three prediction routes in docs/spec/predictions.md §HTTP, and guards for them. */

export type PredictionKind = "gt" | "prediction";
export type PredictionStatus = "tp" | "fp" | "miss" | "ignored" | "below_threshold";
export type CooperativeView = "recovered" | "lost_by_fusion" | "only_other_agent" | "nobody";
export type RunTag = "A" | "B";

/** `run.json`, §`run.json`. Unknown keys are kept out of the type and ignored. */
export type RunMeta = {
  format: string;
  format_version: number;
  run_id: string;
  model: string;
  created_utc: string;
  score_note: string;
  categories: string[];
  sources: Record<string, string>;
  notes?: string;
};

/** §Evaluation config, as copied into `metrics.json`. */
export type EvalConfig = {
  criterion: string;
  thresholds: number[];
  display_threshold: number;
  class_thresholds: Record<string, number[]>;
  ignore_gt_visibility_below: number | null;
  max_range_m: number | null;
  score_threshold: number;
};

/** One output of a run in the `/predictions` body: totals, AP and cooperative counts from `metrics.json`. */
export type RunOutput = {
  agent_id: string;
  source: string;
  totals: Record<string, number | null>;
  ap: Record<string, Record<string, number | null>>;
  cooperative: Record<string, number | null>;
};

export type RunInfo = {
  run_id: string;
  run: RunMeta;
  evaluated: boolean;
  config: EvalConfig | null;
  outputs: RunOutput[];
};

export type RunList = { runs: RunInfo[] };

/** A `matches.parquet` row (§`matches.parquet`) plus the four fields the frame route adds. */
export type PredictionRow = {
  kind: PredictionKind;
  agent_id: string;
  source: string;
  time_us: number;
  category: string;
  row: number | null;
  score: number | null;
  track_id: string | null;
  visibility: number | null;
  status: PredictionStatus;
  pair_id: number | null;
  iou_bev: number | null;
  centre_error_m: number | null;
  yaw_error_deg: number | null;
  cx: number;
  cy: number;
  cz: number;
  length: number;
  width: number;
  height: number;
  qw: number;
  qx: number;
  qy: number;
  qz: number;
  status_single: string | null;
  status_fused: string | null;
  other_kind: string | null;
  seen_by_other_gt: boolean | null;
  other_agent_tp: boolean | null;
  center: number[];
  size_lwh: number[];
  pose: Pose;
  lines: number[];
};

/** Every column of §`matches.parquet`, in table order. */
export const MATCH_COLUMNS = [
  "kind",
  "agent_id",
  "source",
  "time_us",
  "category",
  "row",
  "score",
  "track_id",
  "visibility",
  "status",
  "pair_id",
  "iou_bev",
  "centre_error_m",
  "yaw_error_deg",
  "cx",
  "cy",
  "cz",
  "length",
  "width",
  "height",
  "qw",
  "qx",
  "qy",
  "qz",
  "status_single",
  "status_fused",
  "other_kind",
  "seen_by_other_gt",
  "other_agent_tp",
] as const satisfies readonly (keyof PredictionRow)[];

type MatchColumn = (typeof MATCH_COLUMNS)[number];
type RowField = MatchColumn | "center" | "size_lwh" | "pose" | "lines";
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;

/**
 * Type-level check: `PredictionRow` has exactly the §`matches.parquet` columns plus
 * `center`, `size_lwh`, `pose` and `lines`. A missing or extra field makes this `never`
 * and the constant below stops compiling.
 */
export type PredictionRowCoversMatches = Same<keyof PredictionRow, RowField>;
export const PREDICTION_ROW_COVERS_MATCHES: PredictionRowCoversMatches = true;

export type PredictionFrame = {
  run_id: string;
  time_us: number;
  display_threshold: number;
  score_threshold: number;
  rows: PredictionRow[];
  compare: PredictionFrame | null;
};

export type SeriesOutput = {
  agent_id: string;
  source: string;
  times_us: number[];
  tp: number[];
  fp: number[];
  miss: number[];
  precision: (number | null)[];
  recall: (number | null)[];
};

export type PredictionSeries = {
  run_id: string;
  outputs: SeriesOutput[];
  compare: PredictionSeries | null;
};

const STATUSES: readonly string[] = ["tp", "fp", "miss", "ignored", "below_threshold"];
const BOX_NUMBERS = ["cx", "cy", "cz", "length", "width", "height", "qw", "qx", "qy", "qz"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isNumberList(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => typeof item === "number");
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

/** Absent, null, or the given primitive type: the nullable columns of the tables. */
function nullable(value: unknown, type: "number" | "string" | "boolean"): boolean {
  return value === undefined || value === null || typeof value === type;
}

function isNullableNumberList(value: unknown): value is (number | null)[] {
  return Array.isArray(value) && value.every((item) => item === null || typeof item === "number");
}

export function isRunMeta(value: unknown): value is RunMeta {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.format === "string" &&
    typeof value.format_version === "number" &&
    typeof value.run_id === "string" &&
    typeof value.model === "string" &&
    typeof value.created_utc === "string" &&
    typeof value.score_note === "string" &&
    isStringList(value.categories) &&
    isRecord(value.sources) &&
    Object.values(value.sources).every((item) => typeof item === "string")
  );
}

export function isEvalConfig(value: unknown): value is EvalConfig {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.criterion === "string" &&
    isNumberList(value.thresholds) &&
    typeof value.display_threshold === "number" &&
    isRecord(value.class_thresholds) &&
    nullable(value.ignore_gt_visibility_below, "number") &&
    nullable(value.max_range_m, "number") &&
    typeof value.score_threshold === "number"
  );
}

function isRunOutput(value: unknown): value is RunOutput {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.agent_id === "string" &&
    typeof value.source === "string" &&
    isRecord(value.totals) &&
    isRecord(value.ap) &&
    Object.values(value.ap).every(isRecord) &&
    isRecord(value.cooperative)
  );
}

function isRunInfo(value: unknown): value is RunInfo {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.run_id === "string" &&
    isRunMeta(value.run) &&
    typeof value.evaluated === "boolean" &&
    (value.config === null || isEvalConfig(value.config)) &&
    Array.isArray(value.outputs) &&
    value.outputs.every(isRunOutput)
  );
}

export function isRunList(value: unknown): value is RunList {
  return isRecord(value) && Array.isArray(value.runs) && value.runs.every(isRunInfo);
}

export function isPredictionRow(value: unknown): value is PredictionRow {
  if (!isRecord(value)) {
    return false;
  }
  if (value.kind !== "gt" && value.kind !== "prediction") {
    return false;
  }
  if (typeof value.status !== "string" || !STATUSES.includes(value.status)) {
    return false;
  }
  return (
    typeof value.agent_id === "string" &&
    typeof value.source === "string" &&
    typeof value.time_us === "number" &&
    typeof value.category === "string" &&
    nullable(value.row, "number") &&
    nullable(value.score, "number") &&
    nullable(value.track_id, "string") &&
    nullable(value.visibility, "number") &&
    nullable(value.pair_id, "number") &&
    nullable(value.iou_bev, "number") &&
    nullable(value.centre_error_m, "number") &&
    nullable(value.yaw_error_deg, "number") &&
    BOX_NUMBERS.every((key) => typeof value[key] === "number") &&
    nullable(value.status_single, "string") &&
    nullable(value.status_fused, "string") &&
    nullable(value.other_kind, "string") &&
    nullable(value.seen_by_other_gt, "boolean") &&
    nullable(value.other_agent_tp, "boolean") &&
    isNumberList(value.center) &&
    value.center.length === 3 &&
    isNumberList(value.size_lwh) &&
    value.size_lwh.length === 3 &&
    isPose(value.pose) &&
    isNumberList(value.lines)
  );
}

export function isPredictionFrame(value: unknown): value is PredictionFrame {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.run_id === "string" &&
    typeof value.time_us === "number" &&
    typeof value.display_threshold === "number" &&
    typeof value.score_threshold === "number" &&
    Array.isArray(value.rows) &&
    value.rows.every(isPredictionRow) &&
    (value.compare === null || value.compare === undefined || isPredictionFrame(value.compare))
  );
}

function isSeriesOutput(value: unknown): value is SeriesOutput {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.agent_id === "string" &&
    typeof value.source === "string" &&
    isNumberList(value.times_us) &&
    isNumberList(value.tp) &&
    isNumberList(value.fp) &&
    isNumberList(value.miss) &&
    isNullableNumberList(value.precision) &&
    isNullableNumberList(value.recall)
  );
}

export function isPredictionSeries(value: unknown): value is PredictionSeries {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.run_id === "string" &&
    Array.isArray(value.outputs) &&
    value.outputs.every(isSeriesOutput) &&
    (value.compare === null || value.compare === undefined || isPredictionSeries(value.compare))
  );
}

/* Derivations over the bodies, used by the side panel and the timeline. */

export function outputKey(agentId: string, source: string): string {
  return `${agentId}|${source}`;
}

/** The output a scene opens on: the vehicle's `single`, else the first `single`, else the first output. */
export function defaultOutput(run: RunInfo, types: ReadonlyMap<string, string>): RunOutput | null {
  const outputs = run.outputs;
  return (
    outputs.find((output) => output.source === "single" && types.get(output.agent_id) === "vehicle") ??
    outputs.find((output) => output.source === "single") ??
    outputs[0] ??
    null
  );
}

export type CooperativeCounts = Record<CooperativeView, number>;

export type EvalFigures = {
  runId: string;
  map: { threshold: string; value: number | null }[];
  precision: number | null;
  recall: number | null;
  cooperative: CooperativeCounts;
  recoveryRate: number | null;
};

/** What the scene card shows for one output: mAP at each threshold, recall and the four cooperative counts, with run B beside. */
export type EvalSummary = {
  agentId: string;
  source: string;
  a: EvalFigures;
  b: EvalFigures | null;
};

function numberOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function countOf(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function figuresFor(run: RunInfo, output: RunOutput): EvalFigures {
  const thresholds = Object.keys(output.ap).sort((left, right) => Number(left) - Number(right));
  return {
    runId: run.run_id,
    map: thresholds.map((threshold) => ({ threshold, value: numberOrNull(output.ap[threshold]?.mean) })),
    precision: numberOrNull(output.totals.precision),
    recall: numberOrNull(output.totals.recall),
    cooperative: {
      recovered: countOf(output.cooperative.recovered),
      lost_by_fusion: countOf(output.cooperative.lost_by_fusion),
      only_other_agent: countOf(output.cooperative.only_other_agent),
      nobody: countOf(output.cooperative.nobody),
    },
    recoveryRate: numberOrNull(output.cooperative.recovery_rate),
  };
}

export function summarizeOutput(
  run: RunInfo,
  compare: RunInfo | null,
  agentId: string,
  source: string,
): EvalSummary | null {
  const output = run.outputs.find((item) => item.agent_id === agentId && item.source === source);
  if (!output) {
    return null;
  }
  const other = compare?.outputs.find((item) => item.agent_id === agentId && item.source === source) ?? null;
  return {
    agentId,
    source,
    a: figuresFor(run, output),
    b: compare && other ? figuresFor(compare, other) : null,
  };
}

/** Per-frame series for the timeline, aligned to the scene's sample times. A sample the output has no row for is NaN. */
export type EvalSeries = { precision: number[]; recall: number[]; fp: number[] };

export function alignSeries(
  series: PredictionSeries,
  agentId: string,
  source: string,
  times: readonly number[],
): EvalSeries | null {
  const output = series.outputs.find((item) => item.agent_id === agentId && item.source === source);
  if (!output) {
    return null;
  }
  const at = new Map<number, number>();
  output.times_us.forEach((time, index) => at.set(time, index));
  const pickValue = (values: readonly (number | null)[], index: number | undefined): number => {
    if (index === undefined) {
      return Number.NaN;
    }
    const value = values[index];
    return typeof value === "number" ? value : Number.NaN;
  };
  const precision: number[] = [];
  const recall: number[] = [];
  const fp: number[] = [];
  for (const time of times) {
    const index = at.get(time);
    precision.push(pickValue(output.precision, index));
    recall.push(pickValue(output.recall, index));
    fp.push(pickValue(output.fp, index));
  }
  return { precision, recall, fp };
}

/** A clicked prediction wire: which run it came from, its row, and the ground-truth row it matched. */
export type EvalPick = { tag: RunTag; runId: string; row: PredictionRow; gt: PredictionRow | null };

/** What the inspector adds under a clicked ground-truth box: its status in every drawn output, and who else sees it. */
export type GtEvalDetail = {
  statuses: { tag: RunTag; runId: string; source: string; status: string; score: number | null }[];
  seenByOtherGt: boolean | null;
  otherKind: string | null;
  otherAgentTp: boolean | null;
};

export function gtKey(agentId: string, trackId: string | null | undefined, center: readonly number[] | null | undefined): string {
  if (typeof trackId === "string" && trackId) {
    return `${agentId}|id|${trackId}`;
  }
  if (center && center.length >= 3) {
    return `${agentId}|at|${center.slice(0, 3).map((item) => item.toFixed(2)).join(",")}`;
  }
  return `${agentId}|?`;
}

export function rowKey(row: PredictionRow): string {
  return gtKey(row.agent_id, row.track_id, row.center);
}

export function gtEvalDetail(frame: PredictionFrame, key: string): GtEvalDetail | null {
  const statuses: GtEvalDetail["statuses"] = [];
  let seenByOtherGt: boolean | null = null;
  let otherKind: string | null = null;
  let otherAgentTp: boolean | null = null;
  const runs: { tag: RunTag; frame: PredictionFrame }[] = [{ tag: "A", frame }];
  if (frame.compare) {
    runs.push({ tag: "B", frame: frame.compare });
  }
  for (const run of runs) {
    for (const row of run.frame.rows) {
      if (row.kind !== "gt" || rowKey(row) !== key) {
        continue;
      }
      const match = row.pair_id === null ? null : run.frame.rows.find((item) => item.kind === "prediction" && item.pair_id === row.pair_id);
      statuses.push({ tag: run.tag, runId: run.frame.run_id, source: row.source, status: row.status, score: match?.score ?? null });
      if (run.tag === "A") {
        seenByOtherGt = row.seen_by_other_gt ?? seenByOtherGt;
        otherKind = row.other_kind ?? otherKind;
        otherAgentTp = row.other_agent_tp ?? otherAgentTp;
      }
    }
  }
  if (statuses.length === 0) {
    return null;
  }
  return { statuses, seenByOtherGt, otherKind, otherAgentTp };
}

/** The same prediction in a newer frame body, by run, output and track id. Without a track id the pick does not follow. */
export function followEvalPick(pick: EvalPick, frame: PredictionFrame): EvalPick | null {
  const source = pick.tag === "A" ? frame : frame.compare;
  if (!source || source.run_id !== pick.runId) {
    return null;
  }
  const row = source.rows.find(
    (item) =>
      item.kind === "prediction" &&
      item.agent_id === pick.row.agent_id &&
      item.source === pick.row.source &&
      (pick.row.track_id ? item.track_id === pick.row.track_id : item.time_us === pick.row.time_us && item.row === pick.row.row),
  );
  if (!row) {
    return null;
  }
  const gt = row.pair_id === null ? null : source.rows.find((item) => item.kind === "gt" && item.pair_id === row.pair_id) ?? null;
  return { tag: pick.tag, runId: pick.runId, row, gt };
}
