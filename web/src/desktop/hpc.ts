// SPDX-License-Identifier: Apache-2.0
/**
 * Remote HPC workspace types and pure helpers.
 *
 * These mirror the Rust types (`src-tauri/src/hpc.rs`,
 * `server_profiles.rs`, `detect.rs`) and the slice-0 JSON schema. Every server
 * value (host, roots, partition, account, environment) is user-chosen or
 * discovered read-only; this module never supplies a site default.
 */

export type SchedulerKind = "slurm" | "none";
export type EnvironmentKind = "none" | "modules" | "conda" | "venv" | "container";

export interface JobDefaults {
  partition: string | null;
  account: string | null;
  qos: string | null;
  gres: string | null;
  time_limit: string | null;
  memory: string | null;
  cpus_per_task: number | null;
}

export interface EnvironmentSetup {
  kind: EnvironmentKind;
  modules: string[];
  interpreter: string | null;
  container_image: string | null;
  coperceive_command: string[];
  wheelhouse: string | null;
  compute_has_internet: boolean;
}

export interface ServerProfile {
  name: string;
  ssh_alias: string | null;
  host: string | null;
  user: string | null;
  data_roots: string[];
  scratch_template: string | null;
  scheduler: SchedulerKind;
  defaults: JobDefaults;
  environment: EnvironmentSetup;
  notes: string | null;
}

export interface PartitionInfo {
  name: string;
  is_default: boolean;
  available: boolean;
  max_walltime: string | null;
  cpus: number | null;
  memory_mb: number | null;
  gres: string | null;
  gpu_types: string[];
}

export interface SshHostSettings {
  alias: string;
  hostname: string | null;
  user: string | null;
  port: number | null;
  proxy_jump: string | null;
  proxy_command: string | null;
}

export interface DiscoveredEnvironment {
  module_system: boolean;
  conda: string | null;
  virtualenv: string | null;
  python: string | null;
  container_runtime: string | null;
  coperceive_path: string | null;
  coperceive_version: string | null;
  scratch_candidates: string[];
}

export interface DiscoveredServer {
  partitions: PartitionInfo[];
  gpu_types: string[];
  environment: DiscoveredEnvironment;
  checked_unix: number;
}

export interface ServerEntry {
  id: string;
  schema_version: number;
  profile: ServerProfile;
  discovered: DiscoveredServer | null;
  ssh: SshHostSettings | null;
}

export interface CheckReport {
  name: string;
  label: string;
  ok: boolean;
  duration_ms: number;
  detail: string;
  hint: string | null;
}

export interface ScratchReport {
  path: string | null;
  writable: boolean;
  free_bytes: number | null;
  quota: string | null;
  detail: string;
}

export interface DetectionReport {
  reachable: boolean;
  signed_in: boolean;
  scheduler: SchedulerKind;
  scheduler_note: string | null;
  partitions: PartitionInfo[];
  gpu_types: string[];
  environment: EnvironmentSetup;
  discovered: DiscoveredServer;
  scratch: ScratchReport;
  checks: CheckReport[];
  ssh: SshHostSettings;
  total_ms: number;
}

export function emptyEnvironment(): EnvironmentSetup {
  return {
    kind: "none",
    modules: [],
    interpreter: null,
    container_image: null,
    coperceive_command: [],
    wheelhouse: null,
    compute_has_internet: false,
  };
}

export function emptyProfile(name = ""): ServerProfile {
  return {
    name,
    ssh_alias: null,
    host: null,
    user: null,
    data_roots: [],
    scratch_template: null,
    scheduler: "none",
    defaults: {
      partition: null,
      account: null,
      qos: null,
      gres: null,
      time_limit: null,
      memory: null,
      cpus_per_task: null,
    },
    environment: emptyEnvironment(),
    notes: null,
  };
}

export function emptyEntry(name = ""): ServerEntry {
  return {
    id: "",
    schema_version: 1,
    profile: emptyProfile(name),
    discovered: null,
    ssh: null,
  };
}

const ENVIRONMENT_KINDS: ReadonlySet<string> = new Set([
  "none",
  "modules",
  "conda",
  "venv",
  "container",
]);

/** Client-side validation with the same rules as Rust; plain-language errors. */
export function validateDraft(entry: ServerEntry): string[] {
  const errors: string[] = [];
  const profile = entry.profile;
  if (!profile.name.trim()) {
    errors.push("Give this server a name so you can recognise it later.");
  }
  if (!profile.ssh_alias?.trim() && !profile.host?.trim()) {
    errors.push("Choose a host from ~/.ssh/config, or type a host name.");
  }
  for (const root of profile.data_roots) {
    if (!root.trim()) errors.push("Remove the empty data folder, or type a folder path.");
    if (root.split("/").includes("..")) {
      errors.push("Data folders may not contain '..'. Paths must stay inside the folders you chose.");
    }
  }
  if (profile.scratch_template?.split("/").includes("..")) {
    errors.push("The scratch folder may not contain '..'.");
  }
  if (profile.scheduler === "none" && profile.defaults.partition) {
    errors.push("A server without a scheduler cannot submit to a partition. Clear it or choose Slurm.");
  }
  if (!ENVIRONMENT_KINDS.has(profile.environment.kind)) {
    errors.push("Choose how compute nodes should prepare the environment.");
  }
  if (profile.environment.kind === "container" && !profile.environment.container_image?.trim()) {
    errors.push("A container environment needs an image path (for example a .sif file).");
  }
  return errors;
}

/** The first data folder, or empty. */
export function primaryDataRoot(entry: ServerEntry): string {
  return entry.profile.data_roots[0]?.trim() ?? "";
}

export function partitionNames(entry: ServerEntry): string[] {
  return (entry.discovered?.partitions ?? []).map((partition) => partition.name);
}

