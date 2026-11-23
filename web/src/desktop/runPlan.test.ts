// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import {
  scoringSummary,
  type EvaluationRecord,
  collectValues,
  emptyEnvironmentOverride,
  environmentKindLabel,
  environmentSummary,
  failureNextAction,
  formatArgv,
  historyCanRerun,
  slurmSeconds,
  defaultWalltime,
  walltimeProblem,
  historyStateLabel,
  historyTiming,
  reproPreviewLines,
  isJobActive,
  jobStateLabel,
  metricsRows,
  jobWatchLabel,
  shouldPollJob,
  initialArgumentForm,
  planIsSaveable,
  artifactFingerprintText,
  builtinPlanInputProblem,
  pipelineNeedsCheckpoint,
  pipelineNeedsCkptRoot,
  pipelineNeedsRawRoot,
  pipelineNeedsSplit,
  DEFAULT_EVAL_PROTOCOL,
  EVAL_PROTOCOL_CHOICES,
  evalProtocolConfigLabel,
  evalProtocolLabel,
  sameEvalProtocol,
  planSummary,
  progressLabel,
  validateArgumentForm,
  type JobWatchStatus,
  type PipelineEntry,
  type PipelineSpec,
  type RunPlan,
} from "./runPlan.ts";
import { emptyProfile } from "./hpc.ts";

const spec: PipelineSpec = {
  name: "contract-check",
  script: "/data/code/pipelines/contract_pipeline.py",
  interpreter: ["/usr/bin/python3"],
  environment: emptyEnvironmentOverride(),
  arguments: [
    { name: "checkpoint", kind: "path", help: "weights", required: true, default: null, choices: [], multiple: false, role: "checkpoint" },
    { name: "split", kind: "path", help: "test split", required: true, default: null, choices: [], multiple: false, role: "test_data" },
    { name: "extra", kind: "str", help: "free text", required: false, default: null, choices: [], multiple: false, role: "other" },
    { name: "frame_stride", kind: "int", help: "stride", required: false, default: 1, choices: [], multiple: false, role: "other" },
    { name: "half", kind: "bool", help: "half precision", required: false, default: false, choices: [], multiple: false, role: "other" },
    { name: "tag", kind: "enum", help: "tag", required: false, default: null, choices: ["a", "b"], multiple: false, role: "other" },
    { name: "options", kind: "str", help: "options", required: false, default: null, choices: [], multiple: true, role: "other" },
  ],
  output_contract: "coperceive-pipeline-1",
  description: "",
};

function form() {
  const form = initialArgumentForm(spec);
  form.checkpoint = "/data/code/checkpoints/model.pt";
  form.split = "/data/code/splits/test.txt";
  return form;
}

function plan(overrides: Partial<RunPlan> = {}): RunPlan {
  return {
    schema: "coperceive-run-plan-1",
    id: "plan-1",
    created_unix: 1,
    profile_id: "server-1",
    profile_name: "Cluster B",
    pipeline: { id: "contract-check", name: "contract-check", version: "", source: "custom" },
    argv: ["/usr/bin/python3", "/data/code/pipelines/contract_pipeline.py", "--checkpoint", "/data/code/checkpoints/model.pt", "--extra", "a b; touch /tmp/x", "--output-dir", "/data/results/runs/plan-1"],
    environment: { ...emptyEnvironmentOverride(), kind: "modules", modules: ["python/3.12"] },
    environment_from_override: false,
    environment_note: "Environment from the server profile.",
    checkpoint: { path: "/data/code/checkpoints/model.pt", sha256: "abc", kind: "file", real_path: "/data/code/checkpoints/model.pt" },
    split: { path: "/data/code/splits/test.txt", sha256: "def", kind: "file", real_path: "/data/code/splits/test.txt" },
    ckpt_root: { path: "", sha256: null, kind: "", real_path: null },
    model_options: [],
    eval_protocol: "default",
    output_dir: "/data/results/runs/plan-1",
    scratch_dir: "/work/alice",
    checks: [{ name: "checkpoint", label: "Checkpoint", ok: true, detail: "", hint: null, duration_ms: 1 }],
    contract_check_script: "#!/bin/bash\n",
    nothing_submitted: true,
    saved_path: null,
    ...overrides,
  };
}

