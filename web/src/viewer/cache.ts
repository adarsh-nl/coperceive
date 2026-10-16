import { apiBase, authHeaders, errorDetail } from "../api/client";
import { isFrame, type FrameDocument } from "./types";

const FRAME_LIMIT = 16;
const POINT_LIMIT = 48;

export type FrameCache = {
  loadFrame: (sceneId: string, timeUs: number, token: string) => Promise<FrameDocument>;
  loadPoints: (
    sceneId: string,
    timeUs: number,
    agentId: string,
    sensorId: string,
    token: string,
  ) => Promise<ArrayBuffer>;
  clear: () => void;
};

function remember<T>(map: Map<string, T>, key: string, value: T, limit: number) {
  if (map.has(key)) {
    map.delete(key);
  }
  map.set(key, value);
  while (map.size > limit) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    map.delete(oldest);
  }
}

export function createFrameCache(): FrameCache {
  const frames = new Map<string, FrameDocument>();
  const points = new Map<string, ArrayBuffer>();
  const frameInflight = new Map<string, Promise<FrameDocument>>();
  const pointInflight = new Map<string, Promise<ArrayBuffer>>();

  return {
    clear() {
      frames.clear();
      points.clear();
    },
    loadFrame(sceneId, timeUs, token) {
      const key = `${sceneId}|${timeUs}`;
      const cached = frames.get(key);
      if (cached) {
        remember(frames, key, cached, FRAME_LIMIT);
        return Promise.resolve(cached);
      }
      const pending = frameInflight.get(key);
      if (pending) {
        return pending;
      }
      const promise = (async () => {
        const response = await fetch(
          `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/frame?time_us=${timeUs}`,
          { headers: authHeaders(token) },
        );
        if (!response.ok) {
          throw new Error(await errorDetail(response));
        }
        const body = (await response.json()) as unknown;
        if (!isFrame(body)) {
          throw new Error("The frame document was incomplete.");
        }
        remember(frames, key, body, FRAME_LIMIT);
        return body;
      })().finally(() => {
        frameInflight.delete(key);
      });
      frameInflight.set(key, promise);
      return promise;
    },
    loadPoints(sceneId, timeUs, agentId, sensorId, token) {
      const key = `${sceneId}|${timeUs}|${agentId}|${sensorId}`;
      const cached = points.get(key);
      if (cached) {
        remember(points, key, cached, POINT_LIMIT);
        return Promise.resolve(cached);
      }
      const pending = pointInflight.get(key);
      if (pending) {
        return pending;
      }
      const promise = (async () => {
        const response = await fetch(
          `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/points?time_us=${timeUs}&agent_id=${encodeURIComponent(agentId)}&sensor_id=${encodeURIComponent(sensorId)}`,
          { headers: authHeaders(token) },
        );
        if (!response.ok) {
          throw new Error(await errorDetail(response));
        }
        const buffer = await response.arrayBuffer();
        remember(points, key, buffer, POINT_LIMIT);
        return buffer;
      })().finally(() => {
        pointInflight.delete(key);
      });
      pointInflight.set(key, promise);
      return promise;
    },
  };
}
