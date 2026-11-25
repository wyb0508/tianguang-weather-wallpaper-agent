const el = id => document.getElementById(id);
const desktop = location.hash === '#desktop';
document.body.classList.toggle('desktop', desktop);
el('base').src = el('subject').src = data.photo;
function setMask(node, url) {
  node.style.maskImage = node.style.webkitMaskImage = 'url("' + url + '")';
}
setMask(el('subject'), data.foreground);
setMask(el('sky'), data.sky);
setMask(el('ripples'), data.water);
el('snow').src = data.snow;
el('wet').src = data.rain;
el('inspect').src = data.inspector;
el('name').textContent = data.title;
for (const [key, [name, color]] of Object.entries(data.legend)) {
  if (data.coverage[key] < .004) continue;
  const label = document.createElement('span');
  const dot = document.createElement('i');
  dot.style.backgroundColor = color;
  label.append(dot, name + ' ' + Math.round(100 * data.coverage[key]) + '%');
  el('legend').append(label);
}
const presets = ['晴朗','日出','日落','黄昏','深夜','多云','大风','小雨','大雨','小雪','大雪'];
for (const name of presets) el('preset').add(new Option(name,name));
let mode = desktop ? 'live' : '大雪';
let forecast = null, strength = 0, type = '', wind = 0;
function liveWeather() {
  const t = Date.now()/1000, hourly = forecast?.hourly;
  const i = hourly?.time?.findIndex((x,j) => x <= t && t < x + 3600);
  const current = forecast?.current;
  const code = i >= 0 ? hourly.weather_code[i] : Math.abs(t - current?.time) < 5400 ? current.weather_code : 0;
  if ([71,73,75,77,85,86].includes(code)) return [75,86].includes(code) ? '大雪' : '小雪';
  if (code >= 50) return [65,67,82,95,96,99].includes(code) ? '大雨' : '小雨';
  if (code >= 2) return '多云';
  return '晴朗';
}
function daylight() {
  const date = new Date(), t = date.getTime()/1000, daily = forecast?.daily;
  if (daily) {
    if (daily.sunrise.some(x => Math.abs(t-x)<1500)) return '日出';
    if (daily.sunset.some(x => Math.abs(t-x)<1500)) return '日落';
    if (daily.sunrise.some((x,i) => x<=t && t<daily.sunset[i])) return '晴朗';
    if (daily.sunrise[0]-86400<t && t<daily.sunset.at(-1)+86400) return '深夜';
  }
  const h = date.getHours();
  return h<6 || h>=20 ? '深夜' : h===6 ? '日出' : h===18 ? '日落' : h===19 ? '黄昏' : '晴朗';
}
function applyWeather() {
  const p = mode==='live' ? liveWeather() : mode;
  const light = mode==='live' ? daylight() : p;
  type = p.endsWith('雪') ? 'snow' : p.endsWith('雨') ? 'rain' : '';
  strength = type ? (p.startsWith('大') ? 1 : .45) : 0;
  wind = p==='大风' ? 1 : type==='rain' ? .55 : .3;
  el('snow').style.opacity = type==='snow' ? strength : 0;
  el('wet').style.opacity = type==='rain' ? strength : 0;
  el('sky').style.opacity = p==='多云' ? .64 : type==='rain' ? .42 : type==='snow' ? .25 : 0;
  el('light').style.background = ({'日出':'#ffe2ab','日落':'#ffc6a4','黄昏':'#a59cba','深夜':'#586581'})[light] || 'transparent';
}
window.wall = {};
el('preset').value = mode;
el('preset').onchange = e => window.wall.preview(e.target.value);
el('toggle').onclick = () => {
  const show = el('inspect').hidden;
  el('inspect').hidden = el('legend').hidden = !show;
  el('toggle').setAttribute('aria-pressed', String(show));
};
let width=0, height=0;
const canvases = ['far','near','ripples'].map(id=>el(id));
const [far,near,ripples] = canvases.map(c=>c.getContext('2d'));
function resize() {
  width = innerWidth; height = innerHeight;
  for (const c of canvases) {c.width=width; c.height=height}
}
resize();
addEventListener('resize', resize);
const flakes = Array.from({length:140}, () => [Math.random(),Math.random(),Math.random(),Math.random()]);
function drawParticles(ctx, time, front) {
  ctx.clearRect(0,0,width,height);
  const gust = mode==='大风';
  if (!strength && !gust) return;
  const count = Math.min(flakes.length, Math.ceil(width/13 * (gust ? .35 : strength) * (front ? .28 : 1)));
  ctx.fillStyle = front ? '#fbfdfff0' : '#edf6ffd6';
  ctx.strokeStyle = gust ? '#f2e5b774' : front ? '#dceeffb0' : '#dceeff75';
  ctx.lineWidth = front ? 1.4 : 1;
  for(let i=0; i<count; i++){
    const [x0,y0,z,phase] = flakes[front ? flakes.length-1-i : i];
    const speed = (front?1.35:.8) * (.65+z);
    const dy = gust ? 9 : type==='rain' ? 280 : 34;
    const x = (x0*width + time*speed*(gust ? 150 : wind*52) + (type==='snow'?Math.sin(time*.8+phase*23)*10:0) + width*2) % (width+18)-9;
    const y = (y0*height + time*speed*dy) % (height+22)-11;
    ctx.beginPath();
    if(type==='snow'){ctx.arc(x,y,(front?1.5:.65)+z,0,2*Math.PI);ctx.fill()}
    else {ctx.moveTo(x,y);ctx.lineTo(x+(gust?12:2),y+(gust?2:8+z*7));ctx.stroke()}
  }
}
function drawWater(time) {
  ripples.clearRect(0,0,width,height);
  if(type!=='rain') return;
  const [iw,ih]=data.size, scale=Math.max(width/iw,height/ih);
  ripples.strokeStyle='#d6f2ff';
  data.water_points.slice(0,Math.ceil(40*strength)).forEach(([u,v],i)=>{
    const phase=(time*.85+i*.328)%1;
    ripples.globalAlpha=(1-phase)*.55;
    ripples.beginPath();
    ripples.ellipse((width-iw*scale)/2+u*iw*scale,(height-ih*scale)/2+v*ih*scale,1+phase*12,1+phase*3,0,0,2*Math.PI);
    ripples.stroke();
  });
}
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
function frame() {
  if(document.hidden) return;
  const t = reduced.matches ? 1 : performance.now()/1000;
  drawParticles(far,t,false); drawParticles(near,t,true); drawWater(t);
}
let timer;
function refresh(){
  applyWeather(); clearInterval(timer); frame();
  if(!document.hidden && !reduced.matches && (strength || mode==='大风')) timer=setInterval(frame,50);
}
window.wall.forecast = value => {forecast=value;refresh()};
window.wall.preview = value => {mode=value;refresh()};
addEventListener('resize',frame);
document.addEventListener('visibilitychange',refresh);
reduced.addEventListener('change',refresh);
setInterval(()=>{if(mode==='live')refresh()},60000);
refresh();
if(desktop) window.addEventListener('load',()=>{document.title='桌面已绘制'});
