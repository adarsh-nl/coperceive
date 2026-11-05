// SPDX-License-Identifier: Apache-2.0
/**
 * Share a server profile with a lab, and import one.
 *
 * The file never includes an SSH alias, a password or a key. The person
 * importing types their own SSH alias, then reviews every field before saving.
 */
import { useState } from "react";
import type { ServerEntry } from "./hpc";
import {
  parsePipelineRegistrations,
  reviewFields,
  secretProblem,
  type ReviewField,
} from "./labProfile";
import {
  exportLabProfile,
  pickSavePath,
  prepareLabImport,
  saveExportedFile,
  saveServerProfile,
  testServerConnection,
} from "./local";
import type { PipelineSpec } from "./runPlan";
import type { DetectionReport } from "./hpc";

const field = { marginBottom: "0.75rem" } as const;
const labelStyle = {
  display: "block",
  fontSize: "0.72rem",
  letterSpacing: "0.04em",
  textTransform: "uppercase" as const,
  color: "#86868b",
  marginBottom: "0.25rem",
};
const input = {
  width: "100%",
  boxSizing: "border-box" as const,
  padding: "0.45rem 0.6rem",
  fontSize: "0.85rem",
  borderRadius: "6px",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  backgroundColor: "rgba(0, 0, 0, 0.5)",
  color: "#f5f5f7",
};
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

function ReviewList({ rows }: { rows: ReviewField[] }) {
  return (
    <div style={{ marginTop: "0.75rem" }}>
      {rows.map((row) => (
        <div
          key={row.label}
          style={{
            display: "grid",
            gridTemplateColumns: "14rem 1fr",
            gap: "0.75rem",
            padding: "0.28rem 0",
            fontSize: "0.82rem",
            borderTop: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          <div style={{ color: "#8b949e" }}>{row.label}</div>
          <div style={{ color: "#f5f5f7", overflowWrap: "anywhere" }}>{row.value}</div>
        </div>
      ))}
    </div>
  );
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("Could not read that file."));
    reader.readAsText(file);
  });
}