test("initial form carries typed defaults, never site values", () => {
  const values = initialArgumentForm(spec);
  assert.strictEqual(values.checkpoint, "");
  assert.strictEqual(values.frame_stride, "1");
  assert.strictEqual(values.half, false);
  assert.deepStrictEqual(values.options, []);
  assert.ok(!JSON.stringify(values).toLowerCase().includes("university"));
  assert.strictEqual(emptyProfile("x").scheduler, "none");
});

test("collectValues omits empty optional values and keeps hostile text one value", () => {
  const values = form();
  values.extra = "a b; touch /tmp/x";
  const collected = collectValues(spec, values);
  assert.strictEqual(collected.extra, "a b; touch /tmp/x");
  assert.strictEqual(collected.frame_stride, 1);
  assert.strictEqual(collected.half, false);
  assert.ok(!("tag" in collected));
  assert.ok(!("options" in collected));
  // The hostile value is one JSON string, never split or joined with flags.
  assert.deepStrictEqual(Object.keys(collected).sort(), [
    "checkpoint",
    "extra",
    "frame_stride",
    "half",
    "split",
  ]);
});

test("validation explains required, integer, float and enum mistakes", () => {
  const values = form();
  assert.deepStrictEqual(validateArgumentForm(spec, values), []);
  values.checkpoint = "";
  assert.ok(validateArgumentForm(spec, values).some((error) => error.includes("checkpoint")));
  values.checkpoint = "/data/code/checkpoints/model.pt";
  values.frame_stride = "2.5";
  assert.ok(validateArgumentForm(spec, values).some((error) => error.includes("whole number")));
  values.frame_stride = "2";
  values.tag = "z";
  assert.ok(validateArgumentForm(spec, values).some((error) => error.includes("must be one of")));
});

test("argv is displayed one element per line so a hostile value stays a single element", () => {
  const lines = formatArgv(plan().argv);
  assert.strictEqual(lines[5].element, "a b; touch /tmp/x");
  assert.ok(lines.every((line) => typeof line.element === "string"));
  assert.strictEqual(lines[lines.length - 2].element, "--output-dir");
});

test("a plan is saveable only when every check passed and both hashes exist", () => {
  assert.strictEqual(planIsSaveable(plan()), true);
  const failing = plan({
    checks: [{ name: "checkpoint", label: "Checkpoint", ok: false, detail: "missing", hint: null, duration_ms: 1 }],
  });
  assert.strictEqual(planIsSaveable(failing), false);
  const noHash = plan({ checkpoint: { path: "/x", sha256: null, kind: "folder", real_path: "/x" } });
  assert.strictEqual(planIsSaveable(noHash), false);
  assert.strictEqual(planIsSaveable(null), false);
});

/** Griffin as `list_builtin_pipelines` returns it from `coperceive model list --json`. */
function griffinEntry(builtin: PipelineEntry["builtin"] | undefined = undefined): PipelineEntry {
  return {
    id: "griffin",
    name: "griffin",
    description: "Installed model plugin; runs coperceive model run on the cluster.",
    source: "builtin",
    spec: { ...spec, name: "griffin", arguments: [] },
    registration_path: null,
    plugin_manifest: "/opt/coperceive/plugins/griffin/coperceive_griffin_model/model.yaml",
    builtin:
      builtin === undefined
        ? {
            name: "griffin",
            manifest: "/opt/coperceive/plugins/griffin/coperceive_griffin_model/model.yaml",
            error: null,
            modules: ["nvidia/cuda-11.8"],
            models: {
              "vehicle-side": { gpu: true, options: ["ckpt_root", "det_json", "min_score", "nusc_root", "subset"] },
              "weights-from-option": { gpu: false, options: ["checkpoint"] },
            },
          }
        : builtin,
  };
}

