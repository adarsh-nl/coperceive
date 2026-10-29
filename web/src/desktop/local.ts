// SPDX-License-Identifier: Apache-2.0
import type {
  ConnectionSession,
  ExecutionTarget,
  HealthStatus,
  LocalConnectOptions,
  TargetStatus,
} from "./types";
import type {
  DetectionReport,
  ScanDocument,
  ServerEntry,
  SshHostSettings,
} from "./hpc";
import type {
  JobCancelResult,
  JobLogTail,
  JobSubmitOutcome,
  JobWatchStatus,
  PipelineEntry,
  RunPlan,
  RunPlanRequest,
  SubmissionPreview,
  SubmitOptions,
} from "./runPlan";

interface SessionPayload {
  origin: string;
  token: string;
  server_version: string;
  data_root: string;
  target_type: string;
}

interface HealthPayload {
  healthy: boolean;
  latency_ms: number;
  server_version?: string;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export function isTauriEnvironment(): boolean {
  return typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__);
}

export interface AppPersistentState {
  last_data_root?: string;
  library_root?: string;
  auto_download_updates?: boolean;
  pending_download?: { dataset_id: string; preset_id: string } | null;
  last_scene_id?: string;
  last_sample_index?: number;
  last_timestamp_ns?: number;
  last_viewpoint?: string;
  last_theme?: string;
  recent_roots: string[];
}

export async function getPersistentState(): Promise<AppPersistentState | null> {
  if (isTauriEnvironment()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<AppPersistentState>("get_persistent_state");
    } catch {
      return null;
    }
  }
  return null;
}

export async function isRemoteEnabled(): Promise<boolean> {
  if (isTauriEnvironment()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<boolean>("is_remote_enabled");
    } catch {
      return false;
    }
  }
  return false;
}

/** Hosts from the user's ~/.ssh/config (aliases only, no secrets). */
export async function listSshConfigHosts(): Promise<string[]> {
  if (isTauriEnvironment()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<string[]>("list_ssh_config_hosts");
    } catch {
      return [];
    }
  }
  return [];
}

/** Effective settings for one ssh alias, read with `ssh -G`. */
export async function readSshHostSettings(alias: string): Promise<SshHostSettings | null> {
  if (isTauriEnvironment()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<SshHostSettings>("read_ssh_host_settings", { alias });
    } catch {
      return null;
    }
  }
  return null;
}

export async function listServerProfiles(): Promise<ServerEntry[]> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<ServerEntry[]>("list_server_profiles");
  }
  return [];
}

export async function saveServerProfile(entry: ServerEntry): Promise<ServerEntry> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<ServerEntry>("save_server_profile", { entry });
  }
  throw new Error("Saving server profiles is only available in the desktop app.");
}

export async function deleteServerProfile(id: string): Promise<void> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("delete_server_profile", { id });
    return;
  }
  throw new Error("Deleting server profiles is only available in the desktop app.");
}

/**
 * Runs the read-only "Test connection" checks (reachable, signed in, scheduler,
 * partitions/GPUs, environment, scratch). Each step is time-bounded in Rust.
 */
/**
 * Describes one folder inside a server's data folder (metadata only), over the
 * same reused SSH connection as the rest of the HPC workspace.
 */
export async function remoteScan(
  profileId: string,
  root: string,
  path: string,
): Promise<ScanDocument> {
  if (!isTauriEnvironment()) {
    throw new Error("Browsing a cluster's folders is only available in the desktop app.");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<ScanDocument>("remote_scan", { profileId, root, path });
}

export async function testServerConnection(entry: ServerEntry): Promise<DetectionReport> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<DetectionReport>("test_server_connection", { entry });
  }
  throw new Error("Testing a connection is only available in the desktop app.");
}

/** Installed model plugins on the cluster, offered as built-in pipelines. */
export async function listPipelineOptions(profileId: string): Promise<PipelineEntry[]> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<PipelineEntry[]>("list_pipeline_options", { profileId });
  }
  return [];
}

/** Reads a custom coperceive-pipeline.json registration over SSH (read-only). */
export async function readCustomPipeline(
  profileId: string,
  scriptPath: string,
): Promise<PipelineEntry> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<PipelineEntry>("read_custom_pipeline", { profileId, scriptPath });
  }
  throw new Error("Reading a pipeline registration is only available in the desktop app.");
}

/**
 * Builds and checks a run plan (read-only remote checks). Nothing is submitted.
 */
export async function buildRunPlan(
  profileId: string,
  request: RunPlanRequest,
): Promise<RunPlan> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<RunPlan>("build_run_plan", { profileId, request });
  }
  throw new Error("Building a run plan is only available in the desktop app.");
}

/** Saves a checked plan under the profile's results_root. Nothing is submitted. */
export async function saveRunPlan(
  profileId: string,
  plan: RunPlan,
  request: RunPlanRequest,
): Promise<RunPlan> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<RunPlan>("save_run_plan", { profileId, plan, request });
  }
  throw new Error("Saving a run plan is only available in the desktop app.");
}

