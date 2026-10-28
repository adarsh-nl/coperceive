// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { apiBase } from "./client.ts";

test("apiBase is never a tauri:// URL when connected", () => {
  const originalDocument = globalThis.document;
  const originalSessionStorage = globalThis.sessionStorage;

  try {
    // 1. In browser or server webview where page is served from http://127.0.0.1:8731/
    globalThis.document = { baseURI: "http://127.0.0.1:8731/" } as unknown as Document;
    globalThis.sessionStorage = {
      getItem: (key: string) => (key === "coperceive-token" ? "secret-token" : null),
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
      key: () => null,
      length: 0,
    } as unknown as Storage;

    const base1 = apiBase();
    assert.strictEqual(base1, "http://127.0.0.1:8731");
    assert.ok(!base1.startsWith("tauri://"), "apiBase must not be tauri:// on http origin");

    // 2. In Tauri environment where document.baseURI is tauri://localhost/ but connected
    // with session origin set (e.g. from start_local)
    globalThis.document = { baseURI: "tauri://localhost/" } as unknown as Document;
    globalThis.sessionStorage = {
      getItem: (key: string) => {
        if (key === "coperceive-origin") return "http://127.0.0.1:9123";
        if (key === "coperceive-token") return "secret-token";
        return null;
      },
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
      key: () => null,
      length: 0,
    } as unknown as Storage;

    const base2 = apiBase();
    assert.strictEqual(base2, "http://127.0.0.1:9123");
    assert.ok(!base2.startsWith("tauri://"), "apiBase must not be tauri:// when connected in Tauri");

    // 3. When connected without trailing slash
    globalThis.sessionStorage = {
      getItem: (key: string) => (key === "coperceive-origin" ? "http://127.0.0.1:9123/" : null),
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
      key: () => null,
      length: 0,
    } as unknown as Storage;

    const base3 = apiBase();
    assert.strictEqual(base3, "http://127.0.0.1:9123");
    assert.ok(!base3.startsWith("tauri://"));
  } finally {
    globalThis.document = originalDocument;
    globalThis.sessionStorage = originalSessionStorage;
  }
});
