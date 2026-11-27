"""Photo-lit continuous surface textures. Permission masks are applied by the exporter."""
import cv2
import numpy as np

from inputs import GRASS, LEAVES, PAINT

F32 = np.float32
LUMA = np.array([.213, .715, .072], F32)


def unit(v):
    return np.clip(v, 0, 1)


def ease(v):
    t = unit(v)
    return t * t * (3 - 2 * t)


def tint(light, rgb):
    return light[..., None] * np.array(rgb, F32)


class Lighting:
    def __init__(self, photo, seed):
        self.width, self.height = photo.size
        self.photo = photo
        self.unit_px = max(.5, max(photo.size) / 1350)
        # Estimate the direction towards the brightest part of the environment.
        thumb = np.array(photo.resize((48, 48)), F32) / 255
        lum = thumb @ LUMA
        y, x = np.mgrid[-1:1:48j, -1:1:48j]
        weight = unit(lum - np.percentile(lum, 65)) ** 2
        vec = np.array([(weight * x).sum(), (weight * y).sum()])
        self.light = vec / max(.001, np.linalg.norm(vec))
        self.ambient = thumb[lum >= np.percentile(lum, 75)].mean(axis=0)
        rng = np.random.default_rng(seed)
        self.tiles = rng.random((6, 256, 256), dtype=F32)

    def band(self, start, end, depth, material):
        return Surface(self, start, end, depth, material)


