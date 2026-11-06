// SPDX-License-Identifier: Apache-2.0
/**
 * "New evaluation" panel: choose a saved server profile and a pipeline, fill its
 * typed arguments, pick a checkpoint and a test split, run light login-node
 * checks, and review the full argv plus the rendered contract-check script.
 *
 * Nothing is submitted from this panel. The plan is saved under the profile's
 * results_root; the next step submits it after the user has seen the script.
 */
import { useEffect, useRef, useState } from "react";
import type React from "react";
import IdentifierInput from "./IdentifierInput";
import JobWatch from "./JobWatch";
import {
  buildRunPlan,
  listPipelineOptions,
  listServerProfiles,
  previewSubmission,
  readCustomPipeline,
  saveRunPlan,
  submitRunPlan,
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
  jobStateLabel,
  validateArgumentForm,
  type ArgumentForm,
  type JobSubmitOutcome,
  type PipelineEntry,
  type RunPlan,
  type RunPlanRequest,
  type SubmissionPreview,
  type SubmitOptions,
} from "./runPlan";
import { errorText } from "./errors";
import { InspectorSection, MiniBar, PageLayout, usePageHeader } from "./components/Page";

/**
 * One step of the plan builder. A step opens once the steps it depends on are
 * complete; until then it shows what it will ask for, greyed out.
 */
function Step({
  index,
  title,
  value,
  done,
  open,
  locked,
  children,
}: {
  index: number;
  title: string;
  value: string;
  done: boolean;
  open: boolean;
  locked?: string;
  children: React.ReactNode;
}) {
  const state = done ? "is-done" : open ? "is-current" : "is-locked";
  return (
    <section className={`c-step ${state}`} aria-label={`Step ${index}: ${title}`}>
      <span className="c-step-index" aria-hidden="true">
        {done ? "✓" : index}
      </span>
      <div className="c-step-body">
        <div className="c-step-head">
          <span className="c-step-title">{title}</span>
          {value ? <span className="c-step-value c-mono">{value}</span> : null}
        </div>
        {open ? <div className="c-step-fields">{children}</div> : locked ? <span className="c-step-note">{locked}</span> : null}
      </div>
    </section>
  );
}

const card = {
  backgroundColor: "var(--panel)",
  border: "1px solid var(--line-elevated)",
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
  border: "1px solid var(--line-elevated)",
  backgroundColor: "var(--bg)",
  color: "var(--text)",
} as const;

const labelStyle = {
  display: "block",
  fontSize: "0.72rem",
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "var(--text-subtle)",
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
  border: "1px solid var(--line-elevated)",
  backgroundColor: "transparent",
  color: "var(--text)",
  fontSize: "0.8rem",
  cursor: "pointer",
} as const;

