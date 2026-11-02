// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { errorDetails, errorText, hasMoreDetails } from "./errors.ts";

test("a Tauri string rejection shows the backend's first line", () => {
  const rejection = "Could not read the dataset catalog: catalog.json is not valid JSON\n  at line 3";
  assert.equal(errorText(rejection), "Could not read the dataset catalog: catalog.json is not valid JSON");
  assert.equal(errorDetails(rejection), rejection);
  assert.equal(hasMoreDetails(rejection), true);
});

test("Errors and error-shaped objects read the same way", () => {
  assert.equal(errorText(new Error("Controller request failed (500): boom")), "Controller request failed (500): boom");
  assert.equal(errorText({ message: "from an object" }), "from an object");
  assert.equal(errorText({ error: "from a document" }), "from a document");
  assert.equal(errorText("Error: prefixed"), "prefixed");
  assert.equal(hasMoreDetails("one line"), false);
});

test("an empty or unknown rejection falls back", () => {
  assert.equal(errorText(undefined, "Failed to load catalog."), "Failed to load catalog.");
  assert.equal(errorText("   \n  "), "Something went wrong. Try again.");
  assert.equal(errorText(42), "Something went wrong. Try again.");
});
