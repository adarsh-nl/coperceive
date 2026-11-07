const { chromium } = require('playwright');
const fs = require('fs');
const { checkText, checkPublished } = require('./guard.js');
const catalogJson = fs.readFileSync(process.env.CATALOG || (__dirname + '/realcatalog.json'), 'utf8');
const PORT = process.env.PORT || 8732;
const SEED = `() => {
  const scenes = [
    { scene_id: "demo-junction", path: "~/coperceive-data/demo/demo-junction", dataset_name: "Demo · synthetic", town: "Junction", duration_s: 9.9, agent_count: 3, sample_count: 100, size_bytes: 104857600, has_images: "full", has_thumbnail: false, map_name: null, has_map: false, is_complete: true, is_valid: true },
    { scene_id: "scene-0001", path: "~/coperceive-data/opv2v/scene-0001", dataset_name: "OPV2V · imported", town: "Town03", duration_s: 16.0, agent_count: 3, sample_count: 160, size_bytes: 289406976, has_images: "full", has_thumbnail: false, map_name: "Carla/Maps/Town03", has_map: true, is_complete: true, is_valid: true },
    { scene_id: "scene-0002", path: "~/coperceive-data/v2xset/scene-0002", dataset_name: "V2XSet · imported", town: "Town05", duration_s: 19.6, agent_count: 4, sample_count: 196, size_bytes: 342884352, has_images: "full", has_thumbnail: false, map_name: "Carla/Maps/Town05", has_map: true, is_complete: true, is_valid: true },
    { scene_id: "Town03-000", path: "~/coperceive-data/griffin/Town03-000", dataset_name: "Griffin · vehicle + drone", town: "Town03", duration_s: 24.2, agent_count: 2, sample_count: 242, size_bytes: 512753664, has_images: "full", has_thumbnail: false, map_name: null, has_map: true, is_complete: true, is_valid: true }
  ];
  const storage = { library_root: "~/coperceive-data", is_configured: true, used_bytes: 1249902592, free_bytes: 440401920000, total_bytes: 1000000000000, volume_name: "Data", scene_count: 4 };
  window.dispatchEvent(new CustomEvent("coperceive-test-library-seed", { detail: { scenes, storage } }));
  const catalog = ${catalogJson};
  window.dispatchEvent(new CustomEvent("coperceive-test-catalog-seed", { detail: { catalog, presetBytes: { "griffin:dev": 412000000, "griffin:full": 6840000000 } } }));
  window.dispatchEvent(new CustomEvent("coperceive-test-open-folder-seed", { detail: { dataRoot: "~/coperceive-data/opv2v", recentRoots: ["~/coperceive-data/opv2v", "~/coperceive-data/v2xset", "~/coperceive-data/griffin"] } }));
  const profile = {
    id: "lab-gpu", schema_version: 1,
    profile: { name: "Lab GPU cluster", ssh_alias: "lab-gpu", host: "hpc.example.org", user: "researcher",
      data_roots: ["/datasets/opv2v", "/datasets/v2xset"], scratch_template: "/scratch/demo/coperceive",
      results_root: "/home/researcher/results", code_roots: ["/home/researcher/code/opencood"], scheduler: "slurm",
      defaults: { partition: "gpu", account: null, qos: "normal", gres: "gpu:a100:1", time_limit: "02:00:00", memory: "32G", cpus_per_task: 8 },
      environment: { kind: "conda", modules: ["cuda/12.1"], interpreter: "/home/researcher/miniconda3/envs/coperceive/bin/python", container_image: null, coperceive_command: ["coperceive"], wheelhouse: null, compute_has_internet: false },
      notes: "Example profile for the product tour." },
    discovered: { partitions: [ { name: "gpu", nodes: 14, state: "up", max_time: "2-00:00:00", gres: "gpu:a100:4", gpu_types: ["a100"] }, { name: "short", nodes: 6, state: "up", max_time: "04:00:00", gres: null, gpu_types: [] } ],
      gpu_types: ["a100"], environment: { module_system: true, conda: "/home/researcher/miniconda3/bin/conda", virtualenv: null, python: "/home/researcher/miniconda3/envs/coperceive/bin/python", container_runtime: "apptainer", coperceive_path: "/home/researcher/.local/bin/coperceive", coperceive_version: "0.1.1", scratch_candidates: ["/scratch/researcher"] }, checked_unix: 1762430000 },
    ssh: { alias: "lab-gpu", hostname: "hpc.example.org", user: "researcher", port: 22, proxy_jump: null, proxy_command: null }
  };
  window.__TOUR_PROFILE__ = profile;
  window.dispatchEvent(new CustomEvent("coperceive-test-hpc-servers-seed", { detail: { profiles: [profile], hosts: ["lab-gpu"] } }));
}`;
const DATASET = `() => {
  const profile = window.__TOUR_PROFILE__;
  const scenes = [], statuses = [];
  for (let i = 0; i < 44; i++) {
    const id = "scene_" + String(i).padStart(4, "0");
    const dur = 10 + (i % 5) * 2.5, agents = 2 + (i % 3), frames = Math.round(dur * 10);
    scenes.push({ scene_id: id, sample_count: frames, duration_s: dur, agent_count: agents, sensors: { sensors: agents * 5, camera_samples: frames * agents * 4, lidar_samples: frames * agents } });
    const st = i < 4 ? "ready" : (i < 7 ? "preparing" : "not_prepared");
    statuses.push({ scene_id: id, state: st, reason: null, job_id: st === "not_prepared" ? null : "700001", prepared_unix: st === "ready" ? 1762430000 : null, scene_dir: st === "ready" ? "/scratch/researcher/coperceive-cache/opv2v/test/" + id : null, progress_percent: st === "ready" ? 100 : (st === "preparing" ? 60 - (i - 4) * 15 : 0), images: "originals" });
  }
  const licence = { status: "unknown", spdx: null, notice: "Check the dataset licence", links: [] };
  const dataset = { schema: "coperceive-cluster-detect-1", path: "/datasets/opv2v/test", adapter: "opv2v", raw_root: "/datasets/opv2v", split: "test", params: { split: "test" }, licence, scene_count: 44, status: "ok", reason: null };
  const indexDoc = { schema: "coperceive-cluster-index-1", adapter: "opv2v", raw_root: "/datasets/opv2v", split: "test", params: { split: "test" }, licence,
    fingerprint: { algorithm: "sha1-mtime-size-v1", digest: "a1b2c3d4e5f6", entries: 44, truncated: false }, cache: { relative_path: "coperceive-cache/opv2v/a1b2c3d4e5f6/test" },
    scene_count: 44, scenes, errors: [], truncated: false, elapsed_ms: 310, storage_estimate_bytes: 5200000000, storage_estimate_label: "~5.2 GB" };
  window.dispatchEvent(new CustomEvent("coperceive-open-dataset", { detail: { profile, dataset, indexDoc, statuses,
    submittedJobs: [{ jobId: "700001", planId: "plan-opv2v-test", jobName: "coperceive-prepare-opv2v", scenes: "all", state: "running", reason: null }],
    cacheRoot: "/scratch/researcher/coperceive-cache/opv2v/a1b2c3d4e5f6/test", selectedSceneId: "scene_0000" } }));
}`;

