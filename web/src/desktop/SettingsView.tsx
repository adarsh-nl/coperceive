// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useState } from "react";
import { ClusterDatasetSettings } from "./ClusterDatasetSettings";
import {
  getPersistentState,
  getStorageInfo,
  isViewerWindow,
  revealInFinder,
  setAutoDownloadUpdates,
} from "./local";
import { SettingRow } from "./components/SettingRow";
import { Button } from "./components/Button";
import { IconButton } from "./components/IconButton";
import { useDatasetGrouping } from "./hpc/groupingSetting";
import type { DatasetGrouping } from "./hpc/grouping";

function SettingsGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-label={title} style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <h3
        style={{
          margin: 0,
          padding: "0 4px",
          fontSize: "11px",
          fontWeight: 600,
          letterSpacing: "0.03em",
          textTransform: "uppercase",
          color: "var(--text-subtle)",
        }}
      >
        {title}
      </h3>
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

const GROUPING_CHOICES: ReadonlyArray<readonly [DatasetGrouping, string, string]> = [
  ["none", "None", "One list, sorted by scene name."],
  ["state", "By state", "Failed, stale, preparing, queued, ready, then not prepared."],
  ["day", "By recording day", "From scene names that hold a date (YYYY_MM_DD_HH_MM_SS): the day the data was recorded."],
];

export function DesktopPreferences() {
  const [grouping, setGrouping] = useDatasetGrouping();
  const [reopenLast, setReopenLast] = useState<boolean>(true);
  const [autoUpdate, setAutoUpdate] = useState<boolean>(true);
  const [autoNextScene, setAutoNextScene] = useState<boolean>(false);
  const [openFirstReady, setOpenFirstReady] = useState<boolean>(true);
  const [storageRoot, setStorageRoot] = useState<string>("");

  useEffect(() => {
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      const port = window.__COPERCEIVE_CTRL_PORT__;
      const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
      fetch(`http://127.0.0.1:${port}/state`, {
        headers: { Authorization: `Bearer ${secret}` },
      })
        .then((r) => r.json())
        .then(
          (data: {
            reopen_last_session?: boolean;
            auto_next_scene?: boolean;
            open_first_ready_scene?: boolean;
          }) => {
            if (data && typeof data.reopen_last_session === "boolean") {
              setReopenLast(data.reopen_last_session);
            }
            if (data && typeof data.auto_next_scene === "boolean") {
              setAutoNextScene(data.auto_next_scene);
            }
            if (data && typeof data.open_first_ready_scene === "boolean") {
              setOpenFirstReady(data.open_first_ready_scene);
            }
          },
        )
        .catch(() => {});
    }

    void (async () => {
      const saved = await getPersistentState();
      if (saved && typeof saved.auto_download_updates === "boolean") {
        setAutoUpdate(saved.auto_download_updates);
      }
      const stats = await getStorageInfo();
      if (stats?.library_root) {
        setStorageRoot(stats.library_root);
      }
    })();
  }, []);

  const handleToggle = (checked: boolean) => {
    setReopenLast(checked);
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      const port = window.__COPERCEIVE_CTRL_PORT__;
      const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
      fetch(`http://127.0.0.1:${port}/state`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${secret}`,
        },
        body: JSON.stringify({ reopen_last_session: checked }),
      }).catch(() => {});
    }
  };

  const handleAutoNextToggle = (checked: boolean) => {
    setAutoNextScene(checked);
    window.dispatchEvent(new CustomEvent("coperceive-auto-next-scene", { detail: checked }));
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      const port = window.__COPERCEIVE_CTRL_PORT__;
      const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
      fetch(`http://127.0.0.1:${port}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${secret}` },
        body: JSON.stringify({ auto_next_scene: checked }),
      }).catch(() => {});
    }
  };

  const handleOpenFirstReadyToggle = (checked: boolean) => {
    setOpenFirstReady(checked);
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      const port = window.__COPERCEIVE_CTRL_PORT__;
      const secret = window.__COPERCEIVE_CTRL_SECRET__ || "";
      fetch(`http://127.0.0.1:${port}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${secret}` },
        body: JSON.stringify({ open_first_ready_scene: checked }),
      }).catch(() => {});
    }
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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "14px", minWidth: "260px" }}>
      <SettingsGroup title="General">
        <SettingRow
          checked={reopenLast}
          title="Reopen last session on launch"
          description="When unchecked, Coperceive starts at the Connect screen."
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
            </div>
            <Button variant="secondary" size="sm" onClick={handleShowInFinder}>
              Show in Finder
            </Button>
          </div>
        )}
      </SettingsGroup>

      <SettingsGroup title="Viewer">
        <SettingRow
          checked={openFirstReady}
          title="Open the viewer when the first scene is ready"
          description="After preparing a cluster dataset, show its first ready scene; the rest appear in the viewer's scene menu as they finish."
          onChange={handleOpenFirstReadyToggle}
        />
        <div style={{ borderTop: "1px solid var(--line-subtle)" }}>
          <SettingRow
            checked={autoNextScene}
            title="Continue to the next scene automatically"
            description="When a scene ends, play the next prepared one. Off: a note offers it instead."
            onChange={handleAutoNextToggle}
          />
        </div>
      </SettingsGroup>

      <SettingsGroup title="Datasets">
        <div role="radiogroup" aria-label="Group scenes" style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "8px 0 2px", fontSize: "13px", fontWeight: 500, color: "var(--text)" }}>
            Group scenes
          </div>
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

      <SettingsGroup title="Updates">
        <SettingRow
          checked={autoUpdate}
          title="Automatically download updates"
          description="Prompt when new signed releases are available."
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
