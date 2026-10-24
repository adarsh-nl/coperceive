// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import {
  LocalExecutionTarget,
  cancelImport,
  getPersistentState,
  getStorageInfo,
  isRemoteEnabled,
  isTauriEnvironment,
  onImportProgress,
  pickDataRoot,
  startImport,
  type ImportImages,
} from "./local";
import type { ConnectionSession } from "./types";
import { BrandWordmark } from "./Brand";
import ConnectBackdrop from "./backdrop/ConnectBackdrop";
import LibraryScreen from "./LibraryScreen";
import GetDataScreen from "./GetDataScreen";
import UpdateBanner from "./UpdateBanner";

interface ConnectScreenProps {
  onConnected: (session: ConnectionSession) => void;
}

type TabMode = "library" | "get_data" | "local" | "remote";

export default function ConnectScreen({ onConnected }: ConnectScreenProps) {
  const [activeTab, setActiveTab] = useState<TabMode>("library");
  const [dataRoot, setDataRoot] = useState<string>("");
  const [recentRoots, setRecentRoots] = useState<string[]>([]);
  const [showRemote, setShowRemote] = useState<boolean>(false);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  // Import dialog state
  const [importOpen, setImportOpen] = useState<boolean>(false);
  const [importSource, setImportSource] = useState<string>("");
  const [importDest, setImportDest] = useState<string>("");
  const [importImages, setImportImages] = useState<ImportImages>("full");
  const [importMedia, setImportMedia] = useState<string>("");
  const [importGriffin, setImportGriffin] = useState<string>("");
  const [importing, setImporting] = useState<boolean>(false);
  const [importPercent, setImportPercent] = useState<number>(0);
  const [importNote, setImportNote] = useState<string>("");

  useEffect(() => {
    void (async () => {
      const state = await getPersistentState();
      if (state?.recent_roots && state.recent_roots.length > 0) {
        setRecentRoots(state.recent_roots);
        if (!dataRoot && state.last_data_root) {
          setDataRoot(state.last_data_root);
        }
      }
      const stats = await getStorageInfo();
      if (stats?.library_root) {
        setImportDest(stats.library_root);
      }
      const remote = await isRemoteEnabled();
      setShowRemote(remote);
    })();

    if (!isTauriEnvironment()) return;

    let unlistenImport: (() => void) | undefined;
    let unlistenGetData: (() => void) | undefined;

    void (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      unlistenImport = await listen("import-request", () => {
        setImportOpen(true);
        setActiveTab("local");
      });
      unlistenGetData = await listen("navigate-get-data", () => {
        setActiveTab("get_data");
      });
    })();

    return () => {
      unlistenImport?.();
      unlistenGetData?.();
    };
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

  const handleImport = async () => {
    if (!importSource) {
      setError("Choose the scene folder to import.");
      return;
    }
    setImporting(true);
    setImportPercent(0);
    setImportNote("Starting…");
    setError("");
    const stop = await onImportProgress((event) => {
      if (event.event === "file" && event.total) {
        setImportPercent(Math.round(((event.done ?? 0) / event.total) * 100));
        setImportNote(`${event.done ?? 0} / ${event.total}${event.skipped ? " (already copied)" : ""}`);
      } else if (event.event === "error") {
        setImportNote(event.error ?? "A file was skipped.");
      } else if (event.event === "exit") {
        setImporting(false);
        stop();
        if (event.code === 0) {
          setImportNote("Import finished.");
          if (importDest) {
            setDataRoot(importDest);
          }
        } else {
          setError("Import stopped. Choose the same folders to resume.");
        }
      }
    });
    try {
      await startImport({
        source: importSource,
        dest: importDest,
        images: importImages,
        mediaRoots: importMedia ? [importMedia] : [],
        griffinRoot: importGriffin,
      });
    } catch (e) {
      setImporting(false);
      setError(e instanceof Error ? e.message : String(e));
      stop();
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

    const target = new LocalExecutionTarget();
    try {
      const session = await target.connect({
        type: "local",
        dataRoot: rootToUse,
        startFresh,
      });
      clearInterval(timer);
      setBusy(false);
      setStatus("");
      onConnected(session);
    } catch (err: unknown) {
      clearInterval(timer);
      setBusy(false);
      setStatus("");
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        minHeight: "100vh",
        backgroundColor: "#000000",
        color: "#f5f5f7",
        fontFamily: "'Geist', system-ui, -apple-system, sans-serif",
      }}
    >
      <UpdateBanner />
      <ConnectBackdrop hasRecent={recentRoots.length > 0} />

      {/* Top Header & Tab Navigation */}
      <header
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          background: "rgba(20, 20, 20, 0.8)",
          backdropFilter: "blur(20px)",
          borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
          padding: "0.8rem 2rem",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "2rem" }}>
          <h1 style={{ fontSize: "1.3rem", margin: 0, fontWeight: 600, color: "#f5f5f7" }}>
            <BrandWordmark blink />
          </h1>

          <nav style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              onClick={() => setActiveTab("library")}
              style={{
                background: activeTab === "library" ? "rgba(255, 255, 255, 0.12)" : "transparent",
                color: activeTab === "library" ? "#fff" : "#a1a1a6",
                border: "none",
                borderRadius: "6px",
                padding: "0.4rem 0.8rem",
                fontSize: "0.85rem",
                fontWeight: activeTab === "library" ? 600 : 400,
                cursor: "pointer",
              }}
            >
              Library
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("get_data")}
              style={{
                background: activeTab === "get_data" ? "rgba(255, 255, 255, 0.12)" : "transparent",
                color: activeTab === "get_data" ? "#fff" : "#a1a1a6",
                border: "none",
                borderRadius: "6px",
                padding: "0.4rem 0.8rem",
                fontSize: "0.85rem",
                fontWeight: activeTab === "get_data" ? 600 : 400,
                cursor: "pointer",
              }}
            >
              Get Data
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("local")}
              style={{
                background: activeTab === "local" ? "rgba(255, 255, 255, 0.12)" : "transparent",
                color: activeTab === "local" ? "#fff" : "#a1a1a6",
                border: "none",
                borderRadius: "6px",
                padding: "0.4rem 0.8rem",
                fontSize: "0.85rem",
                fontWeight: activeTab === "local" ? 600 : 400,
                cursor: "pointer",
              }}
            >
              Open Folder
            </button>
            {showRemote && (
              <button
                type="button"
                onClick={() => setActiveTab("remote")}
                style={{
                  background: activeTab === "remote" ? "rgba(255, 255, 255, 0.12)" : "transparent",
                  color: activeTab === "remote" ? "#fff" : "#a1a1a6",
                  border: "none",
                  borderRadius: "6px",
                  padding: "0.4rem 0.8rem",
                  fontSize: "0.85rem",
                  fontWeight: activeTab === "remote" ? 600 : 400,
                  cursor: "pointer",
                }}
              >
                HPC Cluster
              </button>
            )}
          </nav>
        </div>

        {status && (
          <div style={{ fontSize: "0.85rem", color: "#1f6feb", fontWeight: 500 }}>
            {status}
          </div>
        )}
      </header>

      {/* Main Content Area */}
      <main style={{ flex: 1, position: "relative", zIndex: 1, padding: "1.5rem 0" }}>
        {activeTab === "library" && (
          <LibraryScreen
            onOpenScene={(scenePath) => void handleConnectLocal(scenePath)}
            onNavigateGetData={() => setActiveTab("get_data")}
          />
        )}

        {activeTab === "get_data" && (
          <GetDataScreen
            onOpenScene={(scenePath) => void handleConnectLocal(scenePath)}
            onOpenImportFolder={(folder) => {
              if (folder) setImportSource(folder);
              setImportOpen(true);
              setActiveTab("local");
            }}
          />
        )}

        {activeTab === "local" && (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              padding: "2rem",
            }}
          >
            <div
              className="connect-card"
              style={{
                maxWidth: "520px",
                width: "100%",
                backgroundColor: "rgba(28, 28, 30, 0.85)",
                backdropFilter: "blur(22px)",
                borderRadius: "14px",
                border: "1px solid rgba(255, 255, 255, 0.14)",
                padding: "2rem",
                boxShadow: "0 20px 50px rgba(0, 0, 0, 0.6)",
              }}
            >
              <h2 style={{ fontSize: "1.2rem", marginBottom: "0.5rem", color: "#f5f5f7", fontWeight: 600 }}>
                Open Local Folder
              </h2>
              <p style={{ fontSize: "0.85rem", color: "#86868b", marginBottom: "1.2rem" }}>
                Browse directly to any folder holding schema 1 scenes or datasets.
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

              <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem" }}>
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
                    backgroundColor: busy || !dataRoot ? "rgba(255, 255, 255, 0.1)" : "#1f6feb",
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

              {/* Import from folder section */}
              <div style={{ borderTop: "1px solid rgba(255, 255, 255, 0.1)", paddingTop: "1rem" }}>
                <button
                  type="button"
                  onClick={() => setImportOpen((open) => !open)}
                  disabled={busy}
                  style={{
                    background: "transparent",
                    border: "1px solid rgba(255, 255, 255, 0.15)",
                    borderRadius: "6px",
                    padding: "0.4rem 0.8rem",
                    color: "#f5f5f7",
                    fontSize: "0.85rem",
                    cursor: "pointer",
                    width: "100%",
                  }}
                >
                  {importOpen ? "Hide Import from folder" : "Import from folder…"}
                </button>
                {importOpen && (
                  <div style={{ marginTop: "1rem", display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                    <p style={{ fontSize: "0.8rem", color: "#a1a1a6", margin: 0 }}>
                      Copies and packages the scene directly into your library folder.
                    </p>
                    <button
                      type="button"
                      onClick={async () => {
                        const folder = await pickDataRoot();
                        if (folder) setImportSource(folder);
                      }}
                      style={{
                        padding: "0.4rem 0.6rem",
                        textAlign: "left",
                        background: "rgba(0, 0, 0, 0.3)",
                        border: "1px solid rgba(255, 255, 255, 0.1)",
                        borderRadius: "5px",
                        color: "#f5f5f7",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                      }}
                    >
                      Scene source: {importSource || "Choose folder…"}
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        const folder = await pickDataRoot();
                        if (folder) setImportDest(folder);
                      }}
                      style={{
                        padding: "0.4rem 0.6rem",
                        textAlign: "left",
                        background: "rgba(0, 0, 0, 0.3)",
                        border: "1px solid rgba(255, 255, 255, 0.1)",
                        borderRadius: "5px",
                        color: "#f5f5f7",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                      }}
                    >
                      Library dest: {importDest || "Default library"}
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        const folder = await pickDataRoot();
                        if (folder) setImportMedia(folder);
                      }}
                      style={{
                        padding: "0.4rem 0.6rem",
                        textAlign: "left",
                        background: "rgba(0, 0, 0, 0.3)",
                        border: "1px solid rgba(255, 255, 255, 0.1)",
                        borderRadius: "5px",
                        color: "#f5f5f7",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                      }}
                    >
                      Media root: {importMedia || "optional"}
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        const folder = await pickDataRoot();
                        if (folder) setImportGriffin(folder);
                      }}
                      style={{
                        padding: "0.4rem 0.6rem",
                        textAlign: "left",
                        background: "rgba(0, 0, 0, 0.3)",
                        border: "1px solid rgba(255, 255, 255, 0.1)",
                        borderRadius: "5px",
                        color: "#f5f5f7",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                      }}
                    >
                      Griffin root: {importGriffin || "optional"}
                    </button>
                    <label style={{ fontSize: "0.8rem", color: "#a1a1a6", display: "flex", alignItems: "center" }}>
                      Images:
                      <select
                        aria-label="Image copy mode"
                        value={importImages}
                        onChange={(event) => setImportImages(event.target.value as ImportImages)}
                        style={{
                          marginLeft: "0.5rem",
                          background: "rgba(0,0,0,0.5)",
                          color: "#f5f5f7",
                          border: "1px solid rgba(255,255,255,0.2)",
                          borderRadius: "4px",
                          padding: "0.2rem 0.4rem",
                        }}
                      >
                        <option value="full">full</option>
                        <option value="preview">preview</option>
                        <option value="none">none</option>
                      </select>
                    </label>

                    <div
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={importPercent}
                      aria-label="Import progress"
                      style={{ height: "6px", background: "rgba(255,255,255,0.12)", borderRadius: "3px" }}
                    >
                      <div style={{ width: `${importPercent}%`, height: "100%", background: "#1f6feb", borderRadius: "3px" }} />
                    </div>
                    <p style={{ fontSize: "0.75rem", color: "#86868b", margin: 0 }}>{importNote}</p>

                    <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.2rem" }}>
                      <button
                        type="button"
                        onClick={() => void handleImport()}
                        disabled={importing || !importSource}
                        style={{
                          flex: 1,
                          padding: "0.4rem",
                          borderRadius: "5px",
                          border: "none",
                          background: importing || !importSource ? "rgba(255,255,255,0.1)" : "#1f6feb",
                          color: "#fff",
                          fontWeight: 500,
                          cursor: importing || !importSource ? "not-allowed" : "pointer",
                          fontSize: "0.8rem",
                        }}
                      >
                        {importing ? "Importing…" : "Start import"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void cancelImport()}
                        disabled={!importing}
                        style={{
                          padding: "0.4rem 0.8rem",
                          borderRadius: "5px",
                          border: "1px solid rgba(255,255,255,0.2)",
                          background: "transparent",
                          color: "#ff6b6b",
                          cursor: !importing ? "not-allowed" : "pointer",
                          fontSize: "0.8rem",
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Recent Datasets */}
              {recentRoots.length > 0 && (
                <div style={{ marginTop: "1.2rem", borderTop: "1px solid rgba(255, 255, 255, 0.08)", paddingTop: "0.8rem" }}>
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
                            border: `1px solid ${dataRoot === root ? "#1f6feb" : "rgba(255, 255, 255, 0.08)"}`,
                            borderRadius: "6px",
                            cursor: "pointer",
                          }}
                        >
                          <span style={{ fontWeight: 500 }}>📁 {base}</span>
                          <span style={{ color: "#86868b", fontSize: "0.72rem", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "'Geist Mono', monospace" }}>
                            {root}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "remote" && showRemote && (
          <div style={{ display: "flex", justifyContent: "center", padding: "2rem" }}>
            <div
              style={{
                maxWidth: "520px",
                width: "100%",
                padding: "2rem",
                borderRadius: "14px",
                background: "rgba(28, 28, 30, 0.85)",
                border: "1px dashed rgba(255, 255, 255, 0.15)",
                textAlign: "center",
              }}
            >
              <h3 style={{ margin: "0 0 0.5rem 0", color: "#f5f5f7" }}>HPC Cluster (SSH & Slurm)</h3>
              <p style={{ color: "#8b949e", fontSize: "0.9rem" }}>
                Connected to university and cloud GPU partitions.
              </p>
            </div>
          </div>
        )}

        {error && (
          <div
            role="alert"
            style={{
              maxWidth: "520px",
              margin: "1rem auto",
              padding: "0.75rem 1rem",
              borderRadius: "8px",
              backgroundColor: "rgba(255, 98, 89, 0.12)",
              border: "1px solid rgba(255, 98, 89, 0.35)",
              color: "#ff6259",
              fontSize: "0.85rem",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>{error}</span>
            <button
              type="button"
              onClick={() => setError("")}
              style={{ background: "transparent", border: "none", color: "#a1a1a6", cursor: "pointer" }}
            >
              ✕
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
