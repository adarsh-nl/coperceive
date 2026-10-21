import test from "node:test";
import assert from "node:assert/strict";
import { isEndZone, stepIndex } from "./playback.ts";

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
