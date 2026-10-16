import type { Pose } from "./types";

export function headingDeg(pose: Pose): number {
  const x = pose.qx;
  const y = pose.qy;
  const z = pose.qz;
  const w = pose.qw;
  const fx = 1 - 2 * (y * y + z * z);
  const fy = 2 * (x * y + w * z);
  const deg = (Math.atan2(fx, fy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

export function speedMps(previous: Pose, next: Pose, dtUs: number): number | null {
  if (!Number.isFinite(dtUs) || dtUs <= 0) {
    return null;
  }
  const dx = next.tx - previous.tx;
  const dy = next.ty - previous.ty;
  const dz = next.tz - previous.tz;
  const speed = Math.hypot(dx, dy, dz) / (dtUs / 1_000_000);
  return Number.isFinite(speed) ? speed : null;
}

export function formatMetres(value: number): string {
  return `${value.toFixed(1)} m`;
}

export function formatSpeed(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "—";
  }
  const kmh = value * 3.6;
  return `${value.toFixed(1)} m/s · ${kmh.toFixed(0)} km/h`;
}

export function sparkPaths(values: readonly number[], height = 18): { line: string; area: string } {
  if (values.length < 2) {
    return { line: "", area: "" };
  }
  let max = 1e-6;
  for (const value of values) {
    if (value > max) {
      max = value;
    }
  }
  const top = height - 1;
  const line = values
    .map((value, item) => {
      const x = (item / (values.length - 1)) * 100;
      const y = top - (Math.max(0, value) / max) * (top - 1);
      return `${item === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  return { line, area: `${line} L100 ${height.toFixed(2)} L0 ${height.toFixed(2)} Z` };
}

export function formatHeading(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "—";
  }
  return `${value.toFixed(0)}°`;
}

export function compassLabel(deg: number): string {
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const index = Math.round(deg / 45) % 8;
  return names[index] ?? "N";
}
