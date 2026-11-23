const { chromium } = require('playwright');
const fs = require('fs');
const { checkPublished } = require('./guard.js');
const catalog = JSON.parse(fs.readFileSync(process.env.CATALOG || (__dirname + '/realcatalog.json'), 'utf8'));
const DESK = process.env.DESK || 8732, VIEW = process.env.VIEW || 8735;
const ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];
const SCENES = [
  ['opv2v', 'opv2v-style-junction', 'Orbit'],
  ['v2xset', 'v2xset-style-junction', 'Orbit'],
  ['dair-v2x-c', 'dair-style-intersection', "Bird's-eye"],
  ['v2v4real', 'v2v4real-style-highway', 'Chase'],
  ['griffin', 'griffin-style-aerial', 'Drone'],
];
(async () => {
  fs.mkdirSync('ds', { recursive: true });
  const b = await chromium.launch({ args: ARGS });
  const T = {};
  if (!process.env.SKIP_DESK) {
    const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'dark' });
    const p = await ctx.newPage();
    await p.addInitScript(() => { window.__COPERCEIVE_TEST_ENV__ = true; window.__COPERCEIVE_DESKTOP__ = true; });
    await p.goto(`http://127.0.0.1:${DESK}/#/connect`, { waitUntil: 'load' });
    await p.waitForSelector('.c-sidebar', { timeout: 15000 });
    await p.evaluate(`document.documentElement.dataset.theme = 'dark'`);
    await p.locator('button[role=tab]', { hasText: 'Get Data' }).first().click();
    const seed = () => p.evaluate((catalog) => window.dispatchEvent(new CustomEvent('coperceive-test-catalog-seed', { detail: { catalog, presetBytes: { 'griffin:dev': 412000000, 'griffin:full': 6840000000 } } })), catalog);
    await seed(); await p.waitForTimeout(600);
    checkPublished(await p.innerText('body'), 'ds/getdata.png');
    await p.screenshot({ path: 'ds/getdata.png' });
    for (const [id] of SCENES) {
      const name = catalog.find((c) => c.id === id).name;
      const row = p.getByText(name, { exact: true }).first();
      const bb = await row.boundingBox(); T['row.' + id] = bb;
      await row.click(); await p.waitForTimeout(500);
      checkPublished(await p.innerText('body'), `ds/getdata_${id}.png`);
      await p.screenshot({ path: `ds/getdata_${id}.png` });
      const open = p.getByRole('button', { name: /Import|Download|Choose folder|Open/ }).first();
      T['action.' + id] = await open.boundingBox().catch(() => null);
    }
    await ctx.close();
  }
  for (const [id, scene, view] of SCENES.filter((x) => !process.env.ONLY || process.env.ONLY.split(',').includes(x[0]))) {
    const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'dark' });
    const q = await ctx.newPage();
    await q.goto(`http://127.0.0.1:${VIEW}/#/viewer?scene=${scene}`, { waitUntil: 'load' });
    await q.waitForSelector('text=/[0-9,.]+ ?k? pts/', { timeout: 120000 }); await q.waitForTimeout(3000);
    const slider = q.getByRole('slider', { name: 'Point size' }); await slider.focus(); await q.keyboard.press('ArrowLeft');
    const next = q.locator('button[aria-label="Step forward"]').first();
    for (let i = 0; i < 15; i++) { await next.click(); await q.waitForTimeout(200); }
    if (view !== 'Orbit') await q.getByRole('button', { name: new RegExp('^' + view) }).first().click();
    await q.waitForTimeout(3500);
    checkPublished(await q.innerText('body'), `ds/view_${id}.png`);
    await q.screenshot({ path: `ds/view_${id}.png` });
    const txt = await q.innerText('body');
    console.log(id, scene, (txt.match(/[0-9.]+k? pts/) || [''])[0], (txt.match(/Agents\s+\d+ of \d+ shown/) || [''])[0]);
    await ctx.close();
  }
  fs.writeFileSync('ds/targets.json', JSON.stringify(T, null, 1));
  await b.close();
})();
