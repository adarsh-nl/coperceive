// SPDX-License-Identifier: Apache-2.0
/** Remote HPC 6/6: the run history with cancel, re-run and open predictions. */
import { useEffect, useRef, useState } from "react";
import {
  cancelRunJob,
  listRunHistory,
  listServerProfiles,
  openPredictions,
  pickSavePath,
  previewReproCard,
  previewSubmission,
  revealInFinder,
  saveExportedFile,
  submitRunPlan,
} from "./local";
import type { ServerEntry } from "./hpc";
import {
  historyCanRerun,
  historyStateLabel,
  reproPreviewLines,
  slurmSeconds,
  type JobSubmitOutcome,
  type RunHistoryEntry,
  type SubmissionPreview,
  type SubmitOptions,
} from "./runPlan";
import { errorText } from "./errors";
import {
  Facts,
  GridTable,
  InspectorSection,
  MiniBar,
  PageLayout,
  PageToolbar,
  useDefaultSelection,
  usePageHeader,
  type GridColumn,
} from "./components/Page";

const rowKey = (entry: RunHistoryEntry) => entry.job_id;

const TEST_REPRO_ENTRY: RunHistoryEntry = {
  job_id: "1001",
  plan_id: "plan-1790000000-aabbccddeeff",
  state: "completed",
  active: false,
  elapsed: null,
  time_limit: "01:00:00",
  partition: null,
  started: null,
  ended: null,
  plan_available: true,
};

const RERUN_OPTIONS: SubmitOptions = {
  walltime: "04:00:00",
  cpus_per_task: null,
  account: null,
  qos: null,
};

const FILTERS: Array<{ key: string; label: string; match: (entry: RunHistoryEntry) => boolean }> = [
  { key: "all", label: "All", match: () => true },
  { key: "running", label: "Running", match: (entry) => entry.state === "running" },
  { key: "pending", label: "Pending", match: (entry) => entry.state === "pending" },
  { key: "completed", label: "Completed", match: (entry) => entry.state === "completed" },
  { key: "failed", label: "Failed", match: (entry) => entry.state === "failed" || entry.state === "timeout" },
  { key: "cancelled", label: "Cancelled", match: (entry) => entry.state === "cancelled" },
];

function stateTone(state: string): "ok" | "primary" | "warn" | "danger" | "neutral" {
  if (state === "completed") return "ok";
  if (state === "running") return "primary";
  if (state === "pending") return "warn";
  if (state === "failed" || state === "timeout") return "danger";
  return "neutral";
}

function StateLabel({ entry }: { entry: RunHistoryEntry }) {
  return (
    <span className={`c-state tone-${stateTone(entry.state)}`}>
      <span className="c-dot" />
      {historyStateLabel(entry)}
    </span>
  );
}

function TimeUsed({ entry }: { entry: RunHistoryEntry }) {
  const used = slurmSeconds(entry.elapsed);
  const limit = slurmSeconds(entry.time_limit);
  return (
    <span style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
      {used !== null && limit ? (
        <MiniBar value={used / limit} tone={stateTone(entry.state) === "danger" ? "danger" : "neutral"} />
      ) : null}
      <span className="c-mono" style={{ fontSize: "12px", whiteSpace: "nowrap" }}>
        {entry.elapsed ?? "—"}
        {entry.time_limit ? <span style={{ color: "var(--text-subtle)" }}> / {entry.time_limit}</span> : null}
      </span>
    </span>
  );
}

