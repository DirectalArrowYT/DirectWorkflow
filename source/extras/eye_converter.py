"""
Eye Converter (MHA -> Smash).

My Hero Academia models build each eye from two meshes over one texture: the eye
(sclera) and a flat iris disc in front of it, weighted to an eye bone that slides
it around. Smash eyes are a single surface: the sclera texture on map1, and the iris
as a decal on a second UV map (uvSet) that the game scrolls through the EyeL/EyeR
material's CustomVector31 (scale U, scale V, offset U, offset V) - that is how
Smash eye animations look around. (Reference: Mario, c00 - Mario_Eye, EyeL/EyeR,
SFX_PBS_010002000800824f.)

Per eye, the converter:
  1. Finds the iris: faces of an iris/pupil material, or else the loose part of
     the eye material weighted (almost) only to an eye bone (L_eye / R_eye).
  2. Looks at the iris head-on and renders it into a square decal with a
     transparent surround (the game samples it clamp-to-edge, so the border must
     be clear), and writes uvSet on the sclera so that decal lands exactly where the
     iris mesh was at CustomVector31 = (1, 1, 0, 0).
  3. Paints out what the iris covered in the sclera texture - MHA textures often
     have a pupil painted there, which would otherwise stay put while the real
     iris scrolls away.
  4. Flattens the (extruded) iris disc onto the eye surface and keeps it as plain eye
     white - MHA eye meshes usually have a gap under the iris, which the disc covers -
     moves the eye-bone
     weights to the bone above it (Head),
     and gives the eye its own object with an EyeL/EyeR material (plus the
     D/G/L special-state variants), so the existing eye-look tools drive it.
"""

import os
import re
import tempfile

import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from mathutils import Vector

_EYE_BONE_RE = re.compile(r'^(?:([LR])_?eye|eye_?([LR]))$', re.IGNORECASE)
_IRIS_MATERIAL_KEYS = ('iris', 'pupil', 'hitomi')
_EYE_MATERIAL_EXCLUDE = ('shadow', 'brow', 'lash', 'lid', 'line', 'eyebrow', 'highlight')

DEFAULT_NORMAL = '/common/shader/sfxpbs/fighter/default_normal'
DEFAULT_PRM = '/common/shader/sfxpbs/fighter/default_params'

# Smash's eye materials (Mario, c00). Textures name a role, filled in per character.
EYE_MATERIALS = {
    '': {
        'shader': 'SFX_PBS_010002000800824f_opaque',
        'vectors': {'CustomVector0': [1.0, 0.0, 0.0, 0.0], 'CustomVector11': [1.0, 0.6, 0.3, 1.0],
                    'CustomVector13': [1.0, 1.0, 1.0, 1.0], 'CustomVector29': [0.0, 0.0, 0.0, 0.0],
                    'CustomVector30': [0.4, 1.5, 1.0, 1.0], 'CustomVector31': [1.0, 1.0, 0.0, 0.0],
                    'CustomVector8': [1.0, 1.0, 1.0, 1.0]},
        'booleans': {'CustomBoolean1': True, 'CustomBoolean11': False, 'CustomBoolean3': False,
                     'CustomBoolean4': True, 'CustomBoolean6': False},
        'textures': {'Texture0': 'sclera', 'Texture1': 'iris', 'Texture4': 'normal', 'Texture6': 'prm',
                     'Texture7': '#replace_cubemap'},
        'samplers': ['Sampler0', 'Sampler1', 'Sampler4', 'Sampler6', 'Sampler7'],
        'clamp_samplers': ['Sampler1', 'Sampler7'],
    },
    # Special states: glowing, tinted versions of the same eye (D: KO / dead eyes).
    'D': {
        'shader': 'SFX_PBS_010000001800830d_opaque',
        'vectors': {'CustomVector0': [1.0, 0.0, 0.0, 0.0], 'CustomVector3': [1.5, 0.75, 1.5, 1.0],
                    'CustomVector31': [1.0, 1.0, 0.0, 0.0], 'CustomVector8': [1.0, 1.0, 1.0, 1.0]},
        'booleans': {'CustomBoolean1': False, 'CustomBoolean11': False, 'CustomBoolean3': False,
                     'CustomBoolean4': False},
        'textures': {'Texture14': 'iris', 'Texture4': 'normal', 'Texture5': 'sclera', 'Texture6': 'prm',
                     'Texture7': '#replace_cubemap'},
        'samplers': ['Sampler14', 'Sampler4', 'Sampler5', 'Sampler6', 'Sampler7'],
        'clamp_samplers': ['Sampler14', 'Sampler7'],
    },
    'G': {
        'shader': 'SFX_PBS_0100000010008100_opaque',
        'vectors': {'CustomVector0': [1.0, 0.0, 0.0, 0.0], 'CustomVector3': [1.0, 1.0, 1.0, 1.0],
                    'CustomVector31': [1.0, 1.0, 0.0, 0.0], 'CustomVector8': [1.0, 1.0, 1.0, 1.0]},
        'booleans': {'CustomBoolean11': False},
        'textures': {'Texture14': 'iris', 'Texture4': 'normal', 'Texture5': 'sclera'},
        'samplers': ['Sampler14', 'Sampler4', 'Sampler5'],
        'clamp_samplers': ['Sampler14'],
    },
    'L': {
        'shader': 'SFX_PBS_010000001800830d_opaque',
        'vectors': {'CustomVector0': [1.0, 0.0, 0.0, 0.0], 'CustomVector3': [1.9, 0.8, 0.7, 1.0],
                    'CustomVector31': [1.0, 1.0, 0.0, 0.0], 'CustomVector8': [1.0, 1.0, 1.0, 1.0]},
        'booleans': {'CustomBoolean1': False, 'CustomBoolean11': False, 'CustomBoolean3': False,
                     'CustomBoolean4': False},
        'textures': {'Texture14': 'iris', 'Texture4': 'normal', 'Texture5': 'sclera', 'Texture6': 'prm',
                     'Texture7': '#replace_cubemap'},
        'samplers': ['Sampler14', 'Sampler4', 'Sampler5', 'Sampler6', 'Sampler7'],
        'clamp_samplers': ['Sampler14', 'Sampler7'],
    },
}


