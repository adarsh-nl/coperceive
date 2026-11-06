// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import {
  bufferedAheadUs,
  bufferFraction,
  chooseEvictions,
  createStreamBuffer,
  emptyFrameState,
  frameReady,
  missingImages,
  missingPoints,
  percentile,
  planBuffer,
  rollingMegabytesPerSecond,
  sampleGapUs,
  stallState,
  STALL_BUFFER_US,
  RESUME_BUFFER_US,
  taskKey,
  type CameraRef,
  type FrameInfo,
  type FrameStates,
  type PointSensor,
} from "./streamBuffer.ts";

const HZ10 = Array.from({ length: 100 }, (_, index) => index * 100_000);

function sensor(id: number): PointSensor {
  return { agentId: "vehicle", sensorId: `lidar_${id}`, count: 8 };
}

function camera(id: number): CameraRef {
  return { agentId: "vehicle", sensorId: `cam_${id}`, hasImage: true };
}

/** A loaded frame document; `points` sweeps of it are in the cache. */
function state(options: {
  timeUs?: number;
  sensors?: number;
  cameras?: number;
  points?: number;
  images?: number;
}): ReturnType<typeof emptyFrameState> {
  const info: FrameInfo = {
    timeUs: options.timeUs ?? 0,
    pointSensors: Array.from({ length: options.sensors ?? 1 }, (_, index) => sensor(index)),
    cameras: Array.from({ length: options.cameras ?? 0 }, (_, index) => camera(index)),
  };
  return {
    doc: info,
    points: options.points ?? 0,
    images: options.images ?? 0,
  };
}

function plan(states: FrameStates, overrides: Partial<Parameters<typeof planBuffer>[0]> = {}) {
  return planBuffer({
    times: HZ10,
    index: 0,
    states,
    inflight: new Set<string>(),
    targetSeconds: 0.3,
    speed: 1,
    memoryLimitBytes: 1024 ** 3,
    cachedBytes: 0,
    bytesPerFrame: 1_000_000,
    wantsImages: false,
    ...overrides,
  });
}

test("a sample is ready when its document and every sweep it lists are cached", () => {
  assert.equal(frameReady(undefined), false);
  assert.equal(frameReady(emptyFrameState()), false);
  assert.equal(frameReady(state({ sensors: 0 })), true);
  assert.equal(frameReady(state({ sensors: 2, points: 1 })), false);
  assert.equal(frameReady(state({ sensors: 2, points: 2 })), true);
  assert.deepEqual(missingPoints(state({ sensors: 2, points: 1 })).map((item) => item.sensorId), ["lidar_1"]);
  const cameras = state({ sensors: 0, cameras: 2, images: 1 });
  assert.deepEqual(missingImages(cameras).map((item) => item.sensorId), ["cam_1"]);
});

test("the gap of the scene's own times is used, with the playback fallback for a bad gap", () => {
  const times = [0, 50_000, 150_000, 150_000];
  assert.equal(sampleGapUs(times, 1), 50_000);
  assert.equal(sampleGapUs(times, 2), 100_000);
  assert.equal(sampleGapUs(times, 3), 100_000); // non-positive gap
  assert.equal(sampleGapUs(times, 0), 100_000);
});

test("buffered-ahead is the contiguous ready run after the current sample", () => {
  const ready = (sample: number) => [1, 2, 3].includes(sample);
  assert.equal(bufferedAheadUs(HZ10, 0, ready), 300_000);
  assert.equal(bufferedAheadUs(HZ10, 1, ready), 200_000);
  assert.equal(bufferedAheadUs(HZ10, 3, ready), 0);
  // A hole stops playback, so it stops the count.
  const holed = (sample: number) => sample === 1 || sample === 3;
  assert.equal(bufferedAheadUs(HZ10, 0, holed), 100_000);
  assert.equal(bufferedAheadUs(HZ10, 99, ready), 0);
});

test("priority: the current sample, its images, the point clouds ahead, then the rest", () => {
  // The current sample's document is cached but its sweep is not; the next sample's
  // document was cached with it (so its sweep can be fetched); the one after that
  // needs its document first. Camera images are on for the current sample.
  const states = new Map<number, ReturnType<typeof emptyFrameState>>([
    [0, state({ sensors: 1, cameras: 1, points: 0 })],
    [1, state({ sensors: 1, points: 0 })],
  ]);
  const order = plan(states, { targetSeconds: 0.3, wantsImages: true }).tasks.map((task) => task.key);
  // Samples 2 and 3 (still inside the 0.3 s target) have no document yet.
  assert.deepEqual(order, ["points:0", "images:0", "points:1", "frame:2", "frame:3"]);

  // A current sample whose document has not arrived yet is fetched as one task first.
  assert.deepEqual(
    plan(new Map(), { targetSeconds: 0.1 }).tasks.map((task) => task.key),
    ["frame:0", "frame:1"],
  );
});

