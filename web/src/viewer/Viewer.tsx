import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { apiBase, authHeaders, errorDetail } from "../api/client";
import { createFrameCache, type FrameCache } from "./cache";
import { formatClock, frameDelayMs, neighborSpeed, SPEEDS, stepIndex, type Speed } from "./playback";
import {
  cameraKey,
  isPose,
  isSummary,
  type FrameDocument,
  type FrameFrustum,
  type Pose,
  type SceneSummary,
} from "./types";
import {
  applyGoal,
  approachGoal,
  birdGoal,
  cameraGoal,
  chaseGoal,
  droneGoal,
  orbitGoal,
  type CameraGoal,
  type Viewpoint,
} from "./viewpoints";

type Stage = {
  points: THREE.Points;
  boxes: THREE.LineSegments;
  mapLines: THREE.LineSegments;
  frustums: THREE.Group;
  agents: THREE.Group;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
};

type Drawn = { positions: number[]; anchor: { x: number; y: number; z: number } | null };

const COLORS: Record<string, number> = {
  vehicle: 0xe67828,
  uav: 0x2878dc,
};

const VIEWPOINTS: { id: Viewpoint; label: string; shortcut: string }[] = [
  { id: "orbit", label: "Orbit", shortcut: "1" },
  { id: "bird", label: "Bird's-eye", shortcut: "2" },
  { id: "chase", label: "Chase", shortcut: "3" },
  { id: "drone", label: "Drone", shortcut: "4" },
  { id: "camera", label: "Camera", shortcut: "5" },
];

const PREFETCH_FRAMES = 4;

function colorFor(agentType: string): number {
  return COLORS[agentType] ?? 0xb7b1a6;
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
    const corner = corners[index] ?? [0, 0, 0];
    const next = corners[(index + 1) % 4] ?? [0, 0, 0];
    verts.push(0, 0, 0, corner[0], corner[1], corner[2], corner[0], corner[1], corner[2], next[0], next[1], next[2]);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  return new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color }));
}

function mapGeometry(buffer: ArrayBuffer): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  const view = new DataView(buffer);
  if (buffer.byteLength < 4) {
    geometry.setAttribute("position", new THREE.Float32BufferAttribute([], 3));
    return geometry;
  }
  let offset = 0;
  const count = view.getUint32(offset, true);
  offset += 4;
  const positions: number[] = [];
  const colors: number[] = [];
  const lane = new THREE.Color(0x7dcea0);
  const edge = new THREE.Color(0x9aa7b2);
  for (let index = 0; index < count; index += 1) {
    if (offset + 8 > buffer.byteLength) {
      break;
    }
    const layer = view.getUint32(offset, true);
    offset += 4;
    const vertices = view.getUint32(offset, true);
    offset += 4;
    const color = layer === 1 ? lane : edge;
    const coords: number[] = [];
    for (let vertex = 0; vertex < vertices; vertex += 1) {
      coords.push(
        view.getFloat32(offset, true),
        view.getFloat32(offset + 4, true),
        view.getFloat32(offset + 8, true),
      );
      offset += 12;
    }
    for (let vertex = 0; vertex + 1 < vertices; vertex += 1) {
      positions.push(
        coords[vertex * 3] ?? 0,
        coords[vertex * 3 + 1] ?? 0,
        coords[vertex * 3 + 2] ?? 0,
        coords[(vertex + 1) * 3] ?? 0,
        coords[(vertex + 1) * 3 + 1] ?? 0,
        coords[(vertex + 1) * 3 + 2] ?? 0,
      );
      colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
    }
  }
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  return geometry;
}

function clearGroup(group: THREE.Group) {
  for (const child of [...group.children]) {
    group.remove(child);
  }
}

function agentPose(frame: FrameDocument, types: Map<string, string>, agentType: string): Pose | null {
  for (const agent of frame.agents) {
    if (types.get(agent.agent_id) === agentType && isPose(agent.pose)) {
      return agent.pose;
    }
  }
  return null;
}

function frustumFor(frame: FrameDocument, key: string): FrameFrustum | null {
  for (const frustum of frame.frustums) {
    if (cameraKey(frustum.agent_id, frustum.sensor_id) === key && isPose(frustum.pose)) {
      return frustum;
    }
  }
  return null;
}

