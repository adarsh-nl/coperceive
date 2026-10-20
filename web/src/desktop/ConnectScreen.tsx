// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { LocalExecutionTarget, pickDataRoot } from "./local";
import type { ConnectionSession } from "./types";

interface ConnectScreenProps {
  onConnected: (session: ConnectionSession) => void;
}

export default function ConnectScreen({ onConnected }: ConnectScreenProps) {
  const [dataRoot, setDataRoot] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  const handlePickFolder = async () => {
    try {
      const folder = await pickDataRoot();
      if (folder) {
        setDataRoot(folder);
        setError("");
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const handleConnectLocal = async () => {
    if (!dataRoot) {
      setError("Please select a dataset folder first.");
      return;
    }
    setBusy(true);
    setError("");
    setStatus("Launching local perception server...");

    try {
      const target = new LocalExecutionTarget();
      const session = await target.connect({
        type: "local",
        dataRoot,
      });
      setStatus("Connected! Initializing 3D viewer...");
      onConnected(session);
    } catch (e) {
      setError(`Failed to connect: ${e instanceof Error ? e.message : String(e)}`);
      setStatus("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "100vh",
      padding: "2rem",
      backgroundColor: "var(--bg, #111)",
      color: "var(--fg, #eee)",
      fontFamily: "var(--font-sans, system-ui, sans-serif)",
    }}>
      <div style={{
        maxWidth: "480px",
        width: "100%",
        backgroundColor: "var(--card-bg, #1a1a1a)",
        borderRadius: "12px",
        border: "1px solid var(--border, #333)",
        padding: "2rem",
        boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
      }}>
        <h1 style={{ fontSize: "1.5rem", marginBottom: "0.5rem", fontWeight: 600 }}>Coperceive</h1>
        <p style={{ color: "var(--muted, #888)", fontSize: "0.9rem", marginBottom: "1.5rem" }}>
          Choose an execution target to launch or attach to perception data.
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div style={{
            padding: "1rem",
            borderRadius: "8px",
            border: "1px solid var(--border-subtle, #444)",
            backgroundColor: "var(--bg-subtle, #222)",
          }}>
            <h2 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>Local Dataset</h2>
            <p style={{ fontSize: "0.85rem", color: "#aaa", marginBottom: "1rem" }}>
              Run bundled server locally on converted scenes.
            </p>

            <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
              <input
                type="text"
                readOnly
                placeholder="No directory selected..."
                value={dataRoot}
                style={{
                  flex: 1,
                  padding: "0.5rem 0.75rem",
                  fontSize: "0.85rem",
                  borderRadius: "6px",
                  border: "1px solid #444",
                  backgroundColor: "#181818",
                  color: "#eee",
                }}
              />
              <button
                type="button"
                onClick={handlePickFolder}
                disabled={busy}
                style={{
                  padding: "0.5rem 1rem",
                  fontSize: "0.85rem",
                  borderRadius: "6px",
                  cursor: "pointer",
                  border: "1px solid #555",
                  backgroundColor: "#333",
                  color: "#fff",
                }}
              >
                Choose...
              </button>
            </div>

            <button
              type="button"
              onClick={handleConnectLocal}
              disabled={busy || !dataRoot}
              style={{
                width: "100%",
                padding: "0.6rem 1rem",
                fontSize: "0.9rem",
                fontWeight: 500,
                borderRadius: "6px",
                cursor: busy || !dataRoot ? "not-allowed" : "pointer",
                border: "none",
                backgroundColor: busy || !dataRoot ? "#444" : "#2563eb",
                color: "#fff",
              }}
            >
              {busy ? "Connecting..." : "Open Local Viewer"}
            </button>
          </div>

          <div style={{
            padding: "1rem",
            borderRadius: "8px",
            border: "1px dashed var(--border-subtle, #333)",
            opacity: 0.7,
          }}>
            <h2 style={{ fontSize: "1rem", marginBottom: "0.25rem" }}>HPC over SSH</h2>
            <p style={{ fontSize: "0.85rem", color: "#888" }}>
              Slurm GPU & dataset tunnel (Phase 3).
            </p>
          </div>
        </div>

        {status && (
          <div style={{ marginTop: "1rem", fontSize: "0.85rem", color: "#60a5fa" }}>
            {status}
          </div>
        )}

        {error && (
          <div style={{ marginTop: "1rem", fontSize: "0.85rem", color: "#f87171" }}>
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
