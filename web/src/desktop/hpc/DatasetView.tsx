// SPDX-License-Identifier: Apache-2.0
/**
 * Dense HPC Dataset View matching the Claude Design specification
 * (`docs/design/hpc-dataset-view/HPC Dataset View.dc.html`).
 *
 * Accepts the same props and backend data contract as `ClusterDatasetView.tsx`
 * so it can replace `ClusterDatasetView` directly once #110 merges, while also
 * supporting fixture injection for visual and E2E testing.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ServerEntry } from "../hpc";
import {
  canPrepareWholeDataset,
  indexingLabel,
  splitErrorText,
  datasetIndex,
  datasetOpen,
  datasetPrepare,
  datasetPrepareSubmit,
  datasetStatus,
  formatLicenceInfo,
  formatStorageEstimate,
  prepareEstimateLine,
  recoverFromRefusedPrepareSubmit,
  type ClusterDetectDocument,
  type ClusterIndexDocument,
  type ClusterSceneState,
  type ClusterSceneStatusEntry,
  type DatasetPreparePreview,
  type CameraImagesMode,
  cameraImageChoices,
  datasetPrepareJobStatus,
  listenTauri,
  shareDatasetScenesWithViewer,
} from "../clusterDatasets";
import { SettingRow } from "../SettingRow";
import { publishDatasetJobs, setViewerOpen } from "../jobsStore";
import { getPersistentState } from "../local";
import { isJobActive } from "../runPlan";
import { errorDetails, errorText } from "../errors";

export interface SubmittedJob {
  jobId: string;
  planId: string;
  jobName: string;
  scenes: string[] | "all";
  state: string;
  reason: string | null;
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}

function fmtDur(s: number | null | undefined): string {
  if (s == null || isNaN(s)) return "—";
  if (s >= 60) {
    return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  }
  return `${s.toFixed(1)} s`;
}

interface ParsedSceneTimestamp {
  datePart: string;
  timePart: string;
  dayKey: string;
  dayLabel: string;
  longDate: string;
}

function parseSceneTimestamp(sceneId: string): ParsedSceneTimestamp {
  const parts = sceneId.split("_");
  if (
    parts.length >= 6 &&
    /^\d{4}$/.test(parts[0]) &&
    /^\d{2}$/.test(parts[1]) &&
    /^\d{2}$/.test(parts[2])
  ) {
    const year = Number(parts[0]);
    const month = Number(parts[1]);
    const day = Number(parts[2]);
    const d = new Date(year, month - 1, day);
    const dow = DOW[d.getDay()] ?? "Day";
    const mon = MON[month - 1] ?? "Mon";
    return {
      datePart: `${parts[1]}-${parts[2]}`,
      timePart: `${parts[3]}:${parts[4]}:${parts[5]}`,
      dayKey: `${parts[0]}${parts[1]}${parts[2]}`,
      dayLabel: `${dow} ${day} ${mon} ${parts[0]}`,
      longDate: `${dow} ${day} ${mon} ${parts[0]}, ${parts[3]}:${parts[4]}:${parts[5]}`,
    };
  }
  return {
    datePart: "",
    timePart: sceneId,
    dayKey: "all",
    dayLabel: "Scenes",
    longDate: sceneId,
  };
}



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
      style={{
        margin: "0 0 0.6rem",
        padding: "0.5rem 0.65rem",
        border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
        borderRadius: "6px",
      }}
    >
      <legend style={{ padding: "0 0.3rem", fontSize: "0.78rem", color: "var(--text-secondary)" }}>
        Camera images
      </legend>
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

function ErrorLines({ text }: { text: string }) {
  const { line, details } = splitErrorText(text);
  return (
    <>
      <span>{line || "Something went wrong. Try again."}</span>
      {details ? (
        <details style={{ marginTop: "0.35rem" }}>
          <summary style={{ cursor: "pointer", fontSize: "0.76rem" }}>Details</summary>
          <pre
            style={{
              margin: "0.35rem 0 0",
              whiteSpace: "pre-wrap",
              fontSize: "0.72rem",
              color: "var(--text-secondary)",
            }}
          >
            {details}
          </pre>
        </details>
      ) : null}
    </>
  );
}

export interface DatasetViewProps {
  profile: ServerEntry;
  dataset: ClusterDetectDocument;
  onBack: () => void;
  onClose: () => void;
  initialIndexDoc?: ClusterIndexDocument | null;
  initialStatuses?: ClusterSceneStatusEntry[];
  initialSubmittedJobs?: SubmittedJob[];
  initialCacheRoot?: string;
  initialSelectedSceneId?: string;
}

type FilterMode = "all" | "ready" | "preparing" | "queued";

export default function DatasetView({
  profile,
  dataset,
  onBack,
  onClose,
  initialIndexDoc = null,
  initialStatuses,
  initialSubmittedJobs,
  initialCacheRoot = "",
  initialSelectedSceneId,
}: DatasetViewProps) {
  const [indexDoc, setIndexDoc] = useState<ClusterIndexDocument | null>(initialIndexDoc);
  const indexRef = useRef(indexDoc);
  indexRef.current = indexDoc;
  const [loadingIndex, setLoadingIndex] = useState(initialIndexDoc === null);
  const [indexError, setIndexError] = useState<string | null>(null);

  const [statuses, setStatuses] = useState<Map<string, ClusterSceneStatusEntry>>(() => {
    const map = new Map<string, ClusterSceneStatusEntry>();
    if (initialStatuses) {
      for (const s of initialStatuses) {
        map.set(s.scene_id, s);
      }
    }
    return map;
  });
  const [isPolling, setIsPolling] = useState(Boolean(initialSubmittedJobs && initialSubmittedJobs.length > 0));

  // Prepare Whole Dataset state
  const [showPrepareModal, setShowPrepareModal] = useState(false);
  const [preparePreview, setPreparePreview] = useState<DatasetPreparePreview | null>(null);
  const [prepareConfirmed, setPrepareConfirmed] = useState(false);
  const [prepareBusy, setPrepareBusy] = useState(false);
  const [prepareOutcome, setPrepareOutcome] = useState<string | null>(null);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [throttleDraft, setThrottleDraft] = useState<string | null>(null);
  const [wholeThrottlePending, setWholeThrottlePending] = useState(false);

  // Single scene prepare modal/state
  const [singleScenePrepare, setSingleScenePrepare] = useState<string | null>(null);
  const [singlePreparePreview, setSinglePreparePreview] = useState<DatasetPreparePreview | null>(null);
  const [singleConfirmed, setSingleConfirmed] = useState(false);
  const [singlePrepareError, setSinglePrepareError] = useState<string | null>(null);
  const [autoOpenSceneId, setAutoOpenSceneId] = useState<string | null>(null);

  // Filter & Selection
  const [filterQuery, setFilterQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<FilterMode>("all");
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(initialSelectedSceneId ?? null);
  const [wideWindow, setWideWindow] = useState<boolean>(() =>
    typeof window !== "undefined" ? window.innerWidth >= 1240 : true,
  );

  const [serverCacheRoot, setServerCacheRoot] = useState<string>(initialCacheRoot);
  const [submittedJobs, setSubmittedJobs] = useState<SubmittedJob[]>(initialSubmittedJobs ?? []);
  const submittedRef = useRef<SubmittedJob[]>(initialSubmittedJobs ?? []);
  submittedRef.current = submittedJobs;
  const datasetKey = `${profile.id}|${dataset.path}`;
  const datasetLabel = `${(dataset.adapter ?? "dataset").toUpperCase()}${dataset.split ? ` · ${dataset.split}` : ""}`;
  const openFirstReadyRef = useRef<ReadonlySet<string> | null>(null);
  const [wholeImagesPending, setWholeImagesPending] = useState<CameraImagesMode | null>(null);
  const [singleImagesPending, setSingleImagesPending] = useState<CameraImagesMode | null>(null);

  useEffect(() => {
    const onResize = () => {
      setWideWindow(window.innerWidth >= 1240);
    };
    window.addEventListener("resize", onResize);
    onResize();
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const trackJob = (
    job: { job_id: string; plan_id: string },
    jobName: string,
    scenes: string[] | "all",
  ) => {
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

  const [indexStartedAt, setIndexStartedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!loadingIndex) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [loadingIndex]);

  useEffect(() => {
    if (initialIndexDoc !== null) {
      setIndexDoc(initialIndexDoc);
      setLoadingIndex(false);
      return;
    }
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
  }, [profile.id, dataset, initialIndexDoc]);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = async () => {
    if (initialStatuses) return;
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

      if (autoOpenSceneId && nextMap.get(autoOpenSceneId)?.state === "ready") {
        const idToOpen = autoOpenSceneId;
        setAutoOpenSceneId(null);
        void openSceneInViewerRef.current(idToOpen);
      }

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

      const scope = new Set<string>();
      for (const job of submittedRef.current) {
        for (const id of job.scenes === "all"
          ? (indexRef.current?.scenes ?? []).map((s) => s.scene_id)
          : job.scenes) {
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
        queued: Array.from(scope).filter(
          (id) => (nextMap.get(id)?.state ?? "not_prepared") === "not_prepared",
        ).length,
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

      const hasPreparing = Array.from(nextMap.values()).some((s) => s.state === "preparing");
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
    if (pollTimerRef.current || initialStatuses) return;
    setIsPolling(true);
    void fetchStatus();
    pollTimerRef.current = setInterval(() => {
      void fetchStatus();
    }, 5000);
  };

  useEffect(() => {
    if (!initialStatuses) {
      void fetchStatus();
    }
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  const hasPreparingScenes = useMemo(() => {
    return Array.from(statuses.values()).some((s) => s.state === "preparing");
  }, [statuses]);

  useEffect(() => {
    if (hasPreparingScenes && !pollTimerRef.current && !initialStatuses) {
      startPolling();
    }
  }, [hasPreparingScenes]);

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

  useEffect(() => {
    if (!indexDoc || initialStatuses) return;
    void shareDatasetScenesWithViewer(
      indexDoc.scenes.map((scene) => ({
        scene_id: scene.scene_id,
        state: statuses.get(scene.scene_id)?.state ?? "not_prepared",
      })),
    ).catch(() => undefined);
  }, [indexDoc, statuses, initialStatuses]);

  const handleOpenScene = async (sceneId: string) => {
    const currentStatus = statuses.get(sceneId)?.state ?? "not_prepared";

    if (currentStatus === "ready") {
      await openSceneInViewer(sceneId);
      return;
    }

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
      setOpenError(errorText(err));
    }
  };

  const handleOpenSceneRef = useRef(handleOpenScene);
  handleOpenSceneRef.current = handleOpenScene;
  useEffect(() => {
    if (initialStatuses) return;
    let unlisten: (() => void) | undefined;
    void listenTauri<{ scene_id: string }>("prepare-scene", (payload) => {
      if (payload?.scene_id) void handleOpenSceneRef.current(payload.scene_id);
    }).then((stop) => {
      unlisten = stop;
    });
    return () => unlisten?.();
  }, [initialStatuses]);

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

  const handleWholeImagesChange = async (images: CameraImagesMode) => {
    if (!preparePreview || images === preparePreview.images) return;
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
          workers: preparePreview.throttle,
        }),
      );
    } catch (err) {
      setPrepareError(errorDetails(err) || errorText(err));
    } finally {
      setWholeImagesPending(null);
    }
  };

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

  // Compute decorated scenes and aggregates matching the Claude Design mock
  const allScenes = indexDoc?.scenes ?? [];
  const maxDur = useMemo(() => {
    let m = 1;
    for (const s of allScenes) {
      if (s.duration_s && s.duration_s > m) m = s.duration_s;
    }
    return m;
  }, [allScenes]);

  const decoratedScenes = useMemo(() => {
    return allScenes.map((scene) => {
      const statusEntry = statuses.get(scene.scene_id);
      const rawState: ClusterSceneState = statusEntry?.state ?? "not_prepared";
      const queuedJob =
        rawState === "not_prepared"
          ? (submittedJobs.find(
              (job) => job.scenes === "all" || job.scenes.includes(scene.scene_id),
            ) ?? null)
          : null;

      const effectiveFilterState: "ready" | "preparing" | "queued" =
        rawState === "ready"
          ? "ready"
          : rawState === "preparing"
            ? "preparing"
            : "queued";

      const progress = statusEntry?.progress_percent ?? 0;
      const jobId = statusEntry?.job_id || queuedJob?.jobId || null;

      let dot = "rgba(121, 192, 255, 0.35)";
      let statusColor = "var(--text-tertiary, #a1a1a6)";
      let statusLabel = queuedJob || jobId ? "Queued" : "Not prepared";
      let statusNote = jobId ? `job ${jobId}` : "on cluster";

      if (rawState === "ready") {
        dot = "#3fb950";
        statusColor = "var(--ok, #7ee787)";
        statusLabel = "Ready";
        statusNote = statusEntry?.images === "originals" ? "camera originals" : "camera previews";
      } else if (rawState === "preparing") {
        dot = "#388bfd";
        statusColor = "#79c0ff";
        statusLabel = progress > 0 ? `Preparing ${progress}%` : "Preparing…";
        statusNote = jobId ? `job ${jobId}` : "in progress";
      } else if (rawState === "failed") {
        dot = "#f85149";
        statusColor = "var(--danger, #ff7b72)";
        statusLabel = "Failed";
        statusNote = statusEntry?.reason || "check cluster logs";
      }

      const ts = parseSceneTimestamp(scene.scene_id);
      const dur = scene.duration_s ?? 0;
      const cam = scene.sensors?.camera_samples ?? 0;
      const lidar = scene.sensors?.lidar_samples ?? 0;
      const agents = scene.agent_count ?? 1;
      const framesPerAgent = scene.sample_count || (agents > 0 ? Math.round(lidar / agents) : 0);

      return {
        scene,
        id: scene.scene_id,
        rawState,
        effectiveFilterState,
        progress,
        jobId,
        dot,
        statusColor,
        statusLabel,
        statusNote,
        ...ts,
        dur,
        durLabel: fmtDur(scene.duration_s),
        durPct: `${Math.min(100, Math.max(4, (dur / maxDur) * 100))}%`,
        cam,
        lidar,
        camLabel: fmtNum(cam),
        lidarLabel: fmtNum(lidar),
        agents,
        framesPerAgent,
        agentDots: Array.from({ length: Math.min(agents, 8) }, (_, i) => i),
        isReady: rawState === "ready",
        isPrep: rawState === "preparing",
        isQueued: rawState !== "ready" && rawState !== "preparing",
        progPct: `${Math.min(100, Math.max(4, progress))}%`,
        sensorsLabel: `${agents} agent${agents === 1 ? "" : "s"} · ${scene.sensors?.sensors ?? agents * 5} sensors`,
        agentList: Array.from({ length: agents }, (_, i) => ({
          name: i === 0 ? "Ego" : `Agent ${i + 1}`,
        })),
        imagesMode: statusEntry?.images ?? "previews",
      };
    });
  }, [allScenes, statuses, submittedJobs, maxDur]);

  const counts = useMemo(() => {
    const nReady = decoratedScenes.filter((s) => s.effectiveFilterState === "ready").length;
    const nPrep = decoratedScenes.filter((s) => s.effectiveFilterState === "preparing").length;
    const nQueued = decoratedScenes.filter((s) => s.effectiveFilterState === "queued").length;
    const total = decoratedScenes.length;
    const totalDurSec = decoratedScenes.reduce((acc, s) => acc + s.dur, 0);
    const totalCam = decoratedScenes.reduce((acc, s) => acc + s.cam, 0);
    const totalLidar = decoratedScenes.reduce((acc, s) => acc + s.lidar, 0);
    return {
      nReady,
      nPrep,
      nQueued,
      total,
      remaining: nPrep + nQueued,
      totalDur: fmtDur(totalDurSec),
      totalCam: fmtNum(totalCam),
      totalLidar: fmtNum(totalLidar),
      readyPct: total > 0 ? `${(nReady / total) * 100}%` : "0%",
      prepPct: total > 0 ? `${(nPrep / total) * 100}%` : "0%",
      queuedPct: total > 0 ? `${(nQueued / total) * 100}%` : "0%",
    };
  }, [decoratedScenes]);

  const filteredScenes = useMemo(() => {
    const q = filterQuery.trim().toLowerCase().replace(/[-: ]/g, "_");
    return decoratedScenes.filter((s) => {
      if (statusFilter !== "all" && s.effectiveFilterState !== statusFilter) return false;
      if (q && !s.id.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [decoratedScenes, statusFilter, filterQuery]);

  const activeSelectedScene = useMemo(() => {
    if (selectedSceneId) {
      const found = decoratedScenes.find((s) => s.id === selectedSceneId);
      if (found) return found;
    }
    return decoratedScenes.find((s) => s.isReady) ?? decoratedScenes[0] ?? null;
  }, [decoratedScenes, selectedSceneId]);

  const groups = useMemo(() => {
    const result: Array<{
      key: string;
      label: string;
      showHeader: boolean;
      meta: string;
      rows: typeof decoratedScenes;
    }> = [];

    for (const s of filteredScenes) {
      let group = result.find((g) => g.key === s.dayKey);
      if (!group) {
        group = {
          key: s.dayKey,
          label: s.dayLabel,
          showHeader: s.dayKey !== "all",
          meta: "",
          rows: [],
        };
        result.push(group);
      }
      group.rows.push(s);
    }

    for (const group of result) {
      const readyInGroup = group.rows.filter((r) => r.isReady).length;
      const sumDur = group.rows.reduce((acc, r) => acc + r.dur, 0);
      group.meta = `${group.rows.length} scene${group.rows.length === 1 ? "" : "s"} · ${fmtDur(sumDur)}${
        readyInGroup > 0 ? ` · ${readyInGroup} ready` : ""
      }`;
    }

    return result;
  }, [filteredScenes]);

  const resolvedCacheRoot =
    serverCacheRoot ||
    (indexDoc
      ? `${profile.profile.results_root.replace(/\/+$/, "")}/${indexDoc.cache.relative_path}`
      : "Known once the scenes are indexed");

  const shortCacheSuffix = useMemo(() => {
    const parts = resolvedCacheRoot.split("/").filter(Boolean);
    if (parts.length >= 2) {
      return `…/${parts.slice(-2).join("/")}`;
    }
    return resolvedCacheRoot;
  }, [resolvedCacheRoot]);

  const licenceInfo = formatLicenceInfo(dataset.licence);
  const adapterLower = (indexDoc?.adapter || dataset.adapter || "dataset").toLowerCase();
  const adapterUpper = adapterLower.toUpperCase();
  const splitName = indexDoc?.split ?? dataset.split ?? "";
  const activeJobId =
    submittedJobs[0]?.jobId ||
    Array.from(statuses.values()).find((s) => s.job_id)?.job_id ||
    null;

  const showInspector = wideWindow && activeSelectedScene !== null;

  return (
    <section
      aria-label="Dataset view"
      style={{
        display: "grid",
        gridTemplateColumns: showInspector ? "minmax(0, 1fr) 300px" : "minmax(0, 1fr)",
        minHeight: "calc(100vh - 52px)",
        backgroundColor: "var(--bg)",
        color: "var(--text)",
        fontSize: "13px",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
        {/* Header & Dataset Summary */}
        <div
          style={{
            padding: "18px 24px 16px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
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
                <button
                  type="button"
                  onClick={onBack}
                  style={{
                    background: "none",
                    border: "none",
                    padding: 0,
                    color: "var(--text-subtle)",
                    fontSize: "11.5px",
                    cursor: "pointer",
                  }}
                >
                  {profile.profile.name}
                </button>
                {" / "}
                <span>{adapterLower}</span>
                {splitName ? ` / ${splitName}` : ""}
              </div>
              <h1
                style={{
                  margin: 0,
                  fontSize: "20px",
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color: "var(--text)",
                }}
              >
                {adapterUpper}{" "}
                {splitName ? (
                  <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}>{splitName}</span>
                ) : null}
              </h1>
            </div>

            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              <button
                type="button"
                onClick={onClose}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
                  backgroundColor: "transparent",
                  color: "var(--text)",
                  fontSize: "13px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Close
              </button>
              <button
                type="button"
                onClick={() => void handlePrepareWholeDatasetClick()}
                disabled={!canPrepareWholeDataset(indexDoc, loadingIndex)}
                style={{
                  padding: "6px 14px",
                  borderRadius: "6px",
                  border: "none",
                  backgroundColor: "var(--primary, #1f6feb)",
                  color: "#ffffff",
                  fontSize: "13px",
                  fontWeight: 500,
                  cursor: canPrepareWholeDataset(indexDoc, loadingIndex) ? "pointer" : "not-allowed",
                }}
              >
                {counts.remaining > 0 && counts.nReady > 0
                  ? `Prepare remaining ${counts.remaining}`
                  : "Prepare whole dataset"}
              </button>
            </div>
          </div>

          {/* 4-column Stats Row */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
              gap: "12px 24px",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: 0 }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Scenes</span>
              <span style={{ fontSize: "15px", fontVariantNumeric: "tabular-nums", color: "var(--text)" }}>
                {counts.total}{" "}
                <span style={{ color: "var(--text-subtle)", fontSize: "12px" }}>
                  · {counts.totalDur} total
                </span>
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: 0 }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Frames</span>
              <span style={{ fontSize: "15px", fontVariantNumeric: "tabular-nums", color: "var(--text)" }}>
                {counts.totalCam}{" "}
                <span style={{ color: "var(--text-subtle)", fontSize: "12px" }}>cam</span> ·{" "}
                {counts.totalLidar}{" "}
                <span style={{ color: "var(--text-subtle)", fontSize: "12px" }}>LiDAR</span>
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: 0 }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Cache size</span>
              <span style={{ fontSize: "15px", color: "var(--text)" }}>
                {indexDoc ? formatStorageEstimate(indexDoc) : "—"}{" "}
                <span style={{ color: "var(--text-subtle)", fontSize: "12px" }}>est.</span>
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: 0 }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Licence</span>
              <span style={{ fontSize: "12.5px", lineHeight: 1.35, color: "var(--text-secondary)" }}>
                {dataset.licence?.status === "unknown"
                  ? "Not published · research use on this cluster only"
                  : licenceInfo.statusText}
                {licenceInfo.links.length > 0 ? (
                  <>
                    {" "}
                    {licenceInfo.links.map((link, idx) => (
                      <a
                        key={link}
                        href={link}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          color: "var(--primary-hover, #79c0ff)",
                          textDecoration: "none",
                          marginLeft: idx > 0 ? "4px" : "2px",
                        }}
                      >
                        [{idx + 1}]
                      </a>
                    ))}
                  </>
                ) : null}
              </span>
            </div>
          </div>

          {/* Raw root & Cache paths */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "auto minmax(0, 1fr)",
              columnGap: "14px",
              rowGap: "4px",
              fontSize: "11.5px",
            }}
          >
            <span style={{ color: "var(--text-subtle)" }}>Raw root</span>
            <span
              style={{
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                color: "var(--text-secondary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {dataset.raw_root || dataset.path}
            </span>
            <span style={{ color: "var(--text-subtle)" }}>Cache</span>
            <span
              style={{
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                color: "var(--primary-hover, #79c0ff)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {resolvedCacheRoot}
            </span>
          </div>

          {/* Segmented Ready / Preparing / Queued Progress Bar */}
          <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
            <div
              style={{
                display: "flex",
                height: "6px",
                borderRadius: "3px",
                overflow: "hidden",
                backgroundColor: "var(--line, rgba(255,255,255,0.06))",
                gap: "1px",
              }}
            >
              <div
                style={{
                  width: counts.readyPct,
                  backgroundColor: "#3fb950",
                  transition: "width 0.4s",
                }}
              />
              <div
                style={{
                  width: counts.prepPct,
                  backgroundColor: "#388bfd",
                  transition: "width 0.4s",
                }}
              />
              <div
                style={{
                  width: counts.queuedPct,
                  backgroundColor: "rgba(121, 192, 255, 0.28)",
                  transition: "width 0.4s",
                }}
              />
            </div>

            <div
              style={{
                display: "flex",
                gap: "18px",
                flexWrap: "wrap",
                fontSize: "12px",
                color: "var(--text-tertiary)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  style={{
                    width: "8px",
                    height: "8px",
                    borderRadius: "2px",
                    backgroundColor: "#3fb950",
                  }}
                />
                <span style={{ color: "var(--text)" }}>{counts.nReady}</span> ready
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  style={{
                    width: "8px",
                    height: "8px",
                    borderRadius: "2px",
                    backgroundColor: "#388bfd",
                  }}
                />
                <span style={{ color: "var(--text)" }}>{counts.nPrep}</span> preparing
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  style={{
                    width: "8px",
                    height: "8px",
                    borderRadius: "2px",
                    backgroundColor: "rgba(121, 192, 255, 0.28)",
                  }}
                />
                <span style={{ color: "var(--text)" }}>{counts.nQueued}</span>{" "}
                {activeJobId ? `queued · job ${activeJobId}` : "queued"}
              </span>
            </div>
          </div>

          {/* Alerts / Modals when triggered */}
          {openError ? (
            <div role="alert" style={{ color: "var(--danger)", fontSize: "12px" }}>
              <ErrorLines text={openError} />
            </div>
          ) : null}
          {prepareOutcome ? (
            <div
              role="status"
              style={{
                padding: "8px 12px",
                borderRadius: "6px",
                backgroundColor: "rgba(63,185,80,0.14)",
                border: "1px solid rgba(63,185,80,0.35)",
                color: "var(--ok)",
                fontSize: "12px",
              }}
            >
              ✓ {prepareOutcome}
            </div>
          ) : null}
          {showPrepareModal ? (
            <div
              style={{
                padding: "14px",
                borderRadius: "8px",
                border: "1px solid #1f6feb",
                backgroundColor: "rgba(31, 111, 235, 0.1)",
              }}
            >
              <h2 style={{ margin: "0 0 4px", fontSize: "14px", color: "var(--text)" }}>
                Prepare whole dataset ({indexDoc?.scene_count ?? 0} scenes)
              </h2>
              <p style={{ margin: "0 0 8px", fontSize: "12px", color: "var(--text-tertiary)" }}>
                Ingest all remaining raw scenes into the server results cache as schema-1 scenes. The job runs on CPU compute nodes.
              </p>
              {prepareError ? (
                <div role="alert" style={{ color: "var(--danger)", fontSize: "12px", marginBottom: "8px" }}>
                  <ErrorLines text={prepareError} />
                </div>
              ) : null}
              {preparePreview ? (
                <>
                  <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "4px" }}>
                    Script preview (CPU only · No GPU requested · {preparePreview.array_tasks ?? preparePreview.cpus_per_task} task{(preparePreview.array_tasks ?? preparePreview.cpus_per_task) === 1 ? "" : "s"} of 1 CPU, one per scene · partition: {preparePreview.partition}):
                  </div>
                  <div role="note" style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px" }}>
                    {prepareEstimateLine(preparePreview)}
                  </div>
                  <label className="settings-field" style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px", display: "flex", alignItems: "center" }}>
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
                    <span style={{ color: "var(--text-tertiary)", marginLeft: "0.5rem" }}>
                      At most {preparePreview.throttle} task{preparePreview.throttle === 1 ? "" : "s"} at once, each reading 16 raw files at a time: the shared filesystem, not CPUs, is the limit.
                    </span>
                  </label>
                  <div style={{ fontSize: "11.5px", color: "var(--text-tertiary)", marginBottom: "8px" }}>
                    Cache root:{" "}
                    <span style={{ fontFamily: "'Geist Mono', monospace", color: "#79c0ff" }}>
                      {preparePreview.cache_root}
                    </span>
                  </div>
                  <CameraImagesChoice
                    name="whole-camera-images"
                    value={wholeImagesPending ?? preparePreview.images}
                    previewBytes={preparePreview.estimated_preview_bytes}
                    disabled={prepareBusy}
                    onChange={(mode) => void handleWholeImagesChange(mode)}
                  />
                  {wholeImagesPending || wholeThrottlePending ? (
                    <div role="status" style={{ fontSize: "12px", color: "#79c0ff", margin: "0 0 6px" }}>
                      Updating preview…
                    </div>
                  ) : null}
                  <pre
                    aria-label="Prepare job script"
                    aria-busy={wholeImagesPending !== null || wholeThrottlePending}
                    style={{
                      margin: "0 0 10px",
                      padding: "10px",
                      borderRadius: "6px",
                      backgroundColor: "var(--sunken)",
                      border: "1px solid var(--line)",
                      fontSize: "11.5px",
                      color: "var(--text-secondary)",
                      overflow: "auto",
                      whiteSpace: "pre-wrap",
                      maxHeight: "220px",
                    }}
                  >
                    {preparePreview.script}
                  </pre>
                  <label
                    style={{
                      display: "flex",
                      gap: "8px",
                      alignItems: "center",
                      fontSize: "12.5px",
                      color: "var(--text)",
                      cursor: "pointer",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={prepareConfirmed}
                      onChange={(e) => setPrepareConfirmed(e.target.checked)}
                    />
                    I have read the full job script above and confirm this preparation submission.
                  </label>
                  <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                    <button
                      type="button"
                      className="c-button btn-primary"
                      disabled={!prepareConfirmed || prepareBusy || wholeImagesPending !== null || wholeThrottlePending}
                      onClick={() => void handleConfirmWholeDatasetSubmit()}
                      style={{ padding: "5px 12px", borderRadius: "6px", fontSize: "12.5px" }}
                    >
                      {prepareBusy ? "Submitting…" : "Confirm and submit prepare job"}
                    </button>
                    <button
                      type="button"
                      className="c-button btn-secondary"
                      onClick={() => setShowPrepareModal(false)}
                      style={{ padding: "5px 12px", borderRadius: "6px", fontSize: "12.5px" }}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <div style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>
                  Generating submission script preview…
                </div>
              )}
            </div>
          ) : null}

          {singleScenePrepare && singlePreparePreview ? (
            <div
              style={{
                padding: "12px 14px",
                borderRadius: "8px",
                border: "1px solid #1f6feb",
                backgroundColor: "rgba(31, 111, 235, 0.1)",
              }}
            >
              <h2 style={{ margin: "0 0 4px", fontSize: "13.5px", color: "var(--text)" }}>
                Prepare scene: {singleScenePrepare}
              </h2>
              {singlePrepareError ? (
                <div role="alert" style={{ color: "var(--danger)", fontSize: "12px", marginBottom: "6px" }}>
                  <ErrorLines text={singlePrepareError} />
                </div>
              ) : null}
              <div role="note" style={{ fontSize: "11.5px", color: "var(--text-tertiary)", marginBottom: "6px" }}>
                {prepareEstimateLine(singlePreparePreview)} One 1-CPU task · Cache root:{" "}
                <span style={{ fontFamily: "'Geist Mono', monospace", color: "#79c0ff" }}>
                  {singlePreparePreview.cache_root}
                </span>
              </div>
              <CameraImagesChoice
                name="single-camera-images"
                value={singleImagesPending ?? singlePreparePreview.images}
                previewBytes={singlePreparePreview.estimated_preview_bytes}
                onChange={(mode) => void handleSingleImagesChange(mode)}
              />
              {singleImagesPending ? (
                <div role="status" style={{ fontSize: "12px", color: "#79c0ff", margin: "0 0 6px" }}>
                  Updating preview…
                </div>
              ) : null}
              <pre
                aria-label="Prepare scene script"
                aria-busy={singleImagesPending !== null}
                style={{
                  margin: "0 0 8px",
                  padding: "8px",
                  borderRadius: "6px",
                  backgroundColor: "var(--sunken)",
                  border: "1px solid var(--line)",
                  fontSize: "11px",
                  color: "var(--text-secondary)",
                  maxHeight: "180px",
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                }}
              >
                {singlePreparePreview.script}
              </pre>
              <label style={{ display: "flex", gap: "8px", alignItems: "center", fontSize: "12px" }}>
                <input
                  type="checkbox"
                  checked={singleConfirmed}
                  onChange={(e) => setSingleConfirmed(e.target.checked)}
                />
                Confirm preparing scene {singleScenePrepare}
              </label>
              <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
                <button
                  type="button"
                  className="c-button btn-primary"
                  disabled={!singleConfirmed || singleImagesPending !== null}
                  onClick={() => void handleConfirmSingleScenePrepare()}
                  style={{ padding: "5px 12px", borderRadius: "6px", fontSize: "12px" }}
                >
                  Start preparation & view
                </button>
                <button
                  type="button"
                  className="c-button btn-secondary"
                  onClick={() => {
                    setSingleScenePrepare(null);
                    setSinglePrepareError(null);
                  }}
                  style={{ padding: "5px 12px", borderRadius: "6px", fontSize: "12px" }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : null}
        </div>

        {/* Filter Bar */}
        <div
          style={{
            padding: "10px 24px",
            display: "flex",
            alignItems: "center",
            gap: "12px",
            flexWrap: "wrap",
            borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
          }}
        >
          <input
            type="search"
            aria-label="Filter scene ID"
            placeholder="Filter scene ID…"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            style={{
              width: "200px",
              padding: "5px 10px",
              borderRadius: "6px",
              border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
              backgroundColor: "var(--sunken)",
              color: "var(--text)",
              font: "inherit",
              fontSize: "12.5px",
              outline: "none",
            }}
          />

          <div
            role="group"
            aria-label="Filter scenes by status"
            style={{
              display: "flex",
              gap: "2px",
              padding: "2px",
              borderRadius: "7px",
              backgroundColor: "var(--line-subtle, rgba(255,255,255,0.05))",
            }}
          >
            {(
              [
                ["all", `All ${counts.total}`],
                ["ready", `Ready ${counts.nReady}`],
                ["preparing", `Preparing ${counts.nPrep}`],
                ["queued", `Queued ${counts.nQueued}`],
              ] as const
            ).map(([key, label]) => {
              const active = statusFilter === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setStatusFilter(key)}
                  style={{
                    padding: "3px 10px",
                    borderRadius: "5px",
                    border: "none",
                    fontSize: "12px",
                    fontWeight: active ? 500 : 400,
                    cursor: "pointer",
                    backgroundColor: active ? "var(--line-elevated, rgba(255,255,255,0.12))" : "transparent",
                    color: active ? "var(--text)" : "var(--text-tertiary)",
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <div
            style={{
              marginLeft: "auto",
              display: "flex",
              alignItems: "center",
              gap: "12px",
              fontSize: "12px",
              color: "var(--text-subtle)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {isPolling && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", color: "var(--text-tertiary)" }}>
                <span style={{ width: "6px", height: "6px", borderRadius: "50%", backgroundColor: "#79c0ff" }} />
                <span>Polling every 5 s · updated 0 s ago</span>
              </span>
            )}
            <span>
              {filteredScenes.length === counts.total
                ? `${counts.total} scenes`
                : `${filteredScenes.length} of ${counts.total} scenes`}
            </span>
          </div>
        </div>

        {/* Dense Grouped Scene Table */}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {loadingIndex ? (
            <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--text-subtle)" }}>
              {indexingLabel(now - indexStartedAt)}
            </div>
          ) : indexError ? (
            <div role="alert" style={{ padding: "16px 24px", color: "var(--danger)" }}>
              <ErrorLines text={indexError} />
            </div>
          ) : (
            <div style={{ minWidth: "760px" }}>
              {/* Sticky Column Headers */}
              <div
                style={{
                  position: "sticky",
                  top: 0,
                  zIndex: 2,
                  display: "grid",
                  gridTemplateColumns:
                    "minmax(150px,1.3fr) minmax(130px,1.2fr) 84px 84px 84px minmax(150px,1fr) 76px",
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
                <span>Scene · start time</span>
                <span>Duration</span>
                <span>Agents</span>
                <span style={{ textAlign: "right" }}>Cam frames</span>
                <span style={{ textAlign: "right" }}>LiDAR frames</span>
                <span>Status</span>
                <span />
              </div>

              {groups.length === 0 ? (
                <div style={{ padding: "40px 24px", color: "var(--text-subtle)", textAlign: "center" }}>
                  No scenes match this filter.
                </div>
              ) : (
                groups.map((group) => (
                  <div key={group.key}>
                    {group.showHeader && (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "baseline",
                          gap: "10px",
                          padding: "14px 24px 6px",
                          fontSize: "12px",
                        }}
                      >
                        <span style={{ color: "var(--text)", fontWeight: 500 }}>{group.label}</span>
                        <span style={{ color: "var(--text-subtle)", fontVariantNumeric: "tabular-nums" }}>
                          {group.meta}
                        </span>
                      </div>
                    )}
                    {group.rows.map((s) => {
                      const isSelected = activeSelectedScene?.id === s.id;
                      return (
                        <div
                          key={s.id}
                          onClick={() => setSelectedSceneId(s.id)}
                          className="c-table-row"
                          style={{
                            display: "grid",
                            gridTemplateColumns:
                              "minmax(150px,1.3fr) minmax(130px,1.2fr) 84px 84px 84px minmax(150px,1fr) 76px",
                            alignItems: "center",
                            gap: "12px",
                            padding: "0 24px",
                            height: "34px",
                            borderBottom: "1px solid var(--line-subtle, rgba(255,255,255,0.04))",
                            cursor: "default",
                            backgroundColor: isSelected ? "rgba(31, 111, 235, 0.16)" : "transparent",
                            boxShadow: isSelected ? "inset 2px 0 0 #388bfd" : "none",
                          }}
                        >
                          {/* Scene · start time */}
                          <span
                            style={{
                              fontFamily: "'Geist Mono', ui-monospace, monospace",
                              fontSize: "12.5px",
                              fontVariantNumeric: "tabular-nums",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {s.datePart ? (
                              <>
                                <span style={{ color: "var(--text-subtle)" }}>{s.datePart}</span>{" "}
                                <span style={{ color: "var(--text)" }}>{s.timePart}</span>
                              </>
                            ) : (
                              <span style={{ color: "var(--text)" }}>{s.timePart}</span>
                            )}
                          </span>

                          {/* Duration + bar */}
                          <span
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                              fontVariantNumeric: "tabular-nums",
                              color: "var(--text-secondary)",
                            }}
                          >
                            <span style={{ width: "40px", textAlign: "right" }}>{s.durLabel}</span>
                            <span
                              style={{
                                flex: 1,
                                maxWidth: "110px",
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
                                  width: s.durPct,
                                }}
                              />
                            </span>
                          </span>

                          {/* Agents + ticks */}
                          <span
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                              color: "var(--text-secondary)",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            <span style={{ width: "8px" }}>{s.agents}</span>
                            <span style={{ display: "flex", gap: "2px" }}>
                              {s.agentDots.map((d) => (
                                <span
                                  key={d}
                                  style={{
                                    width: "6px",
                                    height: "10px",
                                    borderRadius: "1px",
                                    backgroundColor: "#8e8e93",
                                  }}
                                />
                              ))}
                            </span>
                          </span>

                          {/* Cam frames */}
                          <span
                            style={{
                              textAlign: "right",
                              fontVariantNumeric: "tabular-nums",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {s.camLabel}
                          </span>

                          {/* LiDAR frames */}
                          <span
                            style={{
                              textAlign: "right",
                              fontVariantNumeric: "tabular-nums",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {s.lidarLabel}
                          </span>

                          {/* Status */}
                          <span
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                              fontSize: "12.5px",
                              whiteSpace: "nowrap",
                            }}
                          >
                            <span
                              style={{
                                width: "7px",
                                height: "7px",
                                borderRadius: "50%",
                                flexShrink: 0,
                                backgroundColor: s.dot,
                              }}
                            />
                            <span style={{ color: s.statusColor }}>{s.statusLabel}</span>
                            {s.isPrep && (
                              <span
                                style={{
                                  width: "56px",
                                  height: "3px",
                                  borderRadius: "2px",
                                  backgroundColor: "rgba(56, 139, 253, 0.2)",
                                }}
                              >
                                <span
                                  style={{
                                    display: "block",
                                    height: "100%",
                                    borderRadius: "2px",
                                    backgroundColor: "#388bfd",
                                    transition: "width 0.6s",
                                    width: s.progPct,
                                  }}
                                />
                              </span>
                            )}
                            {s.isReady && (
                              <span style={{ color: "var(--text-subtle)", fontSize: "11.5px" }}>
                                {s.imagesMode}
                              </span>
                            )}
                          </span>

                          {/* Open button */}
                          <span style={{ display: "flex", justifyContent: "flex-end" }}>
                            {s.isReady ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void handleOpenScene(s.id);
                                }}
                                style={{
                                  padding: "3px 12px",
                                  borderRadius: "5px",
                                  border: "1px solid rgba(63, 185, 80, 0.6)",
                                  backgroundColor: "transparent",
                                  color: "var(--ok, #7ee787)",
                                  fontSize: "12px",
                                  fontWeight: 500,
                                  cursor: "pointer",
                                }}
                              >
                                Open
                              </button>
                            ) : null}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <footer
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "16px",
            padding: "7px 24px",
            borderTop: "1px solid var(--line, rgba(255,255,255,0.08))",
            backgroundColor: "var(--sunken)",
            fontSize: "11.5px",
            color: "var(--text-subtle)",
          }}
        >
          <span>
            {viewerStart ? `Opening ${viewerStart}…` : ""}
          </span>
          <span style={{ color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
            {indexDoc?.scene_count ? `${indexDoc.scene_count} scenes` : ""}
          </span>
        </footer>
      </div>

      {/* Inspector Panel on Wide Windows (>= 1240 px) */}
      {showInspector && activeSelectedScene ? (
        <aside
          aria-label="Scene inspector"
          style={{
            borderLeft: "1px solid var(--line, rgba(255,255,255,0.08))",
            backgroundColor: "var(--sunken)",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflow: "auto",
          }}
        >
          <div
            style={{
              padding: "18px 20px 16px",
              display: "flex",
              flexDirection: "column",
              gap: "6px",
              borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
            }}
          >
            <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Selected scene</span>
            <span
              style={{
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: "14px",
                color: "var(--text)",
                wordBreak: "break-all",
              }}
            >
              {activeSelectedScene.id}
            </span>
            <span style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>
              {activeSelectedScene.longDate}
            </span>
          </div>

          <div
            style={{
              padding: "16px 20px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span
                style={{
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  backgroundColor: activeSelectedScene.dot,
                }}
              />
              <span style={{ fontWeight: 500, color: activeSelectedScene.statusColor }}>
                {activeSelectedScene.statusLabel}
              </span>
              <span style={{ marginLeft: "auto", fontSize: "11.5px", color: "var(--text-subtle)" }}>
                {activeSelectedScene.statusNote}
              </span>
            </div>

            {activeSelectedScene.isPrep && (
              <div
                style={{
                  height: "4px",
                  borderRadius: "2px",
                  backgroundColor: "rgba(56, 139, 253, 0.2)",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    borderRadius: "2px",
                    backgroundColor: "#388bfd",
                    transition: "width 0.6s",
                    width: activeSelectedScene.progPct,
                  }}
                />
              </div>
            )}

            {activeSelectedScene.isReady && (
              <button
                type="button"
                onClick={() => void handleOpenScene(activeSelectedScene.id)}
                style={{
                  textAlign: "center",
                  padding: "7px 0",
                  borderRadius: "6px",
                  border: "none",
                  backgroundColor: "#238636",
                  color: "#ffffff",
                  fontWeight: 500,
                  fontSize: "13px",
                  cursor: "pointer",
                  width: "100%",
                }}
              >
                Open scene
              </button>
            )}

            {activeSelectedScene.isQueued && (
              <span style={{ fontSize: "12px", lineHeight: 1.45, color: "var(--text-tertiary)" }}>
                {activeSelectedScene.jobId
                  ? `Waiting for prepare job ${activeSelectedScene.jobId} to reach this scene. It opens automatically once ready.`
                  : "Not prepared yet. Prepare this scene or the whole dataset to view it."}
              </span>
            )}
          </div>

          {/* 2x2 Metrics Grid */}
          <div
            style={{
              padding: "16px 20px",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "14px 12px",
              borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Duration</span>
              <span style={{ fontSize: "15px", fontVariantNumeric: "tabular-nums" }}>
                {activeSelectedScene.durLabel}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Frames / agent</span>
              <span style={{ fontSize: "15px", fontVariantNumeric: "tabular-nums" }}>
                {activeSelectedScene.framesPerAgent}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Cam frames</span>
              <span style={{ fontSize: "15px", fontVariantNumeric: "tabular-nums" }}>
                {activeSelectedScene.camLabel}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>LiDAR frames</span>
              <span style={{ fontSize: "15px", fontVariantNumeric: "tabular-nums" }}>
                {activeSelectedScene.lidarLabel}
              </span>
            </div>
          </div>

          {/* Per-agent Sensor Breakdown */}
          <div
            style={{
              padding: "16px 20px",
              display: "flex",
              flexDirection: "column",
              gap: "8px",
              borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
            }}
          >
            <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>
              {activeSelectedScene.sensorsLabel}
            </span>
            {activeSelectedScene.agentList.map((a) => (
              <div
                key={a.name}
                style={{
                  display: "grid",
                  gridTemplateColumns: "62px 1fr auto",
                  alignItems: "center",
                  gap: "8px",
                  fontSize: "12px",
                }}
              >
                <span style={{ color: "var(--text-secondary)" }}>{a.name}</span>
                <span style={{ display: "flex", gap: "3px", alignItems: "center" }}>
                  <span
                    style={{
                      width: "14px",
                      height: "10px",
                      borderRadius: "2px",
                      backgroundColor: "var(--line-elevated, rgba(255,255,255,0.18))",
                    }}
                  />
                  <span
                    style={{
                      width: "14px",
                      height: "10px",
                      borderRadius: "2px",
                      backgroundColor: "var(--line-elevated, rgba(255,255,255,0.18))",
                    }}
                  />
                  <span
                    style={{
                      width: "14px",
                      height: "10px",
                      borderRadius: "2px",
                      backgroundColor: "var(--line-elevated, rgba(255,255,255,0.18))",
                    }}
                  />
                  <span
                    style={{
                      width: "14px",
                      height: "10px",
                      borderRadius: "2px",
                      backgroundColor: "var(--line-elevated, rgba(255,255,255,0.18))",
                    }}
                  />
                  <span
                    style={{
                      width: "10px",
                      height: "10px",
                      borderRadius: "50%",
                      backgroundColor: "#8e8e93",
                      marginLeft: "3px",
                    }}
                  />
                </span>
                <span style={{ color: "var(--text-subtle)", fontSize: "11px" }}>
                  4 cam · 1 LiDAR
                </span>
              </div>
            ))}
          </div>

          {/* Cache Location */}
          <div
            style={{
              padding: "16px 20px",
              display: "flex",
              flexDirection: "column",
              gap: "4px",
            }}
          >
            <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>Cache location</span>
            <span
              style={{
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: "11px",
                lineHeight: 1.5,
                color: "var(--primary-hover, #79c0ff)",
                wordBreak: "break-all",
              }}
            >
              {shortCacheSuffix}/{activeSelectedScene.id}
            </span>
          </div>
        </aside>
      ) : null}
    </section>
  );
}