test("a built-in model needs a checkpoint only when its manifest declares the option", () => {
  // Griffin vehicle-side pins its own weights: the backend refuses a checkpoint for it.
  assert.strictEqual(pipelineNeedsCheckpoint(griffinEntry(), "vehicle-side"), false);
  assert.strictEqual(pipelineNeedsCheckpoint(griffinEntry(), "weights-from-option"), true);
  // A model the listing does not know, or a cluster too old to list models: no field;
  // the cluster's own message says why when the plan is checked.
  assert.strictEqual(pipelineNeedsCheckpoint(griffinEntry(), "nope"), false);
  assert.strictEqual(pipelineNeedsCheckpoint(griffinEntry(null), "vehicle-side"), false);
  assert.strictEqual(pipelineNeedsSplit(griffinEntry()), true);
  // A custom pipeline follows its own argument roles.
  const custom: PipelineEntry = { ...griffinEntry(null), source: "custom", spec };
  assert.strictEqual(pipelineNeedsCheckpoint(custom, ""), true);
  assert.strictEqual(pipelineNeedsSplit(custom), true);
  const bare: PipelineEntry = { ...custom, spec: { ...spec, arguments: [] } };
  assert.strictEqual(pipelineNeedsCheckpoint(bare, ""), false);
  assert.strictEqual(pipelineNeedsSplit(bare), false);
  assert.strictEqual(pipelineNeedsCheckpoint(null, ""), false);
});

test("a built-in plan requires the model and raw root but not a media root", () => {
  const base = { model: "vehicle-side", rawRoot: "/data/griffin-release", mediaRoot: "" };
  // An empty media root is a valid plan: the CLI's --media-root is optional, and the job
  // script then contains no --media-root at all.
  assert.strictEqual(builtinPlanInputProblem(base), null);
  assert.strictEqual(builtinPlanInputProblem({ ...base, mediaRoot: "/data/griffin-release" }), null);
  // The model key and the raw root are still required.
  const message = "Choose the model key and raw root for this built-in model.";
  assert.strictEqual(builtinPlanInputProblem({ ...base, model: "  " }), message);
  assert.strictEqual(builtinPlanInputProblem({ ...base, rawRoot: " " }), message);
});

test("a model with no raw inputs allows an empty raw root, and Griffin still requires one", () => {
  const message = "Choose the model key and raw root for this built-in model.";
  const griffin = griffinEntry();
  griffin.builtin!.models["vehicle-side"].raw_inputs = true;
  assert.strictEqual(pipelineNeedsRawRoot(griffin, "vehicle-side"), true);
  assert.strictEqual(
    builtinPlanInputProblem({
      model: "vehicle-side",
      rawRoot: "",
      mediaRoot: "",
      needsRawInputs: pipelineNeedsRawRoot(griffin, "vehicle-side"),
    }),
    message,
  );
  // An older listing omits the field: the raw root stays required.
  assert.strictEqual(pipelineNeedsRawRoot(griffinEntry(), "vehicle-side"), true);

  const cobevt = griffinEntry();
  cobevt.builtin!.models["cobevt-lidar-v2xset"] = {
    gpu: true,
    options: ["checkpoint"],
    raw_inputs: false,
  };
  assert.strictEqual(pipelineNeedsRawRoot(cobevt, "cobevt-lidar-v2xset"), false);
  assert.strictEqual(
    builtinPlanInputProblem({
      model: "cobevt-lidar-v2xset",
      rawRoot: " ",
      mediaRoot: "",
      needsRawInputs: pipelineNeedsRawRoot(cobevt, "cobevt-lidar-v2xset"),
    }),
    null,
  );
  // The model key is still required.
  assert.notStrictEqual(
    builtinPlanInputProblem({
      model: " ",
      rawRoot: "",
      mediaRoot: "",
      needsRawInputs: false,
    }),
    null,
  );
});

test("a built-in model needs a checkpoint root only when its manifest declares ckpt_root", () => {
  // Griffin's released checkpoints root is a required parameter with no default.
  assert.strictEqual(pipelineNeedsCkptRoot(griffinEntry(), "vehicle-side"), true);
  assert.strictEqual(pipelineNeedsCkptRoot(griffinEntry(), "weights-from-option"), false);
  assert.strictEqual(pipelineNeedsCkptRoot(griffinEntry(), "nope"), false);
  assert.strictEqual(pipelineNeedsCkptRoot(griffinEntry(null), "vehicle-side"), false);
  assert.strictEqual(pipelineNeedsCkptRoot({ ...griffinEntry(), source: "custom" }, "vehicle-side"), false);
});

