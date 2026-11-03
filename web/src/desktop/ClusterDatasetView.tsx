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
  canPrepareWholeDataset,
  indexingLabel,
  splitErrorText,
  formatIndexHeader,
  datasetIndex,
  datasetOpen,
  datasetPrepare,
  datasetPrepareSubmit,
  datasetStatus,
  formatDuration,
  prepareEstimateLine,
  formatLicenceInfo,
  formatSensorsSummary,
  formatStorageEstimate,
  recoverFromRefusedPrepareSubmit,
  type ClusterDetectDocument,
  type ClusterIndexDocument,
  type ClusterIndexScene,
  type ClusterSceneState,
  type ClusterSceneStatusEntry,
  type DatasetPreparePreview,
  type CameraImagesMode,
  cameraImageChoices,
  datasetPrepareJobStatus,
  wholeDatasetOffer,
  listenTauri,
  shareDatasetScenesWithViewer,
} from "./clusterDatasets";
import { SettingRow } from "./SettingRow";
import { publishDatasetJobs, setViewerOpen } from "./jobsStore";
import { getPersistentState } from "./local";
import { isJobActive } from "./runPlan";
import { errorDetails, errorText } from "./errors";

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

/** Camera images for this prepare: read the dataset's originals, or also save previews. */
function CameraImagesChoice({
  name,
  value,
  previewBytes,
  disabled,
  onChange,
}: {
  name: string;
  value: CameraImagesMode;
  previewBytes: number;
  disabled?: boolean;
  onChange: (mode: CameraImagesMode) => void;
}) {
  return (
    <fieldset
      aria-label="Camera images"
      style={{ margin: "0 0 0.6rem", padding: "0.5rem 0.65rem", border: "1px solid rgba(255,255,255,0.12)", borderRadius: "6px" }}
    >
      <legend style={{ padding: "0 0.3rem", fontSize: "0.78rem", color: "#c9d1d9" }}>Camera images</legend>
      {cameraImageChoices(previewBytes).map((choice) => (
        <SettingRow
          key={choice.mode}
          type="radio"
          name={name}
          value={choice.mode}
          checked={value === choice.mode}
          disabled={disabled}
          title={choice.label}
          description={choice.detail}
          onChange={() => onChange(choice.mode)}
        />
      ))}
    </fieldset>
  );
}

/** A submitted prepare job that has not reached a scene yet. */
interface SubmittedJob {
  jobId: string;
  planId: string;
  jobName: string;
  scenes: string[] | "all";
  state: string;
  reason: string | null;
}