# ---------------------------------------------------------------------------
# Finding the parts
# ---------------------------------------------------------------------------

def guess_eye_material_index(obj):
    for index, material in enumerate(obj.data.materials):
        if material is None:
            continue
        name = material.name.lower()
        if 'eye' in name and not any(k in name for k in _EYE_MATERIAL_EXCLUDE) \
                and not any(k in name for k in _IRIS_MATERIAL_KEYS):
            return index
    return None


def _iris_material_indices(obj):
    return {i for i, m in enumerate(obj.data.materials)
            if m is not None and any(k in m.name.lower() for k in _IRIS_MATERIAL_KEYS)}


def _eye_bone_groups(obj):
    """{vertex group index: 'L' / 'R'} for groups named like an eye bone."""
    out = {}
    for group in obj.vertex_groups:
        match = _EYE_BONE_RE.match(group.name)
        if match:
            out[group.index] = (match.group(1) or match.group(2)).upper()
    return out


def _loose_parts(faces):
    fset = set(faces)
    seen = set()
    parts = []
    for face in faces:
        if face in seen:
            continue
        seen.add(face)
        stack, part = [face], []
        while stack:
            f = stack.pop()
            part.append(f)
            for edge in f.edges:
                for other in edge.link_faces:
                    if other in fset and other not in seen:
                        seen.add(other)
                        stack.append(other)
        parts.append(part)
    return parts


def find_eyes(obj, bm, eye_index, iris_share=0.9):
    """{'L' / 'R': {'sclera': [faces], 'iris': [faces]}} in world-space sides (+X is the
    character's left)."""
    world = obj.matrix_world
    iris_mats = _iris_material_indices(obj)
    eye_faces = [f for f in bm.faces if f.material_index == eye_index or f.material_index in iris_mats]
    deform = bm.verts.layers.deform.active
    eye_groups = _eye_bone_groups(obj)
    eyes = {}
    for part in _loose_parts(eye_faces):
        centre = sum(((world @ f.calc_center_median()) for f in part), Vector()) / len(part)
        side = 'L' if centre.x >= 0.0 else 'R'
        eye = eyes.setdefault(side, {'sclera': [], 'iris': []})
        if any(f.material_index in iris_mats for f in part):
            eye['iris'] += part
            continue
        verts = {v for f in part for v in f.verts}
        on_eye_bone = 0
        if deform is not None and eye_groups:
            for v in verts:
                weights = v[deform]
                total = sum(weights.values())
                if total > 0 and sum(w for g, w in weights.items() if g in eye_groups) / total >= iris_share:
                    on_eye_bone += 1
        if verts and on_eye_bone / len(verts) >= iris_share:
            eye['iris'] += part
        else:
            eye['sclera'] += part
    for eye in eyes.values():
        if not eye['iris']:
            _iris_by_shape(eye, world)
    return eyes


def _iris_by_shape(eye, world, min_roundness=0.75):
    """For eyes whose iris is no longer on an eye bone (e.g. baked by VIS Mesh Bake, where
    everything ends up on Head): the iris is the round, flat disc. The eye white is wide."""
    parts = _loose_parts(eye['sclera'])
    if len(parts) < 2:
        return
    best, best_score = None, 0.0
    for part in parts:
        points = np.array([(world @ v.co)[:] for v in {v for f in part for v in f.verts}])
        if len(points) < 12:
            continue
        spread = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
        roundness = spread[1] / max(spread[0], 1e-12)
        if roundness > best_score:
            best, best_score = part, roundness
    if best is None or best_score < min_roundness:
        return
    ids = {id(f) for f in best}
    eye['iris'] = list(best)
    eye['sclera'] = [f for f in eye['sclera'] if id(f) not in ids]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def eye_frame(obj, iris_faces, sclera_faces):
    """(centre, x axis, y axis, normal) of the iris plane, in world space. X follows the
    character's left (world +X), Y points up."""
    world = obj.matrix_world
    points = np.array([(world @ v.co)[:] for v in {v for f in iris_faces for v in f.verts}])
    centre = points.mean(axis=0)
    _u, _s, vt = np.linalg.svd(points - centre)
    normal = Vector(vt[2])
    facing = sum(((world.to_3x3() @ f.normal) for f in (iris_faces or sclera_faces)), Vector())
    if normal.dot(facing) < 0.0:
        normal = -normal
    x_axis = Vector((1.0, 0.0, 0.0))
    x_axis = (x_axis - normal * x_axis.dot(normal))
    if x_axis.length < 1e-6:
        x_axis = Vector((0.0, 1.0, 0.0)) - normal * normal.y
    x_axis.normalize()
    y_axis = normal.cross(x_axis).normalized()
    if y_axis.z < 0.0:
        y_axis = -y_axis
    return Vector(centre), x_axis, y_axis, normal.normalized()


