import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const root=process.cwd(),require=createRequire(path.join(root,'tools/grokbot/package.json'));
const browser=await require('puppeteer-core').launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
try {
 const page=await browser.newPage();page.on('pageerror',e=>{throw e;});await page.setViewport({width:1120,height:1400});
 await page.goto('http://127.0.0.1:8765/tools/grok-ball/index.html');await page.waitForFunction('window.previewReady');
 fs.mkdirSync('build/grok-ball',{recursive:true});
 await page.screenshot({path:'build/grok-ball/preview.png',fullPage:true});
 console.log(await page.$$eval('.card',cards=>cards.map(c=>c.dataset.state+' '+c.innerText)));
 await page.click('[data-state="37"]');if(!await page.$eval('#status',e=>e.textContent.includes('输出回复')))throw Error('Selection failed');
 await page.setViewport({width:390,height:844});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
 await page.screenshot({path:'build/grok-ball/mobile.png',fullPage:true});
}finally{await browser.close();}
