// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { PANEL_DEFINITIONS, type PanelId, type PanelStateKind } from "./panelStates.ts";

const CLI_JARGON_PATTERNS = [
  /\bpip\b/i,
  /\binstall\b/i,
  /\bbash\b/i,
  /\bssh\b/i,
  /\bgrep\b/i,
  /\bcoperceive\b/i,
  /\bpython\b/i,
  /\bcurl\b/i,
  /\bhdiutil\b/i,
  /\bpkill\b/i,
  /--[a-z0-9_-]+/i,
  /`[^`]+`/,
];

test("panel states cover all 8 required panels", () => {
  const expectedPanels: PanelId[] = [
    "connect",
    "library",
    "get_data",
    "inspector",
    "camera_strip",
    "telemetry",
    "predictions",
    "hpc_workspace",
  ];
  for (const panel of expectedPanels) {
    assert.ok(PANEL_DEFINITIONS[panel], `Panel ${panel} must be defined`);
  }
});

test("every panel has empty, loading, and error states conforming to requirements", () => {
  const kinds: PanelStateKind[] = ["empty", "loading", "error"];

  for (const [panelId, states] of Object.entries(PANEL_DEFINITIONS)) {
    for (const kind of kinds) {
      const state = states[kind];
      assert.ok(state, `Panel ${panelId} must define ${kind} state`);

      // 1. Non-empty title
      assert.ok(state.title.trim().length > 0, `${panelId}.${kind} must have a non-empty title`);
      assert.ok(state.title.length <= 40, `${panelId}.${kind} title should be concise, got "${state.title}"`);

      // 2. Exactly 1 sentence for description
      const desc = state.description.trim();
      assert.ok(desc.endsWith("."), `${panelId}.${kind} description must end with a period: "${desc}"`);
      const periods = desc.split(".").filter((s) => s.trim().length > 0);
      assert.strictEqual(periods.length, 1, `${panelId}.${kind} description must be exactly 1 sentence, got: "${desc}"`);

      // 3. Exactly 1 action button
      assert.ok(state.actionLabel.trim().length > 0, `${panelId}.${kind} must have an action button label`);

      // 4. No CLI jargon
      const fullText = `${state.title} ${state.description} ${state.actionLabel}`;
      for (const pattern of CLI_JARGON_PATTERNS) {
        assert.ok(
          !pattern.test(fullText),
          `${panelId}.${kind} text "${fullText}" must not contain CLI jargon matching ${pattern}`,
        );
      }
    }
  }
});
