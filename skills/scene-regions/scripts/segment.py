"""Export semantic regions, a proposed subject and compositing masks for one photo."""
import argparse
import colorsys
import hashlib
import json
import re
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
if __package__:
    from .vision import infer, load_photo, oriented_size, predict_scene, tensor
    from .boundaries import refine_groups, refine_scene_edges
    from .review_masks import apply_review
else:
    from vision import infer, load_photo, oriented_size, predict_scene, tensor
    from boundaries import refine_groups, refine_scene_edges
    from review_masks import apply_review

NATURAL_LABELS = {"sky", "water", "sea", "lake", "river", "waterfall", "tree", "plant", "grass",
                  "field", "palm", "flower", "earth", "sand", "land", "rock", "mountain", "hill"}
LAND_LABELS = NATURAL_LABELS - {"sky", "water", "sea", "lake", "river", "waterfall"}
GROUND_LABELS = {"road", "sidewalk", "path", "dirt track", "runway", "floor", "earth", "land",
                 "grass", "field", "sand", "step", "stairs", "stairway"}
GROUP_NAMES = {"subject": "主体", "sky": "天空", "track": "赛道", "ground": "地面",
               "caption": "拍摄信息栏", "background": "背景", "water": "水面", "land": "山林与地貌"}
GROUP_COLORS = {"subject": "#ff774c", "sky": "#4ba9ee", "track": "#bc80eb",
                "caption": "#929aa4", "ground": "#d6ab56", "background": "#44906c", "water": "#34bac7", "land": "#ac9763"}


def islands(mask):
    """Label 8-connected row runs without adding a scientific-image dependency."""
    parent = []

    def root(index):
        while index != parent[index]:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    rows = []
    previous = []
    for row in mask:
        bounds = np.flatnonzero(np.diff(row, prepend=False, append=False))
        current = []
        cursor = 0
        for left, right in bounds.reshape(-1, 2).tolist():
            while cursor < len(previous) and previous[cursor][1] < left:
                cursor += 1
            index = len(parent)
            parent.append(index)
            for i in range(cursor, len(previous)):
                start, _, other = previous[i]
                if start > right:
                    break
                parent[root(other)] = index
            current.append((left, right, index))
        rows.append(current)
        previous = current
    result = np.zeros(mask.shape, np.int32)
    for y, runs in enumerate(rows):
        for left, right, index in runs:
            result[y, left:right] = root(index) + 1
    return result


def make_regions(labels, names, split_classes):
    ids = np.zeros(labels.shape, np.uint16)
    regions = []

    def add(key, mask):
        rid = len(regions) + 1
        ids[mask] = rid
        regions.append({"id": rid, "key": key, "label": label, "class_id": int(cid)})

    present = np.unique(labels)
    missing = set(split_classes) - {names[str(cid)].strip() for cid in present}
    if missing:
        raise ValueError(f"本图没有要拆分的类别：{sorted(missing)}")
    for cid in present:
        label = names[str(cid)].strip()
        key = label.replace(" ", "_")
        mask = labels == cid
        if label in split_classes:
            parts = islands(mask)
            sizes = np.bincount(parts.ravel())
            substantial = [i for i in range(1, len(sizes)) if sizes[i] >= max(12, ids.size * .00012)]
            if len(substantial) > 1:
                for n, i in enumerate(sorted(substantial, key=lambda i: -sizes[i]), 1):
                    add(f"{key}.{n}", parts == i)
                mask = mask & (ids == 0)
                key += ".fragments"
        if mask.any():
            add(key, mask)
    return ids, regions


