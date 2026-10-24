import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { apiBase, authHeaders, errorDetail } from "../api/client";
import {
  classColor,
  defaultAppearance,
  loadAppearance,
  saveAppearance,
  setAllFrustumImages,
  setNoFrustumImages,
  showCameraImage,
  toggleFrustumImages,
  toggleOneFrustumImage,
  type Appearance,
} from "./appearance";
import { BLOOM_LAYER, createBloom, enableBloom, type BloomRig } from "./bloom";
import CameraStrip, { CameraSplit, loadImage } from "./cameras";
import { createFrameCache, type FrameCache } from "./cache";
import { EyeToggle, IconMoon, IconSun, Menu, Segmented, Slider, SwatchToggle } from "./controls";
import { createImagePlane, disposeImageTextures, FRUSTUM_DEPTH, FRUSTUM_TEXTURE, textureForBitmap } from "./frustumImages";
import { boxCenter, followPick, type PickRecord } from "./inspect";
import { pickFromObject, syncFrustumPicks, syncPicks } from "./picks";
import { Inspector, Radar, SceneCard } from "./SidePanel";
import ExportDialog from "./ExportDialog";
import { formatClock, indexAfter, isEndZone, neighborSpeed, stepIndex, type Speed } from "./playback";
import {
  buildPredictionObjects,
  disposePredictionGroup,
  STATUS_CHIPS,
  STATUS_WORDS,
  type LayerResult,
  type StatusChip,
} from "./predictionLayer";
import PredictionsBlock, { LineSample } from "./predictions";
import { imageNote, isMapInfo, lidarNote, mapNoteFor, missingNotes, type MapInfo } from "./emptyState";
import { SPLIT_MAX, SPLIT_MIN } from "./appearance";
import {
  alignSeries,
  defaultOutput,
  followEvalPick,
  gtEvalDetail,
  gtKey,
  isPredictionFrame,
  isPredictionSeries,
  isRunList,
  outputKey,
  summarizeOutput,
  type CooperativeView,
  type EvalPick,
  type PredictionFrame,
  type PredictionRow,
  type PredictionSeries,
  type RunInfo,
} from "./predictionTypes";
import { formatHeading, formatSpeed, headingDeg, sparkPaths, speedMps } from "./telemetry";
import Timeline, { type Marker } from "./timeline";
import {
  cameraKey,
  isPose,
  isSummary,
  type FrameBox,
  type FrameDocument,
  type FrameFrustum,
  type Pose,
  type SceneSummary,
  agentLabels,
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
  renderNow: () => void;
  captureView: (width: number, height: number) => string;
  setTheme: (theme: Appearance["theme"]) => void;
  bloom: BloomRig;
  labelRenderer: CSS2DRenderer;
  trails: THREE.Group;
  leaders: THREE.Group;
  labels: THREE.Group;
  trailLog: Map<string, Map<number, { x: number; y: number; z: number }>>;
  trailLines: Map<string, THREE.Line>;
  leaderLines: Map<string, THREE.Line>;
  labelNodes: Map<string, CSS2DObject>;
  clearMotion: () => void;
  picks: THREE.Group;
  frustumPicks: THREE.Group;
  images: THREE.Group;
  imagePlanes: Map<string, THREE.Mesh>;
  highlight: THREE.Mesh;
  /** Prediction wires per status (and per run in compare mode), ADR 0010. */
  predictions: THREE.Group;
  /** Invisible pick meshes for the drawn prediction rows. */
  predictionPicks: THREE.Group;
  /** Ground-truth wires a shown prediction matched, at 40% opacity. */
  dimmedBoxes: THREE.LineSegments;
};

type Drawn = { positions: ArrayLike<number>; anchor: { x: number; y: number; z: number } | null };

/** Which ground-truth wires to dim at which sample; stale for any other sample. */
type Dimmed = { timeUs: number; keys: ReadonlySet<string> };

const PREDICTION_FRAME_LIMIT = 16;

type PredictionCache = {
  clear: () => void;
  load: (sceneId: string, runId: string, compareId: string, timeUs: number, token: string) => Promise<PredictionFrame>;
};

/** `/predictions/{run}/frame` bodies, the same small LRU and in-flight sharing as the frame cache. */
function createPredictionCache(): PredictionCache {
  const frames = new Map<string, PredictionFrame>();
  const inflight = new Map<string, Promise<PredictionFrame>>();
  return {
    clear() {
      frames.clear();
    },
    load(sceneId, runId, compareId, timeUs, token) {
      const key = `${sceneId}|${runId}|${compareId}|${timeUs}`;
      const cached = frames.get(key);
      if (cached) {
        frames.delete(key);
        frames.set(key, cached);
        return Promise.resolve(cached);
      }
      const pending = inflight.get(key);
      if (pending) {
        return pending;
      }
      const promise = (async () => {
        const compare = compareId ? `&compare=${encodeURIComponent(compareId)}` : "";
        const response = await fetch(
          `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/predictions/${encodeURIComponent(runId)}/frame?time_us=${timeUs}${compare}`,
          { headers: authHeaders(token) },
        );
        if (!response.ok) {
          throw new Error(await errorDetail(response));
        }
        const body = (await response.json()) as unknown;
        if (!isPredictionFrame(body)) {
          throw new Error("The prediction frame was incomplete.");
        }
        frames.set(key, body);
        while (frames.size > PREDICTION_FRAME_LIMIT) {
          const oldest = frames.keys().next().value;
          if (oldest === undefined) {
            break;
          }
          frames.delete(oldest);
        }
        return body;
      })().finally(() => {
        inflight.delete(key);
      });
      inflight.set(key, promise);
      return promise;
    },
  };
}

function toggleIn<T>(set: ReadonlySet<T>, item: T): Set<T> {
  const next = new Set(set);
  if (next.has(item)) {
    next.delete(item);
  } else {
    next.add(item);
  }
  return next;
}

const COLORS: Record<string, number> = {
  vehicle: 0xf58a1f,
  uav: 0x3c8dff,
};

const AGENT_INK: Record<string, string> = {
  vehicle: "#F58A1F",
  uav: "#3C8DFF",
  infrastructure: "#b7b1a6",
  other: "#b7b1a6",
};
/** Legend words per agent type, in legend order; only the types present in the scene are shown. */
const LEGEND_ORDER = ["vehicle", "uav", "infrastructure", "other"] as const;
const LEGEND_LABELS: Record<string, string> = {
  vehicle: "Vehicle",
  uav: "Drone",
  infrastructure: "RSU",
  other: "Agent",
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

function fadingGrid(): THREE.LineSegments {
  const size = 240;
  const divisions = 24;
  const step = size / divisions;
  const half = size / 2;
  const positions: number[] = [];
  for (let index = 0; index <= divisions; index += 1) {
    const place = -half + index * step;
    positions.push(-half, place, 0, half, place, 0, place, -half, 0, place, half, 0);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { uColor: { value: new THREE.Color("#3a3a3a") } },
    vertexShader: `
      varying vec3 vWorld;
      void main() {
        vec4 world = modelMatrix * vec4(position, 1.0);
        vWorld = world.xyz;
        gl_Position = projectionMatrix * viewMatrix * world;
      }
    `,
    fragmentShader: `
      uniform vec3 uColor;
      varying vec3 vWorld;
      void main() {
        float depth = distance(cameraPosition, vWorld);
        float fade = smoothstep(150.0, 24.0, depth);
        gl_FragColor = vec4(uColor, fade * 0.55);
      }
    `,
  });
  const lines = new THREE.LineSegments(geometry, material);
  lines.frustumCulled = false;
  return lines;
}

function clampDroneTags(host: HTMLElement) {
  const bounds = host.getBoundingClientRect();
  const pad = 10;
  for (const node of host.querySelectorAll<HTMLElement>(".drone-tag")) {
    node.classList.remove("clamped");
    const box = node.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) {
      continue;
    }
    let dx = 0;
    let dy = 0;
    if (box.left < bounds.left + pad) {
      dx = bounds.left + pad - box.left;
    } else if (box.right > bounds.right - pad) {
      dx = bounds.right - pad - box.right;
    }
    if (box.top < bounds.top + pad) {
      dy = bounds.top + pad - box.top;
    } else if (box.bottom > bounds.bottom - pad) {
      dy = bounds.bottom - pad - box.bottom;
    }
    if (dx !== 0 || dy !== 0) {
      node.style.transform = `${node.style.transform} translate(${dx}px, ${dy}px)`;
      node.classList.add("clamped");
    }
  }
}

function cssColor(value: number): string {
  return `#${value.toString(16).padStart(6, "0")}`;
}

function applyPose(object: THREE.Object3D, pose: Pose) {
  object.position.set(pose.tx, pose.ty, pose.tz);
  object.quaternion.set(pose.qx, pose.qy, pose.qz, pose.qw);
}

function frustumLines(fovY: number, aspect: number, color: number): THREE.LineSegments {
  const depth = FRUSTUM_DEPTH;
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
  const lane = new THREE.Color(0xc6f3dc);
  const edge = new THREE.Color(0xc5d0d8);
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

let sceneDrawToken = 0;

function bumpSceneDrawToken(): number {
  sceneDrawToken += 1;
  return sceneDrawToken;
}

function tagObject(object: THREE.Object3D, sceneId: string) {
  object.userData.coperceiveSceneId = sceneId;
  object.userData.coperceiveLoadId = sceneDrawToken;
}

function untagObject(object: THREE.Object3D) {
  delete object.userData.coperceiveSceneId;
  delete object.userData.coperceiveLoadId;
}

function tagDrawn(stage: Stage, sceneId: string) {
  tagObject(stage.points, sceneId);
  tagObject(stage.boxes, sceneId);
  tagObject(stage.dimmedBoxes, sceneId);
  tagObject(stage.mapLines, sceneId);
  for (const group of [stage.frustums, stage.agents, stage.solids, stage.images]) {
    for (const child of group.children) {
      tagObject(child, sceneId);
    }
  }
}

function sceneGraphAudit(stage: Stage | null) {
  if (!stage) {
    return null;
  }
  const tagged: { kind: string; sceneId: string; loadId: number }[] = [];
  const take = (kind: string, object: THREE.Object3D) => {
    const sceneId = object.userData.coperceiveSceneId;
    const loadId = object.userData.coperceiveLoadId;
    if (typeof sceneId !== "string") {
      return;
    }
    tagged.push({ kind, sceneId, loadId: typeof loadId === "number" ? loadId : -1 });
  };
  take("points", stage.points);
  take("boxes", stage.boxes);
  take("dimmedBoxes", stage.dimmedBoxes);
  take("mapLines", stage.mapLines);
  for (const child of stage.frustums.children) {
    take("frustum", child);
  }
  for (const child of stage.agents.children) {
    take("agent", child);
  }
  for (const child of stage.solids.children) {
    take("solid", child);
  }
  for (const child of stage.images.children) {
    take("image", child);
  }
  const pointAttr = stage.points.geometry.getAttribute("position");
  const boxAttr = stage.boxes.geometry.getAttribute("position");
  const dimAttr = stage.dimmedBoxes.geometry.getAttribute("position");
  const points = stage.points.visible && pointAttr ? pointAttr.count : 0;
  const boxes =
    ((stage.boxes.visible && boxAttr ? boxAttr.count : 0) + (stage.dimmedBoxes.visible && dimAttr ? dimAttr.count : 0)) / 24;
  return { token: sceneDrawToken, points, boxes, tagged };
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
    geometry.dispose();
  }
  geometry.setDrawRange(0, positions.length / 3);
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

/** The key a frame box shares with its `matches.parquet` ground-truth row: track id, else the world centre. */
function frameGtKey(box: FrameBox): string {
  return gtKey(box.agent_id, box.track_id, boxCenter(box));
}

function fillWire(positions: Float32Array, colors: Float32Array, cursor: number, box: FrameBox, color: THREE.Color): number {
  positions.set(box.lines, cursor);
  for (let index = 0; index < box.lines.length; index += 3) {
    colors[cursor + index] = color.r;
    colors[cursor + index + 1] = color.g;
    colors[cursor + index + 2] = color.b;
  }
  return cursor + box.lines.length;
}

/** Ground-truth wires and solids. A box in `dimmed` (matched by a shown prediction) goes to the 40% layer. */
function drawBoxes(
  stage: Stage,
  frame: FrameDocument,
  types: Map<string, string>,
  hidden: Record<string, boolean>,
  appearance: Appearance,
  dimmed: Dimmed | null,
) {
  const keys = dimmed && dimmed.timeUs === frame.time_us && dimmed.keys.size > 0 ? dimmed.keys : null;
  const shown = frame.boxes.filter((box) => boxShown(box, hidden, appearance) && Array.isArray(box.lines));
  const dims = shown.map((box) => keys !== null && keys.has(frameGtKey(box)));
  let boxFloats = 0;
  let dimFloats = 0;
  shown.forEach((box, index) => {
    if (dims[index]) {
      dimFloats += box.lines.length;
    } else {
      boxFloats += box.lines.length;
    }
  });
  const boxPositions = new Float32Array(boxFloats);
  const boxColors = new Float32Array(boxFloats);
  const dimPositions = new Float32Array(dimFloats);
  const dimColors = new Float32Array(dimFloats);
  let boxCursor = 0;
  let dimCursor = 0;
  shown.forEach((box, index) => {
    const color = new THREE.Color(boxColor(box, types, appearance));
    if (dims[index]) {
      dimCursor = fillWire(dimPositions, dimColors, dimCursor, box, color);
    } else {
      boxCursor = fillWire(boxPositions, boxColors, boxCursor, box, color);
    }
  });
  replaceColored(stage.boxes.geometry, boxPositions, boxColors);
  replaceColored(stage.dimmedBoxes.geometry, dimPositions, dimColors);
  stage.boxes.visible = boxPositions.length > 0;
  stage.dimmedBoxes.visible = dimPositions.length > 0;

  clearGroup(stage.solids);
  if (appearance.boxStyle === "solid") {
    shown.forEach((box, index) => {
      if (!box.pose || !isPose(box.pose) || !Array.isArray(box.size_lwh)) {
        return;
      }
      const [length, width, height] = box.size_lwh;
      if (typeof length !== "number" || typeof width !== "number" || typeof height !== "number") {
        return;
      }
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(length, width, height),
        new THREE.MeshBasicMaterial({
          color: boxColor(box, types, appearance),
          transparent: true,
          opacity: dims[index] ? 0.28 * 0.4 : 0.28,
          depthWrite: false,
        }),
      );
      applyPose(mesh, box.pose);
      stage.solids.add(mesh);
    });
  }
}

