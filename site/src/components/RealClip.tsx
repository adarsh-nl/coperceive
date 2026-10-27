import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { tokens } from "../tokens";
import type { ParsedClip } from "../types/clip";
import { sceneDescription } from "../utils/agents";
import { loadClip } from "../utils/clipLoader";

/**
 * Point look from the desktop viewer (`web/src/viewer/Viewer.tsx` draws one
 * `Points` object; `bloom.ts` blooms it). A bloom pass is too heavy for this
 * page, so the same additive glow is a soft point sprite. Each agent's sweep
 * is its own `Points`: white near that agent, the agent's colour far from it.
 */
const POINT_VERTEX = `
  uniform vec3 uOrigin;
  varying float vDist;
  void main() {
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vDist = distance(position, uOrigin);
    gl_PointSize = clamp(820.0 / max(1.0, -mvPosition.z), 2.0, 22.0);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const POINT_FRAGMENT = `
  uniform vec3 uColor;
  varying float vDist;
  void main() {
    vec2 p = gl_PointCoord - vec2(0.5);
    float d = length(p);
    if (d > 0.5) discard;
    float glow = exp(-dot(p, p) * 16.0);
    float t = smoothstep(2.0, 12.0, vDist);
    vec3 nearColor = vec3(1.0, 0.98, 0.94);
    vec3 color = mix(nearColor, uColor, t);
    gl_FragColor = vec4(color * glow * 1.15, glow);
  }
`;

const ORBIT_RADIUS = 48;
const ORBIT_HEIGHT = 54;
const ORBIT_LOOK_Z = 6;
const ORBIT_SPEED = 0.11;
const ORBIT_START = -0.8;
/** Metres kept around the farthest agent when the orbit widens for a spread-out group. */
const ORBIT_MARGIN = 12;

/** `#rrggbb` to 0–255 channels, with no colour-space conversion (the shader takes sRGB as is). */
function hexRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

