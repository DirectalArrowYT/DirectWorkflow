"""HB Master Shader materials in the Smash Viewport.

The Smash Viewport draws Blender meshes through ssbh_wgpu, the game's own
shaders. For a material built on the HB Master Shader this module writes what
Bake Textures + export would ship, so the viewport shows the in-game look while
the material is still being tuned:

  _col  Base Color x Tint -> Hue/Saturation/Brightness -> AO tint. This is the
        group's COL stage, the part Bake Textures writes into _col.
  _prm  R Metalness (the SSS Mask on a subsurface shader), G Roughness,
        B AO (only with AO to PRM), A Specular. The same rules as the baker
        (core.MatlContext).
  _nor  The Normal input, when one is wired.
  _emi  Emission Color x Strength, when the Smash shader reads Texture5.

The toon part of the group (cel shadow, rim, highlight) is viewport-only in
Blender and never reaches the game, so it is left out on purpose: the game
lights the model itself.

Two ways to get the inputs:

  Quick  Inputs wired straight to an Image Texture are read from the image. No
         bake, so it works the moment the viewport opens. The group's own
         ray-traced AO is skipped (the AO Map input still applies).
  Baked  "Bake HB Preview" runs a small Cycles emit bake of every wired input
         and of the group's AO, so procedural inputs (color ramps, mixes, the
         hair gradient) and the AO come out exactly as Bake Textures would.

Either way the sliders (Tint, Hue, AO Strength/Color/Contrast, Metalness,
Roughness, Specular, SSS Mask, Emission) stay live: they are applied to the
cached inputs in numpy, so changing one only rewrites the small textures.
"""

from __future__ import annotations

import hashlib
import os
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import bpy
import numpy as np

from ...dependencies import ssbh_data_py

PREVIEW_SIZES = (512, 1024, 2048)
DEFAULT_SIZE = 1024
PREVIEW_SAMPLES = 16

# Inputs whose value is applied in numpy on every change (live).
_LIVE_FLOATS = (
    "Alpha", "Hue", "Saturation", "Brightness", "AO Strength", "AO Contrast",
    "AO Map", "Emission Strength", "Metalness", "Roughness", "Specular", "SSS Mask",
)
_LIVE_COLORS = ("Base Color", "Tint", "AO Color", "Emission Color")
_LIVE_BOOLS = ("AO to PRM",)
# Inputs that can carry a texture. Each one wired to a node is a "source".
_SOURCE_INPUTS = (
    ("Base Color", "color"),
    ("Alpha", "value"),
    ("AO Map", "value"),
    ("Emission Color", "color"),
    ("Metalness", "value"),
    ("Roughness", "value"),
    ("Specular", "value"),
    ("SSS Mask", "value"),
)

_bake_generation = [0]

# material name -> {"size", "sig", "arrays": {name: ndarray}, "ao": ndarray|None, "nor": ndarray|None}
_baked = {}
# (image pointer, name, filepath, size) -> float32 (H, W, 4), linear
_image_cache = {}


# =============================================================================
# Material lookup
# =============================================================================
def master_node(material):
    """The HB Master Shader node of a material, or None."""
    if material is None or not getattr(material, "use_nodes", False):
        return None
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    try:
        from ..bake_texs.master_shader import is_master_group
    except Exception:
        return None
    for node in tree.nodes:
        if getattr(node, "type", "") == "GROUP" and is_master_group(getattr(node, "node_tree", None)):
            return node
    return None


def _value(node, name, default):
    sock = node.inputs.get(name)
    if sock is None:
        return default
    try:
        value = sock.default_value
    except Exception:
        return default
    if hasattr(value, "__len__"):
        return tuple(float(v) for v in value)
    return float(value)


def _link_source(socket):
    """(node, from_socket) that drives an input, stepping through reroutes."""
    for _ in range(32):
        if socket is None or not getattr(socket, "is_linked", False):
            return None
        link = socket.links[0]
        if getattr(link, "is_muted", False) or not getattr(link, "is_valid", True):
            return None
        node = link.from_node
        if getattr(node, "type", "") == "REROUTE":
            socket = node.inputs[0] if node.inputs else None
            continue
        return node, link.from_socket
    return None


