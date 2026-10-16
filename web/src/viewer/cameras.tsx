import { useEffect, useRef, useState, type RefObject } from "react";
import { apiBase, authHeaders, errorDetail } from "../api/client";
import type { FrameCache } from "./cache";
import { cameraKey, type FrameBox, type FrameFrustum, type FrameSensor } from "./types";
import { projectToImage, type Pinhole } from "./project";

type Cloud = { agentId: string; xyz: Float32Array };

const INK: Record<string, string> = {
  vehicle: "#e67828",
  uav: "#2878dc",
};

const blobs = new Map<string, string>();
const missing = new Set<string>();
const inflight = new Map<string, Promise<string | null>>();

function colorFor(agentType: string): string {
  return INK[agentType] ?? "#b7b1a6";
}

function rememberBlob(key: string, blob: Blob): string {
  const existing = blobs.get(key);
  if (existing) {
    blobs.delete(key);
    blobs.set(key, existing);
    return existing;
  }
  const url = URL.createObjectURL(blob);
  blobs.set(key, url);
  while (blobs.size > 40) {
    const oldest = blobs.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    const dropped = blobs.get(oldest);
    blobs.delete(oldest);
    if (dropped) {
      URL.revokeObjectURL(dropped);
    }
  }
  return url;
}

function clearBlobs() {
  for (const url of blobs.values()) {
    URL.revokeObjectURL(url);
  }
  blobs.clear();
  missing.clear();
  inflight.clear();
}

async function loadImage(
  sceneId: string,
  timeUs: number,
  agentId: string,
  sensorId: string,
  token: string,
): Promise<string | null> {
  const key = `${sceneId}|${timeUs}|${agentId}|${sensorId}`;
  if (missing.has(key)) {
    return null;
  }
  const cached = blobs.get(key);
  if (cached) {
    blobs.delete(key);
    blobs.set(key, cached);
    return cached;
  }
  const pending = inflight.get(key);
  if (pending) {
    return pending;
  }
  const request = fetchImage(key, sceneId, timeUs, agentId, sensorId, token);
  inflight.set(key, request);
  try {
    return await request;
  } finally {
    inflight.delete(key);
  }
}

