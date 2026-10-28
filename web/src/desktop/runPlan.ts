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