function StatusChip({
  state,
  reason,
  progress,
  queued,
}: {
  state: ClusterSceneState;
  reason?: string | null;
  progress?: number | null;
  queued?: SubmittedJob | null;
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
      if (queued) {
        // Submitted, waiting for the job to reach this scene.
        bg = "rgba(56, 139, 253, 0.12)";
        color = "#79c0ff";
        label = `Queued (job ${queued.jobId})`;
      }
      break;
  }

  return (
    <span
      role="status"
      title={
        reason
          ? `Failure reason: ${reason}`
          : queued
            ? `Prepare job ${queued.jobId}: ${queued.state}${queued.reason ? ` (${queued.reason})` : ""}`
            : undefined
      }
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

/** A backend error: its first line, the rest behind a "Details" toggle. */
function ErrorLines({ text }: { text: string }) {
  const { line, details } = splitErrorText(text);
  return (
    <>
      <span>{line || "Something went wrong. Try again."}</span>
      {details ? (
        <details style={{ marginTop: "0.35rem" }}>
          <summary style={{ cursor: "pointer", fontSize: "0.76rem" }}>Details</summary>
          <pre style={{ margin: "0.35rem 0 0", whiteSpace: "pre-wrap", fontSize: "0.72rem", color: "#c9d1d9" }}>
            {details}
          </pre>
        </details>
      ) : null}
    </>
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
  const indexRef = useRef(indexDoc);
  indexRef.current = indexDoc;
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
  const [singlePrepareError, setSinglePrepareError] = useState<string | null>(null);
  const [autoOpenSceneId, setAutoOpenSceneId] = useState<string | null>(null);

  // Search & Pagination for 200+ scenes
  const [filterQuery, setFilterQuery] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 50;

  const [serverCacheRoot, setServerCacheRoot] = useState<string>("");
  // Prepare jobs submitted from here, followed until they end, so a scene reads
  // Queued (job N) -> Preparing -> Ready and polling never stops while one waits.
  const [submittedJobs, setSubmittedJobs] = useState<SubmittedJob[]>([]);
  const submittedRef = useRef<SubmittedJob[]>([]);
  submittedRef.current = submittedJobs;
  const datasetKey = `${profile.id}|${dataset.path}`;
  const datasetLabel = `${(dataset.adapter ?? "dataset").toUpperCase()}${dataset.split ? ` · ${dataset.split}` : ""}`;
  // After "Prepare whole dataset": the scenes ready before it, so the first one
  // it prepares opens the viewer (Settings, on by default).
  const openFirstReadyRef = useRef<ReadonlySet<string> | null>(null);
  // The camera-image choice shows at once; Submit waits for the new script's hash.
  const [wholeImagesPending, setWholeImagesPending] = useState<CameraImagesMode | null>(null);
  // "Scenes at once" (the array's % throttle) being typed, and a re-preview for it.
  const [throttleDraft, setThrottleDraft] = useState<string | null>(null);
  const [wholeThrottlePending, setWholeThrottlePending] = useState(false);
  const [singleImagesPending, setSingleImagesPending] = useState<CameraImagesMode | null>(null);
  const trackJob = (job: { job_id: string; plan_id: string }, jobName: string, scenes: string[] | "all") => {
    const entry: SubmittedJob = {
      jobId: job.job_id,
      planId: job.plan_id,
      jobName,
      scenes,
      state: "pending",
      reason: null,
    };
    submittedRef.current = [...submittedRef.current, entry];
    setSubmittedJobs(submittedRef.current);
  };

  // Elapsed time while indexing, so a slow cluster does not look stuck.
  const [indexStartedAt, setIndexStartedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!loadingIndex) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [loadingIndex]);

  // Load index document on mount
  useEffect(() => {
    let active = true;
    setIndexStartedAt(Date.now());
    setNow(Date.now());
    setLoadingIndex(true);
    setIndexError(null);

    const adapter = dataset.adapter || "opv2v";
    const rawRoot = dataset.raw_root || dataset.path;

    datasetIndex(profile.id, adapter, rawRoot, dataset.split)
      .then((res) => {
        if (!active) return;
        setIndexDoc(res);
        setLoadingIndex(false);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setIndexError(errorDetails(err) || errorText(err));
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
        void openSceneInViewerRef.current(idToOpen);
      }

      // Follow submitted jobs until they end; one that ended without preparing
      // its scenes is reported instead of leaving them "Queued".
      let activeJobs = 0;
      if (submittedRef.current.length > 0) {
        const followed = await Promise.all(
          submittedRef.current.map(async (job) => {
            try {
              const watched = await datasetPrepareJobStatus(profile.id, {
                plan_id: job.planId,
                job_id: job.jobId,
                job_name: job.jobName,
              });
              return { ...job, state: String(watched.state), reason: watched.queue_reason ?? null };
            } catch {
              return job;
            }
          }),
        );
        const ended = followed.filter((job) => !isJobActive(job.state));
        const failed = ended.filter((job) => job.state !== "completed");
        if (failed.length > 0) {
          setPrepareOutcome(
            `Preparation job ${failed.map((job) => `${job.jobId} ended ${job.state}`).join(", ")}. Check the job log on the cluster, then prepare again.`,
          );
        }
        submittedRef.current = followed.filter((job) => isJobActive(job.state));
        setSubmittedJobs(submittedRef.current);
        activeJobs = submittedRef.current.length;
      }

      // The first scene the whole-dataset job made ready opens the viewer.
      const before = openFirstReadyRef.current;
      if (before) {
        const firstReady = (indexRef.current?.scenes ?? [])
          .map((scene) => scene.scene_id)
          .find((id) => nextMap.get(id)?.state === "ready" && !before.has(id));
        if (firstReady) {
          openFirstReadyRef.current = null;
          void openSceneInViewerRef.current(firstReady);
        }
      }

      // The always-visible jobs indicator.
      const scope = new Set<string>();
      for (const job of submittedRef.current) {
        for (const id of job.scenes === "all" ? (indexRef.current?.scenes ?? []).map((s) => s.scene_id) : job.scenes) {
          scope.add(id);
        }
      }
      const states = Array.from(nextMap.values());
      const finished = publishDatasetJobs({
        key: datasetKey,
        profileId: profile.id,
        label: datasetLabel,
        jobIds: submittedRef.current.map((job) => job.jobId),
        ready: states.filter((s) => s.state === "ready").length,
        preparing: states.filter((s) => s.state === "preparing").length,
        queued: Array.from(scope).filter((id) => (nextMap.get(id)?.state ?? "not_prepared") === "not_prepared").length,
        total: indexRef.current?.scene_count ?? states.length,
        viewerOpen: false,
        dataset,
      });
      if (finished) {
        window.dispatchEvent(
          new CustomEvent("coperceive-dataset-finished", {
            detail: { label: datasetLabel, ready: states.filter((s) => s.state === "ready").length },
          }),
        );
      }

      // Check if anything is still preparing
      const hasPreparing = Array.from(nextMap.values()).some(
        (s) => s.state === "preparing",
      );
      if (!hasPreparing && activeJobs === 0 && pollTimerRef.current) {
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

  // Opening a scene: the viewer reads it over SSH (no cluster job), so this is brief.
  const [viewerStart, setViewerStart] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  const openSceneInViewer = async (sceneId: string) => {
    setOpenError(null);
    setViewerStart(sceneId);
    try {
      await datasetOpen(profile.id, sceneId, {
        adapter: dataset.adapter || "opv2v",
        rawRoot: dataset.raw_root || dataset.path,
        split: dataset.split,
      });
      setViewerOpen(true, datasetKey);
    } catch (err) {
      setOpenError(errorDetails(err) || errorText(err));
    } finally {
      setViewerStart(null);
    }
  };

  const openSceneInViewerRef = useRef(openSceneInViewer);
  openSceneInViewerRef.current = openSceneInViewer;

  // The viewer lists every scene of this dataset; keep it told of their states.
  useEffect(() => {
    if (!indexDoc) return;
    void shareDatasetScenesWithViewer(
      indexDoc.scenes.map((scene) => ({
        scene_id: scene.scene_id,
        state: statuses.get(scene.scene_id)?.state ?? "not_prepared",
      })),
    ).catch(() => undefined);
  }, [indexDoc, statuses]);

  // Open a scene flow
  const handleOpenScene = async (sceneId: string) => {
    const currentStatus = statuses.get(sceneId)?.state ?? "not_prepared";

    if (currentStatus === "ready") {
      await openSceneInViewer(sceneId);
      return;
    }

    // Scene not ready: open single-scene prepare flow
    setSingleScenePrepare(sceneId);
    setSingleConfirmed(false);
    setSinglePrepareError(null);
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
      alert(errorText(err));
    }
  };

  // "Prepare & open" chosen in the viewer: show this scene's prepare preview here.
  const handleOpenSceneRef = useRef(handleOpenScene);
  handleOpenSceneRef.current = handleOpenScene;
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listenTauri<{ scene_id: string }>("prepare-scene", (payload) => {
      if (payload?.scene_id) void handleOpenSceneRef.current(payload.scene_id);
    }).then((stop) => {
      unlisten = stop;
    });
    return () => unlisten?.();
  }, []);

  const handleConfirmSingleScenePrepare = async () => {
    if (!singleScenePrepare || !singlePreparePreview) return;
    try {
      const submitted = await datasetPrepareSubmit(profile.id, singlePreparePreview.preview_hash, true);
      trackJob(submitted, singlePreparePreview.job_name, [singleScenePrepare]);
      setAutoOpenSceneId(singleScenePrepare);
      setSingleScenePrepare(null);
      setSinglePreparePreview(null);
      setSinglePrepareError(null);
      startPolling();
    } catch (err) {
      const recovery = await recoverFromRefusedPrepareSubmit(
        profile.id,
        {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
          scenes: [singleScenePrepare],
          images: singlePreparePreview.images,
        },
        err,
      );
      setSinglePrepareError(recovery.error);
      setSingleConfirmed(recovery.confirmed);
      if (recovery.newPreview) {
        if (recovery.newPreview.cache_root) {
          setServerCacheRoot(recovery.newPreview.cache_root);
        }
        setSinglePreparePreview(recovery.newPreview);
      }
    }
  };

  // Choosing the other camera-image mode renders a new script (and hash): the
  // confirmation starts over.
  const handleSingleImagesChange = async (images: CameraImagesMode) => {
    if (!singleScenePrepare) return;
    setSingleConfirmed(false);
    setSinglePrepareError(null);
    setSingleImagesPending(images);
    try {
      setSinglePreparePreview(
        await datasetPrepare(profile.id, {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
          scenes: [singleScenePrepare],
          images,
        }),
      );
    } catch (err) {
      setSinglePrepareError(errorDetails(err) || errorText(err));
    } finally {
      setSingleImagesPending(null);
    }
  };

  const handleWholeImagesChange = async (images: CameraImagesMode) => {
    setPrepareConfirmed(false);
    setPrepareError(null);
    setWholeImagesPending(images);
    try {
      setPreparePreview(
        await datasetPrepare(profile.id, {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
          scenes: "all",
          images,
          workers: preparePreview?.throttle,
        }),
      );
    } catch (err) {
      setPrepareError(errorDetails(err) || errorText(err));
    } finally {
      setWholeImagesPending(null);
    }
  };

  const handleWholeThrottleCommit = async () => {
    const value = Number(throttleDraft);
    setThrottleDraft(null);
    if (!preparePreview || !Number.isInteger(value) || value < 1 || value === preparePreview.throttle) return;
    setPrepareConfirmed(false);
    setPrepareError(null);
    setWholeThrottlePending(true);
    try {
      setPreparePreview(
        await datasetPrepare(profile.id, {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
          scenes: "all",
          images: preparePreview.images,
          workers: value,
        }),
      );
    } catch (err) {
      setPrepareError(errorDetails(err) || errorText(err));
    } finally {
      setWholeThrottlePending(false);
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
      setPrepareError(errorDetails(err) || errorText(err));
    }
  };

  const handleConfirmWholeDatasetSubmit = async () => {
    if (!preparePreview || !prepareConfirmed) return;
    setPrepareBusy(true);
    setPrepareError(null);

    try {
      const res = await datasetPrepareSubmit(profile.id, preparePreview.preview_hash, true);
      trackJob(res, preparePreview.job_name, "all");
      const settings = await getPersistentState().catch(() => null);
      if (settings?.open_first_ready_scene !== false) {
        openFirstReadyRef.current = new Set(
          Array.from(statuses.entries())
            .filter(([, entry]) => entry.state === "ready")
            .map(([id]) => id),
        );
      }
      setPrepareOutcome(`Submitted preparation job ${res.job_id}. Scenes show Queued until it reaches them.`);
      setShowPrepareModal(false);
      startPolling();
    } catch (err) {
      const recovery = await recoverFromRefusedPrepareSubmit(
        profile.id,
        {
          adapter: dataset.adapter || "opv2v",
          rawRoot: dataset.raw_root || dataset.path,
          split: dataset.split,
          scenes: "all",
          images: preparePreview.images,
          workers: preparePreview.throttle,
        },
        err,
      );
      setPrepareError(recovery.error);
      setPrepareConfirmed(recovery.confirmed);
      if (recovery.newPreview) {
        if (recovery.newPreview.cache_root) {
          setServerCacheRoot(recovery.newPreview.cache_root);
        }
        setPreparePreview(recovery.newPreview);
      }
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
            {formatIndexHeader(indexDoc, dataset.adapter)}
          </h3>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <button
            type="button"
            style={primaryButton}
            onClick={() => void handlePrepareWholeDatasetClick()}
            disabled={!canPrepareWholeDataset(indexDoc, loadingIndex)}
            title={
              canPrepareWholeDataset(indexDoc, loadingIndex)
                ? undefined
                : "Available once at least one scene is indexed."
            }
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
          <span style={{ fontFamily: "monospace", color: "#79c0ff" }}>
            {serverCacheRoot ||
              (indexDoc
                ? `${profile.profile.results_root.replace(/\/+$/, "")}/${indexDoc.cache.relative_path}`
                : "Known once the scenes are indexed")}
          </span>
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

      {(() => {
        const readyCount = Array.from(statuses.values()).filter((entry) => entry.state === "ready").length;
        const offer = submittedJobs.length > 0 ? null : wholeDatasetOffer(indexDoc, readyCount);
        if (!offer) return null;
        if (offer.kind === "done") {
          return (
            <p role="status" style={{ margin: "0.6rem 0 0", fontSize: "0.82rem", color: "#7ee787" }}>
              ✓ {offer.detail}
            </p>
          );
        }
        return (
          <div
            aria-label="Prepare the whole dataset"
            style={{ marginTop: "0.75rem", padding: "0.75rem 0.9rem", borderRadius: "8px", border: "1px solid #1f6feb", backgroundColor: "rgba(31, 111, 235, 0.10)" }}
          >
            <button
              type="button"
              style={primaryButton}
              onClick={() => void handlePrepareWholeDatasetClick()}
              disabled={!canPrepareWholeDataset(indexDoc, loadingIndex) || showPrepareModal}
            >
              {offer.button}
            </button>
            <p style={{ margin: "0.4rem 0 0", fontSize: "0.76rem", color: "#c9d1d9" }}>{offer.detail}</p>
          </div>
        );
      })()}
      {viewerStart ? (
        <div role="status" aria-live="polite" style={{ marginTop: "0.5rem", fontSize: "0.82rem", color: "#79c0ff" }}>
          Opening {viewerStart}…
        </div>
      ) : null}
      {openError ? (
        <div role="alert" style={{ marginTop: "0.5rem", color: "#ff7b72", fontSize: "0.8rem" }}>
          <ErrorLines text={openError} />
        </div>
      ) : null}
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
            Prepare whole dataset ({indexDoc?.scene_count ?? 0} scenes)
          </h4>
          <p style={{ margin: "0 0 0.5rem", fontSize: "0.78rem", color: "#8b949e" }}>
            Ingest all remaining raw scenes into the server results cache as schema-1 scenes. The job runs on CPU compute nodes.
          </p>

          {prepareError ? (
            <div role="alert" style={{ color: "#ff7b72", fontSize: "0.8rem", marginBottom: "0.5rem" }}>
              <ErrorLines text={prepareError} />
            </div>
          ) : null}

          {preparePreview ? (
            <>
              <div style={{ fontSize: "0.78rem", color: "#c9d1d9", marginBottom: "0.25rem" }}>
                Script preview (CPU only · No GPU requested · {preparePreview.array_tasks} task{preparePreview.array_tasks === 1 ? "" : "s"} of 1 CPU, one per scene · partition: {preparePreview.partition}):
              </div>
              <div role="note" style={{ fontSize: "0.76rem", color: "#c9d1d9", marginBottom: "0.4rem" }}>
                {prepareEstimateLine(preparePreview)}
              </div>
              <label className="settings-field" style={{ fontSize: "0.78rem", color: "#c9d1d9", marginBottom: "0.4rem" }}>
                <span>Scenes at once</span>
                <input
                  type="number"
                  min={1}
                  max={16}
                  aria-label="Scenes at once"
                  value={throttleDraft ?? String(preparePreview.throttle)}
                  disabled={prepareBusy || wholeThrottlePending}
                  onChange={(event) => setThrottleDraft(event.target.value)}
                  onBlur={() => void handleWholeThrottleCommit()}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void handleWholeThrottleCommit();
                  }}
                  style={{ width: "4.5em", marginLeft: "0.5rem" }}
                />
                <span style={{ color: "#8b949e", marginLeft: "0.5rem" }}>
                  At most {preparePreview.throttle} task{preparePreview.throttle === 1 ? "" : "s"} at once, each reading 16 raw files at a time: the shared filesystem, not CPUs, is the limit.
                </span>
              </label>
              <div style={{ fontSize: "0.75rem", color: "#8b949e", marginBottom: "0.5rem" }}>
                Cache root: <span style={{ fontFamily: "monospace", color: "#79c0ff" }}>{preparePreview.cache_root}</span>
              </div>
              <CameraImagesChoice
                name="whole-camera-images"
                value={wholeImagesPending ?? preparePreview.images}
                previewBytes={preparePreview.estimated_preview_bytes}
                disabled={prepareBusy}
                onChange={(mode) => void handleWholeImagesChange(mode)}
              />
              {wholeImagesPending || wholeThrottlePending ? (
                <div role="status" style={{ fontSize: "0.75rem", color: "#79c0ff", margin: "0 0 0.3rem" }}>
                  Updating preview…
                </div>
              ) : null}
              <pre
                aria-label="Prepare job script"
                aria-busy={wholeImagesPending !== null || wholeThrottlePending}
                style={{
                  opacity: wholeImagesPending || wholeThrottlePending ? 0.45 : 1,
                  margin: "0 0 0.75rem",
                  padding: "0.75rem",
                  borderRadius: "6px",
                  backgroundColor: "rgba(0,0,0,0.6)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  fontSize: "0.72rem",
                  color: "#c9d1d9",
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  minHeight: "16em",
                  maxHeight: "60vh",
                  resize: "vertical",
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
                  disabled={!prepareConfirmed || prepareBusy || wholeImagesPending !== null || wholeThrottlePending || throttleDraft !== null}
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
          {singlePrepareError ? (
            <div role="alert" style={{ color: "#ff7b72", fontSize: "0.8rem", marginBottom: "0.4rem" }}>
              <ErrorLines text={singlePrepareError} />
            </div>
          ) : null}
          <div style={{ fontSize: "0.74rem", color: "#8b949e", marginBottom: "0.4rem" }}>
            {prepareEstimateLine(singlePreparePreview)} One 1-CPU task · Cache root: <span style={{ fontFamily: "monospace", color: "#79c0ff" }}>{singlePreparePreview.cache_root}</span>
          </div>
          <CameraImagesChoice
            name="single-camera-images"
            value={singleImagesPending ?? singlePreparePreview.images}
            previewBytes={singlePreparePreview.estimated_preview_bytes}
            onChange={(mode) => void handleSingleImagesChange(mode)}
          />
          {singleImagesPending ? (
            <div role="status" style={{ fontSize: "0.75rem", color: "#79c0ff", margin: "0 0 0.3rem" }}>
              Updating preview…
            </div>
          ) : null}
          <pre
            aria-label="Prepare scene script"
            aria-busy={singleImagesPending !== null}
            style={{ margin: "0 0 0.5rem", padding: "0.5rem", borderRadius: "6px", backgroundColor: "rgba(0,0,0,0.5)", fontSize: "0.7rem", color: "#c9d1d9", minHeight: "16em", maxHeight: "60vh", overflow: "auto", whiteSpace: "pre-wrap", resize: "vertical" }}
          >
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
              disabled={!singleConfirmed || singleImagesPending !== null}
              onClick={() => void handleConfirmSingleScenePrepare()}
            >
              Start preparation & view
            </button>
            <button
              type="button"
              style={secondaryButton}
              onClick={() => {
                setSingleScenePrepare(null);
                setSinglePrepareError(null);
              }}
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
          {indexingLabel(now - indexStartedAt)}
        </div>
      ) : indexError ? (
        <div role="alert" style={{ padding: "0.75rem", borderRadius: "8px", backgroundColor: "rgba(255,98,89,0.15)", border: "1px solid rgba(255,98,89,0.35)", color: "#ff6259", fontSize: "0.82rem" }}>
          <ErrorLines text={indexError} />
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
                            queued={
                              state === "not_prepared"
                                ? (submittedJobs.find(
                                    (job) => job.scenes === "all" || job.scenes.includes(scene.scene_id),
                                  ) ?? null)
                                : null
                            }
                          />
                          {isReady && statusEntry?.images ? (
                            <span
                              className="camera-mode"
                              title={
                                statusEntry.images === "previews"
                                  ? "Camera previews are saved in your results folder"
                                  : "Camera images are read from the dataset"
                              }
                              style={{ display: "block", marginTop: "0.2rem", fontSize: "0.7rem", color: "#8b949e" }}
                            >
                              Camera: {statusEntry.images === "previews" ? "previews" : "originals"}
                            </span>
                          ) : null}
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
