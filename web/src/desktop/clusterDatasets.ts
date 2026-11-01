// SPDX-License-Identifier: Apache-2.0
/**
 * Cluster datasets client, types, and pure helpers.
 *
 * Implements the contract defined in `docs/datasets/cluster-datasets.md`
 * (schemas: `coperceive-cluster-detect-1`, `coperceive-cluster-index-1`,
 * `coperceive-cluster-scene-status-1`, `coperceive-cluster-dataset-cache-1`).
 */
export function isTauriEnvironment(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  );
}

type InvokeFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

let testInvokeMock: InvokeFn | null = null;

export function _setInvokeForTesting(fn: InvokeFn | null): void {
  testInvokeMock = fn;
}

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (testInvokeMock) {
    return await testInvokeMock<T>(cmd, args);
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<T>(cmd, args);
}

export interface ClusterDatasetLicence {
  status: "known" | "unknown" | string;
  spdx: string | null;
  notice: string;
  links: string[];
}

export interface ClusterDetectDocument {
  schema: "coperceive-cluster-detect-1" | string;
  path: string;
  adapter: "griffin" | "opv2v" | "v2xset" | string | null;
  raw_root: string;
  split: string | null;
  params: Record<string, unknown>;
  licence: ClusterDatasetLicence | null;
  scene_count: number;
  status: "ok" | "none" | "error";
  reason: string | null;
}

export interface ClusterIndexSensors {
  sensors: number;
  camera_samples: number;
  lidar_samples: number;
}

export interface ClusterIndexScene {
  scene_id: string;
  sample_count: number;
  duration_s: number | null;
  agent_count: number;
  sensors: ClusterIndexSensors;
}

export interface ClusterIndexFingerprint {
  algorithm: string;
  digest: string;
  entries: number;
  truncated: boolean;
}

export interface ClusterIndexDocument {
  schema: "coperceive-cluster-index-1" | string;
  adapter: string;
  raw_root: string;
  split: string | null;
  params: Record<string, unknown>;
  licence: ClusterDatasetLicence | null;
  fingerprint: ClusterIndexFingerprint;
  cache: { relative_path: string };
  scene_count: number;
  scenes: ClusterIndexScene[];
  errors: string[];
  truncated: boolean;
  elapsed_ms: number;
  storage_estimate_bytes?: number | null;
  storage_estimate_label?: string | null;
}

export type ClusterSceneState =
  | "not_prepared"
  | "preparing"
  | "ready"
  | "failed"
  | "stale";

export interface ClusterSceneStatusEntry {
  scene_id: string;
  state: ClusterSceneState;
  reason: string | null;
  job_id: string | null;
  prepared_unix: number | null;
  scene_dir: string | null;
  progress_percent?: number | null;
}

export interface ClusterSceneStatusDocument {
  schema: "coperceive-cluster-scene-status-1" | string;
  cache_root: string;
  adapter: string;
  raw_root: string;
  split: string | null;
  updated_unix: number;
  scenes: ClusterSceneStatusEntry[];
}

export interface DatasetPreparePreview {
  plan_id: string;
  job_name: string;
  partition: string;
  walltime: string;
  cpus_per_task: number;
  gres: string | null;
  cache_root: string;
  estimated_cache_bytes: number;
  script: string;
  preview_hash: string;
}

export interface DatasetPrepareSubmitResult {
  job_id: string;
  state: "pending" | "running" | "completed" | "failed" | "cancelled" | "unknown" | string;
  queue_reason: string | null;
  plan_id: string;
}

export interface DatasetOpenResult {
  origin: string;
  token: string;
  server_version: string;
  data_root: string;
  target_type: "remote" | string;
}

// ---------------------------------------------------------------------------
// Pure helper formatting functions
// ---------------------------------------------------------------------------

