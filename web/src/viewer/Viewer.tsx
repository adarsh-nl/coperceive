import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { apiBase, authHeaders, errorDetail } from "../api/client";

type Pose = {
  qw: number;
  qx: number;
  qy: number;
  qz: number;
  tx: number;
  ty: number;
  tz: number;
};

type SceneSummary = {
  scene_id: string;
  world_frame: string;
  agents: { agent_id: string; agent_type: string }[];
  times_us: number[];
};

type FrameBox = { agent_id: string; lines: number[] };
type FrameFrustum = {
  agent_id: string;
  sensor_id: string;
  pose: Pose;
  fov_y_rad: number;
  aspect: number;
};
type FrameSensor = { agent_id: string; sensor_id: string; count: number };
type FrameDocument = {
  scene_id: string;
  time_us: number;
  agents: { agent_id: string; pose: Pose | null }[];
  boxes: FrameBox[];
  frustums: FrameFrustum[];
  point_sensors: FrameSensor[];
};

type Stage = {
  points: THREE.Points;
  boxes: THREE.LineSegments;
  frustums: THREE.Group;
  agents: THREE.Group;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
};

const COLORS: Record<string, number> = {
  vehicle: 0xe67828,
  uav: 0x2878dc,
};

function colorFor(agentType: string): number {
  return COLORS[agentType] ?? 0xb7b1a6;
}

function isPose(value: unknown): value is Pose {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return ["qw", "qx", "qy", "qz", "tx", "ty", "tz"].every((key) => typeof row[key] === "number");
}

