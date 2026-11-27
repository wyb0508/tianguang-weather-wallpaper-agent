"""Use the manifest's numbered groups for both pointer and keyboard selection."""
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import secrets
import sys
import time
import webbrowser

import numpy as np
from PIL import Image

from .vision import infer, tensor


def show_subject(folder, scene, groups, key, merges):
    if key == "none":
        return
    small, labels, names = scene
    if key.startswith("group:"):
        area = groups[key[6:]]
    elif key == "salient":
        raw = infer("foreground.onnx", tensor(small, 320, relative=True))[0]
        area = np.asarray(Image.fromarray(raw).resize(small.size, Image.Resampling.BILINEAR)) > .5
    else:
        area = np.isin(labels, [int(i) for i,v in names.items() if merges.get(v.strip(),v.strip())==key])
    out = folder/"已选主体.jpg"
    dim = Image.blend(small,Image.new("RGB", small.size),.72)
    Image.composite(small,dim,Image.fromarray(area)).save(out,quality=92)
    print(f"已选 {key}。高亮预览 {out}", flush=True)
    if sys.platform == "darwin":
        import subprocess
        subprocess.run(["open",out],check=False)


def pick_group(scene, groups, numbers):
    small = scene[0]
    buffer = io.BytesIO()
    small.save(buffer,format="JPEG",quality=90)
    photo = base64.b64encode(buffer.getvalue()).decode()
    selected = []
    endpoint = "/"+secrets.token_urlsafe(24)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != endpoint:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())
        def do_POST(self):
            if self.path != endpoint or not 0 < int(self.headers.get("Content-Length",0)) <= 12:
                self.send_error(400)
                return
            value = self.rfile.read(int(self.headers["Content-Length"])).decode("ascii")
            if not value.isdecimal() or int(value) not in numbers:
                self.send_error(400)
                return
            selected.append(numbers[int(value)])
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    grid = np.zeros((small.height,small.width),np.uint32)
    for number,key in numbers.items():
        grid[groups[key[6:]]] = number
    indices = base64.b64encode(grid.astype("<u4").tobytes()).decode()
    page = """<!doctype html><meta charset=utf-8><title>选择主体</title>
<style>body{background:#182027;color:white;font:16px system-ui}canvas{max-width:96vw;max-height:78vh;cursor:crosshair}</style>
<p>点选图片中的主体，核对高亮区域后确认。</p><canvas></canvas><p><output></output> <button disabled>确认主体</button></p>
<script>const img=new Image(), c=document.querySelector('canvas'),ctx=c.getContext('2d'),button=document.querySelector('button');
const grid=new DataView(Uint8Array.from(atob('GRID'),v=>v.charCodeAt(0)).buffer),names=NAMES;
let id;img.onload=()=>{c.width=img.width;c.height=img.height;ctx.drawImage(img,0,0)};img.src='data:image/jpeg;base64,PHOTO';
c.onclick=e=>{const r=c.getBoundingClientRect(),x=Math.floor((e.clientX-r.left)*c.width/r.width),y=Math.floor((e.clientY-r.top)*c.height/r.height);
id=grid.getUint32((y*c.width+x)*4,true);ctx.drawImage(img,0,0);const d=ctx.getImageData(0,0,c.width,c.height);
for(let i=0;i<d.data.length;i+=4)if(grid.getUint32(i,true)!==id)for(let j=0;j<3;j++)d.data[i+j]*=.28;
ctx.putImageData(d,0,0);document.querySelector('output').textContent=id+' · '+names[id];button.disabled=!names[id]};
button.onclick=async()=>{if((await fetch(location.pathname,{method:'POST',body:String(id)})).ok){button.disabled=true;button.textContent='已确认，请返回终端'}};</script>"""
    page = page.replace("GRID",indices).replace("NAMES",json.dumps(numbers).replace("<","\\u003c")).replace("PHOTO",photo)
    with HTTPServer(("127.0.0.1",0),Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}{endpoint}"
        print("在浏览器中点选主体",url,flush=True)
        if not webbrowser.open(url):
            return None
        server.timeout = 1
        deadline = time.monotonic()+90
        while not selected and time.monotonic()<deadline:
            server.handle_request()
    return selected[0] if selected else None
