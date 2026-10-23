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

/** Recorded clip. Vehicle and drone poses both come from the file. */
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
      {failed ? (
        <p className="sr-only">The scene could not play.</p>
      ) : null}
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
    "Recorded scene. A vehicle and a drone, with lidar and boxes.",
  );
  renderer.domElement.className = "block h-full w-full";

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, width / Math.max(1, height), 0.5, 800);
  camera.up.set(0, 0, 1);

  const mapGeom = new THREE.BufferGeometry();
  mapGeom.setAttribute("position", new THREE.Float32BufferAttribute(clip.mapPositions, 3));
  mapGeom.setAttribute("color", new THREE.Float32BufferAttribute(clip.mapColors, 3));
  scene.add(new THREE.LineSegments(mapGeom, new THREE.LineBasicMaterial({ vertexColors: true })));

  const points = new THREE.Points(
    new THREE.BufferGeometry(),
    new THREE.PointsMaterial({ color: tokens.vehicle, size: 0.45, sizeAttenuation: true }),
  );
  scene.add(points);

  const boxes = new THREE.LineSegments(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ vertexColors: true }),
  );
  scene.add(boxes);

  const vehicle = new THREE.Mesh(
    new THREE.SphereGeometry(0.7, 16, 12),
    new THREE.MeshBasicMaterial({ color: tokens.vehicle }),
  );
  const drone = new THREE.Mesh(
    new THREE.SphereGeometry(0.55, 16, 12),
    new THREE.MeshBasicMaterial({ color: tokens.drone }),
  );
  const dropGeom = new THREE.BufferGeometry();
  dropGeom.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(6), 3));
  const drop = new THREE.Line(dropGeom, new THREE.LineBasicMaterial({ color: tokens.drone }));
  scene.add(vehicle, drone, drop);

  const apply = (index: number) => {
    const frame = clip.frames[index];
    const geom = points.geometry;
    geom.setAttribute("position", new THREE.Float32BufferAttribute(frame.lidarPoints, 3));
    boxes.geometry.dispose();
    boxes.geometry = boxGeometry(frame.boxes);
    const vp = frame.vehiclePose.position;
    const dp = frame.dronePose.position;
    vehicle.position.set(vp[0], vp[1], vp[2]);
    drone.position.set(dp[0], dp[1], dp[2]);
    const dropPos = drop.geometry.attributes.position as THREE.BufferAttribute;
    dropPos.setXYZ(0, dp[0], dp[1], dp[2]);
    dropPos.setXYZ(1, dp[0], dp[1], 0);
    dropPos.needsUpdate = true;
    camera.position.set(vp[0] - 20, vp[1] - 32, Math.max(24, dp[2] + 8));
    camera.lookAt(vp[0], vp[1], 1);
    const w = Math.max(1, host.clientWidth);
    const h = Math.max(1, host.clientHeight);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(w, h);
  };

  apply(0);
  renderer.render(scene, camera);
  return { renderer, scene, camera, apply };
}
