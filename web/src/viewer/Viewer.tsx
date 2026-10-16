import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { apiBase, authHeaders, errorDetail } from "../api/client";
import {
  classColor,
  defaultAppearance,
  loadAppearance,
  saveAppearance,
  type Appearance,
} from "./appearance";
import CameraStrip from "./cameras";
import { createFrameCache, type FrameCache } from "./cache";
import { formatClock, indexAfter, neighborSpeed, SPEEDS, stepIndex, type Speed } from "./playback";
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
  solids: THREE.Group;
  requestRender: () => void;
  captureView: (width: number, height: number) => string;
  setTheme: (theme: Appearance["theme"]) => void;
};

type Drawn = { positions: ArrayLike<number>; anchor: { x: number; y: number; z: number } | null };

const COLORS: Record<string, number> = {
  vehicle: 0xe67828,
  uav: 0x2878dc,
};

const EXPORT_SIZES = [
  { id: "640x360", width: 640, height: 360, label: "640×360" },
  { id: "960x540", width: 960, height: 540, label: "960×540" },
  { id: "1280x720", width: 1280, height: 720, label: "1280×720" },
  { id: "1920x1080", width: 1920, height: 1080, label: "1920×1080" },
] as const;

const EXPORT_FPS = [5, 10, 15, 30] as const;

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
    const object = child as THREE.LineSegments;
    object.geometry?.dispose();
    const material = object.material;
    if (Array.isArray(material)) {
      for (const item of material) {
        item.dispose();
      }
    } else {
      material?.dispose();
    }
  }
}

