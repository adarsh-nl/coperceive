import { useEffect, useRef, useState, type RefObject } from "react";
import PanelState from "../desktop/PanelState";
import { PANEL_DEFINITIONS } from "../desktop/panelStates";
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
  bytesCache.clear();
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

const BYTES_LIMIT = 12;
const bytesCache = new Map<string, { buffer: ArrayBuffer; type: string }>();
const bytesInflight = new Map<string, Promise<{ buffer: ArrayBuffer; type: string } | null>>();

/** One download per file, shared by the thumbnail and the split pane (decoded per width). */
async function fetchBytes(url: string, token: string): Promise<{ buffer: ArrayBuffer; type: string } | null> {
  const cached = bytesCache.get(url);
  if (cached) {
    bytesCache.delete(url);
    bytesCache.set(url, cached);
    return cached;
  }
  const pending = bytesInflight.get(url);
  if (pending) {
    return pending;
  }
  const request = (async () => {
    const response = await fetch(url, { headers: authHeaders(token) });
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    const entry = { buffer: await response.arrayBuffer(), type: response.headers.get("content-type") ?? "image/png" };
    bytesCache.set(url, entry);
    while (bytesCache.size > BYTES_LIMIT) {
      const oldest = bytesCache.keys().next().value;
      if (oldest === undefined) {
        break;
      }
      bytesCache.delete(oldest);
    }
    return entry;
  })().finally(() => {
    bytesInflight.delete(url);
  });
  bytesInflight.set(url, request);
  return request;
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
  const url = `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/image?time_us=${timeUs}&agent_id=${encodeURIComponent(agentId)}&sensor_id=${encodeURIComponent(sensorId)}&width=${width}`;
  const entry = await fetchBytes(url, token);
  if (entry === null) {
    missing.add(key);
    return null;
  }
  // The decoder takes ownership of the buffer it is handed; keep the cached copy.
  return rememberBitmap(key, await decodeBitmap(entry.buffer.slice(0), entry.type, width));
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
    // Only the camera's own agent's sweep reaches here (CameraSplit loads no other), points
    // behind the camera (projectToImage) or closer than MIN_OVERLAY_DEPTH_M are dropped,
    // and each point is coloured by its depth; 1.6 px squares instead of 3.2 px.
    const radius = 0.8 / scale;
    for (const cloud of clouds) {
      if (hidden[cloud.agentId]) {
        continue;
      }
      for (let index = 0; index + 2 < cloud.xyz.length; index += 3) {
        const pixel = projectToImage(
          camera,
          cloud.xyz[index] ?? 0,
          cloud.xyz[index + 1] ?? 0,
          cloud.xyz[index + 2] ?? 0,
        );
        if (!pixel || pixel.depth < MIN_OVERLAY_DEPTH_M) {
          continue;
        }
        const u = pixel.u * pixelX;
        const v = pixel.v * pixelY;
        if (u < 0 || v < 0 || u >= naturalWidth || v >= naturalHeight) {
          continue;
        }
        ctx.fillStyle = depthColor(pixel.depth);
        ctx.fillRect(u - radius, v - radius, radius * 2, radius * 2);
      }
    }
  }
}

/** Points nearer than this are the sensor's own platform and the lens hood; not drawn. */
export const MIN_OVERLAY_DEPTH_M = 1;
/** Depth at and beyond which the overlay colour stops changing. */
export const MAX_OVERLAY_DEPTH_M = 60;

/** Warm near, cool far: hue 20° (orange) at 1 m to 220° (blue) at 60 m and beyond. */
export function depthColor(depth: number): string {
  const unit = Math.min(1, Math.max(0, (depth - MIN_OVERLAY_DEPTH_M) / (MAX_OVERLAY_DEPTH_M - MIN_OVERLAY_DEPTH_M)));
  return `hsl(${Math.round(20 + 200 * unit)} 85% 55%)`;
}

