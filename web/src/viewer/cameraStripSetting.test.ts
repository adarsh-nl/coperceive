// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_CAMERA_STRIP, initialCameraStrip, isCameraStripMode } from "./cameraStripSetting.ts";

test("camera strip modes: the filmstrip and one agent, nothing else", () => {
  assert.equal(isCameraStripMode("all"), true);
  assert.equal(isCameraStripMode("agent"), true);
  for (const value of ["", "one", "ALL", null, undefined, 1, {}]) {
    assert.equal(isCameraStripMode(value), false, String(value));
  }
});

test("the filmstrip is the default, and the window's initial state wins when valid", () => {
  assert.equal(DEFAULT_CAMERA_STRIP, "all");
  const scope = globalThis as unknown as { window?: { __COPERCEIVE_INITIAL_STATE__?: unknown } };
  const before = scope.window;
  try {
    scope.window = {};
    assert.equal(initialCameraStrip(), "all");
    scope.window = { __COPERCEIVE_INITIAL_STATE__: { camera_strip: "agent" } };
    assert.equal(initialCameraStrip(), "agent");
    scope.window = { __COPERCEIVE_INITIAL_STATE__: { camera_strip: "sideways" } };
    assert.equal(initialCameraStrip(), "all");
  } finally {
    scope.window = before;
  }
});
