"""Estimate visible accumulation surfaces on the segmented, EXIF-oriented grid."""
import cv2
import numpy as np
from PIL import Image

from engine.analysis import geometry
from surface import LABELS

GROUND = {"road", "floor", "earth", "land", "field", "grass", "sand", "path",
          "sidewalk", "runway", "dirt track", "stairs", "stairway"}
TABLE = {"table", "desk", "coffee table", "counter"}
VEHICLE = {"car", "bus", "truck", "van"}
FENCE = {"fence", "railing", "bannister"}
NAMES = {
    "tree": ("树冠外层朝上的枝叶、可承雪的枝条上侧", "树冠侧面与近直立枝条"),
    "table": ("桌板上侧", "桌板侧边及竖向支撑面"),
    "car": ("车身朝上的面板", "车身陡面与轮侧"),
    "fence": ("栏杆朝上的窄边", "栏杆正面与立柱侧面"),
    "building": ("建筑朝上的屋面与凸沿", "建筑立面"),
    "ground": ("地表可承托处", "陡坡或突起侧壁"),
}


def geometry_parts(photo, small, ids, rows, groups):
    depth, up, exposure = geometry(photo, small.size)
    return classify(ids, rows, groups, np.asarray(small), depth, up, exposure)


def vehicle_surfaces(region, rgb):
    """Conservative cabin candidates bounded by the visible vehicle silhouette."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, components, stats, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8))
    out = {k: np.zeros_like(region) for k in ("windscreen","side_window","roof","hood")}
    for cid,(x,y,w,h,area) in enumerate(stats[1:],1):
        if area < 80 or min(w,h) < 8:
            continue
        roi = np.s_[y:y+h,x:x+w]
        body = components[roi] == cid
        tone = gray[roi]
        ys = np.arange(h)[:,None]
        cabin = body & (ys < .56*h)
        # Tinted glazing often forms broad darker panels below the roof contour.
        threshold = np.clip(np.percentile(tone[body],65)*.78,45,165)
        dark = (cabin & (tone < threshold)).astype(np.uint8)
        radius = max(1,round(.018*h))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*radius+1,)*2)
        dark = cv2.morphologyEx(dark,cv2.MORPH_CLOSE,kernel)
        contours,_ = cv2.findContours(dark,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            left,top,width,height = cv2.boundingRect(contour)
            if (width < .09*w or height < .055*h or height > .52*h
                    or cv2.contourArea(contour) < .35*width*height):
                continue
            shape = np.zeros_like(dark)
            cv2.fillConvexPoly(shape,cv2.convexHull(contour),1)
            shape = cv2.dilate(shape,kernel).astype(bool)
            kind = "windscreen" if width >= .23*w else "side_window"
            out[kind][roi] |= body & shape
        glass = out["windscreen"][roi] | out["side_window"][roi]
        roof = body & (ys <= body.argmax(axis=0)+max(1,.045*h)) & (ys < .24*h)
        out["roof"][roi] |= roof & ~glass
        if glass.any():
            bottom = np.nonzero(glass)[0].max()
            out["hood"][roi] |= body & ~glass & (ys>bottom) & (ys<.55*h)
    return out


def reflected_pools(region, rgb, dips):
    """Find enclosed reflection patches with a contrasting, same-ground rim."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    size = max(9, min(91, (min(gray.shape)//8)|1))
    smooth = cv2.GaussianBlur(gray,(5,5),0)
    local = cv2.GaussianBlur(smooth,(size,size),0)
    delta = smooth.astype(float)-local
    # A bright reflected sky with a darker continuous shore is stronger
    # evidence than a uniformly wet or glossy road.
    bright = ((delta > 9) & region).astype(np.uint8)
    contours,_ = cv2.findContours(bright,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    result = np.zeros_like(region)
    for contour in contours:
        x,y,w,h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if min(w,h)<5 or w<1.25*h or area < max(42,region.size*.00008) or area<.24*w*h:
            continue
        x0,y0=max(0,x-4),max(0,y-4)
        roi=np.s_[y0:y+h+4,x0:x+w+4]
        mask=np.zeros(region[roi].shape,np.uint8)
        cv2.drawContours(mask,[contour-np.array([[[x0,y0]]],np.int32)],-1,1,-1)
        core=cv2.erode(mask,np.ones((3,3),np.uint8)).astype(bool)
        ring=(cv2.dilate(mask,np.ones((7,7),np.uint8))>0) & (mask==0)
        if not core.any() or not ring.any() or not region[roi][mask>0].all() or region[roi][ring].mean()<.95:
            continue
        inside=rgb[roi][core].astype(float)
        outside=rgb[roi][ring].astype(float)
        contrast=inside.mean(axis=1).mean()-outside.mean(axis=1).mean()
        # Reject paint stripes and saturated markings. Require either a local
        # depression or a cool sky reflection bounded by darker pavement.
        reflected=(inside[:,2]-inside[:,0]).mean()>5 and np.ptp(inside.mean(axis=0))<75
        if contrast>14 and dips[roi][core].any() and reflected and w/h<5 and core.sum()/area>.50:
            result[roi] |= core
    return result


def pavement_water(region, ground, paved, rgb):
    """A water component needs a ground rim and smoother interior than its surroundings."""
    _, ids, stats, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8))
    detail = np.abs(cv2.Laplacian(cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY),cv2.CV_32F))
    selected = np.zeros_like(region)
    for i,(x,y,w,h,a) in enumerate(stats[1:],1):
        if min(w,h)<5 or w<1.12*h or a<50 or a>region.size*.22 or y+h/2<region.shape[0]*.35:
            continue
        pad = max(3,min(12,round(h*.10)))
        roi=np.s_[max(0,y-pad):y+h+pad,max(0,x-pad):x+w+pad]
        body=(ids[roi]==i).astype(np.uint8)
        ring=cv2.dilate(body,np.ones((pad*2+1,)*2,np.uint8)).astype(bool) & (body==0)
        core=cv2.erode(body,np.ones((5,5),np.uint8)).astype(bool)
        if not core.any() or not ring.any():
            continue
        if ground[roi][ring].mean()<.82 or paved[roi][ring].mean()<.30:
            continue
        if detail[roi][core].mean()<.45*detail[roi][ring].mean():
            selected[roi] |= core
    return selected


