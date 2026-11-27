"""Compile a photo-reviewed part inventory into a location-only weather handoff."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.region_import import import_regions
from engine.vision import load_photo
import numpy as np
from PIL import Image
from infer_places import geometry_parts
from location_maps import apply_spatial_review, write_maps
from preview import write_preview

# Each phenomenon requires all clauses. Within a clause, any listed source suffices.
RULES = {
    "积雪": [("direct",), ("holds_snow",)],
    "积水": [("direct", "inflow"), ("pools",)],
    "结霜": [("frost_ready",)],
    "凝露": [("dew_ready",)],
    "冻雨挂冰": [("direct",), ("freezes",)],
    "积水再冻结": [("wet",), ("freezes",)],
    "冰雹堆积": [("direct", "inflow_hail"), ("traps_hail",)],
}
ALIASES = {w: "积雪" for w in ("下雪", "小雪", "中雪", "大雪", "暴雪")}
ALIASES.update({w: "积水" for w in ("下雨", "小雨", "大雨", "暴雨")})
ALIASES.update({"霜": "结霜", "露水": "凝露", "冻雨": "冻雨挂冰", "再冻结": "积水再冻结", "冰雹": "冰雹堆积"})
FACTS = {
    "direct": "降水可直接抵达", "holds_snow": "有朝上的承托面且雪能留住",
    "inflow": "有来水流入", "pools": "低处能蓄住明水",
    "frost_ready": "湿空气可达且表面低于冰点并达到霜点",
    "dew_ready": "湿空气可达且表面高于冰点并达到露点",
    "freezes": "表面足够冷且持续到液水冻结", "wet": "降温前有水膜或积水",
    "inflow_hail": "有冰粒滚入", "traps_hail": "能截留冰粒并维持堆积",
    "water_freezes": "水面持续散热至封冻",
}


def decide(part, event):
    kind = part["kind"]
    if kind == "air":
        return "不会", []
    if kind == "unknown":
        return "保守略过", ["照片中无法可靠定位该表面"]
    clauses = RULES[event]
    if kind == "water":
        if event == "积水" and part.get("pools") is True:
            return "会", []
        if event != "积水再冻结":
            return "不会", []
        clauses = [("water_freezes",)]
    pending = []
    for clause in clauses:
        evidence = [part.get(key) for key in clause]
        if True in evidence:
            continue
        if all(value is False for value in evidence):
            return "不会", []
        pending.append("或".join(FACTS[k] for k in clause if part.get(k) is None))
    return ("保守略过", pending) if pending else ("会", [])


def compile_placement(data, spec, manifest_hash, weather):
    if spec.get("manifest_sha256") != manifest_hash:
        raise ValueError("部位记录与分区清单指纹不匹配。")
    rows = {row["id"]: row for row in data["regions"]}
    owners = {rid: g["key"] for g in data["groups"] for rid in g["region_ids"]}
    by_id = {rid: {} for rid in rows}
    for part in spec["parts"]:
        rid, name = part["region_id"], part["name"]
        if type(rid) is not int or rid not in rows or not isinstance(name, str) or not name.strip():
            raise ValueError("每个部位须引用本图区块，并写明部位名称。")
        name = name.strip()
        if name in by_id[rid]:
            raise ValueError("同一区块内的部位名称重复。")
        if part["kind"] not in ("solid", "water", "air", "unknown"):
            raise ValueError("部位 kind 无效。")
        for key in part.keys() - {"region_id", "name", "kind"}:
            if key not in FACTS or (part[key] is not None and type(part[key]) is not bool):
                raise ValueError(f"部位属性 {key} 无效。")
        by_id[rid][name] = part
    missing = [rid for rid, parts in by_id.items() if not parts]
    if missing:
        raise ValueError(f"尚未检查区块 {missing}。")
    regions = []
    for rid, parts in by_id.items():
        results = {}
        for w in weather:
            places = {"会": [], "不会": [], "保守略过": []}
            for name, part in parts.items():
                decision, needs = decide(part, ALIASES.get(w, w))
                places[decision].append({"部位": name, "条件": needs} if needs else name)
            results[w] = places
        regions.append(dict(region_id=rid, key=rows[rid].get("key", rows[rid]["label"]),
                            label=rows[rid]["label"], group=owners[rid], placement=results))
    return dict(schema="weather-accumulation-places/v1", image_sha256=data["sha256"],
                manifest_sha256=manifest_hash, assumptions=spec.get("assumptions", []),
                weather={w: ALIASES.get(w, w) for w in weather}, regions=regions)


def weather_list(values):
    result = []
    for value in values:
        if value == "结冰":
            result.extend(("冻雨挂冰", "积水再冻结"))
        else:
            result.append(value)
    return list(dict.fromkeys(result))


def check_group_masks(manifest, data, regions):
    root = manifest.parent.resolve()
    for group in data["groups"]:
        if "mask" not in group:
            continue
        file = (root / group["mask"]).resolve()
        if not file.is_relative_to(root):
            raise ValueError("分组蒙版须位于分区包内。")
        with Image.open(file) as img:
            if img.mode not in ("1", "L") or img.size != regions.shape[::-1]:
                raise ValueError("分组蒙版尺寸或格式与清单不符。")
            actual = np.asarray(img.convert("L"))
            expected = np.isin(regions, group["region_ids"])
            if not np.array_equal(actual, expected.astype(np.uint8) * 255):
                raise ValueError(f"分组蒙版与区块编号不一致：{group['key']}")


def build(photo_path, manifest, folder, weather, inventory=None, patches=None, answers=None):
    raw = manifest.read_bytes()
    data = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    photo = load_photo(photo_path)
    scene, group_masks, signature = import_regions(manifest, photo_path, photo)
    with Image.open(manifest.parent / data["region_map"]) as original:
        regions = np.array(original)
        sample_regions = np.array(original.resize(scene[0].size, Image.Resampling.NEAREST))
    check_group_masks(manifest, data, regions)
    plan = None
    if answers:
        from engine.workflow import collect
        rows = [dict(label=r["label"], name=r["label"]) for r in data["regions"]]
        plan = collect(photo, rows, answers, None, None, group_masks, signature)
        if inventory:
            raise ValueError("确认方案需要可定位的空间部位图")
    grid = None
    if inventory:
        spec = json.loads(inventory.read_text(encoding="utf-8"))
    else:
        review = json.loads(patches.read_text(encoding="utf-8")) if patches else None
        if review is not None and review.get("manifest_sha256") != digest:
            raise ValueError("修订与分区清单的指纹不匹配。")
        groups = {rid: g["key"] for g in data["groups"] for rid in g["region_ids"]}
        labels = {r["id"]: r["label"] for r in data["regions"]}
        rows = [dict(row, label=plan["merge_into"].get(row["label"], row["label"])) for row in data["regions"]] if plan else data["regions"]
        parts, _, part_map = geometry_parts(photo, scene[0], sample_regions, rows, groups)
        parts, grid = apply_spatial_review(regions, part_map, parts, groups, labels, review)
        events = {ALIASES.get(w, w) for w in weather}
        assumptions = ["静止场景，普通降雪按弱风与冷表面估计。表面朝向及遮挡由单张照片推算。"]
        if plan:
            def full_selection(key):
                if key == "none":
                    return np.zeros(regions.shape, bool)
                if key == "salient":
                    from engine.vision import infer, tensor
                    estimated = infer("foreground.onnx", tensor(photo, 320, relative=True))[0]
                    image = Image.fromarray(estimated).resize(regions.shape[::-1], Image.Resampling.BILINEAR)
                    return np.asarray(image) > .5
                if key.startswith("group:"):
                    return np.isin(regions, [rid for rid, name in groups.items() if name == key[6:]])
                return np.isin(regions, [row["id"] for row in rows if row["label"] == key])
            shelter = np.zeros_like(regions, dtype=bool)
            for key in plan["sheltered"]:
                shelter |= full_selection(key)
            assumptions[0] = f"主体 {plan['subject']}，动静 {plan['motion']}，地面 {plan['ground_snow']}。朝向与遮挡由单张照片估计，留雪量由分档仿真计算。"
            # Split only the affected pixels, including partially overlapping salient subjects.
            # Exposed high points still get the simulation's reduced deposition.
            # At lower positions on a fast subject a single view is weak evidence.
            fast = full_selection(plan["subject"]) if plan["motion"] == "high_speed" else np.zeros_like(shelter)
            heights = np.arange(fast.shape[0])[:, None]
            top = fast.argmax(axis=0)
            bottom = fast.shape[0] - fast[::-1].argmax(axis=0)
            uncertain_fast = fast & (heights > top + .40 * (bottom - top))
            for full, suffix, facts in [
                    (uncertain_fast, "高速运动、难以确认持续留雪", dict(holds_snow=None)),
                    (shelter, "确认有遮挡", dict(direct=False, wet=False))]:
                for pid in np.unique(grid[full]):
                    part = parts[int(pid)-1]
                    if part["kind"] != "solid" or ("holds_snow" in facts and part.get("holds_snow") is not True):
                        continue
                    area = full & (grid == pid)
                    changed = dict(part, name=part["name"] + "·" + suffix, **facts)
                    if np.all(full[grid == pid]):
                        parts[int(pid)-1] = changed
                    else:
                        if len(parts) >= 65535:
                            raise ValueError("空间部位数超过编号上限")
                        parts.append(changed)
                        grid[area] = len(parts)
        if events & {"结霜", "凝露"}:
            assumptions.append("霜与露各按相应温湿条件成立的静夜模拟，外露表面较易冷却。")
        if events & {"积水再冻结", "冻雨挂冰"}:
            assumptions.append("按接触面可冻结的短时降温估计；再冻结另假设先前淋雨，开阔水体保持液态。")
        if events & {"积水", "冰雹堆积"}:
            assumptions.append("未见外部来水或滚入冰粒；仅凭相对深度无法确认低处的封闭与排水条件。")
        if review:
            assumptions.extend(review.get("assumptions", []))
        spec = dict(manifest_sha256=digest, assumptions=assumptions, parts=parts)
    output = compile_placement(data, spec, digest, weather)
    if plan:
        output["answers_sha256"] = hashlib.sha256(answers.read_bytes()).hexdigest()
        output["plan"] = plan
    output["source"] = data.get("source", photo_path.name)
    if grid is not None:
        write_maps(folder, photo, grid, spec["parts"], output)
        write_preview(folder, [(output["source"], ".", output)])
    (folder / "handoff.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description="按图中可见部位交接积留位置")
    parser.add_argument("photo", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--answers", type=Path)
    spec = parser.add_mutually_exclusive_group()
    spec.add_argument("--parts", type=Path, help="既有的纯文字部位清单")
    spec.add_argument("--patches", type=Path, help="绑定本次清单的空间部位修订")
    parser.add_argument("--weather", nargs="+", choices=[*RULES, *ALIASES, "结冰"], default=list(RULES))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise ValueError("请选择新的输出目录。")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.out.parent) as tmp:
            package = Path(tmp) / "package"
            package.mkdir()
            build(args.photo, args.manifest, package, weather_list(args.weather), args.parts, args.patches, args.answers)
            package.rename(args.out)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"位置交接失败：{exc}\n")
    print(args.out / "handoff.json")


if __name__ == "__main__":
    main()
