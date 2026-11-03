// SPDX-License-Identifier: Apache-2.0
import type { SceneSummary } from "./types.ts";
import type { Viewpoint } from "./viewpoints.ts";

/**
 * What a scene can show, read from its own data (agent types, sensors), never
 * from which dataset it came from: a new dataset gets the right controls
 * without UI changes.
 */
export type SceneCapabilities = {
  viewpoints: ReadonlySet<Viewpoint>;
  /** Calibrated cameras: frustums and the Camera viewpoint. */
  cameras: boolean;
};

export function sceneCapabilities(summary: SceneSummary | null): SceneCapabilities {
  const types = new Set((summary?.agents ?? []).map((agent) => agent.agent_type));
  const cameras = (summary?.sensors ?? []).some((sensor) => sensor.modality === "camera");
  const viewpoints = new Set<Viewpoint>(["orbit", "bird"]);
  if (types.has("vehicle")) {
    viewpoints.add("chase");
  }
  // The drone view looks from a UAV towards the vehicle it accompanies.
  if (types.has("uav") && types.has("vehicle")) {
    viewpoints.add("drone");
  }
  if (cameras) {
    viewpoints.add("camera");
  }
  return { viewpoints, cameras };
}

/** The viewpoint to keep when the scene changes: the same one if it still exists, else Orbit. */
export function viewpointFor(current: Viewpoint, capabilities: SceneCapabilities): Viewpoint {
  return capabilities.viewpoints.has(current) ? current : "orbit";
}
