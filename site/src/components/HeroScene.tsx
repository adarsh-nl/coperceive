import React, { useEffect, useRef, useState, useCallback } from "react";
import * as THREE from "three";
import { loadClip } from "../utils/clipLoader";
import type { ParsedClip, BoxRecord } from "../types/clip";

type ViewpointMode = "orbit" | "chase" | "drone" | "bev";

const BOX_CORNERS = [
  [-0.5, -0.5, -0.5],
  [0.5, -0.5, -0.5],
  [0.5, 0.5, -0.5],
  [-0.5, 0.5, -0.5],
  [-0.5, -0.5, 0.5],
  [0.5, -0.5, 0.5],
  [0.5, 0.5, 0.5],
  [-0.5, 0.5, 0.5],
];

const BOX_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 0], // bottom
  [4, 5], [5, 6], [6, 7], [7, 4], // top
  [0, 4], [1, 5], [2, 6], [3, 7], // uprights
];

export const HeroScene: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [clipData, setClipData] = useState<ParsedClip | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(true);
  const [currentFrameIdx, setCurrentFrameIdx] = useState(0);
  const [viewpoint, setViewpoint] = useState<ViewpointMode>("orbit");
  const [webglSupported, setWebglSupported] = useState(true);
  const [reducedMotion, setReducedMotion] = useState(false);

  // References for Three.js state
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const lidarPointsRef = useRef<THREE.Points | null>(null);
  const boxesLinesRef = useRef<THREE.LineSegments | null>(null);
  const vehicleGroupRef = useRef<THREE.Group | null>(null);
  const droneGroupRef = useRef<THREE.Group | null>(null);
  const droneAltitudeLineRef = useRef<THREE.Line | null>(null);
  const dronePyramidRef = useRef<THREE.LineSegments | null>(null);
  const beaconMeshRef = useRef<THREE.Mesh | null>(null);

  // Frame timing
  const frameIdxRef = useRef(0);
  const isPlayingRef = useRef(true);
  const viewpointRef = useRef<ViewpointMode>("orbit");
  const animFrameIdRef = useRef<number | null>(null);
  const lastStepTimeRef = useRef<number>(0);

  // Orbit drag state
  const isDraggingRef = useRef(false);
  const prevMouseRef = useRef({ x: 0, y: 0 });
  const orbitAngleRef = useRef({ theta: -0.65, phi: 0.55, radius: 52 });
  const orbitTargetRef = useRef(new THREE.Vector3(-95, -12, 0));

  // Sync state to refs
  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  useEffect(() => {
    viewpointRef.current = viewpoint;
  }, [viewpoint]);

  // Reduced motion preference
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mediaQuery.matches) {
      setReducedMotion(true);
      setIsPlaying(false);
      isPlayingRef.current = false;
      // Default to occlusion showcase frame (52)
      setCurrentFrameIdx(52);
      frameIdxRef.current = 52;
    }
    const handler = (e: MediaQueryListEvent) => {
      setReducedMotion(e.matches);
      if (e.matches) {
        setIsPlaying(false);
        isPlayingRef.current = false;
      }
    };
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, []);

  // Fetch clip data
  useEffect(() => {
    const controller = new AbortController();
    loadClip("/hero/manifest.json", "/hero/clip.bin", controller.signal)
      .then((parsed) => {
        setClipData(parsed);
        setLoading(false);
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          console.error("Clip loading error:", err);
          setError(err.message || "Failed to load 3D clip");
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, []);

  // Construct 3D boxes line buffer
  const buildBoxesGeometry = useCallback((boxes: BoxRecord[]) => {
    const positions: number[] = [];
    const colors: number[] = [];

    // Colors
    const colVehicle = new THREE.Color("#38bdf8"); // vehicle only
    const colDrone = new THREE.Color("#c084fc");   // drone only
    const colCoop = new THREE.Color("#4ade80");    // cooperative both
    const colOccluded = new THREE.Color("#f87171"); // occluded hazard alert

    const tempQuat = new THREE.Quaternion();
    const tempVec = new THREE.Vector3();

    for (const b of boxes) {
      const isOccluded = b.trackId === 265;
      const col = isOccluded
        ? colOccluded
        : b.agentFlag === 3
        ? colCoop
        : b.agentFlag === 2
        ? colDrone
        : colVehicle;

      tempQuat.set(b.rotation[0], b.rotation[1], b.rotation[2], b.rotation[3]);

      // 8 corners
      const corners: THREE.Vector3[] = [];
      for (const [sx, sy, sz] of BOX_CORNERS) {
        tempVec.set(sx * b.size[0], sy * b.size[1], sz * b.size[2]);
        tempVec.applyQuaternion(tempQuat);
        tempVec.add(new THREE.Vector3(b.center[0], b.center[1], b.center[2]));
        corners.push(tempVec.clone());
      }

      for (const [start, end] of BOX_EDGES) {
        positions.push(
          corners[start].x, corners[start].y, corners[start].z,
          corners[end].x, corners[end].y, corners[end].z
        );
        colors.push(
          col.r, col.g, col.b,
          col.r, col.g, col.b
        );
      }
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geom.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    return geom;
  }, []);

  // Update scene at frame
  const applyFrame = useCallback(
    (idx: number) => {
      if (!clipData || idx < 0 || idx >= clipData.frames.length) return;
      const frame = clipData.frames[idx];

      // Update LiDAR Points
      if (lidarPointsRef.current) {
        const geom = lidarPointsRef.current.geometry;
        geom.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(frame.lidarPoints, 3)
        );
        geom.attributes.position.needsUpdate = true;
      }

      // Update Boxes
      if (boxesLinesRef.current) {
        boxesLinesRef.current.geometry.dispose();
        boxesLinesRef.current.geometry = buildBoxesGeometry(frame.boxes);
      }

      // Update Vehicle
      if (vehicleGroupRef.current) {
        const vp = frame.vehiclePose;
        vehicleGroupRef.current.position.set(vp.position[0], vp.position[1], vp.position[2]);
        vehicleGroupRef.current.quaternion.set(vp.quaternion[0], vp.quaternion[1], vp.quaternion[2], vp.quaternion[3]);
      }

      // Update Drone
      if (droneGroupRef.current) {
        const dp = frame.dronePose;
        droneGroupRef.current.position.set(dp.position[0], dp.position[1], dp.position[2]);
        droneGroupRef.current.quaternion.set(dp.quaternion[0], dp.quaternion[1], dp.quaternion[2], dp.quaternion[3]);

        // Altitude connector line
        if (droneAltitudeLineRef.current) {
          const lineGeom = droneAltitudeLineRef.current.geometry;
          const pos = lineGeom.attributes.position;
          pos.setXYZ(0, dp.position[0], dp.position[1], dp.position[2]);
          pos.setXYZ(1, dp.position[0], dp.position[1], 0.1);
          pos.needsUpdate = true;
        }

        // Camera footprint pyramid
        if (dronePyramidRef.current) {
          const pyrGeom = dronePyramidRef.current.geometry;
          const pos = pyrGeom.attributes.position;
          const fSize = 13.0;
          const dx = dp.position[0];
          const dy = dp.position[1];
          const dz = dp.position[2];

          // 4 lines from drone down to ground
          pos.setXYZ(0, dx, dy, dz); pos.setXYZ(1, dx - fSize, dy - fSize, 0.05);
          pos.setXYZ(2, dx, dy, dz); pos.setXYZ(3, dx + fSize, dy - fSize, 0.05);
          pos.setXYZ(4, dx, dy, dz); pos.setXYZ(5, dx + fSize, dy + fSize, 0.05);
          pos.setXYZ(6, dx, dy, dz); pos.setXYZ(7, dx - fSize, dy + fSize, 0.05);

          // ground rectangle
          pos.setXYZ(8, dx - fSize, dy - fSize, 0.05); pos.setXYZ(9, dx + fSize, dy - fSize, 0.05);
          pos.setXYZ(10, dx + fSize, dy - fSize, 0.05); pos.setXYZ(11, dx + fSize, dy + fSize, 0.05);
          pos.setXYZ(12, dx + fSize, dy + fSize, 0.05); pos.setXYZ(13, dx - fSize, dy + fSize, 0.05);
          pos.setXYZ(14, dx - fSize, dy + fSize, 0.05); pos.setXYZ(15, dx - fSize, dy - fSize, 0.05);
          pos.needsUpdate = true;
        }
      }

      // Update Beacon Ring if occlusion is present
      const occluded = frame.boxes.find((b) => b.trackId === 265);
      if (beaconMeshRef.current) {
        if (occluded) {
          beaconMeshRef.current.visible = true;
          beaconMeshRef.current.position.set(occluded.center[0], occluded.center[1], 0.2);
        } else {
          beaconMeshRef.current.visible = false;
        }
      }
    },
    [clipData, buildBoxesGeometry]
  );

  // Initialize Three.js
  useEffect(() => {
    if (!containerRef.current || !clipData) return;

    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;

    // Check WebGL availability
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
      renderer.setSize(width, height);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0x0a0d14, 1);
    } catch {
      setWebglSupported(false);
      return;
    }

    rendererRef.current = renderer;
    containerRef.current.replaceChildren(renderer.domElement);

    const scene = new THREE.Scene();
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.5, 500);
    cameraRef.current = camera;

    // Subtle background ambient light
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambientLight);

    // Directional light for subtle 3D vehicle shadows
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.6);
    dirLight.position.set(50, -60, 80);
    scene.add(dirLight);

    // Add Map Polylines (Lane centres in #7dcea0, Road edges in #334155)
    const mapGeom = new THREE.BufferGeometry();
    mapGeom.setAttribute("position", new THREE.Float32BufferAttribute(clipData.mapPositions, 3));
    mapGeom.setAttribute("color", new THREE.Float32BufferAttribute(clipData.mapColors, 3));
    const mapMaterial = new THREE.LineBasicMaterial({
      vertexColors: true,
      linewidth: 1.5,
      transparent: true,
      opacity: 0.9,
    });
    const mapLines = new THREE.LineSegments(mapGeom, mapMaterial);
    scene.add(mapLines);

    // Ground subtle grid
    const gridHelper = new THREE.GridHelper(120, 24, 0x1a2436, 0x111927);
    gridHelper.rotation.x = Math.PI / 2;
    gridHelper.position.set(clipData.origin[0], clipData.origin[1], -0.1);
    scene.add(gridHelper);

    // LiDAR Points
    const ptsGeom = new THREE.BufferGeometry();
    ptsGeom.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(clipData.frames[0].lidarPoints, 3)
    );
    const ptsMaterial = new THREE.PointsMaterial({
      color: 0x22d3ee,
      size: 0.55,
      transparent: true,
      opacity: 0.82,
      blending: THREE.AdditiveBlending,
    });
    const lidarPoints = new THREE.Points(ptsGeom, ptsMaterial);
    scene.add(lidarPoints);
    lidarPointsRef.current = lidarPoints;

    // Boxes LineSegments
    const boxesGeom = buildBoxesGeometry(clipData.frames[0].boxes);
    const boxesMaterial = new THREE.LineBasicMaterial({
      vertexColors: true,
      linewidth: 1.8,
      transparent: true,
      opacity: 0.95,
    });
    const boxesLines = new THREE.LineSegments(boxesGeom, boxesMaterial);
    scene.add(boxesLines);
    boxesLinesRef.current = boxesLines;

    // Vehicle 3D Model Group
    const vehicleGroup = new THREE.Group();
    // Chassis box (4.8m x 2.0m x 1.5m)
    const chassisGeom = new THREE.BoxGeometry(4.8, 2.0, 1.4);
    const chassisMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      roughness: 0.3,
      metalness: 0.8,
    });
    const chassisMesh = new THREE.Mesh(chassisGeom, chassisMat);
    chassisMesh.position.z = 0.7;
    vehicleGroup.add(chassisMesh);

    // Chassis wire glow
    const wireGeom = new THREE.WireframeGeometry(chassisGeom);
    const wireMat = new THREE.LineBasicMaterial({ color: 0x22d3ee, linewidth: 1.5 });
    const wireLine = new THREE.LineSegments(wireGeom, wireMat);
    wireLine.position.z = 0.7;
    vehicleGroup.add(wireLine);

    // Top LiDAR cylinder
    const lidarPodGeom = new THREE.CylinderGeometry(0.2, 0.2, 0.28, 16);
    const lidarPodMat = new THREE.MeshBasicMaterial({ color: 0x22d3ee });
    const lidarPod = new THREE.Mesh(lidarPodGeom, lidarPodMat);
    lidarPod.rotation.x = Math.PI / 2;
    lidarPod.position.set(0, 0, 1.55);
    vehicleGroup.add(lidarPod);

    scene.add(vehicleGroup);
    vehicleGroupRef.current = vehicleGroup;

    // Drone 3D Model Group
    const droneGroup = new THREE.Group();
    // Quadcopter body
    const droneBodyGeom = new THREE.BoxGeometry(1.2, 1.2, 0.25);
    const droneBodyMat = new THREE.MeshStandardMaterial({ color: 0xc084fc, metalness: 0.9 });
    const droneBody = new THREE.Mesh(droneBodyGeom, droneBodyMat);
    droneGroup.add(droneBody);

    // Drone 4 arms & rotors
    const armMat = new THREE.MeshBasicMaterial({ color: 0xe9d5ff });
    const rotorMat = new THREE.MeshBasicMaterial({ color: 0xc084fc, wireframe: true });
    const armCoords = [
      [1.1, 1.1],
      [-1.1, 1.1],
      [1.1, -1.1],
      [-1.1, -1.1],
    ];
    for (const [ax, ay] of armCoords) {
      const armGeom = new THREE.CylinderGeometry(0.04, 0.04, 1.4);
      const armMesh = new THREE.Mesh(armGeom, armMat);
      armMesh.position.set(ax * 0.45, ay * 0.45, 0);
      armMesh.rotation.z = Math.atan2(ay, ax) + Math.PI / 2;
      droneGroup.add(armMesh);

      const rotorGeom = new THREE.CylinderGeometry(0.38, 0.38, 0.03, 12);
      const rotorMesh = new THREE.Mesh(rotorGeom, rotorMat);
      rotorMesh.position.set(ax, ay, 0.1);
      rotorMesh.rotation.x = Math.PI / 2;
      droneGroup.add(rotorMesh);
    }

    scene.add(droneGroup);
    droneGroupRef.current = droneGroup;

    // Drone altitude indicator line (dashed)
    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 27, 0, 0, 0], 3));
    const altLineMat = new THREE.LineDashedMaterial({
      color: 0xc084fc,
      dashSize: 1.0,
      gapSize: 0.6,
      linewidth: 1.2,
      transparent: true,
      opacity: 0.6,
    });
    const altLine = new THREE.Line(lineGeom, altLineMat);
    altLine.computeLineDistances();
    scene.add(altLine);
    droneAltitudeLineRef.current = altLine;

    // Drone downward camera pyramid footprint
    const pyrGeom = new THREE.BufferGeometry();
    pyrGeom.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(16 * 3), 3));
    const pyrMat = new THREE.LineBasicMaterial({
      color: 0xc084fc,
      transparent: true,
      opacity: 0.45,
      linewidth: 1.2,
    });
    const pyrLines = new THREE.LineSegments(pyrGeom, pyrMat);
    scene.add(pyrLines);
    dronePyramidRef.current = pyrLines;

    // Occlusion Ground Alert Beacon Ring
    const ringGeom = new THREE.RingGeometry(1.6, 2.0, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0xf87171,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85,
    });
    const beaconMesh = new THREE.Mesh(ringGeom, ringMat);
    beaconMesh.visible = false;
    scene.add(beaconMesh);
    beaconMeshRef.current = beaconMesh;

    // WebGL Context loss handler
    const handleContextLost = (e: Event) => {
      e.preventDefault();
      console.warn("WebGL context lost");
      setWebglSupported(false);
    };
    const canvas = renderer.domElement;
    canvas.addEventListener("webglcontextlost", handleContextLost, false);

    // Initial frame apply
    applyFrame(frameIdxRef.current);

    // Animation Loop
    const renderLoop = (timestamp: number) => {
      animFrameIdRef.current = requestAnimationFrame(renderLoop);

      // Playback timing (100 ms per frame = 10 fps)
      if (isPlayingRef.current) {
        if (timestamp - lastStepTimeRef.current >= 100) {
          lastStepTimeRef.current = timestamp;
          frameIdxRef.current = (frameIdxRef.current + 1) % clipData.frames.length;
          setCurrentFrameIdx(frameIdxRef.current);
          applyFrame(frameIdxRef.current);
        }
      }

      // Pulse beacon ring if visible
      if (beaconMeshRef.current && beaconMeshRef.current.visible) {
        const pulse = 1.0 + Math.sin(timestamp * 0.008) * 0.25;
        beaconMeshRef.current.scale.set(pulse, pulse, 1);
      }

      // Camera position based on viewpoint mode
      const activeFrame = clipData.frames[frameIdxRef.current];
      const vpMode = viewpointRef.current;

      if (vpMode === "orbit") {
        // Smooth orbit camera
        const { theta, phi, radius } = orbitAngleRef.current;
        const target = orbitTargetRef.current;
        const cx = target.x + radius * Math.sin(phi) * Math.sin(theta);
        const cy = target.y - radius * Math.sin(phi) * Math.cos(theta);
        const cz = target.z + radius * Math.cos(phi);
        camera.position.set(cx, cy, cz);
        camera.up.set(0, 0, 1);
        camera.lookAt(target);
      } else if (vpMode === "chase") {
        // Chase ground vehicle
        const vPos = activeFrame.vehiclePose.position;
        const vQuat = new THREE.Quaternion(...activeFrame.vehiclePose.quaternion);
        const offset = new THREE.Vector3(-14, 0, 6.5).applyQuaternion(vQuat);
        camera.position.set(vPos[0] + offset.x, vPos[1] + offset.y, vPos[2] + offset.z);
        camera.up.set(0, 0, 1);
        camera.lookAt(vPos[0] + 10, vPos[1], vPos[2] + 1.2);
      } else if (vpMode === "drone") {
        // UAV top-angle downward view
        const dPos = activeFrame.dronePose.position;
        camera.position.set(dPos[0] - 2, dPos[1] - 8, dPos[2] + 5);
        camera.up.set(0, 0, 1);
        camera.lookAt(dPos[0], dPos[1] + 15, 0);
      } else if (vpMode === "bev") {
        // Top-down bird's-eye view
        camera.position.set(-95, -12, 75);
        camera.up.set(0, 1, 0);
        camera.lookAt(-95, -12, 0);
      }

      renderer.render(scene, camera);
    };

    animFrameIdRef.current = requestAnimationFrame(renderLoop);

    // Resize Handler
    const handleResize = () => {
      if (!containerRef.current || !rendererRef.current || !cameraRef.current) return;
      const w = containerRef.current.clientWidth;
      const h = containerRef.current.clientHeight;
      cameraRef.current.aspect = w / h;
      cameraRef.current.updateProjectionMatrix();
      rendererRef.current.setSize(w, h);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
      window.removeEventListener("resize", handleResize);
      canvas.removeEventListener("webglcontextlost", handleContextLost);
      renderer.dispose();
    };
  }, [clipData, buildBoxesGeometry, applyFrame]);

  // Pointer drag for Orbit controls
  const handlePointerDown = (e: React.PointerEvent) => {
    if (viewpoint !== "orbit") return;
    isDraggingRef.current = true;
    prevMouseRef.current = { x: e.clientX, y: e.clientY };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!isDraggingRef.current || viewpoint !== "orbit") return;
    const dx = e.clientX - prevMouseRef.current.x;
    const dy = e.clientY - prevMouseRef.current.y;
    prevMouseRef.current = { x: e.clientX, y: e.clientY };

    orbitAngleRef.current.theta -= dx * 0.008;
    orbitAngleRef.current.phi = Math.max(
      0.15,
      Math.min(Math.PI / 2.1, orbitAngleRef.current.phi - dy * 0.008)
    );
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    isDraggingRef.current = false;
    try {
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
  };

  const handleWheel = (e: React.WheelEvent) => {
    if (viewpoint !== "orbit") return;
    orbitAngleRef.current.radius = Math.max(
      20,
      Math.min(100, orbitAngleRef.current.radius + e.deltaY * 0.05)
    );
  };

  // Keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === " ") {
      e.preventDefault();
      setIsPlaying((prev) => !prev);
    } else if (e.key === "1") {
      setViewpoint("orbit");
    } else if (e.key === "2") {
      setViewpoint("chase");
    } else if (e.key === "3") {
      setViewpoint("drone");
    } else if (e.key === "4") {
      setViewpoint("bev");
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      if (clipData) {
        const next = Math.max(0, frameIdxRef.current - 1);
        frameIdxRef.current = next;
        setCurrentFrameIdx(next);
        applyFrame(next);
      }
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      if (clipData) {
        const next = Math.min(clipData.frames.length - 1, frameIdxRef.current + 1);
        frameIdxRef.current = next;
        setCurrentFrameIdx(next);
        applyFrame(next);
      }
    }
  };

  const jumpToOcclusion = () => {
    if (!clipData) return;
    const target = 52;
    frameIdxRef.current = target;
    setCurrentFrameIdx(target);
    applyFrame(target);
    setIsPlaying(false);
  };

  if (error || !webglSupported) {
    return (
      <div className="relative w-full aspect-[16/9] md:aspect-[21/9] bg-[#0a0d14] rounded-xl overflow-hidden border border-[#232e42] flex items-center justify-center">
        <img
          src="/hero/poster.webp"
          alt="3D Visualization fallback: Town03 cooperative intersection with ground vehicle and aerial drone"
          className="w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#0a0d14]/90 via-[#0a0d14]/40 to-transparent flex flex-col justify-end p-6">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-zinc-900/80 backdrop-blur border border-zinc-700/60 rounded-full text-xs text-zinc-300 w-max mb-2">
            <span className="w-2 h-2 rounded-full bg-cyan-400"></span>
            Static High-Fidelity Poster (WebGL Fallback)
          </div>
          <p className="text-sm text-zinc-300 max-w-xl">
            Town03-000 cooperative scenario (Griffin-25m dataset): Ground CAV LiDAR (cyan) sweeping while Drone UAV (violet, 27m) detects occluded pedestrian #265 around the corner.
          </p>
        </div>
      </div>
    );
  }

  const activeFrame = clipData ? clipData.frames[currentFrameIdx] : null;
  const isOcclusionActive = activeFrame?.boxes.some((b) => b.trackId === 265);

  return (
    <div
      className="relative w-full aspect-[16/10] md:aspect-[21/10] lg:aspect-[2.2/1] bg-[#0a0d14] rounded-xl overflow-hidden border border-[#232e42] shadow-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      role="region"
      aria-label="Interactive 3D Cooperative Scene. Use Space to toggle playback, 1-4 for viewpoints, Left/Right arrows to scrub frames."
    >
      {/* 3D Canvas */}
      <div
        ref={containerRef}
        className="w-full h-full cursor-grab active:cursor-grabbing select-none"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onWheel={handleWheel}
      />

      {/* Loading overlay */}
      {loading && (
        <div className="absolute inset-0 bg-[#0a0d14]/80 backdrop-blur-sm flex flex-col items-center justify-center gap-3 z-30">
          <div className="w-8 h-8 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
          <p className="text-xs font-mono text-zinc-400">Streaming cooperative sensor sweeps...</p>
        </div>
      )}

      {/* Top HUD: Real-time telemetry */}
      <div className="absolute top-4 left-4 right-4 flex flex-wrap items-center justify-between gap-3 pointer-events-none z-20">
        <div className="flex items-center gap-2 pointer-events-auto">
          {/* Agent indicators */}
          <div className="flex items-center gap-2 bg-[#0e131d]/85 backdrop-blur border border-[#232e42] px-3 py-1.5 rounded-lg text-xs font-mono">
            <span className="flex items-center gap-1.5 text-cyan-300 font-medium">
              <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
              CAV (Ground)
            </span>
            <span className="text-zinc-600">|</span>
            <span className="flex items-center gap-1.5 text-purple-300 font-medium">
              <span className="w-2 h-2 rounded-full bg-purple-400" />
              UAV (Aerial 27m)
            </span>
          </div>

          {/* Occlusion badge */}
          {isOcclusionActive && (
            <div className="flex items-center gap-1.5 bg-red-950/80 backdrop-blur border border-red-500/50 px-2.5 py-1.5 rounded-lg text-xs font-mono text-red-200 animate-pulse">
              <span className="w-2 h-2 rounded-full bg-red-400" />
              Occluded Pedestrian #265 Detected (17.5m)
            </div>
          )}
        </div>

        {/* Viewpoint selector buttons */}
        <div className="flex items-center gap-1 bg-[#0e131d]/85 backdrop-blur border border-[#232e42] p-1 rounded-lg pointer-events-auto text-xs font-mono">
          {(["orbit", "chase", "drone", "bev"] as ViewpointMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => setViewpoint(mode)}
              className={`px-2.5 py-1 rounded transition-colors ${
                viewpoint === mode
                  ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60"
              }`}
              title={`Switch viewpoint: ${mode}`}
              aria-label={`Viewpoint: ${mode}`}
            >
              {mode.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Reduced motion banner if active */}
      {reducedMotion && (
        <div className="absolute top-16 left-4 pointer-events-auto z-20">
          <div className="bg-zinc-900/90 border border-zinc-700/60 rounded-md px-3 py-1 text-xs text-zinc-300 flex items-center gap-2">
            <span>Reduced motion active (paused at frame 52)</span>
            <button
              onClick={() => setIsPlaying((p) => !p)}
              className="text-cyan-400 hover:underline font-mono"
            >
              {isPlaying ? "Pause" : "Play"}
            </button>
          </div>
        </div>
      )}

      {/* Bottom Scrubber & Controls */}
      <div className="absolute bottom-4 left-4 right-4 bg-[#0e131d]/90 backdrop-blur-md border border-[#232e42] p-3 rounded-xl flex flex-col gap-2 z-20">
        <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsPlaying((prev) => !prev)}
              className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition focus-visible:ring-1 focus-visible:ring-cyan-400"
              aria-label={isPlaying ? "Pause 3D playback" : "Play 3D playback"}
            >
              {isPlaying ? (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </button>

            <button
              onClick={jumpToOcclusion}
              className="px-2.5 py-1 rounded bg-red-950/60 hover:bg-red-900/60 border border-red-700/50 text-red-300 text-xs transition"
              title="Jump to the frame where pedestrian #265 is occluded from ground vehicle"
            >
              Jump to Occlusion (t=1123.7s)
            </button>
          </div>

          {/* Telemetry counters */}
          <div className="flex items-center gap-4">
            <span>
              Frame: <span className="text-zinc-200 num">{currentFrameIdx + 1}</span>/100
            </span>
            <span>
              Time: <span className="text-zinc-200 num">{activeFrame ? (activeFrame.timeUs / 1e6).toFixed(2) : "0.00"}</span> s
            </span>
            <span className="hidden sm:inline">
              LiDAR: <span className="text-cyan-300 num">{activeFrame ? (activeFrame.lidarPoints.length / 3).toLocaleString() : 0}</span> pts
            </span>
          </div>
        </div>

        {/* Timeline Slider */}
        <div className="relative flex items-center w-full">
          <input
            type="range"
            min={0}
            max={clipData ? clipData.frames.length - 1 : 99}
            value={currentFrameIdx}
            onChange={(e) => {
              const val = parseInt(e.target.value, 10);
              setCurrentFrameIdx(val);
              frameIdxRef.current = val;
              applyFrame(val);
            }}
            className="w-full h-1.5 bg-[#1b2436] rounded-lg appearance-none cursor-pointer accent-cyan-400 focus:outline-none"
            aria-label="Timeline scrubber"
          />
          {/* Key event marker for occlusion at frame 52 */}
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-red-500 rounded-full border border-white pointer-events-none"
            style={{ left: "52%" }}
            title="Occlusion moment (frame 52)"
          />
        </div>
      </div>
    </div>
  );
};
