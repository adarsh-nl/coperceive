// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1:41234/#/connect",
});
const globals = globalThis as unknown as Record<string, unknown>;
globals.window = dom.window;
globals.document = dom.window.document;
Object.defineProperty(globals, "navigator", { value: dom.window.navigator, configurable: true });

interface InvokeCall {
  cmd: string;
  args: unknown;
}

const calls: InvokeCall[] = [];
(dom.window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
  invoke: async (cmd: string, args?: unknown) => {
    calls.push({ cmd, args });
    if (cmd === "get_persistent_state") {
      return {
        reopen_last_session: true,
        auto_next_scene: false,
        open_first_ready_scene: true,
        recent_roots: [],
      };
    }
    if (cmd === "plugin:viewer-bridge|viewer_get_preferences") {
      return {
        dataset_grouping: "day",
        camera_strip: "agent",
        viewer_style: "studio",
        cinema_drift: false,
      };
    }
    if (cmd === "plugin:viewer-bridge|viewer_get_dataset_scenes") {
      return { dataset: { scenes: [{ scene_id: "s1", state: "ready" }] } };
    }
    if (cmd === "plugin:viewer-bridge|viewer_get_job") {
      return { session: { job_id: 42, status: "running" } };
    }
    if (cmd === "plugin:viewer-bridge|viewer_show_library" || cmd === "plugin:viewer-bridge|viewer_prepare_scene") {
      return true;
    }
    if (cmd === "check_desktop_update" || cmd === "plugin:viewer-bridge|viewer_check_update") {
      return { status: "uptodate" };
    }
    return null;
  },
};

const originalFetch = globals.fetch;
globals.fetch = async () => {
  throw new Error("Unexpected HTTP controller fetch in production Tauri mode");
};

const local = await import("./local.ts");

test.after(() => {
  globals.fetch = originalFetch;
});

test("production main window routes updates, state, and reveal via Tauri IPC without controller", async () => {
  calls.length = 0;
  delete dom.window.__COPERCEIVE_VIEWER_WINDOW__;
  delete dom.window.__COPERCEIVE_CTRL_PORT__;
  delete dom.window.__COPERCEIVE_CTRL_SECRET__;

  assert.equal(typeof dom.window.__COPERCEIVE_CTRL_PORT__, "undefined");
  assert.equal(typeof dom.window.__COPERCEIVE_CTRL_SECRET__, "undefined");
  assert.equal(local.hasDesktopController(), false);
  assert.equal(local.isDesktopUpdateBridgeAvailable(), true);

  const state = await local.getPersistentState();
  assert.equal(state?.reopen_last_session, true);
  await local.updatePersistentState({ auto_next_scene: true });
  await local.revealInFinder("/tmp/library");
  const upd = await local.checkDesktopUpdate(true);
  assert.equal(upd.status, "uptodate");
  await local.installDesktopUpdate();

  assert.deepEqual(
    calls.map((c) => c.cmd),
    [
      "get_persistent_state",
      "update_persistent_state",
      "reveal_in_finder",
      "check_desktop_update",
      "install_desktop_update",
    ],
  );
  assert.deepEqual(calls[2]?.args, { path: "/tmp/library" });
});

test("production viewer window routes viewer actions via plugin:viewer-bridge without controller", async () => {
  calls.length = 0;
  dom.window.__COPERCEIVE_VIEWER_WINDOW__ = true;
  delete dom.window.__COPERCEIVE_CTRL_PORT__;
  delete dom.window.__COPERCEIVE_CTRL_SECRET__;

  const prefs = await local.getViewerPreferences();
  assert.equal(prefs?.camera_strip, "agent");
  await local.updatePersistentState({ viewer_style: "cinema" });
  const dataset = await local.getDatasetScenes();
  assert.equal(dataset?.scenes[0]?.scene_id, "s1");
  const job = await local.getViewerJob();
  assert.equal(job?.job_id, 42);
  await local.setViewerTitle("demo-junction");
  assert.equal(await local.prepareSceneInMainWindow("s1"), true);
  assert.equal(await local.showLibrary(), true);
  await local.openFolderDialog();
  await local.revealInFinder("/tmp/ignored-in-viewer");
  await local.checkDesktopUpdate(false);
  await local.installDesktopUpdate();

  assert.deepEqual(
    calls.map((c) => c.cmd),
    [
      "plugin:viewer-bridge|viewer_get_preferences",
      "plugin:viewer-bridge|viewer_update_preferences",
      "plugin:viewer-bridge|viewer_get_dataset_scenes",
      "plugin:viewer-bridge|viewer_get_job",
      "plugin:viewer-bridge|viewer_set_title",
      "plugin:viewer-bridge|viewer_prepare_scene",
      "plugin:viewer-bridge|viewer_show_library",
      "plugin:viewer-bridge|viewer_open_folder_dialog",
      "plugin:viewer-bridge|viewer_reveal_last_export",
      "plugin:viewer-bridge|viewer_check_update",
      "plugin:viewer-bridge|viewer_install_update",
    ],
  );
});
