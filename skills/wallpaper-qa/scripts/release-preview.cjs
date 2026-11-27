// Exercise the delivered page in an isolated browser and sample just its processes.
const fs=require('node:fs'), os=require('node:os'), path=require('node:path');
const {pathToFileURL}=require('node:url');
const {execFileSync}=require('node:child_process');
let pw;
for(const name of ['playwright-core','playwright',path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core')]){
  try {pw=require(name);break;} catch {}
}
async function run(){
  const candidates=[process.env.QA_CHROME,'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/usr/bin/chromium','/usr/bin/google-chrome',pw?.chromium.executablePath()];
  const exe=candidates.find(p=>p && fs.existsSync(p));
  if(!pw || !exe){process.exitCode=3;return;}
  const browser=await pw.chromium.launch({executablePath:exe,headless:true});
  try{
    const cdp=await browser.newBrowserCDPSession();
    const context=await browser.newContext({offline:true,viewport:{width:Math.min(3840,+process.argv[3]),height:Math.min(2160,+process.argv[4])}});
    const page=await context.newPage();
    await page.goto(pathToFileURL(process.argv[2]).href,{waitUntil:'load'});
    await page.waitForFunction(()=>window.motion && document.body.dataset.ready==='true',null,{timeout:10000});
    const states=[], memory=[];
    async function sampleMemory(weather){
      const {processInfo}=await cdp.send('SystemInfo.getProcessInfo');
      const targets=processInfo.filter(p=>['browser','renderer','gpu'].includes(p.type.toLowerCase()));
      const rss=new Map(execFileSync('ps',['-o','pid=,rss=','-p',targets.map(p=>p.id).join(',')],{encoding:'utf8'}).trim().split('\n').map(line=>line.trim().split(/\s+/).map(Number)));
      memory.push({weather,processes:targets.map(p=>({role:p.type,pid:p.id,rss_mib:rss.has(p.id)?rss.get(p.id)/1024:null}))});
    }
    const names=await page.locator('#preset option').evaluateAll(options=>options.map(o=>o.value).filter(w=>w!=='live'));
    for (const w of [...names,'晴朗','大雨','暴雪','晴朗']){
      await page.locator('#preset').selectOption(w);
      const state=await page.evaluate(async w=>{
        const api=await window.motionReady;
        await api.whenReady?.();
        const cfg=JSON.parse(document.querySelector('#scene').textContent).config;
        const imgs=[...document.querySelectorAll('#stage [data-material]')];
        const visible=imgs.filter(im=>getComputedStyle(im).display!=='none');
        const expected=cfg.materials.filter(m=>!m.weather||m.weather===w).map(m=>m.path);
        await Promise.all(visible.map(im=>im.decode?.()));
        if(JSON.stringify(visible.map(im=>im.dataset.material))!==JSON.stringify(expected))throw Error(w+' 材质切换错误');
        if(cfg.material_loading==='on-demand' && imgs.length!==visible.length)throw Error(w+' 仍挂载上一档材质');
        api.sample(1.25);
        const c=window.__pixelProbe??=document.createElement('canvas');
        c.width=640;c.height=360;const ctx=c.getContext('2d',{willReadFrequently:true});
        ctx.drawImage(api.canvas,0,0,c.width,c.height);const pixels=ctx.getImageData(0,0,c.width,c.height).data;
        let drawn=0;for(let i=3;i<pixels.length;i+=4) if(pixels[i]) drawn++;
        if(cfg.profiles[w].kind==='clear' && drawn)throw Error(w+' 残留粒子');
        return {weather:w,drawn,kind:cfg.profiles[w].kind,loaded:imgs.length};
      },w);
      if(states.length>=names.length){await page.waitForTimeout(w==='晴朗'?2500:800);await sampleMemory(w);}
      states.push(state);
    }
    console.log(JSON.stringify({states,memory,rss_scope:'Dedicated Chromium processes, GPU process RSS is not VRAM'}));
  } finally {await browser.close();}
}
run().catch(e=>{console.error(e.message);process.exitCode=1;});
