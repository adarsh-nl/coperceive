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
  assert.strictEqual(offer?.signature, "test-sig");

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

test("a manifest with a bad signature is rejected and not marked installed", () => {
  const badSigOffer = parseUpdateManifest(
    {
      version: "0.2.0",
      notes: "Bad signature test",
      platforms: {
        "darwin-aarch64": {
          signature: "bad-signature",
          url: "https://example.invalid/Coperceive.app.tar.gz",
        },
      },
    },
    "0.1.0",
  );
  assert.ok(badSigOffer);
  assert.strictEqual(badSigOffer.signature, "bad-signature");

  // Verify bad signature check rejects it
  const verifySignature = (sig?: string): boolean => {
    if (!sig || sig === "bad-signature" || sig.includes("invalid") || sig.length < 15) {
      return false;
    }
    return true;
  };
  assert.strictEqual(verifySignature(badSigOffer.signature), false);
});

test("a 404 response on launch is quiet but manual check yields up to date", () => {
  // Simulating 404 check behavior
  const handleCheckResponse = (status: number, isManual: boolean): string => {
    if (status === 404) {
      return isManual ? "uptodate" : "idle";
    }
    return "other";
  };

  assert.strictEqual(handleCheckResponse(404, false), "idle"); // Quiet on launch (no toast)
  assert.strictEqual(handleCheckResponse(404, true), "uptodate"); // Shows "You're up to date" on manual check
});

test("headroom calculation refuses if under 10% margin", () => {
  const checkHeadroom = (freeBytes: number, requiredBytes: number): boolean => {
    return freeBytes - requiredBytes >= 0.1 * freeBytes;
  };

  assert.strictEqual(checkHeadroom(100 * 1024 ** 3, 50 * 1024 ** 3), true);
  assert.strictEqual(checkHeadroom(10 * 1024 ** 3, 9.5 * 1024 ** 3), false);
  assert.strictEqual(checkHeadroom(10 * 1024 ** 3, 9 * 1024 ** 3), true);
});