test("the New evaluation panel shows the declared checkpoint root next to the raw root", () => {
  const source = readFileSync(new URL("./NewEvaluation.tsx", import.meta.url), "utf8");
  assert.ok(source.includes("pipelineNeedsCkptRoot(selectedPipeline, model)"), "ckpt_root rule");
  assert.ok(source.includes('id="plan-ckpt-root"'), "ckpt_root field");
  assert.ok(source.includes('ckpt_root: selectedPipeline.source === "builtin" && needsCkptRoot'), "request field");
  assert.ok(source.includes("pipelineNeedsRawRoot(selectedPipeline, model)"), "raw root rule");
  assert.ok(source.includes("needsRawInputs: needsRawRoot"), "raw root reaches the check");
});

test("the AP protocol choice defaults to OpenCOOD-style and records legacy when chosen", () => {
  assert.strictEqual(DEFAULT_EVAL_PROTOCOL, "default");
  assert.deepStrictEqual(
    EVAL_PROTOCOL_CHOICES.map((choice) => choice.value),
    ["default", "legacy"],
  );
  assert.strictEqual(EVAL_PROTOCOL_CHOICES[0]?.label, "Default");
  assert.strictEqual(
    EVAL_PROTOCOL_CHOICES[0]?.description,
    "OpenCOOD-style: per-frame ranking, ground truth from every connected agent within 70 m",
  );
  assert.strictEqual(EVAL_PROTOCOL_CHOICES[1]?.label, "Legacy (v0.1.1)");
  assert.strictEqual(EVAL_PROTOCOL_CHOICES[1]?.description, "for numbers comparable to v0.1.1");
  assert.ok(evalProtocolLabel("legacy").includes("for numbers comparable to v0.1.1"));
  // The form sends the choice; it starts on the default.
  const source = readFileSync(new URL("./NewEvaluation.tsx", import.meta.url), "utf8");
  assert.ok(source.includes("plan-eval-protocol"), "protocol radios");
  assert.ok(source.includes("eval_protocol: evalProtocol"), "request field");
  assert.ok(source.includes("useState<EvalProtocol>(DEFAULT_EVAL_PROTOCOL)"), "default preselected");
});

test("the evaluated-run protocol label matches eval report and eval compare", () => {
  assert.strictEqual(evalProtocolConfigLabel(null), "legacy (pooled, ego-only)");
  assert.strictEqual(
    evalProtocolConfigLabel({ ranking_scope: "pooled", gt_scope: "ego_only" }),
    "legacy (pooled, ego-only)",
  );
  assert.strictEqual(
    evalProtocolConfigLabel({
      ranking_scope: "per_frame",
      gt_scope: "connected_union",
      comm_range_m: 70,
    }),
    "ranking per_frame, ground truth connected_union (70 m)",
  );
  assert.strictEqual(
    sameEvalProtocol(
      { ranking_scope: "per_frame", gt_scope: "connected_union", comm_range_m: 70 },
      { ranking_scope: "per_frame", gt_scope: "connected_union", comm_range_m: 50 },
    ),
    false,
  );
  assert.strictEqual(
    sameEvalProtocol(
      { ranking_scope: "pooled", gt_scope: "ego_only" },
      { ranking_scope: "pooled", gt_scope: "ego_only", comm_range_m: 70 },
    ),
    true,
  );
});

test("a Griffin plan with no checkpoint and a fingerprinted scenes folder can be saved", () => {
  const griffin = plan({
    checkpoint: { path: "", sha256: null, kind: "", real_path: null },
    split: {
      path: "/data/griffin/scenes",
      sha256: null,
      kind: "folder",
      real_path: "/data/griffin/scenes",
      listing_sha256: "1f".repeat(32),
      file_count: 12,
    },
  });
  assert.strictEqual(planIsSaveable(griffin), true);
  // An empty folder, or one without a listing, has no fingerprint.
  const empty = plan({ ...griffin, split: { ...griffin.split, file_count: 0 } });
  assert.strictEqual(planIsSaveable(empty), false);
  const unlisted = plan({ ...griffin, split: { ...griffin.split, listing_sha256: null } });
  assert.strictEqual(planIsSaveable(unlisted), false);
  // A checkpoint that was given must still have its sha256.
  const unhashed = plan({ ...griffin, checkpoint: { path: "/x.pt", sha256: null, kind: "file", real_path: "/x.pt" } });
  assert.strictEqual(planIsSaveable(unhashed), false);
});

