// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { createFrameCache, isAbortError } from "./cache.ts";

/**
 * The cache talks to `apiBase()` (which reads `document`) and `fetch`. Node has
 * no document, so this file installs one for its own process (node --test runs
 * each file in its own process) and stubs `fetch` per test.
 */
const scope = globalThis as unknown as {
  document?: { baseURI: string };
  fetch?: typeof fetch;
};

const BASE = "http://127.0.0.1:8000/";
scope.document = { baseURI: BASE };

function frameDocument(sceneId: string, timeUs: number) {
  return {
    scene_id: sceneId,
    time_us: timeUs,
    agents: [],
    boxes: [],
    frustums: [],
    point_sensors: [{ agent_id: "vehicle", sensor_id: "lidar_top", count: 8 }],
  };
}

type Pending = {
  url: string;
  signal: AbortSignal | undefined;
  respond: (response: Response) => void;
  fail: (error: unknown) => void;
};

/** A fetch stub whose responses the test makes one by one. */
function deferredFetch(): Pending[] {
  const pending: Pending[] = [];
  scope.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    return new Promise<Response>((resolve, reject) => {
      const signal = init?.signal ?? undefined;
      const entry: Pending = {
        url: String(input),
        signal,
        respond: (response) => resolve(response),
        fail: (error) => reject(error),
      };
      pending.push(entry);
      if (signal?.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
      }
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    });
  }) as typeof fetch;
  return pending;
}

function respondFrame(entry: Pending | undefined, sceneId: string, timeUs: number) {
  assert.ok(entry, "a frame request was made");
  entry.respond(new Response(JSON.stringify(frameDocument(sceneId, timeUs)), { status: 200 }));
}

