// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  collectWebglDiagnostics,
  copyWebglDiagnostics,
  formatWebglDiagnosticsText,
  formatWebglGraphicsReadout,
  sanitizeWebglError,
} from "./webglDiagnostics.ts";

test("collectWebglDiagnostics handles macos-15-intel null precision and null vendor/renderer without PII", () => {
  const dom = new JSDOM("<!doctype html><html><body><canvas></canvas></body></html>");
  const prevDoc = globalThis.document;
  const prevWin = globalThis.window;
  Object.defineProperty(globalThis, "document", { value: dom.window.document, configurable: true });
  Object.defineProperty(globalThis, "window", { value: dom.window, configurable: true });
  try {
    const canvas = dom.window.document.querySelector("canvas")!;
    (canvas as unknown as { getContext: (kind: string) => unknown }).getContext = (kind: string) => {
      if (kind !== "webgl2" && kind !== "webgl") return null;
      return {
        VENDOR: 0x1f00,
        RENDERER: 0x1f01,
        VERTEX_SHADER: 0x8b31,
        HIGH_FLOAT: 0x8df2,
        getParameter: () => null,
        getExtension: () => null,
        getShaderPrecisionFormat: () => null,
      };
    };

    const diag = collectWebglDiagnostics(
      canvas,
      new TypeError(
        "null is not an object (evaluating 'e.getShaderPrecisionFormat(e.VERTEX_SHADER,e.HIGH_FLOAT).precision')",
      ),
    );
    assert.equal(diag.webgl, true);
    assert.equal(diag.webgl2, true);
    assert.equal(diag.vendor, null);
    assert.equal(diag.renderer, null);
    assert.equal(diag.vertexHighFloatPrecision, null);

    const readout = formatWebglGraphicsReadout(diag);
    assert.equal(readout, "Graphics readout: vendor null · renderer null");

    const text = formatWebglDiagnosticsText(diag);
    assert.ok(text.includes("vendor: null"));
    assert.ok(text.includes("renderer: null"));
    assert.ok(text.includes("vertexHighFloatPrecision: null"));
    assert.ok(text.includes("getShaderPrecisionFormat"));
  } finally {
    Object.defineProperty(globalThis, "document", { value: prevDoc, configurable: true });
    Object.defineProperty(globalThis, "window", { value: prevWin, configurable: true });
  }
});

test("sanitizeWebglError strips file paths and URLs so copied diagnostics contain no personal data", () => {
  const cleaned = sanitizeWebglError(
    new Error("Failed at http://127.0.0.1:49335/assets/app.js for /Users/alice/Coperceive/scene"),
  );
  assert.ok(!cleaned.includes("/Users/alice"), cleaned);
  assert.ok(!cleaned.includes("127.0.0.1"), cleaned);
  assert.ok(cleaned.includes("[origin]"), cleaned);
  assert.ok(cleaned.includes("[home]"), cleaned);
});

test("copyWebglDiagnostics writes text via Clipboard API", async () => {
  const prevNav = globalThis.navigator;
  let copied = "";
  Object.defineProperty(globalThis, "navigator", {
    value: {
      clipboard: {
        writeText: async (text: string) => {
          copied = text;
        },
      },
    },
    configurable: true,
  });
  try {
    const ok = await copyWebglDiagnostics("Coperceive 3D view (WebGL) diagnostics\nvendor: Apple");
    assert.equal(ok, true);
    assert.equal(copied, "Coperceive 3D view (WebGL) diagnostics\nvendor: Apple");
  } finally {
    Object.defineProperty(globalThis, "navigator", { value: prevNav, configurable: true });
  }
});
