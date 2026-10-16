export const SPEEDS = [0.25, 0.5, 1, 2, 4] as const;

export type Speed = (typeof SPEEDS)[number];

const FALLBACK_GAP_MS = 100;

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
