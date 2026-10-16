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
