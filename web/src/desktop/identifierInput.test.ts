// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { IDENTIFIER_INPUT_PROPS } from "./identifierProps.ts";

test("IDENTIFIER_INPUT_PROPS disables platform auto-capitalization and correction", () => {
  assert.equal(IDENTIFIER_INPUT_PROPS.autoCapitalize, "off");
  assert.equal(IDENTIFIER_INPUT_PROPS.autoCorrect, "off");
  assert.equal(IDENTIFIER_INPUT_PROPS.spellCheck, false);
  assert.equal(IDENTIFIER_INPUT_PROPS.autoComplete, "off");
});

test("HpcServers.tsx specifies autocapitalize/autocorrect off on all text and number inputs", () => {
  const source = readFileSync(new URL("./HpcServers.tsx", import.meta.url), "utf8");
  const expectedIds = [
    "hpc-name",
    "hpc-host",
    "hpc-user",
    "hpc-data-root",
    "hpc-scratch",
    "hpc-results-root",
    "hpc-code-roots",
    "hpc-partition",
    "hpc-time",
    "hpc-cpus",
    "hpc-mem",
    "hpc-interpreter",
    "hpc-modules",
    "hpc-image",
    "hpc-wheelhouse",
  ];

  for (const id of expectedIds) {
    const inputRegex = new RegExp(`<input[^>]*id=["']${id}["'][^>]*>`, "s");
    const match = source.match(inputRegex);
    assert.ok(match, `Expected input with id="${id}" in HpcServers.tsx`);
    const tag = match[0];
    assert.ok(
      tag.includes('autoCapitalize="off"'),
      `Input "${id}" missing autoCapitalize="off"`,
    );
    assert.ok(
      tag.includes('autoCorrect="off"'),
      `Input "${id}" missing autoCorrect="off"`,
    );
    assert.ok(
      tag.includes("spellCheck={false}"),
      `Input "${id}" missing spellCheck={false}`,
    );
    assert.ok(
      tag.includes('autoComplete="off"'),
      `Input "${id}" missing autoComplete="off"`,
    );
  }
});
