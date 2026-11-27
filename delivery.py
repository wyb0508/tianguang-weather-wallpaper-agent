"""Orchestrate versioned skill outputs and embed exactly those assets in the desktop scene."""
import base64
import hashlib
import json
import mimetypes
import shutil
import subprocess
import sys
from pathlib import Path

from engine.workflow import WEATHER

ROOT = Path(__file__).resolve().parent
PRECIP = ("小雨", "大雨", "小雪", "中雪", "大雪", "暴雪")


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def skill(name, script, *args):
    subprocess.run([sys.executable, ROOT/"skills"/name/"scripts"/script, *map(str, args)], check=True)


def copy_pack(path, target, names):
    root = path.resolve().parent
    target.mkdir(parents=True)
    for name in set(names):
        if not name:
            continue
        file = (root/name).resolve()
        if Path(name).is_absolute() or ".." in Path(name).parts or not file.is_relative_to(root):
            raise ValueError(f"交接文件超出目录 {name}")
        dest = target/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, dest)
    shutil.copyfile(path, target/path.name)
    return target/path.name


def import_pack(path, target):
    data = read(path)
    names = [data.get(k) for k in ("region_map", "class_map", "preview", "region_preview")]
    names.extend(g.get("mask") for g in data["groups"])
    return copy_pack(path, target, names)


def build(source, manifest, folder, reviewed=None, patches=None):
    answers = folder/"天气方案.json"
    shared = [source, "--manifest", manifest, "--answers", answers]
    places = folder/"天气积留/handoff.json"
    skill("weather-surface-physics", "placement.py", *shared, "--out", places.parent,
          *(["--patches", patches] if patches else []))
    if reviewed:
        from skill_pipeline import Placement
        from PIL import Image
        import numpy as np
        external = Placement(reviewed, manifest)
        if external.data.get("answers_sha256", digest(answers)) != digest(answers):
            raise ValueError("空间修订使用了另一份确认方案")
        data = read(places)
        # Intersect imported permissions with the confirmed scene. Preserve per-grade overrides.
        for key, event in external.data["weather"].items():
            if event not in data["maps"] or key not in external.data["maps"]:
                continue
            external.mask(key, external.size)
            with Image.open(external.folder/external.data["maps"][key]) as mask, Image.open(places.parent/data["maps"][event]) as local:
                merged = np.minimum(np.asarray(local), np.asarray(mask))
            dest = "外部修订-" + str(len(data.get("reviewed_maps", []))) + ".png"
            Image.fromarray(merged).save(places.parent/dest)
            data.setdefault("reviewed_maps", []).append(dict(key=key, original=data["maps"].get(key), result=dest))
            data["maps"][key] = dest
            data["weather"][key] = event
        data["review_sha256"] = digest(reviewed)
        write(places, data)
    simulations, materials = {}, {}
    for weather in WEATHER:
        location = places
        if weather in PRECIP:
            location = folder/"天气仿真"/weather/"handoff.json"
            skill("weather-surface-physics", "simulate.py", *shared, "--weather", weather, "--out", location.parent)
            simulations[weather] = location
        pack = folder/"表面材质"/weather/"materials.json"
        skill("weather-material-renderer", "render.py", *shared, "--weather", weather, "--handoff", location,
              *(["--places", places] if location != places else []), "--out", pack.parent)
        materials[weather] = pack
    motion = folder/"动画/motion.json"
    args = []
    for path in [places, simulations["小雨"], simulations["大雨"]]:
        args.extend(["--handoff", path])
    for path in materials.values():
        args.extend(["--materials", path])
    skill("weather-particle-motion", "build.py", *shared, *args, "--out", motion.parent)
    return dict(places=places, simulations=simulations, materials=materials, motion=motion)


def uri(path):
    mime = mimetypes.guess_type(path.name)[0]
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def document(folder, name, sky):
    directory = folder/"动画"
    config = read(directory/"motion.json")
    assets = {key: uri(directory/key) for key in [config["photo"], config["caption"], *(m["path"] for m in config["materials"])] if key}
    bundle = json.loads((directory/"scene-data.js").read_text().removeprefix("window.weatherMotionScene=").rstrip(";\n"))
    def embed(value):
        return json.dumps(value, ensure_ascii=False).replace("<", r"\u003c")
    html = (ROOT/"templates/delivery.html").read_text()
    for key, value in dict(MOTION=(directory/"motion.js").read_text(), BRIDGE=(ROOT/"templates/weather.js").read_text(),
                           SCENE=embed(bundle), ASSETS=embed(assets),
                           DISPLAY=embed(dict(title=name, inspector=uri(folder/"区块预览.jpg"), sky=sky))).items():
        html = html.replace("__"+key+"__", value)
    path = folder/"壁纸预览.html"
    path.write_text(html)
    return path


def release(folder, source, manifest, packs, preview, native, reference):
    if reference:
        shutil.copyfile(reference, folder/"表面抽查.json")
    record = dict(schema="tianguang-release/v1", source_sha256=digest(source),
                  manifest=str(manifest.relative_to(folder)), answers="天气方案.json",
                  preview=preview.name, app="动态天气壁纸.app" if native else None,
                  geometry="表面抽查.json" if reference else None)
    for key, item in packs.items():
        record[key] = ({w:str(p.relative_to(folder)) for w,p in item.items()} if isinstance(item,dict)
                       else str(item.relative_to(folder)))
    record["files"] = {str(p.relative_to(folder)):digest(p) for p in sorted(folder.rglob("*"))
                       if p.is_file() and p.name != "区块清单.json"}
    write(folder/"release.json", record)
