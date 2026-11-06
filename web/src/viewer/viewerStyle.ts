// SPDX-License-Identifier: Apache-2.0
/**
 * Settings → Viewer → Style. Classic is today's layout and the default.
 * Studio, Instrument and Cinema are tokens on that same viewer: colours,
 * panel widths, type, overlay density and the orbit camera. Cinema's drift
 * is off while the OS asks for reduced motion.
 */
import { useEffect, useState } from "react";
import { getPersistentState } from "../desktop/local";
import type { SeenColors } from "./seenBy.ts";

export type ViewerStyle = "classic" | "studio" | "instrument" | "cinema";

export const VIEWER_STYLES: readonly ViewerStyle[] = ["classic", "studio", "instrument", "cinema"];

export const DEFAULT_VIEWER_STYLE: ViewerStyle = "classic";

/** Cinema drift is on unless the user turns it off. Reduced motion forces it off. */
export const DEFAULT_CINEMA_DRIFT = true;

export const VIEWER_STYLE_EVENT = "coperceive-viewer-style";

export type ViewerStyleTokens = {
  label: string;
  blurb: string;
  /** Dark-only looks do not follow the light theme. Classic does. */
  themes: readonly ("light" | "dark")[];
  background: string;
  panel: string;
  text: string;
  textSecondary: string;
  font: string;
  overlay: "regular" | "dense" | "sparse";
  leftPx: number | null;
  rightPx: number | null;
  fov: number;
  /** Relative to Classic's orbit distance. Applied when the style is chosen. */
  distanceScale: number;
  seen: SeenColors;
};

const SEEN_STUDIO: SeenColors = {
  both: "#e5e5ea",
  ego: "#F58A1F",
  other: "#5aa2ff",
  unknown: "#8e8e93",
};

export const VIEWER_STYLE_TOKENS: Record<ViewerStyle, ViewerStyleTokens> = {
  classic: {
    label: "Classic",
    blurb: "Today's layout. Light and dark follow the app theme.",
    themes: ["light", "dark"],
    background: "#141414",
    panel: "#1a1a1c",
    text: "#f5f5f7",
    textSecondary: "#c7c7cc",
    font: "Geist, sans-serif",
    overlay: "regular",
    leftPx: null,
    rightPx: null,
    fov: 55,
    distanceScale: 1,
    seen: SEEN_STUDIO,
  },
  studio: {
    label: "Studio",
    blurb: "Quiet pro tool. Dark chrome, tighter type, a closer orbit.",
    themes: ["dark"],
    background: "#121214",
    panel: "#1a1a1c",
    text: "#f5f5f7",
    textSecondary: "#c7c7cc",
    font: "Geist, sans-serif",
    overlay: "sparse",
    leftPx: 232,
    rightPx: 272,
    fov: 50,
    distanceScale: 0.9,
    seen: SEEN_STUDIO,
  },
  instrument: {
    label: "Instrument",
    blurb: "Heads-up display. Monospace readouts over the scene.",
    themes: ["dark"],
    background: "#04060a",
    panel: "#071016",
    text: "#e6fff9",
    textSecondary: "#9be7d6",
    font: "Geist Mono, ui-monospace, monospace",
    overlay: "dense",
    leftPx: 300,
    rightPx: 268,
    fov: 48,
    distanceScale: 1.05,
    seen: { both: "#d7fff4", ego: "#ffb347", other: "#5cc8ff", unknown: "#6fbfb0" },
  },
  cinema: {
    label: "Cinema",
    blurb: "The scene fills the window. Glass panels float over it.",
    themes: ["dark"],
    background: "#06070a",
    panel: "#1c1e26",
    text: "#f5f5f7",
    textSecondary: "#d0d0d4",
    font: "Geist, sans-serif",
    overlay: "sparse",
    leftPx: 220,
    rightPx: 280,
    fov: 42,
    distanceScale: 1.2,
    seen: { both: "#f5f5f7", ego: "#ffa24d", other: "#6fb0ff", unknown: "#a1a1a6" },
  },
};

/** Classic body text on its light and dark panels. The other styles are dark. */
export const CLASSIC_LIGHT = { text: "#1c1c1e", secondary: "#3a3a3c", panel: "#f6f8fa", page: "#ffffff" };

export function isViewerStyle(value: unknown): value is ViewerStyle {
  return value === "classic" || value === "studio" || value === "instrument" || value === "cinema";
}

export function effectiveCinemaDrift(saved: boolean, reducedMotion: boolean): boolean {
  return saved && !reducedMotion;
}

