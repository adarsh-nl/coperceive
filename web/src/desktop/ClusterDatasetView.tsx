// SPDX-License-Identifier: Apache-2.0
/**
 * Cluster Dataset View
 *
 * Implements section B and C of the Cluster Datasets design:
 * - Dataset header (adapter, split, raw root, cache path, storage estimate)
 * - Index scene table with status chips (ready, preparing, not_prepared, failed, stale)
 * - Status polling every ~5s while anything is preparing
 * - Prepare whole dataset flow with Slurm script preview and confirmation
 * - Single-scene prepare flow with auto-open upon completion
 * - Collapsible non-blocking index errors
 * - Accessible, virtualized/paginated scene table for 200+ scenes
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ServerEntry } from "./hpc";
import {
  datasetIndex,
  datasetOpen,
  datasetPrepare,
  datasetPrepareSubmit,
  datasetStatus,
  formatDuration,
  formatLicenceInfo,
  formatSensorsSummary,
  formatStorageEstimate,
  type ClusterDetectDocument,
  type ClusterIndexDocument,
  type ClusterIndexScene,
  type ClusterSceneState,
  type ClusterSceneStatusEntry,
  type DatasetPreparePreview,
} from "./clusterDatasets";

const cardStyle = {
  backgroundColor: "rgba(28, 28, 30, 0.95)",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  borderRadius: "14px",
  padding: "1.25rem",
  marginTop: "1rem",
  boxShadow: "0 20px 50px rgba(0, 0, 0, 0.45)",
} as const;

const secondaryButton = {
  padding: "0.4rem 0.75rem",
  borderRadius: "6px",
  border: "1px solid rgba(255, 255, 255, 0.18)",
  backgroundColor: "transparent",
  color: "#f5f5f7",
  fontSize: "0.8rem",
  cursor: "pointer",
} as const;

const primaryButton = {
  padding: "0.45rem 0.9rem",
  borderRadius: "6px",
  border: "none",
  backgroundColor: "#1f6feb",
  color: "#ffffff",
  fontSize: "0.82rem",
  fontWeight: 600,
  cursor: "pointer",
} as const;

const chipBase = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "0.15rem 0.55rem",
  borderRadius: "999px",
  fontSize: "0.72rem",
  fontWeight: 500,
  minWidth: "82px",
  textAlign: "center" as const,
  userSelect: "none" as const,
};

function StatusChip({
  state,
  reason,
  progress,
}: {
  state: ClusterSceneState;
  reason?: string | null;
  progress?: number | null;
}) {
  let bg = "rgba(255, 255, 255, 0.08)";
  let color = "#8b949e";
  let label = "Not prepared";

  switch (state) {
    case "ready":
      bg = "rgba(63, 185, 80, 0.2)";
      color = "#7ee787";
      label = "Ready";
      break;
    case "preparing":
      bg = "rgba(56, 139, 253, 0.25)";
      color = "#79c0ff";
      label = progress != null ? `Preparing ${progress}%` : "Preparing…";
      break;
    case "failed":
      bg = "rgba(248, 81, 73, 0.2)";
      color = "#ff7b72";
      label = "Failed";
      break;
    case "stale":
      bg = "rgba(210, 153, 34, 0.25)";
      color = "#e3b341";
      label = "Stale";
      break;
    case "not_prepared":
    default:
      bg = "rgba(255, 255, 255, 0.08)";
      color = "#8b949e";
      label = "Not prepared";
      break;
  }

  return (
    <span
      role="status"
      title={reason ? `Failure reason: ${reason}` : undefined}
      style={{
        ...chipBase,
        backgroundColor: bg,
        color,
        border: `1px solid ${color}40`,
        cursor: reason ? "help" : "default",
      }}
    >
      {label}
    </span>
  );
}

export default function ClusterDatasetView({
  profile,
  dataset,
  onBack,
  onClose,
}: {
  profile: ServerEntry;
  dataset: ClusterDetectDocument;
  onBack: () => void;
  onClose: () => void;
}) {
  const [indexDoc, setIndexDoc] = useState<ClusterIndexDocument | null>(null);
  const [loadingIndex, setLoadingIndex] = useState(true);
  const [indexError, setIndexError] = useState<string | null>(null);

  // Status mapping: scene_id -> status entry
  const [statuses, setStatuses] = useState<Map<string, ClusterSceneStatusEntry>>(new Map());
  const [isPolling, setIsPolling] = useState(false);

  // Prepare Whole Dataset state
  const [showPrepareModal, setShowPrepareModal] = useState(false);
  const [preparePreview, setPreparePreview] = useState<DatasetPreparePreview | null>(null);
  const [prepareConfirmed, setPrepareConfirmed] = useState(false);
  const [prepareBusy, setPrepareBusy] = useState(false);
  const [prepareOutcome, setPrepareOutcome] = useState<string | null>(null);
  const [prepareError, setPrepareError] = useState<string | null>(null);

  // Single scene prepare modal/state
  const [singleScenePrepare, setSingleScenePrepare] = useState<string | null>(null);
  const [singlePreparePreview, setSinglePreparePreview] = useState<DatasetPreparePreview | null>(null);
  const [singleConfirmed, setSingleConfirmed] = useState(false);
  const [autoOpenSceneId, setAutoOpenSceneId] = useState<string | null>(null);

  // Search & Pagination for 200+ scenes
  const [filterQuery, setFilterQuery] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 50;

  const [serverCacheRoot, setServerCacheRoot] = useState<string>("");

  // Load index document on mount
  useEffect(() => {
    let active = true;
    setLoadingIndex(true);
    setIndexError(null);

    const adapter = dataset.adapter || "opv2v";
    const rawRoot = dataset.raw_root || dataset.path;

    datasetIndex(profile.id, adapter, rawRoot)
      .then((res) => {
        if (!active) return;
        setIndexDoc(res);
        setLoadingIndex(false);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setIndexError(err instanceof Error ? err.message : String(err));
        setLoadingIndex(false);
      });

    return () => {
      active = false;
    };
  }, [profile.id, dataset]);

  // Poll status every 5 seconds while anything is preparing
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = async () => {
    try {
      const statusDoc = await datasetStatus(profile.id, {
        adapter: dataset.adapter || "opv2v",
        rawRoot: dataset.raw_root || dataset.path,
        split: dataset.split,
      });
      if (statusDoc.cache_root) {
        setServerCacheRoot(statusDoc.cache_root);
      }
      const nextMap = new Map<string, ClusterSceneStatusEntry>();
      for (const entry of statusDoc.scenes) {
        nextMap.set(entry.scene_id, entry);
      }
      setStatuses(nextMap);

      // Check if autoOpenSceneId became ready
      if (autoOpenSceneId && nextMap.get(autoOpenSceneId)?.state === "ready") {
        const idToOpen = autoOpenSceneId;
        setAutoOpenSceneId(null);
        void datasetOpen(profile.id, idToOpen, {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
        });
      }

      // Check if anything is still preparing
      const hasPreparing = Array.from(nextMap.values()).some(
        (s) => s.state === "preparing",
      );
      if (!hasPreparing && pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
        setIsPolling(false);
      }
    } catch {
      // Ignore transient polling failure
    }
  };

  const startPolling = () => {
    if (pollTimerRef.current) return;
    setIsPolling(true);
    void fetchStatus();
    pollTimerRef.current = setInterval(() => {
      void fetchStatus();
    }, 5000);
  };

  useEffect(() => {
    void fetchStatus();
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  // Check if any scene is currently preparing; if so, ensure polling is active
  const hasPreparingScenes = useMemo(() => {
    return Array.from(statuses.values()).some((s) => s.state === "preparing");
  }, [statuses]);

  useEffect(() => {
    if (hasPreparingScenes && !pollTimerRef.current) {
      startPolling();
    }
  }, [hasPreparingScenes]);

  // Open a scene flow
  const handleOpenScene = async (sceneId: string) => {
    const currentStatus = statuses.get(sceneId)?.state ?? "not_prepared";

    if (currentStatus === "ready") {
      try {
        await datasetOpen(profile.id, sceneId, {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
        });
      } catch (err) {
        alert(err instanceof Error ? err.message : String(err));
      }
      return;
    }

    // Scene not ready: open single-scene prepare flow
    setSingleScenePrepare(sceneId);
    setSingleConfirmed(false);
    try {
      const preview = await datasetPrepare(profile.id, {
        adapter: dataset.adapter || "opv2v",
        rawRoot: dataset.raw_root || dataset.path,
        split: dataset.split,
        scenes: [sceneId],
      });
      if (preview.cache_root) {
        setServerCacheRoot(preview.cache_root);
      }
      setSinglePreparePreview(preview);
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  };

  const handleConfirmSingleScenePrepare = async () => {
    if (!singleScenePrepare || !singlePreparePreview) return;
    try {
      await datasetPrepareSubmit(profile.id, singlePreparePreview.preview_hash, true);
      setAutoOpenSceneId(singleScenePrepare);
      setSingleScenePrepare(null);
      setSinglePreparePreview(null);
      startPolling();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  };

  // Whole dataset prepare flow
  const handlePrepareWholeDatasetClick = async () => {
    setShowPrepareModal(true);
    setPrepareConfirmed(false);
    setPrepareError(null);
    setPrepareOutcome(null);

    try {
      const preview = await datasetPrepare(profile.id, {
        adapter: dataset.adapter || "opv2v",
        rawRoot: dataset.raw_root || dataset.path,
        split: dataset.split,
        scenes: "all",
      });
      if (preview.cache_root) {
        setServerCacheRoot(preview.cache_root);
      }
      setPreparePreview(preview);
    } catch (err) {
      setPrepareError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleConfirmWholeDatasetSubmit = async () => {
    if (!preparePreview || !prepareConfirmed) return;
    setPrepareBusy(true);
    setPrepareError(null);

    try {
      const res = await datasetPrepareSubmit(profile.id, preparePreview.preview_hash, true);
      setPrepareOutcome(`Submitted preparation job ${res.job_id}. Ingesting scenes on compute node.`);
      setShowPrepareModal(false);
      startPolling();
    } catch (err) {
      setPrepareError(err instanceof Error ? err.message : String(err));
    } finally {
      setPrepareBusy(false);
    }
  };

  // Filtered and paginated scene listing
  const allScenes = indexDoc?.scenes || [];
  const filteredScenes = useMemo(() => {
    if (!filterQuery.trim()) return allScenes;
    const q = filterQuery.toLowerCase();
    return allScenes.filter((s) => s.scene_id.toLowerCase().includes(q));
  }, [allScenes, filterQuery]);

  const totalPages = Math.ceil(filteredScenes.length / PAGE_SIZE) || 1;
  const paginatedScenes = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return filteredScenes.slice(start, start + PAGE_SIZE);
  }, [filteredScenes, page]);

  const licenceInfo = formatLicenceInfo(dataset.licence);

  return (
    <section aria-label="Dataset view" style={cardStyle}>
      {/* Top action header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid rgba(255,255,255,0.1)", paddingBottom: "0.75rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <button type="button" style={secondaryButton} onClick={onBack}>
            ← Back to folders
          </button>
          <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#f5f5f7" }}>
            {dataset.adapter ? dataset.adapter.toUpperCase() : "Dataset"} · {dataset.split ? `${dataset.split} · ` : ""}{dataset.scene_count} scenes
          </h3>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <button
            type="button"
            style={primaryButton}
            onClick={() => void handlePrepareWholeDatasetClick()}
            disabled={loadingIndex}
          >
            Prepare whole dataset
          </button>
          <button type="button" style={secondaryButton} onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      {/* Dataset Metadata Box */}
      <div style={{ marginTop: "0.75rem", padding: "0.75rem", borderRadius: "8px", backgroundColor: "rgba(0,0,0,0.35)", fontSize: "0.8rem", color: "#c9d1d9", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "0.6rem" }}>
        <div>
          <span style={{ color: "#8b949e" }}>Raw root: </span>
          <span style={{ fontFamily: "monospace", color: "#f5f5f7" }}>{dataset.raw_root || dataset.path}</span>
        </div>
        <div>
          <span style={{ color: "#8b949e" }}>Cache path: </span>
          <span style={{ fontFamily: "monospace", color: "#79c0ff" }}>{serverCacheRoot || "Loading…"}</span>
        </div>
        <div>
          <span style={{ color: "#8b949e" }}>Storage estimate: </span>
          <span style={{ color: "#f5f5f7", fontWeight: 500 }}>{indexDoc ? formatStorageEstimate(indexDoc) : "Estimating…"}</span>
        </div>
        <div>
          <span style={{ color: "#8b949e" }}>Licence: </span>
          <span style={{ color: "#f5f5f7" }}>{licenceInfo.statusText}</span>
          {licenceInfo.links.length > 0 ? (
            <span style={{ marginLeft: "0.35rem" }}>
              {licenceInfo.links.map((link, idx) => (
                <a
                  key={link}
                  href={link}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "#58a6ff", textDecoration: "underline", marginRight: "0.3rem" }}
                >
                  [{idx + 1}]
                </a>
              ))}
            </span>
          ) : null}
        </div>
      </div>

      {prepareOutcome ? (
        <div role="status" style={{ marginTop: "0.6rem", padding: "0.6rem 0.8rem", borderRadius: "8px", backgroundColor: "rgba(63,185,80,0.15)", border: "1px solid rgba(63,185,80,0.35)", color: "#7ee787", fontSize: "0.82rem" }}>
          ✓ {prepareOutcome}
        </div>
      ) : null}

      {/* Non-blocking index check errors */}
      {indexDoc && indexDoc.errors.length > 0 ? (
        <details style={{ marginTop: "0.75rem", padding: "0.5rem 0.75rem", borderRadius: "8px", backgroundColor: "rgba(210,153,34,0.1)", border: "1px solid rgba(210,153,34,0.3)", color: "#e3b341", fontSize: "0.78rem" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
            ⚠️ {indexDoc.errors.length} non-blocking check issue(s) detected in raw dataset
          </summary>
          <ul style={{ margin: "0.4rem 0 0", paddingLeft: "1.2rem" }}>
            {indexDoc.errors.map((err, idx) => (
              <li key={idx} style={{ marginBottom: "0.15rem" }}>
                {err}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {/* Prepare Whole Dataset Modal */}
      {showPrepareModal ? (
        <div style={{ marginTop: "0.75rem", padding: "1rem", borderRadius: "8px", border: "1px solid #1f6feb", backgroundColor: "rgba(31, 111, 235, 0.12)" }}>
          <h4 style={{ margin: "0 0 0.35rem", color: "#f5f5f7", fontSize: "0.95rem" }}>
            Prepare whole dataset ({dataset.scene_count} scenes)
          </h4>
          <p style={{ margin: "0 0 0.5rem", fontSize: "0.78rem", color: "#8b949e" }}>
            Ingest all remaining raw scenes into the server results cache as schema-1 scenes. The job runs on CPU compute nodes.
          </p>

          {prepareError ? (
            <div style={{ color: "#ff7b72", fontSize: "0.8rem", marginBottom: "0.5rem" }}>{prepareError}</div>
          ) : null}

          {preparePreview ? (
            <>
              <div style={{ fontSize: "0.78rem", color: "#c9d1d9", marginBottom: "0.25rem" }}>
                Script preview (CPU only · No GPU requested · {preparePreview.cpus_per_task} CPU{preparePreview.cpus_per_task === 1 ? "" : "s"} · partition: {preparePreview.partition}):
              </div>
              <div style={{ fontSize: "0.75rem", color: "#8b949e", marginBottom: "0.5rem" }}>
                Cache root: <span style={{ fontFamily: "monospace", color: "#79c0ff" }}>{preparePreview.cache_root}</span>
              </div>
              <pre
                aria-label="Prepare job script"
                style={{
                  margin: "0 0 0.75rem",
                  padding: "0.75rem",
                  borderRadius: "6px",
                  backgroundColor: "rgba(0,0,0,0.6)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  fontSize: "0.72rem",
                  color: "#c9d1d9",
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                }}
              >
                {preparePreview.script}
              </pre>

              <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.82rem", color: "#f5f5f7", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={prepareConfirmed}
                  onChange={(e) => setPrepareConfirmed(e.target.checked)}
                />
                I have read the full job script above and confirm this preparation submission.
              </label>

              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
                <button
                  type="button"
                  style={{
                    ...primaryButton,
                    opacity: !prepareConfirmed || prepareBusy ? 0.5 : 1,
                    cursor: !prepareConfirmed || prepareBusy ? "not-allowed" : "pointer",
                  }}
                  disabled={!prepareConfirmed || prepareBusy}
                  onClick={() => void handleConfirmWholeDatasetSubmit()}
                >
                  {prepareBusy ? "Submitting…" : "Confirm and submit prepare job"}
                </button>
                <button
                  type="button"
                  style={secondaryButton}
                  onClick={() => setShowPrepareModal(false)}
                >
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <div style={{ fontSize: "0.8rem", color: "#8b949e" }}>Generating submission script preview…</div>
          )}
        </div>
      ) : null}

      {/* Single Scene Prepare Confirmation */}
      {singleScenePrepare && singlePreparePreview ? (
        <div style={{ marginTop: "0.75rem", padding: "0.75rem 1rem", borderRadius: "8px", border: "1px solid #1f6feb", backgroundColor: "rgba(31, 111, 235, 0.12)" }}>
          <h4 style={{ margin: "0 0 0.25rem", color: "#f5f5f7", fontSize: "0.9rem" }}>
            Prepare scene: {singleScenePrepare}
          </h4>
          <p style={{ margin: "0 0 0.35rem", fontSize: "0.76rem", color: "#8b949e" }}>
            This scene is not ready yet. Prepare it now? Once preparation finishes, the viewer will open automatically.
          </p>
          <div style={{ fontSize: "0.74rem", color: "#8b949e", marginBottom: "0.4rem" }}>
            Workers: {singlePreparePreview.cpus_per_task} CPU · Cache root: <span style={{ fontFamily: "monospace", color: "#79c0ff" }}>{singlePreparePreview.cache_root}</span>
          </div>
          <pre style={{ margin: "0 0 0.5rem", padding: "0.5rem", borderRadius: "6px", backgroundColor: "rgba(0,0,0,0.5)", fontSize: "0.7rem", color: "#c9d1d9", maxHeight: "100px", overflowY: "auto" }}>
            {singlePreparePreview.script}
          </pre>
          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.8rem", color: "#f5f5f7", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={singleConfirmed}
              onChange={(e) => setSingleConfirmed(e.target.checked)}
            />
            Confirm preparing scene {singleScenePrepare}
          </label>
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.6rem" }}>
            <button
              type="button"
              style={{ ...primaryButton, opacity: !singleConfirmed ? 0.5 : 1 }}
              disabled={!singleConfirmed}
              onClick={() => void handleConfirmSingleScenePrepare()}
            >
              Start preparation & view
            </button>
            <button
              type="button"
              style={secondaryButton}
              onClick={() => setSingleScenePrepare(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {/* Table Toolbar & Search Filter */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "1rem", marginBottom: "0.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <input
            type="search"
            placeholder="Filter scene ID…"
            value={filterQuery}
            onChange={(e) => {
              setFilterQuery(e.target.value);
              setPage(1);
            }}
            style={{
              padding: "0.35rem 0.65rem",
              borderRadius: "6px",
              border: "1px solid rgba(255, 255, 255, 0.2)",
              backgroundColor: "rgba(0,0,0,0.3)",
              color: "#f5f5f7",
              fontSize: "0.8rem",
              width: "200px",
            }}
          />
          <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>
            Showing {filteredScenes.length} of {allScenes.length} scenes
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {isPolling ? (
            <span style={{ fontSize: "0.72rem", color: "#79c0ff", display: "flex", alignItems: "center", gap: "0.3rem" }}>
              <span style={{ display: "inline-block", width: "6px", height: "6px", borderRadius: "50%", backgroundColor: "#79c0ff" }} />
              Polling statuses every 5s
            </span>
          ) : null}
          <button
            type="button"
            style={secondaryButton}
            onClick={() => void fetchStatus()}
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Scene Index Table */}
      {loadingIndex ? (
        <div style={{ padding: "2rem", textAlign: "center", color: "#8b949e", fontSize: "0.85rem" }}>
          Indexing scenes on cluster (metadata only)…
        </div>
      ) : indexError ? (
        <div role="alert" style={{ padding: "0.75rem", borderRadius: "8px", backgroundColor: "rgba(255,98,89,0.15)", border: "1px solid rgba(255,98,89,0.35)", color: "#ff6259", fontSize: "0.82rem" }}>
          {indexError}
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "8px" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem", textAlign: "left" }}>
              <thead>
                <tr style={{ backgroundColor: "rgba(255,255,255,0.04)", borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
                  <th style={{ padding: "0.55rem 0.75rem", color: "#8b949e", fontWeight: 500 }}>Scene ID</th>
                  <th style={{ padding: "0.55rem 0.75rem", color: "#8b949e", fontWeight: 500 }}>Duration</th>
                  <th style={{ padding: "0.55rem 0.75rem", color: "#8b949e", fontWeight: 500 }}>Agents</th>
                  <th style={{ padding: "0.55rem 0.75rem", color: "#8b949e", fontWeight: 500 }}>Sensors</th>
                  <th style={{ padding: "0.55rem 0.75rem", color: "#8b949e", fontWeight: 500 }}>Status</th>
                  <th style={{ padding: "0.55rem 0.75rem", color: "#8b949e", fontWeight: 500, textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {paginatedScenes.length === 0 ? (
                  <tr>
                    <td colSpan={6} style={{ padding: "1.5rem", textAlign: "center", color: "#8b949e" }}>
                      No scenes matched filter.
                    </td>
                  </tr>
                ) : (
                  paginatedScenes.map((scene: ClusterIndexScene) => {
                    const statusEntry = statuses.get(scene.scene_id);
                    const state: ClusterSceneState = statusEntry?.state ?? "not_prepared";
                    const isReady = state === "ready";
                    const isPreparing = state === "preparing";

                    return (
                      <tr
                        key={scene.scene_id}
                        style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}
                      >
                        <td style={{ padding: "0.55rem 0.75rem", fontFamily: "monospace", color: "#f5f5f7" }}>
                          {scene.scene_id}
                        </td>
                        <td style={{ padding: "0.55rem 0.75rem", color: "#c9d1d9" }}>
                          {formatDuration(scene.duration_s)}
                        </td>
                        <td style={{ padding: "0.55rem 0.75rem", color: "#c9d1d9" }}>
                          {scene.agent_count}
                        </td>
                        <td style={{ padding: "0.55rem 0.75rem", color: "#8b949e" }}>
                          {formatSensorsSummary(scene.sensors)}
                        </td>
                        <td style={{ padding: "0.55rem 0.75rem" }}>
                          <StatusChip
                            state={state}
                            reason={statusEntry?.reason}
                            progress={statusEntry?.progress_percent}
                          />
                        </td>
                        <td style={{ padding: "0.55rem 0.75rem", textAlign: "right" }}>
                          <button
                            type="button"
                            style={{
                              ...secondaryButton,
                              borderColor: isReady ? "#3fb950" : "rgba(255,255,255,0.18)",
                              color: isReady ? "#7ee787" : "#f5f5f7",
                            }}
                            disabled={isPreparing}
                            onClick={() => void handleOpenScene(scene.scene_id)}
                          >
                            {isReady ? "Open scene" : isPreparing ? "Preparing…" : "Prepare & open"}
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination for long datasets (> 50 scenes) */}
          {totalPages > 1 ? (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.75rem" }}>
              <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>
                Page {page} of {totalPages}
              </span>
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <button
                  type="button"
                  style={secondaryButton}
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  style={secondaryButton}
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  Next
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
