// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useState } from "react";
import type { LibrarySceneInfo, StorageStats } from "./types";
import PanelState from "./PanelState";
import { PANEL_DEFINITIONS } from "./panelStates";
import {
  cancelDemoScene,
  deleteLibraryScene,
  dismissWelcomeCard,
  downloadTownMap,
  ensureDemoScene,
  getLibraryScenes,
  getPersistentState,
  getStorageInfo,
  libraryThumbnail,
  listServerProfiles,
  moveLibraryRoot,
  onDemoProgress,
  onTownMapProgress,
  pickDataRoot,
  revealInFinder,
  setLibraryRoot,
  validateScene,
} from "./local";
import { errorText } from "./errors";
import { getRevealLabel } from "./platform";
import {
  Facts,
  GridTable,
  InspectorSection,
  MiniBar,
  PageLayout,
  PageToolbar,
  useDefaultSelection,
  usePageHeader,
  type GridColumn,
} from "./components/Page";
import { datasetJobs, subscribeDatasetJobs, type DatasetJobs } from "./jobsStore";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

interface Props {
  onOpenScene: (scenePath: string) => void;
  onNavigateGetData: (initialDatasetId?: string, initialPresetId?: string) => void;
  onNavigateOpenFolder?: () => void;
  onNavigateServers?: () => void;
}

function formatSeconds(seconds: number): string {
  return `${seconds.toFixed(1)} s`;
}

function formatHz(hz: number): string {
  return `${Math.abs(hz - Math.round(hz)) < 0.05 ? Math.round(hz) : hz.toFixed(1)} Hz`;
}

/** The library sends `dataset`; seeded test fixtures use `dataset_name`. */
function datasetLabel(scene: LibrarySceneInfo): string {
  return scene.dataset || scene.dataset_name || "Scene";
}

function modalityChips(scene: LibrarySceneInfo): Array<[string, boolean]> {
  const modalities = scene.sensor_modalities ?? [];
  const chips: Array<[string, boolean]> = [["Cam", scene.has_images !== "none" || modalities.includes("camera")]];
  // LiDAR is known only from the manifest's sensors; without them, say nothing.
  if (scene.sensor_modalities) chips.push(["LiDAR", modalities.includes("lidar")]);
  chips.push(["Map", scene.has_map]);
  return chips;
}

function agentTypeLabel(type: string): string {
  return { vehicle: "Vehicle", uav: "Drone", drone: "Drone", rsu: "RSU", infrastructure: "RSU" }[type] ?? type;
}

function sensorsLabel(agent: { cameras: number; lidars: number }): string {
  const parts = [];
  if (agent.cameras) parts.push(`${agent.cameras} cam`);
  if (agent.lidars) parts.push(`${agent.lidars} LiDAR`);
  return parts.join(" · ") || "no sensors";
}

const sceneKey = (scene: LibrarySceneInfo) => scene.path;

function ValidationBadge({ status }: { status: "valid" | "invalid" | "checking" }) {
  return (
    <span className={`library-badge is-${status}`} data-validate={status}>
      <span className="c-dot" />
      {status === "checking" ? "Validating…" : status === "valid" ? "Valid ✓" : "Check failed"}
    </span>
  );
}

export function DemoProgressBanner({
  stage,
  elapsedSec,
  onCancel,
}: {
  stage: string;
  elapsedSec: number;
  onCancel: () => void;
}) {
  const activeStage =
    stage ||
    (elapsedSec < 3
      ? "Starting bundled Python generator…"
      : elapsedSec < 8
        ? "Synthesising LiDAR & multi-agent cameras…"
        : "Packing LiDAR frames & scoring predictions…");

  return (
    <div
      data-testid="demo-progress-banner"
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        padding: "10px 14px",
        margin: "8px 16px",
        background: "var(--panel)",
        border: "1px solid var(--line)",
        borderRadius: "8px",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "2px", minWidth: 0 }}>
        <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>
          Generating demo scene… (8.2 MB demo cache) · {elapsedSec}s elapsed
        </div>
        <div style={{ fontSize: "12px", color: "var(--text-subtle)" }}>
          {activeStage} · This can take up to 2 minutes on older Macs.
        </div>
      </div>
      <button
        type="button"
        data-testid="demo-progress-cancel"
        className="ghost"
        onClick={onCancel}
        style={{
          padding: "5px 12px",
          fontSize: "12px",
          border: "1px solid var(--line)",
          borderRadius: "6px",
          flexShrink: 0,
        }}
      >
        Cancel
      </button>
    </div>
  );
}

