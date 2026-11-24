// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useState } from "react";
import { CarlaMapsSettings } from "./CarlaMapsSettings";
import { ClusterDatasetSettings } from "./ClusterDatasetSettings";
import { ModelRunSettings } from "./ModelRunSettings";
import {
  getPersistentState,
  getStorageInfo,
  isViewerWindow,
  revealInFinder,
  setAutoDownloadUpdates,
  updatePersistentState,
} from "./local";
import { SettingRow } from "./components/SettingRow";
import { Button } from "./components/Button";
import { IconButton } from "./components/IconButton";
import { saveDatasetGrouping, useDatasetGrouping } from "./hpc/groupingSetting";
import { saveCameraStrip, useCameraStripMode, type CameraStripMode } from "../viewer/cameraStripSetting";
import {
  BUFFER_TARGET_CHOICES,
  MEMORY_LIMIT_CHOICES_MB,
  useStreamingSettings,
} from "../viewer/streamingSettings";
import { POINT_SIZES } from "../viewer/appearance";
import {
  initialThemeChoice,
  saveThemeChoice,
  subscribeThemeChoice,
  type ThemeChoice,
} from "../viewer/themeSetting";
import { useViewerDefaults } from "../viewer/viewerDefaults";
import type { Viewpoint } from "../viewer/viewpoints";
import { saveCinemaDrift, saveViewerStyle } from "../viewer/viewerStyle";
import { ViewerStyleSettings } from "../viewer/ViewerStyleSettings";
import type { DatasetGrouping } from "./hpc/grouping";
import { getRevealLabel } from "./platform";
import { defaultNote, onOff, SETTINGS_DEFAULTS } from "./settingsDefaults";

