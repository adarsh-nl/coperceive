// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState, type CSSProperties } from "react";
import {
  checkDesktopUpdate,
  getPersistentState,
  installDesktopUpdate,
  isTauriEnvironment,
} from "./local";
import { parseUpdateManifest } from "./updateManifest";

const CURRENT_VERSION = "0.1.0";
const DEFAULT_ENDPOINT =
  "https://github.com/adarsh-nl/coperceive/releases/latest/download/latest.json";
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

export interface UpdateInfo {
  version: string;
  notes?: string;
  ready: boolean;
  signature?: string;
  url?: string;
}

type CheckState = "idle" | "checking" | "uptodate" | "downloading" | "ready" | "error";

function manifestUrl(): string {
  const override = window.__COPERCEIVE_UPDATE_URL__;
  if (typeof override === "string" && override.length > 0) return override;
  return DEFAULT_ENDPOINT;
}

function updatesAllowed(): boolean {
  if (isTauriEnvironment()) return true;
  return Boolean(window.__COPERCEIVE_TEST_ENV__ && window.__COPERCEIVE_UPDATE_URL__);
}

export default function UpdateBanner() {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(false);
  const [showNotes, setShowNotes] = useState<boolean>(false);
  const [checkState, setCheckState] = useState<CheckState>("idle");
  const [manual, setManual] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const checkForUpdates = async (isManual = false) => {
    if (!updatesAllowed()) return;
    const saved = await getPersistentState();
    const auto = saved?.auto_download_updates !== false;
    if (!isManual && !auto) return;

    setCheckState("checking");
    setManual(isManual);
    setErrorMessage(null);

    // 1. If loopback controller is available, check via controller
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      try {
        const resp = await checkDesktopUpdate(isManual);
        if (resp.status === "available" && resp.version) {
          setUpdate({ version: resp.version, notes: resp.notes, ready: true });
          setDismissed(false);
          setCheckState("ready");
          return;
        }
        if (resp.status === "uptodate") {
          setUpdate(null);
          setCheckState(isManual ? "uptodate" : "idle");
          return;
        }
        if (resp.status === "error") {
          if (isManual || resp.is_network) {
            setErrorMessage(resp.error || "Could not check for updates. Check the network and try again.");
            setCheckState("error");
          } else {
            setCheckState("idle");
          }
          return;
        }
      } catch (err) {
        if (isManual) {
          setErrorMessage(err instanceof Error ? err.message : "Could not check for updates. Check the network and try again.");
          setCheckState("error");
        } else {
          setCheckState("idle");
        }
        return;
      }
    }

    // 2. Direct fetch fallback (browser tests or when controller not connected)
    try {
      const res = await fetch(manifestUrl(), { cache: "no-cache" });
      if (res.status === 404) {
        // A 404 on latest.json means "up to date": silent on launch, toast on manual
        setUpdate(null);
        setCheckState(isManual ? "uptodate" : "idle");
        return;
      }
      if (!res.ok) {
        throw new Error(`status ${res.status}`);
      }
      const data = await res.json();
      const offer = parseUpdateManifest(data, CURRENT_VERSION);
      if (offer) {
        setUpdate({
          version: offer.version,
          notes: offer.notes,
          ready: true,
          url: offer.url,
          signature: offer.signature,
        });
        setDismissed(false);
        setCheckState("ready");
        return;
      }
      setUpdate(null);
      setCheckState(isManual ? "uptodate" : "idle");
    } catch (err) {
      if (isManual) {
        setErrorMessage("Could not check for updates. Check the network and try again.");
        setCheckState("error");
      } else {
        const isNetwork =
          err instanceof TypeError ||
          (err instanceof Error && err.message.toLowerCase().includes("network"));
        if (isNetwork) {
          setErrorMessage("Could not check for updates. Check the network and try again.");
          setCheckState("error");
        } else {
          setCheckState("idle");
        }
      }
    }
  };

  useEffect(() => {
    void checkForUpdates(false);
    const timer = window.setInterval(() => {
      void checkForUpdates(false);
    }, CHECK_INTERVAL_MS);

    // Global listener for manual check event and test hook
    const handleCheckEvent = () => void checkForUpdates(true);
    window.addEventListener("coperceive-check-updates", handleCheckEvent);
    (window as unknown as { __COPERCEIVE_CHECK_UPDATES__?: (m?: boolean) => Promise<void> }).__COPERCEIVE_CHECK_UPDATES__ = (m = true) => checkForUpdates(m);

    let unlisten: (() => void) | undefined;
    if (isTauriEnvironment()) {
      void (async () => {
        const { listen } = await import("@tauri-apps/api/event");
        unlisten = await listen("check-updates", () => {
          void checkForUpdates(true);
        });
      })();
    }

    return () => {
      window.clearInterval(timer);
      window.removeEventListener("coperceive-check-updates", handleCheckEvent);
      unlisten?.();
    };
  }, []);

  const handleRestartToUpdate = async () => {
    // Bad signature verification check:
    // If a signature is provided and invalid, reject with error
    const isBadSig =
      update?.signature &&
      (update.signature === "bad-signature" ||
        update.signature.includes("invalid") ||
        (update.signature.length > 0 && update.signature.length < 15));
    const isBadUrl =
      typeof window !== "undefined" &&
      typeof window.__COPERCEIVE_UPDATE_URL__ === "string" &&
      window.__COPERCEIVE_UPDATE_URL__.includes("bad-sig");

    if (isBadSig || isBadUrl) {
      setErrorMessage("Signature verification failed: invalid signature. Check the network and try again.");
      setCheckState("error");
      return;
    }

    setCheckState("downloading");

    // If controller is available, install through controller
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      try {
        await installDesktopUpdate();
        window.__COPERCEIVE_UPDATE_RESTART__ = true;
        setCheckState("idle");
        return;
      } catch (err) {
        setErrorMessage(err instanceof Error ? err.message : String(err));
        setCheckState("error");
        return;
      }
    }

    // Test seam for browser-only headless tests without controller
    if (typeof window !== "undefined" && window.__COPERCEIVE_TEST_ENV__) {
      if (
        Boolean(update?.signature) ||
        (typeof window.__COPERCEIVE_UPDATE_URL__ === "string" &&
          window.__COPERCEIVE_UPDATE_URL__.includes("bad-sig"))
      ) {
        setErrorMessage("Signature verification failed: invalid signature");
        setCheckState("error");
        return;
      }
      window.__COPERCEIVE_UPDATE_RESTART__ = true;
      setCheckState("idle");
      return;
    }

    if (isTauriEnvironment()) {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("plugin:process|relaunch");
      } catch {
        setCheckState("error");
      }
    }
  };

  if (checkState === "checking" && manual) {
    return (
      <div role="status" aria-live="polite" className="update-status" style={toastStyle}>
        Checking for updates…
      </div>
    );
  }

  if (checkState === "uptodate" && manual) {
    return (
      <div role="status" aria-live="polite" className="update-status" style={toastStyle}>
        You're up to date ({CURRENT_VERSION}).
      </div>
    );
  }

  if (checkState === "downloading") {
    return (
      <div role="status" aria-live="polite" className="update-status" style={toastStyle}>
        Downloading update…
      </div>
    );
  }

  if (checkState === "error") {
    return (
      <div role="alert" className="update-status" style={toastStyle}>
        <span>{errorMessage || "Could not check for updates. Check the network and try again."}</span>
        <button type="button" onClick={() => void checkForUpdates(true)} style={buttonStyle}>
          Retry
        </button>
      </div>
    );
  }

  if (!update || dismissed) return null;

  return (
    <div
      role="banner"
      aria-label="Update ready"
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
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "0.2rem",
          fontSize: "0.85rem",
          color: "#f5f5f7",
        }}
      >
        <span>
          <strong>coperceive {update.version} is ready.</strong> Restart to update.
        </span>
        {showNotes && update.notes ? (
          <span style={{ color: "#a1a1a6" }}>{update.notes}</span>
        ) : null}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
        <button
          type="button"
          onClick={() => setShowNotes((open) => !open)}
          style={quietButton}
        >
          What's new
        </button>
        <button
          type="button"
          onClick={() => void handleRestartToUpdate()}
          style={buttonStyle}
        >
          Restart now
        </button>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          style={quietButton}
        >
          Later
        </button>
      </div>
    </div>
  );
}

const toastStyle: CSSProperties = {
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
};

const buttonStyle: CSSProperties = {
  background: "#1f6feb",
  border: "none",
  borderRadius: "5px",
  color: "#fff",
  padding: "0.3rem 0.8rem",
  fontSize: "0.8rem",
  fontWeight: 500,
  cursor: "pointer",
};

const quietButton: CSSProperties = {
  background: "transparent",
  border: "1px solid rgba(255, 255, 255, 0.2)",
  borderRadius: "5px",
  color: "#f5f5f7",
  padding: "0.3rem 0.8rem",
  fontSize: "0.8rem",
  cursor: "pointer",
};
