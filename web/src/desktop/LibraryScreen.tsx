// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useState } from "react";
import type { LibrarySceneInfo, StorageStats } from "./types";
import PanelState from "./PanelState";
import { PANEL_DEFINITIONS } from "./panelStates";
import {
  deleteLibraryScene,
  downloadTownMap,
  getLibraryScenes,
  getStorageInfo,
  libraryThumbnail,
  moveLibraryRoot,
  onTownMapProgress,
  pickDataRoot,
  revealInFinder,
  setLibraryRoot,
  validateScene,
} from "./local";
import { errorText } from "./errors";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

interface Props {
  onOpenScene: (scenePath: string) => void;
  onNavigateGetData: () => void;
}

export default function LibraryScreen({ onOpenScene, onNavigateGetData }: Props) {
  const [scenes, setScenes] = useState<LibrarySceneInfo[]>([]);
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [validatingMap, setValidatingMap] = useState<Record<string, boolean>>({});
  const [validateStatusMap, setValidateStatusMap] = useState<Record<string, "valid" | "invalid" | "checking">>({});
  const [showFirstRunModal, setShowFirstRunModal] = useState<boolean>(false);
  const [showSettingsModal, setShowSettingsModal] = useState<boolean>(false);
  const [movingStorage, setMovingStorage] = useState<boolean>(false);
  const [deleteConfirmScene, setDeleteConfirmScene] = useState<LibrarySceneInfo | null>(null);
  const [thumbnails, setThumbnails] = useState<Record<string, string>>({});
  const [mapInstall, setMapInstall] = useState<
    Record<string, { percent: number; message: string; error: string; done: boolean }>
  >({});
  const [viewMode, setViewMode] = useState<"table" | "grid">("table");
  const [filterQuery, setFilterQuery] = useState<string>("");
  const [openActionPath, setOpenActionPath] = useState<string | null>(null);

  useEffect(() => {
    if (!openActionPath) return;
    const handleClick = () => setOpenActionPath(null);
    window.addEventListener("click", handleClick);
    return () => window.removeEventListener("click", handleClick);
  }, [openActionPath]);

  const applyScenes = (list: LibrarySceneInfo[]) => {
    setScenes(list);
    setValidateStatusMap((previous) => {
      const next: Record<string, "valid" | "invalid" | "checking"> = { ...previous };
      for (const scene of list) {
        if (scene.is_valid === true) {
          next[scene.path] = "valid";
        } else if (scene.is_valid === false) {
          next[scene.path] = "invalid";
        } else if (next[scene.path] !== "valid" && next[scene.path] !== "invalid") {
          next[scene.path] = "checking";
        }
      }
      return next;
    });
  };

  useEffect(() => {
    const handleSeed = (event: Event) => {
      const custom = event as CustomEvent<{
        scenes?: LibrarySceneInfo[];
        storage?: StorageStats | null;
      }>;
      if (custom.detail?.storage !== undefined) {
        setStorage(custom.detail.storage);
      }
      if (custom.detail?.scenes) {
        applyScenes(custom.detail.scenes);
        setLoading(false);
        setLoadError(null);
      }
    };
    window.addEventListener("coperceive-test-library-seed", handleSeed);
    return () => window.removeEventListener("coperceive-test-library-seed", handleSeed);
  }, []);

  useEffect(() => {
    const created: string[] = [];
    let cancelled = false;
    void (async () => {
      for (const scene of scenes) {
        if (!scene.has_thumbnail) continue;
        try {
          const bytes = await libraryThumbnail(scene.path);
          if (cancelled || !bytes || bytes.length === 0) continue;
          const blob = new Blob([new Uint8Array(bytes)], { type: "image/jpeg" });
          const url = URL.createObjectURL(blob);
          created.push(url);
          setThumbnails((previous) => ({ ...previous, [scene.path]: url }));
        } catch {
          // A broken thumbnail must not hide the card; it keeps the placeholder.
        }
      }
    })();
    return () => {
      cancelled = true;
      for (const url of created) URL.revokeObjectURL(url);
    };
  }, [scenes]);

  const refreshScenes = async (): Promise<boolean> => {
    try {
      const list = await getLibraryScenes();
      applyScenes(list);
      return list.every((scene) => scene.is_valid != null);
    } catch {
      return false;
    }
  };

  const installTown03Map = async (scene: LibrarySceneInfo) => {
    setMapInstall((current) => ({
      ...current,
      [scene.path]: { percent: 0, message: "Starting…", error: "", done: false },
    }));
    const unlisten = await onTownMapProgress((event) => {
      setMapInstall((current) => ({
        ...current,
        [scene.path]: {
          percent: typeof event.percent === "number" ? event.percent : current[scene.path]?.percent ?? 0,
          message: typeof event.message === "string" ? event.message : current[scene.path]?.message ?? "",
          error: "",
          done: false,
        },
      }));
    });
    try {
      await downloadTownMap(scene.path);
      await refreshScenes();
      setMapInstall((current) => ({
        ...current,
        [scene.path]: {
          percent: 100,
          message: "Map installed and aligned. Opening the scene shows it.",
          error: "",
          done: true,
        },
      }));
    } catch (error) {
      setMapInstall((current) => ({
        ...current,
        [scene.path]: {
          percent: current[scene.path]?.percent ?? 0,
          message: "",
          error: errorText(error),
          done: false,
        },
      }));
    } finally {
      unlisten();
    }
  };

  const loadData = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const stats = await getStorageInfo();
      setStorage(stats);
      if (stats && stats.is_configured === false) {
        setShowFirstRunModal(true);
      }
      const list = await getLibraryScenes();
      applyScenes(list);
    } catch (err) {
      setLoadError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await loadData();
      if (cancelled) return;
      let settled = await refreshScenes();
      for (const delay of [300, 500, 800, 1200, 2000, 3000, 4000, 5000, 5000, 5000]) {
        if (cancelled || settled) break;
        await new Promise((resolve) => setTimeout(resolve, delay));
        if (cancelled) break;
        settled = await refreshScenes();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handlePickLibraryRoot = async (isFirstRun: boolean) => {
    try {
      const folder = await pickDataRoot();
      if (!folder) return;
      if (isFirstRun) {
        const stats = await setLibraryRoot(folder);
        setStorage(stats);
        setShowFirstRunModal(false);
        loadData();
      } else {
        setMovingStorage(true);
        const stats = await moveLibraryRoot(folder);
        setStorage(stats);
        setMovingStorage(false);
        setShowSettingsModal(false);
        loadData();
      }
    } catch (e) {
      alert(`Storage update error: ${errorText(e)}`);
      setMovingStorage(false);
    }
  };

  const handleValidateNow = async (scene: LibrarySceneInfo) => {
    setValidatingMap((prev) => ({ ...prev, [scene.path]: true }));
    setValidateStatusMap((prev) => ({ ...prev, [scene.path]: "checking" }));
    try {
      const res = await validateScene(scene.path);
      setValidateStatusMap((prev) => ({
        ...prev,
        [scene.path]: res.valid ? "valid" : "invalid",
      }));
    } catch {
      setValidateStatusMap((prev) => ({ ...prev, [scene.path]: "invalid" }));
    } finally {
      setValidatingMap((prev) => ({ ...prev, [scene.path]: false }));
    }
  };

  const handleDeleteScene = async (scene: LibrarySceneInfo) => {
    try {
      await deleteLibraryScene(scene.path);
      setDeleteConfirmScene(null);
      loadData();
    } catch (e) {
      alert(`Failed to delete scene: ${errorText(e)}`);
    }
  };

  const maxDuration = useMemo(() => {
    let max = 1;
    for (const s of scenes) {
      if (s.duration_s && s.duration_s > max) max = s.duration_s;
    }
    return max;
  }, [scenes]);

  const filteredScenes = useMemo(() => {
    const q = filterQuery.trim().toLowerCase();
    if (!q) return scenes;
    return scenes.filter(
      (s) =>
        s.scene_id.toLowerCase().includes(q) ||
        (s.dataset_name ?? "").toLowerCase().includes(q) ||
        (s.town ?? "").toLowerCase().includes(q),
    );
  }, [scenes, filterQuery]);

  const totalDurationSec = useMemo(
    () => scenes.reduce((acc, s) => acc + (s.duration_s ?? 0), 0),
    [scenes],
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 52px)", fontSize: "13px" }}>
      {/* Compact Page Header matching Claude Design mock */}
      <section
        style={{
          padding: "18px 24px 14px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "16px",
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <div style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
              Local workspace · library
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
              <h2
                style={{
                  margin: 0,
                  fontSize: "20px",
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color: "var(--text)",
                }}
              >
                Library
              </h2>
              <span style={{ fontSize: "12.5px", color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
                {scenes.length} scene{scenes.length === 1 ? "" : "s"} ready for cooperative playback and evaluation
                {totalDurationSec > 0 ? ` · ${totalDurationSec.toFixed(1)}s total` : ""}
              </span>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {scenes.length > 0 && (
              <div
                role="group"
                aria-label="Library view mode"
                style={{
                  display: "flex",
                  gap: "2px",
                  padding: "2px",
                  borderRadius: "6px",
                  backgroundColor: "var(--line-subtle, rgba(255,255,255,0.05))",
                  border: "1px solid var(--line)",
                }}
              >
                <button
                  type="button"
                  onClick={() => setViewMode("table")}
                  style={{
                    padding: "3px 9px",
                    borderRadius: "4px",
                    border: "none",
                    fontSize: "11.5px",
                    fontWeight: viewMode === "table" ? 500 : 400,
                    backgroundColor: viewMode === "table" ? "var(--line-elevated)" : "transparent",
                    color: viewMode === "table" ? "var(--text)" : "var(--text-tertiary)",
                    cursor: "pointer",
                  }}
                >
                  Table
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("grid")}
                  style={{
                    padding: "3px 9px",
                    borderRadius: "4px",
                    border: "none",
                    fontSize: "11.5px",
                    fontWeight: viewMode === "grid" ? 500 : 400,
                    backgroundColor: viewMode === "grid" ? "var(--line-elevated)" : "transparent",
                    color: viewMode === "grid" ? "var(--text)" : "var(--text-tertiary)",
                    cursor: "pointer",
                  }}
                >
                  Grid
                </button>
              </div>
            )}

            <button
              type="button"
              onClick={onNavigateGetData}
              style={{
                background: "var(--primary, #1f6feb)",
                border: "none",
                borderRadius: "6px",
                color: "#fff",
                padding: "6px 14px",
                fontSize: "13px",
                fontWeight: 500,
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
              }}
            >
              <span>+</span> Get Data
            </button>
          </div>
        </div>

        {/* Compact Storage Line */}
        {storage && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "12px",
              flexWrap: "wrap",
              fontSize: "12px",
              paddingTop: "4px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0, flexWrap: "wrap" }}>
              <span style={{ color: "var(--text-subtle)" }}>Library:</span>
              <span
                style={{
                  color: "var(--text-secondary)",
                  fontFamily: "'Geist Mono', ui-monospace, monospace",
                  fontSize: "11.5px",
                }}
              >
                {storage.library_root}
              </span>
              <span style={{ color: "var(--text-subtle)" }}>·</span>
              <span style={{ color: "var(--text)", fontVariantNumeric: "tabular-nums" }}>
                {formatBytes(storage.used_bytes)} used
              </span>
              <span style={{ color: "var(--text-subtle)" }}>·</span>
              <span style={{ color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
                {formatBytes(storage.free_bytes)} free on {storage.volume_name}
              </span>
            </div>

            <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
              <button
                type="button"
                onClick={() => revealInFinder(storage.library_root)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
                  borderRadius: "5px",
                  color: "var(--text-secondary)",
                  padding: "3px 10px",
                  fontSize: "11.5px",
                  cursor: "pointer",
                }}
              >
                Show in Finder
              </button>
              <button
                type="button"
                onClick={() => setShowSettingsModal(true)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
                  borderRadius: "5px",
                  color: "var(--text-secondary)",
                  padding: "3px 10px",
                  fontSize: "11.5px",
                  cursor: "pointer",
                }}
              >
                Change location…
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Filter Bar when scenes exist */}
      {scenes.length > 0 && (
        <div
          style={{
            padding: "8px 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "12px",
            borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
          }}
        >
          <input
            type="search"
            aria-label="Filter library scenes"
            placeholder="Filter scene ID or dataset…"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            style={{
              width: "220px",
              padding: "4px 10px",
              borderRadius: "6px",
              border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
              backgroundColor: "var(--sunken)",
              color: "var(--text)",
              font: "inherit",
              fontSize: "12px",
              outline: "none",
            }}
          />
          <span
            style={{
              fontSize: "12px",
              color: "var(--text-subtle)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {filteredScenes.length === scenes.length
              ? `${scenes.length} scene${scenes.length === 1 ? "" : "s"}`
              : `${filteredScenes.length} of ${scenes.length} scenes`}
          </span>
        </div>
      )}

      {/* Panel States: Loading, Error, and Empty */}
      {loading && (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="loading"
            title={PANEL_DEFINITIONS.library.loading.title}
            description={PANEL_DEFINITIONS.library.loading.description}
          />
        </div>
      )}
      {!loading && loadError && (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="error"
            title={PANEL_DEFINITIONS.library.error.title}
            description={PANEL_DEFINITIONS.library.error.description}
            actionLabel={PANEL_DEFINITIONS.library.error.actionLabel}
            onAction={() => void loadData()}
          />
        </div>
      )}
      {!loading && !loadError && scenes.length === 0 && (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="empty"
            title={PANEL_DEFINITIONS.library.empty.title}
            description={PANEL_DEFINITIONS.library.empty.description}
            actionLabel={PANEL_DEFINITIONS.library.empty.actionLabel}
            onAction={onNavigateGetData}
          />
        </div>
      )}

      {/* Dense Table View (default) */}
      {scenes.length > 0 && viewMode === "table" && (
        <div style={{ flex: 1, overflowX: "auto" }}>
          <div style={{ minWidth: "820px" }}>
            <div
              style={{
                position: "sticky",
                top: 0,
                zIndex: 2,
                display: "grid",
                gridTemplateColumns:
                  "minmax(140px, 1.3fr) minmax(160px, 1.5fr) 80px 58px minmax(130px, 1.1fr) 68px 84px 96px",
                alignItems: "center",
                gap: "12px",
                padding: "0 24px",
                height: "30px",
                backgroundColor: "var(--panel)",
                borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
                fontSize: "11px",
                color: "var(--text-subtle)",
              }}
            >
              <span>Scene ID</span>
              <span>Dataset · Town</span>
              <span>Duration</span>
              <span>Agents</span>
              <span>Modalities</span>
              <span style={{ textAlign: "right" }}>Size</span>
              <span>Status</span>
              <span style={{ textAlign: "right" }}>Actions</span>
            </div>

            {filteredScenes.map((scene) => {
              const isValidating = validatingMap[scene.path];
              const validateStatus = validateStatusMap[scene.path];
              const dur = scene.duration_s ?? 0;
              const durPct = `${Math.min(100, Math.max(6, (dur / maxDuration) * 100))}%`;
              const agents = scene.agent_count ?? 1;
              const needsTown03Map = !scene.has_map && scene.map_name === "Carla/Maps/Town03";

              return (
                <div
                  key={scene.path}
                  tabIndex={0}
                  role="region"
                  aria-label={`Scene ${scene.scene_id}`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpenScene(scene.path);
                    }
                  }}
                  className="library-scene-card c-table-row"
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    borderBottom: "1px solid var(--line-subtle, rgba(255,255,255,0.04))",
                  }}
                >
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "minmax(140px, 1.3fr) minmax(160px, 1.5fr) 80px 58px minmax(130px, 1.1fr) 68px 84px 96px",
                      alignItems: "center",
                      gap: "12px",
                      padding: "0 24px",
                      minHeight: "38px",
                    }}
                  >
                    {/* Scene ID + mini thumb */}
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
                      {thumbnails[scene.path] ? (
                        <img
                          src={thumbnails[scene.path]}
                          alt={`Preview of ${scene.scene_id}`}
                          style={{
                            width: "28px",
                            height: "20px",
                            objectFit: "cover",
                            borderRadius: "3px",
                            flexShrink: 0,
                            backgroundColor: "var(--sunken)",
                          }}
                        />
                      ) : (
                        <span
                          role="img"
                          aria-label="No preview for this scene"
                          style={{
                            width: "6px",
                            height: "6px",
                            borderRadius: "50%",
                            backgroundColor:
                              validateStatus === "valid"
                                ? "#3fb950"
                                : validateStatus === "invalid"
                                  ? "#f85149"
                                  : "#8e8e93",
                            flexShrink: 0,
                          }}
                        />
                      )}
                      <span
                        style={{
                          fontFamily: "'Geist Mono', ui-monospace, monospace",
                          fontSize: "12.5px",
                          fontWeight: 500,
                          color: "var(--text)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {scene.scene_id}
                      </span>
                    </div>

                    {/* Dataset / Town */}
                    <span
                      style={{
                        color: "var(--text-secondary)",
                        fontSize: "12.5px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {scene.dataset_name || "Cooperative Scene"}
                      {scene.town ? (
                        <span style={{ color: "var(--text-subtle)" }}> · {scene.town}</span>
                      ) : null}
                    </span>

                    {/* Duration + bar */}
                    <span
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        fontVariantNumeric: "tabular-nums",
                        color: "var(--text-secondary)",
                        fontSize: "12.5px",
                      }}
                    >
                      <span style={{ width: "38px", textAlign: "right" }}>
                        {scene.duration_s != null ? `${scene.duration_s.toFixed(1)}s` : "—"}
                      </span>
                      <span
                        style={{
                          flex: 1,
                          maxWidth: "60px",
                          height: "4px",
                          borderRadius: "2px",
                          backgroundColor: "var(--line, rgba(255,255,255,0.06))",
                        }}
                      >
                        <span
                          style={{
                            display: "block",
                            height: "100%",
                            borderRadius: "2px",
                            backgroundColor: "#8e8e93",
                            width: durPct,
                          }}
                        />
                      </span>
                    </span>

                    {/* Agents */}
                    <span
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        color: "var(--text-secondary)",
                        fontVariantNumeric: "tabular-nums",
                        fontSize: "12.5px",
                      }}
                    >
                      <span style={{ width: "10px" }}>{scene.agent_count ?? "—"}</span>
                      <span style={{ display: "flex", gap: "2px" }}>
                        {Array.from({ length: Math.min(agents, 5) }, (_, idx) => (
                          <span
                            key={idx}
                            style={{
                              width: "5px",
                              height: "9px",
                              borderRadius: "1px",
                              backgroundColor: "#8e8e93",
                            }}
                          />
                        ))}
                      </span>
                    </span>

                    {/* Modalities */}
                    <span style={{ fontSize: "12px", color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
                      images: <span style={{ color: "var(--text-secondary)" }}>{scene.has_images}</span> · map:{" "}
                      <span style={{ color: "var(--text-secondary)" }}>{scene.has_map ? "yes" : "no"}</span>
                    </span>

                    {/* Size */}
                    <span
                      style={{
                        textAlign: "right",
                        fontFamily: "'Geist Mono', ui-monospace, monospace",
                        fontSize: "12px",
                        fontVariantNumeric: "tabular-nums",
                        color: "var(--text-tertiary)",
                      }}
                    >
                      {formatBytes(scene.size_bytes)}
                    </span>

                    {/* Status Pill */}
                    <span>
                      <span
                        className="library-badge"
                        data-validate={validateStatus}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "5px",
                          fontSize: "12px",
                          fontWeight: 500,
                          color:
                            validateStatus === "valid"
                              ? "var(--ok)"
                              : validateStatus === "invalid"
                                ? "var(--danger)"
                                : "var(--text-tertiary)",
                        }}
                      >
                        {validateStatus !== "valid" ? (
                          <span
                            style={{
                              width: "6px",
                              height: "6px",
                              borderRadius: "50%",
                              backgroundColor:
                                validateStatus === "invalid"
                                  ? "#f85149"
                                  : "#8e8e93",
                            }}
                          />
                        ) : null}
                        {validateStatus === "checking"
                          ? "Validating…"
                          : validateStatus === "valid"
                            ? "Valid ✓"
                            : validateStatus === "invalid"
                              ? "Check failed"
                              : "Schema 1"}
                      </span>
                    </span>

                    {/* Actions */}
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "flex-end",
                        alignItems: "center",
                        gap: "6px",
                        position: "relative",
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => onOpenScene(scene.path)}
                        aria-label={`Open scene ${scene.scene_id}`}
                        style={{
                          padding: "3px 10px",
                          borderRadius: "5px",
                          border: "1px solid rgba(63, 185, 80, 0.6)",
                          backgroundColor: "transparent",
                          color: "var(--ok)",
                          fontSize: "12px",
                          fontWeight: 500,
                          cursor: "pointer",
                        }}
                      >
                        Open
                      </button>
                      <button
                        type="button"
                        style={{
                          padding: "3px 7px",
                          borderRadius: "5px",
                          border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
                          backgroundColor: "transparent",
                          color: "var(--text-secondary)",
                          fontSize: "11.5px",
                          cursor: "pointer",
                          lineHeight: 1,
                        }}
                        aria-label={`More actions for scene ${scene.scene_id}`}
                        aria-haspopup="true"
                        aria-expanded={openActionPath === scene.path}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenActionPath((curr) => (curr === scene.path ? null : scene.path));
                        }}
                      >
                        ⋯
                      </button>

                      {openActionPath === scene.path && (
                        <div
                          role="menu"
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            position: "absolute",
                            right: 0,
                            top: "100%",
                            marginTop: "4px",
                            zIndex: 20,
                            backgroundColor: "var(--panel, #1c1c1e)",
                            border: "1px solid var(--line-elevated, rgba(255, 255, 255, 0.16))",
                            borderRadius: "6px",
                            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
                            padding: "4px",
                            minWidth: "120px",
                            display: "flex",
                            flexDirection: "column",
                            gap: "2px",
                          }}
                        >
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => {
                              setOpenActionPath(null);
                              revealInFinder(scene.path);
                            }}
                            title="Show in Finder"
                            aria-label={`Show ${scene.scene_id} in Finder`}
                            style={{
                              background: "transparent",
                              border: "none",
                              color: "var(--text)",
                              fontSize: "12px",
                              padding: "5px 8px",
                              borderRadius: "4px",
                              textAlign: "left",
                              cursor: "pointer",
                            }}
                          >
                            Finder
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => {
                              setOpenActionPath(null);
                              handleValidateNow(scene);
                            }}
                            disabled={isValidating}
                            title="Validate scene metadata against schema"
                            aria-label={`Validate scene ${scene.scene_id}`}
                            style={{
                              background: "transparent",
                              border: "none",
                              color: "var(--text-secondary)",
                              fontSize: "12px",
                              padding: "5px 8px",
                              borderRadius: "4px",
                              textAlign: "left",
                              cursor: isValidating ? "wait" : "pointer",
                            }}
                          >
                            {isValidating ? "Validating…" : "Validate"}
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => {
                              setOpenActionPath(null);
                              setDeleteConfirmScene(scene);
                            }}
                            title="Move scene to Trash"
                            aria-label={`Delete scene ${scene.scene_id}`}
                            style={{
                              background: "transparent",
                              border: "none",
                              color: "var(--danger, #ff453a)",
                              fontSize: "12px",
                              padding: "5px 8px",
                              borderRadius: "4px",
                              textAlign: "left",
                              cursor: "pointer",
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  {needsTown03Map && (
                    <div
                      style={{
                        padding: "0 24px 8px 46px",
                        display: "flex",
                        alignItems: "center",
                        gap: "12px",
                        flexWrap: "wrap",
                        fontSize: "11.5px",
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => void installTown03Map(scene)}
                        disabled={
                          mapInstall[scene.path] !== undefined &&
                          !mapInstall[scene.path]?.done &&
                          !mapInstall[scene.path]?.error
                        }
                        title="Downloads the official CARLA 0.9.15 Town03 OpenDRIVE (pinned SHA-256), ingests it and aligns it to this scene. Licence: CARLA 0.9.15 README says the town assets are CC-BY (no version stated)."
                        aria-label={`Download map for ${scene.scene_id}`}
                        style={{
                          background: "var(--sunken)",
                          color: "var(--text)",
                          border: "1px solid var(--line-elevated)",
                          borderRadius: "4px",
                          padding: "2px 8px",
                          fontSize: "11.5px",
                          fontWeight: 500,
                          cursor: "pointer",
                        }}
                      >
                        Download map for Town03 (CARLA 0.9.15, CC-BY)
                      </button>
                      <a
                        href="https://github.com/carla-simulator/carla/blob/0.9.15/README.md"
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: "var(--text-subtle)", fontSize: "11px" }}
                      >
                        Licence: CARLA 0.9.15 assets are CC-BY (README; version not stated)
                      </a>
                      {mapInstall[scene.path] ? (
                        <span style={{ color: "var(--text-tertiary)", fontSize: "11px" }}>
                          {mapInstall[scene.path]?.error
                            ? `Could not install: ${mapInstall[scene.path]?.error}`
                            : `${mapInstall[scene.path]?.percent ?? 0}% · ${mapInstall[scene.path]?.message ?? ""}`}
                        </span>
                      ) : null}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Compact Cards Grid View (when toggled) */}
      {scenes.length > 0 && viewMode === "grid" && (
        <div
          style={{
            padding: "16px 24px",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: "12px",
          }}
        >
          {filteredScenes.map((scene) => {
            const isValidating = validatingMap[scene.path];
            const validateStatus = validateStatusMap[scene.path];
            return (
              <div
                key={scene.path}
                tabIndex={0}
                role="region"
                aria-label={`Scene ${scene.scene_id}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpenScene(scene.path);
                  }
                }}
                className="library-scene-card"
                style={{
                  background: "var(--panel)",
                  border: "1px solid var(--line)",
                  borderRadius: "8px",
                  padding: "12px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  gap: "10px",
                }}
              >
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <h4
                        style={{
                          margin: 0,
                          fontSize: "13px",
                          fontFamily: "'Geist Mono', monospace",
                          color: "var(--text)",
                          fontWeight: 500,
                        }}
                      >
                        {scene.scene_id}
                      </h4>
                      <span style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
                        {scene.dataset_name || "Cooperative Scene"}
                        {scene.town ? ` · ${scene.town}` : ""}
                      </span>
                    </div>
                    <span
                      className="library-badge"
                      data-validate={validateStatus}
                      style={{
                        fontSize: "11px",
                        color:
                          validateStatus === "valid"
                            ? "var(--ok)"
                            : validateStatus === "invalid"
                              ? "var(--danger)"
                              : "var(--text-tertiary)",
                        fontWeight: 500,
                      }}
                    >
                      {validateStatus === "checking"
                        ? "Validating…"
                        : validateStatus === "valid"
                          ? "Valid ✓"
                          : validateStatus === "invalid"
                            ? "Check failed"
                            : "Schema 1"}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px", fontSize: "11.5px", color: "var(--text-secondary)" }}>
                    {scene.duration_s != null && <span>{scene.duration_s.toFixed(1)}s</span>}
                    {scene.agent_count != null && <span>· {scene.agent_count} agents</span>}
                    <span>· images: {scene.has_images}</span>
                    <span>· map: {scene.has_map ? "yes" : "no"}</span>
                    <span>· {formatBytes(scene.size_bytes)}</span>
                  </div>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "8px", borderTop: "1px solid var(--line-subtle)" }}>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <button
                      type="button"
                      onClick={() => onOpenScene(scene.path)}
                      aria-label={`Open scene ${scene.scene_id}`}
                      style={{
                        padding: "3px 10px",
                        borderRadius: "5px",
                        border: "1px solid rgba(63, 185, 80, 0.6)",
                        background: "transparent",
                        color: "var(--ok)",
                        fontSize: "12px",
                        fontWeight: 500,
                      }}
                    >
                      Open
                    </button>
                    <button
                      type="button"
                      onClick={() => revealInFinder(scene.path)}
                      aria-label={`Show ${scene.scene_id} in Finder`}
                      style={{
                        padding: "3px 8px",
                        borderRadius: "5px",
                        border: "1px solid var(--line-elevated)",
                        background: "transparent",
                        color: "var(--text-secondary)",
                        fontSize: "11.5px",
                      }}
                    >
                      Finder
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <button
                      type="button"
                      onClick={() => handleValidateNow(scene)}
                      disabled={isValidating}
                      aria-label={`Validate scene ${scene.scene_id}`}
                      style={{ background: "transparent", border: "none", fontSize: "11.5px", color: "var(--text-tertiary)" }}
                    >
                      {isValidating ? "Validating…" : "Validate"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setDeleteConfirmScene(scene)}
                      aria-label={`Delete scene ${scene.scene_id}`}
                      style={{ background: "transparent", border: "none", fontSize: "11.5px", color: "var(--danger)" }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* First Run Storage Location Modal */}
      {showFirstRunModal && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "1rem",
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              borderRadius: "10px",
              border: "1px solid var(--line)",
              maxWidth: "460px",
              width: "100%",
              padding: "1.25rem",
              boxShadow: "var(--shadow)",
            }}
          >
            <h3 style={{ margin: "0 0 0.5rem 0", color: "var(--text)", fontSize: "15px" }}>
              Where should coperceive keep datasets?
            </h3>
            <p style={{ color: "var(--muted)", fontSize: "12.5px", lineHeight: 1.5, marginBottom: "1rem" }}>
              Coperceive stores downloaded benchmarks and converted multi-agent scenes in your library folder.
              The default location is <code>~/coperceive-data</code>.
            </p>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={() => {
                  if (!storage) return;
                  void (async () => {
                    const stats = await setLibraryRoot(storage.library_root);
                    if (stats) setStorage(stats);
                    setShowFirstRunModal(false);
                  })();
                }}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "1px solid var(--line-elevated)",
                  background: "transparent",
                  color: "var(--text)",
                  fontSize: "12.5px",
                  cursor: "pointer",
                }}
              >
                Use default (~/coperceive-data)
              </button>
              <button
                type="button"
                onClick={() => handlePickLibraryRoot(true)}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "none",
                  background: "#1f6feb",
                  color: "#fff",
                  fontSize: "12.5px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Choose Folder…
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Settings / Change Storage Location Modal */}
      {showSettingsModal && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "1rem",
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              borderRadius: "10px",
              border: "1px solid var(--line)",
              maxWidth: "460px",
              width: "100%",
              padding: "1.25rem",
              boxShadow: "var(--shadow)",
            }}
          >
            <h3 style={{ margin: "0 0 0.5rem 0", color: "var(--text)", fontSize: "15px" }}>
              Change Library Location
            </h3>
            <p style={{ color: "var(--muted)", fontSize: "12.5px", lineHeight: 1.5, marginBottom: "0.75rem" }}>
              Moving your library will automatically transfer existing converted scenes into the new directory.
            </p>
            {storage && (
              <div
                style={{
                  background: "var(--sunken)",
                  padding: "8px 10px",
                  borderRadius: "6px",
                  fontFamily: "'Geist Mono', monospace",
                  fontSize: "12px",
                  color: "var(--text-secondary)",
                  marginBottom: "1rem",
                }}
              >
                Current: {storage.library_root}
              </div>
            )}

            {movingStorage && (
              <div style={{ color: "var(--text)", fontSize: "12.5px", marginBottom: "0.75rem" }}>
                Moving scenes to new storage location…
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={() => setShowSettingsModal(false)}
                disabled={movingStorage}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "1px solid var(--line-elevated)",
                  background: "transparent",
                  color: "var(--text)",
                  fontSize: "12.5px",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handlePickLibraryRoot(false)}
                disabled={movingStorage}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "none",
                  background: "#1f6feb",
                  color: "#fff",
                  fontSize: "12.5px",
                  fontWeight: 500,
                  cursor: movingStorage ? "wait" : "pointer",
                }}
              >
                {movingStorage ? "Moving…" : "Select New Folder…"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Scene Confirm Modal */}
      {deleteConfirmScene && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "1rem",
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              borderRadius: "10px",
              border: "1px solid var(--line)",
              maxWidth: "420px",
              width: "100%",
              padding: "1.25rem",
              boxShadow: "var(--shadow)",
            }}
          >
            <h3 style={{ margin: "0 0 0.5rem 0", color: "var(--text)", fontSize: "15px" }}>
              Delete Scene?
            </h3>
            <p style={{ color: "var(--muted)", fontSize: "12.5px", lineHeight: 1.5, marginBottom: "1rem" }}>
              Are you sure you want to delete <strong>{deleteConfirmScene.scene_id}</strong>? The scene directory will be moved to your Trash.
            </p>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={() => setDeleteConfirmScene(null)}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "1px solid var(--line-elevated)",
                  background: "transparent",
                  color: "var(--text)",
                  fontSize: "12.5px",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handleDeleteScene(deleteConfirmScene)}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "none",
                  background: "#ff453a",
                  color: "#fff",
                  fontSize: "12.5px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Move to Trash
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
