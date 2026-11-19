// SPDX-License-Identifier: Apache-2.0

declare global {
  interface Window {
    __COPERCEIVE_OS__?: "macos" | "windows" | "linux";
    __COPERCEIVE_TITLEBAR__?: string;
  }
}

export function isMac(): boolean {
  if (typeof window !== "undefined" && window.__COPERCEIVE_OS__) {
    return window.__COPERCEIVE_OS__ === "macos";
  }
  if (typeof navigator !== "undefined") {
    const p = (navigator.platform || "").toLowerCase();
    const ua = (navigator.userAgent || "").toLowerCase();
    return p.includes("mac") || ua.includes("macintosh") || ua.includes("mac os");
  }
  return true;
}

export function isWindows(): boolean {
  if (typeof window !== "undefined" && window.__COPERCEIVE_OS__) {
    return window.__COPERCEIVE_OS__ === "windows";
  }
  if (typeof navigator !== "undefined") {
    const p = (navigator.platform || "").toLowerCase();
    const ua = (navigator.userAgent || "").toLowerCase();
    return p.includes("win") || ua.includes("windows");
  }
  return false;
}

export function isLinux(): boolean {
  if (typeof window !== "undefined" && window.__COPERCEIVE_OS__) {
    return window.__COPERCEIVE_OS__ === "linux";
  }
  if (typeof navigator !== "undefined") {
    const p = (navigator.platform || "").toLowerCase();
    const ua = (navigator.userAgent || "").toLowerCase();
    return p.includes("linux") || ua.includes("linux");
  }
  return false;
}

export function isTitlebarOverlay(): boolean {
  if (typeof window !== "undefined" && window.__COPERCEIVE_TITLEBAR__) {
    return window.__COPERCEIVE_TITLEBAR__ === "overlay";
  }
  // Default to overlay on macOS, native on others
  return isMac();
}

export function getModifierKey(): "⌘" | "Ctrl" {
  return isMac() ? "⌘" : "Ctrl";
}

export function getRevealLabel(): string {
  if (isWindows()) {
    return "Show in Explorer";
  }
  if (isLinux()) {
    return "Show in folder";
  }
  return "Show in Finder";
}

export function getSshConfigLabel(): string {
  if (isWindows()) {
    return "%USERPROFILE%\\.ssh\\config";
  }
  return "~/.ssh/config";
}

export function formatShortcut(macShortcut: string): string {
  if (isMac()) {
    return macShortcut;
  }
  return macShortcut
    .replace(/⌘/g, "Ctrl+")
    .replace(/Option\+/gi, "Alt+")
    .replace(/⌥/g, "Alt+");
}
