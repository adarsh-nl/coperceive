// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { sceneCapabilities, viewpointFor } from "./capabilities.ts";
import type { SceneSummary } from "./types.ts";

function scene(agents: string[], cameras: boolean): SceneSummary {
  return {
    scene_id: "s",
    world_frame: "ENU",
    agents: agents.map((agent_type, index) => ({ agent_id: `${agent_type}${index}`, agent_type })),
    sensors: cameras ? [{ sensor_id: "cam", agent_id: "vehicle0", modality: "camera" }] : [],
    times_us: [0],
  };
}

test("a Griffin-like scene (vehicle and UAV, cameras) offers the Drone view", () => {
  const caps = sceneCapabilities(scene(["vehicle", "uav"], true));
  assert.deepStrictEqual([...caps.viewpoints].sort(), ["bird", "camera", "chase", "drone", "orbit"]);
  assert.strictEqual(caps.cameras, true);
});

test("an OPV2V/V2XSet-like scene (vehicles, an RSU) has no Drone view", () => {
  const caps = sceneCapabilities(scene(["vehicle", "vehicle", "infrastructure"], true));
  assert.ok(!caps.viewpoints.has("drone"));
  assert.ok(caps.viewpoints.has("chase") && caps.viewpoints.has("camera"));
});

test("without cameras there is no Camera view, and a vanished view falls back to Orbit", () => {
  const caps = sceneCapabilities(scene(["vehicle"], false));
  assert.ok(!caps.viewpoints.has("camera") && !caps.cameras);
  assert.strictEqual(viewpointFor("drone", caps), "orbit");
  assert.strictEqual(viewpointFor("chase", caps), "chase");
  assert.deepStrictEqual([...sceneCapabilities(null).viewpoints].sort(), ["bird", "orbit"]);
});
