// SPDX-License-Identifier: Apache-2.0
/** Remote HPC 6/6: the run history with cancel, re-run and open predictions. */
import { useEffect, useState } from "react";
import {
  cancelRunJob,
  listRunHistory,
  listServerProfiles,
  openPredictions,
  previewSubmission,
  submitRunPlan,
} from "./local";
import type { ServerEntry } from "./hpc";
import {
  historyCanRerun,
  historyStateLabel,
  historyTiming,
  type JobSubmitOutcome,
  type RunHistoryEntry,
  type SubmissionPreview,
  type SubmitOptions,
} from "./runPlan";

const card = {
  backgroundColor: "rgba(28, 28, 30, 0.85)",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  borderRadius: "14px",
  padding: "1.25rem",
  boxShadow: "0 20px 50px rgba(0, 0, 0, 0.45)",
  color: "#c9d1d9",
  fontSize: "0.85rem",
} as const;

const button = {
  padding: "0.3rem 0.65rem",
  fontSize: "0.78rem",
  borderRadius: "6px",
  border: "1px solid rgba(255, 255, 255, 0.2)",
  backgroundColor: "rgba(255, 255, 255, 0.08)",
  color: "#fff",
  cursor: "pointer",
} as const;

const RERUN_OPTIONS: SubmitOptions = {
  walltime: "04:00:00",
  cpus_per_task: null,
  account: null,
  qos: null,
};

export default function RunHistory() {
  const [profiles, setProfiles] = useState<ServerEntry[]>([]);
  const [profileId, setProfileId] = useState("");
  const [entries, setEntries] = useState<RunHistoryEntry[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<string | null>(null);
  const [opened, setOpened] = useState("");
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
        setProfiles(loaded);
        if (loaded.length > 0) setProfileId((current) => current || loaded[0]?.id || "");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
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
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    if (profileId) void load(profileId);
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
      setError(e instanceof Error ? e.message : String(e));
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
      setError(e instanceof Error ? e.message : String(e));
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
      setRerun({ ...rerun, error: e instanceof Error ? e.message : String(e) });
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
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={card}>
      <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap" }}>
        <strong>Run history</strong>
        <select
          aria-label="Server profile"
          value={profileId}
          onChange={(event) => setProfileId(event.target.value)}
          style={{ padding: "0.35rem 0.5rem", borderRadius: "6px", background: "rgba(0,0,0,0.5)", color: "#fff" }}
        >
          {profiles.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.profile.name}
            </option>
          ))}
        </select>
        <button type="button" style={button} disabled={busy !== null} onClick={() => void load()}>
          {busy === "load" ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {error ? <div style={{ marginTop: "0.5rem", color: "#ff7b72" }}>{error}</div> : null}
      {opened ? <div style={{ marginTop: "0.5rem", color: "#7ee787" }}>{opened}</div> : null}
      {entries === null ? (
        <div style={{ marginTop: "0.75rem", opacity: 0.75 }}>Loading submitted runs…</div>
      ) : entries.length === 0 ? (
        <div style={{ marginTop: "0.75rem", opacity: 0.75 }}>
          No submitted runs in the last 30 days for this server.
        </div>
      ) : (
        <table style={{ width: "100%", marginTop: "0.75rem", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", opacity: 0.75 }}>
              <th style={{ padding: "0.25rem 0.5rem 0.25rem 0" }}>Job</th>
              <th>Plan</th>
              <th>State</th>
              <th>Timing</th>
              <th>Started</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.job_id} style={{ borderTop: "1px solid rgba(255,255,255,0.08)", verticalAlign: "top" }}>
                <td style={{ padding: "0.35rem 0.5rem 0.35rem 0" }}>{entry.job_id}</td>
                <td style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.75rem" }}>{entry.plan_id}</td>
                <td>{historyStateLabel(entry)}</td>
                <td>{historyTiming(entry)}</td>
                <td>{entry.started ?? "—"}</td>
                <td>
                  <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
                    {entry.active ? (
                      confirmCancel === entry.job_id ? (
                        <>
                          <button
                            type="button"
                            style={{ ...button, borderColor: "rgba(248,81,73,0.7)" }}
                            disabled={busy !== null}
                            onClick={() => void cancel(entry)}
                          >
                            Confirm cancel
                          </button>
                          <button type="button" style={button} onClick={() => setConfirmCancel(null)}>
                            Keep
                          </button>
                        </>
                      ) : (
                        <button type="button" style={button} onClick={() => setConfirmCancel(entry.job_id)}>
                          Cancel
                        </button>
                      )
                    ) : null}
                    {historyCanRerun(entry) ? (
                      <button type="button" style={button} disabled={busy !== null} onClick={() => void beginRerun(entry)}>
                        Re-run
                      </button>
                    ) : null}
                    {entry.plan_available && (entry.state === "completed" || entry.state === "failed") ? (
                      <button type="button" style={button} disabled={busy !== null} onClick={() => void open(entry)}>
                        Open predictions
                      </button>
                    ) : null}
                    {!entry.plan_available ? <span style={{ opacity: 0.6 }}>saved plan gone</span> : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rerun ? (
        <div style={{ marginTop: "0.9rem", borderTop: "1px solid rgba(255,255,255,0.12)", paddingTop: "0.75rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <strong>Re-run {rerun.entry.plan_id}</strong>
            <button type="button" style={button} onClick={() => setRerun(null)}>
              Close
            </button>
          </div>
          <div style={{ marginTop: "0.35rem", opacity: 0.8 }}>
            This is a new submission of the saved plan; nothing is submitted until you confirm the
            previewed script.
          </div>
          <pre
            style={{
              maxHeight: "12rem",
              overflow: "auto",
              padding: "0.5rem",
              marginTop: "0.4rem",
              borderRadius: "6px",
              backgroundColor: "rgba(0,0,0,0.55)",
              border: "1px solid rgba(255,255,255,0.1)",
              fontSize: "0.72rem",
            }}
          >
            {rerun.preview.script}
          </pre>
          {rerun.outcome ? (
            <div style={{ marginTop: "0.4rem", color: "#7ee787" }}>
              Resubmitted as job {rerun.outcome.job_id} — {rerun.outcome.state.toUpperCase()}
              {rerun.outcome.queue_reason ? ` (${rerun.outcome.queue_reason})` : ""}
            </div>
          ) : (
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.4rem", flexWrap: "wrap" }}>
              <label style={{ display: "inline-flex", gap: "0.35rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={rerun.confirmed}
                  onChange={(event) => setRerun({ ...rerun, confirmed: event.target.checked })}
                />
                I reviewed the script and want to submit it
              </label>
              <button
                type="button"
                style={button}
                disabled={busy !== null || !rerun.confirmed}
                onClick={() => void submitRerun()}
              >
                {busy !== null ? "Submitting…" : "Submit re-run"}
              </button>
            </div>
          )}
          {rerun.error ? <div style={{ marginTop: "0.4rem", color: "#ff7b72" }}>{rerun.error}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
