// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { LocalExecutionTarget, getPersistentState, isRemoteEnabled, pickDataRoot } from "./local";
import type { ConnectionSession } from "./types";
import ConnectBackdrop from "./backdrop/ConnectBackdrop";

interface ConnectScreenProps {
  onConnected: (session: ConnectionSession) => void;
}

export default function ConnectScreen({ onConnected }: ConnectScreenProps) {
  const [dataRoot, setDataRoot] = useState<string>("");
  const [recentRoots, setRecentRoots] = useState<string[]>([]);
  const [showRemote, setShowRemote] = useState<boolean>(false);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  useEffect(() => {
    void (async () => {
      const state = await getPersistentState();
      if (state?.recent_roots && state.recent_roots.length > 0) {
        setRecentRoots(state.recent_roots);
        // Pre-select the most recent data root if not yet set
        if (!dataRoot && state.last_data_root) {
          setDataRoot(state.last_data_root);
        }
      }
      const remote = await isRemoteEnabled();
      setShowRemote(remote);
    })();
  }, []);

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

  const handleConnectLocal = async (targetRoot?: string, startFresh = false) => {
    const rootToUse = targetRoot || dataRoot;
    if (!rootToUse) {
      setError("Please select a dataset folder first.");
      return;
    }
    setBusy(true);
    setError("");
    let seconds = 0;
    setStatus(`Starting server… ${seconds} s`);
    const timer = setInterval(() => {
      seconds += 1;
      setStatus(`Starting server… ${seconds} s`);
    }, 1000);

    try {
      const target = new LocalExecutionTarget();
      const session = await target.connect({
        type: "local",
        dataRoot: rootToUse,
        startFresh,
      });
      clearInterval(timer);
      setStatus("Connected! Initializing 3D viewer...");
      onConnected(session);
    } catch (e) {
      clearInterval(timer);
      setError(e instanceof Error ? e.message : String(e));
      setStatus("");
    } finally {
      clearInterval(timer);
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        padding: "2rem",
        backgroundColor: "#000000",
        color: "#f5f5f7",
        fontFamily: "'Geist', system-ui, -apple-system, sans-serif",
      }}
    >
      <ConnectBackdrop hasRecent={recentRoots.length > 0} />

      <div
        className="connect-card"
        style={{
          position: "relative",
          zIndex: 1,
          maxWidth: "500px",
          width: "100%",
          backgroundColor: "rgba(28, 28, 30, 0.78)",
          backdropFilter: "blur(22px)",
          WebkitBackdropFilter: "blur(22px)",
          borderRadius: "14px",
          border: "1px solid rgba(255, 255, 255, 0.14)",
          padding: "2rem",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.6)",
        }}
      >
        <h1 style={{ fontSize: "1.5rem", marginBottom: "0.5rem", fontWeight: 600, color: "#f5f5f7" }}>
          Coperceive
        </h1>
        <p style={{ color: "#a1a1a6", fontSize: "0.9rem", marginBottom: "1.5rem" }}>
          Choose a local cooperative perception dataset to launch the viewer.
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div
            style={{
              padding: "1rem",
              borderRadius: "10px",
              border: "1px solid rgba(255, 255, 255, 0.08)",
              backgroundColor: "rgba(0, 0, 0, 0.35)",
            }}
          >
            <h2 style={{ fontSize: "1rem", marginBottom: "0.5rem", color: "#f5f5f7", fontWeight: 500 }}>
              Local Dataset
            </h2>
            <p style={{ fontSize: "0.85rem", color: "#86868b", marginBottom: "1rem" }}>
              Run bundled server locally on converted scenes.
            </p>

            <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
              <input
                type="text"
                readOnly
                placeholder="No directory selected..."
                value={dataRoot}
                aria-label="Selected dataset folder"
                style={{
                  flex: 1,
                  padding: "0.5rem 0.75rem",
                  fontSize: "0.85rem",
                  borderRadius: "6px",
                  border: "1px solid rgba(255, 255, 255, 0.14)",
                  backgroundColor: "rgba(0, 0, 0, 0.5)",
                  color: "#f5f5f7",
                  fontFamily: "'Geist Mono', ui-monospace, monospace",
                }}
              />
              <button
                type="button"
                onClick={handlePickFolder}
                disabled={busy}
                aria-label="Choose dataset folder"
                style={{
                  padding: "0.5rem 1rem",
                  fontSize: "0.85rem",
                  borderRadius: "6px",
                  cursor: "pointer",
                  border: "1px solid rgba(255, 255, 255, 0.14)",
                  backgroundColor: "rgba(255, 255, 255, 0.08)",
                  color: "#f5f5f7",
                  fontWeight: 500,
                }}
              >
                Choose…
              </button>
            </div>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={() => handleConnectLocal()}
                disabled={busy || !dataRoot}
                aria-label="Open Local Viewer"
                style={{
                  flex: 1,
                  padding: "0.6rem 1rem",
                  fontSize: "0.9rem",
                  fontWeight: 500,
                  borderRadius: "6px",
                  cursor: busy || !dataRoot ? "not-allowed" : "pointer",
                  border: "none",
                  backgroundColor: busy || !dataRoot ? "rgba(255, 255, 255, 0.1)" : "#2878dc",
                  color: "#fff",
                }}
              >
                {busy ? "Connecting…" : "Open Local Viewer"}
              </button>
              <button
                type="button"
                onClick={() => handleConnectLocal(undefined, true)}
                disabled={busy || !dataRoot}
                title="Connect without restoring saved session state"
                aria-label="Start fresh without restoring saved session state"
                style={{
                  padding: "0.6rem 0.85rem",
                  fontSize: "0.85rem",
                  fontWeight: 500,
                  borderRadius: "6px",
                  cursor: busy || !dataRoot ? "not-allowed" : "pointer",
                  border: "1px solid rgba(255, 255, 255, 0.14)",
                  backgroundColor: "rgba(0, 0, 0, 0.3)",
                  color: "#a1a1a6",
                  whiteSpace: "nowrap",
                }}
              >
                Start fresh
              </button>
            </div>

            {recentRoots.length > 0 && (
              <div style={{ marginTop: "1rem", borderTop: "1px solid rgba(255, 255, 255, 0.08)", paddingTop: "0.75rem" }}>
                <p style={{ fontSize: "0.75rem", color: "#86868b", marginBottom: "0.4rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  Recent Datasets
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                  {recentRoots.map((root) => {
                    const base = root.split("/").filter(Boolean).pop() || root;
                    return (
                      <button
                        key={root}
                        type="button"
                        onClick={() => {
                          setDataRoot(root);
                          setError("");
                        }}
                        aria-label={`Select recent dataset ${base}`}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          textAlign: "left",
                          padding: "0.4rem 0.6rem",
                          fontSize: "0.8rem",
                          backgroundColor: dataRoot === root ? "rgba(40, 120, 220, 0.2)" : "rgba(255, 255, 255, 0.04)",
                          color: "#f5f5f7",
                          border: `1px solid ${dataRoot === root ? "#2878dc" : "rgba(255, 255, 255, 0.08)"}`,
                          borderRadius: "6px",
                          cursor: "pointer",
                        }}
                      >
                        <span style={{ fontWeight: 500 }}>📁 {base}</span>
                        <span style={{ color: "#86868b", fontSize: "0.72rem", maxWidth: "220px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "'Geist Mono', ui-monospace, monospace" }}>
                          {root}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {showRemote && (
            <div
              style={{
                padding: "1rem",
                borderRadius: "10px",
                border: "1px dashed rgba(255, 255, 255, 0.14)",
                opacity: 0.7,
              }}
            >
              <h2 style={{ fontSize: "1rem", marginBottom: "0.25rem", color: "#f5f5f7", fontWeight: 500 }}>
                HPC over SSH
              </h2>
              <p style={{ fontSize: "0.85rem", color: "#86868b" }}>
                Slurm GPU & dataset tunnel (Phase 3).
              </p>
            </div>
          )}
        </div>

        {status && (
          <div style={{ marginTop: "1rem", fontSize: "0.85rem", color: "#2878dc" }}>
            {status}
          </div>
        )}

        {error && (
          <div
            className="connect-error-banner"
            role="alert"
            style={{
              marginTop: "1rem",
              padding: "0.75rem 1rem",
              borderRadius: "8px",
              backgroundColor: "rgba(255, 98, 89, 0.12)",
              border: "1px solid rgba(255, 98, 89, 0.35)",
            }}
          >
            <div style={{ fontSize: "0.85rem", color: "#ff6259", marginBottom: "0.6rem", lineHeight: 1.4 }}>
              {error}
            </div>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={handlePickFolder}
                aria-label="Choose another dataset folder"
                style={{
                  padding: "0.35rem 0.75rem",
                  fontSize: "0.8rem",
                  borderRadius: "5px",
                  border: "1px solid rgba(255, 255, 255, 0.14)",
                  backgroundColor: "rgba(255, 255, 255, 0.1)",
                  color: "#f5f5f7",
                  cursor: "pointer",
                  fontWeight: 500,
                }}
              >
                Choose another folder
              </button>
              <button
                type="button"
                onClick={() => setError("")}
                aria-label="Dismiss error"
                style={{
                  padding: "0.35rem 0.75rem",
                  fontSize: "0.8rem",
                  borderRadius: "5px",
                  border: "none",
                  backgroundColor: "transparent",
                  color: "#86868b",
                  cursor: "pointer",
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
