// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { defaultNote, onOff, SETTINGS_DEFAULTS } from "./settingsDefaults.ts";

test("each global section resets to the Rust state defaults (impl Default for AppPersistentState)", () => {
  assert.deepEqual(SETTINGS_DEFAULTS.general, { reopen_last_session: true });
  assert.deepEqual(SETTINGS_DEFAULTS.viewer, {
    open_first_ready_scene: true,
    auto_next_scene: false,
    viewer_style: "classic",
    cinema_drift: true,
    camera_strip: "all",
    buffer_target_seconds: 3,
    client_memory_limit_bytes: 512 * 1024 * 1024,
    show_stream_stats: false,
    viewer_point_size: 2,
    default_viewpoint: null,
    theme_choice: "system",
  });
  assert.deepEqual(SETTINGS_DEFAULTS.datasets, { dataset_grouping: "none" });
  assert.deepEqual(SETTINGS_DEFAULTS.updates, { auto_download_updates: true });
});

test("a reset touches only its own section, and never the library location", () => {
  const sections = Object.values(SETTINGS_DEFAULTS).map((section) => Object.keys(section));
  const all = sections.flat();
  assert.equal(new Set(all).size, all.length, "no value belongs to two sections");
  for (const key of ["library_root", "last_data_root", "recent_roots", "last_viewpoint"]) {
    assert.ok(!all.includes(key), `${key} is not reset by Settings`);
  }
  assert.deepEqual(Object.keys(SETTINGS_DEFAULTS), ["general", "viewer", "datasets", "updates"]);
});

test("the default note each row shows", () => {
  assert.equal(defaultNote(onOff(true)), "Default: on.");
  assert.equal(defaultNote(onOff(false)), "Default: off.");
  assert.equal(defaultNote("2 px"), "Default: 2 px.");
});
