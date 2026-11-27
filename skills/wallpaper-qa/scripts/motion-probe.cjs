// Fixed-time sampling through the shipped weather-particle-motion/v1 interface.
const {createHash} = require('node:crypto');
const path = require('node:path');
const fs = require('node:fs');
module.exports = async function probe(page,cfg,out,add,save,selectWeather,presets) {
  const config=await page.evaluate(()=>window.weatherMotionScene.config);
  const declared=config.sample_times??config.sample_seconds;
  const times=Array.isArray(declared)?declared:[declared??1.25];
  const states=[];
  await page.evaluate(()=>window.motion.pause());
  for(const w of presets){
    await selectWeather(page,w);
    for(const seconds of times){
      const frame=await page.evaluate(t=>{
        const api=window.motion;
        const first=api.sample(t), a=api.canvas.toDataURL();
        const second=api.sample(t), b=api.canvas.toDataURL();
        let nonzero=0;
        const c=api.canvas, pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
        for(let i=3;i<pixels.length;i+=4)if(pixels[i])nonzero++;
        return {equal:a===b&&JSON.stringify(first)===JSON.stringify(second),b,nonzero,
          air:first.air.length,impacts:first.impacts.length};
      },seconds);
      const hash=createHash('sha256').update(frame.b).digest('hex');
      const file='fixed-'+states.length+'.png';
      fs.writeFileSync(path.join(out,file),Buffer.from(frame.b.split(',')[1],'base64'));
      delete frame.b;
      const kind=config.profiles[w].kind;
      states.push({weather:w,seconds,seed:config.seed,file,hash,...frame});
      add('定帧 '+w+' '+seconds+'s',frame.equal&&(kind==='clear'?frame.nonzero===0:(config.profiles[w].gain===0?frame.nonzero===0:frame.nonzero>0))?'通过':'失败',
        '固定种子和时间重复采样相同；晴天粒子层清空',{seed:config.seed,seconds,hash,...frame},['fixed-frames.json',file]);
    }
  }
  save('fixed-frames.json',states);
  const spatial=await page.evaluate(async (times)=>{
    const {config,maps}=weatherMotionScene, [w,h]=config.size;
    const load=async uri=>{
      const im=new Image();im.src=uri;await im.decode();
      const el=document.createElement('canvas');el.width=w;el.height=h;
      const ctx=el.getContext('2d');ctx.drawImage(im,0,0);
      return ctx.getImageData(0,0,w,h).data;
    };
    const atlas=await load(maps.scene);
    const weather='大雨', rain=maps.rain?.[weather]?await load(maps.rain[weather]):null;
    let count=0;
    const errors=[];
    if(config.profiles[weather]){
      motion.setWeather(weather);
      for(const time of times.concat(Array.from({length:16},(_,i)=>i/4))){
        for(const p of motion.sample(time).impacts){
          const i=4*(Math.floor(p.y)*w+Math.floor(p.x));
          const bits=atlas[i+1],water=rain?rain[i+2]>0:!!(bits&4),pool=atlas[i+2]>0;
          const paired=(config.drains?.[weather]||[]).some(pair=>Math.hypot(p.x-pair[1][0]*w,p.y-pair[1][1]*h)<=1);
          const allowed=p.paired?paired:!(bits&25) && (rain?(water?rain[i+1]>0:rain[i]>0):water||pool);
          count++;
          if(!allowed || (p.kind==='ring' && !water&&!pool) || (bits&1)) {
            if(errors.length<20) errors.push({time,xy:[p.x,p.y],kind:p.kind});
          }
        }
      }
    }
    const files=[...document.querySelector('#stage canvas').parentNode.children].map(e=>e.tagName==='CANVAS'?'canvas':decodeURIComponent(new URL(e.src).pathname.split('/').pop()));
    const expected=[config.photo,...config.materials.map(r=>r.path),'canvas',config.caption].filter(Boolean).map(f=>f.split('/').pop());
    let z=null;
    const types=window.WeatherParticleMotion;
    if(types?.Field && types?.Painter && config.profiles['大雪']){
      let point=null;
      for(let y=4;y<h-4&&!point;y+=3)for(let x=4;x<w-4;x+=3){
        const i=4*(y*w+x);
        if((atlas[i+1]&3)===2&&atlas[i]>=5&&atlas[i]<=249){point={x:x+.5,y:y+.5,depth:atlas[i]/255};break}
      }
      if(point){
        const c=document.createElement('canvas'),field=new types.Field(config,atlas,{});
        field.set('大雪',0,1);
        const painter=new types.Painter(c,field);painter.viewport(w,h,1,1);
        const alpha=z=>{
          painter.draw({air:[{...point,kind:'snow',near:false,z,detail:0,depth:.6}],impacts:[]});
          return c.getContext('2d').getImageData(Math.floor(point.x),Math.floor(point.y),1,1).data[3];
        };
        z={point,back:alpha(0),front:alpha(1)};
      }
    }
    return {count,errors,files,expected,z};
  },times);
  add('实际动画图层顺序',JSON.stringify(spatial.files)===JSON.stringify(spatial.expected)?'通过':'失败',
    'DOM 中的照片、材质、粒子画布与题字按清单叠放',{actual:spatial.files,expected:spatial.expected},['browser.json']);
  add('大雨接触位置',spatial.count?(spatial.errors.length?'失败':'通过'):'未测',
    '采样落点符合接触、许可或配对滴水通道',{count:spatial.count,firstErrors:spatial.errors},['browser.json']);
  add('动画前后景遮挡',spatial.z?(spatial.z.back===0&&spatial.z.front>0?'通过':'失败'):'未测',
    '用交付渲染器和原深度图在主体同一点绘制，后方被遮挡，前方可见',spatial.z||'缺少可定位的不透明主体或导出的绘制接口',['browser.json']);
  if(presets.includes('大雨')) {
    await selectWeather(page,'大雨');
    for(const size of [cfg.viewport,{width:390,height:844}]){
      await page.setViewportSize(size);
      const checks=await page.evaluate(({size,times})=>{
        const m=window.motion, [iw,ih]=weatherMotionScene.config.size;
        m.resize(size.width,size.height);
        const scale=Math.max(size.width/iw,size.height/ih);
        const failures=[];let tested=0,visibleInk=0;
        const recorded=[],ctx=m.canvas.getContext('2d'),ellipse=ctx.ellipse;
        ctx.ellipse=function(x,y,rx,ry,...args){
          if(rx>ry*2)recorded.push([x,y]);
          return ellipse.call(this,x,y,rx,ry,...args);
        };
        try{
          for(const t of times){
            recorded.length=0;
            const impacts=m.sample(t).impacts.filter(p=>p.kind==='ring').map(p=>
              [(size.width-iw*scale)/2+p.x*scale,(size.height-ih*scale)/2+p.y*scale]);
            const ink=ctx.getImageData(0,0,m.canvas.width,m.canvas.height).data;
            for(const xy of recorded){
              tested++;
              const [cx,cy]=xy.map(Math.round);
              let hit=false;
              for(let y=Math.max(0,cy-8);y<=Math.min(size.height-1,cy+8)&&!hit;y++)
                for(let x=Math.max(0,cx-16);x<=Math.min(size.width-1,cx+16);x++)
                  if(ink[(y*size.width+x)*4+3]){hit=true;break}
              if(hit)visibleInk++;
              if(!impacts.some(([x,y])=>Math.hypot(x-xy[0],y-xy[1])<=2))failures.push({t,xy,expected:impacts});
            }
          }
        }finally{ctx.ellipse=ellipse}
        return {tested,visibleInk,failures};
      },{size,times:[...times,...Array.from({length:24},(_,i)=>i/8)]});
      const file='motion-rain-'+size.width+'.jpg';
      await page.screenshot({path:path.join(out,file),quality:80});
      add('定帧水纹投影 '+size.width,checks.tested?(checks.failures.length||!checks.visibleInk?'失败':'通过'):'未测','渲染的水纹中心落在同帧落点 ±2 CSS px',checks,[file,'browser.json']);
    }
  }
  await page.setViewportSize(cfg.viewport);
  await page.evaluate(()=>window.motion.resume());
};
