// SPDX-License-Identifier: Apache-2.0
/**
 * Component tests for the Servers browser's folder-check line and Open action.
 *
 * The browser is rendered in jsdom (no React test renderer): the scan comes
 * back through the mocked Tauri `remote_scan`, and `dataset_detect` is
 * answered by the test seam in `clusterDatasets.ts`. That covers the reported
 * states end to end: a Checkpoints folder, a folder the cluster reports as
 * "none", an error, and a successful detection.
 */
import test from "node:test";
import assert from "node:assert";
import { JSDOM } from "jsdom";

// React and HpcBrowse need a DOM before they are imported.
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});
const globals = globalThis as unknown as Record<string, unknown>;
globals.window = dom.window;
globals.document = dom.window.document;
globals.localStorage = dom.window.localStorage;
globals.HTMLElement = dom.window.HTMLElement;
globals.Node = dom.window.Node;
globals.Event = dom.window.Event;
globals.CustomEvent = dom.window.CustomEvent;
globals.MouseEvent = dom.window.MouseEvent;
globals.getComputedStyle = dom.window.getComputedStyle;
globals.requestAnimationFrame = (callback: FrameRequestCallback) => dom.window.setTimeout(() => callback(0), 0);
globals.cancelAnimationFrame = (handle: number) => dom.window.clearTimeout(handle);
globals.IS_REACT_ACT_ENVIRONMENT = true;

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { default: HpcBrowse } = await import("./HpcBrowse.tsx");
const { _setInvokeForTesting } = await import("./clusterDatasets.ts");
import type { ClusterDetectDocument } from "./clusterDatasets.ts";
import type { FolderInfo, ScanDocument, ServerEntry } from "./hpc.ts";

const profile = {
  id: "profile-1",
  profile: {
    name: "Fake cluster",
    data_roots: ["/datasets"],
  },
} as unknown as ServerEntry;

function folder(overrides: Partial<FolderInfo> & Pick<FolderInfo, "path">): FolderInfo {
  return {
    depth: 1,
    is_scene: false,
    scene_id: null,
    dataset: false,
    scene_count: 0,
    has_manifest: false,
    has_complete: false,
    subfolder_count: 1,
    file_count: 0,
    recognised: null,
    ...overrides,
  } as FolderInfo;
}

function scan(folders: FolderInfo[]): ScanDocument {
  return {
    status: "ok",
    root: "/datasets",
    path: "/datasets",
    relative_path: ".",
    depth: 2,
    timeout_s: 10,
    max_entries: 200,
    max_total: 2000,
    elapsed_ms: 5,
    truncated: false,
    truncated_reason: null,
    folder_count: folders.length,
    scene_count: 0,
    dataset_count: 0,
    folders: [folder({ path: "." }), ...folders],
    scenes: [],
    datasets: [],
    skipped: [],
  } as unknown as ScanDocument;
}

function detectDocument(overrides: Partial<ClusterDetectDocument>): ClusterDetectDocument {
  return {
    schema: "coperceive-cluster-detect-1",
    path: "/datasets/v2v4real",
    adapter: "v2v4real",
    raw_root: "/datasets/v2v4real",
    split: "test",
    params: { split: "test" },
    licence: null,
    scene_count: 9,
    status: "ok",
    reason: null,
    ...overrides,
  };
}

type DetectAnswer = ClusterDetectDocument | { status: "none" | "error"; reason?: string | null };

let documentToScan = scan([folder({ path: "v2v4real", recognised: "V2V4Real · test" })]);
let detectAnswer: (path: string) => DetectAnswer | Promise<DetectAnswer> = () =>
  detectDocument({ path: "/datasets/v2v4real", raw_root: "/datasets" });
const detectCalls: string[] = [];

(dom.window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
  invoke: async (command: string) => {
    if (command === "remote_scan") return documentToScan;
    throw new Error(`unexpected command ${command}`);
  },
};

function useDetectAnswers(answer: (path: string) => DetectAnswer | Promise<DetectAnswer>): void {
  detectAnswer = answer;
}

_setInvokeForTesting(async (command, args) => {
  if (command !== "dataset_detect") throw new Error(`unexpected command ${command}`);
  const path = String(args?.path ?? "");
  detectCalls.push(path);
  return (await detectAnswer(path)) as never;
});

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

interface Rendered {
  container: HTMLElement;
  root: ReturnType<typeof createRoot>;
  cleanup: () => Promise<void>;
}

