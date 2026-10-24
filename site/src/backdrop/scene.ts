// Landing-page copy of web/src/desktop/backdrop/scene.ts
// on feat/desktop at bf36d0d (file bytes unchanged through 89fa0aa).
// Owned by site/ so this page does not import the unmerged desktop branch.
// Unify with web/src/desktop/backdrop after feat/desktop merges.

// SPDX-License-Identifier: Apache-2.0
// Ported and adapted from the desktop viewer backdrop.
// Extended with a 3-chapter parametric 60-second loop and seamless crossfade.

export type Point3D = [number, number, number];

export interface Pose2D {
  s: number;
  x: number;
  y: number;
  z: number;
  h: number;
}

export type ObjectCategory = "car" | "truck" | "bus" | "pedestrian" | "cyclist" | "barrier";

export interface SceneObject {
  id: number;
  chapter: number;
  cat: ObjectCategory;
  s0: number;
  lat: number;
  v: number;
  l: number;
  w: number;
  h: number;
  pts: Point3D[];
}

export type CoopStatus = "both" | "vehicle" | "drone" | "nobody";
export type DetectionStatus = "tp" | "miss";

export interface ObjectState {
  o: SceneObject;
  p: { x: number; y: number; h: number };
  dv: number;
  du: number;
  inRange: boolean;
  occ: SceneObject | null;
  vVis: boolean;
  dVis: boolean;
  vDet: boolean;
  dDet: boolean;
  coop: CoopStatus | null;
  sV: DetectionStatus | null;
  sF: DetectionStatus | null;
  score: number;
}

export interface GhostState {
  x: number;
  y: number;
  h: number;
  fused: boolean;
  score: number;
}

export interface SceneFrame {
  k: number;
  t: number;
  chapter: number;
  V: Pose2D;
  U: Pose2D;
  P: Pose2D;
  objs: ObjectState[];
  ghosts: GhostState[];
}

export interface BackdropScene {
  N: number;
  T_END: number;
  BLEND_WINDOW: number;
  frames: SceneFrame[];
  objects: SceneObject[];
  env: Point3D[];
  along: (s: number, lat: number) => { x: number; y: number; h: number };
  vehAt: (t: number) => Pose2D;
  partnerAt: (t: number) => Pose2D;
  uavAt: (t: number) => Pose2D;
  objAt: (o: SceneObject, t: number) => { x: number; y: number; h: number };
  stateAt: (t: number) => {
    V: Pose2D;
    U: Pose2D;
    P: Pose2D;
    objs: ObjectState[];
    ghosts: GhostState[];
    k: number;
  };
}

