/** Single source for colour. tailwind.config.ts imports this module. */
export const tokens = {
  canvas: "#000000",
  glass: "rgb(28 28 30 / 0.78)",
  blur: "22px",
  hairline: "rgba(255, 255, 255, 0.08)",
  hairlineBright: "rgba(255, 255, 255, 0.14)",
  text: {
    primary: "#f5f5f7",
    secondary: "#a1a1a6",
    tertiary: "#86868b",
  },
  vehicle: "#F58A1F",
  drone: "#3C8DFF",
  hit: "#1FA3A3",
  miss: "#8E6CEF",
  fp: "#E0A431",
  lanes: "#7dcea0",
  roadEdge: "#86868b",
} as const;

export type Tokens = typeof tokens;
