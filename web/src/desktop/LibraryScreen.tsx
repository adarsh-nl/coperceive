// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { LibrarySceneInfo, StorageStats } from "./types";
import {
  deleteLibraryScene,
  getLibraryScenes,
  getStorageInfo,
  moveLibraryRoot,
  pickDataRoot,
  revealInFinder,
  setLibraryRoot,
  validateScene,
} from "./local";

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
  const [validatingMap, setValidatingMap] = useState<Record<string, boolean>>({});
  const [validateStatusMap, setValidateStatusMap] = useState<Record<string, "valid" | "invalid" | "checking">>({});
  const [showFirstRunModal, setShowFirstRunModal] = useState<boolean>(false);
  const [showSettingsModal, setShowSettingsModal] = useState<boolean>(false);
  const [movingStorage, setMovingStorage] = useState<boolean>(false);
  const [deleteConfirmScene, setDeleteConfirmScene] = useState<LibrarySceneInfo | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const stats = await getStorageInfo();
      setStorage(stats);
      if (stats && !localStorage.getItem("coperceive_storage_prompted")) {
        // If library root is default and hasn't been confirmed
        // But if scenes already exist, no need to prompt
        if (stats.scene_count === 0 && !localStorage.getItem("coperceive_storage_chosen")) {
          setShowFirstRunModal(true);
        }
      }
      const list = await getLibraryScenes();
      setScenes(list);
    } catch {
      // Ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handlePickLibraryRoot = async (isFirstRun: boolean) => {
    try {
      const folder = await pickDataRoot();
      if (!folder) return;
      if (isFirstRun) {
        const stats = await setLibraryRoot(folder);
        setStorage(stats);
        localStorage.setItem("coperceive_storage_chosen", "true");
        localStorage.setItem("coperceive_storage_prompted", "true");
        setShowFirstRunModal(false);
        loadData();
      } else {
        // Move existing scenes with progress
        setMovingStorage(true);
        const stats = await moveLibraryRoot(folder);
        setStorage(stats);
        setMovingStorage(false);
        setShowSettingsModal(false);
        loadData();
      }
    } catch (e) {
      alert(`Storage update error: ${e instanceof Error ? e.message : String(e)}`);
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
      alert(`Failed to delete scene: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div style={{ padding: "1.5rem", maxWidth: "1100px", margin: "0 auto" }}>
      {/* Storage stats header bar */}
      {storage && (
        <div
          style={{
            background: "rgba(255, 255, 255, 0.04)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            borderRadius: "8px",
            padding: "0.75rem 1rem",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "1.5rem",
            fontSize: "0.85rem",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span style={{ color: "#a1a1a6" }}>Library:</span>
            <span style={{ color: "#f5f5f7", fontFamily: "'Geist Mono', monospace" }}>{storage.library_root}</span>
            <span style={{ color: "rgba(255, 255, 255, 0.2)" }}>·</span>
            <span style={{ color: "#f5f5f7" }}>{formatBytes(storage.used_bytes)} used</span>
            <span style={{ color: "rgba(255, 255, 255, 0.2)" }}>·</span>
            <span style={{ color: "#8b949e" }}>
              {formatBytes(storage.free_bytes)} free on {storage.volume_name}
            </span>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              onClick={() => revealInFinder(storage.library_root)}
              style={{
                background: "transparent",
                border: "1px solid rgba(255, 255, 255, 0.15)",
                borderRadius: "6px",
                color: "#f5f5f7",
                padding: "0.3rem 0.6rem",
                fontSize: "0.8rem",
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
                border: "1px solid rgba(255, 255, 255, 0.15)",
                borderRadius: "6px",
                color: "#f5f5f7",
                padding: "0.3rem 0.6rem",
                fontSize: "0.8rem",
                cursor: "pointer",
              }}
            >
              Change location…
            </button>
          </div>
        </div>
      )}

      {/* Screen Title & Quick Action */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.2rem" }}>
        <div>
          <h2 style={{ fontSize: "1.4rem", fontWeight: 600, color: "#f5f5f7", margin: 0 }}>Library</h2>
          <span style={{ fontSize: "0.85rem", color: "#8b949e" }}>
            {scenes.length} scene{scenes.length === 1 ? "" : "s"} ready for cooperative playback and evaluation
          </span>
        </div>
        <button
          type="button"
          onClick={onNavigateGetData}
          style={{
            background: "#2878dc",
            border: "none",
            borderRadius: "6px",
            color: "#fff",
            padding: "0.5rem 1rem",
            fontSize: "0.85rem",
            fontWeight: 500,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: "0.4rem",
          }}
        >
          <span>+</span> Get Data
        </button>
      </div>

      {/* Empty State */}
      {!loading && scenes.length === 0 && (
        <div
          style={{
            border: "1px dashed rgba(255, 255, 255, 0.15)",
            borderRadius: "12px",
            padding: "3.5rem 2rem",
            textAlign: "center",
            background: "rgba(255, 255, 255, 0.01)",
          }}
        >
          <div style={{ fontSize: "2.5rem", marginBottom: "1rem" }}>📦</div>
          <h3 style={{ fontSize: "1.2rem", color: "#f5f5f7", marginBottom: "0.5rem" }}>
            Your library is empty
          </h3>
          <p style={{ color: "#a1a1a6", fontSize: "0.9rem", maxWidth: "440px", margin: "0 auto 1.5rem auto", lineHeight: 1.5 }}>
            Download cooperative-perception benchmarks from the catalog or import local datasets without using the terminal.
          </p>
          <button
            type="button"
            onClick={onNavigateGetData}
            style={{
              background: "#2878dc",
              border: "none",
              borderRadius: "6px",
              color: "#fff",
              padding: "0.6rem 1.5rem",
              fontSize: "0.95rem",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Explore Dataset Catalog
          </button>
        </div>
      )}

      {/* Scenes Grid */}
      {scenes.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: "1rem",
          }}
        >
          {scenes.map((scene) => {
            const isValidating = validatingMap[scene.path];
            const validateStatus = validateStatusMap[scene.path];

            return (
              <div
                key={scene.path}
                style={{
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid rgba(255, 255, 255, 0.1)",
                  borderRadius: "10px",
                  padding: "1.2rem",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.5rem" }}>
                    <div>
                      <h4 style={{ margin: 0, fontSize: "1.05rem", color: "#f5f5f7", fontWeight: 600 }}>
                        {scene.scene_id}
                      </h4>
                      <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>
                        {scene.dataset_name || "Cooperative Scene"}
                        {scene.town ? ` · ${scene.town}` : ""}
                      </span>
                    </div>

                    <span
                      style={{
                        fontSize: "0.75rem",
                        padding: "0.15rem 0.5rem",
                        borderRadius: "10px",
                        background:
                          validateStatus === "valid"
                            ? "rgba(46, 160, 67, 0.2)"
                            : validateStatus === "invalid"
                            ? "rgba(255, 69, 58, 0.2)"
                            : "rgba(255, 255, 255, 0.1)",
                        color:
                          validateStatus === "valid"
                            ? "#3fb950"
                            : validateStatus === "invalid"
                            ? "#ff6b6b"
                            : "#8b949e",
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

                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "0.4rem",
                      margin: "0.8rem 0",
                      fontSize: "0.75rem",
                    }}
                  >
                    {scene.duration_s != null && (
                      <span style={{ background: "rgba(255, 255, 255, 0.08)", padding: "0.2rem 0.4rem", borderRadius: "4px", color: "#f5f5f7" }}>
                        ⏱ {scene.duration_s.toFixed(1)}s
                      </span>
                    )}
                    {scene.agent_count != null && (
                      <span style={{ background: "rgba(255, 255, 255, 0.08)", padding: "0.2rem 0.4rem", borderRadius: "4px", color: "#f5f5f7" }}>
                        👥 {scene.agent_count} agents
                      </span>
                    )}
                    <span style={{ background: "rgba(255, 255, 255, 0.08)", padding: "0.2rem 0.4rem", borderRadius: "4px", color: "#f5f5f7" }}>
                      📷 images: {scene.has_images}
                    </span>
                    <span style={{ background: "rgba(255, 255, 255, 0.08)", padding: "0.2rem 0.4rem", borderRadius: "4px", color: "#f5f5f7" }}>
                      🗺 map: {scene.has_map ? "yes" : "no"}
                    </span>
                    <span style={{ background: "rgba(255, 255, 255, 0.08)", padding: "0.2rem 0.4rem", borderRadius: "4px", color: "#a1a1a6" }}>
                      💾 {formatBytes(scene.size_bytes)}
                    </span>
                  </div>
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginTop: "0.8rem",
                    paddingTop: "0.8rem",
                    borderTop: "1px solid rgba(255, 255, 255, 0.08)",
                  }}
                >
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <button
                      type="button"
                      onClick={() => onOpenScene(scene.path)}
                      style={{
                        background: "#2878dc",
                        color: "#fff",
                        border: "none",
                        borderRadius: "5px",
                        padding: "0.35rem 0.75rem",
                        fontSize: "0.8rem",
                        fontWeight: 500,
                        cursor: "pointer",
                      }}
                    >
                      Open
                    </button>
                    <button
                      type="button"
                      onClick={() => revealInFinder(scene.path)}
                      title="Show in Finder"
                      style={{
                        background: "rgba(255, 255, 255, 0.08)",
                        color: "#f5f5f7",
                        border: "1px solid rgba(255, 255, 255, 0.12)",
                        borderRadius: "5px",
                        padding: "0.35rem 0.6rem",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                      }}
                    >
                      Finder
                    </button>
                  </div>

                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <button
                      type="button"
                      onClick={() => handleValidateNow(scene)}
                      disabled={isValidating}
                      title="Validate scene metadata against schema"
                      style={{
                        background: "transparent",
                        color: "#8b949e",
                        border: "none",
                        fontSize: "0.75rem",
                        cursor: isValidating ? "wait" : "pointer",
                      }}
                    >
                      {isValidating ? "Validating…" : "Validate"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setDeleteConfirmScene(scene)}
                      title="Move scene to Trash"
                      style={{
                        background: "transparent",
                        color: "#ff6b6b",
                        border: "none",
                        fontSize: "0.75rem",
                        cursor: "pointer",
                      }}
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
              background: "#1c1c1e",
              borderRadius: "12px",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              maxWidth: "480px",
              width: "100%",
              padding: "1.5rem",
              boxShadow: "0 20px 40px rgba(0, 0, 0, 0.5)",
            }}
          >
            <h3 style={{ margin: "0 0 0.5rem 0", color: "#f5f5f7" }}>
              Where should coperceive keep datasets?
            </h3>
            <p style={{ color: "#a1a1a6", fontSize: "0.85rem", lineHeight: 1.5, marginBottom: "1.2rem" }}>
              Coperceive stores downloaded benchmarks and converted multi-agent scenes in your library folder.
              The default location is <code>~/coperceive-data</code>.
            </p>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                type="button"
                onClick={() => {
                  localStorage.setItem("coperceive_storage_prompted", "true");
                  setShowFirstRunModal(false);
                }}
                style={{
                  padding: "0.5rem 1rem",
                  borderRadius: "6px",
                  border: "1px solid rgba(255, 255, 255, 0.2)",
                  background: "transparent",
                  color: "#f5f5f7",
                  cursor: "pointer",
                }}
              >
                Use default (~/coperceive-data)
              </button>
              <button
                type="button"
                onClick={() => handlePickLibraryRoot(true)}
                style={{
                  padding: "0.5rem 1rem",
                  borderRadius: "6px",
                  border: "none",
                  background: "#2878dc",
                  color: "#fff",
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
              background: "#1c1c1e",
              borderRadius: "12px",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              maxWidth: "480px",
              width: "100%",
              padding: "1.5rem",
              boxShadow: "0 20px 40px rgba(0, 0, 0, 0.5)",
            }}
          >
            <h3 style={{ margin: "0 0 0.5rem 0", color: "#f5f5f7" }}>Change Library Location</h3>
            <p style={{ color: "#a1a1a6", fontSize: "0.85rem", lineHeight: 1.5, marginBottom: "1rem" }}>
              Moving your library will automatically transfer existing converted scenes into the new directory.
            </p>
            {storage && (
              <div
                style={{
                  background: "rgba(0, 0, 0, 0.3)",
                  padding: "0.75rem",
                  borderRadius: "6px",
                  fontFamily: "'Geist Mono', monospace",
                  fontSize: "0.8rem",
                  color: "#8b949e",
                  marginBottom: "1.2rem",
                }}
              >
                Current: {storage.library_root}
              </div>
            )}

            {movingStorage && (
              <div style={{ color: "#58a6ff", fontSize: "0.85rem", marginBottom: "1rem" }}>
                Moving scenes to new storage location…
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                type="button"
                onClick={() => setShowSettingsModal(false)}
                disabled={movingStorage}
                style={{
                  padding: "0.5rem 1rem",
                  borderRadius: "6px",
                  border: "1px solid rgba(255, 255, 255, 0.2)",
                  background: "transparent",
                  color: "#f5f5f7",
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
                  padding: "0.5rem 1rem",
                  borderRadius: "6px",
                  border: "none",
                  background: "#2878dc",
                  color: "#fff",
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
              background: "#1c1c1e",
              borderRadius: "12px",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              maxWidth: "440px",
              width: "100%",
              padding: "1.5rem",
              boxShadow: "0 20px 40px rgba(0, 0, 0, 0.5)",
            }}
          >
            <h3 style={{ margin: "0 0 0.5rem 0", color: "#f5f5f7" }}>Delete Scene?</h3>
            <p style={{ color: "#a1a1a6", fontSize: "0.85rem", lineHeight: 1.5, marginBottom: "1.2rem" }}>
              Are you sure you want to delete <strong>{deleteConfirmScene.scene_id}</strong>? The scene directory will be moved to your Trash.
            </p>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                type="button"
                onClick={() => setDeleteConfirmScene(null)}
                style={{
                  padding: "0.5rem 1rem",
                  borderRadius: "6px",
                  border: "1px solid rgba(255, 255, 255, 0.2)",
                  background: "transparent",
                  color: "#f5f5f7",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handleDeleteScene(deleteConfirmScene)}
                style={{
                  padding: "0.5rem 1rem",
                  borderRadius: "6px",
                  border: "none",
                  background: "#ff453a",
                  color: "#fff",
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
