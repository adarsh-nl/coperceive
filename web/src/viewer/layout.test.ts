// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  DEFAULT_LEFT_PX,
  DEFAULT_RIGHT_PX,
  DEFAULT_STRIP_PX,
  MIN_LEFT_PX,
  MAX_LEFT_PX,
  MIN_RIGHT_PX,
  MAX_RIGHT_PX,
  MIN_STRIP_PX,
  MAX_STRIP_PX,
  clampLeft,
  clampRight,
  clampStrip,
  defaultLayout,
} from "./layout.ts";

test("defaultLayout provides expected initial sizes", () => {
  const layout = defaultLayout();
  assert.strictEqual(layout.left_px, DEFAULT_LEFT_PX);
  assert.strictEqual(layout.right_px, DEFAULT_RIGHT_PX);
  assert.strictEqual(layout.strip_px, DEFAULT_STRIP_PX);
});

test("clampLeft enforces min, max, and container constraints", () => {
  assert.strictEqual(clampLeft(DEFAULT_LEFT_PX), DEFAULT_LEFT_PX);
  assert.strictEqual(clampLeft(50), MIN_LEFT_PX);
  assert.strictEqual(clampLeft(999), MAX_LEFT_PX);
  assert.strictEqual(clampLeft(NaN), DEFAULT_LEFT_PX);

  // When container is constrained: containerWidth = 800, rightPx = 300, min stage = 250
  // available for left = 800 - 300 - 250 = 250
  assert.strictEqual(clampLeft(400, 800, 300), 250);
});

test("clampRight enforces min, max, and container constraints", () => {
  assert.strictEqual(clampRight(DEFAULT_RIGHT_PX), DEFAULT_RIGHT_PX);
  assert.strictEqual(clampRight(50), MIN_RIGHT_PX);
  assert.strictEqual(clampRight(999), MAX_RIGHT_PX);
  assert.strictEqual(clampRight(NaN), DEFAULT_RIGHT_PX);

  // When container is constrained: containerWidth = 800, leftPx = 300, min stage = 250
  // available for right = 800 - 300 - 250 = 250
  assert.strictEqual(clampRight(400, 800, 300), 250);
});

test("clampStrip enforces min, max, and viewport constraints", () => {
  assert.strictEqual(clampStrip(DEFAULT_STRIP_PX), DEFAULT_STRIP_PX);
  assert.strictEqual(clampStrip(20), MIN_STRIP_PX);
  assert.strictEqual(clampStrip(999), MAX_STRIP_PX);
  assert.strictEqual(clampStrip(NaN), DEFAULT_STRIP_PX);

  // When containerHeight = 300, min viewport = 150 -> max strip = 150
  assert.strictEqual(clampStrip(200, 300), 150);
});
