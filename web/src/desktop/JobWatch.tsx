// SPDX-License-Identifier: Apache-2.0
/**
 * Watch a run submitted from this app: live scheduler state, bounded stdout and
 * stderr tails, and an explicit cancel. Polling stops at a terminal state and
 * never runs in a tight loop (10 s while pending/running).
 */
import { useEffect, useRef, useState } from "react";
import { cancelRunJob, jobWatchLogs, jobWatchStatus, openPredictions, predictionStream, runMetrics } from "./local";
import {
  failureNextAction,
  isJobActive,
  jobWatchLabel,
  metricsRows,
  progressLabel,
  type JobLogTail,
  type JobSubmitOutcome,
  type JobWatchStatus,
  type MetricsReport,
  type LatestPrediction,
  type PredictionProgress,
  type PredictionSourceCount,
} from "./runPlan";

const panel = {
  marginTop: "0.75rem",
  padding: "0.75rem",
  borderRadius: "8px",
  backgroundColor: "rgba(28, 28, 30, 0.85)",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  color: "#c9d1d9",
  fontSize: "0.82rem",
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

const logBox = {
  maxHeight: "14rem",
  overflow: "auto",
  padding: "0.5rem",
  marginTop: "0.4rem",
  borderRadius: "6px",
  backgroundColor: "rgba(0, 0, 0, 0.55)",
  border: "1px solid rgba(255, 255, 255, 0.1)",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: "0.72rem",
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-word" as const,
};

const POLL_MS = 10_000;

export default function JobWatch({
  profileId,
  planId,
  initialOutcome,
}: {
  profileId: string;
  planId: string;
  initialOutcome: JobSubmitOutcome;
}) {
  const [status, setStatus] = useState<JobWatchStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"refresh" | "logs" | "cancel" | null>(null);
  const [log, setLog] = useState<JobLogTail | null>(null);
  const [logStream, setLogStream] = useState<"stdout" | "stderr">("stdout");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [liveBoxes, setLiveBoxes] = useState(0);
  const [liveSources, setLiveSources] = useState<PredictionSourceCount[]>([]);
  const [liveProgress, setLiveProgress] = useState<PredictionProgress | null>(null);
  const [latest, setLatest] = useState<LatestPrediction | null>(null);
  const [liveNote, setLiveNote] = useState("");
  const [metrics, setMetrics] = useState<MetricsReport | null | undefined>(undefined);
  const [failureTail, setFailureTail] = useState("");
  const [opening, setOpening] = useState(false);
  const [opened, setOpened] = useState("");
  const offsetRef = useRef(0);
  const alive = useRef(true);
  const shouldPollRef = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    const refresh = async () => {
      try {
        const next = await jobWatchStatus(profileId, planId, initialOutcome.job_id);
        if (!stopped && alive.current) setStatus(next);
      } catch (e) {
        if (!stopped && alive.current) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      if (!shouldPollRef.current) return;
      void refresh();
    }, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, planId, initialOutcome.job_id]);
  shouldPollRef.current = status === null ? true : isJobActive(status.state);

  // Live predictions: one bounded read every 10 s while the job can still write.
  // metrics.json is written once at the end by contract, so only progress and
  // prediction counts can be live.
  useEffect(() => {
    if (!status || !isJobActive(status.state)) return;
    let stopped = false;
    const poll = async () => {
      try {
        const chunk = await predictionStream(profileId, planId, offsetRef.current, 65536);
        if (stopped || !alive.current) return;
        offsetRef.current = chunk.offset;
        setLiveBoxes((current) => current + chunk.boxes);
        if (chunk.sources.length > 0) {
          setLiveSources((current) => {
            const merged = new Map(current.map((item) => [item.source, item.boxes]));
            for (const item of chunk.sources) {
              merged.set(item.source, (merged.get(item.source) ?? 0) + item.boxes);
            }
            return [...merged.entries()].map(([source, boxes]) => ({ source, boxes }));
          });
        }
        if (chunk.progress) setLiveProgress(chunk.progress);
        if (chunk.latest) setLatest(chunk.latest);
        setLiveNote(chunk.idle ? "waiting for the next prediction line…" : "");
      } catch (e) {
        if (!stopped && alive.current) setLiveNote(e instanceof Error ? e.message : String(e));
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [profileId, planId, status?.state]);

  // Terminal report: metrics.json is the pipeline's report; on failure the last
  // stderr lines plus one next action are the useful output.
  useEffect(() => {
    if (!status || isJobActive(status.state)) return;
    let stopped = false;
    void (async () => {
      try {
        const report = await runMetrics(profileId, planId);
        if (!stopped && alive.current) setMetrics(report);
      } catch (e) {
        if (!stopped && alive.current) setLiveNote(e instanceof Error ? e.message : String(e));
      }
      if (status.state === "failed") {
        try {
          const tail = await jobWatchLogs(profileId, planId, initialOutcome.job_id, "stderr", 50);
          if (!stopped && alive.current) setFailureTail(tail.text);
        } catch {
          // The failure panel still shows the next action without a log tail.
        }
      }
    })();
    return () => {
      stopped = true;
    };
  }, [profileId, planId, status?.state, initialOutcome.job_id]);

  const refreshNow = async () => {
    setBusy("refresh");
    setError("");
    try {
      setStatus(await jobWatchStatus(profileId, planId, initialOutcome.job_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const tail = async (stream: "stdout" | "stderr") => {
    setBusy("logs");
    setError("");
    setLogStream(stream);
    try {
      setLog(await jobWatchLogs(profileId, planId, initialOutcome.job_id, stream, 50));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const cancel = async () => {
    if (!confirmCancel) return;
    setBusy("cancel");
    setError("");
    try {
      const result = await cancelRunJob(profileId, planId, initialOutcome.job_id, true);
      setStatus((current) => ({
        job_id: result.job_id,
        plan_id: planId,
        job_name: current?.job_name ?? "",
        state: result.state,
        queue_reason: result.queue_reason,
        elapsed: current?.elapsed ?? null,
        time_left: current?.time_left ?? null,
        node: current?.node ?? null,
      }));
      setConfirmCancel(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const open = async () => {
    setOpening(true);
    setError("");
    try {
      const imported = await openPredictions(profileId, planId);
      setOpened(`Opened the viewer on run ${imported.run_id}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOpening(false);
    }
  };

  const state = status?.state ?? initialOutcome.state;
  const label = status
    ? jobWatchLabel(status)
    : initialOutcome.state.toUpperCase() + (initialOutcome.queue_reason ? ` — ${initialOutcome.queue_reason}` : "");
  const active = isJobActive(state);

  return (
    <div style={panel} aria-label="Job watch">
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <strong>Job {initialOutcome.job_id}</strong>
        <span>{label}</span>
        {status?.node ? <span style={{ opacity: 0.75 }}>on {status.node}</span> : null}
        <button type="button" style={button} disabled={busy !== null} onClick={() => void refreshNow()}>
          {busy === "refresh" ? "Refreshing…" : "Refresh now"}
        </button>
      </div>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.5rem", flexWrap: "wrap" }}>
        <button type="button" style={button} disabled={busy !== null} onClick={() => void tail("stdout")}>
          Tail stdout (50 lines)
        </button>
        <button type="button" style={button} disabled={busy !== null} onClick={() => void tail("stderr")}>
          Tail stderr (50 lines)
        </button>
        {active ? (
          <label style={{ display: "inline-flex", gap: "0.35rem", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={confirmCancel}
              onChange={(event) => setConfirmCancel(event.target.checked)}
              disabled={busy !== null}
            />
            I want to cancel this job
          </label>
        ) : null}
        {active ? (
          <button
            type="button"
            style={{ ...button, borderColor: "rgba(248, 81, 73, 0.6)" }}
            disabled={busy !== null || !confirmCancel}
            onClick={() => void cancel()}
          >
            {busy === "cancel" ? "Cancelling…" : "Cancel job"}
          </button>
        ) : null}
      </div>
      {active ? (
        <div style={{ marginTop: "0.5rem", opacity: 0.85 }}>
          Live output: {liveBoxes} new prediction{liveBoxes === 1 ? "" : "s"}
          {liveSources.length > 0
            ? ` (${liveSources.map((item) => `${item.source}: ${item.boxes}`).join(", ")})`
            : ""}
          {progressLabel(liveProgress) ? ` · ${progressLabel(liveProgress)}` : ""}
          {latest ? ` · latest ${latest.agent_id}/${latest.source} @ ${latest.time_us} µs` : ""}
          {liveNote ? ` — ${liveNote}` : ""}
        </div>
      ) : null}
      {error ? <div style={{ marginTop: "0.4rem", color: "#ff7b72" }}>{error}</div> : null}
      {log ? (
        <div>
          <div style={{ marginTop: "0.5rem", opacity: 0.8 }}>
            {logStream} — last {log.lines} lines{log.truncated ? " (more may exist)" : ""}
            {log.text.trim() ? "" : ": no output yet"}
          </div>
          {log.text.trim() ? <pre style={logBox}>{log.text}</pre> : null}
        </div>
      ) : null}
      {!active ? (
        <div style={{ marginTop: "0.6rem" }}>
          {state === "failed" ? (
            <div>
              <strong>Run failed.</strong>
              {failureTail.trim() ? (
                <pre style={logBox}>{failureTail}</pre>
              ) : (
                <div style={{ opacity: 0.75 }}>No stderr output was written.</div>
              )}
              <div style={{ marginTop: "0.35rem", color: "#f0b400" }}>
                Next action: {failureNextAction()}
              </div>
            </div>
          ) : (
            <div>
              <strong>Run report</strong>
              {metrics === undefined ? (
                <div style={{ opacity: 0.75 }}>Reading metrics.json…</div>
              ) : metrics === null ? (
                <div style={{ opacity: 0.85 }}>
                  The pipeline wrote no metrics.json, so the run is incomplete or partial. The
                  contract requires metrics.json before a successful exit.
                </div>
              ) : (
                <div>
                  <table style={{ borderCollapse: "collapse", marginTop: "0.35rem" }}>
                    <tbody>
                      {metricsRows(metrics).map((row) => (
                        <tr key={row.label}>
                          <td style={{ padding: "0.1rem 0.75rem 0.1rem 0", opacity: 0.75 }}>{row.label}</td>
                          <td style={{ padding: "0.1rem 0" }}>{row.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {metrics.notes ? (
                    <div style={{ marginTop: "0.35rem", opacity: 0.85 }}>{metrics.notes}</div>
                  ) : null}
                </div>
              )}
            </div>
          )}
          <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <button type="button" style={button} disabled={opening} onClick={() => void open()}>
              {opening ? "Opening…" : "Open predictions"}
            </button>
            {opened ? <span style={{ opacity: 0.85 }}>{opened}</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
