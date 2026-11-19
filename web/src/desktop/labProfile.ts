// SPDX-License-Identifier: Apache-2.0
/**
 * Plain-language review of a lab server profile.
 *
 * Export and import themselves run in Rust (`server_profiles`). This module
 * only turns the resulting profile into the rows the review screen shows, and
 * refuses a file that already looks like it holds a secret before that call.
 */
import type { ServerProfile } from "./hpc";
import type { PipelineSpec } from "./runPlan";

export interface ReviewField {
  label: string;
  value: string;
}

const PLACEHOLDER_TEXT: Record<string, string> = {
  "{{user}}": "your user name on the cluster",
  "{{home}}": "your home directory on the cluster",
  "{{scratch}}": "your scratch folder",
};

export function plain(value: string | null | undefined): string {
  const text = (value ?? "").trim();
  if (!text) return "Not set";
  let out = text;
  for (const [token, words] of Object.entries(PLACEHOLDER_TEXT)) {
    out = out.replaceAll(token, words);
  }
  return out;
}

function joined(values: readonly string[] | null | undefined): string {
  const items = (values ?? []).map((item) => item.trim()).filter((item) => item.length > 0);
  if (items.length === 0) return "Not set";
  return items.map((item) => plain(item)).join(", ");
}

function yesNo(value: boolean): string {
  return value ? "Yes" : "No";
}

/** Every field of a profile, in the order the review screen shows them. */
export function reviewFields(
  profile: ServerProfile,
  pipelines: readonly PipelineSpec[] = [],
): ReviewField[] {
  const defaults = profile.defaults;
  const environment = profile.environment;
  const rows: ReviewField[] = [
    { label: "Server name", value: plain(profile.name) },
    { label: "SSH alias", value: plain(profile.ssh_alias) },
    { label: "Host name", value: plain(profile.host) },
    { label: "User name", value: plain(profile.user) },
    { label: "Data folders", value: joined(profile.data_roots) },
    { label: "Code folders", value: joined(profile.code_roots) },
    { label: "Scratch folder", value: plain(profile.scratch_template) },
    { label: "Results folder", value: plain(profile.results_root) },
    { label: "Scheduler", value: plain(profile.scheduler) },
    { label: "Partition", value: plain(defaults.partition) },
    { label: "Account", value: plain(defaults.account) },
    { label: "Quality of service", value: plain(defaults.qos) },
    { label: "GPU request", value: plain(defaults.gres) },
    { label: "Time limit", value: plain(defaults.time_limit) },
    { label: "Memory", value: plain(defaults.memory) },
    {
      label: "CPUs per task",
      value: defaults.cpus_per_task == null ? "Not set" : String(defaults.cpus_per_task),
    },
    { label: "Environment", value: plain(environment.kind) },
    { label: "Modules", value: joined(environment.modules) },
    { label: "Python interpreter", value: plain(environment.interpreter) },
    { label: "Container image", value: plain(environment.container_image) },
    { label: "Coperceive command", value: joined(environment.coperceive_command) },
    { label: "Wheels folder", value: plain(environment.wheelhouse) },
    {
      label: "Compute nodes can download packages",
      value: yesNo(environment.compute_has_internet),
    },
    { label: "Notes", value: plain(profile.notes) },
  ];
  pipelines.forEach((pipeline, index) => {
    const number = pipelines.length > 1 ? ` ${index + 1}` : "";
    const args =
      pipeline.arguments.length === 0
        ? "None"
        : pipeline.arguments
            .map((argument) => {
              const help = argument.help.trim();
              return help
                ? `${argument.name} (${argument.kind}): ${help}`
                : `${argument.name} (${argument.kind})`;
            })
            .join("; ");
    const penv = pipeline.environment;
    rows.push(
      { label: `Pipeline${number} name`, value: plain(pipeline.name) },
      { label: `Pipeline${number} script`, value: plain(pipeline.script) },
      { label: `Pipeline${number} command`, value: joined(pipeline.interpreter) },
      { label: `Pipeline${number} arguments`, value: args },
      { label: `Pipeline${number} environment`, value: plain(penv.kind) },
      { label: `Pipeline${number} modules`, value: joined(penv.modules) },
      { label: `Pipeline${number} Python interpreter`, value: plain(penv.interpreter) },
      { label: `Pipeline${number} container image`, value: plain(penv.container_image) },
      {
        label: `Pipeline${number} coperceive command`,
        value: joined(penv.coperceive_command),
      },
      { label: `Pipeline${number} wheels folder`, value: plain(penv.wheelhouse) },
      {
        label: `Pipeline${number} compute nodes can download packages`,
        value: yesNo(penv.compute_has_internet),
      },
    );
  });
  return rows;
}

/** A file that already looks like it carries a secret is not a lab profile. */
export function secretProblem(text: string): string | null {
  const lower = text.toLowerCase();
  if (lower.includes("-----begin ")) {
    return "This file looks like it contains a private key. A lab profile cannot include keys.";
  }
  if (
    lower.includes("password=") ||
    lower.includes("token=") ||
    lower.includes("secret=") ||
    lower.includes("api_key=") ||
    lower.includes("private_key=") ||
    lower.includes("keyring:")
  ) {
    return "This file looks like it contains a password or token. A lab profile cannot include those.";
  }
  if (
    /"(password|passwd|passphrase|secret|token|api_key|private_key|credential|credentials|authorization|bearer|keyring)"\s*:/.test(
      lower,
    )
  ) {
    return "This file has a field that looks like a password, token or key. Remove it before importing.";
  }
  return null;
}

export function parsePipelineRegistrations(text: string): PipelineSpec[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  let value: unknown;
  try {
    value = JSON.parse(trimmed) as unknown;
  } catch {
    throw new Error("The pipeline registration is not valid JSON.");
  }
  const items = Array.isArray(value) ? value : [value];
  return items.map((item, index) => {
    if (item == null || typeof item !== "object" || Array.isArray(item)) {
      throw new Error(`Pipeline registration ${index + 1} must be a JSON object.`);
    }
    const record = item as Partial<PipelineSpec>;
    if (typeof record.name !== "string" || record.name.trim() === "") {
      throw new Error(`Pipeline registration ${index + 1} needs a name.`);
    }
    if (typeof record.script !== "string" || record.script.trim() === "") {
      throw new Error(`Pipeline registration ${index + 1} needs a script path.`);
    }
    return record as PipelineSpec;
  });
}
