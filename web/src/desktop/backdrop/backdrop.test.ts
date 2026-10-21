// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createBackdropScene, crossfadeWeights } from "./scene.ts";

test("scene generator is deterministic for a given seed", () => {
  const sceneA = createBackdropScene(42);
  const sceneB = createBackdropScene(42);

  assert.equal(sceneA.N, sceneB.N);
  assert.equal(sceneA.T_END, sceneB.T_END);
  assert.equal(sceneA.objects.length, sceneB.objects.length);

  for (let i = 0; i < sceneA.objects.length; i++) {
    const oA = sceneA.objects[i];
    const oB = sceneB.objects[i];
    assert.equal(oA.id, oB.id);
    assert.equal(oA.cat, oB.cat);
    assert.equal(oA.s0, oB.s0);
    assert.equal(oA.lat, oB.lat);
    assert.equal(oA.v, oB.v);
    assert.deepEqual(oA.pts, oB.pts);
  }

  // Check state at arbitrary times
  for (const t of [0.0, 9.5, 25.0, 50.0, 59.9]) {
    const stA = sceneA.stateAt(t);
    const stB = sceneB.stateAt(t);
    assert.deepEqual(stA.V, stB.V);
    assert.deepEqual(stA.U, stB.U);
    assert.equal(stA.objs.length, stB.objs.length);
    for (let j = 0; j < stA.objs.length; j++) {
      assert.equal(stA.objs[j].inRange, stB.objs[j].inRange);
      assert.equal(stA.objs[j].coop, stB.objs[j].coop);
      assert.equal(stA.objs[j].vDet, stB.objs[j].vDet);
      assert.equal(stA.objs[j].dDet, stB.objs[j].dDet);
    }
  }
});

test("crossfade weights sum to 1.0 and blend seamlessly at wrap", () => {
  const total = 60.0;
  const blend = 1.5;

  // Before blend window
  const before = crossfadeWeights(30.0, total, blend);
  assert.equal(before.isBlending, false);
  assert.equal(before.w1, 1.0);
  assert.equal(before.w2, 0.0);
  assert.equal(before.w1 + before.w2, 1.0);

  // Exactly at blend start (58.5)
  const atStart = crossfadeWeights(58.5, total, blend);
  assert.equal(atStart.isBlending, true);
  assert.equal(atStart.w1, 1.0);
  assert.equal(atStart.w2, 0.0);
  assert.equal(atStart.w1 + atStart.w2, 1.0);

  // Midway through blend (59.25)
  const mid = crossfadeWeights(59.25, total, blend);
  assert.equal(mid.isBlending, true);
  assert.ok(Math.abs(mid.w1 - 0.5) < 1e-6);
  assert.ok(Math.abs(mid.w2 - 0.5) < 1e-6);
  assert.ok(Math.abs(mid.w1 + mid.w2 - 1.0) < 1e-9);

  // Exactly at end of loop (59.999)
  const atEnd = crossfadeWeights(59.999, total, blend);
  assert.equal(atEnd.isBlending, true);
  assert.ok(Math.abs(atEnd.w1 + atEnd.w2 - 1.0) < 1e-9);

  // After wrap (60.0 mod 60.0 = 0.0)
  const wrapped = crossfadeWeights(60.0, total, blend);
  assert.equal(wrapped.isBlending, false);
  assert.equal(wrapped.t1, 0.0);
  assert.equal(wrapped.w1, 1.0);
  assert.equal(wrapped.w2, 0.0);
  assert.equal(wrapped.w1 + wrapped.w2, 1.0);

  // 100 sample points across the blend window
  for (let dt = 0; dt <= blend; dt += blend / 100) {
    const t = 58.5 + dt;
    const cf = crossfadeWeights(t, total, blend);
    assert.ok(
      Math.abs(cf.w1 + cf.w2 - 1.0) < 1e-9,
      `Weights failed to sum to 1 at t=${t}: w1=${cf.w1}, w2=${cf.w2}`,
    );
  }
});

test("3 chapters generate distinct cooperation scenarios", () => {
  const scene = createBackdropScene(42);

  // Chapter 1: bus occlusion (t ≈ 9.5 s)
  const st1 = scene.stateAt(9.5);
  const bus = st1.objs.find((r) => r.o.cat === "bus");
  assert.ok(bus, "Bus present in scene");
  const occludedPeds = st1.objs.filter((r) => r.o.cat === "pedestrian" && r.occ !== null);
  assert.ok(occludedPeds.length > 0, "Pedestrians occluded by bus");
  // Drone should detect occluded pedestrian
  const droneRecovered1 = occludedPeds.some((r) => r.coop === "drone");
  assert.ok(droneRecovered1, "Drone recovers bus-occluded pedestrian");

  // Chapter 2: crossing group seen first by drone (t ≈ 30 s)
  const st2 = scene.stateAt(30.0);
  const ch2Objs = st2.objs.filter((r) => r.o.chapter === 2);
  assert.ok(ch2Objs.length > 0, "Chapter 2 objects present");

  // Chapter 3: oncoming truck occluding cyclist (t ≈ 50 s)
  const st3 = scene.stateAt(50.0);
  const truck = st3.objs.find((r) => r.o.chapter === 3 && r.o.cat === "truck");
  assert.ok(truck, "Chapter 3 oncoming truck present");
});
