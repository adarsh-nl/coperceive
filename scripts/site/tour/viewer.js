const { chromium } = require('playwright');
const fs = require('fs');
const { checkText, noteLoop } = require('./guard.js');
const PORT = process.env.PORT || 8733;
const out = 'vw';
const ONLY_LOOPS = !!process.env.ONLY_LOOPS;
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'dark' });
  const p = await ctx.newPage();
  await p.goto(`http://127.0.0.1:${PORT}/#/viewer?scene=demo-junction`, { waitUntil: 'load' });
  await p.waitForSelector('text=/[0-9,.]+ ?k? pts/', { timeout: 90000 }); await p.waitForTimeout(3000);

  const prep = async (pg) => {
    await pg.click('#prediction-run'); await pg.waitForTimeout(300);
    await pg.getByRole('option', { name: /^demo/ }).first().click(); await pg.waitForTimeout(1200);
    const slider = pg.getByRole('slider', { name: 'Point size' });
    await slider.focus(); await pg.keyboard.press('ArrowLeft'); await pg.waitForTimeout(600);
  };
  await prep(p);
  const T = {};
  if (!ONLY_LOOPS) {
  const rect = async (key, loc) => { try { const bb = await loc.first().boundingBox(); if (bb) T[key] = bb; } catch (e) {} };
  const shot = async (n) => {
    await p.waitForTimeout(1500);
    const text = await p.innerText('body');
    checkText(text, `${out}/${n}.png`);
    await p.screenshot({ path: `${out}/${n}.png` });
  };
  const view = (name) => p.getByRole('button', { name: new RegExp('^' + name) }).first();
  // step to ~3 s so actors are mid-junction
  const next = p.locator('button[aria-label="Step forward"]').first();
  for (let i = 0; i < 30; i++) { await next.click().catch(() => {}); }
  await shot('orbit');
  await rect('canvas', p.locator('canvas').first());
  for (const k of ['Orbit', "Bird's-eye", 'Chase', 'Camera']) await rect('view.' + k, view(k));
  await rect('layers', p.locator('text=Layers').first());
  await rect('agents', p.locator('text=Agents').first());
  await rect('strip', p.locator('text=CAMERAS').first());
  await rect('play', p.locator('button[aria-label*="Play" i]').first());
  await rect('timeline', p.locator('text=boxes in').first());
  await rect('scores', p.locator('text=mAP@0.5').first());
  await rect('colour.class', p.getByRole('button', { name: 'Class', exact: true }));
  await rect('boxes.solid', p.getByRole('button', { name: 'Solid', exact: true }));
  await view("Bird's-eye").click(); await shot('bev');
  await view('Chase').click(); await shot('chase');
  await view('Camera').click(); await shot('camera');
  await view('Orbit').click();
  await p.getByRole('button', { name: 'Class', exact: true }).first().click();
  await p.getByRole('button', { name: 'Solid', exact: true }).first().click();
  await shot('class_solid');
  await p.getByRole('button', { name: 'Agent', exact: true }).first().click();
  await p.getByRole('button', { name: 'Wire', exact: true }).first().click();
  // compare with the baseline run
  await p.locator('#prediction-compare').scrollIntoViewIfNeeded(); await p.waitForTimeout(400);
  checkText(await p.innerText('body'), `${out}/scrolled.png`);
  await p.screenshot({ path: `${out}/scrolled.png` }); await rect('compare', p.locator('#prediction-compare'));
  await p.click('#prediction-compare'); await p.waitForTimeout(300);
  await rect('compare.option', p.getByRole('option', { name: /baseline/ }));
  checkText(await p.innerText('body'), `${out}/compare_open.png`);
  await p.screenshot({ path: `${out}/compare_open.png` });
  await p.getByRole('option', { name: /baseline/ }).first().click(); await p.waitForTimeout(1500);
  await shot('compare');
  await rect('compare.scores', p.locator('text=/Evaluation/').first());
  fs.writeFileSync(`${out}/targets.json`, JSON.stringify(T, null, 1));
  fs.writeFileSync(`${out}/text.txt`, await p.innerText('body'));
  }
  await ctx.close();
  // playback loops, stepped frame by frame (paused) so nothing buffers
  for (const mode of (process.env.LOOPS || 'Orbit,Chase').split(',')) {
    const c = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.25, colorScheme: 'dark' });
    const q = await c.newPage();
    await q.goto(`http://127.0.0.1:${PORT}/#/viewer?scene=demo-junction`, { waitUntil: 'load' });
    await q.waitForSelector('text=/[0-9,.]+ ?k? pts/', { timeout: 90000 }); await q.waitForTimeout(3000);
    await prep(q);
    await q.getByRole('button', { name: new RegExp('^' + mode) }).first().click(); await q.waitForTimeout(1500);
    const nxt = q.locator('button[aria-label="Step forward"]').first();
    const prev = q.locator('button[aria-label="Step back"]').first();
    for (let k = 0; k < 120; k++) {
      const tx = await q.evaluate(() => document.body.innerText);
      if (/sample\s+1\s*\/\s*100/.test(tx)) break;
      await prev.click(); await q.waitForTimeout(40);
    }
    const dir = `${out}/loop_${mode.toLowerCase()}`; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
    for (let i = 0; i < 80; i++) {
      await q.waitForFunction((n) => new RegExp('sample\\s+' + n + '\\s*/\\s*100').test(document.body.innerText.replace(/ /g, ' ')), i + 1, { timeout: 30000 });
      await q.waitForTimeout(350);
      const text = await q.innerText('body');
      noteLoop(text, `${mode} frame ${i}`);
      checkText(text, `${dir}/f${String(i).padStart(3, '0')}.png`);
      await q.screenshot({ path: `${dir}/f${String(i).padStart(3, '0')}.png` });
      await nxt.click();
    }
    await c.close();
  }
  await b.close();
})();
