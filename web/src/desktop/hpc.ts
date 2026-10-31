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
  /** Required by the shared contract; empty only while a draft is being filled. */
  scratch_template: string;
  /** Shared filesystem for run outputs; required, never scratch. */
  results_root: string;
  /** Extra folders a registered pipeline script may live in. */
  code_roots: string[];
  scheduler: SchedulerKind;
  defaults: JobDefaults;
  environment: EnvironmentSetup;
  notes: string | null;
}

export interface DiscoveredPartition {
  name: string;
  nodes: number | null;
  state: string | null;
  max_time: string | null;
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
  partitions: DiscoveredPartition[];
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


/** One folder described by `coperceive remote scan`; paths are relative to the root. */
export interface FolderInfo {
  path: string;
  depth: number;
  is_scene: boolean;
  scene_id: string | null;
  dataset: boolean;
  scene_count: number;
  has_manifest: boolean;
  has_complete: boolean;
  subfolder_count: number;
  file_count: number;
}

/** One detected coperceive scene. */
export interface SceneInfo {
  path: string;
  scene_id: string;
  folder_name: string;
}

/** A non-scene folder with at least one scene below it. */
export interface DatasetInfo {
  path: string;
  scene_count: number;
}

/** A directory entry the scan did not follow, and why. */
export interface SkippedInfo {
  path: string;
  reason: string;
}

/** The JSON document printed by `coperceive remote scan`. */
export interface ScanDocument {
  status: string;
  root: string;
  path: string;
  relative_path: string;
  depth: number;
  timeout_s: number;
  max_entries: number;
  max_total: number;
  elapsed_ms: number;
  truncated: boolean;
  truncated_reason: string | null;
  folder_count: number;
  scene_count: number;
  dataset_count: number;
  folders: FolderInfo[];
  scenes: SceneInfo[];
  datasets: DatasetInfo[];
  skipped: SkippedInfo[];
}

export interface DetectionReport {
  reachable: boolean;
  signed_in: boolean;
  scheduler: SchedulerKind;
  scheduler_note: string | null;
  partitions: DiscoveredPartition[];
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
    // Matches the Rust default and the CLI name. Discovery replaces it with a
    // discovered path or `<interpreter> -m coperceive` when one is available.
    coperceive_command: ["coperceive"],
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
    scratch_template: "",
    results_root: "",
    code_roots: [],
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

/** Shape needed to reach the cluster and test the connection (no folders yet). */
export function validateConnectionShape(entry: ServerEntry): string[] {
  const errors: string[] = [];
  const profile = entry.profile;
  if (!profile.name.trim()) {
    errors.push("Give this server a name so you can recognise it later.");
  }
  if (!profile.ssh_alias?.trim() && !profile.host?.trim()) {
    errors.push("Choose a host from ~/.ssh/config, or type a host name.");
  }
  if (profile.ssh_alias?.trim() && profile.host?.trim()) {
    errors.push("Use either a ~/.ssh/config host or a typed host, not both.");
  }
  return errors;
}

/**
 * The scratch template keeps `$USER` / `$SLURM_JOB_ID` expansion but may contain
 * nothing else shell-special. Mirrors `validate_scratch_template` in Rust.
 */
export function scratchTemplateError(template: string): string | null {
  const text = template.trim();
  if (!text) {
    return "Add the per-user scratch folder your cluster gives you.";
  }
  if (!text.startsWith("/")) {
    return "The scratch folder must be an absolute path on the cluster.";
  }
  const invalid = (): string =>
    "The scratch folder may contain only letters, digits, '_', '.', '/', '-' and the tokens $USER, ${USER}, $SLURM_JOB_ID, ${SLURM_JOB_ID}.";
  let rest = text;
  for (;;) {
    const index = rest.indexOf("$");
    if (index < 0) break;
    if (/[^A-Za-z0-9_./-]/.test(rest.slice(0, index))) return invalid();
    const after = rest.slice(index + 1);
    let name: string;
    if (after.startsWith("{")) {
      const end = after.indexOf("}");
      if (end < 0) return "The scratch folder has an unterminated ${...} token; close it with '}'.";
      name = after.slice(1, end);
      rest = after.slice(end + 1);
    } else {
      const match = /^[A-Za-z0-9_]+/.exec(after);
      name = match?.[0] ?? "";
      rest = after.slice(name.length);
    }
    if (name !== "USER" && name !== "SLURM_JOB_ID") {
      return `The scratch folder uses $${name || "..."}, which Coperceive does not expand. Use $USER or $SLURM_JOB_ID.`;
    }
  }
  if (/[^A-Za-z0-9_./-]/.test(rest)) return invalid();
  if (text.split("/").includes("..")) {
    return "The scratch folder may not contain a '..' segment.";
  }
  return null;
}

/** Full validation before saving, matching the shared slice-0 profile rules. */
export function validateDraft(entry: ServerEntry): string[] {
  const errors = validateConnectionShape(entry);
  const profile = entry.profile;
  if (!profile.data_roots.some((root) => root.trim())) {
    errors.push("Add at least one data folder on the cluster.");
  }
  for (const root of profile.data_roots) {
    if (!root.trim()) continue;
    if (root.split("/").includes("..")) {
      errors.push("Data folders may not contain '..'. Paths must stay inside the folders you chose.");
    }
  }
  const scratchError = scratchTemplateError(profile.scratch_template);
  if (scratchError) {
    errors.push(scratchError);
  }
  if (!profile.results_root.trim()) {
    errors.push(
      "Add the shared results folder where run outputs should live (not scratch).",
    );
  } else {
    if (!profile.results_root.startsWith("/") && !profile.results_root.startsWith("~")) {
      errors.push("The results folder must be an absolute path on the cluster.");
    }
    if (profile.results_root.split("/").includes("..")) {
      errors.push("The results folder may not contain '..'.");
    }
    const scratchPrefix = profile.scratch_template.split("$")[0].replace(/\/+$/, "");
    if (
      scratchPrefix &&
      (profile.results_root === scratchPrefix || profile.results_root.startsWith(`${scratchPrefix}/`))
    ) {
      errors.push("Results must go to shared storage, not inside the scratch folder.");
    }
  }
  for (const root of profile.code_roots) {
    if (!root.startsWith("/") && !root.startsWith("~")) {
      errors.push("Code folders must be absolute paths on the cluster.");
    }
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
  const command = profile.environment.coperceive_command;
  if (command.length === 0 || command.some((part) => !part.trim())) {
    errors.push(
      "The command that starts coperceive is empty. Choose the Python interpreter under Environment, or use the discovered settings.",
    );
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

export type PartitionGroup = {
  name: string;
  gpuTypes: string[];
  maxTime: string | null;
  nodes: number | null;
  down: boolean;
  gres: string | null;
};

/** One chip per partition: sinfo can list the same partition once per node GRES. */
export function groupPartitions(partitions: DiscoveredPartition[]): PartitionGroup[] {
  const groups = new Map<string, PartitionGroup>();
  for (const partition of partitions) {
    const existing = groups.get(partition.name);
    if (!existing) {
      groups.set(partition.name, {
        name: partition.name,
        gpuTypes: [...partition.gpu_types],
        maxTime: partition.max_time ?? null,
        nodes: partition.nodes ?? null,
        down: partition.state === "down",
        gres: partition.gres ?? null,
      });
      continue;
    }
    for (const gpu of partition.gpu_types) {
      if (!existing.gpuTypes.includes(gpu)) existing.gpuTypes.push(gpu);
    }
    if (existing.nodes !== null && partition.nodes !== undefined && partition.nodes !== null) {
      existing.nodes += partition.nodes;
    } else if (existing.nodes === null && partition.nodes !== undefined) {
      existing.nodes = partition.nodes ?? null;
    }
    existing.maxTime ??= partition.max_time ?? null;
    existing.gres ??= partition.gres ?? null;
    existing.down ||= partition.state === "down";
  }
  return [...groups.values()];
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
 * A walltime the form can save. Slurm's `sinfo` says "infinite"/"unlimited" (or
 * may say "unknown") when a partition has no limit; those are not HH:MM:SS and
 * must not be copied into the field the validator checks.
 */
export function discoveredWalltime(value: string | null | undefined): string | null {
  const text = value?.trim();
  if (!text) return null;
  if (/^(infinite|unlimited|unknown|n\/a)$/i.test(text)) return null;
  return text;
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
  // Offer the first partition that is not marked down. The user still confirms
  // every choice; nothing is submitted by this function.
  const partitionDefault =
    partitions.find((p) => p.state !== "down") ?? partitions[0] ?? null;
  if (profile.scheduler === "slurm") {
    if (!profile.defaults.partition?.trim() && partitionDefault) {
      profile.defaults.partition = partitionDefault.name;
    }
    const chosen =
      partitions.find((p) => p.name === profile.defaults.partition) ?? partitionDefault;
    if (chosen) {
      // No GPU default is stored on the profile. A model-evaluation plan picks
      // one GPU of the partition's detected type; the user can choose no GPU at
      // submit time. Detected GPU types remain choices in the form.
      const walltime = discoveredWalltime(chosen.max_time);
      if (!profile.defaults.time_limit && walltime) {
        profile.defaults.time_limit = walltime;
      }
    }
  }
  const detected = report.environment;
  if (profile.environment.kind === "none" && detected.kind !== "none") {
    profile.environment = { ...detected, modules: [...detected.modules] };
  } else if (!profile.environment.interpreter?.trim() && detected.interpreter) {
    profile.environment.interpreter = detected.interpreter;
  }
  // Saving needs a non-empty argv. Fill it independently of the environment
  // kind: on a cluster where both sides are "none", the kind-equality branch
  // above never runs, which used to leave [] and make saving impossible.
  const command = profile.environment.coperceive_command;
  const isBareDefault = command.length === 1 && command[0] === "coperceive";
  if (command.length === 0 || isBareDefault) {
    const detectedCommand = detected.coperceive_command ?? [];
    const detectedIsBareDefault =
      detectedCommand.length === 1 && detectedCommand[0] === "coperceive";
    if (detectedCommand.length > 0 && !detectedIsBareDefault) {
      profile.environment.coperceive_command = [...detectedCommand];
    } else if (report.discovered?.environment.coperceive_path?.trim()) {
      profile.environment.coperceive_command = [
        report.discovered.environment.coperceive_path.trim(),
      ];
    } else if (profile.environment.interpreter?.trim()) {
      // Prefer the interpreter the USER typed. Detection's environment may be
      // plain python3, where coperceive is not installed; using that would save
      // and then fail on the cluster.
      profile.environment.coperceive_command = [
        profile.environment.interpreter.trim(),
        "-m",
        "coperceive",
      ];
    } else if (detected.interpreter?.trim()) {
      profile.environment.coperceive_command = [detected.interpreter.trim(), "-m", "coperceive"];
    } else {
      profile.environment.coperceive_command = ["coperceive"];
    }
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

export function splitFolderList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

