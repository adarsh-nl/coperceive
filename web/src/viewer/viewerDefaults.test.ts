// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_POINT_SIZE,
  defaultAppearance,
  loadAppearance,
  POINT_SIZES,
  resolvePointSize,
  saveAppearance,
  VIEW_KEY,
} from "./appearance.ts";
import { sceneCapabilities, viewpointFor } from "./capabilities.ts";
import type { SceneSummary } from "./types.ts";
import {
  DEFAULT_VIEWER_DEFAULTS,
  initialViewerDefaults,
  saveViewerDefaults,
  startViewpoint,
  VIEWER_DEFAULTS_EVENT,
  viewerDefaultsFromState,
  VIEWPOINT_IDS,
} from "./viewerDefaults.ts";

const scope = globalThis as unknown as {
  window?: {
    __COPERCEIVE_INITIAL_STATE__?: unknown;
    dispatchEvent?: (event: Event) => boolean;
  };
  localStorage?: Storage;
};

function memoryStorage(): Storage {
  const rows = new Map<string, string>();
  return {
    get length() {
      return rows.size;
    },
    clear: () => rows.clear(),
    getItem: (key) => rows.get(key) ?? null,
    key: (index) => [...rows.keys()][index] ?? null,
    removeItem: (key) => void rows.delete(key),
    setItem: (key, value) => void rows.set(key, String(value)),
  };
}

function scene(agents: string[], cameras: boolean): SceneSummary {
  return {
    scene_id: "s",
    world_frame: "ENU",
    agents: agents.map((agent_type, index) => ({ agent_id: `${agent_type}${index}`, agent_type })),
    sensors: cameras ? [{ sensor_id: "cam", agent_id: "vehicle0", modality: "camera" }] : [],
    times_us: [0],
  };
}

test("defaults: point size 2 px of 1-4, camera view Last used (null)", () => {
  assert.equal(DEFAULT_POINT_SIZE, 2);
  assert.deepEqual([...POINT_SIZES], [1, 2, 3, 4]);
  assert.deepEqual(DEFAULT_VIEWER_DEFAULTS, { pointSize: 2, defaultViewpoint: null });
  assert.deepEqual([...VIEWPOINT_IDS], ["orbit", "bird", "chase", "drone", "camera"]);
});

test("a view's point size: chosen in the toolbar wins, otherwise the Settings default", () => {
  // Marked as chosen: kept whatever the default.
  assert.deepEqual(resolvePointSize({ pointSize: 1, pointSizeChosen: true }, 4), { pointSize: 1, pointSizeChosen: true });
  assert.deepEqual(resolvePointSize({ pointSize: 2, pointSizeChosen: true }, 4), { pointSize: 2, pointSizeChosen: true });
  // Marked as not chosen: the default applies, even when the stored size differs.
  assert.deepEqual(resolvePointSize({ pointSize: 3, pointSizeChosen: false }, 4), { pointSize: 4, pointSizeChosen: false });
  // No stored view: the default.
  assert.deepEqual(resolvePointSize({}, 3), { pointSize: 3, pointSizeChosen: false });
  // An invalid stored size, or an invalid default, falls back.
  assert.deepEqual(resolvePointSize({ pointSize: 9, pointSizeChosen: true }, 3), { pointSize: 3, pointSizeChosen: false });
  assert.deepEqual(resolvePointSize({}, 7), { pointSize: 2, pointSizeChosen: false });
});

test("a view saved before the marker existed keeps a size other than the old fixed 2", () => {
  assert.deepEqual(resolvePointSize({ pointSize: 3 }, 2), { pointSize: 3, pointSizeChosen: true });
  assert.deepEqual(resolvePointSize({ pointSize: 1 }, 4), { pointSize: 1, pointSizeChosen: true });
  // A legacy 2 is the old default, not a choice: the Settings default applies.
  assert.deepEqual(resolvePointSize({ pointSize: 2 }, 4), { pointSize: 4, pointSizeChosen: false });
});

