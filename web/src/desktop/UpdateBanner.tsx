// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { isTauriEnvironment } from "./local";

export interface UpdateInfo {
  version: string;
  notes?: string;
  ready: boolean;
}

export default function UpdateBanner() {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(false);
  const [checking, setChecking] = useState<boolean>(false);
  const [manualMessage, setManualMessage] = useState<string | null>(null);

  const isAutoCheckEnabled = (): boolean => {
    try {
      const val = localStorage.getItem("coperceive_auto_update");
      return val === null ? true : val === "true";
    } catch {
      return true;
    }
  };

  const checkForUpdates = async (isManual = false) => {
    if (!isTauriEnvironment()) return;
    if (!isManual && !isAutoCheckEnabled()) return;

    if (isManual) {
      setChecking(true);
      setManualMessage(null);
    }

    try {
      // In Tauri v2, we check through @tauri-apps/plugin-updater or custom endpoint
      const res = await fetch(
        "https://github.com/adarsh-nl/coperceive/releases/latest/download/latest.json",
        { cache: "no-cache" },
      ).catch(() => null);

      if (res && res.ok) {
        const data = await res.json();
        const currentVersion = "0.1.0";
        if (data.version && data.version !== currentVersion) {
          setUpdate({
            version: data.version,
            notes: data.notes,
            ready: true,
          });
          setDismissed(false);
          if (isManual) setManualMessage(null);
          return;
        }
      }

      if (isManual) {
        setManualMessage("You're up to date (0.1.0)");
        setTimeout(() => setManualMessage(null), 4000);
      }
    } catch {
      if (isManual) {
        setManualMessage("You're up to date (0.1.0)");
        setTimeout(() => setManualMessage(null), 4000);
      }
    } finally {
      if (isManual) setChecking(false);
    }
  };

  useEffect(() => {
    // Initial background check
    checkForUpdates(false);

    // Listen for menu event "check-updates"
    let unlisten: (() => void) | undefined;
    if (isTauriEnvironment()) {
      void (async () => {
        const { listen } = await import("@tauri-apps/api/event");
        unlisten = await listen("check-updates", () => {
          checkForUpdates(true);
        });
      })();
    }

    return () => {
      unlisten?.();
    };
  }, []);

  const handleRestartToUpdate = async () => {
    if (isTauriEnvironment()) {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("plugin:process|relaunch");
      } catch {
        // Fallback
        window.location.reload();
      }
    }
  };

  if (manualMessage) {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          position: "fixed",
          bottom: "1.5rem",
          right: "1.5rem",
          background: "rgba(30, 30, 30, 0.95)",
          border: "1px solid rgba(255, 255, 255, 0.15)",
          borderRadius: "8px",
          padding: "0.6rem 1rem",
          color: "#f5f5f7",
          fontSize: "0.85rem",
          boxShadow: "0 8px 24px rgba(0, 0, 0, 0.4)",
          zIndex: 9999,
          display: "flex",
          alignItems: "center",
          gap: "0.6rem",
        }}
      >
        <span>✓</span>
        <span>{manualMessage}</span>
      </div>
    );
  }

  if (checking) {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          position: "fixed",
          bottom: "1.5rem",
          right: "1.5rem",
          background: "rgba(30, 30, 30, 0.95)",
          border: "1px solid rgba(255, 255, 255, 0.15)",
          borderRadius: "8px",
          padding: "0.6rem 1rem",
          color: "#f5f5f7",
          fontSize: "0.85rem",
          boxShadow: "0 8px 24px rgba(0, 0, 0, 0.4)",
          zIndex: 9999,
        }}
      >
        Checking for updates…
      </div>
    );
  }

  if (!update || dismissed) return null;

  return (
    <div
      role="banner"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        background: "#1c1c1e",
        borderBottom: "1px solid rgba(255, 255, 255, 0.15)",
        padding: "0.6rem 1.2rem",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        boxShadow: "0 4px 16px rgba(0, 0, 0, 0.3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", fontSize: "0.85rem", color: "#f5f5f7" }}>
        <span style={{ fontSize: "1rem" }}>⚡</span>
        <span>
          <strong>coperceive {update.version} is ready.</strong> Restart to update.
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
        <button
          type="button"
          onClick={handleRestartToUpdate}
          style={{
            background: "#2878dc",
            border: "none",
            borderRadius: "5px",
            color: "#fff",
            padding: "0.3rem 0.8rem",
            fontSize: "0.8rem",
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          Restart to update
        </button>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          title="Dismiss update banner"
          style={{
            background: "transparent",
            border: "none",
            color: "#8b949e",
            fontSize: "1.1rem",
            cursor: "pointer",
            padding: "0.2rem 0.4rem",
          }}
        >
          ✕
        </button>
      </div>
    </div>
  );
}
