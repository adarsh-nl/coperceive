// SPDX-License-Identifier: Apache-2.0
/** Export review for one result bundle. Nothing is written until Save. */
import { useState } from "react";
import { bundleExport, bundlePreview, pickSavePath, type BundleManifest } from "./local";
import { errorText } from "./errors";

const field = { marginBottom: "0.6rem" } as const;
const label = { display: "block", fontSize: "0.7rem", textTransform: "uppercase" as const, color: "var(--text-subtle)" };
const input = { width: "100%", boxSizing: "border-box" as const, padding: "0.35rem 0.5rem", fontSize: "0.8rem" };
const button = { padding: "0.35rem 0.7rem", fontSize: "0.8rem" } as const;

export default function BundlePanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [scenesRoot, setScenesRoot] = useState("");
  const [predictionsRoot, setPredictionsRoot] = useState("");
  const [frames, setFrames] = useState("worst:6");
  const [title, setTitle] = useState("");
  const [includeRaw, setIncludeRaw] = useState(false);
  const [includeFull, setIncludeFull] = useState(false);
  const [manifest, setManifest] = useState<BundleManifest | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState("");

  const preview = async () => {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      setManifest(await bundlePreview(runId, scenesRoot.trim(), predictionsRoot.trim(), frames, title, includeRaw, includeFull));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!manifest) return;
    setBusy(true);
    setError("");
    try {
      const chosen = await pickSavePath(`${runId}.cpbundle`, "cpbundle");
      if (!chosen) return;
      setSaved(await bundleExport(runId, scenesRoot.trim(), predictionsRoot.trim(), chosen, frames, title, includeRaw, includeFull));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: "0.75rem", borderTop: "1px solid var(--line)", paddingTop: "0.75rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Export bundle</strong>
        <button type="button" style={button} onClick={onClose}>Close</button>
      </div>
      <p style={{ color: "var(--text-subtle)", fontSize: "0.8rem" }}>
        The bundle holds the scrubbed repro card, scores, selected predictions and previews. Nothing is uploaded.
      </p>
      <div style={field}><label style={label}>Scenes folder</label><input style={input} value={scenesRoot} onChange={(e) => setScenesRoot(e.target.value)} placeholder="/data/scenes" /></div>
      <div style={field}><label style={label}>Predictions folder</label><input style={input} value={predictionsRoot} onChange={(e) => setPredictionsRoot(e.target.value)} placeholder="/data/predictions" /></div>
      <div style={field}><label style={label}>Frames</label><input style={input} value={frames} onChange={(e) => setFrames(e.target.value)} /></div>
      <div style={field}><label style={label}>Title</label><input style={input} value={title} onChange={(e) => setTitle(e.target.value)} /></div>
      <label style={{ display: "flex", gap: "6px", fontSize: "0.8rem" }}>
        <input type="checkbox" checked={includeFull} onChange={(e) => setIncludeFull(e.target.checked)} /> Include full predictions
      </label>
      <label style={{ display: "flex", gap: "6px", fontSize: "0.8rem" }}>
        <input type="checkbox" checked={includeRaw} onChange={(e) => setIncludeRaw(e.target.checked)} /> Include raw sensor files for selected frames
      </label>
      {includeRaw ? <p style={{ color: "#d29922", fontSize: "0.8rem" }}>Check the dataset licence before sharing raw data.</p> : null}
      <div style={{ display: "flex", gap: "8px" }}>
        <button type="button" style={button} disabled={busy || !scenesRoot.trim() || !predictionsRoot.trim()} onClick={() => void preview()}>
          {busy ? "Preparing…" : "Review bundle"}
        </button>
        <button type="button" style={button} disabled={busy || !manifest} onClick={() => void save()}>Save bundle</button>
      </div>
      {error ? <div role="alert" style={{ color: "var(--danger)", marginTop: "0.4rem" }}>{error}</div> : null}
      {manifest ? (
        <div style={{ marginTop: "0.6rem", fontSize: "0.8rem" }}>
          <div>{manifest.contents.length} files, {manifest.total_bytes ?? 0} bytes total</div>
          <div>Scrubber redacted {String(manifest.privacy?.redacted_files ?? 0)} file(s).</div>
          {manifest.licences && manifest.licences.length > 0 ? <div>Licence notes: {JSON.stringify(manifest.licences)}</div> : <div>No raw data included.</div>}
          <ul style={{ maxHeight: "10rem", overflow: "auto", paddingLeft: "1rem" }}>
            {manifest.contents.map((item) => <li key={item.path}>{item.path} — {item.size} B</li>)}
          </ul>
          <div role="status" style={{ color: "var(--ok)" }}>{saved ? `Saved ${saved}` : "Review only; Save writes the file."}</div>
        </div>
      ) : null}
    </div>
  );
}