/** Format the detect banner title, e.g. "OPV2V · test · 16 scenes" */
export function formatDetectBannerTitle(doc: ClusterDetectDocument): string {
  const parts: string[] = [];
  if (doc.adapter) {
    parts.push(doc.adapter.toUpperCase());
  }
  if (doc.split) {
    parts.push(doc.split);
  }
  const count = doc.scene_count ?? 0;
  parts.push(`${count} ${count === 1 ? "scene" : "scenes"}`);
  return parts.join(" · ");
}

/** Format licence information plainly. */
export function formatLicenceInfo(licence: ClusterDatasetLicence | null): {
  statusText: string;
  links: string[];
} {
  if (!licence) {
    return {
      statusText: "Licence not specified.",
      links: [],
    };
  }
  if (licence.status === "unknown") {
    return {
      statusText: "licence not published — research use on this cluster only",
      links: licence.links || [],
    };
  }
  const prefix = licence.spdx ? `SPDX: ${licence.spdx}` : licence.notice || "Known licence";
  return {
    statusText: prefix,
    links: licence.links || [],
  };
}

/** Format scene duration in seconds cleanly. */
export function formatDuration(durationS: number | null | undefined): string {
  if (durationS == null || isNaN(durationS)) {
    return "—";
  }
  return `${durationS.toFixed(1)}s`;
}

/** Format coarse sensor summary. */
export function formatSensorsSummary(sensors: ClusterIndexSensors | null | undefined): string {
  if (!sensors) return "—";
  const cam = `${sensors.camera_samples ?? 0} cam`;
  const lidar = `${sensors.lidar_samples ?? 0} lidar`;
  const count = sensors.sensors ? ` (${sensors.sensors} sensors)` : "";
  return `${cam} / ${lidar}${count}`;
}

/** Construct an estimated storage label from scene counts if not provided by backend. */
export function formatStorageEstimate(
  doc: ClusterIndexDocument,
): string {
  if (doc.storage_estimate_label) {
    return doc.storage_estimate_label;
  }
  if (doc.storage_estimate_bytes != null) {
    const gb = doc.storage_estimate_bytes / (1024 * 1024 * 1024);
    return `~${gb.toFixed(1)} GB`;
  }
  // Coarse default estimate: ~150 MB per schema-1 scene for multi-agent camera/lidar
  const estMb = doc.scene_count * 150;
  if (estMb >= 1024) {
    return `~${(estMb / 1024).toFixed(1)} GB`;
  }
  return `~${estMb} MB`;
}

// ---------------------------------------------------------------------------
// In-memory stub store for PR 2 stubs during development / testing (outside Tauri)
// ---------------------------------------------------------------------------

const stubSceneStatuses = new Map<string, Map<string, ClusterSceneStatusEntry>>();

function datasetStubKey(adapter: string, rawRoot: string, split: string | null): string {
  return `${adapter}::${rawRoot}::${split ?? ""}`;
}

export function _resetStubStore(): void {
  stubSceneStatuses.clear();
}

export function _setStubSceneState(
  dataset: { adapter: string; rawRoot: string; split: string | null } | string,
  sceneId: string,
  state: ClusterSceneState,
  reason: string | null = null,
  jobId: string | null = null,
): void {
  const key =
    typeof dataset === "string"
      ? dataset
      : datasetStubKey(dataset.adapter, dataset.rawRoot, dataset.split);
  let cacheMap = stubSceneStatuses.get(key);
  if (!cacheMap) {
    cacheMap = new Map();
    stubSceneStatuses.set(key, cacheMap);
  }
  cacheMap.set(sceneId, {
    scene_id: sceneId,
    state,
    reason,
    job_id: jobId,
    prepared_unix: state === "ready" ? Date.now() / 1000 : null,
    scene_dir: sceneId,
  });
}

// ---------------------------------------------------------------------------
// Tauri IPC commands & PR 2 stubs
// ---------------------------------------------------------------------------

/**
 * Run read-only detection on the server for a folder path.
 * Invokes Tauri command `dataset_detect({ profileId, path })`.
 */
