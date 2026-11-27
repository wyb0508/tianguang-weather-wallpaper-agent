/* The oracle uses the original group PNG and a separate inverse cover transform.
   Inputs to the probes are samples of the delivered scene, in original pixels. */
async function probeImage(uri) {
  const im = new Image();
  im.src = uri;
  await im.decode();
  const c = document.createElement('canvas');
  c.width = im.width; c.height = im.height;
  const ctx = c.getContext('2d');
  ctx.drawImage(im,0,0);
  return ctx.getImageData(0,0,c.width,c.height).data;
}
async function prepareProbe(reference) {
  const {config, maps} = weatherMotionScene;
  const [w,h] = config.size;
  const grid = await probeImage(maps.scene);
  const rain = {};
  for (const k of Object.keys(maps.rain)) rain[k] = await probeImage(maps.rain[k]);
  const field = new WeatherParticleMotion.Field(config,grid,rain);
  const canvas = document.createElement('canvas');
  const painter = new WeatherParticleMotion.Painter(canvas,field);
  const mask = await probeImage(reference);
  const isMember = (x,y) => x>=0 && y>=0 && x<w && y<h && mask[(Math.floor(y)*w+Math.floor(x))*4]>127;
  window.probe = {config,field,painter,canvas,isMember,w,h};
  motion.pause();
}
function requireProbe(ok, msg) { if (!ok) throw Error(msg); }
function alphaPixels(canvas) {
  const rgba = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
  let count=0;
  for(let i=3;i<rgba.length;i+=4) if(rgba[i]) count++;
  return count;
}
function snowProbe() {
  const {isMember,field,canvas,painter,config} = probe;
  const name = '大雪';
  motion.setWeather(name);
  const frame = motion.sample(1.25);
  field.set(name,config.profiles[name].wind,1);
  painter.viewport(innerWidth,innerHeight,1,1);
  const p = frame.air.find(p=>p.near && isMember(p.x,p.y) && isMember(p.x-6,p.y) &&
    isMember(p.x+6,p.y) && field.grid[field.offset(p.x,p.y)]>28 && field.visible(field.offset(p.x,p.y),p.z));
  requireProbe(p,'固定时刻缺少车身内的近景雪花');
  const draw = z => {
    painter.draw({air:[{...p,z}],impacts:[]});
    return alphaPixels(canvas);
  };
  const front=draw(p.z), behind=draw(.03);
  requireProbe(front>0 && behind===0,'同位置雪花的前后遮挡错误');
  const materialRows = [...document.getElementById('stage').querySelectorAll('img')].filter(im => im.src.includes('/material-') && im.style.display !== 'none');
  requireProbe(materialRows.length > 0,'赛车验收样例缺少固定雪层');
  const before = materialRows.map(im=>im.currentSrc);
  motion.sample(180);
  requireProbe(materialRows.every((im,i)=>im.isConnected && im.currentSrc===before[i] && im.style.display!=='none'),'雪循环改变了固定材质层');
  return {weather:name,time:1.25,fixed_snow_layers:before.length,second_sample:180,point:[p.x,p.y],scene_depth:field.grid[field.offset(p.x,p.y)]/255,
    front_depth:p.z,behind_depth:.03,front_pixels:front,behind_pixels:behind};
}
function clearProbe() {
  motion.setWeather('大雨');
  const rain=motion.sample(1.25), rainPixels=alphaPixels(motion.canvas);
  requireProbe(rain.air.length && rain.impacts.length && rainPixels,'缺少雨线或落点');
  motion.setWeather('晴朗');
  const immediately=alphaPixels(motion.canvas);
  const clear=motion.sample(12), later=alphaPixels(motion.canvas);
  requireProbe(!immediately && !later && !clear.air.length && !clear.impacts.length,'切晴后画布有残留');
  return {from:'大雨',to:'晴朗',time:1.25,rain_lines:rain.air.length,impacts:rain.impacts.length,
          rain_pixels:rainPixels,immediate_pixels:immediately,after_12s_pixels:later};
}
function shoreProbe(zoom, previous) {
  const {isMember,w,h,painter,field,config,canvas}=probe;
  const name='大雨', time=1.25;
  motion.setWeather(name); motion.resize(innerWidth,innerHeight,zoom);
  const frame=motion.sample(time);
  field.set(name,config.profiles[name].wind,1);
  const ring = previous || frame.impacts.find(p=>{
    const a=(1.2+p.age*8.5)*h/750;
    return p.kind==='ring' && p.age>.2 && isMember(p.x,p.y) && Math.abs(p.x/w-.5)<.12 &&
      (!isMember(p.x,p.y-a*.29) || !isMember(p.x-a,p.y) || !isMember(p.x+a,p.y));
  });
  requireProbe(ring,'固定时刻未找到裁切边界的实际入水轨迹');
  requireProbe(frame.impacts.some(p=>p.x===ring.x && p.y===ring.y && p.age===ring.age),'视口改变了物理落点');
  painter.viewport(innerWidth,innerHeight,zoom,1);
  painter.draw({air:[],impacts:[ring]});
  const rgba=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
  const full=motion.canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
  const s = Math.max(innerWidth/w,innerHeight/h)*zoom;
  let count=0, outside=0, missing=0;
  for(let i=3;i<rgba.length;i+=4) if(rgba[i]) {
    count++;
    const k=(i-3)/4;
    const x=(k%canvas.width+.5-innerWidth/2)/s+w/2;
    const y=(Math.floor(k/canvas.width)+.5-innerHeight/2)/s+h/2;
    if(!isMember(x,y)) outside++;
    if(!full[i]) missing++;
  }
  requireProbe(count && !outside && !missing,'水纹偏移、缺失或越界');
  return {ring,time,view:[innerWidth,innerHeight],zoom,pixels:count,outside,missing_in_composite:missing};
}
async function measureFrames() {
  const ticks=[];
  const player=await WeatherParticleMotion.create(document.getElementById('stage'),weatherMotionScene,
    {paused:true,onFrame:({timestamp})=>ticks.push(timestamp)});
  window.motion=player;
  player.setWeather('大雨');
  for(let i=0;i<6;i++) {player.setWeather('小雪'); player.setWeather('大雨');}
  player.resume(); player.resume();
  await new Promise(resolve=>setTimeout(resolve,3400));
  player.pause();
  const atPause=ticks.length;
  await new Promise(resolve=>setTimeout(resolve,160));
  requireProbe(atPause===ticks.length,'暂停后仍有绘制回调');
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)
    return {reduced_motion:true,callbacks:ticks.length};
  requireProbe(ticks.length>5,'没有测得实际绘制回调');
  const dt=ticks.slice(1).map((t,i)=>t-ticks[i]);
  const sort=[...dt].sort((a,b)=>a-b);
  player.destroy();
  return {weather:'大雨',view:[innerWidth,innerHeight],target_fps:weatherMotionScene.config.fps,
    interval_ms:dt,median_ms:sort[Math.floor(sort.length/2)],p95_ms:sort[Math.ceil(sort.length*.95)-1],
    min_ms:sort[0],max_ms:sort.at(-1),duration_ms:ticks.at(-1)-ticks[0],callbacks:ticks.length};
}
