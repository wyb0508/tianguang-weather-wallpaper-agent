"""Read the delivered photo, discrete maps and transparent material layers."""
import base64
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

NEAREST = Image.Resampling.NEAREST
PASS, FAIL, SKIP = "通过", "失败", "未测"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def contained(folder, name):
    p = (folder / name).resolve()
    if not p.is_relative_to(folder.resolve()):
        raise ValueError(f"路径超出交付目录 {name}")
    return p


def picture(path):
    with Image.open(path) as im:
        im.load()
        return im.copy()


def embedded(uri):
    prefix, data = uri.split(",", 1)
    if not prefix.startswith("data:image/") or not prefix.endswith(";base64"):
        raise ValueError("预期自包含图像")
    return picture(io.BytesIO(base64.b64decode(data, validate=True)))


def payload(path):
    text = path.read_text(encoding="utf-8")
    start = re.search(r'\bconst\s+data\s*=\s*(?=\{)', text)
    return json.JSONDecoder().raw_decode(text[start.end():])[0] if start else {}


def stats(mask):
    y, x = np.where(mask)
    return dict(count=len(x), first_xy=[int(x[0]), int(y[0])] if len(x) else None,
                bbox_xyxy=[int(x.min()), int(y.min()), int(x.max()), int(y.max())] if len(x) else None)