export function SharePanel({
  entry,
  onClose,
  onSaved,
}: {
  entry: ServerEntry;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [pipelines, setPipelines] = useState<PipelineSpec[]>([]);
  const [rows, setRows] = useState<ReviewField[] | null>(null);
  const [documentText, setDocumentText] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadPipelines = async (file: File | undefined) => {
    setError("");
    if (!file) {
      setPipelines([]);
      return;
    }
    try {
      const text = await readFile(file);
      const problem = secretProblem(text);
      if (problem) {
        setPipelines([]);
        setError(problem);
        return;
      }
      setPipelines(parsePipelineRegistrations(text));
    } catch (caught) {
      setPipelines([]);
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const preview = async () => {
    setBusy(true);
    setError("");
    try {
      const text = await exportLabProfile(entry, pipelines);
      const problem = secretProblem(text);
      if (problem) {
        setError(problem);
        return;
      }
      const parsed = JSON.parse(text) as { profile: ServerEntry["profile"]; pipelines?: PipelineSpec[] };
      setDocumentText(text);
      setRows(reviewFields(parsed.profile, parsed.pipelines ?? []));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!documentText) return;
    setBusy(true);
    setError("");
    try {
      const path = await pickSavePath("coperceive-lab-profile.json", "json");
      if (!path) return;
      await saveExportedFile(undefined, new TextEncoder().encode(documentText));
      onSaved(`Saved the lab profile to ${path}.`);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={{ marginTop: "1rem" }}>
      <h3 style={{ margin: "0 0 0.35rem", color: "#f5f5f7", fontSize: "1.05rem" }}>
        Share with your lab
      </h3>
      <p style={{ margin: "0 0 0.75rem", color: "#8b949e", fontSize: "0.82rem" }}>
        The file keeps the shared folders, scheduler choices and modules. Each person fills in
        their own SSH alias, home and scratch. Passwords and keys are not included.
      </p>
      <div style={field}>
        <label style={labelStyle} htmlFor="lab-pipeline-file">
          Pipeline registration (optional)
        </label>
        <input
          id="lab-pipeline-file"
          type="file"
          accept="application/json,.json"
          onChange={(event) => void loadPipelines(event.target.files?.[0])}
        />
      </div>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button type="button" style={primaryButton} disabled={busy} onClick={() => void preview()}>
          {busy ? "Preparing…" : "Preview"}
        </button>
        <button type="button" style={secondaryButton} disabled={busy || !documentText} onClick={() => void save()}>
          Save file
        </button>
        <button type="button" style={secondaryButton} onClick={onClose}>
          Close
        </button>
      </div>
      {error ? (
        <div role="alert" style={{ color: "#ff6259", marginTop: "0.6rem", fontSize: "0.82rem" }}>
          {error}
        </div>
      ) : null}
      {rows ? <ReviewList rows={rows} /> : null}
    </section>
  );
}

export function ImportPanel({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [raw, setRaw] = useState("");
  const [sshAlias, setSshAlias] = useState("");
  const [user, setUser] = useState("");
  const [home, setHome] = useState("");
  const [scratch, setScratch] = useState("");
  const [entry, setEntry] = useState<
    (ServerEntry & { shared_pipelines?: PipelineSpec[] }) | null
  >(null);
  const [rows, setRows] = useState<ReviewField[] | null>(null);
  const [report, setReport] = useState<DetectionReport | null>(null);
  const [tested, setTested] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const identity = { sshAlias, user, home, scratch };

  const load = async (file: File | undefined) => {
    setError("");
    setEntry(null);
    setRows(null);
    setTested(false);
    if (!file) {
      setRaw("");
      return;
    }
    try {
      const text = await readFile(file);
      const problem = secretProblem(text);
      if (problem) {
        setRaw("");
        setError(problem);
        return;
      }
      setRaw(text);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const review = async () => {
    setBusy(true);
    setError("");
    setTested(false);
    setReport(null);
    try {
      if (!sshAlias.trim()) {
        setError("Enter your SSH alias. It is the Host name in your own SSH config.");
        return;
      }
      const problem = secretProblem(raw);
      if (problem) {
        setError(problem);
        return;
      }
      const prepared = await prepareLabImport(raw, identity);
      setEntry(prepared);
      setRows(reviewFields(prepared.profile, prepared.shared_pipelines ?? []));
    } catch (caught) {
      setEntry(null);
      setRows(null);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    if (!entry) return;
    setBusy(true);
    setError("");
    try {
      const next = await testServerConnection(entry);
      setReport(next);
      setTested(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!entry) return;
    setBusy(true);
    setError("");
    try {
      const saved = await saveServerProfile(entry);
      onSaved(`Saved “${saved.profile.name}”.`);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={{ marginTop: "1rem" }}>
      <h3 style={{ margin: "0 0 0.35rem", color: "#f5f5f7", fontSize: "1.05rem" }}>
        Import lab profile
      </h3>
      <p style={{ margin: "0 0 0.75rem", color: "#ffb84d", fontSize: "0.82rem" }}>
        Commands, modules and containers in this file will run on the cluster under your
        account. Import only from someone you trust.
      </p>
      <p style={{ margin: "0 0 0.75rem", color: "#8b949e", fontSize: "0.82rem" }}>
        Read every field, then enter the SSH alias from your own config. Test the connection
        before saving.
      </p>
      <div style={field}>
        <label style={labelStyle} htmlFor="lab-profile-file">
          Lab profile file
        </label>
        <input
          id="lab-profile-file"
          type="file"
          accept="application/json,.json"
          onChange={(event) => void load(event.target.files?.[0])}
        />
      </div>
      <div style={field}>
        <label style={labelStyle} htmlFor="lab-ssh-alias">
          Your SSH alias
        </label>
        <input
          id="lab-ssh-alias"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="off"
          style={input}
          value={sshAlias}
          onChange={(event) => setSshAlias(event.target.value)}
        />
      </div>
      <div style={field}>
        <label style={labelStyle} htmlFor="lab-user">
          Your user name on the cluster
        </label>
        <input
          id="lab-user"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="off"
          style={input}
          value={user}
          onChange={(event) => setUser(event.target.value)}
        />
      </div>
      <div style={field}>
        <label style={labelStyle} htmlFor="lab-home">
          Your home directory on the cluster
        </label>
        <input
          id="lab-home"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="off"
          style={input}
          value={home}
          onChange={(event) => setHome(event.target.value)}
        />
      </div>
      <div style={field}>
        <label style={labelStyle} htmlFor="lab-scratch">
          Your scratch folder
        </label>
        <input
          id="lab-scratch"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="off"
          style={input}
          value={scratch}
          placeholder="/scratch/$USER/$SLURM_JOB_ID"
          onChange={(event) => setScratch(event.target.value)}
        />
      </div>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        <button type="button" style={primaryButton} disabled={busy || !raw} onClick={() => void review()}>
          {busy ? "Working…" : "Review"}
        </button>
        <button type="button" style={secondaryButton} disabled={busy || !entry} onClick={() => void testConnection()}>
          Test connection
        </button>
        <button type="button" style={secondaryButton} disabled={busy || !entry || !tested} onClick={() => void save()}>
          Save
        </button>
        <button type="button" style={secondaryButton} onClick={onClose}>
          Close
        </button>
      </div>
      {error ? (
        <div role="alert" style={{ color: "#ff6259", marginTop: "0.6rem", fontSize: "0.82rem" }}>
          {error}
        </div>
      ) : null}
      {report ? (
        <p style={{ color: "#8b949e", fontSize: "0.8rem" }}>
          Connection test finished with {report.checks.filter((check) => check.ok).length} of{" "}
          {report.checks.length} checks passing.
        </p>
      ) : null}
      {rows ? <ReviewList rows={rows} /> : null}
    </section>
  );
}
