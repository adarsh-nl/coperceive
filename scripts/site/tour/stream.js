const { chromium } = require('playwright');
const fs = require('fs');
const { checkPublished } = require('./guard.js');
const PORT = process.env.PORT || 8734;
(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'dark' });
  await ctx.route('http://127.0.0.1:9999/**', async (route) => {
    const u = route.request().url();
    const ok = (body) => route.fulfill({ status: 200, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' }, body: JSON.stringify(body) });
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*' } });
    if (u.endsWith('/dataset-scenes')) return ok({ dataset: { label: 'OPV2V test', server: 'Lab GPU cluster', scenes: [{ scene_id: 'opv2v-style-junction', state: 'ready' }] } });
    return ok({});
  });
  const p = await ctx.newPage();
  await p.addInitScript(() => { window.__COPERCEIVE_CTRL_PORT__ = 9999; window.__COPERCEIVE_CTRL_SECRET__ = 'tour'; });
  await p.goto(`http://127.0.0.1:${PORT}/#/viewer?scene=opv2v-style-junction`, { waitUntil: 'load' });
  await p.waitForSelector('text=/[0-9,.]+ ?k? pts/', { timeout: 120000 });
  await p.waitForTimeout(4000);
  const slider = p.getByRole('slider', { name: 'Point size' }); await slider.focus(); await p.keyboard.press('ArrowLeft');
  const next = p.locator('button[aria-label="Step forward"]').first();
  for (let i = 0; i < 20; i++) { await next.click(); await p.waitForTimeout(250); }
  await p.getByRole('button', { name: /^Orbit/ }).first().click();
  await p.waitForTimeout(6000);
  checkPublished(await p.innerText('body'), 'vw/stream.png');
  await p.screenshot({ path: 'vw/stream.png' });
  const bb = await p.locator('.conn-state').first().boundingBox();
  fs.writeFileSync('vw/stream_targets.json', JSON.stringify({ conn: bb }));
  console.log(JSON.stringify({ conn: bb }));
  const t = await p.innerText('body'); console.log(t.slice(0, 200));
  await b.close();
})();
