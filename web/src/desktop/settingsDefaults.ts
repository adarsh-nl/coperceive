// SPDX-License-Identifier: Apache-2.0
/**
 * The defaults of the global, state.json-backed Settings sections (General,
 * Viewer, Datasets, Updates): one table drives both the "Default: …" text each
 * value shows and what that section's "Reset to defaults" writes. The values
 * are the Rust state layer's (`impl Default for AppPersistentState`); the
 * test next to this file pins them.
 *
 * Every value here is display or app behaviour; none changes a reported
 * number (settings-inventory.md, "The rule this inventory applies"). The
 * local library location is not here: it has its own move flow in the Library
 * and is never reset by a button.
 */
import { DEFAULT_CAMERA_STRIP } from "../viewer/cameraStripSetting";
import { DEFAULT_STREAMING_SETTINGS } from "../viewer/streamingSettings";
import { DEFAULT_THEME_CHOICE } from "../viewer/themeSetting";
import { DEFAULT_VIEWER_DEFAULTS } from "../viewer/viewerDefaults";
import { DEFAULT_CINEMA_DRIFT, DEFAULT_VIEWER_STYLE } from "../viewer/viewerStyle";

export const SETTINGS_DEFAULTS = {
  general: {
    reopen_last_session: true,
  },
  viewer: {
    open_first_ready_scene: true,
    auto_next_scene: false,
    viewer_style: DEFAULT_VIEWER_STYLE,
    cinema_drift: DEFAULT_CINEMA_DRIFT,
    camera_strip: DEFAULT_CAMERA_STRIP,
    buffer_target_seconds: DEFAULT_STREAMING_SETTINGS.bufferTargetSeconds,
    client_memory_limit_bytes: DEFAULT_STREAMING_SETTINGS.memoryLimitBytes,
    show_stream_stats: DEFAULT_STREAMING_SETTINGS.showStreamStats,
    viewer_point_size: DEFAULT_VIEWER_DEFAULTS.pointSize,
    default_viewpoint: DEFAULT_VIEWER_DEFAULTS.defaultViewpoint,
    theme_choice: DEFAULT_THEME_CHOICE,
  },
  datasets: {
    dataset_grouping: "none",
  },
  updates: {
    auto_download_updates: true,
  },
} as const;

export type SettingsSection = keyof typeof SETTINGS_DEFAULTS;

/** "on"/"off" for a toggle's default, as the rows print it. */
export function onOff(value: boolean): string {
  return value ? "on" : "off";
}

/** The hint suffix every row shows: "Default: 3 s." */
export function defaultNote(value: string): string {
  return `Default: ${value}.`;
}
