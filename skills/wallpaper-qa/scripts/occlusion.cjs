// Contrast two diagnostic particles on the delivered stage at one solid subject point.
const path=require('node:path');
module.exports=async function occlusion(page,out,add) {
  const point=await page.evaluate(async()=>{
    if(typeof data==='undefined'||!data.foreground)return null;
    const mask=new Image();mask.src=data.foreground;await mask.decode();
    const c=document.createElement('canvas');c.width=innerWidth;c.height=innerHeight;
    const ctx=c.getContext('2d'),scale=Math.max(c.width/mask.width,c.height/mask.height);
    ctx.drawImage(mask,(c.width-mask.width*scale)/2,(c.height-mask.height*scale)/2,mask.width*scale,mask.height*scale);
    const a=ctx.getImageData(0,0,c.width,c.height).data;
    for(let y=8;y<c.height-8;y+=5)for(let x=8;x<c.width-8;x+=5){
      if([0,-4,4].every(dx=>[0,-4,4].every(dy=>a[((y+dy)*c.width+x+dx)*4+3]===255))) return {x,y};
    }
    return null;
  });
  if(!point){add('前后景遮挡像素','未测','有可测试的不透明主体内部','未找到可靠位置');return}
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(()=>{window.wall.preview('晴朗');document.querySelector('#panel').hidden=true});
  const clip={x:point.x-6,y:point.y-6,width:12,height:12};
  async function shot(key) {
    const buffer=await page.screenshot({clip,path:path.join(out,'occlusion-'+key+'.png')});
    return page.evaluate(async url=>{
      const img=new Image();img.src=url;await img.decode();
      const c=document.createElement('canvas');c.width=c.height=12;
      const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
      return Array.from(ctx.getImageData(6,6,1,1).data).slice(0,3);
    },'data:image/png;base64,'+buffer.toString('base64'));
  }
  async function mark(id) {
    await page.evaluate(({id,point})=>{const ctx=document.getElementById(id).getContext('2d');
      ctx.save();ctx.globalAlpha=1;ctx.fillStyle='#fa00ef';ctx.fillRect(point.x-3,point.y-3,7,7);ctx.restore()}, {id,point});
  }
  const base=await shot('base');await mark('far');const far=await shot('far');await mark('near');const near=await shot('near');
  const good=base.every((n,i)=>Math.abs(n-far[i])<=2)&&near.every((n,i)=>Math.abs(n-[250,0,239][i])<=2);
  add('前后景遮挡像素',good?'通过':'失败','主体同一点，远层诊断色被遮挡，近层诊断色可见（RGB 容差 2）',
    {point,base,far,near},['occlusion-base.png','occlusion-far.png','occlusion-near.png'],
    '独立预览切晴朗，在同一主体内部位置分别绘制 7×7 诊断粒子，截取实际合成像素。');
  await page.evaluate(()=>{document.querySelector('#panel').hidden=false;window.wall.preview('晴朗')});
  await page.emulateMedia({reducedMotion:'no-preference'});
};
