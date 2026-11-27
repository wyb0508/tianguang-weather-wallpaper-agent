import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from engine.workflow import WEATHER, collect


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.photo = Image.new("RGB", (4, 3))
        self.regions = [{"label":key,"name":key} for key in ("car","floor","road")]

    def test_headless_requires_explicit_motion(self):
        with patch("sys.stdin.isatty", return_value=False), self.assertRaisesRegex(ValueError, "运动还是静止"):
            collect(self.photo, self.regions, None, "car", None)

    def test_interview_and_image_bound_replay(self):
        replies = ["floor=road", "", "car", "high_speed", "detail", "car", "wet"] + ["strong"]*len(WEATHER)
        with patch("sys.stdin.isatty",return_value=True), patch("builtins.input",side_effect=replies), patch("builtins.print"):
            plan = collect(self.photo, self.regions, None, None, None)
        self.assertEqual(plan["merge_into"], {"floor":"road"})
        self.assertEqual(plan["sheltered"], ["car"])
        self.assertEqual(set(plan["weather"].values()), {"strong"})
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdin.isatty",return_value=False):
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(plan))
            self.assertEqual(collect(self.photo, self.regions, path, None, None), plan)
            with self.assertRaisesRegex(ValueError,"图片不匹配"):
                collect(Image.new("RGB",(4,3),"white"), self.regions, path, None, None)
            with self.assertRaisesRegex(ValueError,"运动还是静止"):
                collect(self.photo, self.regions, path, "road", None)
