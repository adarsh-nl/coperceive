// SPDX-License-Identifier: Apache-2.0
/**
 * "Camera strip" (Settings → Viewer, and the strip's own toggle): every camera
 * in one horizontally scrolling filmstrip ("all", the default), or one agent's
 * cameras at a time with Switch agent ("agent"). Stored in state.json as
 * `camera_strip`, like the other preferences, and applied live.
 */
import { useEffect, useState } from "react";
import { getPersistentState } from "../desktop/local";

export type CameraStripMode = "all" | "agent";

export const DEFAULT_CAMERA_STRIP: CameraStripMode = "all";

/** Fired on window when the setting changes; detail is the new mode. */
export const CAMERA_STRIP_EVENT = "coperceive-camera-strip";

export function isCameraStripMode(value: unknown): value is CameraStripMode {
  return value === "all" || value === "agent";
}

function controller(): { port: number; secret: string } | null {
  if (typeof window === "undefined" || !window.__COPERCEIVE_CTRL_PORT__) return null;
  return { port: window.__COPERCEIVE_CTRL_PORT__, secret: window.__COPERCEIVE_CTRL_SECRET__ || "" };
}

/** What the window was opened with, before any read of state.json answers. */
export function initialCameraStrip(): CameraStripMode {
  if (typeof window === "undefined") return DEFAULT_CAMERA_STRIP;
  const init = window.__COPERCEIVE_INITIAL_STATE__ as { camera_strip?: unknown } | undefined;
  return isCameraStripMode(init?.camera_strip) ? init.camera_strip : DEFAULT_CAMERA_STRIP;
}

export async function loadCameraStrip(): Promise<CameraStripMode> {
  const saved = (await getPersistentState().catch(() => null)) as { camera_strip?: unknown } | null;
  if (saved && isCameraStripMode(saved.camera_strip)) return saved.camera_strip;
  const ctrl = controller();
  if (ctrl) {
    try {
      const response = await fetch(`http://127.0.0.1:${ctrl.port}/state`, {
        headers: { Authorization: `Bearer ${ctrl.secret}` },
      });
      const state = (await response.json()) as { camera_strip?: unknown };
      if (isCameraStripMode(state.camera_strip)) return state.camera_strip;
    } catch {
      // Fall back below.
    }
  }
  return initialCameraStrip();
}

export function saveCameraStrip(mode: CameraStripMode): void {
  window.dispatchEvent(new CustomEvent(CAMERA_STRIP_EVENT, { detail: mode }));
  const ctrl = controller();
  if (!ctrl) return;
  fetch(`http://127.0.0.1:${ctrl.port}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${ctrl.secret}` },
    body: JSON.stringify({ camera_strip: mode }),
  }).catch(() => undefined);
}

/**
 * The saved mode, kept in step with changes made anywhere in this window, and
 * read again when the window gets focus (a change made in the other window's
 * Settings then applies as soon as you come back to the viewer).
 */
export function useCameraStripMode(): [CameraStripMode, (mode: CameraStripMode) => void] {
  const [mode, setMode] = useState<CameraStripMode>(initialCameraStrip);
  useEffect(() => {
    let active = true;
    let changed = false;
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<unknown>).detail;
      if (isCameraStripMode(next)) {
        changed = true;
        setMode(next);
      }
    };
    const reread = () => {
      void loadCameraStrip().then((saved) => {
        if (active) setMode(saved);
      });
    };
    window.addEventListener(CAMERA_STRIP_EVENT, onChange);
    window.addEventListener("focus", reread);
    void loadCameraStrip().then((saved) => {
      // A change made while the saved value loaded wins.
      if (active && !changed) setMode(saved);
    });
    return () => {
      active = false;
      window.removeEventListener(CAMERA_STRIP_EVENT, onChange);
      window.removeEventListener("focus", reread);
    };
  }, []);
  return [mode, saveCameraStrip];
}
