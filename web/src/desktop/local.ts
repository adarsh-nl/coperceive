// SPDX-License-Identifier: Apache-2.0
import type {
  ConnectionSession,
  ExecutionTarget,
  HealthStatus,
  LocalConnectOptions,
  TargetStatus,
} from "./types";

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
