"""Local image segmentation and weather material preparation."""
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter, ImageOps
from pillow_heif import register_heif_opener

ROOT = Path(__file__).resolve().parents[1]
MEAN = np.array([.485, .456, .406], dtype=np.float32)
STD = np.array([.229, .224, .225], dtype=np.float32)
GROUPS = {
    "sky": "sky",
    "greenery": "tree grass plant palm field flower",
    "ground": "road sidewalk earth path sand runway land stairs stairway step",
    "water": "water sea river lake waterfall",
    "structure": "wall building house bridge skyscraper tower fence railing",
    "terrain": "mountain hill rock",
}
# Chinese labels and inspector colours use the same keys as the renderer.
LEGEND = {
    "subject": ("主体", "#ff995e"), "sky": ("天空", "#679de5"),
    "greenery": ("植被", "#63a873"), "ground": ("地面", "#cfa956"),
    "water": ("水面", "#4fbfc3"), "structure": ("建筑", "#ac91cc"),
    "terrain": ("山石", "#bd8976"), "other": ("其他", "#747b80"),
}


def load_photo(path):
    register_heif_opener()
    with Image.open(path) as src:
        img = ImageOps.exif_transpose(src).convert("RGBA")
        img.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
        base = Image.new("RGB", img.size, "#202a34")
        base.paste(img, mask=img.getchannel("A"))
        return base


def infer(model, tensor):
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 3
    session = ort.InferenceSession(str(ROOT / 'models' / model), opts,
                                   providers=["CPUExecutionProvider"])
    return session.run([session.get_outputs()[0].name],
                       {session.get_inputs()[0].name: tensor})[0][0]


def tensor(photo, size, relative=False):
    a = np.asarray(photo.resize((size, size), Image.Resampling.BILINEAR), dtype=np.float32)
    a /= max(float(a.max()), 1) if relative else 255.
    return np.ascontiguousarray(((a - MEAN) / STD).transpose(2, 0, 1)[None])


def grayscale(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype('uint8'))


def soften(a, radius):
    return np.asarray(grayscale(a).filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255


def analyze(photo):
    small = photo.copy()
    small.thumbnail((1024, 1024))
    w, h = small.size
    names = json.loads((ROOT / 'models/classes.json').read_text())['id2label']
    logits = infer("scene.onnx", tensor(photo, 512))
    # Compare logits after interpolation to preserve boundaries of small classes.
    best = np.full((h,w), -np.inf, dtype=np.float32)
    labels = np.zeros((h,w), dtype=np.uint8)
    for idx, plane in enumerate(logits):
        scores = np.asarray(Image.fromarray(plane).resize((w,h), Image.Resampling.BILINEAR))
        take = scores > best
        best[take], labels[take] = scores[take], idx
    raw = infer("foreground.onnx", tensor(photo, 320, relative=True))[0]
    fg = np.asarray(grayscale(raw).resize((w,h), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    obj_ids = [int(i) for i,n in names.items() if n.strip() in
               {"person","car","truck","bus","animal","boat","airplane","bicycle","flower","minibike","train","ship","van"}]
    obj = np.isin(labels, obj_ids)
    binary = fg > .5
    if binary.mean() < .008 or binary.mean() > .94:
        fg = soften(obj, 1) if .008 < obj.mean() < .94 else np.zeros_like(fg)
    subject = fg > .5
    subject_labels = np.bincount(labels[subject], minlength=len(names))
    top = int(subject_labels.argmax())
    subject_name = names[str(top)].strip() if top in obj_ids and subject_labels[top] > .30 * subject.sum() else None

    sky_id = next(int(i) for i,n in names.items() if n.strip() == "sky")
    outdoor = (labels == sky_id).mean() > .05
    masks = {}
    for key, group in GROUPS.items():
        valid = set(group.split())
        if key == 'water': valid.add('swimming pool')
        if key == 'ground':
            valid.add('dirt track')
            if outdoor:
                valid.add('floor')
        ids = [int(i) for i,n in names.items() if n.strip() in valid]
        masks[key] = soften(np.isin(labels, ids), max(.55, w/1250)) * (1-fg)
    masks["subject"] = fg
    masks["other"] = np.clip(1-sum(masks.values()),0,1)

    # Directional deposition: upper silhouette, internal upward ledges, surface texture.
    rgb = np.asarray(small,dtype=np.float32)/255
    lum = rgb @ np.array([.2126,.7152,.0722],dtype=np.float32)
    depth = max(2,round(h*.018))
    above = np.pad(fg, ((depth,0),(0,0)), mode='edge')[:-depth]
    rim = soften(np.clip(fg-above,0,1), max(.7,h/750))
    occupied = fg>.45
    y = np.arange(h)[:,None]
    height = np.maximum(1, h-1-occupied[::-1].argmax(axis=0)-occupied.argmax(axis=0))
    near_top = np.exp(-np.maximum(0,y-occupied.argmax(axis=0))/(height*.19+1))
    ledges = np.clip((lum-soften(lum, max(1,w/145)))*4.5,0,1)
    rng = np.random.default_rng(73)
    noise = rng.uniform(.52,1,(h,w))
    subject_snow = fg*(.78*rim + .44*near_top*(.3+lum*.7)+.36*ledges*near_top)
    if subject_name in ('person','animal'): subject_snow *= .68
    surface = (subject_snow + masks['ground']*(.32+.42*lum) +
               masks['greenery']*(.12+.8*ledges) + masks['terrain']*(.18+.67*ledges) +
               masks['structure']*.48*ledges)
    snow = np.clip(surface*noise,0,1)
    rain = np.clip(.50*masks['sky'] + .39*masks['ground'] + .25*masks['water']+
                   .14*masks['greenery']+.12*masks['subject']+.12*masks['structure'],0,1)
    debug = np.zeros_like(rgb)
    for key,(_,color) in LEGEND.items():
        debug += masks[key][...,None]*np.array(
                    [int(color[i:i+2],16)/255 for i in (1,3,5)],dtype=np.float32)
    inspector = Image.fromarray((np.clip(.30*rgb+.70*debug,0,1)*255).astype('uint8'))
    ys,xs = np.where(masks['water']>.75)
    points = []
    if len(xs) >= max(16, round(w*h*.003)):
        for i in rng.choice(len(xs),size=min(80,len(xs)),replace=False):
            points.append([round(float(xs[i]+.5)/w,5), round(float(ys[i]+.5)/h,5)])
    coverage={k:round(float(v.mean()),4) for k,v in masks.items()}
    report={"subject_class":subject_name,"coverage":coverage,
            "scene_classes":{names[str(i)].strip():round(float((labels==i).mean()),4)
                             for i in np.unique(labels) if (labels==i).mean()>=.008},
            "water_points":points, "size":list(photo.size)}
    return masks, snow, rain, inspector, report
