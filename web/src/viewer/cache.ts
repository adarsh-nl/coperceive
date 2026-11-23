// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's frame cache: frame documents (boxes, poses, frustums) and point
 * sweeps, shared by the 3D view, the camera strip, the split view and the
 * inspector. Two bounds keep the desktop or browser tab from growing without
 * limit: the user's client memory limit (Settings → Viewer, default 512 MB,
 * 256/512/1024 MB choices) and a generous entry-count backstop.
 *
 * Every insert is byte-accounted and the cache evicts through
 * [`chooseEvictions`](../viewer/streamBuffer.ts): entries behind the playhead
 * first (farthest behind first), then the farthest ahead. The sample on the
 * playhead is kept unless it is the only thing left.
 *
 * Requests are deduplicated by key and counted: a caller that joins an
 * in-flight request as `needed` prevents a prefetcher's abort signal from
 * cancelling it. That is what makes a seek safe: the playback loop's request
 * for a sample survives even while the scheduler cancels the prefetch it no
 * longer wants. A rejected request key (a 404, a broken link) is not retried
 * until the cache is cleared, so a scene change (or a reload) starts clean.
 */
import { apiBase, authHeaders, errorDetail } from "../api/client";
import { chooseEvictions, type CacheEntry } from "./streamBuffer";
import { isFrame, type FrameDocument } from "./types";

/** The default client memory limit: 512 MB, the middle choice in Settings → Viewer. */
export const DEFAULT_CACHE_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024;

/** A backstop for pathological tiny samples; the byte cap is the real bound. */
const MAX_FRAME_ENTRIES = 4096;
const MAX_POINT_ENTRIES = 8192;

export type FrameCacheStats = {
  frame_hits: number;
  frame_misses: number;
  point_hits: number;
  point_misses: number;
  /** Bytes held by cached frame documents (the JSON documents). */
  frame_bytes: number;
  /** Bytes held by cached point-sweep buffers. */
  point_bytes: number;
  /** `frame_bytes + point_bytes`. */
  total_bytes: number;
  /** Requests aborted because the scheduler/seeker no longer wanted them. */
  cancelled: number;
  /** Entries dropped to stay under the memory limit. */
  evicted: number;
  /** Cumulative bytes of fetched entries, for the rolling MB/s in the stats line. */
  loaded_bytes: number;
};

export type LoadOptions = {
  /**
   * Default true: this caller displays the result. A cancellable prefetch passes
   * false, so the scheduler may abort it; a displaying caller joining the same
   * request keeps it alive.
   */
  needed?: boolean;
  /** Aborting detaches this caller; the request dies only when nobody needs it. */
  signal?: AbortSignal;
};

export type FrameCache = {
  loadFrame: (
    sceneId: string,
    timeUs: number,
    token: string,
    options?: LoadOptions,
  ) => Promise<FrameDocument>;
  loadPoints: (
    sceneId: string,
    timeUs: number,
    agentId: string,
    sensorId: string,
    token: string,
    options?: LoadOptions,
  ) => Promise<ArrayBuffer>;
  /** Scene change: abort everything and forget every entry. */
  clear: () => void;
  /** The sample the viewer is on; eviction keeps it and its neighbours. */
  setPlayhead: (timeUs: number) => void;
  /** The client memory cap in bytes (state.json `client_memory_limit_bytes`). */
  setMemoryLimit: (bytes: number) => void;
  /** A rough footprint of one sample, for the scheduler's memory admission. */
  estimateFrameBytes: () => number;
  /** In-flight prefetch requests not in `keepCacheKeys`, aborted; returns how many. */
  cancelOutdated: (keepCacheKeys: ReadonlySet<string>) => number;
  stats: FrameCacheStats;
  /** Bytes cached now (same as `stats.total_bytes`). */
  bytes: () => number;
};

type FrameEntry = {
  body: FrameDocument;
  timeUs: number;
  bytes: number;
};

type PointEntry = {
  buffer: ArrayBuffer;
  timeUs: number;
  bytes: number;
};

type Inflight = {
  key: string;
  kind: "frame" | "points";
  timeUs: number;
  controller: AbortController;
  /** Displaying callers: a prefetcher's abort must not kill their request. */
  needed: number;
  /** Callers still attached (displaying or prefetching). */
  waiters: number;
  aborted: boolean;
  promise: Promise<unknown>;
};

/** A load that was cancelled; callers stay quiet instead of reporting it as a failure. */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function abortError(message: string): DOMException {
  return new DOMException(message, "AbortError");
}

/** UTF-16 code units approximate the JSON's bytes for the ASCII frame documents. */
function documentBytes(body: unknown): number {
  try {
    return JSON.stringify(body)?.length ?? 0;
  } catch {
    return 0;
  }
}