test("a folder's fingerprint is shown as its listing, not as a missing sha256", () => {
  assert.strictEqual(
    artifactFingerprintText({
      path: "/data/griffin/scenes",
      sha256: null,
      kind: "folder",
      real_path: "/data/griffin/scenes",
      listing_sha256: "ab".repeat(32),
      file_count: 12,
    }),
    `listing sha256 ${"ab".repeat(32)} (12 files)`,
  );
  assert.strictEqual(
    artifactFingerprintText({ path: "/x.pt", sha256: "cd".repeat(32), kind: "file", real_path: "/x.pt" }),
    `sha256 ${"cd".repeat(32)}`,
  );
  assert.strictEqual(
    artifactFingerprintText({ path: "/x", sha256: null, kind: "file", real_path: "/x" }),
    "not recorded",
  );
});

test("the New evaluation panel asks the plan helpers, not a built-in rule, for the checkpoint", () => {
  const source = readFileSync(new URL("./NewEvaluation.tsx", import.meta.url), "utf8");
  assert.ok(source.includes("pipelineNeedsCheckpoint(selectedPipeline, model)"), "checkpoint rule");
  assert.ok(source.includes("pipelineNeedsSplit(selectedPipeline)"), "split rule");
  assert.ok(!source.includes('Split sha256'), "a folder split is shown by its listing");
  assert.ok(source.includes("artifactFingerprintText(plan.split)"), "split fingerprint text");
});

test("summaries are plain language and mention the resolved environment", () => {
  assert.ok(environmentSummary(plan().environment).includes("python/3.12"));
  assert.strictEqual(environmentKindLabel("container"), "Container (Apptainer/Singularity)");
  const entry = { profile: emptyProfile("Cluster B") } as unknown as Parameters<typeof planSummary>[1];
  assert.ok(planSummary(plan(), entry).includes("Cluster B"));
  assert.ok(planSummary(plan(), entry).includes("1/1 checks passed"));
  assert.ok(planSummary(null, entry).includes("No plan"));
});

test("the run-plan source has no submission call and no site-specific values", () => {
  const source = readFileSync(new URL("./runPlan.ts", import.meta.url), "utf8").toLowerCase();
  for (const forbidden of ["sbatch", "scancel", "university", "utwente", "hpc-node", '"ps"']) {
    assert.ok(!source.includes(forbidden), `runPlan.ts must not contain ${forbidden}`);
  }
});

test("job state label shows the pending queue reason plainly", () => {
  assert.strictEqual(
    jobStateLabel({
      plan_id: "plan-1",
      job_id: "424242",
      state: "pending",
      queue_reason: "Resources",
    }),
    "PENDING — waiting in the queue: Resources",
  );
  assert.strictEqual(
    jobStateLabel({ plan_id: "plan-1", job_id: "1", state: "running", queue_reason: null }),
    "RUNNING",
  );
});

test("the job watch label includes queue reason, elapsed and time left", () => {
  const base: JobWatchStatus = {
    job_id: "424242",
    plan_id: "plan-1",
    job_name: "coperceive-run-plan-1",
    state: "pending",
    queue_reason: "Resources",
    elapsed: null,
    time_left: null,
    node: null,
  };
  assert.strictEqual(jobWatchLabel(base), "PENDING — waiting in the queue: Resources");
  assert.strictEqual(
    jobWatchLabel({ ...base, state: "running", queue_reason: null, elapsed: "00:12:00", time_left: "03:48:00" }),
    "RUNNING · 00:12:00 elapsed · 03:48:00 left",
  );
  assert.strictEqual(
    jobWatchLabel({ ...base, state: "completed", queue_reason: null, elapsed: "04:00:00", time_left: null }),
    "COMPLETED · 04:00:00 elapsed",
  );
});

test("polling only happens while a job can still change", () => {
  assert.strictEqual(shouldPollJob(null), false);
  assert.strictEqual(isJobActive("pending"), true);
  assert.strictEqual(isJobActive("completed"), false);
  assert.strictEqual(shouldPollJob("pending"), true);
  assert.strictEqual(shouldPollJob("running"), true);
  for (const terminal of ["completed", "failed", "cancelled", "unknown"]) {
    assert.strictEqual(shouldPollJob(terminal), false);
  }
});

