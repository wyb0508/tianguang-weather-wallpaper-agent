"""Audit one portable release, including the HTML and the native bundle it ships."""
import argparse
import base64
import hashlib
import json
import os
import shutil
from io import BytesIO
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
from PIL import Image

WEATHER = ("晴朗","日出","日落","黄昏","深夜","多云","大风","小雨","大雨","小雪","中雪","大雪","暴雪")
PRECIP = WEATHER[7:]


def demand(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root, name):
    p = (root/name).resolve()
    demand(p.is_relative_to(root.resolve()), f"资源超出作品目录 {name}")
    return p


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.items = {}
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = dict(attrs).get("id")
            if self.current:
                demand(self.current not in self.items, "重复的嵌入资源")
                self.items[self.current] = ""

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None

    def handle_data(self, data):
        if self.current:
            self.items[self.current] += data


def geometry(alpha, ref, plan, sheltered=None, weather=None):
    checks = []
    for sample in ref["samples"]:
        if sample.get("weather") and weather not in sample["weather"]:
            continue
        x,y,r,b = sample["box"]
        demand(0 <= x < r <= alpha.shape[1] and 0 <= y < b <= alpha.shape[0], "抽查范围超出原图")
        patch = alpha[y:b,x:r]
        if sample.get("unless_sheltered") and sheltered is not None:
            exposed = ~sheltered[y:b,x:r]
            if not exposed.any():
                checks.append(dict(name=sample["name"], exempt="本抽查区全部受到遮挡"))
                continue
            patch = patch[exposed]
        n = int(np.count_nonzero(patch))
        expect = sample["expect"]
        demand(expect in ("clear","some"), "未知的表面抽查条件")
        demand((n == 0) == (expect == "clear"), f"{sample['name']} 抽查失败，可见积雪像素 {n}")
        checks.append(dict(name=sample["name"], visible=n))
    demand(checks, "缺少有效表面抽查")
    return checks


