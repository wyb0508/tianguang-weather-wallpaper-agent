import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_gate", ROOT/"skills/wallpaper-qa/scripts/release_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ReleaseTests(unittest.TestCase):
    def test_stale_ground_setting_rejected_before_material_copy(self):
        scene = ROOT/"skills/weather-material-renderer/examples/race"
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            plan = json.loads((scene/"high_speed.json").read_bytes())
            plan["ground_snow"] = "wet"
            answers = out/"wet.json"
            answers.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
            cmd = [sys.executable, ROOT/"skills/weather-particle-motion/scripts/build.py", scene/"photo.jpg",
                   "--manifest",scene/"regions/manifest.json","--answers",answers,
                   "--materials",scene/"fast/materials.json","--out",out/"result"]
            run = subprocess.run(cmd,capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            self.assertIn("不同的天气方案",run.stderr)
            self.assertFalse((out/"result").exists())

    def test_sheltered_car_has_no_permitted_full_resolution_pixels(self):
        scene = ROOT/"skills/weather-material-renderer/examples/race"
        manifest = scene/"regions/manifest.json"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = gate.read(scene/"high_speed.json")
            plan.update(sheltered=["car"], ground_snow="wet")
            answers = root/"answers.json"
            answers.write_text(json.dumps(plan))
            subprocess.run([sys.executable, ROOT/"skills/weather-surface-physics/scripts/placement.py",
                            scene/"photo.jpg", "--manifest",manifest,"--answers",answers,"--out",root/"places"],
                           check=True, capture_output=True)
            places = gate.read(root/"places/handoff.json")
            data = gate.read(manifest)
            car_ids = [r["id"] for r in data["regions"] if r["label"]=="car"]
            with Image.open(root/"places"/places["maps"]["积雪"]) as mask, Image.open(manifest.parent/data["region_map"]) as ids:
                self.assertFalse((np.asarray(mask)[np.isin(np.asarray(ids),car_ids)]==255).any())

    def test_geometry_samples_measure_rendered_pixels(self):
        ref = dict(samples=[dict(name="轮侧",box=[0,0,2,2],expect="clear"),
                            dict(name="露天路面",box=[0,2,2,3],expect="some")])
        alpha = np.zeros((3,2),np.uint8)
        with self.assertRaisesRegex(ValueError,"露天路面"):
            gate.geometry(alpha,ref,{})
        alpha[2,0] = 36
        self.assertEqual(gate.geometry(alpha,ref,{})[1]["visible"],1)
        alpha[0,0] = 1
        with self.assertRaisesRegex(ValueError,"轮侧"):
            gate.geometry(alpha,ref,{})

    def test_shelter_only_exempts_covered_pixels_in_reference(self):
        ref = dict(samples=[dict(name="露天路面",box=[1,0,3,1],expect="some",unless_sheltered=True)])
        alpha = np.zeros((1,3),np.uint8)
        shelter = np.array([[True,True,False]])
        with self.assertRaisesRegex(ValueError,"露天路面"):
            gate.geometry(alpha,ref,{"sheltered":["car"]},shelter)
        alpha[0,1] = 20
        with self.assertRaises(ValueError):
            gate.geometry(alpha,ref,{"sheltered":["car"]},shelter)
        alpha[0,2] = 30
        self.assertEqual(gate.geometry(alpha,ref,{},shelter)[0]["visible"],1)
        self.assertIn("exempt",gate.geometry(alpha,ref,{},np.ones_like(shelter))[0])

    def test_missing_delivery_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"release.json").write_text(json.dumps(dict(schema="tianguang-release/v1",files={"lost.png":"abc"})))
            self.assertEqual(gate.audit(root,root/"qa"),"failed")
