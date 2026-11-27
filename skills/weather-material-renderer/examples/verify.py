"""Black-box checks against the two bundled photos. Writes only to a temporary directory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
RUN = HERE.parent / "run.sh"
CASES = [
    ("race", "小雪", "stationary", "places", "light"),
    ("race", "大雪", "stationary", "places", "heavy"),
    ("race", "大雪", "high_speed", "places", "fast"),
    ("race", "大雨", "high_speed", "rain", "wet"),
    ("oak", "大雪", "stationary", "places", "snow"),
]


def read(path):
    return json.loads(path.read_bytes())


def file_hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


def render(case, out, *, answers=None, handoff=None, weather=None, success=True):
    scene, grade, plan, pack, _ = case
    src = HERE / scene
    args = [RUN, src/"photo.jpg", "--manifest", src/"regions/manifest.json",
            "--handoff", handoff or src/pack/"handoff.json", "--answers", answers or src/(plan+".json"),
            "--weather", weather or grade, "--seed", "37", "--out", out]
    if pack == "rain":
        args += ["--places", src/"places/handoff.json"]
    result = subprocess.run(args, capture_output=True, text=True)
    assert (result.returncode == 0) == success, result.stderr
    if success:
        return read(out/"materials.json")
    assert not out.exists()


def alpha(folder, layer):
    with Image.open(folder/layer["path"]) as im:
        assert im.mode == "RGBA"
        a = np.array(im.getchannel("A"))
    assert np.count_nonzero(a) == layer["visible_pixels"]
    return a


def audit(a, mask):
    counts = {str(v): {"pixels": int(np.sum(mask == v)), "nonzero_alpha": int(np.count_nonzero(a[mask == v]))}
              for v in (0, 127, 255)}
    assert not counts["0"]["nonzero_alpha"] and not counts["127"]["nonzero_alpha"]
    return counts


def run(tmp):
    results, layers = {}, {}
    for case in CASES:
        scene, grade, _, _, key = case
        folder = tmp/key
        report = render(case, folder)
        # Compare the full set of shipped outputs with a fresh CLI invocation.
        assert file_hashes(folder) == file_hashes(HERE/scene/key)
        loc = HERE/scene/"places/位置"
        if key == "wet":
            road = np.array(Image.open(HERE/"race/regions/classes.png")) == 6
            wet, film, pool = [alpha(folder, layer) for layer in report["layers"]]
            assert wet[road].any() and film[road].any() and not pool.any()
            results[key] = dict(road_wet_pixels=int(np.count_nonzero(wet[road])),
                                road_film_pixels=int(np.count_nonzero(film[road])),
                                puddle=audit(pool, np.array(Image.open(loc/"积水.png"))))
        else:
            a = alpha(folder, report["layers"][0])
            results[key] = dict(snow=audit(a, np.array(Image.open(loc/"积雪.png"))))
            if scene == "race":
                classes = np.array(Image.open(HERE/"race/regions/classes.png"))
                results[key]["mean_alpha_0_255"] = {label: round(float(a[classes == cid].mean()), 3)
                                                     for label, cid in [("car", 20), ("road", 6), ("grass", 9), ("tree", 4)]}
                layers[key] = a
    car = classes == 20
    assert layers["fast"][car].sum() < layers["heavy"][car].sum()
    assert np.array_equal(layers["heavy"][~car], layers["fast"][~car])
    prev = layers["light"]
    stats = []
    for w in ("小雪", "中雪", "大雪", "暴雪"):
        if w in ("小雪", "大雪"):
            a = layers["light" if w == "小雪" else "heavy"]
        else:
            folder = tmp/w
            a = alpha(folder, render(CASES[0], folder, weather=w)["layers"][0])
        assert np.all(a >= prev)
        stats.append(dict(weather=w, visible=int(np.count_nonzero(a)), mean_alpha=round(float(a.mean()), 3)))
        prev = a
    results["snow_grades"] = stats
    for effect in ("结霜", "凝露", "冻雨挂冰", "积水再冻结", "冰雹堆积"):
        folder = tmp/effect
        layer = render(CASES[-1], folder, weather=effect)["layers"][0]
        results[effect] = audit(alpha(folder, layer), np.array(Image.open(HERE/"oak/places/位置"/(effect+".png"))))
        assert bool(layer["empty_reason"]) == (layer["visible_pixels"] == 0)

    # The plan's appearance gain controls airborne snow only. All surface bytes stay identical.
    plan = read(HERE/"race/stationary.json")
    plan["weather"]["大雪"] = "strong"
    altered = tmp/"gain.json"
    altered.write_text(json.dumps(plan))
    variant = tmp/"gain"
    a = alpha(variant, render(CASES[1], variant, answers=altered)["layers"][0])
    assert np.array_equal(a, layers["heavy"])
    render(CASES[1], tmp/"wrong-photo", answers=HERE/"oak/stationary.json", success=False)
    render(CASES[3], tmp/"wrong-weather", weather="小雨", success=False)
    render(CASES[3], tmp/"wrong-motion", answers=HERE/"race/stationary.json", success=False)
    h = read(HERE/"race/places/handoff.json")
    h.pop("maps")
    text_only = tmp/"text-only.json"
    text_only.write_text(json.dumps(h))
    layer = render(CASES[0], tmp/"text", handoff=text_only)["layers"][0]
    assert layer["empty_reason"] and not alpha(tmp/"text", layer).any()
    h["manifest_sha256"] = "0"*64
    text_only.write_text(json.dumps(h))
    render(CASES[0], tmp/"wrong-manifest", handoff=text_only, success=False)

    # Upstream may keep the caller's alias as a map name.
    h = read(HERE/"oak/places/handoff.json")
    h["weather"], h["maps"] = {"霜": "结霜"}, {"霜": "frost.png"}
    (tmp/"frost.png").write_bytes((HERE/"oak/places/位置/结霜.png").read_bytes())
    alias = tmp/"alias.json"
    alias.write_text(json.dumps(h))
    folder = tmp/"alias"
    layer = render(CASES[-1], folder, handoff=alias, weather="结霜")["layers"][0]
    assert results["结霜"] == audit(alpha(folder, layer), np.array(Image.open(tmp/"frost.png")))
    results["repeat_identical_packages"] = len(CASES)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as tmp:
        result = json.dumps(run(Path(tmp)), ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(result, encoding="utf-8")
    print(result)
