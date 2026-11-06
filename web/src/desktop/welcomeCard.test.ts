// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://127.0.0.1/",
  pretendToBeVisual: true,
});
const globals = globalThis as unknown as Record<string, unknown>;
globals.window = dom.window;
globals.document = dom.window.document;
Object.defineProperty(globals, "navigator", { value: dom.window.navigator, configurable: true });
globals.HTMLElement = dom.window.HTMLElement;
globals.IS_REACT_ACT_ENVIRONMENT = true;

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const { WelcomeCard } = await import("./LibraryScreen.tsx");
const axeMod = await import("axe-core");
const axe = (axeMod as { default?: { run: typeof axeMod.run } }).default ?? axeMod;

test("welcome card renders required actions, description, and handles callbacks", async () => {
  const host = document.getElementById("root")!;
  const root = createRoot(host);

  let demoOpened = false;
  let sampleTried = false;
  let dismissed = false;

  await act(async () => {
    root.render(
      React.createElement(WelcomeCard, {
        generatingDemo: false,
        demoElapsedSec: 0,
        demoError: null,
        onOpenDemo: () => {
          demoOpened = true;
        },
        onTrySample: () => {
          sampleTried = true;
        },
        onDismiss: () => {
          dismissed = true;
        },
      }),
    );
  });

  const card = host.querySelector(".welcome-card");
  assert.ok(card, "welcome card is rendered");
  assert.equal(card.getAttribute("role"), "region");
  assert.equal(card.getAttribute("aria-label"), "Welcome to Coperceive");

  const heading = host.querySelector("h2");
  assert.ok(heading?.textContent?.includes("Welcome to Coperceive"));

  // Check buttons
  const buttons = [...host.querySelectorAll("button")];
  const demoBtn = buttons.find((b) => b.textContent?.includes("Open the demo"));
  assert.ok(demoBtn, "demo button rendered");
  assert.ok(demoBtn.textContent?.includes("Works offline"));

  const sampleBtn = buttons.find((b) => b.textContent?.includes("Try a real sample"));
  assert.ok(sampleBtn, "real sample button rendered");
  assert.ok(sampleBtn.textContent?.includes("Griffin, MIT licence"));
  assert.ok(sampleBtn.textContent?.includes("143 MB download"));

  const dismissBtn = buttons.find((b) => b.getAttribute("aria-label") === "Dismiss welcome card");
  assert.ok(dismissBtn, "dismiss button rendered with accessible label");

  // Click actions
  demoBtn.click();
  assert.equal(demoOpened, true);

  sampleBtn.click();
  assert.equal(sampleTried, true);

  dismissBtn.click();
  assert.equal(dismissed, true);

  // Re-render in generating state
  await act(async () => {
    root.render(
      React.createElement(WelcomeCard, {
        generatingDemo: true,
        demoElapsedSec: 4,
        demoError: null,
        onOpenDemo: () => {},
        onTrySample: () => {},
        onDismiss: () => {},
      }),
    );
  });

  const generatingBtn = [...host.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Generating demo scene… (8.2 MB) 4s"),
  );
  assert.ok(generatingBtn, "shows progress and size while generating");
  assert.equal(generatingBtn.disabled, true, "primary button disabled while generating");

  // Re-render with error
  await act(async () => {
    root.render(
      React.createElement(WelcomeCard, {
        generatingDemo: false,
        demoElapsedSec: 0,
        demoError: "Disk full",
        onOpenDemo: () => {},
        onTrySample: () => {},
        onDismiss: () => {},
      }),
    );
  });
  assert.ok(host.textContent?.includes("Failed to generate demo scene: Disk full"));

  // Accessibility check via axe-core
  const axeResults = await axe.run(host);
  assert.equal(axeResults.violations.length, 0, JSON.stringify(axeResults.violations, null, 2));

  root.unmount();
});
