"""Evidence, native delivery and version comparisons."""
import json
import os
import platform
import plistlib
import shlex
import shutil
import statistics
import subprocess
import time
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from inspect_work import FAIL, NEAREST, PASS, SKIP, digest, picture, read, save, stats

HERE=Path(__file__).resolve().parent


def command(*args, timeout=25):
    return subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)


def machine():
    info=dict(os=platform.platform(),arch=platform.machine(),model="未测得",display="未测得",
              refresh="未测得",physical_memory_bytes=None,gpu_allocation="未测得；进程 RSS 不能换算为 GPU 分配量")
    if platform.system()=="Darwin":
        info["os"]="macOS "+platform.mac_ver()[0]
        info["model"]=command("sysctl","-n","hw.model").stdout.strip()
        info["physical_memory_bytes"]=int(command("sysctl","-n","hw.memsize").stdout)
        info["memory_type"]="Apple Silicon 统一内存" if platform.machine()=="arm64" else "系统物理内存"
        proc=command("system_profiler","SPDisplaysDataType","-json",timeout=45)
        cards=json.loads(proc.stdout)["SPDisplaysDataType"]
        info["display"]=[dict(gpu=card.get("_name"),physical=d.get("_spdisplays_pixels"),
                              mode=d.get("_spdisplays_resolution"))
                         for card in cards for d in card.get("spdisplays_ndrvs",[])]
        info["refresh"]=[d["mode"].split("@")[-1].strip() if "@" in (d["mode"] or "") else "未测得" for d in info["display"]]
    return info