test("the target, the memory cap and the scene end bound the prefetch", () => {
  const far = new Map(Array.from({ length: HZ10.length }, (_, index) => [index, state({ sensors: 0 })]));
  // 0.3 s of playback is three samples at 10 Hz: samples 1, 2 and 3.
  assert.equal(plan(far, { targetSeconds: 0.3, states: far }).lastNeeded, 3);
  // A 5 Hz scene consumes twice the media time per playback second: the same
  // 0.3 s target reaches one sample further.
  const slow = [0, 200_000, 400_000, 600_000, 800_000];
  assert.equal(plan(far, { times: slow, targetSeconds: 0.3 }).lastNeeded, 2);
  // A 2x speed needs the same playback time in half the wall clock, but twice the media.
  assert.equal(plan(far, { targetSeconds: 0.3, speed: 2 }).lastNeeded, 6);
  // The scene end caps it.
  assert.equal(plan(far, { times: slow, targetSeconds: 5 }).lastNeeded, 4);
  assert.equal(plan(far, { times: slow, targetSeconds: 5 }).atEnd, true);
  // The memory cap admits whole samples only; the current sample always.
  const plan6 = plan(new Map(), { targetSeconds: 5, memoryLimitBytes: 2_500_000, bytesPerFrame: 1_000_000 });
  assert.equal(plan6.lastNeeded, 1);
  assert.equal(plan(new Map(), { memoryLimitBytes: 0, bytesPerFrame: 1_000_000 }).lastNeeded, 0);
});

test("in-flight requests are never asked twice", () => {
  const inflight = new Set([taskKey("frame", 2), taskKey("points", 1)]);
  const tasks = plan(new Map(), { targetSeconds: 0.3, inflight }).tasks.map((task) => task.key);
  assert.deepEqual(tasks, ["frame:0", "frame:1", "frame:3"]);
});

test("stall and resume use hysteresis, and a fully buffered tail never stalls", () => {
  const ready = (...samples: number[]) =>
    new Map(samples.map((sample) => [sample, state({ sensors: 0 })]));
  // 2 samples ahead = 0.2 s < 0.25 s: about to run dry.
  assert.equal(
    stallState({ times: HZ10, index: 0, states: ready(1, 2), playing: true, wasStalled: false, speed: 1 })
      .stalled,
    true,
  );
  // 3 ahead = 0.3 s is enough while running normally...
  assert.equal(
    stallState({ times: HZ10, index: 0, states: ready(1, 2, 3), playing: true, wasStalled: false, speed: 1 })
      .stalled,
    false,
  );
  // ...but a buffer that already stalled needs the full second to move again.
  for (const play of [0.5, 1, 2]) {
    assert.equal(
      stallState({
        times: HZ10,
        index: 0,
        states: ready(...Array.from({ length: Math.round(play * 10) }, (_, item) => item + 1)),
        playing: true,
        wasStalled: true,
        speed: 1,
      }).stalled,
      play < 1,
      `play=${play}`,
    );
  }
  // Paused playback is never "stalled": only the user resumes it.
  assert.equal(
    stallState({ times: HZ10, index: 0, states: new Map(), playing: false, wasStalled: false, speed: 1 }).stalled,
    false,
  );
  // At the last sample there is nothing left to buffer.
  assert.equal(
    stallState({ times: HZ10, index: 99, states: new Map(), playing: true, wasStalled: false, speed: 1 }).stalled,
    false,
  );
  // A fully buffered tail: index 8 of a 10-sample scene, sample 9 ready: the
  // remaining 0.1 s is the whole target left, so it is not an underrun.
  const tail = stallState({
    times: HZ10.slice(0, 10),
    index: 8,
    states: ready(9),
    playing: true,
    wasStalled: false,
    speed: 1,
  });
  assert.equal(tail.stalled, false);
  assert.equal(tail.stallUs, 100_000);
  // The same with 4x speed scales the thresholds with the media consumed per second.
  const fast = stallState({ times: HZ10, index: 0, states: ready(1, 2), playing: true, wasStalled: false, speed: 4 });
  assert.equal(fast.stallUs, STALL_BUFFER_US * 4);
  assert.equal(fast.stalled, true);
  assert.equal(RESUME_BUFFER_US, 1_000_000);
});