function controller(): { port: number; secret: string } | null {
  if (typeof window === "undefined" || !window.__COPERCEIVE_CTRL_PORT__) return null;
  return { port: window.__COPERCEIVE_CTRL_PORT__, secret: window.__COPERCEIVE_CTRL_SECRET__ || "" };
}

type StyleState = { viewer_style?: unknown; cinema_drift?: unknown };

function readInitial(): { style: ViewerStyle; drift: boolean } {
  if (typeof window === "undefined") return { style: DEFAULT_VIEWER_STYLE, drift: DEFAULT_CINEMA_DRIFT };
  const init = window.__COPERCEIVE_INITIAL_STATE__ as StyleState | undefined;
  return {
    style: isViewerStyle(init?.viewer_style) ? init.viewer_style : DEFAULT_VIEWER_STYLE,
    drift: typeof init?.cinema_drift === "boolean" ? init.cinema_drift : DEFAULT_CINEMA_DRIFT,
  };
}

export function initialViewerStyle(): ViewerStyle {
  return readInitial().style;
}

export function initialCinemaDrift(): boolean {
  return readInitial().drift;
}

async function loadSaved(): Promise<{ style: ViewerStyle; drift: boolean }> {
  const saved = (await getPersistentState().catch(() => null)) as StyleState | null;
  let style = initialViewerStyle();
  let drift = initialCinemaDrift();
  if (saved && isViewerStyle(saved.viewer_style)) style = saved.viewer_style;
  if (saved && typeof saved.cinema_drift === "boolean") drift = saved.cinema_drift;
  const ctrl = controller();
  if (ctrl) {
    try {
      const response = await fetch(`http://127.0.0.1:${ctrl.port}/state`, {
        headers: { Authorization: `Bearer ${ctrl.secret}` },
      });
      const state = (await response.json()) as StyleState;
      if (isViewerStyle(state.viewer_style)) style = state.viewer_style;
      if (typeof state.cinema_drift === "boolean") drift = state.cinema_drift;
    } catch {
      // The initial state stands.
    }
  }
  return { style, drift };
}

function postState(body: StyleState): void {
  const ctrl = controller();
  if (!ctrl) return;
  fetch(`http://127.0.0.1:${ctrl.port}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${ctrl.secret}` },
    body: JSON.stringify(body),
  }).catch(() => undefined);
}

export function saveViewerStyle(style: ViewerStyle): void {
  window.dispatchEvent(new CustomEvent(VIEWER_STYLE_EVENT, { detail: { viewer_style: style } }));
  postState({ viewer_style: style });
}

export function saveCinemaDrift(drift: boolean): void {
  window.dispatchEvent(new CustomEvent(VIEWER_STYLE_EVENT, { detail: { cinema_drift: drift } }));
  postState({ cinema_drift: drift });
}

export function useViewerStyle(): {
  style: ViewerStyle;
  setStyle: (style: ViewerStyle) => void;
  drift: boolean;
  setDrift: (drift: boolean) => void;
  reducedMotion: boolean;
} {
  const initial = readInitial();
  const [style, setStyleState] = useState<ViewerStyle>(initial.style);
  const [drift, setDriftState] = useState<boolean>(initial.drift);
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReducedMotion(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);
  useEffect(() => {
    let active = true;
    let changed = false;
    const onChange = (event: Event) => {
      const detail = (event as CustomEvent<StyleState>).detail;
      changed = true;
      if (detail && isViewerStyle(detail.viewer_style)) setStyleState(detail.viewer_style);
      if (detail && typeof detail.cinema_drift === "boolean") setDriftState(detail.cinema_drift);
    };
    const reread = () => {
      void loadSaved().then((saved) => {
        if (!active) return;
        setStyleState(saved.style);
        setDriftState(saved.drift);
      });
    };
    window.addEventListener(VIEWER_STYLE_EVENT, onChange);
    window.addEventListener("focus", reread);
    void loadSaved().then((saved) => {
      if (active && !changed) {
        setStyleState(saved.style);
        setDriftState(saved.drift);
      }
    });
    return () => {
      active = false;
      window.removeEventListener(VIEWER_STYLE_EVENT, onChange);
      window.removeEventListener("focus", reread);
    };
  }, []);
  return { style, setStyle: saveViewerStyle, drift, setDrift: saveCinemaDrift, reducedMotion };
}

/** Relative luminance contrast, WCAG. */
export function contrastRatio(foreground: string, background: string): number {
  const lum = (hex: string) => {
    const value = hex.replace("#", "");
    const channels = [0, 2, 4].map((index) => {
      const channel = Number.parseInt(value.slice(index, index + 2), 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
  };
  const lighter = Math.max(lum(foreground), lum(background));
  const darker = Math.min(lum(foreground), lum(background));
  return (lighter + 0.05) / (darker + 0.05);
}
