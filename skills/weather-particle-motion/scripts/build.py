"""Build a file://-portable parcel without a web server or runtime model downloads."""
import argparse
import base64
from io import BytesIO
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageOps

from inputs import Scene, asset, digest, raster, read
from engine.workflow import GAIN

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def png_url(pixels):
    stream = BytesIO()
    Image.fromarray(pixels).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def optional(paths, default):
    return paths or ([default] if default.is_file() else [])


def materials(scene, paths, out):
    layers = []
    sources = []
    seen = set()
    for path in paths:
        pack = read(path)
        scene.bind(pack)
        if pack.get("answers_sha256") != scene.hashes["answers_sha256"]:
            raise ValueError("材质使用了不同的天气方案，请重新生成材质")
        if pack.get("weather") in seen:
            raise ValueError("同一档天气有多份材质交接")
        seen.add(pack["weather"])
        for key, expected in (("subject", scene.plan["subject"]), ("motion", scene.plan["motion"]),
                              ("region_pack_sha256", scene.signature)):
            if key in pack and (key != "region_pack_sha256" or expected is not None) and pack[key] != expected:
                raise ValueError(f"材质的 {key} 与确认方案不一致")
        sources.append(digest(path))
        for layer in sorted(pack["layers"], key=lambda x: x.get("order", 0)):
            mode = layer.get("blend_mode", layer.get("blend", pack.get("blend_mode", pack.get("blend", "normal"))))
            if mode not in ("normal", "source-over", "multiply", "screen", "overlay", "soft-light"):
                raise ValueError(f"不支持的材质混合方式 {mode}")
            file = asset(path.parent, layer["path"])
            with Image.open(file) as im:
                if im.mode != "RGBA" or im.size != scene.size or im.getexif().get(274, 1) != 1:
                    raise ValueError("材质层须为与原图对齐的 RGBA 图像")
            name = f"material-{len(layers)}.png"
            shutil.copyfile(file, out / name)
            layers.append(dict(path=name, weather=layer.get("weather", pack.get("weather")),
                               blend="normal" if mode == "source-over" else mode, sha256=digest(file)))
    return layers, sources


