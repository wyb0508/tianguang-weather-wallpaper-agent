"""Generate a self-contained weather wallpaper from one photo."""
import argparse
import base64
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

import numpy as np

import delivery
from skill_pipeline import run_regions
from engine.region_import import import_regions
from engine.regions import write_regions
from engine.subject_picker import pick_group, show_subject
from engine.workflow import collect, group_numbers
from engine.analysis import LEGEND, ROOT, analyze, grayscale, load_photo


def layer(alpha, color):
    image = Image.new("RGBA", (alpha.shape[1], alpha.shape[0]), color)
    image.putalpha(grayscale(alpha))
    return image


def data_url(image, fmt):
    out = io.BytesIO()
    image.save(out, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return "data:image/" + fmt.lower() + ";base64," + base64.b64encode(out.getvalue()).decode("ascii")


def make_app(folder, preview, identifier, title):
    app = folder / "动态天气壁纸.app"
    subprocess.run(["osacompile", "-l", "JavaScript", "-s", "-o", str(app),
                    str(ROOT / "templates/host.js")], check=True)
    contents = app / "Contents"
    plist = contents / "Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info.update(CFBundleIdentifier="local.weatherwall.scene.p"+identifier,
                CFBundleDisplayName=title, CFBundleName=title, LSUIElement=True,
                OSAAppletShowStartupScreen=False,
                NSLocationUsageDescription="根据所在位置更新壁纸的天气和日照。",
                NSLocationWhenInUseUsageDescription="根据所在位置更新壁纸的天气和日照。")
    plist.write_bytes(plistlib.dumps(info))
    shutil.copyfile(preview, contents / "Resources/scene.html")
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)


def render_wallpaper(photo, folder, name, native, scene, plan, groups, packs):
    masks, _, _, inspector, report = analyze(photo, scene, plan, folder, groups)
    material = packs["materials"]["大雪"]
    layer_path = delivery.read(material)["layers"][0]["path"]
    snow_image = Image.open(material.parent/layer_path).resize(inspector.size, Image.Resampling.LANCZOS)
    rain_pack = packs["materials"]["大雨"]
    rain_path = delivery.read(rain_pack)["layers"][0]["path"]
    rain_alpha = Image.open(rain_pack.parent/rain_path).getchannel("A").resize(inspector.size)
    _, labels, names = scene
    ids = {v.strip():int(k) for k,v in names.items()}
    merged = labels.copy()
    for src, dst in plan["merge_into"].items():
        merged[labels == ids[src]] = ids[dst]
    for key, row in report["surface_effects"].items():
        area = merged == ids[key]
        row["snow_mean"] = round(float(np.asarray(snow_image.getchannel("A"))[area].mean()/255), 4)
        row["rain_mean"] = round(float(np.asarray(rain_alpha)[area].mean()/255), 4)
    report.pop("water_points", None)
    report["skills"] = ["scene-regions", "weather-surface-physics", "weather-material-renderer", "weather-particle-motion", "wallpaper-qa"]
    delivery.write(folder/"识别结果.json", report)
    preview = delivery.document(folder, name, data_url(layer(masks["sky"], "white"), "PNG"))
    thumb = photo.resize(inspector.size, Image.Resampling.LANCZOS)
    snowy = Image.composite(snow_image.convert('RGB'), thumb, snow_image.getchannel('A'))
    sheet = Image.new("RGB", (thumb.width*3,thumb.height))
    for i, tile in enumerate([thumb,inspector,snowy]):
        sheet.paste(tile, (i*thumb.width,0))
    sheet.save(folder / "原图·识别·积雪.jpg", quality=90)
    if native:
        make_app(folder, preview, plan["image_sha256"][:16], name)
    return preview


