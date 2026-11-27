"""Render registered weather-material layers in straight-alpha sRGB."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from inputs import (AIR, LIQUID, DRY, GAIN, MOTION, POSITION, RAIN, SIMULATION, SNOW,
                    Handoff, ORIENTATION, Scene, require, sha)
from texture import Lighting, unit


SPECIAL = {"霜": "结霜", "露": "凝露", "露水": "凝露", "冻雨": "冻雨挂冰", "再冻结": "积水再冻结", "冰雹": "冰雹堆积"}
EFFECTS = ("结霜", "凝露", "冻雨挂冰", "积水再冻结", "冰雹堆积")
WET_MAP = {"湿色": "湿润", "水膜": "水膜", "积水反射": "积水", "已有积雪": "已有积雪"}


@dataclass
class Layer:
    name: str
    permission: object
    channels: list
    evidence: list
    empty: str = ""


class Renderer:
    def __init__(self, args):
        self.scene = s = Scene(args.photo, args.manifest, args.answers)
        handoff = Handoff(args.handoff, s)
        self.sim = handoff if handoff.schema == SIMULATION else None
        self.places = handoff if handoff.schema == POSITION else None
        if args.places:
            require(self.sim is not None, "--places 用于给详细仿真补充位置许可")
            self.places = Handoff(args.places, s)
            require(self.places.schema == POSITION, "--places 需要位置交接")
        self.pond = None
        if self.places and self.places.data.get("standing_water"):
            from inputs import grid
            self.pond = grid(self.places.folder,self.places.data["standing_water"],s.size) == 255
        self.weather = args.weather
        if self.weather in (*SNOW, *RAIN, *DRY):
            require(self.weather in s.plan["weather"], "天气档未在方案中确认")
        self.grade = GAIN[s.plan["weather"].get(self.weather, "standard")]
        self.light = Lighting(s.photo, args.seed)
        self.depth = self.kinds = None
        self.shelter = np.zeros_like(s.ground)
        self.retention = np.ones(s.ground.shape, np.float32)
        if self.sim:
            self.sim.check_scenario(self.weather)
            self.depth, self.kinds = self.sim.channel("depth"), self.sim.channel("material")
            # Reproduce Pillow's pixel-centred nearest-neighbour sampling exactly.
            self.sx, self.sy = [np.array(Image.fromarray(np.arange(n, dtype=np.int32)[None])
                                         .resize((full, 1), Image.Resampling.NEAREST))[0]
                                for n, full in zip(s.analysis, s.size)]
        elif self.weather not in DRY:
            for key in s.plan["sheltered"]:
                self.shelter[s.selection(key)] = True
            self.retention[s.ground] = {"natural": .8, "cold": 1, "wet": .25}[s.plan["ground_snow"]]
            if s.plan["motion"] in ("moving", "high_speed"):
                self.retention[s.selection(s.plan["subject"])] *= MOTION[s.plan["motion"]]

    def layers(self):
        weather = self.weather
        if weather in SNOW:
            names = ["积雪"]
        elif weather in RAIN:
            names = ["湿色", "水膜", "积水反射"]
        elif weather in DRY:
            names = []
        elif weather == "结冰":
            names = ["冻雨挂冰", "积水再冻结"]
        else:
            names = [SPECIAL.get(weather, weather)]
        if self.sim and weather in (*RAIN, *DRY):
            snow = self.sim.channel("snow", state=True)
            review = self.sim.data.get("review", {})
            initial = self.sim.data["scenario"].get("old_snow") or any(p.get("old_snow") for p in review.get("patches", []))
            if (snow is not None and snow.any()) or initial:
                names.append("已有积雪")
        return [self.layer(n) for n in names]

    def layer(self, name):
        event = WET_MAP.get(name, name)
        permit, evidence, channels = None, [], []
        if self.places:
            permit, path = self.places.permission(event, self.weather)
            if path is None and name == "湿色":
                permit, path = self.places.permission("接触", self.weather)
            if path:
                evidence.append("placement." + path)
        if self.sim:
            fields = {"积雪": ("snow",), "已有积雪": ("snow",), "湿色": ("absorbed", "film", "contact"), "水膜": ("film",)}
            for key in fields.get(name, ()):
                state = key != "contact"
                a = self.sim.channel(key, state=state)
                if a is not None:
                    channels.append(a)
                    prefix = f"keyframes[{self.sim.last['seconds']}s].maps." if state else "maps."
                    evidence.append(prefix + key)
        layer = Layer(name, permit, channels, evidence)
        if permit is not None and not (permit == 255).any():
            layer.empty = "该现象的位置蒙版没有 255 像素"
        elif not evidence:
            layer.empty = ("交接仅有文字结论，无法定位绘制像素" if self.places and not self.places.maps
                           else f"上游未提供{event}的空间证据")
        elif self.sim and name in ("积雪", "已有积雪") and not channels:
            layer.empty = "详细仿真缺少终态雪量"
        elif channels and not any(a.any() for a in channels):
            layer.empty = "详细仿真中该现象的有效通道全为零"
        return layer

    def render_layer(self, layer):
        scene, effect = self.scene, layer.name
        output = Image.new("RGBA", scene.size)
        drawn = allowed = 0
        if layer.empty:
            return output, 0, 0
        for top in range(0, scene.size[1], 160):
            bottom = min(top + 160, scene.size[1])
            band = slice(top, bottom)
            ids = scene.ids[band]
            valid = ~scene.protected[ids]
            material = scene.material[ids]
            depth = None
            if self.sim:
                uv = np.ix_(self.sy[band], self.sx)
                # A thin full-resolution region can disappear on the analysis grid.
                # An adjacent region's state or material never supplies its samples.
                aligned = scene.small_ids[uv] == ids
                if layer.channels:
                    valid &= aligned
                if self.kinds is not None:
                    material = np.where(aligned, self.kinds[uv], material)
                if self.depth is not None:
                    depth = self.depth[uv]
            valid &= material != AIR
            if effect == "积水反射" and self.pond is not None:
                valid &= (material != LIQUID) | self.pond[band]
            elif effect != "积水再冻结":
                valid &= material != LIQUID
            if layer.permission is not None:
                valid &= layer.permission[band] == 255
            if not self.sim and effect in ("积雪", "冻雨挂冰", "水膜", "湿色"):
                valid &= ~self.shelter[ids]
            amount = 1.
            if layer.channels:
                amount = np.maximum.reduce([a[uv] for a in layer.channels])
                valid &= amount > 0
            elif effect == "积雪":
                amount = SNOW[self.weather] * self.retention[ids]
            elif self.weather in RAIN:
                amount = RAIN[self.weather] * self.grade
            if not valid.any():
                continue
            allowed += int(valid.sum())
            surface = self.light.band(top, bottom, depth, material)
            if effect in ("积雪", "已有积雪"):
                rgb, alpha = surface.snow(amount)
            elif effect in WET_MAP:
                rgb, alpha = surface.water(effect, amount)
            else:
                rgb, alpha = surface.frozen(effect)
            # Last operation on alpha. Export at the upright native resolution.
            rgba = np.empty((*ids.shape, 4), np.uint8)
            rgba[..., 3] = np.where(valid, np.rint(unit(alpha)*255), 0).astype(np.uint8)
            rgba[..., :3] = np.rint(unit(rgb)*255).astype(np.uint8)
            visible = rgba[..., 3] > 0
            rgba[~visible] = 0
            output.paste(Image.fromarray(rgba), (0, top))
            drawn += int(visible.sum())
        if not drawn:
            layer.empty = "当前区块、许可和已确认条件下无可见像素"
        return output, allowed, drawn


def export(args, folder):
    renderer = Renderer(args)
    scene = renderer.scene
    composed = scene.photo.convert("RGBA")
    report = dict(schema="weather-material-renderer/v1", version=1, image_sha256=scene.file_hash,
                  manifest_sha256=scene.manifest_hash, region_pack_sha256=scene.signature,
                  answers_sha256=sha(args.answers), handoff_sha256=sha(args.handoff),
                  handoff_schema=SIMULATION if renderer.sim else POSITION,
                  size=list(scene.size), orientation=ORIENTATION, source_exif_orientation=scene.exif,
                  origin="top-left", axes=["right", "down"], weather=args.weather, seed=args.seed,
                  subject=scene.plan["subject"], motion=scene.plan["motion"], ground_snow=scene.plan["ground_snow"],
                  blend="source-over", alpha="straight", color_space="sRGB", layers=[],
                  preview=args.weather+"-预览.jpg")
    if args.places:
        report["places_sha256"] = sha(args.places)
    if renderer.sim:
        report["sample_seconds"] = renderer.sim.last["seconds"]
        report["initial_snow"] = renderer.sim.data["scenario"].get("old_snow", 0)
        report["snow_state"] = "终态存量（含仍然留存的初始雪）"
    for index, layer in enumerate(renderer.layers()):
        overlay, permitted, visible = renderer.render_layer(layer)
        path = f"{args.weather}-{layer.name}.png"
        overlay.save(folder / path)
        composed.alpha_composite(overlay)
        report["layers"].append(dict(order=index, phenomenon=layer.name, path=path, blend="source-over",
                                     evidence=layer.evidence, eligible_pixels=permitted, visible_pixels=visible,
                                     empty_reason=layer.empty or None))
    composed.convert("RGB").save(folder / report["preview"], quality=94, subsampling=0)
    (folder / "materials.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="根据已确认的天气方案，在原尺寸输出可叠加的表面材质")
    parser.add_argument("photo", type=Path)
    for arg in ("manifest", "handoff", "answers", "out"):
        parser.add_argument("--"+arg, required=True, type=Path)
    parser.add_argument("--places", type=Path, help="详细仿真可另加同分区的位置交接")
    parser.add_argument("--weather", required=True, choices=(*SNOW, *RAIN, *DRY, *EFFECTS, *SPECIAL, "结冰"))
    parser.add_argument("--seed", type=int, default=37)
    args = parser.parse_args()
    try:
        require(args.seed >= 0, "种子须为非负整数")
        require(not args.out.exists(), "请选择新的输出目录")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.out.parent) as tmp:
            stage = Path(tmp) / "package"
            stage.mkdir()
            export(args, stage)
            stage.rename(args.out)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"材质渲染失败：{exc}\n")
    print(args.out / "materials.json")


if __name__ == "__main__":
    main()
