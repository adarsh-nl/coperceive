import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { tokens } from "../tokens";
import type { BoxRecord, ParsedClip } from "../types/clip";
import { loadClip } from "../utils/clipLoader";

const CORNERS: [number, number, number][] = [
  [-0.5, -0.5, -0.5],
  [0.5, -0.5, -0.5],
  [0.5, 0.5, -0.5],
  [-0.5, 0.5, -0.5],
  [-0.5, -0.5, 0.5],
  [0.5, -0.5, 0.5],
  [0.5, 0.5, 0.5],
  [-0.5, 0.5, 0.5],
];

const EDGES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 0],
  [4, 5], [5, 6], [6, 7], [7, 4],
  [0, 4], [1, 5], [2, 6], [3, 7],
];

/** Lane samples from the clip, the arm opposite the recorded vehicle, far end first. */
const VEHICLE2_LANE: [number, number][] = [
  [-77.63, -84.37], [-77.64, -81.87], [-77.65, -79.37], [-77.66, -76.87],
  [-77.66, -74.37], [-77.67, -71.87], [-77.68, -69.37], [-77.68, -66.87],
  [-77.69, -64.37], [-77.7, -61.87], [-77.7, -59.37], [-77.71, -56.87],
  [-77.72, -54.37], [-77.72, -51.87], [-77.73, -49.37], [-77.74, -46.87],
  [-77.74, -44.37], [-77.75, -41.87], [-77.76, -39.37], [-77.77, -36.87],
  [-77.77, -34.37],
];

const SWEEP_COUNT = 380;

function boxColor(flag: number): THREE.Color {
  if (flag === 3) return new THREE.Color(tokens.hit);
  if (flag === 2) return new THREE.Color(tokens.drone);
  return new THREE.Color(tokens.vehicle);
}

function boxGeometry(boxes: BoxRecord[]): THREE.BufferGeometry {
  const positions: number[] = [];
  const colors: number[] = [];
  const quat = new THREE.Quaternion();
  const vec = new THREE.Vector3();
  const corners: THREE.Vector3[] = [];
  for (const box of boxes) {
    const color = boxColor(box.agentFlag);
    quat.set(box.rotation[0], box.rotation[1], box.rotation[2], box.rotation[3]);
    corners.length = 0;
    for (const [sx, sy, sz] of CORNERS) {
      vec.set(sx * box.size[0], sy * box.size[1], sz * box.size[2]);
      vec.applyQuaternion(quat);
      vec.x += box.center[0];
      vec.y += box.center[1];
      vec.z += box.center[2];
      corners.push(vec.clone());
    }
    for (const [a, b] of EDGES) {
      positions.push(corners[a].x, corners[a].y, corners[a].z, corners[b].x, corners[b].y, corners[b].z);
      colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
    }
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geom.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  return geom;
}

function labelSprite(text: string, color: string): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  if (!ctx) return new THREE.Sprite();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.font = "600 64px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(0, 0, 0, 0.85)";
  ctx.strokeText(text, 256, 64);
  ctx.fillStyle = color;
  ctx.fillText(text, 256, 64);
  const material = new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(canvas),
    transparent: true,
    depthTest: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(12, 3, 1);
  sprite.renderOrder = 3;
  return sprite;
}

function smallDrone(): THREE.Group {
  const group = new THREE.Group();
  const material = new THREE.MeshBasicMaterial({ color: tokens.drone });
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.72, 0.72, 0.22, 18), material);
  body.rotation.x = Math.PI / 2;
  group.add(body);
  for (const [x, y] of [
    [1.05, 1.05],
    [1.05, -1.05],
    [-1.05, 1.05],
    [-1.05, -1.05],
  ] as const) {
    const rotor = new THREE.Mesh(new THREE.SphereGeometry(0.2, 8, 6), material);
    rotor.position.set(x, y, 0.1);
    group.add(rotor);
  }
  return group;
}

