/* Isolated Chromium delivery probe. Run via qa.py. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const {setTimeout: sleep} = require('node:timers/promises');
const cfg = JSON.parse(fs.readFileSync(process.argv[2]));
const out = path.dirname(process.argv[2]);
const save = (name, data) => fs.writeFileSync(path.join(out,name),JSON.stringify(data,null,2));
const rows = [];
const add = (item,status,expected,actual,evidence=['browser.json'],steps='离线打开交付 HTML，按所列天气操作。') =>
  rows.push({item,status,expected,actual,evidence,steps});
let playwright;
for (const lib of ['playwright-core','playwright',path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core')]) {
  try { playwright = require(lib); break } catch {}
}
const ok = test => test ? '通过' : '失败';
async function selectWeather(page, weather) {
  await page.locator('#preset').selectOption(weather);
  await page.evaluate(async () => Promise.all([...document.querySelectorAll('#stage img[src]')].map(i=>i.decode())));
}
function canvasState() {
  const result = {};
  for (const c of document.querySelectorAll('#stage canvas')) {
    const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
    let pixels=0;
    for(let i=3;i<a.length;i+=4) if(a[i]) pixels++;
    result[c.id]=pixels;
  }
  for(const id of ['wet','snow','puddle']) {
    const el=document.getElementById(id);
    if(el) result[id]=!el.hidden && +getComputedStyle(el).opacity>0;
  }
  return result;
}
const summary = a => {
  if(!a.length) return null;
  const sorted = [...a].sort((a,b)=>a-b);
  return {n:a.length,mean:a.reduce((a,b)=>a+b)/a.length,p95:sorted[Math.ceil(.95*a.length)-1],max:sorted.at(-1)};
};
function deltas(times) {return times.slice(1).map((t,i)=>t-times[i]);}
async function resources(cdp) {
  const {processInfo}=await cdp.send('SystemInfo.getProcessInfo');
  const ids=processInfo.filter(p=>p.id>0);
  // CDP returns only processes belonging to this dedicated browser instance.
  let rss = new Map();
  try {
    const ps=execFileSync('ps',['-o','pid=,rss=','-p',ids.map(p=>p.id).join(',')],{encoding:'utf8'});
    rss = new Map(ps.trim().split('\n').map(s=>s.trim().split(/\s+/).map(Number)));
  } catch {}
  return {time:Date.now(),processes:ids.map(p=>({pid:p.id,role:p.type,cpuSeconds:p.cpuTime,rssKiB:rss.get(p.id)??null}))};
}
async function performanceRun(page,cdp,weather,rep) {
  await selectWeather(page,weather);
  await sleep(cfg.warmup*1000);
  await page.evaluate(() => {
    window.qa.draw=[];window.qa.raf=[];window.qa.record=true;
    const frame=t=>{if(window.qa.record){window.qa.raf.push(t);requestAnimationFrame(frame)}};
    requestAnimationFrame(frame);
  });
  const samples=[await resources(cdp)];
  const start=Date.now(), end=start+cfg.seconds*1000;
  while(Date.now()<end) {
    await sleep(Math.min(1000,end-Date.now()));
    samples.push(await resources(cdp));
  }
  const times=await page.evaluate(()=>{window.qa.record=false;return {raf:window.qa.raf,draw:window.qa.draw}});
  const elapsed=(samples.at(-1).time-samples[0].time)/1000;
  const groups = {};
  for(let i=1;i<samples.length;i++) {
    const prev=new Map(samples[i-1].processes.map(p=>[p.pid,p]));
    const current={};
    for(const p of samples[i].processes){
      const q=prev.get(p.pid);
      if(!q || q.role!==p.role) continue;
      const v=current[p.role]??={cpu:0,rss:0,valid:true};
      v.cpu+=100000*(p.cpuSeconds-q.cpuSeconds)/(samples[i].time-samples[i-1].time);
      v.valid &&= p.rssKiB!==null;
      v.rss+=p.rssKiB??0;
    }
    for(const [role,v] of Object.entries(current)){
      const series=groups[role]??={cpu:[],rss:[]};
      series.cpu.push(v.cpu);if(v.valid)series.rss.push(v.rss);
    }
  }
  const roles=Object.fromEntries(Object.entries(groups).map(([role,v])=>[role,{cpuPercent:summary(v.cpu),rssKiB:summary(v.rss)}]));
  const evidence='perf-'+rep+'-'+weather+'.json';
  save(evidence,{weather,rep,elapsed,parameters:cfg,rafMs:times.raf,farClearMs:times.draw,samples});
  return {weather,rep,elapsed,rafMs:summary(deltas(times.raf)),farDrawMs:summary(deltas(times.draw)),roles,evidence};
}
async function main() {
  if(!playwright) throw Error('缺少 playwright-core');
  const chrome=process.env.QA_CHROME || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/usr/bin/google-chrome','/usr/bin/chromium'].find(p=>fs.existsSync(p));
  const browser=await playwright.chromium.launch({executablePath:chrome,headless:true});
  try {
    const context=await browser.newContext({viewport:cfg.viewport,deviceScaleFactor:1,offline:true,serviceWorkers:'block'});
    const errors=[],requests=[];
    await context.route(/^https?:/i,r=>{requests.push(r.request().url().split('?')[0]);return r.abort()});
    const page=await context.newPage();
    const cdp=await browser.newBrowserCDPSession();
    await cdp.send('Browser.setPermission',{permission:{name:'geolocation'},setting:'denied',browserContextId:(await cdp.send('Target.getBrowserContexts')).browserContextIds[0]});
    page.on('pageerror',e=>errors.push(e.message));
    page.on('requestfailed',r=>{if(r.url().startsWith('file:')) errors.push(r.failure()?.errorText+' '+r.url())});
    await page.addInitScript(()=>{
      window.qa={frames:0,ellipses:[],record:false};
      const proto=CanvasRenderingContext2D.prototype, clear=proto.clearRect, ellipse=proto.ellipse;
      proto.clearRect=function(...args){
        if(this.canvas.id==='far'||this.canvas===window.motion?.canvas){window.qa.frames++;if(window.qa.record)window.qa.draw.push(performance.now())}
        if(this.canvas.id==='ripples') window.qa.ellipses=[];
        return clear.apply(this,args);
      };
      proto.ellipse=function(x,y,...rest){
        if(this.canvas.id==='ripples')window.qa.ellipses.push([x,y]);
        return ellipse.call(this,x,y,...rest);
      };
    });
    await page.goto(pathToFileURL(cfg.entry).href,{waitUntil:'load'});
    const modern=await page.evaluate(async()=>{if(!window.motionReady)return false;await window.motionReady;return true});
    const base=await page.evaluate(()=>document.querySelector('#base,#stage img')?.naturalWidth);
    const denied=await page.evaluate(async()=>(await navigator.permissions.query({name:'geolocation'})).state);
    const presets=await page.locator('#preset option').evaluateAll(opts=>opts.map(o=>o.value));
    add('HTML 离线与定位不可用',base===undefined&&!errors.length?'未测':ok(!!base && !errors.length && denied==='denied' && presets.length>0),'本地照片解码，离线且定位被拒后可选择天气',{base,denied,presets,errors:[...errors],requests});
    if(!presets.length) {add('HTML 天气交互','未测','遍历入口支持的天气','入口未提供项目 #preset 适配器');return}
    const viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
    const env={browser:browser.version(),host:'Chromium headless',viewport,presets};
    save('browser-env.json',env);
    if(modern) await require('./motion-probe.cjs')(page,cfg,out,add,save,selectWeather,presets);
    if(!modern) for(const w of presets){
      await selectWeather(page,w);await sleep(150);
      const state=await page.evaluate(canvasState);
      const gain=await page.evaluate(w=>typeof data!=='undefined'?data.weatherGain?.[w]:undefined,w);
      const precip=/[雪雨]$/.test(w),animated=precip||w==='大风';
      const valid=['far','near'].every(k=>k in state);
      const active=state.far+state.near;
      const visible=animated?(gain===0||active>0):active===0;
      const layers=(!('snow' in state)||state.snow===w.endsWith('雪'))&&(!('wet' in state)||state.wet===w.endsWith('雨'));
      add('HTML 天气 '+w,valid?ok(visible&&layers):'未测','切换后粒子与表面可见性符合该档',{state,gain},['browser.json'],'天气选择 '+w+'，等待 150 ms 后读取分层 alpha 和样式。');
    }
    for(const w of ['大雨','暴雪'].filter(w=>presets.includes(w)&&presets.includes('晴朗'))){
      await selectWeather(page,w);await selectWeather(page,'晴朗');await sleep(180);
      if(modern) {
        const n=await page.evaluate(()=>{const c=window.motion.canvas;return c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v)});
        add(w+' 切回晴朗',ok(!n),'单一动态画布完全清空',{hasAlpha:n});continue;
      }
      const s=await page.evaluate(canvasState);
      add(w+' 切回晴朗',ok(s.far===0&&s.near===0&&(s.ripples??0)===0&&!s.snow&&!s.wet&&!s.puddle),'雨雪粒子、涟漪和积留全部清空',s);
    }
    const storm=presets.find(w=>w==='暴雪')||presets.at(-1);
    await selectWeather(page,storm);
    const frames=()=>page.evaluate(()=>window.qa.frames);
    const before=await frames();await sleep(300);const after=await frames();
    await page.emulateMedia({reducedMotion:'reduce'});await sleep(100);
    let a=await frames();await sleep(350);let b=await frames();
    add('减少动态效果',after===before?'未测':ok(a===b),'开启后连续 350 ms 无粒子重绘',{normal:after-before,reduced:b-a});
    await page.emulateMedia({reducedMotion:'no-preference'});
    // Exercise visibilitychange handler without affecting a real window.
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
    a=await frames();await sleep(350);b=await frames();
    await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});await sleep(350);
    const resumed=await frames();
    add('隐藏恢复处理器',after===before?'未测':ok(b===a&&resumed>b),'模拟隐藏停止重绘，恢复后继续',{hiddenDraws:b-a,resumedDraws:resumed-b,method:'合成 visibilitychange；不涵盖系统睡眠及长时间恢复位移'});
    if(!modern&&presets.includes('大雨')) {
      await selectWeather(page,'大雨');
      for(const size of [cfg.viewport,{width:390,height:844}]){
        await page.setViewportSize(size);await sleep(200);
        const water=await page.evaluate(()=>{
          if(typeof data==='undefined'||!data.water_points||!data.size)return null;
          const [iw,ih]=data.size,scale=Math.max(innerWidth/iw,innerHeight/ih);
          const points=data.water_points.slice(0,Math.ceil(40*(data.weatherGain?.['大雨']??1)));
          const expected=points.map(([u,v])=>[(innerWidth-iw*scale)/2+u*iw*scale,(innerHeight-ih*scale)/2+v*ih*scale]);
          return {expected,actual:window.qa.ellipses,viewport:[innerWidth,innerHeight]};
        });
        const filename='rain-'+size.width+'.jpg';
        await page.screenshot({path:path.join(out,filename),quality:77,type:'jpeg'});
        const matched=water && water.actual.length===water.expected.length&&water.expected.every(([x,y],i)=>Math.hypot(x-water.actual[i][0],y-water.actual[i][1])<=2);
        const layout=await page.evaluate(()=>[...document.querySelectorAll('#stage canvas')].map(c=>[c.id,c.width,c.height]));
        add('窗口缩放 '+size.width,ok(layout.length>0&&layout.every(([,w,h])=>w===size.width&&h===size.height)),'画布与当前视口一致',layout,[filename,'browser.json']);
        add('大雨水纹中心 '+size.width,!water||(water.expected.length===0&&water.actual.length===0)?'未测':ok(matched),'实际圆心与 cover 投影落点误差 ≤2 CSS px',water||'无独立落点表',[filename,'browser.json']);
      }
    }
    await page.setViewportSize(cfg.viewport);
    if(!modern&&presets.includes('大雪')){
      await selectWeather(page,'大雪');
      await page.locator('#panel').evaluate(el=>el.hidden=true);
      await page.screenshot({path:path.join(out,'snow-stage.jpg'),quality:95,type:'jpeg'});
      const stack=await page.evaluate(()=>[...document.querySelector('#stage').children].map(el=>({id:el.id,mask:getComputedStyle(el).maskImage!=='none',z:getComputedStyle(el).zIndex})));
      const ids=stack.map(s=>s.id),back=ids.indexOf('far'),mid=ids.indexOf('subject'),front=ids.indexOf('near');
      add('前后景合成顺序',ok(back>=0&&mid>back&&front>mid&&stack[mid].mask&&stack.every(s=>s.z==='auto')),'主体掩膜处于远近粒子两层之间',{stack},['browser.json','snow-stage.jpg']);
      await page.locator('#panel').evaluate(el=>el.hidden=false);
    }
    if(!modern && presets.includes('晴朗')) await require('./occlusion.cjs')(page,out,add);
    add('HTML 执行错误',ok(!errors.length),'切换全部支持的天气无未捕获异常',errors);
    if(cfg.comparison){
      const images=[];
      for(const [i,w] of presets.entries()){
        await selectWeather(page,w);
        let seconds=null;
        if(modern){
          seconds=await page.evaluate(()=>{const cfg=weatherMotionScene.config, list=cfg.sample_times??cfg.sample_seconds;
            const t=Array.isArray(list)?list[0]:list??1.25;motion.pause();motion.sample(t);return t});
        }
        // Old scenes lack a seed; compare their actual composed surface and lighting in isolation.
        const style=await page.addStyleTag({content:'#panel,.controls,#status{visibility:hidden!important}'+
          (modern?'':'#stage canvas{visibility:hidden!important}')});
        const file='compare-'+i+'.png';
        await page.screenshot({path:path.join(out,file)});
        images.push({weather:w,file,seconds,seed:modern?await page.evaluate(()=>weatherMotionScene.config.seed):null});
        await style.evaluate(n=>n.remove());
      }
      if(modern)await page.evaluate(()=>motion.resume());
      save('comparison-frames.json',images);
    }
    if(cfg.seconds>0){
      const perf=[];
      for(let rep=1;rep<=cfg.repeat;rep++){
        for(const w of ['晴朗','大雨','暴雪']){
          if(presets.includes(w)) perf.push(await performanceRun(page,cdp,w,rep));
          else add('性能 '+w,'未测','作品提供该档','入口无此天气');
        }
      }
      save('performance.json',{env,parameters:cfg,series:perf});
    }
  } catch(error) {
    add('HTML 运行异常','失败','实际入口正常加载，支持的交互可执行',String(error.stack));
  } finally {await browser.close()}
}
main().catch(e=>{add('浏览器探针','未测','可启动独立浏览器并完成交互',String(e.stack));process.exitCode=2})
.finally(()=>save('browser.json',rows));
