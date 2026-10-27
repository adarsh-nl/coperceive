// SPDX-License-Identifier: Apache-2.0
/**
 * Remote HPC workspace: add a server, test the connection, review what was
 * detected, and override anything. Sign-in uses the user's own ssh-agent and
 * ~/.ssh/config; there is no password or key field anywhere.
 */
import { useEffect, useState } from "react";
import {
  deleteServerProfile,
  listServerProfiles,
  listSshConfigHosts,
  readSshHostSettings,
  saveServerProfile,
  testServerConnection,
} from "./local";
import HpcBrowse from "./HpcBrowse";
import {
  applyDiscovery,
  emptyEntry,
  environmentLabel,
  formatBytes,
  gpuTypesFor,
  missingExplanations,
  partitionNames,
  profileSummary,
  reportSummary,
  splitFolderList,
  validateConnectionShape,
  validateDraft,
  type DetectionReport,
  type EnvironmentKind,
  type SchedulerKind,
  type ServerEntry,
} from "./hpc";

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

function CheckRow({ ok, label, detail, hint }: { ok: boolean; label: string; detail: string; hint?: string | null }) {
  return (
    <div style={{ display: "flex", gap: "0.5rem", padding: "0.35rem 0", fontSize: "0.82rem" }}>
      <span aria-hidden style={{ color: ok ? "#3fb950" : "#f0883e", flex: "0 0 auto" }}>
        {ok ? "✓" : "!"}
      </span>
      <div>
        <span style={{ color: ok ? "#f5f5f7" : "#ffb86b", fontWeight: 500 }}>{label}</span>
        <span style={{ color: "#a1a1a6" }}> — {detail}</span>
        {!ok && hint ? (
          <div style={{ color: "#8b949e", marginTop: "0.15rem" }}>{hint}</div>
        ) : null}
      </div>
    </div>
  );
}

