// SPDX-License-Identifier: Apache-2.0
/**
 * "Who sees it": both agents, the ego vehicle only, or the other agent only.
 * A box with no track id is not given a side. Nothing here uses distance.
 */
import type { FrameBox } from "./types.ts";

export type SeenKind = "both" | "ego" | "other" | "unknown";

export type SeenColors = {
  both: string;
  ego: string;
  other: string;
  unknown: string;
};

/** The other agent's legend word. Infrastructure is "RSU only", a UAV "Drone only". */
export function otherSeenLabel(agentTypes: readonly string[]): string {
  if (agentTypes.includes("infrastructure")) return "RSU only";
  if (agentTypes.includes("uav")) return "Drone only";
  return "Other vehicle only";
}

/**
 * Agents that stored this track id at this sample. Two or more → both.
 * Only the ego vehicle → ego. Only someone else → other. No track id → unknown.
 */
export function seenKind(box: FrameBox, boxes: readonly FrameBox[], egoId: string | null): SeenKind {
  if (typeof box.track_id !== "string" || box.track_id.length === 0) return "unknown";
  const agents = new Set<string>();
  for (const other of boxes) {
    if (other.track_id === box.track_id) agents.add(other.agent_id);
  }
  if (agents.size >= 2) return "both";
  if (egoId && agents.has(egoId)) return "ego";
  return "other";
}

/** Line pattern so the three kinds are not told apart by colour alone. */
export function seenDash(kind: SeenKind): "solid" | "dashed" | "dotted" {
  if (kind === "ego") return "dashed";
  if (kind === "other") return "dotted";
  return "solid";
}
