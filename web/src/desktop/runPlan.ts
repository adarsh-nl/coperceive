// SPDX-License-Identifier: Apache-2.0
/**
 * "New evaluation" run plan types and pure helpers.
 *
 * A plan is built and checked by Rust over SSH; this module mirrors the JSON
 * contract and holds the small pure functions the panel uses. Nothing here
 * submits a job: a plan is shown and saved, and the next step submits it after
 * the user has seen the full script.
 */
import type { EnvironmentSetup, EnvironmentKind, ServerEntry } from "./hpc";

export type ArgumentKind = "str" | "int" | "float" | "bool" | "path" | "enum";
export type ArgumentRole = "checkpoint" | "test_data" | "config" | "output" | "other";

export interface ArgumentSpec {
  name: string;
  kind: ArgumentKind;
  help: string;
  required: boolean;
  default: unknown;
  choices: string[];
  multiple: boolean;
  role: ArgumentRole;
}

export interface PipelineSpec {
  name: string;
  script: string;
  interpreter: string[];
  environment: EnvironmentSetup;
  arguments: ArgumentSpec[];
  output_contract: string;
  description: string;
}

export interface PipelineEntry {
  id: string;
  name: string;
  description: string;
  source: "builtin" | "custom";
  spec: PipelineSpec;
  registration_path: string | null;
  plugin_manifest: string | null;
}

export interface RunPlanRequest {
  profile_id: string;
  pipeline: PipelineSpec;
  pipeline_source: string;
  registration_path: string | null;
  plugin_manifest: string | null;
  values: Record<string, unknown>;
  checkpoint: string;
  split: string;
  environment_override: EnvironmentSetup | null;
}

export interface PlanArtifact {
  path: string;
  sha256: string | null;
  kind: string;
  real_path: string | null;
}

export interface PlanCheck {
  name: string;
  label: string;
  ok: boolean;
  detail: string;
  hint: string | null;
  duration_ms: number;
}

export interface PlanPipelineRef {
  id: string;
  name: string;
  version: string;
  source: string;
}

export interface RunPlan {
  schema: string;
  id: string;
  created_unix: number;
  profile_id: string;
  profile_name: string;
  pipeline: PlanPipelineRef;
  argv: string[];
  environment: EnvironmentSetup;
  environment_from_override: boolean;
  environment_note: string;
  checkpoint: PlanArtifact;
  split: PlanArtifact;
  output_dir: string;
  scratch_dir: string;
  /** Model-evaluation GPU default, e.g. gpu:lovelace:1; null for CPU-only. */
  gres?: string | null;
  checks: PlanCheck[];
  contract_check_script: string;
  nothing_submitted: boolean;
  saved_path: string | null;
}

/** One editable value per pipeline argument. */
export type ArgumentForm = Record<string, string | boolean | string[]>;

export function emptyEnvironmentOverride(): EnvironmentSetup {
  return {
    kind: "none",
    modules: [],
    interpreter: null,
    container_image: null,
    coperceive_command: ["coperceive"],
    wheelhouse: null,
    compute_has_internet: false,
  };
}

/** Form values for a pipeline: typed defaults from its schema. */
export function initialArgumentForm(spec: PipelineSpec): ArgumentForm {
  const form: ArgumentForm = {};
  for (const argument of spec.arguments) {
    const fallback = argument.default;
    if (argument.multiple) {
      form[argument.name] = Array.isArray(fallback)
        ? fallback.map((item) => String(item))
        : [];
    } else if (argument.kind === "bool") {
      form[argument.name] = fallback === true;
    } else if (fallback !== null && fallback !== undefined) {
      form[argument.name] = String(fallback);
    } else {
      form[argument.name] = "";
    }
  }
  return form;
}

/** Values to send to Rust; empty optional fields are omitted so defaults apply. */
export function collectValues(spec: PipelineSpec, form: ArgumentForm): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const argument of spec.arguments) {
    const raw = form[argument.name];
    if (argument.multiple) {
      const items = Array.isArray(raw) ? raw.map((item) => item.trim()).filter(Boolean) : [];
      if (items.length > 0) values[argument.name] = items;
      continue;
    }
    if (argument.kind === "bool") {
      values[argument.name] = raw === true;
      continue;
    }
    const text = typeof raw === "string" ? raw.trim() : "";
    if (text === "") continue;
    if (argument.kind === "int") {
      values[argument.name] = Number.parseInt(text, 10);
    } else if (argument.kind === "float") {
      values[argument.name] = Number.parseFloat(text);
    } else {
      values[argument.name] = text;
    }
  }
  return values;
}

