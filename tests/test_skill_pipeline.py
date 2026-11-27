import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from skill_pipeline import Placement, snow_layer


class PlacementIntegrationTests(unittest.TestCase):
    def test_aligned_three_state_mask_and_manifest_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            manifest = folder / "manifest.json"
            manifest.write_text(json.dumps({"sha256":"photo", "size":[3,1]}))
            path = folder / "handoff.json"
            path.write_text(json.dumps({
                "schema":"weather-accumulation-places/v1", "image_sha256":"photo",
                "manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "encoding":{"0":"不会","127":"保守略过","255":"会"}, "maps":{"积雪":"snow.png"}
            }))
            Image.fromarray(np.array([[0,127,255]],dtype=np.uint8)).save(folder/"snow.png")
            placement = Placement(path, manifest)
            gate = placement.mask("积雪",(6,2))
            alpha = snow_layer(np.ones((1,2)), gate).getchannel("A")
            np.testing.assert_array_equal(np.asarray(alpha)[0], [0,0,0,0,255,255])
            manifest.write_text(manifest.read_text()+"\n")
            with self.assertRaisesRegex(ValueError,"不匹配"):
                Placement(path, manifest)
