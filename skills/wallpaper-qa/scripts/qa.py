#!/usr/bin/env python3
"""壁纸视觉与性能验收。所有写入位于新的报告目录。"""
import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from inspect_work import Inspection, SKIP, FAIL, PASS, read, save, digest, picture
from reporting import HERE, compare, crops, machine, native, perf_report


def inventory(a):
    entries={}
    for p in sorted(a.work.rglob("*")):
        if not p.is_file() or not p.resolve().is_relative_to(a.work):
            continue
        entries[str(p.relative_to(a.work))]=dict(bytes=p.stat().st_size,sha256=digest(p))
        if p.suffix.lower() in (".png",".jpg",".jpeg",".webp"):
            a.step("完整解码 "+p.name,lambda:picture(p))
    for name,p in a.files.items():
        entries[name+" 输入"]={"path":str(p),"sha256":digest(p)}
    if a.entry.is_file(): entries["HTML 入口"]={"path":str(a.entry),"sha256":digest(a.entry)}
    save(a.out/"inventory.json",dict(files=entries,metadata=a.meta))
    return entries


def browser_config(a):
    motion=a.docs.get("motion.json",{})
    target=a.args.target_fps or motion.get("fps")
    source="用户" if a.args.target_fps else "motion.json" if target else "未测得"
    if target is None and a.data:
        match=re.search(r"setInterval\(\s*frame\s*,\s*(\d+)\s*\)",a.entry.read_text())
        if match and int(match[1])>0:
            target,source=1000/int(match[1]),"交付 HTML 的粒子绘制定时器"
    return dict(entry=str(a.entry),comparison=bool(a.args.compare or getattr(a.args,"as_baseline",False)),viewport=dict(width=a.args.width,height=a.args.height),warmup=a.args.warmup,
                seconds=a.args.seconds,repeat=a.args.repeat,target_fps=target,target_source=source)


def browser(a):
    cfg=browser_config(a)
    save(a.out/"browser-config.json",cfg)
    if not a.entry.is_file() or not shutil.which("node"):
        a.row("HTML 实机探针",SKIP,"使用独立浏览器打开实际交付 HTML","缺少 HTML 入口或 Node.js")
        return
    try:
        p=subprocess.run(["node",str(HERE/"browser.cjs"),str(a.out/"browser-config.json")],text=True,capture_output=True,
                         timeout=120+3*cfg["repeat"]*(cfg["seconds"]+cfg["warmup"]+10))
        if (a.out/"browser.json").exists():
            a.rows.extend(read(a.out/"browser.json"))
        else:
            a.row("浏览器执行",SKIP,"有结构化运行证据",p.stderr[-1200:])
    except (OSError,subprocess.TimeoutExpired) as e:
        a.row("浏览器执行",SKIP,"探针按时完成",str(e))


def report(a):
    counts=dict(Counter(r["status"] for r in a.rows))
    result=dict(version=1,work=str(a.work),reproduce=a.command,summary=counts,metadata=a.meta,checks=a.rows)
    save(a.out/"qa-report.json",result)
    lines=["# 壁纸视觉与性能验收", "",f"作品 **{a.work.name}**。通过 {counts.get(PASS,0)} 项，失败 {counts.get(FAIL,0)} 项，未测 {counts.get(SKIP,0)} 项。","",
           "## 运行条件","","```json",json.dumps(a.meta,ensure_ascii=False,indent=2),"```","",
           "RAF 间隔反映浏览器回调。绘制间隔来自粒子画布 clearRect 的时间戳。静态天气没有连续粒子绘制。CPU 按单核 100% 计，RSS 按同角色进程汇总，GPU 进程的 RSS 是系统内存指标。实际提交帧、显示帧与 GPU 分配量未测得。","",
           "## 复现","","从项目根目录执行。输出参数须指向新目录。","","```sh",a.command,"```",""]
    if (a.out/"performance.json").is_file():
        table=["## 性能实测","","单位为毫秒、单核 CPU 百分比和 MiB。CPU 为该轮均值，RSS 为该轮角色总量峰值。","","| 天气 | 轮次 | 观察秒数 | RAF P95 | 粒子绘制 P95 | 主进程 CPU / RSS | 渲染进程 CPU / RSS | GPU 进程 CPU / RSS |","|---|---:|---:|---:|---:|---:|---:|---:|"]
        for sample in read(a.out/"performance.json")["series"]:
            def metric(name):
                v=sample[name]
                return f"{v['p95']:.2f}" if v else "无连续绘制"
            def resource(role):
                v=sample["roles"].get(role,{})
                cpu,rss=v.get("cpuPercent"),v.get("rssKiB")
                return f"{cpu['mean']:.1f}% / {rss['max']/1024:.1f}" if cpu and rss else "未测得"
            cells=[sample["weather"],str(sample["rep"]),f"{sample['elapsed']:.2f}",metric("rafMs"),metric("farDrawMs"),
                   resource("browser"),resource("renderer"),resource("GPU")]
            table.append("| "+" | ".join(cells)+" |")
        lines[4:4]=table+[""]
    for status in (FAIL,SKIP,PASS):
        lines.extend(["## "+status,""])
        for r in (r for r in a.rows if r["status"]==status):
            actual=json.dumps(r["actual"],ensure_ascii=False) if not isinstance(r["actual"],str) else r["actual"]
            links=" · ".join(f"[{f}](<{f}>)" for f in r["evidence"])
            steps=r["steps"] if r["steps"]!=a.command else "执行上方复现命令。"
            lines.extend([f"### {r['item']}","",f"期望为{r['expected']}。","",actual,"",steps,"",links,""])
    (a.out/"验收报告.md").write_text("\n".join(lines),encoding="utf-8")
    return counts