def classify(ids, rows, groups, rgb, depth, up, exposure):
    # A local dip must sit behind its neighbours on both axes. Ignore semantic edges.
    # This is evidence of a possible basin, not metric relief on a reconstructed mesh.
    depth = np.asarray(depth, np.float32)
    radius = max(1, min(ids.shape) // 90)
    dips = np.ones(ids.shape, bool)
    for dy, dx in ((0, radius), (0, -radius), (radius, 0), (-radius, 0)):
        neighbour = np.roll(depth, (dy, dx), axis=(0, 1))
        same = np.roll(ids, (dy, dx), axis=(0, 1)) == ids
        if dy > 0:
            same[:dy] = False
        if dy < 0:
            same[dy:] = False
        if dx > 0:
            same[:, :dx] = False
        if dx < 0:
            same[:, dx:] = False
        dips &= same & (neighbour > depth + .004)
    index = np.zeros(ids.shape, np.int32)
    parts, locations = [], []
    def add(mask, title, kind, **facts):
        if not mask.any():
            return
        pid = len(parts) + 1
        parts.append(dict(region_id=rid, name=title, kind=kind, **facts))
        locations.append(dict(id=pid, region_id=rid, name=title))
        index[mask] = pid

    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    for row in rows:
        rid, label = row["id"], row["label"]
        region = ids == rid
        group = groups[rid]
        if not region.any():
            parts.append(dict(region_id=rid, name="小于分析网格的细部", kind="unknown"))
            continue
        if group in {"footer", "metadata", "caption"}:
            add(region, "画面信息栏", "air")
            continue
        if label == "sky" or group == "sky":
            add(region, "天空", "air")
            continue
        if label in LABELS["water"] or group == "water":
            ground = np.isin(ids,[r["id"] for r in rows if r["label"] in GROUND])
            paved = np.isin(ids,[r["id"] for r in rows if r["label"] in {"road","floor","sidewalk","path","runway","dirt track"}])
            pond = pavement_water(region,ground,paved,rgb) if label=="water" else np.zeros_like(region)
            add(pond, "路缘包围的已有浅积水", "water", direct=True,pools=True,water_freezes=False)
            add(region & ~pond, "可见开阔水面", "water", water_freezes=False)
            continue
        # Visible blue gaps can remain inside a coarse canopy mask.
        if label == "tree":
            gap = region & (b > r + 32) & (b > g + 15) & (b > 130)
            add(gap, "枝间透出的蓝天", "air")
            region = region & ~gap
        vegetation = label in LABELS["foliage"]
        ground = label in GROUND
        family = ("tree" if vegetation else "ground" if ground else "table" if label in TABLE
                  else "car" if label in VEHICLE else "fence" if label in FENCE
                  else "building" if label in {"building", "house", "skyscraper"} else "other")
        upper, side = NAMES.get(family, ("可见朝上承托面", "可见陡面或侧面"))
        if vegetation and label != "tree":
            upper, side = "植株朝上的枝叶", "植株侧面和直立茎部"
        top = up >= .36
        if family == "car":
            surfaces = vehicle_surfaces(region,rgb)
            for key,title in (("windscreen","疑似前风挡"),("side_window","疑似侧窗")):
                area = region & surfaces[key]
                add(area,title,"solid",direct=True,holds_snow=None,pools=False,traps_hail=False)
                region = region & ~area
            for key,title in (("roof","顶部承托面"),("hood","窗线下的朝上面板")):
                area = region & surfaces[key] & top & (exposure>=.70)
                add(area,title,"solid",direct=True,holds_snow=True,pools=False,traps_hail=False)
                region = region & ~area
            # Near the lower silhouette, a bumper, grille or wheel often yields
            # the same depth gradient as a horizontal panel. Require review there.
            _, components, bounds, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8))
            ys = np.arange(region.shape[0])[:, None]
            part_y = bounds[components, cv2.CC_STAT_TOP]
            part_height = bounds[components, cv2.CC_STAT_HEIGHT]
            uncertain = region & top & (ys >= part_y + part_height * .56)
            add(uncertain, "车身下部，缺少水平承托证据", "solid", direct=None,
                holds_snow=None, pools=False, traps_hail=False)
            region = region & ~uncertain
        if ground:
            top = np.ones_like(region)  # The ground label provides a support prior at distance.
        if vegetation or family == "fence":
            # The first visible silhouette in each column gives a support cue on thin foliage/rails.
            heights = np.arange(region.shape[0])[:, None]
            first = region.argmax(axis=0)
            top |= region & (heights >= first) & (heights < first + max(1, region.shape[0] // 200))
        exposed = exposure >= .70
        trunk = np.zeros_like(region)
        if label == "tree":
            # Elongated bark-coloured runs provide a stronger trunk cue than a noisy depth edge.
            bark = region & (r > g + 4) & (r < 150) & (g > b) & (up < .6)
            height = max(5, min(ids.shape) // 40)
            broad = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_OPEN,
                                     np.ones((1, height), np.uint8)).astype(bool)
            grey = (np.maximum(np.maximum(r, b), g) - np.minimum(np.minimum(r, b), g)) < 22
            narrow_stem = region & ~broad & grey & (r < 175)
            trunk = cv2.morphologyEx((bark | narrow_stem).astype(np.uint8), cv2.MORPH_OPEN,
                                     np.ones((height, 1), np.uint8)).astype(bool)
        can_pool = label in (GROUND | TABLE | {"rock", "roof"}) and label not in {"sand", "grass", "earth"}
        if can_pool and ground:
            confirmed_pool = reflected_pools(region,rgb,dips) & top & exposed
            add(confirmed_pool,"有封闭边界和反射证据的地面积水","solid",direct=True,
                holds_snow=True,pools=True,wet=True,freezes=True,traps_hail=None)
            region = region & ~confirmed_pool
        pools = dips & top if can_pool else np.zeros_like(region)
        zones = [(trunk, "直立树干侧面", False, False),
                 (region & ~trunk & pools, upper + "的局部低洼", True, True),
                 (region & ~trunk & top & ~pools, upper, True, False),
                 (region & ~trunk & ~top, side, False, False)]
        for zone, name, support, basin in zones:
            for direct, light in ((True, "外露"), (False, "受遮挡")):
                mask = zone & (exposed == direct)
                # Each event is a separate hypothetical scenario, not a measurement of temperature.
                add(mask, light + "的" + name, "solid", direct=direct, holds_snow=support,
                    pools=None if basin else False, inflow=False, freezes=True, wet=direct and (support or basin),
                    frost_ready=direct, dew_ready=direct, traps_hail=None if basin else False, inflow_hail=False)
    if len(parts) > 65535:
        raise ValueError("分析部位数超过 PNG 编号上限。")
    return parts, locations, Image.fromarray(index.astype(np.uint16))
