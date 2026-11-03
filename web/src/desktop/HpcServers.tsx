// SPDX-License-Identifier: Apache-2.0
/**
 * Remote HPC workspace: add a server, test the connection, review what was
 * detected, and override anything. Sign-in uses the user's own ssh-agent and
 * ~/.ssh/config; there is no password or key field anywhere.
 */
import { useEffect, useRef, useState } from "react";
import PanelState from "./PanelState";
import { PANEL_DEFINITIONS } from "./panelStates";
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
  groupPartitions,
  gpuTypesFor,
  missingExplanations,
  partitionNames,
  profileSummary,
  reportSummary,
  validateConnectionShape,
  validateDraft,
  type DetectionReport,
  type EnvironmentKind,
  type SchedulerKind,
  type ServerEntry,
  splitFolderList,
} from "./hpc";
import { errorText } from "./errors";

const input = {
  width: "100%",
  boxSizing: "border-box" as const,
  padding: "5px 9px",
  fontSize: "12.5px",
  borderRadius: "6px",
  border: "1px solid var(--line-elevated, rgba(255, 255, 255, 0.14))",
  backgroundColor: "var(--sunken)",
  color: "var(--text)",
};

const labelStyle = {
  display: "block",
  fontSize: "11px",
  fontWeight: 500,
  color: "var(--text-subtle)",
  marginBottom: "4px",
} as const;

const field = { marginBottom: "10px" } as const;

const primaryButton = {
  padding: "6px 14px",
  borderRadius: "6px",
  border: "none",
  backgroundColor: "var(--primary, #1f6feb)",
  color: "#fff",
  fontSize: "13px",
  fontWeight: 500,
  cursor: "pointer",
} as const;

const secondaryButton = {
  padding: "4px 10px",
  borderRadius: "5px",
  border: "1px solid var(--line-elevated, rgba(255, 255, 255, 0.14))",
  backgroundColor: "transparent",
  color: "var(--text)",
  fontSize: "12px",
  fontWeight: 500,
  cursor: "pointer",
  whiteSpace: "nowrap" as const,
} as const;

