"""Run the two project-local skills and read their versioned handoffs."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from engine.vision import ROOT


def run_regions(source, folder):
    subprocess.run([sys.executable, str(ROOT/"skills/scene-regions/scripts/segment.py"),
                    str(source), "--out", str(folder)], check=True)
    return folder/"manifest.json"


def run_placement(source, manifest, folder):
    subprocess.run([sys.executable, str(ROOT/"skills/weather-surface-physics/scripts/placement.py"),
                    str(source), "--manifest", str(manifest), "--out", str(folder)], check=True)
    return folder/"handoff.json"


class Placement:
    def __init__(self, path, manifest):
        self.folder = path.resolve().parent
        self.data = json.loads(path.read_text())
        raw = manifest.read_bytes()
        source = json.loads(raw)
        if (self.data.get("schema") != "weather-accumulation-places/v1"
                or self.data.get("manifest_sha256") != hashlib.sha256(raw).hexdigest()
                or self.data.get("image_sha256") != source["sha256"]):
            raise ValueError("物理交接与本次分区或照片不匹配。")
        self.size = tuple(source["size"])
        if not self.data.get("maps") or self.data.get("encoding") != {"0":"不会","127":"保守略过","255":"会"}:
            raise ValueError("物理交接需要带三值空间蒙版，请使用 placement 输出。")

    def mask(self, key, size):
        path = (self.folder / self.data["maps"][key]).resolve()
        if not path.is_relative_to(self.folder):
            raise ValueError("位置蒙版超出交接目录。")
        with Image.open(path) as image:
            if image.size != self.size or image.mode != "L":
                raise ValueError("物理蒙版尺寸或格式与原图不匹配。")
            if any(n not in (0,127,255) for _,n in image.getcolors(256)):
                raise ValueError("物理蒙版包含未定义的状态。")
            permitted = image.point(lambda v: 255 if v == 255 else 0)
        return permitted.resize(size, Image.Resampling.NEAREST)


def snow_layer(alpha, permit):
    # Resample the continuous texture first. Clip on the final grid so a
    # neighbouring "yes" never interpolates material into an uncertain pixel.
    image = Image.fromarray(np.uint8(np.clip(alpha,0,1)*255)).resize(permit.size, Image.Resampling.BILINEAR)
    texture = Image.new("RGBA", permit.size, "#f3f8ff")
    texture.putalpha(Image.composite(image, Image.new("L",permit.size), permit))
    return texture