def build(args, out):
    scene = Scene(args.photo, args.manifest, args.answers)
    session = args.answers.parent
    for path in optional(args.handoff, session / "天气积留/handoff.json"):
        scene.physics(path)
    depth_path = args.depth
    depth_source = "explicit:relative-inverse-depth"
    if not depth_path and not scene.depth_source and args.manifest.resolve() == (session/"场景分区/manifest.json").resolve():
        candidate = session/"相对深度.png"
        if candidate.is_file():
            depth_path, depth_source = candidate, "agent:相对深度.png"
    if depth_path:
        if depth_path.suffix.lower() == ".json":
            descriptor = read(depth_path)
            if "orientation" not in descriptor or "size" not in descriptor:
                raise ValueError("深度图描述文件缺少原图方向或尺寸")
            scene.bind(descriptor)
            scene.hashes["depth_descriptor_sha256"] = digest(depth_path)
            depth_path = asset(depth_path.parent, descriptor["path"])
            if digest(depth_path) != descriptor["sha256"] or descriptor["encoding"] != "relative-inverse-depth-u8":
                raise ValueError("深度图指纹或远近编码与描述文件不匹配")
            depth_source = "bound:relative-inverse-depth"
        with Image.open(depth_path) as im:
            size = im.size
        if size not in (scene.size, scene.analysis):
            raise ValueError("相对深度须为原尺寸或分析尺寸")
        scene.set_depth(raster(depth_path, size, "L"), depth_source)
        scene.hashes["depth_sha256"] = digest(depth_path)
    profiles = read(ASSETS / "profiles.json")
    for key, profile in profiles.items():
        profile["gain"] = GAIN[scene.plan["weather"][key]]
    layers, material_hashes = materials(scene, optional(args.materials, session / "materials.json"), out)
    # Strip orientation metadata. The mask/UV grid always uses the oriented original.
    with Image.open(args.photo) as source:
        source_orientation = source.getexif().get(274, 1)
        upright = ImageOps.exif_transpose(source).convert("RGBA")
        photo = Image.new("RGB", scene.size, "#202a34")
        photo.paste(upright, mask=upright.getchannel("A"))
        if source.format == "JPEG" and source.getexif().get(274, 1) == 1:
            shutil.copyfile(args.photo, out / "photo.jpg")
        else:
            photo.save(out / "photo.jpg", quality=95)
    caption = None
    if scene.caption.any():
        caption = "caption.png"
        text = Image.new("RGBA", scene.size)
        text.paste(photo, mask=Image.fromarray(scene.caption))
        text.save(out / caption)
    # Encode as opaque PNG RGB; no alpha premultiplication or file-origin canvas reads.
    flags = (scene.caption.astype(np.uint8) + 2*scene.subject.astype(np.uint8) +
             4*scene.water.astype(np.uint8) + 8*scene.shelter.astype(np.uint8) +
             16*scene.air.astype(np.uint8))
    atlas = np.stack((scene.depth, flags, scene.puddle.astype(np.uint8)), axis=-1)
    inputs = dict(**scene.hashes, region_pack_sha256=scene.signature, physics=scene.sources,
                  materials_sha256=material_hashes, group_masks_sha256=scene.group_mask_hashes,
                  plan_region_pack_sha256=scene.reported_signature,
                  region_verification="classes-and-groups" if scene.signature else "image-bound-group-masks-only")
    config = dict(schema="weather-particle-motion/v1", inputs=inputs, **scene.hashes,
                  size=scene.size, orientation="exif-transposed", source_exif_orientation=source_orientation,
                  subject=scene.plan["subject"],
                  motion=scene.plan["motion"], merge_into=scene.plan["merge_into"],
                  coordinates=dict(origin="top-left", u="x/imageWidth", v="y/imageHeight",
                                   fit="center-cover", scale="max(viewWidth/imageWidth,viewHeight/imageHeight)*zoom",
                                   offset="(viewport-scaledImage)/2", zoom=1),
                  occlusion=dict(method="relative-inverse-depth" if scene.depth_source else "group-two-layer",
                                 source=scene.depth_source or "confirmed-subject-mask",
                                 precision="uint8/255" if scene.depth_source else "background=0, subject=128/255; particles=0.25 or 0.85",
                                 direction="far-black-near-white"),
                  material_loading="on-demand", seed=args.seed, time_unit="seconds", fps=args.fps, profiles=profiles,
                  wind_unit="image heights/second; signed horizontal component (+right)",
                  layers=["photo", "materials (listed order)", "surface impacts", "depth-tested air", "caption", "controls"],
                  photo="photo.jpg", caption=caption, materials=layers, drains=scene.pairs,
                  entry=dict(preview="index.html", module="motion.js", data="scene-data.js",
                             factory="WeatherParticleMotion.create", sampling="sample(seconds)"),
                  default_weather=args.weather)
    (out/"motion.json").write_text(json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False))
    atlas_urls = dict(scene=png_url(atlas), rain={k: png_url(v) for k,v in scene.responses.items()})
    bundle = dict(config=config, maps=atlas_urls)
    (out/"scene-data.js").write_text("window.weatherMotionScene=" + json.dumps(bundle, ensure_ascii=False) + ";\n")
    for name in ("motion.js", "index.html"):
        shutil.copyfile(ASSETS / name, out / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, action="append")
    parser.add_argument("--materials", type=Path, action="append")
    parser.add_argument("--depth", type=Path, help="与本图绑定的远黑近白灰度图")
    parser.add_argument("--seed", type=int, default=731)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--weather", choices=read(ASSETS/"profiles.json"), default="大雪")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.fps <= 120 or not 0 <= args.seed <= 0xffffffff:
        parser.error("fps 需为 1–120，seed 需为无符号 32 位整数")
    if args.out.exists():
        parser.error("请指定新的输出目录")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(dir=args.out.parent) as temp:
            stage = Path(temp)/"scene"
            stage.mkdir()
            build(args, stage)
            stage.rename(args.out)
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(2, f"生成失败：{error}\n")
    print(args.out / "index.html")


if __name__ == "__main__":
    main()