function isSummary(value: unknown): value is SceneSummary {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return (
    typeof row.scene_id === "string" &&
    typeof row.world_frame === "string" &&
    Array.isArray(row.agents) &&
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

function isFrame(value: unknown): value is FrameDocument {
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

function applyPose(object: THREE.Object3D, pose: Pose) {
  object.position.set(pose.tx, pose.ty, pose.tz);
  object.quaternion.set(pose.qx, pose.qy, pose.qz, pose.qw);
}

function frustumLines(fovY: number, aspect: number, color: number): THREE.LineSegments {
  const depth = 4;
  const halfHeight = Math.tan(fovY / 2) * depth;
  const halfWidth = halfHeight * aspect;
  const corners = [
    [-halfWidth, -halfHeight, depth],
    [halfWidth, -halfHeight, depth],
    [halfWidth, halfHeight, depth],
    [-halfWidth, halfHeight, depth],
  ];
  const verts: number[] = [];
  for (let index = 0; index < 4; index += 1) {
    const corner = corners[index];
    const next = corners[(index + 1) % 4];
    verts.push(
      0,
      0,
      0,
      corner[0],
      corner[1],
      corner[2],
      corner[0],
      corner[1],
      corner[2],
      next[0],
      next[1],
      next[2],
    );
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  return new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color }));
}

function clearGroup(group: THREE.Group) {
  for (const child of [...group.children]) {
    group.remove(child);
  }
}

async function drawFrame(
  stage: Stage,
  frame: FrameDocument,
  types: Map<string, string>,
  hidden: Record<string, boolean>,
  token: string,
  signal: AbortSignal,
) {
  const positions: number[] = [];
  const colors: number[] = [];
  for (const sensor of frame.point_sensors) {
    if (hidden[sensor.agent_id] || sensor.count === 0) {
      continue;
    }
    const response = await fetch(
      `${apiBase()}/api/scenes/${encodeURIComponent(frame.scene_id)}/points?time_us=${frame.time_us}&agent_id=${encodeURIComponent(sensor.agent_id)}&sensor_id=${encodeURIComponent(sensor.sensor_id)}`,
      { headers: authHeaders(token), signal },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    const xyz = new Float32Array(await response.arrayBuffer());
    const color = new THREE.Color(colorFor(types.get(sensor.agent_id) ?? ""));
    for (let index = 0; index < xyz.length; index += 3) {
      positions.push(xyz[index], xyz[index + 1], xyz[index + 2]);
      colors.push(color.r, color.g, color.b);
    }
  }
  stage.points.geometry.dispose();
  stage.points.geometry = new THREE.BufferGeometry();
  stage.points.geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  stage.points.geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  if (positions.length > 0) {
    stage.points.geometry.computeBoundingSphere();
    const center = stage.points.geometry.boundingSphere?.center;
    if (center) {
      stage.controls.target.copy(center);
      stage.camera.position.set(center.x + 25, center.y - 25, center.z + 18);
    }
  }

  const boxPositions: number[] = [];
  const boxColors: number[] = [];
  for (const box of frame.boxes) {
    if (hidden[box.agent_id] || !Array.isArray(box.lines)) {
      continue;
    }
    const color = new THREE.Color(colorFor(types.get(box.agent_id) ?? ""));
    boxPositions.push(...box.lines);
    for (let index = 0; index < box.lines.length; index += 3) {
      boxColors.push(color.r, color.g, color.b);
    }
  }
  stage.boxes.geometry.dispose();
  stage.boxes.geometry = new THREE.BufferGeometry();
  stage.boxes.geometry.setAttribute("position", new THREE.Float32BufferAttribute(boxPositions, 3));
  stage.boxes.geometry.setAttribute("color", new THREE.Float32BufferAttribute(boxColors, 3));

  clearGroup(stage.frustums);
  for (const frustum of frame.frustums) {
    if (hidden[frustum.agent_id] || !isPose(frustum.pose)) {
      continue;
    }
    const lines = frustumLines(
      frustum.fov_y_rad,
      frustum.aspect,
      colorFor(types.get(frustum.agent_id) ?? ""),
    );
    applyPose(lines, frustum.pose);
    stage.frustums.add(lines);
  }

  clearGroup(stage.agents);
  for (const agent of frame.agents) {
    if (hidden[agent.agent_id] || !isPose(agent.pose)) {
      continue;
    }
    const axes = new THREE.AxesHelper(2);
    applyPose(axes, agent.pose);
    stage.agents.add(axes);
  }
}

export default function Viewer({ token }: { token: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<Stage | null>(null);
  const [scenes, setScenes] = useState<string[]>([]);
  const [sceneId, setSceneId] = useState("");
  const [summary, setSummary] = useState<SceneSummary | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [status, setStatus] = useState("Choose a scene under the server root.");
  const [exportNote, setExportNote] = useState("");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x16130f);
    const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000);
    camera.up.set(0, 0, 1);
    camera.position.set(40, -40, 25);
    const controls = new OrbitControls(camera, canvas);
    controls.target.set(0, 0, 0);
    const points = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({ size: 6, sizeAttenuation: false, vertexColors: true }),
    );
    const boxes = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ vertexColors: true }),
    );
    const frustums = new THREE.Group();
    const agents = new THREE.Group();
    scene.add(points, boxes, frustums, agents);
    stageRef.current = { points, boxes, frustums, agents, camera, controls };
    let frameId = 0;
    const draw = () => {
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      renderer.setPixelRatio(window.devicePixelRatio || 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      controls.update();
      renderer.render(scene, camera);
      frameId = window.requestAnimationFrame(draw);
    };
    draw();
    return () => {
      window.cancelAnimationFrame(frameId);
      controls.dispose();
      renderer.dispose();
      points.geometry.dispose();
      boxes.geometry.dispose();
      stageRef.current = null;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const response = await fetch(`${apiBase()}/api/scenes`, {
        headers: authHeaders(token),
        signal: controller.signal,
      });
      if (!response.ok) {
        setStatus(await errorDetail(response));
        return;
      }
      const body = (await response.json()) as unknown;
      if (!Array.isArray(body)) {
        setStatus("The scene list was not a list.");
        return;
      }
      const ids = body.flatMap((item) => {
        if (
          item &&
          typeof item === "object" &&
          typeof (item as { scene_id?: unknown }).scene_id === "string"
        ) {
          return [(item as { scene_id: string }).scene_id];
        }
        return [];
      });
      setScenes(ids);
      setSceneId((current) => current || ids[0] || "");
      if (ids.length === 0) {
        setStatus("No scene directories under the server root.");
      }
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setStatus(error instanceof Error ? error.message : "Could not list scenes.");
      }
    });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!sceneId) {
      return;
    }
    const controller = new AbortController();
    void (async () => {
      const response = await fetch(`${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}`, {
        headers: authHeaders(token),
        signal: controller.signal,
      });
      if (!response.ok) {
        setStatus(await errorDetail(response));
        return;
      }
      const body = (await response.json()) as unknown;
      if (!isSummary(body)) {
        setStatus("The scene summary was incomplete.");
        return;
      }
      setSummary(body);
      setFrameIndex(0);
      setHidden({});
      setStatus(`${body.scene_id}: ${body.times_us.length} samples, world ${body.world_frame}.`);
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setStatus(error instanceof Error ? error.message : "Could not read the scene.");
      }
    });
    return () => controller.abort();
  }, [sceneId, token]);

  useEffect(() => {
    if (!summary || !stageRef.current) {
      return;
    }
    const time = summary.times_us[frameIndex];
    if (typeof time !== "number") {
      return;
    }
    const stage = stageRef.current;
    const controller = new AbortController();
    const types = new Map(summary.agents.map((agent) => [agent.agent_id, agent.agent_type]));
    void (async () => {
      const response = await fetch(
        `${apiBase()}/api/scenes/${encodeURIComponent(summary.scene_id)}/frame?time_us=${time}`,
        { headers: authHeaders(token), signal: controller.signal },
      );
      if (!response.ok) {
        setStatus(await errorDetail(response));
        return;
      }
      const body = (await response.json()) as unknown;
      if (!isFrame(body)) {
        setStatus("The frame document was incomplete.");
        return;
      }
      await drawFrame(stage, body, types, hidden, token, controller.signal);
      const shown = body.point_sensors.reduce((sum, sensor) => {
        return hidden[sensor.agent_id] ? sum : sum + sensor.count;
      }, 0);
      setStatus(
        `${summary.scene_id} at ${time} µs. ${shown} points, ${body.boxes.length} boxes, ${body.frustums.length} frustums.`,
      );
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setStatus(error instanceof Error ? error.message : "Could not draw the frame.");
      }
    });
    return () => controller.abort();
  }, [summary, frameIndex, hidden, token]);

  async function exportRerun() {
    if (!summary) {
      return;
    }
    setExportNote("Writing the .rrd…");
    const response = await fetch(
      `${apiBase()}/api/scenes/${encodeURIComponent(summary.scene_id)}/export/rerun`,
      {
        method: "POST",
        headers: { ...authHeaders(token), "X-Coperceive-Request": "1" },
      },
    );
    if (!response.ok) {
      setExportNote(await errorDetail(response));
      return;
    }
    const body = (await response.json()) as { path?: unknown; samples?: unknown };
    setExportNote(`Wrote ${String(body.path)} (${String(body.samples)} samples).`);
  }

  const last = summary ? Math.max(summary.times_us.length - 1, 0) : 0;

  return (
    <section className="card viewer-card">
      <h2>Viewer</h2>
      <p className="muted">
        three.js on this origin. The scrubber steps through recorded pose times and does not
        interpolate. Camera images are not drawn; the frustums are the pinholes. Rerun export writes
        an <span className="mono">.rrd</span> under the server root and needs the viewer extra.
      </p>
      <div className="viewer-bar">
        <label htmlFor="scene-id">Scene</label>
        <select id="scene-id" value={sceneId} onChange={(event) => setSceneId(event.target.value)}>
          {scenes.length === 0 ? <option value="">No scenes</option> : null}
          {scenes.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
        <label htmlFor="scrubber">
          Sample {summary ? frameIndex + 1 : 0} / {summary ? summary.times_us.length : 0}
        </label>
        <input
          id="scrubber"
          type="range"
          min={0}
          max={last}
          value={Math.min(frameIndex, last)}
          onChange={(event) => setFrameIndex(Number(event.target.value))}
        />
        <button type="button" onClick={() => void exportRerun()} disabled={!summary}>
          Export Rerun
        </button>
      </div>
      <div className="viewer-agents">
        {(summary?.agents ?? []).map((agent) => (
          <label key={agent.agent_id}>
            <input
              type="checkbox"
              checked={!hidden[agent.agent_id]}
              onChange={() =>
                setHidden((current) => ({
                  ...current,
                  [agent.agent_id]: !current[agent.agent_id],
                }))
              }
            />
            {agent.agent_id}
          </label>
        ))}
      </div>
      <canvas ref={canvasRef} className="viewer-canvas" />
      <p className="muted">{status}</p>
      {exportNote ? <p className="muted">{exportNote}</p> : null}
    </section>
  );
}
