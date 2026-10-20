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
