"""Region inspection, before the user chooses the main subject."""
import json

import numpy as np
from PIL import Image

from .workflow import draft

# Raw class IDs are retained in the PNG, including minor and uncertain classes.
DISPLAY = {
    "car": ("汽车", "#ff784d"), "tree": ("树木", "#246e4d"),
    "sky": ("天空", "#67bafa"), "grass": ("草坪", "#b4e545"),
    "road": ("道路", "#c595df"), "floor": ("地板／待核对路面", "#8a7db9"),
    "wall": ("墙面", "#bd9678"), "fence": ("围栏", "#efcb68"),
}


def write_regions(photo, folder, scene, groups=None, signature=None):
    small, labels, names = scene
    Image.fromarray(labels).save(folder / "区块类别.png")
    overlay = np.empty((*labels.shape, 3), dtype=np.uint8)
    regions = []
    for idx, count in zip(*np.unique(labels, return_counts=True)):
        key = names[str(idx)].strip()
        title, color = DISPLAY.get(key, (key, f"#{(int(idx)+1)*2654435761 % (1<<24):06x}"))
        overlay[labels == idx] = tuple(bytes.fromhex(color[1:]))
        regions.append(dict(id=int(idx), label=key, name=title, color=color,
                            pixels=int(count), fraction=round(float(count / labels.size), 5)))
    blend = Image.blend(small, Image.fromarray(overlay), .57)
    blend.save(folder / "区块预览.jpg", quality=93)
    report = {"stage":"awaiting_subject", "subject":None, "mask_size":list(small.size),
              "regions":sorted(regions, key=lambda row:-row["pixels"])}
    if groups:
        report["effect_groups"] = ["group:" + key for key in groups]
    (folder / "区块清单.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (folder / "天气方案.json").write_text(json.dumps(draft(photo, signature), ensure_ascii=False, indent=2))
    return report["regions"]