function respondPoints(entry: Pending | undefined, bytes: number) {
  assert.ok(entry, "a points request was made");
  entry.respond(new Response(new ArrayBuffer(bytes), { status: 200 }));
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function withFetch(run: (pending: Pending[]) => Promise<void>): Promise<void> {
  const before = scope.fetch;
  const pending = deferredFetch();
  try {
    await run(pending);
  } finally {
    scope.fetch = before;
  }
}

test("frame documents and point sweeps are byte-accounted, hits do not double-count", async () => {
  await withFetch(async (fetch) => {
    const cache = createFrameCache();
    const body = frameDocument("scene", 100);
    const pendingFrame = cache.loadFrame("scene", 100, "token");
    assert.equal(fetch.length, 1);
    respondFrame(fetch[0], "scene", 100);
    await pendingFrame;
    const expectedFrameBytes = JSON.stringify(body).length;
    assert.equal(cache.stats.frame_bytes, expectedFrameBytes);
    assert.equal(cache.stats.total_bytes, expectedFrameBytes);
    assert.equal(cache.stats.frame_misses, 1);

    const points = cache.loadPoints("scene", 100, "vehicle", "lidar_top", "token");
    respondPoints(fetch[1], 64);
    await points;
    assert.equal(cache.stats.point_bytes, 64);
    assert.equal(cache.stats.total_bytes, expectedFrameBytes + 64);

    // A hit is served from the cache: no second request, no second charge.
    assert.deepEqual(await cache.loadFrame("scene", 100, "token"), body);
    assert.equal(fetch.length, 2);
    assert.equal(cache.stats.frame_hits, 1);
    assert.equal(cache.stats.frame_bytes, expectedFrameBytes);
  });
});

test("the memory limit evicts the oldest entries behind the playhead first", async () => {
  await withFetch(async (fetch) => {
    const body = frameDocument("scene", 100);
    const entryBytes = JSON.stringify(body).length;
    const cache = createFrameCache(entryBytes * 2);
    cache.setPlayhead(300);
    const load = async (timeUs: number) => {
      const pending = cache.loadFrame("scene", timeUs, "token");
      respondFrame(fetch[fetch.length - 1], "scene", timeUs);
      await pending;
    };
    await load(100);
    await load(200);
    assert.equal(cache.stats.total_bytes, entryBytes * 2);
    // A third entry does not fit: the farthest behind (100) goes first.
    await load(300);
    assert.equal(cache.stats.evicted, 1);
    assert.equal(cache.stats.total_bytes, entryBytes * 2);
    const requests = fetch.length;
    // 100 was evicted (a miss asks again); 300 stays (a hit asks nothing).
    const reload = cache.loadFrame("scene", 100, "token");
    respondFrame(fetch[fetch.length - 1], "scene", 100);
    assert.equal(fetch.length, requests + 1, "the evicted sample is re-requested");
    await reload;
    const requests2 = fetch.length;
    await cache.loadFrame("scene", 300, "token");
    assert.equal(fetch.length, requests2, "the playhead's sample is still cached");
  });
});

test("the sample on the playhead is not evicted while it has neighbours", async () => {
  await withFetch(async (fetch) => {
    const body = frameDocument("scene", 200);
    const entryBytes = JSON.stringify(body).length;
    const cache = createFrameCache(entryBytes);
    cache.setPlayhead(100);
    const load = async (timeUs: number) => {
      const pending = cache.loadFrame("scene", timeUs, "token");
      respondFrame(fetch[fetch.length - 1], "scene", timeUs);
      await pending;
    };
    await load(100); // the playhead's own sample
    await load(300); // ahead: evicted, not the current one
    assert.equal(cache.stats.evicted, 1);
    const requests = fetch.length;
    await cache.loadFrame("scene", 100, "token");
    assert.equal(fetch.length, requests, "the current sample is still cached");
  });
});

test("shrinking the memory limit evicts immediately", async () => {
  await withFetch(async (fetch) => {
    const body = frameDocument("scene", 100);
    const entryBytes = JSON.stringify(body).length;
    const cache = createFrameCache(entryBytes * 4);
    for (const timeUs of [100, 200, 300]) {
      const pending = cache.loadFrame("scene", timeUs, "token");
      respondFrame(fetch[fetch.length - 1], "scene", timeUs);
      await pending;
    }
    assert.equal(cache.stats.total_bytes, entryBytes * 3);
    cache.setPlayhead(300);
    cache.setMemoryLimit(entryBytes);
    assert.equal(cache.stats.total_bytes, entryBytes);
    assert.ok(cache.stats.evicted >= 2);
  });
});

test("a prefetcher's abort cancels its request, but a displaying caller keeps it alive", async () => {
  await withFetch(async (fetch) => {
    const cache = createFrameCache();
    const prefetch = new AbortController();
    const first = cache.loadPoints("scene", 100, "vehicle", "lidar_top", "token", {
      needed: false,
      signal: prefetch.signal,
    });
    assert.equal(fetch.length, 1);
    // A displaying caller joins the same in-flight request.
    const shown = cache.loadPoints("scene", 100, "vehicle", "lidar_top", "token");
    prefetch.abort();
    await settle();
    assert.equal(fetch[0].signal?.aborted, false, "the needed caller keeps the request alive");
    respondPoints(fetch[0], 32);
    assert.equal((await first).byteLength, 32);
    assert.equal((await shown).byteLength, 32);
    assert.equal(cache.stats.cancelled, 0);
    assert.equal(cache.stats.point_bytes, 32);
  });
});

test("an aborted prefetch request is cancelled, counted, and retried from scratch", async () => {
  await withFetch(async (fetch) => {
    const cache = createFrameCache();
    const prefetch = new AbortController();
    const pending = cache.loadFrame("scene", 100, "token", { needed: false, signal: prefetch.signal });
    const outcome = pending.then(
      () => null,
      (error: unknown) => error,
    );
    prefetch.abort();
    await settle();
    assert.equal(fetch[0].signal?.aborted, true);
    assert.equal(cache.stats.cancelled, 1);
    const error = await outcome;
    assert.ok(isAbortError(error), String(error));
    // A later display load of the same sample starts a new request.
    const shown = cache.loadFrame("scene", 100, "token");
    assert.equal(fetch.length, 2);
    respondFrame(fetch[1], "scene", 100);
    assert.equal((await shown).scene_id, "scene");
  });
});

test("cancelOutdated aborts only what nobody needs and that is not in the keep set", async () => {
  await withFetch(async (fetch) => {
    const cache = createFrameCache();
    // Discarded prefetches still count as handled activity: attach a catch.
    void cache.loadFrame("scene", 100, "token", { needed: false }).catch(() => undefined);
    void cache.loadFrame("scene", 200, "token", { needed: false }).catch(() => undefined);
    void cache.loadFrame("scene", 300, "token").catch(() => undefined);
    assert.equal(fetch.length, 3);
    const cancelled = cache.cancelOutdated(new Set(["scene|100"]));
    assert.equal(cancelled, 1);
    const aborted = fetch.filter((entry) => entry.signal?.aborted).map((entry) => entry.url);
    assert.equal(aborted.length, 1);
    assert.ok(aborted[0]?.includes("time_us=200"), aborted[0]);
    assert.equal(cache.stats.cancelled, 1);
  });
});

test("clear aborts everything and forgets the bytes; the estimate uses the cached average", async () => {
  await withFetch(async (fetch) => {
    const cache = createFrameCache();
    const pending = cache.loadFrame("scene", 100, "token");
    const points = cache.loadPoints("scene", 100, "vehicle", "lidar_top", "token").catch(() => undefined);
    respondFrame(fetch[0], "scene", 100);
    respondPoints(fetch[1], 64);
    await pending;
    await points;
    await settle();
    assert.equal(cache.estimateFrameBytes(), Math.round(cache.stats.total_bytes));
    cache.clear();
    assert.equal(cache.stats.total_bytes, 0);
    assert.equal(cache.bytes(), 0);
    // The cache starts from scratch after a scene change.
    const again = cache.loadFrame("scene", 200, "token");
    assert.equal(fetch.length, 3);
    respondFrame(fetch[2], "scene", 200);
    await again;
    assert.ok(cache.stats.total_bytes > 0);
  });
});
