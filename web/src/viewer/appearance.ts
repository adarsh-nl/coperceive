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
  };
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

function classes(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string").slice(0, 40);
}