class Surface:
    def __init__(self, scene, start, end, depth, material):
        self.scene = scene
        w, h = scene.width, scene.height
        self.mat = material
        # Extend across strip boundaries before computing lighting gradients.
        radius = max(1., min(10., 2.4 * scene.unit_px))
        pad = int(np.ceil(4 * radius)) + 2
        top = max(0, start - pad)
        photo = np.array(scene.photo.crop((0, top, w, min(h, end + pad))), F32) / 255
        sl = slice(start-top, end-top)
        self.rgb = photo[sl]
        lum = photo @ LUMA
        soft = cv2.GaussianBlur(lum, (0, 0), radius)
        self.lum = lum[sl]
        self.relief = np.clip((lum-soft)[sl] * 4, -.45, .45)
        # A dark twig against a brighter opening has a distinct upper lip;
        # bias deposition toward that side even if the lit side faces elsewhere.
        rows = np.arange(sl.start, sl.stop)
        above = np.maximum(0, rows - max(1, round(radius)))
        self.branch_lip = np.clip(lum[above] - self.lum, -.4, .4)
        lx, ly = scene.light
        self.lit_edge = np.clip((cv2.Sobel(soft, -1, 1, 0) * lx + cv2.Sobel(soft, -1, 0, 1) * ly)[sl], -.5, .5)
        self.y, self.x = np.mgrid[start:end, :w].astype(F32)
        distance = self.y / max(h-1, 1)
        self.depth = distance if depth is None else depth
        # Project the track onto a receding plane. For other materials blend two
        # world scales by depth, avoiding coordinate jumps at inferred depth steps.
        u = scene.unit_px
        scale = .30 + 1.45 * distance ** 1.7
        self.tx = (self.x - w * .5) / (u * scale)
        self.ty = -h / (u * (.35 + distance)) * .95
        self.xu, self.yu = self.x / u, self.y / u

    def noise(self, x, y, tile=0, smooth=True):
        mode = cv2.INTER_CUBIC if smooth else cv2.INTER_NEAREST
        return cv2.remap(self.scene.tiles[tile], x.astype(F32), y.astype(F32), mode, borderMode=cv2.BORDER_WRAP)

    def snow(self, amount):
        ground = (.46 * self.noise(self.tx / 47, self.ty / 29)
                  + .31 * self.noise(self.tx / 13, self.ty / 10, 1)
                  + .23 * self.noise(self.tx / 3.2, self.ty / 3, 2))
        coarse = self.noise(self.xu / 19, self.yu / 12)
        fine = self.noise(self.xu / 5, self.yu / 4, 1)
        cluster = self.depth * coarse + (1-self.depth) * fine
        grain = self.noise(self.xu / 1.7, self.yu / 1.4, 2)
        blades = self.noise((self.xu + self.yu * .36) / 2.5, self.yu / 8, 3)
        field = np.select(
            [self.mat == GRASS, self.mat == LEAVES, self.mat == PAINT],
            [.42*cluster + .39*blades + .19*grain + .42*self.relief,
             .55*cluster + .28*fine + .17*grain + .34*self.relief + .32*self.lit_edge + .62*self.branch_lip,
             .67*cluster + .21*fine + .12*grain + .13*self.relief],
            default=ground + .23*self.relief,
        )
        # Higher load both expands contiguous patches and thickens their cores.
        load = unit(amount)
        threshold = .79 - .64 * load ** .76
        core = ease((field - threshold) / (.14 + .07 * load))
        opacity = (.13 + .84 * load ** .65) * core
        opacity *= np.where(self.mat == PAINT, .76, 1)
        light = .68 + .31 * np.sqrt(self.lum) + .09*self.relief + .05*(grain-.5) + .03*self.lit_edge
        colour = (.91 * tint(light, [.98, .993, 1.]) +
                  .09 * self.scene.ambient * light[..., None])
        return colour, opacity

    def water(self, effect, amount):
        # Compress water-equivalent units into optical response without
        # normalizing each image independently (light/heavy rain stay comparable).
        wetness = 1 - np.exp(-3.8 * amount)
        if effect == "湿色":
            porous = np.isin(self.mat, [GRASS, LEAVES])
            return self.rgb * [.54, .58, .63], wetness * np.where(porous, .66, .54)
        if effect == "水膜":
            glint = unit(.06 + .62*self.lum**2 + .32*self.lit_edge + .24*self.relief)
            return .65*self.rgb + .35*self.scene.ambient, wetness*glint*.72
        # Sample a vertically reflected, horizontally rippled environment image.
        reflected_y = np.clip(.83 - self.y / self.scene.height, 0, 1)
        shift = self.noise(self.tx/33, self.ty/3) - .5
        env = np.asarray(self.scene.photo.resize((256, 192)), dtype=F32) / 255
        reflection = cv2.remap(env, (self.x*255/self.scene.width + 2*shift).astype(F32),
                               (reflected_y*191).astype(F32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return reflection*.8 + .2*self.rgb, wetness*(.34 + .16*shift)

    def particles(self, hail):
        # Jitter one centre inside each cell. Centre/radius bounds prevent seams
        # at cell edges. Near and distant cells subtend different image areas.
        size = 8. if hail else 10.
        gx, gy = self.tx / size, self.ty / size
        col, row = np.floor(gx), np.floor(gy)
        jitter = lambda i: self.noise(col, row, i, smooth=False)
        dx = gx-col-(.29 + .42*jitter(0))
        dy = gy-row-(.29 + .42*jitter(1))
        radius = (.13 + .15*jitter(2)) if hail else (.08 + .12*jitter(2))
        dx, dy = dx/radius, dy/radius
        dist = np.sqrt(dx*dx + dy*dy)
        normal = np.sqrt(unit(1 - dist*dist))
        lx, ly = self.scene.light
        lighting = unit((dx*lx + dy*ly)*.65 + .55*normal)
        if hail:
            cluster = self.noise(self.tx/35, self.ty/24, 5)
            keep = jitter(3) < .2 + .77*cluster
            return tint(.55 + .44*lighting, [.94, .97, 1.]), ease((1-dist)*4)*keep*.94
        glint = np.exp(-((dx-.4*lx)**2 + (dy-.4*ly)**2)*10.)
        rim = ease((dist-.57)*3.3)
        colour = self.rgb*(.43 + .4*normal[..., None]) + tint(.16*rim + .78*glint, [.9,.96,1.])
        return colour, ease((1-dist)*5)*(jitter(3) < .57)*.86

    def frozen(self, effect):
        if effect in ("凝露", "冰雹堆积"):
            return self.particles(effect == "冰雹堆积")
        grain = self.noise(self.xu/1.35, self.yu/1.35, 2)
        if effect == "结霜":
            islands = self.noise(self.xu/12, self.yu/10)
            return tint(.76+.19*self.lum, [.93,.97,1]), unit(.09+.52*grain+.25*(islands-.5))**1.5
        if effect == "冻雨挂冰":
            ridges = self.noise(self.xu/2.5, self.yu/20, 4)
            polish = unit(.12 + .60*ridges**3 + .57*self.lit_edge)
            return self.rgb*.45 + tint(.48 + .20*ridges, [.78,.90,.99]), polish
        veins = 1 - ease(abs(self.noise(self.tx/29, self.ty/17)-.5)*19)
        return self.rgb*.54 + tint(.34+.09*grain, [.75,.88,1.]), unit(.25+.23*self.lum+.17*veins+.18*self.lit_edge)