function holdNote(mode: Viewpoint, frame: FrameDocument, types: Map<string, string>, key: string): string {
  if (mode === "chase" && !agentPose(frame, types, "vehicle")) {
    return " No vehicle pose at this sample, so chase is holding.";
  }
  if (mode === "bird" && !agentPose(frame, types, "vehicle")) {
    return " No vehicle pose at this sample, so bird's-eye is holding.";
  }
  if (mode === "drone" && (!agentPose(frame, types, "uav") || !agentPose(frame, types, "vehicle"))) {
    return " No UAV pose at this sample, so the drone view is holding.";
  }
  if (mode === "camera" && !frustumFor(frame, key)) {
    return " That camera has no frustum at this sample.";
  }
  return "";
}

function goalFor(
  mode: Viewpoint,
  frame: FrameDocument | null,
  types: Map<string, string>,
  key: string,
  home: CameraGoal | null,
): CameraGoal | null {
  if (!frame || mode === "orbit") {
    return home;
  }
  const vehicle = agentPose(frame, types, "vehicle");
  if (mode === "bird") {
    return vehicle ? birdGoal(vehicle) : null;
  }
  if (mode === "chase") {
    return vehicle ? chaseGoal(vehicle) : null;
  }
  if (mode === "drone") {
    const drone = agentPose(frame, types, "uav");
    return drone && vehicle ? droneGoal(drone, vehicle) : null;
  }
  const frustum = frustumFor(frame, key);
  return frustum && isPose(frustum.pose) ? cameraGoal(frustum.pose, frustum.fov_y_rad) : null;
}

async function drawFrame(
  stage: Stage,
  frame: FrameDocument,
  types: Map<string, string>,
  hidden: Record<string, boolean>,
  token: string,
  cache: FrameCache,
  stale: () => boolean,
): Promise<Drawn | null> {
  const positions: number[] = [];
  const colors: number[] = [];
  for (const sensor of frame.point_sensors) {
    if (hidden[sensor.agent_id] || sensor.count === 0) {
      continue;
    }
    const buffer = await cache.loadPoints(
      frame.scene_id,
      frame.time_us,
      sensor.agent_id,
      sensor.sensor_id,
      token,
    );
    if (stale()) {
      return null;
    }
    const xyz = new Float32Array(buffer);
    const color = new THREE.Color(colorFor(types.get(sensor.agent_id) ?? ""));
    for (let index = 0; index < xyz.length; index += 3) {
      positions.push(xyz[index] ?? 0, xyz[index + 1] ?? 0, xyz[index + 2] ?? 0);
      colors.push(color.r, color.g, color.b);
    }
  }
  if (stale()) {
    return null;
  }
  stage.points.geometry.dispose();
  stage.points.geometry = new THREE.BufferGeometry();
  stage.points.geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  stage.points.geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));

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
    const lines = frustumLines(frustum.fov_y_rad, frustum.aspect, colorFor(types.get(frustum.agent_id) ?? ""));
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
  const vehicle = agentPose(frame, types, "vehicle");
  return {
    positions,
    anchor: vehicle ? { x: vehicle.tx, y: vehicle.ty, z: vehicle.tz } : null,
  };
}

function replaceMap(stage: Stage, buffer: ArrayBuffer) {
  const next = mapGeometry(buffer);
  stage.mapLines.geometry.dispose();
  stage.mapLines.geometry = next;
}

