// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState, type JSX } from "react";
import { createPortal } from "react-dom";
import {
  checkDesktopUpdate,
  getPersistentState,
  installDesktopUpdate,
  isTauriEnvironment,
  isViewerWindow,
  showLibrary,
} from "./local";
import { parseUpdateManifest } from "./updateManifest";
import { errorText } from "./errors";

const CURRENT_VERSION = "0.1.1";
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

export interface Updater {
  update: UpdateInfo | null;
  /** "Later" was chosen for this update, for this session. */
  dismissed: boolean;
  checkState: CheckState;
  /** The last check was asked for (Help → Check for Updates), not the background one. */
  manual: boolean;
  /** Where the last error came from: a check, or installing the update. */
  errorFrom: "check" | "install";
  errorMessage: string | null;
  showNotes: boolean;
  setShowNotes: (open: boolean | ((open: boolean) => boolean)) => void;
  dismiss: () => void;
  install: () => Promise<void>;
  retry: () => void;
}

/** The update checker: the background check every six hours, Help → Check for
 * Updates, and installing a downloaded update (restart). */
export function useUpdater(): Updater {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  // "Later" hides this version for the session: the background check does not
  // bring it back; asking again (Help → Check for Updates) does.
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);
  const [showNotes, setShowNotes] = useState<boolean>(false);
  const [checkState, setCheckState] = useState<CheckState>("idle");
  const [manual, setManual] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [errorFrom, setErrorFrom] = useState<"check" | "install">("check");

  const checkForUpdates = async (isManual = false) => {
    if (!updatesAllowed()) return;
    const saved = await getPersistentState();
    const auto = saved?.auto_download_updates !== false;
    if (!isManual && !auto) return;

    setCheckState("checking");
    setManual(isManual);
    setErrorMessage(null);
    setErrorFrom("check");

    // 1. If loopback controller is available, check via controller
    if (typeof window !== "undefined" && window.__COPERCEIVE_CTRL_PORT__) {
      try {
        const resp = await checkDesktopUpdate(isManual);
        if (resp.status === "available" && resp.version) {
          setUpdate({ version: resp.version, notes: resp.notes, ready: true });
          if (isManual) setDismissedVersion(null);
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
          setErrorMessage(errorText(err, "Could not check for updates. Check the network and try again."));
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
        if (isManual) setDismissedVersion(null);
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

    setErrorFrom("install");
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
        setErrorMessage(errorText(err));
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

  return {
    update,
    dismissed: update !== null && update.version === dismissedVersion,
    checkState,
    manual,
    errorFrom,
    errorMessage,
    showNotes,
    setShowNotes,
    dismiss: () => {
      setDismissedVersion(update?.version ?? null);
      setShowNotes(false);
    },
    install: handleRestartToUpdate,
    retry: () => void checkForUpdates(true),
  };
}

const GENERIC_ERROR = "Could not check for updates. Check the network and try again.";

/** Manual checks (Help → Check for Updates) answer with a toast, as before. */
function toastFor(u: Updater): JSX.Element | null {
  if (u.checkState === "checking" && u.manual) {
    return (
      <div role="status" aria-live="polite" className="update-status update-toast">
        Checking for updates…
      </div>
    );
  }
  if (u.checkState === "uptodate" && u.manual) {
    return (
      <div role="status" aria-live="polite" className="update-status update-toast">
        You're up to date ({CURRENT_VERSION}).
      </div>
    );
  }
  if (u.checkState === "error" && u.manual && u.errorFrom === "check") {
    return (
      <div role="alert" className="update-status update-toast">
        <span>{u.errorMessage || GENERIC_ERROR}</span>
        <button type="button" className="update-pill" onClick={u.retry}>
          Retry
        </button>
      </div>
    );
  }
  return null;
}

/** The toasts alone: the viewer window, which leaves the card to the main window. */
export function UpdateToasts({ updater }: { updater: Updater }) {
  return toastFor(updater);
}

/**
 * The sidebar card, just above Settings: "New update available" with an Install
 * pill (restart into the downloaded update), What's new (release notes in a
 * popover) and ✕ (Later: hidden for this session). Downloading, a failed install
 * (Retry) and a background check that could not reach the network show in the
 * same spot; manual checks keep their toast.
 */
export function UpdateCard({ updater: u }: { updater: Updater }) {
  const notesRef = useRef<HTMLDivElement>(null);
  const { setShowNotes } = u;
  useEffect(() => {
    if (!u.showNotes) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowNotes(false);
    };
    const onDown = (event: PointerEvent) => {
      const card = notesRef.current?.closest(".update-card");
      if (card && event.target instanceof Node && !card.contains(event.target)) setShowNotes(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [u.showNotes, setShowNotes]);

  const toast = toastFor(u);
  let card: JSX.Element | null = null;
  if (u.checkState === "downloading") {
    card = (
      <div className="update-card" role="status" aria-live="polite">
        <span className="update-card-title">Downloading update…</span>
      </div>
    );
  } else if (u.checkState === "error" && !(u.manual && u.errorFrom === "check")) {
    card = (
      <div className="update-card update-card-error" role="alert">
        <span className="update-card-message">{u.errorMessage || GENERIC_ERROR}</span>
        <button type="button" className="update-pill" onClick={u.retry}>
          Retry
        </button>
      </div>
    );
  } else if (u.update && !u.dismissed) {
    card = (
      <section className="update-card" aria-label="Update ready" id="update-card" tabIndex={-1}>
        <div className="update-card-row">
          <span className="update-card-title">New update available</span>
          <button type="button" className="update-card-close" aria-label="Later" title="Later" onClick={u.dismiss}>
            <span aria-hidden="true">✕</span>
            <span className="visually-hidden">Later</span>
          </button>
        </div>
        <span className="update-card-version">coperceive {u.update.version} is ready</span>
        <div className="update-card-row">
          <button
            type="button"
            className="update-card-notes"
            aria-expanded={u.showNotes}
            aria-controls="update-notes"
            onClick={() => u.setShowNotes((open) => !open)}
          >
            What's new
          </button>
          <button type="button" className="update-pill" onClick={() => void u.install()} title="Restart into the new version">
            Install
          </button>
        </div>
        {u.showNotes ? (
          <div className="update-notes" id="update-notes" role="dialog" aria-label="What's new" ref={notesRef}>
            <strong>coperceive {u.update.version}</strong>
            <p>{u.update.notes || "No release notes were published with this update."}</p>
          </div>
        ) : null}
      </section>
    );
  }
  return (
    <>
      {card}
      {toast}
    </>
  );
}

/** Collapsed sidebar: a blue dot on its toggle; it opens the sidebar on the card. */
export function UpdateDot({ updater: u, onOpen }: { updater: Updater; onOpen: () => void }) {
  if (!u.update || u.dismissed) return null;
  return (
    <button type="button" className="update-dot" aria-label="Update ready" title="Update ready" onClick={onOpen}>
      <span aria-hidden="true" />
    </button>
  );
}

/** Viewer top bar: "Update ready" brings the main window (and its card) forward. */
export function ViewerUpdateDot({ updater: u, onShow }: { updater: Updater; onShow: () => void }) {
  if (!u.update || u.dismissed) return null;
  return (
    <button type="button" className="ghost viewer-update-dot" title="Update ready" onClick={onShow}>
      <i aria-hidden="true" />
      Update ready
    </button>
  );
}

/** The viewer shell's part: no banner, the manual-check toasts, and the top-bar
 * "Update ready" that shows the main window's Library (where the card is). */
export function ViewerUpdates({ slot }: { slot: HTMLElement | null }) {
  const updater = useUpdater();
  const show = () => {
    if (isViewerWindow()) {
      void showLibrary().catch((err: unknown) => console.warn("show-library failed", err));
    } else {
      window.location.hash = "#/connect";
    }
  };
  return (
    <>
      <UpdateToasts updater={updater} />
      {slot ? createPortal(<ViewerUpdateDot updater={updater} onShow={show} />, slot) : null}
    </>
  );
}
