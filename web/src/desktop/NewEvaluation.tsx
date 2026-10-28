// SPDX-License-Identifier: Apache-2.0
/**
 * "New evaluation" panel: choose a saved server profile and a pipeline, fill its
 * typed arguments, pick a checkpoint and a test split, run light login-node
 * checks, and review the full argv plus the rendered contract-check script.
 *
 * Nothing is submitted from this panel. The plan is saved under the profile's
 * results_root; the next step submits it after the user has seen the script.
 */
import { useEffect, useState } from "react";
import {
  buildRunPlan,
  listPipelineOptions,
  listServerProfiles,
  readCustomPipeline,
  saveRunPlan,
} from "./local";
import {
  environmentLabel,
  type EnvironmentKind,
  type EnvironmentSetup,
  type ServerEntry,
} from "./hpc";
import {
  collectValues,
  emptyEnvironmentOverride,
  environmentSummary,
  formatArgv,
  initialArgumentForm,
  planIsSaveable,
  planSummary,
  validateArgumentForm,
  type ArgumentForm,
  type PipelineEntry,
  type RunPlan,
  type RunPlanRequest,
} from "./runPlan";

const card = {
  backgroundColor: "rgba(28, 28, 30, 0.85)",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  borderRadius: "14px",
  padding: "1.25rem",
  boxShadow: "0 20px 50px rgba(0, 0, 0, 0.45)",
} as const;

const input = {
  width: "100%",
  boxSizing: "border-box",
  padding: "0.45rem 0.6rem",
  fontSize: "0.85rem",
  borderRadius: "6px",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  backgroundColor: "rgba(0, 0, 0, 0.5)",
  color: "#f5f5f7",
} as const;

const labelStyle = {
  display: "block",
  fontSize: "0.72rem",
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "#86868b",
  marginBottom: "0.25rem",
} as const;

const field = { marginBottom: "0.75rem" } as const;

const primaryButton = {
  padding: "0.5rem 0.9rem",
  borderRadius: "6px",
  border: "none",
  backgroundColor: "#1f6feb",
  color: "#fff",
  fontSize: "0.85rem",
  fontWeight: 500,
  cursor: "pointer",
} as const;

const secondaryButton = {
  padding: "0.45rem 0.8rem",
  borderRadius: "6px",
  border: "1px solid rgba(255, 255, 255, 0.18)",
  backgroundColor: "transparent",
  color: "#f5f5f7",
  fontSize: "0.8rem",
  cursor: "pointer",
} as const;

function CheckRow({ check }: { check: RunPlan["checks"][number] }) {
  return (
    <div style={{ display: "flex", gap: "0.5rem", padding: "0.3rem 0", fontSize: "0.82rem" }}>
      <span aria-hidden style={{ color: check.ok ? "#3fb950" : "#f0883e", flex: "0 0 auto" }}>
        {check.ok ? "✓" : "!"}
      </span>
      <div>
        <span style={{ color: check.ok ? "#f5f5f7" : "#ffb86b", fontWeight: 500 }}>{check.label}</span>
        <span style={{ color: "#a1a1a6" }}> — {check.detail}</span>
        {!check.ok && check.hint ? (
          <div style={{ color: "#8b949e", marginTop: "0.15rem" }}>{check.hint}</div>
        ) : null}
      </div>
    </div>
  );
}

