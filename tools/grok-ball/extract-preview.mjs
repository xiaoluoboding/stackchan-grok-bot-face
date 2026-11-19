// Browser preview only: full precision; does not write firmware data.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(path.join(root,'tools/grokbot/package.json'));
const browser=await require('puppeteer-core').launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const periods=[5200,1100,3600,1400,1200,4400,2400,8000,1200,2040,2800,2000];
const oneShots=[31,33,34,38,41];
const result=[];
try {
 const page=await browser.newPage();await page.setViewport({width:240,height:240});
 await page.setContent('<div id="mount" style="width:200px;height:200px"></div>');
 await page.evaluate(()=>{
  window.clock=1000;Object.defineProperty(performance,'now',{value:()=>window.clock});
  window.requestAnimationFrame=()=>0;window.cancelAnimationFrame=()=>{};
 });
 await page.addScriptTag({path:path.join(root,'third_party/grok-ball/grok-ball.js')});
 for(let id=30;id<=41;id++) {
  if (id === 38 || id === 39) continue;
  const frames=await page.evaluate(({id,period})=>{
   let seed=123;Math.random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
   window.clock=1000;
   const bot=GrokBall.create('#mount',{emotion:String(id),autostart:false,idle:false,lite:true});
   bot._active=true;
   const svg=bot.ball.svg;
   const paths=svg.querySelectorAll('g')[1].querySelectorAll(':scope > path');
   const eyes=[paths[1],paths[2]];
   if(eyes.some(x=>!x))throw Error('Upstream eye selector changed');
   const frames=[];
   for(let f=0;f<20;f++) {
    const target=1000+f*period/20;
    while(window.clock<target-.001){window.clock=Math.min(target,window.clock+1000/60);bot._tick(window.clock);}
    bot._tick(window.clock);
    frames.push(eyes.map(eye=>{
     const length=eye.getTotalLength(), matrix=eye.getCTM();
     return Array.from({length:96},(_,i)=>{
      const p=eye.getPointAtLength(i/96*length);const v=new DOMPoint(p.x,p.y).matrixTransform(matrix);return [v.x,v.y];
     });
    }));
   }
   bot.destroy();return frames;
  },{id,period:periods[id-30]});
  const points=frames.flat(2),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const cx=(minX+maxX)/2,cy=(minY+maxY)/2;
  const scale=Math.min(1.8,164/(maxX-minX),164/(maxY-minY));
  const data=frames.flat(2).flatMap(([x,y])=>[Math.round((100+(x-cx)*scale)*100)/100,Math.round((100+(y-cy)*scale)*100)/100]);
  if(data.some(x=>x<0||x>199))throw Error('Eye bounds escaped');
  const meta=id===37 ? "输出回复" : await page.evaluate(id=>GrokBall.config.get(String(id)).name,id);
  result.push({id,name:meta,period:periods[id-30],loop:!oneShots.includes(id),frames:20,points:96,data});
  console.log(id,meta,data.length,'coordinates');
 }
} finally {await browser.close();}
fs.writeFileSync(path.join(root,'tools/grok-ball/states-preview.json'),JSON.stringify(result));
