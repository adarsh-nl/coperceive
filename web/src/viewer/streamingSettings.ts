// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's streaming settings (Settings → Viewer): how much playback to
 * buffer ahead, the client memory limit for the frame cache, and whether the
 * small streaming stats line is shown. They follow the camera strip's pattern:
 * stored in state.json (`buffer_target_seconds`, `client_memory_limit_bytes`,
 * `show_stream_stats`), applied live through a window event, and read back when
 * the window gets focus so a change made in the other window's Settings applies
 * on return.
 *
 * Defaults: 3 s of playback buffered ahead, 512 MB of client memory, stats line off.
 * The numeric choices are validated on both sides: the Rust state layer keeps
 * state.json to the same values, so a hand-edited file cannot make the viewer
 * buffer for a minute or allocate gigabytes.
 */
import { useEffect, useRef, useState } from "react";
import { getPersistentState } from "../desktop/local";

export type StreamingSettings = {
  /** Seconds of playback buffered ahead: 1, 3 or 5. */
  bufferTargetSeconds: number;
  /** Cap of the viewer's in-memory frame/point cache in bytes. */
  memoryLimitBytes: number;
  /** Show the small buffer/MB/s/frame-load line under the status line. */
  showStreamStats: boolean;
};

export const DEFAULT_STREAMING_SETTINGS: StreamingSettings = {
  bufferTargetSeconds: 3,
  memoryLimitBytes: 512 * 1024 * 1024,
  showStreamStats: false,
};

/** The buffer targets offered in Settings → Viewer, in seconds. */
export const BUFFER_TARGET_CHOICES = [1, 3, 5] as const;

/** The client memory limits offered in Settings → Viewer, in MiB. */
export const MEMORY_LIMIT_CHOICES_MB = [256, 512, 1024] as const;

/** The same choices in bytes, as state.json stores them. */
export const MEMORY_LIMIT_CHOICES_BYTES = [256 * 1024 * 1024, 512 * 1024 * 1024, 1024 * 1024 * 1024] as const;

/** Fired on window when a setting changes; detail is a `Partial<StreamingSettings>`. */
export const STREAMING_SETTINGS_EVENT = "coperceive-streaming-settings";

export function isBufferTargetSeconds(value: unknown): value is number {
  return typeof value === "number" && (BUFFER_TARGET_CHOICES as readonly number[]).includes(value);
}

export function isMemoryLimitBytes(value: unknown): value is number {
  return typeof value === "number" && (MEMORY_LIMIT_CHOICES_BYTES as readonly number[]).includes(value);
}

/** The persisted state fields, as state.json spells them. */
type StreamState = {
  buffer_target_seconds?: unknown;
  client_memory_limit_bytes?: unknown;
  show_stream_stats?: unknown;
};

/** A settings patch from a state.json-shaped object, keeping only valid values. */
export function streamingSettingsFromState(state: StreamState | null | undefined): Partial<StreamingSettings> {
  const settings: Partial<StreamingSettings> = {};
  if (!state) {
    return settings;
  }
  if (isBufferTargetSeconds(state.buffer_target_seconds)) {
    settings.bufferTargetSeconds = state.buffer_target_seconds;
  }
  if (isMemoryLimitBytes(state.client_memory_limit_bytes)) {
    settings.memoryLimitBytes = state.client_memory_limit_bytes;
  }
  if (typeof state.show_stream_stats === "boolean") {
    settings.showStreamStats = state.show_stream_stats;
  }
  return settings;
}

function controller(): { port: number; secret: string } | null {
  if (typeof window === "undefined" || !window.__COPERCEIVE_CTRL_PORT__) return null;
  return { port: window.__COPERCEIVE_CTRL_PORT__, secret: window.__COPERCEIVE_CTRL_SECRET__ || "" };
}

/** What the window was opened with, before any read of state.json answers. */
export function initialStreamingSettings(): StreamingSettings {
  if (typeof window === "undefined") return { ...DEFAULT_STREAMING_SETTINGS };
  const init = (window.__COPERCEIVE_INITIAL_STATE__ ?? undefined) as unknown as StreamState | undefined;
  return { ...DEFAULT_STREAMING_SETTINGS, ...streamingSettingsFromState(init) };
}

export async function loadStreamingSettings(): Promise<StreamingSettings> {
  let settings = initialStreamingSettings();
  const saved = (await getPersistentState().catch(() => null)) as StreamState | null;
  if (saved) {
    settings = { ...settings, ...streamingSettingsFromState(saved) };
  }
  const ctrl = controller();
  if (ctrl) {
    try {
      const response = await fetch(`http://127.0.0.1:${ctrl.port}/state`, {
        headers: { Authorization: `Bearer ${ctrl.secret}` },
      });
      settings = { ...settings, ...streamingSettingsFromState((await response.json()) as StreamState) };
    } catch {
      // Fall back to the initial state.
    }
  }
  return settings;
}

/** state.json spellings of the camelCase settings the UI uses. */
function persistedPatch(patch: Partial<StreamingSettings>): StreamState {
  const state: StreamState = {};
  if (patch.bufferTargetSeconds !== undefined) state.buffer_target_seconds = patch.bufferTargetSeconds;
  if (patch.memoryLimitBytes !== undefined) state.client_memory_limit_bytes = patch.memoryLimitBytes;
  if (patch.showStreamStats !== undefined) state.show_stream_stats = patch.showStreamStats;
  return state;
}

export function saveStreamingSettings(patch: Partial<StreamingSettings>): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(STREAMING_SETTINGS_EVENT, { detail: patch }));
  const ctrl = controller();
  if (!ctrl) return;
  fetch(`http://127.0.0.1:${ctrl.port}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${ctrl.secret}` },
    body: JSON.stringify(persistedPatch(patch)),
  }).catch(() => undefined);
}

/**
 * The saved settings, kept in step with changes made anywhere in this window,
 * and read again when the window gets focus (a change made in the other
 * window's Settings then applies as soon as you come back to the viewer).
 */
export function useStreamingSettings(): [
  StreamingSettings,
  (patch: Partial<StreamingSettings>) => void,
] {
  const [settings, setSettings] = useState<StreamingSettings>(initialStreamingSettings);
  // Fields changed in this window win over a saved read that answers later.
  const changedRef = useRef<Partial<StreamingSettings>>({});
  useEffect(() => {
    let active = true;
    const onChange = (event: Event) => {
      const patch = (event as CustomEvent<Partial<StreamingSettings>>).detail;
      if (!patch) return;
      changedRef.current = { ...changedRef.current, ...patch };
      setSettings((current) => ({ ...current, ...patch }));
    };
    const reread = () => {
      void loadStreamingSettings().then((saved) => {
        if (active) setSettings({ ...saved, ...changedRef.current });
      });
    };
    window.addEventListener(STREAMING_SETTINGS_EVENT, onChange);
    window.addEventListener("focus", reread);
    void loadStreamingSettings().then((saved) => {
      if (active) setSettings({ ...saved, ...changedRef.current });
    });
    return () => {
      active = false;
      window.removeEventListener(STREAMING_SETTINGS_EVENT, onChange);
      window.removeEventListener("focus", reread);
    };
  }, []);
  const save = (patch: Partial<StreamingSettings>) => {
    changedRef.current = { ...changedRef.current, ...patch };
    setSettings((current) => ({ ...current, ...patch }));
    saveStreamingSettings(patch);
  };
  return [settings, save];
}
