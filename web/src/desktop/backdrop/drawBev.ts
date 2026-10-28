// SPDX-License-Identifier: Apache-2.0
// Ported and adapted from "viewer-engine.js" by Adarsh (Coperceive UI Design Options, 2026-10-18).
// Canvas 2D Bird's-Eye View (BEV) rendering for the desktop Connect screen backdrop.

import type { BackdropScene, ObjectState, SceneObject } from "./scene";

export interface BevRenderOptions {
  scale?: number;
  showRibbon?: boolean;
  drift?: boolean;
  alpha?: number;
}

const COLOR = {
  tp: "#1FA3A3",
  fp: "#E0A431",
  miss: "#8E6CEF",
  veh: "#F58A1F",
  uav: "#3C8DFF",
};

const EDGES_BEV: [number, number][] = [
  [4, 5],
  [5, 6],
  [6, 7],
  [7, 4],
];

function corners(p: { x: number; y: number; h: number }, o: { l: number; w: number; h: number }): [number, number, number][] {
  const c = Math.cos(p.h);
  const s = Math.sin(p.h);
  const out: [number, number, number][] = [];
  for (const z of [0, o.h]) {
    for (const [a, b] of [[1, 1], [1, -1], [-1, -1], [-1, 1]]) {
      const lx = (a * o.l) / 2;
      const ly = (b * o.w) / 2;
      out.push([p.x + lx * c - ly * s, p.y + lx * s + ly * c, z]);
    }
  }
  return out;
}

