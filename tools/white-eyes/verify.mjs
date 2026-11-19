import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(new URL('../grokbot/package.json', import.meta.url));
const puppeteer = require('puppeteer-core');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root,'build/white-eyes'); fs.mkdirSync(out,{recursive:true});
const browser = await puppeteer.launch({headless:true, executablePath:process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const errors = [];
try {
  const page = await browser.newPage(); page.on('pageerror', e=>errors.push(e.message));
  await page.setViewport({width:1100,height:1000});
  await page.goto('http://127.0.0.1:8765/tools/white-eyes/index.html?frozenAt=1');
  await page.waitForFunction('window.previewReady');
  await page.screenshot({path:path.join(out,'preview.png')});
  const result = await page.evaluate(async()=>{
    const source = await fetch('../../third_party/grokbot/grok-recipes.json').then(r=>r.json());
    const before = JSON.stringify(source);
    const { createWhiteEyes } = await import('./white-eyes.js');
    const bounds = [], empty = [], failed = [];
    for (const state of Object.keys(source.states)) {
      const host = document.createElement('div'); document.body.append(host);
      const bot = createWhiteEyes(host,{data:source,state,frozenAt:0,reducedMotion:false});
      await bot.ready;
      let visible = 0;
      for (let i=0; i<120; i++) {
        bot.step(2);
        const path = bot.el.querySelector('path'); const d = path.getAttribute('d');
        if (/NaN|Infinity/.test(d)) failed.push(state);
        if (d) {
          const b = path.getBoundingClientRect(), r = bot.el.getBoundingClientRect();
          if (b.width > 0 && b.height > 0) visible++;
          if (b.left < r.left-1 || b.top < r.top-1 || b.right > r.right+1 || b.bottom > r.bottom+1) bounds.push(state);
        }
      }
      if (!visible) empty.push(state);
      bot.destroy(); host.remove();
    }
    return {states:Object.keys(source.states).length,samplesPerState:120,empty,invalid:[...new Set(failed)],clipped:[...new Set(bounds)],sourceUnchanged:before===JSON.stringify(source)};
  });
  await page.click('[data-state="thinking"]');
  if(await page.$eval('#status',e=>e.textContent)!=='THINKING') throw Error('State control failed');
  await page.$eval('#scale',e=>{e.value='2';e.dispatchEvent(new Event('input'));});
  if(await page.evaluate(()=>bot.debugFrame().scale)!==2) throw Error('Scale control failed');
  await page.setViewport({width:390,height:844});
  await page.screenshot({path:path.join(out,'mobile.png')});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw Error('Mobile horizontal overflow');
  await page.goto('http://127.0.0.1:8765/tools/white-eyes/index.html?face&frozenAt=1');
  await page.waitForFunction('window.previewReady');
  await (await page.$('#new')).screenshot({path:path.join(out,'face.png')});
  fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({...result,errors},null,2));
  console.log(JSON.stringify({...result,errors},null,2));
  if(result.clipped.length || result.empty.length || result.invalid.length || !result.sourceUnchanged || errors.length) process.exitCode=1;
} finally {await browser.close();}
