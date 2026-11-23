// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  advance,
  chapterProgress,
  clickDelay,
  clock,
  eagerScreenKeys,
  frameAt,
  placeCamera,
  range,
  seekChapter,
  timeline,
  type Step,
} from "./engine.ts";

const spec = JSON.parse(readFileSync(new URL("./tour_spec.json", import.meta.url), "utf8")) as {
  chapters: string[];
  steps: Step[];
};

const steps = spec.steps;
const tl = timeline(steps);

test("timeline places every step end to end", () => {
  assert.equal(tl[0].t0, 0);
  assert.equal(tl[0].t1, steps[0].dur);
  for (let i = 1; i < tl.length; i++) {
    assert.equal(tl[i].t0, tl[i - 1].t1);
    assert.ok(Math.abs(tl[i].t1 - tl[i].t0 - steps[i].dur) < 1e-9);
  }
  assert.equal(tl.length, steps.length);
});

test("click delay is min(1.3, 0.6*dur)", () => {
  assert.equal(clickDelay(1), 0.6);
  assert.equal(clickDelay(3), 1.3);
  assert.equal(clickDelay(2.4), 1.3);
});

test("camera clamps and keeps a reachable pointer between 6% and 90%", () => {
  const clamped = placeCamera({ x: 0, y: 0, s: 2 }, { x: 40, y: 40 });
  assert.ok(clamped.tx <= 0 && clamped.tx >= -100);
  assert.ok(clamped.px >= 6 && clamped.px <= 90);
  assert.ok(clamped.py >= 6 && clamped.py <= 90);

  const pulled = placeCamera({ x: 10, y: 10, s: 1.8 }, { x: 90, y: 8 });
  assert.ok(pulled.px <= 90 && pulled.px >= 6, `px ${pulled.px}`);
  assert.ok(pulled.py <= 90 && pulled.py >= 6, `py ${pulled.py}`);
});

test("pointer stays within 4–92% at every half second", () => {
  const total = tl[tl.length - 1].t1;
  for (let t = 0; t < total; t += 0.5) {
    const frame = frameAt(steps, t);
    assert.ok(frame.px >= 4 && frame.px <= 92, `t=${t} px=${frame.px}`);
    assert.ok(frame.py >= 4 && frame.py <= 92, `t=${t} py=${frame.py}`);
  }
});

test("seeking a chapter lands inside that chapter", () => {
  for (let chapter = 0; chapter < spec.chapters.length; chapter++) {
    const t = seekChapter(tl, chapter);
    const frame = frameAt(steps, t);
    assert.equal(frame.step.ch, chapter);
    const [start, end] = range(tl, chapter + 1);
    assert.ok(t >= start && t < end);
    assert.equal(chapterProgress(tl, chapter, start), 0);
    assert.equal(chapterProgress(tl, chapter, end), 1);
    const mid = start + (end - start) / 2;
    const fill = chapterProgress(tl, chapter, mid);
    assert.ok(fill > 0 && fill < 1);
  }
});

test("a chapter loops inside its own range", () => {
  const span = range(tl, 3);
  assert.ok(span[1] > span[0]);
  assert.equal(advance(span[0] + 0.4, 0.1, span), span[0] + 0.5);
  assert.equal(advance(span[1] - 0.05, 0.05, span), span[0]);
  assert.equal(advance(span[0] - 0.2, 0, span), span[0]);
  const outside = frameAt(steps, span[0] + 0.2);
  assert.equal(outside.step.ch, 2);
});

test("a shared caption stays mounted from the first step", () => {
  let found = false;
  for (let i = 1; i < tl.length; i++) {
    const title = tl[i].cap?.[0];
    if (!title || tl[i - 1].cap?.[0] !== title) continue;
    found = true;
    const frame = frameAt(steps, tl[i].t0 + Math.min(0.05, tl[i].dur / 2));
    let expected = i;
    while (expected > 0 && tl[expected - 1].cap?.[0] === title) expected -= 1;
    assert.equal(frame.capIdx, expected);
    assert.equal(tl[frame.capIdx].cap?.[0], title);
    assert.equal(frame.step.cap?.[0], title);
  }
  assert.equal(found, true);
});

test("opening chapter media is the library and the open-folder still", () => {
  assert.deepEqual([...eagerScreenKeys(steps)], ["library", "openfolder"]);
});

test("clock is m:ss", () => {
  assert.equal(clock(0), "0:00");
  assert.equal(clock(65.9), "1:05");
  assert.equal(clock(tl[tl.length - 1].t1), clock(tl[tl.length - 1].t1));
});