export function WelcomeCard({
  generatingDemo,
  demoElapsedSec,
  demoStage,
  demoError,
  onOpenDemo,
  onCancelDemo,
  onTrySample,
  onDismiss,
}: {
  generatingDemo: boolean;
  demoElapsedSec: number;
  demoStage?: string;
  demoError: string | null;
  onOpenDemo: () => void;
  onCancelDemo?: () => void;
  onTrySample: () => void;
  onDismiss: () => void;
}) {
  const activeStage =
    demoStage ||
    (demoElapsedSec < 3
      ? "Starting bundled Python generator…"
      : demoElapsedSec < 8
        ? "Synthesising LiDAR & multi-agent cameras…"
        : "Packing LiDAR frames & scoring predictions…");

  return (
    <div
      className="welcome-card"
      role="region"
      aria-label="Welcome to Coperceive"
      style={{
        maxWidth: "600px",
        margin: "0 auto",
        padding: "24px 28px",
        background: "var(--panel)",
        border: "1px solid var(--line)",
        borderRadius: "10px",
        boxShadow: "var(--shadow)",
        display: "flex",
        flexDirection: "column",
        gap: "18px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "17px", fontWeight: 600, color: "var(--text)" }}>
            Welcome to Coperceive
          </h2>
          <p style={{ margin: "6px 0 0", fontSize: "13px", color: "var(--text-subtle)", lineHeight: 1.45 }}>
            Explore multi-agent cooperative perception, inspect multi-vehicle & roadside LiDAR and cameras,
            and evaluate connected vehicle detections.
          </p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss welcome card"
          title="Dismiss welcome card"
          className="ghost"
          style={{
            fontSize: "16px",
            lineHeight: 1,
            padding: "4px 8px",
            color: "var(--muted)",
            cursor: "pointer",
            flexShrink: 0,
          }}
        >
          ✕
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <button
          type="button"
          data-testid="welcome-open-demo"
          className="c-primary-wide"
          disabled={generatingDemo}
          onClick={onOpenDemo}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            padding: "12px 16px",
            gap: "2px",
            textAlign: "left",
          }}
        >
          <span style={{ fontWeight: 600, fontSize: "13.5px" }}>
            {generatingDemo
              ? `Generating demo scene… (8.2 MB) · ${demoElapsedSec}s elapsed`
              : "Open the demo"}
          </span>
          <span style={{ fontSize: "11.5px", opacity: 0.85 }}>
            {generatingDemo
              ? `${activeStage} · This can take up to 2 minutes on older Macs.`
              : "Works offline · No download needed (~8.2 MB synthetic junction)"}
          </span>
        </button>

        {generatingDemo && onCancelDemo && (
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
            <span style={{ fontSize: "12px", color: "var(--text-subtle)" }}>
              Running in background — you can still browse other tabs or cancel.
            </span>
            <button
              type="button"
              data-testid="welcome-cancel-demo"
              className="ghost"
              onClick={onCancelDemo}
              style={{
                padding: "4px 10px",
                fontSize: "12px",
                border: "1px solid var(--line)",
                borderRadius: "6px",
              }}
            >
              Cancel
            </button>
          </div>
        )}

        <button
          type="button"
          data-testid="welcome-try-sample"
          className="ghost"
          onClick={onTrySample}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            padding: "10px 16px",
            gap: "2px",
            textAlign: "left",
            border: "1px solid var(--line)",
            borderRadius: "6px",
          }}
        >
          <span style={{ fontWeight: 550, fontSize: "13px", color: "var(--text)" }}>
            Try a real sample (Griffin, MIT licence)
          </span>
          <span style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
            Dev sample · 143 MB download from Hugging Face
          </span>
        </button>
      </div>

      {demoError && (
        <div style={{ fontSize: "12px", color: "var(--danger)" }}>
          Failed to generate demo scene: {demoError}
        </div>
      )}
    </div>
  );
}

