export const SPEEDS = [0.25, 0.5, 1, 2, 4] as const;

export type Speed = (typeof SPEEDS)[number];

const FALLBACK_GAP_MS = 100;

/**
 * Hold duration before showing "Buffering…" during a genuine stream stall.
 * Exceeds one 10 Hz frame interval (100 ms) and typical local point/draw
 * jitter so cached local playback does not falsely show "Buffering…".
 */
export const PLAYBACK_BUFFERING_AFTER_MS = 250;

export type SyncRedrawDeps = {
  sceneId: string;
  summarySceneId: string | null;
  hidden: unknown;
  appearance: unknown;
};

/**
 * Decide whether the viewer's synchronous pre-async stage redraw should run.
 * Only needed when visibility (`hidden`) or layer/display settings (`appearance`)
 * changed within the same scene; plain playback `frameIndex` advances must skip
 * it so the previous frame is not rendered synchronously before the new sample.
 */
export function shouldRunSyncStageRedraw(
  prev: SyncRedrawDeps | null,
  next: SyncRedrawDeps,
): boolean {
  if (!prev) {
    return false;
  }
  return (
    prev.sceneId === next.sceneId &&
    prev.summarySceneId === next.summarySceneId &&
    (prev.hidden !== next.hidden || prev.appearance !== next.appearance)
  );
}

export type DrawnHoldInput = {
  nowMs: number;
  waitingSinceMs: number | null;
  stalled: boolean;
  bufferingAfterMs?: number;
};

export type DrawnHoldDecision = {
  waitingSinceMs: number;
  showBuffering: boolean;
  resetOrigin: boolean;
};

/**
 * Decide how playback timing and "Buffering…" respond while waiting for the
 * target sample to finish drawing. Transient local draw delays keep the wall
 * clock anchored and never trigger "Buffering…"; only an active stream stall
 * exceeding `bufferingAfterMs` displays "Buffering…" and pins `origin`.
 */
export function stepDrawnHoldWait(input: DrawnHoldInput): DrawnHoldDecision {
  const waitingSinceMs = input.waitingSinceMs ?? input.nowMs;
  const thresholdMs = input.bufferingAfterMs ?? PLAYBACK_BUFFERING_AFTER_MS;
  const stalledOverThreshold = input.stalled && input.nowMs - waitingSinceMs > thresholdMs;
  return {
    waitingSinceMs,
    showBuffering: stalledOverThreshold,
    resetOrigin: stalledOverThreshold,
  };
}

/**
 * Clear "Buffering…" as soon as the held frame finishes drawing unless playback
 * is currently paused by the stream-buffer underrun guard.
 */
export function shouldClearBufferingOnDraw(stallPaused: boolean): boolean {
  return !stallPaused;
}

export function frameDelayMs(times: readonly number[], index: number, speed: number): number | null {
  if (index < 0 || index >= times.length - 1) {
    return null;
  }
  const current = times[index];
  const next = times[index + 1];
  if (typeof current !== "number" || typeof next !== "number" || speed <= 0) {
    return null;
  }
  const dtUs = next - current;
  if (!Number.isFinite(dtUs) || dtUs <= 0) {
    return FALLBACK_GAP_MS / speed;
  }
  return dtUs / 1000 / speed;
}

export type PlaybackPlace = {
  index: number;
  done: boolean;
  remainMs: number;
};

function gapUs(times: readonly number[], index: number): number {
  const delay = frameDelayMs(times, index, 1);
  if (delay === null) {
    return FALLBACK_GAP_MS * 1000;
  }
  return delay * 1000;
}

/** Sample that should be on screen after `elapsedMs` of playback at `speed`. */
export function indexAfter(
  times: readonly number[],
  originIndex: number,
  elapsedMs: number,
  speed: number,
  loop: boolean,
): PlaybackPlace {
  const length = times.length;
  const idle = { index: Math.max(0, originIndex), done: false, remainMs: FALLBACK_GAP_MS };
  if (length < 2 || speed <= 0 || !Number.isFinite(elapsedMs) || elapsedMs < 0) {
    return idle;
  }
  let remainingUs = elapsedMs * 1000 * speed;
  let index = Math.min(Math.max(originIndex, 0), length - 1);
  let guard = 0;
  while (guard < length * 4 + 8) {
    guard += 1;
    const atEnd = index >= length - 1;
    const basis = atEnd ? length - 2 : index;
    const gap = gapUs(times, basis);
    if (remainingUs < gap) {
      return { index, done: false, remainMs: (gap - remainingUs) / 1000 / speed };
    }
    remainingUs -= gap;
    if (atEnd) {
      if (!loop) {
        return { index, done: true, remainMs: 0 };
      }
      index = 0;
      continue;
    }
    index += 1;
  }
  return { index, done: !loop && index >= length - 1, remainMs: 0 };
}

export function stepIndex(index: number, delta: number, length: number, loop: boolean): number {
  if (length <= 0) {
    return 0;
  }
  const next = index + delta;
  if (loop) {
    return ((next % length) + length) % length;
  }
  return Math.min(Math.max(next, 0), length - 1);
}

export function neighborSpeed(current: Speed, direction: number): Speed {
  const index = SPEEDS.indexOf(current);
  const next = index + (direction >= 0 ? 1 : -1);
  return SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, next))] ?? current;
}

export function formatClock(timeUs: number): string {
  return `${(timeUs / 1_000_000).toFixed(1)} s`;
}

/**
 * Returns true if the index falls inside the end zone:
 * the last 5% of samples or the last 2 seconds, whichever covers more samples.
 */
export function isEndZone(times: readonly number[] | undefined, index: number): boolean {
  if (!times || times.length < 2) {
    return false;
  }
  const length = times.length;
  if (index >= length - 1) {
    return true;
  }
  const fivePercentIndex = Math.max(0, Math.floor(length * 0.95));
  const endTime = times[length - 1];
  const twoSecondsBefore = endTime - 2_000_000;
  return index >= fivePercentIndex || (typeof times[index] === "number" && times[index] >= twoSecondsBefore);
}