test("the buffer bar shows the filled fraction of the target", () => {
  assert.equal(bufferFraction(1_500_000, 3_000_000), 0.5);
  assert.equal(bufferFraction(4_000_000, 3_000_000), 1);
  assert.equal(bufferFraction(-1, 3_000_000), 0);
  assert.equal(bufferFraction(1, 0), 0);
});

test("eviction goes oldest-behind-the-playhead first and keeps the current sample", () => {
  const entries = [
    { key: "frame:1", timeUs: 100, bytes: 100 },
    { key: "points:1", timeUs: 100, bytes: 400 },
    { key: "frame:2", timeUs: 200, bytes: 100 },
    { key: "frame:3", timeUs: 300, bytes: 100 },
  ];
  // 700 bytes over a 550 limit: the farthest-behind (and the biggest at that
  // time) go first, the sample at the playhead stays.
  assert.deepEqual(chooseEvictions(entries, 550, 200), ["frame:1", "points:1"]);
  // A tighter limit also drops the farthest ahead, never the playhead.
  assert.deepEqual(chooseEvictions(entries, 150, 200), ["frame:1", "points:1", "frame:3"]);
  assert.equal(chooseEvictions(entries, 700, 200).length, 0);
  // With nothing behind, the farthest ahead goes first.
  assert.deepEqual(chooseEvictions(entries.slice(2), 150, 0), ["frame:3"]);
  // Only the playhead's own samples left: they are reloaded from the server.
  assert.deepEqual(
    chooseEvictions(
      [
        { key: "frame:2", timeUs: 200, bytes: 100 },
        { key: "points:2", timeUs: 200, bytes: 300 },
      ],
      200,
      200,
    ),
    ["points:2"],
  );
});

test("percentiles and the rolling MB/s used by the stats line", () => {
  assert.equal(percentile([], 0.5), null);
  assert.equal(percentile([10, 20, 30, 40], 0.5), 25);
  assert.equal(percentile([10, 20, 30, 40], 0.95), 38.5);
  assert.equal(percentile([5], 0.95), 5);
  const samples = [
    { at: 1000, bytes: 1024 * 1024 },
    { at: 1500, bytes: 1024 * 1024 },
    { at: 500, bytes: 1024 * 1024 },
  ];
  // Three MiB inside the two-second window, smoothed over the window.
  assert.equal(rollingMegabytesPerSecond(samples, 2000, 2000), 1.5);
  // A sample older than the window is out.
  assert.equal(rollingMegabytesPerSecond(samples, 5000, 2000), 0);
});

test("the scheduler fills the buffer to the time target and dedupes its requests", async () => {
  const calls = new Map<string, number>();
  const infoFor = (index: number): FrameInfo => ({
    timeUs: HZ10[index],
    pointSensors: index % 2 === 0 ? [sensor(0)] : [],
    cameras: [],
  });
  const source = {
    frame: async (index: number) => {
      calls.set(`frame:${index}`, (calls.get(`frame:${index}`) ?? 0) + 1);
      return infoFor(index);
    },
    points: async (index: number) => {
      calls.set(`points:${index}`, (calls.get(`points:${index}`) ?? 0) + 1);
    },
  };
  const progress: import("./streamBuffer.ts").StreamProgress[] = [];
  const buffer = createStreamBuffer({ source, maxConcurrent: 6, onProgress: (state) => progress.push(state) });
  buffer.configure({
    sceneId: "a",
    epoch: 0,
    times: HZ10,
    targetSeconds: 0.5,
    speed: 1,
    memoryLimitBytes: 1024 ** 3,
    cachedBytes: () => 0,
    bytesPerFrame: 1_000_000,
    wantsImages: false,
  });
  // Pumping again before the first replies land must not duplicate requests.
  buffer.pump();
  buffer.pump();
  for (let tick = 0; tick < 200; tick += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    const state = buffer.progress();
    if (state.bufferedAheadUs >= 500_000) {
      break;
    }
  }
  const state = buffer.progress();
  assert.ok(state.bufferedAheadUs >= 500_000, state);
  assert.ok(state.readyFrames >= 6, state);
  for (const [key, count] of calls) {
    assert.equal(count, 1, `${key} asked ${count} times`);
  }
  buffer.dispose();
});

