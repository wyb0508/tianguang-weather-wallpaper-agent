"""Exercise EXIF registration and channel semantics on a small controlled handoff."""
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageOps

RUN = Path(__file__).resolve().parents[1] / "run.sh"


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def check(d):
    # A sideways stored source exceeds the interview decoder's 3200-pixel cap.
    raw = Image.new("RGB", (3400, 86), (74, 84, 96))
    exif = Image.Exif()
    exif[274] = 6
    photo = d/"photo.jpg"
    raw.save(photo, exif=exif)
    with Image.open(photo) as src:
        original = ImageOps.exif_transpose(src).convert("RGBA")
    size = original.size
    original.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
    small = original.convert("RGB")
    pixel_hash = hashlib.sha256(str(small.size).encode() + small.tobytes()).hexdigest()
    small.thumbnail((1024, 1024))
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    ids = np.ones(size[::-1], np.uint8)
    ids[:, 18] = 2  # Native one-pixel strand absent at many analysis sample centres.
    ids[-150:] = 3
    classes = np.choose(ids-1, [20, 4, 43]).astype(np.uint8)
    Image.fromarray(ids).save(d/"ids.png")
    Image.fromarray(classes).save(d/"classes.png")
    write(d/"manifest.json", dict(schema="scene-regions/v1", sha256=sha(photo), orientation="exif-transposed",
          size=size, analysis_size=small.size, region_map="ids.png", class_map="classes.png",
          regions=[dict(id=i+1, class_id=c, label=k) for i, (c, k) in enumerate([(20,"car"),(4,"tree"),(43,"signboard")])],
          groups=[dict(key=k, region_ids=[i+1]) for i, k in enumerate(["subject","background","caption"])]))
    plan = dict(version=1, image_sha256=pixel_hash, subject="group:subject", motion="high_speed", merge_into={},
                sheltered=[], ground_snow="natural", accumulation="fixed_per_intensity",
                weather={"大雪":"standard", "大雨":"standard"})
    write(d/"answers.json", plan)
    base = dict(image_sha256=sha(photo), manifest_sha256=sha(d/"manifest.json"))
    permit = np.full(size[::-1], 255, np.uint8)
    permit[:700] = 127
    permit[:, :9] = 0
    Image.fromarray(permit).save(d/"permit.png")
    places = dict(**base, schema="weather-accumulation-places/v1", locations=dict(size=size, orientation="exif-transposed"),
                  encoding={"0":"不会","127":"保守略过","255":"会"},
                  maps={k:"permit.png" for k in ["积雪","积水","冰雹堆积"]})
    write(d/"places.json", places)
    # Positive snow already incorporates motion and exposure. Leave zero-state
    # islands on the simulation grid with an otherwise permissive native mask.
    stock = np.full(small.size[::-1], 255, np.uint8)
    stock[510:618] = 0
    Image.fromarray(stock).save(d/"state.png")
    detail = dict(**base, schema="weather-surface-physics/v1", size=size, analysis_size=small.size,
                  region_map="ids.png", scenario=dict(weather="大雪", old_snow=0, plan=plan),
                  maps={}, state_scales={"snow":.6}, keyframes=[dict(seconds=600, maps={"snow":"state.png"})])
    def run(handoff, weather, key, extra=(), expected=True):
        out = d/key
        proc = subprocess.run([RUN, photo,"--manifest",d/"manifest.json","--answers",d/"answers.json",
                               "--handoff",d/handoff,"--weather",weather,"--out",out,*extra], capture_output=True)
        assert (proc.returncode == 0) == expected, proc.stderr
        if not expected:
            assert not out.exists()
            return
        report = json.loads((out/"materials.json").read_bytes())
        assert report["size"] == list(size) and report["source_exif_orientation"] == 6
        return {r["phenomenon"]: np.array(Image.open(out/r["path"]).getchannel("A")) for r in report["layers"]}

    write(d/"detail.json", detail)
    both = ["--places",d/"places.json"]
    snow = run("detail.json", "大雪", "fast", both)["积雪"]
    nearest = Image.Resampling.NEAREST
    state = np.array(Image.fromarray(stock).resize(size, nearest))
    small_ids = Image.fromarray(ids).resize(small.size, nearest)
    aligned = np.array(small_ids.resize(size, nearest)) == ids
    blocked = (permit != 255) | (state == 0) | ~aligned | (ids == 3)
    assert snow.any() and not snow[blocked].any()
    # Equal final states must yield equal texture for both baked-in motions.
    plan["motion"] = "stationary"
    write(d/"answers.json",plan)
    write(d/"detail.json",detail)
    again = run("detail.json","大雪","still",both)["积雪"]
    assert np.array_equal(snow, again)
    detail["state_scales"]["snow"] = .03
    write(d/"detail.json",detail)
    thin = run("detail.json","大雪","scale",both)["积雪"]
    assert thin.sum() < snow.sum()

    detail["scenario"].update(weather="大雨", old_snow=.4)
    write(d/"detail.json",detail)
    rain = run("detail.json","大雨","old-snow")
    assert rain["已有积雪"].any() and not rain["湿色"].any() and not rain["积水反射"].any()
    water = run("places.json","大雨","puddle")
    assert not water["湿色"].any() and not water["水膜"].any()
    for a in (water["积水反射"], run("places.json","冰雹","hail")["冰雹堆积"]):
        assert a.any() and not a[(permit != 255) | (ids == 3)].any()
    permit[54,21] = 128
    Image.fromarray(permit).save(d/"permit.png")
    run("places.json","大雪","invalid",expected=False)
    places["locations"]["orientation"] = "raw"
    write(d/"places.json",places)
    run("places.json","大雪","orientation",expected=False)


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        check(Path(tmp))
    print("EXIF 6、原尺寸边界、终态比例、一次性运动系数、旧雪、积水与冰粒许可检查通过。")
