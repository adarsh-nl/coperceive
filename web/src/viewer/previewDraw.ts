// SPDX-License-Identifier: Apache-2.0
/**
 * One small scene, drawn for real into a canvas for each viewer style.
 * The boxes and points are the same; the style changes colour, type of line
 * and camera. Cinema adds a slow yaw when drift is on.
 */
import { VIEWER_STYLE_TOKENS, type ViewerStyle } from "./viewerStyle.ts";

export type PreviewBox = {
  kind: "both" | "ego" | "other";
  x: number;
  y: number;
  z: number;
  length: number;
  width: number;
  height: number;
};

/** The scene every card draws. Not a dataset frame. */
export const PREVIEW_BOXES: readonly PreviewBox[] = [
  { kind: "ego", x: -6, y: 2, z: 0.8, length: 4.2, width: 1.8, height: 1.5 },
  { kind: "both", x: 4, y: -1, z: 0.9, length: 4.6, width: 1.9, height: 1.6 },
  { kind: "other", x: 12, y: 6, z: 0.8, length: 4.4, width: 1.8, height: 1.5 },
  { kind: "both", x: 1, y: 8, z: 0.7, length: 3.8, width: 1.7, height: 1.4 },
];

export const PREVIEW_POINTS: readonly [number, number, number][] = [
  [-8, -2, 0.2],
  [-2, 0, 0.3],
  [2, 3, 0.2],
  [6, -4, 0.25],
  [10, 2, 0.2],
  [0, -6, 0.15],
  [8, 7, 0.3],
  [-4, 6, 0.2],
];

const CAMERA: Record<ViewerStyle, { yaw: number; pitch: number; dist: number; fov: number }> = {
  classic: { yaw: -0.8, pitch: 0.55, dist: 28, fov: 55 },
  studio: { yaw: -0.8, pitch: 0.6, dist: 24, fov: 50 },
  instrument: { yaw: -0.95, pitch: 0.48, dist: 30, fov: 48 },
  cinema: { yaw: -0.7, pitch: 0.42, dist: 34, fov: 42 },
};

export interface PreviewCtx {
  fillStyle: string | CanvasGradient | CanvasPattern;
  strokeStyle: string | CanvasGradient | CanvasPattern;
  lineWidth: number;
  setLineDash(segments: number[]): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  stroke(): void;
}

export type PreviewOptions = {
  /** Frame index. Cinema drift adds yaw from this when drift is on. */
  frame?: number;
  drift?: boolean;
};

/** Screen position of the shared "both" box centre. Tests compare styles with it. */
export function previewBothPoint(style: ViewerStyle, width: number, height: number, options: PreviewOptions = {}): { x: number; y: number } {
  const camera = cameraFor(style, options);
  const box = PREVIEW_BOXES.find((item) => item.kind === "both")!;
  return project(camera, box.x, box.y, box.z, width, height);
}

export function renderStylePreview(ctx: PreviewCtx, width: number, height: number, style: ViewerStyle, options: PreviewOptions = {}): void {
  const tokens = VIEWER_STYLE_TOKENS[style];
  const camera = cameraFor(style, options);
  ctx.setLineDash([]);
  ctx.fillStyle = tokens.background;
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = style === "instrument" ? "rgba(90,255,220,0.35)" : "rgba(255,255,255,0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  const ground: [number, number][] = [
    [-16, -10],
    [20, -10],
    [20, 14],
    [-16, 14],
  ];
  ground.forEach(([x, y], index) => {
    const point = project(camera, x, y, 0, width, height);
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.stroke();
  ctx.fillStyle = tokens.seen.both;
  for (const [x, y, z] of PREVIEW_POINTS) {
    const point = project(camera, x, y, z, width, height);
    ctx.fillRect(point.x, point.y, 2, 2);
  }
  for (const box of PREVIEW_BOXES) {
    const color = tokens.seen[box.kind];
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash(box.kind === "ego" ? [5, 3] : box.kind === "other" ? [1.5, 3] : []);
    ctx.beginPath();
    const corners: [number, number, number][] = [
      [box.x - box.length / 2, box.y - box.width / 2, box.z],
      [box.x + box.length / 2, box.y - box.width / 2, box.z],
      [box.x + box.length / 2, box.y + box.width / 2, box.z],
      [box.x - box.length / 2, box.y + box.width / 2, box.z],
    ];
    corners.forEach((corner, index) => {
      const point = project(camera, corner[0], corner[1], corner[2], width, height);
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function cameraFor(style: ViewerStyle, options: PreviewOptions): { yaw: number; pitch: number; dist: number; fov: number } {
  const base = CAMERA[style];
  const drift = style === "cinema" && options.drift ? (options.frame ?? 0) * 0.02 : 0;
  return { ...base, yaw: base.yaw + drift };
}

function project(
  camera: { yaw: number; pitch: number; dist: number; fov: number },
  x: number,
  y: number,
  z: number,
  width: number,
  height: number,
): { x: number; y: number } {
  const eyeX = Math.cos(camera.yaw) * Math.cos(camera.pitch) * camera.dist;
  const eyeY = Math.sin(camera.yaw) * Math.cos(camera.pitch) * camera.dist;
  const eyeZ = Math.sin(camera.pitch) * camera.dist + 2;
  const dx = x - eyeX;
  const dy = y - eyeY;
  const dz = z - eyeZ;
  const forwardX = -eyeX;
  const forwardY = -eyeY;
  const forwardZ = 2 - eyeZ;
  const fl = Math.hypot(forwardX, forwardY, forwardZ) || 1;
  const fx = forwardX / fl;
  const fy = forwardY / fl;
  const fz = forwardZ / fl;
  const rx = -fy;
  const ry = fx;
  const rl = Math.hypot(rx, ry) || 1;
  const rightX = rx / rl;
  const rightY = ry / rl;
  const ux = fy * 0 - fz * rightY;
  const uy = fz * rightX - fx * 0;
  const uz = fx * rightY - fy * rightX;
  const camX = dx * rightX + dy * rightY;
  const camY = dx * ux + dy * uy + dz * uz;
  const camZ = dx * fx + dy * fy + dz * fz;
  const depth = Math.max(0.5, -camZ);
  const focal = height / 2 / Math.tan((camera.fov * Math.PI) / 360);
  return { x: width / 2 + (camX / depth) * focal, y: height / 2 - (camY / depth) * focal };
}