function SettingsGroup({
  title,
  onReset,
  children,
}: {
  title: string;
  /** Resets this section's values to their defaults. */
  onReset?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section aria-label={title} style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", padding: "0 4px" }}>
        <h3
          style={{
            margin: 0,
            fontSize: "11px",
            fontWeight: 600,
            letterSpacing: "0.03em",
            textTransform: "uppercase",
            color: "var(--text-subtle)",
          }}
        >
          {title}
        </h3>
        {onReset ? (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Reset ${title} settings to defaults`}
            onClick={onReset}
            style={{ height: "22px", padding: "0 6px", fontSize: "11.5px" }}
          >
            Reset to defaults
          </Button>
        ) : null}
      </div>
      <div
        style={{
          backgroundColor: "var(--sunken)",
          border: "1px solid var(--line, rgba(255,255,255,0.08))",
          borderRadius: "8px",
          padding: "4px 12px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </div>
    </section>
  );
}

/** A choice group's name, with its one-line hint (unit and default). */
function ChoiceHeading({ title, hint }: { title: string; hint: string }) {
  return (
    <div style={{ padding: "8px 0 2px", display: "flex", flexDirection: "column", gap: "2px" }}>
      <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--text)" }}>{title}</span>
      <span style={{ fontSize: "12px", color: "var(--text-tertiary)", lineHeight: 1.4 }}>{hint}</span>
    </div>
  );
}

const D = SETTINGS_DEFAULTS;

const GROUPING_CHOICES: ReadonlyArray<readonly [DatasetGrouping, string, string]> = [
  ["none", "None", "One list, sorted by scene name."],
  ["state", "By state", "Failed, stale, preparing, queued, ready, then not prepared."],
  ["day", "By recording day", "From scene names that hold a date (YYYY_MM_DD_HH_MM_SS): the day the data was recorded."],
];

const CAMERA_STRIP_CHOICES: ReadonlyArray<readonly [CameraStripMode, string, string]> = [
  ["all", "All cameras (scroll)", "Every agent's cameras in one filmstrip under the 3D view; scroll it sideways with the trackpad or Shift and the wheel."],
  ["agent", "One agent at a time", "One agent's cameras, larger, with Switch agent to move to the next."],
];

/** Settings → Viewer → Camera view (inventory L91). null = the last used view. */
const CAMERA_VIEW_CHOICES: ReadonlyArray<readonly [Viewpoint | null, string, string]> = [
  [null, "Last used", "Start in the view the last viewer used."],
  ["orbit", "Orbit", "Free camera around the scene."],
  ["bird", "Bird's-eye", "48 m above the vehicle, north up."],
  ["chase", "Chase", "Behind the vehicle; scenes without a vehicle open in Orbit."],
  ["drone", "Drone", "Behind the UAV, looking at the vehicle; scenes without a UAV open in Orbit."],
  ["camera", "Camera", "Through a camera; scenes without calibrated cameras open in Orbit."],
];

/** Settings → Viewer → Theme (inventory L90). */
const THEME_CHOICE_ROWS: ReadonlyArray<readonly [ThemeChoice, string, string]> = [
  ["system", "System", "Follow the system's light or dark appearance."],
  ["light", "Light", ""],
  ["dark", "Dark", ""],
];

const LABEL_FOR_VIEW = new Map(CAMERA_VIEW_CHOICES.map(([value, title]) => [value, title]));
const LABEL_FOR_THEME = new Map(THEME_CHOICE_ROWS.map(([value, title]) => [value, title]));
const LABEL_FOR_STRIP = new Map(CAMERA_STRIP_CHOICES.map(([value, title]) => [value, title]));
const LABEL_FOR_GROUPING = new Map(GROUPING_CHOICES.map(([value, title]) => [value, title]));

export function DesktopPreferences() {
  const [grouping, setGrouping] = useDatasetGrouping();
  const [cameraStrip, setCameraStrip] = useCameraStripMode();
  const [streaming, setStreaming] = useStreamingSettings();
  const [viewerDefaults, setViewerDefaults] = useViewerDefaults();
  const [themeChoice, setThemeChoice] = useState<ThemeChoice>(initialThemeChoice);
  const [reopenLast, setReopenLast] = useState<boolean>(D.general.reopen_last_session);
  const [autoUpdate, setAutoUpdate] = useState<boolean>(D.updates.auto_download_updates);
  const [autoNextScene, setAutoNextScene] = useState<boolean>(D.viewer.auto_next_scene);
  const [openFirstReady, setOpenFirstReady] = useState<boolean>(D.viewer.open_first_ready_scene);
  const [storageRoot, setStorageRoot] = useState<string>("");

  useEffect(() => subscribeThemeChoice(setThemeChoice), []);

  useEffect(() => {
    void (async () => {
      const saved = await getPersistentState();
      if (saved) {
        if (typeof saved.reopen_last_session === "boolean") {
          setReopenLast(saved.reopen_last_session);
        }
        if (typeof saved.auto_next_scene === "boolean") {
          setAutoNextScene(saved.auto_next_scene);
        }
        if (typeof saved.open_first_ready_scene === "boolean") {
          setOpenFirstReady(saved.open_first_ready_scene);
        }
        if (typeof saved.auto_download_updates === "boolean") {
          setAutoUpdate(saved.auto_download_updates);
        }
      }
      const stats = await getStorageInfo();
      if (stats?.library_root) {
        setStorageRoot(stats.library_root);
      }
    })();
  }, []);

  const handleToggle = (checked: boolean) => {
    setReopenLast(checked);
    void updatePersistentState({ reopen_last_session: checked }).catch(() => {});
  };

  const handleAutoNextToggle = (checked: boolean) => {
    setAutoNextScene(checked);
    window.dispatchEvent(new CustomEvent("coperceive-auto-next-scene", { detail: checked }));
    void updatePersistentState({ auto_next_scene: checked }).catch(() => {});
  };

  const handleOpenFirstReadyToggle = (checked: boolean) => {
    setOpenFirstReady(checked);
    void updatePersistentState({ open_first_ready_scene: checked }).catch(() => {});
  };

  const handleAutoUpdateToggle = (checked: boolean) => {
    setAutoUpdate(checked);
    void setAutoDownloadUpdates(checked);
  };

  const handleShowInFinder = () => {
    if (storageRoot) {
      revealInFinder(storageRoot);
    }
  };

  // Each reset goes through the value's own setter, one write per value: the
  // live events fire, and in the viewer window a value that window may not
  // write is refused on its own instead of failing the whole reset.
  const resetGeneral = () => {
    handleToggle(D.general.reopen_last_session);
  };

  const resetViewer = () => {
    handleOpenFirstReadyToggle(D.viewer.open_first_ready_scene);
    handleAutoNextToggle(D.viewer.auto_next_scene);
    saveViewerStyle(D.viewer.viewer_style);
    saveCinemaDrift(D.viewer.cinema_drift);
    saveCameraStrip(D.viewer.camera_strip);
    setStreaming({ bufferTargetSeconds: D.viewer.buffer_target_seconds });
    setStreaming({ memoryLimitBytes: D.viewer.client_memory_limit_bytes });
    setStreaming({ showStreamStats: D.viewer.show_stream_stats });
    setViewerDefaults({ pointSize: D.viewer.viewer_point_size });
    setViewerDefaults({ defaultViewpoint: D.viewer.default_viewpoint });
    saveThemeChoice(D.viewer.theme_choice);
  };

  const resetDatasets = () => {
    saveDatasetGrouping(D.datasets.dataset_grouping);
  };

  const resetUpdates = () => {
    handleAutoUpdateToggle(D.updates.auto_download_updates);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "14px", minWidth: "260px" }}>
      <SettingsGroup title="General" onReset={resetGeneral}>
        <SettingRow
          checked={reopenLast}
          title="Reopen last session on launch"
          description={`When unchecked, Coperceive starts at the Connect screen. ${defaultNote(onOff(D.general.reopen_last_session))}`}
          onChange={handleToggle}
        />
        {storageRoot && (
          <div
            style={{
              borderTop: "1px solid var(--line-subtle)",
              padding: "8px 0",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "12px",
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: "13px", fontWeight: 500, color: "var(--text)" }}>
                Local library folder
              </div>
              <div
                style={{
                  fontSize: "11.5px",
                  color: "var(--text-tertiary)",
                  fontFamily: "'Geist Mono', ui-monospace, monospace",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {storageRoot}
              </div>
              <div style={{ fontSize: "12px", color: "var(--text-tertiary)", lineHeight: 1.4 }}>
                Moved from the Library (Change location); Reset to defaults leaves it where it is. Default: ~/coperceive-data.
              </div>
            </div>
            <Button variant="secondary" size="sm" onClick={handleShowInFinder}>
              {getRevealLabel()}
            </Button>
          </div>
        )}
      </SettingsGroup>

      <SettingsGroup title="Viewer" onReset={resetViewer}>
        <SettingRow
          checked={openFirstReady}
          title="Open the viewer when the first scene is ready"
          description={`After preparing a cluster dataset, show its first ready scene; the rest appear in the viewer's scene menu as they finish. ${defaultNote(onOff(D.viewer.open_first_ready_scene))}`}
          onChange={handleOpenFirstReadyToggle}
        />
        <div style={{ borderTop: "1px solid var(--line-subtle)" }}>
          <SettingRow
            checked={autoNextScene}
            title="Continue to the next scene automatically"
            description={`When a scene ends, play the next prepared one. Off: a note offers it instead. ${defaultNote(onOff(D.viewer.auto_next_scene))}`}
            onChange={handleAutoNextToggle}
          />
        </div>
        <ViewerStyleSettings />
        <div
          role="radiogroup"
          aria-label="Theme"
          style={{ borderTop: "1px solid var(--line-subtle)", display: "flex", flexDirection: "column" }}
        >
          <ChoiceHeading
            title="Theme"
            hint={`Light or dark app and viewer; the viewer toolbar's sun/moon button changes the same setting. Only Classic has a light look. ${defaultNote(LABEL_FOR_THEME.get(D.viewer.theme_choice) ?? "")}`}
          />
          {THEME_CHOICE_ROWS.map(([value, title, description]) => (
            <SettingRow
              key={value}
              type="radio"
              name="theme-choice"
              value={value}
              checked={themeChoice === value}
              title={title}
              description={description || undefined}
              onChange={() => saveThemeChoice(value)}
            />
          ))}
        </div>
        <div
          role="radiogroup"
          aria-label="Camera view"
          style={{ borderTop: "1px solid var(--line-subtle)", display: "flex", flexDirection: "column" }}
        >
          <ChoiceHeading
            title="Camera view"
            hint={`The view a scene opens in; a view the scene cannot show opens in Orbit. ${defaultNote(LABEL_FOR_VIEW.get(D.viewer.default_viewpoint) ?? "")}`}
          />
          {CAMERA_VIEW_CHOICES.map(([value, title, description]) => (
            <SettingRow
              key={value ?? "last"}
              type="radio"
              name="default-viewpoint"
              value={value ?? "last"}
              checked={viewerDefaults.defaultViewpoint === value}
              title={title}
              description={description}
              onChange={() => setViewerDefaults({ defaultViewpoint: value })}
            />
          ))}
        </div>
        <div
          role="radiogroup"
          aria-label="Point size"
          style={{ borderTop: "1px solid var(--line-subtle)", display: "flex", flexDirection: "column" }}
        >
          <ChoiceHeading
            title="Point size"
            hint={`Lidar points, in screen pixels, for a view whose toolbar Point size was not changed. ${defaultNote(`${D.viewer.viewer_point_size} px`)}`}
          />
          {POINT_SIZES.map((size) => (
            <SettingRow
              key={size}
              type="radio"
              name="viewer-point-size"
              value={String(size)}
              checked={viewerDefaults.pointSize === size}
              title={`${size} px`}
              onChange={() => setViewerDefaults({ pointSize: size })}
            />
          ))}
        </div>
        <div
          role="radiogroup"
          aria-label="Camera strip"
          style={{ borderTop: "1px solid var(--line-subtle)", display: "flex", flexDirection: "column" }}
        >
          <ChoiceHeading
            title="Camera strip"
            hint={`The camera thumbnails under the 3D view. ${defaultNote(LABEL_FOR_STRIP.get(D.viewer.camera_strip) ?? "")}`}
          />
          {CAMERA_STRIP_CHOICES.map(([value, title, description]) => (
            <SettingRow
              key={value}
              type="radio"
              name="camera-strip"
              value={value}
              checked={cameraStrip === value}
              title={title}
              description={description}
              onChange={() => setCameraStrip(value)}
            />
          ))}
        </div>
        <div
          role="radiogroup"
          aria-label="Streaming buffer"
          style={{ borderTop: "1px solid var(--line-subtle)", display: "flex", flexDirection: "column" }}
        >
          <ChoiceHeading
            title="Streaming buffer"
            hint={`Seconds of playback loaded ahead of the playhead. ${defaultNote(`${D.viewer.buffer_target_seconds} s`)}`}
          />
          {BUFFER_TARGET_CHOICES.map((seconds) => (
            <SettingRow
              key={seconds}
              type="radio"
              name="buffer-target"
              value={String(seconds)}
              checked={streaming.bufferTargetSeconds === seconds}
              title={`${seconds} s ahead`}
              description={
                seconds === 3
                  ? "Default. Playback pauses and says Buffering… rather than stuttering when the link falls behind."
                  : seconds === 1
                    ? "Less memory for buffering; playback rebuffers sooner on a slow link."
                    : "Extra headroom for a slow link; fills for longer before playback starts."
              }
              onChange={() => setStreaming({ bufferTargetSeconds: seconds })}
            />
          ))}
        </div>
        <div
          role="radiogroup"
          aria-label="Client memory limit"
          style={{ borderTop: "1px solid var(--line-subtle)", display: "flex", flexDirection: "column" }}
        >
          <ChoiceHeading
            title="Client memory limit"
            hint={`Memory for the viewer's frame and point cache, in MB. ${defaultNote(`${D.viewer.client_memory_limit_bytes / (1024 * 1024)} MB`)}`}
          />
          {MEMORY_LIMIT_CHOICES_MB.map((megabytes) => (
            <SettingRow
              key={megabytes}
              type="radio"
              name="client-memory-limit"
              value={String(megabytes)}
              checked={streaming.memoryLimitBytes === megabytes * 1024 * 1024}
              title={`${megabytes} MB`}
              description={
                megabytes === 512
                  ? "Default. Cached frame documents and point clouds are evicted oldest-first to stay under this."
                  : megabytes === 256
                    ? "Less memory; the buffer target may not be reached on dense scenes."
                    : "More memory for the buffer on dense scenes."
              }
              onChange={() => setStreaming({ memoryLimitBytes: megabytes * 1024 * 1024 })}
            />
          ))}
        </div>
        <div style={{ borderTop: "1px solid var(--line-subtle)" }}>
          <SettingRow
            checked={streaming.showStreamStats}
            title="Show streaming stats"
            description={`A small line under the status line: buffer ahead, MB/s and frame-load p50/p95. ${defaultNote(onOff(D.viewer.show_stream_stats))}`}
            onChange={(checked) => setStreaming({ showStreamStats: checked })}
          />
        </div>
      </SettingsGroup>

      <SettingsGroup title="Datasets" onReset={resetDatasets}>
        <div role="radiogroup" aria-label="Group scenes" style={{ display: "flex", flexDirection: "column" }}>
          <ChoiceHeading
            title="Group scenes"
            hint={`How the dataset view lists its scenes. ${defaultNote(LABEL_FOR_GROUPING.get(D.datasets.dataset_grouping) ?? "")}`}
          />
          {GROUPING_CHOICES.map(([value, title, description]) => (
            <SettingRow
              key={value}
              type="radio"
              name="dataset-grouping"
              value={value}
              checked={grouping === value}
              title={title}
              description={description}
              onChange={() => setGrouping(value)}
            />
          ))}
        </div>
      </SettingsGroup>

      {/* Cluster commands need the main window's backend access. */}
      {isViewerWindow() ? null : (
        <div className="settings-cluster-card">
          <ClusterDatasetSettings />
        </div>
      )}

      {isViewerWindow() ? null : (
        <div className="settings-cluster-card">
          <ModelRunSettings />
        </div>
      )}

      <SettingsGroup title="Maps">
        <CarlaMapsSettings />
      </SettingsGroup>

      <SettingsGroup title="Updates" onReset={resetUpdates}>
        <SettingRow
          checked={autoUpdate}
          title="Automatically download updates"
          description={`Prompt when new signed releases are available. ${defaultNote(onOff(D.updates.auto_download_updates))}`}
          onChange={handleAutoUpdateToggle}
        />
      </SettingsGroup>
    </div>
  );
}


export interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsModal({ open, onClose }: SettingsModalProps) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && open) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.65)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
        padding: "20px",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "560px",
          maxHeight: "86vh",
          backgroundColor: "var(--panel, #1a1a1c)",
          border: "1px solid var(--line-elevated, rgba(255, 255, 255, 0.14))",
          borderRadius: "10px",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.5)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "44px",
            padding: "0 18px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid var(--line, rgba(255, 255, 255, 0.08))",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: "13.5px", fontWeight: 600, color: "var(--text, #f5f5f7)" }}>
            Settings
          </span>
          <IconButton size="sm" label="Close settings (Esc)" onClick={onClose}>
            ✕
          </IconButton>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "16px 18px 24px" }}>
          <DesktopPreferences />
        </div>

        <div
          style={{
            height: "44px",
            padding: "0 18px",
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            borderTop: "1px solid var(--line, rgba(255, 255, 255, 0.08))",
            backgroundColor: "var(--sunken, #18181a)",
            flexShrink: 0,
          }}
        >
          <Button variant="primary" size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}