def select_subject(photo, ids, regions, choice):
    if choice == "none":
        return None, "none", []
    if choice not in ("auto", "salient"):
        selected = set()
        for value in choice.split(","):
            matches = [r["id"] for r in regions if value.strip() in (r["key"], r["label"], str(r["id"]))]
            if not matches:
                raise ValueError(f"主体 {value!r} 不在本图的区块中，请使用本图的 key 或 id。")
            selected.update(matches)
        return selected, "specified", []

    if choice == "auto":
        natural_ids = [r["id"] for r in regions if r["label"] in NATURAL_LABELS]
        if np.isin(ids, natural_ids).mean() >= .985:
            return None, "landscape", []
    raw = infer("foreground.onnx", tensor(photo, 320, relative=True))[0]
    saliency = np.asarray(Image.fromarray(raw).resize((ids.shape[1], ids.shape[0]),
                                                      Image.Resampling.BILINEAR)) >= .5
    if not .001 <= saliency.mean() <= .95:
        return None, "no_clear_subject", []
    overlap = np.bincount(ids[saliency], minlength=len(regions) + 1)
    sizes = np.bincount(ids.ravel())
    union = sizes + saliency.sum() - overlap
    order = sorted(regions, key=lambda r: -overlap[r["id"]] / union[r["id"]])
    candidates = [{"id": r["id"], "iou": round(float(overlap[r["id"]] / union[r["id"]]), 4)}
                  for r in order[:3] if overlap[r["id"]]]
    # Saliency may highlight one member of a repeated object class.
    main = int(overlap.argmax())
    dominant = next(r for r in regions if r["id"] == main)
    if (choice == "auto" and dominant["label"] not in NATURAL_LABELS
            and overlap[main] >= .85 * saliency.sum() and saliency.sum() >= .01 * ids.size):
        return {main}, "semantic_saliency_agreement", candidates
    if choice == "auto" and candidates[0]["iou"] >= .5:
        return {candidates[0]["id"]}, "semantic_saliency_agreement", candidates
    return saliency, "salient" if choice == "salient" else "review_salient", candidates


def partition_regions(ids, regions, mask, suffix):
    """Keep the original class ID on both sides of a spatial group boundary."""
    members = set()
    for r in list(regions):
        region = ids == r["id"]
        part = region & mask
        if not part.any():
            continue
        if part.sum() == region.sum():
            members.add(r["id"])
        else:
            child = dict(r, id=len(regions) + 1, key=r["key"] + "." + suffix)
            ids[part] = child["id"]
            regions.append(child)
            members.add(child["id"])
    return members


def group_regions(regions, specs, has_subject):
    assignment = {}
    for spec in specs:
        key, sep, values = spec.partition("=")
        if not sep or not re.fullmatch(r"[a-z][a-z0-9_-]*", key) or key == "subject":
            raise ValueError(f"无效分组 {spec!r}，示例：--group track=road,floor")
        for value in values.split(","):
            matches = [r["id"] for r in regions if value.strip() in (r["key"], r["label"])]
            if not matches:
                raise ValueError(f"本图没有类别或区块 {value!r}")
            for rid in matches:
                if rid in assignment:
                    raise ValueError(f"区块 {value!r} 已分配给主体或其他组")
                assignment[rid] = key
    groups = {}
    for region in regions:
        rid, label = region["id"], region["label"]
        key = assignment.get(rid)
        if key is None:
            if label == "sky":
                key = "sky"
            elif label in ("water", "sea", "lake", "river", "waterfall", "swimming pool"):
                key = "water"
            elif label in GROUND_LABELS:
                key = "ground"
            elif not has_subject and label in LAND_LABELS:
                key = "land"
            else:
                key = "background"
        groups.setdefault(key, []).append(rid)
    return groups


def color(index):
    rgb = colorsys.hsv_to_rgb((.09 + index * .61803398875) % 1, .67, .94)
    return "#" + "".join(f"{round(v * 255):02x}" for v in rgb)


