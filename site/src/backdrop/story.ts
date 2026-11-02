import { revealGate } from "./drawBev";
import type { BackdropScene, ObjectState } from "./scene";

let cachedTime: number | null = null;

/** Scene clock where chapter 1 hides the most in-range objects from the car. */
export function occlusionTime(scene: BackdropScene): number {
  if (cachedTime !== null) return cachedTime;
  let best = -1;
  let t = 12;
  for (const frame of scene.frames) {
    if (frame.chapter !== 1) continue;
    let n = 0;
    for (const row of frame.objs) {
      if (row.inRange && row.occ && !row.vDet && row.dDet) n += 1;
    }
    if (n > best) {
      best = n;
      t = frame.t;
    }
  }
  cachedTime = t;
  return t;
}

export function hiddenFromCar(scene: BackdropScene, t: number): ObjectState[] {
  return scene.stateAt(t).objs.filter((row) => row.inRange && !row.vDet && row.dDet);
}

export function recoveredCount(rows: ObjectState[], reveal: number): number {
  let n = 0;
  for (const row of rows) {
    if (reveal >= revealGate(row.o.id)) n += 1;
  }
  return n;
}

const BAND = {
  both: "#1FA3A3",
  vehicle: "#F58A1F",
  drone: "#3C8DFF",
  nobody: "#8E6CEF",
} as const;

/** Who saw each object, left to right. Same colours as the desktop ribbon. */
export function drawLedger(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scene: BackdropScene,
  until: number,
) {
  const padL = Math.min(128, Math.max(72, w * 0.16));
  const padR = 16;
  const padT = 12;
  const padB = 16;
  const rows = scene.objects.length;
  const rw = Math.max(1, w - padL - padR);
  const rh = (h - padT - padB) / rows;
  const kMax = Math.max(0, Math.min(scene.N, Math.round(scene.N * until)));
  const cw = rw / scene.N;

  ctx.font = "11px 'Geist Mono Variable', ui-monospace, monospace";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#86868b";
  scene.objects.forEach((object, i) => {
    const y = padT + i * rh + rh / 2;
    ctx.fillText(`${object.cat} ${object.id}`, 10, y);
  });

  for (let i = 0; i < rows; i++) {
    for (let k = 0; k < kMax; k += 2) {
      const row = scene.frames[k]?.objs[i];
      if (!row?.coop) continue;
      ctx.fillStyle = BAND[row.coop];
      ctx.fillRect(padL + k * cw, padT + i * rh + rh * 0.22, Math.max(1.2, cw * 2), rh * 0.56);
    }
  }

  if (until > 0) {
    const x = padL + Math.min(1, until) * rw;
    ctx.fillStyle = "#f5f5f7";
    ctx.fillRect(x - 0.5, padT, 1, h - padT - padB);
  }
}

/** Teal is drone-only sight in the illustrative scene. No numeric score is drawn. */
export function drawAddedBand(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scene: BackdropScene,
  until: number,
) {
  const counts: number[] = [];
  let peak = 1;
  for (let k = 0; k < scene.N; k += 2) {
    let n = 0;
    for (const row of scene.frames[k].objs) {
      if (row.coop === "drone") n += 1;
    }
    counts.push(n);
    if (n > peak) peak = n;
  }
  ctx.fillStyle = "rgba(125, 206, 160, 0.35)";
  ctx.fillRect(0, h - 2, w, 2);
  const kMax = Math.round(counts.length * until);
  const step = w / counts.length;
  ctx.beginPath();
  ctx.moveTo(0, h);
  for (let i = 0; i < kMax; i++) {
    const x = i * step;
    const y = h - (counts[i] / peak) * (h - 8);
    if (i === 0) ctx.lineTo(x, y);
    else ctx.lineTo(x, y);
  }
  if (kMax > 0) {
    ctx.lineTo((kMax - 1) * step, h);
    ctx.closePath();
    ctx.fillStyle = "rgba(31, 163, 163, 0.85)";
    ctx.fill();
  }
}