def _plane(points, frame):
    centre, x_axis, y_axis, _n = frame
    rel = points - np.array(centre[:])
    return np.stack([rel @ np.array(x_axis[:]), rel @ np.array(y_axis[:])], axis=-1)


# ---------------------------------------------------------------------------
# Rasterizing
# ---------------------------------------------------------------------------

def _triangles(faces, uv_layer, world):
    """(positions (n, 3, 3), uvs (n, 3, 2)) of the faces as triangles."""
    pos, uvs = [], []
    for face in faces:
        loops = face.loops
        for i in range(1, len(loops) - 1):
            tri = (loops[0], loops[i], loops[i + 1])
            pos.append([(world @ l.vert.co)[:] for l in tri])
            uvs.append([l[uv_layer].uv[:] for l in tri])
    return np.array(pos, dtype=np.float64), np.array(uvs, dtype=np.float64)


def _raster(tris_px, width, height, callback):
    """For every pixel centre inside a triangle (pixel coordinates): callback(tri index,
    xs, ys, barycentric (n, 3))."""
    for index, tri in enumerate(tris_px):
        x0, y0 = np.floor(tri.min(axis=0)).astype(int)
        x1, y1 = np.ceil(tri.max(axis=0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, width - 1), min(y1, height - 1)
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        px = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], axis=-1)
        a, b, c = tri
        v0, v1 = b - a, c - a
        den = v0[0] * v1[1] - v1[0] * v0[1]
        if abs(den) < 1e-12:
            continue
        d = px - a
        w1 = (d[:, 0] * v1[1] - v1[0] * d[:, 1]) / den
        w2 = (v0[0] * d[:, 1] - d[:, 0] * v0[1]) / den
        w0 = 1.0 - w1 - w2
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if inside.any():
            bary = np.stack([w0, w1, w2], axis=-1)[inside]
            callback(index, xs.ravel()[inside], ys.ravel()[inside], bary)


def _image_array(image):
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape(height, width, 4)


def _sample(texture, uv):
    """Bilinear, repeat-wrapped sample of an (h, w, 4) array at uvs (n, 2)."""
    h, w = texture.shape[:2]
    x = (uv[:, 0] % 1.0) * w - 0.5
    y = (uv[:, 1] % 1.0) * h - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x0, x1 = x0 % w, (x0 + 1) % w
    y0, y1 = y0 % h, (y0 + 1) % h
    top = texture[y0, x0] * (1 - fx) + texture[y0, x1] * fx
    bottom = texture[y1, x0] * (1 - fx) + texture[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def iris_decal(iris_tris_plane, iris_tris_uv, source, half, size, supersample=3):
    """Square RGBA decal of the iris seen head-on, plane coordinates [-half, half]."""
    rgba = np.zeros((size * supersample, size * supersample, 4), dtype=np.float32)
    scale = size * supersample / (2.0 * half)
    tris_px = (iris_tris_plane + half) * scale

    def fill(index, xs, ys, bary):
        uv = bary @ iris_tris_uv[index]
        colour = _sample(source, uv)
        rgba[ys, xs, :3] = colour[:, :3]
        rgba[ys, xs, 3] = 1.0

    _raster(tris_px, size * supersample, size * supersample, fill)
    # Box-filter down: premultiplied, so the rim blends instead of going dark.
    s = supersample
    premul = rgba.copy()
    premul[..., :3] *= premul[..., 3:4]
    small = premul.reshape(size, s, size, s, 4).mean(axis=(1, 3))
    alpha = small[..., 3:4]
    colour = np.where(alpha > 1e-6, small[..., :3] / np.maximum(alpha, 1e-6), 0.0)
    decal = np.concatenate([colour, alpha], axis=-1)
    _bleed(decal)
    decal[0, :, 3] = decal[-1, :, 3] = decal[:, 0, 3] = decal[:, -1, 3] = 0.0
    return decal


def _bleed(image, known=None, passes=None):
    """Push colour from known pixels into unknown ones (alpha 0 by default), so filtering
    at the iris rim or over a painted-out area never pulls in black."""
    if known is None:
        known = image[..., 3] > 0.01
    known = known.copy()
    h, w = known.shape
    passes = passes or (h + w)
    for _ in range(passes):
        if known.all():
            break
        acc = np.zeros((h, w, 3), dtype=np.float32)
        cnt = np.zeros((h, w), dtype=np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted_known = np.roll(known, (dy, dx), axis=(0, 1))
            shifted = np.roll(image[..., :3], (dy, dx), axis=(0, 1))
            acc += shifted * shifted_known[..., None]
            cnt += shifted_known
        grow = (~known) & (cnt > 0)
        if not grow.any():
            break
        image[grow, :3] = acc[grow] / cnt[grow][:, None]
        known |= grow


def fit_plane_to_uv(sclera_tris_pos, sclera_tris_uv, frame, radius):
    """Affine map (3 x 2) from eye-plane coordinates to the sclera's texture UVs, fitted on
    the sclera around the iris. MHA eye meshes often have no surface under the iris (the
    iris disc fills that gap), so this is what gives the gap its eye-white UVs."""
    q = _plane(sclera_tris_pos.reshape(-1, 3), frame)
    uv = sclera_tris_uv.reshape(-1, 2)
    near = np.linalg.norm(q, axis=1) < radius
    if near.sum() < 6:
        near = np.ones(len(q), dtype=bool)
    design = np.hstack([q[near], np.ones((near.sum(), 1))])
    matrix, *_ = np.linalg.lstsq(design, uv[near], rcond=None)
    return matrix


def fit_surface(sclera_tris_pos, frame, radius):
    """Quadratic height field (depth along the eye normal over the eye plane) fitted to the
    sclera around the iris: the surface the iris is flattened onto."""
    points = sclera_tris_pos.reshape(-1, 3)
    q = _plane(points, frame)
    depth = (points - np.array(frame[0][:])) @ np.array(frame[3][:])
    near = np.linalg.norm(q, axis=1) < radius
    if near.sum() < 10:
        near = np.ones(len(q), dtype=bool)
    x, y = q[near, 0], q[near, 1]
    design = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)
    coeffs, *_ = np.linalg.lstsq(design, depth[near], rcond=None)
    return coeffs


class EyeSurface:
    """Depth of the eye (sclera) surface along the eye normal, anywhere on the eye plane:
    exact where the sclera mesh is (ray cast), and the quadratic fit corrected by the nearest
    sclera vertices' offsets from it where it is not (the gap under the old iris)."""

    def __init__(self, sclera_tris_pos, frame, radius):
        from mathutils.bvhtree import BVHTree
        self.frame = frame
        self.coeffs = fit_surface(sclera_tris_pos, frame, radius)
        points = sclera_tris_pos.reshape(-1, 3)
        self.tree = BVHTree.FromPolygons([Vector(p) for p in points],
                                         [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(sclera_tris_pos))])
        self.q = _plane(points, frame)
        self.residual = self._depth(points) - surface_depth(self.coeffs, self.q)
        self.reach = radius * 4.0

    def _depth(self, points):
        return (points - np.array(self.frame[0][:])) @ np.array(self.frame[3][:])

    def depth(self, q):
        centre, x_axis, y_axis, normal = self.frame
        out = np.empty(len(q))
        for i, (x, y) in enumerate(q):
            origin = centre + x_axis * x + y_axis * y + normal * self.reach
            hit, _n, _i, _d = self.tree.ray_cast(origin, -normal, self.reach * 2.0)
            if hit is not None:
                out[i] = (hit - centre).dot(normal)
                continue
            dist = np.linalg.norm(self.q - (x, y), axis=1)
            nearest = np.argsort(dist)[:8]
            weights = 1.0 / np.maximum(dist[nearest], 1e-6) ** 2
            base = surface_depth(self.coeffs, np.array([[x, y]]))[0]
            out[i] = base + float((self.residual[nearest] * weights).sum() / weights.sum())
        return out


