"""Replay reviewed group edits on EXIF-oriented original pixels."""
import json

import numpy as np
from PIL import Image

if __package__:
    from .boundaries import nearest_values
else:
    from boundaries import nearest_values


def apply_review(path, digest, region_map, groups):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("sha256") != digest:
        raise ValueError("精修蒙版与原图指纹不匹配")
    edits = config.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("精修蒙版清单需要非空 edits")
    source = np.asarray(region_map)
    updated = source.copy()
    records = []
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("group"), str) or not isinstance(edit.get("mask"), str):
            raise ValueError("每项精修蒙版需要 group 和 mask")
        key = edit["group"]
        if key not in groups:
            raise ValueError(f"本图没有组 {key!r}")
        with Image.open(path.parent / edit["mask"]) as im:
            if im.size != region_map.size or im.mode not in ("1", "L"):
                raise ValueError("精修蒙版须为与转正原图同尺寸的黑白 PNG")
            raw = np.asarray(im.convert("L"))
            if im.format != "PNG" or ((raw != 0) & (raw != 255)).any():
                raise ValueError("精修蒙版须为二值 PNG")
        members = groups[key]
        added = (raw == 255) & ~np.isin(updated, members)
        if added.any():
            seeds = np.isin(source, members)
            if not seeds.any():
                raise ValueError(f"组 {key!r} 没有可供延展的原图像素")
            updated[added] = members[0] if len(members) == 1 else nearest_values(source, seeds, added)
        records.append({"group": key, "changed_pixels": int(added.sum())})
    return Image.fromarray(updated), records
