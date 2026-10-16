import type { FrameBox, FrameDocument, Pose, SceneSummary } from "./types";
import { isPose } from "./types";

export type PickRecord = {
  agentId: string;
  category: string;
  trackId: string | null;
  center: [number, number, number];
  size: [number, number, number];
  index: number;
};

export function boxCenter(box: FrameBox): [number, number, number] | null {
  const center = box.center;
  if (center && center.length === 3 && center.every((item) => typeof item === "number" && Number.isFinite(item))) {
    return [center[0], center[1], center[2]];
  }
  if (isPose(box.pose)) {
    return [box.pose.tx, box.pose.ty, box.pose.tz];
  }
  return null;
}

export function boxSize(box: FrameBox): [number, number, number] | null {
  const size = box.size_lwh;
  if (!size || size.length < 3) {
    return null;
  }
  const [length, width, height] = size;
  if (![length, width, height].every((item) => typeof item === "number" && item > 0)) {
    return null;
  }
  return [length, width, height];
}

export function toPick(box: FrameBox, index: number): PickRecord | null {
  const center = boxCenter(box);
  const size = boxSize(box);
  if (!center || !size || !isPose(box.pose)) {
    return null;
  }
  return {
    agentId: box.agent_id,
    category: typeof box.category === "string" && box.category ? box.category : "object",
    trackId: typeof box.track_id === "string" && box.track_id ? box.track_id : null,
    center,
    size,
    index,
  };
}

export function samePick(a: PickRecord | null, b: PickRecord | null): boolean {
  if (!a || !b) {
    return a === b;
  }
  if (a.trackId && b.trackId) {
    return a.agentId === b.agentId && a.trackId === b.trackId;
  }
  return a.agentId === b.agentId && a.index === b.index;
}

export function distance(a: [number, number, number], b: [number, number, number]): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

export function matchBox(boxes: readonly FrameBox[], pick: PickRecord): [number, number, number] | null {
  if (pick.trackId) {
    const tracked = boxes.find((box) => box.agent_id === pick.agentId && box.track_id === pick.trackId);
    const center = tracked ? boxCenter(tracked) : null;
    if (center) {
      return center;
    }
  }
  let best: [number, number, number] | null = null;
  let bestDistance = 4;
  for (const box of boxes) {
    if (box.agent_id !== pick.agentId) {
      continue;
    }
    if (typeof box.category === "string" && box.category !== pick.category) {
      continue;
    }
    const center = boxCenter(box);
    if (!center) {
      continue;
    }
    const gap = distance(center, pick.center);
    if (gap < bestDistance) {
      best = center;
      bestDistance = gap;
    }
  }
  return best;
}

/** Agents with a box near this one. The gate matches the computed-pair rule: max(2 m, half the longer side). */
export function visibleAgents(pick: PickRecord, boxes: readonly FrameBox[]): string[] {
  const longer = Math.max(...pick.size);
  const gate = Math.max(2, 0.5 * longer);
  const found = new Set<string>([pick.agentId]);
  for (const box of boxes) {
    const center = boxCenter(box);
    const size = boxSize(box);
    if (!center || !size) {
      continue;
    }
    const other = Math.max(gate, Math.max(2, 0.5 * Math.max(...size)));
    if (distance(pick.center, center) <= other) {
      found.add(box.agent_id);
    }
  }
  return [...found];
}

export function followPick(frame: FrameDocument, pick: PickRecord): PickRecord | null {
  if (pick.trackId) {
    const index = frame.boxes.findIndex((box) => box.agent_id === pick.agentId && box.track_id === pick.trackId);
    if (index >= 0) {
      const box = frame.boxes[index];
      return box ? toPick(box, index) : null;
    }
  }
  const box = frame.boxes[pick.index];
  if (box && box.agent_id === pick.agentId) {
    return toPick(box, pick.index);
  }
  return null;
}

export function classCounts(boxes: readonly FrameBox[]): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const box of boxes) {
    const name = typeof box.category === "string" && box.category ? box.category : "other";
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

export function sceneDuration(summary: SceneSummary): number | null {
  const times = summary.times_us;
  if (times.length < 2) {
    return times.length === 1 ? 0 : null;
  }
  const first = times[0];
  const last = times[times.length - 1];
  if (typeof first !== "number" || typeof last !== "number") {
    return null;
  }
  return (last - first) / 1_000_000;
}

export function agentDistances(
  pick: PickRecord,
  frame: FrameDocument,
): { agentId: string; metres: number | null }[] {
  return frame.agents.map((agent) => {
    const pose: Pose | null = isPose(agent.pose) ? agent.pose : null;
    return {
      agentId: agent.agent_id,
      metres: pose ? distance(pick.center, [pose.tx, pose.ty, pose.tz]) : null,
    };
  });
}