function glowTexture(r: number, g: number, b: number): THREE.CanvasTexture {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const texture = new THREE.CanvasTexture(canvas);
  if (!ctx) return texture;
  const grad = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.2, "rgba(190,214,255,1)");
  grad.addColorStop(0.42, `rgba(${r},${g},${b},0.95)`);
  grad.addColorStop(0.7, `rgba(${r},${g},${b},0.28)`);
  grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  texture.needsUpdate = true;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Recorded agent poses and sweeps. Nothing else is added. */
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

    loadClip(undefined, controller.signal)
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
        const started = performance.now();
        const step = (now: number) => {
          raf = requestAnimationFrame(step);
          if (!activeRef.current) return;
          if (now - last >= 100) {
            last = now;
            index = (index + 1) % clip.frames.length;
            mounted.apply(index);
          }
          mounted.place((now - started) / 1000);
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
  renderer.domElement.setAttribute("aria-label", `Recorded scene. ${sceneDescription(clip.agents)}`);
  renderer.domElement.className = "block h-full w-full";

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(52, width / Math.max(1, height), 0.5, 1400);
  camera.up.set(0, 0, 1);

  const mapGeom = new THREE.BufferGeometry();
  mapGeom.setAttribute("position", new THREE.Float32BufferAttribute(clip.mapPositions, 3));
  scene.add(
    new THREE.LineSegments(
      mapGeom,
      new THREE.LineBasicMaterial({ color: tokens.lanes }),
    ),
  );

  // Agents that carry a sweep somewhere in the clip. The camera orbits their centroid.
  const sensing = clip.agents
    .map((_, i) => i)
    .filter((i) => clip.frames.some((f) => f.agents[i].points.length > 0));
  const anchorAgents = sensing.length > 0 ? sensing : clip.agents.map((_, i) => i);

  const sweeps = sensing.map((i) => {
    const origin = new THREE.Vector3();
    const points = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.ShaderMaterial({
        uniforms: {
          uOrigin: { value: origin },
          uColor: { value: new THREE.Vector3(...hexRgb(clip.agents[i].color)).divideScalar(255) },
        },
        vertexShader: POINT_VERTEX,
        fragmentShader: POINT_FRAGMENT,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        transparent: true,
      }),
    );
    points.frustumCulled = false;
    points.renderOrder = 1;
    scene.add(points);
    return { index: i, origin, points };
  });

  // Drones are glow markers with a faint tether to the nearest ground agent.
  const ground = clip.agents.map((a, i) => (a.kind === "drone" ? -1 : i)).filter((i) => i >= 0);
  const drones = clip.agents.flatMap((agent, i) => {
    if (agent.kind !== "drone") return [];
    const marker = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: glowTexture(...hexRgb(agent.color)),
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
        depthTest: false,
      }),
    );
    marker.scale.set(3.6, 3.6, 1);
    marker.renderOrder = 3;
    scene.add(marker);

    const tetherGeom = new THREE.BufferGeometry();
    tetherGeom.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(6), 3));
    const tether = new THREE.Line(
      tetherGeom,
      new THREE.LineBasicMaterial({
        color: agent.color,
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
      }),
    );
    tether.frustumCulled = false;
    tether.renderOrder = 2;
    scene.add(tether);
    return [{ index: i, marker, tether }];
  });

  const centroid = (index: number, out: THREE.Vector3) => {
    let n = 0;
    out.set(0, 0, 0);
    for (const i of anchorAgents) {
      const a = clip.frames[index].agents[i];
      if (!a.present) continue;
      out.x += a.position[0];
      out.y += a.position[1];
      out.z += a.position[2];
      n += 1;
    }
    if (n > 0) out.divideScalar(n);
    return n > 0;
  };

  // Widen the orbit once for the whole clip, so the camera does not pump.
  let spread = 0;
  const probe = new THREE.Vector3();
  clip.frames.forEach((frame, index) => {
    if (!centroid(index, probe)) return;
    for (const i of anchorAgents) {
      const a = frame.agents[i];
      if (a.present) spread = Math.max(spread, Math.hypot(a.position[0] - probe.x, a.position[1] - probe.y));
    }
  });
  const orbitScale = Math.max(1, (spread + ORBIT_MARGIN) / (ORBIT_RADIUS / 2));

  const anchor = new THREE.Vector3();

  const apply = (index: number) => {
    const frame = clip.frames[index];
    for (const sweep of sweeps) {
      const a = frame.agents[sweep.index];
      sweep.points.geometry.setAttribute("position", new THREE.BufferAttribute(a.points, 3));
      sweep.origin.set(a.position[0], a.position[1], a.position[2]);
    }
    centroid(index, anchor);

    for (const drone of drones) {
      const dp = frame.agents[drone.index].position;
      drone.marker.position.set(dp[0], dp[1], dp[2]);
      let best = -1;
      let bestD = Infinity;
      for (const g of ground) {
        const a = frame.agents[g];
        if (!a.present) continue;
        const d = Math.hypot(a.position[0] - dp[0], a.position[1] - dp[1]);
        if (d < bestD) {
          bestD = d;
          best = g;
        }
      }
      drone.tether.visible = best >= 0;
      if (best < 0) continue;
      const gp = frame.agents[best].position;
      const tetherPos = drone.tether.geometry.attributes.position as THREE.BufferAttribute;
      tetherPos.setXYZ(0, dp[0], dp[1], dp[2]);
      tetherPos.setXYZ(1, gp[0], gp[1], gp[2] + 1.2);
      tetherPos.needsUpdate = true;
    }
  };

  const place = (seconds: number) => {
    const angle = ORBIT_START + seconds * ORBIT_SPEED;
    camera.position.set(
      anchor.x + Math.cos(angle) * ORBIT_RADIUS * orbitScale,
      anchor.y + Math.sin(angle) * ORBIT_RADIUS * orbitScale,
      ORBIT_HEIGHT * orbitScale,
    );
    camera.lookAt(anchor.x, anchor.y, ORBIT_LOOK_Z);
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
  place(0);
  renderer.render(scene, camera);
  return { renderer, scene, camera, apply: framed, place };
}
