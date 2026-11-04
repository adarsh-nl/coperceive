// SPDX-License-Identifier: Apache-2.0
/**
 * Small, pure figures for the viewer's panels: counts as people read them, the
 * boxes each camera sees, the samples without a lidar sweep, the timeline's
 * histogram and the stage's scale bar. Everything here is computed from the
 * scene's own data; nothing is estimated.
 */
import type { FrameBox, FrameFrustum, Pose } from "./types.ts";

/** The box centre (as `inspect.ts` reads it), without importing three.js-free code paths twice. */
function boxCenter(box: FrameBox): [number, number, number] | null {
  const c = box.center;
  if (c && c.length === 3 && c.every((v) => typeof v === "number" && Number.isFinite(v))) {
    return [c[0] as number, c[1] as number, c[2] as number];
  }
  const p = box.pose;
  if (p && [p.tx, p.ty, p.tz].every((v) => typeof v === "number" && Number.isFinite(v))) {
    return [p.tx, p.ty, p.tz];
  }
  return null;
}

/** 980, 70.1k, 1.2M: short enough for a panel row. */
export function compactCount(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    return "—";
  }
  if (value < 1000) {
    return String(Math.round(value));
  }
  if (value < 1_000_000) {
    const k = value / 1000;
    return `${k < 100 ? k.toFixed(1) : Math.round(k)}k`;
  }
  const m = value / 1_000_000;
  return `${m < 100 ? m.toFixed(1) : Math.round(m)}M`;
}

const ROLE: Record<string, string> = {
  vehicle: "Vehicle",
  uav: "Drone",
  infrastructure: "Roadside unit",
  rsu: "Roadside unit",
};

/** The agent's role as the panel words it, from its recorded type. */
export function agentRole(agentType: string): string {
  return ROLE[agentType] ?? (agentType ? agentType.charAt(0).toUpperCase() + agentType.slice(1) : "Agent");
}

/** World point to camera pixel, or null when it is behind the camera. */
function project(pose: Pose, fx: number, fy: number, cx: number, cy: number, point: [number, number, number]) {
  // Rotate the offset by the inverse of the camera quaternion (q* v q).
  const vx = point[0] - pose.tx;
  const vy = point[1] - pose.ty;
  const vz = point[2] - pose.tz;
  const qx = -pose.qx;
  const qy = -pose.qy;
  const qz = -pose.qz;
  const qw = pose.qw;
  const tx = 2 * (qy * vz - qz * vy);
  const ty = 2 * (qz * vx - qx * vz);
  const tz = 2 * (qx * vy - qy * vx);
  const x = vx + qw * tx + (qy * tz - qz * ty);
  const y = vy + qw * ty + (qz * tx - qx * tz);
  const z = vz + qw * tz + (qx * ty - qy * tx);
  if (z <= 0) {
    return null;
  }
  return { u: (fx * x) / z + cx, v: (fy * y) / z + cy };
}

/**
 * How many shown boxes have their centre inside this camera's image, or null
 * when the camera's intrinsics or image size are not recorded.
 */
export function boxesInView(
  camera: FrameFrustum,
  boxes: readonly FrameBox[],
  hidden: Readonly<Record<string, boolean>> = {},
): number | null {
  const { fx, fy, cx, cy, image_width_px: width, image_height_px: height, pose } = camera;
  if (
    typeof fx !== "number" ||
    typeof fy !== "number" ||
    typeof cx !== "number" ||
    typeof cy !== "number" ||
    typeof width !== "number" ||
    typeof height !== "number" ||
    width <= 0 ||
    height <= 0
  ) {
    return null;
  }
  let count = 0;
  for (const box of boxes) {
    if (hidden[box.agent_id]) {
      continue;
    }
    const center = boxCenter(box);
    if (!center) {
      continue;
    }
    const pixel = project(pose, fx, fy, cx, cy, center);
    if (pixel && pixel.u >= 0 && pixel.v >= 0 && pixel.u < width && pixel.v < height) {
      count += 1;
    }
  }
  return count;
}

/**
 * Samples where the scene has lidar but no sweep arrived: `sweeps[i]` is the
 * number of point sensors that reported at sample i (null while unread).
 */
export function sweepGaps(sweeps: readonly (number | null)[], sceneHasLidar: boolean): number[] {
  if (!sceneHasLidar) {
    return [];
  }
  const gaps: number[] = [];
  sweeps.forEach((value, index) => {
    if (value === 0) {
      gaps.push(index);
    }
  });
  return gaps;
}

/** At most `bins` bars, each the largest value of the samples it covers. */
export function histogram(values: readonly number[], bins: number): { bars: number[]; max: number } {
  if (values.length === 0 || bins <= 0) {
    return { bars: [], max: 0 };
  }
  const count = Math.min(bins, values.length);
  const bars: number[] = [];
  for (let bin = 0; bin < count; bin += 1) {
    const start = Math.floor((bin * values.length) / count);
    const end = Math.max(start + 1, Math.floor(((bin + 1) * values.length) / count));
    let peak = 0;
    for (let index = start; index < end; index += 1) {
      const value = values[index] ?? 0;
      if (Number.isFinite(value) && value > peak) {
        peak = value;
      }
    }
    bars.push(peak);
  }
  return { bars, max: bars.reduce((max, value) => Math.max(max, value), 0) };
}

/** The bar for a scale: the longest 1, 2 or 5 × 10^k metres that fits in `maxPx`. */
export function scaleBar(metresPerPixel: number, maxPx: number): { metres: number; px: number } | null {
  if (!Number.isFinite(metresPerPixel) || metresPerPixel <= 0 || maxPx <= 0) {
    return null;
  }
  const most = metresPerPixel * maxPx;
  let pick = 0;
  for (let power = -2; power <= 5; power += 1) {
    for (const lead of [1, 2, 5]) {
      const metres = lead * 10 ** power;
      if (metres <= most) {
        pick = metres;
      }
    }
  }
  if (pick <= 0) {
    return null;
  }
  return { metres: pick, px: pick / metresPerPixel };
}

/** "10 m", "500 m", "1.2 km", "0.5 m". */
export function formatDistance(metres: number): string {
  if (metres >= 1000) {
    const km = metres / 1000;
    return `${Number.isInteger(km) ? km : km.toFixed(1)} km`;
  }
  return `${metres >= 10 || Number.isInteger(metres) ? Math.round(metres) : metres.toFixed(1)} m`;
}

/**
 * The agents with cameras in the order they appear, and the one the strip shows:
 * the chosen agent when it still has cameras, else the first.
 */
export function stripAgents(
  frustums: readonly FrameFrustum[],
  chosen: string | null,
): { agents: string[]; current: string | null; next: string | null } {
  const agents: string[] = [];
  for (const camera of frustums) {
    if (!agents.includes(camera.agent_id)) {
      agents.push(camera.agent_id);
    }
  }
  const current = chosen && agents.includes(chosen) ? chosen : (agents[0] ?? null);
  const at = current === null ? -1 : agents.indexOf(current);
  const next = agents.length > 1 ? (agents[(at + 1) % agents.length] ?? null) : null;
  return { agents, current, next };
}
