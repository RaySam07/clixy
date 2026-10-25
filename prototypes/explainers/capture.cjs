const { chromium } = require('playwright');
const { spawn } = require('child_process');
(async () => {
const [recipe, out, fps = '30', stillsArg = ''] = process.argv.slice(2);
const stills = stillsArg ? stillsArg.split(',').map(Number) : null;
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('console', (m) => { if (m.type() === 'error') console.log('page error:', m.text()); });
page.on('pageerror', (e) => console.log('page exception:', e.message));
await page.goto(`http://localhost:${process.env.CLIXY_PORT || 8765}/explainer.html?recipe=${recipe}`);
await page.waitForFunction(() => window.ready === true, null, { timeout: 60000 });
const rate = Number(fps);
if (stills) {
  for (const t of stills) {
    for (let k = 20; k >= 0; k--) await page.evaluate((x) => window.renderAt(x), t - k / rate);  // let Ember settle
    await page.screenshot({ path: `${out}_${t.toFixed(1)}.png` });
  }
} else {
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(rate), '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out]);
  const n = Math.round((await page.evaluate(() => window.recipeDuration || 12)) * rate);
  const t0 = Date.now();
  for (let i = 0; i < n; i++) {
    await page.evaluate((x) => window.renderAt(x), i / rate);
    const buf = await page.screenshot({ type: 'png' });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    if (i % 60 === 0) console.log(`frame ${i}/${n} ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
}
await browser.close();
console.log('done', out);
})();
