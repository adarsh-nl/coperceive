// SPDX-License-Identifier: Apache-2.0
// Camera, pointer and timeline maths from reference/Tour.engine.reference.html.

export type Cam = { x: number; y: number; s: number };
export type Ptr = { x: number; y: number };

export type Step = {
  ch: number;
  dur: number;
  screen?: string;
  cam?: Cam;
  ptr?: Ptr;
  click?: boolean;
  cap?: [string, string];
  capSide?: string;
  capTop?: boolean;
  hidePtr?: boolean;
  end?: boolean;
};

export type TimedStep = Step & { t0: number; t1: number };

export type Placement = {
  tx: number;
  ty: number;
  s: number;
  px: number;
  py: number;
};

export type Frame = Placement & {
  idx: number;
  step: TimedStep;
  screen: string | null;
  ptrOn: boolean;
  clickDelay: number;
  capIdx: number;
  end: boolean;
};

export function timeline(steps: readonly Step[]): TimedStep[] {
  let t = 0;
  return steps.map((step) => {
    const t0 = t;
    t += step.dur;
    return { ...step, t0, t1: t };
  });
}

/** chapter is 1-based, matching ?chapter= and the reference onlyChapter. */
export function range(tl: readonly TimedStep[], chapter?: number): [number, number] {
  const end = tl[tl.length - 1]?.t1 ?? 0;
  if (chapter == null) return [0, end];
  const mine = tl.filter((step) => step.ch === chapter - 1);
  if (mine.length === 0) return [0, end];
  return [mine[0].t0, mine[mine.length - 1].t1];
}

export function advance(t: number, dt: number, span: readonly [number, number]): number {
  const next = t + dt;
  if (next < span[0] || next >= span[1]) return span[0];
  return next;
}

export function seekChapter(tl: readonly TimedStep[], chapterIndex: number): number {
  const mine = tl.filter((step) => step.ch === chapterIndex);
  if (mine.length === 0) return 0.01;
  return mine[0].t0 + 0.01;
}

export function chapterProgress(tl: readonly TimedStep[], chapterIndex: number, t: number): number {
  const mine = tl.filter((step) => step.ch === chapterIndex);
  if (mine.length === 0) return 0;
  const start = mine[0].t0;
  const end = mine[mine.length - 1].t1;
  if (t <= start) return 0;
  if (t >= end) return 1;
  return (t - start) / (end - start);
}

export function clickDelay(dur: number): number {
  return Math.min(1.3, dur * 0.6);
}

/** translate(tx%, ty%) scale(s), origin 0 0, clamped, then nudged so the pointer stays in 6–90%. */
export function placeCamera(cam: Cam, ptr: Ptr): Placement {
  const s = cam.s;
  let tx = 50 - s * cam.x;
  let ty = 50 - s * cam.y;
  const maxT = 0;
  const minT = 100 - 100 * s;
  const clamp = (value: number) => Math.min(maxT, Math.max(minT, value));
  tx = clamp(tx);
  ty = clamp(ty);
  const keep = (value: number, p: number) => {
    const at = value + s * p;
    if (at > 90) value -= at - 90;
    if (at < 6) value += 6 - at;
    return clamp(value);
  };
  tx = keep(tx, ptr.x);
  ty = keep(ty, ptr.y);
  return { tx, ty, s, px: tx + s * ptr.x, py: ty + s * ptr.y };
}

export function stepIndex(tl: readonly TimedStep[], t: number): number {
  let idx = 0;
  for (let i = 0; i < tl.length; i++) if (t >= tl[i].t0) idx = i;
  return idx;
}

export function frameAt(steps: readonly Step[], t: number): Frame {
  const tl = timeline(steps);
  const idx = stepIndex(tl, t);
  let screen: string | null = null;
  let cam: Cam = { x: 50, y: 50, s: 1 };
  let ptr: Ptr = { x: 50, y: 60 };
  let ptrOn = false;
  for (let j = 0; j <= idx; j++) {
    const step = tl[j];
    if (step.screen) screen = step.screen;
    if (step.cam) cam = step.cam;
    if (step.ptr) {
      ptr = step.ptr;
      ptrOn = true;
    }
    if (step.hidePtr) ptrOn = false;
  }
  const placed = placeCamera(cam, ptr);
  const cur = tl[idx];
  let capIdx = idx;
  while (capIdx > 0) {
    const here = tl[capIdx]?.cap?.[0];
    const prev = tl[capIdx - 1]?.cap?.[0];
    if (!here || here !== prev) break;
    capIdx -= 1;
  }
  if (!cur) throw new Error("tour timeline is empty");
  return {
    idx,
    step: cur,
    screen,
    ptrOn,
    clickDelay: clickDelay(cur.dur),
    capIdx,
    end: !!cur.end,
    ...placed,
  };
}

export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** Screens that belong to the opening chapter. Those files load first. */
export function eagerScreenKeys(steps: readonly Step[]): Set<string> {
  const keys = new Set<string>();
  for (const step of steps) {
    if (step.ch === 0 && step.screen) keys.add(step.screen);
  }
  return keys;
}