export default function NewEvaluation() {
  const [profiles, setProfiles] = useState<ServerEntry[]>([]);
  const [profileId, setProfileId] = useState("");
  const [pipelines, setPipelines] = useState<PipelineEntry[]>([]);
  const [lastRequest, setLastRequest] = useState<RunPlanRequest | null>(null);
  const [pipelineId, setPipelineId] = useState("");
  const [customScript, setCustomScript] = useState("");
  const [argumentForm, setArgumentForm] = useState<ArgumentForm>({});
  const [checkpoint, setCheckpoint] = useState("");
  const [split, setSplit] = useState("");
  const [overrideEnv, setOverrideEnv] = useState(false);
  const [environment, setEnvironment] = useState<EnvironmentSetup>(emptyEnvironmentOverride());
  const [plan, setPlan] = useState<RunPlan | null>(null);
  const [busy, setBusy] = useState<"catalog" | "check" | "save" | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const selectedProfile = profiles.find((entry) => entry.id === profileId) ?? null;
  const selectedPipeline = pipelines.find((entry) => entry.id === pipelineId) ?? null;

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await listServerProfiles();
        setProfiles(loaded);
        if (loaded.length > 0) setProfileId(loaded[0].id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  useEffect(() => {
    if (!profileId) return;
    setPipelines([]);
    setPipelineId("");
    setPlan(null);
    setBusy("catalog");
    void (async () => {
      try {
        const installed = await listPipelineOptions(profileId);
        setPipelines(installed);
        if (installed.length > 0) selectPipeline(installed[0]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  const selectPipeline = (entry: PipelineEntry) => {
    setPipelineId(entry.id);
    setArgumentForm(initialArgumentForm(entry.spec));
    setPlan(null);
    setNote("");
    // Seed the dedicated checkpoint/split fields from role-tagged arguments.
    const checkpointArgument = entry.spec.arguments.find((argument) => argument.role === "checkpoint");
    const splitArgument = entry.spec.arguments.find((argument) => argument.role === "test_data");
    const form = initialArgumentForm(entry.spec);
    setCheckpoint(typeof form[checkpointArgument?.name ?? ""] === "string" ? String(form[checkpointArgument?.name ?? ""]) : "");
    setSplit(typeof form[splitArgument?.name ?? ""] === "string" ? String(form[splitArgument?.name ?? ""]) : "");
  };

  const loadCustom = async () => {
    if (!selectedProfile) {
      setError("Choose a server profile first.");
      return;
    }
    setBusy("catalog");
    setError("");
    try {
      const entry = await readCustomPipeline(selectedProfile.id, customScript.trim());
      setPipelines((current) => {
        const others = current.filter((pipeline) => pipeline.id !== entry.id);
        return [...others, entry];
      });
      selectPipeline(entry);
      setNote(`Loaded “${entry.name}” from ${entry.registration_path ?? "the cluster"}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const syncRoleValue = (role: "checkpoint" | "test_data", value: string) => {
    if (!selectedPipeline) return;
    const argument = selectedPipeline.spec.arguments.find((candidate) => candidate.role === role);
    if (!argument) return;
    setArgumentForm((current) => ({ ...current, [argument.name]: value }));
  };

  const updateEnvironment = (patch: Partial<EnvironmentSetup>) => {
    setEnvironment((current) => ({ ...current, ...patch }));
  };

  const checkPlan = async () => {
    if (!selectedProfile || !selectedPipeline) {
      setError("Choose a server profile and a pipeline first.");
      return;
    }
    const localErrors = validateArgumentForm(selectedPipeline.spec, argumentForm);
    if (localErrors.length > 0) {
      setError(localErrors.join(" "));
      return;
    }
    if (!checkpoint.trim() || !split.trim()) {
      setError("Choose the checkpoint and the test split on the cluster.");
      return;
    }
    setError("");
    setNote("");
    setBusy("check");
    try {
      const request: RunPlanRequest = {
        profile_id: selectedProfile.id,
        pipeline: selectedPipeline.spec,
        pipeline_source: selectedPipeline.source,
        registration_path: selectedPipeline.registration_path,
        plugin_manifest: selectedPipeline.plugin_manifest,
        values: collectValues(selectedPipeline.spec, argumentForm),
        checkpoint: checkpoint.trim(),
        split: split.trim(),
        environment_override: overrideEnv ? environment : null,
      };
      const built = await buildRunPlan(selectedProfile.id, request);
      setLastRequest(request);
      setPlan(built);
      setNote("Plan checked. Nothing was submitted.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (!selectedProfile || !plan) return;
    if (!lastRequest) {
      setError("Check the plan again before saving it.");
      return;
    }
    setBusy("save");
    setError("");
    try {
      const saved = await saveRunPlan(selectedProfile.id, plan, lastRequest);
      setPlan(saved);
      setNote(`Saved to ${saved.saved_path ?? "the cluster"}. Nothing was submitted.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ maxWidth: "820px", margin: "0 auto", padding: "0 1.5rem 3rem", width: "100%" }}>
      <div style={{ ...card, marginBottom: "1rem" }}>
        <h2 style={{ fontSize: "1.2rem", margin: "0 0 0.35rem", color: "#f5f5f7", fontWeight: 600 }}>
          New evaluation
        </h2>
        <p style={{ fontSize: "0.85rem", color: "#86868b", margin: 0, maxWidth: "40rem" }}>
          Build a run plan for one of your servers: pipeline, typed arguments, checkpoint, test
          split, environment and the contract-check script. The plan is saved on the cluster;
          <strong style={{ color: "#f5f5f7" }}> nothing is submitted here</strong>.
        </p>
      </div>

      {error ? (
        <div role="alert" style={{ marginBottom: "1rem", padding: "0.6rem 0.8rem", borderRadius: "8px", backgroundColor: "rgba(255, 98, 89, 0.12)", border: "1px solid rgba(255, 98, 89, 0.35)", color: "#ff6259", fontSize: "0.82rem" }}>
          {error}
        </div>
      ) : null}
      {note ? (
        <div role="status" style={{ marginBottom: "1rem", padding: "0.6rem 0.8rem", borderRadius: "8px", backgroundColor: "rgba(63, 185, 80, 0.12)", border: "1px solid rgba(63, 185, 80, 0.35)", color: "#7ee787", fontSize: "0.82rem" }}>
          {note}
        </div>
      ) : null}

      {profiles.length === 0 ? (
        <div style={{ ...card, textAlign: "center", padding: "2rem" }}>
          <p style={{ color: "#8b949e", margin: 0 }}>
            Add a server first in the Servers tab, then come back to build an evaluation plan.
          </p>
        </div>
      ) : (
        <div style={card}>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="plan-profile">
                Server
              </label>
              <select
                id="plan-profile"
                style={input}
                value={profileId}
                onChange={(event) => setProfileId(event.target.value)}
              >
                {profiles.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.profile.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="plan-pipeline">
                Pipeline
              </label>
              <select
                id="plan-pipeline"
                style={input}
                value={pipelineId}
                onChange={(event) => {
                  const entry = pipelines.find((candidate) => candidate.id === event.target.value);
                  if (entry) selectPipeline(entry);
                }}
              >
                {pipelines.length === 0 ? <option value="">No pipelines found yet</option> : null}
                {pipelines.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} {entry.source === "builtin" ? "(installed plugin)" : "(custom)"}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", marginBottom: "0.75rem" }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle} htmlFor="plan-custom">
                Custom pipeline script on the cluster (reads coperceive-pipeline.json beside it)
              </label>
              <input
                id="plan-custom"
                style={input}
                value={customScript}
                placeholder="/data/code/pipelines/my_pipeline.py"
                onChange={(event) => setCustomScript(event.target.value)}
              />
            </div>
            <button type="button" style={secondaryButton} disabled={busy !== null || !customScript.trim()} onClick={() => void loadCustom()}>
              {busy === "catalog" ? "Loading…" : "Load pipeline"}
            </button>
          </div>

          {selectedPipeline ? (
            <>
              <p style={{ fontSize: "0.8rem", color: "#8b949e", marginTop: 0 }}>
                {selectedPipeline.description || "A script with a typed argument schema."}
                {selectedPipeline.plugin_manifest ? ` Manifest: ${selectedPipeline.plugin_manifest}.` : ""}
              </p>

              <div style={{ display: "flex", gap: "0.5rem" }}>
                <div style={{ ...field, flex: 1 }}>
                  <label style={labelStyle} htmlFor="plan-checkpoint">
                    Checkpoint (inside data/code folders)
                  </label>
                  <input
                    id="plan-checkpoint"
                    style={input}
                    value={checkpoint}
                    placeholder="/data/code/checkpoints/model.pt"
                    onChange={(event) => {
                      setCheckpoint(event.target.value);
                      syncRoleValue("checkpoint", event.target.value);
                    }}
                  />
                </div>
                <div style={{ ...field, flex: 1 }}>
                  <label style={labelStyle} htmlFor="plan-split">
                    Test split (inside data/code folders)
                  </label>
                  <input
                    id="plan-split"
                    style={input}
                    value={split}
                    placeholder="/data/scenes or /data/code/splits/test.txt"
                    onChange={(event) => {
                      setSplit(event.target.value);
                      syncRoleValue("test_data", event.target.value);
                    }}
                  />
                </div>
              </div>

              {selectedPipeline.spec.arguments.length > 0 ? (
                <div style={{ marginTop: "0.25rem" }}>
                  {selectedPipeline.spec.arguments.map((argument) => {
                    const value = argumentForm[argument.name];
                    const id = `plan-arg-${argument.name}`;
                    return (
                      <div style={field} key={argument.name}>
                        <label style={labelStyle} htmlFor={id}>
                          {argument.name} ({argument.kind})
                          {argument.required ? " · required" : ""}
                        </label>
                        {argument.kind === "bool" ? (
                          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.82rem", color: "#c9d1d9" }}>
                            <input
                              id={id}
                              type="checkbox"
                              checked={value === true}
                              onChange={(event) =>
                                setArgumentForm((current) => ({ ...current, [argument.name]: event.target.checked }))
                              }
                            />
                            {argument.help || "Enabled"}
                          </label>
                        ) : argument.kind === "enum" ? (
                          <select
                            id={id}
                            style={input}
                            value={typeof value === "string" ? value : ""}
                            onChange={(event) =>
                              setArgumentForm((current) => ({ ...current, [argument.name]: event.target.value }))
                            }
                          >
                            <option value="">{argument.required ? "choose one" : "not set"}</option>
                            {argument.choices.map((choice) => (
                              <option key={choice} value={choice}>
                                {choice}
                              </option>
                            ))}
                          </select>
                        ) : argument.multiple ? (
                          <input
                            id={id}
                            style={input}
                            value={Array.isArray(value) ? value.join(", ") : ""}
                            placeholder="comma separated values"
                            onChange={(event) =>
                              setArgumentForm((current) => ({
                                ...current,
                                [argument.name]: event.target.value
                                  .split(",")
                                  .map((item) => item.trim())
                                  .filter(Boolean),
                              }))
                            }
                          />
                        ) : (
                          <input
                            id={id}
                            style={input}
                            type={argument.kind === "int" || argument.kind === "float" ? "number" : "text"}
                            value={typeof value === "string" ? value : ""}
                            placeholder={argument.help || argument.kind}
                            onChange={(event) =>
                              setArgumentForm((current) => ({ ...current, [argument.name]: event.target.value }))
                            }
                          />
                        )}
                        {argument.help && argument.kind !== "bool" ? (
                          <div style={{ color: "#8b949e", fontSize: "0.72rem", marginTop: "0.15rem" }}>{argument.help}</div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}

              <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", margin: "0.75rem 0" }} />
              <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.85rem", color: "#c9d1d9", marginBottom: "0.5rem" }}>
                <input type="checkbox" checked={overrideEnv} onChange={(event) => setOverrideEnv(event.target.checked)} />
                Override the environment for this plan (otherwise the server profile's environment
                is used)
              </label>
              {overrideEnv ? (
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <div style={{ ...field, flex: 1 }}>
                    <label style={labelStyle} htmlFor="plan-env-kind">
                      Environment
                    </label>
                    <select
                      id="plan-env-kind"
                      style={input}
                      value={environment.kind}
                      onChange={(event) => updateEnvironment({ kind: event.target.value as EnvironmentKind })}
                    >
                      {(["none", "modules", "conda", "venv", "container"] as EnvironmentKind[]).map((kind) => (
                        <option key={kind} value={kind}>
                          {environmentLabel(kind)}
                        </option>
                      ))}
                    </select>
                  </div>
                  {environment.kind === "container" ? (
                    <div style={{ ...field, flex: 2 }}>
                      <label style={labelStyle} htmlFor="plan-env-image">
                        Container image
                      </label>
                      <input
                        id="plan-env-image"
                        style={input}
                        value={environment.container_image ?? ""}
                        placeholder="/data/code/containers/pipeline.sif"
                        onChange={(event) => updateEnvironment({ container_image: event.target.value || null })}
                      />
                    </div>
                  ) : (
                    <div style={{ ...field, flex: 2 }}>
                      <label style={labelStyle} htmlFor="plan-env-interp">
                        {environment.kind === "modules" ? "Modules (comma separated)" : "Python path"}
                      </label>
                      <input
                        id="plan-env-interp"
                        style={input}
                        value={
                          environment.kind === "modules"
                            ? environment.modules.join(", ")
                            : (environment.interpreter ?? "")
                        }
                        placeholder={environment.kind === "modules" ? "python/3.12" : "/home/you/venv/bin/python"}
                        onChange={(event) =>
                          updateEnvironment(
                            environment.kind === "modules"
                              ? {
                                  modules: event.target.value
                                    .split(",")
                                    .map((module) => module.trim())
                                    .filter(Boolean),
                                }
                              : { interpreter: event.target.value || null },
                          )
                        }
                      />
                    </div>
                  )}
                </div>
              ) : null}

              <p style={{ fontSize: "0.75rem", color: "#8b949e", margin: "0 0 0.75rem" }}>
                Environment in the plan: {environmentSummary(overrideEnv ? environment : (selectedProfile?.profile.environment ?? environment))}.
                Compute nodes without internet never install or download anything.
              </p>

              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button type="button" style={primaryButton} disabled={busy !== null} onClick={() => void checkPlan()}>
                  {busy === "check" ? "Checking… (up to 1 minute)" : "Check plan"}
                </button>
                <button
                  type="button"
                  style={secondaryButton}
                  disabled={busy !== null || !planIsSaveable(plan)}
                  onClick={() => void save()}
                >
                  {busy === "save" ? "Saving…" : "Save plan"}
                </button>
              </div>
            </>
          ) : (
            <p style={{ fontSize: "0.82rem", color: "#8b949e" }}>
              No pipeline selected yet. Load a custom pipeline (its coperceive-pipeline.json next to
              the script) or pick an installed plugin.
            </p>
          )}
        </div>
      )}

      {plan ? (
        <div style={{ ...card, marginTop: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
            <h3 style={{ margin: 0, fontSize: "1rem", color: "#f5f5f7" }}>Plan {plan.id}</h3>
            <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>
              {planSummary(plan, selectedProfile)}
            </span>
          </div>
          <p style={{ fontSize: "0.8rem", color: "#8b949e", marginTop: 0 }}>
            {plan.environment_note} Environment: {environmentSummary(plan.environment)}. Outputs:
            {" "}
            {plan.output_dir}. Scratch: {plan.scratch_dir}.
          </p>

          <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "0.4rem" }}>
            {plan.checks.map((check) => (
              <CheckRow key={check.name} check={check} />
            ))}
          </div>

          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
            <div style={{ flex: 1 }}>
              <div style={labelStyle}>Checkpoint sha256</div>
              <code style={{ fontSize: "0.75rem", color: "#f5f5f7", wordBreak: "break-all" }}>
                {plan.checkpoint.sha256 ?? "not recorded"} · {plan.checkpoint.path}
              </code>
            </div>
            <div style={{ flex: 1 }}>
              <div style={labelStyle}>Split sha256</div>
              <code style={{ fontSize: "0.75rem", color: "#f5f5f7", wordBreak: "break-all" }}>
                {plan.split.sha256 ?? "not recorded"} · {plan.split.path}
              </code>
            </div>
          </div>

          <div style={{ marginTop: "0.75rem" }}>
            <div style={labelStyle}>Typed argv — one element per line, never a shell string</div>
            <ol style={{ margin: "0.3rem 0 0.5rem", paddingLeft: "2.2rem", fontFamily: "'Geist Mono', ui-monospace, monospace", fontSize: "0.75rem", color: "#c9d1d9" }}>
              {formatArgv(plan.argv).map((line) => (
                <li key={line.index} style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {line.element}
                </li>
              ))}
            </ol>
          </div>

          <div style={{ marginTop: "0.5rem" }}>
            <div style={labelStyle}>Contract-check Slurm script — shown in full, NOT submitted</div>
            <pre
              aria-label="Contract check script"
              style={{ margin: "0.3rem 0 0", padding: "0.75rem", borderRadius: "8px", backgroundColor: "rgba(0,0,0,0.55)", border: "1px solid rgba(255,255,255,0.1)", fontSize: "0.72rem", color: "#c9d1d9", overflowX: "auto", whiteSpace: "pre-wrap" }}
            >
              {plan.contract_check_script}
            </pre>
          </div>

          <div style={{ marginTop: "0.75rem", padding: "0.5rem 0.75rem", borderRadius: "8px", backgroundColor: "rgba(31, 111, 235, 0.15)", border: "1px solid rgba(31, 111, 235, 0.35)", color: "#c9d1d9", fontSize: "0.8rem" }}>
            Nothing was submitted. This step only builds and saves the plan; the next step submits
            the script after you have seen it in full.
            {plan.saved_path ? <div style={{ marginTop: "0.25rem", color: "#7ee787" }}>Saved to {plan.saved_path}.</div> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
