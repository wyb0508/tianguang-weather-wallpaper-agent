const el = id => document.getElementById(id);
const load = id => JSON.parse(el(id).textContent);
const bundle = load('scene'), assets = load('assets'), display = load('display');
el('assets').remove();
const cfg = bundle.config, profiles = cfg.profiles;
const desktop = ['#desktop','#qa'].includes(location.hash);
document.body.classList.toggle('desktop',desktop);
el('name').textContent = display.title;
el('inspect').src = display.inspector;
el('sky').style.webkitMaskImage = 'url("' + display.sky + '")';
if (cfg.caption) {el('protected').src=assets[cfg.caption]; el('protected').hidden=false;}
let mode = desktop ? 'live' : cfg.default_weather;
let forecast = null, motion = null, paused = false, measuring = null;
for (const key of ['live',...Object.keys(profiles)]) el('preset').add(new Option(key==='live'?'跟随当地天气':key,key));
function localLight(t) {
  const daily=forecast?.daily;
  if (daily?.sunrise && daily?.sunset) {
    for(let i=0;i<daily.sunrise.length;i++){
      const rise=daily.sunrise[i], set=daily.sunset[i];
      if(!rise || !set) continue;
      if(Math.abs(t-rise)<1500) return '日出';
      if(Math.abs(t-set)<1500) return '日落';
      if(t>set+1500 && t<set+4800) return '黄昏';
      if(rise<t && t<set) return '晴朗';
      if(t>rise-21600 && t<set+21600) return '深夜';
    }
  }
  const h=new Date(t*1000).getHours();
  return h<6 || h>=20?'深夜':h===6?'日出':h===18?'日落':h===19?'黄昏':'晴朗';
}
function liveState(t) {
  const current=forecast?.current, hourly=forecast?.hourly;
  const index=hourly?.time?.findIndex(x=>x<=t && t<x+3600) ?? -1;
  const field=k=>current && Math.abs(t-current.time)<1800 && current[k]!=null ? current[k] : index>=0 ? hourly[k]?.[index] : null;
  const code=field('weather_code'), speed=field('wind_speed_10m'), gust=field('wind_gusts_10m'), dir=field('wind_direction_10m');
  let weather='晴朗';
  if ([71,73,75,77,85,86].includes(code)) weather=[75,86].includes(code)?(Math.max(speed||0,gust||0)>=50?'暴雪':'大雪'):code===73?'中雪':'小雪';
  else if (code>=50) weather=[65,67,82,95,96,99].includes(code)?'大雨':'小雨';
  else if(speed>=40) weather='大风';
  else if(code>=2) weather='多云';
  const options={};
  if(Number.isFinite(dir) && Number.isFinite(speed)) options.wind=-Math.sin(dir*Math.PI/180)*Math.min(2,Math.max(0,speed)/100);
  return {weather,options};
}
function apply() {
  const t=Date.now()/1000;
  const {weather,options}=mode==='live'?liveState(t):{weather:mode,options:{}};
  const light=mode==='live'?localLight(t):weather;
  motion?.setWeather(weather,options);
  el('preset').value=mode;
  el('light').style.background=({'日出':'#ffa14a','日落':'#d96646','黄昏':'#524870','深夜':'#101c38','晴朗':'#ffdc9d'})[light] || 'transparent';
  el('light').style.opacity=Math.min(.85,({'深夜':.57,'黄昏':.34,'晴朗':.035}[light]??.19)*(profiles[light]?.gain||1));
  el('sky').style.opacity=Math.min(.9,(weather==='多云'?.55:weather.endsWith('雨')?.34:weather.endsWith('雪')?.28:0)*profiles[weather].gain);
  return {weather,wind:options.wind??profiles[weather].wind??0};
}
window.wall={preview(value){if(value!=='live' && !Object.hasOwn(profiles,value)) throw Error('未知天气'); mode=value;return apply();},
             forecast(value){forecast=value;return apply();}};
