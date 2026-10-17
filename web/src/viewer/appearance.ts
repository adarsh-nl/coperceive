export type ColorBy = "agent" | "class";
export type BoxStyle = "wire" | "solid";
export type ThemeName = "dark" | "light";

export type Appearance = {
  points: boolean;
  boxes: boolean;
  frustums: boolean;
  map: boolean;
  axes: boolean;
  colorBy: ColorBy;
  pointSize: number;
  boxStyle: BoxStyle;
  hiddenClasses: string[];
  theme: ThemeName;
  frustumImages: boolean;
  frustumOpacity: number;
  /** null means every camera. An array is the cameras allowed to show an image. */
  frustumImageCameras: string[] | null;
  /** Score floor for drawn predictions; null means the run's `score_threshold`. */
  predictionScore: number | null;
};

export const VIEW_KEY = "coperceive-view";

const CLASS_COLORS: Record<string, number> = {
  car: 0xe67828,
  truck: 0xc45c26,
  bus: 0xd4a017,
  pedestrian: 0x3d9a86,
  cyclist: 0x2878dc,
  bicycle: 0x2878dc,
  motorcycle: 0x6a5acd,
  barrier: 0x9aa7b2,
  traffic_cone: 0xe07a72,
};

const FALLBACK = [0xb06a9a, 0x5aa0c4, 0xc4a15a, 0x7dcea0];

export function defaultAppearance(): Appearance {
  return {
    points: true,
    boxes: true,
    frustums: true,
    map: true,
    axes: true,
    colorBy: "agent",
    pointSize: 2,
    boxStyle: "wire",
    hiddenClasses: [],
    theme: "light",
    frustumImages: false,
    frustumOpacity: 0.85,
    frustumImageCameras: null,
    predictionScore: null,
  };
}

export function showCameraImage(appearance: Appearance, key: string): boolean {
  if (!appearance.frustumImages) {
    return false;
  }
  const chosen = appearance.frustumImageCameras;
  return chosen === null || chosen.includes(key);
}

export function toggleFrustumImages(appearance: Appearance): Appearance {
  if (appearance.frustumImages) {
    return { ...appearance, frustumImages: false };
  }
  const chosen = appearance.frustumImageCameras;
  const empty = chosen !== null && chosen.length === 0;
  return { ...appearance, frustumImages: true, frustumImageCameras: empty ? null : chosen };
}

export function setAllFrustumImages(appearance: Appearance): Appearance {
  return { ...appearance, frustumImages: true, frustumImageCameras: null };
}

export function setNoFrustumImages(appearance: Appearance): Appearance {
  return { ...appearance, frustumImages: false, frustumImageCameras: [] };
}

export function toggleOneFrustumImage(
  appearance: Appearance,
  key: string,
  cameras: readonly string[],
): Appearance {
  const known = cameras.includes(key) ? [...cameras] : [...cameras, key];
  if (showCameraImage(appearance, key)) {
    const source = appearance.frustumImageCameras === null ? known : appearance.frustumImageCameras;
    const next = source.filter((item) => item !== key);
    return { ...appearance, frustumImages: next.length > 0, frustumImageCameras: next };
  }
  if (!appearance.frustumImages) {
    return { ...appearance, frustumImages: true, frustumImageCameras: [key] };
  }
  const source = appearance.frustumImageCameras === null ? known : appearance.frustumImageCameras;
  const next = source.includes(key) ? source : [...source, key];
  const every = known.length > 0 && known.every((item) => next.includes(item));
  return { ...appearance, frustumImages: true, frustumImageCameras: every ? null : next };
}

export function classColor(category: string): number {
  const known = CLASS_COLORS[category];
  if (known !== undefined) {
    return known;
  }
  let hash = 0;
  for (const ch of category) {
    hash = (hash + ch.charCodeAt(0)) % FALLBACK.length;
  }
  return FALLBACK[hash] ?? FALLBACK[0];
}

export function loadAppearance(): Appearance {
  const base = defaultAppearance();
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (!raw) {
      return base;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return base;
    }
    const row = parsed as Record<string, unknown>;
    return {
      points: bool(row.points, base.points),
      boxes: bool(row.boxes, base.boxes),
      frustums: bool(row.frustums, base.frustums),
      map: bool(row.map, base.map),
      axes: bool(row.axes, base.axes),
      colorBy: row.colorBy === "class" ? "class" : "agent",
      pointSize: pointSize(row.pointSize),
      boxStyle: row.boxStyle === "solid" ? "solid" : "wire",
      hiddenClasses: classes(row.hiddenClasses),
      theme: row.theme === "dark" ? "dark" : row.theme === "light" ? "light" : base.theme,
      frustumImages: "frustumImageCameras" in row ? bool(row.frustumImages, false) : false,
      frustumOpacity: opacity(row.frustumOpacity, base.frustumOpacity),
      frustumImageCameras: cameraChoice(row.frustumImageCameras),
      predictionScore: score(row.predictionScore),
    };
  } catch {
    return base;
  }
}

export function saveAppearance(value: Appearance): void {
  try {
    writeAppearance(value);
  } catch {
    // A full or blocked store leaves the page on the choices for this visit.
  }
}

function writeAppearance(value: Appearance): void {
  const stored: Appearance = {
    points: value.points,
    boxes: value.boxes,
    frustums: value.frustums,
    map: value.map,
    axes: value.axes,
    colorBy: value.colorBy,
    pointSize: value.pointSize,
    boxStyle: value.boxStyle,
    hiddenClasses: value.hiddenClasses,
    theme: value.theme,
    frustumImages: value.frustumImages,
    frustumOpacity: value.frustumOpacity,
    frustumImageCameras: value.frustumImageCameras,
    predictionScore: value.predictionScore,
  };
  localStorage.setItem(VIEW_KEY, JSON.stringify(stored));
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function pointSize(value: unknown): number {
  if (value === 1 || value === 2 || value === 3 || value === 4) {
    return value;
  }
  return 2;
}

function opacity(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(1, Math.max(0.15, value));
}

function score(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return Math.min(1, Math.max(0, value));
}

function cameraChoice(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  return value
    .filter((item): item is string => typeof item === "string" && item.length > 0 && item.length < 160)
    .slice(0, 80);
}

function classes(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string").slice(0, 40);
}
