"""Class-agnostic group boundary proposals from the bundled ONNX models."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

if __package__:
    from .vision import MEAN, STD
else:
    from vision import MEAN, STD

MODELS = Path(__file__).resolve().parents[1] / "models/refiners"


@lru_cache(maxsize=5)
def session(name):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 3
    return ort.InferenceSession(str(MODELS / name), options, providers=["CPUExecutionProvider"])


def interior_points(mask, limit):
    distances = cv2.distanceTransform(np.pad(mask.astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1].copy()
    cutoff = max(1., .09 * distances.max())
    radius = max(4, round(.14 * min(mask.shape)))
    points = []
    for _ in range(limit):
        y, x = np.unravel_index(distances.argmax(), distances.shape)
        if distances[y, x] < cutoff:
            break
        points.append((int(x), int(y)))
        cv2.circle(distances, points[-1], radius, 0, -1)
    return points


def slim_input(photo):
    width, height = (max(1, round(d * 1024 / max(photo.size))) for d in photo.size)
    rgb = np.asarray(photo.resize((width, height), Image.Resampling.BILINEAR), np.float32) / 255
    pixels = np.zeros((1, 3, 1024, 1024), np.float32)
    pixels[0, :, :height, :width] = ((rgb - MEAN) / STD).transpose(2, 0, 1)
    embeddings, positions = session("vision_encoder_quantized.onnx").run(None, {"pixel_values": pixels})
    return {"image_embeddings": embeddings, "image_positional_embeddings": positions}, (width, height)


def slim_proposal(inputs, mask, positives, negatives, size):
    coordinates = np.array(positives + negatives, np.float32)[None, None]
    signs = np.array([1] * len(positives) + [0] * len(negatives), np.int64)[None, None]
    decoder = session("prompt_encoder_mask_decoder_quantized.onnx")
    _, logits = decoder.run(None, dict(inputs, input_points=coordinates, input_labels=signs))
    best_iou, best = 0., None
    for candidate in logits[0, 0]:
        coarse = cv2.resize(candidate, (1024, 1024))[:mask.shape[0], :mask.shape[1]]
        region = coarse > 0
        iou = np.count_nonzero(mask & region) / max(1, np.count_nonzero(mask | region))
        if iou >= .70 and iou > best_iou:
            best_iou, best = iou, coarse
    if best is None:
        return None
    return np.tanh(cv2.resize(best, size) * .35)


def pool_matrix(length, bins):
    weights = np.zeros((bins, length), np.float32)
    for i in range(bins):
        start = i * length // bins
        end = ((i + 1) * length + bins - 1) // bins
        weights[i, start:end] = 1 / (end - start)
    return weights


def cascade_input(photo):
    width, height = (max(8, round(d * 512 / max(photo.size) / 8) * 8) for d in photo.size)
    rgb = cv2.resize(np.asarray(photo), (width, height), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    image = np.ascontiguousarray(((rgb - MEAN) / STD).transpose(2, 0, 1)[None])
    matrices = {}
    for bins in (2, 3, 6):
        matrices[f"ph{bins}"] = pool_matrix(height // 8, bins)
        matrices[f"pwt{bins}"] = pool_matrix(width // 8, bins).T
    return image, matrices


def cascade_proposal(image, matrices, mask, size):
    height, width = image.shape[-2:]
    base = cv2.resize(mask.astype(np.float32), (width, height), interpolation=cv2.INTER_NEAREST)
    base = base[None, None] * 2 - 1
    trunk, mid, head = (session(name + ".onnx") for name in ("trunk", "mid", "head"))

    def encode(a, b):
        return trunk.run(None, dict(matrices, x=np.concatenate((image, base, a, b), axis=1)))

    coarse = np.tanh(encode(base, base)[3])
    pyramid, skip2, _, medium8 = encode(coarse, coarse)
    _, medium4 = mid.run(None, {"p": pyramid, "f2": skip2})
    pyramid, skip2, skip1, _ = encode(np.tanh(medium8), np.tanh(medium4))
    features, _ = mid.run(None, {"p": pyramid, "f2": skip2})
    logits, = head.run(None, {"q": features, "f1": skip1, "im": image})
    return cv2.resize(logits[0, 0], size)


def group_proposals(photo, owners, keys):
    """Accumulate per-group votes without storing one full-resolution float map per class."""
    slim_inputs, grid_size = slim_input(photo)
    image, matrices = cascade_input(photo)
    grid = cv2.resize(owners, grid_size, interpolation=cv2.INTER_NEAREST)
    ids = list(range(1, len(keys) + 1))
    points = {i: interior_points(grid == i, 6) for i in ids}
    slim, cascade = owners.copy(), owners.copy()
    slim_best = np.full(owners.shape, -np.inf, np.float32)
    cascade_best = slim_best.copy()
    band = max(4, round(.016 * min(photo.size)))
    fixed = np.zeros(owners.shape, bool)
    for i, key in zip(ids, keys):
        base = owners == i
        if key == "caption" or base.mean() < .005 or not points[i]:
            fixed |= base
            continue
        outside = cv2.distanceTransform((~base).astype(np.uint8), cv2.DIST_L2, 3)
        inside = cv2.distanceTransform(base.astype(np.uint8), cv2.DIST_L2, 3)
        fixed |= inside > band
        nearby = outside <= band
        score = cascade_proposal(image, matrices, base, photo.size)
        take = nearby & (score > cascade_best)
        cascade[take] = i
        cascade_best[take] = score[take]

        negatives = [point for j in ids if j != i for point in points[j][:2]]
        score = slim_proposal(slim_inputs, grid == i, points[i], negatives, photo.size)
        if score is None:
            score = np.where(base, .5, -np.inf).astype(np.float32)
        else:
            score += .06 * base
        take = nearby & (score > 0) & (score > slim_best)
        slim[take] = i
        slim_best[take] = score[take]
    slim[fixed] = owners[fixed]
    cascade[fixed] = owners[fixed]
    return slim, cascade, fixed


def fuse_groups(owners, slim, cascade, keys):
    winners = cascade.copy()
    if "sky" in keys:
        sky = keys.index("sky") + 1
        edge = (owners == sky) | (slim == sky)
        winners[edge] = slim[edge]
    if "subject" in keys:
        subject = keys.index("subject") + 1
        base = owners == subject
        votes = base.astype(np.uint8) + (slim == subject) + (cascade == subject)
        accepted = votes >= 2
        # Keep supported secondary instances when the prompted mask overlooks them.
        count, parts, stats, _ = cv2.connectedComponentsWithStats(base.astype(np.uint8))
        kept = np.bincount(parts[accepted & base], minlength=count)
        omitted = kept < .5 * stats[:, cv2.CC_STAT_AREA]
        omitted[0] = False
        accepted |= omitted[parts]
        released = (winners == subject) & ~accepted
        for alternative in (cascade, slim, owners):
            take = released & (alternative != subject)
            winners[take] = alternative[take]
            released[take] = False
        winners[accepted] = subject
    return winners