export function createPrng(seed: number) {
  let s = (seed >>> 0) || 0x12345678;
  return function next(): number {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function crossfadeWeights(t: number, totalDuration: number = 60.0, blendWindow: number = 1.5): {
  t1: number;
  t2: number;
  w1: number;
  w2: number;
  isBlending: boolean;
} {
  const modT = ((t % totalDuration) + totalDuration) % totalDuration;
  const blendStart = totalDuration - blendWindow;
  if (modT < blendStart) {
    return { t1: modT, t2: 0, w1: 1.0, w2: 0.0, isBlending: false };
  }
  const progress = (modT - blendStart) / blendWindow;
  const clamped = Math.max(0.0, Math.min(1.0, progress));
  return {
    t1: modT,
    t2: clamped * blendWindow,
    w1: 1.0 - clamped,
    w2: clamped,
    isBlending: true,
  };
}

export function alongPath(s: number, lat: number): { x: number; y: number; h: number } {
  const y = 14 * Math.sin(s / 55);
  const h = Math.atan2((14 / 55) * Math.cos(s / 55), 1);
  return {
    x: s - Math.sin(h) * lat,
    y: y + Math.cos(h) * lat,
    h,
  };
}

export function createBackdropScene(seed: number = 42): BackdropScene {
  const N = 600; // 60 s @ 10 Hz
  const T_END = 60.0;
  const BLEND_WINDOW = 1.5;

  function hash(a: number, b: number): number {
    const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453;
    return x - Math.floor(x);
  }

  function vehAt(t: number): Pose2D {
    const s = 5 + 7.5 * t;
    const p = alongPath(s, -1.75);
    return { s, x: p.x, y: p.y, z: 0, h: p.h };
  }

  function partnerAt(t: number): Pose2D {
    const s = 28 + 6.4 * t;
    const p = alongPath(s, 1.75);
    return { s, x: p.x, y: p.y, z: 0, h: p.h };
  }

  function uavAt(t: number): Pose2D {
    const s = 19 + 7.5 * t;
    const p = alongPath(s, 4);
    return { s, x: p.x, y: p.y, z: 24, h: p.h };
  }

  // 3 Chapters of Cooperation Stories:
  // Chapter 1 [0s - 20s]: Bus occlusion (parked bus occludes 3 pedestrians)
  // Chapter 2 [20s - 40s]: Crossing pedestrian group spotted by drone ahead
  // Chapter 3 [40s - 60s]: Oncoming truck occluding a cyclist
  const objects: SceneObject[] = [
    // Chapter 1: The Design Reference Bus Occlusion
    { id: 1, chapter: 1, cat: "car", s0: 22, lat: -1.75, v: 6.6, l: 4.6, w: 1.9, h: 1.5, pts: [] },
    { id: 2, chapter: 1, cat: "car", s0: 120, lat: 1.75, v: -8, l: 4.6, w: 1.9, h: 1.5, pts: [] },
    { id: 3, chapter: 1, cat: "truck", s0: 200, lat: 1.75, v: -6.5, l: 8, w: 2.5, h: 3.2, pts: [] },
    { id: 4, chapter: 1, cat: "bus", s0: 96, lat: -5.4, v: 0, l: 12, w: 2.6, h: 3.2, pts: [] },
    { id: 5, chapter: 1, cat: "pedestrian", s0: 93, lat: -8.2, v: 0.35, l: 0.6, w: 0.6, h: 1.75, pts: [] },
    { id: 6, chapter: 1, cat: "pedestrian", s0: 97, lat: -8.8, v: 0.25, l: 0.6, w: 0.6, h: 1.7, pts: [] },
    { id: 7, chapter: 1, cat: "pedestrian", s0: 100.5, lat: -8, v: -0.3, l: 0.6, w: 0.6, h: 1.8, pts: [] },
    { id: 8, chapter: 1, cat: "cyclist", s0: 58, lat: -4, v: 3.8, l: 1.8, w: 0.6, h: 1.6, pts: [] },
    { id: 9, chapter: 1, cat: "car", s0: 118, lat: 5.6, v: 0, l: 4.6, w: 1.9, h: 1.5, pts: [] },
    { id: 10, chapter: 1, cat: "barrier", s0: 78, lat: 4.6, v: 0, l: 2, w: 0.4, h: 1, pts: [] },

    // Chapter 2: Crossing Pedestrian Group Seen First by Drone [20s - 40s] (s around 170-320m)
    { id: 11, chapter: 2, cat: "car", s0: 190, lat: -1.75, v: 7.2, l: 4.6, w: 1.9, h: 1.5, pts: [] },
    { id: 12, chapter: 2, cat: "barrier", s0: 240, lat: -4.8, v: 0, l: 8, w: 0.5, h: 1.6, pts: [] },
    { id: 13, chapter: 2, cat: "pedestrian", s0: 242, lat: -7.5, v: 0.5, l: 0.6, w: 0.6, h: 1.75, pts: [] },
    { id: 14, chapter: 2, cat: "pedestrian", s0: 244, lat: -8.2, v: 0.45, l: 0.6, w: 0.6, h: 1.7, pts: [] },
    { id: 15, chapter: 2, cat: "pedestrian", s0: 246, lat: -7.8, v: 0.4, l: 0.6, w: 0.6, h: 1.8, pts: [] },
    { id: 16, chapter: 2, cat: "car", s0: 275, lat: 1.75, v: -7.5, l: 4.8, w: 2.0, h: 1.5, pts: [] },

    // Chapter 3: Oncoming Truck Occluding a Cyclist [40s - 60s] (s around 320-470m)
    { id: 17, chapter: 3, cat: "truck", s0: 450, lat: 1.75, v: -6.8, l: 9.5, w: 2.6, h: 3.4, pts: [] },
    { id: 18, chapter: 3, cat: "cyclist", s0: 380, lat: 4.2, v: -3.5, l: 1.8, w: 0.6, h: 1.6, pts: [] },
    { id: 19, chapter: 3, cat: "car", s0: 330, lat: -1.75, v: 7.0, l: 4.6, w: 1.9, h: 1.5, pts: [] },
    { id: 20, chapter: 3, cat: "barrier", s0: 395, lat: -4.5, v: 0, l: 3.5, w: 0.4, h: 1.0, pts: [] },
  ];

  // Precompute surface point cloud for objects
  objects.forEach((o) => {
    o.pts = [];
    for (let i = 0; i < 26; i++) {
      o.pts.push([
        hash(o.id + seed, i) - 0.5,
        hash(o.id + seed, i + 50) - 0.5,
        hash(o.id + seed, i + 99),
      ]);
    }
  });

  function objAt(o: SceneObject, t: number): { x: number; y: number; h: number } {
    const s = o.s0 + o.v * t;
    const p = alongPath(s, o.lat);
    return { x: p.x, y: p.y, h: p.h + (o.v < 0 ? Math.PI : 0) };
  }

  function occluder(from: Pose2D, to: { x: number; y: number }, self: SceneObject, t: number): SceneObject | null {
    for (const o of objects) {
      if (o === self || (o.cat !== "bus" && o.cat !== "truck" && o.cat !== "barrier")) continue;
      const q = objAt(o, t);
      const c = Math.cos(-q.h);
      const sn = Math.sin(-q.h);
      for (let j = 2; j < 30; j++) {
        const f = j / 31;
        const x = from.x + (to.x - from.x) * f - q.x;
        const y = from.y + (to.y - from.y) * f - q.y;
        const lx = x * c - y * sn;
        const ly = x * sn + y * c;
        if (Math.abs(lx) < o.l / 2 && Math.abs(ly) < o.w / 2) {
          return o;
        }
      }
    }
    return null;
  }

  // Pre-generate environment points along road
  const env: Point3D[] = [];
  for (let s = -40; s < 520; s += 1.4) {
    for (const side of [-1, 1]) {
      const lat = side * (12.5 + hash(s, side) * 2.5);
      const p = alongPath(s, lat);
      for (let z = 0; z < 4; z++) {
        env.push([p.x, p.y, hash(s, z + side) * 7]);
      }
      const e = alongPath(s, side * 3.7);
      env.push([e.x, e.y, 0.05]);
    }
    for (let i = 0; i < 5; i++) {
      const p = alongPath(s + hash(s, i) * 2, -11 + hash(i, s) * 22);
      env.push([p.x, p.y, 0]);
    }
  }

  const frames: SceneFrame[] = [];
  for (let k = 0; k < N; k++) {
    const t = k / 10;
    const chapter = t < 20 ? 1 : t < 40 ? 2 : 3;
    const V = vehAt(t);
    const U = uavAt(t);
    const P = partnerAt(t);

    const objs: ObjectState[] = objects.map((o) => {
      const p = objAt(o, t);
      const dv = Math.hypot(p.x - V.x, p.y - V.y);
      const du = Math.hypot(p.x - U.x, p.y - U.y);
      const inRange = dv <= 45;
      const occ = inRange ? occluder(V, p, o, t) : null;
      const vVis = inRange && !occ;
      const dVis = du <= 55;
      const b = Math.floor(k / 3);
      const vDet = vVis && hash(o.id, b) < (dv > 32 ? 0.72 : 0.95);
      const dDet = dVis && hash(o.id + 40, b) < 0.93;
      let coop: CoopStatus | null = null;
      if (inRange) {
        coop = vDet && dDet ? "both" : vDet ? "vehicle" : dDet ? "drone" : "nobody";
      }
      return {
        o,
        p,
        dv,
        du,
        inRange,
        occ,
        vVis,
        dVis,
        vDet,
        dDet,
        coop,
        sV: inRange ? (vDet ? "tp" : "miss") : null,
        sF: inRange ? (vDet || dDet ? "tp" : "miss") : null,
        score: 0.45 + hash(o.id, k) * 0.5,
      };
    });

    const bl = Math.floor(k / 7);
    const ghosts: GhostState[] = [];
    if (hash(bl, 9) < 0.5) {
      const g = alongPath(V.s + 12 + hash(bl, 3) * 26, -9 + hash(bl, 4) * 18);
      ghosts.push({
        x: g.x,
        y: g.y,
        h: g.h,
        fused: hash(bl, 5) < 0.45,
        score: 0.31 + hash(bl, 6) * 0.3,
      });
    }

    frames.push({ k, t, chapter, V, U, P, objs, ghosts });
  }

  function stateAt(t: number) {
    const clampedT = Math.max(0, Math.min(T_END - 0.05, t));
    const k = Math.min(N - 1, Math.floor(clampedT * 10));
    const f = frames[k];
    return {
      k,
      V: vehAt(t),
      U: uavAt(t),
      P: partnerAt(t),
      objs: f.objs.map((r) => ({ ...r, p: objAt(r.o, t) })),
      ghosts: f.ghosts,
    };
  }

  return {
    N,
    T_END,
    BLEND_WINDOW,
    frames,
    objects,
    env,
    along: alongPath,
    vehAt,
    partnerAt,
    uavAt,
    objAt,
    stateAt,
  };
}
