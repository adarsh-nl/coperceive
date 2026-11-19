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
import type React from "react";
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
  type ClusterIndexScene,
  type ClusterSceneStatusEntry,
  type DatasetPreparePreview,
  type CameraImagesMode,
  cameraImageChoices,
  datasetPrepareJobStatus,
  listenTauri,
  shareDatasetScenesWithViewer,
} from "../clusterDatasets";
import { SettingRow } from "../SettingRow";
import { datasetJobs, publishDatasetJobs, setViewerOpen, subscribeDatasetJobs } from "../jobsStore";
import { getPersistentState } from "../local";
import { isJobActive } from "../runPlan";
import { getModifierKey } from "../platform";
import { errorDetails, errorText } from "../errors";
import {
  STATE_LABEL,
  groupScenes,
  recordingTime,
  type DatasetGrouping,
  type RecordingTime,
  type SceneRowState,
} from "./grouping";
import { useDatasetGrouping } from "./groupingSetting";

export interface SubmittedJob {
  jobId: string;
  planId: string;
  jobName: string;
  scenes: string[] | "all";
  state: string;
  reason: string | null;
}

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

const STATE_ORDER: readonly SceneRowState[] = ["ready", "preparing", "queued", "stale", "failed", "not_prepared"];

interface SceneRow {
  scene: ClusterIndexScene;
  scene_id: string;
  id: string;
  state: SceneRowState;
  /** Why a scene is failed or stale; the row shows the first line, the inspector all of it. */
  reason: string | null;
  progress: number | null;
  jobId: string | null;
  time: RecordingTime | null;
  dur: number | null;
  frames: number;
  rateHz: number | null;
  agents: number;
  sensors: number;
  cam: number;
  lidar: number;
  images: CameraImagesMode | null;
  sceneDir: string | null;
}

/**
 * A row's state from the scene's status, whether a prepare job this install
 * submitted still covers it, and a failure this install saw that job end with.
 */
export function rowStateOf(
  entry: Pick<ClusterSceneStatusEntry, "state" | "reason"> | undefined,
  coveredByActiveJob: boolean,
  jobFailure: string | null,
): { state: SceneRowState; reason: string | null } {
  const state = entry?.state ?? "not_prepared";
  if (state === "ready" || state === "preparing") return { state, reason: null };
  if (coveredByActiveJob) return { state: "queued", reason: null };
  if (state === "failed") return { state, reason: entry?.reason || "The prepare step failed; see the job log on the cluster." };
  if (state === "stale") return { state, reason: entry?.reason || "raw dataset fingerprint changed" };
  if (jobFailure) return { state: "failed", reason: jobFailure };
  return { state: "not_prepared", reason: null };
}

function firstLine(text: string): string {
  return text.split("\n")[0] ?? text;
}