test("live progress reads plainly and omits counters the pipeline did not send", () => {
  assert.strictEqual(progressLabel(null), "");
  assert.strictEqual(
    progressLabel({ scenes_done: 2, scenes_total: 5, frames_done: 300, frames_total: 745 }),
    "scenes 2/5 · frames 300/745",
  );
  assert.strictEqual(
    progressLabel({ scenes_done: null, scenes_total: null, frames_done: 3, frames_total: null }),
    "frames 3/?",
  );
});

test("the metrics report table shows model, metrics and outputs", () => {
  const rows = metricsRows({
    run: { model: "bev-fusion", checkpoint: "ckpt.pt", scenes: ["Town03-000"] },
    outputs: [{ agent_id: "vehicle", source: "bev-fusion", boxes: 1490, scenes: 1 }],
    metrics: { latency_ms_per_frame: 41.2, notes: "fast" },
    notes: "pipeline note",
    raw: "{}",
  });
  assert.ok(rows.some((row) => row.label === "model" && row.value === "bev-fusion"));
  assert.ok(rows.some((row) => row.label === "checkpoint" && row.value === "ckpt.pt"));
  assert.ok(rows.some((row) => row.label === "latency_ms_per_frame" && row.value === "41.2"));
  assert.ok(rows.some((row) => row.label === "vehicle/bev-fusion" && row.value === "1490 boxes"));
});

test("a failed run has one concrete next action", () => {
  const action = failureNextAction();
  assert.ok(action.includes("stderr"));
  assert.ok(action.includes("submit a new run"));
});

test("history rows show state, timing and whether re-run is possible", () => {
  const entry = {
    job_id: "424242",
    plan_id: "plan-1",
    state: "completed",
    active: false,
    elapsed: "01:02:03",
    time_limit: "04:00:00",
    partition: "main-cpu",
    started: "2026-10-28T10:00:00",
    ended: "2026-10-28T11:02:03",
    plan_available: true,
  };
  assert.strictEqual(historyStateLabel(entry), "COMPLETED");
  assert.strictEqual(historyTiming(entry), "01:02:03 elapsed · 04:00:00 limit");
  assert.strictEqual(historyCanRerun(entry), true);
  const running = { ...entry, state: "running", active: true, plan_available: false };
  assert.strictEqual(historyStateLabel(running), "RUNNING");
  assert.strictEqual(historyCanRerun(running), false);
  assert.strictEqual(historyTiming({ ...entry, elapsed: null, time_limit: null }), "—");
});

test("slurmSeconds reads sacct durations and refuses anything else", () => {
  assert.strictEqual(slurmSeconds("01:12:40"), 4360);
  assert.strictEqual(slurmSeconds("1-02:00:00"), 93600);
  assert.strictEqual(slurmSeconds("05:30"), 330);
  assert.strictEqual(slurmSeconds(null), null);
  assert.strictEqual(slurmSeconds("UNLIMITED"), null);
  assert.strictEqual(slurmSeconds(""), null);
});

test("repro card preview names what is included and what is missing", () => {
  const lines = reproPreviewLines({
    dataset: { id: null, version: null },
    split: { name: null, scene_ids: ["scene-a"] },
    pipeline: { name: "synthetic" },
    checkpoint: { file_name: "model.pt", sha256: null },
    code: { git_commit: null },
    coperceive: { version: "0.1.0" },
    environment: { python: null },
    metrics: null,
    unknown: ["checkpoint.sha256", "environment.python", "metrics", "metrics.scene-a"],
  } as Record<string, unknown>);
  assert.ok(lines.included.some((line) => line.includes("synthetic")));
  assert.ok(lines.included.some((line) => line.includes("has not scored")));
  assert.ok(lines.missing.some((line) => line.includes("checkpoint hash")));
  assert.ok(lines.missing.some((line) => line.includes("Python version")));
  assert.ok(lines.missing.some((line) => line.includes("has not scored")));
  assert.ok(lines.missing.some((line) => line.includes("metrics.scene-a")));
});

test("a built-in run's walltime defaults to the CLI's estimate, never to four hours", () => {
  assert.strictEqual(defaultWalltime({ time_estimate: "01:00:00" }), "01:00:00");
  assert.strictEqual(defaultWalltime({ time_estimate: null }), "");
  assert.strictEqual(defaultWalltime(null), "");
  assert.match(walltimeProblem("", { time_estimate: null }) ?? "", /Enter the walltime/);
  assert.strictEqual(walltimeProblem("01:00:00", { time_estimate: "01:00:00" }), null);
});

