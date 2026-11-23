// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's time-based stream buffer, replacing the fixed four-frame horizon
 * (`PREFETCH_FRAMES`). It is the scheduler's brain and holds no DOM or network
 * code: given the scene's sample times, the current sample index, what is
 * already loaded and the user's settings, it answers three questions.
 *
 * - What should be fetched next, in priority order? The current sample first
 *   (its point clouds, then its frame document and camera images), then the
 *   point clouds of the samples ahead in order, then the rest of the prefetch
 *   (the frame documents that tell us which sweeps those samples have).
 * - How much playback time is buffered ahead? A sample counts as buffered once
 *   its frame document and every point sweep it lists are in the cache.
 * - Is the buffer about to run dry? Stall when less than [`STALL_BUFFER_US`] of
 *   playback is buffered ahead, stay stalled until [`RESUME_BUFFER_US`] is; the
 *   scene's own time gaps are used, so a 5 Hz scene just works.
 *
 * [`createStreamBuffer`] turns those answers into requests with a per-request
 * abort controller and a generation token: a seek or scene change cancels the
 * requests for samples the new plan no longer wants (not only the old scene's),
 * and a reply for an outdated generation never enters the loaded set.
 *
 * The cache's byte cap is enforced in [`chooseEvictions`], which the cache calls
 * on every insert: entries behind the playhead go first, farthest behind first,
 * then entries farthest ahead. The sample at the playhead is never evicted
 * unless it is the only thing left.
 *
 * Settings (Settings → Viewer) are the buffer target in seconds (1, 3 or 5,
 * default 3) and the client memory limit in bytes (256, 512 or 1024 MB, default
 * 512 MB); both live in state.json next to the other preferences.
 */

/** The buffer is about to run dry: less than this much playback is buffered ahead. */
export const STALL_BUFFER_US = 250_000;
/** A stalled buffer resumes once at least this much playback is buffered ahead. */
export const RESUME_BUFFER_US = 1_000_000;
/** A scene gap that is missing or non-positive uses the playback module's 100 ms fallback. */
export const FALLBACK_GAP_US = 100_000;

/** A camera referenced by a frame document (agent id plus sensor id). */
export type CameraRef = {
  agentId: string;
  sensorId: string;
  /** No image was recorded for this camera at this sample: nothing to fetch. */
  hasImage: boolean;
};

/** A point sweep the frame document lists: the expensive part of a sample. */
export type PointSensor = {
  agentId: string;
  sensorId: string;
  count: number;
};

/** What a frame document tells the buffer about one sample. */
export type FrameInfo = {
  timeUs: number;
  pointSensors: readonly PointSensor[];
  cameras: readonly CameraRef[];
};

/** What the buffer knows about one sample: the document, and how much of it arrived. */
export type FrameState = {
  /** The frame document (boxes, poses, the sweeps and cameras to fetch); null until it arrives. */
  doc: FrameInfo | null;
  /** Point sweeps loaded so far. */
  points: number;
  /** Camera images loaded so far (only filled when the viewer wants them prefetched). */
  images: number;
};

export type FrameStates = ReadonlyMap<number, FrameState>;

export function emptyFrameState(): FrameState {
  return { doc: null, points: 0, images: 0 };
}

/** Every point sweep this sample lists has arrived: the sample can be drawn. */
export function frameReady(state: FrameState | undefined): boolean {
  if (!state?.doc) {
    return false;
  }
  return state.points >= state.doc.pointSensors.length;
}

/** The point sweeps of this sample that are still missing. */
export function missingPoints(state: FrameState | undefined): readonly PointSensor[] {
  if (!state?.doc) {
    return [];
  }
  return state.doc.pointSensors.slice(Math.max(0, state.points));
}

/** The camera images of this sample that are still missing and were recorded. */
export function missingImages(state: FrameState | undefined): readonly CameraRef[] {
  if (!state?.doc) {
    return [];
  }
  return state.doc.cameras.filter((camera) => camera.hasImage).slice(Math.max(0, state.images));
}

/** The scene times are in µs; a non-positive gap uses the playback fallback. */
export function sampleGapUs(times: readonly number[], index: number): number {
  if (index <= 0 || index >= times.length) {
    return FALLBACK_GAP_US;
  }
  const current = times[index - 1];
  const next = times[index];
  if (typeof current !== "number" || typeof next !== "number" || !Number.isFinite(current) || !Number.isFinite(next)) {
    return FALLBACK_GAP_US;
  }
  const gap = next - current;
  return gap > 0 ? gap : FALLBACK_GAP_US;
}

/**
 * How much playback is buffered ahead of `index`: the contiguous run of ready
 * samples after it, as the sum of their recorded gaps. A hole stops the count,
 * because playback stops there.
 */
export function bufferedAheadUs(
  times: readonly number[],
  index: number,
  ready: (sample: number) => boolean,
): number {
  const last = times.length - 1;
  if (last < 1 || index < 0 || index >= last) {
    return 0;
  }
  let buffered = 0;
  for (let sample = index + 1; sample <= last; sample += 1) {
    if (!ready(sample)) {
      break;
    }
    buffered += sampleGapUs(times, sample);
  }
  return buffered;
}

export type TaskKind = "frame" | "points" | "images";

/** One request the scheduler wants. Point sweeps share one task per sample. */
export type FetchTask = {
  kind: TaskKind;
  index: number;
  /**
   * 1 the current sample (its sweeps, then its frame document),
   * 2 the current sample's camera images,
   * 3 the point sweeps of the samples ahead, nearest first,
   * 4 the rest of the prefetch (frame documents, then camera images).
   */
  priority: 1 | 2 | 3 | 4;
  key: string;
};

/** The cache key of one task. The same string is used for in-flight deduplication. */
export function taskKey(kind: TaskKind, index: number): string {
  return `${kind}:${index}`;
}

export type BufferPlan = {
  tasks: FetchTask[];
  /** Playback buffered ahead of the current sample, in µs. */
  bufferedAheadUs: number;
  /** Playback the settings ask for ahead of the current sample, in µs (target × speed). */
  targetUs: number;
  /** The last sample the plan wants loaded (the target, the memory cap or the scene end). */
  lastNeeded: number;
  /** The plan reaches the end of the scene. */
  atEnd: boolean;
};

export type PlanInput = {
  times: readonly number[];
  index: number;
  states: FrameStates;
  /** Task keys already in flight, so a pump never asks twice. */
  inflight: ReadonlySet<string>;
  /** Buffer target in playback seconds (Settings → Viewer). */
  targetSeconds: number;
  /** Playback speed: at 2× twice the media time is consumed per second. */
  speed: number;
  /** Cap of the client cache in bytes (Settings → Viewer). */
  memoryLimitBytes: number;
  /** Bytes the cache holds now. */
  cachedBytes: number;
  /** A rough size of one unloaded sample, for the memory-cap admission. */
  bytesPerFrame: number;
  /** Whether the current sample's camera images should be prefetched too. */
  wantsImages: boolean;
};

function emptyPlan(index: number): BufferPlan {
  return { tasks: [], bufferedAheadUs: 0, targetUs: 0, lastNeeded: index, atEnd: true };
}

/**
 * The fetches the buffer wants next, in priority order, never past the buffer
 * target, the memory cap or the end of the scene. Pure: no fetch is made here.
 */
export function planBuffer(input: PlanInput): BufferPlan {
  const { times, states, inflight, targetSeconds, speed } = input;
  const last = times.length - 1;
  if (last < 0) {
    return emptyPlan(0);
  }
  const index = Math.min(Math.max(input.index, 0), last);
  const safeSpeed = Number.isFinite(speed) && speed > 0 ? speed : 1;
  const targetUs = Math.max(0, Math.round(targetSeconds * 1_000_000 * safeSpeed));
  const frameBytes = Math.max(0, input.bytesPerFrame);
  const ready = (sample: number) => frameReady(states.get(sample));
  const bufferedNowUs = bufferedAheadUs(times, index, ready);
  const wantsImages = input.wantsImages;

  // How far ahead the target, the memory cap and the scene end allow the prefetch
  // to go. The current sample is always admitted: drawing it is the point.
  let remaining = Math.max(0, input.memoryLimitBytes - input.cachedBytes);
  let lastNeeded = index;
  let mediaAheadUs = 0;
  for (let sample = index; sample <= last; sample += 1) {
    if (sample > index) {
      mediaAheadUs += sampleGapUs(times, sample);
    }
    const cost = frameReady(states.get(sample)) ? 0 : frameBytes;
    if (sample > index && cost > remaining) {
      break;
    }
    remaining -= cost;
    lastNeeded = sample;
    if (mediaAheadUs >= targetUs) {
      break;
    }
  }

  const tasks: FetchTask[] = [];
  const push = (kind: TaskKind, sample: number, priority: FetchTask["priority"]) => {
    const key = taskKey(kind, sample);
    if (!inflight.has(key)) {
      tasks.push({ kind, index: sample, priority, key });
    }
  };

  // 1. The current sample: its point clouds to draw it, or its document when that
  // is what is still missing. (The viewer's draw loop also awaits these; the
  // cache deduplicates the two.)
  const current = states.get(index);
  if (!current?.doc) {
    push("frame", index, 1);
  } else if (current.points < current.doc.pointSensors.length) {
    push("points", index, 1);
  }

  // 2. The current sample's camera images (its boxes arrive with the frame document).
  if (wantsImages && current?.doc) {
    if (current.images < current.doc.cameras.filter((camera) => camera.hasImage).length) {
      push("images", index, 2);
    }
  }

  // 3. The point clouds of the samples ahead, nearest first.
  for (let sample = index + 1; sample <= lastNeeded; sample += 1) {
    const state = states.get(sample);
    if (state?.doc && state.points < state.doc.pointSensors.length) {
      push("points", sample, 3);
    }
  }

  // 4. The rest of the prefetch: the frame documents ahead, nearest first. Their
  // documents tell the next pump which sweeps to fetch. Camera images are not
  // prefetched ahead: the strip requests the thumbnails it is showing and the
  // next sample's, so a whole horizon of images would be wasted bandwidth.
  for (let sample = index + 1; sample <= lastNeeded; sample += 1) {
    const state = states.get(sample);
    if (!state?.doc) {
      push("frame", sample, 4);
    }
  }

  return {
    tasks,
    bufferedAheadUs: bufferedNowUs,
    targetUs,
    lastNeeded,
    atEnd: lastNeeded >= last,
  };
}

export type StallInput = {
  times: readonly number[];
  index: number;
  states: FrameStates;
  playing: boolean;
  /** Whether the previous evaluation was stalled: resume needs more headroom than stall. */
  wasStalled: boolean;
  speed: number;
};

export type StallResult = {
  stalled: boolean;
  resumable: boolean;
  bufferedAheadUs: number;
  /** Media µs that count as "about to run dry" for this scene and speed. */
  stallUs: number;
  /** Media µs a stalled playback needs before it resumes. */
  resumeUs: number;
};

/**
 * Stall/resume with hysteresis: a playing buffer pauses when under
 * [`STALL_BUFFER_US`] of playback is buffered ahead, and only moves again at
 * [`RESUME_BUFFER_US`]. Near the end of the scene the remaining scene time is
 * the cap, so a fully buffered tail never reads as a stall. Paused playback is
 * never stalled (the user paused; only the user resumes it).
 */
export function stallState(input: StallInput): StallResult {
  const last = input.times.length - 1;
  const index = Math.min(Math.max(input.index, 0), Math.max(last, 0));
  const safeSpeed = Number.isFinite(input.speed) && input.speed > 0 ? input.speed : 1;
  const bufferedNow = bufferedAheadUs(input.times, index, (sample) => frameReady(input.states.get(sample)));
  const leftUs = last > index ? Math.max(0, (input.times[last] ?? 0) - (input.times[index] ?? 0)) : 0;
  const stallUs = Math.min(STALL_BUFFER_US * safeSpeed, leftUs);
  const resumeUs = Math.min(RESUME_BUFFER_US * safeSpeed, leftUs);
  const stalled =
    Boolean(input.playing) &&
    (input.wasStalled ? bufferedNow < resumeUs : bufferedNow < stallUs);
  return {
    stalled,
    resumable: bufferedNow >= resumeUs,
    bufferedAheadUs: bufferedNow,
    stallUs,
    resumeUs,
  };
}

/**
 * The fraction of the buffer target that is filled ahead of the playhead, for
 * the timeline's buffer bar. `targetUs` is in media time (target × speed).
 */
export function bufferFraction(bufferedUs: number, targetUs: number): number {
  if (!(targetUs > 0) || !Number.isFinite(bufferedUs)) {
    return 0;
  }
  return Math.min(1, Math.max(0, bufferedUs / targetUs));
}

/** Linear-interpolated percentile of the finite values; null for none. */
export function percentile(values: readonly number[], fraction: number): number | null {
  const sorted = values.filter((value) => Number.isFinite(value)).slice().sort((a, b) => a - b);
  if (sorted.length === 0) {
    return null;
  }
  const at = Math.min(1, Math.max(0, fraction)) * (sorted.length - 1);
  const low = Math.floor(at);
  const high = Math.ceil(at);
  return sorted[low] + (sorted[high] - sorted[low]) * (at - low);
}

export type ByteSample = { at: number; bytes: number };

/** Bytes per second over a rolling window, smoothed over the whole window. */
export function rollingBytesPerSecond(
  samples: readonly ByteSample[],
  nowMs: number,
  windowMs = 2000,
): number {
  if (!(windowMs > 0)) {
    return 0;
  }
  let bytes = 0;
  for (const sample of samples) {
    if (sample.at <= nowMs && nowMs - sample.at <= windowMs) {
      bytes += sample.bytes;
    }
  }
  return bytes / (windowMs / 1000);
}

/** The same, in MiB/s (the viewer's "MB/s"). */
export function rollingMegabytesPerSecond(
  samples: readonly ByteSample[],
  nowMs: number,
  windowMs = 2000,
): number {
  return rollingBytesPerSecond(samples, nowMs, windowMs) / (1024 * 1024);
}

/** One cached thing the byte cap may evict. */
export type CacheEntry = {
  key: string;
  /** The sample time this entry belongs to, in µs. */
  timeUs: number;
  bytes: number;
};

/**
 * Keys to evict so the cache fits `limitBytes`, oldest-behind-the-playhead
 * first (farthest behind first), then farthest ahead. The entries at the
 * playhead's own time are kept unless they are the only ones left. Pure: the
 * cache applies the list to its maps.
 */
export function chooseEvictions(
  entries: readonly CacheEntry[],
  limitBytes: number,
  playheadUs: number,
): string[] {
  let total = 0;
  for (const entry of entries) {
    total += Math.max(0, entry.bytes);
  }
  if (total <= limitBytes) {
    return [];
  }
  const atPlayhead = entries.filter((entry) => entry.timeUs === playheadUs);
  const behind = entries
    .filter((entry) => entry.timeUs < playheadUs)
    .sort((a, b) => a.timeUs - b.timeUs || a.key.localeCompare(b.key));
  const ahead = entries
    .filter((entry) => entry.timeUs > playheadUs)
    .sort((a, b) => b.timeUs - a.timeUs || a.key.localeCompare(b.key));
  const evicted: string[] = [];
  for (const entry of [...behind, ...ahead]) {
    if (total <= limitBytes) {
      break;
    }
    total -= Math.max(0, entry.bytes);
    evicted.push(entry.key);
  }
  // Only the playhead's own samples are left: drop the largest so a tiny limit
  // cannot deadlock the viewer (it reloads from the server).
  if (total > limitBytes) {
    const own = atPlayhead.slice().sort((a, b) => b.bytes - a.bytes || a.key.localeCompare(b.key));
    for (const entry of own) {
      if (total <= limitBytes) {
        break;
      }
      total -= Math.max(0, entry.bytes);
      evicted.push(entry.key);
    }
  }
  return evicted;
}

/** The network side of the scheduler: the viewer binds this to the frame cache. */
export type StreamSource = {
  frame: (index: number, options: { signal: AbortSignal; needed: boolean }) => Promise<FrameInfo>;
  points: (
    index: number,
    sensor: PointSensor,
    options: { signal: AbortSignal; needed: boolean },
  ) => Promise<unknown>;
  /** Optional: the current sample's camera images, when the viewer prefetches them. */
  image?: (index: number, camera: CameraRef, options: { signal: AbortSignal }) => Promise<unknown>;
};

export type StreamConfig = {
  /** Changing the scene clears the loaded set and aborts everything in flight. */
  sceneId: string;
  /**
   * Changes when the viewer's cache was emptied without a scene change (a token
   * change) or shrunk (the memory limit), so the loaded set is rebuilt from what
   * the cache can actually hold.
   */
  epoch: number;
  times: readonly number[];
  targetSeconds: number;
  speed: number;
  memoryLimitBytes: number;
  /** The cache's current byte usage, read when a plan is made. */
  cachedBytes: () => number;
  /** A rough size for an unloaded sample, for the memory-cap admission. */
  bytesPerFrame: number;
  /** Whether the current sample's camera images should be fetched too. */
  wantsImages: boolean;
};

export type StreamProgress = {
  sceneId: string;
  index: number;
  bufferedAheadUs: number;
  targetUs: number;
  stalled: boolean;
  resumable: boolean;
  readyFrames: number;
  pending: number;
  lastNeeded: number;
  atEnd: boolean;
};

export type StreamBuffer = {
  configure: (config: StreamConfig) => void;
  setIndex: (index: number) => void;
  setPlaying: (playing: boolean) => void;
  /** Ask the scheduler to fill the buffer; safe to call as often as wanted. */
  pump: () => void;
  /** Abort in-flight requests whose samples the current plan no longer wants. */
  cancelOutdated: () => void;
  cancelAll: () => void;
  progress: () => StreamProgress;
  dispose: () => void;
};

type Request = {
  key: string;
  taskKey: string;
  sample: number;
  controller: AbortController;
  needed: boolean;
};

type Failure = {
  at: number;
  taskKey: string;
};

const FAILURE_RETRY_MS = 5000;

/**
 * A stateful scheduler around [`planBuffer`]. It starts the fetches the plan
 * asks for (up to `maxConcurrent`), deduplicates them, and turns a seek or a
 * scene change into an abort of everything the new plan does not want. A reply
 * from an older scene or a cancelled request never enters the loaded set.
 */
export function createStreamBuffer(options: {
  source: StreamSource;
  maxConcurrent?: number;
  now?: () => number;
  /** Called whenever the buffer state changes (fetch started/finished, seek, config). */
  onProgress?: (progress: StreamProgress) => void;
}): StreamBuffer {
  const source = options.source;
  const maxConcurrent = Math.max(1, options.maxConcurrent ?? 6);
  const now = options.now ?? (() => (typeof performance !== "undefined" ? performance.now() : Date.now()));
  const states = new Map<number, FrameState>();
  const requests = new Map<string, Request>();
  const failures = new Map<string, Failure>();
  let config: StreamConfig | null = null;
  let index = 0;
  let playing = false;
  let wasStalled = false;
  let generation = 0;
  let disposed = false;
  let lastProgress: StreamProgress = {
    sceneId: "",
    index: 0,
    bufferedAheadUs: 0,
    targetUs: 0,
    stalled: false,
    resumable: true,
    readyFrames: 0,
    pending: 0,
    lastNeeded: 0,
    atEnd: true,
  };

  const stateFor = (sample: number): FrameState => {
    let state = states.get(sample);
    if (!state) {
      state = emptyFrameState();
      states.set(sample, state);
    }
    return state;
  };

  const blockedTaskKeys = (): Set<string> => {
    const blocked = new Set<string>();
    const at = now();
    for (const request of requests.values()) {
      blocked.add(request.taskKey);
    }
    for (const [key, failure] of failures) {
      if (at - failure.at < FAILURE_RETRY_MS) {
        blocked.add(failure.taskKey);
        blocked.add(key);
      } else {
        failures.delete(key);
      }
    }
    return blocked;
  };

  const planNow = (ignoreInflight = false): BufferPlan => {
    if (!config) {
      return emptyPlan(index);
    }
    return planBuffer({
      times: config.times,
      index,
      states,
      inflight: ignoreInflight ? new Set<string>() : blockedTaskKeys(),
      targetSeconds: config.targetSeconds,
      speed: config.speed,
      memoryLimitBytes: config.memoryLimitBytes,
      cachedBytes: config.cachedBytes(),
      bytesPerFrame: config.bytesPerFrame,
      wantsImages: config.wantsImages,
    });
  };

  const makeProgress = (): StreamProgress => {
    const plan = planNow();
    const stall = stallState({
      times: config?.times ?? [],
      index,
      states,
      playing,
      wasStalled,
      speed: config?.speed ?? 1,
    });
    // Remember the hysteresis state: once stalled, the resume threshold applies
    // until the buffer is full enough (or playback stops).
    wasStalled = stall.stalled;
    let readyFrames = 0;
    for (const [sample, state] of states) {
      if (sample >= index && frameReady(state)) {
        readyFrames += 1;
      }
    }
    lastProgress = {
      sceneId: config?.sceneId ?? "",
      index,
      bufferedAheadUs: plan.bufferedAheadUs,
      targetUs: plan.targetUs,
      stalled: stall.stalled,
      resumable: stall.resumable,
      readyFrames,
      pending: requests.size,
      lastNeeded: plan.lastNeeded,
      atEnd: plan.atEnd,
    };
    return lastProgress;
  };

  const notify = () => {
    options.onProgress?.(makeProgress());
  };

  const pump = () => {
    if (disposed || !config) {
      return;
    }
    const plan = planNow();
    const slots = maxConcurrent - requests.size;
    // On a paused first load, the current sample is not drawable yet: queue no
    // ahead sweeps behind it. On a narrow link (one serialized sweep at a time)
    // that would put the first frame's own point cloud at the back of the
    // prefetch, delaying the first paint by seconds. The current sample's task
    // (priority 1) and its camera images (2) may start; prefetch waits until it
    // is ready. Playing keeps the full prefetch: it is no longer a cold start.
    const holdPrefetch = !playing && !frameReady(states.get(index));
    let used = 0;
    for (const task of plan.tasks) {
      if (used >= slots) {
        break;
      }
      if (holdPrefetch && task.priority > 2) {
        break;
      }
      const started = start(task, slots - used);
      if (started > 0) {
        used += started;
      }
    }
    notify();
  };

  const startFrame = (sample: number) => {
    if (!config) {
      return;
    }
    const key = taskKey("frame", sample);
    if (requests.has(key)) {
      return;
    }
    const request: Request = {
      key,
      taskKey: key,
      sample,
      controller: new AbortController(),
      needed: sample === index,
    };
    requests.set(key, request);
    const mine = generation;
    let aborted = false;
    source
      .frame(sample, { signal: request.controller.signal, needed: request.needed })
      .then((info) => {
        if (disposed || mine !== generation || request.controller.signal.aborted) {
          return;
        }
        const state = stateFor(sample);
        state.doc = info;
        state.points = Math.min(state.points, info.pointSensors.length);
        state.images = Math.min(state.images, info.cameras.filter((camera) => camera.hasImage).length);
      })
      .catch((error: unknown) => {
        aborted = isAbortError(error);
        if (!aborted && mine === generation) {
          failures.set(key, { at: now(), taskKey: key });
        }
      })
      .finally(() => {
        requests.delete(key);
        // An aborted request is not retried here: the seek or scene change that
        // aborted it is the one that plans the next pump.
        if (!disposed && mine === generation && !aborted) {
          pump();
        } else {
          notify();
        }
      });
  };

  const startPoints = (sample: number, sensors: readonly PointSensor[]) => {
    for (const sensor of sensors) {
      const key = `point:${sample}:${sensor.agentId}/${sensor.sensorId}`;
      if (requests.has(key)) {
        continue;
      }
      const request: Request = {
        key,
        taskKey: taskKey("points", sample),
        sample,
        controller: new AbortController(),
        needed: sample === index,
      };
      requests.set(key, request);
      const mine = generation;
      let aborted = false;
      source
        .points(sample, sensor, { signal: request.controller.signal, needed: request.needed })
        .then(() => {
          if (disposed || mine !== generation || request.controller.signal.aborted) {
            return;
          }
          stateFor(sample).points += 1;
        })
        .catch((error: unknown) => {
          aborted = isAbortError(error);
          if (!aborted && mine === generation) {
            failures.set(key, { at: now(), taskKey: request.taskKey });
          }
        })
        .finally(() => {
          requests.delete(key);
          if (!disposed && mine === generation && !aborted) {
            pump();
          } else {
            notify();
          }
        });
    }
  };

  const startImages = (sample: number, cameras: readonly CameraRef[]) => {
    if (!source.image) {
      return;
    }
    for (const camera of cameras) {
      if (!camera.hasImage) {
        continue;
      }
      const key = `image:${sample}:${camera.agentId}/${camera.sensorId}`;
      if (requests.has(key)) {
        continue;
      }
      const request: Request = {
        key,
        taskKey: taskKey("images", sample),
        sample,
        controller: new AbortController(),
        needed: sample === index,
      };
      requests.set(key, request);
      const mine = generation;
      let aborted = false;
      source
        .image(sample, camera, { signal: request.controller.signal })
        .then(() => {
          if (disposed || mine !== generation || request.controller.signal.aborted) {
            return;
          }
          stateFor(sample).images += 1;
        })
        .catch((error: unknown) => {
          aborted = isAbortError(error);
        })
        .finally(() => {
          requests.delete(key);
          if (!disposed && mine === generation && !aborted) {
            pump();
          } else {
            notify();
          }
        });
    }
  };

  /** Start at most `limit` requests for a task; returns how many were started. */
  const start = (task: FetchTask, limit: number): number => {
    if (task.kind === "frame") {
      startFrame(task.index);
      return 1;
    }
    if (limit <= 0) {
      return 0;
    }
    if (task.kind === "points") {
      const sensors = missingPoints(states.get(task.index)).slice(0, limit);
      startPoints(task.index, sensors);
      return sensors.length;
    }
    const cameras = missingImages(states.get(task.index)).slice(0, limit);
    startImages(task.index, cameras);
    return cameras.length;
  };

  const cancelKeys = (keepTaskKeys: ReadonlySet<string>) => {
    for (const request of [...requests.values()]) {
      if (keepTaskKeys.has(request.taskKey)) {
        continue;
      }
      requests.delete(request.key);
      request.controller.abort();
    }
  };

  /** The task keys the current plan wants, ignoring what is already in flight. */
  const wantedTaskKeys = (): Set<string> => new Set(planNow(true).tasks.map((task) => task.key));

  return {
    configure(next) {
      const sceneChanged = !config || config.sceneId !== next.sceneId || config.epoch !== next.epoch;
      const sceneLengthChanged =
        config !== null && !sceneChanged && config.times.length !== next.times.length;
      if (sceneChanged) {
        generation += 1;
        for (const request of requests.values()) {
          request.controller.abort();
        }
        requests.clear();
        states.clear();
        failures.clear();
        wasStalled = false;
      } else if (sceneLengthChanged) {
        states.clear();
        failures.clear();
      }
      config = next;
      cancelKeys(wantedTaskKeys());
      pump();
    },
    setIndex(next) {
      const wanted = Math.max(0, Math.floor(next));
      if (wanted === index) {
        pump();
        return;
      }
      index = wanted;
      // A seek invalidates the prefetch for the old position: abort what the new
      // plan does not want (per-request controllers, not only the scene switch).
      cancelKeys(wantedTaskKeys());
      pump();
    },
    setPlaying(next) {
      playing = Boolean(next);
      if (!playing) {
        wasStalled = false;
      }
      notify();
    },
    pump,
    cancelOutdated() {
      cancelKeys(wantedTaskKeys());
      notify();
    },
    cancelAll() {
      generation += 1;
      for (const request of requests.values()) {
        request.controller.abort();
      }
      requests.clear();
      notify();
    },
    progress: makeProgress,
    dispose() {
      disposed = true;
      generation += 1;
      for (const request of requests.values()) {
        request.controller.abort();
      }
      requests.clear();
      states.clear();
      failures.clear();
    },
  };
}

/** The abort error the cache and the scheduler throw for a cancelled request. */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
