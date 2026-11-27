import test from "node:test";
import assert from "node:assert/strict";
import {
  indexAfter,
  isEndZone,
  PLAYBACK_BUFFERING_AFTER_MS,
  shouldClearBufferingOnDraw,
  shouldRunSyncStageRedraw,
  stepDrawnHoldWait,
} from "./playback.ts";
import { createFrameCache } from "./cache.ts";

test("isEndZone with 2-second threshold being larger (short 150-sample scene at 10Hz)", () => {
  // 150 samples at 10 Hz: 0 to 14.9 s (14_900_000 us).
  // 5% is 7.5 samples -> index 142.
  // 2 seconds before end is 12.9 s -> index 129.
  // 2-second zone is larger (covers indices 129..149).
  const times = Array.from({ length: 150 }, (_, i) => i * 100_000);

  assert.equal(isEndZone(times, 120), false);
  assert.equal(isEndZone(times, 128), false);
  assert.equal(isEndZone(times, 129), true);
  assert.equal(isEndZone(times, 144), true);
  assert.equal(isEndZone(times, 149), true);
});

test("isEndZone with 5% threshold being larger (long 1000-sample scene at 10Hz = 100s)", () => {
  // 1000 samples at 10 Hz: 0 to 99.9 s.
  // 5% is 50 samples -> index 950 (last 5 seconds).
  // 2 seconds before end is 97.9 s -> index 979.
  // 5% zone is larger (covers indices 950..999).
  const times = Array.from({ length: 1000 }, (_, i) => i * 100_000);

  assert.equal(isEndZone(times, 940), false);
  assert.equal(isEndZone(times, 950), true);
  assert.equal(isEndZone(times, 970), true);
  assert.equal(isEndZone(times, 999), true);
});

test("isEndZone handles edge cases safely", () => {
  assert.equal(isEndZone(undefined, 0), false);
  assert.equal(isEndZone([], 0), false);
  assert.equal(isEndZone([0], 0), false);
  assert.equal(isEndZone([0, 100_000], 1), true);
});

test("FrameCache retains all 150 frames of a local scene after telemetry scan so playback does not re-fetch", async () => {
  const originalFetch = globalThis.fetch;
  const g = globalThis as unknown as Record<string, unknown>;
  const prevWindow = g.window;
  const prevDocument = g.document;
  g.window = { location: { origin: "http://127.0.0.1:8000", protocol: "http:" } };
  g.document = { baseURI: "http://127.0.0.1:8000/", querySelector: () => null };
  let frameFetches = 0;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/frame?")) {
      frameFetches += 1;
      const t = Number(new URL(url, "http://127.0.0.1").searchParams.get("time_us") ?? 0);
      return new Response(
        JSON.stringify({
          scene_id: "Town03-000",
          time_us: t,
          agents: [],
          boxes: [],
          point_sensors: [],
          frustums: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(new ArrayBuffer(0), { status: 200 });
  }) as typeof fetch;

  try {
    const cache = createFrameCache();
    const times = Array.from({ length: 150 }, (_, i) => i * 100_000);
    for (const t of times) {
      await cache.loadFrame("Town03-000", t, "tok");
    }
    assert.equal(frameFetches, 150);
    for (const t of times) {
      await cache.loadFrame("Town03-000", t, "tok");
    }
    assert.equal(frameFetches, 150, "all 150 frames must remain cached after telemetry scan");
  } finally {
    globalThis.fetch = originalFetch;
    g.window = prevWindow;
    g.document = prevDocument;
  }
});

test("shouldRunSyncStageRedraw skips sync stage redraw on frameIndex playback ticks and runs on hidden/appearance toggles", () => {
  const hiddenA = { ego: false };
  const appearanceA = { boxes: true, points: true };
  const initial = { sceneId: "demo-junction", summarySceneId: "demo-junction", hidden: hiddenA, appearance: appearanceA };

  assert.equal(shouldRunSyncStageRedraw(null, initial), false);
  // Plain playback tick: only frameIndex changed, so scene/hidden/appearance refs are identical.
  assert.equal(shouldRunSyncStageRedraw(initial, { ...initial }), false);
  // Scene switch: never synchronously redraw the previous scene's latestRef.
  assert.equal(
    shouldRunSyncStageRedraw(initial, { sceneId: "Town03-000", summarySceneId: "Town03-000", hidden: hiddenA, appearance: appearanceA }),
    false,
  );
  // Agent visibility or appearance toggle within the same scene requires immediate redraw.
  assert.equal(
    shouldRunSyncStageRedraw(initial, { ...initial, hidden: { ego: true } }),
    true,
  );
  assert.equal(
    shouldRunSyncStageRedraw(initial, { ...initial, appearance: { boxes: false, points: true } }),
    true,
  );
});

test("stepDrawnHoldWait ignores transient local draw spikes and shouldClearBufferingOnDraw avoids sticky 1-frame hold", () => {
  assert.ok(PLAYBACK_BUFFERING_AFTER_MS >= 250, "buffering threshold must exceed a 10 Hz frame interval and local jitter");

  // Local 1x playback (stalled=false) with a 137 ms draw spike (like CI job 137719013538):
  // previously BUFFERING_AFTER_MS=60 fired setBuffering(true) and skipped clearing on draw.
  const firstPoll = stepDrawnHoldWait({ nowMs: 200, waitingSinceMs: null, stalled: false });
  assert.equal(firstPoll.waitingSinceMs, 200);
  assert.equal(firstPoll.showBuffering, false);

  const spikePoll = stepDrawnHoldWait({ nowMs: 337, waitingSinceMs: firstPoll.waitingSinceMs, stalled: false });
  assert.equal(spikePoll.showBuffering, false);

  // Genuine stream stall only triggers after exceeding PLAYBACK_BUFFERING_AFTER_MS.
  const earlyStall = stepDrawnHoldWait({ nowMs: 380, waitingSinceMs: 200, stalled: true });
  assert.equal(earlyStall.showBuffering, false);

  const realStall = stepDrawnHoldWait({ nowMs: 460, waitingSinceMs: 200, stalled: true });
  assert.equal(realStall.showBuffering, true);

  // Once the held frame finishes drawing, Buffering clears immediately unless stall-paused.
  assert.equal(shouldClearBufferingOnDraw(false), true);
  assert.equal(shouldClearBufferingOnDraw(true), false);
});

test("stepDrawnHoldWait keeps wall-clock origin anchored during transient draw waits so 1x playback catches up", () => {
  const times = Array.from({ length: 10 }, (_, i) => i * 100_000);
  const originMs = 1000;
  let waitingSinceMs: number | null = null;

  // Frame 1 takes 160 ms to load/draw locally (stalled=false); tick polls at +200..+248 ms.
  for (const nowMs of [1200, 1216, 1232, 1248]) {
    const hold = stepDrawnHoldWait({ nowMs, waitingSinceMs, stalled: false });
    waitingSinceMs = hold.waitingSinceMs;
    assert.equal(hold.resetOrigin, false, "transient local draw wait must not slip origin forward");
  }

  // At 1264 ms frame 1 is drawn; anchored at originMs=1000, clock reaches index 2 (1.00x pace).
  const place = indexAfter(times, 0, 1264 - originMs, 1, true);
  assert.equal(place.index, 2);

  // Only a prolonged genuine stream stall resets/pins the origin so it won't skip unbuffered frames.
  const realStall = stepDrawnHoldWait({ nowMs: 1460, waitingSinceMs, stalled: true });
  assert.equal(realStall.resetOrigin, true);
});