export async function datasetDetect(
  profileId: string,
  path: string,
): Promise<ClusterDetectDocument> {
  if (isTauriEnvironment()) {
    return await tauriInvoke<ClusterDetectDocument>("dataset_detect", {
      profileId,
      path,
    });
  }

  // Web / browser test fallback outside Tauri
  return {
    schema: "coperceive-cluster-detect-1",
    path,
    adapter: "opv2v",
    raw_root: path,
    split: "test",
    params: { split: "test" },
    licence: {
      status: "unknown",
      spdx: null,
      notice: "No licence published.",
      links: ["https://mobility-lab.seas.ucla.edu/opv2v/"],
    },
    scene_count: 16,
    status: "ok",
    reason: null,
  };
}

/**
 * Run read-only scene indexing on the server for a detected dataset.
 * Invokes Tauri command `dataset_index({ profileId, adapter, rawRoot })`.
 */
export async function datasetIndex(
  profileId: string,
  adapter: string,
  rawRoot: string,
): Promise<ClusterIndexDocument> {
  if (isTauriEnvironment()) {
    return await tauriInvoke<ClusterIndexDocument>("dataset_index", {
      profileId,
      adapter,
      rawRoot,
    });
  }

  // Web / browser test fallback outside Tauri
  return {
    schema: "coperceive-cluster-index-1",
    adapter,
    raw_root: rawRoot,
    split: "test",
    params: { split: "test" },
    licence: {
      status: "unknown",
      spdx: null,
      notice: "No licence published.",
      links: ["https://mobility-lab.seas.ucla.edu/opv2v/"],
    },
    fingerprint: {
      algorithm: "sha1-mtime-size-v1",
      digest: "9f1c2b3a4e5d",
      entries: 1600,
      truncated: false,
    },
    cache: {
      relative_path: `coperceive-cache/${adapter}/mockdigest/test`,
    },
    scene_count: 16,
    scenes: Array.from({ length: 16 }, (_, i) => ({
      scene_id: `scene_${String(i + 1).padStart(4, "0")}`,
      sample_count: 60,
      duration_s: 6.0,
      agent_count: 3,
      sensors: {
        sensors: 2,
        camera_samples: 240,
        lidar_samples: 60,
      },
    })),
    errors: [],
    truncated: false,
    elapsed_ms: 120.5,
  };
}

/**
 * Retrieve per-scene status from the dataset cache on the cluster.
 * Invokes Tauri command `dataset_status({ profileId, adapter, rawRoot, split })`.
 */
export async function datasetStatus(
  profileId: string,
  params: {
    adapter: string;
    rawRoot: string;
    split: string | null;
  },
): Promise<ClusterSceneStatusDocument> {
  if (isTauriEnvironment()) {
    return await tauriInvoke<ClusterSceneStatusDocument>("dataset_status", {
      profileId,
      adapter: params.adapter,
      rawRoot: params.rawRoot,
      split: params.split,
    });
  }

  // Web / browser test fallback outside Tauri
  const key = datasetStubKey(params.adapter, params.rawRoot, params.split);
  const entries: ClusterSceneStatusEntry[] = [];
  const cacheMap = stubSceneStatuses.get(key);
  if (cacheMap) {
    for (const entry of cacheMap.values()) {
      entries.push({ ...entry });
    }
  }

  return {
    schema: "coperceive-cluster-scene-status-1",
    cache_root: `<results_root>/coperceive-cache/${params.adapter}/mockdigest/${params.split ?? "all"}`,
    adapter: params.adapter,
    raw_root: params.rawRoot,
    split: params.split,
    updated_unix: Math.floor(Date.now() / 1000),
    scenes: entries,
  };
}

/**
 * Generate preview script for preparing a dataset or single scene.
 * Invokes Tauri command `dataset_prepare({ profileId, adapter, rawRoot, split, scenes, workers })`.
 */