export function emptyCacheStats(): FrameCacheStats {
  return {
    frame_hits: 0,
    frame_misses: 0,
    point_hits: 0,
    point_misses: 0,
    frame_bytes: 0,
    point_bytes: 0,
    total_bytes: 0,
    cancelled: 0,
    evicted: 0,
    loaded_bytes: 0,
  };
}

export function createFrameCache(limitBytes = DEFAULT_CACHE_MEMORY_LIMIT_BYTES): FrameCache {
  const frames = new Map<string, FrameEntry>();
  const points = new Map<string, PointEntry>();
  const inflight = new Map<string, Inflight>();
  let frameBytes = 0;
  let pointBytes = 0;
  let limit = limitBytes;
  let playheadUs = 0;
  let generation = 0;
  const stats = emptyCacheStats();

  const refreshBytes = () => {
    stats.frame_bytes = frameBytes;
    stats.point_bytes = pointBytes;
    stats.total_bytes = frameBytes + pointBytes;
  };

  const entriesOf = (): CacheEntry[] => {
    const entries: CacheEntry[] = [];
    for (const [key, entry] of frames) {
      entries.push({ key, timeUs: entry.timeUs, bytes: entry.bytes });
    }
    for (const [key, entry] of points) {
      entries.push({ key, timeUs: entry.timeUs, bytes: entry.bytes });
    }
    return entries;
  };

  /**
   * Evict through the shared policy until the cache fits the memory limit. Runs
   * on every insert and when the limit shrinks settings-side.
   */
  const enforceLimit = () => {
    let total = frameBytes + pointBytes;
    if (total <= limit) {
      return;
    }
    const doomed = chooseEvictions(entriesOf(), limit, playheadUs);
    for (const key of doomed) {
      const frame = frames.get(key);
      if (frame) {
        frames.delete(key);
        frameBytes -= frame.bytes;
        stats.evicted += 1;
        total -= frame.bytes;
        continue;
      }
      const point = points.get(key);
      if (point) {
        points.delete(key);
        pointBytes -= point.bytes;
        stats.evicted += 1;
        total -= point.bytes;
      }
    }
    refreshBytes();
  };

  const rememberFrame = (key: string, timeUs: number, body: FrameDocument) => {
    if (frames.has(key)) {
      return;
    }
    const entry: FrameEntry = { body, timeUs, bytes: documentBytes(body) };
    frames.set(key, entry);
    frameBytes += entry.bytes;
    stats.loaded_bytes += entry.bytes;
    while (frames.size > MAX_FRAME_ENTRIES) {
      const oldest = frames.keys().next().value;
      if (oldest === undefined) {
        break;
      }
      const dropped = frames.get(oldest);
      frames.delete(oldest);
      frameBytes -= dropped?.bytes ?? 0;
      stats.evicted += 1;
    }
    refreshBytes();
    enforceLimit();
  };

  const rememberPoints = (key: string, timeUs: number, buffer: ArrayBuffer) => {
    if (points.has(key)) {
      return;
    }
    const entry: PointEntry = { buffer, timeUs, bytes: buffer.byteLength };
    points.set(key, entry);
    pointBytes += entry.bytes;
    stats.loaded_bytes += entry.bytes;
    while (points.size > MAX_POINT_ENTRIES) {
      const oldest = points.keys().next().value;
      if (oldest === undefined) {
        break;
      }
      const dropped = points.get(oldest);
      points.delete(oldest);
      pointBytes -= dropped?.bytes ?? 0;
      stats.evicted += 1;
    }
    refreshBytes();
    enforceLimit();
  };

  /** Cancel one request: out of the map first, so a new caller starts a fresh one. */
  const cancelInflight = (entry: Inflight) => {
    if (entry.aborted) {
      return;
    }
    entry.aborted = true;
    if (inflight.get(entry.key) === entry) {
      inflight.delete(entry.key);
    }
    stats.cancelled += 1;
    entry.controller.abort();
  };

  /**
   * Attach one caller to an in-flight request. The caller's `signal` detaches
   * it; the request is aborted only when no displaying caller remains.
   */
  const attach = <T>(entry: Inflight, options?: LoadOptions): Promise<T> => {
    // Displaying callers are the default: only an explicit `needed: false`
    // marks a request as cancellable prefetch.
    const needed = options?.needed === false ? 0 : 1;
    entry.waiters += 1;
    entry.needed += needed;
    const signal = options?.signal;
    if (signal) {
      const detach = () => {
        entry.waiters = Math.max(0, entry.waiters - 1);
        entry.needed = Math.max(0, entry.needed - needed);
        if (entry.waiters === 0 && entry.needed === 0) {
          cancelInflight(entry);
        }
      };
      if (signal.aborted) {
        detach();
      } else {
        signal.addEventListener("abort", detach, { once: true });
      }
    }
    return entry.promise as Promise<T>;
  };

  const loadFrame = (
    sceneId: string,
    timeUs: number,
    token: string,
    options?: LoadOptions,
  ): Promise<FrameDocument> => {
    const key = `${sceneId}|${timeUs}`;
    const cached = frames.get(key);
    if (cached) {
      stats.frame_hits += 1;
      frames.delete(key);
      frames.set(key, cached);
      return Promise.resolve(cached.body);
    }
    const pending = inflight.get(key);
    if (pending) {
      stats.frame_hits += 1;
      return attach<FrameDocument>(pending, options);
    }
    stats.frame_misses += 1;
    const mine = generation;
    const entry: Inflight = {
      key,
      kind: "frame",
      timeUs,
      controller: new AbortController(),
      needed: 0,
      waiters: 0,
      aborted: false,
      promise: Promise.resolve(),
    };
    entry.promise = (async () => {
      try {
        const response = await fetch(
          `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/frame?time_us=${timeUs}`,
          { headers: authHeaders(token), signal: entry.controller.signal },
        );
        if (!response.ok) {
          throw new Error(await errorDetail(response));
        }
        const body = (await response.json()) as unknown;
        if (!isFrame(body)) {
          throw new Error("The frame document was incomplete.");
        }
        if (mine !== generation || entry.controller.signal.aborted) {
          throw abortError("The scene changed while the frame was loading.");
        }
        rememberFrame(key, timeUs, body);
        return body;
      } finally {
        if (inflight.get(key) === entry) {
          inflight.delete(key);
        }
        entry.waiters = 0;
        entry.needed = 0;
      }
    })();
    inflight.set(key, entry);
    return attach<FrameDocument>(entry, options);
  };

  const loadPoints = (
    sceneId: string,
    timeUs: number,
    agentId: string,
    sensorId: string,
    token: string,
    options?: LoadOptions,
  ): Promise<ArrayBuffer> => {
    const key = `${sceneId}|${timeUs}|${agentId}|${sensorId}`;
    const cached = points.get(key);
    if (cached) {
      stats.point_hits += 1;
      points.delete(key);
      points.set(key, cached);
      return Promise.resolve(cached.buffer);
    }
    const pending = inflight.get(key);
    if (pending) {
      stats.point_hits += 1;
      return attach<ArrayBuffer>(pending, options);
    }
    stats.point_misses += 1;
    const mine = generation;
    const entry: Inflight = {
      key,
      kind: "points",
      timeUs,
      controller: new AbortController(),
      needed: 0,
      waiters: 0,
      aborted: false,
      promise: Promise.resolve(),
    };
    entry.promise = (async () => {
      try {
        const response = await fetch(
          `${apiBase()}/api/scenes/${encodeURIComponent(sceneId)}/points?time_us=${timeUs}&agent_id=${encodeURIComponent(agentId)}&sensor_id=${encodeURIComponent(sensorId)}`,
          { headers: authHeaders(token), signal: entry.controller.signal },
        );
        if (!response.ok) {
          throw new Error(await errorDetail(response));
        }
        const buffer = await response.arrayBuffer();
        if (mine !== generation || entry.controller.signal.aborted) {
          throw abortError("The scene changed while the points were loading.");
        }
        rememberPoints(key, timeUs, buffer);
        return buffer;
      } finally {
        if (inflight.get(key) === entry) {
          inflight.delete(key);
        }
        entry.waiters = 0;
        entry.needed = 0;
      }
    })();
    inflight.set(key, entry);
    return attach<ArrayBuffer>(entry, options);
  };

  return {
    stats,
    bytes: () => frameBytes + pointBytes,
    loadFrame,
    loadPoints,
    clear() {
      generation += 1;
      for (const entry of inflight.values()) {
        cancelInflight(entry);
      }
      inflight.clear();
      frames.clear();
      points.clear();
      frameBytes = 0;
      pointBytes = 0;
      refreshBytes();
    },
    setPlayhead(timeUs) {
      if (Number.isFinite(timeUs)) {
        playheadUs = timeUs;
      }
    },
    setMemoryLimit(bytes) {
      if (Number.isFinite(bytes) && bytes > 0) {
        limit = Math.floor(bytes);
      }
      enforceLimit();
    },
    estimateFrameBytes() {
      const frameEntries = frames.size;
      if (frameEntries === 0) {
        return 2 * 1024 * 1024;
      }
      return Math.max(1, Math.round((frameBytes + pointBytes) / frameEntries));
    },
    cancelOutdated(keepCacheKeys) {
      let cancelled = 0;
      for (const entry of [...inflight.values()]) {
        if (entry.needed > 0 || keepCacheKeys.has(entry.key)) {
          continue;
        }
        cancelInflight(entry);
        cancelled += 1;
      }
      return cancelled;
    },
  };
}