def generate(source, out, title, native, regions_only=False, subject=None, motion=None, answers=None, regions_manifest=None, physics_handoff=None, surface_review=None, geometry_reference=None):
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[/\\:\x00-\x1f]', '_', title or source.stem).strip(' .')[:64] or '天气壁纸'
    with tempfile.TemporaryDirectory(prefix=".生成中-", dir=out) as tmp:
        scratch = Path(tmp)
        folder = scratch / "export"
        folder.mkdir()
        photo = load_photo(source)
        manifest = (delivery.import_pack(regions_manifest, folder/"场景分区") if regions_manifest
                    else run_regions(source, folder/"场景分区"))
        scene, groups, signature = import_regions(manifest, source, photo)
        regions = write_regions(photo, folder, scene, groups, signature)
        pack = delivery.read(manifest)
        numbers = group_numbers(pack) if all("id" in g for g in pack["groups"]) else {}
        region_summary = delivery.read(folder/"区块清单.json")
        region_summary["preview_numbers"] = numbers
        delivery.write(folder/"区块清单.json", region_summary)
        preview = manifest.parent / pack.get("preview","preview.jpg")
        if preview.is_file():
            shutil.copyfile(preview, folder/"区块预览.jpg")
        if not regions_only:
            interactive = sys.stdin.isatty()
            saved_plan = delivery.read(answers) if answers else {}
            pending = not saved_plan.get("subject") or "weather" not in saved_plan.get("confirmed", ["weather"])
            if interactive and pending:
                print("区块预览：", folder / "区块预览.jpg", flush=True)
                for row in regions:
                    print(f"  {row['name']} ({row['label']})  {row['fraction']:.1%}")
                if sys.platform == "darwin":
                    subprocess.run(["open", str(folder / "区块预览.jpg")], check=False)
            checkpoint_path = None
            def checkpoint(value):
                nonlocal checkpoint_path
                if checkpoint_path is None:
                    with tempfile.NamedTemporaryFile(dir=out, prefix=name+"·问答-", suffix=".json",delete=False) as f:
                        checkpoint_path = Path(f.name)
                    print("中断后可用 --answers 继续",checkpoint_path,flush=True)
                temp_path = checkpoint_path.with_suffix(".tmp")
                delivery.write(temp_path,value)
                temp_path.replace(checkpoint_path)
            plan = collect(photo, regions, answers, subject, motion, groups, signature, numbers=numbers,
                           pick=(lambda: pick_group(scene,groups,numbers)) if numbers else None,
                           highlight=lambda key,merges: show_subject(folder,scene,groups,key,merges),
                           checkpoint=checkpoint if interactive else None)
            (folder / "天气方案.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
            packs = delivery.build(source, manifest, folder, physics_handoff, surface_review)
            preview = render_wallpaper(photo, folder, name, native, scene, plan, groups, packs)
            reference = geometry_reference or ROOT/"skills/wallpaper-qa/references"/(delivery.digest(source)+".json")
            if geometry_reference or reference.is_file():
                reference = reference.resolve(strict=True)
            else:
                reference = None
            delivery.release(folder, source, manifest, packs, preview, native, reference)
            delivery.skill("wallpaper-qa", "release_gate.py", folder, "--out", folder/"验收", "--record")
            status = delivery.read(folder/"验收/qa-report.json")["status"]
            manifest = folder / "区块清单.json"
            summary = json.loads(manifest.read_text())
            summary.update(stage=status, subject=plan["subject"], motion=plan["motion"], merge_into=plan["merge_into"])
            manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        # Publish only completed builds. Retain previous runs of the same image.
        suffix = 1
        while True:
            dest = out / (name if suffix == 1 else f"{name} ({suffix})")
            if not dest.exists() and not dest.is_symlink():
                try:
                    os.rename(folder, dest)
                    if not regions_only and checkpoint_path:
                        checkpoint_path.unlink(missing_ok=True)
                    return dest
                except OSError:
                    if not dest.exists():
                        raise
            suffix += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT/"作品")
    parser.add_argument("--name")
    parser.add_argument("--surface-review", type=Path, help="绑定分区的部位修订多边形")
    parser.add_argument("--geometry-reference", type=Path, help="独立的原图坐标验收样本")
    parser.add_argument("--physics-handoff", type=Path, help="复用同一份分区的 handoff.json 空间修订")
    parser.add_argument("--regions-manifest", type=Path, help="复用 scene-regions 技能输出的 manifest.json")
    parser.add_argument("--answers", type=Path, help="读取这张图片已确认的天气方案 JSON")
    parser.add_argument("--subject", help="用户确认的类别标签，或 salient（显著主体）")
    parser.add_argument("--motion", choices=["stationary","moving","high_speed"], help="用户对主体动静问题的回答")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--regions-only", action="store_true", help="仅识别并导出区块，等待确认主体")
    mode.add_argument("--preview-only", action="store_true", help="仅生成 HTML 和识别结果")
    parser.add_argument("--open", action="store_true", help="完成后打开浏览器预览")
    args = parser.parse_args()
    try:
        result = generate(args.image.expanduser().resolve(strict=True), args.out.expanduser().resolve(),
                          args.name, sys.platform == "darwin" and not args.preview_only, args.regions_only, args.subject, args.motion, args.answers, args.regions_manifest, args.physics_handoff, args.surface_review, args.geometry_reference)
        print(result, flush=True)
        if not args.regions_only:
            status = delivery.read(result/"验收/qa-report.json")["status"]
            print("验收状态：" + status)
            if status == "failed":
                sys.exit("验收失败，请查看作品中的验收报告")
        if args.open:
            subprocess.run(["open",str(result / ("区块预览.jpg" if args.regions_only else "壁纸预览.html"))],check=True)
    except (OSError, EOFError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"生成失败：{error}")