def native(a):
    apps=sorted(a.work.glob("*.app"))
    if not apps:
        a.row("桌面应用",SKIP,"独立核验应用资源、签名、运行与退出","作品中无 .app")
    for app in apps:
        contents=app/"Contents"
        info=plistlib.loads((contents/"Info.plist").read_bytes())
        scene=contents/"Resources/scene.html"
        exe=contents/"MacOS"/info["CFBundleExecutable"]
        required=[scene,exe]
        if exe.name=="applet": required.append(contents/"Resources/Scripts/main.scpt")
        missing=[str(p.relative_to(app)) for p in required if not p.is_file() or not p.stat().st_size]
        a.check("应用资源 "+app.name,not missing and os.access(exe,os.X_OK),"入口可执行，场景和宿主脚本完整",dict(missing=missing,executable=exe.name))
        if a.entry.is_file() and scene.is_file() and a.entry.name=="壁纸预览.html":
            a.check("应用与 HTML 的场景副本",digest(scene)==digest(a.entry),"项目打包的 scene.html 与预览逐字节一致",
                    dict(app=digest(scene),html=digest(a.entry)))
        if not shutil.which("codesign"):
            a.row("签名与 WKWebView",SKIP,"在 macOS 检查应用","当前环境缺少 codesign")
            continue
        verify=command("codesign","--verify","--strict","--deep",app)
        detail=command("codesign","-dv","--verbose=2",app)
        a.check("应用签名",verify.returncode==0,"codesign 严格验证通过",dict(code=verify.returncode,verification=verify.stderr,
                 details=[line for line in detail.stderr.splitlines() if line.startswith(("Signature","Identifier","TeamIdentifier","Authority"))]))
        if not scene.is_file() or not shutil.which("swiftc"):
            a.row("WKWebView 隔离预览",SKIP,"打开包内场景","缺少场景或 Swift 工具链")
            continue
        with tempfile.TemporaryDirectory(prefix="wallpaper-qa-wk-") as temp:
            exe=Path(temp)/"preview"
            build=command("swiftc",HERE/"wk-preview.swift","-o",exe,timeout=90)
            if build.returncode:
                a.row("WKWebView 隔离预览",SKIP,"测试实例能编译",build.stderr[-1500:])
                continue
            evidence=a.out/"wk-preview.json"
            readings=[]
            with subprocess.Popen([str(exe),str(scene),str(evidence)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True) as process:
                deadline=time.monotonic()+50
                while process.poll() is None and time.monotonic()<deadline:
                    snap=command("ps","-p",str(process.pid),"-o","%cpu=,rss=")
                    values=snap.stdout.split()
                    if len(values)==2:
                        readings.append(dict(time=time.time(),pid=process.pid,cpu_percent=float(values[0]),rss_kib=int(values[1])))
                    time.sleep(.25)
                if process.poll() is None: process.kill()
                _,stderr=process.communicate()
                run=subprocess.CompletedProcess([str(exe)],process.returncode,stderr=stderr)
            save(a.out/"wk-process.json",readings)
            if readings:
                a.row("专用 WKWebView 宿主进程",PASS,"分开记录短时预览进程 PID、ps CPU 与 RSS",
                      dict(pid=readings[0]["pid"],samples=len(readings),max_rss_kib=max(r["rss_kib"] for r in readings),
                           duration=readings[-1]["time"]-readings[0]["time"]),["wk-process.json"])
            if not evidence.exists():
                a.row("WKWebView 隔离预览",SKIP,"专用预览进程可运行",run.stderr[-1000:])
                continue
            rows=read(evidence)
            result=next((r for r in rows if "states" in r),{})
            states=result.get("states",[])
            if not states:
                a.row("WKWebView 天气预览",SKIP if any(r.get('unsupported') for r in rows) else FAIL,
                      "包内场景在离线测试宿主中绘制",rows,[evidence.name])
            else:
                a.check("WKWebView 离线预览与天气切换",result.get("photo",0)>0 and result.get("title")=="桌面已绘制" and
                        all((s["snow"]>0)==s["weather"].endswith("雪") for s in states),
                        "无定位输入、屏蔽 HTTP(S) 后显示底图，切换各档雪层",result,[evidence.name])
                visible=not result.get("hidden",True)
                a.row("WKWebView 粒子", (PASS if all((s["far"]+s["near"]>0)==(s["weather"].endswith(("雪","雨")) or s["weather"]=="大风")
                        for s in states) else FAIL) if visible else SKIP,
                      "可见测试窗口的雨雪、大风档有粒子，光照档清空","前后层像素见原始状态；hidden="+str(not visible),[evidence.name])
            a.check("隔离预览退出",run.returncode==0 and bool(states),"专用 WKWebView 进程正常自行退出",
                    dict(exit_code=run.returncode),[evidence.name])
        a.row("桌面菜单退出与系统定位拒绝",SKIP,"验证安装应用的菜单退出及拒绝定位后的实际行为",
              "此版本启动即创建桌面窗口并请求定位。运行证据限于包内场景和专用 WKWebView，未启动用户壁纸。")
        a.row("桌面主进程 / WebContent / GPU",SKIP,"各自 PID 的 CPU、RSS 及 GPU 分配量",
              "此轮资源采样针对独立 Chromium 实例。未取得可归属到原生壁纸的 WebContent、GPU PID 或 GPU 内存。")


def crops(a):
    stage=a.out/"snow-stage.jpg"
    base=a.original or a.photo
    mask=a.maps.get("积雪")
    if not stage.is_file() or base is None or mask is None:
        a.row("同尺度局部对照",SKIP,"原图、许可、交付帧对齐","缺少大雪截图、底图或物理图")
        return
    finished=picture(stage)
    canvas=finished.size
    w,h=base.size
    scale=max(canvas[0]/w,canvas[1]/h)
    dx=(canvas[0]-w*scale)/2
    dy=(canvas[1]-h*scale)/2
    reference=ImageOps.fit(base,canvas)
    permit=ImageOps.fit(mask.convert("RGB"),canvas,method=NEAREST)
    rois=read(a.args.roi) if a.args.roi else {}
    if not rois and a.ids is not None:
        for rid,label in a.region_names.items():
            if label in ('car','tree','grass','windowpane'):
                ys,xs=np.nonzero(a.ids==rid)
                i=len(xs)//2
                rois[label]=[int(xs[i]),int(ys[i])]
    for n,(label,xy) in enumerate(rois.items()):
        x,y=xy
        if not (0<=x<w and 0<=y<h): raise ValueError("抽查坐标超出底图 "+label)
        vx,vy=round(x*scale+dx),round(y*scale+dy)
        if not (0<=vx<canvas[0] and 0<=vy<canvas[1]):
            a.row("大雪局部 "+label,SKIP,"部位在视口内","该视口已裁去此点")
            continue
        radius=min(60,canvas[0]//2,canvas[1]//2)
        box=(max(0,vx-radius),max(0,vy-radius),min(canvas[0],vx+radius),min(canvas[1],vy+radius))
        ww,hh=box[2]-box[0],box[3]-box[1]
        sheet=Image.new("RGB",(3*ww,hh))
        for i,im in enumerate([reference,permit,finished]):
            sheet.paste(im.crop(box),(i*ww,0))
        file=f"crop-{n+1}.png"
        sheet.save(a.out/file)
        a.row("大雪局部 "+label,PASS,"导出同尺度底图、物理区和成品局部供复核",
              dict(photo_xy=xy,screen_crop=box,order=["原图","积雪许可 0/127/255","实际 HTML 帧"]),[file,stage.name])
    if "caption" not in a.data:
        a.row("题字保护",SKIP,"独立题字掩膜或原图标注可用于遮挡判定","无独立题字掩膜；文字局部可随截图人工复核")


def perf_report(a,budget):
    f=a.out/"performance.json"
    if not f.exists():
        a.row("性能测量",SKIP,"同视口无降水、大雨、暴雪","未采样或运行环境不可用")
        for w,limits in budget.items():
            for key,limit in limits.items():
                a.row(f"预算 {w} {key}",SKIP,f"每轮 ≤ {limit}","缺少该轮实测")
        return {}
    perf=read(f)
    result={}
    for s in perf["series"]:
        w=s["weather"]
        values=result.setdefault(w,{})
        for key,field in [("raf_p95_ms","rafMs"),("draw_p95_ms","farDrawMs")]:
            if s[field]: values.setdefault(key,[]).append(s[field]["p95"])
        for role,v in s["roles"].items():
            if v["cpuPercent"]: values.setdefault(role+"_cpu_pct",[]).append(v["cpuPercent"]["mean"])
            if v["rssKiB"]: values.setdefault(role+"_rss_mib",[]).append(v["rssKiB"]["max"]/1024)
    for w,metrics in result.items():
        files=[s["evidence"] for s in perf["series"] if s["weather"]==w]
        a.row("性能实测 "+w,PASS if metrics.get("raf_p95_ms") else SKIP,
              "完成指定观察窗并保存间隔、分角色 CPU 与 RSS",
              {k:[round(x,3) for x in v] for k,v in metrics.items()},files)
    for w,limits in budget.items():
        for key,limit in limits.items():
            vals=result.get(w,{}).get(key)
            a.row(f"预算 {w} {key}",(PASS if max(vals)<=limit else FAIL) if vals else SKIP,
                  f"每轮 ≤ {limit}",vals or "未测得该指标",["performance.json"])
    return result


def compare(a,b,perf,baseperf,budget):
    comparable=a.manifest.get("sha256",a.raw_hash) and a.manifest.get("sha256",a.raw_hash)==b.manifest.get("sha256",b.raw_hash)
    if not comparable:
        comparable=a.photo and b.photo and a.photo.size==b.photo.size and a.photo.tobytes()==b.photo.tobytes()
    if not comparable and a.docs.get("motion.json") and b.docs.get("motion.json"):
        comparable=a.docs["motion.json"].get("image_sha256")==b.docs["motion.json"].get("image_sha256") and a.photo and b.photo and a.photo.size==b.photo.size and a.photo.tobytes()==b.photo.tobytes()
    if not comparable:
        a.row("跨版本图层差异",SKIP,"同一原图","无法证明是同一张图")
    else:
        for key in sorted(a.layers.keys()|b.layers.keys()):
            im,ref=a.layers.get(key),b.layers.get(key)
            if im is None or ref is None or im.size!=ref.size:
                a.row("版本差异 "+key,SKIP,"同层同尺寸","缺少对应图层或尺寸不同")
                continue
            diff=np.any(np.asarray(im.convert("RGBA"))!=np.asarray(ref.convert("RGBA")),axis=2)
            location=stats(diff)
            if location["count"] and a.ids is not None:
                labels=np.asarray(Image.fromarray(a.ids).resize(im.size,NEAREST))
                location["regions"]={a.region_names[k]:int(v) for k,v in zip(*np.unique(labels[diff],return_counts=True))}
            a.row("版本差异 "+key,PASS,"量化相同天气与位置的像素变化",location,["inventory.json","baseline/inventory.json"])
        if (a.out/"comparison-frames.json").exists() and (b.out/"comparison-frames.json").exists():
            refs={v["weather"]:v for v in read(b.out/"comparison-frames.json")}
            for f in read(a.out/"comparison-frames.json"):
                ref=refs.get(f["weather"])
                if not ref or (f["seed"],f["seconds"])!=(ref["seed"],ref["seconds"]):
                    a.row("版本成品 "+f["weather"],SKIP,"同天气、种子、时刻","缺少可配对帧")
                    continue
                im=picture(a.out/f["file"]).convert("RGB")
                old=picture(b.out/ref["file"]).convert("RGB")
                if im.size!=old.size:
                    a.row("版本成品 "+f["weather"],SKIP,"视口尺寸一致","截图尺寸不同")
                    continue
                diff=np.max(np.abs(np.asarray(im,dtype=np.int16)-np.asarray(old)),axis=2)>2
                location=stats(diff)
                if a.ids is not None and diff.any():
                    regions=np.asarray(ImageOps.fit(Image.fromarray(a.ids),im.size,method=NEAREST))[diff]
                    location["regions"]={a.region_names[k]:int(n) for k,n in zip(*np.unique(regions,return_counts=True))}
                a.row("版本成品 "+f["weather"],PASS,"逐像素量化成品变化（RGB 容差 2）；旧版暂隐空气与水纹画布",
                      location,[f["file"],"baseline/"+ref["file"]])
        if (a.out/"fixed-frames.json").exists() and (b.out/"fixed-frames.json").exists():
            refs={(v["weather"],v["seconds"],v["seed"]):v for v in read(b.out/"fixed-frames.json")}
            for frame in read(a.out/"fixed-frames.json"):
                key=(frame["weather"],frame["seconds"],frame["seed"])
                ref=refs.get(key)
                item=f"版本定帧 {key[0]} {key[1]}s"
                if not ref:
                    a.row(item,SKIP,"种子与时刻一致","无匹配的对照帧")
                    continue
                im=picture(a.out/frame["file"])
                old=picture(b.out/ref["file"])
                if im.size!=old.size:
                    a.row(item,SKIP,"视口尺寸一致","帧尺寸不同")
                    continue
                diff=stats(np.any(np.asarray(im)!=np.asarray(old),axis=2))
                a.row(item,PASS,"记录同种子同时间同视口的变化范围",diff,[frame["file"],"baseline/"+ref["file"]])
    meta=("os","model","display","arch")
    same=all(a.meta["environment"].get(k)==b.meta["environment"].get(k) for k in meta)
    for f in ("browser-config.json","browser-env.json"):
        if not (a.out/f).exists() or not (b.out/f).exists(): same=False;continue
        p,q=read(a.out/f),read(b.out/f)
        same &= all(p.get(k)==q.get(k) for k in ("browser","viewport","seconds","warmup","repeat","target_fps"))
    for w,metrics in baseperf.items():
        for key,vals in metrics.items():
            if key in budget.get(w,{}): continue
            current=perf.get(w,{}).get(key)
            # Compare P95 against the baseline repeat envelope. CPU/RSS deltas are descriptive.
            tested=same and bool(current)
            failure=key.endswith("_p95_ms") and tested and statistics.median(current)>max(vals)
            a.row("版本性能 "+w+" "+key, (FAIL if failure else PASS) if tested else SKIP,
                  "P95 重复值中位数 ≤ 旧版各轮最大值；CPU/RSS 记录差值",
                  dict(current=current,baseline=vals,median_delta=statistics.median(current)-statistics.median(vals) if current else None,matched_conditions=same),
                  ["performance.json","baseline/performance.json"] if tested else ["inventory.json"])
