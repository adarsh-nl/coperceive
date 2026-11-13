// SPDX-License-Identifier: Apache-2.0
import type { DemoOutcome } from "./local";

export interface DemoProgressInfo {
  stage?: string;
  elapsed_seconds?: number;
}

/** Approximate demo cache size before generation (must stay within ±20% of real output). */
export const DEMO_APPROX_BYTES = 8 * 1024 * 1024;
export const DEMO_APPROX_SIZE_LABEL = "about 8 MB";

export function formatDemoSize(bytes?: number | null): string {
  if (typeof bytes === "number" && Number.isFinite(bytes) && bytes > 0) {
    return `${(bytes / 1_000_000).toFixed(1)} MB`;
  }
  return DEMO_APPROX_SIZE_LABEL;
}

export interface DemoOpenCallbacks {
  ensureDemo: () => Promise<DemoOutcome>;
  subscribeProgress: (handler: (payload: DemoProgressInfo) => void) => Promise<() => void>;
  onGeneratingStart: () => void;
  onGeneratingEnd: () => void;
  onProgress: (stage: string | undefined, elapsedSec: number) => void;
  onDemoBytes?: (bytes: number) => void;
  progressDelayMs?: number;
}

/**
 * Runs demo ensure + progress tracking so cached demos open immediately without
 * flashing "Generating demo scene…", while uncached runs show the progress
 * banner as soon as progress arrives (or after `progressDelayMs`).
 */
export async function runDemoOpenFlow({
  ensureDemo,
  subscribeProgress,
  onGeneratingStart,
  onGeneratingEnd,
  onProgress,
  onDemoBytes,
  progressDelayMs = 150,
}: DemoOpenCallbacks): Promise<DemoOutcome> {
  let started = false;
  let finished = false;
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  const startMs = Date.now();

  const markGenerating = () => {
    if (finished || started) return;
    started = true;
    onGeneratingStart();
    elapsedTimer = setInterval(() => {
      onProgress(undefined, Math.floor((Date.now() - startMs) / 1000));
    }, 1000);
  };

  const delayTimer = setTimeout(markGenerating, progressDelayMs);
  const unlisten = await subscribeProgress((payload) => {
    markGenerating();
    const elapsed =
      typeof payload.elapsed_seconds === "number"
        ? Math.floor(payload.elapsed_seconds)
        : Math.floor((Date.now() - startMs) / 1000);
    onProgress(payload.stage, elapsed);
  });

  try {
    const outcome = await ensureDemo();
    if (typeof outcome.bytes === "number" && outcome.bytes > 0) {
      onDemoBytes?.(outcome.bytes);
    }
    return outcome;
  } finally {
    finished = true;
    clearTimeout(delayTimer);
    if (elapsedTimer) clearInterval(elapsedTimer);
    unlisten();
    if (started) {
      onGeneratingEnd();
    }
  }
}
