"""Bind upright photo coordinates, the Agent's answers and the two physics formats."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from engine.region_import import import_regions
from engine.vision import infer, load_photo, oriented_size, tensor
from engine.workflow import collect


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset(folder, name):
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError(f"资源路径超出交接包范围：{name}")
    return path


def raster(path, size, mode=None):
    with Image.open(path) as im:
        if im.size != tuple(size) or im.getexif().get(274, 1) != 1:
            raise ValueError(f"图层方向或尺寸不匹配：{path.name}")
        if mode and im.mode != mode:
            raise ValueError(f"{path.name} 需要 {mode} 像素格式")
        return np.array(im)


def nearest(array, size):
    return np.asarray(Image.fromarray(array).resize(tuple(size), Image.Resampling.NEAREST))


class Scene:
    def __init__(self, photo_path, manifest, answers):
        m = read(manifest)
        photo = load_photo(photo_path)
        self.size = tuple(m["size"])
        self.analysis = tuple(m["analysis_size"])
        if (m["schema"] != "scene-regions/v1" or m.get("orientation") != "exif-transposed"
                or self.size != oriented_size(photo_path) or m["sha256"] != digest(photo_path)):
            raise ValueError("原图指纹、尺寸或 EXIF 正向声明不匹配")
        self.classes = None
        self.photo = photo
        self.hashes = dict(image_sha256=m["sha256"], manifest_sha256=digest(manifest),
                           answers_sha256=digest(answers))
        self.rows = m.get("regions", [])
        folder = manifest.parent
        self.group_mask_hashes = {}
        has_classes = m.get("class_map") and asset(folder, m["class_map"]).is_file()
        if has_classes and m.get("region_map") and asset(folder, m["region_map"]).is_file():
            _, groups, signature = import_regions(manifest, photo_path, photo)
            self.ids = raster(asset(folder, m["region_map"]), self.size)
            self.groups = {g["key"]: np.isin(self.ids, g["region_ids"]) for g in m["groups"]}
        else:
            self.ids = None
            self.groups = {}
            seen = np.zeros(self.size[::-1], bool)
            for g in m["groups"]:
                path = asset(folder, g["mask"])
                self.group_mask_hashes[g["key"]] = digest(path)
                pixels = raster(path, self.size)
                if pixels.ndim == 3 and pixels.shape[2] == 4:
                    pixels = pixels[:, :, 3]
                if pixels.ndim != 2 or not np.isin(pixels, [0, 255]).all():
                    raise ValueError("分组蒙版须为二值灰度或二值 Alpha")
                mask = pixels == 255
                if (seen & mask).any() or g["key"] in self.groups:
                    raise ValueError("分组蒙版重叠或重复")
                seen |= mask
                self.groups[g["key"]] = mask
            if not seen.all():
                raise ValueError("分组蒙版未覆盖全图")
            groups = {k: nearest(v, self.analysis) for k, v in self.groups.items()}
            signature = None
            if has_classes:
                classes = raster(asset(folder, m["class_map"]), self.size)
                self.classes = classes
                sig = hashlib.sha256(nearest(classes, self.analysis).astype(np.uint16).tobytes())
                for k, mask in sorted(groups.items()):
                    sig.update(k.encode() + b"\0" + np.packbits(mask).tobytes())
                signature = sig.hexdigest()
        self.signature = signature
        saved = read(answers)
        if not saved.get("subject") or (saved["subject"] != "none" and not saved.get("motion")):
            raise ValueError("需要主 Agent 已确认的主体与动静方案")
        # The Agent signature starts with the class plane. With only group PNGs
        # its digest cannot be reconstructed. Keep both the claimed signature
        # and the actual group asset hashes; still enforce the plan's RGB digest.
        self.reported_signature = saved.get("region_pack_sha256")
        check_signature = signature if signature is not None else self.reported_signature
        self.plan = collect(photo, [dict(label=r["label"], name=r["label"]) for r in self.rows],
                            answers, None, None, groups, check_signature)
        self.empty = np.zeros(self.size[::-1], bool)
        self.members = {g["key"]: g.get("region_ids", []) for g in m["groups"]}
        self.subject = self.select(self.plan["subject"])
        self.caption = self.empty.copy()
        self.shelter = self.empty.copy()
        for k in ("caption", "footer", "metadata"):
            self.caption |= self.groups.get(k, self.empty)
        for k in self.plan["sheltered"]:
            self.shelter |= self.select(k)
        self.water = self.groups.get("water", self.empty).copy()
        if self.ids is not None:
            self.water |= np.isin(self.ids, [r["id"] for r in self.rows if self.label(r) in
                                            {"water", "sea", "river", "lake", "swimming pool"}])
        self.air = self.groups.get("sky", self.empty).copy()
        if any(self.label(r) == "sky" for r in self.rows) and (self.ids is not None or self.classes is not None):
            self.air |= self.select("sky")
        self.depth = np.where(self.subject, 128, 0).astype(np.uint8)
        self.depth_source = None
        self.puddle = self.empty.copy()
        self.responses = {}
        self.pairs = {}
        self.sources = []

    def label(self, row):
        return self.plan["merge_into"].get(row["label"], row["label"])

    def select(self, key):
        if key == "none":
            return self.empty
        if key == "salient":
            mask = infer("foreground.onnx", tensor(self.photo, 320, relative=True))[0]
            return nearest(mask > .5, self.size)
        if key.startswith("group:"):
            return self.groups[key[6:]]
        wanted = {r["id"] for r in self.rows if self.label(r) == key}
        if self.ids is not None:
            return np.isin(self.ids, list(wanted))
        if self.classes is not None:
            return np.isin(self.classes, [r["class_id"] for r in self.rows if r["id"] in wanted])
        result = self.empty.copy()
        for k, ids in self.members.items():
            if wanted.intersection(ids):
                if not set(ids) <= wanted:
                    raise ValueError("主体或遮挡标签只占部分分组，需要原始区块图")
                result |= self.groups[k]
        if not result.any():
            raise ValueError("仅分组的输入缺少已确认标签的可定位蒙版")
        return result

    def bind(self, data):
        if any(data.get(k) != self.hashes[k] for k in ("image_sha256", "manifest_sha256")):
            raise ValueError("交接包的原图或分区指纹不匹配")
        if tuple(data.get("size", self.size)) != self.size or data.get("orientation", "exif-transposed") != "exif-transposed":
            raise ValueError("交接包坐标方向或尺寸不匹配")

    def set_depth(self, values, source):
        if values.shape != self.size[::-1]:
            values = nearest(values, self.size)
            if self.ids is not None:
                agree = nearest(nearest(self.ids, self.analysis), self.size) == self.ids
                values = np.where(agree, values, np.where(self.subject, 128, 255)).astype(np.uint8)
        if self.depth_source and not np.array_equal(self.depth, values):
            raise ValueError("多份深度图不一致")
        self.depth, self.depth_source = values, source

    def physics(self, path):
        data = read(path)
        self.bind(data)
        folder = path.parent
        maps = data.get("maps", {})
        schema = data["schema"]
        source = dict(schema=schema, sha256=digest(path), channels={})
        self.sources.append(source)

        def channel(name, paths, size):
            file = asset(folder, paths[name])
            source["channels"][name] = digest(file)
            return raster(file, size, "L")

        if schema == "weather-accumulation-places/v1":
            loc = data.get("locations") or {}
            if tuple(loc.get("size", self.size)) != self.size or loc.get("orientation", "exif-transposed") != "exif-transposed":
                raise ValueError("位置包尺寸或方向不匹配")
            if "积水" in maps:
                self.puddle |= channel("积水", maps, self.size) == 255
            return
        if schema != "weather-surface-physics/v1" or tuple(data["analysis_size"]) != self.analysis:
            raise ValueError("详细仿真版本或分析尺寸不匹配")
        if self.ids is None or data.get("region_pack_sha256", self.signature) != self.signature:
            raise ValueError("详细仿真需要匹配的区块图与分组签名")
        if not np.array_equal(self.ids, raster(asset(folder, data["region_map"]), self.size)):
            raise ValueError("详细仿真与分区包的原始区块图不一致")
        scenario = data["scenario"]
        saved = scenario.get("plan")
        if saved and any(saved.get(k) != self.plan.get(k) for k in
                         ("subject", "motion", "merge_into", "sheltered", "weather", "image_sha256", "ground_snow")):
            raise ValueError("物理仿真与当前主体或天气方案不一致")
        ids = nearest(self.ids, self.analysis)
        agree = nearest(ids, self.size) == self.ids

        def expand(name, paths=maps):
            values = channel(name, paths, self.analysis)
            return np.where(agree, nearest(values, self.size), 0).astype(np.uint8)

        if "depth" in maps:
            self.set_depth(channel("depth", maps, self.analysis), "handoff:maps.depth")
        weather = source["weather"] = scenario["weather"]
        if weather not in ("小雨", "大雨"):
            return
        if weather in self.responses:
            raise ValueError("同一天气有多份直接接触通道")
        scale = float(data.get("contact_scale", 1))
        def weight(name):
            if name not in maps:
                return np.zeros(self.size[::-1], np.uint8)
            return np.clip(expand(name).astype(float) * scale, 0, 255).astype(np.uint8)
        contact = weight("contact")
        water = self.water
        air = self.air
        if "material" in maps:
            material = expand("material")
            codes = data["material_codes"]
            water = agree & (material == codes.index("water"))
            air = (material == codes.index("air")) | ~agree
        ripple = weight("ripple") if "ripple" in maps else contact
        if "contact" in maps:
            ripple = ripple * (contact > 0)
        allowed = ~(self.caption | self.shelter | air)
        self.responses[weather] = np.stack((contact * allowed * ~water,
                                            ripple * allowed * water, water.astype(np.uint8)), axis=-1)
        frames = data.get("keyframes", [])
        pairs = self.pairs[weather] = []
        drains = data.get("review", {}).get("drains", [])
        if not frames or not drains:
            return
        last = max(frames, key=lambda f: f["seconds"])["maps"]
        if not all(k in last and data.get("state_scales", {}).get(k, 0) > 0 for k in ("drip", "landing")):
            return
        flows = {k: channel(k, last, self.analysis) for k in ("drip", "landing")}
        for drain in drains:
            endpoints = []
            for key, flow in (("from", "drip"), ("to", "landing")):
                item = drain[key]
                mask = Image.new("1", self.analysis, 1 if "polygon" not in item else 0)
                if "polygon" in item:
                    ImageDraw.Draw(mask).polygon([tuple(p) for p in item["polygon"]], fill=1)
                weight_map = np.where(np.asarray(mask) & (ids == item["region_id"]), flows[flow], 0)
                y, x = np.unravel_index(weight_map.argmax(), weight_map.shape)
                u, v = (x + .5) / self.analysis[0], (y + .5) / self.analysis[1]
                px, py = int(u*self.size[0]), int(v*self.size[1])
                if weight_map[y,x] and self.ids[py,px] == item["region_id"] and not self.caption[py,px]:
                    endpoints.append([float(u), float(v)])
            if len(endpoints) == 2 and endpoints[1][1] > endpoints[0][1]:
                pairs.append(endpoints)
