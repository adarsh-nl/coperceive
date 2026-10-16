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

const STRIP_WIDTH = 640;
const DECODERS = 3;

const workerSource = `
self.onmessage = async (event) => {
  const data = event.data;
  try {
    const blob = new Blob([data.buffer], { type: data.type || "image/png" });
    let bitmap;
    try {
      bitmap = await createImageBitmap(blob, { resizeWidth: ${STRIP_WIDTH}, resizeQuality: "low" });
    } catch (err) {
      bitmap = await createImageBitmap(blob);
    }
    self.postMessage({ id: data.id, bitmap }, [bitmap]);
  } catch (error) {
    self.postMessage({ id: data.id, error: String(error && error.message ? error.message : error) });
  }
};
`;

type PendingDecode = {
  resolve: (bitmap: ImageBitmap) => void;
  reject: (error: Error) => void;
};

let workerUrl: string | null = null;
let workerCount = 0;
let decodeId = 1;
const spareWorkers: Worker[] = [];
const decodeJobs = new Map<number, PendingDecode>();
const decodeQueue: { id: number; buffer: ArrayBuffer; type: string }[] = [];

function workerScript(): string {
  if (!workerUrl) {
    workerUrl = URL.createObjectURL(new Blob([workerSource], { type: "text/javascript" }));
  }
  return workerUrl;
}

function attachDecoder(worker: Worker) {
  worker.onmessage = (event: MessageEvent<{ id: number; bitmap?: ImageBitmap; error?: string }>) => {
    const pending = decodeJobs.get(event.data.id);
    decodeJobs.delete(event.data.id);
    if (pending) {
      if (event.data.bitmap) {
        pending.resolve(event.data.bitmap);
      } else {
        pending.reject(new Error(event.data.error ?? "Could not decode the image."));
      }
    } else {
      event.data.bitmap?.close();
    }
    spareWorkers.push(worker);
    drainDecoders();
  };
}

function takeDecoder(): Worker {
  const existing = spareWorkers.pop();
  if (existing) {
    return existing;
  }
  const worker = new Worker(workerScript());
  workerCount += 1;
  attachDecoder(worker);
  return worker;
}

function drainDecoders() {
  while (decodeQueue.length > 0 && (spareWorkers.length > 0 || workerCount < DECODERS)) {
    const job = decodeQueue.shift();
    if (!job) {
      break;
    }
    takeDecoder().postMessage(job, [job.buffer]);
  }
}

function decodeBitmap(buffer: ArrayBuffer, type: string): Promise<ImageBitmap> {
  const id = decodeId;
  decodeId += 1;
  return new Promise((resolve, reject) => {
    decodeJobs.set(id, { resolve, reject });
    decodeQueue.push({ id, buffer, type });
    drainDecoders();
  });
}

const bitmaps = new Map<string, ImageBitmap>();
const missing = new Set<string>();
const inflight = new Map<string, Promise<ImageBitmap | null>>();

function colorFor(agentType: string): string {
  return INK[agentType] ?? "#b7b1a6";
}

function rememberBitmap(key: string, bitmap: ImageBitmap): ImageBitmap {
  const existing = bitmaps.get(key);
  if (existing) {
    bitmaps.delete(key);
    bitmaps.set(key, existing);
    if (existing !== bitmap) {
      bitmap.close();
    }
    return existing;
  }
  bitmaps.set(key, bitmap);
  while (bitmaps.size > 40) {
    const oldest = bitmaps.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    const dropped = bitmaps.get(oldest);
    bitmaps.delete(oldest);
    dropped?.close();
  }
  return bitmap;
}

function clearBlobs() {
  for (const bitmap of bitmaps.values()) {
    bitmap.close();
  }
  bitmaps.clear();
  missing.clear();
  inflight.clear();
}

async function loadImage(
  sceneId: string,
  timeUs: number,
  agentId: string,
  sensorId: string,
  token: string,
): Promise<ImageBitmap | null> {
  const key = `${sceneId}|${timeUs}|${agentId}|${sensorId}`;
  if (missing.has(key)) {
    return null;
  }
  const cached = bitmaps.get(key);
  if (cached) {
    bitmaps.delete(key);
    bitmaps.set(key, cached);
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
): Promise<ImageBitmap | null> {
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
  const type = response.headers.get("content-type") ?? "image/png";
  const buffer = await response.arrayBuffer();
  return rememberBitmap(key, await decodeBitmap(buffer, type));
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
  image: ImageBitmap,
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
  const width = canvas.clientWidth || 1;
  const height = canvas.clientHeight || 1;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const bufferWidth = Math.max(1, Math.round(width * ratio));
  const bufferHeight = Math.max(1, Math.round(height * ratio));
  if (canvas.width !== bufferWidth || canvas.height !== bufferHeight) {
    canvas.width = bufferWidth;
    canvas.height = bufferHeight;
  }
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const naturalWidth = image.width;
  const naturalHeight = image.height;
  if (naturalWidth === 0 || naturalHeight === 0) {
    return;
  }
  const pixelX = camera.imageWidth && camera.imageWidth > 0 ? naturalWidth / camera.imageWidth : 1;
  const pixelY = camera.imageHeight && camera.imageHeight > 0 ? naturalHeight / camera.imageHeight : 1;
  const fit = Math.min(width / naturalWidth, height / naturalHeight);
  const destWidth = naturalWidth * fit;
  const destHeight = naturalHeight * fit;
  const originX = (width - destWidth) / 2;
  const originY = (height - destHeight) / 2;
  ctx.drawImage(image, originX, originY, destWidth, destHeight);
  const scale = fit;
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
  bitmap,
  boxes,
  clouds,
  types,
  hidden,
  showBoxes,
  showLidar,
}: {
  camera: FrameFrustum;
  bitmap: ImageBitmap | null | undefined;
  boxes: FrameBox[];
  clouds: Cloud[];
  types: Map<string, string>;
  hidden: Record<string, boolean>;
  showBoxes: boolean;
  showLidar: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const model = pinhole(camera);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !model || !bitmap) {
      return;
    }
    let paintFrame = 0;
    paintFrame = window.requestAnimationFrame(() => {
      paintFrame = 0;
      drawOverlay(canvas, bitmap, model, boxes, clouds, types, hidden, showBoxes, showLidar);
    });
    return () => {
      if (paintFrame !== 0) {
        window.cancelAnimationFrame(paintFrame);
      }
    };
  }, [camera, boxes, clouds, types, hidden, showBoxes, showLidar, bitmap, model]);

  const label = `${camera.sensor_id} · ${camera.agent_id}`;
  return (
    <figure className="camera-panel">
      <figcaption>{label}</figcaption>
      {bitmap ? (
        <div className="camera-frame">
          <canvas ref={canvasRef} role="img" aria-label={`${label} at this sample`} />
        </div>
      ) : (
        <p className="camera-missing">{bitmap === null ? "No image file for this sample." : "Loading image…"}</p>
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
    urls: Record<string, ImageBitmap | null>;
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
      const next: Record<string, ImageBitmap | null> = {};
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
    if (!showLidar) {
      return;
    }
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
  }, [cacheRef, sceneId, timeUs, token, pointSensors, showLidar]);

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
              bitmap={shown ? shown.urls[key] : undefined}
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