export async function datasetPrepare(
  profileId: string,
  params: {
    adapter: string;
    rawRoot: string;
    split: string | null;
    scenes?: string[] | "all";
    workers?: number;
  },
): Promise<DatasetPreparePreview> {
  const scenes = params.scenes ?? "all";

  if (isTauriEnvironment()) {
    const args: Record<string, unknown> = {
      profileId,
      adapter: params.adapter,
      rawRoot: params.rawRoot,
      split: params.split,
      scenes,
    };
    if (params.workers != null) {
      args.workers = params.workers;
    }
    return await tauriInvoke<DatasetPreparePreview>("dataset_prepare", args);
  }

  // Web / browser test fallback outside Tauri
  const sceneArg = Array.isArray(scenes) ? `--scenes ${scenes.join(" ")}` : "--all";
  const workers = params.workers ?? (Array.isArray(scenes) ? Math.min(scenes.length, 8) : 8);
  const script = `#!/bin/bash
#SBATCH --job-name=coperceive-prepare-${params.adapter}
#SBATCH --cpus-per-task=${workers}
#SBATCH --nodes=1
#SBATCH --time=02:00:00
# No GPU requested (CPU-only job).

set -euo pipefail

echo "Starting dataset preparation: adapter=${params.adapter}"
coperceive dataset prepare \\
  --adapter "${params.adapter}" \\
  --raw-root "${params.rawRoot}" \\
  --workers ${workers} \\
  ${sceneArg}
`;

  return {
    plan_id: `plan-stub-${Date.now()}`,
    job_name: `coperceive-prepare-${params.adapter}`,
    partition: "cpu",
    walltime: "02:00:00",
    cpus_per_task: workers,
    gres: null,
    cache_root: `<results_root>/coperceive-cache/${params.adapter}/mockdigest/${params.split ?? "all"}`,
    estimated_cache_bytes: 1024 * 1024 * 50,
    script,
    preview_hash: `hash-stub-${Date.now()}`,
  };
}

/**
 * Submit confirmed prepare job to Slurm on the cluster.
 * Invokes Tauri command `dataset_prepare_submit({ profileId, previewHash, confirmed })`.
 */
export async function datasetPrepareSubmit(
  profileId: string,
  previewHash: string,
  confirmed: boolean = true,
): Promise<DatasetPrepareSubmitResult> {
  if (!previewHash) {
    throw new Error("A valid preview hash is required to submit dataset preparation.");
  }

  if (isTauriEnvironment()) {
    return await tauriInvoke<DatasetPrepareSubmitResult>("dataset_prepare_submit", {
      profileId,
      previewHash,
      confirmed,
    });
  }

  // Web / browser test fallback outside Tauri
  if (!confirmed) {
    throw new Error("Preparation submission was not confirmed.");
  }

  const fakeJobId = String(Math.floor(600000 + Math.random() * 100000));
  return {
    job_id: fakeJobId,
    state: "pending",
    queue_reason: null,
    plan_id: `plan-${fakeJobId}`,
  };
}

/**
 * Open a ready scene in the viewer via the remote viewer session.
 * Invokes Tauri command `dataset_open({ profileId, adapter, rawRoot, split, sceneId })`.
 */
export async function datasetOpen(
  profileId: string,
  sceneId: string,
  params: {
    adapter: string;
    rawRoot: string;
    split: string | null;
  },
): Promise<DatasetOpenResult> {
  if (isTauriEnvironment()) {
    return await tauriInvoke<DatasetOpenResult>("dataset_open", {
      profileId,
      adapter: params.adapter,
      rawRoot: params.rawRoot,
      split: params.split,
      sceneId,
    });
  }

  // Web / browser test fallback outside Tauri
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("coperceive-open-cluster-scene", {
        detail: {
          profileId,
          sceneId,
          adapter: params.adapter,
          rawRoot: params.rawRoot,
          split: params.split,
        },
      }),
    );
  }

  return {
    origin: "http://127.0.0.1:40123",
    token: "mock-token",
    server_version: "0.1.0",
    data_root: `coperceive-cache/${params.adapter}/stub/${params.split ?? "all"}`,
    target_type: "remote",
  };
}
