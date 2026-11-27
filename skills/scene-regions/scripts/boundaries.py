"""Align effect-group boundaries to the photo while keeping one owner per pixel."""
import cv2
import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .vision import infer, tensor
else:
    from vision import infer, tensor


def resize_ids(ids, size):
    return np.array(Image.fromarray(ids).resize(size, Image.Resampling.NEAREST))


def guided_scores(guide, scores, radius):
    """Local linear guided filter on foreground scores with normalized luminance."""
    window = (2 * radius + 1,) * 2

    def mean(values):
        return cv2.blur(values, window)

    light, prior = mean(guide), mean(scores)
    variance = np.maximum(mean(guide * guide) - light * light, 0)
    slope = (mean(guide * scores) - light * prior) / (variance + .003)
    return mean(slope) * guide + mean(prior - slope * light)


def refine_subject(photo, base, guide):
    """Use a local saliency pass per substantial component and pin the opaque core."""
    result = base.copy()
    _, components, stats, _ = cv2.connectedComponentsWithStats(base.astype(np.uint8))
    source_scale = max(1., max(photo.size) / 1024)
    for cid in np.argsort(-stats[1:, cv2.CC_STAT_AREA])[:8] + 1:
        x, y, w, h, area = stats[cid]
        # A small object already undersamples the 320px saliency input. Magnifying
        # it can move the border onto adjacent shadows without adding evidence.
        if max(w, h) < 320 or min(w, h) < 6 * source_scale or area < .04 * w * h:
            continue
        pad = max(6, round(.15 * max(w, h)))
        left, top = max(0, x-pad), max(0, y-pad)
        right, bottom = min(photo.width, x+w+pad), min(photo.height, y+h+pad)
        crop = photo.crop((left, top, right, bottom))
        roi = np.s_[top:bottom, left:right]
        obj = (components[roi] == cid).astype(np.uint8)
        original = base[roi]
        raw = infer("foreground.onnx", tensor(crop, 320, relative=True))[0]
        score = cv2.resize(raw, crop.size, interpolation=cv2.INTER_LINEAR)
        salient = score >= .5
        agreement = np.count_nonzero(salient & obj) / max(1, np.count_nonzero(salient | obj))
        if agreement < .85:
            continue
        radius = max(2, round(max(crop.size) / 180))
        aligned = guided_scores(guide[roi], score, radius) >= .5
        # Move only this object's edge. Neighboring vehicles retain their own seeds.
        outer = cv2.distanceTransform(1 - obj, cv2.DIST_L2, 3)
        inner = cv2.distanceTransform(obj, cv2.DIST_L2, 3)
        band = max(3, round(.025 * max(w, h)))
        editable = (outer <= band) & (inner <= band) & ((obj != 0) | ~original)
        result[roi][editable] = aligned[editable]
    return result


def refine_groups(photo, ids, groups, edits=None):
    """Return high-resolution region IDs with coarse-group ownership resolved jointly."""
    original = resize_ids(ids, photo.size)
    if min(photo.size) < 32 or len(groups) < 2:
        return original
    guide = np.asarray(photo.convert("L"), dtype=np.float32) / 255
    owner = np.zeros(int(ids.max()) + 1, np.uint16)
    for gid, members in enumerate(groups.values(), 1):
        owner[members] = gid
    previous = owner[original]
    radius = max(2, round(2 * photo.width / ids.shape[1]))
    best = np.full(previous.shape, -np.inf, np.float32)
    winners = previous.copy()
    subject_id = None
    for gid, key in enumerate(groups, 1):
        if key == "subject":
            subject_id = gid
            continue
        if key == "caption":
            continue
        prior = (previous == gid).astype(np.float32)
        score = guided_scores(guide, prior, radius)
        # Near a tie, preserve the semantic model's decision.
        score += prior * .08
        take = score > best
        winners[take] = gid
        np.maximum(best, score, out=best)
    if subject_id is not None and np.isfinite(best[0, 0]):
        old_subject = previous == subject_id
        new_subject = refine_subject(photo, old_subject, guide)
        # A removed shadow or missed road pixel inherits its nearest exterior group.
        released = old_subject & ~new_subject
        if released.any():
            exterior = ~old_subject
            if "caption" in groups:
                exterior &= ~np.isin(original, groups["caption"])
            if exterior.any():
                winners[released] = nearest_values(previous, exterior, released)
        winners[new_subject] = subject_id
    if "caption" in groups:
        locked = np.isin(original, groups["caption"])
        winners[locked] = previous[locked]
    if edits:
        protected = Image.new("L", ids.shape[::-1])
        draw = ImageDraw.Draw(protected)
        for edit in edits:
            draw.polygon([tuple(point) for point in edit["polygon"]], fill=255)
        locked = resize_ids(np.asarray(protected), photo.size) != 0
        winners[locked] = previous[locked]
    return transfer_groups(original, previous, winners, groups)


