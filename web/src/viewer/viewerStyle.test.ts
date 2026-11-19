// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { sceneCapabilities } from "./capabilities.ts";
import { PREVIEW_BOXES, previewBothPoint, renderStylePreview, type PreviewCtx } from "./previewDraw.ts";
import type { SceneSummary } from "./types.ts";
import {
  CLASSIC_LIGHT,
  contrastRatio,
  DEFAULT_CINEMA_DRIFT,
  DEFAULT_VIEWER_STYLE,
  effectiveCinemaDrift,
  initialCinemaDrift,
  initialViewerStyle,
  isViewerStyle,
  VIEWER_STYLES,
  VIEWER_STYLE_TOKENS,
} from "./viewerStyle.ts";

test("classic is the default style, and only the four names are accepted", () => {
  assert.equal(DEFAULT_VIEWER_STYLE, "classic");
  assert.equal(DEFAULT_CINEMA_DRIFT, true);
  for (const style of VIEWER_STYLES) assert.equal(isViewerStyle(style), true);
  for (const value of ["", "bold", "CLASSIC", null, 1]) assert.equal(isViewerStyle(value), false);
});

test("the saved style and drift come from the window, and unknown values fall back", () => {
  const scope = globalThis as unknown as { window?: { __COPERCEIVE_INITIAL_STATE__?: unknown } };
  const before = scope.window;
  try {
    scope.window = {};
    assert.equal(initialViewerStyle(), "classic");
    assert.equal(initialCinemaDrift(), true);
    scope.window = { __COPERCEIVE_INITIAL_STATE__: { viewer_style: "cinema", cinema_drift: false } };
    assert.equal(initialViewerStyle(), "cinema");
    assert.equal(initialCinemaDrift(), false);
    scope.window = { __COPERCEIVE_INITIAL_STATE__: { viewer_style: "neon", cinema_drift: "yes" } };
    assert.equal(initialViewerStyle(), "classic");
    assert.equal(initialCinemaDrift(), true);
  } finally {
    scope.window = before;
  }
});

test("cinema drift is off when the system asks for reduced motion", () => {
  assert.equal(effectiveCinemaDrift(true, false), true);
  assert.equal(effectiveCinemaDrift(true, true), false);
  assert.equal(effectiveCinemaDrift(false, false), false);
});

test("body text in every style is at least 4.5:1", () => {
  const classicDark = VIEWER_STYLE_TOKENS.classic;
  assert.ok(contrastRatio(CLASSIC_LIGHT.text, CLASSIC_LIGHT.page) >= 4.5);
  assert.ok(contrastRatio(CLASSIC_LIGHT.secondary, CLASSIC_LIGHT.panel) >= 4.5);
  assert.ok(contrastRatio(classicDark.text, "#141415") >= 4.5);
  assert.ok(contrastRatio(classicDark.textSecondary, classicDark.panel) >= 4.5);
  for (const style of ["studio", "instrument", "cinema"] as const) {
    const tokens = VIEWER_STYLE_TOKENS[style];
    assert.ok(contrastRatio(tokens.text, tokens.panel) >= 4.5, `${style} text`);
    assert.ok(contrastRatio(tokens.textSecondary, tokens.panel) >= 4.5, `${style} secondary`);
    assert.ok(contrastRatio(tokens.text, tokens.background) >= 4.5, `${style} on the view`);
  }
});

test("each style renders the same preview scene, and cinema drift moves the camera", () => {
  const paints = VIEWER_STYLES.map((style) => paint(style, { drift: false, frame: 0 }));
  const backgrounds = paints.map((row) => row.fills[0]);
  assert.equal(new Set(backgrounds).size, VIEWER_STYLES.length);
  for (const row of paints) {
    assert.ok(row.strokes >= PREVIEW_BOXES.length, "one stroke per box");
    assert.ok(row.fills.length > 1, "points are filled");
  }
  const still = previewBothPoint("cinema", 320, 180, { drift: false, frame: 0 });
  const moved = previewBothPoint("cinema", 320, 180, { drift: true, frame: 8 });
  assert.notEqual(moved.x, still.x);
  const classic = previewBothPoint("classic", 320, 180);
  const studio = previewBothPoint("studio", 320, 180);
  assert.notEqual(classic.x, studio.x);
});

test("capability rules do not take a style: no camera, no chase or drone without the agent", () => {
  const lidarOnly = scene({ agents: ["vehicle"], cameras: false });
  const griffin = scene({ agents: ["vehicle", "uav"], cameras: true });
  for (const style of VIEWER_STYLES) {
    assert.equal(style.length > 0, true);
    const plain = sceneCapabilities(lidarOnly);
    assert.equal(plain.cameras, false);
    assert.equal(plain.viewpoints.has("camera"), false);
    assert.equal(plain.viewpoints.has("chase"), true);
    assert.equal(plain.viewpoints.has("drone"), false);
    const aerial = sceneCapabilities(griffin);
    assert.equal(aerial.viewpoints.has("drone"), true);
    assert.equal(aerial.viewpoints.has("camera"), true);
  }
});

function scene({ agents, cameras }: { agents: string[]; cameras: boolean }): SceneSummary {
  return {
    scene_id: "preview",
    world_frame: "ENU",
    agents: agents.map((agent_type) => ({ agent_id: agent_type, agent_type })),
    sensors: cameras
      ? [{ sensor_id: "front", agent_id: agents[0] ?? "vehicle", modality: "camera" }]
      : [{ sensor_id: "lidar", agent_id: agents[0] ?? "vehicle", modality: "lidar" }],
    times_us: [0],
  };
}

function paint(style: (typeof VIEWER_STYLES)[number], options: { drift: boolean; frame: number }) {
  const fills: string[] = [];
  let strokes = 0;
  const ctx: PreviewCtx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    setLineDash() {},
    fillRect() {
      fills.push(ctx.fillStyle);
    },
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {
      strokes += 1;
    },
  };
  renderStylePreview(ctx, 320, 180, style, options);
  return { fills, strokes };
}