class Inspection:
    def __init__(self, work, out, args, command):
        self.work, self.out, self.args, self.command = work, out, args, command
        self.rows, self.files, self.docs, self.layers, self.maps = [], {}, {}, {}, {}
        self.photo = self.original = self.ids = self.parts = None
        self.part_names, self.region_names, self.meta = {}, {}, {}
        self.simulation = None
        self.data, self.manifest, self.plan = {}, {}, {}
        self.raw_hash = self.pixel_hash = self.signature = None
        self.entry = work / "壁纸预览.html"

    def row(self, item, status, expected, actual, evidence=None, steps=None):
        self.rows.append(dict(item=item, status=status, expected=expected, actual=actual,
                              evidence=evidence or ["inventory.json"], steps=steps or self.command))

    def check(self, item, good, expected, actual, evidence=None):
        self.row(item, PASS if good else FAIL, expected, actual, evidence)

    def step(self, label, func):
        try:
            return func()
        except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError, OverflowError) as e:
            self.row(label, FAIL, "声明的文件与字段完整可读", str(e))
            return None

    def locate(self, name, override=None):
        if override:
            path = override.resolve(strict=True)
        else:
            paths = sorted(p for p in self.work.rglob(name)
                           if not any(s.endswith(".app") for s in p.parts))
            if len(paths) != 1:
                self.row(name, SKIP, "找到唯一交接", "未提供" if not paths else f"有 {len(paths)} 份，需指定")
                return
            path = paths[0]
        value = read(path)
        if not isinstance(value,dict):
            raise ValueError(f"{name} 须为 JSON 对象")
        self.files[name] = path
        self.docs[name] = value
        return value

    def inputs(self):
        for name, arg in [("manifest.json", "manifest"), ("handoff.json", "handoff"),
                          ("天气方案.json", "plan"), ("materials.json", "materials"), ("motion.json", "motion")]:
            self.step(name, lambda: self.locate(name, getattr(self.args, arg)))
        self.manifest = self.docs.get("manifest.json", {})
        self.plan = self.docs.get("天气方案.json", {})
        if self.args.entry:
            self.entry = self.args.entry.resolve()
        elif not self.entry.exists() and self.docs.get('motion.json'):
            entry = self.docs['motion.json'].get('entry')
            if isinstance(entry, dict):
                entry = entry.get("preview")
            if isinstance(entry, str):
                self.entry = contained(self.files['motion.json'].parent, entry)
        if self.entry.is_file():
            self.data = self.step("HTML 内嵌图像清单", lambda: payload(self.entry)) or {}
            if 'photo' in self.data:
                self.photo = self.step("交付底图解码", lambda: embedded(self.data["photo"]).convert("RGB"))
        else:
            self.row("HTML 交付入口", SKIP, "可用本地 HTML", str(self.entry))
        motion=self.docs.get("motion.json",{})
        def load_motion_photo():
            path=contained(self.files["motion.json"].parent,motion["photo"])
            self.photo=self.step("交付动画底图",lambda:picture(path).convert("RGB"))
            if self.photo and list(self.photo.size)!=motion.get("size"):
                self.row("动画底图尺寸",FAIL,"与 motion.json 正向网格一致",list(self.photo.size))
            if path.is_file() and digest(path)==motion.get("image_sha256"):
                self.files["matching_photo"]=path
        if motion.get("photo"):
            self.step("动画底图",load_motion_photo)
        self.step("原图", self.source)
        if self.manifest:
            self.step("原尺寸类别与分组", self.regions)
        if "handoff.json" in self.docs:
            self.step("物理交接", self.physics)

    def source(self):
        name = self.manifest.get("source")
        source = self.args.source or self.files.get("matching_photo")
        if source is None and name:
            candidates = [self.work / Path(name).name, self.files["manifest.json"].parent / Path(name).name]
            source = next((p for p in candidates if p.is_file()), None)
        if source is None:
            self.row("原图文件 / EXIF / 方案像素指纹", SKIP, "原图字节、正向像素可复算",
                     "缺原始文件；仅使用预览中的底图" if self.photo else "未提供原图或内嵌底图")
            return
        self.files["original"] = source.resolve()
        self.raw_hash = digest(source)
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        with Image.open(source) as im:
            exif = im.getexif().get(274, 1)
            rgba = ImageOps.exif_transpose(im).convert("RGBA")
        self.original = Image.new("RGB",rgba.size,"#202a34")
        self.original.paste(rgba,mask=rgba.getchannel("A"))
        self.meta["source_size"] = list(rgba.size)
        if self.manifest:
            self.check("原图文件指纹、尺寸与方向", self.raw_hash == self.manifest["sha256"]
                       and list(rgba.size) == self.manifest["size"] and self.manifest["orientation"] == "exif-transposed",
                       "文件 SHA-256 和 EXIF 校正后尺寸与 manifest 相符",
                       dict(sha256=self.raw_hash, exif=exif, size=rgba.size, manifest=self.manifest["sha256"]))
        rgba.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
        photo = Image.new("RGB", rgba.size, "#202a34")
        photo.paste(rgba, mask=rgba.getchannel("A"))
        self.pixel_hash = hashlib.sha256(str(photo.size).encode() + photo.tobytes()).hexdigest()
        if self.plan.get("version") == 1:
            self.check("方案正向像素指纹", self.pixel_hash == self.plan.get("image_sha256"),
                       "按生成端缩放、透明底色与 RGB 字节复算", self.pixel_hash)
        if self.data.get("photo") and self.photo is not None:
            encoded = io.BytesIO()
            photo.save(encoded, "JPEG", quality=90)
            ref = np.asarray(picture(io.BytesIO(encoded.getvalue())), dtype=np.int16)
            same = photo.size == self.photo.size
            error = float(np.abs(ref-np.asarray(self.photo)).mean()) if same else None
            self.check("HTML 底图绑定", same and error <= 1, "同尺寸，按生成端 JPEG 质量 90 重编码后平均通道误差 ≤1",
                       dict(size=self.photo.size,mean_abs_error=error))

    def regions(self):
        m = self.manifest
        root = self.files["manifest.json"].parent
        for key in ("preview","region_preview"):
            if key in m:
                self.step("清单图像 "+key,lambda:picture(contained(root,m[key])))
        def grid(key):
            im = picture(contained(root, m[key]))
            if list(im.size) != m["size"] or im.mode not in ("L","I","I;16") or im.getexif().get(274,1)!=1:
                raise ValueError(f"{key} 尺寸、模式或方向错误")
            return im
        if not m.get("region_map") or not m.get("class_map"):
            self.row("原尺寸类别对应",SKIP,"有 region_map 和 class_map","旧分区缺少原始编号图")
            return
        reg = grid("region_map")
        ids = self.ids = np.asarray(reg)
        rows = {r["id"]: r for r in m["regions"]}
        self.region_names = {i:r["label"] for i,r in rows.items()}
        present, counts = np.unique(ids, return_counts=True)
        if set(present)!=set(rows) or len(rows)!=len(m["regions"]):
            raise ValueError("实际区块编号与清单不符")
        lut = np.zeros(int(ids.max())+1, np.uint16)
        for i,r in rows.items():
            lut[i] = r["class_id"]
        mismatch = stats(lut[ids]!=np.asarray(grid("class_map")))
        self.check("原始类别图及区块面积", not mismatch["count"] and all(rows[i]["pixels"]==n for i,n in zip(present,counts)),
                   "类别图逐像素等于 region→class_id，面积符合声明", mismatch)
        small = np.asarray(reg.resize(m["analysis_size"], NEAREST))
        signature = hashlib.sha256(lut[small].tobytes())
        groups = m["groups"]
        assigned = [i for g in groups for i in g["region_ids"]]
        self.check("分组覆盖", sorted(assigned)==sorted(rows) and len({g['key'] for g in groups})==len(groups),
                   "每个区块恰属一个分组，分组键唯一", dict(ids=assigned,total_pixels=ids.size))
        for g in sorted(groups, key=lambda g:g["key"]):
            name = g["key"]
            signature.update(name.encode()+b"\0"+np.packbits(np.isin(small,g["region_ids"])).tobytes())
            def verify_group():
                expected = np.isin(ids,g["region_ids"])
                actual = picture(contained(root,g["mask"]))
                if actual.size!=reg.size or actual.mode!="L":
                    raise ValueError("分组尺寸或模式错误")
                diff = stats(np.asarray(actual)!=expected.astype("uint8")*255)
                if diff["count"]:
                    x,y=diff["first_xy"]
                    diff["region"]=self.region_names[int(ids[y,x])]
                self.check("分组蒙版 "+name, diff["count"]==0 and g.get("pixels",int(expected.sum()))==expected.sum(),
                           f"{g['mask']} 逐像素等于区块 {g['region_ids']} 的并集", diff)
            self.step("分组蒙版 "+name, verify_group)
        self.signature = signature.hexdigest()
        if self.plan.get("region_pack_sha256"):
            self.check("方案分区指纹", self.signature==self.plan["region_pack_sha256"], "在分析网格复算分区与组掩膜签名", self.signature)
        if (self.work/"区块类别.png").exists():
            im = picture(self.work/"区块类别.png")
            equal = im.size==tuple(m["analysis_size"]) and np.array_equal(np.asarray(im),lut[small])
            self.check("生成端类别图",equal,"分析网格类别与交付区块类别一致",dict(size=im.size,equal=equal))

    def binding(self, name, key, expected, observed):
        if observed is not None:
            if expected is None:
                self.row(name+" "+key, SKIP, "与独立输入指纹一致", "缺少参照文件")
            else:
                self.check(name+" "+key,expected==observed,"与独立输入指纹一致",dict(expected=expected,observed=observed))

    def physics(self):
        h=self.docs["handoff.json"]
        root=self.files["handoff.json"].parent
        self.binding("物理","原图",self.raw_hash or self.manifest.get("sha256"),h.get("image_sha256"))
        self.binding("物理","分区",digest(self.files["manifest.json"]) if self.manifest else None,h.get("manifest_sha256"))
        self.binding("物理","分组",self.signature,h.get("region_pack_sha256"))
        if h.get("schema")=="weather-surface-physics/v1":
            self.simulation=h
            for key,path in h.get("maps",{}).items():
                im=picture(contained(root,path))
                if list(im.size)!=h["analysis_size"]:
                    raise ValueError("详细通道尺寸不符 "+key)
            if self.ids is not None:
                equal=np.array_equal(np.asarray(picture(contained(root,h["region_map"]))),self.ids)
                self.check("详细仿真区块图",equal,"仿真区块逐像素等于原尺寸分区",equal)
        if h.get("encoding")!={"0":"不会","127":"保守略过","255":"会"}:
            self.row("三值许可",SKIP,"交接声明 0/127/255 位置编码","详细仿真或文字交接；连续通道须单独解释")
            return
        size=self.manifest.get("size",h.get("locations",{}).get("size"))
        for key, filename in h.get("maps",{}).items():
            def load():
                im=picture(contained(root,filename))
                if list(im.size)!=size or im.mode!="L" or set(np.unique(im))- {0,127,255}:
                    raise ValueError(f"{filename} 尺寸、灰度或状态值不合法")
                self.maps[key]=im
            self.step("物理蒙版 "+key,load)
        places=h.get("locations",{})
        if "map" in places:
            self.parts=picture(contained(root,places["map"]))
            self.part_names={p["id"]:p["name"] for p in places["parts"]}

    def audit_layer(self, name, weather, effect, im):
        key=f"{name}/{weather}/{effect}"
        if "A" not in im.getbands():
            raise ValueError(f"{key} 缺少 alpha 通道")
        alpha=np.asarray(im.getchannel("A"))
        self.layers[key]=im
        h=self.docs.get("handoff.json",{})
        specific=weather if h.get("weather",{}).get(weather)==effect else effect
        permit=self.maps.get(specific)
        if permit is None and self.simulation and name=="材质":
            h=self.simulation
            seconds=self.docs["materials.json"].get("sample_seconds")
            state=next((r for r in h.get("keyframes",[]) if r.get("seconds")==seconds),{})
            fields={"湿润":["absorbed","film","contact"],"水膜":["film"],"积雪":["snow"],"已有积雪":["snow"]}.get(effect,[])
            channels=[]
            for field in fields:
                rel=(h["maps"] if field=="contact" else state.get("maps",{})).get(field)
                if rel:
                    grid=picture(contained(self.files["handoff.json"].parent,rel))
                    channels.append(np.asarray(grid.resize(im.size,NEAREST))>0)
            if channels and self.ids is not None and im.size==(self.ids.shape[1],self.ids.shape[0]):
                ids=Image.fromarray(self.ids)
                aligned=np.asarray(ids.resize(h["analysis_size"],NEAREST).resize(im.size,NEAREST))==self.ids
                bad=stats((alpha>0)&~(aligned & np.logical_or.reduce(channels)))
                self.check(key+" 连续通道",bad["count"]==0,"同区块、指定时刻的相关通道至少一个为正",
                           dict(seconds=seconds,outside=bad))
        if permit is None:
            self.row(key,SKIP,"与同现象物理许可逐像素比对",f"有 {int(np.count_nonzero(alpha))} 个非零 alpha 像素，缺少{effect}三值许可")
            return
        if abs(im.width/im.height-permit.width/permit.height)>max(2/im.height,2/permit.height):
            raise ValueError(f"{key} 与物理图纵横比不符")
        mask=np.asarray(permit.resize(im.size,NEAREST))
        details={str(k):stats((mask==k)&(alpha>0)) for k in (0,127)}
        evidence=["inventory.json"]
        bad=(mask!=255)&(alpha>0)
        if bad.any():
            index=len(self.rows)
            file=f"alpha-{index}.png"
            Image.fromarray(bad.astype("uint8")*255).save(self.out/file)
            evidence.append(file)
            for d in details.values():
                if d["count"]:
                    x,y=d["first_xy"]
                    if self.ids is not None:
                        region=Image.fromarray(self.ids).resize(im.size,NEAREST).getpixel((x,y))
                        d["region"]=self.region_names[region]
                    if self.parts:
                        d["part"]=self.part_names.get(self.parts.resize(im.size,NEAREST).getpixel((x,y)))
        self.check(key,not bad.any(),"0 区、127 区的非零 alpha 均为 0（最近邻许可，材质最终像素坐标）",
                   dict(size=im.size,nonzero=int(np.count_nonzero(alpha)),zones=details),evidence)

    def surfaces(self):
        d=self.data
        for effect,field in [('积雪','snowLayers'),('积水','puddles')]:
            layers=dict(d.get(field,{}))
            if effect=='积雪' and 'snow' in d:
                layers.setdefault('大雪',d['snow'])
            for w,uri in layers.items():
                self.step(f"{w}/{effect}",lambda:self.audit_layer("HTML",w,effect,embedded(uri)))
        if 'rain' in d:
            self.step("湿润",lambda:self.audit_layer("HTML","雨天","湿润",embedded(d['rain'])))
        if not self.layers and "materials.json" not in self.docs:
            self.row("材质 alpha",SKIP,"可分离的透明图层","没有可识别的透明材质")

    def newer(self):
        for name in ("materials.json", "motion.json"):
            if name not in self.docs:
                continue
            doc=self.docs[name]
            root=self.files[name].parent
            refs={"image_sha256":self.raw_hash or self.manifest.get("sha256"),
                  "region_pack_sha256":self.signature}
            for field,file in [("manifest_sha256","manifest.json"),("answers_sha256","天气方案.json"),("handoff_sha256","handoff.json")]:
                refs[field]=digest(self.files[file]) if file in self.files else None
            for key,value in refs.items():
                for section in [doc,doc.get("inputs",{})]:
                    if key in section:
                        self.binding(name,key,value,section[key])
                        if section is doc and section[key]==doc.get("inputs",{}).get(key):
                            break
            self.meta[name]={k:doc.get(k) for k in ("schema","size","orientation","seed","fps","time_unit","sample_seconds","layers")}
            if self.manifest:
                self.check(name+" 坐标",doc.get("size")==self.manifest["size"] and doc.get("orientation")==self.manifest["orientation"],
                           "图层使用正向原图尺寸与方向",self.meta[name]["size"])
            if name=="materials.json":
                order=[r["order"] for r in doc["layers"]]
                self.check("材质顺序",len(set(order))==len(order),"合成顺序无歧义",order)
                composite=self.original.convert("RGBA") if self.original is not None else None
                compatible=composite is not None and all(layer.get("blend",doc.get("blend")) in ("source-over","normal") for layer in doc["layers"])
                for layer in sorted(doc["layers"],key=lambda l:l["order"]):
                    def audit():
                        im=picture(contained(root,layer["path"]))
                        if list(im.size)!=doc["size"] or im.mode!="RGBA" or im.getexif().get(274,1)!=1:
                            raise ValueError("应为原尺寸正向 RGBA")
                        effect={"湿色":"湿润","积水反射":"积水"}.get(layer["phenomenon"],layer["phenomenon"])
                        if effect=="湿润" and "湿润" not in self.maps and "接触" in self.maps:
                            effect="接触"
                        self.audit_layer("材质",layer.get("weather",doc.get("weather","")),effect,im)
                        if compatible:
                            composite.alpha_composite(im)
                    self.step("材质 "+layer["path"],audit)
                if doc.get("preview"):
                    actual=picture(contained(root,doc["preview"])).convert("RGB")
                    if compatible and doc.get("schema")=="weather-material-renderer/v1":
                        encoded=io.BytesIO()
                        composite.convert("RGB").save(encoded,"JPEG",quality=94,subsampling=0)
                        ref=np.asarray(picture(io.BytesIO(encoded.getvalue())),dtype=np.int16)
                        mae=float(np.abs(ref-np.asarray(actual)).mean()) if actual.size==composite.size else None
                        self.check("材质顺序合成与交付预览",mae is not None and mae<=1,
                                   "依清单顺序合成并按导出质量编码，平均通道误差 ≤1",dict(mean_abs_error=mae,order=order))
            elif name=="motion.json":
                for layer in doc.get("materials",[]):
                    self.binding("动画材质",layer["path"],layer.get("sha256"),digest(contained(root,layer["path"])))
                inputs=doc.get("inputs",{})
                for k,file in [("physics","handoff.json"),("materials_sha256","materials.json")]:
                    if inputs.get(k) and file in self.files:
                        hashes=[v["sha256"] if isinstance(v,dict) else v for v in inputs[k]]
                        self.check("动画上游 "+file,digest(self.files[file]) in hashes,"交付清单指纹在动画输入中",hashes)
                entry=doc.get("entry",{})
                if isinstance(entry,dict):
                    for key in ("preview","module","data"):
                        if key in entry:
                            p=contained(root,entry[key])
                            self.check("动画资源 "+key,p.is_file() and p.stat().st_size>0,"入口资源齐全",str(p))
                    if entry.get("data"):
                        body=contained(root,entry["data"]).read_text()
                        prefix="window.weatherMotionScene="
                        if body.startswith(prefix):
                            config=json.JSONDecoder().raw_decode(body[len(prefix):])[0]["config"]
                            self.check("实际动画配置",config==doc,"入口加载的种子、采样、顺序及完整配置与 motion.json 一致",
                                       dict(seed=config.get("seed"),layers=config.get("layers")))
