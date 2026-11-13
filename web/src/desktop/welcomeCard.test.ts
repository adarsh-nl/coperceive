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
const { WelcomeCard, DemoProgressBanner } = await import("./LibraryScreen.tsx");
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
  let cancelled = false;
  await act(async () => {
    root.render(
      React.createElement(WelcomeCard, {
        generatingDemo: true,
        demoElapsedSec: 4,
        demoStage: "Packing LiDAR frames & camera strip…",
        demoError: null,
        onOpenDemo: () => {},
        onCancelDemo: () => {
          cancelled = true;
        },
        onTrySample: () => {},
        onDismiss: () => {},
      }),
    );
  });

  const generatingBtn = [...host.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Generating demo scene… (about 8 MB) · 4s elapsed"),
  );
  assert.ok(generatingBtn, "shows progress and size while generating");
  assert.ok(
    generatingBtn.textContent?.includes("Packing LiDAR frames & camera strip…"),
    "shows current generation stage",
  );
  assert.ok(
    generatingBtn.textContent?.includes("This can take up to 2 minutes on older Macs."),
    "shows older Mac timing guidance",
  );
  assert.equal(generatingBtn.disabled, true, "primary button disabled while generating");

  const cancelBtn = host.querySelector('[data-testid="welcome-cancel-demo"]') as HTMLButtonElement | null;
  assert.ok(cancelBtn, "cancel button rendered while generating");
  cancelBtn.click();
  assert.equal(cancelled, true, "cancel callback invoked");

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

test("DemoProgressBanner renders progress, stage, older Mac timing guidance, and cancel button for Help -> Open the Demo", async () => {
  const host = document.getElementById("root")!;
  const root = createRoot(host);
  let cancelled = false;

  await act(async () => {
    root.render(
      React.createElement(DemoProgressBanner, {
        stage: "Packing LiDAR frames & camera strip…",
        elapsedSec: 12,
        onCancel: () => {
          cancelled = true;
        },
      }),
    );
  });

  const banner = host.querySelector('[data-testid="demo-progress-banner"]');
  assert.ok(banner, "demo progress banner is rendered");
  assert.ok(banner.textContent?.includes("Packing LiDAR frames & camera strip…"));
  assert.ok(banner.textContent?.includes("12s elapsed"));
  assert.ok(banner.textContent?.includes("This can take up to 2 minutes on older Macs."));

  const cancelBtn = host.querySelector('[data-testid="demo-progress-cancel"]') as HTMLButtonElement | null;
  assert.ok(cancelBtn, "cancel button is rendered");
  cancelBtn.click();
  assert.equal(cancelled, true);

  root.unmount();
});

test("runDemoOpenFlow opens cached demo without generating banner and shows progress when generating", async () => {
  const { DEMO_APPROX_BYTES, DEMO_APPROX_SIZE_LABEL, formatDemoSize, runDemoOpenFlow } =
    await import("./demoMeta.ts");
  assert.equal(DEMO_APPROX_SIZE_LABEL, "about 8 MB");
  assert.equal(formatDemoSize(8_218_188), "8.2 MB");
  assert.equal(formatDemoSize(null), "about 8 MB");

  let cachedGenStarted = false;
  const cachedOutcome = await runDemoOpenFlow({
    ensureDemo: async () => ({ path: "/tmp/demo", generated: false, bytes: 8_218_188 }),
    subscribeProgress: async () => () => {},
    onGeneratingStart: () => {
      cachedGenStarted = true;
    },
    onGeneratingEnd: () => {},
    onProgress: () => {},
  });
  assert.equal(cachedGenStarted, false, "cached demo must not flash generating banner");
  assert.equal(cachedOutcome.generated, false);

  let genStarted = false;
  let genEnded = false;
  let measuredBytes = 0;
  let lastStage = "";
  let progressHandler: ((p: { stage?: string; elapsed_seconds?: number }) => void) | null = null;
  await runDemoOpenFlow({
    ensureDemo: async () => {
      progressHandler?.({ stage: "Packing LiDAR frames & camera strip…", elapsed_seconds: 2 });
      return { path: "/tmp/demo", generated: true, bytes: 8_218_188 };
    },
    subscribeProgress: async (handler) => {
      progressHandler = handler;
      return () => {
        progressHandler = null;
      };
    },
    onGeneratingStart: () => {
      genStarted = true;
    },
    onGeneratingEnd: () => {
      genEnded = true;
    },
    onProgress: (stage) => {
      if (stage) lastStage = stage;
    },
    onDemoBytes: (bytes) => {
      measuredBytes = bytes;
    },
  });
  assert.equal(genStarted, true, "uncached demo triggers generating banner");
  assert.equal(genEnded, true);
  assert.equal(lastStage, "Packing LiDAR frames & camera strip…");
  assert.equal(measuredBytes, 8_218_188);
  assert.ok(DEMO_APPROX_BYTES > 0);
});

test("DEMO_APPROX_BYTES is within 20% of real coperceive demo create output", async () => {
  const fs = await import("node:fs");
  const os = await import("node:os");
  const path = await import("node:path");
  const { execFileSync } = await import("node:child_process");
  const { DEMO_APPROX_BYTES } = await import("./demoMeta.ts");

  const repoRoot = path.resolve(import.meta.dirname, "../../..");
  const venvPy = path.join(repoRoot, ".venv", "bin", "python");
  if (!fs.existsSync(venvPy)) {
    return;
  }
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "demo-size-check-"));
  try {
    execFileSync(venvPy, ["-m", "coperceive", "demo", "create", tmpDir], {
      cwd: repoRoot,
      stdio: "pipe",
    });
    const dirSize = (dir: string): number => {
      let total = 0;
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) total += dirSize(full);
        else total += fs.statSync(full).size;
      }
      return total;
    };
    const actualBytes = dirSize(tmpDir);
    const ratio = Math.abs(actualBytes - DEMO_APPROX_BYTES) / actualBytes;
    assert.ok(
      ratio <= 0.2,
      `DEMO_APPROX_BYTES (${DEMO_APPROX_BYTES}) differs from actual (${actualBytes}) by ${(ratio * 100).toFixed(1)}% (>20%)`,
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
