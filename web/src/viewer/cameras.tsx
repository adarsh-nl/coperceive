import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { apiBase, authHeaders, errorDetail } from "../api/client";
import type { FrameCache } from "./cache";
import { IconImage } from "./controls";
import { cameraKey, type FrameBox, type FrameFrustum, type FrameSensor } from "./types";
import { projectToImage, type Pinhole } from "./project";

type Cloud = { agentId: string; xyz: Float32Array };

const INK: Record<string, string> = {
  vehicle: "#F58A1F",
  uav: "#3C8DFF",
};

const STRIP_WIDTH = 640;
const DECODERS = 3;

const workerSource = `
self.onmessage = async (event) => {
  const data = event.data;
  try {
    const blob = new Blob([data.buffer], { type: data.type || "image/png" });
    const width = data.width > 0 ? data.width : ${STRIP_WIDTH};
    let bitmap;
    try {
      bitmap = await createImageBitmap(blob, { resizeWidth: width, resizeQuality: "low" });
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
const decodeQueue: { id: number; buffer: ArrayBuffer; type: string; width: number }[] = [];

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

function decodeBitmap(buffer: ArrayBuffer, type: string, width: number): Promise<ImageBitmap> {
  const id = decodeId;
  decodeId += 1;
  return new Promise((resolve, reject) => {
    decodeJobs.set(id, { resolve, reject });
    decodeQueue.push({ id, buffer, type, width });
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

export async function loadImage(
  sceneId: string,
  timeUs: number,
  agentId: string,
  sensorId: string,
  token: string,
  width = STRIP_WIDTH,
): Promise<ImageBitmap | null> {
  const key = `${sceneId}|${timeUs}|${agentId}|${sensorId}|${width}`;
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
  const request = fetchImage(key, sceneId, timeUs, agentId, sensorId, token, width);
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
  width: number,
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
  return rememberBitmap(key, await decodeBitmap(buffer, type, width));
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
  const fit = Math.max(width / naturalWidth, height / naturalHeight);
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
  focusKey,
  imageOn,
  onToggleImage,
  onSelect,
  hud,
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
  focusKey: string;
  imageOn: (key: string) => boolean;
  onToggleImage: (key: string) => void;
  onSelect: (key: string) => void;
  hud: { speed: string; heading: number; stamp: string } | null;
}) {
  const [showBoxes, setShowBoxes] = useState(true);
  const [showLidar, setShowLidar] = useState(true);
  const [shown, setShown] = useState<{
    timeUs: number;
    urls: Record<string, ImageBitmap | null>;
    frustums: FrameFrustum[];
    boxes: FrameBox[];
  } | null>(null);
  const [clouds, setClouds] = useState<Cloud[]>([]);
  const [cloudTime, setCloudTime] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);
  const [width, setWidth] = useState(420);
  const dragRef = useRef<{ dx: number; dy: number; hostW: number; hostH: number; width: number; height: number } | null>(null);
  const resizeRef = useRef<{ x: number; width: number } | null>(null);
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

  const panels = shown?.frustums ?? frustums;
  const focus = panels.find((camera) => cameraKey(camera.agent_id, camera.sensor_id) === focusKey) ?? panels[0];
  const heading = hud?.heading ?? 0;

  function onDragStart(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }
    const shell = event.currentTarget.parentElement;
    const host = shell?.parentElement;
    if (!shell || !host) {
      return;
    }
    const box = shell.getBoundingClientRect();
    const hostBox = host.getBoundingClientRect();
    dragRef.current = {
      dx: event.clientX - box.left,
      dy: event.clientY - box.top,
      hostW: hostBox.width,
      hostH: hostBox.height,
      width: box.width,
      height: box.height,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onDragMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const host = event.currentTarget.parentElement?.parentElement;
    if (!drag || !host || !event.currentTarget.hasPointerCapture(event.pointerId)) {
      return;
    }
    const hostBox = host.getBoundingClientRect();
    const left = Math.min(Math.max(0, event.clientX - hostBox.left - drag.dx), Math.max(0, drag.hostW - drag.width));
    const top = Math.min(Math.max(0, event.clientY - hostBox.top - drag.dy), Math.max(0, drag.hostH - drag.height));
    setPlace({ left, top });
  }

  function onResizeStart(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) {
      return;
    }
    event.stopPropagation();
    resizeRef.current = { x: event.clientX, width };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onResizeMove(event: ReactPointerEvent<HTMLButtonElement>) {
    const resize = resizeRef.current;
    const shell = event.currentTarget.parentElement;
    const host = shell?.parentElement;
    if (!resize || !shell || !host || !event.currentTarget.hasPointerCapture(event.pointerId)) {
      return;
    }
    const hostBox = host.getBoundingClientRect();
    const next = Math.min(Math.max(280, resize.width + (event.clientX - resize.x)), Math.max(280, hostBox.width - 16));
    setWidth(next);
    setPlace((current) => {
      if (!current) {
        return current;
      }
      const box = shell.getBoundingClientRect();
      return {
        left: Math.min(current.left, Math.max(0, hostBox.width - next)),
        top: Math.min(current.top, Math.max(0, hostBox.height - box.height)),
      };
    });
  }

  return (
    <section
      className="pip"
      aria-label="Agent camera"
      style={{
        width,
        ...(place ? { left: place.left, top: place.top, right: "auto", bottom: "auto" } : {}),
      }}
    >
      <div
        className="pip-bar"
        onPointerDown={onDragStart}
        onPointerMove={onDragMove}
        onPointerUp={() => {
          dragRef.current = null;
        }}
      >
        <span className="grip" aria-hidden="true">
          <i /><i /><i /><i /><i /><i />
        </span>
        <strong>Camera</strong>
        <button
          type="button"
          className="pip-toggle"
          aria-pressed={showBoxes}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => setShowBoxes((current) => !current)}
        >
          Boxes
        </button>
        <button
          type="button"
          className="pip-toggle"
          aria-pressed={showLidar}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => setShowLidar((current) => !current)}
        >
          Lidar
        </button>
        <span className="pip-drag">Drag</span>
      </div>
      {focus ? (
        <div className="pip-stage">
          <CameraPanel
            camera={focus}
            bitmap={shown ? shown.urls[cameraKey(focus.agent_id, focus.sensor_id)] : undefined}
            boxes={shown?.boxes ?? []}
            clouds={shown && cloudTime === shown.timeUs ? clouds : []}
            types={types}
            hidden={hidden}
            showBoxes={showBoxes}
            showLidar={showLidar}
          />
          <div className="compass" aria-hidden="true">
            <div className="compass-tape" style={{ transform: `translateX(${(-heading / 360) * 304}px)` }}>
              {["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N", "NE", "E", "SE", "S", "SW", "W", "NW"].map((name, item) => (
                <span key={`${name}-${item}`}>{name}</span>
              ))}
            </div>
            <i />
          </div>
          <div className="hud">
            <p className="num">
              <span>{hud?.speed ?? "—"}</span>
              <span>{hud ? `${hud.heading.toFixed(0)}°` : "—"}</span>
              <span>{hud?.stamp ?? "—"}</span>
            </p>
          </div>
        </div>
      ) : null}
      <div className="pip-thumbs" role="group" aria-label="Cameras">
        {panels.map((camera) => {
          const key = cameraKey(camera.agent_id, camera.sensor_id);
          const shownImage = imageOn(key);
          return (
            <div key={key} className="pip-chip">
              <button
                type="button"
                className="pip-name"
                aria-pressed={focus ? key === cameraKey(focus.agent_id, focus.sensor_id) : false}
                onClick={() => onSelect(key)}
              >
                {camera.sensor_id}
                <span>{camera.agent_id}</span>
              </button>
              <button
                type="button"
                className="pip-image"
                aria-pressed={shownImage}
                aria-label={`${shownImage ? "Hide" : "Show"} ${camera.agent_id} ${camera.sensor_id} on its frustum`}
                onClick={() => onToggleImage(key)}
              >
                <IconImage />
              </button>
            </div>
          );
        })}
      </div>
      {note ? <p className="muted">{note}</p> : null}
      <button
        type="button"
        className="pip-resize"
        aria-label="Resize camera panel"
        onPointerDown={onResizeStart}
        onPointerMove={onResizeMove}
        onPointerUp={() => {
          resizeRef.current = null;
        }}
      />
    </section>
  );
}