test("a walltime over the partition's discovered limit is refused before the preview", () => {
  const plan = { time_estimate: "01:00:00", partition_max_time: "1-00:00:00" };
  assert.strictEqual(walltimeProblem("1-00:00:00", plan), null);
  assert.match(walltimeProblem("1-00:00:01", plan) ?? "", /longer than the partition's time limit 1-00:00:00/);
  assert.match(walltimeProblem("48:00:00", plan) ?? "", /1-00:00:00/);
  // No discovered limit: nothing to refuse.
  assert.strictEqual(walltimeProblem("7-00:00:00", { time_estimate: "01:00:00", partition_max_time: null }), null);
});

const record = (over: Partial<EvaluationRecord> = {}): EvaluationRecord => ({
  status: "ok",
  error: null,
  protocol: "default (ranking per_frame, GT connected_union)",
  config_sha256: "ab12",
  created_utc: "2026-11-23T12:00:00Z",
  command: "eval run",
  evaluated: ["coop", "coop2"],
  skipped: [],
  errors: [],
  ...over,
});

test("a scored job's result: exit 5 is the model finished and scoring failed, never a model failure", () => {
  // Not a scored plan, or still running: nothing about scoring.
  assert.equal(scoringSummary({ evaluate: false, state: "completed", exitCode: 0, record: record() }), null);
  assert.equal(scoringSummary({ evaluate: true, state: "running", record: undefined }), null);
  assert.equal(scoringSummary({ evaluate: true, state: "completed", exitCode: 0, record: undefined })?.headline,
    "Reading the scoring record…");

  const ok = scoringSummary({ evaluate: true, state: "completed", exitCode: 0, record: record() });
  assert.equal(ok?.headline, "Scored.");
  assert.equal(ok?.failed, false);
  // The protocol always comes with the result; no AP number is shown without it.
  assert.ok(ok?.detail.includes("AP protocol: default (ranking per_frame, GT connected_union)"), ok?.detail.join("\n"));
  assert.ok(ok?.detail.includes("2 scenes scored"));

  const failed = scoringSummary({
    evaluate: true,
    state: "failed",
    exitCode: 5,
    record: record({ status: "failed", error: "KeyError: 'ap'", evaluated: [] }),
  });
  assert.equal(failed?.headline, "Model finished, scoring failed.");
  assert.equal(failed?.modelFinished, true);
  assert.equal(failed?.detail[0], "KeyError: 'ap'");
  assert.ok(failed?.detail.some((line) => line.startsWith("AP protocol: ")));

  // Exit 5 without a record: the run left no run.json; the stderr tail explains.
  const norecord = scoringSummary({ evaluate: true, state: "failed", exitCode: 5, record: null });
  assert.equal(norecord?.headline, "Model finished, scoring failed.");
  assert.match(norecord?.detail[0] ?? "", /no run\.json/);

  // Scene errors under exit 5 list the failing scenes.
  const errors = scoringSummary({
    evaluate: true,
    state: "failed",
    exitCode: 5,
    record: record({ status: "errors", errors: ["coop2: ValueError"], evaluated: ["coop"] }),
  });
  assert.deepEqual(errors?.detail.slice(0, 2), ["Scoring status: errors.", "coop2: ValueError"]);

  // A model failure keeps its own panel: a partial run (exit 3) scored on its finished scenes,
  // a timeout or a model failure with no record is "not scored", not "scoring failed".
  const partial = scoringSummary({ evaluate: true, state: "failed", exitCode: 3, record: record({ skipped: ["coop2"] }) });
  assert.equal(partial?.modelFinished, false);
  assert.equal(partial?.headline, "The model's finished scenes were scored.");
  assert.ok(partial?.detail.includes("2 scenes scored, 1 skipped"));
  for (const [state, exitCode] of [["timeout", null], ["failed", 1], ["out_of_memory", null]] as const) {
    const summary = scoringSummary({ evaluate: true, state, exitCode, record: null });
    assert.equal(summary?.modelFinished, false, state);
    assert.equal(summary?.failed, false, state);
    assert.match(summary?.headline ?? "", /^Not scored: the model did not finish/);
  }
  // A completed job that left no record is reported, not silently passed.
  assert.equal(scoringSummary({ evaluate: true, state: "completed", exitCode: 0, record: null })?.failed, true);
});