def command_line(args,work,baseline=False):
    argv=[".venv/bin/python","skills/wallpaper-qa/scripts/qa.py",str(work),"--out","/tmp/壁纸验收-新的目录"]
    for k in ("width","height","seconds","warmup","repeat","target_fps","roi","budget"):
        value=getattr(args,k)
        if value is not None: argv.extend(["--"+k.replace("_","-"),str(value)])
    if not baseline:
        for k in ("source","manifest","handoff","entry","materials","motion","plan","compare"):
            if getattr(args,k): argv.extend(["--"+k,str(getattr(args,k))])
    return shlex.join(argv)


def run(args,work,out,baseline=False):
    a=Inspection(work,out,args,command_line(args,work,baseline))
    a.step("作品输入",a.inputs)
    a.step("透明材质",a.surfaces)
    a.step("新版交接",a.newer)
    a.meta["environment"]=a.step("测试设备信息",machine) or {}
    a.meta["sampling"]=browser_config(a)
    a.step("文件完整性",lambda:inventory(a))
    browser(a)
    try:
        a.step("桌面交付",lambda:native(a))
    except subprocess.TimeoutExpired as e:
        a.row("桌面探针",SKIP,"专用测试实例按时退出",str(e))
    a.step("抽查裁图",lambda:crops(a))
    if "motion.json" not in a.docs:
        a.row("确定性同帧复现",SKIP,"同一种子、同一时间的逐像素证据",
              "旧版没有 seek / seed 接口。天气切换按 150–180 ms 窗口检查，水纹圆心容差 2 CSS px。")
    stats=perf_report(a,read(args.budget) if args.budget else {})
    a.meta["performance_scope"]="绘制与 RAF 回调、独立 Chromium 的 browser/renderer/GPU 进程；实测范围内无预算时仅记录。"
    if (out/"browser-env.json").exists(): a.meta["browser"]=read(out/"browser-env.json")
    return a,stats


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("work",type=Path)
    p.add_argument("--out",required=True,type=Path)
    for k in ("source","manifest","handoff","entry","materials","motion","plan","compare","roi","budget"):
        p.add_argument("--"+k,type=Path)
    for k,default in [("width",960),("height",540),("repeat",2),("target-fps",None)]:
        p.add_argument("--"+k,type=int,default=default)
    p.add_argument("--warmup",type=float,default=10)
    p.add_argument("--seconds",type=float,default=60,help="0 跳过长采样，保留交互探针")
    args=p.parse_args()
    if min(args.width,args.height,args.repeat)<=0 or not (0<=args.seconds<86400 and 0<=args.warmup<86400):
        p.error("视口、轮数或时长无效")
    roots=[p.resolve() for p in [args.work,args.compare] if p]
    if not all(r.is_dir() for r in roots): p.error("输入须为已有作品目录")
    out=args.out.resolve()
    if out.exists() or any(out.is_relative_to(r) for r in roots): p.error("请指定作品目录外尚不存在的输出目录")
    release_status = None
    if (roots[0]/"release.json").is_file():
        from release_gate import audit
        extended = any(x.split("=")[0] not in ("--out",) for x in sys.argv[2:] if x.startswith("--"))
        release_status = audit(roots[0], out/"发布验收" if extended else out)
        if not extended:
            print(out/"验收报告.md", release_status)
            return 0 if release_status.endswith("_verified") else 3 if release_status=="needs_review" else 1
    if args.budget:
        limits=read(args.budget)
        if not isinstance(limits,dict) or not all(isinstance(v,dict) and all(isinstance(n,(float,int)) and n>=0 for n in v.values()) for v in limits.values()):
            p.error("预算格式须为天气到指标上限的映射")
    out.mkdir(parents=True, exist_ok=release_status is not None)
    if args.compare:
        from copy import copy
        old=copy(args)
        old.as_baseline=True
        for k in ("source","manifest","handoff","materials","motion","entry","plan","compare"):
            setattr(old,k,None)
        (out/"baseline").mkdir()
        b,baseperf=run(old,roots[1],out/"baseline",True)
        report(b)
    a,perf=run(args,roots[0],out)
    if args.compare: compare(a,b,perf,baseperf,read(args.budget) if args.budget else {})
    counts=report(a)
    print(out/"验收报告.md",counts)
    if FAIL in counts or release_status == "failed":
        return 1
    if SKIP in counts or release_status == "needs_review":
        return 3
    return 0


if __name__=="__main__":
    sys.exit(main())
