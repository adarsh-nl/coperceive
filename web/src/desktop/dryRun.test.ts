// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { dryRunFailureText } from "./dryRun.ts";

test("the dry-run card keeps the real cause and one next step", () => {
  const network = dryRunFailureText(
    new Error(
      "Traceback (most recent call last):\nRuntimeError: Could not read the Griffin file sizes from the dataset host (TimeoutError: timed out). Next: check the network or VPN connection, then try again.",
    ),
  );
  assert.ok(network.includes("TimeoutError: timed out"), network);
  assert.ok(network.includes("Next: check the network or VPN"), network);
  assert.ok(!network.includes("Could not check the size. Choose"), network);

  const missing = dryRunFailureText(new Error("ModuleNotFoundError: No module named 'huggingface_hub'"));
  assert.ok(missing.includes("missing the Hugging Face client"), missing);
  assert.ok(missing.includes("update the desktop app"), missing);

  const auth = dryRunFailureText(new Error("RuntimeError: 401 Unauthorized"));
  assert.ok(auth.includes("Next: check HF_TOKEN access"), auth);

  assert.strictEqual(
    dryRunFailureText(new Error("some odd failure")),
    "Could not check the size: some odd failure. Next: choose this preset to try again.",
  );
});