def nearest_values(values, seeds, target):
    _, indices = cv2.distanceTransformWithLabels((~seeds).astype(np.uint8), cv2.DIST_L2, 5,
                                                 labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros(int(indices.max()) + 1, values.dtype)
    lookup[indices[seeds]] = values[seeds]
    return lookup[indices[target]]


def transfer_groups(regions, owners, winners, groups):
    result = regions.copy()
    for gid, members in enumerate(groups.values(), 1):
        added = (winners == gid) & (owners != gid)
        if added.any():
            original = owners == gid
            result[added] = members[0] if len(members) == 1 else nearest_values(regions, original, added)
    return result


def refine_scene_edges(photo, initial, groups, regions, edits, analysis_size):
    if __package__:
        from .refiners import group_proposals, fuse_groups
    else:
        from refiners import group_proposals, fuse_groups

    lookup = np.zeros(int(initial.max()) + 1, np.uint8)
    # A tiny region can disappear in the first refinement.
    present = set(np.unique(initial))
    active = {key: [r for r in members if r in present] for key, members in groups.items()}
    active = {key: members for key, members in active.items() if members}
    if len(active) < 2 or min(photo.size) < 32:
        return initial, []
    for gid, members in enumerate(active.values(), 1):
        lookup[members] = gid
    owners = lookup[initial]
    keys = list(active)
    slim, cascade, fixed = group_proposals(photo, owners, keys)
    winners = fuse_groups(owners, slim, cascade, keys)
    winners[fixed] = owners[fixed]

    if "sky" in active:
        # Recover blue gaps in foliage near observed blue sky. Work from tree IDs so
        # reflections, ground, blue architecture and a selected tree subject stay out.
        sky = keys.index("sky") + 1
        trees = [r["id"] for r in regions if r["label"] in ("tree", "palm")
                 and any(r["id"] in active.get(k, ()) for k in ("background", "land"))]
        if trees:
            red, green, blue = np.asarray(photo).astype(np.int16).transpose(2, 0, 1)
            bluish = (blue - red > 13) & (green - red > 4) & (blue > 102)
            seeds = (owners == sky) & bluish
            if seeds.sum() >= .4 * (owners == sky).sum():
                distance = cv2.distanceTransform((~seeds).astype(np.uint8), cv2.DIST_L2, 3)
                foliage = np.isin(initial, trees)
                span = min(photo.size)
                within = distance <= .08 * span
                # Distant blue openings are distinguishable in warm foliage. Limit
                # this wider search to pixels surrounded by a local leaf canopy.
                window = max(3, 2 * round(.02 * span) + 1)
                warm_leaves = foliage & (red - blue > 30) & (green - blue > 10)
                density = cv2.blur(warm_leaves.astype(np.uint8) * 255, (window, window))
                within |= (distance <= .50 * span) & (density >= 50)
                winners[foliage & bluish & within & (winners == owners)] = sky

    if edits:
        protected = Image.new("L", analysis_size)
        draw = ImageDraw.Draw(protected)
        for edit in edits:
            draw.polygon([tuple(point) for point in edit["polygon"]], fill=255)
        locked = resize_ids(np.asarray(protected), photo.size) != 0
        winners[locked] = owners[locked]
    return transfer_groups(initial, owners, winners, active), ["CascadePSP-global-512", "SlimSAM-1024"]
