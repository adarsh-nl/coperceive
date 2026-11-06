// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import {
  BUFFER_TARGET_CHOICES,
  DEFAULT_STREAMING_SETTINGS,
  initialStreamingSettings,
  isBufferTargetSeconds,
  isMemoryLimitBytes,
  MEMORY_LIMIT_CHOICES_BYTES,
  saveStreamingSettings,
  STREAMING_SETTINGS_EVENT,
  streamingSettingsFromState,
} from "./streamingSettings.ts";

const scope = globalThis as unknown as {
  window?: {
    __COPERCEIVE_INITIAL_STATE__?: unknown;
    __COPERCEIVE_CTRL_PORT__?: number;
    dispatchEvent?: (event: Event) => boolean;
  };
};

test("the choices are 1/3/5 seconds and 256/512/1024 MB, nothing else", () => {
  assert.deepEqual([...BUFFER_TARGET_CHOICES], [1, 3, 5]);
  assert.deepEqual(
    [...MEMORY_LIMIT_CHOICES_BYTES],
    [256 * 1024 * 1024, 512 * 1024 * 1024, 1024 * 1024 * 1024],
  );
  for (const value of [1, 3, 5]) {
    assert.equal(isBufferTargetSeconds(value), true, String(value));
  }
  for (const value of [0, 2, 4, 6, 3.5, "3", null, undefined]) {
    assert.equal(isBufferTargetSeconds(value), false, String(value));
  }
  for (const value of MEMORY_LIMIT_CHOICES_BYTES) {
    assert.equal(isMemoryLimitBytes(value), true, String(value));
  }
  for (const value of [0, 100 * 1024 * 1024, 2 * 1024 ** 3, "512", null]) {
    assert.equal(isMemoryLimitBytes(value), false, String(value));
  }
});

test("the defaults are 3 s, 512 MB and the stats line off", () => {
  assert.equal(DEFAULT_STREAMING_SETTINGS.bufferTargetSeconds, 3);
  assert.equal(DEFAULT_STREAMING_SETTINGS.memoryLimitBytes, 512 * 1024 * 1024);
  assert.equal(DEFAULT_STREAMING_SETTINGS.showStreamStats, false);
});

test("state.json fields are validated one by one; old files read as the defaults", () => {
  assert.deepEqual(streamingSettingsFromState(null), {});
  assert.deepEqual(streamingSettingsFromState({}), {});
  assert.deepEqual(
    streamingSettingsFromState({
      buffer_target_seconds: 5,
      client_memory_limit_bytes: 256 * 1024 * 1024,
      show_stream_stats: true,
    }),
    { bufferTargetSeconds: 5, memoryLimitBytes: 256 * 1024 * 1024, showStreamStats: true },
  );
  // A hand-edited state.json with an unsupported value keeps the default.
  assert.deepEqual(
    streamingSettingsFromState({
      buffer_target_seconds: 60,
      client_memory_limit_bytes: 8 * 1024 ** 3,
      show_stream_stats: "yes",
    }),
    {},
  );
});

test("the window's initial state supplies the first render, before state.json answers", () => {
  const before = scope.window;
  try {
    scope.window = {};
    assert.deepEqual(initialStreamingSettings(), { ...DEFAULT_STREAMING_SETTINGS });
    scope.window = {
      __COPERCEIVE_INITIAL_STATE__: { buffer_target_seconds: 1, show_stream_stats: true },
    };
    assert.deepEqual(initialStreamingSettings(), {
      bufferTargetSeconds: 1,
      memoryLimitBytes: 512 * 1024 * 1024,
      showStreamStats: true,
    });
    scope.window = { __COPERCEIVE_INITIAL_STATE__: { buffer_target_seconds: 42 } };
    assert.equal(initialStreamingSettings().bufferTargetSeconds, 3);
  } finally {
    scope.window = before;
  }
});

test("saving announces the change on the window for every open viewer", () => {
  const before = scope.window;
  try {
    const events: { type: string; detail: unknown }[] = [];
    scope.window = {
      dispatchEvent: (event: Event) => {
        events.push({ type: event.type, detail: (event as CustomEvent<unknown>).detail });
        return true;
      },
    };
    saveStreamingSettings({ bufferTargetSeconds: 5 });
    saveStreamingSettings({ memoryLimitBytes: 1024 * 1024 * 1024, showStreamStats: true });
    assert.deepEqual(events, [
      { type: STREAMING_SETTINGS_EVENT, detail: { bufferTargetSeconds: 5 } },
      {
        type: STREAMING_SETTINGS_EVENT,
        detail: { memoryLimitBytes: 1024 * 1024 * 1024, showStreamStats: true },
      },
    ]);
  } finally {
    scope.window = before;
  }
});
