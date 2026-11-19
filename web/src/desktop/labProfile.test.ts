// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { emptyProfile } from "./hpc.ts";
import { parsePipelineRegistrations, plain, reviewFields, secretProblem } from "./labProfile.ts";
import type { PipelineSpec } from "./runPlan.ts";

const LABELS = [
  "Server name",
  "SSH alias",
  "Host name",
  "User name",
  "Data folders",
  "Code folders",
  "Scratch folder",
  "Results folder",
  "Scheduler",
  "Partition",
  "Account",
  "Quality of service",
  "GPU request",
  "Time limit",
  "Memory",
  "CPUs per task",
  "Environment",
  "Modules",
  "Python interpreter",
  "Container image",
  "Coperceive command",
  "Wheels folder",
  "Compute nodes can download packages",
  "Notes",
];

function fullProfile() {
  const profile = emptyProfile("Lab cluster");
  profile.ssh_alias = null;
  profile.data_roots = ["/data/scenes"];
  profile.code_roots = ["{{home}}/code"];
  profile.scratch_template = "{{scratch}}";
  profile.results_root = "/data/results";
  profile.scheduler = "slurm";
  profile.defaults = {
    partition: "shared",
    account: null,
    qos: null,
    gres: null,
    time_limit: "04:00:00",
    memory: "8G",
    cpus_per_task: 2,
  };
  profile.environment.kind = "modules";
  profile.environment.modules = ["python/3.12"];
  profile.environment.coperceive_command = ["{{home}}/app/.venv/bin/python", "-m", "coperceive"];
  profile.notes = "Shared by the lab.";
  return profile;
}

const pipeline: PipelineSpec = {
  name: "lab-eval",
  script: "{{home}}/pipelines/run.py",
  interpreter: ["python3"],
  environment: fullProfile().environment,
  arguments: [
    {
      name: "config",
      kind: "path",
      help: "shared config",
      required: false,
      default: null,
      choices: [],
      multiple: false,
      role: "config",
    },
  ],
  output_contract: "coperceive-pipeline-1",
  description: "Shared evaluation script.",
};

test("the review names every profile field in plain language", () => {
  const rows = reviewFields(fullProfile(), [pipeline]);
  const labels = rows.map((row) => row.label);
  for (const label of LABELS) {
    assert.ok(labels.includes(label), `missing ${label}`);
  }
  assert.ok(labels.includes("Pipeline name"));
  assert.ok(labels.includes("Pipeline script"));
  assert.ok(labels.includes("Pipeline command"));
  assert.ok(labels.includes("Pipeline arguments"));
  assert.ok(labels.includes("Pipeline environment"));
  assert.ok(labels.includes("Pipeline modules"));
  assert.ok(labels.includes("Pipeline container image"));
  pipeline.environment.kind = "container";
  pipeline.environment.modules = ["python/3.12"];
  pipeline.environment.container_image = "registry.example/lab:1";
  const withEnv = new Map(reviewFields(fullProfile(), [pipeline]).map((row) => [row.label, row.value]));
  assert.equal(withEnv.get("Pipeline modules"), "python/3.12");
  assert.equal(withEnv.get("Pipeline container image"), "registry.example/lab:1");
  const byLabel = new Map(rows.map((row) => [row.label, row.value]));
  assert.equal(byLabel.get("SSH alias"), "Not set");
  assert.equal(byLabel.get("Scratch folder"), "your scratch folder");
  assert.equal(byLabel.get("Code folders"), "your home directory on the cluster/code");
  assert.match(byLabel.get("Coperceive command") ?? "", /your home directory on the cluster/);
  assert.equal(byLabel.get("Pipeline arguments"), "config (path): shared config");
  assert.equal(byLabel.get("Compute nodes can download packages"), "No");
  assert.equal(plain("{{user}}"), "your user name on the cluster");
});

test("a secret-shaped lab file is refused and a path containing tokenizer is not", () => {
  assert.match(secretProblem('{"password": "x"}') ?? "", /password|token|key/i);
  assert.match(secretProblem("token=abcd") ?? "", /password or token/);
  assert.equal(secretProblem('{"notes": "see /data/tokenizer.json"}'), null);
  assert.throws(() => parsePipelineRegistrations("{"), /JSON/);
});

test("the lab profile screen asks for an SSH alias and turns off autocorrect", () => {
  const servers = readFileSync(new URL("./HpcServers.tsx", import.meta.url), "utf8");
  assert.match(servers, /Share with your lab/);
  assert.match(servers, /Import lab profile/);
  const panel = readFileSync(new URL("./LabShare.tsx", import.meta.url), "utf8");
  for (const id of ["lab-ssh-alias", "lab-user", "lab-home", "lab-scratch"]) {
    const tag = panel.match(new RegExp(`<input[^>]*id=["']${id}["'][^>]*>`, "s"));
    assert.ok(tag, id);
    assert.match(tag[0], /autoCapitalize="off"/);
    assert.match(tag[0], /autoCorrect="off"/);
    assert.match(tag[0], /spellCheck=\{false\}/);
    assert.match(tag[0], /autoComplete="off"/);
  }
  assert.match(panel, /reviewFields/);
});
