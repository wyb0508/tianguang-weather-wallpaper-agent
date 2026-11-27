import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from engine import vision


class ExportAlignmentTests(unittest.TestCase):
    def test_oriented_masks_and_classes_use_the_same_pixel_grid(self):
        with patch.dict(sys.modules, vision=vision):
            segment = importlib.import_module("skills.scene-regions.scripts.segment")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "portrait.jpg"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (941, 1672)).save(source, exif=exif)
            small = Image.new("RGB", (1024, 576))
            y, x = np.indices((576, 1024))
            labels = np.where((x + y) % 3 == 0, 20, 6).astype(np.uint8)
            scene = small, labels, {"20": "car", "6": "road"}
            out = Path(tmp) / "pack"
            with patch.object(segment, "predict_scene", return_value=(scene, (labels, labels))):
                path = segment.export(source, out, "car", [], [])
            data = json.loads(path.read_text())
            self.assertEqual(data["size"], [1672, 941])
            ids = np.array(Image.open(out / data["region_map"]))
            raw = np.array(Image.open(out / data["class_map"]))
            for row in data["regions"]:
                self.assertTrue(np.all(raw[ids == row["id"]] == row["class_id"]))
            for group in data["groups"]:
                mask = np.array(Image.open(out / group["mask"]))
                expected = np.isin(ids, group["region_ids"])
                np.testing.assert_array_equal(mask, expected * 255)
                self.assertEqual(int(expected.sum()), group["pixels"])

    def test_review_and_corrections_respect_group_and_polygon_boundaries(self):
        with patch.dict(sys.modules, vision=vision):
            segment = importlib.import_module("skills.scene-regions.scripts.segment")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            labels = np.array([[3, 6, 20], [3, 6, 20]], dtype=np.uint8)
            names = {"3": "floor", "6": "road", "20": "car", "32": "fence"}
            regions = [{"id": i, "class_id": cid} for i, cid in enumerate((3, 6, 20), 1)]
            groups = [{"id": 1, "region_ids": [1, 2]}, {"id": 2, "region_ids": [3]}]
            alternate = np.array([[6, 3, 6], [3, 6, 20]], np.uint8)
            review = segment.write_review(folder, Image.new("RGB", (3, 2)), (labels, alternate), regions, groups)
            self.assertEqual(review["analysis_pixels"], 1)
            path = folder / "corrections.json"
            path.write_text(json.dumps({"sha256": "image", "analysis_size": [3, 2],
                "edits": [{"from": ["road"], "to": "fence", "polygon": [[1, 0], [2, 0], [2, 1]]}]}))
            fixed, stats = segment.correct_labels(labels, names, path, "image")
            self.assertEqual(stats["changed_pixels"], 1)
            np.testing.assert_array_equal(fixed, [[3, 32, 20], [3, 6, 20]])
            with self.assertRaisesRegex(ValueError, "匹配"):
                segment.correct_labels(labels, names, path, "different")

    def test_ground_union_respects_subject_overrides_and_caption(self):
        with patch.dict(sys.modules, vision=vision):
            segment = importlib.import_module("skills.scene-regions.scripts.segment")
        labels = ["road", "sidewalk", "grass", "earth", "path", "tree", "car", "sky", "river"]
        rows = [dict(id=i, label=label, key=label, class_id=i)
                for i, label in enumerate(labels, 1)]
        base = segment.group_regions(rows, [], has_subject=False)
        self.assertEqual(base["ground"], [1, 2, 3, 4, 5])
        self.assertEqual(base["land"], [6])
        override = segment.group_regions(rows, ["track=road"], has_subject=True)
        self.assertEqual(override["ground"], [2, 3, 4, 5])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "photo.png"
            photo = Image.new("RGB", (9, 6))
            photo.save(source)
            raw = np.array([list(range(1, 10))] * 5 + [[1] * 9], np.uint8)
            names = {str(row["id"]): row["label"] for row in rows}
            out = Path(tmp) / "pack"
            with patch.object(segment, "predict_scene", return_value=((photo, raw, names), None)):
                segment.export(source, out, "grass", [], [], footer_start=5)
            ground = np.asarray(Image.open(out / "masks/ground.png")) > 0
            expected = np.isin(raw, [1, 2, 4, 5])
            expected[-1] = False
            np.testing.assert_array_equal(ground, expected)
            np.testing.assert_array_equal(np.asarray(Image.open(out / "classes.png")), raw)

    def test_edge_refinement_recovers_body_and_releases_background(self):
        with patch.dict(sys.modules, vision=vision):
            boundary = importlib.import_module("skills.scene-regions.scripts.boundaries")
        truth = np.zeros((200, 280), bool)
        truth[45:155, 40:235] = True
        # Width/position errors on both vertical sides of one opaque subject.
        base = np.zeros_like(truth)
        base[45:155, 43:238] = True
        truth = truth.repeat(2, axis=0).repeat(2, axis=1)
        base = base.repeat(2, axis=0).repeat(2, axis=1)
        ids = np.where(base, 1, 2).astype(np.uint16)
        rgb = np.zeros((*truth.shape, 3), np.uint8)
        rgb[truth] = (210, 165, 160)
        photo = Image.fromarray(rgb)
        def predict(_, pixels):
            return (pixels[0, :1] > 0).astype(np.float32)
        with patch.object(boundary, "infer", side_effect=predict):
            result = boundary.refine_groups(photo, ids, {"subject": [1], "ground": [2]})
            protected = boundary.refine_groups(photo, ids, {"subject": [1], "ground": [2]},
                                              [{"polygon": [[70, 120], [100, 120], [100, 200], [70, 200]]}])
        predicted = result == 1
        self.assertLess(np.count_nonzero(predicted & ~truth), np.count_nonzero(base & ~truth))
        self.assertLess(np.count_nonzero(truth & ~predicted), np.count_nonzero(truth & ~base))
        np.testing.assert_array_equal(protected[121:200, 71:100], ids[121:200, 71:100])
        self.assertTrue(predicted[96:304, 96:456].all())

    def test_boundary_vote_keeps_a_second_subject_and_works_without_subject(self):
        refiners = importlib.import_module("skills.scene-regions.scripts.refiners")
        owners = np.full((30, 60), 2, np.uint8)
        owners[5:20, 5:20] = 1
        owners[8:12, 37:45] = 1
        slim = owners.copy()
        slim[:, 30:] = 2
        cascade = slim.copy()
        result = refiners.fuse_groups(owners, slim, cascade, ["subject", "ground"])
        np.testing.assert_array_equal(result, owners)
        result = refiners.fuse_groups(owners, slim, cascade, ["land", "ground"])
        np.testing.assert_array_equal(result, cascade)

    def test_review_masks_use_oriented_source_pixels_and_verified_image(self):
        review = importlib.import_module("skills.scene-regions.scripts.review_masks")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            ids = np.array([[1, 1, 3], [2, 2, 3]], dtype=np.uint16)
            maps = {"ground": [1, 2], "caption": [3]}
            Image.fromarray(np.array([[0, 0, 255], [0, 0, 0]], np.uint8)).save(folder / "ink.png")
            edits = folder / "edits.json"
            edits.write_text(json.dumps(dict(sha256="right", edits=[dict(group="ground", mask="ink.png")])))
            changed, report = review.apply_review(edits, "right", Image.fromarray(ids), maps)
            np.testing.assert_array_equal(np.array(changed), [[1, 1, 1], [2, 2, 3]])
            self.assertEqual(report[0]["changed_pixels"], 1)
            with self.assertRaisesRegex(ValueError, "指纹"):
                review.apply_review(edits, "wrong", Image.fromarray(ids), maps)
