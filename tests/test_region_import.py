import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from engine.analysis import analyze
from engine.region_import import import_regions
from engine.vision import load_photo
from engine.workflow import collect, draft
import hashlib


class RegionHandoffTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.folder = Path(work.name)
        self.source = self.folder / "scene.png"
        Image.new("RGB", (12, 8), "gray").save(self.source)
        self.photo = load_photo(self.source)
        labels = np.full((8, 12), 3, np.uint8)  # floor, reviewed as track
        labels[:, :4] = 20
        regions = np.where(labels == 20, 1, 2).astype(np.uint16)
        Image.fromarray(labels).save(self.folder / "classes.png")
        Image.fromarray(regions).save(self.folder / "regions.png")
        self.data = dict(schema="scene-regions/v1", sha256=hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         size=[12, 8], analysis_size=[12, 8], region_map="regions.png", class_map="classes.png",
                         regions=[dict(id=1, class_id=20, label="car"), dict(id=2, class_id=3, label="floor")],
                         groups=[dict(key="subject", region_ids=[1]), dict(key="track", region_ids=[2])])
        self.manifest = self.folder / "manifest.json"

    def read_pack(self):
        self.manifest.write_text(json.dumps(self.data))
        return import_regions(self.manifest, self.source, self.photo)

    def test_masks_drive_weather_and_preserve_raw_class(self):
        scene, groups, signature = self.read_pack()
        plan = draft(self.photo, signature)
        plan.update(subject="group:subject", motion="stationary")
        blank = np.zeros((8, 12), np.float32)
        with patch("engine.analysis.geometry", return_value=(blank, blank, blank + 1)):
            masks, _, rain, _, _ = analyze(self.photo, scene, plan, self.folder, groups)
        self.assertEqual(int(scene[1][4, 8]), 3)
        self.assertGreater(masks["subject"][4, 1], .95)
        self.assertLess(masks["subject"][4, 8], .01)
        self.assertGreater(rain[4, 8], .45)

    def test_reject_mismatched_source_and_overlapping_groups(self):
        self.data["groups"][0]["region_ids"].append(2)
        with self.assertRaisesRegex(ValueError, "恰好"):
            self.read_pack()
        self.data["sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "指纹"):
            self.read_pack()

    def test_saved_plan_bound_to_masks_and_no_subject_has_no_motion(self):
        _, groups, signature = self.read_pack()
        regions = [dict(label="car", name="汽车")]
        plan = collect(self.photo, regions, None, "none", None, groups, signature)
        self.assertIsNone(plan["motion"])
        saved = self.folder / "plan.json"
        saved.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "匹配"):
            collect(self.photo, regions, saved, None, None, groups, "changed")