function prefetchAhead(
  cache: FrameCache,
  summary: SceneSummary,
  frameIndex: number,
  token: string,
) {
  const times = summary.times_us.slice(frameIndex + 1, frameIndex + 1 + PREFETCH_FRAMES);
  for (const time of times) {
    void cache
      .loadFrame(summary.scene_id, time, token)
      .then((frame) => {
        for (const sensor of frame.point_sensors) {
          if (sensor.count === 0) {
            continue;
          }
          void cache
            .loadPoints(summary.scene_id, time, sensor.agent_id, sensor.sensor_id, token)
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);
  }
}

export default function Viewer({ token }: { token: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<Stage | null>(null);
  const cacheRef = useRef<FrameCache>(createFrameCache());
  const [scenes, setScenes] = useState<string[]>([]);
  const [sceneId, setSceneId] = useState("");
  const [summary, setSummary] = useState<SceneSummary | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [status, setStatus] = useState("Choose a scene under the server root.");
  const [mapNote, setMapNote] = useState("");
  const [exportNote, setExportNote] = useState("");
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  const [viewpoint, setViewpoint] = useState<Viewpoint>("orbit");
  const [cameraId, setCameraId] = useState("");
  const framedScene = useRef("");
  const modeRef = useRef<Viewpoint>("orbit");
  const cameraKeyRef = useRef("");
  const latestRef = useRef<{ frame: FrameDocument; types: Map<string, string> } | null>(null);
  const orbitHomeRef = useRef<CameraGoal | null>(null);
  const settleRef = useRef(false);
  const reduceMotionRef = useRef(false);
  const lengthRef = useRef(0);
  const loopRef = useRef(false);
  const frameRef = useRef(0);
  const drawGen = useRef(0);

  useEffect(() => {
    lengthRef.current = summary?.times_us.length ?? 0;
    loopRef.current = loop;
    frameRef.current = frameIndex;
    cameraKeyRef.current = cameraId;
    modeRef.current = viewpoint;
  }, [summary, loop, cameraId, viewpoint, frameIndex]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      reduceMotionRef.current = media.matches;
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e100d);
    const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000);
    camera.up.set(0, 0, 1);
    camera.position.set(40, -40, 25);
    const controls = new OrbitControls(camera, canvas);
    controls.target.set(0, 0, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    const points = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true }),
    );
    const boxes = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ vertexColors: true }),
    );
    const mapLines = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ vertexColors: true }),
    );
    mapLines.renderOrder = 0;
    points.renderOrder = 1;
    boxes.renderOrder = 2;
    const frustums = new THREE.Group();
    const agents = new THREE.Group();
    frustums.renderOrder = 3;
    agents.renderOrder = 4;
    scene.add(mapLines, points, boxes, frustums, agents);
    stageRef.current = { points, boxes, mapLines, frustums, agents, camera, controls };
    const clock = new THREE.Clock();
    let frameId = 0;
    const draw = () => {
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      const dt = clock.getDelta();
      const mode = modeRef.current;
      const latest = latestRef.current;
      const goal = goalFor(
        mode,
        latest?.frame ?? null,
        latest?.types ?? new Map(),
        cameraKeyRef.current,
        orbitHomeRef.current,
      );
      const orbitFree = mode === "orbit" && !settleRef.current;
      if (orbitFree) {
        controls.enabled = true;
        controls.enableDamping = true;
        controls.update();
      } else if (goal) {
        controls.enabled = false;
        const arrived = reduceMotionRef.current
          ? (applyGoal(camera, controls.target, goal), true)
          : approachGoal(camera, controls.target, goal, dt);
        if (mode === "orbit" && arrived) {
          applyGoal(camera, controls.target, goal);
          settleRef.current = false;
          controls.enabled = true;
          controls.update();
        }
      }
      camera.updateProjectionMatrix();
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
      mapLines.geometry.dispose();
      stageRef.current = null;
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target;
      if (target instanceof HTMLElement) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || target.isContentEditable) {
          return;
        }
        if (tag === "BUTTON" && event.key === " ") {
          return;
        }
      }
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      if (event.key === " ") {
        event.preventDefault();
        setPlaying((current) => {
          if (current) {
            return false;
          }
          if (lengthRef.current < 2) {
            return false;
          }
          if (!loopRef.current && frameRef.current >= lengthRef.current - 1) {
            setFrameIndex(0);
          }
          return true;
        });
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        setPlaying(false);
        const delta = event.key === "ArrowLeft" ? -1 : 1;
        setFrameIndex((current) => stepIndex(current, delta, lengthRef.current, loopRef.current));
      } else if (event.key === "l" || event.key === "L") {
        setLoop((current) => !current);
      } else if (event.key === "[" || event.key === "]") {
        const direction = event.key === "]" ? 1 : -1;
        setSpeed((current) => neighborSpeed(current, direction));
      } else if (event.key === "1") {
        chooseViewpoint("orbit");
      } else if (event.key === "2") {
        chooseViewpoint("bird");
      } else if (event.key === "3") {
        chooseViewpoint("chase");
      } else if (event.key === "4") {
        chooseViewpoint("drone");
      } else if (event.key === "5") {
        chooseViewpoint("camera");
      }
    }
    function chooseViewpoint(next: Viewpoint) {
      if (next === "orbit") {
        settleRef.current = true;
      }
      modeRef.current = next;
      setViewpoint(next);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!playing || !summary) {
      return;
    }
    const length = summary.times_us.length;
    if (length === 0) {
      return;
    }
    const atEnd = frameIndex >= length - 1;
    const basis = atEnd ? length - 2 : frameIndex;
    const delay = frameDelayMs(summary.times_us, basis, speed);
    if (delay === null) {
      return;
    }
    const id = window.setTimeout(() => {
      if (atEnd && !loop) {
        setPlaying(false);
        return;
      }
      setFrameIndex((current) => stepIndex(current, 1, length, loop));
    }, delay);
    return () => window.clearTimeout(id);
  }, [playing, summary, frameIndex, speed, loop]);

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
        if (item && typeof item === "object" && typeof (item as { scene_id?: unknown }).scene_id === "string") {
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
    cacheRef.current.clear();
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
      setPlaying(false);
      const cameras = body.sensors.filter((sensor) => sensor.modality === "camera");
      setCameraId((current) => {
        const stillThere = cameras.some((sensor) => cameraKey(sensor.agent_id, sensor.sensor_id) === current);
        if (stillThere) {
          return current;
        }
        const first = cameras[0];
        return first ? cameraKey(first.agent_id, first.sensor_id) : "";
      });
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
    const gen = ++drawGen.current;
    const types = new Map(summary.agents.map((agent) => [agent.agent_id, agent.agent_type]));
    const stale = () => drawGen.current !== gen;
    void (async () => {
      const body = await cacheRef.current.loadFrame(summary.scene_id, time, token);
      if (stale()) {
        return;
      }
      latestRef.current = { frame: body, types };
      const drawn = await drawFrame(stage, body, types, hidden, token, cacheRef.current, stale);
      if (!drawn || stale()) {
        return;
      }
      if (framedScene.current !== summary.scene_id) {
        const home = orbitGoal(drawn.positions, drawn.anchor);
        orbitHomeRef.current = home;
        const snap = goalFor(modeRef.current, body, types, cameraKeyRef.current, home) ?? home;
        applyGoal(stage.camera, stage.controls.target, snap);
        stage.controls.enabled = modeRef.current === "orbit";
        settleRef.current = false;
        framedScene.current = summary.scene_id;
      }
      const shown = body.point_sensors.reduce((sum, sensor) => {
        return hidden[sensor.agent_id] ? sum : sum + sensor.count;
      }, 0);
      const boxes = body.boxes.filter((box) => !hidden[box.agent_id]).length;
      const frustums = body.frustums.filter((frustum) => !hidden[frustum.agent_id]).length;
      setStatus(
        `${summary.scene_id} at ${time} µs. ${shown} points, ${boxes} boxes, ${frustums} frustums. ${mapNote}${holdNote(modeRef.current, body, types, cameraKeyRef.current)}`,
      );
      prefetchAhead(cacheRef.current, summary, frameIndex, token);
    })().catch((error: unknown) => {
      if (!stale()) {
        setStatus(error instanceof Error ? error.message : "Could not draw the frame.");
      }
    });
  }, [summary, frameIndex, hidden, token, mapNote]);

  useEffect(() => {
    if (!summary || !stageRef.current) {
      return;
    }
    const stage = stageRef.current;
    const controller = new AbortController();
    void (async () => {
      const response = await fetch(`${apiBase()}/api/scenes/${encodeURIComponent(summary.scene_id)}/map/lines`, {
        headers: authHeaders(token),
        signal: controller.signal,
      });
      if (response.status === 404) {
        replaceMap(stage, new ArrayBuffer(0));
        setMapNote("no map.");
        return;
      }
      if (!response.ok) {
        setMapNote(await errorDetail(response));
        return;
      }
      replaceMap(stage, await response.arrayBuffer());
      setMapNote("map drawn.");
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setMapNote(error instanceof Error ? error.message : "Could not draw the map.");
      }
    });
    return () => controller.abort();
  }, [summary, token]);

  function togglePlay() {
    canvasRef.current?.focus();
    if (!summary || summary.times_us.length < 2) {
      return;
    }
    if (!playing && !loop && frameIndex >= summary.times_us.length - 1) {
      setFrameIndex(0);
      setPlaying(true);
      return;
    }
    setPlaying((current) => !current);
  }

  function chooseViewpoint(next: Viewpoint) {
    if (next === "orbit") {
      settleRef.current = true;
    }
    modeRef.current = next;
    setViewpoint(next);
    canvasRef.current?.focus();
  }

  async function exportRerun() {
    if (!summary) {
      return;
    }
    setExportNote("Writing the .rrd…");
    const response = await fetch(`${apiBase()}/api/scenes/${encodeURIComponent(summary.scene_id)}/export/rerun`, {
      method: "POST",
      headers: { ...authHeaders(token), "X-Coperceive-Request": "1" },
    });
    if (!response.ok) {
      setExportNote(await errorDetail(response));
      return;
    }
    const body = (await response.json()) as { path?: unknown; samples?: unknown };
    setExportNote(`Wrote ${String(body.path)} (${String(body.samples)} samples).`);
  }

  const last = summary ? Math.max(summary.times_us.length - 1, 0) : 0;
  const timeUs = summary?.times_us[Math.min(frameIndex, last)];
  const cameras = (summary?.sensors ?? []).filter((sensor) => sensor.modality === "camera");
  const viewpointLabel = VIEWPOINTS.find((item) => item.id === viewpoint)?.label ?? "Orbit";

  return (
    <section className="card viewer-card">
      <div className="viewer-head">
        <h2>Viewer</h2>
      </div>
      <div className="viewer-toolbar">
        <label htmlFor="scene-id">Scene</label>
        <select id="scene-id" value={sceneId} onChange={(event) => setSceneId(event.target.value)}>
          {scenes.length === 0 ? <option value="">No scenes</option> : null}
          {scenes.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
        <div className="segment" role="group" aria-label="Viewpoint">
          {VIEWPOINTS.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={viewpoint === item.id}
              onClick={() => chooseViewpoint(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <label htmlFor="camera-id">Camera</label>
        <select
          id="camera-id"
          value={cameraId}
          disabled={cameras.length === 0}
          onChange={(event) => {
            cameraKeyRef.current = event.target.value;
            setCameraId(event.target.value);
            chooseViewpoint("camera");
          }}
        >
          {cameras.length === 0 ? <option value="">No cameras</option> : null}
          {cameras.map((sensor) => {
            const key = cameraKey(sensor.agent_id, sensor.sensor_id);
            return (
              <option key={key} value={key}>
                {sensor.sensor_id} · {sensor.agent_id}
              </option>
            );
          })}
        </select>
        <button type="button" className="ghost" onClick={() => void exportRerun()} disabled={!summary}>
          Export Rerun
        </button>
      </div>
      <canvas
        ref={canvasRef}
        className="viewer-canvas"
        tabIndex={0}
        aria-label="Scene view. Click here, then use Space to play and 1 through 5 for viewpoints."
        onPointerDown={() => canvasRef.current?.focus()}
      />
      <div className="transport">
        <button type="button" onClick={togglePlay} disabled={!summary || (summary.times_us.length < 2)}>
          {playing ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          className="ghost"
          onClick={() => {
            setPlaying(false);
            setFrameIndex((current) => stepIndex(current, -1, lengthRef.current, loop));
          }}
          disabled={!summary}
        >
          Step back
        </button>
        <button
          type="button"
          className="ghost"
          onClick={() => {
            setPlaying(false);
            setFrameIndex((current) => stepIndex(current, 1, lengthRef.current, loop));
          }}
          disabled={!summary}
        >
          Step forward
        </button>
        <label htmlFor="speed">Speed</label>
        <select
          id="speed"
          value={String(speed)}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (SPEEDS.includes(next as Speed)) {
              setSpeed(next as Speed);
            }
          }}
        >
          {SPEEDS.map((item) => (
            <option key={item} value={item}>
              {item}×
            </option>
          ))}
        </select>
        <label className="check" htmlFor="loop">
          <input id="loop" type="checkbox" checked={loop} onChange={() => setLoop((current) => !current)} />
          Loop
        </label>
        <label className="clock" htmlFor="scrubber">
          {typeof timeUs === "number" ? formatClock(timeUs) : "0.0 s"} · sample{" "}
          {summary ? Math.min(frameIndex, last) + 1 : 0} / {summary ? summary.times_us.length : 0}
        </label>
        <input
          id="scrubber"
          type="range"
          min={0}
          max={last}
          value={Math.min(frameIndex, last)}
          onChange={(event) => setFrameIndex(Number(event.target.value))}
        />
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
      <p className="muted status-line">{status}</p>
      <p className="hint">
        {playing ? "Playing" : "Paused"} at {speed}×{loop ? ", loop on" : ""}. {viewpointLabel}. Click the
        view, then Space plays, arrows step, L loops, [ ] change speed, and 1–5 pick a viewpoint.
      </p>
      {exportNote ? <p className="muted">{exportNote}</p> : null}
    </section>
  );
}