function vehicle2Pose(frame: number, frameCount: number): { x: number; y: number; yaw: number } {
  const span = VEHICLE2_LANE.length - 1;
  const t = frameCount <= 1 ? 0 : frame / (frameCount - 1);
  const scaled = t * span;
  const index = Math.min(span - 1, Math.floor(scaled));
  const mix = scaled - index;
  const a = VEHICLE2_LANE[index];
  const b = VEHICLE2_LANE[index + 1];
  return {
    x: a[0] + (b[0] - a[0]) * mix,
    y: a[1] + (b[1] - a[1]) * mix,
    yaw: Math.atan2(b[1] - a[1], b[0] - a[0]),
  };
}

function writeSweep(target: Float32Array, x: number, y: number, yaw: number) {
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  for (let i = 0; i < SWEEP_COUNT; i += 1) {
    const angle = (i / SWEEP_COUNT) * Math.PI * 2;
    const radius = 2.4 + (i % 8) * 1.05;
    const forward = Math.cos(angle) * radius + 3.2;
    const side = Math.sin(angle) * radius * 0.72;
    target[i * 3] = x + forward * cos - side * sin;
    target[i * 3 + 1] = y + forward * sin + side * cos;
    target[i * 3 + 2] = 0.35 + (i % 5) * 0.16;
  }
}

/** Recorded clip, plus one illustrative vehicle on a lane from that clip. */
export function RealClip({ active, onReady }: { active: boolean; onReady: () => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const controller = new AbortController();
    let disposed = false;
    let raf = 0;
    let renderer: THREE.WebGLRenderer | null = null;

    loadClip(undefined, undefined, controller.signal)
      .then((clip) => {
        if (disposed) return;
        const mounted = mount(host, clip);
        if (!mounted) {
          setFailed(true);
          return;
        }
        renderer = mounted.renderer;
        onReady();
        let last = 0;
        let index = 0;
        const step = (now: number) => {
          raf = requestAnimationFrame(step);
          if (!activeRef.current) return;
          if (now - last >= 100) {
            last = now;
            index = (index + 1) % clip.frames.length;
            mounted.apply(index);
          }
          mounted.renderer.render(mounted.scene, mounted.camera);
        };
        raf = requestAnimationFrame(step);
      })
      .catch((error: unknown) => {
        if (disposed || controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
      });

    return () => {
      disposed = true;
      controller.abort();
      cancelAnimationFrame(raf);
      renderer?.dispose();
      host.replaceChildren();
    };
  }, [onReady]);

  return (
    <div className="absolute inset-0" ref={hostRef}>
      {failed ? <p className="sr-only">The scene could not play.</p> : null}
    </div>
  );
}

