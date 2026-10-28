// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import {
  collectValues,
  emptyEnvironmentOverride,
  environmentKindLabel,
  environmentSummary,
  formatArgv,
  initialArgumentForm,
  planIsSaveable,
  planSummary,
  validateArgumentForm,
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
