// SPDX-License-Identifier: Apache-2.0
import { JobsIndicator } from "./JobsIndicator";
import { useEffect, useState } from "react";
import {
  LocalExecutionTarget,
  cancelImport,
  getPersistentState,
  getStorageInfo,
  isTauriEnvironment,
  listServerProfiles,
  onImportProgress,
  pickDataRoot,
  startImport,
  type ImportImages,
} from "./local";
import type { ConnectionSession } from "./types";
import { BrandWordmark } from "./Brand";
import IdentifierInput from "./IdentifierInput";
import HpcWorkspace, { type ActiveDatasetPayload, type WorkspaceTab } from "./HpcWorkspace";
import LibraryScreen from "./LibraryScreen";
import GetDataScreen from "./GetDataScreen";
import UpdateBanner from "./UpdateBanner";
import PanelState from "./PanelState";
import { PANEL_DEFINITIONS } from "./panelStates";
import { errorText } from "./errors";
import { Sidebar } from "./components/Sidebar";
import { IconButton } from "./components/IconButton";
import { SettingsModal } from "./SettingsView";
import type { ServerEntry } from "./hpc";

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

  // HPC sidebar state synced with HpcWorkspace
  const [hpcServers, setHpcServers] = useState<ServerEntry[]>([]);
  const [hpcSubTab, setHpcSubTab] = useState<WorkspaceTab>("servers");
  const [activeHpcDataset, setActiveHpcDataset] = useState<ActiveDatasetPayload | null>(null);

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

    const handleOpenFolderSeed = (event: Event) => {
      const custom = event as CustomEvent<{ dataRoot?: string; recentRoots?: string[] }>;
      if (custom.detail?.dataRoot !== undefined) setDataRoot(custom.detail.dataRoot);
      if (custom.detail?.recentRoots !== undefined) setRecentRoots(custom.detail.recentRoots);
    };
    window.addEventListener("coperceive-test-open-folder-seed", handleOpenFolderSeed);

    const handleHpcState = (event: Event) => {
      const custom = event as CustomEvent<{
        tab?: WorkspaceTab;
        activeDataset?: ActiveDatasetPayload | null;
      }>;
      if (custom.detail?.tab) setHpcSubTab(custom.detail.tab);
      if (custom.detail?.activeDataset !== undefined) {
        setActiveHpcDataset(custom.detail.activeDataset);
        if (custom.detail.activeDataset?.profile) {
          const prof = custom.detail.activeDataset.profile;
          setHpcServers((prev) =>
            prev.some((s) => s.id === prof.id) ? prev : [prof, ...prev],
          );
        }
      }
    };
    window.addEventListener("coperceive-hpc-state", handleHpcState);

    const handleHpcProfilesUpdated = (event: Event) => {
      const custom = event as CustomEvent<{ profiles?: ServerEntry[] }>;
      if (custom.detail?.profiles) {
        setHpcServers(custom.detail.profiles);
      }
    };
    window.addEventListener("coperceive-hpc-profiles-updated", handleHpcProfilesUpdated);

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
      try {
        const profiles = await listServerProfiles();
        if (Array.isArray(profiles) && profiles.length > 0) {
          setHpcServers(profiles);
        }
      } catch {}
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
        window.removeEventListener("coperceive-test-open-folder-seed", handleOpenFolderSeed);
        window.removeEventListener("coperceive-hpc-state", handleHpcState);
        window.removeEventListener("coperceive-hpc-profiles-updated", handleHpcProfilesUpdated);
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
      window.removeEventListener("coperceive-test-open-folder-seed", handleOpenFolderSeed);
      window.removeEventListener("coperceive-hpc-state", handleHpcState);
      window.removeEventListener("coperceive-hpc-profiles-updated", handleHpcProfilesUpdated);
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

  const navigateHpcSection = (section: WorkspaceTab) => {
    setActiveTab("remote");
    setVisitedTabs((current) => (current.has("remote") ? current : new Set([...current, "remote"])));
    setHpcSubTab(section);
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent("coperceive-hpc-section", { detail: { tab: section } }));
    }, 0);
  };

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

  const activeFooterServer = activeHpcDataset?.profile ?? hpcServers[0] ?? null;

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
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {activeTab === "remote" && activeFooterServer ? (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "8px",
                  paddingBottom: "10px",
                  borderBottom: "1px solid var(--line-subtle)",
                }}
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr",
                    columnGap: "10px",
                    rowGap: "3px",
                    fontSize: "11.5px",
                  }}
                >
                  <span style={{ color: "var(--text-subtle)" }}>Host</span>
                  <span
                    style={{
                      fontFamily: "'Geist Mono', ui-monospace, monospace",
                      color: "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {activeFooterServer.profile.host ||
                      activeFooterServer.ssh?.hostname ||
                      activeFooterServer.profile.ssh_alias ||
                      activeFooterServer.profile.name}
                  </span>
                  <span style={{ color: "var(--text-subtle)" }}>Scheduler</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    {activeFooterServer.profile.scheduler === "slurm"
                      ? `Slurm${
                          activeFooterServer.profile.defaults?.partition
                            ? ` · partition ${activeFooterServer.profile.defaults.partition}`
                            : ""
                        }`
                      : "Files only"}
                  </span>
                  {activeFooterServer.discovered?.partitions ? (
                    <>
                      <span style={{ color: "var(--text-subtle)" }}>Checked</span>
                      <span style={{ color: "var(--text-secondary)" }}>
                        {activeFooterServer.discovered.partitions.length} partitions found
                      </span>
                    </>
                  ) : null}
                </div>
                <p
                  style={{
                    margin: 0,
                    fontSize: "11px",
                    lineHeight: 1.4,
                    color: "var(--text-subtle)",
                  }}
                >
                  Signs in with your ssh-agent and ~/.ssh/config. Never asks for a password or key.
                </p>
              </div>
            ) : null}

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <button
                type="button"
                aria-label="Settings (⌘,)"
                onClick={() => setSettingsOpen(true)}
                className="c-sidebar-item"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  background: "transparent",
                  border: "none",
                  color: "var(--text-secondary)",
                  padding: "5px 8px",
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
                <span
                  style={{
                    marginLeft: "auto",
                    fontSize: "11px",
                    color: "var(--text-subtle)",
                    fontFamily: "'Geist Mono', monospace",
                  }}
                >
                  ⌘,
                </span>
              </button>
            </div>
          </div>
        }
      >
        {/* Local / General Section */}
        <div
          style={{
            padding: "4px 8px 4px",
            fontSize: "11px",
            fontWeight: 500,
            color: "var(--text-subtle)",
          }}
        >
          Local
        </div>
        <nav
          style={{ display: "flex", flexDirection: "column", gap: "1px" }}
          role="tablist"
          aria-label="Connect navigation"
        >
          {(
            [
              ["library", "Library"],
              ["get_data", "Get Data"],
              ["local", "Open Folder"],
            ] as const
          ).map(([tabKey, label]) => {
            const selected = activeTab === tabKey;
            return (
              <button
                key={tabKey}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setActiveTab(tabKey)}
                className={`c-sidebar-item ${selected ? "active" : ""}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "5px 8px",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: selected ? 500 : 400,
                  color: selected ? "var(--text)" : "var(--text-secondary)",
                  backgroundColor: selected ? "var(--line, rgba(255, 255, 255, 0.08))" : "transparent",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                  width: "100%",
                }}
              >
                <span>{label}</span>
              </button>
            );
          })}
        </nav>

        {/* HPC Workspace Section in Sidebar (matching Claude Design mock) */}
        <nav
          style={{ display: "flex", flexDirection: "column", gap: "1px", marginTop: "12px" }}
          aria-label="HPC workspace navigation"
        >
          <button
            type="button"
            onClick={() => navigateHpcSection("servers")}
            style={{
              padding: "6px 8px 4px",
              fontSize: "11px",
              fontWeight: 500,
              color: "var(--text-subtle)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              width: "100%",
            }}
          >
            HPC Workspace
          </button>
          {(
            [
              ["servers", "Servers", hpcServers.length > 0 ? String(hpcServers.length) : undefined],
              ["evaluation", "New evaluation", undefined],
              ["history", "Run history", undefined],
            ] as const
          ).map(([secKey, label, badge]) => {
            const isSelected = activeTab === "remote" && hpcSubTab === secKey;
            return (
              <button
                key={secKey}
                type="button"
                onClick={() => navigateHpcSection(secKey)}
                className="c-sidebar-item"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "5px 8px",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: isSelected ? 500 : 400,
                  color: isSelected ? "var(--text)" : "var(--text-secondary)",
                  backgroundColor: isSelected
                    ? "var(--line, rgba(255, 255, 255, 0.08))"
                    : "transparent",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                  width: "100%",
                }}
              >
                <span>{label}</span>
                {badge ? (
                  <span
                    style={{
                      fontSize: "11px",
                      color: "var(--text-tertiary)",
                      fontWeight: 400,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {badge}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>

        {/* Servers Tree in Sidebar (matching Claude Design mock) */}
        <div
          style={{
            marginTop: "12px",
            display: "flex",
            flexDirection: "column",
            gap: "1px",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 8px 4px",
            }}
          >
            <span style={{ fontSize: "11px", fontWeight: 500, color: "var(--text-subtle)" }}>
              Servers
            </span>
            <button
              type="button"
              title="Add server"
              aria-label="New server profile"
              onClick={() => {
                navigateHpcSection("servers");
                setTimeout(() => {
                  window.dispatchEvent(new CustomEvent("coperceive-hpc-add-server"));
                }, 0);
              }}
              style={{
                width: "18px",
                height: "18px",
                padding: 0,
                display: "grid",
                placeItems: "center",
                borderRadius: "4px",
                border: "none",
                background: "transparent",
                color: "var(--text-tertiary)",
                fontSize: "15px",
                lineHeight: 1,
                cursor: "pointer",
              }}
            >
              +
            </button>
          </div>

          {hpcServers.map((srv) => {
            const srvName = srv.profile?.name || srv.id;
            const hasDataset = activeHpcDataset && activeHpcDataset.profile.id === srv.id;
            const dsDoc = hasDataset ? activeHpcDataset.dataset : null;
            const isDatasetSelected =
              activeTab === "remote" && hpcSubTab === "dataset" && hasDataset;
            return (
              <div key={srv.id} style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "5px 8px",
                    color: "var(--text)",
                    fontSize: "13px",
                  }}
                >
                  <span
                    style={{
                      width: "7px",
                      height: "7px",
                      borderRadius: "50%",
                      backgroundColor: "#3fb950",
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontWeight: 500,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {srvName}
                  </span>
                </div>

                <div
                  style={{
                    padding: "0 8px 0 23px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "1px",
                  }}
                >
                  {dsDoc && (
                    <>
                      <div
                        style={{
                          fontFamily: "'Geist Mono', ui-monospace, monospace",
                          fontSize: "11px",
                          color: "var(--text-subtle)",
                          padding: "0 0 4px",
                        }}
                      >
                        {(dsDoc.adapter || "dataset").toLowerCase()}
                      </div>
                      <button
                        type="button"
                        onClick={() => navigateHpcSection("dataset")}
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          padding: "5px 8px",
                          marginLeft: "-8px",
                          borderRadius: "6px",
                          border: "none",
                          backgroundColor: isDatasetSelected
                            ? "rgba(31, 111, 235, 0.22)"
                            : "transparent",
                          color: "var(--text)",
                          fontSize: "12.5px",
                          fontWeight: isDatasetSelected ? 500 : 400,
                          cursor: "pointer",
                          textAlign: "left",
                        }}
                      >
                        <span>{dsDoc.split || "all"}</span>
                        <span
                          style={{
                            fontSize: "11px",
                            color: isDatasetSelected ? "var(--primary-hover, #a5c8ff)" : "var(--text-tertiary)",
                            fontVariantNumeric: "tabular-nums",
                            fontFamily: "'Geist Mono', ui-monospace, monospace",
                          }}
                        >
                          {dsDoc.scene_count}
                        </span>
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      navigateHpcSection("servers");
                      setTimeout(() => {
                        window.dispatchEvent(
                          new CustomEvent("coperceive-hpc-browse-server", {
                            detail: { profile: srv },
                          }),
                        );
                      }, 0);
                    }}
                    className="c-sidebar-item"
                    style={{
                      padding: "5px 8px",
                      marginLeft: "-8px",
                      borderRadius: "6px",
                      border: "none",
                      background: "transparent",
                      color: "var(--text-tertiary)",
                      fontSize: "12.5px",
                      textAlign: "left",
                      cursor: "pointer",
                    }}
                  >
                    Browse data…
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </Sidebar>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
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
                <div
                  data-tauri-drag-region
                  className="traffic-lights-spacer"
                  style={{ width: "78px", flexShrink: 0, height: "100%" }}
                  aria-hidden="true"
                />
                <IconButton
                  size="sm"
                  label="Open sidebar (⌘\)"
                  onClick={() => setSidebarOpen(true)}
                >
                  <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
                    <path d="M1.5 2.5A1 1 0 0 1 2.5 1.5h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11zm1 0v11h3.5v-11h-3.5zm4.5 0v11h6.5v-11H7z" />
                  </svg>
                </IconButton>
                <BrandWordmark fontSize="13px" />
              </div>
            )}
            <h1 style={{ fontSize: "13.5px", fontWeight: 500, margin: 0, color: "var(--text)" }}>
              {activeTab === "library" && "Library"}
              {activeTab === "get_data" && "Get Data"}
              {activeTab === "local" && "Open Local Folder"}
              {activeTab === "remote" && "HPC Workspace"}
            </h1>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
            {status && (
              <div style={{ fontSize: "12px", color: "var(--primary, #1f6feb)", fontWeight: 500 }}>
                {status}
              </div>
            )}
            <JobsIndicator onOpenWorkspace={() => setActiveTab("remote")} />
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

          {/* Open Folder Tab: Left-aligned full-width compact form + Dense Recent Datasets table */}
          <div className="tab-panel" hidden={activeTab !== "local"}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                minHeight: "calc(100vh - 52px)",
                fontSize: "13px",
              }}
            >
              <section
                style={{
                  padding: "18px 24px 14px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "4px",
                  borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
                }}
              >
                <div style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
                  Local workspace · open folder
                </div>
                <div style={{ display: "flex", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
                  <h2
                    style={{
                      fontSize: "20px",
                      margin: 0,
                      color: "var(--text)",
                      fontWeight: 600,
                      letterSpacing: "-0.01em",
                    }}
                  >
                    Open Local Folder
                  </h2>
                  <span style={{ fontSize: "12.5px", color: "var(--text-tertiary)" }}>
                    Browse directly to any folder holding schema 1 scenes or datasets.
                  </span>
                </div>
              </section>

              <div
                style={{
                  padding: "18px 24px",
                  display: "grid",
                  gridTemplateColumns:
                    recentRoots.length > 0
                      ? "minmax(340px, 520px) minmax(280px, 1fr)"
                      : "minmax(340px, 560px)",
                  gap: "24px",
                  alignItems: "start",
                }}
              >
                {/* Left Column: Compact Folder Form + Collapsible Import */}
                <div
                  className="connect-card"
                  style={{
                    backgroundColor: "var(--panel)",
                    borderRadius: "8px",
                    border: "1px solid var(--line)",
                    padding: "16px 18px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "12px",
                  }}
                >
                  {busy && (
                    <PanelState
                      compact
                      kind="loading"
                      title={PANEL_DEFINITIONS.connect.loading.title}
                      description={PANEL_DEFINITIONS.connect.loading.description}
                      actionLabel={PANEL_DEFINITIONS.connect.loading.actionLabel}
                      onAction={() => {
                        setBusy(false);
                        setStatus("");
                      }}
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

                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <span style={{ fontSize: "11px", fontWeight: 500, color: "var(--text-subtle)" }}>
                      Dataset folder path
                    </span>
                    <div style={{ display: "flex", gap: "8px" }}>
                      <IdentifierInput
                        type="text"
                        readOnly
                        placeholder="No directory selected..."
                        value={dataRoot}
                        aria-label="Selected dataset folder"
                        style={{
                          flex: 1,
                          padding: "6px 10px",
                          fontSize: "12.5px",
                          borderRadius: "6px",
                          border: "1px solid var(--line-elevated)",
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
                          padding: "6px 12px",
                          fontSize: "12.5px",
                          borderRadius: "6px",
                          cursor: "pointer",
                          border: "1px solid var(--line-elevated)",
                          backgroundColor: "transparent",
                          color: "var(--text)",
                          fontWeight: 500,
                        }}
                      >
                        Choose…
                      </button>
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: "8px" }}>
                    <button
                      type="button"
                      onClick={() => handleConnectLocal()}
                      disabled={busy || !dataRoot}
                      aria-label="Open Local Viewer"
                      style={{
                        flex: 1,
                        padding: "7px 14px",
                        fontSize: "13px",
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
                        padding: "7px 12px",
                        fontSize: "12.5px",
                        fontWeight: 500,
                        borderRadius: "6px",
                        cursor: busy || !dataRoot ? "not-allowed" : "pointer",
                        border: "1px solid var(--line-elevated)",
                        backgroundColor: "transparent",
                        color: "var(--text)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Start fresh
                    </button>
                  </div>

                  {/* Import from folder section */}
                  <div style={{ borderTop: "1px solid var(--line-subtle)", paddingTop: "10px" }}>
                    <button
                      type="button"
                      onClick={() => setImportOpen((open) => !open)}
                      disabled={busy}
                      style={{
                        background: "transparent",
                        border: "1px solid var(--line-elevated)",
                        borderRadius: "6px",
                        padding: "5px 10px",
                        color: "var(--text)",
                        fontSize: "12.5px",
                        cursor: "pointer",
                        width: "100%",
                      }}
                    >
                      {importOpen ? "Hide Import from folder" : "Import from folder…"}
                    </button>
                    {importOpen && (
                      <div
                        style={{
                          marginTop: "10px",
                          display: "flex",
                          flexDirection: "column",
                          gap: "8px",
                        }}
                      >
                        <p style={{ fontSize: "12px", color: "var(--text-tertiary)", margin: 0 }}>
                          Copies and packages the scene directly into your library folder.
                        </p>
                        <button
                          type="button"
                          onClick={async () => {
                            const folder = await pickDataRoot();
                            if (folder) setImportSource(folder);
                          }}
                          style={{
                            padding: "5px 8px",
                            textAlign: "left",
                            background: "var(--sunken)",
                            border: "1px solid var(--line-elevated)",
                            borderRadius: "5px",
                            color: "var(--text)",
                            fontSize: "12px",
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
                            padding: "5px 8px",
                            textAlign: "left",
                            background: "var(--sunken)",
                            border: "1px solid var(--line-elevated)",
                            borderRadius: "5px",
                            color: "var(--text)",
                            fontSize: "12px",
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
                            padding: "5px 8px",
                            textAlign: "left",
                            background: "var(--sunken)",
                            border: "1px solid var(--line-elevated)",
                            borderRadius: "5px",
                            color: "var(--text)",
                            fontSize: "12px",
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
                            padding: "5px 8px",
                            textAlign: "left",
                            background: "var(--sunken)",
                            border: "1px solid var(--line-elevated)",
                            borderRadius: "5px",
                            color: "var(--text)",
                            fontSize: "12px",
                            cursor: "pointer",
                          }}
                        >
                          Griffin root: {importGriffin || "optional"}
                        </button>
                        <label
                          style={{
                            fontSize: "12px",
                            color: "var(--text-secondary)",
                            display: "flex",
                            alignItems: "center",
                          }}
                        >
                          Images:
                          <select
                            aria-label="Image copy mode"
                            value={importImages}
                            onChange={(event) =>
                              setImportImages(event.target.value as ImportImages)
                            }
                            style={{
                              marginLeft: "8px",
                              background: "var(--sunken)",
                              color: "var(--text)",
                              border: "1px solid var(--line-elevated)",
                              borderRadius: "4px",
                              padding: "3px 6px",
                              fontSize: "12px",
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
                          style={{
                            height: "6px",
                            background: "var(--sunken)",
                            borderRadius: "3px",
                          }}
                        >
                          <div
                            style={{
                              width: `${importPercent}%`,
                              height: "100%",
                              background: "var(--accent)",
                              borderRadius: "3px",
                            }}
                          />
                        </div>
                        <p style={{ fontSize: "11.5px", color: "var(--text-tertiary)", margin: 0 }}>
                          {importNote}
                        </p>

                        <div style={{ display: "flex", gap: "8px", marginTop: "2px" }}>
                          <button
                            type="button"
                            onClick={() => void handleImport()}
                            disabled={importing || !importSource}
                            style={{
                              flex: 1,
                              padding: "5px 10px",
                              borderRadius: "5px",
                              border: "none",
                              background:
                                importing || !importSource ? "var(--sunken)" : "var(--accent)",
                              color:
                                importing || !importSource ? "var(--muted)" : "var(--accent-ink)",
                              fontWeight: 500,
                              cursor: importing || !importSource ? "not-allowed" : "pointer",
                              fontSize: "12px",
                            }}
                          >
                            {importing ? "Importing…" : "Start import"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void cancelImport()}
                            disabled={!importing}
                            style={{
                              padding: "5px 10px",
                              borderRadius: "5px",
                              border: "1px solid var(--line-elevated)",
                              background: "transparent",
                              color: "var(--danger)",
                              cursor: !importing ? "not-allowed" : "pointer",
                              fontSize: "12px",
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Right Column: Dense Recent Datasets Table */}
                {recentRoots.length > 0 && (
                  <div
                    style={{
                      backgroundColor: "var(--panel)",
                      border: "1px solid var(--line)",
                      borderRadius: "8px",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "minmax(120px, 1fr) minmax(180px, 2fr)",
                        alignItems: "center",
                        gap: "12px",
                        padding: "0 16px",
                        height: "30px",
                        backgroundColor: "var(--sunken)",
                        borderBottom: "1px solid var(--line)",
                        fontSize: "11px",
                        color: "var(--text-subtle)",
                      }}
                    >
                      <span>Recent Datasets</span>
                      <span style={{ textAlign: "right" }}>Path</span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column" }}>
                      {recentRoots.map((root, idx) => {
                        const base = root.split("/").filter(Boolean).pop() || root;
                        const isSelected = dataRoot === root;
                        return (
                          <button
                            key={root}
                            type="button"
                            onClick={() => {
                              setDataRoot(root);
                              setError("");
                            }}
                            aria-label={`Select recent dataset ${base}`}
                            className="c-table-row"
                            style={{
                              display: "grid",
                              gridTemplateColumns: "minmax(120px, 1fr) minmax(180px, 2fr)",
                              alignItems: "center",
                              gap: "12px",
                              textAlign: "left",
                              padding: "8px 16px",
                              fontSize: "12.5px",
                              backgroundColor: isSelected
                                ? "rgba(31, 111, 235, 0.16)"
                                : "transparent",
                              boxShadow: isSelected ? "inset 2px 0 0 #388bfd" : "none",
                              color: "var(--text)",
                              border: "none",
                              borderBottom:
                                idx < recentRoots.length - 1
                                  ? "1px solid var(--line-subtle)"
                                  : "none",
                              borderRadius: 0,
                              cursor: "pointer",
                            }}
                          >
                            <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>{base}</span>
                            <span
                              style={{
                                color: "var(--text-tertiary)",
                                fontSize: "11.5px",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                                fontFamily: "'Geist Mono', ui-monospace, monospace",
                                textAlign: "right",
                              }}
                            >
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
            <div role="alert" style={{ padding: "16px 24px", maxWidth: "560px" }}>
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