/**
 * Renders the complete job script for a saved plan without submitting anything.
 * The UI shows this text; its preview_hash is sent back with the confirmation.
 */
export async function previewSubmission(
  profileId: string,
  planId: string,
  options: SubmitOptions,
): Promise<SubmissionPreview> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<SubmissionPreview>("preview_submission", {
      profileId,
      planId,
      options,
    });
  }
  throw new Error("Rendering a submission script is only available in the desktop app.");
}

/**
 * Submits a saved plan after the user confirmed the previewed script on screen.
 * The backend refuses when `confirmed` is false or the preview hash changed.
 */
export async function submitRunPlan(
  profileId: string,
  planId: string,
  options: SubmitOptions,
  previewHash: string,
  confirmed: boolean,
): Promise<JobSubmitOutcome> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<JobSubmitOutcome>("submit_run_plan", {
      profileId,
      planId,
      options,
      previewHash,
      confirmed,
    });
  }
  throw new Error("Submitting a run plan is only available in the desktop app.");
}

export async function pickDataRoot(): Promise<string | null> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<string | null>("pick_data_root");
  }
  return prompt("Enter local dataset folder path:", "/tmp/scenes");
}

export class LocalExecutionTarget implements ExecutionTarget {
  readonly id = "local";
  readonly name = "Local Dataset";
  readonly type = "local" as const;

  private currentStatus: TargetStatus = { state: "disconnected" };
  private statusListeners: Set<(status: TargetStatus) => void> = new Set();
  private logListeners: Set<(line: string) => void> = new Set();
  private activeSession: ConnectionSession | null = null;

  async connect(options: LocalConnectOptions): Promise<ConnectionSession> {
    this.updateStatus({ state: "connecting", message: "Starting local data server..." });

    let session: ConnectionSession;
    if (isTauriEnvironment()) {
      const { invoke } = await import("@tauri-apps/api/core");
      const payload = await invoke<SessionPayload>("start_local", {
        dataRoot: options.dataRoot,
        pythonBinary: options.pythonBinary,
        startFresh: options.startFresh ?? false,
      });
      session = {
        origin: payload.origin,
        token: payload.token,
        serverVersion: payload.server_version,
        dataRoot: payload.data_root,
        targetType: "local",
      };
    } else {
      // Browser fallback (direct serve)
      const origin = new URL(".", document.baseURI).href.replace(/\/$/, "");
      session = {
        origin,
        token: sessionStorage.getItem("coperceive-token") || "",
        serverVersion: "0.0.0",
        dataRoot: options.dataRoot,
        targetType: "local",
      };
    }

    // Set token and origin in sessionStorage before any API calls per ADR 0007 / ADR 0014
    if (typeof window !== "undefined") {
      sessionStorage.setItem("coperceive-token", session.token);
      sessionStorage.setItem("coperceive-origin", session.origin);
    }

    this.activeSession = session;
    this.updateStatus({
      state: "connected",
      message: `Connected to local server at ${session.origin}`,
    });
    return session;
  }

  async checkHealth(): Promise<HealthStatus> {
    if (isTauriEnvironment()) {
      const { invoke } = await import("@tauri-apps/api/core");
      const payload = await invoke<HealthPayload>("health");
      return {
        healthy: payload.healthy,
        latencyMs: payload.latency_ms,
        serverVersion: payload.server_version,
      };
    }

    if (!this.activeSession) {
      return { healthy: false, latencyMs: 0 };
    }
    const start = performance.now();
    try {
      const res = await fetch(`${this.activeSession.origin}/api/health`, {
        headers: {
          Authorization: `Bearer ${this.activeSession.token}`,
        },
      });
      const latencyMs = Math.round(performance.now() - start);
      if (!res.ok) {
        return { healthy: false, latencyMs };
      }
      const data = (await res.json()) as { version?: string };
      return { healthy: true, latencyMs, serverVersion: data.version };
    } catch {
      return { healthy: false, latencyMs: Math.round(performance.now() - start) };
    }
  }

  async disconnect(): Promise<void> {
    if (isTauriEnvironment()) {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("stop_local");
    }
    if (typeof window !== "undefined") {
      sessionStorage.removeItem("coperceive-token");
    }
    this.activeSession = null;
    this.updateStatus({ state: "disconnected" });
  }

  onStatusChange(callback: (status: TargetStatus) => void): () => void {
    this.statusListeners.add(callback);
    callback(this.currentStatus);
    return () => this.statusListeners.delete(callback);
  }

  onLog(callback: (line: string) => void): () => void {
    this.logListeners.add(callback);
    return () => this.logListeners.delete(callback);
  }

  private updateStatus(status: TargetStatus) {
    this.currentStatus = status;
    for (const listener of this.statusListeners) {
      listener(status);
    }
  }
}

export type ImportImages = "full" | "preview" | "none";

export interface ImportProgress {
  event: string;
  path?: string;
  error?: string;
  line?: string;
  done?: number;
  total?: number;
  bytes?: number;
  code?: number;
  skipped?: boolean;
}