def inspect(root, release, report):
    def file(key):
        return inside(root, release[key])
    demand(release["schema"] == "tianguang-release/v1", "未知发布格式")
    for name, expected in release["files"].items():
        demand(sha(inside(root,name)) == expected, f"发布后文件变化 {name}")
    plan, regions, places, motion = [read(file(k)) for k in ("answers","manifest","places","motion")]
    hashes = dict(image_sha256=regions["sha256"], manifest_sha256=sha(file("manifest")), answers_sha256=sha(file("answers")))
    demand(regions["sha256"] == release["source_sha256"] and plan == places["plan"], "照片或位置方案不匹配")
    demand(all(motion["inputs"][k] == places[k] == v for k,v in hashes.items()), "动画或位置输入不匹配")
    expected = set(WEATHER)
    demand(all(set(x) == expected for x in (plan["weather"], release["materials"], motion["profiles"])), "天气清单不完整")
    demand(set(release["simulations"]) == set(PRECIP), "雨雪仿真不完整")
    ref = read(file("geometry")) if release.get("geometry") else None
    if ref:
        demand(ref["image_sha256"] == regions["sha256"] and ref["size"] == regions["size"], "抽查样本对应另一张照片")
    scripts = Scripts()
    scripts.feed(file("preview").read_text())
    scripts.close()
    bundle = json.loads(scripts.items["scene"])
    assets = json.loads(scripts.items["assets"])
    directory = file("motion").parent
    demand(bundle["config"] == motion and scripts.items["module"] == (directory/"motion.js").read_text(), "交付动画或配置与审核文件不一致")
    original = json.loads((directory/"scene-data.js").read_text().removeprefix("window.weatherMotionScene=").rstrip(";\n"))
    demand(original == bundle and set(bundle["maps"]["rain"]) == set(PRECIP[:2]), "深度或雨点通道不一致")
    for name in [motion["photo"], motion["caption"], *(m["path"] for m in motion["materials"])]:
        if name:
            demand(base64.b64decode(assets[name].split(",",1)[1], validate=True) == inside(directory,name).read_bytes(), "预览内嵌图层不一致 "+name)
    with Image.open(inside(file("manifest").parent, regions["region_map"])) as image:
        ids = np.asarray(image)
    sheltered_ids = set()
    for key in plan["sheltered"]:
        if key.startswith("group:"):
            for group in regions["groups"]:
                if group["key"] == key[6:]:
                    sheltered_ids.update(group["region_ids"])
        else:
            sheltered_ids.update(r["id"] for r in regions["regions"] if plan["merge_into"].get(r["label"],r["label"]) == key)
    sheltered = np.isin(ids,list(sheltered_ids))
    if "salient" in plan["sheltered"]:
        with Image.open(BytesIO(base64.b64decode(bundle["maps"]["scene"].split(",",1)[1]))) as im:
            sheltered |= (np.asarray(im)[:,:,1] & 8) != 0
    input_hashes, drawn_layers = [], []
    for weather in WEATHER:
        material = inside(root,release["materials"][weather])
        pack = read(material)
        demand(pack["weather"] == weather and all(pack[k] == v for k,v in hashes.items()), f"{weather} 材质照片、分区或方案不一致")
        handoff = inside(root,release["simulations"][weather]) if weather in PRECIP else file("places")
        demand(pack["handoff_sha256"] == sha(handoff), weather+" 仿真指纹不符")
        if weather in PRECIP:
            scenario = read(handoff)["scenario"]
            demand(scenario["weather"] == weather and scenario["plan"] == plan and pack["places_sha256"] == sha(file("places")), weather+" 物理条件不符")
        expected_layers = (["积雪"] if weather.endswith("雪") else ["湿色","水膜","积水反射"] if weather.endswith("雨") else [])
        demand([l["phenomenon"] for l in pack["layers"]] == expected_layers, weather+" 图层缺失")
        input_hashes.append(sha(material))
        row = report["weather"][weather] = {}
        for layer in pack["layers"]:
            path = inside(material.parent, layer["path"])
            with Image.open(path) as im:
                demand(im.mode == "RGBA" and list(im.size) == regions["size"], weather+" 图层网格不符")
                alpha = np.array(im.getchannel("A"))
            event = layer["phenomenon"]
            row[event] = int(np.count_nonzero(alpha))
            demand(row[event] == layer["visible_pixels"], weather+" 可见像素统计不符")
            if event in ("积雪","积水反射"):
                key = "积雪" if event == "积雪" else "积水"
                if places["weather"].get(weather) == key:
                    key = weather
                with Image.open(inside(file("places").parent,places["maps"][key])) as mask:
                    values = np.asarray(mask)
                    demand(np.isin(values,(0,127,255)).all() and not alpha[values!=255].any(), weather+" 材质越过许可")
                if event == "积雪":
                    demand(not alpha[sheltered].any(), weather+" 在已确认遮挡的区块绘出了新雪")
                if ref and event == "积雪":
                    report.setdefault("geometry_checks",{})[weather] = geometry(alpha,ref,plan,sheltered,weather)
            drawn_layers.append((weather,sha(path)))
    demand(motion["inputs"]["materials_sha256"] == input_hashes, "动画未消费全部已审材质")
    demand([(l["weather"],l["sha256"]) for l in motion["materials"]] == drawn_layers, "动画图层与材质输出不一致")
    for layer in motion["materials"]:
        demand(sha(inside(directory,layer["path"])) == layer["sha256"], "动画纹理内容不符")
    evidence = preview_runtime(file("preview"),regions["size"])
    if evidence is None:
        report["pending"].append("缺少可用浏览器探针，HTML 实际运行待测")
    else:
        report["preview_runtime"] = evidence
        states = evidence["states"][:len(WEATHER)]
        demand([s["weather"] for s in states]==list(WEATHER),"预览天气切换不完整")
        demand(all(not s["drawn"] for s in states if s["kind"]=="clear"),"晴天仍有粒子")
        if ref:
            demand(all(s["drawn"]>0 for s in states if s["kind"] in ("rain","snow")), "预览雨雪帧没有可见像素")
    if not ref:
        report["pending"].append("缺少当前照片独立标注的表面抽查")
    if release["app"]:
        app = file("app")
        demand((app/"Contents/Resources/scene.html").read_bytes() == file("preview").read_bytes(), "应用与预览内容不同")
        if sys.platform == "darwin":
            subprocess.run(["codesign","--verify","--strict",app],check=True)
            native(app, report)
        else:
            report["pending"].append("需要在 macOS 验证已打包应用")


