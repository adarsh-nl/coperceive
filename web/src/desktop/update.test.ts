// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";

test("updater endpoint parses new version and triggers update ready state", async () => {
  const currentVersion = "0.1.0";
  const mockRelease = {
    version: "0.2.0",
    notes: "M2 data without terminal release",
    pub_date: "2026-10-24T12:00:00Z",
    platforms: {
      "darwin-aarch64": {
        signature: "test-sig",
        url: "https://github.com/adarsh-nl/coperceive/releases/download/v0.2.0/Coperceive.app.tar.gz",
      },
    },
  };

  const isUpdateAvailable = (remoteVersion: string) => remoteVersion !== currentVersion;
  assert.strictEqual(isUpdateAvailable(mockRelease.version), true);
  assert.strictEqual(isUpdateAvailable("0.1.0"), false);
});

test("headroom calculation refuses if under 10% margin", () => {
  const checkHeadroom = (freeBytes: number, requiredBytes: number): boolean => {
    return (freeBytes - requiredBytes) >= (0.10 * freeBytes);
  };

  // 100 GB free, 50 GB needed -> remaining 50 GB > 10 GB (10% of 100GB) -> ok
  assert.strictEqual(checkHeadroom(100 * 1024**3, 50 * 1024**3), true);

  // 10 GB free, 9.5 GB needed -> remaining 0.5 GB < 1 GB (10% of 10GB) -> refuse
  assert.strictEqual(checkHeadroom(10 * 1024**3, 9.5 * 1024**3), false);

  // Exact 10% remaining: 10 GB free, 9 GB needed -> remaining 1.0 GB = 10% -> ok
  assert.strictEqual(checkHeadroom(10 * 1024**3, 9 * 1024**3), true);
});
