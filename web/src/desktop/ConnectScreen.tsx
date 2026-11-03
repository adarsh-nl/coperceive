// SPDX-License-Identifier: Apache-2.0
import { JobsIndicator } from "./JobsIndicator";
import { useEffect, useState } from "react";
import {
  LocalExecutionTarget,
  cancelImport,
  getPersistentState,
  getStorageInfo,
  isTauriEnvironment,
  onImportProgress,
  pickDataRoot,
  startImport,
  type ImportImages,
} from "./local";
import type { ConnectionSession } from "./types";
import { BrandWordmark } from "./Brand";
import IdentifierInput from "./IdentifierInput";
import HpcWorkspace from "./HpcWorkspace";
import LibraryScreen from "./LibraryScreen";
import GetDataScreen from "./GetDataScreen";
import UpdateBanner from "./UpdateBanner";
import PanelState from "./PanelState";
import { PANEL_DEFINITIONS } from "./panelStates";
import { errorText } from "./errors";
import { Sidebar } from "./components/Sidebar";
import { IconButton } from "./components/IconButton";
import { SettingsModal } from "./SettingsView";

interface ConnectScreenProps {
  onConnected: (session: ConnectionSession) => void;
}

type TabMode = "library" | "get_data" | "local" | "remote";

export default function ConnectScreen({ onConnected }: ConnectScreenProps) {
  const [activeTab, setActiveTab] = useState<TabMode>(() => {
    if (typeof window !== "undefined") {
      try {
        if (sessionStorage.getItem("coperceive-connect-tab") === "remote") {
          sessionStorage.removeItem("coperceive-connect-tab");
          return "remote";
        }
      } catch {}
      if (window.location.hash.includes("evaluation")) {
        return "remote";
      }
    }
    return "library";
  });
  // A tab mounts the first time it is shown and then stays mounted (hidden),
  // so nothing remote is read at launch and nothing is lost by switching.
  const [visitedTabs, setVisitedTabs] = useState<ReadonlySet<TabMode>>(() => new Set([activeTab]));
  useEffect(() => {
    setVisitedTabs((current) => (current.has(activeTab) ? current : new Set([...current, activeTab])));
  }, [activeTab]);
  // Bumped when the Library is asked for again, so it re-reads the scenes.
  const [libraryKey, setLibraryKey] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dataRoot, setDataRoot] = useState<string>("");
  const [recentRoots, setRecentRoots] = useState<string[]>([]);
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
    const applyTestImport = () => {
      const src = window.__COPERCEIVE_TEST_IMPORT_SOURCE__;
      if (typeof src === "string" && src.length > 0) {
        setImportSource(src);
        setImportImages("none");
      }
    };
    applyTestImport();
    window.addEventListener("coperceive-test-import", applyTestImport);

    const handleTestPanelState = (event: Event) => {
      if (
        typeof window !== "undefined" &&
        !window.__COPERCEIVE_TEST_ENV__ &&
        import.meta.env.MODE !== "test"
      ) {
        return;
      }
      const custom = event as CustomEvent<{ busy?: boolean; error?: string; tab?: TabMode }>;
      if (typeof custom.detail?.busy === "boolean") setBusy(custom.detail.busy);
      if (typeof custom.detail?.error === "string") setError(custom.detail.error);
      if (custom.detail?.tab) setActiveTab(custom.detail.tab);
    };
    window.addEventListener("coperceive-test-panel-state", handleTestPanelState);
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
    })();

    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        setSettingsOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);

    if (!isTauriEnvironment()) {
      return () => {
        window.removeEventListener("coperceive-test-import", applyTestImport);
        window.removeEventListener("coperceive-test-panel-state", handleTestPanelState);
        window.removeEventListener("keydown", handleKeyDown);
      };
    }

    let unlistenImport: (() => void) | undefined;
    let unlistenGetData: (() => void) | undefined;
    let unlistenLibrary: (() => void) | undefined;
    let unlistenOpenFolder: (() => void) | undefined;
    let unlistenHpcWorkspace: (() => void) | undefined;
    let unlistenToggleSidebar: (() => void) | undefined;
    let unlistenSettings: (() => void) | undefined;

    void (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      unlistenImport = await listen("import-request", () => {
        setImportOpen(true);
        setActiveTab("local");
      });
      unlistenGetData = await listen("navigate-get-data", () => {
        setActiveTab("get_data");
      });
      unlistenLibrary = await listen("navigate-library", () => {
        setActiveTab("library");
        setLibraryKey((key) => key + 1);
      });
      unlistenOpenFolder = await listen("navigate-open-folder", () => {
        setActiveTab("local");
      });
      unlistenHpcWorkspace = await listen("navigate-hpc-workspace", () => {
        setActiveTab("remote");
      });
      unlistenToggleSidebar = await listen("toggle-sidebar", () => {
        setSidebarOpen((open) => !open);
      });
      unlistenSettings = await listen("open-settings", () => {
        setSettingsOpen(true);
      });
    })();

    const handleOpenEval = () => {
      setActiveTab("remote");
    };
    window.addEventListener("coperceive-open-evaluation", handleOpenEval);

    return () => {
      window.removeEventListener("coperceive-test-import", applyTestImport);
      window.removeEventListener("coperceive-test-panel-state", handleTestPanelState);
      window.removeEventListener("coperceive-open-evaluation", handleOpenEval);
      window.removeEventListener("keydown", handleKeyDown);
      unlistenImport?.();
      unlistenGetData?.();
      unlistenLibrary?.();
      unlistenOpenFolder?.();
      unlistenHpcWorkspace?.();
      unlistenToggleSidebar?.();
      unlistenSettings?.();
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
      setError(errorText(e));
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
      setError(errorText(err));
    }
  };

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        height: "100vh",
        width: "100vw",
        overflow: "hidden",
        backgroundColor: "var(--bg)",
        color: "var(--text)",
        fontFamily: "'Geist', system-ui, -apple-system, sans-serif",
      }}
    >
      <Sidebar
        collapsed={!sidebarOpen}
        onToggle={() => setSidebarOpen((open) => !open)}
        footer={
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="c-sidebar-item"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                background: "transparent",
                border: "none",
                color: "var(--text-secondary)",
                padding: "6px 8px",
                borderRadius: "var(--radius-md)",
                cursor: "pointer",
                fontSize: "12.5px",
                width: "100%",
                textAlign: "left",
              }}
            >
              <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
                <path d="M8 4.75a3.25 3.25 0 1 0 0 6.5 3.25 3.25 0 0 0 0-6.5zM6 8a2 2 0 1 1 4 0 2 2 0 0 1-4 0z" />
                <path d="M9.22 1.077a1.75 1.75 0 0 0-2.44 0l-.287.288a1.75 1.75 0 0 1-1.428.514l-.403-.048a1.75 1.75 0 0 0-1.724 1.724l.048.403a1.75 1.75 0 0 1-.514 1.428l-.288.287a1.75 1.75 0 0 0 0 2.44l.288.287a1.75 1.75 0 0 1 .514 1.428l-.048.403a1.75 1.75 0 0 0 1.724 1.724l.403-.048a1.75 1.75 0 0 1 1.428.514l.287.288a1.75 1.75 0 0 0 2.44 0l.287-.288a1.75 1.75 0 0 1 1.428-.514l.403.048a1.75 1.75 0 0 0 1.724-1.724l-.048-.403a1.75 1.75 0 0 1 .514-1.428l.288-.287a1.75 1.75 0 0 0 0-2.44l-.288-.287a1.75 1.75 0 0 1-.514-1.428l.048-.403a1.75 1.75 0 0 0-1.724-1.724l-.403.048a1.75 1.75 0 0 1-1.428-.514l-.287-.288z" />
              </svg>
              <span>Settings</span>
              <span style={{ marginLeft: "auto", fontSize: "11px", color: "var(--text-subtle)", fontFamily: "'Geist Mono', monospace" }}>⌘,</span>
            </button>
          </div>
        }
      >
        <div style={{ padding: "6px 8px 4px", fontSize: "11px", fontWeight: 500, color: "var(--text-subtle)", letterSpacing: "0.04em" }}>
          NAVIGATION
        </div>
        <nav style={{ display: "flex", flexDirection: "column", gap: "2px" }} role="tablist" aria-label="Connect navigation">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "library"}
            onClick={() => setActiveTab("library")}
            className={`c-sidebar-item ${activeTab === "library" ? "active" : ""}`}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 10px",
              borderRadius: "var(--radius-md, 6px)",
              fontSize: "13px",
              fontWeight: activeTab === "library" ? 500 : 400,
              color: activeTab === "library" ? "var(--text)" : "var(--text-secondary)",
              backgroundColor: activeTab === "library" ? "var(--line, rgba(255, 255, 255, 0.08))" : "transparent",
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              width: "100%",
            }}
          >
            <span>Library</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "get_data"}
            onClick={() => setActiveTab("get_data")}
            className={`c-sidebar-item ${activeTab === "get_data" ? "active" : ""}`}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 10px",
              borderRadius: "var(--radius-md, 6px)",
              fontSize: "13px",
              fontWeight: activeTab === "get_data" ? 500 : 400,
              color: activeTab === "get_data" ? "var(--text)" : "var(--text-secondary)",
              backgroundColor: activeTab === "get_data" ? "var(--line, rgba(255, 255, 255, 0.08))" : "transparent",
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              width: "100%",
            }}
          >
            <span>Get Data</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "local"}
            onClick={() => setActiveTab("local")}
            className={`c-sidebar-item ${activeTab === "local" ? "active" : ""}`}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 10px",
              borderRadius: "var(--radius-md, 6px)",
              fontSize: "13px",
              fontWeight: activeTab === "local" ? 500 : 400,
              color: activeTab === "local" ? "var(--text)" : "var(--text-secondary)",
              backgroundColor: activeTab === "local" ? "var(--line, rgba(255, 255, 255, 0.08))" : "transparent",
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              width: "100%",
            }}
          >
            <span>Open Folder</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "remote"}
            onClick={() => setActiveTab("remote")}
            className={`c-sidebar-item ${activeTab === "remote" ? "active" : ""}`}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 10px",
              borderRadius: "var(--radius-md, 6px)",
              fontSize: "13px",
              fontWeight: activeTab === "remote" ? 500 : 400,
              color: activeTab === "remote" ? "var(--text)" : "var(--text-secondary)",
              backgroundColor: activeTab === "remote" ? "var(--line, rgba(255, 255, 255, 0.08))" : "transparent",
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              width: "100%",
            }}
          >
            <span>HPC Workspace</span>
          </button>
        </nav>
      </Sidebar>

      <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, minHeight: 0, overflow: "hidden" }}>
        {/* Top Header */}
        <header
          data-tauri-drag-region
          style={{
            height: "52px",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: !sidebarOpen ? "0 20px 0 0" : "0 20px",
            borderBottom: "1px solid var(--line, rgba(255, 255, 255, 0.08))",
            backgroundColor: "var(--panel, #1a1a1c)",
            zIndex: 10,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px", minWidth: 0 }}>
            {!sidebarOpen && (
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <div data-tauri-drag-region className="traffic-lights-spacer" style={{ width: "78px", flexShrink: 0, height: "100%" }} aria-hidden="true" />
                <IconButton size="sm" label="Open sidebar (⌘\)" onClick={() => setSidebarOpen(true)}>
                  <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
                    <path d="M1.5 2.5A1 1 0 0 1 2.5 1.5h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11zm1 0v11h3.5v-11h-3.5zm4.5 0v11h6.5v-11H7z" />
                  </svg>
                </IconButton>
                <BrandWordmark fontSize="14px" />
              </div>
            )}
            <h1 style={{ fontSize: "15px", fontWeight: 600, margin: 0, color: "var(--text)" }}>
              {activeTab === "library" && "Library"}
              {activeTab === "get_data" && "Get Data"}
              {activeTab === "local" && "Open Local Folder"}
              {activeTab === "remote" && "HPC Workspace"}
            </h1>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
            {status && (
              <div style={{ fontSize: "0.85rem", color: "var(--primary, #1f6feb)", fontWeight: 500 }}>
                {status}
              </div>
            )}
            <JobsIndicator onOpenWorkspace={() => setActiveTab("remote")} />
            <IconButton size="sm" label="Settings (⌘,)" onClick={() => setSettingsOpen(true)}>
              <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
                <path d="M8 4.75a3.25 3.25 0 1 0 0 6.5 3.25 3.25 0 0 0 0-6.5zM6 8a2 2 0 1 1 4 0 2 2 0 0 1-4 0z" />
                <path d="M9.22 1.077a1.75 1.75 0 0 0-2.44 0l-.287.288a1.75 1.75 0 0 1-1.428.514l-.403-.048a1.75 1.75 0 0 0-1.724 1.724l.048.403a1.75 1.75 0 0 1-.514 1.428l-.288.287a1.75 1.75 0 0 0 0 2.44l.288.287a1.75 1.75 0 0 1 .514 1.428l-.048.403a1.75 1.75 0 0 0 1.724 1.724l.403-.048a1.75 1.75 0 0 1 1.428.514l.287.288a1.75 1.75 0 0 0 2.44 0l.287-.288a1.75 1.75 0 0 1 1.428-.514l.403.048a1.75 1.75 0 0 0 1.724-1.724l-.048-.403a1.75 1.75 0 0 1 .514-1.428l.288-.287a1.75 1.75 0 0 0 0-2.44l-.288-.287a1.75 1.75 0 0 1-.514-1.428l.048-.403a1.75 1.75 0 0 0-1.724-1.724l-.403.048a1.75 1.75 0 0 1-1.428-.514l-.287-.288z" />
              </svg>
            </IconButton>
          </div>
        </header>

        <main style={{ flex: 1, minHeight: 0, overflowY: "auto", position: "relative" }}>
          <UpdateBanner />
        {/* Every tab stays mounted (hidden when not shown): an open dataset,
            scroll and filters survive switching tabs. */}
        <div className="tab-panel" hidden={activeTab !== "library"}>
          <LibraryScreen
            key={libraryKey}
            onOpenScene={(scenePath) => void handleConnectLocal(scenePath)}
            onNavigateGetData={() => setActiveTab("get_data")}
          />
        </div>

        <div className="tab-panel" hidden={activeTab !== "get_data"}>
          <GetDataScreen
            onOpenScene={(scenePath) => void handleConnectLocal(scenePath)}
            onOpenImportFolder={(folder) => {
              if (folder) setImportSource(folder);
              setImportOpen(true);
              setActiveTab("local");
            }}
          />
        </div>

        <div className="tab-panel" hidden={activeTab !== "local"}>
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
                backgroundColor: "var(--panel)",
                borderRadius: "14px",
                border: "1px solid var(--line)",
                padding: "2rem",
                boxShadow: "var(--shadow)",
              }}
            >
              <h2 style={{ fontSize: "1.2rem", marginBottom: "0.5rem", color: "var(--text)", fontWeight: 600 }}>
                Open Local Folder
              </h2>
              <p style={{ fontSize: "0.85rem", color: "var(--muted)", marginBottom: "1.2rem" }}>
                Browse directly to any folder holding schema 1 scenes or datasets.
              </p>

              {busy && (
                <PanelState
                  compact
                  kind="loading"
                  title={PANEL_DEFINITIONS.connect.loading.title}
                  description={PANEL_DEFINITIONS.connect.loading.description}
                  actionLabel={PANEL_DEFINITIONS.connect.loading.actionLabel}
                  onAction={() => { setBusy(false); setStatus(""); }}
                />
              )}

              {!busy && !dataRoot && recentRoots.length === 0 && (
                <PanelState
                  compact
                  kind="empty"
                  title={PANEL_DEFINITIONS.connect.empty.title}
                  description={PANEL_DEFINITIONS.connect.empty.description}
                  actionLabel={PANEL_DEFINITIONS.connect.empty.actionLabel}
                  onAction={handlePickFolder}
                />
              )}

              <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
                <IdentifierInput
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
                    border: "1px solid var(--line)",
                    backgroundColor: "var(--sunken)",
                    color: "var(--text)",
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
                    border: "1px solid var(--line)",
                    backgroundColor: "var(--sunken)",
                    color: "var(--text)",
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
                    backgroundColor: busy || !dataRoot ? "var(--sunken)" : "var(--accent)",
                    color: busy || !dataRoot ? "var(--muted)" : "var(--accent-ink)",
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
                    border: "1px solid var(--line)",
                    backgroundColor: "var(--sunken)",
                    color: "var(--text)",
                    whiteSpace: "nowrap",
                  }}
                >
                  Start fresh
                </button>
              </div>

              {/* Import from folder section */}
              <div style={{ borderTop: "1px solid var(--line)", paddingTop: "1rem" }}>
                <button
                  type="button"
                  onClick={() => setImportOpen((open) => !open)}
                  disabled={busy}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--line)",
                    borderRadius: "6px",
                    padding: "0.4rem 0.8rem",
                    color: "var(--text)",
                    fontSize: "0.85rem",
                    cursor: "pointer",
                    width: "100%",
                  }}
                >
                  {importOpen ? "Hide Import from folder" : "Import from folder…"}
                </button>
                {importOpen && (
                  <div style={{ marginTop: "1rem", display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                    <p style={{ fontSize: "0.8rem", color: "var(--muted)", margin: 0 }}>
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
                        background: "var(--sunken)",
                        border: "1px solid var(--line)",
                        borderRadius: "5px",
                        color: "var(--text)",
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
                        background: "var(--sunken)",
                        border: "1px solid var(--line)",
                        borderRadius: "5px",
                        color: "var(--text)",
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
                        background: "var(--sunken)",
                        border: "1px solid var(--line)",
                        borderRadius: "5px",
                        color: "var(--text)",
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
                        background: "var(--sunken)",
                        border: "1px solid var(--line)",
                        borderRadius: "5px",
                        color: "var(--text)",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                      }}
                    >
                      Griffin root: {importGriffin || "optional"}
                    </button>
                    <label style={{ fontSize: "0.8rem", color: "var(--muted)", display: "flex", alignItems: "center" }}>
                      Images:
                      <select
                        aria-label="Image copy mode"
                        value={importImages}
                        onChange={(event) => setImportImages(event.target.value as ImportImages)}
                        style={{
                          marginLeft: "0.5rem",
                          background: "var(--sunken)",
                          color: "var(--text)",
                          border: "1px solid var(--line)",
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
                      style={{ height: "6px", background: "var(--sunken)", borderRadius: "3px" }}
                    >
                      <div style={{ width: `${importPercent}%`, height: "100%", background: "var(--accent)", borderRadius: "3px" }} />
                    </div>
                    <p style={{ fontSize: "0.75rem", color: "var(--muted)", margin: 0 }}>{importNote}</p>

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
                          background: importing || !importSource ? "var(--sunken)" : "var(--accent)",
                          color: importing || !importSource ? "var(--muted)" : "var(--accent-ink)",
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
                          border: "1px solid var(--line)",
                          background: "transparent",
                          color: "var(--danger)",
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
                <div style={{ marginTop: "1.2rem", borderTop: "1px solid var(--line)", paddingTop: "0.8rem" }}>
                  <p style={{ fontSize: "0.75rem", color: "var(--muted)", marginBottom: "0.4rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
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
                            backgroundColor: dataRoot === root ? "var(--sunken)" : "transparent",
                            color: "var(--text)",
                            border: `1px solid ${dataRoot === root ? "var(--accent)" : "var(--line)"}`,
                            borderRadius: "6px",
                            cursor: "pointer",
                          }}
                        >
                          <span style={{ fontWeight: 500 }}>📁 {base}</span>
                          <span style={{ color: "var(--muted)", fontSize: "0.72rem", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "'Geist Mono', monospace" }}>
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
        </div>

        <div className="tab-panel" hidden={activeTab !== "remote"}>
          {visitedTabs.has("remote") ? <HpcWorkspace /> : null}
        </div>

        {error && (
          <div role="alert" style={{ maxWidth: "520px", margin: "1rem auto" }}>
            <PanelState
              kind="error"
              title={PANEL_DEFINITIONS.connect.error.title}
              description={PANEL_DEFINITIONS.connect.error.description}
              actionLabel={PANEL_DEFINITIONS.connect.error.actionLabel}
              onAction={() => void handlePickFolder()}
            />
          </div>
        )}
      </main>
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