(async () => {
  const theme = process.env.THEME || 'dark';
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const p = await ctx.newPage();
  await p.addInitScript(() => { window.__COPERCEIVE_TEST_ENV__ = true; window.__COPERCEIVE_DESKTOP__ = true; });
  await p.goto(`http://127.0.0.1:${PORT}/#/connect`, { waitUntil: 'load' });
  await p.waitForSelector('.c-sidebar', { timeout: 15000 });
  await p.evaluate(`document.documentElement.dataset.theme = '${theme}'`);
  const seed = async () => { await p.evaluate(`(${SEED})()`); await p.waitForTimeout(400); };
  await seed();
  const out = process.env.OUT || 'desk';
  fs.mkdirSync(out, { recursive: true });
  const shoot = async (name) => {
    const text = await p.innerText('body');
    const file = `${out}/${name}.png`;
    if (name === 'servers') checkText(text, file);
    else checkPublished(text, file);
    await p.screenshot({ path: file });
  };
  const tab = async (label, name) => { await p.locator('button[role=tab]', { hasText: label }).first().click(); await seed(); await shoot(name); };
  const side = async (label, name) => { await p.locator('.c-sidebar button', { hasText: label }).first().click(); await seed(); await shoot(name); };
  const T = {};
  const rect = async (key, loc) => { try { const bb = await loc.first().boundingBox(); if (bb) T[key] = bb; } catch (e) {} };
  await tab('Library', 'library');
  await rect('library.tab', p.locator('button[role=tab]', { hasText: 'Library' }));
  await rect('library.row_demo', p.locator('text=demo-junction').first());
  await rect('library.open', p.getByRole('button', { name: 'Open in viewer' }));
  await rect('library.add', p.getByRole('button', { name: /Add scenes/ }));
  await tab('Get Data', 'getdata');
  await rect('getdata.tab', p.locator('button[role=tab]', { hasText: 'Get Data' }));
  await rect('getdata.griffin', p.locator('text=Griffin').first());
  await rect('getdata.download', p.getByRole('button', { name: 'Download' }));
  await rect('getdata.opv2v', p.locator('text=OPV2V').first());
  await tab('Open Folder', 'openfolder');
  await rect('openfolder.tab', p.locator('button[role=tab]', { hasText: 'Open Folder' }));
  await rect('openfolder.choose', p.getByRole('button', { name: /^Choose/ }));
  await rect('openfolder.import', p.getByRole('button', { name: /Import from folder/ }));
  await rect('openfolder.open', p.getByRole('button', { name: 'Open Local Viewer' }));
  await side('Servers', 'servers');
  await rect('servers.side', p.locator('.c-sidebar button', { hasText: 'Servers' }));
  await rect('servers.test', p.getByRole('button', { name: 'Test', exact: true }));
  await rect('servers.row', p.locator('text=Lab GPU cluster').nth(1));
  await p.evaluate(`(${DATASET})()`); await p.waitForSelector('text=Scene · start time', { timeout: 8000 }).catch(() => {}); await p.waitForTimeout(700);
  await shoot('dataset');
  await rect('dataset.prepare', p.getByRole('button', { name: /Prepare remaining/ }));
  await rect('dataset.open', p.getByRole('button', { name: 'Open scene' }));
  await rect('dataset.row0', p.locator('text=scene_0000').first());
  await rect('dataset.progress', p.locator('text=Preparing 60%').first());
  await p.locator('.c-sidebar button', { hasText: 'New evaluation' }).first().click(); await p.waitForTimeout(300);
  const pipe = { id: "custom-detector", name: "My detector (custom script)", description: "Your own evaluation script, registered with a coperceive-pipeline.json beside it.", source: "custom",
    spec: { name: "my-detector-eval", script: "/home/researcher/code/my_detector/eval_coperceive.py", interpreter: ["python"],
      environment: { kind: "conda", modules: ["cuda/12.1"], interpreter: "/home/researcher/miniconda3/envs/coperceive/bin/python", container_image: null, coperceive_command: ["coperceive"], wheelhouse: null, compute_has_internet: false },
      arguments: [
        { name: "checkpoint", kind: "path", help: "Model checkpoint on the cluster", required: true, default: null, choices: [], multiple: false, role: "checkpoint" },
        { name: "split", kind: "enum", help: "Prepared split to evaluate", required: true, default: "test", choices: ["test", "validate"], multiple: false, role: "test_data" },
        { name: "batch_size", kind: "int", help: "Frames per batch", required: false, default: 4, choices: [], multiple: false, role: "other" } ],
      output_contract: "coperceive-predictions-1", description: "Writes predictions in the Coperceive run format." },
    registration_path: "/home/researcher/code/my_detector/coperceive-pipeline.json", plugin_manifest: null };
  await p.evaluate((pipe) => window.dispatchEvent(new CustomEvent("coperceive-test-evaluation-seed", { detail: { profiles: [window.__TOUR_PROFILE__], pipelines: [pipe] } })), pipe);
  await p.waitForTimeout(800); await shoot('neweval');
  await rect('neweval.side', p.locator('.c-sidebar button', { hasText: 'New evaluation' }));
  await rect('neweval.save', p.getByRole('button', { name: /Save plan/ }));
  await p.locator('.c-sidebar button', { hasText: 'Run history' }).first().click(); await p.waitForTimeout(300);
  const entries = [
    { job_id: "700214", plan_id: "plan-my-detector-test", state: "running", active: true, elapsed: "00:41:12", time_limit: "02:00:00", partition: "gpu", started: "2026-11-06T13:02:11", ended: null, plan_available: true },
    { job_id: "700198", plan_id: "plan-my-detector-validate", state: "completed", active: false, elapsed: "01:12:40", time_limit: "02:00:00", partition: "gpu", started: "2026-11-05T21:10:03", ended: "2026-11-05T22:22:43", plan_available: true },
    { job_id: "700001", plan_id: "plan-opv2v-prepare", state: "completed", active: false, elapsed: "00:18:05", time_limit: "01:00:00", partition: "short", started: "2026-11-05T19:40:00", ended: "2026-11-05T19:58:05", plan_available: true } ];
  await p.evaluate((entries) => window.dispatchEvent(new CustomEvent("coperceive-test-run-history-seed", { detail: { profiles: [window.__TOUR_PROFILE__], entries } })), entries);
  await p.waitForTimeout(800); await shoot('history');
  await rect('history.side', p.locator('.c-sidebar button', { hasText: 'Run history' }));
  await rect('history.row0', p.locator('text=700214').first());
  fs.writeFileSync(`${out}/targets.json`, JSON.stringify(T, null, 1));
  const body = await p.innerText('body');
  fs.writeFileSync(`${out}/text.txt`, body);
  await b.close();
})();
