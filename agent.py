"""Generate a self-contained weather wallpaper from one photo."""
import argparse
import base64
import hashlib
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


def generate(source, out, title, native):
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[/\\:\x00-\x1f]', '_', title or source.stem).strip(' .')[:64] or '天气壁纸'
    with tempfile.TemporaryDirectory(prefix=".生成中-", dir=out) as tmp:
        scratch = Path(tmp)
        folder = scratch / "export"
        folder.mkdir()
        photo = load_photo(source)
        identifier = hashlib.sha256(photo.tobytes()).hexdigest()[:16]
        masks, snow, rain, inspector, report = analyze(photo)
        snow_image = layer(snow, "#f3f8ff")
        payload = dict(title=name, photo=data_url(photo, "JPEG"),
                       inspector=data_url(inspector, "JPEG"), snow=data_url(snow_image, "PNG"),
                       rain=data_url(layer(rain, "#496579"), "PNG"), legend=LEGEND, **report)
        for key, field in [('subject','foreground'),('sky','sky'),('water','water')]:
            payload[field] = data_url(layer(masks[key], "white"), "PNG")
        html = (ROOT / "templates/scene.html").read_text()
        js = (ROOT / "templates/scene.js").read_text()
        html = html.replace("__RENDERER__", js).replace("__PAYLOAD__", json.dumps(payload,ensure_ascii=False).replace("<","\\u003c"))
        preview = folder / "壁纸预览.html"
        preview.write_text(html, encoding="utf-8")
        report_file = {k:v for k,v in report.items() if k != "water_points"}
        (folder / "识别结果.json").write_text(json.dumps(report_file, ensure_ascii=False, indent=2))
        thumb = photo.resize(inspector.size, Image.Resampling.LANCZOS)
        snowy = Image.composite(snow_image.convert('RGB'), thumb, snow_image.getchannel('A'))
        sheet = Image.new("RGB", (thumb.width*3,thumb.height))
        for i, tile in enumerate([thumb,inspector,snowy]):
            sheet.paste(tile, (i*thumb.width,0))
        sheet.save(folder / "原图·识别·积雪.jpg", quality=90)
        if native:
            make_app(folder, preview, identifier, name)
        # Publish only completed builds. Retain previous runs of the same image.
        suffix = 1
        while True:
            dest = out / (name if suffix == 1 else f"{name} ({suffix})")
            if not dest.exists() and not dest.is_symlink():
                try:
                    os.rename(folder, dest)
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
    parser.add_argument("--preview-only", action="store_true", help="仅生成 HTML 和识别结果")
    parser.add_argument("--open", action="store_true", help="完成后打开浏览器预览")
    args = parser.parse_args()
    try:
        result = generate(args.image.expanduser().resolve(strict=True), args.out.expanduser().resolve(),
                          args.name, sys.platform == "darwin" and not args.preview_only)
        print(result, flush=True)
        if args.open:
            subprocess.run(["open",str(result/"壁纸预览.html")],check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"生成失败：{error}")