/** Plain pre-flight errors; Rust still validates everything authoritatively. */
export function validateArgumentForm(spec: PipelineSpec, form: ArgumentForm): string[] {
  const errors: string[] = [];
  for (const argument of spec.arguments) {
    const raw = form[argument.name];
    if (argument.multiple) continue;
    if (argument.kind === "bool") continue;
    const text = typeof raw === "string" ? raw.trim() : "";
    if (argument.required && text === "") {
      errors.push(`“${argument.name}” is required. ${argument.help}`.trim());
    }
    if (text !== "" && argument.kind === "int" && !Number.isInteger(Number(text))) {
      errors.push(`“${argument.name}” must be a whole number.`);
    }
    if (text !== "" && argument.kind === "float" && Number.isNaN(Number(text))) {
      errors.push(`“${argument.name}” must be a number.`);
    }
    if (text !== "" && argument.kind === "enum" && !argument.choices.includes(text)) {
      errors.push(`“${argument.name}” must be one of: ${argument.choices.join(", ")}.`);
    }
  }
  return errors;
}

/** Every check passed, so the plan can be saved. */
export function planIsSaveable(plan: RunPlan | null): boolean {
  if (!plan) return false;
  if (!plan.checks.every((check) => check.ok)) return false;
  if (!plan.checkpoint.sha256 || !plan.split.sha256) return false;
  return plan.nothing_submitted === true;
}

/** argv rendered one element per line, never joined into one shell string. */
export function formatArgv(argv: string[]): { index: number; element: string }[] {
  return argv.map((element, index) => ({ index, element }));
}

export function environmentSummary(environment: EnvironmentSetup): string {
  switch (environment.kind) {
    case "modules":
      return environment.modules.length > 0
        ? `Modules: ${environment.modules.join(", ")}`
        : "Modules: none selected";
    case "conda":
    case "venv":
      return `Python: ${environment.interpreter ?? "not set"}`;
    case "container":
      return `Container: ${environment.container_image ?? "no image"}`;
    default:
      return "No modules, conda or container setup";
  }
}

export function environmentKindLabel(kind: EnvironmentKind): string {
  switch (kind) {
    case "modules":
      return "Environment modules";
    case "conda":
      return "conda";
    case "venv":
      return "Python virtualenv";
    case "container":
      return "Container (Apptainer/Singularity)";
    default:
      return "No special environment";
  }
}

/** Plain-language summary of a plan's result, for the panel header. */
export function planSummary(plan: RunPlan | null, entry: ServerEntry | null): string {
  if (!plan) return "No plan checked yet.";
  const server = entry?.profile.name ?? plan.profile_name;
  const ok = plan.checks.filter((check) => check.ok).length;
  return `${plan.pipeline.name} on ${server} · ${ok}/${plan.checks.length} checks passed`;
}

/** What the user can change under Advanced before submitting a saved plan. */
export interface SubmitOptions {
  walltime: string;
  cpus_per_task: number | null;
  account: string | null;
  qos: string | null;
  /** "" explicitly requests no GPU; a value requests that GRES; null keeps the plan default. */
  gres?: string | null;
}

/** The complete job script the user reviews before an explicit confirm. */
export interface SubmissionPreview {
  plan_id: string;
  job_name: string;
  partition: string;
  walltime: string;
  cpus_per_task: number | null;
  gres?: string | null;
  script: string;
  preview_hash: string;
}

/** What the scheduler answered when the user confirmed the submission. */
export interface JobSubmitOutcome {
  job_id: string;
  state: string;
  queue_reason: string | null;
  plan_id: string;
}

/** Plain-language scheduler state, including the pending queue reason. */
export function jobStateLabel(outcome: JobSubmitOutcome): string {
  if (outcome.state === "pending") {
    return outcome.queue_reason
      ? `PENDING — waiting in the queue: ${outcome.queue_reason}`
      : "PENDING — waiting in the queue";
  }
  return outcome.state.toUpperCase();
}

/** A live view of a submitted run (mirrors the Rust JobWatchStatus). */
export interface JobWatchStatus {
  job_id: string;
  plan_id: string;
  job_name: string;
  state: string;
  queue_reason: string | null;
  elapsed: string | null;
  time_left: string | null;
  node: string | null;
}

/** A bounded tail of one log stream. */
export interface JobLogTail {
  job_id: string;
  stream: string;
  lines: number;
  text: string;
  truncated: boolean;
}

/** The scheduler's answer to an explicit cancel. */
export interface JobCancelResult {
  job_id: string;
  state: string;
  queue_reason: string | null;
}

/** Terminal states never poll again. */
export function isJobActive(state: string): boolean {
  return state === "pending" || state === "running";
}

