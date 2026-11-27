#!/usr/bin/env python3
"""在赛道示例的临时副本注入两个空间故障并验证检测坐标。"""
import argparse
import base64
import io
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from inspect_work import digest, embedded, payload, picture, read, save
from qa import report, run

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("work",type=Path)
    p.add_argument("--source",type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--roi",type=Path)
    args=p.parse_args()
    work=args.work.resolve(strict=True)
    out=args.out.resolve()
    if out.exists() or out.is_relative_to(work): p.error("须使用作品外的新目录")
    out.mkdir(parents=True)
    args.width,args.height,args.seconds,args.warmup,args.repeat=960,540,0,0,1
    for k in ("target_fps","budget","compare","manifest","entry","handoff","materials","motion","plan"):
        setattr(args,k,None)
    reproduction=shlex.join([".venv/bin/python","skills/wallpaper-qa/scripts/selftest.py",str(work),"--out","/tmp/壁纸故障复验-新的目录"]+
                            (["--source",str(args.source)] if args.source else [])+(["--roi",str(args.roi)] if args.roi else []))
    entry=Path("壁纸预览.html")
    group=Path("场景分区/masks/subject.png")
    originals={str(p):digest(work/p) for p in (entry,group)}
    with tempfile.TemporaryDirectory(prefix="wallpaper-qa-fault-") as folder:
        copy=Path(folder)/"故障副本"
        shutil.copytree(work,copy)
        mask=np.asarray(picture(copy/"天气积留/位置/积雪.png"))
        ids=np.asarray(picture(copy/"场景分区/regions.png"))
        rows=read(copy/"场景分区/manifest.json")["regions"]
        car=next(r["id"] for r in rows if r["label"]=="car")
        candidate=(mask==127)&(ids==car)
        # Choose a compact occupied 5×5 patch even on a one-pixel boundary.
        windows=np.lib.stride_tricks.sliding_window_view(candidate,(5,5))
        counts=windows.sum(axis=(2,3),dtype=np.uint8)
        y,x=map(int,np.unravel_index(counts.argmax(),counts.shape))
        dy,dx=np.argwhere(candidate[y:y+5,x:x+5])[0]
        first=[x+int(dx),y+int(dy)]
        html=(copy/entry).read_text()
        uri=payload(copy/entry)["snow"]
        snow=embedded(uri)
        alpha=np.array(snow.getchannel("A"))
        patch=(mask[y:y+5,x:x+5]==127)&(ids[y:y+5,x:x+5]==car)
        alpha[y:y+5,x:x+5][patch]=255
        count=int(patch.sum())
        snow.putalpha(Image.fromarray(alpha))
        buffer=io.BytesIO()
        snow.save(buffer,format="PNG")
        new="data:image/png;base64,"+base64.b64encode(buffer.getvalue()).decode()
        (copy/entry).write_text(html.replace(uri,new,1))
        # Pick a solid sky pixel so the expected effect group is unambiguous.
        sky=next(r["id"] for r in rows if r["label"]=="sky")
        sy,sx=np.where(ids==sky)
        j=len(sx)//2
        gx,gy=int(sx[j]),int(sy[j])
        g=picture(copy/group)
        g.putpixel((gx,gy),255)
        g.save(copy/group)
        a,_=run(args,copy,out)
        a.command=reproduction
        for row in a.rows:
            if "qa.py " in row["steps"]: row["steps"]=reproduction
        a.meta["fixture"]=dict(original=str(work),source_hashes=originals,temporary_copy=True,
                               faults=[dict(weather="大雪",region="car",zone=127,xy=first,pixels=count),
                                       dict(group="subject",region="sky",xy=[gx,gy],pixels=1)])
        snow_row=next(r for r in a.rows if r["item"]=="HTML/大雪/积雪")
        group_row=next(r for r in a.rows if r["item"]=="分组蒙版 subject")
        z=snow_row["actual"]["zones"]["127"]
        verified=(snow_row["status"]==group_row["status"]=="失败" and z["count"]==count and z["first_xy"]==first
                  and z["region"]=="car" and group_row["actual"]["first_xy"]==[gx,gy]
                  and all(digest(work/k)==v for k,v in originals.items()))
        save(out/"injections.json",dict(**a.meta["fixture"],matched=verified,reproduce=reproduction))
        a.row("故障回归断言","通过" if verified else "失败","两处故障的坐标与数量吻合，原件哈希一致",
              {"matched":verified},["injections.json"],reproduction)
        report(a)
        if not verified: sys.exit("故障检测结果与注入不符")
        print(out/"验收报告.md",a.meta["fixture"]["faults"])
if __name__=="__main__": main()