export default function RunHistory() {
  const [profiles, setProfiles] = useState<ServerEntry[]>([]);
  const [profileId, setProfileId] = useState("");
  const [entries, setEntries] = useState<RunHistoryEntry[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<string | null>(null);
  const [opened, setOpened] = useState("");
  const [savedCard, setSavedCard] = useState("");
  const [repro, setRepro] = useState<{
    planId: string;
    included: string[];
    missing: string[];
    card: Record<string, unknown>;
    error: string;
  } | null>(null);
  const testRepro =
    typeof window !== "undefined" && window.__COPERCEIVE_TEST_REPRO__ === true;
  const [filter, setFilter] = useState("all");
  const seeded = useRef(false);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const [rerun, setRerun] = useState<{
    entry: RunHistoryEntry;
    preview: SubmissionPreview;
    confirmed: boolean;
    outcome: JobSubmitOutcome | null;
    error: string;
  } | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await listServerProfiles();
        if (seeded.current) return;
        setProfiles(loaded);
        if (loaded.length > 0) setProfileId((current) => current || loaded[0]?.id || "");
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, []);

  const load = async (id = profileId) => {
    if (!id) return;
    setBusy("load");
    setError("");
    try {
      setEntries(await listRunHistory(id, 50));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  // Test-only: seeded servers and runs, as the screenshot tests have no cluster.
  useEffect(() => {
    const handleSeed = (event: Event) => {
      const detail = (event as CustomEvent<{ profiles?: ServerEntry[]; entries?: RunHistoryEntry[] }>).detail;
      if (!detail?.profiles) return;
      seeded.current = true;
      setProfiles(detail.profiles);
      setProfileId(detail.profiles[0]?.id ?? "");
      setEntries(detail.entries ?? []);
      setError("");
    };
    window.addEventListener("coperceive-test-run-history-seed", handleSeed);
    return () => window.removeEventListener("coperceive-test-run-history-seed", handleSeed);
  }, []);

  useEffect(() => {
    if (profileId && !seeded.current) void load(profileId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  const cancel = async (entry: RunHistoryEntry) => {
    setBusy(entry.job_id);
    setError("");
    try {
      await cancelRunJob(profileId, entry.plan_id, entry.job_id, true);
      setConfirmCancel(null);
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const beginRerun = async (entry: RunHistoryEntry) => {
    setBusy(entry.job_id);
    setError("");
    try {
      const preview = await previewSubmission(profileId, entry.plan_id, RERUN_OPTIONS);
      setRerun({ entry, preview, confirmed: false, outcome: null, error: "" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const submitRerun = async () => {
    if (!rerun || !rerun.confirmed) return;
    setBusy(rerun.entry.job_id);
    setRerun({ ...rerun, error: "" });
    try {
      const outcome = await submitRunPlan(
        profileId,
        rerun.entry.plan_id,
        RERUN_OPTIONS,
        rerun.preview.preview_hash,
        true,
      );
      setRerun({ ...rerun, outcome });
      await load();
    } catch (e) {
      setRerun({ ...rerun, error: errorText(e) });
    } finally {
      setBusy(null);
    }
  };

  const exportCard = async (entry: RunHistoryEntry) => {
    setBusy(entry.job_id);
    setError("");
    setSavedCard("");
    try {
      const card = await previewReproCard(profileId, entry.plan_id, entry.time_limit);
      const lines = reproPreviewLines(card);
      setRepro({ planId: entry.plan_id, ...lines, card, error: "" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const saveCard = async () => {
    if (!repro) return;
    setBusy("save-card");
    setRepro({ ...repro, error: "" });
    try {
      const name = `repro-${typeof repro.card.id === "string" ? repro.card.id : repro.planId}.json`;
      const chosen = await pickSavePath(name, "json");
      if (!chosen) {
        setBusy(null);
        return;
      }
      const bytes = new TextEncoder().encode(`${JSON.stringify(repro.card, null, 2)}\n`);
      setSavedCard(await saveExportedFile(undefined, bytes));
    } catch (e) {
      setRepro({ ...repro, error: errorText(e) });
    } finally {
      setBusy(null);
    }
  };

  const open = async (entry: RunHistoryEntry) => {
    setBusy(entry.job_id);
    setError("");
    setOpened("");
    try {
      const imported = await openPredictions(profileId, entry.plan_id);
      setOpened(`Opened the viewer on run ${imported.run_id}.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const source = testRepro ? [TEST_REPRO_ENTRY, ...(entries ?? [])] : (entries ?? []);
  const shown = source.filter((entry) => (FILTERS.find((item) => item.key === filter) ?? FILTERS[0]).match(entry));
  const selected = shown.find((entry) => entry.job_id === selectedJob) ?? null;
  useDefaultSelection(shown, rowKey, selectedJob, setSelectedJob);
  const running = (entries ?? []).filter((entry) => entry.state === "running").length;
  const serverName = profiles.find((entry) => entry.id === profileId)?.profile.name ?? "";
  usePageHeader("history", {
    meta: entries && profiles.length > 0
      ? `${entries.length} run${entries.length === 1 ? "" : "s"} in the last 30 days${serverName ? ` · ${serverName}` : ""} · from sacct`
      : undefined,
    navCount: running > 0 ? `${running} running` : undefined,
    actions: (
      <button type="button" className="c-header-action" disabled={busy !== null || !profileId} onClick={() => void load()}>
        {busy === "load" ? "Refreshing…" : "Refresh"}
      </button>
    ),
  });

  const columns: GridColumn<RunHistoryEntry>[] = [
    {
      key: "job",
      header: "Job",
      min: 80,
      max: 110,
      render: (entry) => (
        <span className="c-mono" style={{ color: "var(--text)" }}>
          {entry.job_id}
        </span>
      ),
    },
    {
      key: "plan",
      header: "Plan",
      min: 130,
      max: 360,
      render: (entry) => (
        <span className="c-mono" style={{ fontSize: "12px" }}>
          {entry.plan_id}
          {!entry.plan_available ? <span style={{ color: "var(--text-subtle)" }}> · saved plan gone</span> : null}
        </span>
      ),
    },
    { key: "state", header: "State", min: 104, max: 130, render: (entry) => <StateLabel entry={entry} /> },
    { key: "time", header: "Time used / limit", min: 176, max: 260, render: (entry) => <TimeUsed entry={entry} /> },
    {
      key: "started",
      header: "Started",
      min: 128,
      max: 180,
      render: (entry) => <span className="c-mono" style={{ fontSize: "12px" }}>{entry.started ?? "—"}</span>,
    },
  ];

  const inspector = selected ? (
    <>
      <InspectorSection title={`Job ${selected.job_id}`}>
        <StateLabel entry={selected} />
        <span className="c-mono" style={{ fontSize: "12px", color: "var(--text-subtle)", overflowWrap: "anywhere" }}>
          plan {selected.plan_id}
        </span>
      </InspectorSection>
      <InspectorSection>
        <Facts
          items={[
            ["Time used", selected.elapsed ?? "—"],
            ["Limit", selected.time_limit ?? "—"],
            selected.partition ? ["Partition", selected.partition] : null,
            ["Started", selected.started ?? "—"],
            selected.ended ? ["Ended", selected.ended] : null,
          ]}
        />
      </InspectorSection>
      <InspectorSection>
        {selected.plan_available && (selected.state === "completed" || selected.state === "failed") ? (
          <>
            <button type="button" className="c-primary-wide is-blue" disabled={busy !== null} onClick={() => void open(selected)}>
              Open predictions
            </button>
            <button type="button" className="c-secondary" disabled={busy !== null} onClick={() => void exportCard(selected)}>
              Export repro card
            </button>
          </>
        ) : null}
        {historyCanRerun(selected) ? (
          <button type="button" className="c-secondary" disabled={busy !== null} onClick={() => void beginRerun(selected)}>
            Re-run with same plan
          </button>
        ) : (
          <span className="c-step-note">The saved plan is no longer on the server, so this run cannot be repeated.</span>
        )}
        {selected.active ? (
          confirmCancel === selected.job_id ? (
            <div style={{ display: "flex", gap: "8px" }}>
              <button type="button" className="c-secondary is-danger" disabled={busy !== null} onClick={() => void cancel(selected)}>
                Confirm cancel
              </button>
              <button type="button" className="c-secondary" onClick={() => setConfirmCancel(null)}>
                Keep
              </button>
            </div>
          ) : (
            <button type="button" className="c-secondary is-danger" onClick={() => setConfirmCancel(selected.job_id)}>
              Cancel job
            </button>
          )
        ) : null}
      </InspectorSection>
      {repro ? (
        <InspectorSection title="Repro card">
          <span className="c-step-note">
            This file describes the run so someone else can repeat it. It stays on this computer.
          </span>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {repro.included.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {repro.missing.length > 0 ? (
            <div>
              <div>Not recorded, and left blank in the file:</div>
              <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
                {repro.missing.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div style={{ display: "flex", gap: "8px" }}>
            <button type="button" className="c-secondary" disabled={busy !== null} onClick={() => void saveCard()}>
              {busy === "save-card" ? "Saving…" : "Save repro card"}
            </button>
            <button type="button" className="c-secondary" onClick={() => setRepro(null)}>
              Close
            </button>
          </div>
          {repro.error ? <span style={{ color: "var(--danger)" }}>{repro.error}</span> : null}
        </InspectorSection>
      ) : null}
      {savedCard ? (
        <p role="status" className="c-page-note" style={{ color: "var(--ok)" }}>
          Saved the repro card.{" "}
          <button type="button" className="c-secondary" onClick={() => void revealInFinder(savedCard)}>
            Show in Finder
          </button>
        </p>
      ) : null}
      {rerun && rerun.entry.job_id === selected.job_id ? (
        <InspectorSection title="Re-run">
          <span className="c-step-note">
            A new submission of the saved plan. Nothing is submitted until you confirm the previewed script.
          </span>
          <pre className="c-script">{rerun.preview.script}</pre>
          {rerun.outcome ? (
            <span style={{ color: "var(--ok)" }}>
              Resubmitted as job {rerun.outcome.job_id} — {rerun.outcome.state.toUpperCase()}
              {rerun.outcome.queue_reason ? ` (${rerun.outcome.queue_reason})` : ""}
            </span>
          ) : (
            <>
              <label style={{ display: "inline-flex", gap: "6px", alignItems: "center", fontSize: "12.5px" }}>
                <input
                  type="checkbox"
                  checked={rerun.confirmed}
                  onChange={(event) => setRerun({ ...rerun, confirmed: event.target.checked })}
                />
                I reviewed the script and want to submit it
              </label>
              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  type="button"
                  className="c-secondary"
                  disabled={busy !== null || !rerun.confirmed}
                  onClick={() => void submitRerun()}
                >
                  {busy !== null ? "Submitting…" : "Submit re-run"}
                </button>
                <button type="button" className="c-secondary" onClick={() => setRerun(null)}>
                  Close
                </button>
              </div>
            </>
          )}
          {rerun.error ? <span style={{ color: "var(--danger)" }}>{rerun.error}</span> : null}
        </InspectorSection>
      ) : null}
    </>
  ) : null;

  return (
    <PageLayout inspectorLabel="Selected run" inspector={inspector}>
      {profiles.length === 0 ? null : (
      <PageToolbar>
        {profiles.length > 1 ? (
          <select
            aria-label="Server profile"
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
        ) : null}
        {profiles.length === 0 ? null : <div role="group" aria-label="Filter runs by state" className="c-chips">
          {FILTERS.map((item) => {
            const count = (entries ?? []).filter(item.match).length;
            if (item.key !== "all" && count === 0) return null;
            return (
              <button
                key={item.key}
                type="button"
                className={`c-filter${filter === item.key ? " is-active" : ""}`}
                aria-pressed={filter === item.key}
                onClick={() => setFilter(item.key)}
              >
                {item.label} <span className="c-filter-count">{count}</span>
              </button>
            );
          })}
        </div>}
      </PageToolbar>
      )}
      {error ? (
        <p role="alert" className="c-page-note" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      ) : null}
      {opened ? (
        <p role="status" className="c-page-note" style={{ color: "var(--ok)" }}>
          {opened}
        </p>
      ) : null}
      {profiles.length === 0 && !error && !testRepro ? (
        <p className="c-page-note">Add a server in Servers; runs you submit there are listed here.</p>
      ) : entries === null && !testRepro ? (
        <p className="c-page-note">Loading submitted runs…</p>
      ) : (
        <GridTable
          label="Runs"
          columns={columns}
          rows={shown}
          rowKey={rowKey}
          selectedKey={selectedJob}
          onSelect={(entry) => setSelectedJob(entry.job_id)}
          rowHeight={34}
          empty="No submitted runs in the last 30 days for this server."
        />
      )}
    </PageLayout>
  );
}