test("a cache epoch change rebuilds the loaded set from scratch", async () => {
  const infoFor = (index: number): FrameInfo => ({
    timeUs: HZ10[index],
    pointSensors: [],
    cameras: [],
  });
  const source = {
    frame: async (index: number) => infoFor(index),
    points: async () => undefined,
  };
  const buffer = createStreamBuffer({ source });
  const configure = (epoch: number) =>
    buffer.configure({
      sceneId: "a",
      epoch,
      times: HZ10,
      targetSeconds: 0.3,
      speed: 1,
      memoryLimitBytes: 1024 ** 3,
      cachedBytes: () => 0,
      bytesPerFrame: 1_000_000,
      wantsImages: false,
    });
  configure(0);
  for (let tick = 0; tick < 100; tick += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    if (buffer.progress().bufferedAheadUs >= 300_000) {
      break;
    }
  }
  assert.ok(buffer.progress().bufferedAheadUs >= 300_000);
  // Same scene, but the cache was emptied/shrunk: the loaded set is rebuilt.
  configure(1);
  assert.equal(buffer.progress().bufferedAheadUs, 0, "an emptied cache cannot report buffered samples");
  for (let tick = 0; tick < 100; tick += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    if (buffer.progress().bufferedAheadUs >= 300_000) {
      break;
    }
  }
  assert.ok(buffer.progress().bufferedAheadUs >= 300_000);
  buffer.dispose();
});

test("a seek aborts the requests the new position no longer wants", async () => {
  const started: { index: number; signal: AbortSignal }[] = [];
  const resolvers = new Map<number, (info: FrameInfo) => void>();
  const source = {
    frame: (index: number, options: { signal: AbortSignal }) => {
      started.push({ index, signal: options.signal });
      return new Promise<FrameInfo>((resolve) => resolvers.set(index, resolve));
    },
    points: async () => undefined,
  };
  const buffer = createStreamBuffer({ source, maxConcurrent: 4 });
  buffer.configure({
    sceneId: "a",
    epoch: 0,
    times: HZ10,
    targetSeconds: 5,
    speed: 1,
    memoryLimitBytes: 1024 ** 3,
    cachedBytes: () => 0,
    bytesPerFrame: 1_000_000,
    wantsImages: false,
  });
  assert.deepEqual(started.map((item) => item.index).sort((a, b) => a - b), [0, 1, 2, 3]);
  // Seek to sample 10: samples 1 and 2 are now behind the playhead and were
  // cancelled; the request for the new current sample starts.
  buffer.setIndex(10);
  const aborted = started.filter((item) => item.signal.aborted).map((item) => item.index).sort((a, b) => a - b);
  assert.deepEqual(aborted, [0, 1, 2, 3]);
  assert.ok(started.some((item) => item.index === 10), started.map((item) => item.index));
  const fresh = started.find((item) => item.index === 10);
  assert.equal(fresh?.signal.aborted, false);
  // A reply for a cancelled request is ignored.
  resolvers.get(1)?.({
    timeUs: HZ10[1],
    pointSensors: [],
    cameras: [],
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(buffer.progress().index, 10);
  buffer.dispose();
});

test("a scene change aborts everything and ignores a late reply from the old scene", async () => {
  const started: { index: number; signal: AbortSignal }[] = [];
  const resolvers = new Map<number, (info: FrameInfo) => void>();
  const source = {
    frame: (index: number, options: { signal: AbortSignal }) => {
      started.push({ index, signal: options.signal });
      return new Promise<FrameInfo>((resolve) => resolvers.set(index, resolve));
    },
    points: async () => undefined,
  };
  const buffer = createStreamBuffer({ source, maxConcurrent: 2 });
  const config = {
    epoch: 0,
    times: HZ10,
    targetSeconds: 1,
    speed: 1,
    memoryLimitBytes: 1024 ** 3,
    cachedBytes: () => 0,
    bytesPerFrame: 1_000_000,
    wantsImages: false,
  };
  buffer.configure({ ...config, sceneId: "a" });
  const oldSignals = started.map((item) => item.signal);
  buffer.configure({ ...config, sceneId: "b" });
  assert.ok(oldSignals.every((signal) => signal.aborted));
  resolvers.get(0)?.({
    timeUs: HZ10[0],
    pointSensors: [],
    cameras: [],
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  // The late a reply never entered b's loaded set: the buffer is still empty.
  assert.equal(buffer.progress().sceneId, "b");
  assert.equal(buffer.progress().bufferedAheadUs, 0);
  buffer.dispose();
});