export async function startImport(options: {
  source: string;
  dest: string;
  images: ImportImages;
  mediaRoots: string[];
  griffinRoot: string;
}): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("import_from_folder", {
    source: options.source,
    dest: options.dest,
    images: options.images,
    mediaRoots: options.mediaRoots,
    griffinRoot: options.griffinRoot || null,
  });
}

export async function cancelImport(): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("cancel_import");
}

export async function onImportProgress(
  callback: (event: ImportProgress) => void,
): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen<ImportProgress>("import-progress", (event) => {
    callback(event.payload);
  });
}

export async function getStorageInfo(): Promise<import("./types").StorageStats | null> {
  if (isTauriEnvironment()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<import("./types").StorageStats>("get_storage_info");
    } catch {
      return null;
    }
  }
  return null;
}

export async function setLibraryRoot(newRoot: string): Promise<import("./types").StorageStats | null> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<import("./types").StorageStats>("set_library_root", { newRoot });
  }
  return null;
}

export async function moveLibraryRoot(newRoot: string): Promise<import("./types").StorageStats | null> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<import("./types").StorageStats>("move_library_root", { newRoot });
  }
  return null;
}

export async function getLibraryScenes(): Promise<import("./types").LibrarySceneInfo[]> {
  if (isTauriEnvironment()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<import("./types").LibrarySceneInfo[]>("get_library_scenes");
    } catch {
      return [];
    }
  }
  return [];
}

/** Reads the small import-time preview for a library card (null when absent). */
export async function libraryThumbnail(scenePath: string): Promise<number[] | null> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<number[] | null>("library_thumbnail", { scenePath });
  }
  return null;
}

export async function deleteLibraryScene(scenePath: string): Promise<void> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("delete_library_scene", { scenePath });
  }
}

export async function revealInFinder(path: string): Promise<void> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("reveal_in_finder", { path });
  }
}

export async function datasetDryRun(
  datasetId: string,
  presetId: string,
): Promise<import("./types").DryRunResult> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<import("./types").DryRunResult>("dataset_dry_run", {
      datasetId,
      presetId,
    });
  }
  throw new Error("Tauri environment required for dry-run");
}

export async function startDatasetJob(
  datasetId: string,
  presetId: string,
  acceptTerms: boolean,
): Promise<void> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("start_dataset_job", {
      datasetId,
      presetId,
      acceptTerms,
    });
  }
}

export async function loadDatasetCatalog(): Promise<import("./types").CatalogDataset[]> {
  if (!isTauriEnvironment()) {
    throw new Error("Open the desktop app to see the dataset list.");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<import("./types").CatalogDataset[]>("dataset_catalog");
}

export async function setAutoDownloadUpdates(enabled: boolean): Promise<void> {
  if (!isTauriEnvironment()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("set_auto_download_updates", { enabled });
}

export async function pauseDatasetJob(): Promise<void> {
  if (!isTauriEnvironment()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("pause_dataset_job");
}

export async function resumeDatasetJob(): Promise<void> {
  if (!isTauriEnvironment()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("resume_dataset_job");
}

export async function cancelDatasetJob(): Promise<void> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("cancel_dataset_job");
  }
}

export async function onDatasetProgress(
  callback: (event: import("./types").DatasetProgressEvent) => void,
): Promise<() => void> {
  if (isTauriEnvironment()) {
    const { listen } = await import("@tauri-apps/api/event");
    return listen<import("./types").DatasetProgressEvent>("dataset-progress", (event) => {
      callback(event.payload);
    });
  }
  return () => {};
}

export async function validateScene(
  scenePath: string,
): Promise<{ scene_path: string; valid: boolean; stdout: string; stderr: string }> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke("validate_scene", { scenePath });
  }
  return { scene_path: scenePath, valid: true, stdout: "", stderr: "" };
}

/** Live state of a run submitted from this app. */
export async function jobWatchStatus(
  profileId: string,
  planId: string,
  jobId: string,
): Promise<JobWatchStatus> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<JobWatchStatus>("job_watch_status", { profileId, planId, jobId });
  }
  throw new Error("Watching a run is only available in the desktop app.");
}

/** A bounded tail of the run's stdout or stderr log. */
export async function jobWatchLogs(
  profileId: string,
  planId: string,
  jobId: string,
  stream: "stdout" | "stderr",
  lines = 50,
): Promise<JobLogTail> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<JobLogTail>("job_watch_logs", { profileId, planId, jobId, stream, lines });
  }
  throw new Error("Reading a job log is only available in the desktop app.");
}

/** Cancels a pending/running job after the user confirmed it. */
export async function cancelRunJob(
  profileId: string,
  planId: string,
  jobId: string,
  confirmed: boolean,
): Promise<JobCancelResult> {
  if (isTauriEnvironment()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<JobCancelResult>("cancel_run_job", { profileId, planId, jobId, confirmed });
  }
  throw new Error("Cancelling a run is only available in the desktop app.");
}
