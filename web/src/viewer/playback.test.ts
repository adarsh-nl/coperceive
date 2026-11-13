import test from "node:test";
import assert from "node:assert/strict";
import { isEndZone } from "./playback.ts";
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
