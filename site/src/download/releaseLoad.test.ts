// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { refreshRelease, type ReleaseStore } from "./releaseLoad.ts";
import type { ReleaseJson } from "./assets.ts";

function memory(): ReleaseStore {
  const kept = new Map<string, string>();
  return {
    getItem: (key) => kept.get(key) ?? null,
    setItem: (key, value) => {
      kept.set(key, value);
    },
  };
}

function release(tag: string, name: string): ReleaseJson {
  return {
    tag_name: tag,
    assets: [{ name, browser_download_url: `https://example.test/${name}` }],
  };
}

const EMBEDDED = release("v0.1.0", "Coperceive_0.1.0_aarch64.dmg");
const LIVE = release("v0.1.1", "Coperceive_0.1.1_aarch64.dmg");

test("a live v0.1.1 replaces an embedded v0.1.0", async () => {
  let calls = 0;
  const shown = await refreshRelease(EMBEDDED, {
    storage: memory(),
    fetch: async () => {
      calls += 1;
      return Response.json(LIVE);
    },
  });
  assert.equal(calls, 1);
  assert.equal(shown?.tag_name, "v0.1.1");
  assert.equal(shown?.assets[0]?.name, "Coperceive_0.1.1_aarch64.dmg");
});

test("a failed fetch keeps the embedded release", async () => {
  const shown = await refreshRelease(EMBEDDED, {
    storage: memory(),
    fetch: async () => {
      throw new Error("offline");
    },
  });
  assert.equal(shown?.tag_name, "v0.1.0");
  assert.equal(shown?.assets[0]?.name, "Coperceive_0.1.0_aarch64.dmg");
});

test("a rate-limited response keeps the embedded release", async () => {
  const shown = await refreshRelease(EMBEDDED, {
    storage: memory(),
    fetch: async () => new Response("rate limit exceeded", { status: 403 }),
  });
  assert.equal(shown?.tag_name, "v0.1.0");
  assert.equal(shown?.assets[0]?.name, "Coperceive_0.1.0_aarch64.dmg");
});

test("a cached live release is reused without another request", async () => {
  let calls = 0;
  const storage = memory();
  const fetchLive = async () => {
    calls += 1;
    return Response.json(LIVE);
  };
  await refreshRelease(EMBEDDED, { storage, fetch: fetchLive });
  const again = await refreshRelease(EMBEDDED, { storage, fetch: fetchLive });
  assert.equal(calls, 1);
  assert.equal(again?.tag_name, "v0.1.1");
});
