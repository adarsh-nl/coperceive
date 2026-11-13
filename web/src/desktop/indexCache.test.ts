// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import {
  _resetIndexCache,
  _setMaxCacheBytesForTesting,
  forgetCachedIndex,
  indexCacheBytes,
  indexCacheEntryCount,
  indexChange,
  indexRefreshMessage,
  pruneIndexCache,
  readCachedIndex,
  shouldCacheIndex,
  shouldReplaceIndex,
  writeCachedIndex,
} from "./indexCache.ts";
import type { ClusterIndexDocument } from "./clusterDatasets.ts";

class MemoryStorage {
  private values = new Map<string, string>();
  /** When set, setItem fails above this many characters (a full browser quota). */
  maxChars = Number.POSITIVE_INFINITY;
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    if (value.length > this.maxChars) throw new Error("QuotaExceededError");
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
  clear(): void {
    this.values.clear();
  }
}

function useStorage(maxChars = Number.POSITIVE_INFINITY): MemoryStorage {
  const storage = new MemoryStorage();
  storage.maxChars = maxChars;
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

function storedRaw(): string {
  return ((globalThis as unknown as { localStorage: MemoryStorage }).localStorage.getItem("coperceive-cluster-index-cache")) ?? "[]";
}

test("a dataset's index is reused at the next open, in this session and after a restart", () => {
  useStorage();
  assert.strictEqual(readCachedIndex(...AT), null);
  const document = index();
  assert.strictEqual(writeCachedIndex(...AT, document, 1_700_000_000), true);
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
  writeCachedIndex(AT[0], "griffin", "/datasets/griffin", null, index({ adapter: "griffin", raw_root: "/datasets/griffin", split: null }));
  assert.strictEqual(readCachedIndex(AT[0], "griffin", "/datasets/griffin", null)?.document.adapter, "griffin");
  assert.strictEqual(readCachedIndex(AT[0], "griffin", "/datasets/griffin", "25m"), null);
});

test("a malformed cached document is dropped, not handed to the view", () => {
  const storage = useStorage();
  const cases: unknown[] = [
    { ...index(), scenes: "not a list" },
    { ...index(), errors: [1, 2] },
    { ...index(), scenes: [{ scene_id: 5 }] },
    { ...index(), fingerprint: { algorithm: "sha1-mtime-size-v1", digest: 5 } },
    { ...index(), cache: null },
    { ...index(), schema: "something-else" },
  ];
  for (const document of cases) {
    _resetIndexCache();
    storage.setItem(
      "coperceive-cluster-index-cache",
      JSON.stringify([{ profile_id: AT[0], adapter: AT[1], raw_root: AT[2], split: AT[3], saved_unix: 1, document }]),
    );
    assert.strictEqual(readCachedIndex(...AT), null);
    assert.strictEqual(indexCacheEntryCount(), 0);
  }
  // A valid entry next to a broken one still reads; the broken one is pruned.
  _resetIndexCache();
  storage.setItem(
    "coperceive-cluster-index-cache",
    JSON.stringify([
      { profile_id: "gone", adapter: AT[1], raw_root: AT[2], split: AT[3], saved_unix: 1, document: { nope: true } },
      { profile_id: AT[0], adapter: AT[1], raw_root: AT[2], split: AT[3], saved_unix: 2, document: index() },
    ]),
  );
  assert.deepStrictEqual(readCachedIndex(...AT)?.document, index());
  assert.strictEqual(indexCacheEntryCount(), 1);
});

test("the persisted cache is capped in bytes, oldest entries first", () => {
  const storage = useStorage();
  _setMaxCacheBytesForTesting(6_000);
  const wide = index({
    scenes: Array.from({ length: 30 }, (_, i) => ({
      scene_id: `train_${String(i).padStart(4, "0")}`,
      sample_count: 4,
      duration_s: 0.2,
      agent_count: 3,
      sensors: { sensors: 2, camera_samples: 8, lidar_samples: 4 },
    })),
    scene_count: 30,
  });
  for (let i = 0; i < 10; i += 1) {
    assert.strictEqual(
      writeCachedIndex(AT[0], AT[1], AT[2], `split-${i}`, { ...wide, split: `split-${i}` }, 1_700_000_000 + i),
      true,
    );
  }
  assert.ok(indexCacheBytes() <= 6_000, `cache is ${indexCacheBytes()} bytes`);
  assert.strictEqual(indexCacheBytes(), storedRaw().length);
  assert.ok(indexCacheEntryCount() > 0);
  assert.strictEqual(readCachedIndex(AT[0], AT[1], AT[2], "split-0"), null);
  assert.notStrictEqual(readCachedIndex(AT[0], AT[1], AT[2], "split-9"), null);
  assert.ok(storage.getItem("coperceive-cluster-index-cache"));
});

test("a full quota evicts the oldest entry and retries instead of failing silently", () => {
  const storage = useStorage(2_000);
  const warnings: string[] = [];
  const originalWarn = console.warn;
  console.warn = (...parts: unknown[]) => warnings.push(parts.join(" "));
  try {
    assert.strictEqual(writeCachedIndex(...AT, index(), 1_700_000_000), true);
    const later = writeCachedIndex(AT[0], AT[1], AT[2], "test", {
      ...index({ split: "test" }),
      // Big enough that both documents together exceed the fake quota.
      errors: Array.from({ length: 8 }, (_, i) => `scene_${i}: unreadable record ${"x".repeat(40)}`),
    }, 1_700_000_100);
    assert.strictEqual(later, true);
    assert.ok(storedRaw().length <= 2_000, `stored ${storedRaw().length} chars`);
    assert.strictEqual(readCachedIndex(AT[0], AT[1], AT[2], "train"), null);
    assert.notStrictEqual(readCachedIndex(AT[0], AT[1], AT[2], "test"), null);
  } finally {
    console.warn = originalWarn;
  }
  assert.deepStrictEqual(warnings, []);

  // Storage that can never fit anything keeps the document in memory and warns.
  useStorage(1);
  console.warn = (...parts: unknown[]) => warnings.push(parts.join(" "));
  try {
    assert.strictEqual(writeCachedIndex(...AT, index()), false);
    assert.notStrictEqual(readCachedIndex(...AT), null);
  } finally {
    console.warn = originalWarn;
  }
  assert.strictEqual(warnings.length, 1);
  assert.match(warnings[0], /could not be saved/);
});

test("deleting a server profile drops its cached indexes", () => {
  useStorage();
  writeCachedIndex("profile-1", "opv2v", "/d/a", "train", index({ raw_root: "/d/a" }));
  writeCachedIndex("profile-2", "opv2v", "/d/b", "train", index({ raw_root: "/d/b" }));
  assert.strictEqual(pruneIndexCache(["profile-2"]), 1);
  assert.strictEqual(readCachedIndex("profile-1", "opv2v", "/d/a", "train"), null);
  assert.notStrictEqual(readCachedIndex("profile-2", "opv2v", "/d/b", "train"), null);
  assert.strictEqual(indexCacheEntryCount(), 1);
  assert.strictEqual(pruneIndexCache(["profile-2"]), 0);
});

test("a retry forgets the cached index so the next open indexes again", () => {
  useStorage();
  writeCachedIndex(...AT, index());
  forgetCachedIndex(...AT);
  assert.strictEqual(readCachedIndex(...AT), null);
});

test("only a completed metadata walk that really differs counts as changed", () => {
  const cached = index();
  assert.strictEqual(indexChange(cached, index()), "unchanged");
  assert.strictEqual(indexRefreshMessage(cached, index()), null);
  // A capped walk cannot prove "unchanged", but it must not claim a change:
  // the real OPV2V train tree hits the 200,000-entry/30 s cap routinely.
  const capped = index({ fingerprint: { ...cached.fingerprint, truncated: true } });
  assert.strictEqual(indexChange(cached, capped), "uncertain");
  assert.strictEqual(indexRefreshMessage(cached, capped), null);
  // A capped walk whose digest differs but whose scene list is identical may
  // just have been cut at a different point.
  const cappedDiff = index({ fingerprint: { ...cached.fingerprint, digest: "bbb", truncated: true } });
  assert.strictEqual(indexChange(cached, cappedDiff), "uncertain");
  assert.strictEqual(indexRefreshMessage(cached, cappedDiff), null);
  // A capped walk that lists a different scene is a real change anyway.
  const cappedGrown = index({
    fingerprint: { ...cached.fingerprint, digest: "bbb", truncated: true },
    scene_count: 3,
    scenes: [...(cached.scenes ?? []), { scene_id: "train_0002", sample_count: 4, duration_s: 0.2, agent_count: 3, sensors: { sensors: 2, camera_samples: 8, lidar_samples: 4 } }],
  });
  assert.strictEqual(indexChange(cached, cappedGrown), "changed");
  assert.match(String(indexRefreshMessage(cached, cappedGrown)), /3 scenes now/);
  // A complete walk with a different digest is a real change.
  const grown = index({
    fingerprint: { ...cached.fingerprint, digest: "bbb" },
    scene_count: 3,
    scenes: [...(cached.scenes ?? []), { scene_id: "train_0002", sample_count: 4, duration_s: 0.2, agent_count: 3, sensors: { sensors: 2, camera_samples: 8, lidar_samples: 4 } }],
  });
  assert.strictEqual(indexChange(cached, grown), "changed");
  assert.match(String(indexRefreshMessage(cached, grown)), /3 scenes now/);
  const sameCount = index({ fingerprint: { ...cached.fingerprint, digest: "bbb" } });
  assert.match(String(indexRefreshMessage(cached, sameCount)), /raw files changed/);
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
