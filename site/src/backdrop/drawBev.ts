// Landing-page copy of web/src/desktop/backdrop/drawBev.ts
// on feat/desktop at bf36d0d (file bytes unchanged through 89fa0aa).
// Owned by site/ so this page does not import the unmerged desktop branch.
// Unify with web/src/desktop/backdrop after feat/desktop merges.
// Optional BevRenderOptions fields below are landing-only. Defaults match the desktop port.

// SPDX-License-Identifier: Apache-2.0
// Ported and adapted from "viewer-engine.js" by Adarsh (Coperceive UI Design Options, 2026-10-18).
// Canvas 2D Bird's-Eye View (BEV) rendering for the desktop Connect screen backdrop.

import type { BackdropScene, ObjectState, SceneObject } from "./scene";

export interface BevRenderOptions {
  scale?: number;
  showRibbon?: boolean;
  drift?: boolean;
  alpha?: number;
  /** 0..1 sweep of the vehicle range ring. Default 1. */
  vehicleRing?: number;
  /** 0..1 sweep of the drone range ring. Default 1. */
  droneRing?: number;
  showVehicle?: boolean;
  showDrone?: boolean;
  /** 0..1. Drone-only hits stay misses until this passes revealGate(id). */
  droneReveal?: number;
  showSightLines?: boolean;
  showGhosts?: boolean;
  /** fused uses both agents. vehicle and drone colour only that agent’s hits. */
  detection?: "fused" | "vehicle" | "drone";
  follow?: "both" | "vehicle" | "drone";
  lidar?: "both" | "vehicle" | "drone" | "none";
  /** Seconds for the teal pulse. Defaults to the scene clock. */
  pulseClock?: number;
  /** Fraction of ledger samples drawn from the left. Default 1. */
  ribbonUntil?: number;
}

/** Landing scroll story: which drone-only misses have flipped to hits. */
export function revealGate(id: number): number {
  return ((id * 17) % 100) / 100;
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
  const vehicleRing = options.vehicleRing ?? 1;
  const droneRing = options.droneRing ?? 1;
  const showVehicle = options.showVehicle ?? true;
  const showDrone = options.showDrone ?? true;
  const droneReveal = options.droneReveal ?? 1;
  const showSightLines = options.showSightLines ?? true;
  const showGhosts = options.showGhosts ?? true;
  const detection = options.detection ?? "fused";
  const follow = options.follow ?? "both";
  const lidar = options.lidar ?? "both";
  const pulseClock = options.pulseClock ?? t;
  const allowV = lidar === "both" || lidar === "vehicle";
  const allowU = lidar === "both" || lidar === "drone";

  const st = scene.stateAt(t);
  const { V, U } = st;

  const hitOf = (r: ObjectState): boolean => {
    if (detection === "vehicle") return r.vDet;
    if (detection === "drone") return r.dDet;
    return r.vDet || (r.dDet && droneReveal >= revealGate(r.o.id));
  };

  ctx.save();
  ctx.globalAlpha = alpha;

  const cx = follow === "vehicle" ? V.x + 4 : follow === "drone" ? U.x : (V.x + U.x) / 2 + 6;
  const cy = follow === "vehicle" ? V.y : follow === "drone" ? U.y : (V.y + U.y) / 2;

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
    ctx.strokeStyle = lat === 0 ? "rgba(125, 206, 160, 0.28)" : "rgba(125, 206, 160, 0.55)";
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
    const inV = allowV && dv < 48;
    const inU = allowU && du < 58;
    if (!inV && !inU) continue;

    const q = transformX(pt[0], pt[1]);
    if (q[0] < -10 || q[0] > w + 10 || q[1] < -10 || q[1] > h + 10) continue;

    ctx.fillStyle = inV ? "rgba(245, 138, 31, 0.45)" : "rgba(60, 141, 255, 0.45)";
    ctx.fillRect(q[0], q[1], 1.4, 1.4);
  }

  // 4. Dotted range rings. Portion 0..1 draws the arc from 12 o'clock.
  ctx.setLineDash([3, 5]);
  if (showVehicle && vehicleRing > 0) {
    const q = transformX(V.x, V.y);
    ctx.strokeStyle = "rgba(245, 138, 31, 0.45)";
    ctx.beginPath();
    ctx.arc(q[0], q[1], 45 * scale, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, vehicleRing));
    ctx.stroke();
  }
  if (showDrone && droneRing > 0) {
    const q = transformX(U.x, U.y);
    ctx.strokeStyle = "rgba(60, 141, 255, 0.45)";
    ctx.beginPath();
    ctx.arc(q[0], q[1], 55 * scale, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, droneRing));
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // 5. Violet dotted blocked sight lines from vehicle to occluded objects
  st.objs.forEach((r: ObjectState) => {
    if (!showSightLines || !showVehicle || !r.occ || hitOf(r)) return;
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
    const seenV = allowV && r.vVis && detection !== "drone";
    const seenU = allowU && r.dVis && detection !== "vehicle";
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

    const isHit = hitOf(r);
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
    const recovered = detection !== "vehicle" && !r.vDet && r.dDet && isHit;
    if (recovered) {
      const q = transformX(r.p.x, r.p.y);
      const rad = 3 * scale * (1 + 0.25 * Math.sin(pulseClock * 6));
      ctx.strokeStyle = "rgba(31, 163, 163, 0.6)";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(q[0], q[1], Math.max(6, rad), 0, Math.PI * 2);
      ctx.stroke();
    }
  });

  // 7. False Positives (amber dashed boxes)
  if (showGhosts) st.ghosts.forEach((g) => {
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
  if (showVehicle) {
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
  if (showDrone) {
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
    drawRibbonStrip(ctx, w, h, scene, t, options.ribbonUntil ?? 1);
  }
}

export function drawRibbonStrip(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scene: BackdropScene,
  t: number,
  until = 1,
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
    const kMax = Math.max(0, Math.min(scene.N, Math.round(scene.N * until)));
    for (let k = 0; k < kMax; k += 2) {
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