function CheckRow({ check }: { check: RunPlan["checks"][number] }) {
  return (
    <div style={{ display: "flex", gap: "0.5rem", padding: "0.3rem 0", fontSize: "0.82rem" }}>
      <span aria-hidden style={{ color: check.ok ? "var(--ok)" : "#d29922", flex: "0 0 auto" }}>
        {check.ok ? "✓" : "!"}
      </span>
      <div>
        <span style={{ color: check.ok ? "var(--text)" : "#d29922", fontWeight: 500 }}>{check.label}</span>
        <span style={{ color: "var(--text-subtle)" }}> — {check.detail}</span>
        {!check.ok && check.hint ? (
          <div style={{ color: "var(--text-subtle)", marginTop: "0.15rem" }}>{check.hint}</div>
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
  const [model, setModel] = useState("vehicle-side");
  const [rawRoot, setRawRoot] = useState("");
  const [mediaRoot, setMediaRoot] = useState("");
  const [overrideEnv, setOverrideEnv] = useState(false);
  const [environment, setEnvironment] = useState<EnvironmentSetup>(emptyEnvironmentOverride());
  const [plan, setPlan] = useState<RunPlan | null>(null);
  const [busy, setBusy] = useState<"catalog" | "check" | "save" | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [showSubmitAdvanced, setShowSubmitAdvanced] = useState(false);
  const [submitWalltime, setSubmitWalltime] = useState("04:00:00");
  const [submitCpus, setSubmitCpus] = useState("");
  const [submitGres, setSubmitGres] = useState("");
  const [submitPreview, setSubmitPreview] = useState<SubmissionPreview | null>(null);
  const [submitConfirmed, setSubmitConfirmed] = useState(false);
  const [submitBusy, setSubmitBusy] = useState<"preview" | "submit" | null>(null);
  const [submitOutcome, setSubmitOutcome] = useState<JobSubmitOutcome | null>(null);
  const [submitError, setSubmitError] = useState("");

  const selectedProfile = profiles.find((entry) => entry.id === profileId) ?? null;
  const selectedPipeline = pipelines.find((entry) => entry.id === pipelineId) ?? null;
  // Builtin model plugins always take a checkpoint and a scene/split. A custom
  // pipeline only gets these fields when its registration declares the role;
  // otherwise its own typed arguments are the complete input set.
  const needsCheckpoint =
    selectedPipeline?.source === "builtin" ||
    Boolean(selectedPipeline?.spec.arguments.some((argument) => argument.role === "checkpoint"));
  const needsSplit =
    selectedPipeline?.source === "builtin" ||
    Boolean(selectedPipeline?.spec.arguments.some((argument) => argument.role === "test_data"));
  const seeded = useRef(false);
  const stepArguments = (selectedPipeline?.spec.arguments ?? []).filter(
    (argument) => argument.role !== "checkpoint" && argument.role !== "test_data",
  );
  const selectedPartition = selectedProfile?.discovered?.partitions.find(
    (partition) => partition.name === selectedProfile.profile.defaults.partition,
  );
  const partitionGpuTypes = selectedPartition?.gpu_types ?? [];
  const gpuChoices = plan?.gres && !partitionGpuTypes.some(
    (type) => (type === "gpu" ? "gpu:1" : `gpu:${type}:1`) === plan.gres,
  )
    ? [...partitionGpuTypes, plan.gres]
    : partitionGpuTypes;

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await listServerProfiles();
        if (seeded.current) return;
        setProfiles(loaded);
        if (loaded.length > 0) setProfileId(loaded[0].id);
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, []);

  // Test-only: seeded servers and pipelines, as the screenshot tests have no cluster.
  useEffect(() => {
    const handleSeed = (event: Event) => {
      const detail = (event as CustomEvent<{ profiles?: ServerEntry[]; pipelines?: PipelineEntry[] }>).detail;
      if (!detail?.profiles) return;
      seeded.current = true;
      setProfiles(detail.profiles);
      setProfileId(detail.profiles[0]?.id ?? "");
      setPipelines(detail.pipelines ?? []);
      setError("");
      if (detail.pipelines?.[0]) selectPipeline(detail.pipelines[0]);
    };
    window.addEventListener("coperceive-test-evaluation-seed", handleSeed);
    return () => window.removeEventListener("coperceive-test-evaluation-seed", handleSeed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!profileId || seeded.current) return;
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
        setError(errorText(e));
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
      setError(errorText(e));
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

  const submitOptions = (): SubmitOptions => ({
    walltime: submitWalltime.trim() || "04:00:00",
    cpus_per_task: submitCpus.trim() ? Number(submitCpus.trim()) : null,
    account: null,
    qos: null,
    gres: submitGres,
  });

  useEffect(() => {
    setSubmitGres(plan?.gres ?? "");
  }, [plan]);

  const invalidateSubmitPreview = () => {
    setSubmitPreview(null);
    setSubmitConfirmed(false);
    setSubmitOutcome(null);
    setSubmitError("");
  };

  const previewJobScript = async () => {
    if (!selectedProfile || !plan || !plan.saved_path) {
      setError("Save the plan before preparing a submission.");
      return;
    }
    setSubmitBusy("preview");
    setSubmitError("");
    setSubmitOutcome(null);
    try {
      const preview = await previewSubmission(
        selectedProfile.id,
        plan.id,
        submitOptions(),
      );
      setSubmitPreview(preview);
      setSubmitConfirmed(false);
    } catch (e) {
      setSubmitError(errorText(e));
    } finally {
      setSubmitBusy(null);
    }
  };

  const submitConfirmedPlan = async () => {
    if (!selectedProfile || !plan || !submitPreview || !submitConfirmed) return;
    setSubmitBusy("submit");
    setSubmitError("");
    try {
      const outcome = await submitRunPlan(
        selectedProfile.id,
        plan.id,
        submitOptions(),
        submitPreview.preview_hash,
        true,
      );
      setSubmitOutcome(outcome);
    } catch (e) {
      setSubmitError(errorText(e));
    } finally {
      setSubmitBusy(null);
    }
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
    if ((needsCheckpoint && !checkpoint.trim()) || (needsSplit && !split.trim())) {
      setError(
        "Choose the checkpoint and the test split on the cluster for this pipeline.",
      );
      return;
    }
    if (
      selectedPipeline.source === "builtin" &&
      (!model.trim() || !rawRoot.trim() || !mediaRoot.trim())
    ) {
      setError("Choose the model key, raw root and media root for this built-in model.");
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
        model: selectedPipeline.source === "builtin" ? model.trim() : "",
        raw_root: selectedPipeline.source === "builtin" ? rawRoot.trim() : "",
        media_root: selectedPipeline.source === "builtin" ? mediaRoot.trim() : "",
        environment_override: overrideEnv ? environment : null,
      };
      const built = await buildRunPlan(selectedProfile.id, request);
      setLastRequest(request);
      setPlan(built);
      invalidateSubmitPreview();
      setNote("Plan checked. Nothing was submitted.");
    } catch (e) {
      setError(errorText(e));
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
      invalidateSubmitPreview();
      setNote(`Saved to ${saved.saved_path ?? "the cluster"}. Nothing was submitted.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const profileName = selectedProfile?.profile.name ?? "";
  usePageHeader("evaluation", {
    meta: selectedProfile
      ? `Build a run plan on ${profileName} · nothing is submitted until you confirm it`
      : "Build a run plan on one of your servers",
  });
  const serverDone = Boolean(selectedProfile);
  const pipelineDone = Boolean(selectedPipeline);
  const checkpointDone = pipelineDone && (!needsCheckpoint || checkpoint.trim().length > 0);
  const splitDone = pipelineDone && (!needsSplit || split.trim().length > 0);
  const environmentDone = pipelineDone && (!overrideEnv || environment.kind !== "none");
  const contractDone = Boolean(plan);
  const doneCount = [serverDone, pipelineDone, checkpointDone, splitDone, environmentDone, contractDone].filter(Boolean).length;
  const environmentText = environmentSummary(overrideEnv ? environment : (selectedProfile?.profile.environment ?? environment));
  const scriptPreview = plan?.contract_check_script ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0, fontSize: "13px" }}>
      <PageLayout
        inspectorLabel="Run plan"
        inspector={
          profiles.length === 0 ? null : <>
            <InspectorSection title="Run plan">
              <span style={{ fontWeight: 500, color: "var(--text)" }}>
                {doneCount === 6 ? "Ready to save" : `${doneCount} of 6 steps complete`}
              </span>
              <MiniBar value={doneCount / 6} tone="primary" />
            </InspectorSection>
            <InspectorSection>
              <dl className="c-pairs">
                <dt>Server</dt>
                <dd>{profileName || "—"}</dd>
                <dt>Pipeline</dt>
                <dd>{selectedPipeline?.name ?? "—"}</dd>
                <dt>Checkpoint</dt>
                <dd className="c-mono">{needsCheckpoint ? checkpoint.trim() || "—" : pipelineDone ? "not used" : "—"}</dd>
                <dt>Test split</dt>
                <dd className="c-mono">{needsSplit ? split.trim() || "—" : pipelineDone ? "not used" : "—"}</dd>
                {selectedPipeline?.source === "builtin" ? (
                  <>
                    <dt>Model</dt>
                    <dd className="c-mono">{model.trim() || "—"}</dd>
                    <dt>Raw root</dt>
                    <dd className="c-mono">{rawRoot.trim() || "—"}</dd>
                    <dt>Media root</dt>
                    <dd className="c-mono">{mediaRoot.trim() || "—"}</dd>
                  </>
                ) : null}
                <dt>Environment</dt>
                <dd>{pipelineDone ? environmentText : "—"}</dd>
                <dt>Contract check</dt>
                <dd>{plan ? (plan.checks.every((check) => check.ok) ? "passed" : "needs attention") : "—"}</dd>
              </dl>
            </InspectorSection>
            <InspectorSection title="Contract-check script">
              <pre className="c-script" aria-label="Contract check script preview">
                {scriptPreview ?? "Appears once the contract check (step 6) has run."}
              </pre>
            </InspectorSection>
            <InspectorSection>
              <button
                type="button"
                className="c-primary-wide is-blue"
                disabled={busy !== null || !planIsSaveable(plan)}
                onClick={() => void save()}
              >
                {busy === "save" ? "Saving…" : "Save plan to cluster"}
              </button>
              <span style={{ fontSize: "12px", color: "var(--text-subtle)", textAlign: "center" }}>
                {plan?.saved_path
                  ? `Saved to ${plan.saved_path}. Nothing was submitted.`
                  : "Saved next to your pipeline. Saving submits nothing; submitting is a separate, confirmed step."}
              </span>
            </InspectorSection>
          </>
        }
      >
        <div className="c-steps">
          {error ? (
            <div role="alert" className="c-banner is-danger" style={{ margin: "0 0 12px" }}>
              {error}
            </div>
          ) : null}
          {note ? (
            <div role="status" className="c-banner" style={{ margin: "0 0 12px" }}>
              {note}
            </div>
          ) : null}
          {profiles.length === 0 ? (
            <p className="c-page-note" style={{ padding: "12px 0" }}>
              Add a server first in Servers, then come back to build an evaluation plan.
            </p>
          ) : (
            <>
              <Step index={1} title="Server" value={profileName} done={serverDone} open>
                <select
                  id="plan-profile"
                  aria-label="Server"
                  className="c-select"
                  value={profileId}
                  onChange={(event) => setProfileId(event.target.value)}
                >
                  {profiles.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.profile.name}
                    </option>
                  ))}
                </select>
              </Step>
              <Step index={2} title="Pipeline" value={selectedPipeline?.name ?? ""} done={pipelineDone} open={serverDone}>
                {pipelines.length > 0 ? (
                  <select
                    id="plan-pipeline"
                    aria-label="Pipeline"
                    className="c-select"
                    value={pipelineId}
                    onChange={(event) => {
                      const entry = pipelines.find((candidate) => candidate.id === event.target.value);
                      if (entry) selectPipeline(entry);
                    }}
                  >
                    {pipelines.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.name} {entry.source === "builtin" ? "(installed plugin)" : "(custom)"}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="c-step-note">
                    {busy === "catalog"
                      ? "Looking for installed plugins…"
                      : "No installed plugins found. Load a custom script — its coperceive-pipeline.json must sit beside it."}
                  </span>
                )}
                <div style={{ display: "flex", gap: "8px" }}>
                  <IdentifierInput
                    id="plan-custom"
                    aria-label="Custom pipeline script on the cluster"
                    className="c-path-input"
                    value={customScript}
                    placeholder="/data/code/pipelines/my_pipeline.py"
                    onChange={(event) => setCustomScript(event.target.value)}
                  />
                  <button
                    type="button"
                    className="c-secondary"
                    disabled={busy !== null || !customScript.trim()}
                    onClick={() => void loadCustom()}
                  >
                    {busy === "catalog" ? "Loading…" : "Load pipeline"}
                  </button>
                </div>
                {selectedPipeline ? (
                  <>
                    <span className="c-step-note">
                      {selectedPipeline.description || "A script with a typed argument schema."}
                      {selectedPipeline.plugin_manifest ? ` Manifest: ${selectedPipeline.plugin_manifest}.` : ""}
                    </span>
                    {selectedPipeline.source === "builtin" ? (
                      <>
                        <div style={field}>
                          <label style={labelStyle} htmlFor="plan-model">Model key</label>
                          <input
                            id="plan-model"
                            className="c-path-input"
                            value={model}
                            onChange={(event) => setModel(event.target.value)}
                            placeholder="vehicle-side"
                          />
                        </div>
                        <div style={field}>
                          <label style={labelStyle} htmlFor="plan-raw">Raw root</label>
                          <input
                            id="plan-raw"
                            className="c-path-input"
                            value={rawRoot}
                            onChange={(event) => setRawRoot(event.target.value)}
                            placeholder="/data/griffin-release"
                          />
                        </div>
                        <div style={field}>
                          <label style={labelStyle} htmlFor="plan-media">Media root</label>
                          <input
                            id="plan-media"
                            className="c-path-input"
                            value={mediaRoot}
                            onChange={(event) => setMediaRoot(event.target.value)}
                            placeholder="/data/griffin-release"
                          />
                        </div>
                      </>
                    ) : null}
              {/* Checkpoint and test-split arguments have their own steps (3 and 4). */}
              {stepArguments.length > 0 ? (
                <div style={{ marginTop: "0.25rem" }}>
                  {stepArguments.map((argument) => {
                    const value = argumentForm[argument.name];
                    const id = `plan-arg-${argument.name}`;
                    return (
                      <div style={field} key={argument.name}>
                        <label style={labelStyle} htmlFor={id}>
                          {argument.name} ({argument.kind})
                          {argument.required ? " · required" : ""}
                        </label>
                        {argument.kind === "bool" ? (
                          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.82rem", color: "var(--text)" }}>
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
                          <IdentifierInput
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
                          <IdentifierInput
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
                          <div style={{ color: "var(--text-subtle)", fontSize: "0.72rem", marginTop: "0.15rem" }}>{argument.help}</div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
                  </>
                ) : null}
              </Step>
              <Step
                index={3}
                title="Checkpoint"
                value={needsCheckpoint ? checkpoint.trim() : pipelineDone ? "not used" : ""}
                done={checkpointDone}
                open={pipelineDone}
                locked="Pick a model checkpoint on the cluster. Unlocks after the pipeline loads."
              >
                {needsCheckpoint ? (
                  <IdentifierInput
                    id="plan-checkpoint"
                    aria-label="Checkpoint (inside data/code folders)"
                    className="c-path-input"
                    value={checkpoint}
                    placeholder="/data/code/checkpoints/model.pt"
                    onChange={(event) => {
                      setCheckpoint(event.target.value);
                      syncRoleValue("checkpoint", event.target.value);
                    }}
                  />
                ) : (
                  <span className="c-step-note">This pipeline takes no checkpoint.</span>
                )}
              </Step>
              <Step
                index={4}
                title="Test split"
                value={needsSplit ? split.trim() : pipelineDone ? "not used" : ""}
                done={splitDone}
                open={pipelineDone}
                locked="Choose which prepared split to evaluate on. Unlocks after the pipeline loads."
              >
                {needsSplit ? (
                  <IdentifierInput
                    id="plan-split"
                    aria-label="Test split (inside data/code folders)"
                    className="c-path-input"
                    value={split}
                    placeholder="/data/scenes or /data/code/splits/test.txt"
                    onChange={(event) => {
                      setSplit(event.target.value);
                      syncRoleValue("test_data", event.target.value);
                    }}
                  />
                ) : (
                  <span className="c-step-note">This pipeline takes no test split.</span>
                )}
              </Step>
              <Step
                index={5}
                title="Environment"
                value={pipelineDone ? environmentText : ""}
                done={environmentDone}
                open={pipelineDone}
                locked="Conda env, GPU count and time limit for the job. Unlocks after the pipeline loads."
              >
              <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.85rem", color: "var(--text)", marginBottom: "0.5rem" }}>
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
                      <IdentifierInput
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
                      <IdentifierInput
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

              <p style={{ fontSize: "0.75rem", color: "var(--text-subtle)", margin: "0 0 0.75rem" }}>
                Environment in the plan: {environmentSummary(overrideEnv ? environment : (selectedProfile?.profile.environment ?? environment))}.
                Compute nodes without internet never install or download anything.
              </p>
              </Step>
              <Step
                index={6}
                title="Contract check"
                value={plan ? `plan ${plan.id}` : ""}
                done={contractDone}
                open={checkpointDone && splitDone}
                locked="Dry-runs the pipeline on one frame to confirm its outputs match what the viewer expects. Unlocks after the pipeline loads."
              >
                <button type="button" className="c-secondary" style={{ alignSelf: "flex-start" }} disabled={busy !== null} onClick={() => void checkPlan()}>
                  {busy === "check" ? "Checking… (up to 1 minute)" : "Check plan"}
                </button>
              </Step>
            </>
          )}
        </div>
      {plan ? (
        <div style={{ ...card, marginTop: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
            <h3 style={{ margin: 0, fontSize: "1rem", color: "var(--text)" }}>Plan {plan.id}</h3>
            <span style={{ fontSize: "0.75rem", color: "var(--text-subtle)" }}>
              {planSummary(plan, selectedProfile)}
            </span>
          </div>
          <p style={{ fontSize: "0.8rem", color: "var(--text-subtle)", marginTop: 0 }}>
            {plan.environment_note} Environment: {environmentSummary(plan.environment)}. Outputs:
            {" "}
            {plan.output_dir}. Scratch: {plan.scratch_dir}.
          </p>

          <div style={{ borderTop: "1px solid var(--line-elevated)", paddingTop: "0.4rem" }}>
            {plan.checks.map((check) => (
              <CheckRow key={check.name} check={check} />
            ))}
          </div>

          {plan.checkpoint.path || plan.split.path ? (
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
              <div style={{ flex: 1 }}>
                <div style={labelStyle}>Checkpoint sha256</div>
                <code style={{ fontSize: "0.75rem", color: "var(--text)", wordBreak: "break-all" }}>
                  {plan.checkpoint.sha256 ?? "not recorded"} · {plan.checkpoint.path}
                </code>
              </div>
              <div style={{ flex: 1 }}>
                <div style={labelStyle}>Split sha256</div>
                <code style={{ fontSize: "0.75rem", color: "var(--text)", wordBreak: "break-all" }}>
                  {plan.split.sha256 ?? "not recorded"} · {plan.split.path}
                </code>
              </div>
            </div>
          ) : null}

          <div style={{ marginTop: "0.75rem" }}>
            <div style={labelStyle}>Typed argv — one element per line, never a shell string</div>
            <ol style={{ margin: "0.3rem 0 0.5rem", paddingLeft: "2.2rem", fontFamily: "'Geist Mono', ui-monospace, monospace", fontSize: "0.75rem", color: "var(--text)" }}>
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
              style={{ margin: "0.3rem 0 0", padding: "0.75rem", borderRadius: "8px", backgroundColor: "var(--panel)", border: "1px solid var(--line-elevated)", fontSize: "0.72rem", color: "var(--text)", overflowX: "auto", whiteSpace: "pre-wrap" }}
            >
              {plan.contract_check_script}
            </pre>
          </div>

          <div style={{ marginTop: "0.75rem", padding: "0.5rem 0.75rem", borderRadius: "8px", backgroundColor: "rgba(31, 111, 235, 0.15)", border: "1px solid rgba(31, 111, 235, 0.35)", color: "var(--text)", fontSize: "0.8rem" }}>
            Nothing was submitted. This step only builds and saves the plan; the next step submits
            the script after you have seen it in full.
            {plan.saved_path ? <div style={{ marginTop: "0.25rem", color: "var(--ok)" }}>Saved to {plan.saved_path}.</div> : null}
          </div>

          {plan.saved_path ? (
            <div style={{ marginTop: "0.9rem", borderTop: "1px solid var(--line-elevated)", paddingTop: "0.75rem" }}>
              <h4 style={{ margin: "0 0 0.25rem", fontSize: "0.95rem", color: "var(--text)" }}>
                Submit this run
              </h4>
              <p style={{ margin: "0 0 0.5rem", fontSize: "0.8rem", color: "var(--text-subtle)" }}>
                Generate the full job script and read it before submitting. Nothing is submitted
                automatically; the Submit button is enabled only after you confirm the script you see.
              </p>
              <button
                type="button"
                style={secondaryButton}
                onClick={() => setShowSubmitAdvanced((current) => !current)}
              >
                {showSubmitAdvanced ? "Hide advanced" : "Advanced"}
              </button>
              {showSubmitAdvanced ? (
                <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle} htmlFor="submit-walltime">
                      Walltime
                    </label>
                    <IdentifierInput
                      id="submit-walltime"
                      style={input}
                      value={submitWalltime}
                      placeholder="04:00:00"
                      onChange={(event) => {
                        setSubmitWalltime(event.target.value);
                        invalidateSubmitPreview();
                      }}
                    />
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle} htmlFor="submit-cpus">
                      CPUs per task
                    </label>
                    <IdentifierInput
                      id="submit-cpus"
                      type="number"
                      min={1}
                      style={input}
                      value={submitCpus}
                      placeholder="from the profile"
                      onChange={(event) => {
                        setSubmitCpus(event.target.value);
                        invalidateSubmitPreview();
                      }}
                    />
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle} htmlFor="submit-gres">
                      GPU
                    </label>
                    <select
                      id="submit-gres"
                      style={input}
                      value={submitGres}
                      onChange={(event) => {
                        setSubmitGres(event.target.value);
                        invalidateSubmitPreview();
                      }}
                    >
                      <option value="">No GPU (CPU-only)</option>
                      {gpuChoices.map((type) => {
                        const value = type.includes(":") ? type : type === "gpu" ? "gpu:1" : `gpu:${type}:1`;
                        return (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        );
                      })}
                    </select>
                    {selectedPartition && partitionGpuTypes.length === 0 ? (
                      <p style={{ margin: "0.25rem 0 0", color: "var(--text-subtle)", fontSize: "0.72rem" }}>
                        This partition has no GPUs; the job will not request one.
                      </p>
                    ) : null}
                  </div>
                </div>
              ) : null}
              <div style={{ marginTop: "0.6rem" }}>
                <button
                  type="button"
                  style={primaryButton}
                  onClick={previewJobScript}
                  disabled={submitBusy !== null}
                >
                  {submitBusy === "preview" ? "Rendering…" : "Generate submission script"}
                </button>
              </div>
              {submitError ? (
                <div style={{ marginTop: "0.5rem", color: "var(--danger)", fontSize: "0.8rem" }}>
                  {submitError}
                </div>
              ) : null}
              {submitPreview ? (
                <div style={{ marginTop: "0.6rem" }}>
                  <div style={{ fontSize: "0.78rem", color: "var(--text)" }}>
                    Partition {submitPreview.partition} · walltime {submitPreview.walltime} · GPU:{" "}
                    {submitPreview.gres && submitPreview.gres.trim() ? submitPreview.gres : "no GPU requested"}{" "}
                    · {submitPreview.job_name}
                  </div>
                  <pre
                    aria-label="Submission job script"
                    style={{ margin: "0.3rem 0 0.5rem", padding: "0.75rem", borderRadius: "8px", backgroundColor: "var(--panel)", border: "1px solid var(--line-elevated)", fontSize: "0.72rem", color: "var(--text)", overflowX: "auto", whiteSpace: "pre-wrap" }}
                  >
                    {submitPreview.script}
                  </pre>
                  <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.82rem", color: "var(--text)" }}>
                    <input
                      type="checkbox"
                      checked={submitConfirmed}
                      onChange={(event) => setSubmitConfirmed(event.target.checked)}
                    />
                    I have read the full job script above and confirm this submission.
                  </label>
                  <div style={{ marginTop: "0.5rem" }}>
                    <button
                      type="button"
                      style={primaryButton}
                      onClick={submitConfirmedPlan}
                      disabled={!submitConfirmed || submitBusy !== null}
                    >
                      {submitBusy === "submit" ? "Submitting…" : "Submit job"}
                    </button>
                  </div>
                  {submitOutcome && plan ? (
                    <div style={{ marginTop: "0.5rem", padding: "0.5rem 0.75rem", borderRadius: "8px", backgroundColor: "rgba(63, 185, 80, 0.12)", border: "1px solid rgba(63, 185, 80, 0.35)", color: "var(--text)", fontSize: "0.82rem" }}>
                      Submitted as job <strong>{submitOutcome.job_id}</strong> —{" "}
                      {jobStateLabel(submitOutcome)}
                      <JobWatch
                        profileId={profileId}
                        planId={plan.id}
                        initialOutcome={submitOutcome}
                      />
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      </PageLayout>
    </div>
  );
}
