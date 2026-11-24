// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { getThemeOverride, THEME_OVERRIDE_KEY } from "./appearance.ts";
import {
  applyThemeChoiceLocally,
  DEFAULT_THEME_CHOICE,
  resolveThemeChoice,
  saveThemeChoice,
  THEME_CHOICE_EVENT,
  THEME_CHOICES,
  themeChoiceFrom,
  themeOverrideFor,
} from "./themeSetting.ts";

const scope = globalThis as unknown as {
  window?: {
    __COPERCEIVE_INITIAL_STATE__?: { theme_override?: string };
    dispatchEvent?: (event: Event) => boolean;
  };
  localStorage?: Storage;
};

function memoryStorage(): Storage {
  const rows = new Map<string, string>();
  return {
    get length() {
      return rows.size;
    },
    clear: () => rows.clear(),
    getItem: (key) => rows.get(key) ?? null,
    key: (index) => [...rows.keys()][index] ?? null,
    removeItem: (key) => void rows.delete(key),
    setItem: (key, value) => void rows.set(key, String(value)),
  };
}

test("Theme is System, Light or Dark; System by default and stored as null", () => {
  assert.deepEqual([...THEME_CHOICES], ["system", "light", "dark"]);
  assert.equal(DEFAULT_THEME_CHOICE, "system");
  assert.equal(themeOverrideFor("system"), null);
  assert.equal(themeOverrideFor("dark"), "dark");
  assert.equal(themeChoiceFrom("light"), "light");
  assert.equal(themeChoiceFrom("dark"), "dark");
  for (const value of [null, undefined, "", "sepia", 1]) {
    assert.equal(themeChoiceFrom(value), "system", String(value));
  }
});

test("System follows the system's appearance; Light and Dark do not", () => {
  assert.equal(resolveThemeChoice("system", true), "dark");
  assert.equal(resolveThemeChoice("system", false), "light");
  assert.equal(resolveThemeChoice("light", true), "light");
  assert.equal(resolveThemeChoice("dark", false), "dark");
});

test("a choice keeps the page's own copies in step, so no stale override answers", () => {
  const events: Event[] = [];
  const previousWindow = scope.window;
  const previousStorage = scope.localStorage;
  // A viewer window opened with a saved dark override.
  scope.window = {
    __COPERCEIVE_INITIAL_STATE__: { theme_override: "dark" },
    dispatchEvent: (event: Event) => {
      events.push(event);
      return true;
    },
  };
  scope.localStorage = memoryStorage();
  try {
    scope.localStorage.setItem(THEME_OVERRIDE_KEY, "dark");
    assert.equal(getThemeOverride(), "dark");

    // Settings → Light: both copies say light.
    saveThemeChoice("light");
    assert.equal(getThemeOverride(), "light");
    assert.equal(scope.localStorage.getItem(THEME_OVERRIDE_KEY), "light");
    assert.equal(events.at(-1)?.type, THEME_CHOICE_EVENT);
    assert.equal((events.at(-1) as CustomEvent).detail, "light");

    // Settings → System: no override is left anywhere.
    saveThemeChoice("system");
    assert.equal(getThemeOverride(), null);
    assert.equal(scope.localStorage.getItem(THEME_OVERRIDE_KEY), null);
    assert.equal(scope.window.__COPERCEIVE_INITIAL_STATE__?.theme_override, undefined);
    assert.equal((events.at(-1) as CustomEvent).detail, "system");

    // A choice read back from state.json (another window) is applied the same way.
    applyThemeChoiceLocally("dark");
    assert.equal(getThemeOverride(), "dark");
  } finally {
    scope.window = previousWindow;
    scope.localStorage = previousStorage;
  }
});
