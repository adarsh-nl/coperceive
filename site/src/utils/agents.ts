import { tokens } from "../tokens";
import type { AgentKind, ClipAgent, ManifestAgents } from "../types/clip";

/** The version 1 clip has a fixed table: the vehicle (with the sweep), then the drone. */
export const V1_AGENTS: ClipAgent[] = [
  { id: "vehicle", kind: "vehicle", color: tokens.vehicle },
  { id: "drone", kind: "drone", color: tokens.drone },
];

const KIND_LABEL: Record<AgentKind, string> = {
  vehicle: "Vehicle",
  drone: "Drone",
  infrastructure: "RSU",
  other: "Agent",
};

function asKind(value: string | undefined): AgentKind {
  if (value === "uav") return "drone";
  if (value === "vehicle" || value === "drone" || value === "infrastructure") return value;
  return "other";
}

/** Normalise `manifest.json` agents (either shape) to the agent table. */
export function manifestAgents(agents: ManifestAgents | undefined): ClipAgent[] {
  if (!agents) return V1_AGENTS;
  if (Array.isArray(agents)) {
    return agents.map((a) => ({ id: a.id, kind: asKind(a.kind), color: a.color }));
  }
  const v1 = V1_AGENTS.filter((a) => a.id in agents);
  return v1.length === V1_AGENTS.length ? V1_AGENTS : v1;
}

export interface LegendEntry {
  key: string;
  label: string;
  color: string;
}

/**
 * Legend chip entries: one per agent, named by kind, numbered when a kind
 * repeats ("Vehicle 1", "Vehicle 2"), then Lane.
 */
export function legendEntries(agents: ClipAgent[]): LegendEntry[] {
  const totals = new Map<AgentKind, number>();
  for (const a of agents) totals.set(a.kind, (totals.get(a.kind) ?? 0) + 1);
  const seen = new Map<AgentKind, number>();
  const entries = agents.map((a) => {
    const n = (seen.get(a.kind) ?? 0) + 1;
    seen.set(a.kind, n);
    const base = KIND_LABEL[a.kind];
    return { key: a.id, label: (totals.get(a.kind) ?? 0) > 1 ? `${base} ${n}` : base, color: a.color };
  });
  entries.push({ key: "lane", label: "Lane", color: tokens.lanes });
  return entries;
}

const WORDS = ["", "a", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const NOUN: Record<AgentKind, [string, string]> = {
  vehicle: ["vehicle", "vehicles"],
  drone: ["drone", "drones"],
  infrastructure: ["roadside unit", "roadside units"],
  other: ["agent", "agents"],
};

/** "A vehicle and a drone." / "Three vehicles and a drone." for the canvas label. */
export function sceneDescription(agents: ClipAgent[]): string {
  const counts = new Map<AgentKind, number>();
  for (const a of agents) counts.set(a.kind, (counts.get(a.kind) ?? 0) + 1);
  const parts = [...counts].map(([kind, n]) => {
    const [one, many] = NOUN[kind];
    return n === 1 ? `a ${one}` : `${WORDS[n] ?? String(n)} ${many}`;
  });
  const text = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0] ?? "";
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
