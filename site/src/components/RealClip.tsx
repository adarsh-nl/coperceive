import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { tokens } from "../tokens";
import type { BoxRecord, ParsedClip } from "../types/clip";
import { loadClip } from "../utils/clipLoader";

const FACES: [number, number, number][] = [
  [0, 1, 2], [0, 2, 3],
  [4, 6, 5], [4, 7, 6],
  [0, 5, 1], [0, 4, 5],
  [3, 2, 6], [3, 6, 7],
  [0, 3, 7], [0, 7, 4],
  [1, 5, 6], [1, 6, 2],
];

function boxColor(flag: number): THREE.Color {
  if (flag === 3) return new THREE.Color(tokens.hit);
  if (flag === 2) return new THREE.Color(tokens.drone);
  return new THREE.Color(tokens.vehicle);
}

function appendBox(
  positions: number[],
  colors: number[],
  center: [number, number, number],
  size: [number, number, number],
  rotation: [number, number, number, number],
  color: THREE.Color,
) {
  const quat = new THREE.Quaternion(rotation[0], rotation[1], rotation[2], rotation[3]);
  const corners = [
    [-0.5, -0.5, -0.5],
    [0.5, -0.5, -0.5],
    [0.5, 0.5, -0.5],
    [-0.5, 0.5, -0.5],
    [-0.5, -0.5, 0.5],
    [0.5, -0.5, 0.5],
    [0.5, 0.5, 0.5],
    [-0.5, 0.5, 0.5],
  ].map(([sx, sy, sz]) =>
    new THREE.Vector3(sx * size[0], sy * size[1], sz * size[2]).applyQuaternion(quat).add(
      new THREE.Vector3(center[0], center[1], center[2]),
    ),
  );
  for (const [a, b, c] of FACES) {
    for (const corner of [corners[a], corners[b], corners[c]]) {
      positions.push(corner.x, corner.y, corner.z);
      colors.push(color.r, color.g, color.b);
    }
  }
}

function detectionGeometry(boxes: BoxRecord[]): THREE.BufferGeometry {
  const positions: number[] = [];
  const colors: number[] = [];
  for (const box of boxes) {
    appendBox(positions, colors, box.center, box.size, box.rotation, boxColor(box.agentFlag));
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
  ctx.font = "600 72px sans-serif";
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
  sprite.scale.set(7.2, 1.8, 1);
  sprite.renderOrder = 3;
  return sprite;
}

function droneIcon(): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: ctx ? new THREE.CanvasTexture(canvas) : null,
      transparent: true,
      depthTest: false,
    }),
  );
  if (!ctx) return sprite;
  ctx.clearRect(0, 0, 256, 256);
  ctx.lineCap = "round";
  ctx.strokeStyle = tokens.drone;
  ctx.lineWidth = 26;
  ctx.beginPath();
  ctx.moveTo(36, 36);
  ctx.lineTo(220, 220);
  ctx.moveTo(220, 36);
  ctx.lineTo(36, 220);
  ctx.stroke();
  ctx.fillStyle = tokens.drone;
  for (const [x, y] of [
    [36, 36],
    [220, 36],
    [36, 220],
    [220, 220],
  ]) {
    ctx.beginPath();
    ctx.arc(x, y, 22, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.beginPath();
  ctx.arc(128, 128, 26, 0, Math.PI * 2);
  ctx.fill();
  sprite.scale.set(9.5, 9.5, 1);
  sprite.renderOrder = 2;
  return sprite;
}

function marker(color: string, size: [number, number, number]): THREE.Mesh {
  return new THREE.Mesh(
    new THREE.BoxGeometry(size[0], size[1], size[2]),
    new THREE.MeshBasicMaterial({ color }),
  );
}

/** Recorded clip. Both platform poses come from the file. */
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
    "Recorded scene. A vehicle and a drone, each in its own colour.",
  );
  renderer.domElement.className = "block h-full w-full";

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, width / Math.max(1, height), 0.5, 800);
  camera.up.set(0, 0, 1);

  const mapGeom = new THREE.BufferGeometry();
  mapGeom.setAttribute("position", new THREE.Float32BufferAttribute(clip.mapPositions, 3));
  mapGeom.setAttribute("color", new THREE.Float32BufferAttribute(clip.mapColors, 3));
  scene.add(new THREE.LineSegments(mapGeom, new THREE.LineBasicMaterial({ vertexColors: true })));

  const points = new THREE.Points(
    new THREE.BufferGeometry(),
    new THREE.PointsMaterial({ color: tokens.vehicle, size: 0.85, sizeAttenuation: true }),
  );
  scene.add(points);

  const detections = new THREE.Mesh(
    new THREE.BufferGeometry(),
    new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.5, depthWrite: false }),
  );
  detections.renderOrder = 1;
  scene.add(detections);

  const vehicle = new THREE.Group();
  const vehicleBody = marker(tokens.vehicle, [4.8, 2.05, 1.7]);
  vehicleBody.position.z = 0.85;
  vehicle.add(vehicleBody);
  scene.add(vehicle);

  const drone = droneIcon();
  scene.add(drone);

  const tether = new THREE.Mesh(
    new THREE.CylinderGeometry(0.42, 0.42, 1, 12),
    new THREE.MeshBasicMaterial({ color: tokens.drone }),
  );
  tether.rotation.x = Math.PI / 2;
  tether.frustumCulled = false;
  scene.add(tether);

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(3.6, 32),
    new THREE.MeshBasicMaterial({ color: tokens.drone, transparent: true, opacity: 0.5, depthWrite: false }),
  );
  shadow.frustumCulled = false;
  scene.add(shadow);

  const vehicleLabel = labelSprite("Vehicle", tokens.vehicle);
  const droneLabel = labelSprite("Drone", tokens.drone);
  vehicleLabel.scale.set(11, 2.6, 1);
  droneLabel.scale.set(11, 2.6, 1);
  scene.add(vehicleLabel, droneLabel);
  points.frustumCulled = false;
  detections.frustumCulled = false;

  const apply = (index: number) => {
    const frame = clip.frames[index];
    points.geometry.setAttribute("position", new THREE.Float32BufferAttribute(frame.lidarPoints, 3));
    detections.geometry.dispose();
    detections.geometry = detectionGeometry(frame.boxes);

    const vp = frame.vehiclePose.position;
    const vq = frame.vehiclePose.quaternion;
    vehicle.position.set(vp[0], vp[1], vp[2]);
    vehicle.quaternion.set(vq[0], vq[1], vq[2], vq[3]);

    const dp = frame.dronePose.position;
    drone.position.set(dp[0], dp[1], dp[2]);

    const height = Math.max(0.5, dp[2]);
    tether.scale.set(1, height, 1);
    tether.position.set(dp[0], dp[1], height / 2);
    shadow.position.set(dp[0], dp[1], 0.15);

    vehicleLabel.position.set(vp[0], vp[1], vp[2] + 3.4);
    droneLabel.position.set(dp[0], dp[1], dp[2] + 2.4);

    const lookX = (vp[0] + dp[0]) / 2;
    const lookY = (vp[1] + dp[1]) / 2;
    // Pulled back so the vertical pair stays above the headline for the whole loop.
    camera.position.set(lookX - 74, lookY - 54, 8);
    camera.lookAt(lookX, lookY, 6);
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