test("saving a view that follows the default does not turn it into a choice", () => {
  const previous = scope.localStorage;
  scope.localStorage = memoryStorage();
  try {
    // The view follows a Settings default of 3 and is saved with that size.
    const following = loadAppearance(3);
    assert.deepEqual([following.pointSize, following.pointSizeChosen], [3, false]);
    saveAppearance(following);
    const stored = JSON.parse(scope.localStorage.getItem(VIEW_KEY) ?? "{}") as Record<string, unknown>;
    assert.equal(stored.pointSizeChosen, false, "the marker is always written");
    // Settings goes back to 2: the view follows, it did not choose 3.
    const after = loadAppearance(2);
    assert.deepEqual([after.pointSize, after.pointSizeChosen], [2, false]);

    // A toolbar choice survives a change of the default.
    saveAppearance({ ...after, pointSize: 4, pointSizeChosen: true });
    const chosen = loadAppearance(1);
    assert.deepEqual([chosen.pointSize, chosen.pointSizeChosen], [4, true]);

    // A legacy record (no marker) with 3 is kept as a choice.
    scope.localStorage.setItem(VIEW_KEY, JSON.stringify({ pointSize: 3, colorBy: "class" }));
    const legacy = loadAppearance(2);
    assert.deepEqual([legacy.pointSize, legacy.pointSizeChosen, legacy.colorBy], [3, true, "class"]);
  } finally {
    scope.localStorage = previous;
  }
});

test("Reset view returns to the Settings default, not a fixed 2", () => {
  const reset = defaultAppearance(4);
  assert.deepEqual([reset.pointSize, reset.pointSizeChosen], [4, false]);
});

test("the start view: the Settings default, else the last used view, else orbit", () => {
  assert.equal(startViewpoint(null, "chase"), "chase", "Last used keeps today's behaviour");
  assert.equal(startViewpoint(undefined, undefined), "orbit");
  assert.equal(startViewpoint("bird", "chase"), "bird");
  assert.equal(startViewpoint("top", "drone"), "drone", "an unknown default is ignored");
  assert.equal(startViewpoint(null, "sideways"), "orbit");
});

test("the start view still falls back to orbit when the scene cannot show it", () => {
  const vehicleOnly = sceneCapabilities(scene(["vehicle"], false));
  assert.equal(viewpointFor(startViewpoint("drone", "chase"), vehicleOnly), "orbit");
  assert.equal(viewpointFor(startViewpoint("camera", null), vehicleOnly), "orbit");
  assert.equal(viewpointFor(startViewpoint("chase", null), vehicleOnly), "chase");
  const griffin = sceneCapabilities(scene(["vehicle", "uav"], true));
  assert.equal(viewpointFor(startViewpoint("drone", "orbit"), griffin), "drone");
  // A scene with neither a vehicle nor cameras: only orbit and bird.
  const empty = sceneCapabilities(null);
  assert.equal(viewpointFor(startViewpoint("bird", null), empty), "bird");
  assert.equal(viewpointFor(startViewpoint(null, "chase"), empty), "orbit");
});

test("state.json values are read only when valid; an omitted camera view is Last used", () => {
  assert.deepEqual(viewerDefaultsFromState(null), {});
  assert.deepEqual(viewerDefaultsFromState({}), { defaultViewpoint: null });
  assert.deepEqual(viewerDefaultsFromState({ viewer_point_size: 3, default_viewpoint: "bird" }), {
    pointSize: 3,
    defaultViewpoint: "bird",
  });
  assert.deepEqual(viewerDefaultsFromState({ viewer_point_size: 0, default_viewpoint: "top" }), {
    defaultViewpoint: null,
  });
  assert.deepEqual(viewerDefaultsFromState({ viewer_point_size: "3" }), { defaultViewpoint: null });
});

test("the window-open snapshot seeds the defaults; a save fires the live event", () => {
  const events: Event[] = [];
  const previous = scope.window;
  scope.window = {
    __COPERCEIVE_INITIAL_STATE__: { viewer_point_size: 4, default_viewpoint: "chase", last_viewpoint: "bird" },
    dispatchEvent: (event: Event) => {
      events.push(event);
      return true;
    },
  };
  try {
    assert.deepEqual(initialViewerDefaults(), { pointSize: 4, defaultViewpoint: "chase" });
    saveViewerDefaults({ pointSize: 1 });
    assert.equal(events.length, 1);
    assert.equal(events[0]?.type, VIEWER_DEFAULTS_EVENT);
    assert.deepEqual((events[0] as CustomEvent).detail, { pointSize: 1 });
  } finally {
    scope.window = previous;
  }
});