export function gpuTypesFor(entry: ServerEntry, partitionName: string | null): string[] {
  const partitions = entry.discovered?.partitions ?? [];
  const chosen = partitionName ? partitions.find((p) => p.name === partitionName) : undefined;
  if (chosen) return chosen.gpu_types;
  const all = new Set<string>();
  for (const partition of partitions) {
    for (const type of partition.gpu_types) all.add(type);
  }
  return [...all].sort();
}

/**
 * Applies read-only discovery to a draft profile without overwriting anything
 * the user chose. Returns a new entry; the stored `discovered` snapshot is kept
 * so the partition/GPU choices survive a restart.
 */
export function applyDiscovery(entry: ServerEntry, report: DetectionReport): ServerEntry {
  const profile: ServerProfile = {
    ...entry.profile,
    defaults: { ...entry.profile.defaults },
    environment: { ...entry.profile.environment },
    data_roots: [...entry.profile.data_roots],
  };
  if (profile.scheduler === "none" || report.scheduler !== "none") {
    // Detection is the source of truth about whether this server has Slurm;
    // the user can still change it afterwards in the form.
    profile.scheduler = report.scheduler;
  }
  if (!profile.scratch_template?.trim() && report.scratch.path) {
    profile.scratch_template = report.scratch.path;
  }
  const partitions = report.partitions;
  const partitionDefault = partitions.find((p) => p.is_default) ?? partitions[0];
  if (profile.scheduler === "slurm") {
    if (!profile.defaults.partition?.trim() && partitionDefault) {
      profile.defaults.partition = partitionDefault.name;
    }
    const chosen =
      partitions.find((p) => p.name === profile.defaults.partition) ?? partitionDefault;
    if (chosen) {
      // Default to a single GPU of the detected type, not the whole node.
      const firstGpu = chosen.gpu_types[0];
      if (!profile.defaults.gres?.trim() && firstGpu) {
        profile.defaults.gres = firstGpu === "gpu" ? "gpu:1" : `gpu:${firstGpu}:1`;
      } else if (!profile.defaults.gres?.trim() && chosen.gres) {
        profile.defaults.gres = chosen.gres;
      }
      if (!profile.defaults.time_limit && chosen.max_walltime) {
        profile.defaults.time_limit = chosen.max_walltime;
      }
      if (!profile.defaults.memory && chosen.memory_mb) {
        profile.defaults.memory = `${Math.round(chosen.memory_mb / 1024)}G`;
      }
      if (profile.defaults.cpus_per_task === null && chosen.cpus) {
        profile.defaults.cpus_per_task = chosen.cpus;
      }
    }
  }
  if (profile.environment.kind === "none" && report.environment.kind !== "none") {
    profile.environment = { ...report.environment, modules: [...report.environment.modules] };
  } else if (!profile.environment.interpreter?.trim() && report.environment.interpreter) {
    profile.environment.interpreter = report.environment.interpreter;
  }
  if (!profile.environment.wheelhouse?.trim()) {
    profile.environment.wheelhouse = null;
  }
  return {
    ...entry,
    profile,
    discovered: report.discovered,
    ssh: report.ssh,
  };
}

export function schedulerLabel(scheduler: SchedulerKind): string {
  return scheduler === "slurm" ? "Slurm" : "No scheduler (files only)";
}

export function environmentLabel(kind: EnvironmentKind): string {
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

/** Short, plain summary for the server card. */
export function profileSummary(entry: ServerEntry): string {
  const profile = entry.profile;
  const target = profile.ssh_alias ? `ssh ${profile.ssh_alias}` : profile.host ?? "no host yet";
  const scheduler = profile.scheduler === "slurm" ? "Slurm" : "no scheduler";
  const partition = profile.defaults.partition ? ` · partition ${profile.defaults.partition}` : "";
  return `${target} · ${scheduler}${partition}`;
}

/** One-line result of the last connection test, for the server card. */
export function reportSummary(report: DetectionReport | null): string {
  if (!report) return "Not tested yet.";
  if (!report.reachable) return "Not reachable. Check the VPN and your network.";
  if (!report.signed_in) return "Reachable, but sign-in failed. Add your key to ssh-agent.";
  const parts = [report.scheduler === "slurm" ? "Slurm found" : "No scheduler found"];
  if (report.partitions.length > 0) parts.push(`${report.partitions.length} partition(s)`);
  if (report.scratch.writable) parts.push("scratch writable");
  if (report.environment.interpreter) parts.push("environment found");
  return `${parts.join(" · ")}. ${Math.round(report.total_ms / 100) / 10}s`;
}

/** Plain-language explanations for things detection could not find. */
export function missingExplanations(report: DetectionReport): string[] {
  const notes: string[] = [];
  if (!report.reachable) {
    notes.push("The cluster did not answer. Connect your VPN (for example eduVPN) and try again.");
  } else if (!report.signed_in) {
    notes.push(
      "Your SSH key was not accepted. Run `ssh-add` in Terminal, then test again. Coperceive never asks for a password.",
    );
  }
  if (report.signed_in && report.scheduler === "none") {
    notes.push(
      "No scheduler was found. You can still open files over SSH, but job submission is unavailable.",
    );
  }
  if (report.signed_in && report.scheduler === "slurm" && report.partitions.length === 0) {
    notes.push(
      "No partitions were reported. Type the partition you use in the resources section if you know it.",
    );
  }
  if (report.signed_in && !report.scratch.writable) {
    notes.push(
      "Coperceive could not write to the scratch folder. Choose the per-user scratch path your cluster gives you.",
    );
  }
  if (report.scheduler_note) notes.push(report.scheduler_note);
  return notes;
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes <= 0) return "unknown";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}
