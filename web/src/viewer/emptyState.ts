import type { SceneSummary } from "./types";

/** `GET /api/scenes/{id}/map`: the scene's map name and whether a sidecar map resolves for it. */
export type MapInfo = { map_name: string | null; available: boolean };

export function isMapInfo(value: unknown): value is MapInfo {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return (row.map_name === null || typeof row.map_name === "string") && typeof row.available === "boolean";
}

/** `Carla/Maps/Town03` → `Town03`, the directory name the server looks for under the maps root. */
export function mapSlug(mapName: string): string {
  const parts = mapName.split("/").filter((part) => part !== "");
  return parts[parts.length - 1] ?? mapName;
}

/** Status wording for a scene whose map did not draw. */
export function mapNoteFor(info: MapInfo | null): string {
  if (info === null) {
    return "no map.";
  }
  if (info.map_name === null) {
    return "no map name in this scene.";
  }
  return `no map for ${mapSlug(info.map_name)}.`;
}

/** Why the points layer is empty, or null when the scene carries lidar (or an old server did not say). */
export function lidarNote(summary: SceneSummary | null): string | null {
  if (!summary || typeof summary.point_sweeps !== "number") {
    return null;
  }
  return summary.point_sweeps === 0 ? "no lidar staged." : null;
}

/** Why the camera panel is empty, or null when image files were found for the scene. */
export function imageNote(summary: SceneSummary | null): string | null {
  if (!summary || typeof summary.image_rows !== "number") {
    return null;
  }
  if (summary.image_rows === 0) {
    return "no camera images in this scene.";
  }
  return summary.images_staged === false ? "no camera images staged." : null;
}

/** One line per pane that has nothing to show, for the viewport's missing-data pill. */
export function missingNotes(summary: SceneSummary | null, map: MapInfo | null, predictionsNote: string): string[] {
  const notes: string[] = [];
  const lidar = lidarNote(summary);
  if (lidar) {
    notes.push(`Points: ${lidar}`);
  }
  const images = imageNote(summary);
  if (images) {
    notes.push(`Camera: ${images}`);
  }
  if (map && !map.available) {
    notes.push(`Map: ${mapNoteFor(map)}`);
  }
  if (predictionsNote) {
    notes.push(`Predictions: ${predictionsNote}`);
  }
  return notes;
}
