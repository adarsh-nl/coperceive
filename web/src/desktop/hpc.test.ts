// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import {
  applyDiscovery,
  emptyEntry,
  environmentLabel,
  formatBytes,
  gpuTypesFor,
  missingExplanations,
  groupPartitions,
  partitionNames,
  profileSummary,
  reportSummary,
  schedulerLabel,
  validateConnectionShape,
  validateDraft,
  type DetectionReport,
  type DiscoveredPartition,
} from "./hpc.ts";

const partitions: DiscoveredPartition[] = [
  {
    name: "gpu-a100",
    nodes: 8,
    state: "up",
    max_time: "2-00:00:00",
    gres: "gpu:a100:4",
    gpu_types: ["a100"],
  },
  {
    name: "main-cpu",
    nodes: 32,
    state: "up",
    max_time: "4:00:00",
    gres: null,
    gpu_types: [],
  },
];

function report(overrides: Partial<DetectionReport> = {}): DetectionReport {
  return {
    reachable: true,
    signed_in: true,
    scheduler: "slurm",
    scheduler_note: null,
    partitions,
    gpu_types: ["a100"],
    environment: {
      kind: "modules",
      modules: ["python/3.12"],
      interpreter: "/apps/python/3.12/bin/python",
      container_image: null,
      coperceive_command: ["/apps/coperceive/bin/coperceive"],
      wheelhouse: null,
      compute_has_internet: false,
    },
    discovered: {
      partitions,
      gpu_types: ["a100"],
      environment: {
        module_system: true,
        conda: null,
        virtualenv: null,
        python: "/usr/bin/python3",
        container_runtime: null,
        coperceive_path: "/apps/coperceive/bin/coperceive",
        coperceive_version: "0.4.0",
        scratch_candidates: ["/scratch/alice"],
      },
      checked_unix: 1,
    },
    scratch: {
      path: "/scratch/alice",
      writable: true,
      free_bytes: 500_000 * 1024,
      quota: null,
      detail: "Scratch folder is writable, with about 0.5 GB free.",
    },
    checks: [],
    ssh: {
      alias: "cluster-a",
      hostname: "cluster-a.example.org",
      user: "alice",
      port: 22,
      proxy_jump: null,
      proxy_command: null,
    },
    total_ms: 1200,
    ...overrides,
  };
}

test("empty profile carries no site default and no secrets", () => {
  const entry = emptyEntry("New server");
  const profile = entry.profile;
  assert.strictEqual(profile.scheduler, "none");
  assert.strictEqual(profile.defaults.partition, null);
  assert.strictEqual(profile.defaults.gres, null);
  assert.strictEqual(profile.environment.interpreter, null);
  assert.strictEqual(profile.environment.compute_has_internet, false);
  assert.strictEqual(profile.scratch_template, "");
  assert.strictEqual(profile.results_root, "");
  const serialised = JSON.stringify(entry).toLowerCase();
  for (const forbidden of ["password", "private_key", "token", "secret", "\"ps\""]) {
    assert.ok(!serialised.includes(forbidden), `must not contain ${forbidden}`);
  }
});

test("connection shape and save validation explain missing pieces", () => {
  const empty = emptyEntry("");
  const shapeErrors = validateConnectionShape(empty);
  assert.ok(shapeErrors.some((error) => error.includes("name")));
  assert.ok(shapeErrors.some((error) => error.includes("host")));
  assert.deepStrictEqual(validateConnectionShape(emptyEntry("Cluster")), [
    "Choose a host from ~/.ssh/config, or type a host name.",
  ]);

  const reachableShape = emptyEntry("Cluster");
  reachableShape.profile.ssh_alias = "cluster-a";
  assert.deepStrictEqual(validateConnectionShape(reachableShape), []);
  // Reaching the cluster does not need folders, but saving does.
  assert.ok(validateDraft(reachableShape).some((error) => error.includes("data folder")));
  assert.ok(validateDraft(reachableShape).some((error) => error.includes("scratch")));
  assert.ok(validateDraft(reachableShape).some((error) => error.includes("results folder")));

  const escaped = emptyEntry("Cluster");
  escaped.profile.ssh_alias = "cluster-a";
  escaped.profile.data_roots = ["/data/../../etc"];
  escaped.profile.scratch_template = "/scratch/alice";
  escaped.profile.results_root = "/data/results";
  assert.ok(validateDraft(escaped).some((error) => error.includes("..")));

  const resultsInScratch = emptyEntry("Cluster");
  resultsInScratch.profile.ssh_alias = "cluster-a";
  resultsInScratch.profile.data_roots = ["/data/scenes"];
  resultsInScratch.profile.scratch_template = "/scratch/$USER";
  resultsInScratch.profile.results_root = "/scratch/alice/results";
  assert.ok(validateDraft(resultsInScratch).some((error) => error.includes("shared storage")));

  const noPartitionOnSlurm = emptyEntry("Cluster");
  noPartitionOnSlurm.profile.ssh_alias = "cluster-a";
  noPartitionOnSlurm.profile.data_roots = ["/data/scenes"];
  noPartitionOnSlurm.profile.scratch_template = "/scratch/$USER";
  noPartitionOnSlurm.profile.scheduler = "none";
  noPartitionOnSlurm.profile.defaults.partition = "gpu-a100";
  assert.ok(validateDraft(noPartitionOnSlurm).some((error) => error.includes("scheduler")));

  const both = emptyEntry("Cluster");
  both.profile.ssh_alias = "cluster-a";
  both.profile.host = "typed.example.org";
  assert.ok(validateConnectionShape(both).some((error) => error.includes("not both")));
});