export default function HpcServers() {
  const [profiles, setProfiles] = useState<ServerEntry[]>([]);
  const [hosts, setHosts] = useState<string[]>([]);
  const [draft, setDraft] = useState<ServerEntry | null>(null);
  const [report, setReport] = useState<DetectionReport | null>(null);
  const [cardReports, setCardReports] = useState<Record<string, DetectionReport>>({});
  const [busy, setBusy] = useState<"test" | "save" | "card" | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [browseProfile, setBrowseProfile] = useState<ServerEntry | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [loadedProfiles, loadedHosts] = await Promise.all([
          listServerProfiles(),
          listSshConfigHosts(),
        ]);
        setProfiles(loadedProfiles);
        setHosts(loadedHosts);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  const updateProfile = (patch: Partial<ServerEntry["profile"]>) => {
    setDraft((current) =>
      current ? { ...current, profile: { ...current.profile, ...patch } } : current,
    );
  };

  const updateDefaults = (patch: Partial<ServerEntry["profile"]["defaults"]>) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            profile: {
              ...current.profile,
              defaults: { ...current.profile.defaults, ...patch },
            },
          }
        : current,
    );
  };

  const updateEnvironment = (patch: Partial<ServerEntry["profile"]["environment"]>) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            profile: {
              ...current.profile,
              environment: { ...current.profile.environment, ...patch },
            },
          }
        : current,
    );
  };

  const startAdd = () => {
    setDraft(emptyEntry());
    setReport(null);
    setError("");
    setNote("");
  };

  const chooseAlias = async (alias: string) => {
    if (!draft) return;
    if (!alias) {
      updateProfile({ ssh_alias: null });
      return;
    }
    updateProfile({ ssh_alias: alias, name: draft.profile.name || alias });
    const settings = await readSshHostSettings(alias);
    if (settings) {
      setDraft((current) =>
        current
          ? {
              ...current,
              ssh: settings,
              profile: {
                ...current.profile,
                name: current.profile.name || settings.hostname || alias,
                ssh_alias: alias,
              },
            }
          : current,
      );
    }
  };

  const runTest = async (entry: ServerEntry, forCard: boolean) => {
    const validation = validateConnectionShape(entry);
    if (validation.length > 0) {
      setError(validation.join(" "));
      return;
    }
    setError("");
    setNote("");
    setBusy(forCard ? "card" : "test");
    try {
      const result = await testServerConnection(entry);
      setReport(result);
      if (forCard) {
        setCardReports((current) => ({ ...current, [entry.id]: result }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const applyDetected = () => {
    if (!draft || !report) return;
    setDraft(applyDiscovery(draft, report));
    setNote("Detected settings were applied where you had not chosen a value. You can change anything below.");
  };

  const save = async () => {
    if (!draft) return;
    const validation = validateDraft(draft);
    if (validation.length > 0) {
      setError(validation.join(" "));
      return;
    }
    setError("");
    setBusy("save");
    try {
      const saved = await saveServerProfile(draft);
      setProfiles(await listServerProfiles());
      setDraft(saved);
      setNote(`Saved “${saved.profile.name}”.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (entry: ServerEntry) => {
    if (!window.confirm(`Remove the server “${entry.profile.name}”? Saved data folders are not touched.`)) {
      return;
    }
    try {
      await deleteServerProfile(entry.id);
      setProfiles(await listServerProfiles());
      if (draft?.id === entry.id) setDraft(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const closeEditor = () => {
    setDraft(null);
    setReport(null);
    setError("");
    setNote("");
  };

  const discoveredPartitions = draft?.discovered?.partitions ?? [];
  const partitionChoices = [
    ...new Set([...partitionNames(draft ?? emptyEntry()), ...discoveredPartitions.map((p) => p.name)]),
  ];
  const gpuChoices = gpuTypesFor(draft ?? emptyEntry(), draft?.profile.defaults.partition ?? null);
  const selectedGpu = (() => {
    const gres = draft?.profile.defaults.gres ?? "";
    if (!gres.startsWith("gpu")) return "";
    const parts = gres.split(":");
    if (parts.length >= 2 && !/^\d+$/.test(parts[1])) return parts[1];
    return "gpu";
  })();

  return (
    <div style={{ maxWidth: "760px", margin: "0 auto", padding: "0 1.5rem 3rem", width: "100%" }}>
      <div style={{ ...card, marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "flex-start" }}>
          <div>
            <h2 style={{ fontSize: "1.2rem", margin: "0 0 0.35rem", color: "#f5f5f7", fontWeight: 600 }}>
              Remote HPC workspace
            </h2>
            <p style={{ fontSize: "0.85rem", color: "#86868b", margin: 0, maxWidth: "34rem" }}>
              Connect your own cluster over SSH. Sign-in uses your own ssh-agent and
              <code style={{ margin: "0 0.25rem" }}>~/.ssh/config</code> — Coperceive never asks for
              a password or a private key.
            </p>
          </div>
          <button type="button" onClick={startAdd} style={primaryButton}>
            Add server
          </button>
        </div>
      </div>

      {error ? (
        <div
          role="alert"
          style={{
            marginBottom: "1rem",
            padding: "0.6rem 0.8rem",
            borderRadius: "8px",
            backgroundColor: "rgba(255, 98, 89, 0.12)",
            border: "1px solid rgba(255, 98, 89, 0.35)",
            color: "#ff6259",
            fontSize: "0.82rem",
          }}
        >
          {error}
        </div>
      ) : null}
      {note ? (
        <div
          role="status"
          style={{
            marginBottom: "1rem",
            padding: "0.6rem 0.8rem",
            borderRadius: "8px",
            backgroundColor: "rgba(63, 185, 80, 0.12)",
            border: "1px solid rgba(63, 185, 80, 0.35)",
            color: "#7ee787",
            fontSize: "0.82rem",
          }}
        >
          {note}
        </div>
      ) : null}

      {profiles.length === 0 && !draft ? (
        <div style={{ ...card, textAlign: "center", padding: "2.5rem 1.25rem" }}>
          <p style={{ color: "#8b949e", fontSize: "0.9rem", margin: "0 0 1rem" }}>
            No servers yet. Add your cluster to see its partitions, GPUs and environment.
          </p>
          <button type="button" onClick={startAdd} style={primaryButton}>
            Add your first server
          </button>
        </div>
      ) : null}

      {profiles.map((entry) => {
        const cardReport = cardReports[entry.id] ?? null;
        return (
          <div key={entry.id} style={{ ...card, marginBottom: "0.75rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "center" }}>
              <div>
                <div style={{ color: "#f5f5f7", fontWeight: 600, fontSize: "1rem" }}>
                  {entry.profile.name}
                </div>
                <div style={{ color: "#8b949e", fontSize: "0.8rem", marginTop: "0.15rem" }}>
                  {profileSummary(entry)}
                </div>
                <div style={{ color: "#8b949e", fontSize: "0.75rem", marginTop: "0.35rem" }}>
                  {cardReport
                    ? reportSummary(cardReport)
                    : entry.discovered
                      ? `Last checked: ${entry.discovered.partitions.length} partition(s) found.`
                      : "Not tested yet."}
                </div>
              </div>
              <div style={{ display: "flex", gap: "0.4rem", flexShrink: 0 }}>
                <button
                  type="button"
                  style={secondaryButton}
                  disabled={busy !== null || entry.profile.data_roots.length === 0}
                  title={
                    entry.profile.data_roots.length === 0
                      ? "Add a data folder to this server first."
                      : "Browse the data folders on this server (metadata only)."
                  }
                  onClick={() => setBrowseProfile(entry)}
                >
                  Browse data
                </button>
                <button
                  type="button"
                  style={secondaryButton}
                  disabled={busy !== null}
                  onClick={() => {
                    setDraft(entry);
                    setReport(null);
                    setError("");
                    setNote("");
                  }}
                >
                  Edit
                </button>
                <button
                  type="button"
                  style={secondaryButton}
                  disabled={busy !== null}
                  onClick={() => void runTest(entry, true)}
                >
                  {busy === "card" ? "Testing…" : "Test connection"}
                </button>
                <button
                  type="button"
                  style={{ ...secondaryButton, color: "#ff7b72" }}
                  onClick={() => void remove(entry)}
                >
                  Remove
                </button>
              </div>
            </div>
            {cardReport ? (
              <div style={{ marginTop: "0.6rem", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "0.4rem" }}>
                {cardReport.checks.map((check) => (
                  <CheckRow
                    key={check.name}
                    ok={check.ok}
                    label={check.label}
                    detail={check.detail}
                    hint={check.hint}
                  />
                ))}
              </div>
            ) : null}
          </div>
        );
      })}

      {browseProfile ? (
        <HpcBrowse profile={browseProfile} onClose={() => setBrowseProfile(null)} />
      ) : null}

      {draft ? (
        <div style={{ ...card, marginTop: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
            <h3 style={{ margin: 0, fontSize: "1.05rem", color: "#f5f5f7" }}>
              {draft.id ? "Edit server" : "Add a server"}
            </h3>
            <button type="button" style={secondaryButton} onClick={closeEditor}>
              Close
            </button>
          </div>

          <div style={field}>
            <label style={labelStyle} htmlFor="hpc-name">
              Server name
            </label>
            <input
              id="hpc-name"
              style={input}
              value={draft.profile.name}
              placeholder="My cluster"
              onChange={(event) => updateProfile({ name: event.target.value })}
            />
          </div>

          <div style={field}>
            <label style={labelStyle} htmlFor="hpc-alias">
              Host from ~/.ssh/config
            </label>
            <select
              id="hpc-alias"
              style={input}
              value={draft.profile.ssh_alias ?? ""}
              onChange={(event) => void chooseAlias(event.target.value)}
            >
              <option value="">Type a host manually instead</option>
              {hosts.map((host) => (
                <option key={host} value={host}>
                  {host}
                </option>
              ))}
            </select>
            {draft.ssh ? (
              <p style={{ color: "#8b949e", fontSize: "0.75rem", margin: "0.3rem 0 0" }}>
                Read from your SSH config: {draft.ssh.hostname ?? draft.ssh.alias}
                {draft.ssh.user ? ` as ${draft.ssh.user}` : ""}
                {draft.ssh.port ? ` on port ${draft.ssh.port}` : ""}
                {draft.ssh.proxy_jump ? ` through ${draft.ssh.proxy_jump}` : ""}.
              </p>
            ) : null}
          </div>

          {!draft.profile.ssh_alias ? (
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <div style={{ ...field, flex: 2 }}>
                <label style={labelStyle} htmlFor="hpc-host">
                  Host name
                </label>
                <input
                  id="hpc-host"
                  style={input}
                  value={draft.profile.host ?? ""}
                  placeholder="cluster.example.org"
                  onChange={(event) => updateProfile({ host: event.target.value || null })}
                />
              </div>
              <div style={{ ...field, flex: 1 }}>
                <label style={labelStyle} htmlFor="hpc-user">
                  User name (optional)
                </label>
                <input
                  id="hpc-user"
                  style={input}
                  value={draft.profile.user ?? ""}
                  placeholder="your account"
                  onChange={(event) => updateProfile({ user: event.target.value || null })}
                />
              </div>
            </div>
          ) : null}

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-data-root">
                Data folder on the cluster
              </label>
              <input
                id="hpc-data-root"
                style={input}
                value={draft.profile.data_roots[0] ?? ""}
                placeholder="/path/your/account/owns"
                onChange={(event) =>
                  updateProfile({ data_roots: event.target.value ? [event.target.value] : [] })
                }
              />
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-scratch">
                Per-user scratch folder
              </label>
              <input
                id="hpc-scratch"
                style={input}
                value={draft.profile.scratch_template}
                placeholder="/scratch/$USER"
                onChange={(event) => updateProfile({ scratch_template: event.target.value })}
              />
            </div>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-results-root">
                Results folder (shared storage)
              </label>
              <input
                id="hpc-results-root"
                style={input}
                value={draft.profile.results_root}
                placeholder="/project/your-account/runs"
                onChange={(event) => updateProfile({ results_root: event.target.value })}
              />
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-code-roots">
                Pipeline code folders (optional, comma separated)
              </label>
              <input
                id="hpc-code-roots"
                style={input}
                value={draft.profile.code_roots.join(", ")}
                placeholder="/project/your-account/code"
                onChange={(event) =>
                  updateProfile({ code_roots: splitFolderList(event.target.value) })
                }
              />
            </div>
          </div>

          <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", margin: "0.5rem 0 0.75rem" }} />

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-scheduler">
                Scheduler
              </label>
              <select
                id="hpc-scheduler"
                style={input}
                value={draft.profile.scheduler}
                onChange={(event) =>
                  updateProfile({ scheduler: event.target.value as SchedulerKind })
                }
              >
                <option value="slurm">Slurm</option>
                <option value="none">No scheduler (files only)</option>
              </select>
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-partition">
                Partition
              </label>
              <input
                id="hpc-partition"
                list="hpc-partition-choices"
                style={input}
                disabled={draft.profile.scheduler !== "slurm"}
                value={draft.profile.defaults.partition ?? ""}
                placeholder="choose or type a partition"
                onChange={(event) => updateDefaults({ partition: event.target.value || null })}
              />
              <datalist id="hpc-partition-choices">
                {partitionChoices.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-gpu">
                GPU type
              </label>
              <select
                id="hpc-gpu"
                style={input}
                value={selectedGpu}
                disabled={gpuChoices.length === 0}
                onChange={(event) => {
                  const type = event.target.value;
                  updateDefaults({
                    gres: type ? (type === "gpu" ? "gpu:1" : `gpu:${type}:1`) : null,
                  });
                }}
              >
                <option value="">{gpuChoices.length === 0 ? "no GPUs detected" : "no GPU"}</option>
                {gpuChoices.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-time">
                Walltime
              </label>
              <input
                id="hpc-time"
                style={input}
                value={draft.profile.defaults.time_limit ?? ""}
                placeholder="from the cluster, e.g. 2-00:00:00"
                onChange={(event) => updateDefaults({ time_limit: event.target.value || null })}
              />
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-cpus">
                CPUs per task
              </label>
              <input
                id="hpc-cpus"
                type="number"
                min={1}
                style={input}
                value={draft.profile.defaults.cpus_per_task ?? ""}
                placeholder="from the profile"
                onChange={(event) =>
                  updateDefaults({
                    cpus_per_task: event.target.value ? Number(event.target.value) : null,
                  })
                }
              />
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-mem">
                Memory
              </label>
              <input
                id="hpc-mem"
                style={input}
                value={draft.profile.defaults.memory ?? ""}
                placeholder="e.g. 16G"
                onChange={(event) => updateDefaults({ memory: event.target.value || null })}
              />
            </div>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-env-kind">
                Environment on compute nodes
              </label>
              <select
                id="hpc-env-kind"
                style={input}
                value={draft.profile.environment.kind}
                onChange={(event) =>
                  updateEnvironment({ kind: event.target.value as EnvironmentKind })
                }
              >
                {(["none", "modules", "conda", "venv", "container"] as EnvironmentKind[]).map(
                  (kind) => (
                    <option key={kind} value={kind}>
                      {environmentLabel(kind)}
                    </option>
                  ),
                )}
              </select>
            </div>
            <div style={{ ...field, flex: 2 }}>
              <label style={labelStyle} htmlFor="hpc-interpreter">
                Python interpreter
              </label>
              <input
                id="hpc-interpreter"
                style={input}
                value={draft.profile.environment.interpreter ?? ""}
                placeholder="/path/to/python"
                onChange={(event) => updateEnvironment({ interpreter: event.target.value || null })}
              />
            </div>
          </div>

          {draft.profile.environment.kind === "modules" ? (
            <div style={field}>
              <label style={labelStyle} htmlFor="hpc-modules">
                Modules to load (comma separated)
              </label>
              <input
                id="hpc-modules"
                style={input}
                value={draft.profile.environment.modules.join(", ")}
                placeholder="python/3.12"
                onChange={(event) =>
                  updateEnvironment({
                    modules: event.target.value
                      .split(",")
                      .map((module) => module.trim())
                      .filter(Boolean),
                  })
                }
              />
            </div>
          ) : null}

          {draft.profile.environment.kind === "container" ? (
            <div style={field}>
              <label style={labelStyle} htmlFor="hpc-image">
                Container image
              </label>
              <input
                id="hpc-image"
                style={input}
                value={draft.profile.environment.container_image ?? ""}
                placeholder="/path/to/coperceive.sif"
                onChange={(event) =>
                  updateEnvironment({ container_image: event.target.value || null })
                }
              />
            </div>
          ) : null}

          <div style={field}>
            <label style={labelStyle} htmlFor="hpc-wheelhouse">
              Pre-downloaded wheels folder (optional)
            </label>
            <input
              id="hpc-wheelhouse"
              style={input}
              value={draft.profile.environment.wheelhouse ?? ""}
              placeholder="/path/to/wheelhouse"
              onChange={(event) => updateEnvironment({ wheelhouse: event.target.value || null })}
            />
          </div>

          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.82rem", color: "#c9d1d9", marginBottom: "0.75rem" }}>
            <input
              type="checkbox"
              checked={draft.profile.environment.compute_has_internet}
              onChange={(event) =>
                updateEnvironment({ compute_has_internet: event.target.checked })
              }
            />
            Compute nodes on this server can download packages. Leave this off unless you know they
            can; Coperceive never installs anything on offline nodes.
          </label>

          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
            <button
              type="button"
              style={primaryButton}
              disabled={busy !== null}
              onClick={() => void runTest(draft, false)}
            >
              {busy === "test" ? "Testing… (up to 1 minute)" : "Test connection"}
            </button>
            <button type="button" style={secondaryButton} disabled={busy !== null} onClick={() => void save()}>
              {busy === "save" ? "Saving…" : "Save server"}
            </button>
          </div>

          {report ? (
            <div style={{ marginTop: "1rem", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "0.75rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                <h4 style={{ margin: 0, fontSize: "0.9rem", color: "#f5f5f7" }}>Connection test</h4>
                <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>{reportSummary(report)}</span>
              </div>
              {report.checks.map((check) => (
                <CheckRow
                  key={check.name}
                  ok={check.ok}
                  label={check.label}
                  detail={check.detail}
                  hint={check.hint}
                />
              ))}

              {report.partitions.length > 0 ? (
                <div style={{ marginTop: "0.6rem" }}>
                  <div style={{ ...labelStyle, marginBottom: "0.3rem" }}>Partitions offered</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
                    {report.partitions.map((partition) => (
                      <button
                        key={partition.name}
                        type="button"
                        onClick={() =>
                          updateDefaults({
                            partition: partition.name,
                            gres: partition.gpu_types[0]
                              ? partition.gpu_types[0] === "gpu"
                                ? "gpu:1"
                                : `gpu:${partition.gpu_types[0]}:1`
                              : (partition.gres ?? null),
                          })
                        }
                        style={{
                          padding: "0.3rem 0.55rem",
                          borderRadius: "999px",
                          fontSize: "0.75rem",
                          cursor: "pointer",
                          border:
                            draft.profile.defaults.partition === partition.name
                              ? "1px solid #1f6feb"
                              : "1px solid rgba(255,255,255,0.18)",
                          backgroundColor:
                            draft.profile.defaults.partition === partition.name
                              ? "rgba(31,111,235,0.25)"
                              : "transparent",
                          color: "#f5f5f7",
                        }}
                      >
                        {partition.name}
                        {partition.gpu_types.length > 0 ? ` · ${partition.gpu_types.join(", ")}` : ""}
                        {partition.max_time ? ` · max ${partition.max_time}` : ""}
                        {partition.nodes ? ` · ${partition.nodes} nodes` : ""}
                        {partition.state === "down" ? " · unavailable" : ""}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {report.scratch.writable ? (
                <p style={{ fontSize: "0.8rem", color: "#8b949e", marginTop: "0.5rem" }}>
                  Scratch: {report.scratch.path} — {formatBytes(report.scratch.free_bytes)} free
                  {report.scratch.quota ? ` · quota: ${report.scratch.quota.split("\n")[0]}` : ""}
                </p>
              ) : null}

              {missingExplanations(report).length > 0 ? (
                <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.1rem", color: "#8b949e", fontSize: "0.8rem" }}>
                  {missingExplanations(report).map((text) => (
                    <li key={text} style={{ marginBottom: "0.25rem" }}>
                      {text}
                    </li>
                  ))}
                </ul>
              ) : null}

              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
                <button type="button" style={secondaryButton} onClick={applyDetected}>
                  Use detected settings
                </button>
              </div>
              <p style={{ fontSize: "0.75rem", color: "#8b949e", marginTop: "0.5rem" }}>
                Detected values are suggestions. Nothing is submitted without your confirmation,
                and every job script is shown in full first.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
