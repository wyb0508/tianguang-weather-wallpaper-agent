ObjC.import('Cocoa');ObjC.import('WebKit');ObjC.import('Quartz');ObjC.import('CoreLocation');
const fm=$.NSFileManager.defaultManager, prefs=$.NSUserDefaults.standardUserDefaults, res=ObjC.unwrap($.NSBundle.mainBundle.resourcePath);
const temp=ObjC.unwrap($.NSTemporaryDirectory())+'WeatherScene-'+ObjC.unwrap($.NSUUID.UUID.UUIDString);
const qaResult=ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey('TIANGUANG_QA_RESULT')) || '';
const weatherFile=temp+'/forecast.json';
let statusItem,menu,handler,manager,locDelegate,info,locInfo,views=[],screenKey='',gps=null,forecast=null,fetcher=null,nextFetch=0,locationText='等待系统定位',weatherText='等待天气',preview='live',lastCheck=0,locating=false,lastFix=0,fetchPoint=null;
function str(o){return o&&!o.isNil()?ObjC.unwrap(o):''}
function get(k){return str(prefs.stringForKey(k))}
function save(k,v){prefs.setObjectForKey($(v),k)}
function now(){return Date.now()/1000}
function read(p){return str($.NSString.stringWithContentsOfFileEncodingError(p,4,null))}
function task(path,args){let t=$.NSTask.alloc.init;t.launchPath=path;t.arguments=args;t.standardError=$.NSFileHandle.fileHandleWithNullDevice;t.standardOutput=$.NSFileHandle.fileHandleWithNullDevice;t.launch;return t}
// Our updates are fire-and-forget. Send an actual Objective-C nil for the
// optional block; JXA's block marshaller can manufacture a forwarding block for JS null.
ObjC.bindFunction('objc_msgSend',['void',['id','SEL','id','id']]);
function send(method,value,one){
 const script=$('window.wall && window.wall['+JSON.stringify(method)+']('+JSON.stringify(value)+')');
 for(const view of one?[one]:views){
   $.objc_msgSend(view.web,'evaluateJavaScript:completionHandler:',script,$());
 }
}
function acceptFix(l){if(!l||l.isNil()||l.horizontalAccuracy<0||now()-l.timestamp.timeIntervalSince1970>3600)return;let coord=l.coordinate,p=[+coord.latitude,+coord.longitude];if(!p.every(Number.isFinite))return;
 if(!gps||Math.abs(p[0]-gps[0])+Math.abs(p[1]-gps[1])>.025){nextFetch=0;if(gps&&Math.abs(p[0]-gps[0])+Math.abs(p[1]-gps[1])>.25){forecast=null;send('forecast',null)}}
 gps=p;save('lastGPS',JSON.stringify(p));lastFix=now();locationText='已定位 · '+p.map(x=>x.toFixed(2)+'°').join('，');manager.stopUpdatingLocation;locating=false}
