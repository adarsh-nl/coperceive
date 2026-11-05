// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useState } from "react";
import {
  buildDiagnosticsReport,
  copyDiagnosticsText,
  saveDiagnosticsZip,
  type DiagnosticsReport,
} from "./local";

const ISSUE_URL = "https://github.com/adarsh-nl/coperceive/issues/new";

export function DiagnosticsDialog({
  context,
  initialError,
  onClose,
}: {
  context: Record<string, unknown>;
  initialError?: { message: string; stack?: string } | null;
  onClose: () => void;
}) {
  const [report, setReport] = useState<DiagnosticsReport | null>(null);
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("");
  const merged = useMemo(() => {
    const value = { ...context };
    if (initialError) value.ui_error = initialError;
    return value;
  }, [context, initialError]);

  useEffect(() => {
    let cancelled = false;
    void buildDiagnosticsReport(merged).then((next) => {
      if (!cancelled) setReport(next);
    });
    return () => {
      cancelled = true;
    };
  }, [merged]);

  const preview = report?.preview ?? "Collecting diagnostics…";
  const withNote = note.trim()
    ? `${preview}\n\n## What I was doing\n${note.trim()}\n`
    : preview;

  const github = () => {
    const title = "Crash report: Coperceive";
    const body = `What I was doing:\n${note.trim() || "(not specified)"}\n\n${preview.slice(0, 6000)}`;
    const url = `${ISSUE_URL}?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="diagnostics-backdrop" role="presentation" onClick={onClose}>
      <div
        className="diagnostics-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="diagnostics-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="diagnostics-title">Share diagnostics</h2>
        <p className="muted">
          Nothing is sent automatically. Review everything below before you copy, save or open a GitHub issue.
        </p>
        <label className="diagnostics-note">
          What were you doing? <span className="muted">(optional)</span>
          <textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} />
        </label>
        <pre className="diagnostics-preview" aria-label="Diagnostics preview">
          {withNote}
        </pre>
        <p className="muted" role="status">
          {status || `${report?.bytes ?? 0} bytes shown before sharing.`}
        </p>
        <div className="diagnostics-actions">
          <button
            type="button"
            onClick={async () => {
              const ok = await copyDiagnosticsText(merged, note);
              setStatus(ok ? "Copied to clipboard." : "Could not copy diagnostics.");
            }}
          >
            Copy to clipboard
          </button>
          <button
            type="button"
            onClick={async () => {
              const path = await saveDiagnosticsZip(merged, note);
              setStatus(path ? `Saved ${path}` : "Save cancelled.");
            }}
          >
            Save as file…
          </button>
          <button type="button" onClick={github}>
            Report on GitHub
          </button>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