def _direct_image(socket):
    """(image, use_alpha) when an input is wired straight to an Image Texture."""
    found = _link_source(socket)
    if found is None:
        return None
    node, from_socket = found
    image = getattr(node, "image", None) if getattr(node, "type", "") == "TEX_IMAGE" else None
    if image is None:
        return None
    return image, getattr(from_socket, "name", "") == "Alpha"


def live_signature(material):
    """Everything the preview textures depend on that can change without a bake."""
    node = master_node(material)
    if node is None:
        return None
    parts = [material.name]
    for name in _LIVE_FLOATS + _LIVE_COLORS + _LIVE_BOOLS:
        sock = node.inputs.get(name)
        if sock is None:
            continue
        if sock.is_linked:
            found = _direct_image(sock)
            if found is not None:
                image = found[0]
                parts.append((name, "img", image.name, image.filepath, found[1]))
            else:
                parts.append((name, "linked"))
            continue
        value = _value(node, name, 0.0)
        if isinstance(value, tuple):
            value = tuple(round(v, 4) for v in value)
        else:
            value = round(value, 4)
        parts.append((name, value))
    normal = _link_source(node.inputs.get("Normal"))
    parts.append(("Normal", bool(normal)))
    baked = _baked.get(material.name)
    parts.append(("baked", baked["gen"] if baked else 0))
    data = _smash_data_signature(material)
    if data:
        parts.append(data)
    return tuple(parts)


def _smash_data_signature(material):
    sub = getattr(material, "sub_matl_data", None)
    label = getattr(sub, "shader_label", "") if sub is not None else ""
    return ("shader", label) if label else None


def preview_settings(scene=None):
    """(enabled, size) from the scene's Smash Viewport settings."""
    scene = scene or getattr(bpy.context, "scene", None)
    ssp = getattr(scene, "sub_scene_properties", None) if scene is not None else None
    if ssp is None:
        return True, DEFAULT_SIZE
    try:
        size = int(getattr(ssp, "smash_vp_hb_size", DEFAULT_SIZE))
    except Exception:
        size = DEFAULT_SIZE
    return bool(getattr(ssp, "smash_vp_hb_preview", True)), size


def preview_size(scene=None):
    return preview_settings(scene)[1]


def meshes_signature(meshes, scene=None):
    """Settings + live signature of every HB material on meshes (for rebuilds)."""
    enabled, size = preview_settings(scene)
    if not enabled:
        return ("hb", False)
    mats = {}
    for obj in meshes:
        for slot in getattr(obj, "material_slots", ()):
            mat = slot.material
            if mat is not None and mat.name not in mats:
                mats[mat.name] = mat
    sigs = []
    for name in sorted(mats):
        sig = live_signature(mats[name])
        if sig is not None:
            sigs.append(sig)
    return ("hb", size, tuple(sigs))


def _ptr(block):
    try:
        return int(block.as_pointer())
    except Exception:
        return id(block)


# =============================================================================
# Pixels
# =============================================================================
def _srgb_to_linear(c):
    c = np.clip(c, 0.0, None)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def _fit(width, height, size):
    """Largest power-of-two-ish shape no bigger than size, keeping the aspect."""
    scale = min(1.0, float(size) / float(max(width, height, 1)))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _resize(arr, width, height):
    """Box filter down (or nearest up) to (height, width)."""
    h, w = arr.shape[:2]
    if (w, h) == (width, height):
        return arr
    if w % width == 0 and h % height == 0:
        fy, fx = h // height, w // width
        shape = (height, fy, width, fx) + arr.shape[2:]
        return arr.reshape(shape).mean(axis=(1, 3)).astype(np.float32)
    ys = np.minimum((np.arange(height) + 0.5) * h / height, h - 1).astype(np.int64)
    xs = np.minimum((np.arange(width) + 0.5) * w / width, w - 1).astype(np.int64)
    return arr[ys][:, xs]