export function drawBevScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scene: BackdropScene,
  t: number,
  options: BevRenderOptions = {},
) {
  const scale = options.scale ?? 6.2;
  const alpha = options.alpha ?? 1.0;
  const drift = options.drift ?? true;

  const st = scene.stateAt(t);
  const { V, U } = st;

  ctx.save();
  ctx.globalAlpha = alpha;

  // Center camera between vehicle and UAV with gentle lookahead
  const cx = (V.x + U.x) / 2 + 6;
  const cy = (V.y + U.y) / 2;

  // Optional gentle camera drift (±3° rotation over the 60s loop)
  const driftAngle = drift ? Math.sin((t / scene.T_END) * Math.PI * 2) * (3 * Math.PI / 180) : 0;

  const transformX = (x: number, y: number): [number, number] => {
    let dx = x - cx;
    let dy = y - cy;
    if (driftAngle !== 0) {
      const cosA = Math.cos(driftAngle);
      const sinA = Math.sin(driftAngle);
      const rx = dx * cosA - dy * sinA;
      const ry = dx * sinA + dy * cosA;
      dx = rx;
      dy = ry;
    }
    return [w / 2 + dx * scale, h / 2 - dy * scale];
  };

  // 1. Road band fill
  ctx.fillStyle = "rgba(244, 244, 237, 0.04)";
  ctx.beginPath();
  let first = true;
  for (let s = V.s - 80; s < V.s + 120; s += 3) {
    const p = scene.along(s, -3.6);
    const q = transformX(p.x, p.y);
    if (first) {
      ctx.moveTo(q[0], q[1]);
      first = false;
    } else {
      ctx.lineTo(q[0], q[1]);
    }
  }
  for (let s = V.s + 120; s > V.s - 80; s -= 3) {
    const p = scene.along(s, 3.6);
    const q = transformX(p.x, p.y);
    ctx.lineTo(q[0], q[1]);
  }
  ctx.closePath();
  ctx.fill();

  // 2. Map lane lines
  ctx.lineWidth = 1;
  for (const lat of [-3.6, 3.6, 0]) {
    ctx.beginPath();
    let started = false;
    ctx.strokeStyle = lat === 0 ? "rgba(244, 244, 237, 0.18)" : "rgba(244, 244, 237, 0.28)";
    ctx.setLineDash(lat === 0 ? [6, 8] : []);
    for (let s = V.s - 70; s < V.s + 110; s += 2) {
      const p = scene.along(s, lat);
      const q = transformX(p.x, p.y);
      if (started) {
        ctx.lineTo(q[0], q[1]);
      } else {
        ctx.moveTo(q[0], q[1]);
        started = true;
      }
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // 3. Environment LiDAR points
  for (const pt of scene.env) {
    const dv = Math.hypot(pt[0] - V.x, pt[1] - V.y);
    const du = Math.hypot(pt[0] - U.x, pt[1] - U.y);
    const inV = dv < 48;
    const inU = du < 58;
    if (!inV && !inU) continue;

    const q = transformX(pt[0], pt[1]);
    if (q[0] < -10 || q[0] > w + 10 || q[1] < -10 || q[1] > h + 10) continue;

    ctx.fillStyle = inV ? "rgba(245, 138, 31, 0.45)" : "rgba(60, 141, 255, 0.45)";
    ctx.fillRect(q[0], q[1], 1.4, 1.4);
  }

  // 4. Dotted range rings
  ctx.setLineDash([3, 5]);
  // Vehicle 45m range ring
  {
    const q = transformX(V.x, V.y);
    ctx.strokeStyle = "rgba(245, 138, 31, 0.45)";
    ctx.beginPath();
    ctx.arc(q[0], q[1], 45 * scale, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "rgba(245, 138, 31, 0.03)";
    ctx.fill();
  }
  // UAV 55m range ring
  {
    const q = transformX(U.x, U.y);
    ctx.strokeStyle = "rgba(60, 141, 255, 0.45)";
    ctx.beginPath();
    ctx.arc(q[0], q[1], 55 * scale, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "rgba(60, 141, 255, 0.03)";
    ctx.fill();
  }
  ctx.setLineDash([]);

  // 5. Violet dotted blocked sight lines from vehicle to occluded objects
  st.objs.forEach((r: ObjectState) => {
    if (!r.occ) return;
    const a = transformX(V.x, V.y);
    const b = transformX(r.p.x, r.p.y);
    ctx.strokeStyle = "rgba(142, 108, 239, 0.65)";
    ctx.setLineDash([2, 4]);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    ctx.setLineDash([]);
  });

  // 6. Objects: points, boxes, and recovery ring
  st.objs.forEach((r: ObjectState) => {
    const o = r.o;
    const seenV = r.vVis;
    const seenU = r.dVis;
    const cs = corners(r.p, o);

    // Object surface LiDAR points
    for (const u of o.pts) {
      if (!seenV && !seenU) break;
      const c = Math.cos(r.p.h);
      const s = Math.sin(r.p.h);
      const lx = u[0] * o.l;
      const ly = u[1] * o.w;
      const q = transformX(r.p.x + lx * c - ly * s, r.p.y + lx * s + ly * c);
      ctx.fillStyle = seenV ? "rgba(255, 170, 90, 0.95)" : "rgba(120, 175, 255, 0.95)";
      ctx.fillRect(q[0], q[1], 2, 2);
    }

    if (!r.inRange) return;

    // Fused mode: vehicle hit is TP, drone hit is TP, miss is violet
    const isHit = r.vDet || r.dDet;
    const col = isHit ? COLOR.tp : COLOR.miss;
    const isMiss = !isHit;

    ctx.strokeStyle = col;
    ctx.lineWidth = 1.6;
    ctx.setLineDash(isMiss ? [5, 3] : []);
    ctx.beginPath();
    for (const [a, b] of EDGES_BEV) {
      const p1 = transformX(cs[a][0], cs[a][1]);
      const p2 = transformX(cs[b][0], cs[b][1]);
      ctx.moveTo(p1[0], p1[1]);
      ctx.lineTo(p2[0], p2[1]);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // Pulsing teal recovery ring (the cooperation moment)
    const recovered = r.coop === "drone";
    if (recovered) {
      const q = transformX(r.p.x, r.p.y);
      const rad = 3 * scale * (1 + 0.25 * Math.sin(t * 6));
      ctx.strokeStyle = "rgba(31, 163, 163, 0.6)";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(q[0], q[1], Math.max(6, rad), 0, Math.PI * 2);
      ctx.stroke();
    }
  });

  // 7. False Positives (amber dashed boxes)
  st.ghosts.forEach((g) => {
    const cs = corners(g, { l: 4.4, w: 1.8, h: 1.4 });
    ctx.strokeStyle = COLOR.fp;
    ctx.lineWidth = 1.3;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    for (const [a, b] of EDGES_BEV) {
      const p1 = transformX(cs[a][0], cs[a][1]);
      const p2 = transformX(cs[b][0], cs[b][1]);
      ctx.moveTo(p1[0], p1[1]);
      ctx.lineTo(p2[0], p2[1]);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  });

  // 8. Vehicle trail and body
  {
    // Vehicle Trail
    ctx.strokeStyle = "rgba(245, 138, 31, 0.5)";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    let started = false;
    for (let tt = Math.max(0, t - 6); tt <= t; tt += 0.25) {
      const a = scene.vehAt(tt);
      const q = transformX(a.x, a.y);
      if (started) {
        ctx.lineTo(q[0], q[1]);
      } else {
        ctx.moveTo(q[0], q[1]);
        started = true;
      }
    }
    ctx.stroke();

    // Vehicle Body
    const cs = corners(V, { l: 4.6, w: 1.9, h: 1.5 });
    ctx.fillStyle = "rgba(245, 138, 31, 0.4)";
    ctx.strokeStyle = COLOR.veh;
    ctx.lineWidth = 2.0;
    ctx.beginPath();
    const top = [0, 1, 2, 3].map((i) => transformX(cs[i][0], cs[i][1]));
    top.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  // 9. Drone trail and glyph with 4 rotors
  {
    // UAV Trail
    ctx.strokeStyle = "rgba(60, 141, 255, 0.5)";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    let started = false;
    for (let tt = Math.max(0, t - 6); tt <= t; tt += 0.25) {
      const a = scene.uavAt(tt);
      const q = transformX(a.x, a.y);
      if (started) {
        ctx.lineTo(q[0], q[1]);
      } else {
        ctx.moveTo(q[0], q[1]);
        started = true;
      }
    }
    ctx.stroke();

    // UAV Glyph
    const q = transformX(U.x, U.y);
    ctx.fillStyle = COLOR.uav;
    ctx.strokeStyle = "#000000";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(q[0], q[1], 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // 4 Rotors
    ctx.strokeStyle = COLOR.uav;
    ctx.lineWidth = 1.5;
    for (const a of [0.8, 2.35, 3.9, 5.5]) {
      ctx.beginPath();
      ctx.arc(q[0] + Math.cos(a) * 9, q[1] + Math.sin(a) * 9, 3.2, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  ctx.restore();

  // 10. Optional bottom ledger strip (drawRibbon) when enabled and height >= 700px
  if (options.showRibbon && h >= 700) {
    drawRibbonStrip(ctx, w, h, scene, t);
  }
}

export function drawRibbonStrip(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scene: BackdropScene,
  t: number,
) {
  const ribbonHeight = 36;
  const ribbonY = h - ribbonHeight - 12;
  const padX = 24;
  const rw = w - padX * 2;
  if (rw <= 50) return;

  const rows = scene.objects.length;
  const rh = ribbonHeight / rows;
  const cw = rw / scene.N;
  const col: Record<string, string> = {
    both: COLOR.tp,
    vehicle: COLOR.veh,
    drone: COLOR.uav,
    nobody: COLOR.miss,
  };

  ctx.save();
  ctx.globalAlpha = 0.55;

  // Background glass bar
  ctx.fillStyle = "rgba(20, 20, 22, 0.7)";
  ctx.beginPath();
  ctx.roundRect(padX - 4, ribbonY - 4, rw + 8, ribbonHeight + 8, 6);
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctx.lineWidth = 1;
  ctx.stroke();

  scene.objects.forEach((_o: SceneObject, i: number) => {
    for (let k = 0; k < scene.N; k += 2) {
      const r = scene.frames[k]?.objs[i];
      if (!r || !r.coop) continue;
      ctx.fillStyle = col[r.coop] || COLOR.miss;
      ctx.fillRect(padX + k * cw, ribbonY + i * rh + rh * 0.2, cw * 2, rh * 0.6);
    }
  });

  // Playhead line
  const curX = padX + (t / scene.T_END) * rw;
  ctx.fillStyle = "#de3b21";
  ctx.fillRect(curX - 1, ribbonY, 2, ribbonHeight);

  ctx.restore();
}
