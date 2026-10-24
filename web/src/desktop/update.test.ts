// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { compareVersions, parseUpdateManifest } from "./updateManifest.ts";

test("version compare treats a higher numeric tuple as newer", () => {
  assert.ok(compareVersions("0.1.0", "0.2.0") > 0);
  assert.ok(compareVersions("0.1.9", "0.1.10") > 0);
  assert.strictEqual(compareVersions("0.1.0", "0.1.0"), 0);
  assert.ok(compareVersions("0.2.0", "0.1.0") < 0);
  assert.ok(compareVersions("v0.1.0", "0.1.1") > 0);
});

test("manifest parsing requires a newer darwin-aarch64 build", () => {
  const offer = parseUpdateManifest(
    {
      version: "0.2.0",
      notes: "Maps and library",
      platforms: {
        "darwin-aarch64": {
          signature: "test-sig",
          url: "https://example.invalid/Coperceive.app.tar.gz",
        },
      },
    },
    "0.1.0",
  );
  assert.ok(offer);
  assert.strictEqual(offer?.version, "0.2.0");
  assert.strictEqual(offer?.notes, "Maps and library");

  assert.strictEqual(
    parseUpdateManifest(
      {
        version: "0.1.0",
        platforms: { "darwin-aarch64": { url: "https://example.invalid/a" } },
      },
      "0.1.0",
    ),
    null,
  );
  assert.strictEqual(parseUpdateManifest({ version: "9.0.0" }, "0.1.0"), null);
  assert.strictEqual(parseUpdateManifest("nope", "0.1.0"), null);
});

test("headroom calculation refuses if under 10% margin", () => {
  const checkHeadroom = (freeBytes: number, requiredBytes: number): boolean => {
    return freeBytes - requiredBytes >= 0.1 * freeBytes;
  };

  assert.strictEqual(checkHeadroom(100 * 1024 ** 3, 50 * 1024 ** 3), true);
  assert.strictEqual(checkHeadroom(10 * 1024 ** 3, 9.5 * 1024 ** 3), false);
  assert.strictEqual(checkHeadroom(10 * 1024 ** 3, 9 * 1024 ** 3), true);
});