function CameraPanel({
  camera,
  agentLabel,
  bitmap,
  boxes,
  clouds,
  types,
  hidden,
  showBoxes,
  showLidar,
  missingText,
}: {
  camera: FrameFrustum;
  /** The agent's display name, or its raw id when the scene has none. */
  agentLabel: string;
  bitmap: ImageBitmap | null | undefined;
  /** Shown when there is no image for this sample: per-sample or per-scene wording. */
  missingText: string;
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

  const label = `${camera.sensor_id} · ${agentLabel}`;
  return (
    <figure className="camera-panel">
      <figcaption title={camera.agent_id}>{label}</figcaption>
      {bitmap ? (
        <div className="camera-frame">
          <canvas ref={canvasRef} role="img" aria-label={`${label} at this sample`} />
        </div>
      ) : (
        <p className="camera-missing">{bitmap === null ? missingText : "Loading image…"}</p>
      )}
    </figure>
  );
}

const THUMB_WIDTH = 160;
const SPLIT_WIDTH = 960;

export type CameraHud = { speed: string; heading: number; stamp: string };

function agentOrder(frustums: readonly FrameFrustum[]): { agentId: string; cameras: FrameFrustum[] }[] {
  const groups: { agentId: string; cameras: FrameFrustum[] }[] = [];
  for (const camera of frustums) {
    const group = groups.find((item) => item.agentId === camera.agent_id);
    if (group) {
      group.cameras.push(camera);
    } else {
      groups.push({ agentId: camera.agent_id, cameras: [camera] });
    }
  }
  return groups;
}

/** One camera's low-resolution picture; it asks for the file only while it is on screen. */
function Thumb({
  camera,
  agentLabel,
  sceneId,
  timeUs,
  nextTimeUs,
  token,
  imagesAvailable,
  open,
  onOpen,
  imageOn,
  onToggleImage,
  playing = false,
}: {
  camera: FrameFrustum;
  agentLabel: string;
  sceneId: string;
  timeUs: number;
  nextTimeUs: number | null;
  token: string;
  imagesAvailable: boolean;
  open: boolean;
  onOpen: () => void;
  imageOn: boolean;
  onToggleImage: () => void;
  playing?: boolean;
}) {
  const hostRef = useRef<HTMLButtonElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Without IntersectionObserver every thumbnail counts as visible.
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");
  const [bitmap, setBitmap] = useState<ImageBitmap | null | undefined>(undefined);
  const lastLoadRef = useRef(0);
  const key = cameraKey(camera.agent_id, camera.sensor_id);
  const picture = imagesAvailable ? bitmap : null;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        setVisible(entry.isIntersecting);
      }
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || !imagesAvailable) {
      return;
    }
    // During playback a 10 Hz full strip would ask for six images every tick and
    // queue the render thread behind decode/canvas work; refresh at most ~3 Hz.
    const now = performance.now();
    if (playing && now - lastLoadRef.current < 300) {
      return;
    }
    lastLoadRef.current = now;
    let cancelled = false;
    void (async () => {
      try {
        const next = await loadImage(sceneId, timeUs, camera.agent_id, camera.sensor_id, token, THUMB_WIDTH);
        if (!cancelled) {
          setBitmap(next);
        }
      } catch {
        if (!cancelled) {
          setBitmap(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [visible, imagesAvailable, sceneId, timeUs, nextTimeUs, token, camera.agent_id, camera.sensor_id, playing]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !picture) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    canvas.width = picture.width;
    canvas.height = picture.height;
    ctx.drawImage(picture, 0, 0);
  }, [picture]);

  return (
    <div className="cam-thumb-wrap">
      <button
        ref={hostRef}
        type="button"
        className="cam-thumb"
        aria-pressed={open}
        aria-label={`${open ? "Close" : "Open"} ${agentLabel} ${camera.sensor_id}`}
        title={`${agentLabel} ${camera.sensor_id} (${camera.agent_id})`}
        data-camera={key}
        onClick={onOpen}
      >
        {picture ? <canvas ref={canvasRef} /> : <span className="cam-thumb-empty">{picture === null ? "no image" : "…"}</span>}
        <span className="cam-thumb-name">{camera.sensor_id}</span>
      </button>
      <button
        type="button"
        className="cam-thumb-image"
        aria-pressed={imageOn}
        aria-label={`${imageOn ? "Hide" : "Show"} ${camera.agent_id} ${camera.sensor_id} on its frustum`}
        onClick={onToggleImage}
      >
        <IconImage />
      </button>
    </div>
  );
}

/** The strip docked under the 3D view: one thumbnail per camera, grouped by agent. */
export default function CameraStrip({
  token,
  sceneId,
  timeUs,
  nextTimeUs,
  frustums,
  types,
  names,
  open,
  onToggleOpen,
  splitKey,
  onSplit,
  imageOn,
  onToggleImage,
  imagesAvailable = true,
  missingText = "No image file for this sample.",
  playing = false,
  heightPx,
}: {
  token: string;
  sceneId: string;
  timeUs: number;
  nextTimeUs: number | null;
  frustums: FrameFrustum[];
  types: Map<string, string>;
  /** `agent_id -> display name`; an agent missing here is labelled by its id. */
  names: Map<string, string>;
  /** Collapsed strips render no thumbnails and request nothing. */
  open: boolean;
  onToggleOpen: () => void;
  /** The camera shown in the split view, or null. */
  splitKey: string | null;
  onSplit: (key: string) => void;
  imageOn: (key: string) => boolean;
  onToggleImage: (key: string) => void;
  /** False when the scene summary says no image file is staged: nothing is requested. */
  imagesAvailable?: boolean;
  missingText?: string;
  /** While playing, refresh thumbnails at most a few times per second. */
  playing?: boolean;
  /** Optional custom open height in pixels when resized. */
  heightPx?: number;
}) {
  const sceneRef = useRef(sceneId);
  useEffect(() => {
    if (sceneRef.current === sceneId) {
      return;
    }
    sceneRef.current = sceneId;
    clearBlobs();
  }, [sceneId]);

  if (frustums.length === 0) {
    return null;
  }
  const groups = agentOrder(frustums);
  return (
    <section
      className="cam-strip"
      data-open={open ? "true" : "false"}
      aria-label="Cameras"
      style={{ height: open && typeof heightPx === "number" ? `${heightPx}px` : undefined }}
    >
      <button
        type="button"
        className="cam-strip-toggle"
        aria-expanded={open}
        aria-keyshortcuts="C"
        aria-label={open ? "Collapse camera strip" : "Expand camera strip"}
        onClick={onToggleOpen}
        title="Cameras (C)"
      >
        <span>Cameras</span>
        <span className="num">{frustums.length}</span>
        <kbd>C</kbd>
      </button>
      {open ? (
        <div className="cam-groups">
          {groups.map((group) => (
            <div
              key={group.agentId}
              className="cam-group"
              style={{ ["--agent" as string]: colorFor(types.get(group.agentId) ?? "") }}
            >
              <span className="cam-group-name" title={group.agentId}>
                {names.get(group.agentId) ?? group.agentId}
              </span>
              <div className="cam-thumbs">
                {group.cameras.map((camera) => {
                  const key = cameraKey(camera.agent_id, camera.sensor_id);
                  return (
                    <Thumb
                      key={key}
                      camera={camera}
                      agentLabel={names.get(camera.agent_id) ?? camera.agent_id}
                      sceneId={sceneId}
                      timeUs={timeUs}
                      nextTimeUs={nextTimeUs}
                      token={token}
                      imagesAvailable={imagesAvailable}
                      playing={playing}
                      open={splitKey === key}
                      onOpen={() => onSplit(key)}
                      imageOn={imageOn(key)}
                      onToggleImage={() => onToggleImage(key)}
                    />
                  );
                })}
              </div>
            </div>
          ))}
          {frustums.length === 0 ? (
            <PanelState
              compact
              kind="empty"
              title={PANEL_DEFINITIONS.camera_strip.empty.title}
              description={PANEL_DEFINITIONS.camera_strip.empty.description}
              actionLabel={PANEL_DEFINITIONS.camera_strip.empty.actionLabel}
              onAction={onToggleOpen}
            />
          ) : null}
          {!imagesAvailable ? (
            <>
              <p className="cam-strip-note muted">{missingText}</p>
              <PanelState
                compact
                kind="error"
                title={PANEL_DEFINITIONS.camera_strip.error.title}
                description={PANEL_DEFINITIONS.camera_strip.error.description}
                actionLabel={PANEL_DEFINITIONS.camera_strip.error.actionLabel}
                onAction={onToggleOpen}
              />
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** The right half of the split view: the chosen camera with boxes and lidar drawn on it. */
export function CameraSplit({
  token,
  sceneId,
  timeUs,
  nextTimeUs,
  camera,
  boxes,
  pointSensors,
  types,
  names,
  hidden,
  cacheRef,
  hud,
  onClose,
  imagesAvailable = true,
  missingText = "No image file for this sample.",
}: {
  token: string;
  sceneId: string;
  timeUs: number;
  nextTimeUs: number | null;
  camera: FrameFrustum;
  boxes: FrameBox[];
  pointSensors: FrameSensor[];
  types: Map<string, string>;
  names: Map<string, string>;
  hidden: Record<string, boolean>;
  cacheRef: RefObject<FrameCache>;
  hud: CameraHud | null;
  onClose: () => void;
  imagesAvailable?: boolean;
  missingText?: string;
}) {
  const [showBoxes, setShowBoxes] = useState(true);
  const [showLidar, setShowLidar] = useState(true);
  const [shown, setShown] = useState<{ timeUs: number; bitmap: ImageBitmap | null; boxes: FrameBox[] } | null>(null);
  const [clouds, setClouds] = useState<Cloud[]>([]);
  const [cloudTime, setCloudTime] = useState<number | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    const sampleBoxes = boxes;
    void (async () => {
      if (!imagesAvailable) {
        if (!cancelled) {
          setShown({ timeUs, bitmap: null, boxes: sampleBoxes });
        }
        return;
      }
      let bitmap: ImageBitmap | null;
      try {
        bitmap = await loadImage(sceneId, timeUs, camera.agent_id, camera.sensor_id, token, SPLIT_WIDTH);
      } catch (error) {
        if (!cancelled) {
          setNote(error instanceof Error ? error.message : "Could not load the camera image.");
        }
        return;
      }
      if (cancelled) {
        return;
      }
      setShown({ timeUs, bitmap, boxes: sampleBoxes });
      setNote("");
    })();
    return () => {
      cancelled = true;
    };
  }, [imagesAvailable, sceneId, timeUs, nextTimeUs, token, camera.agent_id, camera.sensor_id, boxes]);

  useEffect(() => {
    if (!showLidar) {
      return;
    }
    let cancelled = false;
    void (async () => {
      const loaded: Cloud[] = [];
      for (const sensor of pointSensors) {
        // The camera's own agent only: another agent's sweep, seen from here, is a wall of
        // points below the horizon (OPV2V camera0 in job 629223: 83% of the drawn points).
        if (sensor.count === 0 || sensor.agent_id !== camera.agent_id) {
          continue;
        }
        const buffer = await cacheRef.current.loadPoints(sceneId, timeUs, sensor.agent_id, sensor.sensor_id, token);
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

  const heading = hud?.heading ?? 0;
  return (
    <section className="camera-split" aria-label={`Camera ${names.get(camera.agent_id) ?? camera.agent_id} ${camera.sensor_id}`}>
      <div className="camera-split-bar">
        <strong>
          {camera.sensor_id} <span title={camera.agent_id}>{names.get(camera.agent_id) ?? camera.agent_id}</span>
        </strong>
        <button type="button" className="pip-toggle" aria-pressed={showBoxes} onClick={() => setShowBoxes((current) => !current)}>
          Boxes
        </button>
        <button type="button" className="pip-toggle" aria-pressed={showLidar} onClick={() => setShowLidar((current) => !current)}>
          Lidar
        </button>
        <button type="button" className="camera-split-close" aria-label="Close camera (Esc)" aria-keyshortcuts="Escape" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="camera-split-stage">
        <CameraPanel
          camera={camera}
          agentLabel={names.get(camera.agent_id) ?? camera.agent_id}
          bitmap={shown ? shown.bitmap : undefined}
          boxes={shown?.boxes ?? []}
          clouds={shown && cloudTime === shown.timeUs ? clouds : []}
          types={types}
          hidden={hidden}
          showBoxes={showBoxes}
          showLidar={showLidar}
          missingText={missingText}
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
      {note ? <p className="muted">{note}</p> : null}
    </section>
  );
}
