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
(dom.window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {};

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const { ErrorBoundary } = await import("./ErrorBoundary.tsx");
const { installGlobalErrorRecorders } = await import("./globalErrors.ts");
const { _setDiagnosticsInvokeForTesting } = await import("./local.ts");

function child(throws: boolean) {
  function MaybeThrows() {
    if (throws) throw new Error("render exploded");
    return React.createElement("p", { id: "normal-child" }, "normal content");
  }
  return React.createElement(MaybeThrows);
}

function buttonByText(text: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((button) => button.textContent?.includes(text));
  assert.ok(found, `button ${text}`);
  return found as HTMLButtonElement;
}

test("render error shows the panel, reload recovers, share opens diagnostics", async () => {
  _setDiagnosticsInvokeForTesting(async (command) => {
    if (command === "diagnostics_build_report") {
      return { preview: "REDACTED PREVIEW", crashes: 1, bytes: 16 };
    }
    return null;
  });
  const host = document.getElementById("root")!;
  let root = createRoot(host);
  let throws = true;
  let reloads = 0;
  await act(async () => {
    root.render(
      React.createElement(ErrorBoundary, {
        onReload: () => {
          reloads += 1;
          throws = false;
        },
      }, child(true)),
    );
  });
  assert.match(host.textContent ?? "", /Something went wrong/);
  assert.ok(buttonByText("Reload"));
  assert.ok(buttonByText("Share diagnostics"));

  await act(async () => buttonByText("Reload").click());
  assert.equal(reloads, 1);
  root.unmount();
  root = createRoot(host);
  await act(async () => {
    root.render(React.createElement(ErrorBoundary, null, child(throws)));
  });
  assert.match(host.textContent ?? "", /normal content/);

  // Force a fresh error to open the sharing dialog.
  root.unmount();
  root = createRoot(host);
  await act(async () => {
    root.render(React.createElement(ErrorBoundary, null, child(true)));
  });
  await act(async () => buttonByText("Share diagnostics").click());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  assert.match(host.textContent ?? "", /Share diagnostics/);
  assert.match(host.textContent ?? "", /REDACTED PREVIEW/);
  _setDiagnosticsInvokeForTesting(null);
});

test("unhandledrejection is recorded and the panel still works", async () => {
  const uninstall = installGlobalErrorRecorders();
  const event = new dom.window.Event("unhandledrejection") as Event & { reason?: unknown };
  event.reason = "background promise exploded";
  dom.window.dispatchEvent(event);
  const recorded = (dom.window as unknown as { __COPERCEIVE_LAST_ERROR__?: { message: string } })
    .__COPERCEIVE_LAST_ERROR__;
  assert.equal(recorded?.message, "background promise exploded");
  uninstall();
});