async function drawFrame(
  stage: Stage,
  frame: FrameDocument,
  types: Map<string, string>,
  hidden: Record<string, boolean>,
  appearance: Appearance,
  token: string,
  cache: FrameCache,
  dimmed: () => Dimmed | null,
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
      (window as unknown as { __LAST_DRAW__?: unknown }).__LAST_DRAW__ = { stale: true, where: "mid-sensor" };
      return null;
    }
    const xyz = new Float32Array(buffer);
    pointCount += xyz.length / 3;
    sweeps.push({ xyz, color: new THREE.Color(colorFor(types.get(sensor.agent_id) ?? "")) });
  }
  if (stale()) {
    (window as unknown as { __LAST_DRAW__?: unknown }).__LAST_DRAW__ = { stale: true, where: "post-sensor" };
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
  stage.points.visible = positions.length > 0;
  (window as unknown as { __LAST_DRAW__?: unknown }).__LAST_DRAW__ = {
    step: "drawn",
    pointCount,
    positionsLength: positions.length,
    visible: stage.points.visible,
    timeUs: frame.time_us,
    sceneId: frame.scene_id,
  };
  // Read the dim set now, not at call time: the prediction frame for this sample may have arrived while
  // the point buffers were loading.
  drawBoxes(stage, frame, types, hidden, appearance, dimmed());

  clearGroup(stage.frustums);
  const pickable: { key: string; fovY: number; aspect: number; pose: Pose }[] = [];
  for (const frustum of frame.frustums) {
    if (!appearance.frustums || hidden[frustum.agent_id] || !isPose(frustum.pose)) {
      continue;
    }
    const lines = frustumLines(frustum.fov_y_rad, frustum.aspect, colorFor(types.get(frustum.agent_id) ?? ""));
    applyPose(lines, frustum.pose);
    stage.frustums.add(lines);
    pickable.push({
      key: cameraKey(frustum.agent_id, frustum.sensor_id),
      fovY: frustum.fov_y_rad,
      aspect: frustum.aspect,
      pose: frustum.pose,
    });
  }
  syncFrustumPicks(stage.frustumPicks, pickable);

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
  tagDrawn(stage, frame.scene_id);
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

function syncMotion(stage: Stage, frame: FrameDocument, types: Map<string, string>, hidden: Record<string, boolean>) {
  const alive = new Set<string>();
  for (const agent of frame.agents) {
    if (!isPose(agent.pose)) {
      continue;
    }
    alive.add(agent.agent_id);
    let log = stage.trailLog.get(agent.agent_id);
    if (!log) {
      log = new Map();
      stage.trailLog.set(agent.agent_id, log);
    }
    log.set(frame.time_us, { x: agent.pose.tx, y: agent.pose.ty, z: agent.pose.tz });
    const times = [...log.keys()].sort((a, b) => a - b);
    const positions = new Float32Array(times.length * 3);
    for (let index = 0; index < times.length; index += 1) {
      const point = log.get(times[index] ?? 0);
      if (!point) {
        continue;
      }
      positions[index * 3] = point.x;
      positions[index * 3 + 1] = point.y;
      positions[index * 3 + 2] = point.z;
    }
    let line = stage.trailLines.get(agent.agent_id);
    if (!line) {
      line = new THREE.Line(
        new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({ color: colorFor(types.get(agent.agent_id) ?? "") }),
      );
      line.frustumCulled = false;
      enableBloom(line);
      stage.trails.add(line);
      stage.trailLines.set(agent.agent_id, line);
    }
    line.geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    line.geometry.computeBoundingSphere();
    const material = line.material;
    if (material instanceof THREE.LineBasicMaterial) {
      material.color.setHex(colorFor(types.get(agent.agent_id) ?? ""));
    }
    line.visible = !hidden[agent.agent_id] && times.length > 1;

    const drone = types.get(agent.agent_id) === "uav";
    let leader = stage.leaderLines.get(agent.agent_id);
    if (drone && !leader) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
      leader = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: COLORS.uav }));
      leader.frustumCulled = false;
      stage.leaders.add(leader);
      stage.leaderLines.set(agent.agent_id, leader);
    }
    if (leader) {
      const attribute = leader.geometry.getAttribute("position");
      if (attribute instanceof THREE.BufferAttribute && attribute.array instanceof Float32Array) {
        const array = attribute.array;
        array[0] = agent.pose.tx;
        array[1] = agent.pose.ty;
        array[2] = agent.pose.tz;
        array[3] = agent.pose.tx;
        array[4] = agent.pose.ty;
        array[5] = 0;
        attribute.needsUpdate = true;
      }
      leader.visible = drone && !hidden[agent.agent_id];
    }
    let label = stage.labelNodes.get(agent.agent_id);
    if (drone && !label) {
      const element = document.createElement("div");
      element.className = "drone-tag num";
      label = new CSS2DObject(element);
      label.center.set(0.5, 1.2);
      stage.labels.add(label);
      stage.labelNodes.set(agent.agent_id, label);
    }
    if (label) {
      label.position.set(agent.pose.tx, agent.pose.ty, agent.pose.tz);
      label.element.textContent = `${agent.agent_id}  ${agent.pose.tz.toFixed(1)} m`;
      label.visible = drone && !hidden[agent.agent_id];
    }
  }
  for (const [id, line] of stage.trailLines) {
    if (!alive.has(id)) {
      line.visible = false;
    }
  }
}

export type ClockMark = { ratio: number; label: string };

declare global {
  interface Window {
    __COPERCEIVE_DESKTOP__?: boolean;
    __COPERCEIVE_CTRL_PORT__?: number;
    __COPERCEIVE_CTRL_SECRET__?: string;
    __COPERCEIVE_DATA_ROOT__?: string;
    __COPERCEIVE_DATA_BASENAME__?: string;
    __COPERCEIVE_INITIAL_STATE__?: {
      last_scene_id?: string;
      last_sample_index?: number;
      last_timestamp_us?: number;
      last_timestamp_ns?: number;
      last_viewpoint?: string;
      last_theme?: string;
      reopen_last_session?: boolean;
      agent_visibility?: Record<string, boolean>;
      layer_toggles?: {
        points?: boolean;
        boxes?: boolean;
        frustums?: boolean;
        map?: boolean;
        axes?: boolean;
        frustum_images?: boolean;
        frustum_opacity?: number;
        frustum_image_cameras?: string[] | null;
      };
      selected_camera?: string;
      split_view?: boolean;
      panel_collapse?: {
        camera_strip_open?: boolean;
      };
      last_loop?: boolean;
    };
    __COPERCEIVE_VIEWER_STATE__?: unknown;
    __COPERCEIVE_SHOW_TOAST__?: (text: string, type?: "error" | "info") => void;
    __COPERCEIVE_SET_VIEWPOINT__?: (vp: string) => void;
    __COPERCEIVE_TOGGLE_THEME__?: () => void;
    __COPERCEIVE_PLAYBACK__?: (action: string, value?: number) => void;
    __COPERCEIVE_PLAYBACK_STATE__?: () => {
      playing: boolean;
      loop: boolean;
      frameIndex: number;
      timeUs: number | null;
      samples: number;
    };
    __COPERCEIVE_DATASET_CHANGED__?: (
      origin: string,
      token: string,
      basename: string,
      root: string
    ) => void;
    __COPERCEIVE_DATASET_ERROR__?: (error: string) => void;
    __COPERCEIVE_SCENES__?: () => string[];
    __COPERCEIVE_TEST_ENV__?: boolean;
    __COPERCEIVE_FORCE_MOTION__?: boolean;
    __COPERCEIVE_VIEW_SETTINGS__?: () => {
      sceneId: string;
      viewpoint: string;
      layers: {
        points: boolean;
        boxes: boolean;
        frustums: boolean;
        map: boolean;
        axes: boolean;
        frustumImages: boolean;
      };
      hiddenAgents: Record<string, boolean>;
      hiddenClasses: string[];
    };
    __COPERCEIVE_SCENE_GRAPH__?: () => {
      token: number;
      points: number;
      boxes: number;
      tagged: { kind: string; sceneId: string; loadId: number }[];
    } | null;
  }
}

function clearStage(stage: Stage) {
  stage.points.geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.points.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.boxes.geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.boxes.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.dimmedBoxes.geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.dimmedBoxes.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.mapLines.geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.mapLines.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
  stage.frustums.clear();
  stage.agents.clear();
  stage.solids.clear();
  stage.picks.clear();
  stage.frustumPicks.clear();
  stage.images.clear();
  stage.imagePlanes.clear();
  stage.predictions.clear();
  stage.predictionPicks.clear();
  stage.clearMotion();
  untagObject(stage.points);
  untagObject(stage.boxes);
  untagObject(stage.dimmedBoxes);
  untagObject(stage.mapLines);
  stage.requestRender();
}

const LOOP_STORAGE_KEY = "coperceive-loop-preference";

function readWebLoopChoice(): boolean | null {
  try {
    const val = localStorage.getItem(LOOP_STORAGE_KEY);
    if (val === "true") return true;
    if (val === "false") return false;
  } catch {
    /* private mode */
  }
  return null;
}

function writeWebLoopChoice(val: boolean) {
  try {
    localStorage.setItem(LOOP_STORAGE_KEY, String(val));
  } catch {
    /* private mode */
  }
}

