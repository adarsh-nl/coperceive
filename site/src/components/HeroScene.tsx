import React, { useEffect, useRef, useState, useCallback } from "react";
import * as THREE from "three";
import { loadClip } from "../utils/clipLoader";
import type { ParsedClip, BoxRecord } from "../types/clip";
import { tokens } from "../tokens";

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
  const [webglSupported, setWebglSupported] = useState(true);
  const [reducedMotion, setReducedMotion] = useState(false);

  // References for Three.js objects
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

  // Scripted playback timing
  const frameIdxRef = useRef(0);
  const isPlayingRef = useRef(true);
  const animFrameIdRef = useRef<number | null>(null);
  const lastStepTimeRef = useRef<number>(0);

  // Sync state to ref
  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

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

  // Construct 3D boxes line buffer with strict token colors
  const buildBoxesGeometry = useCallback((boxes: BoxRecord[]) => {
    const positions: number[] = [];
    const colors: number[] = [];

    const colVehicle = new THREE.Color(tokens.data.vehicle);
    const colDrone = new THREE.Color(tokens.data.uav);
    const colCoop = new THREE.Color(tokens.status.ok);
    const colOccluded = new THREE.Color(tokens.status.warn);

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

  // Initialize Three.js scripted film scene
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
      renderer.setClearColor(0x000000, 1);
    } catch {
      setWebglSupported(false);
      return;
    }

    rendererRef.current = renderer;
    containerRef.current.replaceChildren(renderer.domElement);

    const scene = new THREE.Scene();
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(40, width / height, 0.5, 500);
    cameraRef.current = camera;

    // Ambient light
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambientLight);

    // Subtle directional light
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
    dirLight.position.set(40, -50, 70);
    scene.add(dirLight);

    // Map Polylines (Lane centres and Road edges)
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

    // Subtle ground grid
    const gridHelper = new THREE.GridHelper(120, 24, 0x222222, 0x111111);
    gridHelper.rotation.x = Math.PI / 2;
    gridHelper.position.set(clipData.origin[0], clipData.origin[1], -0.1);
    scene.add(gridHelper);

    // Vehicle LiDAR points in vehicle orange
    const ptsGeom = new THREE.BufferGeometry();
    ptsGeom.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(clipData.frames[0].lidarPoints, 3)
    );
    const ptsMaterial = new THREE.PointsMaterial({
      color: new THREE.Color(tokens.data.vehicle),
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
    const chassisGeom = new THREE.BoxGeometry(4.8, 2.0, 1.4);
    const chassisMat = new THREE.MeshStandardMaterial({
      color: 0x1c1c1e,
      roughness: 0.3,
      metalness: 0.8,
    });
    const chassisMesh = new THREE.Mesh(chassisGeom, chassisMat);
    chassisMesh.position.z = 0.7;
    vehicleGroup.add(chassisMesh);

    const wireGeom = new THREE.WireframeGeometry(chassisGeom);
    const wireMat = new THREE.LineBasicMaterial({
      color: new THREE.Color(tokens.data.vehicle),
      linewidth: 1.5,
    });
    const wireLine = new THREE.LineSegments(wireGeom, wireMat);
    wireLine.position.z = 0.7;
    vehicleGroup.add(wireLine);

    // Top LiDAR cylinder
    const lidarPodGeom = new THREE.CylinderGeometry(0.2, 0.2, 0.28, 16);
    const lidarPodMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(tokens.data.vehicle) });
    const lidarPod = new THREE.Mesh(lidarPodGeom, lidarPodMat);
    lidarPod.rotation.x = Math.PI / 2;
    lidarPod.position.set(0, 0, 1.55);
    vehicleGroup.add(lidarPod);

    scene.add(vehicleGroup);
    vehicleGroupRef.current = vehicleGroup;

    // Drone 3D Model Group
    const droneGroup = new THREE.Group();
    const droneBodyGeom = new THREE.BoxGeometry(1.2, 1.2, 0.25);
    const droneBodyMat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(tokens.data.uav),
      metalness: 0.8,
    });
    const droneBody = new THREE.Mesh(droneBodyGeom, droneBodyMat);
    droneGroup.add(droneBody);

    const armMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(tokens.data.infrastructure) });
    const rotorMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(tokens.data.uav),
      wireframe: true,
    });
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
      color: new THREE.Color(tokens.data.uav),
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

    // Drone downward camera footprint pyramid
    const pyrGeom = new THREE.BufferGeometry();
    pyrGeom.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(16 * 3), 3));
    const pyrMat = new THREE.LineBasicMaterial({
      color: new THREE.Color(tokens.data.uav),
      transparent: true,
      opacity: 0.45,
      linewidth: 1.2,
    });
    const pyrLines = new THREE.LineSegments(pyrGeom, pyrMat);
    scene.add(pyrLines);
    dronePyramidRef.current = pyrLines;

    // Occlusion Ground Alert Beacon Ring (warn colour tokens.status.warn)
    const ringGeom = new THREE.RingGeometry(1.6, 2.0, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(tokens.status.warn),
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

    // Scripted Cinematic Animation Loop
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

      // Scripted Cinematic Camera Trajectory:
      // The camera glides along a smooth spline as the scenario unfolds:
      // Phase 1 (frames 0-40): Elevated follow-approach along vehicle trajectory.
      // Phase 2 (frames 40-75): Moves into focal view of the blind intersection where pedestrian (track 265) appears.
      // Phase 3 (frames 75-99): Broadens into bird's-eye cooperative synthesis.
      const currentProgress = frameIdxRef.current / (clipData.frames.length - 1);
      const activeFrame = clipData.frames[frameIdxRef.current];
      const vp = activeFrame.vehiclePose.position;

      // Smooth camera position progression
      const camX = vp[0] - 22 + Math.sin(currentProgress * Math.PI) * 12;
      const camY = vp[1] - 32 + Math.cos(currentProgress * Math.PI * 0.7) * 8;
      const camZ = 28 - Math.sin(currentProgress * Math.PI * 0.8) * 10;

      camera.position.set(camX, camY, camZ);
      camera.up.set(0, 0, 1);

      // Look at intersection area around vehicle and occluded hazard
      const lookX = vp[0] + 6;
      const lookY = vp[1] + 8;
      const lookZ = 1.5;
      camera.lookAt(lookX, lookY, lookZ);

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

  // Keyboard navigation: Spacebar toggles playback
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === " ") {
      e.preventDefault();
      setIsPlaying((prev) => !prev);
    }
  };

  if (error || !webglSupported) {
    return (
      <div className="relative w-full aspect-[16/9] md:aspect-[21/9] bg-canvas rounded-2xl overflow-hidden border border-hairline flex items-center justify-center">
        <img
          src="/hero/poster.webp"
          alt="3D Visualization fallback: Town03 cooperative intersection with ground vehicle and aerial drone"
          className="w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-canvas via-canvas/40 to-transparent flex flex-col justify-end p-8">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-glass backdrop-blur-22 border border-hairline rounded-full text-xs text-secondary w-max mb-2">
            <span className="w-1.5 h-1.5 rounded-full bg-warn"></span>
            Static High-Fidelity Poster (WebGL Fallback)
          </div>
          <p className="text-sm text-secondary max-w-xl">
            Town03-000 cooperative scenario (Griffin-25m): Ground CAV LiDAR (orange) sweeping while Drone UAV (blue, 27m) detects occluded pedestrian (track 265).
          </p>
        </div>
      </div>
    );
  }

  const activeFrame = clipData ? clipData.frames[currentFrameIdx] : null;
  const isOcclusionActive = activeFrame?.boxes.some((b) => b.trackId === 265);

  return (
    <div
      className="relative w-full aspect-[16/10] md:aspect-[21/10] lg:aspect-[2.2/1] bg-canvas rounded-2xl overflow-hidden border border-hairline shadow-2xl focus:outline-none focus-visible:ring-1 focus-visible:ring-uav"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      role="region"
      aria-label="Scripted 3D Cooperative Perception Film. Press Space to pause or play."
    >
      {/* 3D Canvas */}
      <div
        ref={containerRef}
        className="w-full h-full select-none"
      />

      {/* Loading overlay */}
      {loading && (
        <div className="absolute inset-0 bg-canvas/80 backdrop-blur-22 flex flex-col items-center justify-center gap-3 z-30">
          <div className="w-6 h-6 border-2 border-uav border-t-transparent rounded-full animate-spin" />
          <p className="text-xs font-mono text-tertiary">Streaming sensor sweeps...</p>
        </div>
      )}

      {/* Top Telemetry Overlay */}
      <div className="absolute top-4 left-4 right-4 flex flex-wrap items-center justify-between gap-3 pointer-events-none z-20">
        <div className="flex items-center gap-2 pointer-events-auto">
          {/* Agent indicators */}
          <div className="flex items-center gap-2.5 bg-glass backdrop-blur-22 border border-hairline px-3 py-1.5 rounded-full text-xs font-mono">
            <span className="flex items-center gap-1.5 text-vehicle font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-vehicle animate-pulse" />
              CAV (Ground)
            </span>
            <span className="text-tertiary">|</span>
            <span className="flex items-center gap-1.5 text-uav font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-uav" />
              UAV (Aerial 27m)
            </span>
          </div>

          {/* Real-time Occlusion Alert Badge */}
          {isOcclusionActive && (
            <div className="flex items-center gap-1.5 bg-glass backdrop-blur-22 border border-warn/40 px-3 py-1.5 rounded-full text-xs font-mono text-warn animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-warn" />
              Occluded Pedestrian (Track 265) Detected (17.5m)
            </div>
          )}
        </div>

        {/* Link to docs viewer */}
        <div className="pointer-events-auto">
          <a
            href="https://github.com/adarsh-nl/coperceive/blob/main/docs/viewer.md"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 bg-glass hover:bg-glass/90 backdrop-blur-22 border border-hairline hover:border-hairline-bright px-3 py-1.5 rounded-full text-xs text-secondary hover:text-primary transition"
          >
            <span>Open the viewer</span>
            <svg className="w-3.5 h-3.5 text-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>
      </div>

      {/* Reduced motion banner if active */}
      {reducedMotion && (
        <div className="absolute top-16 left-4 pointer-events-auto z-20">
          <div className="bg-glass backdrop-blur-22 border border-hairline rounded-full px-3.5 py-1.5 text-xs text-secondary flex items-center gap-3">
            <span>Reduced motion active (paused at frame 52)</span>
            <button
              onClick={() => setIsPlaying((p) => !p)}
              className="text-uav hover:underline font-mono"
            >
              {isPlaying ? "Pause" : "Play film"}
            </button>
          </div>
        </div>
      )}

      {/* Bottom Minimal Film Controls */}
      <div className="absolute bottom-4 left-4 right-4 flex items-center justify-between pointer-events-none z-20">
        <div className="pointer-events-auto">
          <button
            onClick={() => setIsPlaying((prev) => !prev)}
            className="flex items-center gap-2 bg-glass hover:bg-glass/90 backdrop-blur-22 border border-hairline hover:border-hairline-bright px-3.5 py-1.5 rounded-full text-xs font-mono text-secondary hover:text-primary transition focus-visible:ring-1 focus-visible:ring-uav"
            aria-label={isPlaying ? "Pause 3D film" : "Play 3D film"}
          >
            {isPlaying ? (
              <>
                <svg className="w-3.5 h-3.5 text-primary" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
                </svg>
                <span>Pause</span>
              </>
            ) : (
              <>
                <svg className="w-3.5 h-3.5 text-primary" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
                <span>Play</span>
              </>
            )}
          </button>
        </div>

        {/* Telemetry frame timestamp */}
        <div className="bg-glass backdrop-blur-22 border border-hairline px-3 py-1.5 rounded-full text-xs font-mono text-secondary flex items-center gap-3">
          <span>
            t = <span className="text-primary num">{activeFrame ? (activeFrame.timeUs / 1e6).toFixed(2) : "0.00"}</span> s
          </span>
          <span className="text-tertiary">|</span>
          <span>
            Frame <span className="text-primary num">{currentFrameIdx + 1}</span>/100
          </span>
        </div>
      </div>
    </div>
  );
};