def _image_linear(image, size):
    """(H, W, 4) float32 of an image as the shader reads it (linear), capped to size."""
    try:
        w, h = int(image.size[0]), int(image.size[1])
        if w == 0 or h == 0:
            # Touching pixels loads a file that has not been read yet.
            len(image.pixels)
            w, h = int(image.size[0]), int(image.size[1])
    except Exception:
        return None
    if w == 0 or h == 0:
        return None
    key = (_ptr(image), image.name, image.filepath, w, h, size)
    cached = _image_cache.get(key)
    if cached is not None:
        return cached
    try:
        px = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(px)
    except Exception:
        return None
    px = px.reshape(h, w, 4)
    tw, th = _fit(w, h, size)
    px = _resize(px, tw, th)
    colorspace = ""
    try:
        colorspace = (image.colorspace_settings.name or "").lower()
    except Exception:
        pass
    if not getattr(image, "is_float", False) and colorspace in ("srgb", "srgb 2.2"):
        px = px.copy()
        px[..., :3] = _srgb_to_linear(px[..., :3])
    # Drop stale entries for this image (another size or a reloaded file).
    for old in [k for k in _image_cache if k[0] == key[0] and k != key]:
        _image_cache.pop(old, None)
    _image_cache[key] = px
    return px


def _encode_png(path, rgba8):
    h, w = rgba8.shape[:2]
    rows = np.empty((h, w * 4 + 1), dtype=np.uint8)
    rows[:, 0] = 0
    rows[:, 1:] = rgba8.reshape(h, w * 4)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        handle.write(chunk(b"IDAT", zlib.compress(rows.tobytes(), 1)))
        handle.write(chunk(b"IEND", b""))


def _tex_cli():
    try:
        from ..model.material.texture.convert_nutexb_to_png import get_ultimate_tex_path
        cli = Path(get_ultimate_tex_path())
    except Exception:
        return None
    if not cli.is_file():
        return None
    if sys.platform != "win32" and not os.access(cli, os.X_OK):
        try:
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            return None
    return cli


