// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { otherSeenLabel, seenDash, seenKind } from "./seenBy.ts";
import type { FrameBox } from "./types.ts";

function box(agent: string, track: string | null): FrameBox {
  return { agent_id: agent, track_id: track, lines: [] };
}

test("a shared track id is both, one agent is that side, and a missing id is unknown", () => {
  const boxes = [box("vehicle", "7"), box("rsu", "7"), box("vehicle", "8"), box("rsu", "9"), box("vehicle", null)];
  assert.equal(seenKind(boxes[0]!, boxes, "vehicle"), "both");
  assert.equal(seenKind(boxes[1]!, boxes, "vehicle"), "both");
  assert.equal(seenKind(boxes[2]!, boxes, "vehicle"), "ego");
  assert.equal(seenKind(boxes[3]!, boxes, "vehicle"), "other");
  assert.equal(seenKind(boxes[4]!, boxes, "vehicle"), "unknown");
});

test("the other agent's word follows its type, and the line pattern is not colour alone", () => {
  assert.equal(otherSeenLabel(["vehicle", "infrastructure"]), "RSU only");
  assert.equal(otherSeenLabel(["vehicle", "uav"]), "Drone only");
  assert.equal(otherSeenLabel(["vehicle", "vehicle"]), "Other vehicle only");
  assert.equal(seenDash("both"), "solid");
  assert.equal(seenDash("ego"), "dashed");
  assert.equal(seenDash("other"), "dotted");
  assert.equal(seenDash("unknown"), "solid");
});