/** Plain-language live state, including the pending reason and timings. */
export function jobWatchLabel(status: JobWatchStatus): string {
  const base = status.state.toUpperCase();
  if (status.state === "pending") {
    const queue = status.queue_reason ? ` — waiting in the queue: ${status.queue_reason}` : " — waiting in the queue";
    return `PENDING${queue}`;
  }
  const parts = [base];
  if (status.elapsed) parts.push(`${status.elapsed} elapsed`);
  if (status.time_left && isJobActive(status.state)) parts.push(`${status.time_left} left`);
  return parts.join(" · ");
}

/** Poll only while the job can still change; never in a tight loop. */
export function shouldPollJob(state: string | null): boolean {
  return state !== null && isJobActive(state);
}

/** Latest progress counters from the pipeline's `{"type":"progress"}` lines. */
export interface PredictionProgress {
  scenes_done: number | null;
  scenes_total: number | null;
  frames_done: number | null;
  frames_total: number | null;
}

export interface LatestPrediction {
  agent_id: string;
  source: string;
  time_us: number;
}

export interface PredictionSourceCount {
  source: string;
  boxes: number;
}

/** One bounded read of the streamed predictions file. */
export interface PredictionTail {
  offset: number;
  size: number;
  boxes: number;
  sources: PredictionSourceCount[];
  progress: PredictionProgress | null;
  idle: boolean;
  latest: LatestPrediction | null;
}

/** The pipeline's own final report (docs/spec/pipeline-output.md). */
export interface MetricsReport {
  run: unknown;
  outputs: unknown;
  metrics: unknown;
  notes: string | null;
  raw: string;
}

/** What `Open predictions` imported and the viewer link it opens. */
export interface ImportedPredictions {
  run_id: string;
  scenes: string[];
  predictions_root: string;
  url: string;
}

export function progressLabel(progress: PredictionProgress | null): string {
  if (!progress) return "";
  const parts: string[] = [];
  if (progress.scenes_done !== null || progress.scenes_total !== null) {
    parts.push(`scenes ${progress.scenes_done ?? 0}/${progress.scenes_total ?? "?"}`);
  }
  if (progress.frames_done !== null || progress.frames_total !== null) {
    parts.push(`frames ${progress.frames_done ?? 0}/${progress.frames_total ?? "?"}`);
  }
  return parts.join(" · ");
}

/** Flatten the pipeline report's free-form metrics/outputs into table rows. */
export function metricsRows(report: MetricsReport): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  const run = report.run;
  if (run && typeof run === "object") {
    for (const key of ["model", "checkpoint"]) {
      const value = (run as Record<string, unknown>)[key];
      if (typeof value === "string" && value) rows.push({ label: key, value });
    }
  }
  if (report.metrics && typeof report.metrics === "object") {
    for (const [key, value] of Object.entries(report.metrics as Record<string, unknown>)) {
      rows.push({ label: key, value: typeof value === "string" ? value : String(value) });
    }
  }
  if (Array.isArray(report.outputs)) {
    for (const output of report.outputs) {
      if (!output || typeof output !== "object") continue;
      const item = output as Record<string, unknown>;
      const source = typeof item.source === "string" ? item.source : "output";
      const agent = typeof item.agent_id === "string" ? item.agent_id : "?";
      const boxes = typeof item.boxes === "number" ? item.boxes : "?";
      rows.push({ label: `${agent}/${source}`, value: `${boxes} boxes` });
    }
  }
  return rows;
}

/** The single next action shown after a failed run. */
export function failureNextAction(): string {
  return "Open the stderr tail above, fix the reported cause (checkpoint, environment, or split), then submit a new run from this plan.";
}

/** One submitted run in the history table. */
export interface RunHistoryEntry {
  job_id: string;
  plan_id: string;
  state: string;
  active: boolean;
  elapsed: string | null;
  time_limit: string | null;
  partition: string | null;
  started: string | null;
  ended: string | null;
  plan_available: boolean;
}

/** Plain-language state for a history row. */
export function historyStateLabel(entry: RunHistoryEntry): string {
  if (entry.state === "pending") return "PENDING";
  if (entry.state === "running") return "RUNNING";
  return entry.state.toUpperCase();
}

/** Re-run is possible only while the saved plan is still on the server. */
export function historyCanRerun(entry: RunHistoryEntry): boolean {
  return entry.plan_available;
}

/** A compact "elapsed / limit" column. */
export function historyTiming(entry: RunHistoryEntry): string {
  const elapsed = entry.elapsed ? `${entry.elapsed} elapsed` : "";
  const limit = entry.time_limit ? `${entry.time_limit} limit` : "";
  return [elapsed, limit].filter(Boolean).join(" · ") || "—";
}