def surface_depth(coeffs, q):
    x, y = q[:, 0], q[:, 1]
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1) @ coeffs


def _apply_affine(matrix, q):
    return np.hstack([q, np.ones((len(q), 1))]) @ matrix


def paint_out_under_iris(texture, affine, extent, margin=1.25):
    """Mark every texel whose eye-plane position (through the fitted map) is under the iris
    ellipse grown by `margin`. Returns the mask."""
    h, w = texture.shape[:2]
    rx, ry = extent[0] * margin, extent[1] * margin
    # Only look at the texels around the iris.
    corners = _apply_affine(affine, np.array([[-rx, -ry], [rx, -ry], [-rx, ry], [rx, ry]]))
    u0, v0 = np.floor(corners.min(axis=0) * [w, h]).astype(int) - 2
    u1, v1 = np.ceil(corners.max(axis=0) * [w, h]).astype(int) + 2
    u0, v0, u1, v1 = max(u0, 0), max(v0, 0), min(u1, w - 1), min(v1, h - 1)
    mask = np.zeros((h, w), dtype=bool)
    if u1 < u0 or v1 < v0:
        return mask
    xs, ys = np.meshgrid(np.arange(u0, u1 + 1), np.arange(v0, v1 + 1))
    uv = np.stack([(xs.ravel() + 0.5) / w, (ys.ravel() + 0.5) / h], axis=-1)
    linear, offset = affine[:2], affine[2]
    q = (uv - offset) @ np.linalg.inv(linear)
    under = (q[:, 0] / rx) ** 2 + (q[:, 1] / ry) ** 2 < 1.0
    mask[ys.ravel()[under], xs.ravel()[under]] = True
    return mask


def paint_out_iris(texture, sclera_tris_pos, sclera_tris_uv, frame, extent, margin=1.25, extra_mask=None):
    """Replace, in the sclera texture, every texel whose surface point lies under the iris
    (the iris ellipse grown by `margin`) with the surrounding sclera colour. Returns the
    number of texels painted."""
    h, w = texture.shape[:2]
    mask = np.zeros((h, w), dtype=bool)
    tris_px = sclera_tris_uv * np.array([w, h])
    rx, ry = extent[0] * margin, extent[1] * margin

    def mark(index, xs, ys, bary):
        points = bary @ sclera_tris_pos[index]
        q = _plane(points, frame)
        under = (q[:, 0] / rx) ** 2 + (q[:, 1] / ry) ** 2 < 1.0
        mask[ys[under], xs[under]] = True

    _raster(tris_px, w, h, mark)
    if extra_mask is not None:
        mask |= extra_mask
    if not mask.any():
        return 0
    return _clean_under_iris(texture, mask)


