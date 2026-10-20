// SPDX-License-Identifier: Apache-2.0
import type {
  ConnectionSession,
  ExecutionTarget,
  HealthStatus,
  LocalConnectOptions,
  TargetStatus,
} from "./types";

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
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

    // In a Tauri environment, invoke native sidecar start command
    let session: ConnectionSession;
    if (typeof window !== "undefined" && window.__TAURI_INTERNALS__) {
      const { invoke } = await import("@tauri-apps/api/core");
      session = await invoke<ConnectionSession>("start_local_sidecar", {
        dataRoot: options.dataRoot,
        pythonBinary: options.pythonBinary,
      });
    } else {
      // Direct browser fallback for development/testing
      const origin = new URL(".", document.baseURI).href.replace(/\/$/, "");
      session = {
        origin,
        token: sessionStorage.getItem("coperceive-token") || "",
        serverVersion: "0.0.0",
        dataRoot: options.dataRoot,
        targetType: "local",
      };
    }

    // Set token in sessionStorage before any API calls per ADR 0007 / ADR 0014
    if (typeof window !== "undefined") {
      sessionStorage.setItem("coperceive-token", session.token);
    }

    // Await healthy backend response
    await this.pollUntilHealthy(session.origin, session.token, 10000);

    this.activeSession = session;
    this.updateStatus({ state: "connected", message: `Connected to local server at ${session.origin}` });
    return session;
  }

  async checkHealth(): Promise<HealthStatus> {
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
    if (this.activeSession && typeof window !== "undefined" && window.__TAURI_INTERNALS__) {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("stop_local_sidecar");
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

  private async pollUntilHealthy(origin: string, token: string, timeoutMs: number): Promise<void> {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      try {
        const res = await fetch(`${origin}/api/health`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          return;
        }
      } catch {
        // Retry
      }
      await new Promise((r) => setTimeout(r, 150));
    }
    throw new Error(`Timeout waiting for local server at ${origin} to become healthy.`);
  }
}
