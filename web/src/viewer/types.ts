export type Pose = {
  qw: number;
  qx: number;
  qy: number;
  qz: number;
  tx: number;
  ty: number;
  tz: number;
};

export type SensorRow = {
  sensor_id: string;
  agent_id: string;
  modality: string;
};

export type SceneSummary = {
  scene_id: string;
  world_frame: string;
  agents: { agent_id: string; agent_type: string }[];
  sensors: SensorRow[];
  times_us: number[];
};

export type FrameBox = { agent_id: string; lines: number[] };
export type FrameFrustum = {
  agent_id: string;
  sensor_id: string;
  pose: Pose;
  fov_y_rad: number;
  aspect: number;
};
export type FrameSensor = { agent_id: string; sensor_id: string; count: number };
export type FrameDocument = {
  scene_id: string;
  time_us: number;
  agents: { agent_id: string; pose: Pose | null }[];
  boxes: FrameBox[];
  frustums: FrameFrustum[];
  point_sensors: FrameSensor[];
};

export function isPose(value: unknown): value is Pose {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return ["qw", "qx", "qy", "qz", "tx", "ty", "tz"].every((key) => typeof row[key] === "number");
}

function isSensorRow(value: unknown): value is SensorRow {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return (
    typeof row.sensor_id === "string" &&
    typeof row.agent_id === "string" &&
    typeof row.modality === "string"
  );
}

export function isSummary(value: unknown): value is SceneSummary {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return (
    typeof row.scene_id === "string" &&
    typeof row.world_frame === "string" &&
    Array.isArray(row.agents) &&
    Array.isArray(row.sensors) &&
    row.sensors.every(isSensorRow) &&
    Array.isArray(row.times_us) &&
    row.times_us.every((item) => typeof item === "number") &&
    row.agents.every(
      (item) =>
        !!item &&
        typeof item === "object" &&
        typeof (item as { agent_id?: unknown }).agent_id === "string" &&
        typeof (item as { agent_type?: unknown }).agent_type === "string",
    )
  );
}

export function isFrame(value: unknown): value is FrameDocument {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return (
    typeof row.scene_id === "string" &&
    typeof row.time_us === "number" &&
    Array.isArray(row.agents) &&
    Array.isArray(row.boxes) &&
    Array.isArray(row.frustums) &&
    Array.isArray(row.point_sensors)
  );
}

export function cameraKey(agentId: string, sensorId: string): string {
  return `${agentId}/${sensorId}`;
}