def write_nutexb(folder, stem, rgba_float, srgb):
    """Write (H, W, 4) 0..1 values as an uncompressed .nutexb. Returns the name or None.

    Rows are Blender order (bottom first); textures are stored top first.
    Uncompressed RGBA8 keeps the write fast enough to follow a slider.
    """
    cli = _tex_cli()
    if cli is None:
        return None
    data = np.ascontiguousarray(rgba_float[::-1])
    if srgb:
        data = data.copy()
        data[..., :3] = _linear_to_srgb(data[..., :3])
    rgba8 = (np.clip(data, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    digest = hashlib.sha1(rgba8.tobytes()).hexdigest()[:10]
    name = f"{stem}_{digest}"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{name}.nutexb"
    if out.is_file():
        return name
    png = folder / f"{name}.png"
    try:
        _encode_png(png, rgba8)
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            [str(cli), str(png), str(out), "--format",
             "Rgba8UnormSrgb" if srgb else "Rgba8Unorm", "--no-mipmaps"],
            capture_output=True, check=True, **kwargs,
        )
    except Exception:
        return None
    finally:
        try:
            png.unlink()
        except Exception:
            pass
    return name if out.is_file() else None


def prune_textures(folder, keep):
    """Remove preview textures in folder that no material uses any more."""
    try:
        for path in Path(folder).glob("hbvp_*.nutexb"):
            if path.stem not in keep:
                path.unlink()
    except Exception:
        pass


# =============================================================================
# Inputs: quick (images) or baked
# =============================================================================
def _source_array(material, node, name, kind, size):
    """(array or None, exact) for one wired input.

    array: (H, W, 3) linear colour or (H, W) value. None = use the socket value.
    exact: False when the input is wired to something the quick path can't read.
    """
    sock = node.inputs.get(name)
    if sock is None or not sock.is_linked:
        return None, True
    baked = _baked.get(material.name)
    if baked is not None and name in baked["arrays"]:
        return baked["arrays"][name], True
    found = _direct_image(sock)
    exact = found is not None
    if found is None and kind == "color":
        # Something between the image and the input (a mix, a color ramp):
        # show the image under it rather than nothing.
        try:
            from .smash_viewport import _follow_color_socket
            follow = _follow_color_socket(sock)
        except Exception:
            follow = None
        if follow:
            found = (follow[0], False)
    if found is None:
        return None, False
    px = _image_linear(found[0], size)
    if px is None:
        return None, False
    if kind == "color":
        return px[..., :3], exact
    if found[1]:
        return px[..., 3], exact
    return px[..., :3].mean(axis=2), exact


def material_status(material):
    """'baked', 'quick', 'approx' (needs a bake for an exact look) or None."""
    node = master_node(material)
    if node is None:
        return None
    if material.name in _baked:
        return "baked"
    for name, _kind in _SOURCE_INPUTS:
        sock = node.inputs.get(name)
        if sock is not None and sock.is_linked and _direct_image(sock) is None:
            return "approx"
    return "quick"


# =============================================================================
# Compose the Smash maps
# =============================================================================
def _hsv_adjust(rgb, hue, sat, val):
    """Blender's Hue/Saturation/Value node on linear RGB."""
    if abs(hue - 0.5) < 1e-6 and abs(sat - 1.0) < 1e-6 and abs(val - 1.0) < 1e-6:
        return rgb
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax = np.max(rgb, axis=-1)
    cmin = np.min(rgb, axis=-1)
    delta = cmax - cmin
    v = cmax
    s = np.where(cmax > 0.0, delta / np.where(cmax > 0.0, cmax, 1.0), 0.0)
    safe = np.where(delta > 0.0, delta, 1.0)
    rc = (cmax - r) / safe
    gc = (cmax - g) / safe
    bc = (cmax - b) / safe
    h = np.where(r == cmax, bc - gc, np.where(g == cmax, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.where(delta > 0.0, (h / 6.0) % 1.0, 0.0)
    h = (h + hue + 0.5) % 1.0
    s = np.clip(s * sat, 0.0, 1.0)
    v = v * val
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i.astype(np.int64) % 6
    out = np.empty_like(rgb)
    choices_r = (v, q, p, p, t, v)
    choices_g = (t, v, v, q, p, p)
    choices_b = (p, p, t, v, v, q)
    out[..., 0] = np.choose(i, choices_r)
    out[..., 1] = np.choose(i, choices_g)
    out[..., 2] = np.choose(i, choices_b)
    return out


def _common_shape(arrays, size):
    shapes = [a.shape[:2] for a in arrays if a is not None]
    if not shapes:
        return None
    h, w = max(shapes, key=lambda s: s[0] * s[1])
    w, h = _fit(w, h, size)
    return w, h


def _as_map(value, arr, shape, channels):
    """Broadcast a socket value or an array to (H, W[, channels])."""
    w, h = shape
    if arr is None:
        if channels:
            return np.broadcast_to(np.array(value[:channels], dtype=np.float32), (h, w, channels))
        return np.full((h, w), float(value), dtype=np.float32)
    return _resize(arr, w, h).astype(np.float32)


def compose(material, size=DEFAULT_SIZE):
    """The Smash maps for an HB Master material.

    Returns {"col", "prm", "nor", "emi", "cv3", "subsurface", "reads_emissive",
    "status"}; each map is (H, W, 4) float 0..1 (col linear), nor/emi may be None.
    """
    node = master_node(material)
    if node is None:
        return None
    arrays = {}
    exact = True
    for name, kind in _SOURCE_INPUTS:
        arr, ok = _source_array(material, node, name, kind, size)
        arrays[name] = arr
        exact = exact and ok
    baked = _baked.get(material.name)
    ao_raw = baked.get("ao") if baked else None
    nor = baked.get("nor") if baked else None
    if nor is None and _link_source(node.inputs.get("Normal")) is not None:
        nor = _quick_normal(node, size)

    shape = _common_shape(list(arrays.values()) + [ao_raw, nor], size) or (4, 4)

    base = _as_map(_value(node, "Base Color", (0.8, 0.8, 0.8, 1.0)), arrays["Base Color"], shape, 3)
    tint = np.array(_value(node, "Tint", (1.0, 1.0, 1.0, 1.0))[:3], dtype=np.float32)
    c1 = _hsv_adjust(base * tint, _value(node, "Hue", 0.5), _value(node, "Saturation", 1.0),
                     _value(node, "Brightness", 1.0))

    # Ambient occlusion: the group's ray-traced AO (baked only), remapped by
    # AO Contrast, times the AO Map input.
    contrast = min(_value(node, "AO Contrast", 0.3), 0.999)
    if ao_raw is not None:
        openness = np.clip((_resize(ao_raw, *shape) - contrast) / (1.0 - contrast), 0.0, 1.0)
    else:
        openness = np.ones((shape[1], shape[0]), dtype=np.float32)
    openness = openness * _as_map(_value(node, "AO Map", 1.0), arrays["AO Map"], shape, 0)
    occl = np.clip((1.0 - openness) * _value(node, "AO Strength", 0.6), 0.0, 1.0)[..., None]
    ao_color = np.array(_value(node, "AO Color", (0.5, 0.35, 0.35, 1.0))[:3], dtype=np.float32)
    col_rgb = c1 + (c1 * ao_color - c1) * occl
    alpha = _as_map(_value(node, "Alpha", 1.0), arrays["Alpha"], shape, 0)
    col = np.concatenate([col_rgb, alpha[..., None]], axis=-1)

    context = _matl_context(material)
    subsurface = bool(context and context.is_subsurface)
    reads_emissive = bool(context and context.reads_emissive)
    red_name = "SSS Mask" if subsurface else "Metalness"
    red = _as_map(_value(node, red_name, 0.0), arrays[red_name], shape, 0)
    rough = _as_map(_value(node, "Roughness", 0.6), arrays["Roughness"], shape, 0)
    if bool(_value(node, "AO to PRM", 0.0)):
        prm_b = openness
    else:
        prm_b = np.ones_like(red)
    spec = _as_map(_value(node, "Specular", 0.16), arrays["Specular"], shape, 0)
    prm = np.stack([red, rough, prm_b, spec], axis=-1)

    nor_map = None
    if nor is not None:
        nor = _resize(nor, *shape)
        nor_map = np.concatenate(
            [nor[..., :2], np.ones(nor.shape[:2] + (2,), dtype=np.float32)], axis=-1)

    emi = None
    cv3 = None
    if reads_emissive:
        strength = _value(node, "Emission Strength", 1.0)
        color = _as_map(_value(node, "Emission Color", (0.0, 0.0, 0.0, 1.0)),
                        arrays["Emission Color"], shape, 3) * strength
        peak = float(color.max()) if color.size else 0.0
        if peak > 1.0:
            color = color / peak
            cv3 = peak
        emi = np.concatenate([color, np.ones(color.shape[:2] + (1,), dtype=np.float32)], axis=-1)

    status = "baked" if baked else ("quick" if exact else "approx")
    return {"col": col, "prm": prm, "nor": nor_map, "emi": emi, "cv3": cv3,
            "subsurface": subsurface, "reads_emissive": reads_emissive, "status": status}


def _quick_normal(node, size):
    """A Normal Map node fed straight by an image: its tangent-space RGB."""
    found = _link_source(node.inputs.get("Normal"))
    if found is None:
        return None
    nmap = found[0]
    if getattr(nmap, "type", "") != "NORMAL_MAP":
        return None
    color = nmap.inputs.get("Color")
    direct = _direct_image(color)
    if direct is None:
        return None
    px = _image_linear(direct[0], size)
    return None if px is None else px[..., :3]


def shader_is_subsurface(shader_label):
    try:
        from ..model.material import shader_info
        return bool(shader_info.uses_param(shader_label, "CustomVector30"))
    except Exception:
        return True


def _matl_context(material):
    try:
        from ..bake_texs import core
        return core.MatlContext(material)
    except Exception:
        return None


# =============================================================================
# Smash material entry
# =============================================================================
class _Quiet:
    def report(self, *_args, **_kwargs):
        pass


def _default_texture(param):
    try:
        from ..model.export_model import default_texture
        return default_texture(param)
    except Exception:
        return "/common/shader/sfxpbs/default_white"


def matl_entry(label, material, textures, cv3=None):
    """The Smash material export would write, with the preview textures in it.

    textures: {"Texture0": name, ...}. Every other texture slot gets the neutral
    default, since the preview folder only holds the maps written here.
    """
    matl = ssbh_data_py.matl_data
    entry = None
    context = _matl_context(material)
    source = getattr(context, "data_source", material) if context else material
    sub = getattr(source, "sub_matl_data", None)
    if sub is not None and getattr(sub, "shader_label", ""):
        try:
            from ..model.material.create_matl_from_blender_materials import (
                create_matl_entry_from_sub_matl_data,
            )
            entry = create_matl_entry_from_sub_matl_data(_Quiet(), label, sub)
        except Exception:
            entry = None
    if entry is None:
        from ..model.export_model import default_ssbh_material
        entry = default_ssbh_material(label)
    new_textures = []
    for tex in entry.textures:
        param = tex.param_id.name
        tex.data = textures.get(param) or _default_texture(param)
        new_textures.append(tex)
    entry.textures = new_textures
    # Preview textures repeat like the exported ones would.
    for sampler in entry.samplers:
        try:
            sampler.data.wraps = matl.WrapMode.from_str("Repeat")
            sampler.data.wrapt = matl.WrapMode.from_str("Repeat")
            sampler.data.wrapr = matl.WrapMode.from_str("Repeat")
        except Exception:
            pass
    return entry


# material name -> (key, {param: texture name}, status)
_texture_cache = {}


def texture_stem(material):
    digest = hashlib.sha1(material.name.encode("utf-8")).hexdigest()[:6]
    token = "".join(ch if ch.isalnum() else "_" for ch in material.name)[-20:]
    return f"{token}_{digest}"


def build_textures(material, folder, size, stem=None):
    """Write the maps for one material. Returns ({param: name}, {"status": ...}).

    A material whose inputs have not changed since the last build reuses its
    files, so one slider only rewrites that material's maps.
    """
    stem = stem or texture_stem(material)
    key = (live_signature(material), size, str(folder))
    cached = _texture_cache.get(material.name)
    if cached is not None and cached[0] == key and all(
            (Path(folder) / f"{name}.nutexb").is_file() for name in cached[1].values()):
        return dict(cached[1]), dict(cached[2])
    result = compose(material, size)
    if result is None:
        return None, None
    names = {}
    col = write_nutexb(folder, f"hbvp_{stem}_col", result["col"], srgb=True)
    if col:
        names["Texture0"] = col
    prm = write_nutexb(folder, f"hbvp_{stem}_prm", result["prm"], srgb=False)
    if prm:
        names["Texture6"] = prm
    if result["nor"] is not None:
        nor = write_nutexb(folder, f"hbvp_{stem}_nor", result["nor"], srgb=False)
        if nor:
            names["Texture4"] = nor
    if result["emi"] is not None:
        emi = write_nutexb(folder, f"hbvp_{stem}_emi", result["emi"], srgb=True)
        if emi:
            names["Texture5"] = emi
            names["Texture14"] = emi
    _texture_cache[material.name] = (
        key, dict(names), {"status": result["status"], "subsurface": result["subsurface"]})
    return names, result


# =============================================================================
# Bake HB Preview (Cycles)
# =============================================================================
def _bake_uv_name(obj):
    layers = getattr(obj.data, "uv_layers", None)
    if not layers:
        return None
    layer = layers.get("map1") or layers.get("UVMap") or layers.active or layers[0]
    return layer.name if layer is not None else None


def bake_sources(context, objects, size=DEFAULT_SIZE, report=None):
    """Emit-bake every wired HB Master input (and the group's AO) of the materials
    on objects into the preview cache. Returns the number of materials baked."""
    from ..bake_texs import core

    scene = context.scene
    mats = {}
    for obj in objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if master_node(mat) is not None:
                mats.setdefault(mat, []).append(obj)
    if not mats:
        return 0

    view_layer = context.view_layer
    old_active = view_layer.objects.active
    old_selected = [o for o in view_layer.objects if o.select_get()]
    old_uv = {}
    state = core.save_scene_state(scene)
    scratch = bpy.data.images.new("__HBVP_SCRATCH__", 8, 8, alpha=True, float_buffer=True)
    baked_count = 0
    # A preview does not need final-bake noise levels: the AO node already
    # takes its own 16 rays per sample.
    old_samples = core.EMIT_SAMPLES_STOCHASTIC
    core.EMIT_SAMPLES_STOCHASTIC = PREVIEW_SAMPLES
    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        core.ensure_cycles()
        for mat, users in mats.items():
            node = master_node(mat)
            for obj in users:
                layers = obj.data.uv_layers
                name = _bake_uv_name(obj)
                if name and obj.data.name not in old_uv and layers.active is not None:
                    old_uv[obj.data.name] = (obj.data, layers.active.name)
                    layers.active = layers[name]
            for o in view_layer.objects:
                o.select_set(False)
            for obj in users:
                try:
                    obj.hide_set(False)
                except Exception:
                    pass
                obj.select_set(True)
            view_layer.objects.active = users[0]

            arrays = {}
            for name, kind in _SOURCE_INPUTS:
                sock = node.inputs.get(name)
                if sock is None or not sock.is_linked:
                    continue
                channel = core.channel_from_input(sock, f"HB preview {name}")
                if channel.mode != "SOCKET":
                    continue
                px = _bake_channel(scene, users, mat, channel, size, scratch)
                if px is None:
                    continue
                arrays[name] = px[..., :3] if kind == "color" else px[..., :3].mean(axis=2)

            ao = None
            inner = next((n for n in node.node_tree.nodes if n.type == "AMBIENT_OCCLUSION"), None)
            if inner is not None:
                channel = core.Channel("SOCKET", socket=inner.outputs["AO"], path=(node,),
                                       origin="HB preview AO")
                px = _bake_channel(scene, users, mat, channel, size, scratch)
                if px is not None:
                    ao = px[..., :3].mean(axis=2)

            nor = None
            if _link_source(node.inputs.get("Normal")) is not None:
                image = _bake_image(f"__HBVP_{mat.name}_nor", size)
                try:
                    core.bake_pass(scene, users, [mat], "NORMAL", image, "Non-Color", scratch)
                    nor = _read_image(image)[..., :3]
                except Exception as exc:
                    if report:
                        report({"WARNING"}, f"{mat.name}: normal bake failed ({exc})")
                finally:
                    bpy.data.images.remove(image)

            _bake_generation[0] += 1
            _baked[mat.name] = {"gen": _bake_generation[0], "size": size, "arrays": arrays, "ao": ao, "nor": nor,
                                 "name": mat.name}
            baked_count += 1
    finally:
        core.EMIT_SAMPLES_STOCHASTIC = old_samples
        core.restore_scene_state(scene, state)
        for mesh, name in old_uv.values():
            try:
                mesh.uv_layers.active = mesh.uv_layers[name]
            except Exception:
                pass
        for o in view_layer.objects:
            try:
                o.select_set(o in old_selected)
            except Exception:
                pass
        try:
            view_layer.objects.active = old_active
        except Exception:
            pass
        try:
            bpy.data.images.remove(scratch)
        except Exception:
            pass
    return baked_count


def _bake_image(name, size):
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    image = bpy.data.images.new(name, size, size, alpha=True, float_buffer=True)
    try:
        image.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    return image


def _read_image(image):
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def _bake_channel(scene, objects, mat, channel, size, scratch):
    from ..bake_texs import core
    image = _bake_image(f"__HBVP_{mat.name}", size)
    try:
        core.bake_members(scene, objects, [(mat, channel)], image, "Non-Color", scratch)
        return _read_image(image)
    except Exception:
        return None
    finally:
        bpy.data.images.remove(image)


def clear_baked(materials=None):
    if materials is None:
        _baked.clear()
        return
    for mat in materials:
        _baked.pop(mat.name, None)


def baked_size(material):
    entry = _baked.get(material.name)
    return entry["size"] if entry else 0
