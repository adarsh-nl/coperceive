// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
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

export function DesktopPreferences() {
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
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", minWidth: "260px" }}>
      <SettingRow
        checked={reopenLast}
        title="Reopen last session on launch"
        description="When unchecked, Coperceive starts at the Connect screen."
        onChange={handleToggle}
      />
      <SettingRow
        checked={autoUpdate}
        title="Automatically download updates"
        description="Prompt when new signed releases are available."
        onChange={handleAutoUpdateToggle}
      />
      <SettingRow
        checked={openFirstReady}
        title="Open the viewer when the first scene is ready"
        description="After preparing a cluster dataset, show its first ready scene; the rest appear in the viewer's scene menu as they finish."
        onChange={handleOpenFirstReadyToggle}
      />
      <SettingRow
        checked={autoNextScene}
        title="Continue to the next scene automatically"
        description="When a scene ends, play the next prepared one. Off: a note offers it instead."
        onChange={handleAutoNextToggle}
      />
      {/* Cluster commands need the main window's backend access. */}
      {isViewerWindow() ? null : <ClusterDatasetSettings />}

      {storageRoot && (
        <div
          style={{
            marginTop: "0.75rem",
            borderTop: "1px solid var(--line, rgba(255, 255, 255, 0.1))",
            paddingTop: "0.75rem",
          }}
        >
          <div style={{ fontSize: "12px", fontWeight: 600, color: "var(--text)", marginBottom: "0.2rem" }}>
            Storage
          </div>
          <p
            className="muted"
            style={{
              fontSize: "11px",
              color: "var(--text-tertiary, #a1a1a6)",
              margin: "0 0 0.5rem 0",
              wordBreak: "break-all",
              fontFamily: "'Geist Mono', monospace",
            }}
          >
            {storageRoot}
          </p>
          <Button variant="secondary" size="sm" onClick={handleShowInFinder}>
            Show in Finder
          </Button>
        </div>
      )}
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
          maxWidth: "540px",
          maxHeight: "85vh",
          backgroundColor: "var(--panel, #1a1a1c)",
          border: "1px solid var(--line-elevated, rgba(255, 255, 255, 0.14))",
          borderRadius: "var(--radius-lg, 10px)",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.5)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "48px",
            padding: "0 20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid var(--line, rgba(255, 255, 255, 0.08))",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text, #f5f5f7)" }}>
            Settings
          </span>
          <IconButton size="sm" label="Close settings (Esc)" onClick={onClose}>
            ✕
          </IconButton>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "20px" }}>
          <DesktopPreferences />
        </div>

        <div
          style={{
            height: "48px",
            padding: "0 20px",
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
