// SPDX-License-Identifier: Apache-2.0
/**
 * Desktop ExecutionTarget and session types.
 * Process, SSH, and Slurm logic live in Rust; TypeScript holds thin bindings.
 */

export type TargetType = "local" | "ssh";

export type TargetState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "degraded"
  | "error";

export interface TargetStatus {
  readonly state: TargetState;
  readonly message?: string;
  readonly pendingReason?: string;
  readonly elapsedSeconds?: number;
}

export interface HealthStatus {
  readonly healthy: boolean;
  readonly latencyMs: number;
  readonly serverVersion?: string;
}

export interface ConnectionSession {
  readonly origin: string;
  readonly token: string;
  readonly serverVersion: string;
  readonly dataRoot: string;
  readonly targetType: TargetType;
  readonly slurmJobId?: string;
}

export interface SlurmJobInfo {
  readonly jobId: string;
  readonly jobName: string;
  readonly state: string;
  readonly reason: string;
  readonly timeElapsed: string;
  readonly timeLimit: string;
  readonly nodelist: string;
}

export interface LocalConnectOptions {
  readonly type: "local";
  readonly dataRoot: string;
  readonly pythonBinary?: string;
  readonly startFresh?: boolean;
}

export interface SshConnectOptions {
  readonly type: "ssh";
  readonly hostAlias: string;
  readonly remoteDataRoot: string;
  readonly remotePythonBinary?: string;
  readonly remoteCoperceiveBinary?: string;
  readonly partition?: string;
  readonly walltime?: string;
}

export type ConnectOptions = LocalConnectOptions | SshConnectOptions;

export interface ExecutionTarget {
  readonly id: string;
  readonly name: string;
  readonly type: TargetType;

  /** Connects / attaches to the backend serve instance. */
  connect(options: ConnectOptions): Promise<ConnectionSession>;

  /** Actively checks backend health. */
  checkHealth(): Promise<HealthStatus>;

  /** Cleanly tears down processes, allocations, and tunnels. */
  disconnect(): Promise<void>;

  /** Subscribes to status transitions. */
  onStatusChange(callback: (status: TargetStatus) => void): () => void;

  /** Subscribes to diagnostic log lines. */
  onLog(callback: (line: string) => void): () => void;
}

export interface StorageStats {
  readonly library_root: string;
  readonly is_configured?: boolean;
  readonly volume_name: string;
  readonly free_bytes: number;
  readonly total_bytes: number;
  readonly used_bytes: number;
  readonly scene_count: number;
}

export interface LibrarySceneInfo {
  readonly scene_id: string;
  readonly path: string;
  readonly dataset_name?: string;
  readonly town?: string;
  readonly duration_s?: number;
  readonly agent_count?: number;
  readonly sample_count?: number;
  readonly size_bytes: number;
  readonly has_images: "full" | "preview" | "none";
  /** A 640 px preview was written at import time (thumbnail.jpg). */
  readonly has_thumbnail: boolean;
  readonly has_map: boolean;
  readonly is_complete: boolean;
  /** Cached `coperceive validate` answer: true/false, or null until the background check finishes. */
  readonly is_valid?: boolean | null;
}

export interface CatalogPreset {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly estimated_download_bytes?: number;
  readonly estimated_disk_bytes?: number;
}

export interface CatalogDataset {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly modalities: string[];
  readonly agents: string;
  readonly licence: string;
  readonly licence_url?: string;
  readonly source_url?: string;
  readonly paper_url?: string;
  readonly status: "ready" | "local-import" | "planned";
  readonly requires_terms?: boolean;
  readonly notice?: string;
  readonly presets?: CatalogPreset[];
}

export interface DryRunResult {
  readonly event: "dry_run";
  readonly dataset_id: string;
  readonly preset_id: string;
  readonly download_bytes: number;
  readonly disk_bytes: number;
  readonly free_bytes: number;
  readonly total_bytes: number;
  readonly has_headroom: boolean;
  readonly requires_terms: boolean;
  readonly licence: string;
  readonly licence_url?: string;
}

export interface DatasetProgressEvent {
  readonly event: "stage" | "complete" | "error" | "log" | "exit";
  readonly stage?: "download" | "verify_md5" | "extract" | "convert" | "validate";
  readonly message?: string;
  readonly percent?: number;
  readonly dataset_id?: string;
  readonly preset_id?: string;
  readonly scenes?: string[];
  readonly first_scene?: string;
  readonly path?: string;
  readonly code?: number;
}