async function fetchImage(
  key: string,
  sceneId: string,
  timeUs: number,
  agentId: string,
  sensorId: string,
  token: string,
): Promise<string | null> {
  const response = await fetch(
    `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/image?time_us=${timeUs}&agent_id=${encodeURIComponent(agentId)}&sensor_id=${encodeURIComponent(sensorId)}`,
    { headers: authHeaders(token) },
  );
  if (response.status === 404) {
    missing.add(key);
    return null;
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return rememberBlob(key, await response.blob());
}

function pinhole(camera: FrameFrustum): Pinhole | null {
  const { fx, fy, cx, cy, pose, image_width_px, image_height_px } = camera;
  if (
    typeof fx !== "number" ||
    typeof fy !== "number" ||
    typeof cx !== "number" ||
    typeof cy !== "number" ||
    fy === 0
  ) {
    return null;
  }
  return {
    pose,
    fx,
    fy,
    cx,
    cy,
    imageWidth: typeof image_width_px === "number" ? image_width_px : null,
    imageHeight: typeof image_height_px === "number" ? image_height_px : null,
  };
}

function drawOverlay(
  canvas: HTMLCanvasElement,
  image: HTMLImageElement,
  camera: Pinhole,
  boxes: FrameBox[],
  clouds: Cloud[],
  types: Map<string, string>,
  hidden: Record<string, boolean>,
  showBoxes: boolean,
  showLidar: boolean,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  const width = image.clientWidth || 1;
  const height = image.clientHeight || 1;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.max(1, Math.round(height * ratio));
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const naturalWidth = image.naturalWidth;
  const naturalHeight = image.naturalHeight;
  if (naturalWidth === 0 || naturalHeight === 0) {
    return;
  }
  const pixelX = camera.imageWidth && camera.imageWidth > 0 ? naturalWidth / camera.imageWidth : 1;
  const pixelY = camera.imageHeight && camera.imageHeight > 0 ? naturalHeight / camera.imageHeight : 1;
  const scale = Math.min(width / naturalWidth, height / naturalHeight);
  const drawnWidth = naturalWidth * scale;
  const drawnHeight = naturalHeight * scale;
  const originX = (width - drawnWidth) / 2;
  const originY = (height - drawnHeight) / 2;
  ctx.setTransform(scale * ratio, 0, 0, scale * ratio, originX * ratio, originY * ratio);
  ctx.lineWidth = 2 / scale;

  if (showBoxes) {
    for (const box of boxes) {
      if (hidden[box.agent_id] || !Array.isArray(box.lines)) {
        continue;
      }
      ctx.strokeStyle = colorFor(types.get(box.agent_id) ?? "");
      ctx.beginPath();
      for (let index = 0; index + 5 < box.lines.length; index += 6) {
        const start = projectToImage(
          camera,
          box.lines[index] ?? 0,
          box.lines[index + 1] ?? 0,
          box.lines[index + 2] ?? 0,
        );
        const end = projectToImage(
          camera,
          box.lines[index + 3] ?? 0,
          box.lines[index + 4] ?? 0,
          box.lines[index + 5] ?? 0,
        );
        if (!start || !end) {
          continue;
        }
        ctx.moveTo(start.u * pixelX, start.v * pixelY);
        ctx.lineTo(end.u * pixelX, end.v * pixelY);
      }
      ctx.stroke();
    }
  }

  if (showLidar) {
    const radius = 1.6 / scale;
    for (const cloud of clouds) {
      if (hidden[cloud.agentId]) {
        continue;
      }
      ctx.fillStyle = colorFor(types.get(cloud.agentId) ?? "");
      for (let index = 0; index + 2 < cloud.xyz.length; index += 3) {
        const pixel = projectToImage(
          camera,
          cloud.xyz[index] ?? 0,
          cloud.xyz[index + 1] ?? 0,
          cloud.xyz[index + 2] ?? 0,
        );
        if (!pixel) {
          continue;
        }
        const u = pixel.u * pixelX;
        const v = pixel.v * pixelY;
        if (u < 0 || v < 0 || u >= naturalWidth || v >= naturalHeight) {
          continue;
        }
        ctx.fillRect(u - radius, v - radius, radius * 2, radius * 2);
      }
    }
  }
}

function CameraPanel({
  camera,
  url,
  boxes,
  clouds,
  types,
  hidden,
  showBoxes,
  showLidar,
}: {
  camera: FrameFrustum;
  url: string | null | undefined;
  boxes: FrameBox[];
  clouds: Cloud[];
  types: Map<string, string>;
  hidden: Record<string, boolean>;
  showBoxes: boolean;
  showLidar: boolean;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const model = pinhole(camera);

  useEffect(() => {
    const image = imgRef.current;
    const canvas = canvasRef.current;
    if (!image || !canvas || !model || !url) {
      return;
    }
    const paint = () => {
      if (image.naturalWidth === 0) {
        return;
      }
      drawOverlay(canvas, image, model, boxes, clouds, types, hidden, showBoxes, showLidar);
    };
    if (image.complete) {
      paint();
    }
    image.addEventListener("load", paint);
    return () => image.removeEventListener("load", paint);
  }, [camera, boxes, clouds, types, hidden, showBoxes, showLidar, url, model]);

  const label = `${camera.sensor_id} · ${camera.agent_id}`;
  return (
    <figure className="camera-panel">
      <figcaption>{label}</figcaption>
      {url ? (
        <div className="camera-frame">
          <img
            ref={imgRef}
            src={url}
            alt={`${label} at this sample`}
          />
          <canvas ref={canvasRef} />
        </div>
      ) : (
        <p className="camera-missing">{url === null ? "No image file for this sample." : "Loading image…"}</p>
      )}
    </figure>
  );
}

export default function CameraStrip({
  token,
  sceneId,
  timeUs,
  nextTimeUs,
  frustums,
  boxes,
  pointSensors,
  types,
  hidden,
  cacheRef,
}: {
  token: string;
  sceneId: string;
  timeUs: number;
  nextTimeUs: number | null;
  frustums: FrameFrustum[];
  boxes: FrameBox[];
  pointSensors: FrameSensor[];
  types: Map<string, string>;
  hidden: Record<string, boolean>;
  cacheRef: RefObject<FrameCache>;
}) {
  const [showBoxes, setShowBoxes] = useState(true);
  const [showLidar, setShowLidar] = useState(false);
  const [shown, setShown] = useState<{
    timeUs: number;
    urls: Record<string, string | null>;
    frustums: FrameFrustum[];
    boxes: FrameBox[];
  } | null>(null);
  const [clouds, setClouds] = useState<Cloud[]>([]);
  const [cloudTime, setCloudTime] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const sceneRef = useRef(sceneId);

  useEffect(() => {
    if (sceneRef.current === sceneId) {
      return;
    }
    sceneRef.current = sceneId;
    clearBlobs();
    setShown(null);
    setClouds([]);
    setCloudTime(null);
  }, [sceneId]);

  useEffect(() => {
    let cancelled = false;
    const sampleFrustums = frustums;
    const sampleBoxes = boxes;
    void (async () => {
      const next: Record<string, string | null> = {};
      try {
        await Promise.all(
          sampleFrustums.map(async (camera) => {
            const key = cameraKey(camera.agent_id, camera.sensor_id);
            next[key] = await loadImage(sceneId, timeUs, camera.agent_id, camera.sensor_id, token);
          }),
        );
      } catch (error) {
        if (!cancelled) {
          setNote(error instanceof Error ? error.message : "Could not load a camera image.");
        }
        return;
      }
      if (cancelled) {
        return;
      }
      setShown({ timeUs, urls: next, frustums: sampleFrustums, boxes: sampleBoxes });
      setNote("");
      if (nextTimeUs === null) {
        return;
      }
      for (const camera of sampleFrustums) {
        void loadImage(sceneId, nextTimeUs, camera.agent_id, camera.sensor_id, token).catch(() => undefined);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sceneId, timeUs, nextTimeUs, token, frustums, boxes]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const loaded: Cloud[] = [];
      for (const sensor of pointSensors) {
        if (sensor.count === 0) {
          continue;
        }
        const buffer = await cacheRef.current.loadPoints(
          sceneId,
          timeUs,
          sensor.agent_id,
          sensor.sensor_id,
          token,
        );
        if (cancelled) {
          return;
        }
        loaded.push({ agentId: sensor.agent_id, xyz: new Float32Array(buffer) });
      }
      if (!cancelled) {
        setClouds(loaded);
        setCloudTime(timeUs);
      }
    })().catch((error: unknown) => {
      if (!cancelled) {
        setNote(error instanceof Error ? error.message : "Could not load lidar for the image overlay.");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [cacheRef, sceneId, timeUs, token, pointSensors]);

  if (frustums.length === 0) {
    return null;
  }

  return (
    <section className="camera-strip" aria-label="Camera images">
      <div className="camera-tools">
        <h3>Cameras</h3>
        <label className="check">
          <input type="checkbox" checked={showBoxes} onChange={() => setShowBoxes((current) => !current)} />
          Boxes
        </label>
        <label className="check">
          <input type="checkbox" checked={showLidar} onChange={() => setShowLidar((current) => !current)} />
          Lidar
        </label>
        {note ? <span className="muted">{note}</span> : null}
      </div>
      <div className="camera-row">
        {(shown?.frustums ?? frustums).map((camera) => {
          const key = cameraKey(camera.agent_id, camera.sensor_id);
          return (
            <CameraPanel
              key={key}
              camera={camera}
              url={shown ? shown.urls[key] : undefined}
              boxes={shown?.boxes ?? []}
              clouds={shown && cloudTime === shown.timeUs ? clouds : []}
              types={types}
              hidden={hidden}
              showBoxes={showBoxes}
              showLidar={showLidar}
            />
          );
        })}
      </div>
    </section>
  );
}