function mount(host: HTMLDivElement, clip: ParsedClip) {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
      preserveDrawingBuffer: true,
    });
  } catch {
    return null;
  }
  const width = Math.max(1, host.clientWidth);
  const height = Math.max(1, host.clientHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(width, height);
  renderer.setClearColor(0x000000, 1);
  host.replaceChildren(renderer.domElement);
  renderer.domElement.dataset.bev = "hero";
  renderer.domElement.setAttribute("role", "img");
  renderer.domElement.setAttribute(
    "aria-label",
    "Recorded scene. A vehicle and a drone, plus an illustrative second vehicle.",
  );
  renderer.domElement.className = "block h-full w-full";

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, width / Math.max(1, height), 0.5, 1400);
  camera.up.set(0, 0, 1);
  camera.position.set(-125, -155, 90);
  camera.lookAt(-95, -42, 7);

  const mapGeom = new THREE.BufferGeometry();
  mapGeom.setAttribute("position", new THREE.Float32BufferAttribute(clip.mapPositions, 3));
  mapGeom.setAttribute("color", new THREE.Float32BufferAttribute(clip.mapColors, 3));
  scene.add(new THREE.LineSegments(mapGeom, new THREE.LineBasicMaterial({ vertexColors: true })));

  const points = new THREE.Points(
    new THREE.BufferGeometry(),
    new THREE.PointsMaterial({ color: tokens.vehicle, size: 0.55, sizeAttenuation: true }),
  );
  points.frustumCulled = false;
  scene.add(points);

  const boxes = new THREE.LineSegments(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ vertexColors: true }),
  );
  boxes.frustumCulled = false;
  scene.add(boxes);

  const vehicle = new THREE.Group();
  const vehicleBody = new THREE.Mesh(
    new THREE.BoxGeometry(4.8, 2.05, 1.7),
    new THREE.MeshBasicMaterial({ color: tokens.vehicle }),
  );
  vehicleBody.position.z = 0.85;
  vehicle.add(vehicleBody);
  scene.add(vehicle);

  const vehicle2 = new THREE.Group();
  const vehicle2Body = new THREE.Mesh(
    new THREE.BoxGeometry(4.8, 2.05, 1.7),
    new THREE.MeshBasicMaterial({ color: tokens.vehicle2 }),
  );
  vehicle2Body.position.z = 0.85;
  vehicle2.add(vehicle2Body);
  scene.add(vehicle2);

  const sweep = new Float32Array(SWEEP_COUNT * 3);
  const sweepGeom = new THREE.BufferGeometry();
  sweepGeom.setAttribute("position", new THREE.BufferAttribute(sweep, 3));
  const sweepPoints = new THREE.Points(
    sweepGeom,
    new THREE.PointsMaterial({ color: tokens.vehicle2, size: 0.55, sizeAttenuation: true }),
  );
  sweepPoints.frustumCulled = false;
  scene.add(sweepPoints);

  const drone = smallDrone();
  scene.add(drone);

  const tetherGeom = new THREE.BufferGeometry();
  tetherGeom.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(6), 3));
  const tether = new THREE.Line(
    tetherGeom,
    new THREE.LineDashedMaterial({
      color: tokens.drone,
      transparent: true,
      opacity: 0.38,
      dashSize: 1.1,
      gapSize: 0.75,
    }),
  );
  tether.frustumCulled = false;
  scene.add(tether);

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(1.7, 24),
    new THREE.MeshBasicMaterial({
      color: tokens.drone,
      transparent: true,
      opacity: 0.28,
      depthWrite: false,
    }),
  );
  shadow.frustumCulled = false;
  scene.add(shadow);

  const vehicleLabel = labelSprite("Vehicle", tokens.vehicle);
  const droneLabel = labelSprite("Drone", tokens.drone);
  const vehicle2Label = labelSprite("Vehicle 2", tokens.vehicle2);
  vehicle2Label.scale.set(14, 3.2, 1);
  scene.add(vehicleLabel, droneLabel, vehicle2Label);

  const apply = (index: number) => {
    const frame = clip.frames[index];
    points.geometry.setAttribute("position", new THREE.Float32BufferAttribute(frame.lidarPoints, 3));
    boxes.geometry.dispose();
    boxes.geometry = boxGeometry(frame.boxes);

    const vp = frame.vehiclePose.position;
    const vq = frame.vehiclePose.quaternion;
    vehicle.position.set(vp[0], vp[1], vp[2]);
    vehicle.quaternion.set(vq[0], vq[1], vq[2], vq[3]);
    vehicleLabel.position.set(vp[0], vp[1], vp[2] + 3.2);

    const second = vehicle2Pose(index, clip.frames.length);
    vehicle2.position.set(second.x, second.y, 0);
    vehicle2.rotation.set(0, 0, second.yaw);
    vehicle2Label.position.set(second.x, second.y, 3.2);
    writeSweep(sweep, second.x, second.y, second.yaw);
    (sweepGeom.attributes.position as THREE.BufferAttribute).needsUpdate = true;

    const dp = frame.dronePose.position;
    drone.position.set(dp[0], dp[1], dp[2]);
    const tetherPos = tether.geometry.attributes.position as THREE.BufferAttribute;
    tetherPos.setXYZ(0, dp[0], dp[1], dp[2]);
    tetherPos.setXYZ(1, dp[0], dp[1], 0.15);
    tetherPos.needsUpdate = true;
    tether.computeLineDistances();
    shadow.position.set(dp[0], dp[1], 0.12);
    droneLabel.position.set(dp[0], dp[1], dp[2] + 2.2);
  };

  let sizedW = 0;
  let sizedH = 0;
  const resize = () => {
    const w = Math.max(1, host.clientWidth);
    const h = Math.max(1, host.clientHeight);
    if (w === sizedW && h === sizedH) return;
    sizedW = w;
    sizedH = h;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(w, h);
  };

  const framed = (index: number) => {
    apply(index);
    resize();
  };

  framed(0);
  renderer.render(scene, camera);
  return { renderer, scene, camera, apply: framed };
}