test("applyDiscovery fills blanks from detection without inventing site defaults", () => {
  const entry = emptyEntry("Cluster A");
  entry.profile.ssh_alias = "cluster-a";
  entry.profile.data_roots = ["/data/scenes"];
  entry.profile.scratch_template = "/scratch/$USER";
  const applied = applyDiscovery(entry, report());
  assert.strictEqual(applied.profile.scheduler, "slurm");
  assert.strictEqual(applied.profile.defaults.partition, "gpu-a100");
  assert.strictEqual(applied.profile.defaults.gres, "gpu:a100:1");
  assert.strictEqual(applied.profile.defaults.time_limit, "2-00:00:00");
  assert.strictEqual(applied.profile.defaults.memory, null);
  assert.strictEqual(applied.profile.defaults.cpus_per_task, null);
  assert.strictEqual(applied.profile.scratch_template, "/scratch/$USER");
  assert.strictEqual(applied.profile.environment.kind, "modules");
  assert.ok(applied.discovered);
  assert.ok(applied.ssh);
});

test("applyDiscovery keeps what the user chose", () => {
  const entry = emptyEntry("Cluster A");
  entry.profile.ssh_alias = "cluster-a";
  entry.profile.defaults.partition = "main-cpu";
  entry.profile.defaults.time_limit = "01:00:00";
  entry.profile.data_roots = ["/data/mine"];
  entry.profile.scratch_template = "/scratch/me";
  entry.profile.environment.kind = "venv";
  entry.profile.environment.interpreter = "/home/alice/venvs/coperceive/bin/python";
  const applied = applyDiscovery(entry, report());
  assert.strictEqual(applied.profile.data_roots[0], "/data/mine");
  assert.strictEqual(applied.profile.defaults.partition, "main-cpu");
  assert.strictEqual(applied.profile.defaults.time_limit, "01:00:00");
  assert.strictEqual(applied.profile.scratch_template, "/scratch/me");
  assert.strictEqual(applied.profile.environment.kind, "venv");
  assert.strictEqual(
    applied.profile.environment.interpreter,
    "/home/alice/venvs/coperceive/bin/python",
  );
});

test("partition and GPU choices come from discovery, not constants", () => {
  const entry = applyDiscovery(emptyEntry("Cluster A"), report());
  assert.deepStrictEqual(partitionNames(entry), ["gpu-a100", "main-cpu"]);
  assert.deepStrictEqual(gpuTypesFor(entry, "gpu-a100"), ["a100"]);
  assert.deepStrictEqual(gpuTypesFor(entry, "main-cpu"), []);
  assert.deepStrictEqual(gpuTypesFor(entry, null), ["a100"]);
});

test("summaries are truthful and explain missing things in plain words", () => {
  const base = emptyEntry("Cluster A");
  base.profile.ssh_alias = "cluster-a";
  const entry = applyDiscovery(base, report());
  assert.ok(profileSummary(entry).includes("ssh cluster-a"));
  assert.ok(profileSummary(entry).includes("Slurm"));
  assert.ok(!profileSummary(entry).toLowerCase().includes("university"));
  assert.ok(reportSummary(report()).includes("Slurm"));
  assert.ok(reportSummary(null).includes("Not tested"));

  const offline = report({ reachable: false, signed_in: false, partitions: [] });
  const notes = missingExplanations(offline);
  assert.ok(notes.some((note) => note.includes("VPN")));
  assert.ok(!notes.join(" ").toLowerCase().includes("university"));

  const lockedReport = report({ signed_in: false });
  assert.ok(missingExplanations(lockedReport).some((note) => note.includes("ssh-add")));

  const filesOnly = report({ scheduler: "none", partitions: [], scheduler_note: "No scheduler was found on this server." });
  assert.ok(missingExplanations(filesOnly).some((note) => note.includes("No scheduler")));

  assert.strictEqual(schedulerLabel("none"), "No scheduler (files only)");
  assert.strictEqual(environmentLabel("container"), "Container (Apptainer/Singularity)");
  assert.strictEqual(formatBytes(1024 ** 3), "1.0 GB");
  assert.strictEqual(formatBytes(null), "unknown");
});

test("the HPC tab source has no site-specific values or false connection claim", () => {
  const source = readFileSync(new URL("./hpc.ts", import.meta.url), "utf8").toLowerCase();
  for (const forbidden of ["university", "utwente", "hpc-node", "\"ps\"", "hpc-head"]) {
    assert.ok(!source.includes(forbidden), `hpc.ts must not contain ${forbidden}`);
  }
  assert.ok(!source.includes("cloud gpu partitions"));
});

test("partitions are grouped one chip per name with GPU types inside", () => {
  const groups = groupPartitions([
    { name: "main-cpu", nodes: 8, state: "up", max_time: "4:00:00", gres: "gpu:a100:2", gpu_types: ["a100"] },
    { name: "main-cpu", nodes: 4, state: "up", max_time: null, gres: "gpu:h100:1", gpu_types: ["h100"] },
    { name: "cpu", nodes: 32, state: "down", max_time: null, gres: null, gpu_types: [] },
  ]);
  assert.strictEqual(groups.length, 2);
  assert.deepStrictEqual(groups[0].gpuTypes, ["a100", "h100"]);
  assert.strictEqual(groups[0].nodes, 12);
  assert.strictEqual(groups[1].down, true);
  assert.deepStrictEqual(groups[1].gpuTypes, []);
});
