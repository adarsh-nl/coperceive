// Design Canvas Tokens (https://claude.ai/artifact/FEfojn3drJXYfphm9RRgEz)
// The single source of truth for all color hex values across the application.
export const tokens = {
  canvas: "#000000",
  glass: "rgb(28 28 30 / 0.78)",
  hairline: "rgba(255, 255, 255, 0.08)",
  hairlineBright: "rgba(255, 255, 255, 0.14)",
  text: {
    primary: "#f5f5f7",
    secondary: "#a1a1a6",
    tertiary: "#86868b",
  },
  data: {
    vehicle: "#e67828",
    uav: "#2878dc",
    infrastructure: "#b7b1a6",
    lanes: "#7dcea0",
    roadEdge: "#86868b",
  },
  status: {
    ok: "#5dd39e",
    warn: "#f0b64a",
    bad: "#ff6259",
  },
} as const;

export type Tokens = typeof tokens;