def preview_runtime(path, size):
    node = shutil.which("node")
    if node:
        probe = Path(__file__).with_name("release-preview.cjs")
        run = subprocess.run([node,probe,path,*map(str,size)],capture_output=True,text=True,timeout=180)
        if run.returncode != 3:
            demand(run.returncode==0, "预览运行失败 "+run.stderr[-1000:])
            return json.loads(run.stdout)
    if sys.platform == "darwin" and shutil.which("swiftc"):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp)/"preview"
            output = Path(tmp)/"result.json"
            build = subprocess.run(["swiftc",Path(__file__).with_name("wk-preview.swift"),"-o",executable],
                                   capture_output=True,timeout=90)
            if build.returncode == 0:
                subprocess.run([executable,path,output,"--release"],check=True,timeout=60)
                rows = read(output)
                demand(rows and "states" in rows[0], "WebKit 预览运行失败 "+str(rows)[:200])
                return rows[0]
    return None


def native(app, report):
    with tempfile.TemporaryDirectory() as temp:
        result = Path(temp)/"native.json"
        with subprocess.Popen([app/"Contents/MacOS/applet"],env={**os.environ,"TIANGUANG_QA_RESULT":str(result)},
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as proc:
            deadline = time.monotonic()+75
            resident = []
            web_samples = []
            try:
                while time.monotonic()<deadline and not result.is_file() and proc.poll() is None:
                    ps = subprocess.run(["ps","-p",str(proc.pid),"-o","rss="],capture_output=True,text=True)
                    if ps.stdout.strip().isdigit():
                        resident.append(int(ps.stdout.strip()))
                    pid_file = result.with_suffix(".json.pids")
                    if pid_file.is_file():
                        roles = read(pid_file)
                        if roles.get("webContent"):
                            snap = subprocess.run(["ps","-p",str(roles["webContent"]),"-o","rss="],capture_output=True,text=True)
                            if snap.stdout.strip().isdigit():
                                web_samples.append(dict(weather=roles.get("weather"),rss_mib=round(int(snap.stdout)/1024,1)))
                    time.sleep(.5)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if not result.is_file():
                report["pending"].append("原生桌面窗口未返回实测结果")
                return
            data = report["native"] = read(result)
            data["host_peak_rss_mib"] = round(max(resident,default=0)/1024,1)
            data["web_content_rss_samples"] = web_samples
            data["gpu_allocation_mib"] = None
            data["memory_scope"] = "RSS 按宿主和可识别的 WebContent 进程采样；共享 WebKit GPU 的单壁纸分配量不可归属。"
            demand("error" not in data, str(data.get("error")))
            states = data["states"] = [dict(zip(("name","kind","particles","drawn"),row)) for row in data["states"]]
            demand([s["name"] for s in states] == list(WEATHER), "原生窗口天气切换不完整")
            demand(all(s["drawn"] == 0 for s in states if s["kind"]=="clear"), "切换后残留粒子")
            if report.get("geometry_checks"):
                demand(all(s["drawn"] > 0 for s in states if s["kind"] in ("rain","snow")), "原生降水帧没有可见像素")
            demand(data["east"]["weather"] == data["west"]["weather"] == "暴雪" and data["eastV"]>0>data["westV"]
                   and data["rain"]["weather"] == "大雨" and data["offline"]["weather"] == "晴朗", "原生实时天气或风向回放失败")
            samples = data["performanceSamples"]
            if data["reduced"] or any(samples[w]["frames"] < 2 for w in ("大雨","暴雪")):
                report["pending"].append("系统减少动态效果或原生绘制采样不足")
            demand(samples["晴朗"]["frames"]==0, "静态天气仍在循环绘制")


def audit(root, out):
    out.mkdir(parents=True, exist_ok=False)
    report = dict(status="failed", weather={}, pending=[], errors=[])
    try:
        release = read(root/"release.json")
        inspect(root,release,report)
        report["status"] = "needs_review" if report["pending"] else "desktop_verified" if release["app"] else "preview_verified"
    except (OSError, KeyError, IndexError, ValueError, TypeError, subprocess.SubprocessError) as error:
        report["errors"].append(str(error))
    (out/"qa-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    lines = ["# 天气壁纸验收", "", "结果 "+report["status"], ""]
    lines.extend("- "+item for item in report["pending"]+report["errors"])
    for w,layers in report["weather"].items():
        lines.append(f"- {w}　"+("、".join(f"{k} {n} 像素" for k,n in layers.items()) or "光照或空气效果"))
    (out/"验收报告.md").write_text("\n".join(lines)+"\n")
    return report["status"]


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("work",type=Path)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--record",action="store_true",help="保留失败证据，由生成入口按报告返回退出状态")
    args=parser.parse_args()
    status=audit(args.work.resolve(),args.out.resolve())
    print(status,flush=True)
    sys.exit(0 if args.record or status.endswith("_verified") else 1 if status=="failed" else 3)
