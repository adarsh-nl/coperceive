// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { neighbourScene } from "./sceneNav.ts";

const order = ["a", "b", "c", "d", "e"];

test("neighbourScene moves to the nearest prepared scene and names the skipped ones", () => {
  const ready = new Set(["a", "c", "e"]);
  assert.deepStrictEqual(neighbourScene(order, ready, "a", 1), { scene: "c", skipped: ["b"] });
  assert.deepStrictEqual(neighbourScene(order, ready, "c", -1), { scene: "a", skipped: ["b"] });
  assert.deepStrictEqual(neighbourScene(order, ready, "e", 1), { scene: null, skipped: [] });
  assert.deepStrictEqual(neighbourScene(order, new Set(["c"]), "c", 1), { scene: null, skipped: ["d", "e"] });
  assert.deepStrictEqual(neighbourScene(order, ready, "zz", 1), { scene: null, skipped: [] });
});
