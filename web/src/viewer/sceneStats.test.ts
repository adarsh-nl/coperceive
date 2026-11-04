// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  agentRole,
  boxesInView,
  compactCount,
  formatDistance,
  histogram,
  scaleBar,
  stripAgents,
  sweepGaps,
} from "./sceneStats.ts";
import type { FrameBox, FrameFrustum } from "./types.ts";

test("compactCount keeps small numbers and shortens large ones", () => {
  assert.equal(compactCount(0), "0");
  assert.equal(compactCount(980), "980");
  assert.equal(compactCount(70_100), "70.1k");
  assert.equal(compactCount(15_800), "15.8k");
  assert.equal(compactCount(250_000), "250k");
  assert.equal(compactCount(1_240_000), "1.2M");
  assert.equal(compactCount(Number.NaN), "—");
});

test("agentRole words the recorded type", () => {
  assert.equal(agentRole("vehicle"), "Vehicle");
  assert.equal(agentRole("uav"), "Drone");
  assert.equal(agentRole("infrastructure"), "Roadside unit");
  assert.equal(agentRole("boat"), "Boat");
  assert.equal(agentRole(""), "Agent");
});

// A camera at the origin looking down +x (camera z), image 100 x 50, f = 50.
// Quaternion rotating camera axes (x right, y down, z forward) onto world (x fwd, y left, z up).
const camera: FrameFrustum = {
  agent_id: "car",
  sensor_id: "front",
  pose: { qw: 0.5, qx: -0.5, qy: 0.5, qz: -0.5, tx: 0, ty: 0, tz: 0 },
  fov_y_rad: 1,
  aspect: 2,
  fx: 50,
  fy: 50,
  cx: 50,
  cy: 25,
  image_width_px: 100,
  image_height_px: 50,
};

function box(agent: string, center: [number, number, number]): FrameBox {
  return { agent_id: agent, lines: [], center };
}

test("boxesInView counts box centres inside the image, in front of the camera", () => {
  const boxes = [
    box("car", [10, 0, 0]), // straight ahead
    box("car", [10, 2, 0]), // a little left, still inside
    box("car", [-10, 0, 0]), // behind
    box("car", [1, 30, 0]), // far off to the side
    box("rsu", [20, 0, 0]), // ahead, another agent's box
  ];
  assert.equal(boxesInView(camera, boxes), 3);
  assert.equal(boxesInView(camera, boxes, { rsu: true }), 2);
});

test("boxesInView is null without intrinsics or image size", () => {
  assert.equal(boxesInView({ ...camera, fx: null }, [box("car", [10, 0, 0])]), null);
  assert.equal(boxesInView({ ...camera, image_width_px: null }, [box("car", [10, 0, 0])]), null);
});

test("sweepGaps lists samples with no sweep, only when the scene has lidar", () => {
  assert.deepEqual(sweepGaps([2, 2, 0, 1, 0, null], true), [2, 4]);
  assert.deepEqual(sweepGaps([0, 0, 0], false), []);
});

test("histogram keeps the peak of each bin", () => {
  assert.deepEqual(histogram([1, 5, 2, 2, 9, 0], 3), { bars: [5, 2, 9], max: 9 });
  assert.deepEqual(histogram([3, 4], 10), { bars: [3, 4], max: 4 });
  assert.deepEqual(histogram([], 10), { bars: [], max: 0 });
});

test("scaleBar picks a round length that fits", () => {
  assert.deepEqual(scaleBar(0.15, 80), { metres: 10, px: 10 / 0.15 });
  assert.deepEqual(scaleBar(1, 80), { metres: 50, px: 50 });
  assert.equal(scaleBar(0, 80), null);
  assert.equal(formatDistance(10), "10 m");
  assert.equal(formatDistance(0.5), "0.5 m");
  assert.equal(formatDistance(2000), "2 km");
});

test("stripAgents keeps the chosen agent and offers the next", () => {
  const frustums = [
    { ...camera, agent_id: "a" },
    { ...camera, agent_id: "a", sensor_id: "rear" },
    { ...camera, agent_id: "b" },
  ];
  assert.deepEqual(stripAgents(frustums, null), { agents: ["a", "b"], current: "a", next: "b" });
  assert.deepEqual(stripAgents(frustums, "b"), { agents: ["a", "b"], current: "b", next: "a" });
  assert.deepEqual(stripAgents(frustums, "gone"), { agents: ["a", "b"], current: "a", next: "b" });
  assert.deepEqual(stripAgents([camera], "car"), { agents: ["car"], current: "car", next: null });
  assert.deepEqual(stripAgents([], null), { agents: [], current: null, next: null });
});
