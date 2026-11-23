// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { test } from "node:test";

const spec = JSON.parse(readFileSync(new URL("./tour_spec.json", import.meta.url), "utf8")) as {
  screens: { key: string; capture_output: string }[];
};

const dir = new URL("../../public/tour/", import.meta.url);
const BUDGET = 8 * 1024 * 1024;

function mediaNames(): string[] {
  return readdirSync(dir).filter((name) => !name.startsWith("."));
}

function webpSize(bytes: Buffer): [number, number] {
  assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
  assert.equal(bytes.toString("ascii", 8, 12), "WEBP");
  const chunk = bytes.toString("ascii", 12, 16);
  if (chunk === "VP8X") {
    return [1 + bytes.readUIntLE(24, 3), 1 + bytes.readUIntLE(27, 3)];
  }
  if (chunk === "VP8 ") {
    return [bytes.readUInt16LE(26) & 0x3fff, bytes.readUInt16LE(28) & 0x3fff];
  }
  if (chunk === "VP8L") {
    const bits = bytes.readUInt32LE(21);
    return [(bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1];
  }
  throw new Error(`unsupported webp chunk ${chunk}`);
}

test("every tour screen has its file, no file is unused, and the folder stays under 8 MiB", () => {
  const names = new Set(mediaNames());
  const expected = new Set<string>();
  for (const screen of spec.screens) {
    const loop = screen.capture_output.includes("f*.png");
    if (loop) {
      assert.ok(names.has(`${screen.key}.mp4`), `${screen.key}.mp4`);
      assert.ok(names.has(`${screen.key}.webp`), `${screen.key}.webp`);
      expected.add(`${screen.key}.mp4`);
      expected.add(`${screen.key}.webp`);
    } else {
      assert.ok(names.has(`${screen.key}.webp`), `${screen.key}.webp`);
      expected.add(`${screen.key}.webp`);
    }
  }
  for (const name of names) {
    assert.equal(name.endsWith(".webm"), false, name);
    assert.ok(expected.has(name), `unused tour media file: ${name}`);
  }
  const total = mediaNames().reduce((sum, name) => sum + statSync(new URL(name, dir)).size, 0);
  assert.ok(total <= BUDGET, `tour media is ${total} bytes`);
});

test("loop posters match the 1800×1124 video", () => {
  for (const screen of spec.screens) {
    if (!screen.capture_output.includes("f*.png")) continue;
    const bytes = readFileSync(new URL(`${screen.key}.webp`, dir));
    assert.deepEqual(webpSize(bytes), [1800, 1124], screen.key);
  }
});
