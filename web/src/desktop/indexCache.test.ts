// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  _resetIndexCache,
  forgetCachedIndex,
  indexIsUnchanged,
  indexRefreshMessage,
  readCachedIndex,
  shouldCacheIndex,
  shouldReplaceIndex,
  writeCachedIndex,
} from "./indexCache.ts";
import type { ClusterIndexDocument } from "./clusterDatasets.ts";

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
  clear(): void {
    this.values.clear();
  }
}

function useStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = storage;
  _resetIndexCache();
  return storage;
}

function index(overrides: Partial<ClusterIndexDocument> = {}): ClusterIndexDocument {
  return {
    schema: "coperceive-cluster-index-1",
    adapter: "opv2v",
    raw_root: "/datasets/opencood/opv2v",
    split: "train",
    params: { split: "train" },
    licence: null,
    fingerprint: { algorithm: "sha1-mtime-size-v1", digest: "aaa", entries: 120, truncated: false },
    cache: { relative_path: "coperceive-cache/opv2v/abc/train" },
    scene_count: 2,
    scenes: [
      { scene_id: "train_0000", sample_count: 4, duration_s: 0.2, agent_count: 3, sensors: { sensors: 2, camera_samples: 8, lidar_samples: 4 } },
      { scene_id: "train_0001", sample_count: 4, duration_s: 0.2, agent_count: 3, sensors: { sensors: 2, camera_samples: 8, lidar_samples: 4 } },
    ],
    errors: [],
    truncated: false,
    elapsed_ms: 45_000,
    ...overrides,
  };
}

const AT = ["profile-1", "opv2v", "/datasets/opencood/opv2v", "train"] as const;

test("a dataset's index is reused at the next open, in this session and after a restart", () => {
  useStorage();
  assert.strictEqual(readCachedIndex(...AT), null);
  const document = index();
  writeCachedIndex(...AT, document, 1_700_000_000);
  assert.deepStrictEqual(readCachedIndex(...AT), { document, saved_unix: 1_700_000_000 });

  // A fresh process only has localStorage: the same document comes back.
  _resetIndexCache();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage.clear();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage.setItem(
    "coperceive-cluster-index-cache",
    JSON.stringify([
      { profile_id: AT[0], adapter: AT[1], raw_root: AT[2], split: AT[3], saved_unix: 1_700_000_000, document },
    ]),
  );
  assert.deepStrictEqual(readCachedIndex(...AT)?.document, document);
});

test("another server, adapter, root or split never reuses a cached index", () => {
  useStorage();
  const document = index();
  writeCachedIndex(...AT, document);
  assert.strictEqual(readCachedIndex("profile-2", AT[1], AT[2], AT[3]), null);
  assert.strictEqual(readCachedIndex(AT[0], "v2xset", AT[2], AT[3]), null);
  assert.strictEqual(readCachedIndex(AT[0], AT[1], "/datasets/other/opv2v", AT[3]), null);
  assert.strictEqual(readCachedIndex(AT[0], AT[1], AT[2], "test"), null);
  // ...and the split-less Griffin cache does not answer for split "all".
  writeCachedIndex(AT[0], "griffin", "/datasets/griffin", null, index({ adapter: "griffin", raw_root: "/datasets/griffin", split: null }));
  assert.strictEqual(readCachedIndex(AT[0], "griffin", "/datasets/griffin", null)?.document.adapter, "griffin");
  assert.strictEqual(readCachedIndex(AT[0], "griffin", "/datasets/griffin", "25m"), null);
});

test("a stored index for a different document is never trusted", () => {
  useStorage();
  const storage = (globalThis as unknown as { localStorage: MemoryStorage }).localStorage;
  const foreign = index({ schema: "something-else" });
  storage.setItem(
    "coperceive-cluster-index-cache",
    JSON.stringify([{ profile_id: AT[0], adapter: AT[1], raw_root: AT[2], split: AT[3], saved_unix: 1, document: foreign }]),
  );
  assert.strictEqual(readCachedIndex(...AT), null);
});

test("a retry forgets the cached index so the next open indexes again", () => {
  useStorage();
  writeCachedIndex(...AT, index());
  forgetCachedIndex(...AT);
  assert.strictEqual(readCachedIndex(...AT), null);
});

test("the sha1-mtime-size fingerprint decides whether the raw tree moved", () => {
  const cached = index();
  assert.strictEqual(indexIsUnchanged(cached, index()), true);
  assert.strictEqual(indexIsUnchanged(cached, index({ fingerprint: { ...cached.fingerprint, digest: "bbb" } })), false);
  assert.strictEqual(indexIsUnchanged(cached, index({ fingerprint: { ...cached.fingerprint, entries: 121 } })), false);
  assert.strictEqual(indexIsUnchanged(cached, index({ fingerprint: { ...cached.fingerprint, truncated: true } })), false);
  // An unchanged walk says nothing and changes no view.
  assert.strictEqual(indexRefreshMessage(cached, index()), null);
});

test("a changed tree updates the view with a plain sentence", () => {
  const cached = index();
  const sameCount = index({ fingerprint: { ...cached.fingerprint, digest: "bbb" } });
  assert.match(String(indexRefreshMessage(cached, sameCount)), /raw files changed/);
  const grown = index({
    fingerprint: { ...cached.fingerprint, digest: "bbb" },
    scene_count: 3,
    scenes: [...(cached.scenes ?? []), { scene_id: "train_0002", sample_count: 4, duration_s: 0.2, agent_count: 3, sensors: { sensors: 2, camera_samples: 8, lidar_samples: 4 } }],
  });
  assert.match(String(indexRefreshMessage(cached, grown)), /3 scenes now/);
});


test("one bad walk does not blank the scene list already on screen", () => {
  const cached = index();
  const unreadable = index({ scene_count: 0, scenes: [], errors: ["train_0000: no samples found with streams.poses"] });
  assert.strictEqual(shouldReplaceIndex(cached, unreadable), false);
  assert.strictEqual(shouldReplaceIndex(null, unreadable), true);
  assert.strictEqual(shouldReplaceIndex(cached, index({ scene_count: 0, scenes: [], errors: [] })), true);
});

test("an empty index with read errors is not remembered", () => {
  assert.strictEqual(shouldCacheIndex(index({ scene_count: 0, scenes: [], errors: ["bad"] })), false);
  assert.strictEqual(shouldCacheIndex(index({ scene_count: 0, scenes: [], errors: [] })), true);
  assert.strictEqual(shouldCacheIndex(index()), true);
});
