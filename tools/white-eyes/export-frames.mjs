import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(new URL('../grokbot/package.json', import.meta.url));

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const framesRoot = path.join(root, 'build/white-eyes-frames');
const recipes = JSON.parse(fs.readFileSync(path.join(root, 'third_party/grokbot/grok-recipes.json'), 'utf8'));
const availableStates = recipes.lifecycleGroups.flatMap(group => group.states);
const states = process.env.STATES ? process.env.STATES.split(',') : availableStates;
if (states.some(state => !availableStates.includes(state))) throw new Error('Unknown STATES');
const fps = Number(process.env.FPS || 15);
if (![10, 15, 20, 30].includes(fps)) throw new Error('FPS must be 10, 15, 20 or 30');
const size = Number(process.env.SIZE || 200);
if (!Number.isInteger(size) || size < 32 || size > 400 || size % 2) throw new Error('SIZE must be even, 32..400');
const chrome = process.env.CHROME_BIN || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome');
const mime = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};

const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  const file = path.resolve(root, `.${pathname === '/' ? '/tools/white-eyes/render.html' : pathname}`);
  if (!file.startsWith(root)) {
    res.writeHead(403).end();
    return;
  }
  fs.readFile(file, (error, data) => {
    if (error) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
});

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
fs.rmSync(framesRoot, { recursive: true, force: true });
fs.mkdirSync(framesRoot, { recursive: true });

let playwright = null;
try {
  playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
} catch {
  // Fall back to puppeteer-core below.
}

let browser;
let page;
let waitUntil = 'networkidle0';
if (playwright) {
  browser = await playwright.chromium.launch({
    headless: true,
    executablePath: chrome,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  page = await browser.newPage();
  await page.setViewportSize({ width: size, height: size });
  waitUntil = 'networkidle';
} else {
  const puppeteer = require('puppeteer-core').default;
  browser = await puppeteer.launch({
    headless: true,
    executablePath: chrome,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  page = await browser.newPage();
  await page.setViewport({ width: size, height: size, deviceScaleFactor: 1 });
}
const stateSummary = [];

try {
  for (const state of states) {
    const out = path.join(framesRoot, state);
    fs.mkdirSync(out, { recursive: true });
    await page.goto(`http://127.0.0.1:${port}/tools/white-eyes/render.html?state=${state}&size=${size}&seed=stopwatch`, {
      waitUntil,
    });
    await page.waitForFunction('window.bakerReady === true');
    const snapshot = await page.evaluate(() => window.bot.debugFrame());
    const count = Math.max(1, Math.round(snapshot.duration * fps));
    for (let i = 0; i < count; ++i) {
      const encoded = await page.evaluate(async () => {
        const svg = new XMLSerializer().serializeToString(window.bot.el);
        const blob = new Blob([svg], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        try {
          const image = new Image(); image.src = url; await image.decode();
          const canvas = document.createElement('canvas');
          canvas.width = image.width; canvas.height = image.height;
          canvas.getContext('2d').drawImage(image, 0, 0);
          return canvas.toDataURL('image/png').split(',')[1];
        } finally { URL.revokeObjectURL(url); }
      });
      const png = Buffer.from(encoded, 'base64');
      fs.writeFileSync(path.join(out, `${String(i).padStart(4, '0')}.png`), png);
      if (i + 1 < count) await page.evaluate(n => window.bot.step(n), 60 / fps);
    }
    const item = { state, clip: snapshot.clip, duration: snapshot.duration, count, fps, width: size, height: size };
    stateSummary.push(item);
    console.log(`${state.padEnd(10)} ${snapshot.clip.padEnd(12)} ${count} frames`);
  }


} finally {
  await browser.close();
  server.close();
}

fs.writeFileSync(path.join(framesRoot, 'summary.json'), JSON.stringify({ fps, width: size, height: size, states: stateSummary }, null, 2));
console.log(`Transparent RGBA frames written to ${framesRoot}`);
