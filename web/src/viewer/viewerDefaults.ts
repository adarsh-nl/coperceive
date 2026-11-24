// SPDX-License-Identifier: Apache-2.0
/**
 * Settings → Viewer display defaults (Settings phase 1): the point size a view
 * starts with (`viewer_point_size`, inventory L87) and the camera view a scene
 * opens in (`default_viewpoint`, inventory L91). Both are display-only: they
 * never change a stored result, so they may be global.
 *
 * They follow the streaming settings' pattern: stored in state.json, applied
 * live through a window event, and read back when the window gets focus so a
 * change made in the other window's Settings applies on return.
 *
 * Point size: the toolbar slider keeps a per-view override (`pointSizeChosen`
 * in `coperceive-view`, see `resolvePointSize` in appearance.ts); a view that
 * has not chosen follows this default.
 *
 * Camera view: null ("Last used", the default) starts where the last viewer
 * left off (`last_viewpoint`), as before. A view the scene cannot show still
 * falls back to orbit (`viewpointFor` in capabilities.ts).
 */
import { useEffect, useRef, useState } from "react";
import { getViewerPreferences, updatePersistentState } from "../desktop/local";
import { DEFAULT_POINT_SIZE, initialDefaultPointSize, isPointSize } from "./appearance";
import type { Viewpoint } from "./viewpoints";

export type ViewerDefaults = {
  /** CSS pixels, 1–4. */
  pointSize: number;
  /** The view a scene opens in; null = the last used view. */
  defaultViewpoint: Viewpoint | null;
};

export const DEFAULT_VIEWER_DEFAULTS: ViewerDefaults = {
  pointSize: DEFAULT_POINT_SIZE,
  defaultViewpoint: null,
};

/** The viewpoints the viewer offers, in toolbar order. */
export const VIEWPOINT_IDS: readonly Viewpoint[] = ["orbit", "bird", "chase", "drone", "camera"];

/** Fired on window when a default changes; detail is a `Partial<ViewerDefaults>`. */
export const VIEWER_DEFAULTS_EVENT = "coperceive-viewer-defaults";

export function isViewpoint(value: unknown): value is Viewpoint {
  return typeof value === "string" && (VIEWPOINT_IDS as readonly string[]).includes(value);
}

/** The persisted fields, as state.json spells them. */
type DefaultsState = {
  viewer_point_size?: unknown;
  default_viewpoint?: unknown;
};

/**
 * A settings patch from a state.json-shaped object, keeping only valid values.
 * state.json omits `default_viewpoint` when it is "Last used", so a read without
 * it means null.
 */
export function viewerDefaultsFromState(state: DefaultsState | null | undefined): Partial<ViewerDefaults> {
  if (!state) return {};
  const settings: Partial<ViewerDefaults> = {
    defaultViewpoint: isViewpoint(state.default_viewpoint) ? state.default_viewpoint : null,
  };
  if (isPointSize(state.viewer_point_size)) settings.pointSize = state.viewer_point_size;
  return settings;
}

/**
 * The view a scene opens in: the Settings default when one is chosen, else the
 * last used view, else orbit. The caller still applies the scene's capability
 * fallback (`viewpointFor`).
 */
export function startViewpoint(
  defaultViewpoint: unknown,
  lastViewpoint: unknown,
): Viewpoint {
  if (isViewpoint(defaultViewpoint)) return defaultViewpoint;
  if (isViewpoint(lastViewpoint)) return lastViewpoint;
  return "orbit";
}

/** What the window was opened with, before any read of state.json answers. */
export function initialViewerDefaults(): ViewerDefaults {
  if (typeof window === "undefined") return { ...DEFAULT_VIEWER_DEFAULTS };
  const init = window.__COPERCEIVE_INITIAL_STATE__ as unknown as DefaultsState | undefined;
  return { ...DEFAULT_VIEWER_DEFAULTS, ...viewerDefaultsFromState(init), pointSize: initialDefaultPointSize() };
}

export async function loadViewerDefaults(): Promise<ViewerDefaults> {
  const saved = (await getViewerPreferences().catch(() => null)) as DefaultsState | null;
  return { ...initialViewerDefaults(), ...viewerDefaultsFromState(saved) };
}

/** state.json spellings of a patch. */
function persistedPatch(patch: Partial<ViewerDefaults>): Record<string, unknown> {
  const state: Record<string, unknown> = {};
  if (patch.pointSize !== undefined) state.viewer_point_size = patch.pointSize;
  if (patch.defaultViewpoint !== undefined) state.default_viewpoint = patch.defaultViewpoint;
  return state;
}

export function saveViewerDefaults(patch: Partial<ViewerDefaults>): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(VIEWER_DEFAULTS_EVENT, { detail: patch }));
  void updatePersistentState(persistedPatch(patch)).catch(() => undefined);
}

/**
 * The saved defaults, kept in step with changes made anywhere in this window,
 * and read again when the window gets focus.
 */
export function useViewerDefaults(): [ViewerDefaults, (patch: Partial<ViewerDefaults>) => void] {
  const [settings, setSettings] = useState<ViewerDefaults>(initialViewerDefaults);
  // Fields changed in this window win over a saved read that answers later.
  const changedRef = useRef<Partial<ViewerDefaults>>({});
  useEffect(() => {
    let active = true;
    const onChange = (event: Event) => {
      const patch = (event as CustomEvent<Partial<ViewerDefaults>>).detail;
      if (!patch) return;
      changedRef.current = { ...changedRef.current, ...patch };
      setSettings((current) => ({ ...current, ...patch }));
    };
    // On focus the saved values win: a change made in this window was saved
    // long before, and one made in the other window's Settings is newer.
    const reread = () => {
      void loadViewerDefaults().then((saved) => {
        if (!active) return;
        changedRef.current = {};
        setSettings(saved);
      });
    };
    window.addEventListener(VIEWER_DEFAULTS_EVENT, onChange);
    window.addEventListener("focus", reread);
    void loadViewerDefaults().then((saved) => {
      if (active) setSettings({ ...saved, ...changedRef.current });
    });
    return () => {
      active = false;
      window.removeEventListener(VIEWER_DEFAULTS_EVENT, onChange);
      window.removeEventListener("focus", reread);
    };
  }, []);
  const save = (patch: Partial<ViewerDefaults>) => {
    changedRef.current = { ...changedRef.current, ...patch };
    setSettings((current) => ({ ...current, ...patch }));
    saveViewerDefaults(patch);
  };
  return [settings, save];
}
