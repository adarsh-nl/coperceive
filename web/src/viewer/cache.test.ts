// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { POINT_BYTE_LIMIT, rememberPointBuffer } from "./cache.ts";

test("rememberPointBuffer evicts oldest entries once byteLimit is exceeded", () => {
  const map = new Map<string, ArrayBuffer>();
  let total = 0;
  total = rememberPointBuffer(map, "a", new ArrayBuffer(400), total, 1000);
  total = rememberPointBuffer(map, "b", new ArrayBuffer(400), total, 1000);
  total = rememberPointBuffer(map, "c", new ArrayBuffer(400), total, 1000);
  assert.equal(total, 800);
  assert.deepEqual([...map.keys()], ["b", "c"]);

  // Re-touching "b" moves it to MRU so "c" is evicted next
  total = rememberPointBuffer(map, "b", map.get("b")!, total, 1000);
  total = rememberPointBuffer(map, "d", new ArrayBuffer(400), total, 1000);
  assert.equal(total, 800);
  assert.deepEqual([...map.keys()], ["b", "d"]);
});

test("POINT_BYTE_LIMIT holds all 300 demo point buffers (~25 KB each) without eviction", () => {
  const map = new Map<string, ArrayBuffer>();
  let total = 0;
  for (let i = 0; i < 300; i += 1) {
    total = rememberPointBuffer(map, `demo|${i}`, new ArrayBuffer(25 * 1024), total);
  }
  assert.equal(map.size, 300);
  assert.ok(total < POINT_BYTE_LIMIT);
});