def preview(photo, ids, rows, target):
    rgb = np.zeros((*ids.shape, 3), np.uint8)
    for r in rows:
        rgb[ids == r["id"]] = tuple(bytes.fromhex(r["color"][1:]))
    overlay = Image.blend(photo, Image.fromarray(rgb), .60)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 16)
        local_names = True
    except OSError:
        font = ImageFont.load_default()
        local_names = False
    line = 23
    columns = max(1, overlay.width // 210)
    legend = line * ((len(rows) + columns - 1) // columns)
    sheet = Image.new("RGB", (max(overlay.width, 240), overlay.height + legend + 12), "#20262c")
    sheet.paste(overlay)
    draw = ImageDraw.Draw(sheet)
    for i, r in enumerate(rows):
        x = 8 + (i % columns) * (sheet.width // columns)
        y = overlay.height + 6 + i // columns * line
        draw.rectangle((x, y + 2, x + 14, y + 16), fill=r["color"])
        title = r.get("name", r["key"]) if local_names else r["key"]
        draw.text((x + 21, y), f"{r['id']}  {title}", fill="white", font=font)
        yy, xx = np.nonzero(ids[::4, ::4] == r["id"])
        if xx.size >= max(10, ids.size * .005 / 16):
            near = np.argmin((xx - np.median(xx)) ** 2 + (yy - np.median(yy)) ** 2)
            cx, cy = int(xx[near]) * 4, int(yy[near]) * 4
            draw.ellipse((cx-11, cy-11, cx+11, cy+11), fill="#20262c", outline="white")
            draw.text((cx, cy), str(r["id"]), font=font, anchor="mm", fill="white")
    sheet.save(target, quality=92)


def correct_labels(labels, names, correction_file, digest):
    """Replay local reviewed edits on the display grid after EXIF orientation."""
    config = json.loads(correction_file.read_text(encoding="utf-8"))
    h, w = labels.shape
    if not isinstance(config, dict) or config.get("sha256") != digest or config.get("analysis_size") != [w, h]:
        raise ValueError("修正文件必须匹配本图的 sha256 和 analysis_size")
    edits = config.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("修正文件需要非空 edits 列表")
    ids = {name.strip(): int(cid) for cid, name in names.items()}
    corrected = labels.copy()
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("每项修正都需要 from、to 和 polygon")
        source, target, polygon = edit.get("from"), edit.get("to"), edit.get("polygon")
        if (not isinstance(source, list) or not source or not isinstance(target, str) or target not in ids
                or not all(isinstance(key, str) and key in ids for key in source)):
            raise ValueError("修正需要有效的 ADE20K 来源类别列表和目标类别")
        if (not isinstance(polygon, list) or len(polygon) < 3 or not all(
                isinstance(p, list) and len(p) == 2 and all(type(v) is int for v in p)
                and 0 <= p[0] < w and 0 <= p[1] < h for p in polygon)):
            raise ValueError("polygon 需要至少三个图内整数像素坐标")
        area = Image.new("1", (w, h))
        ImageDraw.Draw(area).polygon([tuple(p) for p in polygon], fill=1)
        selected = np.asarray(area) & np.isin(corrected, [ids[key] for key in source])
        corrected[selected] = ids[target]
    return corrected, dict(edits=edits, changed_pixels=int(np.count_nonzero(labels != corrected)))


def write_review(folder, small, views, regions, layers):
    global_ids, detail_ids = views
    # A disagreement between floor and road can disappear once both mean track.
    # A class split across multiple groups has no unambiguous class-level mapping.
    class_group = np.zeros(256, np.int16)
    by_region = {r["id"]: r["class_id"] for r in regions}
    for layer in layers:
        for rid in layer["region_ids"]:
            cid = by_region[rid]
            if class_group[cid] in (0, layer["id"]):
                class_group[cid] = layer["id"]
            else:
                class_group[cid] = -1
    a, b = class_group[global_ids], class_group[detail_ids]
    disputed = (global_ids != detail_ids) & ((a != b) | (a <= 0))
    pixels = int(disputed.sum())
    report = {"analysis_pixels": pixels, "fraction": round(pixels / disputed.size, 5)}
    if pixels:
        overlay = np.array(small)
        overlay[disputed] = .35 * overlay[disputed] + .65 * np.array([255, 45, 145])
        Image.fromarray(overlay).save(folder / "review.jpg", quality=94)
        report["preview"] = "review.jpg"
    return report


def export(source, out, choice, specs, split_classes, quality="precise", corrections=None, footer_start=None, group_edits=None):
    if out.exists():
        raise ValueError(f"输出目录已存在，请选择新的目录：{out}")
    photo = load_photo(source)
    size = oriented_size(source)
    detail_model = Path(__file__).resolve().parents[1] / "models/scene-detail.onnx"
    scene, views = predict_scene(photo, detail_model if quality == "precise" else None)
    small, labels, names = scene
    with source.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    digest = digest.hexdigest()
    edited = None
    if corrections is not None:
        labels, edited = correct_labels(labels, names, corrections, digest)
    ids, regions = make_regions(labels, names, split_classes)
    subject, method, candidates = select_subject(photo, ids, regions, choice)
    if isinstance(subject, np.ndarray):
        subject_ids = partition_regions(ids, regions, subject, "subject")
    else:
        subject_ids = subject or set()
    caption_ids = set()
    if footer_start is not None:
        if not 0 < footer_start < small.height:
            raise ValueError("信息栏起始行必须在分析图范围内")
        caption = np.arange(small.height)[:, None] >= footer_start
        caption_ids = partition_regions(ids, regions, caption, "caption")
        subject_ids -= caption_ids
    reserved = subject_ids | caption_ids
    remaining = [r for r in regions if r["id"] not in reserved]
    groups = group_regions(remaining, specs, bool(subject_ids))
    if caption_ids:
        groups.setdefault("caption", []).extend(sorted(caption_ids))
    if subject_ids:
        groups = {"subject": sorted(subject_ids), **groups}

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        folder = Path(tmp) / "export"
        (folder / "masks").mkdir(parents=True)
        # Resize the ID map once. PIL's 8-bit and 16-bit nearest paths can
        # round ties differently, so derive every full-sized mask from this grid.
        original_grid = ids
        tools_used = []
        reviewed = []
        if quality == "precise":
            edits = edited["edits"] if edited else None
            refined = refine_groups(photo, ids, groups, edits)
            refined, tools_used = refine_scene_edges(photo, refined, groups, regions, edits, small.size)
            full_ids = Image.fromarray(refined).resize(size, Image.Resampling.NEAREST)
            ids = np.array(full_ids.resize(small.size, Image.Resampling.NEAREST))
        else:
            full_ids = Image.fromarray(ids).resize(size, Image.Resampling.NEAREST)
        if group_edits is not None:
            full_ids, reviewed = apply_review(group_edits, digest, full_ids, groups)
            ids = np.array(full_ids.resize(small.size, Image.Resampling.NEAREST))
        full_ids.save(folder / "regions.png")
        full_array = np.asarray(full_ids)
        classes = np.zeros(len(regions) + 1, np.uint8)
        for r in regions:
            classes[r["id"]] = r["class_id"]
        Image.fromarray(classes[full_array]).save(folder / "classes.png")
        pixels = np.bincount(full_array.ravel(), minlength=len(regions) + 1)
        present = {r["id"] for r in regions if pixels[r["id"]]}
        regions = [r for r in regions if r["id"] in present]
        groups = {k: [rid for rid in members if rid in present] for k, members in groups.items()}
        groups = {k: members for k, members in groups.items() if members}
        subject_ids &= present
        candidates = [c for c in candidates if c["id"] in present]
        for r in regions:
            r.update(color=color(r["id"]), pixels=int(pixels[r["id"]]))
        layers = []
        group_ids = np.zeros(ids.shape, np.uint16)
        for gid, (key, members) in enumerate(groups.items(), 1):
            mask = np.isin(ids, members)
            group_ids[mask] = gid
            name = f"masks/{key}.png"
            binary = np.zeros(len(pixels), np.uint8)
            binary[members] = 255
            Image.fromarray(binary[full_array]).save(folder / name)
            layers.append(dict(id=gid, key=key, name=GROUP_NAMES.get(key, key), color=GROUP_COLORS.get(key, color(gid)),
                               region_ids=members, mask=name, pixels=int(pixels[members].sum())))
        preview(small, ids, regions, folder / "regions-preview.jpg")
        preview(small, group_ids, layers, folder / "preview.jpg")
        manifest = dict(schema="scene-regions/v1", source=source.name, sha256=digest,
                        size=list(size), analysis_size=list(small.size), orientation="exif-transposed",
                        region_map="regions.png", class_map="classes.png", preview="preview.jpg",
                        region_preview="regions-preview.jpg", regions=regions, groups=layers,
                        subject=dict(region_ids=sorted(subject_ids), method=method, candidates=candidates))
        if quality == "precise":
            manifest["boundary_refinement"] = {"changed_analysis_pixels": int(np.count_nonzero(ids != original_grid)),
                                               "working_size": list(photo.size), "models": tools_used}
        if reviewed:
            manifest["reviewed_group_edits"] = reviewed
        if footer_start is not None:
            manifest["footer_start"] = footer_start
        manifest["quality"] = quality
        if edited is not None:
            manifest["corrections"] = edited
        if views is not None:
            manifest["model_disagreement"] = write_review(folder, small, views, regions, layers)
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        folder.rename(out)
    return out / "manifest.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="新建的输出目录")
    parser.add_argument("--corrections", type=Path, help="仅覆盖已核对的局部类别，坐标来自分析图")
    parser.add_argument("--group-edits", type=Path, help="在原图尺寸回放逐像素分组修正")
    parser.add_argument("--footer-start", type=int, help="已核对的底部拍摄信息栏首行，以分析图像素计")
    parser.add_argument("--quality", choices=("precise", "fast"), default="precise",
                        help="precise 融合双模型并标出分歧，fast 使用原先的单模型")
    parser.add_argument("--subject", default="auto", help="auto、none、salient、区块 key 或编号")
    parser.add_argument("--split-class", action="append", default=[], help="仅在需要细分时，拆开指定类别的独立连通块")
    parser.add_argument("--group", action="append", default=[], help="例如 track=road,floor，可重复")
    args = parser.parse_args()
    try:
        print(export(args.image.expanduser(), args.out.expanduser(), args.subject, args.group, args.split_class, args.quality, args.corrections, args.footer_start, args.group_edits))
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