def _clean_under_iris(texture, mask, tolerance=0.12, grow=64):
    """Repaint the masked texels (and anything iris-like around them) as eye white.

    The mask comes from geometry, but textures paint the iris a little past the iris
    mesh - an outline, a shadow ring, a transparent fringe. Those texels are added when
    they touch the mask, differ from the eye-white colour (or are transparent), and lie
    near the iris. The fill then only draws from opaque eye-white texels, so it never
    pulls in black from the empty parts of the texture. Works on a window around the
    iris. Returns the number of texels repainted."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    pad_y = int((ys.max() - ys.min() + 1) * 0.6) + 2
    pad_x = int((xs.max() - xs.min() + 1) * 0.6) + 2
    y0, y1 = max(ys.min() - pad_y, 0), min(ys.max() + pad_y + 1, h)
    x0, x1 = max(xs.min() - pad_x, 0), min(xs.max() + pad_x + 1, w)
    window = texture[y0:y1, x0:x1]
    region = mask[y0:y1, x0:x1].copy()
    opaque = window[..., 3] > 0.5
    around = opaque & ~region
    if around.any():
        white = np.median(window[around][:, :3], axis=0)
        not_white = (~opaque) | (np.abs(window[..., :3] - white).max(axis=-1) > tolerance)
        for _ in range(grow):
            neighbours = region.copy()
            neighbours[1:] |= region[:-1]
            neighbours[:-1] |= region[1:]
            neighbours[:, 1:] |= region[:, :-1]
            neighbours[:, :-1] |= region[:, 1:]
            added = neighbours & not_white & ~region
            if not added.any():
                break
            region |= added
    known = opaque & ~region
    _bleed(window, known=known)
    window[region, 3] = 1.0
    texture[y0:y1, x0:x1] = window
    return int(region.sum())


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _eye_matl(labels_textures):
    """MatlData with EyeL/EyeR (+ variants). labels_textures: {side: {'sclera', 'iris'}}"""
    from ...dependencies import ssbh_data_py
    from ..model.export_model import default_ssbh_material
    md = ssbh_data_py.matl_data
    entries = []
    for side, names in labels_textures.items():
        for suffix, spec in EYE_MATERIALS.items():
            entry = default_ssbh_material(f'Eye{side}{suffix}')
            entry.shader_label = spec['shader']
            entry.floats = []
            entry.vectors = [md.Vector4Param(getattr(md.ParamId, k), list(v)) for k, v in spec['vectors'].items()]
            entry.booleans = [md.BooleanParam(getattr(md.ParamId, k), v) for k, v in spec['booleans'].items()]
            role = {'sclera': names['sclera'], 'iris': names['iris'], 'normal': DEFAULT_NORMAL,
                    'prm': DEFAULT_PRM, '#replace_cubemap': '#replace_cubemap'}
            entry.textures = [md.TextureParam(getattr(md.ParamId, k), role[v]) for k, v in spec['textures'].items()]
            samplers = []
            for name in spec['samplers']:
                data = md.SamplerData()
                wrap = md.WrapMode.ClampToEdge if name in spec['clamp_samplers'] else md.WrapMode.Repeat
                data.wraps = data.wrapt = data.wrapr = wrap
                samplers.append(md.SamplerParam(getattr(md.ParamId, name), data))
            entry.samplers = samplers
            entries.append(entry)
    matl = md.MatlData()
    matl.entries = entries
    return matl


def _free_material_names(labels):
    """Old materials with these names would push the new ones to 'EyeL.001', and the
    exporter uses the material name as the Smash label."""
    for label in labels:
        old = bpy.data.materials.get(label)
        if old is None:
            continue
        if old.users == 0:
            bpy.data.materials.remove(old)
        else:
            old.name = f'{label}_old'


# ---------------------------------------------------------------------------
# Converting
# ---------------------------------------------------------------------------

def _save_png(array, name, folder):
    h, w = array.shape[:2]
    image = bpy.data.images.new(name, w, h, alpha=True)
    image.pixels.foreach_set(np.clip(array, 0.0, 1.0).astype(np.float32).ravel())
    path = os.path.join(folder, f'{name}.png')
    image.filepath_raw = path
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)
    return path


def _source_image(material):
    if material is None:
        return None
    smd = getattr(material, 'sub_matl_data', None)
    if smd is not None:
        for texture in smd.textures:
            if texture.name == 'Texture0' and texture.image is not None:
                return texture.image
    if not material.use_nodes:
        return None
    images = [n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image is not None]
    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None and node.outputs['Color'].is_linked:
            return node.image
    return images[0] if images else None


def _replacement_bone(obj, eye_group_name):
    armature = obj.find_armature()
    if armature is None:
        return None
    bone = armature.data.bones.get(eye_group_name)
    if bone is not None and bone.parent is not None:
        return bone.parent.name
    for name in ('Head', 'HeadN', 'Face', 'head'):
        if armature.data.bones.get(name) is not None:
            return name
    return None


def _reweight_eye_bones(obj):
    """Move eye-bone weights to the bone above the eye bone. Returns moved group names."""
    moved = []
    for group in list(obj.vertex_groups):
        if not _EYE_BONE_RE.match(group.name):
            continue
        target_name = _replacement_bone(obj, group.name)
        if target_name is None:
            continue
        target = obj.vertex_groups.get(target_name) or obj.vertex_groups.new(name=target_name)
        for vertex in obj.data.vertices:
            for g in vertex.groups:
                if g.group == group.index and g.weight > 0.0:
                    target.add([vertex.index], g.weight, 'ADD')
        name = group.name
        obj.vertex_groups.remove(group)
        moved.append(f'{name} -> {target_name}')
    return moved


def _drop_empty_groups(obj):
    """The copy carries every vertex group of the source (face, hair, ...); keep the used ones."""
    used = {g.group for v in obj.data.vertices for g in v.groups if g.weight > 0.0}
    for group in [g for g in obj.vertex_groups if g.index not in used]:
        obj.vertex_groups.remove(group)


def _keep_faces(mesh, keep):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    doomed = [f for f in bm.faces if f.index not in keep]
    bmesh.ops.delete(bm, geom=doomed, context='FACES')
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context='VERTS')
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


# Mario's eye spans about this much of uvSet across its width. Using the same scale makes
# CustomVector31 offsets (vanilla eye animations, the eye-look tools) move the iris as far
# across the eye as they do on vanilla fighters.
VANILLA_EYE_UV_WIDTH = 0.55


def convert_eyes(context, obj, operator=None, eye_index=None, prefix='chara', decal_size=512,
                 margin=1.3, paint_margin=1.25, variants=True, reweight=True, folder=None,
                 uv_scale='VANILLA', shared=None):
    """Convert the MHA eyes on `obj`. Returns (eye objects, report lines).

    `shared` (a dict, filled on the first call) lets several objects with the same eyes -
    e.g. one per baked expression - use one set of textures and EyeL/EyeR materials, with
    the iris the same size in every one of them."""
    if shared is None:
        shared = {}
    report = []
    if eye_index is None:
        eye_index = guess_eye_material_index(obj)
    if eye_index is None:
        raise RuntimeError(f"{obj.name}: no eye material found (a material named '...eye...').")
    eye_material = obj.data.materials[eye_index]
    source_image = _source_image(eye_material)
    if source_image is None:
        raise RuntimeError(f"{eye_material.name}: no image texture to take the sclera and iris from.")

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free()
        raise RuntimeError(f"{obj.name}: no UV map.")
    eyes = find_eyes(obj, bm, eye_index)
    if not eyes:
        bm.free()
        raise RuntimeError(f"{obj.name}: no eye faces found in '{eye_material.name}'.")

    world = obj.matrix_world
    if 'materials' in shared:
        source = sclera_texture = None  # textures come from the first object
    else:
        source = _image_array(source_image).copy()
        sclera_texture = source.copy()
    decals, uvsets, keep = {}, {}, {}
    for side, parts in sorted(eyes.items()):
        if not parts['iris']:
            report.append(f"{side}: no iris found - skipped (no part weighted to an eye bone, and no "
                          f"round iris disc - not an eye mesh?)")
            continue
        if not parts['sclera']:
            report.append(f"{side}: only an iris, no eye mesh - skipped")
            continue
        frame = eye_frame(obj, parts['iris'], parts['sclera'])
        iris_pos, iris_uv = _triangles(parts['iris'], uv_layer, world)
        iris_plane = _plane(iris_pos.reshape(-1, 3), frame).reshape(-1, 3, 2)
        extent = np.abs(iris_plane.reshape(-1, 2)).max(axis=0)
        if side in shared.get('half', {}):
            half = shared['half'][side]
            sclera_pos, sclera_uv = _triangles(parts['sclera'], uv_layer, world)
            surface = EyeSurface(sclera_pos, frame, float(extent.max()) * 3.0)
            affine = fit_plane_to_uv(sclera_pos, sclera_uv, frame, float(extent.max()) * 3.0)
            uvsets[side] = (frame, half, affine, surface)
            keep[side] = {f.index for f in parts['sclera']} | {f.index for f in parts['iris']}
            continue
        half = float(extent.max() * margin)
        if uv_scale == 'VANILLA':
            sclera_q = _plane(np.array([(world @ v.co)[:] for v in {v for f in parts['sclera'] for v in f.verts}]),
                              frame)
            eye_width = float(sclera_q[:, 0].max() - sclera_q[:, 0].min())
            half = max(half, eye_width / (2.0 * VANILLA_EYE_UV_WIDTH))
        decals[side] = iris_decal(iris_plane, iris_uv, source, half, decal_size)
        sclera_pos, sclera_uv = _triangles(parts['sclera'], uv_layer, world)
        affine = fit_plane_to_uv(sclera_pos, sclera_uv, frame, float(extent.max()) * 3.0)
        gap = paint_out_under_iris(sclera_texture, affine, extent, paint_margin)
        painted = paint_out_iris(sclera_texture, sclera_pos, sclera_uv, frame, extent, paint_margin,
                                 extra_mask=gap)
        surface = EyeSurface(sclera_pos, frame, float(extent.max()) * 3.0)
        iris_points = iris_pos.reshape(-1, 3)
        iris_depth = float(np.max((iris_points - np.array(frame[0][:])) @ np.array(frame[3][:])
                                  - surface.depth(iris_plane.reshape(-1, 2))))
        uvsets[side] = (frame, half, affine, surface)
        shared.setdefault('half', {})[side] = half
        # The iris disc stays as eye surface (it covers the gap in the eye mesh), showing eye white.
        keep[side] = {f.index for f in parts['sclera']} | {f.index for f in parts['iris']}
        report.append(f"{side}: iris {len(parts['iris'])} faces (radius {extent.max():.3f}, "
                      f"up to {iris_depth:.3f} in front of the eye - flattened), eye {len(parts['sclera'])} faces, painted out {painted} texels under the iris")
    iris_faces = {f.index for parts in eyes.values() for f in parts['iris']}
    bm.free()
    # Tag the iris faces so they can be found again in the per-eye copies.
    tag = obj.data.attributes.get('_sub_eye_iris') or obj.data.attributes.new('_sub_eye_iris', 'BOOLEAN', 'FACE')
    tag.data.foreach_set('value', [p.index in iris_faces for p in obj.data.polygons])
    if not keep:
        raise RuntimeError("; ".join(report) or "Nothing to convert.")

    if 'materials' in shared:
        materials = shared['materials']
    else:
        # Textures.
        if folder is None:
            folder = bpy.path.abspath('//eye_textures') if bpy.data.filepath else tempfile.mkdtemp(prefix='sub_eyes_')
        os.makedirs(folder, exist_ok=True)
        sclera_name = f'eye_{prefix}_w_col'
        _save_png(sclera_texture, sclera_name, folder)
        names = {}
        for side, decal in decals.items():
            iris_name = f'eye_{prefix}_b{side.lower()}_col'
            _save_png(decal, iris_name, folder)
            names[side] = {'sclera': sclera_name, 'iris': iris_name}

        # Materials (built the way the model importer builds them, so they export as Smash eyes).
        from ..model.material.create_blender_materials_from_matl import create_blender_materials_from_matl
        labels = [f'Eye{side}{suffix}' for side in names for suffix in (EYE_MATERIALS if variants else {'': 0})]
        _free_material_names(labels)
        matl = _eye_matl(names)
        if not variants:
            matl.entries = [e for e in matl.entries if e.material_label in {f'Eye{s}' for s in names}]

        class _Reporter:
            def report(self, level, message):
                if operator is not None and 'WARNING' in level:
                    operator.report(level, message)

        materials = create_blender_materials_from_matl(_Reporter(), matl, model_dir=folder)
        if not bpy.data.filepath:
            for image in {s.image for m in materials.values() for s in m.sub_matl_data.textures if s.image}:
                if image.filepath and not image.packed_file:
                    try:
                        image.pack()
                    except RuntimeError:
                        pass
        shared['materials'] = materials
        shared['folder'] = folder

    # One object per eye (the exporter takes one material per mesh), named like the source
    # so it stays in the same mesh / visibility group.
    eye_objects = []
    for side, faces in keep.items():
        eye = obj.copy()
        eye.data = obj.data.copy()
        for collection in obj.users_collection:
            collection.objects.link(eye)
        eye.name = obj.name
        eye.hide_render = obj.hide_render
        try:
            eye.hide_set(obj.hide_get())
        except RuntimeError:
            pass
        _keep_faces(eye.data, faces)
        mesh = eye.data
        render_uv = next((l for l in mesh.uv_layers if l.active_render), mesh.uv_layers[0])
        for layer in [l for l in mesh.uv_layers if l.name != render_uv.name]:
            mesh.uv_layers.remove(layer)
        mesh.uv_layers[0].name = 'map1'
        uvset = mesh.uv_layers.new(name='uvSet')
        frame, half, affine, surface = uvsets[side]
        co = np.array([(world @ v.co)[:] for v in mesh.vertices])
        plane = _plane(co, frame)
        loop_verts = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get('vertex_index', loop_verts)
        uvset.data.foreach_set('uv', (plane / (2.0 * half) + 0.5)[loop_verts].astype(np.float32).ravel())
        # Former iris faces: eye-white UVs from the fitted map (the painted-out area).
        is_iris = np.zeros(len(mesh.polygons), dtype=bool)
        mesh.attributes['_sub_eye_iris'].data.foreach_get('value', is_iris)
        map1 = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        mesh.uv_layers['map1'].data.foreach_get('uv', map1)
        map1 = map1.reshape(-1, 2)
        loop_start = np.empty(len(mesh.polygons), dtype=np.int64)
        loop_total = np.empty(len(mesh.polygons), dtype=np.int64)
        mesh.polygons.foreach_get('loop_start', loop_start)
        mesh.polygons.foreach_get('loop_total', loop_total)
        iris_loops = np.concatenate([np.arange(s, s + t) for s, t, i in zip(loop_start, loop_total, is_iris) if i]
                                    or [np.zeros(0, dtype=np.int64)])
        if len(iris_loops):
            map1[iris_loops] = _apply_affine(affine, plane[loop_verts[iris_loops]])
            mesh.uv_layers['map1'].data.foreach_set('uv', map1.ravel())
            # Flatten: MHA irises are extruded in front of the eye. Put the disc on the eye's
            # surface, a hair behind it, so the eye mesh wins where it exists and the disc only
            # fills the gap under the old iris.
            iris_verts = np.unique(loop_verts[iris_loops])
            normal = np.array(frame[3][:])
            depth = (co[iris_verts] - np.array(frame[0][:])) @ normal
            behind = 0.002 * half
            target = surface.depth(plane[iris_verts]) - behind
            flat = co[iris_verts] + np.outer(target - depth, normal)
            to_local = eye.matrix_world.inverted()
            for index, point in zip(iris_verts, flat):
                mesh.vertices[int(index)].co = to_local @ Vector(point)
        mesh.attributes.remove(mesh.attributes['_sub_eye_iris'])
        mesh.uv_layers['map1'].active_render = True
        # Smash eyes carry colorSet1 at a neutral 0.502 (Mario's); MHA vertex colours
        # (VERTEXCOLOR, ...) are not valid Smash attribute names.
        for attribute in list(mesh.color_attributes):
            mesh.color_attributes.remove(attribute)
        colors = mesh.color_attributes.new('colorSet1', 'FLOAT_COLOR', 'POINT')
        colors.data.foreach_set('color', [0.502] * (4 * len(mesh.vertices)))
        mesh.materials.clear()
        mesh.materials.append(materials[f'Eye{side}'])
        for polygon in mesh.polygons:
            polygon.material_index = 0
        if reweight:
            moved = _reweight_eye_bones(eye)
            if moved:
                report.append(f"{side}: weights {', '.join(moved)}")
        _drop_empty_groups(eye)
        eye['sub_eye_converted'] = side
        eye_objects.append(eye)

    # Remove the converted eyes and the irises from the source.
    remaining = {p.index for p in obj.data.polygons} - iris_faces - set().union(*keep.values())
    if remaining:
        _keep_faces(obj.data, remaining)
        if obj.data.attributes.get('_sub_eye_iris') is not None:
            obj.data.attributes.remove(obj.data.attributes['_sub_eye_iris'])
    else:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data.users == 0:
            bpy.data.meshes.remove(data)
    if 'folder' in shared and not shared.get('reported'):
        report.append(f"textures in {shared['folder']}")
        shared['reported'] = True
    return eye_objects, report


def _eye_material_items(self, context):
    obj = context.active_object
    items = [('AUTO', "Auto", "The first material named like an eye (not eyeshadow / brow / lash)")]
    if obj is not None and obj.type == 'MESH':
        for index, material in enumerate(obj.data.materials):
            if material is not None:
                items.append((str(index), material.name, ""))
    return items


class SUB_OP_convert_mha_eyes(Operator):
    """Turn MHA eye + iris meshes into Smash scrolling eyes"""
    bl_idname = 'sub.convert_mha_eyes'
    bl_label = 'Convert Eyes (MHA → Smash)'
    bl_description = (
        "Replace the MHA iris meshes with Smash's scrolling eyes: an iris decal on uvSet, a sclera "
        "texture with the painted iris removed, and EyeL/EyeR materials driven by CustomVector31"
    )
    bl_options = {'REGISTER', 'UNDO'}

    eye_material: EnumProperty(name='Eye Material', items=_eye_material_items)
    prefix: StringProperty(
        name='Texture Name', default='chara',
        description="Textures are named eye_<name>_w_col (eye white) and eye_<name>_bl/br_col (iris)",
    )
    decal_size: EnumProperty(
        name='Iris Texture', items=(('256', "256", ""), ('512', "512", ""), ('1024', "1024", "")), default='512',
        description="Resolution of the iris decal textures",
    )
    uv_scale: EnumProperty(
        name='Scroll Scale',
        items=(
            ('VANILLA', "Match Vanilla", "The eye spans as much of uvSet as Mario's, so CustomVector31 offsets from "
                                          "vanilla eye animations and the eye-look tools move the iris as far"),
            ('DETAIL', "Max Iris Detail", "The iris fills its texture; offsets move it less far across the eye"),
        ),
        default='VANILLA',
    )
    margin: FloatProperty(
        name='Iris Margin', default=1.3, min=1.05, max=3.0,
        description="Decal size relative to the iris. Larger leaves more transparent border around it",
    )
    paint_margin: FloatProperty(
        name='Paint-Out Margin', default=1.25, min=1.0, max=2.0,
        description="How far past the iris outline the painted pupil is removed from the eye texture",
    )
    variants: BoolProperty(
        name='Special-State Variants', default=True,
        description="Also make EyeLD/LG/LL and EyeRD/RG/RL (KO and glow eyes), like vanilla fighters",
    )
    reweight: BoolProperty(
        name='Move Eye-Bone Weights to Head', default=True,
        description="The iris no longer moves by bone, so L_eye / R_eye weights go to the bone above them",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None \
            and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        props = getattr(context.scene, 'sub_bake_texs_properties', None)
        tag = getattr(props, 'char_tag', '') if props is not None else ''
        if tag and self.prefix == 'chara':
            self.prefix = tag
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        active = context.active_object
        index = None if self.eye_material == 'AUTO' else int(self.eye_material)
        # Every selected mesh with eyes; the active one first, so it decides the textures.
        targets = [active] + [o for o in context.selected_objects if o is not active and o.type == 'MESH']
        targets = [o for o in targets if o is not None and o.type == 'MESH'
                   and (index is not None or guess_eye_material_index(o) is not None)]
        if not targets:
            self.report({'ERROR'}, "No selected mesh has an eye material.")
            return {'CANCELLED'}
        shared, eyes, lines, failed = {}, [], [], []
        for obj in targets:
            name = obj.name
            try:
                made, report = convert_eyes(
                    context, obj, operator=self,
                    eye_index=index if obj is active else None, prefix=self.prefix or 'chara',
                    decal_size=int(self.decal_size), margin=self.margin, paint_margin=self.paint_margin,
                    variants=self.variants, reweight=self.reweight, uv_scale=self.uv_scale, shared=shared)
            except RuntimeError as error:
                failed.append(f"{name}: {error}")
                continue
            eyes += made
            lines += [f"{name}: {line}" for line in report]
        for line in lines + failed:
            print('Eye Converter:', line)
        if not eyes:
            self.report({'ERROR'}, "; ".join(failed) or "Nothing converted.")
            return {'CANCELLED'}
        bpy.ops.object.select_all(action='DESELECT')
        for eye in eyes:
            eye.select_set(True)
        if eyes:
            context.view_layer.objects.active = eyes[0]
        summary = f"Converted {len(eyes)} eye(s) on {len(targets) - len(failed)} object(s)"
        if failed:
            summary += f"; {len(failed)} skipped (see console)"
        self.report({'WARNING'} if failed else {'INFO'}, summary + ". " + "; ".join(lines[:4]))
        return {'FINISHED'}