export default function LibraryScreen({
  onOpenScene,
  onNavigateGetData,
  onNavigateOpenFolder,
  onNavigateServers,
}: Props) {
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
  const [filterQuery, setFilterQuery] = useState<string>("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [welcomeCardDismissed, setWelcomeCardDismissed] = useState<boolean>(false);
  const [serverCount, setServerCount] = useState<number>(0);
  const [generatingDemo, setGeneratingDemo] = useState<boolean>(false);
  const [demoElapsedSec, setDemoElapsedSec] = useState<number>(0);
  const [demoStage, setDemoStage] = useState<string>("");
  const [demoError, setDemoError] = useState<string | null>(null);
  const [clusterProgress, setClusterProgress] = useState<DatasetJobs | null>(() => datasetJobs()[0] ?? null);
  useEffect(() => subscribeDatasetJobs(() => setClusterProgress(datasetJobs()[0] ?? null)), []);
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
        welcomeCardDismissed?: boolean;
        serverCount?: number;
      }>;
      if (custom.detail?.storage !== undefined) {
        setStorage(custom.detail.storage);
        setShowFirstRunModal(custom.detail.storage?.is_configured === false);
      }
      if (custom.detail?.welcomeCardDismissed !== undefined) {
        setWelcomeCardDismissed(custom.detail.welcomeCardDismissed);
      }
      if (custom.detail?.serverCount !== undefined) {
        setServerCount(custom.detail.serverCount);
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

  const handleOpenDemo = async () => {
    setGeneratingDemo(true);
    setDemoElapsedSec(0);
    setDemoStage("Starting bundled Python generator…");
    setDemoError(null);
    const start = Date.now();
    const timer = setInterval(() => {
      setDemoElapsedSec(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    const unlisten = await onDemoProgress((payload) => {
      if (payload.stage) {
        setDemoStage(payload.stage);
      }
      if (typeof payload.elapsed_seconds === "number") {
        setDemoElapsedSec(Math.floor(payload.elapsed_seconds));
      }
    });
    try {
      const outcome = await ensureDemoScene();
      clearInterval(timer);
      unlisten();
      setGeneratingDemo(false);
      onOpenScene(outcome.path);
    } catch (err) {
      clearInterval(timer);
      unlisten();
      setGeneratingDemo(false);
      const msg = errorText(err);
      if (!msg.toLowerCase().includes("cancelled")) {
        setDemoError(msg);
      }
    }
  };

  const handleCancelDemo = async () => {
    await cancelDemoScene();
    setGeneratingDemo(false);
  };

  const handleDismissWelcomeCard = async () => {
    setWelcomeCardDismissed(true);
    await dismissWelcomeCard();
  };

  const loadData = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [stats, list, pState, servers] = await Promise.all([
        getStorageInfo().catch(() => null),
        getLibraryScenes().catch(() => []),
        getPersistentState().catch(() => null),
        listServerProfiles().catch(() => []),
      ]);
      setStorage(stats);
      applyScenes(list);
      if (stats && stats.is_configured === false) {
        setShowFirstRunModal(true);
      }
      if (pState?.welcome_card_dismissed) {
        setWelcomeCardDismissed(true);
      }
      setServerCount(servers.length);
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

  // The first row is selected by default (and again if the selected one goes).
  useDefaultSelection(filteredScenes, sceneKey, selectedPath, setSelectedPath);
  const selected = filteredScenes.find((scene) => scene.path === selectedPath) ?? null;
  const knownDurations = scenes.filter((scene) => typeof scene.duration_s === "number");
  usePageHeader("library", {
    navCount: loading ? undefined : String(scenes.length),
    meta: scenes.length
      ? [
          `${scenes.length} ${scenes.length === 1 ? "scene" : "scenes"}`,
          knownDurations.length === scenes.length ? formatSeconds(totalDurationSec) : null,
          formatBytes(scenes.reduce((sum, scene) => sum + scene.size_bytes, 0)),
        ]
          .filter(Boolean)
          .join(" · ")
      : undefined,
    actions: (
      <button type="button" className="c-header-action has-plus" onClick={() => onNavigateGetData?.()}>
        Add scenes
      </button>
    ),
  });

  const columns: GridColumn<LibrarySceneInfo>[] = [
    {
      key: "scene",
      header: "Scene",
      min: 130,
      max: 260,
      render: (scene) => (
        <span className="c-mono" style={{ color: "var(--text)", fontSize: "12.5px" }} aria-label={`Scene ${scene.scene_id}`}>
          {scene.scene_id}
        </span>
      ),
    },
    {
      key: "dataset",
      header: "Dataset · town",
      min: 150,
      max: 320,
      render: (scene) => (
        <>
          {datasetLabel(scene)}
          {scene.town ? <span style={{ color: "var(--text-subtle)" }}> · {scene.town}</span> : null}
        </>
      ),
    },
    {
      key: "duration",
      header: "Duration",
      min: 110,
      max: 110,
      render: (scene) =>
        typeof scene.duration_s === "number" ? (
          <span style={{ display: "flex", alignItems: "center", gap: "8px", fontVariantNumeric: "tabular-nums" }}>
            <span style={{ width: "40px", textAlign: "right" }}>{formatSeconds(scene.duration_s)}</span>
            <MiniBar value={scene.duration_s / maxDuration} />
          </span>
        ) : (
          <span style={{ color: "var(--text-subtle)" }} title="Not recorded yet; the background check fills it in">
            —
          </span>
        ),
    },
    {
      key: "agents",
      header: "Agents",
      min: 64,
      max: 64,
      render: (scene) => (
        <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {scene.agent_count ?? "—"}
          <span style={{ display: "flex", gap: "2px" }} aria-hidden="true">
            {Array.from({ length: Math.min(scene.agent_count ?? 0, 6) }).map((_, index) => (
              <span key={index} style={{ width: "4px", height: "10px", borderRadius: "1px", background: "var(--text-subtle)" }} />
            ))}
          </span>
        </span>
      ),
    },
    {
      key: "modalities",
      header: "Modalities",
      min: 150,
      max: 150,
      render: (scene) => (
        <span style={{ display: "flex", gap: "4px" }}>
          {modalityChips(scene).map(([label, on]) => (
            <span key={label} className={`c-chip${on ? "" : " is-off"}`} title={on ? `Has ${label}` : `No ${label}`}>
              {label}
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "size",
      header: "Size",
      min: 72,
      max: 72,
      align: "right",
      render: (scene) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{formatBytes(scene.size_bytes)}</span>,
    },
    {
      key: "check",
      header: "Check",
      min: 96,
      max: 96,
      render: (scene) => <ValidationBadge status={validateStatusMap[scene.path] ?? "checking"} />,
    },
    {
      key: "open",
      header: "",
      min: 64,
      max: 64,
      align: "right",
      render: (scene) => (
        <button
          type="button"
          className="c-row-open"
          onClick={(event) => {
            event.stopPropagation();
            onOpenScene(scene.path);
          }}
          aria-label={`Open scene ${scene.scene_id}`}
        >
          Open
        </button>
      ),
    },
  ];

  const prepared = clusterProgress;
  const install = selected ? mapInstall[selected.path] : undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 52px)", fontSize: "13px" }}>
      <PageLayout
        inspectorLabel="Selected scene"
        inspector={
          selected ? (
            <>
              <InspectorSection title="Selected scene">
                <div className="c-mono" style={{ fontSize: "14px", color: "var(--text)" }}>
                  {selected.scene_id}
                </div>
                <div style={{ color: "var(--text-subtle)", fontSize: "12px" }}>
                  {[datasetLabel(selected), selected.map_name ? selected.map_name.replace("Carla/Maps/", "CARLA ") : selected.town]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
                {thumbnails[selected.path] ? (
                  <img
                    src={thumbnails[selected.path]}
                    alt={`First camera frame of ${selected.scene_id}`}
                    style={{ width: "100%", borderRadius: "6px", border: "1px solid var(--line)" }}
                  />
                ) : null}
                <button type="button" className="c-primary-wide" onClick={() => onOpenScene(selected.path)}>
                  Open in viewer
                </button>
              </InspectorSection>
              <InspectorSection>
                <Facts
                  items={[
                    typeof selected.duration_s === "number" && ["Duration", formatSeconds(selected.duration_s)],
                    typeof selected.sample_count === "number" && [
                      "Frames / agent",
                      <>
                        {selected.sample_count}
                        {typeof selected.rate_hz === "number" ? (
                          <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}> @ {formatHz(selected.rate_hz)}</span>
                        ) : null}
                      </>,
                    ],
                    ["Camera images", { full: "Full", preview: "Previews", none: "None" }[selected.has_images]],
                    ["HD map", selected.has_map ? "Yes" : "No"],
                  ]}
                />
                {!selected.has_map && selected.map_name === "Carla/Maps/Town03" ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <button
                      type="button"
                      className="c-secondary"
                      onClick={() => void installTown03Map(selected)}
                      disabled={install !== undefined && !install.done && !install.error}
                      title="Downloads the official CARLA 0.9.15 Town03 OpenDRIVE (pinned SHA-256), ingests it and aligns it to this scene. Licence: CARLA 0.9.15 README says the town assets are CC-BY (no version stated)."
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
                    {install ? (
                      <span style={{ color: "var(--text-tertiary)", fontSize: "11px" }}>
                        {install.error ? `Could not install: ${install.error}` : `${install.percent}% · ${install.message}`}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </InspectorSection>
              {selected.agents && selected.agents.length > 0 ? (
                <InspectorSection title={`${selected.agents.length} ${selected.agents.length === 1 ? "agent" : "agents"}`}>
                  {selected.agents.map((agent) => (
                    <div key={agent.agent_id} className="c-agent-row">
                      <span style={{ color: "var(--text)" }}>{agent.display_name || agentTypeLabel(agent.agent_type)}</span>
                      <span style={{ color: "var(--text-subtle)", fontSize: "11px" }}>{sensorsLabel(agent)}</span>
                    </div>
                  ))}
                </InspectorSection>
              ) : null}
              <InspectorSection title={`${formatBytes(selected.size_bytes)} on disk`}>
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  <button type="button" className="c-secondary" onClick={() => revealInFinder(selected.path)}>
                    {getRevealLabel()}
                  </button>
                  <button
                    type="button"
                    className="c-secondary"
                    onClick={() => void handleValidateNow(selected)}
                    disabled={validatingMap[selected.path]}
                  >
                    {validatingMap[selected.path] ? "Checking…" : "Re-check"}
                  </button>
                  <button
                    type="button"
                    className="c-secondary is-danger"
                    onClick={() => setDeleteConfirmScene(selected)}
                  >
                    Remove…
                  </button>
                </div>
              </InspectorSection>
            </>
          ) : null
        }
      >
        {storage ? (
          <PageToolbar>
            <span>Folder</span>
            <span className="c-mono" style={{ color: "var(--text)" }}>
              {storage.library_root}
            </span>
            <button type="button" className="c-link" onClick={() => revealInFinder(storage.library_root)}>
              {getRevealLabel()}
            </button>
            <button type="button" className="c-link" onClick={() => setShowSettingsModal(true)}>
              Change location…
            </button>
            <span className="c-toolbar-end" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              {storage.total_bytes > 0 ? (
                <span title={storage.volume_name} style={{ width: "160px", display: "flex" }}>
                  <MiniBar value={storage.used_bytes / storage.total_bytes} tone="primary" />
                </span>
              ) : null}
              <span style={{ color: "var(--text-secondary)" }}>
                {formatBytes(storage.used_bytes)} library · {formatBytes(storage.free_bytes)} free
              </span>
            </span>
          </PageToolbar>
        ) : null}

        {scenes.length > 0 ? (
          <PageToolbar>
            <input
              type="search"
              placeholder="Filter scene or dataset"
              aria-label="Filter library scenes"
              value={filterQuery}
              onChange={(event) => setFilterQuery(event.target.value)}
              className="c-search"
            />
            <span className="c-toolbar-end">
              {filteredScenes.length === scenes.length
                ? `${scenes.length} ${scenes.length === 1 ? "scene" : "scenes"}`
                : `${filteredScenes.length} of ${scenes.length} scenes`}
            </span>
          </PageToolbar>
        ) : null}

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
        {!loading && !loadError && scenes.length === 0 && !showFirstRunModal && (
          <div style={{ padding: "24px" }}>
            {serverCount === 0 && !welcomeCardDismissed ? (
              <WelcomeCard
                generatingDemo={generatingDemo}
                demoElapsedSec={demoElapsedSec}
                demoStage={demoStage}
                demoError={demoError}
                onOpenDemo={() => void handleOpenDemo()}
                onCancelDemo={() => void handleCancelDemo()}
                onTrySample={() => onNavigateGetData("griffin", "dev")}
                onDismiss={() => void handleDismissWelcomeCard()}
              />
            ) : (
              <PanelState
                kind="empty"
                title={PANEL_DEFINITIONS.library.empty.title}
                description={PANEL_DEFINITIONS.library.empty.description}
                actionLabel={PANEL_DEFINITIONS.library.empty.actionLabel}
                onAction={() => onNavigateGetData()}
              />
            )}
          </div>
        )}

        {scenes.length > 0 ? (
          <GridTable
            label="Library scenes"
            columns={columns}
            rows={filteredScenes}
            rowKey={sceneKey}
            selectedKey={selected?.path ?? null}
            onSelect={(scene) => setSelectedPath(scene.path)}
            onActivate={(scene) => onOpenScene(scene.path)}
          />
        ) : null}

        <section className="c-add-more" aria-label="Add more scenes">
          <div className="c-inspector-label" style={{ padding: "0 0 10px" }}>
            Add more scenes
          </div>
          <div className="c-add-cards">
            <button type="button" className="c-add-card" onClick={() => onNavigateGetData?.()}>
              <span className="c-add-title">Download from catalog →</span>
              <span className="c-add-note">Datasets with a recorded licence, measured before you download</span>
            </button>
            <button type="button" className="c-add-card" onClick={onNavigateOpenFolder}>
              <span className="c-add-title">Import a local folder →</span>
              <span className="c-add-note">Any folder holding schema 1 scenes</span>
            </button>
            <button type="button" className="c-add-card" onClick={onNavigateServers}>
              <span className="c-add-title">Prepare on HPC →</span>
              <span className="c-add-note">
                {prepared
                  ? `${prepared.label} · ${prepared.ready} of ${prepared.total} ready`
                  : "Prepare a dataset on a cluster you reach over SSH"}
              </span>
            </button>
          </div>
        </section>
      </PageLayout>

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
