/** Single source for colour. tailwind.config.ts imports this module. */
export const tokens = {
  canvas: "#000000",
  /** Raised card surface, from landing-page-design's dark background set. */
  surface: "#181818",
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
  /** Second vehicle platform. Distinct from vehicle, drone, hit, miss, and lanes. */
  vehicleAlt: "#4CC9F0",
  drone: "#3C8DFF",
  /** Wordmark block cursor. Sampled from the 4c mark, about #E03A22. */
  cursor: "#E03A22",
  hit: "#1FA3A3",
  miss: "#8E6CEF",
  fp: "#E0A431",
  lanes: "#7dcea0",
  roadEdge: "#86868b",
} as const;

export type Tokens = typeof tokens;