function CheckRow({
  ok,
  label,
  detail,
  hint,
}: {
  ok: boolean;
  label: string;
  detail: string;
  hint?: string | null;
}) {
  return (
    <div style={{ display: "flex", gap: "8px", padding: "4px 0", fontSize: "12px" }}>
      <span aria-hidden style={{ color: ok ? "var(--ok)" : "var(--vehicle)", flex: "0 0 auto" }}>
        {ok ? "✓" : "!"}
      </span>
      <div>
        <span style={{ color: ok ? "var(--text)" : "var(--vehicle)", fontWeight: 500 }}>{label}</span>
        <span style={{ color: "var(--text-tertiary)" }}> — {detail}</span>
        {!ok && hint ? (
          <div style={{ color: "var(--text-subtle)", marginTop: "2px" }}>{hint}</div>
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
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [browseProfile, setBrowseProfile] = useState<ServerEntry | null>(null);
  const formErrorRef = useRef<HTMLDivElement | null>(null);

  const updateProfilesList = (next: ServerEntry[]) => {
    setProfiles(next);
    window.dispatchEvent(
      new CustomEvent("coperceive-hpc-profiles-updated", { detail: { profiles: next } }),
    );
  };

  const refreshServers = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [loadedProfiles, loadedHosts] = await Promise.all([
        listServerProfiles(),
        listSshConfigHosts(),
      ]);
      updateProfilesList(loadedProfiles);
      setHosts(loadedHosts);
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refreshServers();
  }, []);

  useEffect(() => {
    const handleSeed = (event: Event) => {
      const custom = event as CustomEvent<{ profiles?: ServerEntry[]; hosts?: string[] }>;
      if (custom.detail?.profiles !== undefined) {
        updateProfilesList(custom.detail.profiles);
        setLoading(false);
        setLoadError(null);
      }
      if (custom.detail?.hosts) {
        setHosts(custom.detail.hosts);
      }
    };
    const handleAddServer = () => {
      setDraft(emptyEntry());
      setReport(null);
      setError("");
      setNote("");
    };
    const handleBrowseServer = (event: Event) => {
      const custom = event as CustomEvent<{ profile?: ServerEntry }>;
      if (custom.detail?.profile) {
        setBrowseProfile(custom.detail.profile);
      }
    };
    window.addEventListener("coperceive-test-hpc-servers-seed", handleSeed);
    window.addEventListener("coperceive-hpc-add-server", handleAddServer);
    window.addEventListener("coperceive-hpc-browse-server", handleBrowseServer);
    return () => {
      window.removeEventListener("coperceive-test-hpc-servers-seed", handleSeed);
      window.removeEventListener("coperceive-hpc-add-server", handleAddServer);
      window.removeEventListener("coperceive-hpc-browse-server", handleBrowseServer);
    };
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
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const applyDetected = () => {
    if (!draft || !report) return;
    setDraft(applyDiscovery(draft, report));
    setNote(
      "Detected settings were applied where you had not chosen a value. You can change anything below.",
    );
  };

  const save = async () => {
    if (!draft) return;
    const validation = validateDraft(draft);
    if (validation.length > 0) {
      setError(validation.join(" "));
      setNote("");
      requestAnimationFrame(() => formErrorRef.current?.scrollIntoView({ block: "center" }));
      return;
    }
    setError("");
    setNote("");
    setBusy("save");
    try {
      const saved = await saveServerProfile(draft);
      updateProfilesList(await listServerProfiles());
      setDraft(saved);
      setNote(`Saved “${saved.profile.name}”.`);
    } catch (e) {
      setError(errorText(e));
      setNote("");
      requestAnimationFrame(() => formErrorRef.current?.scrollIntoView({ block: "center" }));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (entry: ServerEntry) => {
    if (
      !window.confirm(
        `Remove the server “${entry.profile.name}”? Saved data folders are not touched.`,
      )
    ) {
      return;
    }
    try {
      await deleteServerProfile(entry.id);
      updateProfilesList(await listServerProfiles());
      if (draft?.id === entry.id) setDraft(null);
    } catch (e) {
      setError(errorText(e));
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
    ...new Set([
      ...partitionNames(draft ?? emptyEntry()),
      ...discoveredPartitions.map((p) => p.name),
    ]),
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
    <div style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 52px)", fontSize: "13px" }}>
      {/* Compact Page Header */}
      <section
        style={{
          padding: "18px 24px 14px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: "16px",
            alignItems: "flex-start",
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <div style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
              HPC workspace / servers
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
              <h2
                style={{
                  fontSize: "20px",
                  margin: 0,
                  color: "var(--text)",
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                }}
              >
                Remote HPC workspace
              </h2>
              <span style={{ fontSize: "12.5px", color: "var(--text-tertiary)" }}>
                Connect your own cluster over SSH using ssh-agent and <code>~/.ssh/config</code> — never asks for a password or private key.
              </span>
            </div>
          </div>
          <button type="button" onClick={startAdd} style={primaryButton}>
            Add server
          </button>
        </div>

        {error && !draft ? (
          <div
            role="alert"
            style={{
              padding: "8px 12px",
              borderRadius: "6px",
              backgroundColor: "rgba(248, 81, 73, 0.12)",
              border: "1px solid rgba(248, 81, 73, 0.35)",
              color: "var(--danger)",
              fontSize: "12px",
            }}
          >
            {error}
          </div>
        ) : null}
        {note ? (
          <div
            role="status"
            style={{
              padding: "8px 12px",
              borderRadius: "6px",
              backgroundColor: "rgba(63, 185, 80, 0.12)",
              border: "1px solid rgba(63, 185, 80, 0.35)",
              color: "var(--ok)",
              fontSize: "12px",
            }}
          >
            {note}
          </div>
        ) : null}
      </section>

      {loading && !draft ? (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="loading"
            title={PANEL_DEFINITIONS.hpc_workspace.loading.title}
            description={PANEL_DEFINITIONS.hpc_workspace.loading.description}
          />
        </div>
      ) : null}

      {!loading && loadError && !draft ? (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="error"
            title={PANEL_DEFINITIONS.hpc_workspace.error.title}
            description={PANEL_DEFINITIONS.hpc_workspace.error.description}
            actionLabel={PANEL_DEFINITIONS.hpc_workspace.error.actionLabel}
            onAction={() => void refreshServers()}
          />
        </div>
      ) : null}

      {!loading && !loadError && profiles.length === 0 && !draft ? (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="empty"
            title={PANEL_DEFINITIONS.hpc_workspace.empty.title}
            description={PANEL_DEFINITIONS.hpc_workspace.empty.description}
            actionLabel={PANEL_DEFINITIONS.hpc_workspace.empty.actionLabel}
            onAction={startAdd}
          />
        </div>
      ) : null}

      {/* Dense Server Profiles Table */}
      {profiles.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <div style={{ minWidth: "760px" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns:
                  "minmax(130px, 1.1fr) minmax(160px, 1.3fr) minmax(150px, 1.2fr) minmax(140px, 1.1fr) 310px",
                alignItems: "center",
                gap: "12px",
                padding: "0 24px",
                height: "30px",
                backgroundColor: "var(--panel)",
                borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
                fontSize: "11px",
                color: "var(--text-subtle)",
              }}
            >
              <span>Server</span>
              <span>Connection · Scheduler</span>
              <span>Data & results roots</span>
              <span>Status</span>
              <span style={{ textAlign: "right" }}>Actions</span>
            </div>

            {profiles.map((entry) => {
              const cardReport = cardReports[entry.id] ?? null;
              const isHealthy = Boolean(cardReport?.reachable ?? entry.discovered);
              return (
                <div
                  key={entry.id}
                  className="c-table-row"
                  style={{
                    borderBottom: "1px solid var(--line-subtle, rgba(255,255,255,0.04))",
                  }}
                >
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "minmax(130px, 1.1fr) minmax(160px, 1.3fr) minmax(150px, 1.2fr) minmax(140px, 1.1fr) 310px",
                      alignItems: "center",
                      gap: "12px",
                      padding: "8px 24px",
                      minHeight: "42px",
                    }}
                  >
                    {/* Server Name + Dot */}
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
                      <span
                        style={{
                          width: "7px",
                          height: "7px",
                          borderRadius: "50%",
                          backgroundColor: isHealthy ? "#3fb950" : "#8e8e93",
                          flexShrink: 0,
                        }}
                      />
                      <span
                        style={{
                          color: "var(--text)",
                          fontWeight: 500,
                          fontSize: "13px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {entry.profile.name}
                      </span>
                    </div>

                    {/* Summary */}
                    <span
                      style={{
                        color: "var(--text-secondary)",
                        fontSize: "12.5px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {profileSummary(entry)}
                    </span>

                    {/* Roots */}
                    <span
                      style={{
                        fontFamily: "'Geist Mono', ui-monospace, monospace",
                        fontSize: "11.5px",
                        color: "var(--text-tertiary)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {entry.profile.data_roots[0] || "—"}
                    </span>

                    {/* Status */}
                    <span
                      style={{
                        color: isHealthy ? "var(--text-secondary)" : "var(--text-subtle)",
                        fontSize: "12px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {cardReport
                        ? reportSummary(cardReport)
                        : entry.discovered
                          ? `Last checked: ${entry.discovered.partitions.length} partition(s) found.`
                          : "Not tested yet."}
                    </span>

                    {/* Actions */}
                    <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end", flexShrink: 0 }}>
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
                        style={{ ...secondaryButton, color: "var(--danger)" }}
                        onClick={() => void remove(entry)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>

                  {cardReport ? (
                    <div
                      style={{
                        padding: "6px 24px 10px 39px",
                        borderTop: "1px solid var(--line-subtle)",
                        backgroundColor: "var(--sunken)",
                      }}
                    >
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
          </div>
        </div>
      )}

      {browseProfile ? (
        <div style={{ padding: "16px 24px" }}>
          <HpcBrowse profile={browseProfile} onClose={() => setBrowseProfile(null)} />
        </div>
      ) : null}

      {/* Compact Inline Server Editor */}
      {draft ? (
        <div
          style={{
            margin: "16px 24px 32px",
            backgroundColor: "var(--panel)",
            border: "1px solid var(--line, rgba(255, 255, 255, 0.08))",
            borderRadius: "8px",
            padding: "16px 20px",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "14px",
              paddingBottom: "10px",
              borderBottom: "1px solid var(--line-subtle)",
            }}
          >
            <h3 style={{ margin: 0, fontSize: "14px", fontWeight: 600, color: "var(--text)" }}>
              {draft.id ? "Edit server" : "Add a server"}
            </h3>
            <button type="button" style={secondaryButton} onClick={closeEditor}>
              Close
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            <div style={field}>
              <label style={labelStyle} htmlFor="hpc-name">
                Server name
              </label>
              <input
                id="hpc-name"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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
              {draft.ssh &&
              (draft.ssh.hostname ||
                draft.ssh.alias ||
                draft.ssh.user ||
                draft.ssh.port ||
                draft.ssh.proxy_jump) ? (
                <p style={{ color: "var(--text-subtle)", fontSize: "11.5px", margin: "4px 0 0" }}>
                  Read from your SSH config: {draft.ssh.hostname ?? draft.ssh.alias}
                  {draft.ssh.user ? ` as ${draft.ssh.user}` : ""}
                  {draft.ssh.port ? ` on port ${draft.ssh.port}` : ""}
                  {draft.ssh.proxy_jump ? ` through ${draft.ssh.proxy_jump}` : ""}.
                </p>
              ) : null}
            </div>
          </div>

          {!draft.profile.ssh_alias ? (
            <div style={{ display: "flex", gap: "10px" }}>
              <div style={{ ...field, flex: 2 }}>
                <label style={labelStyle} htmlFor="hpc-host">
                  Host name
                </label>
                <input
                  id="hpc-host"
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  autoComplete="off"
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
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  autoComplete="off"
                  style={input}
                  value={draft.profile.user ?? ""}
                  placeholder="your account"
                  onChange={(event) => updateProfile({ user: event.target.value || null })}
                />
              </div>
            </div>
          ) : null}

          <div style={{ display: "flex", gap: "10px" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-data-root">
                Data folder on the cluster
              </label>
              <input
                id="hpc-data-root"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
                style={input}
                value={draft.profile.scratch_template}
                placeholder="your cluster's scratch path, for example /scratch/$USER"
                onChange={(event) => updateProfile({ scratch_template: event.target.value })}
              />
              {!draft.profile.scratch_template.trim() ? (
                <p style={{ color: "var(--text-subtle)", fontSize: "11px", margin: "4px 0 0" }}>
                  This is a hint, not a saved value. Enter the per-user scratch folder your cluster gives you.
                </p>
              ) : null}
            </div>
          </div>

          <div style={{ display: "flex", gap: "10px" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-results-root">
                Shared results folder
              </label>
              <input
                id="hpc-results-root"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
                style={input}
                value={draft.profile.results_root}
                placeholder="/path/your/account/can/write (not scratch)"
                onChange={(event) => updateProfile({ results_root: event.target.value })}
              />
              <p style={{ color: "var(--text-subtle)", fontSize: "11px", margin: "4px 0 0" }}>
                Run outputs and predictions are written here, on shared storage the login node can read. Coperceive never puts outputs in scratch.
              </p>
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-code-roots">
                Pipeline code folders (optional, comma separated)
              </label>
              <input
                id="hpc-code-roots"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
                style={input}
                value={draft.profile.code_roots.join(", ")}
                placeholder="/project/you/code, /project/you/tools"
                onChange={(event) =>
                  updateProfile({ code_roots: splitFolderList(event.target.value) })
                }
              />
            </div>
          </div>

          <div style={{ borderTop: "1px solid var(--line-subtle)", margin: "6px 0 10px" }} />

          <div style={{ display: "flex", gap: "10px" }}>
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
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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

          <div style={{ display: "flex", gap: "10px" }}>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-time">
                Walltime
              </label>
              <input
                id="hpc-time"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
                style={input}
                value={draft.profile.defaults.time_limit ?? ""}
                placeholder="from the cluster, e.g. 2-00:00:00"
                onChange={(event) => updateDefaults({ time_limit: event.target.value || null })}
              />
              {!draft.profile.defaults.time_limit ? (
                <p style={{ color: "var(--text-subtle)", fontSize: "11px", margin: "4px 0 0" }}>
                  Leave empty for the partition default. Use HH:MM:SS or D-HH:MM:SS; discovery does not copy “infinite”.
                </p>
              ) : null}
            </div>
            <div style={{ ...field, flex: 1 }}>
              <label style={labelStyle} htmlFor="hpc-cpus">
                CPUs per task
              </label>
              <input
                id="hpc-cpus"
                type="number"
                min={1}
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
                style={input}
                value={draft.profile.defaults.memory ?? ""}
                placeholder="e.g. 16G"
                onChange={(event) => updateDefaults({ memory: event.target.value || null })}
              />
            </div>
          </div>

          <div style={{ display: "flex", gap: "10px" }}>
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
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
                style={input}
                value={draft.profile.environment.interpreter ?? ""}
                placeholder="/path/to/python"
                onChange={(event) => updateEnvironment({ interpreter: event.target.value || null })}
              />
              <p style={{ color: "var(--text-subtle)", fontSize: "11px", margin: "4px 0 0" }}>
                Runs:{" "}
                {draft.profile.environment.coperceive_command.length > 0
                  ? draft.profile.environment.coperceive_command.join(" ")
                  : "(no coperceive command yet)"}
              </p>
            </div>
          </div>

          {draft.profile.environment.kind === "modules" ? (
            <div style={field}>
              <label style={labelStyle} htmlFor="hpc-modules">
                Modules to load (comma separated)
              </label>
              <input
                id="hpc-modules"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoComplete="off"
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
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              autoComplete="off"
              style={input}
              value={draft.profile.environment.wheelhouse ?? ""}
              placeholder="/path/to/wheelhouse"
              onChange={(event) => updateEnvironment({ wheelhouse: event.target.value || null })}
            />
          </div>

          <label
            style={{
              display: "flex",
              gap: "8px",
              alignItems: "center",
              fontSize: "12.5px",
              color: "var(--text-secondary)",
              marginBottom: "12px",
            }}
          >
            <input
              type="checkbox"
              checked={draft.profile.environment.compute_has_internet}
              onChange={(event) =>
                updateEnvironment({ compute_has_internet: event.target.checked })
              }
            />
            Compute nodes on this server can download packages. Leave this off unless you know they can; Coperceive never installs anything on offline nodes.
          </label>

          {error ? (
            <div
              ref={formErrorRef}
              role="alert"
              style={{
                marginTop: "8px",
                padding: "8px 10px",
                borderRadius: "6px",
                backgroundColor: "rgba(248, 81, 73, 0.12)",
                border: "1px solid rgba(248, 81, 73, 0.35)",
                color: "var(--danger)",
                fontSize: "12px",
              }}
            >
              {error}
            </div>
          ) : null}

          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button
              type="button"
              style={primaryButton}
              disabled={busy !== null}
              onClick={() => void runTest(draft, false)}
            >
              {busy === "test" ? "Testing… (up to 1 minute)" : "Test connection"}
            </button>
            <button
              type="button"
              style={secondaryButton}
              disabled={busy !== null}
              onClick={() => void save()}
            >
              {busy === "save" ? "Saving…" : "Save server"}
            </button>
          </div>

          {report ? (
            <div
              style={{
                marginTop: "14px",
                borderTop: "1px solid var(--line-subtle)",
                paddingTop: "12px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "6px",
                }}
              >
                <h4 style={{ margin: 0, fontSize: "13px", color: "var(--text)" }}>
                  Connection test
                </h4>
                <span style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
                  {reportSummary(report)}
                </span>
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
                <div style={{ marginTop: "10px" }}>
                  <div style={{ ...labelStyle, marginBottom: "6px" }}>Partitions offered</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {groupPartitions(report.partitions).map((partition) => (
                      <button
                        key={partition.name}
                        type="button"
                        onClick={() =>
                          updateDefaults({
                            partition: partition.name,
                            gres: partition.gpuTypes[0]
                              ? partition.gpuTypes[0] === "gpu"
                                ? "gpu:1"
                                : `gpu:${partition.gpuTypes[0]}:1`
                              : (partition.gres ?? null),
                          })
                        }
                        style={{
                          padding: "4px 10px",
                          borderRadius: "999px",
                          fontSize: "11.5px",
                          cursor: "pointer",
                          border:
                            draft.profile.defaults.partition === partition.name
                              ? "1px solid #1f6feb"
                              : "1px solid var(--line-elevated)",
                          backgroundColor:
                            draft.profile.defaults.partition === partition.name
                              ? "rgba(31,111,235,0.22)"
                              : "transparent",
                          color: "var(--text)",
                        }}
                      >
                        <span>{partition.name}</span>
                        {partition.gpuTypes.length > 0 ? (
                          <span style={{ marginLeft: "6px", opacity: 0.85 }}>
                            {partition.gpuTypes.map((gpu) => (
                              <span
                                key={gpu}
                                style={{
                                  marginLeft: "4px",
                                  padding: "0 5px",
                                  borderRadius: "999px",
                                  backgroundColor: "var(--line)",
                                  fontSize: "10.5px",
                                }}
                              >
                                {gpu}
                              </span>
                            ))}
                          </span>
                        ) : null}
                        {partition.maxTime ? ` · max ${partition.maxTime}` : ""}
                        {partition.nodes ? ` · ${partition.nodes} nodes` : ""}
                        {partition.down ? " · unavailable" : ""}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {report.scratch.writable ? (
                <p style={{ fontSize: "12px", color: "var(--text-tertiary)", marginTop: "8px" }}>
                  Scratch: {report.scratch.path} — {formatBytes(report.scratch.free_bytes)} free
                  {report.scratch.quota ? ` · quota: ${report.scratch.quota.split("\n")[0]}` : ""}
                </p>
              ) : null}

              {missingExplanations(report).length > 0 ? (
                <ul
                  style={{
                    margin: "8px 0 0",
                    paddingLeft: "18px",
                    color: "var(--text-tertiary)",
                    fontSize: "12px",
                  }}
                >
                  {missingExplanations(report).map((text) => (
                    <li key={text} style={{ marginBottom: "4px" }}>
                      {text}
                    </li>
                  ))}
                </ul>
              ) : null}

              <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                <button type="button" style={secondaryButton} onClick={applyDetected}>
                  Use detected settings
                </button>
              </div>
              <p style={{ fontSize: "11.5px", color: "var(--text-subtle)", marginTop: "8px" }}>
                Detected values are suggestions. Nothing is submitted without your confirmation, and every job script is shown in full first.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
