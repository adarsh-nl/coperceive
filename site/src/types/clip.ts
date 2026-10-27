/**
 * Hero clip formats. Both are written by `scripts/site/export_hero_clip.py`
 * (the layout of `CPCVCLP2` is documented there).
 *
 * - `CPCVCLIP` (version 1): one vehicle pose, one drone pose and the vehicle
 *   lidar per frame, plus boxes. The committed clip uses it.
 * - `CPCVCLP2` (version 2): an agent table (id, kind, colour) and per-agent
 *   poses and points for any number of agents, two or more.
 */

export type AgentKind = "vehicle" | "drone" | "infrastructure" | "other";

export interface ClipAgent {
  id: string;
  kind: AgentKind;
  /** `#rrggbb` */
  color: string;
}

export interface BoxRecord {
  trackId: number;
  agentFlag: number; // 1 = vehicle, 2 = drone, 3 = both
  category: number;  // 0 = car, 1 = pedestrian, 2 = cyclist, 3 = other
  center: [number, number, number];
  size: [number, number, number];
  rotation: [number, number, number, number]; // qx, qy, qz, qw
}

export interface AgentFrame {
  /** False when the agent has no pose at this time; the pose is then identity. */
  present: boolean;
  position: [number, number, number];
  quaternion: [number, number, number, number];
  /** World xyz floats. Empty when the agent has no sweep at this time. */
  points: Float32Array;
}

export interface FrameData {
  timeUs: number;
  /** One entry per agent, in agent-table order. */
  agents: AgentFrame[];
  /** Version 1 only. Version 2 stores no boxes. */
  boxes: BoxRecord[];
}

export interface ParsedClip {
  version: 1 | 2;
  agents: ClipAgent[];
  origin: [number, number, number];
  mapPositions: Float32Array;
  mapColors: Float32Array;
  frames: FrameData[];
}

/** Agent entries as `manifest.json` stores them: a list (version 2) or the version 1 object. */
export type ManifestAgents =
  | { id: string; kind: string; color: string }[]
  | Record<string, { id?: string; type?: string; color?: string }>;