async function renderBrowse(scanDocument: ScanDocument): Promise<Rendered> {
  documentToScan = scanDocument;
  detectCalls.length = 0;
  const container = dom.window.document.createElement("div");
  dom.window.document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(HpcBrowse, { profile }));
  });
  // The scan and then the debounced folder check (150 ms) finish.
  await act(async () => {
    await delay(450);
  });
  return {
    container,
    root,
    cleanup: async () => {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function openButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector("button.c-primary-wide");
  assert.ok(button, "the inspector's primary Open button must render");
  return button as HTMLButtonElement;
}

function inspectorText(container: HTMLElement): string {
  return container.textContent ?? "";
}

test("a Checkpoints folder never says 'Checking what this folder holds…' and cannot open", async () => {
  useDetectAnswers(() => detectDocument({}));
  const rendered = await renderBrowse(
    scan([folder({ path: "checkpoints", recognised: "Checkpoints · 2" })]),
  );
  try {
    assert.doesNotMatch(inspectorText(rendered.container), /Checking what this folder holds/);
    assert.strictEqual(openButton(rendered.container).disabled, true);
    assert.deepStrictEqual(detectCalls, [], "a Checkpoints folder is not a dataset to detect");
  } finally {
    await rendered.cleanup();
  }
});

test("a folder the cluster reports as 'none' stops checking and says so", async () => {
  useDetectAnswers(() => ({ status: "none", reason: null }));
  const rendered = await renderBrowse(
    scan([folder({ path: "v2v4real", recognised: "V2V4Real · test" })]),
  );
  try {
    assert.doesNotMatch(inspectorText(rendered.container), /Checking what this folder holds/);
    assert.match(inspectorText(rendered.container), /No dataset coperceive recognises in this folder/);
    assert.strictEqual(openButton(rendered.container).disabled, true);
    assert.deepStrictEqual(detectCalls, ["/datasets/v2v4real"]);
  } finally {
    await rendered.cleanup();
  }
});

test("a folder the cluster reports as an error shows the reason and stops checking", async () => {
  useDetectAnswers(() => ({ status: "error", reason: "permission denied" }));
  const rendered = await renderBrowse(
    scan([folder({ path: "v2v4real", recognised: "V2V4Real · test" })]),
  );
  try {
    assert.doesNotMatch(inspectorText(rendered.container), /Checking what this folder holds/);
    assert.match(inspectorText(rendered.container), /permission denied/);
    assert.strictEqual(openButton(rendered.container).disabled, true);
  } finally {
    await rendered.cleanup();
  }
});

test("a detected folder shows its split and count, and Open opens that dataset", async () => {
  useDetectAnswers(() => detectDocument({}));
  const rendered = await renderBrowse(
    scan([folder({ path: "v2v4real", recognised: "V2V4Real · test" })]),
  );
  try {
    assert.doesNotMatch(inspectorText(rendered.container), /Checking what this folder holds/);
    assert.match(inspectorText(rendered.container), /split test · 9 scenes/);
    const button = openButton(rendered.container);
    assert.strictEqual(button.disabled, false);

    const opened: ClusterDetectDocument[] = [];
    const listener = (event: Event) => {
      opened.push((event as CustomEvent<{ dataset: ClusterDetectDocument }>).detail.dataset);
    };
    dom.window.addEventListener("coperceive-open-dataset", listener);
    try {
      await act(async () => {
        button.click();
      });
    } finally {
      dom.window.removeEventListener("coperceive-open-dataset", listener);
    }
    assert.strictEqual(opened.length, 1);
    assert.strictEqual(opened[0]?.adapter, "v2v4real");
    assert.strictEqual(opened[0]?.split, "test");
  } finally {
    await rendered.cleanup();
  }
});

test("selecting another folder drops the previous detection while its check runs", async () => {
  const pending: Array<(answer: DetectAnswer) => void> = [];
  useDetectAnswers((path) => {
    if (path === "/datasets/opv2v") return detectDocument({ path, raw_root: path, adapter: "opv2v", scene_count: 16 });
    return new Promise<DetectAnswer>((resolve) => pending.push(resolve));
  });
  const rendered = await renderBrowse(
    scan([
      folder({ path: "opv2v", recognised: "OPV2V · test" }),
      folder({ path: "v2v4real", recognised: "V2V4Real · test" }),
    ]),
  );
  try {
    assert.match(inspectorText(rendered.container), /split test · 16 scenes/);
    const row = rendered.container.querySelector('[data-row-index="1"]');
    assert.ok(row, "the v2v4real row must render");
    await act(async () => {
      (row as HTMLElement).click();
      await delay(250);
    });
    // The new check is still pending: the previous folder's result must not
    // stay on screen or keep Open enabled.
    assert.match(inspectorText(rendered.container), /Checking what this folder holds/);
    assert.doesNotMatch(inspectorText(rendered.container), /16 scenes/);
    assert.strictEqual(openButton(rendered.container).disabled, true);
    let opened = 0;
    const listener = () => {
      opened += 1;
    };
    dom.window.addEventListener("coperceive-open-dataset", listener);
    try {
      await act(async () => {
        openButton(rendered.container).click();
      });
    } finally {
      dom.window.removeEventListener("coperceive-open-dataset", listener);
    }
    assert.strictEqual(opened, 0, "a pending folder check must not open a dataset");
  } finally {
    await rendered.cleanup();
  }
});