function postControllerState(body: Record<string, unknown>) {
  if (typeof window === "undefined" || !window.__COPERCEIVE_CTRL_PORT__) {
    return;
  }
  const port = window.__COPERCEIVE_CTRL_PORT__;
  const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
  void fetch(`http://127.0.0.1:${port}/state`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${secret}`,
    },
    body: JSON.stringify(body),
  }).catch(() => undefined);
}

function savedLoopChoice(): boolean | null {
  if (typeof window === "undefined") {
    return null;
  }
  const saved = window.__COPERCEIVE_INITIAL_STATE__?.last_loop;
  if (typeof saved === "boolean") {
    return saved;
  }
  // The desktop sidecar origin changes every launch, so localStorage is not
  // the resume record there. The fixed-port web app still uses it.
  if (window.__COPERCEIVE_DESKTOP__ || window.__COPERCEIVE_CTRL_PORT__) {
    return null;
  }
  return readWebLoopChoice();
}

function initialSavedViewpoint(): Viewpoint {
  if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__?.last_viewpoint) {
    const vp = window.__COPERCEIVE_INITIAL_STATE__.last_viewpoint as Viewpoint;
    if (vp === "orbit" || vp === "chase" || vp === "drone" || vp === "bird" || vp === "camera") {
      return vp;
    }
  }
  return "orbit";
}

function initialSavedAppearance(): Appearance {
  const base = loadAppearance();
  if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__?.layer_toggles) {
    const lt = window.__COPERCEIVE_INITIAL_STATE__.layer_toggles;
    return {
      ...base,
      points: lt.points ?? base.points,
      boxes: lt.boxes ?? base.boxes,
      frustums: lt.frustums ?? base.frustums,
      map: lt.map ?? base.map,
      axes: lt.axes ?? base.axes,
      frustumImages: lt.frustum_images !== undefined ? lt.frustum_images : base.frustumImages,
      frustumOpacity: typeof lt.frustum_opacity === "number" ? lt.frustum_opacity : base.frustumOpacity,
      frustumImageCameras: lt.frustum_image_cameras !== undefined ? lt.frustum_image_cameras : base.frustumImageCameras,
    };
  }
  return base;
}

export default function Viewer({
  token,
  slot,
  actions,
  poster = false,
  scrubTo = null,
  onClock,
  onTheme,
}: {
  token: string;
  slot: HTMLElement | null;
  actions: HTMLElement | null;
  poster?: boolean;
  scrubTo?: number | null;
  onClock?: (mark: ClockMark) => void;
  onTheme?: (theme: Appearance["theme"]) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<Stage | null>(null);
  const [activeToken, setActiveToken] = useState(() => {
    return token || (typeof sessionStorage !== "undefined" ? sessionStorage.getItem("coperceive-token") ?? "" : "");
  });
  const prevTokenRef = useRef(token);
  useEffect(() => {
    if (token && token !== prevTokenRef.current) {
      prevTokenRef.current = token;
      setActiveToken(token);
    } else if (!activeToken) {
      const stored = typeof sessionStorage !== "undefined" ? sessionStorage.getItem("coperceive-token") ?? "" : "";
      if (stored) setActiveToken(stored);
    }
  }, [token, activeToken]);
  const effectiveToken = activeToken || token;
  const cacheRef = useRef<FrameCache>(createFrameCache());
  const [scenes, setScenes] = useState<string[]>([]);
  const [sceneId, setSceneId] = useState<string>(() => {
    if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__?.last_scene_id) {
      return window.__COPERCEIVE_INITIAL_STATE__.last_scene_id;
    }
    return "";
  });
  const sceneIdRef = useRef(sceneId);
  sceneIdRef.current = sceneId;
  const [summary, setSummary] = useState<SceneSummary | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [hidden, setHidden] = useState<Record<string, boolean>>(() => {
    if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__?.agent_visibility) {
      return window.__COPERCEIVE_INITIAL_STATE__.agent_visibility;
    }
    return {};
  });
  const [status, setStatus] = useState("Choose a scene under the server root.");
  const [mapNote, setMapNote] = useState("");
  const [webglError, setWebglError] = useState("");
  const [mapInfoFor, setMapInfoFor] = useState<{ sceneId: string; info: MapInfo | null } | null>(null);
  const [exportNote, setExportNote] = useState("");
  const [exportSource, setExportSource] = useState("view");
  const [exportFormat, setExportFormat] = useState<"mp4" | "gif" | "png">("mp4");
  const [exportFps, setExportFps] = useState(10);
  const [exportSize, setExportSize] = useState<string>("960x540");
  const [exportStart, setExportStart] = useState(1);
  const [exportEnd, setExportEnd] = useState(10);
  const [exporting, setExporting] = useState(false);
  const [plotFormat, setPlotFormat] = useState<"png" | "svg">("png");
  const [plotNote, setPlotNote] = useState("");
  const [plotUrl, setPlotUrl] = useState<string | null>(null);
  const [plotAlt, setPlotAlt] = useState("Bird's-eye plot");
  const [plotting, setPlotting] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [series, setSeries] = useState<{
    points: number[];
    speeds: number[];
    boxes: number[];
    agentSpeed: Record<string, number[]>;
    markers: Marker[];
  }>({ points: [], speeds: [], boxes: [], agentSpeed: {}, markers: [] });
  const [liveSpeed, setLiveSpeed] = useState<Record<string, number>>({});
  const [appearance, setAppearance] = useState<Appearance>(initialSavedAppearance);
  const appearanceRef = useRef(appearance);
  appearanceRef.current = appearance;
  const [playing, setPlaying] = useState(false);
  const loopChoiceRef = useRef<boolean | null>(savedLoopChoice());
  const [loop, setLoop] = useState<boolean>(() => loopChoiceRef.current ?? true);

  const handleToggleLoop = () => {
    const next = !loopRef.current;
    loopRef.current = next;
    loopChoiceRef.current = next;
    setLoop(next);
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      postControllerState({ loop: next });
    } else {
      writeWebLoopChoice(next);
    }
  };
  const handleToggleLoopRef = useRef(handleToggleLoop);
  handleToggleLoopRef.current = handleToggleLoop;
  const isDesktop = typeof window !== "undefined" && Boolean(window.__COPERCEIVE_DESKTOP__);
  const [dataBasename, setDataBasename] = useState<string>(
    () => (typeof window !== "undefined" && window.__COPERCEIVE_DATA_BASENAME__) || "",
  );
  const [dataRoot, setDataRoot] = useState<string>(
    () => (typeof window !== "undefined" && window.__COPERCEIVE_DATA_ROOT__) || "",
  );
  const [toast, setToast] = useState<{ id: number; text: string; type: "error" | "info" } | null>(null);
  const [datasetError, setDatasetError] = useState<string | null>(null);

  const triggerOpenFolder = () => {
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
      fetch(`http://127.0.0.1:${window.__COPERCEIVE_CTRL_PORT__}/open-dialog`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${secret}`,
          "Content-Type": "application/json",
        },
      }).catch((e) => {
        console.error("open-dialog fetch error:", e);
      });
    }
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "o" || e.key === "O")) {
        e.preventDefault();
        triggerOpenFolder();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    window.__COPERCEIVE_SHOW_TOAST__ = (text: string, type: "error" | "info" = "info") => {
      setToast({ id: Date.now(), text, type });
    };
    window.__COPERCEIVE_SET_VIEWPOINT__ = (vp: string) => {
      if (vp === "orbit" || vp === "chase" || vp === "drone" || vp === "bird" || vp === "camera") {
        setViewpoint(vp as Viewpoint);
      }
    };
    window.__COPERCEIVE_TOGGLE_THEME__ = () => {
      setAppearance((curr) => {
        const next = curr.theme === "dark" ? "light" : "dark";
        onTheme?.(next);
        return { ...curr, theme: next };
      });
    };
    window.__COPERCEIVE_DATASET_CHANGED__ = async (
      newOrigin: string,
      newToken: string,
      basename: string,
      root: string,
    ) => {
      (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
        step: "started",
        time: Date.now(),
        newOrigin,
        newTokenPresent: Boolean(newToken),
        basename,
        root,
      };
      if (typeof sessionStorage !== "undefined") {
        sessionStorage.setItem("coperceive-origin", newOrigin);
        sessionStorage.setItem("coperceive-token", newToken);
      }
      setActiveToken(newToken);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new Event("coperceive-token-sync"));
      }
      setDataBasename(basename);
      setDataRoot(root);
      setDatasetError(null);
      setPlaying(false);

      bumpSceneDrawToken();
      if (stageRef.current) {
        clearStage(stageRef.current);
      }
      setSummary(null);
      setCameraFrame(null);
      setPick(null);
      setEvalPick(null);
      cacheRef.current.clear();
      predCacheRef.current.clear();

      try {
        const response = await fetch(`${newOrigin}/api/scenes`, {
          headers: authHeaders(newToken),
        });
        if (!response.ok) {
          const detail = await errorDetail(response);
          setStatus(detail);
          (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
            step: "scenes_fetch_failed",
            status: response.status,
            detail,
          };
          return;
        }
        const body = (await response.json()) as unknown;
        if (!Array.isArray(body)) {
          setStatus("The scene list was not a list.");
          (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
            step: "scenes_not_array",
            body,
          };
          return;
        }
        const ids = body.flatMap((item) => {
          if (item && typeof item === "object" && typeof (item as { scene_id?: unknown }).scene_id === "string") {
            return [(item as { scene_id: string }).scene_id];
          }
          return [];
        });
        setScenes(ids);
        if (ids.length >= 1) {
          const nextScene = ids.length === 1 ? ids[0] : (ids.includes(sceneIdRef.current) ? sceneIdRef.current : ids[0]);
          setSceneId(nextScene);
          try {
            const sumResp = await fetch(`${newOrigin}/api/scenes/${encodeURIComponent(nextScene)}`, {
              headers: authHeaders(newToken),
            });
            if (sumResp.ok) {
              const sumBody = (await sumResp.json()) as unknown;
              if (isSummary(sumBody)) {
                setFrameIndex(0);
                setSummary(sumBody);
                setStatus(`${sumBody.scene_id}: ${sumBody.times_us.length} samples, world ${sumBody.world_frame}.`);
                (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
                  step: "success",
                  ids,
                  nextScene,
                  samples: sumBody.times_us.length,
                };
              } else {
                (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
                  step: "isSummary_false",
                  sumBody,
                };
              }
            } else {
              (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
                step: "sumResp_not_ok",
                status: sumResp.status,
              };
            }
          } catch (e) {
            (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
              step: "sumResp_exception",
              error: String(e),
            };
          }
        } else {
          setSceneId("");
          setStatus("No scene directories under the server root.");
          (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
            step: "no_scenes",
            ids,
          };
        }
      } catch (e) {
        setStatus(e instanceof Error ? e.message : "Could not list scenes.");
        (window as unknown as { __LAST_DATASET_CHANGE__?: unknown }).__LAST_DATASET_CHANGE__ = {
          step: "scenes_exception",
          error: String(e),
        };
      }

      setToast({ id: Date.now(), text: `Switched dataset to ${basename}`, type: "info" });
    };

    window.__COPERCEIVE_DATASET_ERROR__ = (msg: string) => {
      setDatasetError(msg);
    };

    return () => {
      delete window.__COPERCEIVE_SHOW_TOAST__;
      delete window.__COPERCEIVE_SET_VIEWPOINT__;
      delete window.__COPERCEIVE_TOGGLE_THEME__;
      delete window.__COPERCEIVE_DATASET_CHANGED__;
      delete window.__COPERCEIVE_DATASET_ERROR__;
    };
  }, [onTheme]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);
  const [speed, setSpeed] = useState<Speed>(1);
  const [viewpoint, setViewpoint] = useState<Viewpoint>(initialSavedViewpoint);
  const [cameraId, setCameraId] = useState("");
  const [pick, setPick] = useState<PickRecord | null>(null);
  const [cameraFrame, setCameraFrame] = useState<FrameDocument | null>(null);
  const agentTypes = useMemo(
    () => new Map((summary?.agents ?? []).map((agent) => [agent.agent_id, agent.agent_type])),
    [summary],
  );
  // People-facing labels ("Vehicle 1"); the raw agent_id stays the key and the tooltip.
  const agentNames = useMemo(() => agentLabels(summary), [summary]);
  // The legend names the agent types this scene has (OPV2V: Vehicle; V2XSet: Vehicle, RSU; Griffin: Vehicle, Drone).
  const legendTypes = useMemo(
    () => LEGEND_ORDER.filter((kind) => (summary?.agents ?? []).some((agent) => agent.agent_type === kind)),
    [summary],
  );
  // Predictions (ADR 0010). Fetched state is keyed by scene and run, so a change of either reads as "not loaded"
  // without a reset; nothing here persists except `appearance.predictionScore`.
  const [runList, setRunList] = useState<{ sceneId: string; runs: RunInfo[]; note: string } | null>(null);
  const [runId, setRunId] = useState("");
  const [compareId, setCompareId] = useState("");
  const [outputChoice, setOutputChoice] = useState("");
  const [hiddenSources, setHiddenSources] = useState<ReadonlySet<string>>(() => new Set());
  const [hiddenStatuses, setHiddenStatuses] = useState<ReadonlySet<StatusChip>>(() => new Set());
  const [cooperative, setCooperative] = useState<ReadonlySet<CooperativeView>>(() => new Set());
  const [predFrame, setPredFrame] = useState<{ sceneId: string; runId: string; compareId: string; frame: PredictionFrame } | null>(null);
  const [predNote, setPredNote] = useState<{ key: string; text: string } | null>(null);
  const [predSeries, setPredSeries] = useState<{
    sceneId: string;
    runId: string;
    compareId: string;
    series: PredictionSeries | null;
    note: string;
  } | null>(null);
  const [evalPick, setEvalPick] = useState<EvalPick | null>(null);
  const predCacheRef = useRef<PredictionCache>(createPredictionCache());
  const predGen = useRef(0);
  const dimmedRef = useRef<Dimmed | null>(null);
  const onEvalPickRef = useRef<(value: EvalPick | null) => void>(() => undefined);
  const framedScene = useRef("");
  const modeRef = useRef<Viewpoint>(initialSavedViewpoint());
  const cameraKeyRef = useRef("");
  const latestRef = useRef<{ frame: FrameDocument; types: Map<string, string> } | null>(null);
  const orbitHomeRef = useRef<CameraGoal | null>(null);
  const settleRef = useRef(false);
  const reduceMotionRef = useRef(false);
  const lengthRef = useRef(0);
  const loopRef = useRef(loop);
  loopRef.current = loop;
  const frameRef = useRef(0);
  const playingRef = useRef(false);
  playingRef.current = playing;
  const summaryRef = useRef(summary);
  summaryRef.current = summary;

  const handleTogglePlay = () => {
    canvasRef.current?.focus();
    const sum = summaryRef.current;
    if (!sum || sum.times_us.length < 2) {
      return;
    }
    if (playingRef.current) {
      setPlaying(false);
      return;
    }
    if (isEndZone(sum.times_us, frameRef.current)) {
      setFrameIndex(0);
      frameRef.current = 0;
    }
    setPlaying(true);
  };
  const handleTogglePlayRef = useRef(handleTogglePlay);
  handleTogglePlayRef.current = handleTogglePlay;
  const drawGen = useRef(0);
  const paintWait = useRef<((time: number) => void) | null>(null);
  const onPickRef = useRef<(value: PickRecord | null) => void>(() => undefined);
  onPickRef.current = setPick;
  const onFrustumRef = useRef<(key: string) => void>(() => undefined);
  const cameraKeysRef = useRef<string[]>([]);

  useEffect(() => {
    onEvalPickRef.current = setEvalPick;
  }, []);

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
    onTheme?.(appearance.theme);
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    stage.points.visible = appearance.points;
    stage.boxes.visible = appearance.boxes;
    stage.dimmedBoxes.visible = appearance.boxes;
    stage.frustums.visible = appearance.frustums;
    stage.mapLines.visible = appearance.map;
    stage.agents.visible = appearance.axes;
    stage.solids.visible = appearance.boxes && appearance.boxStyle === "solid";
    const material = stage.points.material;
    if (material instanceof THREE.PointsMaterial) {
      material.size = appearance.pointSize;
    }
    stage.setTheme(appearance.theme);
  }, [appearance, onTheme]);

  useEffect(() => {
    stageRef.current?.requestRender();
  }, [viewpoint, cameraId]);

  useEffect(() => {
    if (!poster || !summary) {
      return;
    }
    setLoop(true);
    setPlaying(true);
  }, [poster, summary]);

  useEffect(() => {
    if (!poster || scrubTo === null || !summary) {
      return;
    }
    const last = Math.max(summary.times_us.length - 1, 0);
    setPlaying(false);
    setFrameIndex(Math.round(Math.min(1, Math.max(0, scrubTo)) * last));
  }, [poster, scrubTo, summary]);

  useEffect(() => {
    if (!poster || !onClock || !summary) {
      return;
    }
    const last = Math.max(summary.times_us.length - 1, 1);
    const time = summary.times_us[Math.min(frameIndex, summary.times_us.length - 1)];
    onClock({ ratio: Math.min(frameIndex, last) / last, label: formatClock(typeof time === "number" ? time : 0) });
  }, [poster, onClock, summary, frameIndex]);

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
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    } catch (error) {
      // No WebGL (blocked, no GPU, remote desktop): say so in the viewport instead of
      // throwing out of the effect, which would unmount the whole page. The state is
      // set on the next tick so the effect itself stays free of state updates.
      const reason = error instanceof Error ? error.message : String(error);
      void Promise.resolve().then(() => setWebglError(reason));
      return;
    }
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x141414);
    const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000);
    camera.up.set(0, 0, 1);
    camera.position.set(40, -40, 25);
    camera.layers.enable(BLOOM_LAYER);
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
    enableBloom(points);
    enableBloom(boxes);
    const dimmedBoxes = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.4, depthWrite: false }),
    );
    enableBloom(dimmedBoxes);
    dimmedBoxes.renderOrder = 2;
    const predictions = new THREE.Group();
    predictions.renderOrder = 2;
    const predictionPicks = new THREE.Group();
    const mapLines = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ vertexColors: true }),
    );
    mapLines.renderOrder = 0;
    points.renderOrder = 1;
    boxes.renderOrder = 2;
    const frustums = new THREE.Group();
    const images = new THREE.Group();
    const imagePlanes = new Map<string, THREE.Mesh>();
    const picks = new THREE.Group();
    const frustumPicks = new THREE.Group();
    const highlight = new THREE.Mesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshBasicMaterial({ color: 0xf4f4ed, wireframe: true }),
    );
    highlight.visible = false;
    highlight.frustumCulled = false;
    const agents = new THREE.Group();
    const solids = new THREE.Group();
    frustums.renderOrder = 3;
    agents.renderOrder = 4;
    solids.renderOrder = 2;
    const grid = fadingGrid();
    const trails = new THREE.Group();
    const leaders = new THREE.Group();
    const labels = new THREE.Group();
    const labelRenderer = new CSS2DRenderer();
    labelRenderer.domElement.className = "label-layer";
    const labelHost = canvas.parentElement?.querySelector(".label-host");
    if (labelHost) {
      labelHost.appendChild(labelRenderer.domElement);
    }
    const bloom = createBloom(renderer, scene, camera);
    const trailLog = new Map<string, Map<number, { x: number; y: number; z: number }>>();
    const trailLines = new Map<string, THREE.Line>();
    const leaderLines = new Map<string, THREE.Line>();
    const labelNodes = new Map<string, CSS2DObject>();
    scene.add(grid, mapLines, points, boxes, dimmedBoxes, predictions, solids, frustums, images, picks, predictionPicks, frustumPicks, highlight, agents, trails, leaders, labels);
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
        bloom.setSize(width, height, ratio);
        labelRenderer.setSize(width, height);
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
      bloom.render();
      labelRenderer.render(scene, camera);
      if (labelHost instanceof HTMLElement) {
        clampDroneTags(labelHost);
      }
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
    const renderNow = () => {
      if (frameId !== 0) {
        window.cancelAnimationFrame(frameId);
        frameId = 0;
      }
      draw();
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
    let downX = 0;
    let downY = 0;
    const onPointerDown = (event: PointerEvent) => {
      downX = event.clientX;
      downY = event.clientY;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (Math.hypot(event.clientX - downX, event.clientY - downY) > 5) {
        return;
      }
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return;
      }
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(
        new THREE.Vector2(
          ((event.clientX - rect.left) / rect.width) * 2 - 1,
          -((event.clientY - rect.top) / rect.height) * 2 + 1,
        ),
        camera,
      );
      const boxHit = raycaster.intersectObjects([...picks.children, ...predictionPicks.children], false)[0];
      const frustumHit = raycaster.intersectObjects(frustumPicks.children, false)[0];
      if (boxHit && (!frustumHit || boxHit.distance <= frustumHit.distance)) {
        const evalHit: unknown = boxHit.object.userData.evalPick;
        if (evalHit && typeof evalHit === "object") {
          onPickRef.current(null);
          onEvalPickRef.current(evalHit as EvalPick);
          return;
        }
        onEvalPickRef.current(null);
        onPickRef.current(pickFromObject(boxHit.object));
        return;
      }
      const cameraKeyHit = frustumHit?.object.userData.cameraKey;
      if (frustumHit && typeof cameraKeyHit === "string") {
        onFrustumRef.current(cameraKeyHit);
        return;
      }
      onPickRef.current(null);
      onEvalPickRef.current(null);
    };
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointerup", onPointerUp);
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
      bloom.setSize(width, height, 1);
      labelRenderer.setSize(width, height);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
      bloom.render();
      const url = canvas.toDataURL("image/png");
      sizedWidth = 0;
      sizedHeight = 0;
      sizedRatio = 0;
      requestRender();
      return url;
    };
    const setTheme = (theme: Appearance["theme"]) => {
      scene.background = new THREE.Color(theme === "light" || theme === "dark" ? 0x141414 : 0x141414);
      requestRender();
    };
    const clearMotion = () => {
      trailLog.clear();
      for (const line of trailLines.values()) {
        trails.remove(line);
        line.geometry.dispose();
      }
      trailLines.clear();
      for (const line of leaderLines.values()) {
        leaders.remove(line);
        line.geometry.dispose();
      }
      leaderLines.clear();
      for (const label of labelNodes.values()) {
        labels.remove(label);
        label.element.remove();
      }
      labelNodes.clear();
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
      renderNow,
      captureView,
      setTheme,
      bloom,
      labelRenderer,
      trails,
      leaders,
      labels,
      trailLog,
      trailLines,
      leaderLines,
      labelNodes,
      clearMotion,
      picks,
      frustumPicks,
      images,
      imagePlanes,
      highlight,
      predictions,
      predictionPicks,
      dimmedBoxes,
    };
    const isTestOrDesktopEnv =
      typeof window !== "undefined" &&
      (import.meta.env.MODE === "test" || Boolean(window.__COPERCEIVE_TEST_ENV__));
    if (isTestOrDesktopEnv) {
      if (import.meta.env.MODE === "test") {
        (window as unknown as { __COPERCEIVE_STAGE__?: unknown }).__COPERCEIVE_STAGE__ = stageRef.current;
      }
      window.__COPERCEIVE_SCENE_GRAPH__ = () => sceneGraphAudit(stageRef.current);
      (window as unknown as { __COPERCEIVE_SCENE_COUNTS__?: unknown }).__COPERCEIVE_SCENE_COUNTS__ = () => {
        const s = stageRef.current;
        if (!s) return null;
        const pAttr = s.points.geometry.getAttribute("position");
        const pts = s.points.visible && pAttr ? pAttr.count : 0;
        const bAttr = s.boxes.geometry.getAttribute("position");
        const dimAttr = s.dimmedBoxes.geometry.getAttribute("position");
        const bxs = ((s.boxes.visible && bAttr ? bAttr.count : 0) + (s.dimmedBoxes.visible && dimAttr ? dimAttr.count : 0)) / 24;
        const frs = s.frustums.children.length;
        const ags = s.agents.children.length;
        return { points: pts, boxes: bxs, frustums: frs, agents: ags };
      };
    }
    setTheme(appearanceRef.current.theme);
    const pointsMaterial = points.material;
    if (pointsMaterial instanceof THREE.PointsMaterial) {
      pointsMaterial.size = appearanceRef.current.pointSize;
    }
    requestRender();
    return () => {
      if (isTestOrDesktopEnv) {
        if (import.meta.env.MODE === "test") {
          delete (window as unknown as { __COPERCEIVE_STAGE__?: unknown }).__COPERCEIVE_STAGE__;
        }
        delete window.__COPERCEIVE_SCENE_GRAPH__;
        delete (window as unknown as { __COPERCEIVE_SCENE_COUNTS__?: unknown }).__COPERCEIVE_SCENE_COUNTS__;
      }
      resize.disconnect();
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointerup", onPointerUp);
      controls.removeEventListener("start", onStart);
      controls.removeEventListener("end", onEnd);
      disposeImageTextures();
      for (const child of frustumPicks.children) {
        if (child instanceof THREE.Mesh) {
          child.geometry.dispose();
        }
      }
      for (const mesh of imagePlanes.values()) {
        mesh.geometry.dispose();
        if (mesh.material instanceof THREE.Material) {
          mesh.material.dispose();
        }
      }
      window.cancelAnimationFrame(frameId);
      controls.dispose();
      bloom.dispose();
      labelRenderer.domElement.remove();
      grid.geometry.dispose();
      if (grid.material instanceof THREE.Material) {
        grid.material.dispose();
      }
      renderer.dispose();
      points.geometry.dispose();
      boxes.geometry.dispose();
      dimmedBoxes.geometry.dispose();
      disposePredictionGroup(predictions);
      disposePredictionGroup(predictionPicks);
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
        handleTogglePlayRef.current();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        setPlaying(false);
        const delta = event.key === "ArrowLeft" ? -1 : 1;
        setFrameIndex((current) => stepIndex(current, delta, lengthRef.current, loopRef.current));
      } else if (event.key === "l" || event.key === "L") {
        handleToggleLoopRef.current();
      } else if (event.key === "c" || event.key === "C") {
        setAppearance((current) => ({ ...current, stripOpen: !current.stripOpen }));
      } else if (event.key === "Escape") {
        setAppearance((current) => (current.splitCamera === null ? current : { ...current, splitCamera: null }));
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
      const resolved = next === "chase" && modeRef.current === "chase" ? "orbit" : next;
      if (resolved === "orbit") {
        settleRef.current = true;
      }
      modeRef.current = resolved;
      setViewpoint(resolved);
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
        headers: authHeaders(activeToken),
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
      let initialScene = "";
      if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__?.last_scene_id) {
        const saved = window.__COPERCEIVE_INITIAL_STATE__.last_scene_id;
        if (ids.includes(saved)) {
          initialScene = saved;
        }
      }
      setScenes(ids);
      setSceneId((current) => current || initialScene || ids[0] || "");
      if (ids.length === 0) {
        setStatus("No scene directories under the server root.");
      }
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setStatus(error instanceof Error ? error.message : "Could not list scenes.");
      }
    });
    return () => controller.abort();
  }, [effectiveToken]);

  useEffect(() => {
    if (!sceneId) {
      return;
    }
    cacheRef.current.clear();
    predCacheRef.current.clear();
    stageRef.current?.clearMotion();
    const controller = new AbortController();
    void (async () => {
      const response = await fetch(`${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}`, {
        headers: authHeaders(effectiveToken),
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
      let initialFrame = 0;
      let initialHidden: Record<string, boolean> = { ...hidden };
      if (typeof window !== "undefined" && window.__COPERCEIVE_INITIAL_STATE__) {
        const init = window.__COPERCEIVE_INITIAL_STATE__;
        if (typeof init.last_sample_index === "number" && init.last_sample_index < body.times_us.length) {
          initialFrame = init.last_sample_index;
        } else if (typeof init.last_timestamp_us === "number") {
          const found = body.times_us.findIndex((t) => t >= init.last_timestamp_us!);
          if (found >= 0) initialFrame = found;
        } else if (typeof init.last_timestamp_ns === "number") {
          const targetUs = Math.round(init.last_timestamp_ns / 1000);
          const found = body.times_us.findIndex((t) => t >= targetUs);
          if (found >= 0) initialFrame = found;
        }
        if (init.last_viewpoint) {
          const vp = init.last_viewpoint as Viewpoint;
          if (vp === "orbit" || vp === "chase" || vp === "drone" || vp === "bird" || vp === "camera") {
            modeRef.current = vp;
            setViewpoint(vp);
          }
        }
        if (init.last_theme && (init.last_theme === "dark" || init.last_theme === "light")) {
          onTheme?.(init.last_theme);
          setAppearance((curr) => ({ ...curr, theme: init.last_theme as "dark" | "light" }));
        }
        if (init.agent_visibility) {
          initialHidden = init.agent_visibility;
        }
        if (init.layer_toggles) {
          setAppearance((curr) => ({
            ...curr,
            points: init.layer_toggles?.points ?? curr.points,
            boxes: init.layer_toggles?.boxes ?? curr.boxes,
            frustums: init.layer_toggles?.frustums ?? curr.frustums,
            map: init.layer_toggles?.map ?? curr.map,
            axes: init.layer_toggles?.axes ?? curr.axes,
            frustumImages: init.layer_toggles?.frustum_images !== undefined ? init.layer_toggles.frustum_images : curr.frustumImages,
            frustumOpacity: typeof init.layer_toggles?.frustum_opacity === "number" ? init.layer_toggles.frustum_opacity : curr.frustumOpacity,
            frustumImageCameras: init.layer_toggles?.frustum_image_cameras !== undefined ? init.layer_toggles.frustum_image_cameras : curr.frustumImageCameras,
          }));
        }
        if (init.panel_collapse) {
          setAppearance((curr) => ({
            ...curr,
            stripOpen: init.panel_collapse?.camera_strip_open ?? curr.stripOpen,
          }));
        }
        if (init.selected_camera) {
          setCameraId(init.selected_camera);
        }
        if (init.split_view !== undefined) {
          setAppearance((curr) => ({
            ...curr,
            splitCamera: init.split_view ? (init.selected_camera ?? curr.splitCamera) : null,
          }));
        }
        window.__COPERCEIVE_INITIAL_STATE__ = undefined;
      }
      if (isEndZone(body.times_us, initialFrame)) {
        initialFrame = 0;
      }
      if (loopChoiceRef.current !== null) {
        loopRef.current = loopChoiceRef.current;
        setLoop(loopChoiceRef.current);
      } else {
        const durationUs = body.times_us.length >= 2 ? body.times_us[body.times_us.length - 1] - body.times_us[0] : 0;
        const next = durationUs < 60_000_000;
        loopRef.current = next;
        setLoop(next);
      }
      setSummary(body);
      setFrameIndex(initialFrame);
      frameRef.current = initialFrame;
      setExportStart(1);
      setExportEnd(Math.min(10, body.times_us.length));
      setExportSource("view");
      setHidden(initialHidden);
      setPick(null);
      setEvalPick(null);
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
  }, [sceneId, effectiveToken]);

  useEffect(() => {
    window.__COPERCEIVE_PLAYBACK__ = (action: string, value?: number) => {
      if (action === "toggle") {
        handleTogglePlayRef.current();
      } else if (action === "loop") {
        handleToggleLoopRef.current();
      } else if (action === "step_forward") {
        setPlaying(false);
        setFrameIndex((curr) => (summary ? Math.min(curr + 1, summary.times_us.length - 1) : curr));
      } else if (action === "step_backward") {
        setPlaying(false);
        setFrameIndex((curr) => Math.max(0, curr - 1));
      } else if (action === "seek" && typeof value === "number") {
        setPlaying(false);
        setFrameIndex(summary ? Math.max(0, Math.min(value, summary.times_us.length - 1)) : 0);
      } else if (action === "speed" && typeof value === "number") {
        if (value === 0.25 || value === 0.5 || value === 1 || value === 2 || value === 4) {
          setSpeed(value);
        }
      }
    };
    window.__COPERCEIVE_PLAYBACK_STATE__ = () => ({
      playing: playingRef.current,
      loop: loopRef.current,
      frameIndex: frameRef.current,
      timeUs: summaryRef.current?.times_us[frameRef.current] ?? null,
      samples: summaryRef.current?.times_us.length ?? 0,
    });
    return () => {
      window.__COPERCEIVE_PLAYBACK__ = undefined;
      window.__COPERCEIVE_PLAYBACK_STATE__ = undefined;
    };
  }, [summary]);

  useEffect(() => {
    const enabled =
      typeof window !== "undefined" && (import.meta.env.MODE === "test" || Boolean(window.__COPERCEIVE_TEST_ENV__));
    if (!enabled) {
      return;
    }
    window.__COPERCEIVE_VIEW_SETTINGS__ = () => ({
      sceneId,
      viewpoint,
      layers: {
        points: appearance.points,
        boxes: appearance.boxes,
        frustums: appearance.frustums,
        map: appearance.map,
        axes: appearance.axes,
        frustumImages: appearance.frustumImages,
      },
      hiddenAgents: hidden,
      hiddenClasses: appearance.hiddenClasses,
    });
    return () => {
      delete window.__COPERCEIVE_VIEW_SETTINGS__;
    };
  }, [sceneId, viewpoint, appearance, hidden]);

  useEffect(() => {
    window.__COPERCEIVE_SCENES__ = () => scenes;
    return () => {
      window.__COPERCEIVE_SCENES__ = undefined;
    };
  }, [scenes]);

  useEffect(() => {
    if (typeof window === "undefined" || !window.__COPERCEIVE_CTRL_PORT__ || !summary) return;
    const timer = setTimeout(() => {
      const port = window.__COPERCEIVE_CTRL_PORT__;
      const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
      const timeUs = summary?.times_us?.[frameIndex];
      fetch(`http://127.0.0.1:${port}/state`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${secret}`,
        },
        body: JSON.stringify({
          scene_id: sceneId || undefined,
          sample_index: frameIndex,
          timestamp_us: typeof timeUs === "number" ? timeUs : undefined,
          viewpoint,
          theme: appearance.theme,
          agent_visibility: hidden,
          layer_toggles: {
            points: appearance.points,
            boxes: appearance.boxes,
            frustums: appearance.frustums,
            map: appearance.map,
            axes: appearance.axes,
            frustum_images: appearance.frustumImages,
            frustum_opacity: appearance.frustumOpacity,
            frustum_image_cameras: appearance.frustumImageCameras ?? undefined,
          },
          selected_camera: appearance.splitCamera || cameraId || undefined,
          split_view: Boolean(appearance.splitCamera),
          panel_collapse: {
            camera_strip_open: appearance.stripOpen,
          },
          ...(loopChoiceRef.current !== null ? { loop: loopChoiceRef.current } : {}),
        }),
      }).catch(() => {});
    }, 500);
    return () => clearTimeout(timer);
  }, [sceneId, frameIndex, viewpoint, appearance, hidden, summary, cameraId, loop]);

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

    // Update status line immediately on every frame tick during playback
    const latest = latestRef.current;
    if (latest && latest.frame) {
      const shown = appearance.points
        ? latest.frame.point_sensors.reduce((sum, sensor) => (hidden[sensor.agent_id] ? sum : sum + sensor.count), 0)
        : 0;
      const boxes = latest.frame.boxes.filter((box) => boxShown(box, hidden, appearance)).length;
      const frustums = latest.frame.frustums.filter((frustum) => appearance.frustums && !hidden[frustum.agent_id]).length;
      setStatus(
        `${summary.scene_id} at ${time} µs. ${shown} points, ${boxes} boxes, ${frustums} frustums. ${mapNote}${holdNote(modeRef.current, latest.frame, types, cameraKeyRef.current)}`,
      );
    } else {
      setStatus(`${summary.scene_id} at ${time} µs. ${mapNote}`);
    }

    // Immediately remove hidden agents' objects and trigger immediate redraw
    if (latestRef.current) {
      const { frame: curFrame, types: curTypes } = latestRef.current;
      drawBoxes(stage, curFrame, curTypes, hidden, appearance, dimmedRef.current);

      clearGroup(stage.frustums);
      const pickable: { key: string; fovY: number; aspect: number; pose: Pose }[] = [];
      for (const frustum of curFrame.frustums) {
        if (!appearance.frustums || hidden[frustum.agent_id] || !isPose(frustum.pose)) {
          continue;
        }
        const lines = frustumLines(frustum.fov_y_rad, frustum.aspect, colorFor(curTypes.get(frustum.agent_id) ?? ""));
        applyPose(lines, frustum.pose);
        stage.frustums.add(lines);
        pickable.push({
          key: cameraKey(frustum.agent_id, frustum.sensor_id),
          fovY: frustum.fov_y_rad,
          aspect: frustum.aspect,
          pose: frustum.pose,
        });
      }
      syncFrustumPicks(stage.frustumPicks, pickable);

      clearGroup(stage.agents);
      for (const agent of curFrame.agents) {
        if (!appearance.axes || hidden[agent.agent_id] || !isPose(agent.pose)) {
          continue;
        }
        const axes = new THREE.AxesHelper(2);
        applyPose(axes, agent.pose);
        stage.agents.add(axes);
      }

      for (const [key, mesh] of stage.imagePlanes) {
        const agentId = key.split(":")[0];
        if (hidden[agentId]) {
          mesh.visible = false;
        }
      }

      syncMotion(stage, curFrame, curTypes, hidden);
      syncPicks(stage.picks, curFrame.boxes, (box) => boxShown(box, hidden, appearance));

      const anyPointsShown = appearance.points && curFrame.point_sensors.some((sensor) => !hidden[sensor.agent_id] && sensor.count > 0);
      if (!anyPointsShown) {
        replaceColored(stage.points.geometry, new Float32Array(0), new Float32Array(0));
        stage.points.visible = false;
      }

      stage.renderNow();
    }

    void (async () => {
      const body = await cacheRef.current.loadFrame(summary.scene_id, time, effectiveToken);
      if (stale()) {
        return;
      }
      latestRef.current = { frame: body, types };
      const drawn = await drawFrame(stage, body, types, hidden, appearance, effectiveToken, cacheRef.current, () => dimmedRef.current, stale);
      if (!drawn || stale()) {
        return;
      }
      syncMotion(stage, body, types, hidden);
      syncPicks(stage.picks, body.boxes, (box) => boxShown(box, hidden, appearance));
      stage.renderNow();
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
      prefetchAhead(cacheRef.current, summary, frameIndex, effectiveToken);
      paintWait.current?.(time);
    })().catch((error: unknown) => {
      if (!stale()) {
        setStatus(error instanceof Error ? error.message : "Could not draw the frame.");
      }
    });
  }, [summary, frameIndex, hidden, appearance, effectiveToken, mapNote]);

  useEffect(() => {
    const latest = latestRef.current;
    if (!summary || !latest || !summary.times_us.includes(latest.frame.time_us)) {
      return;
    }
    const shown = appearance.points
      ? latest.frame.point_sensors.reduce((sum, sensor) => (hidden[sensor.agent_id] ? sum : sum + sensor.count), 0)
      : 0;
    const boxes = latest.frame.boxes.filter((box) => boxShown(box, hidden, appearance)).length;
    const frustums = latest.frame.frustums.filter((frustum) => appearance.frustums && !hidden[frustum.agent_id]).length;
    setStatus(
      `${summary.scene_id} at ${latest.frame.time_us} µs. ${shown} points, ${boxes} boxes, ${frustums} frustums. ${mapNote}${holdNote(viewpoint, latest.frame, latest.types, cameraId)}`,
    );
  }, [viewpoint, cameraId, summary, mapNote, hidden, appearance]);

  useEffect(() => {
    const stage = stageRef.current;
    const frame = cameraFrame;
    if (!stage || !frame) {
      return;
    }
    let cancelled = false;
    const imageWanted = (key: string) => appearance.frustums && (poster || showCameraImage(appearance, key));
    const wanted = frame.frustums.filter((frustum) => {
      if (hidden[frustum.agent_id] || !isPose(frustum.pose)) {
        return false;
      }
      const key = cameraKey(frustum.agent_id, frustum.sensor_id);
      if (viewpoint === "camera" && key === cameraId) {
        return false;
      }
      return imageWanted(key);
    });
    stage.images.visible = wanted.length > 0;
    if (wanted.length === 0) {
      for (const mesh of stage.imagePlanes.values()) {
        mesh.visible = false;
      }
      stage.requestRender();
      return;
    }
    const opacity = appearance.frustumOpacity;
    void (async () => {
      const seen = new Set<string>();
      await Promise.all(
        wanted.map(async (frustum) => {
          const key = cameraKey(frustum.agent_id, frustum.sensor_id);
          const bitmap = await loadImage(frame.scene_id, frame.time_us, frustum.agent_id, frustum.sensor_id, effectiveToken, FRUSTUM_TEXTURE);
          if (cancelled || !bitmap) {
            return;
          }
          seen.add(key);
          let mesh = stage.imagePlanes.get(key);
          if (!mesh || mesh.userData.aspect !== frustum.aspect || mesh.userData.fov !== frustum.fov_y_rad) {
            if (mesh) {
              stage.images.remove(mesh);
              mesh.geometry.dispose();
              if (mesh.material instanceof THREE.Material) {
                mesh.material.dispose();
              }
            }
            mesh = createImagePlane(frustum.fov_y_rad, frustum.aspect);
            mesh.userData.aspect = frustum.aspect;
            mesh.userData.fov = frustum.fov_y_rad;
            stage.images.add(mesh);
            stage.imagePlanes.set(key, mesh);
          }
          applyPose(mesh, frustum.pose);
          const material = mesh.material;
          if (material instanceof THREE.MeshBasicMaterial) {
            material.opacity = opacity;
            material.map = textureForBitmap(`${frame.scene_id}|${frame.time_us}|${key}|${FRUSTUM_TEXTURE}`, bitmap);
            material.needsUpdate = true;
          }
          mesh.visible = true;
        }),
      );
      if (cancelled) {
        return;
      }
      for (const [key, mesh] of stage.imagePlanes) {
        if (!seen.has(key)) {
          mesh.visible = false;
        }
      }
      const nextTime = summary?.times_us[frameIndex + 1];
      if (typeof nextTime === "number") {
        for (const frustum of wanted) {
          void loadImage(frame.scene_id, nextTime, frustum.agent_id, frustum.sensor_id, effectiveToken, FRUSTUM_TEXTURE).catch(() => undefined);
        }
      }
      stage.requestRender();
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [cameraFrame, appearance.frustumImages, appearance.frustumImageCameras, appearance.frustumOpacity, appearance.frustums, hidden, effectiveToken, viewpoint, cameraId, summary, frameIndex, poster]);

  const sceneKey = summary?.scene_id ?? "";
  // Keyed by scene like the run list, so a switch never shows the previous town's map note.
  const mapInfo = mapInfoFor && mapInfoFor.sceneId === sceneKey ? mapInfoFor.info : null;
  const runs = runList && runList.sceneId === sceneKey ? runList.runs : null;
  const runNote = runList && runList.sceneId === sceneKey ? runList.note : "";
  const run = runs?.find((item) => item.run_id === runId) ?? null;
  const compareRun =
    compareId && compareId !== runId ? (runs?.find((item) => item.run_id === compareId && item.evaluated) ?? null) : null;
  const activeCompare = compareRun?.run_id ?? "";
  const predKey = `${sceneKey}|${runId}|${activeCompare}`;
  const livePred =
    predFrame && predFrame.sceneId === sceneKey && predFrame.runId === runId && predFrame.compareId === activeCompare
      ? predFrame.frame
      : null;
  const liveSeries =
    predSeries && predSeries.sceneId === sceneKey && predSeries.runId === runId && predSeries.compareId === activeCompare
      ? predSeries
      : null;
  const livePredNote = predNote && predNote.key === predKey ? predNote.text : "";
  const scoreDefault = livePred?.score_threshold ?? run?.config?.score_threshold ?? null;
  const minScore = appearance.predictionScore ?? scoreDefault ?? 0.3;
  const sources = useMemo(() => {
    const names = new Set<string>();
    if (run) {
      for (const name of Object.keys(run.run.sources)) {
        names.add(name);
      }
      for (const output of run.outputs) {
        names.add(output.source);
      }
    }
    return [...names];
  }, [run]);
  const activeOutput = run
    ? (run.outputs.find((output) => outputKey(output.agent_id, output.source) === outputChoice) ?? defaultOutput(run, agentTypes))
    : null;
  const evalSummary = run && activeOutput ? summarizeOutput(run, compareRun, activeOutput.agent_id, activeOutput.source) : null;
  const evalSeries =
    liveSeries?.series && activeOutput && summary
      ? alignSeries(liveSeries.series, activeOutput.agent_id, activeOutput.source, summary.times_us)
      : null;
  const evalCompare =
    liveSeries?.series?.compare && activeOutput && summary
      ? alignSeries(liveSeries.series.compare, activeOutput.agent_id, activeOutput.source, summary.times_us)
      : null;
  const liveEvalPick = evalPick && livePred ? followEvalPick(evalPick, livePred) : null;
  const missing = missingNotes(summary, mapInfo, runs !== null && runs.length === 0 ? runNote : "");
  const pickedGt = cameraFrame && pick ? followPick(cameraFrame, pick) : null;
  const gtDetail = pickedGt && livePred ? gtEvalDetail(livePred, gtKey(pickedGt.agentId, pickedGt.trackId, pickedGt.center)) : null;

  useEffect(() => {
    if (!summary) {
      return;
    }
    const sceneId = summary.scene_id;
    const controller = new AbortController();
    void (async () => {
      const response = await fetch(`${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/predictions`, {
        headers: authHeaders(effectiveToken),
        signal: controller.signal,
      });
      if (response.status === 404) {
        const detail = await errorDetail(response);
        setRunList({
          sceneId,
          runs: [],
          note:
            detail === "no predictions root"
              ? "no predictions available for this scene."
              : "no prediction runs for this scene.",
        });
        return;
      }
      if (!response.ok) {
        setRunList({ sceneId, runs: [], note: await errorDetail(response) });
        return;
      }
      const body = (await response.json()) as unknown;
      if (!isRunList(body)) {
        setRunList({ sceneId, runs: [], note: "The prediction run list was incomplete." });
        return;
      }
      setRunList({ sceneId, runs: body.runs, note: body.runs.length === 0 ? "no prediction runs for this scene." : "" });
      setRunId((current) =>
        body.runs.some((item) => item.run_id === current)
          ? current
          : ((body.runs.find((item) => item.evaluated) ?? body.runs[0])?.run_id ?? ""),
      );
      setCompareId((current) => (body.runs.some((item) => item.run_id === current && item.evaluated) ? current : ""));
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setRunList({ sceneId, runs: [], note: error instanceof Error ? error.message : "Could not list prediction runs." });
      }
    });
    return () => controller.abort();
  }, [summary, effectiveToken]);

  useEffect(() => {
    if (!summary || !run || !run.evaluated) {
      return;
    }
    const sceneId = summary.scene_id;
    const id = run.run_id;
    const other = activeCompare;
    const controller = new AbortController();
    void (async () => {
      const query = other ? `?compare=${encodeURIComponent(other)}` : "";
      const response = await fetch(
        `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/predictions/${encodeURIComponent(id)}/series${query}`,
        { headers: authHeaders(effectiveToken), signal: controller.signal },
      );
      if (!response.ok) {
        setPredSeries({ sceneId, runId: id, compareId: other, series: null, note: await errorDetail(response) });
        return;
      }
      const body = (await response.json()) as unknown;
      if (!isPredictionSeries(body)) {
        setPredSeries({ sceneId, runId: id, compareId: other, series: null, note: "The prediction series was incomplete." });
        return;
      }
      setPredSeries({ sceneId, runId: id, compareId: other, series: body, note: "" });
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setPredSeries({
          sceneId,
          runId: id,
          compareId: other,
          series: null,
          note: error instanceof Error ? error.message : "Could not read the prediction series.",
        });
      }
    });
    return () => controller.abort();
  }, [summary, run, activeCompare, effectiveToken]);

  useEffect(() => {
    if (!summary || !run || !run.evaluated) {
      return;
    }
    const time = summary.times_us[frameIndex];
    if (typeof time !== "number") {
      return;
    }
    const sceneId = summary.scene_id;
    const id = run.run_id;
    const other = activeCompare;
    const key = `${sceneId}|${id}|${other}`;
    const gen = ++predGen.current;
    const cache = predCacheRef.current;
    void cache
      .load(sceneId, id, other, time, effectiveToken)
      .then((frame) => {
        if (predGen.current !== gen) {
          return;
        }
        setPredFrame({ sceneId, runId: id, compareId: other, frame });
        setPredNote({ key, text: "" });
        for (const ahead of summary.times_us.slice(frameIndex + 1, frameIndex + 1 + PREFETCH_FRAMES)) {
          void cache.load(sceneId, id, other, ahead, effectiveToken).catch(() => undefined);
        }
      })
      .catch((error: unknown) => {
        if (predGen.current === gen) {
          setPredNote({ key, text: error instanceof Error ? error.message : "Could not read the prediction frame." });
        }
      });
  }, [summary, run, activeCompare, frameIndex, effectiveToken]);

  const layer = useMemo<{ a: LayerResult; b: LayerResult | null; timeUs: number } | null>(() => {
    if (!livePred) {
      return null;
    }
    const showSources = new Set(sources.filter((source) => !hiddenSources.has(source)));
    const showStatuses = new Set(STATUS_CHIPS.filter((chip) => !hiddenStatuses.has(chip)));
    const visible = (rows: readonly PredictionRow[]) => rows.filter((row) => !hidden[row.agent_id]);
    const a = buildPredictionObjects(visible(livePred.rows), {
      showSources,
      showStatuses,
      cooperative,
      minScore,
      runTag: "A",
      runId: livePred.run_id,
    });
    const b = livePred.compare
      ? buildPredictionObjects(visible(livePred.compare.rows), {
          showSources,
          showStatuses,
          cooperative,
          minScore,
          runTag: "B",
          runId: livePred.compare.run_id,
        })
      : null;
    return { a, b, timeUs: livePred.time_us };
  }, [livePred, sources, hiddenSources, hiddenStatuses, cooperative, minScore, hidden]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    if (layer) {
      stage.predictions.add(layer.a.group);
      stage.predictionPicks.add(layer.a.picks);
      if (layer.b) {
        stage.predictions.add(layer.b.group);
        stage.predictionPicks.add(layer.b.picks);
      }
      dimmedRef.current = { timeUs: layer.timeUs, keys: new Set([...layer.a.dimmedGtKeys, ...(layer.b?.dimmedGtKeys ?? [])]) };
    } else {
      dimmedRef.current = null;
    }
    const latest = latestRef.current;
    if (latest) {
      drawBoxes(stage, latest.frame, latest.types, hidden, appearanceRef.current, dimmedRef.current);
    }
    stage.requestRender();
    return () => {
      disposePredictionGroup(stage.predictions);
      disposePredictionGroup(stage.predictionPicks);
    };
  }, [layer, hidden]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    const mesh = stage.highlight;
    if (liveEvalPick) {
      const row = liveEvalPick.row;
      mesh.position.set(row.center[0] ?? 0, row.center[1] ?? 0, row.center[2] ?? 0);
      mesh.quaternion.set(row.pose.qx, row.pose.qy, row.pose.qz, row.pose.qw);
      mesh.scale.set(row.size_lwh[0] ?? 1, row.size_lwh[1] ?? 1, row.size_lwh[2] ?? 1).multiplyScalar(1.04);
      mesh.visible = true;
      stage.requestRender();
      return;
    }
    const match = pick
      ? stage.picks.children.find((child) => {
          const stored = pickFromObject(child);
          if (!stored || stored.agentId !== pick.agentId) {
            return false;
          }
          if (pick.trackId && stored.trackId) {
            return stored.trackId === pick.trackId;
          }
          return stored.index === pick.index;
        })
      : undefined;
    if (!(match instanceof THREE.Mesh)) {
      mesh.visible = false;
      stage.requestRender();
      return;
    }
    mesh.position.copy(match.position);
    mesh.quaternion.copy(match.quaternion);
    mesh.scale.copy(match.scale).multiplyScalar(1.04);
    mesh.visible = true;
    stage.requestRender();
  }, [pick, cameraFrame, liveEvalPick]);

  useEffect(() => {
    if (!summary || !stageRef.current) {
      return;
    }
    const stage = stageRef.current;
    const controller = new AbortController();
    void (async () => {
      const base = `${apiBase()}/api/scenes/${encodeURIComponent(summary.scene_id)}`;
      const which = await fetch(`${base}/map`, { headers: authHeaders(effectiveToken), signal: controller.signal });
      let info: MapInfo | null = null;
      if (which.ok) {
        const body = (await which.json()) as unknown;
        info = isMapInfo(body) ? body : null;
        setMapInfoFor({ sceneId: summary.scene_id, info });
      }
      if (info && !info.available) {
        replaceMap(stage, new ArrayBuffer(0));
        stage.requestRender();
        setMapNote(mapNoteFor(info));
        return;
      }
      const response = await fetch(`${base}/map/lines`, {
        headers: authHeaders(effectiveToken),
        signal: controller.signal,
      });
      if (response.status === 404) {
        replaceMap(stage, new ArrayBuffer(0));
        stage.requestRender();
        setMapNote(mapNoteFor(info));
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
  }, [summary, effectiveToken]);


  function chooseViewpoint(next: Viewpoint) {
    const resolved = next === "chase" && modeRef.current === "chase" ? "orbit" : next;
    if (resolved === "orbit") {
      settleRef.current = true;
    }
    modeRef.current = resolved;
    setViewpoint(resolved);
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
        ...authHeaders(effectiveToken),
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
      points?: unknown;
      boxes?: unknown;
      map_drawn?: unknown;
    };
  }

  async function downloadExported(relative: string) {
    const file =
      relative.endsWith(".mp4") ||
      relative.endsWith(".gif") ||
      relative.endsWith(".png") ||
      relative.endsWith(".svg");
    if (!file) {
      return;
    }
    const response = await fetch(`${apiBase()}/api/files?path=${encodeURIComponent(relative)}`, {
      headers: authHeaders(effectiveToken),
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
      headers: { ...authHeaders(effectiveToken), "X-Coperceive-Request": "1" },
    });
    if (!response.ok) {
      setExportNote(await errorDetail(response));
      return;
    }
    const body = (await response.json()) as { path?: unknown; samples?: unknown };
    setExportNote(`Wrote ${String(body.path)} (${String(body.samples)} samples).`);
  }

  async function savePlot(kind: "bev" | "overlay") {
    if (!summary || plotting) {
      return;
    }
    const when = summary.times_us[Math.min(frameIndex, Math.max(summary.times_us.length - 1, 0))];
    if (typeof when !== "number") {
      return;
    }
    const shown = summary.agents.filter((agent) => !hidden[agent.agent_id]).map((agent) => agent.agent_id);
    if (shown.length === 0) {
      setPlotNote("Show an agent to draw the plot.");
      return;
    }
    let agentId = "";
    let sensorId = "";
    if (kind === "overlay") {
      const slash = cameraId.indexOf("/");
      if (slash <= 0) {
        setPlotNote("Choose a camera for the overlay.");
        return;
      }
      agentId = cameraId.slice(0, slash);
      sensorId = cameraId.slice(slash + 1);
    }
    setPlotting(true);
    setPlotNote(kind === "bev" ? "Drawing the bird's-eye plot…" : "Drawing the camera overlay…");
    try {
      const saved = await postExport(summary.scene_id, kind === "bev" ? "/export/bev" : "/export/overlay", {
        time_us: when,
        format: plotFormat,
        theme: appearance.theme,
        color_by: appearance.colorBy,
        points: appearance.points,
        boxes: appearance.boxes,
        map: appearance.map,
        box_style: appearance.boxStyle,
        hidden_classes: appearance.hiddenClasses,
        agents: shown,
        ...(kind === "overlay" ? { agent_id: agentId, sensor_id: sensorId } : {}),
        ...(kind === "bev" && run?.evaluated ? { run_id: run.run_id } : {}),
      });
      const path = typeof saved.path === "string" ? saved.path : "";
      const mapNote = saved.map_drawn === true ? ", map drawn" : "";
      setPlotNote(
        `Wrote ${path} (${String(saved.points)} points, ${String(saved.boxes)} boxes${mapNote}).`,
      );
      setPlotAlt(kind === "bev" ? "Bird's-eye plot of the current sample" : "Camera overlay of the current sample");
      const response = await fetch(`${apiBase()}/api/files?path=${encodeURIComponent(path)}`, {
        headers: authHeaders(effectiveToken),
      });
      if (!response.ok) {
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      setPlotUrl((previous) => {
        if (previous) {
          URL.revokeObjectURL(previous);
        }
        return url;
      });
      const link = document.createElement("a");
      link.href = url;
      link.download = path.split("/").pop() ?? `plot.${plotFormat}`;
      link.click();
    } catch (error) {
      setPlotNote(error instanceof Error ? error.message : "Plot failed.");
    } finally {
      setPlotting(false);
    }
  }

  useEffect(() => {
    if (!summary) {
      return;
    }
    let cancelled = false;
    const times = summary.times_us;
    const vehicleId = summary.agents.find((agent) => agent.agent_type === "vehicle")?.agent_id ?? "";
    void (async () => {
      const points: number[] = [];
      const speeds: number[] = [];
      const boxes: number[] = [];
      const agentSpeed: Record<string, number[]> = {};
      for (const agent of summary.agents) {
        agentSpeed[agent.agent_id] = [];
      }
      const markers: Marker[] = [];
      let previous: FrameDocument | null = null;
      let previousTime = 0;
      for (let index = 0; index < times.length; index += 1) {
        if (cancelled) {
          return;
        }
        const time = times[index];
        if (typeof time !== "number") {
          continue;
        }
        const frame = await cacheRef.current.loadFrame(summary.scene_id, time, effectiveToken);
        if (cancelled) {
          return;
        }
        let pointCount = 0;
        for (const sensor of frame.point_sensors) {
          if (appearance.points && !hidden[sensor.agent_id]) {
            pointCount += sensor.count;
          }
        }
        points.push(pointCount);
        boxes.push(frame.boxes.filter((box) => boxShown(box, hidden, appearance)).length);
        for (const agent of summary.agents) {
          const current = frame.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
          const prior = previous?.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
          const value = isPose(current) && isPose(prior) ? speedMps(prior, current, time - previousTime) : null;
          agentSpeed[agent.agent_id]?.push(value ?? 0);
        }
        const lead = vehicleId ? agentSpeed[vehicleId] : undefined;
        speeds.push(lead && lead.length > 0 ? (lead[lead.length - 1] ?? 0) : 0);
        if (previous && time - previousTime > 150_000 && markers.length < 24) {
          markers.push({ index, label: "Sweep gap" });
        }
        const vehicleNow = frame.agents.find((item) => item.agent_id === vehicleId)?.pose;
        const vehicleThen = previous?.agents.find((item) => item.agent_id === vehicleId)?.pose;
        for (const agent of summary.agents) {
          if (markers.length >= 24) {
            break;
          }
          const now = frame.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
          const then = previous?.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
          if (!isPose(then) && isPose(now)) {
            markers.push({ index, label: `${agent.agent_id} enters` });
          } else if (isPose(then) && !isPose(now)) {
            markers.push({ index, label: `${agent.agent_id} leaves` });
          } else if (
            agent.agent_id !== vehicleId &&
            isPose(now) &&
            isPose(vehicleNow) &&
            isPose(then) &&
            isPose(vehicleThen)
          ) {
            const far = (pose: Pose, origin: Pose) => Math.hypot(pose.tx - origin.tx, pose.ty - origin.ty);
            const wasOut = far(then, vehicleThen) > 80;
            const isOut = far(now, vehicleNow) > 80;
            if (wasOut !== isOut) {
              markers.push({
                index,
                label: `${agent.agent_id} ${isOut ? "leaves range" : "enters range"}`,
              });
            }
          }
        }
        previous = frame;
        previousTime = time;
        if (index >= 1) {
          for (const row of Object.values(agentSpeed)) {
            const next = row[1];
            if (typeof next === "number") {
              row[0] = next;
            }
          }
          const leadSpeed = speeds[1];
          if (typeof leadSpeed === "number") {
            speeds[0] = leadSpeed;
          }
        }
        if (index % 12 === 0 || index === times.length - 1) {
          setSeries({
            points: [...points],
            speeds: [...speeds],
            boxes: [...boxes],
            agentSpeed: Object.fromEntries(Object.entries(agentSpeed).map(([id, values]) => [id, [...values]])),
            markers: [...markers],
          });
        }
      }
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [summary, effectiveToken, hidden, appearance]);

  useEffect(() => {
    if (!summary || !cameraFrame) {
      return;
    }
    const times = summary.times_us;
    const index = times.findIndex((time) => time === cameraFrame.time_us);
    const neighbor = index > 0 ? index - 1 : index + 1;
    const neighborTime = times[neighbor];
    const currentTime = times[index];
    if (index < 0 || typeof neighborTime !== "number" || typeof currentTime !== "number" || neighbor === index) {
      return;
    }
    let cancelled = false;
    const frame = cameraFrame;
    void (async () => {
      const other = await cacheRef.current.loadFrame(summary.scene_id, neighborTime, effectiveToken);
      if (cancelled) {
        return;
      }
      const next: Record<string, number> = {};
      for (const agent of summary.agents) {
        const here = frame.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
        const there = other.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
        if (!isPose(here) || !isPose(there)) {
          continue;
        }
        const value = index > 0 ? speedMps(there, here, currentTime - neighborTime) : speedMps(here, there, neighborTime - currentTime);
        if (value !== null) {
          next[agent.agent_id] = value;
        }
      }
      setLiveSpeed(next);
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [summary, cameraFrame, effectiveToken]);

  const last = summary ? Math.max(summary.times_us.length - 1, 0) : 0;
  const timeUs = summary?.times_us[Math.min(frameIndex, last)];
  const cameras = (summary?.sensors ?? []).filter((sensor) => sensor.modality === "camera");
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

  const tone =
    /could not|incomplete|refused|failed/i.test(status) ? "bad" : /holding|no frustum|no map/i.test(status) ? "wait" : "live";
  function agentSpeed(agentId: string): number | null {
    const live = liveSpeed[agentId];
    if (typeof live === "number") {
      return live;
    }
    const row = series.agentSpeed[agentId];
    const sample = frameIndex > 0 ? row?.[frameIndex] : row?.[1];
    return typeof sample === "number" ? sample : null;
  }
  const splitCamera =
    cameraFrame?.frustums.find((item) => cameraKey(item.agent_id, item.sensor_id) === appearance.splitCamera) ?? null;
  const splitPose = splitCamera ? cameraFrame?.agents.find((agent) => agent.agent_id === splitCamera.agent_id)?.pose : null;
  const splitHud = splitCamera
    ? {
        speed: formatSpeed(agentSpeed(splitCamera.agent_id)),
        heading: isPose(splitPose) ? headingDeg(splitPose) : 0,
        stamp: typeof timeUs === "number" ? formatClock(timeUs) : "0.0 s",
      }
    : null;
  function toggleSplit(key: string) {
    cameraKeyRef.current = key;
    setCameraId(key);
    setAppearance((current) => ({ ...current, splitCamera: current.splitCamera === key ? null : key }));
  }
  function onDividerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onDividerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const row = event.currentTarget.parentElement;
    if (!row || !event.currentTarget.hasPointerCapture(event.pointerId)) {
      return;
    }
    const box = row.getBoundingClientRect();
    if (box.width <= 0) {
      return;
    }
    const next = Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, (event.clientX - box.left) / box.width));
    setAppearance((current) => ({ ...current, splitRatio: next }));
  }

  const cameraChoices = useMemo(() => {
    const seen = new Set<string>();
    const rows: { key: string; agent: string; sensor: string }[] = [];
    for (const frustum of cameraFrame?.frustums ?? []) {
      const key = cameraKey(frustum.agent_id, frustum.sensor_id);
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      rows.push({ key, agent: frustum.agent_id, sensor: frustum.sensor_id });
    }
    return rows;
  }, [cameraFrame]);
  cameraKeysRef.current = cameraChoices.map((row) => row.key);
  onFrustumRef.current = (key) => {
    setAppearance((current) => toggleOneFrustumImage(current, key, cameraKeysRef.current));
  };

  const sceneStem = sceneId.includes("-") ? sceneId.slice(0, sceneId.lastIndexOf("-")) : sceneId || "Scene";
  const crumb = (
    <div className="crumb">
      {isDesktop && (
        <div className="dataset-badge" title={dataRoot || undefined}>
          <span className="dataset-folder-icon" aria-hidden="true">📁</span>
          <span className="dataset-name">{dataBasename || "Dataset"}</span>
          <button
            type="button"
            className="btn-open-dataset"
            onClick={triggerOpenFolder}
            aria-label="Open dataset folder (⌘O)"
            title="Open dataset folder (⌘O)"
          >
            Open…
          </button>
        </div>
      )}
      <span className="crumb-dataset">{sceneId ? sceneStem : "Scenes"}</span>
      <span className="crumb-sep" aria-hidden="true">/</span>
      <Menu
        id="scene-id"
        ariaLabel="Scene"
        value={sceneId}
        options={
          scenes.length === 0
            ? [{ value: "", label: "No scenes" }]
            : scenes.map((id) => ({ value: id, label: id }))
        }
        onChange={(id) => {
          if (id === sceneId) {
            return;
          }
          // Pause now, not when the new summary arrives: a playing scene keeps
          // prefetching frames, and over a slow link those queue ahead of the
          // request for the chosen scene.
          setPlaying(false);
          setSceneId(id);
        }}
      />
    </div>
  );
  const actionTools = (
    <div className="top-actions">
      <button type="button" className="ghost" onClick={() => setExportOpen(true)} disabled={!summary}>
        Export
      </button>
      <button
        type="button"
        className="icon ghost"
        aria-label={appearance.theme === "dark" ? "Use light theme" : "Use dark theme"}
        aria-pressed={appearance.theme === "dark"}
        onClick={() =>
          setAppearance((current) => ({ ...current, theme: current.theme === "dark" ? "light" : "dark" }))
        }
      >
        {appearance.theme === "dark" ? <IconMoon /> : <IconSun />}
      </button>
    </div>
  );

  return (
    <div className={poster ? "viewer viewer-poster" : "viewer"}>
      {datasetError && (
        <div className="dataset-error-banner" role="alert">
          <div className="dataset-error-content">
            <span className="dataset-error-icon" aria-hidden="true">⚠️</span>
            <span className="dataset-error-msg">{datasetError}</span>
          </div>
          <div className="dataset-error-actions">
            <button
              type="button"
              className="dataset-error-choose-btn"
              onClick={triggerOpenFolder}
              aria-label="Choose another dataset folder"
            >
              Choose another folder
            </button>
            <button
              type="button"
              className="dataset-error-dismiss-btn"
              onClick={() => setDatasetError(null)}
              aria-label="Dismiss error"
            >
              ×
            </button>
          </div>
        </div>
      )}
      {toast && (
        <div className="toast-container" role="status" aria-live="polite">
          <div className={`toast toast-${toast.type}`}>
            <span className="toast-text">{toast.text}</span>
            <button
              type="button"
              className="toast-close"
              onClick={() => setToast(null)}
              aria-label="Close notification"
            >
              ×
            </button>
          </div>
        </div>
      )}
      {slot ? createPortal(crumb, slot) : null}
      {actions ? createPortal(actionTools, actions) : null}
      <div className="workspace">
        <aside className="panel" aria-label="Layers and agents">
          <details open>
            <summary>Layers</summary>
            <div className="checks">
              {(
                [
                  ["points", "Points"],
                  ["boxes", "Boxes"],
                  ["frustums", "Frustums"],
                  ["map", "Map"],
                  ["axes", "Axes"],
                ] as const
              ).map(([key, label]) => (
                <EyeToggle
                  key={key}
                  on={appearance[key]}
                  label={label}
                  onChange={() => setAppearance((current) => ({ ...current, [key]: !current[key] }))}
                />
              ))}
              {lidarNote(summary) ? <p className="muted empty-state">Points: {lidarNote(summary)}</p> : null}
              {mapInfo && !mapInfo.available ? <p className="muted empty-state">Map: {mapNoteFor(mapInfo)}</p> : null}
              <EyeToggle
                on={appearance.frustumImages}
                label="Frustum images"
                onChange={() => setAppearance((current) => toggleFrustumImages(current))}
              />
              {appearance.frustumImages ? (
                <Slider
                  label="Image opacity"
                  value={appearance.frustumOpacity}
                  min={0.15}
                  max={1}
                  step={0.05}
                  onChange={(value) => setAppearance((current) => ({ ...current, frustumOpacity: value }))}
                />
              ) : null}
              <details className="cam-fold">
                <summary>
                  Cameras
                  <span className="num">{cameraChoices.filter((row) => showCameraImage(appearance, row.key)).length}</span>
                </summary>
                <div className="cam-actions">
                  <button type="button" onClick={() => setAppearance((current) => setAllFrustumImages(current))}>
                    All
                  </button>
                  <button type="button" onClick={() => setAppearance((current) => setNoFrustumImages(current))}>
                    None
                  </button>
                </div>
                <div className="cam-list">
                  {cameraChoices.length === 0 ? <p className="muted">No cameras in this sample.</p> : null}
                  {cameraChoices.map((row) => (
                    <EyeToggle
                      key={row.key}
                      on={showCameraImage(appearance, row.key)}
                      label={`${agentNames.get(row.agent) ?? row.agent} ${row.sensor}`}
                      onChange={() =>
                        setAppearance((current) =>
                          toggleOneFrustumImage(current, row.key, cameraChoices.map((item) => item.key)),
                        )
                      }
                    />
                  ))}
                </div>
              </details>
            </div>
          </details>
          <details open>
            <summary>Agents</summary>
            <ul className="agent-list">
              {(summary?.agents ?? []).map((agent) => {
                const points =
                  cameraFrame?.point_sensors
                    .filter((sensor) => sensor.agent_id === agent.agent_id)
                    .reduce((sum, sensor) => sum + sensor.count, 0) ?? 0;
                const boxes = cameraFrame?.boxes.filter((box) => box.agent_id === agent.agent_id).length ?? 0;
                return (
                  <li key={agent.agent_id}>
                    <SwatchToggle
                      on={!hidden[agent.agent_id]}
                      label={agentNames.get(agent.agent_id) ?? agent.agent_id}
                      title={agent.agent_id}
                      color={AGENT_INK[agent.agent_type] ?? "#b7b1a6"}
                      meta={
                        <>
                          <span className="num">{points}</span>
                          <span className="num">{boxes}</span>
                        </>
                      }
                      onChange={() =>
                        setHidden((current) => ({
                          ...current,
                          [agent.agent_id]: !current[agent.agent_id],
                        }))
                      }
                    />
                  </li>
                );
              })}
            </ul>
          </details>
          <details open>
            <summary>Classes</summary>
            {categories.length === 0 ? <p className="muted">No classes in this sample.</p> : null}
            <div className="checks">
              {categories.map((category) => (
                <SwatchToggle
                  key={category}
                  on={!appearance.hiddenClasses.includes(category)}
                  label={category}
                  color={cssColor(classColor(category))}
                  onChange={() =>
                    setAppearance((current) => {
                      const next = new Set(current.hiddenClasses);
                      if (next.has(category)) {
                        next.delete(category);
                      } else {
                        next.add(category);
                      }
                      return { ...current, hiddenClasses: [...next] };
                    })
                  }
                />
              ))}
            </div>
          </details>
          <PredictionsBlock
            runs={summary ? runs : []}
            note={runs && runs.length === 0 ? runNote : livePredNote || liveSeries?.note || ""}
            runId={runId}
            onRun={(id) => {
              setRunId(id);
              setCompareId((current) => (current === id ? "" : current));
              setOutputChoice("");
              setEvalPick(null);
            }}
            compareId={activeCompare}
            onCompare={setCompareId}
            outputKey={activeOutput ? outputKey(activeOutput.agent_id, activeOutput.source) : ""}
            onOutput={setOutputChoice}
            sources={sources}
            hiddenSources={hiddenSources}
            onToggleSource={(source) => setHiddenSources((current) => toggleIn(current, source))}
            hiddenStatuses={hiddenStatuses}
            onToggleStatus={(chip) => setHiddenStatuses((current) => toggleIn(current, chip))}
            cooperative={cooperative}
            onToggleCooperative={(view) => setCooperative((current) => toggleIn(current, view))}
            counts={layer ? layer.a.counts : null}
            cooperativeCounts={evalSummary ? evalSummary.a.cooperative : null}
            score={minScore}
            scoreDefault={scoreDefault}
            onScore={(value) => setAppearance((current) => ({ ...current, predictionScore: value }))}
          />
          <details>
            <summary>Display</summary>
            <div className="stack">
              <Segmented
                label="Color"
                value={appearance.colorBy}
                options={[
                  { value: "agent", label: "Agent" },
                  { value: "class", label: "Class" },
                ]}
                onChange={(value) =>
                  setAppearance((current) => ({ ...current, colorBy: value === "class" ? "class" : "agent" }))
                }
              />
              <Segmented
                label="Point size"
                value={String(appearance.pointSize)}
                options={[1, 2, 3, 4].map((size) => ({ value: String(size), label: String(size) }))}
                onChange={(value) => setAppearance((current) => ({ ...current, pointSize: Number(value) }))}
              />
              <Segmented
                label="Boxes"
                value={appearance.boxStyle}
                options={[
                  { value: "wire", label: "Wire" },
                  { value: "solid", label: "Solid" },
                ]}
                onChange={(value) =>
                  setAppearance((current) => ({ ...current, boxStyle: value === "solid" ? "solid" : "wire" }))
                }
              />
              <button type="button" className="ghost" onClick={() => setAppearance(defaultAppearance())}>
                Reset view
              </button>
            </div>
          </details>
        </aside>
        <div className="stage">
          <div className={splitCamera ? "stage-row split" : "stage-row"} style={{ ["--split" as string]: appearance.splitRatio }}>
          <div className="viewport">
            <canvas
              ref={canvasRef}
              className="viewer-canvas"
              tabIndex={0}
              aria-label="Scene view. Click here, then use Space to play and 1 through 5 for viewpoints."
              onPointerDown={() => canvasRef.current?.focus()}
            />
            <div className="label-host" />
            <div className="vignette" aria-hidden="true" />
            {webglError ? (
              <div className="webgl-missing" role="alert">
                <strong>The 3D view needs WebGL, which this browser did not provide.</strong>
                <p>
                  Boxes, points and the map cannot be drawn. The scene list, the timeline and the tables still
                  work. Turn on hardware acceleration or open the page in another browser.
                </p>
                <p className="mono">{webglError}</p>
              </div>
            ) : null}
            <div className="glass status-pill">
              <i className={tone} aria-hidden="true" />
              <span>
                {summary ? summary.scene_id : "No scene"} · {viewpointLabel}
              </span>
            </div>
            {missing.length > 0 ? (
              <ul className="glass empty-pill" aria-label="Missing data">
                {missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : null}
            <div
              className="glass view-pill"
              role="group"
              aria-label="Viewpoint"
              style={{ ["--count" as string]: VIEWPOINTS.length, ["--index" as string]: Math.max(0, VIEWPOINTS.findIndex((item) => item.id === viewpoint)) }}
            >
              <span className="view-slide" aria-hidden="true" />
              {VIEWPOINTS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={viewpoint === item.id}
                  aria-checked={viewpoint === item.id}
                  aria-label={`${item.label} viewpoint (${item.shortcut})`}
                  aria-keyshortcuts={item.shortcut}
                  onClick={() => chooseViewpoint(item.id)}
                >
                  {item.id === "chase" ? "Chase" : item.label}
                </button>
              ))}
            </div>
            <div className="glass legend" aria-label="Legend">
              {legendTypes.map((kind) => (
                <span key={kind}>
                  <i style={{ background: AGENT_INK[kind] ?? "#b7b1a6" }} /> {LEGEND_LABELS[kind] ?? kind}
                </span>
              ))}
              <span>
                <i style={{ background: "#c6f3dc" }} /> Lane
              </span>
              {run?.evaluated
                ? STATUS_CHIPS.map((chip) => (
                    <span key={chip}>
                      <LineSample chip={chip} /> {STATUS_WORDS[chip]}
                    </span>
                  ))
                : null}
            </div>
          </div>
          {splitCamera && cameraFrame && summary ? (
            <>
              <div
                className="split-divider"
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize the camera view"
                aria-valuenow={Math.round(appearance.splitRatio * 100)}
                onPointerDown={onDividerDown}
                onPointerMove={onDividerMove}
              />
              <CameraSplit
                token={effectiveToken}
                sceneId={summary.scene_id}
                timeUs={cameraFrame.time_us}
                nextTimeUs={summary.times_us[frameIndex + 1] ?? null}
                camera={splitCamera}
                boxes={cameraFrame.boxes}
                pointSensors={cameraFrame.point_sensors}
                types={agentTypes}
                names={agentNames}
                hidden={hidden}
                cacheRef={cacheRef}
                hud={splitHud}
                onClose={() => setAppearance((current) => ({ ...current, splitCamera: null }))}
                imagesAvailable={imageNote(summary) === null}
                missingText={imageNote(summary) ? `No image: ${imageNote(summary)}` : "No image file for this sample."}
              />
            </>
          ) : null}
          </div>
          {cameraFrame && summary ? (
            <CameraStrip
              token={effectiveToken}
              sceneId={summary.scene_id}
              timeUs={cameraFrame.time_us}
              nextTimeUs={summary.times_us[frameIndex + 1] ?? null}
              frustums={cameraFrame.frustums}
              types={agentTypes}
              names={agentNames}
              open={appearance.stripOpen}
              onToggleOpen={() => setAppearance((current) => ({ ...current, stripOpen: !current.stripOpen }))}
              splitKey={appearance.splitCamera}
              onSplit={toggleSplit}
              imagesAvailable={imageNote(summary) === null}
              missingText={imageNote(summary) ? `No image: ${imageNote(summary)}` : "No image file for this sample."}
              imageOn={(key) => showCameraImage(appearance, key)}
              onToggleImage={(key) =>
                setAppearance((current) => toggleOneFrustumImage(current, key, cameraKeysRef.current))
              }
            />
          ) : null}
          <p className="status-line">{status}</p>
        </div>
        <aside className="panel panel-right" aria-label="Agent telemetry">
          <h2>Telemetry</h2>
          {(summary?.agents ?? []).filter((agent) => !hidden[agent.agent_id]).map((agent) => {
            const pose = cameraFrame?.agents.find((item) => item.agent_id === agent.agent_id)?.pose;
            const sampleSpeed = agentSpeed(agent.agent_id);
            const history = series.agentSpeed[agent.agent_id] ?? [];
            const paths = sparkPaths(history, 22);
            const points =
              cameraFrame?.point_sensors
                .filter((sensor) => sensor.agent_id === agent.agent_id)
                .reduce((sum, sensor) => sum + sensor.count, 0) ?? 0;
            const ink = AGENT_INK[agent.agent_type] ?? "#b7b1a6";
            return (
              <article key={agent.agent_id} className="tele">
                <header>
                  <i style={{ background: ink }} aria-hidden="true" />
                  <strong title={agent.agent_id}>{agentNames.get(agent.agent_id) ?? agent.agent_id}</strong>
                  <span>{agent.agent_type}</span>
                </header>
                <dl>
                  <div className="span-2">
                    <dt>Speed</dt>
                    <dd className="num">{formatSpeed(sampleSpeed)}</dd>
                  </div>
                  <div>
                    <dt>Heading</dt>
                    <dd className="num">{formatHeading(isPose(pose) ? headingDeg(pose) : null)}</dd>
                  </div>
                  {agent.agent_type === "uav" && isPose(pose) ? (
                    <div>
                      <dt>Altitude</dt>
                      <dd className="num">{pose.tz.toFixed(1)} m</dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>Points</dt>
                    <dd className="num">{points}</dd>
                  </div>
                </dl>
                <svg className="tele-spark" viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true">
                  <path d={paths.area} fill={ink} opacity="0.18" />
                  <path d={paths.line} stroke={ink} />
                </svg>
              </article>
            );
          })}
          <SceneCard summary={summary} frame={cameraFrame} evalSummary={evalSummary} />
          <Inspector
            pick={pick}
            frame={cameraFrame}
            summary={summary}
            token={effectiveToken}
            cache={cacheRef.current}
            evalPick={liveEvalPick}
            gtDetail={gtDetail}
          />
          <Radar frame={cameraFrame} pick={pick} types={agentTypes} />
        </aside>
      </div>
      <Timeline
        length={summary?.times_us.length ?? 0}
        index={Math.min(frameIndex, last)}
        timeUs={typeof timeUs === "number" ? timeUs : null}
        times={summary?.times_us ?? []}
        playing={playing}
        speed={speed}
        loop={loop}
        disabled={!summary || (summary.times_us.length < 2)}
        points={series.points}
        speeds={series.speeds}
        boxes={series.boxes}
        markers={series.markers}
        evalSeries={evalSeries}
        evalCompare={evalCompare}
        onIndex={(index) => setFrameIndex(index)}
        onStep={(delta) => {
          setPlaying(false);
          setFrameIndex((current) => stepIndex(current, delta, lengthRef.current, loop));
        }}
        onToggle={handleTogglePlay}
        onSpeed={setSpeed}
        onLoop={handleToggleLoop}
      />
      <ExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        detail={run?.evaluated ? `The bird's-eye figure also draws prediction run ${run.run_id}.` : undefined}
      >
        <div className="export-bar">
          <Menu
            id="export-source"
            label="Source"
            value={exportSource}
            options={[
              { value: "view", label: "This view" },
              ...cameras.map((sensor) => {
                const key = cameraKey(sensor.agent_id, sensor.sensor_id);
                return { value: key, label: `${sensor.sensor_id} · ${sensor.agent_id}` };
              }),
            ]}
            onChange={setExportSource}
          />
          <Menu
            id="export-format"
            label="Format"
            value={exportFormat}
            options={[
              { value: "mp4", label: "MP4" },
              { value: "gif", label: "GIF" },
              { value: "png", label: "PNG frames" },
            ]}
            onChange={(value) => {
              if (value === "mp4" || value === "gif" || value === "png") {
                setExportFormat(value);
              }
            }}
          />
          <Menu
            id="export-fps"
            label="fps"
            value={String(exportFps)}
            options={EXPORT_FPS.map((item) => ({ value: String(item), label: String(item) }))}
            onChange={(value) => setExportFps(Number(value))}
          />
          <Menu
            id="export-size"
            label="Size"
            value={exportSize}
            options={EXPORT_SIZES.map((item) => ({ value: item.id, label: item.label }))}
            onChange={setExportSize}
          />
          <label htmlFor="export-start">
            From
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
          </label>
          <label htmlFor="export-end">
            To
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
          </label>
          <button type="button" className="ghost" onClick={() => void exportSequence(true)} disabled={!summary || exporting}>
            Save still
          </button>
          <button type="button" className="ghost" onClick={() => void exportSequence(false)} disabled={!summary || exporting}>
            Export sequence
          </button>
        </div>
        <div className="plot-bar">
          <Menu
            id="plot-format"
            label="Plot"
            value={plotFormat}
            options={[
              { value: "png", label: "PNG" },
              { value: "svg", label: "SVG" },
            ]}
            onChange={(value) => {
              if (value === "png" || value === "svg") {
                setPlotFormat(value);
              }
            }}
          />
          <button type="button" className="ghost" onClick={() => void savePlot("bev")} disabled={!summary || plotting}>
            Bird's-eye
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() => void savePlot("overlay")}
            disabled={!summary || plotting || cameras.length === 0}
          >
            Camera overlay
          </button>
          <button type="button" className="ghost" onClick={() => void exportRerun()} disabled={!summary || exporting}>
            Export Rerun
          </button>
        </div>
        <p className="muted">{exportNote || plotNote || "Sequence, still, bird's-eye, camera overlay, and Rerun use the current sample and layers."}</p>
        {plotUrl ? <img className="plot-preview" src={plotUrl} alt={plotAlt} /> : null}
      </ExportDialog>
    </div>
  );
}
