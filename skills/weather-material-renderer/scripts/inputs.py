"""Decode the project handoffs without importing the wallpaper agent."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


POSITION = "weather-accumulation-places/v1"
SIMULATION = "weather-surface-physics/v1"
ORIENTATION = "exif-transposed"
ENCODING = {"0": "不会", "127": "保守略过", "255": "会"}
SNOW = {"小雪": .16, "中雪": .36, "大雪": .68, "暴雪": 1.}
RAIN = {"小雨": .28, "大雨": 1.}
DRY = ("晴朗", "日出", "日落", "黄昏", "深夜", "多云", "大风")
GAIN = {"subtle": .65, "standard": 1., "strong": 1.25}
MOTION = {"stationary": 1., "moving": .55, "high_speed": .12}
GROUND = {"road", "floor", "earth", "path", "sidewalk", "land", "runway", "grass", "field", "dirt track"}
WATER = {"water", "sea", "river", "lake", "swimming pool", "waterfall"}
AIR, LIQUID, HARD, GRASS, LEAVES, PAINT = range(6)
KINDS = {"air": AIR, "water": LIQUID, "hard": HARD, "porous": GRASS, "foliage": LEAVES, "smooth": PAINT}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def grid(folder, name, size, modes=("L",)):
    root = folder.resolve()
    path = (root / name).resolve()
    require(path.is_relative_to(root), f"图层路径超出交接目录：{name}")
    with Image.open(path) as image:
        require(image.size == tuple(size) and image.mode in modes and image.getexif().get(274, 1) == 1,
                f"图层尺寸、像素格式或方向错误：{name}")
        return np.array(image)


def decode_photo(path):
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(path) as src:
        orientation = src.getexif().get(274, 1)
        rgba = ImageOps.exif_transpose(src).convert("RGBA")
    def flatten(image):
        base = Image.new("RGB", image.size, "#202a34")
        base.paste(image, mask=image.getchannel("A"))
        return base
    full = flatten(rgba)
    rgba.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
    plan_photo = flatten(rgba)
    digest = hashlib.sha256(str(plan_photo.size).encode() + plan_photo.tobytes()).hexdigest()
    plan_photo.thumbnail((1024, 1024))
    return full, orientation, digest, plan_photo.size


def base_material(label, group):
    if group in {"caption", "metadata", "footer", "sky"} or label == "sky":
        return AIR
    if group == "water" or label in WATER:
        return LIQUID
    if label in {"tree", "palm", "flower", "plant", "bush"}:
        return LEAVES
    if label in {"grass", "field", "earth", "sand", "land", "moss"}:
        return GRASS
    if label in {"car", "truck", "bus", "van", "windowpane", "mirror"}:
        return PAINT
    return HARD


class Scene:
    def __init__(self, photo, manifest, answers):
        self.photo, self.exif, pixel_hash, self.analysis = decode_photo(photo)
        self.size = self.photo.size
        self.file_hash, self.manifest_hash = sha(photo), sha(manifest)
        self.answers_hash = sha(answers)
        m, p = read(manifest), read(answers)
        require(m["schema"] == "scene-regions/v1" and m["sha256"] == self.file_hash, "原图文件与分区指纹不符")
        require(m["orientation"] == ORIENTATION and m["size"] == list(self.size)
                and m["analysis_size"] == list(self.analysis), "分区方向或尺寸与正向原图不符")
        self.ids = grid(manifest.parent, m["region_map"], self.size, ("L", "I", "I;16"))
        classes = grid(manifest.parent, m["class_map"], self.size)
        rows = m["regions"]
        rids = [r["id"] for r in rows]
        require(sorted(rids) == list(np.unique(self.ids)) and min(rids) >= 0 and max(rids) <= 65535,
                "区块编号表与原尺寸编号图不符")
        self.labels = {r["id"]: r["label"] for r in rows}
        lut = np.zeros(max(rids) + 1, np.uint16)
        for r in rows:
            lut[r["id"]] = r["class_id"]
        require(np.array_equal(lut[self.ids], classes), "区块与类别图不匹配")
        self.small_ids = np.array(Image.fromarray(self.ids).resize(self.analysis, Image.Resampling.NEAREST))
        self.groups = {g["key"]: g["region_ids"] for g in m["groups"]}
        require(len(self.groups) == len(m["groups"]) and
                sorted(rid for group in self.groups.values() for rid in group) == sorted(rids), "分组须完整且互斥")
        signature = hashlib.sha256(lut[self.small_ids].tobytes())
        for key, group in sorted(self.groups.items()):
            signature.update(key.encode() + b"\0" + np.packbits(np.isin(self.small_ids, group)).tobytes())
        self.signature = signature.hexdigest()
        require(p["version"] == 1 and p["image_sha256"] == pixel_hash and
                p.get("region_pack_sha256", self.signature) == self.signature, "天气方案与照片像素或分区不匹配")
        require(p["ground_snow"] in ("natural", "wet", "cold") and p["accumulation"] == "fixed_per_intensity"
                and isinstance(p["sheltered"], list) and isinstance(p["weather"], dict)
                and all(v in GAIN for v in p["weather"].values()), "天气方案取值无效")
        self.plan = p
        merges = p["merge_into"]
        choices = set(self.labels.values())
        require(isinstance(merges, dict) and all(k in choices and v in choices and v not in merges
                                                for k, v in merges.items()), "合并类别不属于当前照片或存在循环")
        self.labels = {rid: merges.get(label, label) for rid, label in self.labels.items()}
        self.subject_info = m.get("subject", {})
        self.material = np.zeros(len(lut), np.uint8)
        self.ground = np.zeros(len(lut), bool)
        self.protected = np.zeros(len(lut), bool)
        for key, group in self.groups.items():
            for rid in group:
                self.material[rid] = base_material(self.labels[rid], key)
                self.ground[rid] = key in {"ground", "track"} or self.labels[rid] in GROUND
                self.protected[rid] = key in {"caption", "footer", "metadata", "sky"} or self.labels[rid] == "sky"
        require((p["subject"] == "none" and p["motion"] is None)
                or (p["subject"] != "none" and p["motion"] in MOTION), "缺少已确认的主体或动静状态")
        for key in [p["subject"], *p["sheltered"]]:
            if key != "salient":
                self.selection(key)

    def selection(self, key):
        if key == "none":
            return []
        if key == "salient":
            require(self.subject_info.get("method") in {"salient", "review_salient"} and self.subject_info["region_ids"],
                    "位置交接需使用已导出显著性轮廓的分区或按该方案完成的详细仿真")
            return self.subject_info["region_ids"]
        if key.startswith("group:"):
            return self.groups[key[6:]]
        selected = [rid for rid, label in self.labels.items() if key == label]
        require(selected, f"本图没有已确认的标签 {key}")
        return selected


class Handoff:
    def __init__(self, path, scene):
        self.data, self.folder, self.scene = read(path), path.parent, scene
        d = self.data
        self.schema = d["schema"]
        require(self.schema in (POSITION, SIMULATION) and d["image_sha256"] == scene.file_hash
                and d["manifest_sha256"] == scene.manifest_hash, "物理交接类型或照片、分区指纹不符")
        require(d.get("answers_sha256", scene.answers_hash) == scene.answers_hash, "位置交接使用了不同的天气方案")
        loc = d.get("locations", {})
        for meta in (loc, d):
            require(meta.get("size", list(scene.size)) == list(scene.size)
                    and meta.get("orientation", ORIENTATION) == ORIENTATION, "交接方向或尺寸不符")
        self.maps = d.get("maps", {})
        if self.schema == POSITION:
            if self.maps:
                require(d["encoding"] == ENCODING, "未知的位置蒙版编码")
            return
        require(d["analysis_size"] == list(scene.analysis) and d["size"] == list(scene.size)
                and d.get("region_pack_sha256", scene.signature) == scene.signature, "详细仿真与分区网格不符")
        ids = grid(path.parent, d["region_map"], scene.size, ("L", "I;16", "I"))
        require(np.array_equal(ids, scene.ids), "详细仿真的原尺寸区块边界不符")
        self.last = max(d.get("keyframes", []), key=lambda f: f["seconds"], default={"maps": {}, "seconds": None})

    def check_scenario(self, weather):
        scenario, p = self.data["scenario"], self.scene.plan
        require(scenario["weather"] == weather, "详细仿真仅适用于其中记录的天气档")
        q = scenario.get("plan")
        if q is None:
            ok = p["motion"] in (None, "stationary") and p["ground_snow"] == "natural" and not p["sheltered"] and not p["merge_into"]
        else:
            ok = all(q[k] == p[k] for k in ("motion", "subject", "sheltered", "merge_into", "ground_snow"))
        if weather in RAIN:
            ok = ok and scenario.get("visual_gain", 1.) == GAIN[p["weather"][weather]]
        require(ok, "详细仿真使用了不同的主体、动静或天气条件，请使用按本方案生成的交接")

    def permission(self, event, weather):
        # A grade-specific export only applies to that grade. Generic maps serve all grades.
        bindings = self.data.get("weather", {})
        keys = ([weather] if bindings.get(weather) == event else []) + [event]
        grades = {*SNOW, *RAIN, "暴雨"}
        keys.extend(k for k, phenomenon in bindings.items() if phenomenon == event and k not in grades)
        key = next((k for k in keys if k in self.maps), None)
        if key is None:
            return None, None
        raw = grid(self.folder, self.maps[key], self.scene.size)
        require(np.isin(raw, (0, 127, 255)).all(), f"{key} 蒙版含未知离散状态")
        return raw, "maps." + key

    def channel(self, key, *, state=False):
        paths = self.last["maps"] if state else self.maps
        if key not in paths:
            return None
        pixels = grid(self.folder, paths[key], self.scene.analysis)
        if key == "material":
            codes = self.data["material_codes"]
            require(pixels.max() < len(codes), "局部材质编号越界")
            return np.array([KINDS[k] for k in codes], np.uint8)[pixels]
        scale = self.data["state_scales"][key] if state else self.data["contact_scale"] if key == "contact" else 1.
        require(isinstance(scale, (float, int)) and np.isfinite(scale) and scale >= 0, f"{key} 比例无效")
        return pixels.astype(np.float32) * (scale / 255)