function replaceColored(geometry: THREE.BufferGeometry, positions: Float32Array, colors: Float32Array) {
  const current = geometry.getAttribute("position");
  const colored = geometry.getAttribute("color");
  if (
    current instanceof THREE.BufferAttribute &&
    colored instanceof THREE.BufferAttribute &&
    current.array.length === positions.length &&
    colored.array.length === colors.length
  ) {
    (current.array as Float32Array).set(positions);
    (colored.array as Float32Array).set(colors);
    current.needsUpdate = true;
    colored.needsUpdate = true;
  } else {
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  }
  geometry.computeBoundingSphere();
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

function boxShown(box: FrameDocument["boxes"][number], hidden: Record<string, boolean>, appearance: Appearance) {
  if (!appearance.boxes || hidden[box.agent_id]) {
    return false;
  }
  return typeof box.category !== "string" || !appearance.hiddenClasses.includes(box.category);
}

function boxColor(box: FrameDocument["boxes"][number], types: Map<string, string>, appearance: Appearance) {
  if (appearance.colorBy === "class" && typeof box.category === "string") {
    return classColor(box.category);
  }
  return colorFor(types.get(box.agent_id) ?? "");
}

async function drawFrame(
  stage: Stage,
  frame: FrameDocument,
  types: Map<string, string>,
  hidden: Record<string, boolean>,
  appearance: Appearance,
  token: string,
  cache: FrameCache,
  stale: () => boolean,
): Promise<Drawn | null> {
  let pointCount = 0;
  const sweeps: { xyz: Float32Array; color: THREE.Color }[] = [];
  for (const sensor of frame.point_sensors) {
    if (!appearance.points || hidden[sensor.agent_id] || sensor.count === 0) {
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
    pointCount += xyz.length / 3;
    sweeps.push({ xyz, color: new THREE.Color(colorFor(types.get(sensor.agent_id) ?? "")) });
  }
  if (stale()) {
    return null;
  }
  const positions = new Float32Array(pointCount * 3);
  const colors = new Float32Array(pointCount * 3);
  let cursor = 0;
  for (const sweep of sweeps) {
    positions.set(sweep.xyz, cursor);
    for (let index = 0; index < sweep.xyz.length; index += 3) {
      colors[cursor + index] = sweep.color.r;
      colors[cursor + index + 1] = sweep.color.g;
      colors[cursor + index + 2] = sweep.color.b;
    }
    cursor += sweep.xyz.length;
  }
  replaceColored(stage.points.geometry, positions, colors);

  let boxFloats = 0;
  for (const box of frame.boxes) {
    if (!boxShown(box, hidden, appearance) || !Array.isArray(box.lines)) {
      continue;
    }
    boxFloats += box.lines.length;
  }
  const boxPositions = new Float32Array(boxFloats);
  const boxColors = new Float32Array(boxFloats);
  let boxCursor = 0;
  for (const box of frame.boxes) {
    if (!boxShown(box, hidden, appearance) || !Array.isArray(box.lines)) {
      continue;
    }
    boxPositions.set(box.lines, boxCursor);
    const color = new THREE.Color(boxColor(box, types, appearance));
    for (let index = 0; index < box.lines.length; index += 3) {
      boxColors[boxCursor + index] = color.r;
      boxColors[boxCursor + index + 1] = color.g;
      boxColors[boxCursor + index + 2] = color.b;
    }
    boxCursor += box.lines.length;
  }
  replaceColored(stage.boxes.geometry, boxPositions, boxColors);

  clearGroup(stage.solids);
  if (appearance.boxStyle === "solid") {
    for (const box of frame.boxes) {
      if (!boxShown(box, hidden, appearance) || !box.pose || !isPose(box.pose) || !Array.isArray(box.size_lwh)) {
        continue;
      }
      const [length, width, height] = box.size_lwh;
      if (typeof length !== "number" || typeof width !== "number" || typeof height !== "number") {
        continue;
      }
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(length, width, height),
        new THREE.MeshBasicMaterial({
          color: boxColor(box, types, appearance),
          transparent: true,
          opacity: 0.28,
          depthWrite: false,
        }),
      );
      applyPose(mesh, box.pose);
      stage.solids.add(mesh);
    }
  }

  clearGroup(stage.frustums);
  for (const frustum of frame.frustums) {
    if (!appearance.frustums || hidden[frustum.agent_id] || !isPose(frustum.pose)) {
      continue;
    }
    const lines = frustumLines(frustum.fov_y_rad, frustum.aspect, colorFor(types.get(frustum.agent_id) ?? ""));
    applyPose(lines, frustum.pose);
    stage.frustums.add(lines);
  }

  clearGroup(stage.agents);
  for (const agent of frame.agents) {
    if (!appearance.axes || hidden[agent.agent_id] || !isPose(agent.pose)) {
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
  const [exportSource, setExportSource] = useState("view");
  const [exportFormat, setExportFormat] = useState<"mp4" | "gif" | "png">("mp4");
  const [exportFps, setExportFps] = useState(10);
  const [exportSize, setExportSize] = useState<string>("960x540");
  const [exportStart, setExportStart] = useState(1);
  const [exportEnd, setExportEnd] = useState(10);
  const [exporting, setExporting] = useState(false);
  const [appearance, setAppearance] = useState<Appearance>(loadAppearance);
  const appearanceRef = useRef(appearance);
  appearanceRef.current = appearance;
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  const [viewpoint, setViewpoint] = useState<Viewpoint>("orbit");
  const [cameraId, setCameraId] = useState("");
  const [cameraFrame, setCameraFrame] = useState<FrameDocument | null>(null);
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
  const paintWait = useRef<((time: number) => void) | null>(null);

  useEffect(() => {
    lengthRef.current = summary?.times_us.length ?? 0;
    loopRef.current = loop;
    frameRef.current = frameIndex;
    cameraKeyRef.current = cameraId;
    modeRef.current = viewpoint;
  }, [summary, loop, cameraId, viewpoint, frameIndex]);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = appearance.theme;
    saveAppearance(appearance);
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    stage.points.visible = appearance.points;
    stage.boxes.visible = appearance.boxes;
    stage.frustums.visible = appearance.frustums;
    stage.mapLines.visible = appearance.map;
    stage.agents.visible = appearance.axes;
    stage.solids.visible = appearance.boxes && appearance.boxStyle === "solid";
    const material = stage.points.material;
    if (material instanceof THREE.PointsMaterial) {
      material.size = appearance.pointSize;
    }
    stage.setTheme(appearance.theme);
  }, [appearance]);

  useEffect(() => {
    stageRef.current?.requestRender();
  }, [viewpoint, cameraId]);

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
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
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
    const solids = new THREE.Group();
    frustums.renderOrder = 3;
    agents.renderOrder = 4;
    solids.renderOrder = 2;
    scene.add(mapLines, points, boxes, solids, frustums, agents);
    const clock = new THREE.Clock();
    let frameId = 0;
    let sizedWidth = 0;
    let sizedHeight = 0;
    let sizedRatio = 0;
    let dragging = false;
    let damp = 0;
    const draw = () => {
      frameId = 0;
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      if (width !== sizedWidth || height !== sizedHeight || ratio !== sizedRatio) {
        renderer.setPixelRatio(ratio);
        renderer.setSize(width, height, false);
        sizedWidth = width;
        sizedHeight = height;
        sizedRatio = ratio;
      }
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
      let easing = false;
      if (orbitFree) {
        controls.enabled = true;
        controls.enableDamping = true;
        controls.update();
        if (!dragging && damp > 0) {
          damp -= 1;
        }
        easing = dragging || damp > 0;
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
        easing = !arrived;
      }
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
      if (easing) {
        requestRender();
      }
    };
    const requestRender = () => {
      if (frameId !== 0) {
        return;
      }
      frameId = window.requestAnimationFrame(draw);
    };
    const onStart = () => {
      dragging = true;
      requestRender();
    };
    const onEnd = () => {
      dragging = false;
      damp = 24;
      requestRender();
    };
    controls.addEventListener("start", onStart);
    controls.addEventListener("end", onEnd);
    const resize = new ResizeObserver(requestRender);
    resize.observe(canvas);
    const captureView = (width: number, height: number) => {
      const mode = modeRef.current;
      const latest = latestRef.current;
      const goal = goalFor(
        mode,
        latest?.frame ?? null,
        latest?.types ?? new Map(),
        cameraKeyRef.current,
        orbitHomeRef.current,
      );
      if (goal && mode !== "orbit") {
        applyGoal(camera, controls.target, goal);
      }
      renderer.setPixelRatio(1);
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
      const url = canvas.toDataURL("image/png");
      sizedWidth = 0;
      sizedHeight = 0;
      sizedRatio = 0;
      requestRender();
      return url;
    };
    const setTheme = (theme: Appearance["theme"]) => {
      scene.background = new THREE.Color(theme === "light" ? 0xe7e2d6 : 0x0e100d);
      requestRender();
    };
    stageRef.current = {
      points,
      boxes,
      mapLines,
      frustums,
      agents,
      solids,
      camera,
      controls,
      requestRender,
      captureView,
      setTheme,
    };
    setTheme(appearanceRef.current.theme);
    const pointsMaterial = points.material;
    if (pointsMaterial instanceof THREE.PointsMaterial) {
      pointsMaterial.size = appearanceRef.current.pointSize;
    }
    requestRender();
    return () => {
      resize.disconnect();
      controls.removeEventListener("start", onStart);
      controls.removeEventListener("end", onEnd);
      window.cancelAnimationFrame(frameId);
      controls.dispose();
      renderer.dispose();
      points.geometry.dispose();
      boxes.geometry.dispose();
      mapLines.geometry.dispose();
      clearGroup(solids);
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
    const times = summary.times_us;
    if (times.length < 2) {
      return;
    }
    let originIndex = frameRef.current;
    let cursor = originIndex;
    let origin = performance.now();
    let stopped = false;
    let timeoutId = 0;

    const tick = () => {
      if (stopped) {
        return;
      }
      if (frameRef.current !== cursor) {
        originIndex = frameRef.current;
        cursor = originIndex;
        origin = performance.now();
      }
      let place = indexAfter(times, originIndex, performance.now() - origin, speed, loopRef.current);
      cursor = place.index;
      frameRef.current = place.index;
      setFrameIndex(place.index);
      if (place.done) {
        setPlaying(false);
        return;
      }
      // Drawing may have used part of this gap. Land on the sample that
      // matches the clock now, then wait for the next one. The index never
      // runs ahead of elapsed time.
      place = indexAfter(times, originIndex, performance.now() - origin, speed, loopRef.current);
      if (place.index !== frameRef.current) {
        cursor = place.index;
        frameRef.current = place.index;
        setFrameIndex(place.index);
      }
      if (place.done) {
        setPlaying(false);
        return;
      }
      timeoutId = window.setTimeout(tick, place.remainMs > 0 ? place.remainMs : 16);
    };

    const first = indexAfter(times, originIndex, 0, speed, loopRef.current);
    timeoutId = window.setTimeout(tick, first.remainMs > 0 ? first.remainMs : 16);
    return () => {
      stopped = true;
      window.clearTimeout(timeoutId);
    };
  }, [playing, summary, speed, loop]);

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
      setExportStart(1);
      setExportEnd(Math.min(10, body.times_us.length));
      setExportSource("view");
      setHidden({});
      setPlaying(false);
      setCameraFrame(null);
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
      const drawn = await drawFrame(stage, body, types, hidden, appearance, token, cacheRef.current, stale);
      if (!drawn || stale()) {
        return;
      }
      stage.requestRender();
      if (framedScene.current !== summary.scene_id) {
        const home = orbitGoal(drawn.positions, drawn.anchor);
        orbitHomeRef.current = home;
        const snap = goalFor(modeRef.current, body, types, cameraKeyRef.current, home) ?? home;
        applyGoal(stage.camera, stage.controls.target, snap);
        stage.controls.enabled = modeRef.current === "orbit";
        settleRef.current = false;
        framedScene.current = summary.scene_id;
      }
      const shown = appearance.points
        ? body.point_sensors.reduce((sum, sensor) => {
            return hidden[sensor.agent_id] ? sum : sum + sensor.count;
          }, 0)
        : 0;
      const boxes = body.boxes.filter((box) => boxShown(box, hidden, appearance)).length;
      const frustums = body.frustums.filter((frustum) => appearance.frustums && !hidden[frustum.agent_id]).length;
      setCameraFrame(body);
      setStatus(
        `${summary.scene_id} at ${time} µs. ${shown} points, ${boxes} boxes, ${frustums} frustums. ${mapNote}${holdNote(modeRef.current, body, types, cameraKeyRef.current)}`,
      );
      prefetchAhead(cacheRef.current, summary, frameIndex, token);
      paintWait.current?.(time);
    })().catch((error: unknown) => {
      if (!stale()) {
        setStatus(error instanceof Error ? error.message : "Could not draw the frame.");
      }
    });
  }, [summary, frameIndex, hidden, appearance, token, mapNote]);

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
        stage.requestRender();
        setMapNote("no map.");
        return;
      }
      if (!response.ok) {
        setMapNote(await errorDetail(response));
        return;
      }
      replaceMap(stage, await response.arrayBuffer());
      stage.requestRender();
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

  function exportSizeChoice() {
    return EXPORT_SIZES.find((item) => item.id === exportSize) ?? EXPORT_SIZES[1];
  }

  async function waitForSample(index: number, time: number) {
    if (frameRef.current === index && latestRef.current?.frame.time_us === time) {
      await new Promise((resolve) => {
        window.requestAnimationFrame(() => resolve(undefined));
      });
      return;
    }
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        paintWait.current = null;
        reject(new Error("That sample did not finish drawing."));
      }, 8000);
      paintWait.current = (drawn) => {
        if (drawn !== time) {
          return;
        }
        paintWait.current = null;
        window.clearTimeout(timer);
        window.requestAnimationFrame(() => resolve());
      };
      setFrameIndex(index);
    });
  }

  async function postExport(scene: string, path: string, payload: unknown) {
    const response = await fetch(`${apiBase()}/api/scenes/${encodeURIComponent(scene)}${path}`, {
      method: "POST",
      headers: {
        ...authHeaders(token),
        "X-Coperceive-Request": "1",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    return (await response.json()) as {
      path?: unknown;
      frames?: unknown;
      width?: unknown;
      height?: unknown;
      fps?: unknown;
    };
  }

  async function downloadExported(relative: string) {
    const file = relative.endsWith(".mp4") || relative.endsWith(".gif") || relative.endsWith(".png");
    if (!file) {
      return;
    }
    const response = await fetch(`${apiBase()}/api/files?path=${encodeURIComponent(relative)}`, {
      headers: authHeaders(token),
    });
    if (!response.ok) {
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = relative.split("/").pop() ?? "sequence";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function exportSequence(still: boolean) {
    if (!summary || exporting) {
      return;
    }
    const length = summary.times_us.length;
    const start = still ? frameIndex : exportStart - 1;
    const end = still ? frameIndex : exportEnd - 1;
    if (start < 0 || end < start || end >= length) {
      setExportNote("The sample range is outside this scene.");
      return;
    }
    const count = end - start + 1;
    if (exportSource === "view" && count > 60) {
      setExportNote("This view exports at most 60 samples. A camera can cover a longer range.");
      return;
    }
    const size = exportSizeChoice();
    const format = still ? "png" : exportFormat;
    setExporting(true);
    setPlaying(false);
    setExportNote(still ? "Writing the still…" : "Writing the sequence…");
    try {
      const startUs = summary.times_us[start];
      const endUs = summary.times_us[end];
      let saved: { path?: unknown; frames?: unknown; width?: unknown; height?: unknown; fps?: unknown };
      if (exportSource === "view") {
        const stage = stageRef.current;
        if (!stage) {
          throw new Error("The view is not ready.");
        }
        const frames: string[] = [];
        for (let index = start; index <= end; index += 1) {
          const time = summary.times_us[index];
          if (typeof time !== "number") {
            throw new Error("That sample is not in the scene.");
          }
          setExportNote(`Drawing sample ${index + 1}…`);
          await waitForSample(index, time);
          const url = stage.captureView(size.width, size.height);
          frames.push(url.slice(url.indexOf(",") + 1));
        }
        saved = await postExport(summary.scene_id, "/export/view", {
          format,
          fps: exportFps,
          start_us: startUs,
          end_us: endUs,
          frames_b64: frames,
        });
      } else {
        const slash = exportSource.indexOf("/");
        saved = await postExport(summary.scene_id, "/export/sequence", {
          agent_id: exportSource.slice(0, slash),
          sensor_id: exportSource.slice(slash + 1),
          start_us: startUs,
          end_us: endUs,
          fps: exportFps,
          width: size.width,
          height: size.height,
          format,
        });
      }
      const path = typeof saved.path === "string" ? saved.path : "";
      setExportNote(
        `Wrote ${path} (${String(saved.frames)} frames, ${String(saved.width)}×${String(saved.height)} at ${String(saved.fps)} fps).`,
      );
      await downloadExported(path);
    } catch (error) {
      setExportNote(error instanceof Error ? error.message : "Export failed.");
    } finally {
      setExporting(false);
    }
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
  const agentTypes = useMemo(
    () => new Map((summary?.agents ?? []).map((agent) => [agent.agent_id, agent.agent_type])),
    [summary],
  );
  const viewpointLabel = VIEWPOINTS.find((item) => item.id === viewpoint)?.label ?? "Orbit";
  const categories = useMemo(() => {
    const names = new Set<string>();
    for (const box of cameraFrame?.boxes ?? []) {
      if (typeof box.category === "string" && box.category) {
        names.add(box.category);
      }
    }
    return [...names].sort();
  }, [cameraFrame]);

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
        <button type="button" className="ghost" onClick={() => void exportRerun()} disabled={!summary || exporting}>
          Export Rerun
        </button>
      </div>
      <div className="export-bar">
        <label htmlFor="export-source">Export</label>
        <select
          id="export-source"
          value={exportSource}
          onChange={(event) => setExportSource(event.target.value)}
        >
          <option value="view">This view</option>
          {cameras.map((sensor) => {
            const key = cameraKey(sensor.agent_id, sensor.sensor_id);
            return (
              <option key={key} value={key}>
                {sensor.sensor_id} · {sensor.agent_id}
              </option>
            );
          })}
        </select>
        <label htmlFor="export-format">Format</label>
        <select
          id="export-format"
          value={exportFormat}
          onChange={(event) => {
            const next = event.target.value;
            if (next === "mp4" || next === "gif" || next === "png") {
              setExportFormat(next);
            }
          }}
        >
          <option value="mp4">MP4</option>
          <option value="gif">GIF</option>
          <option value="png">PNG frames</option>
        </select>
        <label htmlFor="export-fps">fps</label>
        <select
          id="export-fps"
          value={String(exportFps)}
          onChange={(event) => setExportFps(Number(event.target.value))}
        >
          {EXPORT_FPS.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <label htmlFor="export-size">Size</label>
        <select id="export-size" value={exportSize} onChange={(event) => setExportSize(event.target.value)}>
          {EXPORT_SIZES.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <label htmlFor="export-start">From</label>
        <input
          id="export-start"
          type="number"
          min={1}
          max={summary ? summary.times_us.length : 1}
          value={exportStart}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isInteger(next)) {
              setExportStart(next);
            }
          }}
        />
        <label htmlFor="export-end">to</label>
        <input
          id="export-end"
          type="number"
          min={1}
          max={summary ? summary.times_us.length : 1}
          value={exportEnd}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isInteger(next)) {
              setExportEnd(next);
            }
          }}
        />
        <button type="button" className="ghost" onClick={() => void exportSequence(true)} disabled={!summary || exporting}>
          Save still
        </button>
        <button type="button" onClick={() => void exportSequence(false)} disabled={!summary || exporting}>
          Export sequence
        </button>
        <span className="muted">
          {exportNote || "Each sample in the range is one frame. 10 fps matches a 10 Hz recording."}
        </span>
      </div>
      <div className="customise-bar">
        <span className="muted">View</span>
        {(
          [
            ["points", "Points"],
            ["boxes", "Boxes"],
            ["frustums", "Frustums"],
            ["map", "Map"],
            ["axes", "Axes"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="check">
            <input
              type="checkbox"
              checked={appearance[key]}
              onChange={() => setAppearance((current) => ({ ...current, [key]: !current[key] }))}
            />
            {label}
          </label>
        ))}
        <label htmlFor="color-by">Color</label>
        <select
          id="color-by"
          value={appearance.colorBy}
          onChange={(event) =>
            setAppearance((current) => ({
              ...current,
              colorBy: event.target.value === "class" ? "class" : "agent",
            }))
          }
        >
          <option value="agent">Agent</option>
          <option value="class">Class</option>
        </select>
        <label htmlFor="point-size">Point size</label>
        <select
          id="point-size"
          value={String(appearance.pointSize)}
          onChange={(event) =>
            setAppearance((current) => ({ ...current, pointSize: Number(event.target.value) }))
          }
        >
          {[1, 2, 3, 4].map((size) => (
            <option key={size} value={size}>
              {size} px
            </option>
          ))}
        </select>
        <label htmlFor="box-style">Boxes</label>
        <select
          id="box-style"
          value={appearance.boxStyle}
          onChange={(event) =>
            setAppearance((current) => ({
              ...current,
              boxStyle: event.target.value === "solid" ? "solid" : "wire",
            }))
          }
        >
          <option value="wire">Wire</option>
          <option value="solid">Solid</option>
        </select>
        <label htmlFor="theme">Theme</label>
        <select
          id="theme"
          value={appearance.theme}
          onChange={(event) =>
            setAppearance((current) => ({
              ...current,
              theme: event.target.value === "light" ? "light" : "dark",
            }))
          }
        >
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
        <button type="button" className="ghost" onClick={() => setAppearance(defaultAppearance())}>
          Reset view
        </button>
        {categories.map((category) => (
          <label key={category} className="check">
            <input
              type="checkbox"
              checked={!appearance.hiddenClasses.includes(category)}
              onChange={() =>
                setAppearance((current) => {
                  const hidden = new Set(current.hiddenClasses);
                  if (hidden.has(category)) {
                    hidden.delete(category);
                  } else {
                    hidden.add(category);
                  }
                  return { ...current, hiddenClasses: [...hidden] };
                })
              }
            />
            {category}
          </label>
        ))}
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
      <p className="muted status-line">{status}</p>
      <p className="hint">
        {playing ? "Playing" : "Paused"} at {speed}×{loop ? ", loop on" : ""}. {viewpointLabel}. Click the
        view, then Space plays, arrows step, L loops, [ ] change speed, and 1–5 pick a viewpoint.
      </p>
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
      {cameraFrame && summary ? (
        <CameraStrip
          token={token}
          sceneId={summary.scene_id}
          timeUs={cameraFrame.time_us}
          nextTimeUs={summary.times_us[frameIndex + 1] ?? null}
          frustums={cameraFrame.frustums}
          boxes={cameraFrame.boxes}
          pointSensors={cameraFrame.point_sensors}
          types={agentTypes}
          hidden={hidden}
          cacheRef={cacheRef}
        />
      ) : null}
      {exportNote ? <p className="muted">{exportNote}</p> : null}
    </section>
  );
}