function stateExplanation(row: SceneRow, server: string): string {
  switch (row.state) {
    case "ready":
      return `${row.images === "previews" ? "Prepared with camera previews." : row.images === "originals" ? "Prepared; camera images are read from the originals." : "Prepared."} Opens in the viewer over SSH from ${server}; no job runs.`;
    case "preparing":
      return row.jobId ? `Being prepared by job ${row.jobId}.` : "Being prepared.";
    case "queued":
      return row.jobId ? `Waiting for prepare job ${row.jobId} to reach this scene.` : "Waiting for its prepare job to reach this scene.";
    case "stale":
      return `The raw files changed since this scene was prepared (${row.reason ?? "fingerprint changed"}). Re-prepare it to view the data as it is now.`;
    case "failed":
      return row.reason ?? "The prepare step failed.";
    default:
      return "Not prepared yet. Prepare this scene, or the whole dataset, to view it.";
  }
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

export default function DatasetView({
  profile,
  dataset,
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
  const [statusFilter, setStatusFilter] = useState<"all" | SceneRowState>("all");
  const [grouping, setGrouping] = useDatasetGrouping();
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(initialSelectedSceneId ?? null);
  const [wideWindow, setWideWindow] = useState<boolean>(() =>
    typeof window !== "undefined" ? window.innerWidth >= 1240 : true,
  );

  const [serverCacheRoot, setServerCacheRoot] = useState<string>(initialCacheRoot);
  // Scenes a watched prepare job ended without preparing, with the reason.
  const [jobFailures, setJobFailures] = useState<Map<string, string>>(() => new Map());
  // When this install last read the scenes' status; nothing is shown before the first read.
  const [lastStatusAt, setLastStatusAt] = useState<number | null>(null);
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
    setJobFailures((current) => {
      if (current.size === 0) return current;
      const next = new Map(current);
      for (const id of scenes === "all" ? Array.from(current.keys()) : scenes) next.delete(id);
      return next;
    });
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
      setLastStatusAt(Date.now());

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
        if (ended.length > 0) {
          // A job that ended without preparing a scene leaves it Failed, with the job's end
          // state as the reason. Only jobs this install submitted and watched count.
          setJobFailures((current) => {
            const next = new Map(current);
            for (const job of ended) {
              const ids = job.scenes === "all" ? (indexRef.current?.scenes ?? []).map((s) => s.scene_id) : job.scenes;
              for (const id of ids) {
                if (nextMap.get(id)?.state === "ready") next.delete(id);
                else if (job.state !== "completed") next.set(id, `Prepare job ${job.jobId} ended ${job.state}.`);
              }
            }
            return next;
          });
        }
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
        // Keep what the viewer reported; a status poll knows nothing about it.
        viewerOpen: datasetJobs().find((entry) => entry.key === datasetKey)?.viewerOpen ?? false,
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
      if (indexDoc && !initialStatuses) {
        await shareDatasetScenesWithViewer(
          indexDoc.scenes.map((scene) => ({
            scene_id: scene.scene_id,
            state: statuses.get(scene.scene_id)?.state ?? "not_prepared",
          })),
          {
            label: [(indexDoc.adapter || dataset.adapter || "dataset").toUpperCase(), indexDoc.split ?? dataset.split ?? ""]
              .filter(Boolean)
              .join(" "),
            server: profile.profile.name,
          },
        ).catch(() => undefined);
      }
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
      {
        label: [(indexDoc.adapter || dataset.adapter || "dataset").toUpperCase(), indexDoc.split ?? dataset.split ?? ""]
          .filter(Boolean)
          .join(" "),
        server: profile.profile.name,
      },
    ).catch(() => undefined);
  }, [indexDoc, statuses, initialStatuses, dataset.adapter, dataset.split, profile.profile.name]);

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

  // -- the rows: only what the index, the status and this install's own jobs say --
  const allScenes = useMemo(() => indexDoc?.scenes ?? [], [indexDoc]);
  const maxDur = useMemo(
    () => allScenes.reduce((max, scene) => Math.max(max, scene.duration_s ?? 0), 1),
    [allScenes],
  );

  const rows = useMemo<SceneRow[]>(
    () =>
      allScenes.map((scene) => {
        const entry = statuses.get(scene.scene_id);
        const job =
          submittedJobs.find((candidate) => candidate.scenes === "all" || candidate.scenes.includes(scene.scene_id)) ??
          null;
        const { state, reason } = rowStateOf(entry, job !== null, jobFailures.get(scene.scene_id) ?? null);
        const frames = scene.sample_count || 0;
        const dur = scene.duration_s;
        return {
          scene,
          scene_id: scene.scene_id,
          id: scene.scene_id,
          state,
          reason,
          progress: state === "preparing" ? (entry?.progress_percent ?? null) : null,
          jobId: entry?.job_id || job?.jobId || null,
          time: recordingTime(scene.scene_id),
          dur,
          frames,
          rateHz: dur && frames > 1 ? frames / dur : null,
          agents: scene.agent_count || 0,
          sensors: scene.sensors?.sensors || 0,
          cam: scene.sensors?.camera_samples ?? 0,
          lidar: scene.sensors?.lidar_samples ?? 0,
          images: entry?.images ?? null,
          sceneDir: entry?.scene_dir ?? null,
        };
      }),
    [allScenes, statuses, submittedJobs, jobFailures],
  );

  const counts = useMemo(() => {
    const by: Record<SceneRowState, number> = {
      ready: 0,
      preparing: 0,
      queued: 0,
      stale: 0,
      failed: 0,
      not_prepared: 0,
    };
    for (const row of rows) by[row.state] += 1;
    const durations = rows.map((row) => row.dur).filter((d): d is number => d !== null);
    return {
      by,
      total: rows.length,
      totalDur: durations.length ? durations.reduce((a, b) => a + b, 0) : null,
      cam: rows.reduce((a, row) => a + row.cam, 0),
      lidar: rows.reduce((a, row) => a + row.lidar, 0),
      // What "Prepare remaining" would prepare: not started, failed or stale.
      remaining: by.not_prepared + by.failed + by.stale,
    };
  }, [rows]);

  const visibleRows = useMemo(() => {
    const q = filterQuery.trim().toLowerCase().replace(/[-: ]/g, "_");
    return rows.filter((row) => {
      if (statusFilter !== "all" && row.state !== statusFilter) return false;
      if (q && !row.id.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [rows, statusFilter, filterQuery]);

  const groups = useMemo(() => groupScenes(visibleRows, grouping, (row) => row.state), [visibleRows, grouping]);
  const ordered = useMemo(() => groups.flatMap((group) => group.rows), [groups]);

  const selected = useMemo(() => {
    if (selectedSceneId) {
      const found = rows.find((row) => row.id === selectedSceneId);
      if (found) return found;
    }
    return ordered.find((row) => row.state === "ready") ?? ordered[0] ?? null;
  }, [rows, ordered, selectedSceneId]);

  const resolvedCacheRoot =
    serverCacheRoot ||
    (indexDoc ? `${profile.profile.results_root.replace(/\/+$/, "")}/${indexDoc.cache.relative_path}` : "");

  const licenceInfo = formatLicenceInfo(dataset.licence);
  const adapterUpper = (indexDoc?.adapter || dataset.adapter || "dataset").toUpperCase();
  const splitName = indexDoc?.split ?? dataset.split ?? "";
  const showInspector = wideWindow && selected !== null;

  // The action a row offers, by state; none while its prepare is queued or running.
  const rowAction = (row: SceneRow): { label: string; tone: "ok" | "danger" | "warn" | "plain" } | null => {
    if (row.state === "ready") return { label: "Open", tone: "ok" };
    if (row.state === "failed") return { label: "Retry", tone: "danger" };
    if (row.state === "stale") return { label: "Re-prepare", tone: "warn" };
    if (row.state === "not_prepared") return { label: "Prepare", tone: "plain" };
    return null;
  };
  const runRowAction = (row: SceneRow) => {
    if (rowAction(row)) void handleOpenScene(row.id);
  };

  // Keyboard: ↑↓ select, ↵ open or prepare, "/" filter (⌘\ is the shell's sidebar toggle).
  const sectionRef = useRef<HTMLElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const onGridKeyDown = (event: React.KeyboardEvent) => {
    if (ordered.length === 0) return;
    const index = selected ? ordered.findIndex((row) => row.id === selected.id) : -1;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = ordered[Math.max(0, Math.min(ordered.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
      setSelectedSceneId(next.id);
      sectionRef.current?.querySelector(`[data-scene-id="${CSS.escape(next.id)}"]`)?.scrollIntoView?.({ block: "nearest" });
    } else if (event.key === "Enter" && selected) {
      event.preventDefault();
      runRowAction(selected);
    }
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      // Only the dataset view that is on screen takes the key.
      if (!sectionRef.current || sectionRef.current.offsetParent === null) return;
      event.preventDefault();
      filterRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // "Dataset info ▾"
  const [infoOpen, setInfoOpen] = useState(false);
  const infoRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!infoOpen) return;
    const onDown = (event: MouseEvent) => {
      if (infoRef.current && !infoRef.current.contains(event.target as Node)) setInfoOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setInfoOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [infoOpen]);

  // The viewer's real state, as the viewer reports it to the jobs store.
  const [viewerOpenNow, setViewerOpenNow] = useState(
    () => datasetJobs().find((entry) => entry.key === datasetKey)?.viewerOpen ?? false,
  );
  useEffect(
    () =>
      subscribeDatasetJobs(() =>
        setViewerOpenNow(datasetJobs().find((entry) => entry.key === datasetKey)?.viewerOpen ?? false),
      ),
    [datasetKey],
  );

  // "updated N s ago" counts from this install's last status read.
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    if (!isPolling) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isPolling]);

  const metaParts = [
    `${counts.total} scene${counts.total === 1 ? "" : "s"}`,
    counts.totalDur !== null ? fmtDur(counts.totalDur) : null,
    counts.cam || counts.lidar ? `${fmtNum(counts.cam)} cam + ${fmtNum(counts.lidar)} LiDAR frames` : null,
    // A size estimate of nothing says nothing: only for a split with scenes.
    indexDoc && counts.total > 0 ? formatStorageEstimate(indexDoc) : null,
  ].filter(Boolean);

  const prepareLabel = counts.by.ready + counts.by.stale > 0 ? "Prepare remaining" : "Prepare whole dataset";
  const segments: Array<[SceneRowState, string]> = [
    ["ready", "var(--dv-ready)"],
    ["preparing", "var(--dv-preparing)"],
    ["queued", "var(--dv-queued)"],
    ["stale", "var(--dv-stale)"],
    ["failed", "var(--dv-failed)"],
  ];

  return (
    <section
      ref={sectionRef}
      aria-label="Dataset view"
      className={`dv${showInspector ? " has-inspector" : ""}`}
    >
      <div className="dv-main">
        <header className="dv-header">
          <h1 className="dv-title">
            {adapterUpper}
            {splitName ? <span className="dv-split"> {splitName}</span> : null}
          </h1>
          <span className="dv-meta">{metaParts.join(" · ")}</span>
          <span className="dv-header-actions">
            <button type="button" className="c-secondary dv-small" onClick={onClose}>
              Close
            </button>
            <div className="dv-info" ref={infoRef}>
              <button
                type="button"
                className="c-secondary dv-small"
                aria-expanded={infoOpen}
                aria-controls="dv-info-panel"
                onClick={() => setInfoOpen((open) => !open)}
              >
                Dataset info <span aria-hidden="true">▾</span>
              </button>
              {infoOpen ? (
                <div id="dv-info-panel" role="dialog" aria-label="Dataset info" className="dv-info-panel">
                  <dl>
                    <dt>Server</dt>
                    <dd>{profile.profile.name}</dd>
                    <dt>Raw root</dt>
                    <dd className="c-mono">{dataset.raw_root || dataset.path}</dd>
                    <dt>Cache</dt>
                    <dd className="c-mono">{resolvedCacheRoot || "Known once the scenes are indexed"}</dd>
                    <dt>Licence</dt>
                    <dd>
                      {dataset.licence?.status === "unknown"
                        ? "Not published · research use on this cluster only"
                        : licenceInfo.statusText}
                      {licenceInfo.links.map((link, idx) => (
                        <a key={link} href={link} target="_blank" rel="noreferrer" className="dv-link">
                          {" "}[{idx + 1}]
                        </a>
                      ))}
                    </dd>
                  </dl>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              className="c-primary-wide is-blue dv-prepare"
              onClick={() => void handlePrepareWholeDatasetClick()}
              disabled={!canPrepareWholeDataset(indexDoc, loadingIndex)}
            >
              {prepareLabel}
              {counts.remaining > 0 ? <span className="dv-badge">{counts.remaining}</span> : null}
            </button>
          </span>
        </header>

        {indexDoc ? (
          <div className="dv-progress" role="img" aria-label={`${counts.by.ready} of ${counts.total} scenes ready`}>
            {segments.map(([state, color]) =>
              counts.by[state] > 0 ? (
                <span key={state} style={{ width: `${(counts.by[state] / Math.max(1, counts.total)) * 100}%`, background: color }} />
              ) : null,
            )}
          </div>
        ) : null}

        {openError || prepareOutcome || showPrepareModal || (singleScenePrepare && singlePreparePreview) ? (
          <div className="dv-panels">
            {openError ? (
              <div role="alert" style={{ color: "var(--danger)", fontSize: "12px" }}>
                <ErrorLines text={openError} />
              </div>
            ) : null}
            {prepareOutcome ? (
              <div role="status" className="dv-outcome">
                ✓ {prepareOutcome}
              </div>
            ) : null}
            {showPrepareModal ? (
              <div className="dv-prepare-panel">
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
                      Script preview (CPU only · No GPU requested · {preparePreview.array_tasks ?? preparePreview.cpus_per_task} task
                      {(preparePreview.array_tasks ?? preparePreview.cpus_per_task) === 1 ? "" : "s"} of 1 CPU, one per scene · partition:{" "}
                      {preparePreview.partition}):
                    </div>
                    <div role="note" style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px" }}>
                      {prepareEstimateLine(preparePreview)}
                    </div>
                    <label
                      className="settings-field"
                      style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px", display: "flex", alignItems: "center" }}
                    >
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
                        At most {preparePreview.throttle} task{preparePreview.throttle === 1 ? "" : "s"} at once, each reading 16 raw files at a
                        time: the shared filesystem, not CPUs, is the limit.
                      </span>
                    </label>
                    <div style={{ fontSize: "11.5px", color: "var(--text-tertiary)", marginBottom: "8px" }}>
                      Cache root: <span className="c-mono dv-path">{preparePreview.cache_root}</span>
                    </div>
                    <CameraImagesChoice
                      name="whole-camera-images"
                      value={wholeImagesPending ?? preparePreview.images}
                      previewBytes={preparePreview.estimated_preview_bytes}
                      disabled={prepareBusy}
                      onChange={(mode) => void handleWholeImagesChange(mode)}
                    />
                    {wholeImagesPending || wholeThrottlePending ? (
                      <div role="status" style={{ fontSize: "12px", color: "var(--dv-preparing)", margin: "0 0 6px" }}>
                        Updating preview…
                      </div>
                    ) : null}
                    <pre
                      aria-label="Prepare job script"
                      aria-busy={wholeImagesPending !== null || wholeThrottlePending}
                      className="dv-script"
                    >
                      {preparePreview.script}
                    </pre>
                    <label className="dv-confirm">
                      <input type="checkbox" checked={prepareConfirmed} onChange={(e) => setPrepareConfirmed(e.target.checked)} />
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
                  <div style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>Generating submission script preview…</div>
                )}
              </div>
            ) : null}

            {singleScenePrepare && singlePreparePreview ? (
              <div className="dv-prepare-panel">
                <h2 style={{ margin: "0 0 4px", fontSize: "13.5px", color: "var(--text)" }}>Prepare scene: {singleScenePrepare}</h2>
                {singlePrepareError ? (
                  <div role="alert" style={{ color: "var(--danger)", fontSize: "12px", marginBottom: "6px" }}>
                    <ErrorLines text={singlePrepareError} />
                  </div>
                ) : null}
                <div role="note" style={{ fontSize: "11.5px", color: "var(--text-tertiary)", marginBottom: "6px" }}>
                  {prepareEstimateLine(singlePreparePreview)} One 1-CPU task · Cache root:{" "}
                  <span className="c-mono dv-path">{singlePreparePreview.cache_root}</span>
                </div>
                <CameraImagesChoice
                  name="single-camera-images"
                  value={singleImagesPending ?? singlePreparePreview.images}
                  previewBytes={singlePreparePreview.estimated_preview_bytes}
                  onChange={(mode) => void handleSingleImagesChange(mode)}
                />
                {singleImagesPending ? (
                  <div role="status" style={{ fontSize: "12px", color: "var(--dv-preparing)", margin: "0 0 6px" }}>
                    Updating preview…
                  </div>
                ) : null}
                <pre aria-label="Prepare scene script" aria-busy={singleImagesPending !== null} className="dv-script is-small">
                  {singlePreparePreview.script}
                </pre>
                <label className="dv-confirm">
                  <input type="checkbox" checked={singleConfirmed} onChange={(e) => setSingleConfirmed(e.target.checked)} />
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
        ) : null}

        {/* Filters: every real state, the scene filter and the grouping */}
        <div className="dv-toolbar">
          <div role="group" aria-label="Filter scenes by status" className="dv-chips">
            {(["all", ...STATE_ORDER] as const).map((key) => {
              const count = key === "all" ? counts.total : counts.by[key];
              const active = statusFilter === key;
              return (
                <button
                  key={key}
                  type="button"
                  className={`dv-chip${active ? " is-active" : ""}`}
                  aria-pressed={active}
                  onClick={() => setStatusFilter(key)}
                >
                  {key !== "all" ? <span className={`dv-dot is-${key}`} aria-hidden="true" /> : null}
                  {key === "all" ? "All" : STATE_LABEL[key]} <span className="dv-chip-count">{count}</span>
                </button>
              );
            })}
          </div>
          <span className="dv-toolbar-end">
            <select
              aria-label="Group scenes"
              className="c-select dv-group"
              value={grouping}
              onChange={(event) => setGrouping(event.target.value as DatasetGrouping)}
            >
              <option value="none">No grouping</option>
              <option value="state">Group by state</option>
              <option value="day">Group by recording day</option>
            </select>
            <input
              ref={filterRef}
              type="search"
              aria-label="Filter scene ID"
              placeholder="Filter scene ID…   /"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              className="dv-filter"
            />
          </span>
        </div>

        {/* The scene table */}
        <div className="dv-table-scroll">
          {loadingIndex ? (
            <div className="dv-empty">{indexingLabel(now - indexStartedAt)}</div>
          ) : indexError ? (
            <div role="alert" className="dv-empty is-error">
              <ErrorLines text={indexError} />
            </div>
          ) : (
            <div
              role="grid"
              aria-label="Scenes"
              aria-rowcount={ordered.length + 1}
              tabIndex={0}
              onKeyDown={onGridKeyDown}
              className="dv-grid"
            >
              <div role="row" className="dv-row dv-head">
                <span role="columnheader">Scene · start time</span>
                <span role="columnheader">Duration</span>
                <span role="columnheader">Agents</span>
                <span role="columnheader" className="is-right">
                  Frames
                </span>
                <span role="columnheader">Status</span>
                <span role="columnheader" aria-label="Action" />
              </div>
              {ordered.length === 0 ? (
                <div className="dv-empty">
                  {rows.length === 0 ? "No scenes in this split." : "No scenes match this filter."}
                </div>
              ) : (
                groups.map((group) => (
                  <div key={group.key} role="rowgroup" aria-label={group.label || "Scenes"}>
                    {group.label ? (
                      <div role="row" className="dv-group-head">
                        <span role="gridcell">
                          <strong>{group.label}</strong>
                          <span className="dv-group-meta">
                            {group.rows.length} scene{group.rows.length === 1 ? "" : "s"}
                            {grouping !== "state"
                              ? ` · ${group.rows.filter((row) => row.state === "ready").length}/${group.rows.length} ready`
                              : ""}
                          </span>
                        </span>
                      </div>
                    ) : null}
                    {group.rows.map((row) => {
                      const isSelected = selected?.id === row.id;
                      const action = rowAction(row);
                      return (
                        <div
                          key={row.id}
                          role="row"
                          aria-selected={isSelected}
                          data-scene-id={row.id}
                          data-state={row.state}
                          className={`dv-row${isSelected ? " is-selected" : ""}`}
                          onClick={() => setSelectedSceneId(row.id)}
                          onDoubleClick={() => runRowAction(row)}
                        >
                          <span role="gridcell" className="c-mono dv-name">
                            {row.time ? (
                              <>
                                <span className="dv-subtle">{row.time.short.slice(0, 5)}</span> {row.time.short.slice(6)}
                              </>
                            ) : (
                              row.id
                            )}
                          </span>
                          <span role="gridcell" className="dv-dur">
                            <span className="dv-num">{fmtDur(row.dur)}</span>
                            {row.dur !== null ? (
                              <span className="dv-bar">
                                <span style={{ width: `${Math.max(4, (row.dur / maxDur) * 100)}%` }} />
                              </span>
                            ) : null}
                          </span>
                          <span role="gridcell" className="dv-agents">
                            <span className="dv-num">{row.agents}</span>
                            <span className="dv-blocks" aria-hidden="true">
                              {Array.from({ length: Math.min(row.agents, 8) }, (_, i) => (
                                <span key={i} />
                              ))}
                            </span>
                          </span>
                          <span role="gridcell" className="dv-num is-right">
                            {fmtNum(row.frames)}
                          </span>
                          <span role="gridcell" className="dv-status">
                            <span className={`dv-dot is-${row.state}`} aria-hidden="true" />
                            <span className={`dv-state is-${row.state}`}>
                              {row.state === "preparing" && row.progress !== null && row.progress > 0
                                ? `Preparing ${row.progress}%`
                                : STATE_LABEL[row.state]}
                            </span>
                            {row.state === "preparing" && row.progress !== null && row.progress > 0 ? (
                              <span className="dv-bar is-progress">
                                <span style={{ width: `${Math.min(100, row.progress)}%` }} />
                              </span>
                            ) : null}
                            {row.state === "queued" && row.jobId ? <span className="dv-reason">job {row.jobId}</span> : null}
                            {(row.state === "failed" || row.state === "stale") && row.reason ? (
                              <span className="dv-reason" title={row.reason}>
                                {firstLine(row.reason)}
                              </span>
                            ) : null}
                          </span>
                          <span role="gridcell" className="dv-action">
                            {action ? (
                              <button
                                type="button"
                                className={`dv-row-button is-${action.tone}`}
                                aria-label={`${action.label} ${row.id}`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setSelectedSceneId(row.id);
                                  runRowAction(row);
                                }}
                              >
                                {action.label}
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

        <footer className="dv-footer">
          <span className="dv-hints" aria-label="Keyboard shortcuts">
            <span>
              <kbd>↑↓</kbd> select
            </span>
            <span>
              <kbd>↵</kbd> open / prepare
            </span>
            <span>
              <kbd>/</kbd> filter
            </span>
            <span>
              <kbd>{getModifierKey()}\</kbd> sidebar
            </span>
          </span>
          <span className="dv-footer-state" role="status">
            {isPolling && lastStatusAt !== null ? (
              <span>
                Status every 5 s · updated {Math.max(0, Math.round((clock - lastStatusAt) / 1000))} s ago
              </span>
            ) : null}
            <span>
              <span className={`dv-dot${viewerStart || viewerOpenNow ? " is-ready" : ""}`} aria-hidden="true" />{" "}
              {viewerStart
                ? `Opening ${viewerStart} over SSH…`
                : viewerOpenNow
                  ? `Viewer open · streaming over SSH from ${profile.profile.name}`
                  : `Viewer closed · scenes stream over SSH from ${profile.profile.name}`}
            </span>
          </span>
        </footer>
      </div>

      {showInspector && selected ? (
        <aside aria-label="Scene inspector" className="dv-inspector">
          <section className="dv-insp-section">
            <span className="dv-insp-label">Selected scene</span>
            <span className="c-mono dv-insp-id">{selected.id}</span>
            {selected.time ? <span className="dv-subtle">{selected.time.recorded}</span> : null}
          </section>

          <section className="dv-insp-section">
            <span className="dv-status">
              <span className={`dv-dot is-${selected.state}`} aria-hidden="true" />
              <strong className={`dv-state is-${selected.state}`}>{STATE_LABEL[selected.state]}</strong>
            </span>
            {selected.state === "preparing" && selected.progress !== null ? (
              <span className="dv-bar is-progress is-wide">
                <span style={{ width: `${Math.min(100, selected.progress)}%` }} />
              </span>
            ) : null}
            <span className="dv-explain">{stateExplanation(selected, profile.profile.name)}</span>
            {(() => {
              const action = rowAction(selected);
              if (!action) return null;
              return (
                <button
                  type="button"
                  className={`dv-insp-button is-${action.tone}`}
                  onClick={() => runRowAction(selected)}
                >
                  {action.label === "Open" ? "Open scene" : action.label === "Prepare" ? "Prepare this scene" : action.label}
                </button>
              );
            })()}
          </section>

          <section className="dv-insp-section">
            <dl className="c-facts">
              <div className="c-fact">
                <dt>Duration</dt>
                <dd>{fmtDur(selected.dur)}</dd>
              </div>
              <div className="c-fact">
                <dt>Frames / agent</dt>
                <dd>
                  {fmtNum(selected.frames)}
                  {selected.rateHz !== null ? <span className="dv-subtle"> @ {Math.round(selected.rateHz)} Hz</span> : null}
                </dd>
              </div>
              <div className="c-fact">
                <dt>Cam frames</dt>
                <dd>{fmtNum(selected.cam)}</dd>
              </div>
              <div className="c-fact">
                <dt>LiDAR frames</dt>
                <dd>{fmtNum(selected.lidar)}</dd>
              </div>
            </dl>
            <span className="dv-subtle">
              {selected.agents} agent{selected.agents === 1 ? "" : "s"}
              {selected.sensors > 0 ? ` · ${selected.sensors} sensors` : ""}
            </span>
          </section>

          {selected.images || selected.sceneDir || resolvedCacheRoot ? (
            <section className="dv-insp-section">
              {selected.images ? (
                <>
                  <span className="dv-insp-label">Camera images</span>
                  <span>
                    {selected.images === "previews"
                      ? "Previews saved in the results folder"
                      : "Read from the raw dataset (originals)"}
                  </span>
                </>
              ) : null}
              {selected.sceneDir || resolvedCacheRoot ? (
                <>
                  <span className="dv-insp-label">Cache location</span>
                  <span className="c-mono dv-path">
                    {selected.sceneDir && selected.sceneDir.startsWith("/")
                      ? selected.sceneDir
                      : `${resolvedCacheRoot}/${selected.sceneDir || selected.id}`}
                  </span>
                </>
              ) : null}
            </section>
          ) : null}
        </aside>
      ) : null}
    </section>
  );
}
