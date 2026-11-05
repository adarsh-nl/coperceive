// SPDX-License-Identifier: Apache-2.0

export const DEFAULT_LEFT_PX = 248;
export const DEFAULT_RIGHT_PX = 268;
export const DEFAULT_STRIP_PX = 96;

export const MIN_LEFT_PX = 180;
export const MAX_LEFT_PX = 480;

export const MIN_RIGHT_PX = 200;
export const MAX_RIGHT_PX = 480;

export const MIN_STRIP_PX = 64;
export const MAX_STRIP_PX = 240;

export const MIN_STAGE_WIDTH = 250;
export const MIN_VIEWPORT_HEIGHT = 150;

export const RESIZE_STEP_PX = 10;
export const RESIZE_STEP_LARGE_PX = 50;

export interface LayoutSizes {
  left_px: number;
  right_px: number;
  strip_px: number;
}

export function defaultLayout(): LayoutSizes {
  return {
    left_px: DEFAULT_LEFT_PX,
    right_px: DEFAULT_RIGHT_PX,
    strip_px: DEFAULT_STRIP_PX,
  };
}

export function clampLeft(val: number, containerWidth?: number, rightPx?: number): number {
  if (!Number.isFinite(val)) {
    return DEFAULT_LEFT_PX;
  }
  let max = MAX_LEFT_PX;
  if (typeof containerWidth === "number" && containerWidth > 0) {
    const r = typeof rightPx === "number" ? rightPx : DEFAULT_RIGHT_PX;
    const available = containerWidth - r - MIN_STAGE_WIDTH;
    if (available >= MIN_LEFT_PX) {
      max = Math.min(MAX_LEFT_PX, available);
    }
  }
  return Math.min(max, Math.max(MIN_LEFT_PX, Math.round(val)));
}

export function clampRight(val: number, containerWidth?: number, leftPx?: number): number {
  if (!Number.isFinite(val)) {
    return DEFAULT_RIGHT_PX;
  }
  let max = MAX_RIGHT_PX;
  if (typeof containerWidth === "number" && containerWidth > 0) {
    const l = typeof leftPx === "number" ? leftPx : DEFAULT_LEFT_PX;
    const available = containerWidth - l - MIN_STAGE_WIDTH;
    if (available >= MIN_RIGHT_PX) {
      max = Math.min(MAX_RIGHT_PX, available);
    }
  }
  return Math.min(max, Math.max(MIN_RIGHT_PX, Math.round(val)));
}

export function clampStrip(val: number, containerHeight?: number): number {
  if (!Number.isFinite(val)) {
    return DEFAULT_STRIP_PX;
  }
  let max = MAX_STRIP_PX;
  if (typeof containerHeight === "number" && containerHeight > 0) {
    const available = containerHeight - MIN_VIEWPORT_HEIGHT;
    if (available >= MIN_STRIP_PX) {
      max = Math.min(MAX_STRIP_PX, available);
    }
  }
  return Math.min(max, Math.max(MIN_STRIP_PX, Math.round(val)));
}