el('preset').onchange=e=>wall.preview(e.target.value);
el('regions').onclick=()=>{el('inspect').hidden=!el('inspect').hidden;el('regions').setAttribute('aria-pressed',String(!el('inspect').hidden));};
el('pause').onclick=()=>{if(!motion)return;paused=!paused;paused?motion.pause():motion.resume();el('pause').textContent=paused?'继续':'暂停';};
window.motionReady=WeatherParticleMotion.create(el('stage'),bundle,{assets,weather:desktop?liveState(Date.now()/1000).weather:mode,
  onError:e=>{el('status').textContent='材质加载失败 '+e.message;},onFrame:({timestamp})=>measuring?.push(timestamp)}).then(api=>{
  window.motion=motion=api;apply();el('status').textContent='';document.body.dataset.ready='true';
  document.title=desktop?'桌面已绘制':display.title;
  if(mode==='live' || !desktop) setInterval(()=>{if(mode==='live')apply();},60000);
  return api;
}).catch(e=>{el('status').textContent=e.message;document.title='加载失败 '+e.message;throw e;});
// Only the isolated native QA host opens this fragment.
if (location.hash==='#qa') motionReady.then(async api=>{
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const states=[];
  const probe=document.createElement('canvas');probe.width=640;probe.height=360;
  const ctx=probe.getContext('2d',{willReadFrequently:true});
  for(const name of Object.keys(profiles)){
    wall.preview(name); await api.whenReady();
    const a=api.sample(1.25), b=api.sample(1.25);
    if(JSON.stringify(a)!==JSON.stringify(b)) throw Error(name+' 抽帧不稳定');
    const visible=[...el('stage').querySelectorAll('[data-material]')].filter(im=>getComputedStyle(im).display!=='none').map(im=>im.dataset.material);
    const expected=cfg.materials.filter(layer=>layer.weather===name).map(layer=>layer.path);
    if(JSON.stringify(visible)!==JSON.stringify(expected)) throw Error(name+' 显示的材质与清单不一致');
    const kind=profiles[name].kind;
    ctx.clearRect(0,0,640,360);ctx.drawImage(api.canvas,0,0,640,360);
    const pixels=ctx.getImageData(0,0,640,360).data;
    let drawn=0;
    for(let i=3;i<pixels.length;i+=4) if(pixels[i]) drawn++;
    states.push([name,kind,a.air.length,drawn]);
  }
  wall.preview('live');
  async function fixture(step) {
    const previous=forecast;
    document.title='QA-WEATHER-'+step;
    for(let n=0;n<80;n++){
      await wait(100);
      if(forecast!==previous) return apply();
    }
    throw Error('桌面天气桥没有返回 '+step);
  }
  const east=await fixture('east'), eastV=api.sample(1).air[0]?.vx;
  const west=await fixture('west'), westV=api.sample(1).air[0]?.vx;
  const rain=await fixture('rain'), offline=await fixture('offline');
  const performanceSamples={};
  for(const name of ['晴朗','大雨','暴雪']){
    wall.preview(name);await api.whenReady();document.title='QA-SAMPLE-'+name;await wait(500);measuring=[];await wait(3000);
    const times=measuring;measuring=null;
    const intervals=times.slice(1).map((t,i)=>t-times[i]).sort((a,b)=>a-b);
    performanceSamples[name]={frames:times.length,p95_ms:intervals[Math.floor(intervals.length*.95)]??null};
  }
  wall.preview('晴朗');await api.whenReady();document.title='QA-SAMPLE-回晴';await wait(3000);
  document.title='QA:'+JSON.stringify({states,east,west,eastV,westV,rain,offline,performanceSamples,
    viewport:[innerWidth,innerHeight],canvas:[api.canvas.width,api.canvas.height],dpr:devicePixelRatio,
    reduced:matchMedia('(prefers-reduced-motion: reduce)').matches});
}).catch(e=>{document.title='QA:'+JSON.stringify({error:String(e)});});