ObjC.registerSubclass({name:'WeatherLocationDelegate',protocols:['CLLocationManagerDelegate'],methods:{
 'locationManager:didUpdateLocations:':{types:['void',['id','id']],implementation:function(m,l){acceptFix(l.lastObject)}},
 'locationManager:didFailWithError:':{types:['void',['id','id']],implementation:function(m,e){locationText='定位暂不可用 · 请检查定位服务';locating=false;m.stopUpdatingLocation}}
}});
function locate(){lastCheck=now();if(!manager)return;manager.requestWhenInUseAuthorization;manager.startUpdatingLocation;locating=true}
function weather(){
 if(fetcher&&!fetcher.running){let ok=false;if(fetcher.terminationStatus===0)try{let val=JSON.parse(read(weatherFile));if(val.hourly.time.length>=24&&val.daily.sunrise.length>=2&&val.current&&gps&&Math.abs(fetchPoint[0]-gps[0])+Math.abs(fetchPoint[1]-gps[1])<.25){forecast=val;save('forecast',JSON.stringify({p:fetchPoint,data:val}));send('forecast',val);ok=true}}catch(e){}
 weatherText=ok?'已更新今天与明天的预报 · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'天气更新失败 · 将在 2 分钟后重试';nextFetch=now()+(ok?900:120);fetcher=null}
 if(!fetcher&&gps&&now()>=nextFetch){const fields='weather_code,cloud_cover,precipitation,rain,showers,snowfall,wind_speed_10m,wind_direction_10m,wind_gusts_10m';fetchPoint=gps.slice();const url='https://api.open-meteo.com/v1/forecast?latitude='+gps[0].toFixed(3)+'&longitude='+gps[1].toFixed(3)+'&current='+fields+'&hourly='+fields+'&daily=sunrise,sunset&forecast_days=2&timezone=auto&timeformat=unixtime';fetcher=task('/usr/bin/curl',['-fsS','--max-time','25','-o',weatherFile,url]);weatherText='正在读取今明两天的天气…'}}
function screens(){let ss=$.NSScreen.screens,items=[];for(let i=0;i<ss.count;i++)items.push(ss.objectAtIndex(i));let key=items.map(s=>{let f=s.frame;return [f.origin.x,f.origin.y,f.size.width,f.size.height].join(',')}).join('|');if(key===screenKey)return;screenKey=key;views.forEach(v=>v.win.orderOut(null));views=[];
 for(let s of (qaResult?items.slice(0,1):items)){let f=s.frame,win=$.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(f,0,2,false);win.setFrameDisplay(f,true);win.releasedWhenClosed=false;win.level=Number($.CGWindowLevelForKey(2))+1;win.opaque=true;win.hasShadow=false;win.ignoresMouseEvents=true;win.collectionBehavior=1|16|64;
 let web=$.WKWebView.alloc.initWithFrameConfiguration($.NSMakeRect(0,0,f.size.width,f.size.height),$.WKWebViewConfiguration.alloc.init);web.autoresizingMask=18;win.contentView=web;
 let file=$.NSURL.fileURLWithPath(res+'/scene.html');let url=$.NSURL.URLWithStringRelativeToURL(qaResult?'#qa':'#desktop',file);web.loadFileURLAllowingReadAccessToURL(url,file.URLByDeletingLastPathComponent);if(qaResult)win.alphaValue=.015;win.orderFrontRegardless;views.push({win,web,ready:false})}}
function sync(){for(let v of views){if(!v.ready&&!v.web.loading&&v.web.URL&&!v.web.URL.isNil()){if(forecast)send('forecast',forecast,v);send('preview',preview,v);v.ready=true}}}
function item(title,action,tag){let m=$.NSMenuItem.alloc.initWithTitleActionKeyEquivalent(title,action?'act:':null,'');if(action){m.target=handler;m.representedObject=$(action);if(tag!==undefined)m.tag=tag}menu.addItem(m);return m}
function act(m){let a=str(m.representedObject);
 if(a==='loc'){locate();nextFetch=0}
 else if(a==='geo'){task('/usr/bin/open',['x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices'])}
 else if(a==='quit')$.NSApp.terminate(null);
 else{preview=a;send('preview',a);}}
ObjC.registerSubclass({name:'WeatherMenuTarget',methods:{'act:':{types:['void',['id']],implementation:act}}});
let initialized=false;
function initialize(){
 if(initialized)return;
 if(qaResult){$.NSApp.setActivationPolicy(1);screens();initialized=true;return;}
 if(!fm.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(temp,true,$.NSDictionary.dictionary,null))throw Error('无法创建壁纸临时目录');
 $.NSApp.setActivationPolicy(1);handler=$.WeatherMenuTarget.alloc.init;menu=$.NSMenu.alloc.init;statusItem=$.NSStatusBar.systemStatusBar.statusItemWithLength(-1);statusItem.button.title='天气壁纸';statusItem.menu=menu;
 locInfo=item(locationText);info=item(weatherText);item('天气数据 · Open-Meteo');menu.addItem($.NSMenuItem.separatorItem);
 item('更新位置与天气','loc');item('系统定位权限…','geo');menu.addItem($.NSMenuItem.separatorItem);
 item('跟随当地天气','live');(()=>{let parent=item('天气特效预览'),root=menu;menu=$.NSMenu.alloc.init;parent.submenu=menu;['日出','日落','黄昏','晴朗','深夜','多云','大风','小雨','大雨','小雪','中雪','大雪','暴雪'].forEach(v=>item(v,v));menu=root})();menu.addItem($.NSMenuItem.separatorItem);item('退出动态壁纸','quit');
 try{gps=JSON.parse(get('lastGPS'));if(gps)locationText='使用上次定位 · '+gps.map(x=>x.toFixed(2)).join('，')}catch(e){}try{let cache=JSON.parse(get('forecast'));if(gps&&Math.abs(cache.p[0]-gps[0])+Math.abs(cache.p[1]-gps[1])<.25)forecast=cache.data}catch(e){}
 locDelegate=$.WeatherLocationDelegate.alloc.init;manager=$.CLLocationManager.alloc.init;manager.delegate=locDelegate;manager.desiredAccuracy=3000;
 screens();locate();initialized=true;save('wallpaperSession',String($.NSProcessInfo.processInfo.processIdentifier))
}
function run(){initialize()}
let lastStatus='';
function idle(){
 let stage='初始化';
 try{
   initialize();
   if(qaResult){
     const title=views.length?str(views[0].web.title):'';
     if(views.length){
       const web=views[0].web;
       try{
         const pid=Number(ObjC.unwrap(web.valueForKey('webProcessIdentifier')));
         if(pid>0) $(JSON.stringify({webContent:pid,weather:title.startsWith('QA-SAMPLE-')?title.slice(10):null})).writeToFileAtomicallyEncodingError(qaResult+'.pids',true,4,null);
       }catch(e){}
     }
     const replay={'QA-WEATHER-east':[75,55,270],'QA-WEATHER-west':[75,55,90],'QA-WEATHER-rain':[65,15,180]};
     if(replay[title]){
       const [code,speed,dir]=replay[title];
       send('forecast',{current:{time:now(),weather_code:code,wind_speed_10m:speed,wind_direction_10m:dir}},views[0]);
     } else if(title==='QA-WEATHER-offline') send('forecast',null,views[0]);
     if(title.startsWith('QA:')){
       $(title.slice(3)).writeToFileAtomicallyEncodingError(qaResult,true,4,null);
       $.NSApp.terminate(null);
     }
     return 1;
   }
   stage='桌面';screens();sync();
   stage='定位';if(now()-lastCheck>900)locate();
   stage='天气';weather();
   locInfo.title=locationText;info.title=weatherText;
   const rendered=views.filter(v=>str(v.web.title)==='桌面已绘制').length;
   const status='已绘制 '+rendered+'/'+views.length+' 个桌面 · '+weatherText;
   if(status!==lastStatus){save('wallpaperStatus',status);lastStatus=status}
 }catch(error){
   const status=stage+'更新错误 · '+String(error);
   if(status!==lastStatus){save('wallpaperStatus',status);lastStatus=status}
   if(info)info.title=status;
 }
 return 1
}
function quit(){if(fetcher&&fetcher.running)fetcher.terminate;fm.removeItemAtPathError(temp,null);return true}

