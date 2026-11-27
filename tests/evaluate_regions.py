"""Evaluate vehicle, road and sky IoU on local SegNet CamVid test/testannot PNG pairs.

Run from the project as PYTHONPATH=. .venv/bin/python tests/evaluate_regions.py CAMVID
Optional --stride 20 --offset 10 selects a systematic subset from the sorted test set.
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from engine.vision import ROOT, load_photo, predict_scene


def evaluate(folder, stride, offset):
    frames = sorted((folder / "test").glob("*.png"))[offset::stride]
    if not frames:
        raise ValueError("没有找到测试图片")
    classes = (("车辆", 8, [20, 80, 83, 102]), ("道路", 3, [6]), ("天空", 0, [2]))
    counts = np.zeros((2, len(classes), 2), dtype=np.int64)
    for frame in frames:
        with Image.open(folder / "testannot" / frame.name) as image:
            reference = np.array(image)
        small_scene, (fast, _) = predict_scene(load_photo(frame), ROOT / "skills/scene-regions/models/scene-detail.onnx")
        precise = small_scene[1]
        if reference.shape != precise.shape:
            raise ValueError(f"标注尺寸不匹配：{frame.name}")
        for mode, prediction in enumerate((fast, precise)):
            for k, (_, camvid_id, ade_ids) in enumerate(classes):
                target = reference == camvid_id
                mask = np.isin(prediction, ade_ids) & (reference != 11)
                counts[mode, k] += ((target & mask).sum(), (target | mask).sum())
    scores = 100 * counts[:, :, 0] / counts[:, :, 1]
    print(f"{len(frames)} 张；各类按全部有效像素累计 IoU，忽略 CamVid ID 11。")
    for i, (name, _, _) in enumerate(classes):
        print(f"{name} {scores[0,i]:.2f} -> {scores[1,i]:.2f}")
    print("三类宏平均", scores.mean(axis=1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    evaluate(args.folder, args.stride, args.offset)
