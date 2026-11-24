// SPDX-License-Identifier: Apache-2.0
/**
 * Settings → Viewer → Theme (inventory L90): System, Light or Dark, stored in
 * state.json as `theme_override` (null = follow the system), with the viewer
 * toolbar's sun/moon button as a shortcut that writes the same field.
 *
 * A change is applied live in this window through a window event, and the
 * saved value is read again when a window gets focus, so a theme chosen in the
 * other window's Settings applies on return. The page's own copies (the
 * `coperceive-theme-override` localStorage key and the window-open snapshot)
 * are kept in step so `systemTheme()` never answers with a stale override.
 */
import { getViewerPreferences, updatePersistentState } from "../desktop/local";
import { clearThemeOverride, getThemeOverride, setThemeOverride, type ThemeName } from "./appearance";

export type ThemeChoice = "system" | ThemeName;

export const THEME_CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];

/** Settings' Theme when nothing is saved: follow the system. */
export const DEFAULT_THEME_CHOICE: ThemeChoice = "system";

/** Fired on window when the choice changes; detail is the new `ThemeChoice`. */
export const THEME_CHOICE_EVENT = "coperceive-theme-choice";

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

/** A state.json `theme_override` as a choice: anything but light/dark is System. */
export function themeChoiceFrom(themeOverride: unknown): ThemeChoice {
  return themeOverride === "light" || themeOverride === "dark" ? themeOverride : "system";
}

/** The state.json value for a choice. */
export function themeOverrideFor(choice: ThemeChoice): ThemeName | null {
  return choice === "system" ? null : choice;
}

/** The theme a choice shows, given whether the system asks for dark. */
export function resolveThemeChoice(choice: ThemeChoice, systemDark: boolean): ThemeName {
  if (choice === "system") return systemDark ? "dark" : "light";
  return choice;
}

export function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-color-scheme: dark)").matches);
}

/** What the window was opened with, before any read of state.json answers. */
export function initialThemeChoice(): ThemeChoice {
  return getThemeOverride() ?? "system";
}

/** Keep this page's own copies (localStorage, the window-open snapshot) in step. */
export function applyThemeChoiceLocally(choice: ThemeChoice): void {
  if (typeof window === "undefined") return;
  if (choice === "system") {
    clearThemeOverride();
    return;
  }
  setThemeOverride(choice);
  const init = window.__COPERCEIVE_INITIAL_STATE__ as { theme_override?: string } | undefined;
  if (init) init.theme_override = choice;
}

/** The saved choice; null when there is no app state to read (the web page). */
export async function loadThemeChoice(): Promise<ThemeChoice | null> {
  const saved = await getViewerPreferences().catch(() => null);
  return saved ? themeChoiceFrom(saved.theme_override) : null;
}

/** Choose a theme from Settings or the toolbar: apply here, tell this window, save. */
export function saveThemeChoice(choice: ThemeChoice): void {
  if (typeof window === "undefined") return;
  applyThemeChoiceLocally(choice);
  window.dispatchEvent(new CustomEvent(THEME_CHOICE_EVENT, { detail: choice }));
  void updatePersistentState({ theme_override: themeOverrideFor(choice) }).catch(() => undefined);
}

/**
 * Calls `listener` with the saved choice once it is read, on every change made
 * in this window, and again when the window gets focus. Returns the unsubscribe.
 */
export function subscribeThemeChoice(listener: (choice: ThemeChoice) => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  let active = true;
  let changed = false;
  const onChange = (event: Event) => {
    const next = (event as CustomEvent<unknown>).detail;
    if (!isThemeChoice(next)) return;
    changed = true;
    listener(next);
  };
  const reread = (initial: boolean) => {
    void loadThemeChoice().then((saved) => {
      // A choice made while the first read was answering wins.
      if (!active || saved === null || (initial && changed)) return;
      applyThemeChoiceLocally(saved);
      listener(saved);
    });
  };
  const onFocus = () => reread(false);
  window.addEventListener(THEME_CHOICE_EVENT, onChange);
  window.addEventListener("focus", onFocus);
  reread(true);
  return () => {
    active = false;
    window.removeEventListener(THEME_CHOICE_EVENT, onChange);
    window.removeEventListener("focus", onFocus);
  };
}
