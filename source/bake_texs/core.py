# =============================================================================
# BakeTexs core - Smash Ultimate COL / NOR / PRM baker
#
# Ported from the standalone SmashScripts/BakeTexs.py Text-Editor script into
# a real addon module. The baking algorithm itself (node graph walking,
# channel resolution, bake plumbing, image assembly) is unchanged from that
# script - only the settings source changed: what used to be module-level
# CONFIG constants edited by hand are now read from SUB_PG_bake_texs_settings
# via apply_settings(), called once by the operator before bake_all() runs.
#
# WHAT IT DOES
#   For every material on the selected meshes it bakes:
#     <prefix><name>_col.png   RGB = albedo / baked toon color, A = alpha
#     <prefix><name>_nor.png   RG  = tangent normal, B = transition, A = cavity
#     <prefix><name>_prm.png   R=metal G=rough B=ao A=spec
#
#   Node-group setups - a custom toon group whose Result plugs into Surface,
#   or the addon's "Smash Ultimate Master Shader" - are walked into, not just
#   materials with a Principled/Glossy wired straight to Material Output.
#
# WHERE PRM VALUES COME FROM, in increasing order of priority
#   1. A Principled found anywhere in the material or its groups, even when it
#      only feeds a Shader-to-RGB toon ramp rather than Surface. Its Metallic /
#      Roughness / IOR are read per material: if the group reads them from its
#      Group Input, the value taken is the one set on that material's own
#      instance of the group, so one shared group still gives every material
#      its own PRM. Name a BSDF "BAKE_SHADER" to pick which one, if several.
#   2. Override nodes. Drop a Value node (metal/rough/spec) or an RGB node
#      (col/alpha) anywhere in the material OR inside any node group it uses,
#      and set its node NAME (not label) to BAKE_METAL / BAKE_ROUGH /
#      BAKE_SPEC / BAKE_COL / BAKE_ALPHA. Its output is baked for that channel.
#   3. Per-material presets set via the panel, for flat per-material constants.
#
#   The HB Master Shader (master_shader.py) skips step 1: its Smash PRM panel
#   is already in PRM units, so those sockets are read straight off the
#   material's instance of the group. Override nodes and presets still win.
# =============================================================================

import bpy
import os
import re
import random
import numpy as np
from math import isfinite


# =============================================================================
# SETTINGS
#
# These mirror the standalone script's CONFIG block. apply_settings() copies
# from the scene's SUB_PG_bake_texs_settings into these module globals right
# before a bake, the same way the old RunBakeTexs.py loader's OVERRIDES dict
# used to override the script's own constants for one run.
# =============================================================================
BAKE_SIZE        = 2048
BAKE_MARGIN      = 16
BAKE_MARGIN_TYPE = 'EXTEND'          # 'EXTEND' (best for atlases) or 'ADJACENT_FACES'
BAKE_DIR         = None              # set from //bakes relative to the .blend at bake time
FILE_FORMAT      = "PNG"
OVERWRITE_FILES  = True

CHAR_TAG  = "Character"
NAME_FROM = "MATERIAL"               # "MATERIAL" or "OBJECT"

# First keyword found in (object name + material name) wins.
PREFIX_RULES = [
    (["hair", "haircolor", "alp"], "alp_"),
]
DEFAULT_PREFIX = "def_"

# Force an exact output stem for a material, bypassing prefix/tag rules.
#   "MII_Pl39000_hair": "alp_hair_shigaraki"   ->  alp_hair_shigaraki_col.png
NAME_OVERRIDES = {}

# Material names to leave alone entirely - an outline shell is geometry, not
# a surface that wants a texture set, and its master shader still points at
# the importer's default placeholders, so baking it produces garbage.
SKIP_MATERIALS = set()

# Materials that are the same surface with a different finish - cloth vs metal -
# don't each need their own texture set. Bundled materials bake into a single
# col/nor/prm named after the base material, each filling its own UV islands.
# This grouping is shared with model export (create_matl_from_blender_materials.py,
# export_nutexb.py) so a bundled bake's file names always match what export
# looks for - see source/material_grouping.py.
from ..material_grouping import MATERIAL_MERGE_SUFFIXES, MATERIAL_GROUPS, group_key_for

USE_SELECTION_ONLY = True
DRY_RUN = False

WRITE_COL = True
WRITE_NOR = True
WRITE_PRM = True
WRITE_EMI = True
WRITE_COMPONENT_DEBUG_MAPS = False

# ---- Smash EMI ---------------------------------------------------------------
# Texture5 / Texture14. 27,567 of the 108,192 vanilla fighter materials carry an
# emissive map, so this is not a niche channel - but the bake pipeline had no
# way to produce one, and any emission authored in Blender was silently dropped.
#
# EMI is written only for materials whose shader actually reads Texture5; a
# material on a plain PRM shader has nowhere to put one. "AUTO" follows the
# shader, "ALWAYS" writes one regardless (useful when you intend to move the
# material onto an emissive shader afterwards), "NEVER" disables the channel.
EMI_MODE = "AUTO"

# The Principled's Emission Strength routinely exceeds 1.0, which an 8-bit
# texture cannot hold. Rather than clipping, the map is divided by its own peak
# and that peak is reported as the CustomVector3 value which restores it -
# which is exactly what CustomVector3 is for, and why vanilla emissive
# materials so often carry values above 1. Turn this off to clamp instead.
EMI_NORMALIZE_TO_CV3 = True
EMI_DEFAULT = (0.0, 0.0, 0.0, 1.0)

# ---- Smash PRM --------------------------------------------------------------
# PRM.r -> Principled Metallic (1:1). PRM.g -> Roughness (1:1, no remap).
# PRM.b -> ambient occlusion. PRM.a -> specular, scaled by the master shader's
# Specular Boost - Blender's default Specular IOR Level of 0.5 round-trips to
# PRM.a 0.2, and vanilla Smash's usual 0.16 corresponds to Blender IOR 0.4.
PRM_SPECULAR_SCALE = 1.0 / 2.5

# Derive PRM.a from the Principled's IOR instead of assuming IOR 1.5. Off by
# default - only correct when IOR means reflectance, not when it's a toon
# ramp look knob (an IOR of 2.6 measured PRM.a 0.988 against a vanilla ~0.16).
PRM_SPEC_FROM_IOR = False

PRM_ROUGHNESS_EXPORT_SQRT = False
PRM_ROUGHNESS_MIN         = 0.01

# "AUTO" bakes AO only when a real PBR shader drives the surface - toon/color
# setups already have their shading burned into COL. "BAKE" always bakes AO.
# "CONST" always uses PRM_AO_CONST.
PRM_AO_MODE  = "AUTO"
PRM_AO_CONST = 1.0
PRM_AO_FLOOR = 0.35

PRM_FALLBACK_METAL = 0.0
PRM_FALLBACK_ROUGH = 0.5
PRM_FALLBACK_SPEC  = 0.16 / PRM_SPECULAR_SCALE

# ---- PRM.r on a subsurface shader -------------------------------------------
# A Blender material's inputs serve the Blender render. In a stylised or toon
# setup, Metallic is frequently turned up because of what it does to the shaded
# result that gets baked into COL - it is a look knob, and its value is a
# statement about colour, not about metal.
#
# Smash reads the same channel very differently. On a subsurface shader PRM.r
# is the SSS mask, and Textures.md records vanilla skin setting it to 1. Piping
# a Blender look knob into it produces a mask of 0.35 - subsurface almost off -
# while the COL bake that the knob was actually for comes out fine, so nothing
# looks wrong until the model is in game.
#
# "AUTO" keeps the two apart: COL bakes from the Blender shader exactly as
# before, and PRM.r gets the vanilla mask unless something in the material
# genuinely drives subsurface (a texture in Subsurface Weight, or a non-zero
# constant there). "BLENDER" restores the old behaviour of taking whatever the
# node graph offers.
PRM_SKIN_MASK_MODE  = "AUTO"
PRM_SKIN_MASK_CONST = 1.0

# Flat per-material constants, the last word over everything else.
#   "Hair": dict(metal=0.0, rough=0.35, spec=0.5, ior=1.5)
MATERIAL_PRESETS = {}

# ---- Smash NOR --------------------------------------------------------------
NOR_BLUE_MODE  = "CONST"             # "CONST" or "Z_FROM_BAKE"
NOR_BLUE_CONST = 1.0
NOR_ALPHA_MODE = "CONST"             # "CONST", "FROM_AO", or "FROM_CAVITY"
NOR_ALPHA_CONST = 1.0
NOR_CAVITY_DISTANCE = 0.05
NOR_FLIP_GREEN = False

# ---- nutexb compile ----------------------------------------------------------
COMPILE_NUTEXB   = True

# Confirmed against the shipped assets: 79 _col files are BC7RgbaUnormSrgb,
# 81 _nor and 66 _prm are BC7RgbaUnorm. COL holds colour so it needs the sRGB
# variant; NOR and PRM hold data and must stay linear.
NUTEXB_FORMATS = {
    "_col": "BC7RgbaUnormSrgb",
    "_nor": "BC7RgbaUnorm",
    "_prm": "BC7RgbaUnorm",
    # Emissive maps hold colour, so they take the sRGB variant like _col.
    # Matches expects_srgb() in SSBH Editor, which treats every texture slot
    # except Texture2/4/6/7/16 as colour.
    "_emi": "BC7RgbaUnormSrgb",
}

# The bundled ultimate_tex_cli (0.3.1) accepts an unknown --format silently:
# it exits 0 and quietly writes BC7RgbaUnorm instead - confirmed directly,
# a deliberately bogus format name still produced a file (format id 1248).
# Every name is checked against this list first, and the format actually
# written into each file is read back afterwards (NUTEXB_FORMAT_IDS) to
# confirm it took, since a silent wrong-format write is otherwise invisible.
NUTEXB_VALID_FORMATS = {
    "Rgba8Unorm", "Rgba8UnormSrgb", "Bgra8Unorm", "Bgra8UnormSrgb",
    "Rgba16Float", "Rgba32Float",
    "BC1RgbaUnorm", "BC1RgbaUnormSrgb", "BC2RgbaUnorm", "BC2RgbaUnormSrgb",
    "BC3RgbaUnorm", "BC3RgbaUnormSrgb", "BC4RUnorm", "BC4RSnorm",
    "BC5RgUnorm", "BC5RgSnorm", "BC6hRgbUfloat", "BC6hRgbSfloat",
    "BC7RgbaUnorm", "BC7RgbaUnormSrgb",
}
NUTEXB_FORMAT_IDS = {          # value stored in the nutexb footer, for verifying
    1024: "Rgba8Unorm", 1029: "Rgba8UnormSrgb",
    1157: "BC1RgbaUnormSrgb", 1248: "BC7RgbaUnorm", 1253: "BC7RgbaUnormSrgb",
}
NUTEXB_MIPMAPS = True

# The Switch filesystem is case-sensitive; when a .nutexb already exists that
# differs only by case, reuse its exact name so it keeps matching whatever
# the numatb references.
NUTEXB_MATCH_EXISTING_CASE = True

# ---- Texels outside every UV island -----------------------------------------
FILL_UNCOVERED_TEXELS = True
COL_DEFAULT = (0.0, 0.0, 0.0, 0.0)
NOR_DEFAULT = (0.5, 0.5, 1.0, 1.0)
PRM_DEFAULT = (0.0, 0.5, 1.0, 0.16)
SKIP_EMPTY_MATERIALS = True

# ---- Sampling ---------------------------------------------------------------
EMIT_SAMPLES_FLAT       = 1
EMIT_SAMPLES_STOCHASTIC = 256
# AO is genuinely ray-bound - measured against a 1024-sample reference:
#   32spp  1.4s  5.03/255 error      128spp  3.8s  2.07/255
#   64spp  2.2s  3.17/255            256spp  6.4s  1.30/255
# Roughly linear in both time and accuracy, so there is no free lunch here;
# drop it only if you want the speed and can live with the error. Adaptive
# sampling made no measurable difference to AO either way.
AO_SAMPLES              = 256
BAKE_ADAPTIVE_SAMPLING  = False

# Skip the tangent-space normal bake when nothing in the material can perturb
# the shading normal (see normal_bake_would_be_flat). Each bake call costs
# ~0.5s of fixed setup before tracing a ray, so this is worth real time on a
# character whose materials carry no normal detail.
#
# Not quite free: geometry can still bake a hair off flat where a UV seam
# flips the tangent basis. Measured on this character's hair - 14 texels out
# of 262,144 (0.005%) came back 129 instead of 128 in the red channel. Every
# other map was bit-identical. Set this False if you want the bake to be
# exact rather than 1/255 off on a handful of seam texels.
SKIP_FLAT_NORMAL_BAKE   = True
BAKE_ADAPTIVE_THRESHOLD = 0.002

# ---- Debug --------------------------------------------------------------
DEBUG_PRINT_GRAPH        = True
DEBUG_SAMPLE_PIXELS      = True
DEBUG_SAMPLE_COUNT       = 8
DEBUG_WRITE_DEBUG_REPORT = True

OVERRIDE_NODE_BASECOLOR = "BAKE_COL"
OVERRIDE_NODE_ALPHA     = "BAKE_ALPHA"
OVERRIDE_NODE_METAL     = "BAKE_METAL"
OVERRIDE_NODE_ROUGH     = "BAKE_ROUGH"
OVERRIDE_NODE_SPEC      = "BAKE_SPEC"
OVERRIDE_NODE_SHADER    = "BAKE_SHADER"

MASTER_SHADER_PREFIX = "Smash Ultimate Master Shader"
SUB_SOCKET_COL_RGB   = "Texture0 RGB (Col Map Layer 1)"
SUB_SOCKET_COL_A     = "Texture0 Alpha (Col Map Layer 1)"
SUB_SOCKET_NOR_RGB   = "Texture4 RGB (NOR Map)"
SUB_SOCKET_NOR_A     = "Texture4 Alpha (NOR Map Cavity Channel)"
SUB_SOCKET_PRM_RGB   = "Texture6 RGB (PRM Map)"
SUB_SOCKET_PRM_A     = "Texture6 Alpha (PRM Map Specular)"
SUB_SOCKET_EMI_RGB   = "Texture5 RGB (Emissive Map Layer 1)"

# master_shader.MASTER_NAME. Its internal Principled is named BAKE_SHADER, so
# the generic walk already lands on it; this prefix is what lets the PRM
# channels come from the instance sockets instead.
HB_MASTER_PREFIX     = "HB Master Shader"


# =============================================================================
# WHAT THE TARGET MATERIAL'S SHADER EXPECTS
#
# The baker used to be purely a Blender-node -> PNG converter: it read the node
# graph and wrote col/nor/prm the same way for every material, never looking at
# the Smash shader those files were about to be used by. That is wrong for
# three documented cases, all of which change what a PRM channel MEANS:
#
#   PRM.a   is the specular value only while CustomBoolean1 is on and
#           CustomFloat10 (anisotropy) is zero. With anisotropy on, the shader
#           reads it as a highlight rotation angle instead, so a specular map
#           baked there becomes a field of random rotations.
#   PRM.r   is metalness on a normal shader, but on a subsurface shader it is
#           the SSS mask. Baking Metallic there leaves skin at 0 and the
#           subsurface effect never appears.
#   EMI     only exists if the shader reads Texture5.
#
# See Smush-Material-Research/Textures.md and Material Parameters.md.
# =============================================================================
from ..model.material import shader_info


class MatlContext:
    """What the material's Smash shader expects from the maps being baked.

    Every field degrades to the plain PBR interpretation when the material has
    no sub_matl_data yet, so an un-set-up material bakes exactly as it did
    before this existed.
    """

    def __init__(self, material):
        self.material = material
        self.data_source = material
        self.shader_label = ''
        self.anisotropy = 0.0
        self.prm_alpha_is_used = True
        self.is_subsurface = False
        self.reads_emissive = False
        self.notes = []

        # Read the same material data export will read. A side-loaded twin is
        # never assigned to a mesh, so walking material slots only ever finds
        # the live material - and when the twin is the one carrying the real
        # shader (the whole point of side-loading), every decision below was
        # being made against the wrong material. That silently disabled all of
        # it: a Skin preset applied to the twin left the baker still writing
        # metalness into PRM.r, because as far as it could see the material was
        # on a plain PBR shader.
        # Guarded: the scene property group is not guaranteed to be attached
        # yet depending on when this runs, and losing the side-load resolution
        # is far better than the whole bake dying over a missing preference.
        try:
            from ..model.material.create_matl_from_blender_materials import (
                resolve_material_data_source,
            )
            scene_props = getattr(bpy.context.scene, 'sub_scene_properties', None)
            prefer_side_loaded = bool(
                scene_props and scene_props.export_prefer_sideloaded_materials)
            self.data_source = resolve_material_data_source(material, prefer_side_loaded)
        except Exception as e:
            self.notes.append(f'could not resolve side-loaded data ({e}) - using this material')
            self.data_source = material

        sub_matl_data = getattr(self.data_source, 'sub_matl_data', None)
        if sub_matl_data is None or not sub_matl_data.shader_label:
            return

        self.shader_label = sub_matl_data.shader_label
        if not shader_info.exists(self.shader_label):
            self.notes.append(
                f"shader label '{self.shader_label}' is not in the shader "
                f"database - baking with plain PBR rules")
            return

        # CustomFloat10: anisotropy strength. Non-zero repurposes PRM.a.
        aniso = sub_matl_data.floats.get('CustomFloat10')
        if aniso is not None:
            self.anisotropy = float(aniso.value)

        # CustomBoolean1 off means the shader substitutes a flat 0.16 for PRM.a.
        cb1 = sub_matl_data.bools.get('CustomBoolean1')
        if cb1 is not None:
            self.prm_alpha_is_used = bool(cb1.value)

        # A shader that reads CustomVector30 is doing fake subsurface.
        self.is_subsurface = shader_info.uses_param(self.shader_label, 'CustomVector30')
        self.reads_emissive = shader_info.uses_param(self.shader_label, 'Texture5')

    @property
    def prm_alpha_is_rotation(self):
        return abs(self.anisotropy) > 1e-6

    @property
    def uses_side_loaded_data(self):
        return self.data_source is not self.material

    def describe(self):
        if not self.shader_label:
            if self.uses_side_loaded_data:
                return (f"no Smash material data on '{self.data_source.name}' - "
                        f"baking with plain PBR rules")
            return "no Smash material data - baking with plain PBR rules"
        bits = [self.shader_label]
        if self.uses_side_loaded_data:
            bits.append(f"data from '{self.data_source.name}'")
        if self.is_subsurface:
            bits.append("subsurface (PRM.r = SSS mask)")
        if self.prm_alpha_is_rotation:
            bits.append(f"anisotropic CF10={self.anisotropy:g} (PRM.a = rotation)")
        elif not self.prm_alpha_is_used:
            bits.append("CustomBoolean1 off (PRM.a unused)")
        if self.reads_emissive:
            bits.append("reads Texture5 (EMI)")
        return "  |  ".join(bits)


def apply_settings(props):
    """Copy SUB_PG_bake_texs_settings fields into this module's globals.

    Called once by the bake/compile operators right before running - the rest
    of this file reads the plain module-level names below exactly as the
    original standalone script did.
    """
    global BAKE_SIZE, BAKE_MARGIN, BAKE_MARGIN_TYPE, BAKE_DIR, OVERWRITE_FILES
    global CHAR_TAG, NAME_FROM, USE_SELECTION_ONLY, DRY_RUN
    global WRITE_COL, WRITE_NOR, WRITE_PRM, WRITE_EMI, WRITE_COMPONENT_DEBUG_MAPS
    global PRM_AO_MODE, COMPILE_NUTEXB, EMI_MODE, EMI_NORMALIZE_TO_CV3
    global PRM_SKIN_MASK_MODE, PRM_SKIN_MASK_CONST

    BAKE_SIZE = int(props.bake_size)
    BAKE_MARGIN = props.bake_margin
    BAKE_MARGIN_TYPE = props.bake_margin_type
    BAKE_DIR = bpy.path.abspath(props.bake_subfolder and f"//{props.bake_subfolder}" or "//bakes")
    OVERWRITE_FILES = props.overwrite_files

    CHAR_TAG = props.char_tag or "Character"
    NAME_FROM = props.name_from
    USE_SELECTION_ONLY = props.use_selection_only
    DRY_RUN = props.dry_run

    WRITE_COL = props.write_col
    WRITE_NOR = props.write_nor
    WRITE_PRM = props.write_prm
    WRITE_EMI = props.write_emi
    WRITE_COMPONENT_DEBUG_MAPS = props.write_component_debug_maps

    PRM_AO_MODE = props.prm_ao_mode
    PRM_SKIN_MASK_MODE = props.prm_skin_mask_mode
    PRM_SKIN_MASK_CONST = props.prm_skin_mask_const
    EMI_MODE = props.emi_mode
    EMI_NORMALIZE_TO_CV3 = props.emi_normalize_to_cv3
    COMPILE_NUTEXB = props.compile_nutexb


# =============================================================================
# SMALL UTILS
# =============================================================================
def ensure_cycles():
    bpy.context.scene.render.engine = 'CYCLES'

def save_scene_state(scene):
    """Snapshot everything baking has to change, so it can all be put back."""
    bake = scene.render.bake
    state = {'engine': scene.render.engine}
    for key in ('margin', 'use_clear', 'use_selected_to_active', 'target',
                'normal_space'):
        try:
            state['bake_' + key] = getattr(bake, key)
        except Exception:
            pass
    try:
        state['bake_margin_type'] = bake.margin_type
    except Exception:
        pass
    try:
        state['samples'] = scene.cycles.samples
        state['adaptive'] = scene.cycles.use_adaptive_sampling
        state['threshold'] = scene.cycles.adaptive_threshold
    except Exception:
        pass
    return state

def restore_scene_state(scene, state):
    bake = scene.render.bake
    for key, value in state.items():
        try:
            if key == 'engine':
                scene.render.engine = value
            elif key == 'samples':
                scene.cycles.samples = value
            elif key == 'adaptive':
                scene.cycles.use_adaptive_sampling = value
            elif key == 'threshold':
                scene.cycles.adaptive_threshold = value
            elif key.startswith('bake_'):
                setattr(bake, key[5:], value)
        except Exception:
            pass

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def safe_filename(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip()

def strip_dot_number(name):
    return re.sub(r"\.\d+$", "", name)

def clamp01(v):
    if not isfinite(v):
        return 0.0
    return max(0.0, min(1.0, v))

def pick_prefix(obj_name, mat_name):
    s = (obj_name + " " + mat_name).lower()
    for keys, prefix in PREFIX_RULES:
        for k in keys:
            if k.lower() in s:
                return prefix
    return DEFAULT_PREFIX

def make_stem(obj, mat):
    if mat.name in NAME_OVERRIDES:
        return NAME_OVERRIDES[mat.name]
    src = mat.name if NAME_FROM == "MATERIAL" else obj.name
    return f"{pick_prefix(obj.name, mat.name)}{safe_filename(strip_dot_number(src))}_{CHAR_TAG}"

def set_colorspace(img, cs_name):
    try:
        img.colorspace_settings.name = cs_name
    except Exception:
        pass

def create_or_get_image(name, size):
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) != (size, size):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name=name, width=size, height=size,
                                  alpha=True, float_buffer=False)
    img.alpha_mode = 'STRAIGHT'
    img.generated_color = (0.0, 0.0, 0.0, 1.0)
    return img

def save_image(img, filepath):
    img.filepath_raw = filepath
    img.file_format = FILE_FORMAT
    img.save()

def load_fresh_image(filepath, colorspace="Non-Color"):
    img = bpy.data.images.load(filepath, check_existing=False)
    set_colorspace(img, colorspace)
    try:
        img.reload()
    except Exception:
        pass
    img.update()
    return img

def unique_path(path):
    if OVERWRITE_FILES or not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{root}_{i:03d}{ext}"):
        i += 1
    return f"{root}_{i:03d}{ext}"

def free_image(img):
    try:
        if img is not None and img.users == 0:
            bpy.data.images.remove(img)
    except Exception:
        pass


# =============================================================================
# PIXEL HELPERS
# =============================================================================
def img_to_np(img):
    """(W*H, 4) float32 copy of the image."""
    img.update()
    arr = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(arr)
    return arr.reshape(-1, 4)

def np_to_img(img, arr):
    img.pixels.foreach_set(np.clip(arr, 0.0, 1.0).astype(np.float32).reshape(-1))
    img.update()

def fill_image_const(img, rgba):
    n = img.size[0] * img.size[1]
    np_to_img(img, np.tile(np.array(rgba, dtype=np.float32), (n, 1)))
    return img

def gray_of(px):
    """Collapse RGB to the single value a baked scalar map represents."""
    return np.nan_to_num(px[:, :3].max(axis=1))

def sample_pixels(img, label, count=8):
    w, h = img.size
    px = img.pixels
    pts = [(w // 2, h // 2)]
    for _ in range(max(0, count - 1)):
        pts.append((random.randint(0, w - 1), random.randint(0, h - 1)))
    vals = []
    for x, y in pts:
        i = (y * w + x) * 4
        vals.append(f"{x},{y}:({px[i]:.3f},{px[i+1]:.3f},{px[i+2]:.3f},{px[i+3]:.3f})")
    line = f"SAMPLES {label}: " + " | ".join(vals)
    print("   " + line)
    return line

def write_report(path, lines):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        print(f"  ! could not write {path}: {e}")


# =============================================================================
# NODE GRAPH - GROUP AWARE
# =============================================================================
# A socket is addressed as (socket, path), where path is the tuple of group
# node *instances* you had to step through to reach it, outermost first. An
# empty path means the socket already lives in the material's own node tree.

def get_output_node(mat):
    """The output Cycles will actually render, not just the first one found."""
    nt = mat.node_tree
    try:
        node = nt.get_output_node('CYCLES')
        if node is not None:
            return node
    except Exception:
        pass
    for n in nt.nodes:
        if n.type == 'OUTPUT_MATERIAL':
            return n
    return None

def resolve_source(socket, path, depth=0):
    """Follow an INPUT socket back to whatever actually drives it.

    Steps through reroutes, and up out of a group when the value comes from the
    group's own Group Input node. Returns (out_socket, path, terminal_input):

      driven by a node   -> (output socket, its path, None)
      ends up a constant -> (None, None, the unlinked input socket)
    """
    if socket is None or depth > 64 or not socket.is_linked:
        return None, None, socket
    link = socket.links[0]
    node = link.from_node

    if node.type == 'REROUTE':
        return resolve_source(node.inputs[0], path, depth + 1)

    if node.type == 'GROUP_INPUT':
        if not path:
            return None, None, socket
        outer = path[-1]
        try:
            idx = list(node.outputs).index(link.from_socket)
        except ValueError:
            return None, None, socket
        if idx >= len(outer.inputs):
            return None, None, socket
        return resolve_source(outer.inputs[idx], path[:-1], depth + 1)

    return link.from_socket, path, None

def constant_of(socket):
    """The literal value on an unlinked input socket, as an RGBA tuple."""
    if socket is None:
        return None
    try:
        dv = socket.default_value
    except Exception:
        return None
    if hasattr(dv, "__len__"):
        vals = list(dv)
        if len(vals) >= 4:
            return (vals[0], vals[1], vals[2], vals[3])
        if len(vals) >= 3:
            return (vals[0], vals[1], vals[2], 1.0)
        return None
    v = float(dv)
    return (v, v, v, 1.0)

def find_main_shader(mat):
    """Identify what is driving Surface.

    Returns (kind, payload, path):
      'PRINCIPLED' / 'GLOSSY' -> payload is the BSDF node
      'SUB_MASTER'            -> payload is the master-shader group instance
      'COLOR'                 -> payload is the output socket feeding Surface
      None                    -> nothing usable
    """
    out_node = get_output_node(mat)
    if out_node is None:
        return (None, None, ())
    surf = out_node.inputs.get("Surface")
    if surf is None or not surf.is_linked:
        return (None, None, ())

    def walk(socket, path, depth):
        if depth > 16 or socket is None or not socket.is_linked:
            return (None, None, None)
        link = socket.links[0]
        node = link.from_node

        if node.type == 'REROUTE':
            return walk(node.inputs[0], path, depth + 1)

        if node.type == 'BSDF_PRINCIPLED':
            return ('PRINCIPLED', node, path)
        if node.type == 'BSDF_GLOSSY':
            return ('GLOSSY', node, path)

        if node.type in {'MIX_SHADER', 'ADD_SHADER'}:
            for inp in node.inputs:
                if inp.type == 'SHADER' and inp.is_linked:
                    found = walk(inp, path, depth + 1)
                    if found[0] is not None:
                        return found
            return (None, None, None)

        if node.type == 'GROUP' and node.node_tree is not None:
            if node.node_tree.name.startswith(MASTER_SHADER_PREFIX):
                return ('SUB_MASTER', node, path)
            try:
                idx = list(node.outputs).index(link.from_socket)
            except ValueError:
                return (None, None, None)
            for gout in node.node_tree.nodes:
                if gout.type != 'GROUP_OUTPUT':
                    continue
                if idx < len(gout.inputs):
                    found = walk(gout.inputs[idx], path + (node,), depth + 1)
                    if found[0] is not None:
                        return found
            return (None, None, None)

        if node.type == 'GROUP_INPUT':
            if not path:
                return (None, None, None)
            outer = path[-1]
            try:
                idx = list(node.outputs).index(link.from_socket)
            except ValueError:
                return (None, None, None)
            if idx < len(outer.inputs):
                return walk(outer.inputs[idx], path[:-1], depth + 1)
            return (None, None, None)

        return (None, None, None)

    kind, payload, path = walk(surf, (), 0)
    if kind is not None:
        return (kind, payload, path)

    return ('COLOR', surf.links[0].from_socket, ())

def iter_shaders(mat):
    """Every Principled/Glossy in the material and its groups, with paths."""
    found = []

    def search(tree, path, depth, seen):
        if depth > 8 or tree is None or tree.name in seen:
            return
        seen = seen | {tree.name}
        for n in tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                found.append(('PRINCIPLED', n, path))
            elif n.type == 'BSDF_GLOSSY':
                found.append(('GLOSSY', n, path))
        for n in tree.nodes:
            if n.type == 'GROUP' and n.node_tree is not None:
                if n.node_tree.name.startswith(MASTER_SHADER_PREFIX):
                    continue
                search(n.node_tree, path + (n,), depth + 1, seen)

    search(mat.node_tree, (), 0, frozenset())
    return found

def prm_inputs_driven(node):
    """How many PRM inputs are actually wired to something on this shader."""
    count = 0
    for name in ("Metallic", "Roughness", "IOR", "Specular IOR Level"):
        sock = node.inputs.get(name)
        if sock is not None and sock.is_linked:
            count += 1
    return count

def pick_prm_donor(mat, surface_kind, surface_node, surface_path):
    """Choose which shader supplies metal / rough / spec / IOR."""
    shaders = iter_shaders(mat)
    for kind, node, path in shaders:
        if node.name == OVERRIDE_NODE_SHADER:
            return (kind, node, path, f"named '{OVERRIDE_NODE_SHADER}'")

    if surface_node is not None and prm_inputs_driven(surface_node) > 0:
        return (surface_kind, surface_node, surface_path, "drives Surface")

    best = None
    for kind, node, path in shaders:
        score = prm_inputs_driven(node)
        if score > 0 and (best is None or score > best[0]):
            best = (score, kind, node, path)
    if best is not None:
        return (best[1], best[2], best[3],
                f"has {best[0]} wired PRM input(s); the Surface shader has none")

    if surface_node is not None:
        return (surface_kind, surface_node, surface_path, "drives Surface")
    if shaders:
        kind, node, path = shaders[0]
        return (kind, node, path, "only shader found")
    return (None, None, (), "")

def find_pbr_donor(mat):
    """Find a Principled/Glossy that is present but is NOT driving Surface.

    Returns (kind, node, path) or (None, None, ()).
    """
    def search(tree, path, depth, seen):
        if depth > 8 or tree is None or tree.name in seen:
            return (None, None, ())
        seen = seen | {tree.name}
        named = tree.nodes.get(OVERRIDE_NODE_SHADER)
        if named is not None and named.type in {'BSDF_PRINCIPLED', 'BSDF_GLOSSY'}:
            kind = 'PRINCIPLED' if named.type == 'BSDF_PRINCIPLED' else 'GLOSSY'
            return (kind, named, path)
        for n in tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                return ('PRINCIPLED', n, path)
            if n.type == 'BSDF_GLOSSY':
                return ('GLOSSY', n, path)
        for n in tree.nodes:
            if n.type == 'GROUP' and n.node_tree is not None:
                if n.node_tree.name.startswith(MASTER_SHADER_PREFIX):
                    continue
                found = search(n.node_tree, path + (n,), depth + 1, seen)
                if found[0] is not None:
                    return found
        return (None, None, ())
    return search(mat.node_tree, (), 0, frozenset())

def find_override(mat, node_name):
    """Locate an override node by name, in the material or any group it uses."""
    def search(tree, path, depth, seen):
        if depth > 8 or tree is None or tree.name in seen:
            return (None, None)
        seen = seen | {tree.name}
        node = tree.nodes.get(node_name)
        if node is not None and len(node.outputs):
            return (node.outputs[0], path)
        for n in tree.nodes:
            if n.type == 'GROUP' and n.node_tree is not None:
                s, p = search(n.node_tree, path + (n,), depth + 1, seen)
                if s is not None:
                    return (s, p)
        return (None, None)
    return search(mat.node_tree, (), 0, frozenset())

# Nodes that ray-trace, so anything downstream of one needs real samples.
STOCHASTIC_NODES = {'AMBIENT_OCCLUSION', 'BEVEL', 'SHADERTORGB'}

def tree_is_stochastic(tree, depth=0, seen=None):
    """True if the tree contains a ray-traced node anywhere, groups included."""
    if tree is None or depth > 12:
        return False
    seen = set() if seen is None else seen
    if tree.name in seen:
        return False
    seen.add(tree.name)
    for n in tree.nodes:
        if n.type in STOCHASTIC_NODES:
            return True
        if n.type == 'GROUP' and tree_is_stochastic(n.node_tree, depth + 1, seen):
            return True
    return False

def upstream_is_stochastic(socket, depth=0, seen=None):
    """Walk back from an output socket looking for anything that needs samples.

    The visited set is keyed on as_pointer(), NOT id() - Blender hands out a
    fresh, short-lived Python wrapper every time you touch node.inputs[..]
    .links[..].from_node, so id() values get recycled and collide between
    unrelated nodes.
    """
    if socket is None or depth > 32:
        return False
    node = getattr(socket, "node", None)
    if node is None:
        return False
    seen = set() if seen is None else seen
    try:
        key = node.as_pointer()
    except Exception:
        key = (getattr(node.id_data, "name", ""), node.name)
    if key in seen:
        return False
    seen.add(key)

    if node.type in STOCHASTIC_NODES:
        return True
    if node.type == 'GROUP' and tree_is_stochastic(node.node_tree):
        return True

    for inp in node.inputs:
        for link in inp.links:
            if upstream_is_stochastic(link.from_socket, depth + 1, seen):
                return True
    return False


# =============================================================================
# SOCKET TAPPING
# =============================================================================
# Links cannot cross a node-tree boundary, so a socket buried inside a group
# cannot be wired to a material-level Emission node directly. Instead we add a
# temporary output to each group on the path, hand the value up one level at a
# time, and delete the temporary sockets afterwards.

_TAP_ID = [0]

def tap_socket(socket, path):
    """Expose a socket at material level. Returns (socket, cleanup)."""
    created = []
    cur = socket
    for group_node in reversed(path):
        tree = group_node.node_tree
        _TAP_ID[0] += 1
        name = f"__BAKE_TAP_{_TAP_ID[0]}__"
        item = tree.interface.new_socket(name=name, in_out='OUTPUT',
                                         socket_type='NodeSocketColor')
        created.append((tree, item))

        linked = False
        for gout in tree.nodes:
            if gout.type != 'GROUP_OUTPUT':
                continue
            inp = gout.inputs.get(name)
            if inp is not None:
                tree.links.new(cur, inp)
                linked = True
        if not linked:
            untap(created)
            raise RuntimeError(f"could not attach tap inside group '{tree.name}'")

        nxt = group_node.outputs.get(name)
        if nxt is None:
            untap(created)
            raise RuntimeError(f"tap socket missing on instance of '{tree.name}'")
        cur = nxt
    return cur, created

def untap(created):
    for tree, item in reversed(created):
        try:
            tree.interface.remove(item)
        except Exception:
            pass


# =============================================================================
# BAKE PLUMBING
# =============================================================================
def remove_nodes_safe(nt, nodes):
    """Delete temporary nodes, ignoring any that are already gone."""
    for node in nodes:
        if node is None:
            continue
        try:
            nt.nodes.remove(node)
        except Exception:
            pass

def disconnect_surface(nt, out_node):
    stored = []
    surf = out_node.inputs.get("Surface")
    if surf:
        for link in list(surf.links):
            stored.append(link.from_socket)
            nt.links.remove(link)
    return stored

def restore_surface(nt, out_node, stored):
    surf = out_node.inputs.get("Surface")
    if not surf:
        return
    for from_sock in stored:
        try:
            nt.links.new(from_sock, surf)
        except Exception:
            pass

def set_bake_targets(objects, target_mats, image, colorspace, scratch):
    """Point every material in target_mats at `image`.

    Bundled materials all aim at the same image so one bake fills the whole
    set. Every other material on the selected objects still needs an active
    image node or the bake operator aborts on it, so those get a throwaway.
    """
    if not isinstance(target_mats, (list, tuple, set, frozenset)):
        target_mats = [target_mats]
    target_names = {m.name for m in target_mats if m is not None}
    set_colorspace(image, colorspace)
    added = []
    seen = set()
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes or mat.name in seen:
                continue
            seen.add(mat.name)
            nt = mat.node_tree
            node = nt.nodes.new("ShaderNodeTexImage")
            node.image = image if mat.name in target_names else scratch
            node.name = "__BAKE_TARGET__"
            node.label = "__BAKE_TARGET__"
            for other in nt.nodes:
                other.select = False
            node.select = True
            nt.nodes.active = node
            added.append((nt, node))
    return added

def clear_bake_targets(added):
    for nt, node in added:
        try:
            nt.nodes.remove(node)
        except Exception:
            pass

def run_bake(scene, bake_type, samples=None, margin=None):
    bake = scene.render.bake
    bake.use_clear = True
    bake.margin = BAKE_MARGIN if margin is None else margin
    try:
        bake.margin_type = BAKE_MARGIN_TYPE
    except Exception:
        pass
    bake.use_selected_to_active = False
    bake.target = 'IMAGE_TEXTURES'

    prev_samples = scene.cycles.samples
    prev_adaptive = scene.cycles.use_adaptive_sampling
    prev_threshold = scene.cycles.adaptive_threshold
    if samples is not None:
        scene.cycles.samples = samples
        scene.cycles.use_adaptive_sampling = bool(BAKE_ADAPTIVE_SAMPLING)
        if BAKE_ADAPTIVE_SAMPLING:
            scene.cycles.adaptive_threshold = float(BAKE_ADAPTIVE_THRESHOLD)
    try:
        bpy.ops.object.bake(type=bake_type)
    finally:
        scene.cycles.samples = prev_samples
        scene.cycles.use_adaptive_sampling = prev_adaptive
        scene.cycles.adaptive_threshold = prev_threshold

def bake_members(scene, objects, members, image, colorspace, scratch):
    """Emit-bake a channel for a whole bundle in ONE pass.

    `members` is [(material, Channel), ...]. Every member is wired to its own
    source and aimed at the same image, then a single bake fills all of them.
    """
    setups = []
    targets = []
    stochastic = False
    try:
        for mat, channel in members:
            if channel is None or channel.mode not in ('CONST', 'SOCKET'):
                continue
            nt = mat.node_tree
            out_node = get_output_node(mat)
            if out_node is None:
                continue

            temp_nodes = []
            created = []
            if channel.mode == 'CONST':
                rgb = nt.nodes.new("ShaderNodeRGB")
                rgb.label = "__BAKE_CONST__"
                value = channel.value
                rgb.outputs[0].default_value = (value[0], value[1], value[2], 1.0)
                temp_nodes.append(rgb)
                source = rgb.outputs[0]
            else:
                source, created = tap_socket(channel.socket, channel.path)
                if upstream_is_stochastic(channel.socket):
                    stochastic = True

            stored = disconnect_surface(nt, out_node)
            emission = nt.nodes.new("ShaderNodeEmission")
            emission.label = "__BAKE_EMISSION__"
            emission.location = (out_node.location.x - 250, out_node.location.y)
            nt.links.new(source, emission.inputs["Color"])
            nt.links.new(emission.outputs["Emission"], out_node.inputs["Surface"])
            setups.append((nt, out_node, stored, emission, temp_nodes, created))

        if not setups:
            return image

        targets = set_bake_targets(objects, [m for m, _ in members],
                                   image, colorspace, scratch)
        run_bake(scene, 'EMIT',
                 EMIT_SAMPLES_STOCHASTIC if stochastic else EMIT_SAMPLES_FLAT)
    finally:
        clear_bake_targets(targets)
        for nt, out_node, stored, emission, temp_nodes, created in setups:
            remove_nodes_safe(nt, [emission] + temp_nodes)
            restore_surface(nt, out_node, stored)
            untap(created)
    return image

def bake_pass(scene, objects, mats, bake_type, image, colorspace, scratch, samples=None):
    """Bake a native pass (AO, NORMAL) with the materials' real graphs intact."""
    targets = []
    try:
        targets = set_bake_targets(objects, mats, image, colorspace, scratch)
        run_bake(scene, bake_type, samples)
    finally:
        clear_bake_targets(targets)
    return image

def bake_coverage_mask(scene, objects, mats, size, scratch, name):
    """White wherever any bundled material covers a texel, black elsewhere."""
    mask = create_or_get_image(f"__COVERAGE_{safe_filename(name)}", size)
    set_colorspace(mask, "Non-Color")
    setups = []
    targets = []
    try:
        for mat in mats:
            nt = mat.node_tree
            out_node = get_output_node(mat)
            if out_node is None:
                continue
            stored = disconnect_surface(nt, out_node)
            emission = nt.nodes.new("ShaderNodeEmission")
            emission.label = "__BAKE_EMISSION__"
            emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
            nt.links.new(emission.outputs["Emission"], out_node.inputs["Surface"])
            setups.append((nt, out_node, stored, emission))
        targets = set_bake_targets(objects, mats, mask, "Non-Color", scratch)
        # Same margin as the real channel bakes below, not 0. The mask feeds
        # apply_coverage_default(), which flood-fills every texel outside it
        # with a flat default - at margin 0 that region is exactly the UV
        # island's own edge pixels, so it would immediately overwrite the
        # bleed COL/NOR/PRM just extended into and the "EXTEND" margin would
        # have no visible effect. Matching the margin here means only texels
        # genuinely beyond the bleed (dead atlas space) get defaulted.
        run_bake(scene, 'EMIT', 1, margin=BAKE_MARGIN)
    finally:
        clear_bake_targets(targets)
        for nt, out_node, stored, emission in setups:
            remove_nodes_safe(nt, [emission])
            restore_surface(nt, out_node, stored)
    return mask


# =============================================================================
# CHANNEL RESOLUTION
# =============================================================================
class _EmptyMaterial(Exception):
    """Raised to bail out of a material that no face actually uses."""


class Channel:
    """Where one output channel's data comes from, once everything is resolved."""
    def __init__(self, mode, value=None, socket=None, path=(), origin=""):
        self.mode = mode          # 'CONST' | 'SOCKET' | 'PASS' | 'NONE'
        self.value = value        # RGBA tuple for CONST
        self.socket = socket
        self.path = path
        self.origin = origin      # human-readable, for the report

    def __repr__(self):
        if self.mode == 'CONST':
            return f"CONST {tuple(round(v, 4) for v in self.value)}  ({self.origin})"
        if self.mode == 'SOCKET':
            depth = f", {len(self.path)} group(s) deep" if self.path else ""
            return f"BAKE from {self.socket.node.name}.{self.socket.name}{depth}  ({self.origin})"
        return f"{self.mode}  ({self.origin})"

NONE_CHANNEL = Channel('NONE', origin="unavailable")

def channel_from_input(socket, origin, path=()):
    """A Principled/Glossy input: bake it if driven, otherwise take its value."""
    if socket is None:
        return NONE_CHANNEL
    src, src_path, terminal = resolve_source(socket, path)
    if src is not None:
        return Channel('SOCKET', socket=src, path=src_path, origin=origin)
    const = constant_of(terminal if terminal is not None else socket)
    if const is None:
        return NONE_CHANNEL
    where = "" if terminal is socket else f" on {terminal.node.name}"
    return Channel('CONST', value=const, origin=f"{origin}, unlinked{where}")

def channel_from_group_input(group_node, socket_name, origin):
    """A named input on a group instance, e.g. the master shader's Texture6."""
    socket = group_node.inputs.get(socket_name)
    if socket is None:
        return NONE_CHANNEL
    src, src_path, terminal = resolve_source(socket, ())
    if src is not None:
        return Channel('SOCKET', socket=src, path=src_path, origin=origin)
    const = constant_of(terminal if terminal is not None else socket)
    if const is None:
        return NONE_CHANNEL
    return Channel('CONST', value=const, origin=origin + ", unlinked")

def hb_master_instance(path):
    """The HB Master Shader instance a surface shader sits directly inside."""
    if path:
        tree = path[-1].node_tree
        if tree is not None and tree.name.startswith(HB_MASTER_PREFIX):
            return path[-1]
    return None

def channel_from_instance(group_node, outer_path, socket_name, origin):
    """A group instance input, followed out to whatever drives it."""
    return channel_from_input(group_node.inputs.get(socket_name), origin, outer_path)

def scalar_from_instance(group_node, outer_path, socket_name, default):
    """A group instance input as a number: (value, driven_by_a_node).

    A driven socket has no single value, so it reports the default and True.
    """
    socket = group_node.inputs.get(socket_name)
    if socket is None:
        return default, False
    src, _path, terminal = resolve_source(socket, outer_path)
    if src is not None:
        return default, True
    const = constant_of(terminal if terminal is not None else socket)
    return (float(const[0]) if const is not None else default), False

def channel_from_override(mat, node_name, origin):
    socket, path = find_override(mat, node_name)
    if socket is None:
        return None
    return Channel('SOCKET', socket=socket, path=path,
                   origin=f"{origin} override node '{node_name}'")

def scalar_channel(value, origin):
    v = clamp01(float(value))
    return Channel('CONST', value=(v, v, v, 1.0), origin=origin)

def scalar_channel_raw(value, origin):
    """Unclamped - for IOR, which is legitimately greater than 1."""
    v = float(value)
    return Channel('CONST', value=(v, v, v, 1.0), origin=origin)

def channel_from_emission(node, node_path, label):
    """The emissive colour off a Principled, plus its strength as a multiplier.

    Emission Color and Emission Strength are separate inputs and the map wants
    their product, but a bake can only capture one socket. So the colour is
    baked and the strength is carried alongside as a scalar applied afterwards
    - the same split the specular channel already uses for its scale.

    Returns (channel, strength). A strength of 0 - the Blender default - means
    the material does not emit, and returns a NONE channel.
    """
    colour_input = node.inputs.get("Emission Color")
    strength_input = node.inputs.get("Emission Strength")
    if colour_input is None:
        return NONE_CHANNEL, 0.0

    strength = 1.0
    if strength_input is not None:
        if strength_input.is_linked:
            # A textured strength cannot be folded into a scalar. Bake the
            # colour at full strength and let CustomVector3 carry the level.
            strength = 1.0
        else:
            try:
                strength = float(strength_input.default_value)
            except (TypeError, ValueError):
                strength = 1.0

    if strength <= 0.0:
        return NONE_CHANNEL, 0.0

    channel = channel_from_input(colour_input, f"{label} Emission Color", node_path)
    if channel.mode == 'NONE':
        return NONE_CHANNEL, 0.0

    # A constant black emission colour is "no emission" however strong it is.
    if channel.mode == 'CONST' and max(float(v) for v in channel.value[:3]) <= 0.0:
        return NONE_CHANNEL, 0.0

    return channel, strength


def resolve_channels(mat, kind, payload, path, ctx=None):
    """Decide, per channel, exactly where the data will come from."""
    ch = {}
    preset = MATERIAL_PRESETS.get(mat.name, {})
    if ctx is None:
        ctx = MatlContext(mat)
    ch['matl_context'] = ctx

    col = alpha = metal = rough = spec = NONE_CHANNEL
    ch['prm_rgb'] = NONE_CHANNEL
    ch['nor_rgb'] = NONE_CHANNEL
    ch['nor_a']   = NONE_CHANNEL
    ch['emi']       = NONE_CHANNEL
    ch['emi_scale'] = 0.0

    ior = NONE_CHANNEL

    def pull_principled(node, node_path, label):
        """Metal / rough / spec / IOR off a Principled, wherever it lives."""
        m = channel_from_input(node.inputs.get("Metallic"), f"{label} Metallic", node_path)
        r = channel_from_input(node.inputs.get("Roughness"), f"{label} Roughness", node_path)
        spec_name = ("Specular IOR Level" if node.inputs.get("Specular IOR Level")
                     else "Specular" if node.inputs.get("Specular") else None)
        s = (channel_from_input(node.inputs.get(spec_name), f"{label} {spec_name}", node_path)
             if spec_name else NONE_CHANNEL)
        i = channel_from_input(node.inputs.get("IOR"), f"{label} IOR", node_path)

        # On a subsurface shader PRM.r carries the SSS mask rather than
        # metalness, so the Principled's Subsurface Weight is what belongs
        # there. Skin authored the ordinary way has Metallic 0, which would
        # otherwise bake a mask of zeroes and switch the effect off entirely.
        if ctx.is_subsurface:
            sss = channel_from_input(node.inputs.get("Subsurface Weight"),
                                     f"{label} Subsurface Weight", node_path)

            # A textured weight, or a non-zero constant, is someone actually
            # saying how much subsurface they want. A zero or absent one is
            # not - it is just Blender's default - and neither is Metallic,
            # which in a stylised setup is a look knob feeding the COL bake.
            # Baking either into PRM.r writes a mask that switches the
            # subsurface off, on the very shader that was chosen for it.
            explicit = sss.mode == 'SOCKET' or (
                sss.mode == 'CONST' and abs(float(sss.value[0])) > 1e-6)

            if explicit:
                m = sss
                ch['sss_note'] = (
                    f"PRM.r from Subsurface Weight, not Metallic - "
                    f"{ctx.shader_label} reads it as the SSS mask")
            elif PRM_SKIN_MASK_MODE == 'AUTO':
                m = scalar_channel_raw(
                    PRM_SKIN_MASK_CONST,
                    f"vanilla SSS mask for {ctx.shader_label} - nothing in the "
                    f"material drives subsurface, so Blender's Metallic is left "
                    f"to the COL bake instead of being written here")
                ch['sss_note'] = (
                    f"PRM.r = {PRM_SKIN_MASK_CONST:g} (vanilla SSS mask). Blender's "
                    f"Metallic is a look knob for COL and is NOT written to PRM.r "
                    f"on a subsurface shader.")
            elif sss.mode != 'NONE':
                m = sss
                ch['sss_note'] = (
                    f"PRM.r from Subsurface Weight, not Metallic - "
                    f"{ctx.shader_label} reads it as the SSS mask")

        # While anisotropy is on, PRM.a is a rotation angle, so the specular
        # value has no channel to live in and Anisotropic Rotation takes its
        # place. Blender stores rotation as 0-1 over a full turn; Smash maps
        # 0-1 onto half a turn, so the conversion is a doubling, wrapped
        # because an anisotropic highlight repeats every 180 degrees.
        # The Blender-to-Smash conversion itself lives in pack_prm(), so that
        # a constant rotation and a textured one go through exactly the same
        # arithmetic. What is carried here is the raw Blender value.
        if ctx.prm_alpha_is_rotation:
            rot = channel_from_input(node.inputs.get("Anisotropic Rotation"),
                                     f"{label} Anisotropic Rotation", node_path)
            if rot.mode == 'NONE':
                s = scalar_channel_raw(0.0, "PRM.a rotation (no rotation input, 0 deg)")
            else:
                s = rot
                s.origin += " -> PRM.a rotation"
            ch['spec_scale'] = 1.0
            ch['prm_alpha_is_rotation'] = True
            ch['aniso_note'] = (
                f"PRM.a is anisotropic rotation, not specular "
                f"(CustomFloat10={ctx.anisotropy:g})")

        return m, r, s, i

    if kind == 'PRINCIPLED':
        node = payload
        col   = channel_from_input(node.inputs.get("Base Color"), "Principled Base Color", path)
        alpha = channel_from_input(node.inputs.get("Alpha"), "Principled Alpha", path)
        ch['emi'], ch['emi_scale'] = channel_from_emission(node, path, "Principled")
        d_kind, d_node, d_path, why = pick_prm_donor(mat, kind, node, path)
        if d_node is not None and d_kind == 'PRINCIPLED':
            label = "Principled" if d_node is node else f"'{d_node.name}'"
            metal, rough, spec, ior = pull_principled(d_node, d_path, label)
            if d_node is not node:
                ch['prm_donor'] = Channel('CONST', value=(0, 0, 0, 1),
                                          origin=f"PRM from '{d_node.name}' ({why})")

        # The HB Master Shader keeps each Smash channel on its own socket,
        # already in PRM units, so take them off the instance instead of
        # reading them back out of its internal Principled - whose Specular
        # IOR Level is PRM.a x 2.5 through a Math node and would otherwise
        # cost a bake to recover. A texture wired into a socket still bakes.
        hb = hb_master_instance(path)
        if hb is not None:
            outer = path[:-1]
            label = f"'{hb.name}'"
            ch['hb_master'] = hb
            alpha = channel_from_instance(hb, outer, "Alpha", f"{label} Alpha")
            rough = channel_from_instance(hb, outer, "Roughness", f"{label} Roughness (PRM.g)")
            if ctx.is_subsurface:
                sss = channel_from_instance(hb, outer, "SSS Mask", f"{label} SSS Mask (PRM.r)")
                explicit = sss.mode == 'SOCKET' or (
                    sss.mode == 'CONST' and abs(float(sss.value[0])) > 1e-6)
                # Otherwise pull_principled already chose the vanilla mask.
                if explicit or PRM_SKIN_MASK_MODE != 'AUTO':
                    metal = sss
                    ch['sss_note'] = (
                        f"PRM.r from the master shader's SSS Mask - "
                        f"{ctx.shader_label} reads it as the SSS mask")
            else:
                metal = channel_from_instance(hb, outer, "Metalness",
                                              f"{label} Metalness (PRM.r)")
            if not ctx.prm_alpha_is_rotation:
                spec = channel_from_instance(hb, outer, "Specular", f"{label} Specular (PRM.a)")
                ch['spec_scale'] = 1.0
            emi = channel_from_instance(hb, outer, "Emission Color", f"{label} Emission Color")
            strength, textured = scalar_from_instance(hb, outer, "Emission Strength", 1.0)
            if strength <= 0.0 or emi.mode == 'NONE' or (
                    emi.mode == 'CONST' and max(float(v) for v in emi.value[:3]) <= 0.0):
                ch['emi'], ch['emi_scale'] = NONE_CHANNEL, 0.0
            else:
                ch['emi'], ch['emi_scale'] = emi, (1.0 if textured else strength)

    elif kind == 'GLOSSY':
        node = payload
        col   = channel_from_input(node.inputs.get("Color"), "Glossy Color", path)
        rough = channel_from_input(node.inputs.get("Roughness"), "Glossy Roughness", path)

    elif kind == 'SUB_MASTER':
        node = payload
        col   = channel_from_group_input(node, SUB_SOCKET_COL_RGB, "master shader Texture0 RGB")
        alpha = channel_from_group_input(node, SUB_SOCKET_COL_A,   "master shader Texture0 Alpha")
        ch['prm_rgb'] = channel_from_group_input(node, SUB_SOCKET_PRM_RGB, "master shader Texture6 RGB")
        spec          = channel_from_group_input(node, SUB_SOCKET_PRM_A,   "master shader Texture6 Alpha")
        ch['nor_rgb'] = channel_from_group_input(node, SUB_SOCKET_NOR_RGB, "master shader Texture4 RGB")
        ch['nor_a']   = channel_from_group_input(node, SUB_SOCKET_NOR_A,   "master shader Texture4 Alpha")
        # The master shader already holds a real Emi map on its own socket, so
        # it passes straight through at strength 1 - it is emissive data, not
        # a Blender emission colour that needs converting.
        ch['emi'] = channel_from_group_input(node, SUB_SOCKET_EMI_RGB, "master shader Texture5 RGB")
        ch['emi_scale'] = 1.0
        if spec.mode != 'NONE':
            spec.origin += " (already PRM-space)"
            ch['spec_scale'] = 1.0

    elif kind == 'COLOR':
        col = Channel('SOCKET', socket=payload, path=path,
                      origin="color wired straight into Surface")
        d_kind, d_node, d_path = find_pbr_donor(mat)
        if d_kind == 'PRINCIPLED':
            metal, rough, spec, ior = pull_principled(d_node, d_path, "donor Principled")
            # A toon setup routes colour into Surface directly, but its
            # Principled still carries the emission the material wants.
            ch['emi'], ch['emi_scale'] = channel_from_emission(
                d_node, d_path, "donor Principled")
        elif d_kind == 'GLOSSY':
            rough = channel_from_input(d_node.inputs.get("Roughness"),
                                       "donor Glossy Roughness", d_path)

    overrides = {
        'col':   channel_from_override(mat, OVERRIDE_NODE_BASECOLOR, "COL"),
        'alpha': channel_from_override(mat, OVERRIDE_NODE_ALPHA, "ALPHA"),
        'metal': channel_from_override(mat, OVERRIDE_NODE_METAL, "METAL"),
        'rough': channel_from_override(mat, OVERRIDE_NODE_ROUGH, "ROUGH"),
        'spec':  channel_from_override(mat, OVERRIDE_NODE_SPEC, "SPEC"),
    }
    if overrides['col'] is not None:
        col = overrides['col']
    if overrides['alpha'] is not None:
        alpha = overrides['alpha']
    if overrides['metal'] is not None:
        metal = overrides['metal']
    if overrides['rough'] is not None:
        rough = overrides['rough']
    if overrides['spec'] is not None:
        spec = overrides['spec']
        ch.pop('spec_scale', None)
        ior = NONE_CHANNEL

    if 'metal' in preset:
        metal = scalar_channel(preset['metal'], f"preset for '{mat.name}'")
    if 'rough' in preset:
        rough = scalar_channel(preset['rough'], f"preset for '{mat.name}'")
    if 'spec' in preset:
        spec = scalar_channel(preset['spec'], f"preset for '{mat.name}'")
        ch.pop('spec_scale', None)
        ior = NONE_CHANNEL
    if 'ior' in preset:
        ior = scalar_channel_raw(preset['ior'], f"preset for '{mat.name}'")

    if metal.mode == 'NONE':
        metal = scalar_channel(PRM_FALLBACK_METAL, "fallback (no metallic source)")

    # --- Is PRM.r going to mean anything sensible? ---------------------------
    # Two different mistakes, both of which bake a perfectly valid-looking
    # texture that renders wrong, and neither of which is visible afterwards
    # without knowing what the channel is for.
    if metal.mode == 'CONST':
        metal_value = float(metal.value[0])
        if ctx.is_subsurface:
            # PRM.r is the SSS mask here. Textures.md: vanilla skin sets it to
            # 1. Near zero means the subsurface the shader was chosen for never
            # appears - and Blender's Subsurface Weight defaults to 0, so this
            # is the state a skin material lands in by simply not being told.
            if metal_value < 0.05:
                ch['prm_r_note'] = (
                    f"PRM.r is the SSS mask on {ctx.shader_label} but bakes to "
                    f"{metal_value:.3g} - subsurface will not show. Vanilla skin "
                    f"uses 1.0; raise the Principled's Subsurface Weight.")
        elif 0.1 < metal_value < 0.9:
            # Textures.md: "Metalness is usually either 0 (not metallic) or 1
            # (metallic)." A constant in between is almost always someone using
            # Metallic as a sheen knob in Blender, which in game reads as a
            # partly-metal surface - dark diffuse and albedo-tinted specular.
            ch['prm_r_note'] = (
                f"PRM.r bakes to a flat {metal_value:.3g}. In game this channel is "
                f"metalness, which vanilla keeps at 0 or 1 - a middle value renders "
                f"as a half-metal surface. If this is skin, use the Skin preset so "
                f"PRM.r becomes the SSS mask instead.")
    if rough.mode == 'NONE':
        rough = scalar_channel(PRM_FALLBACK_ROUGH, "fallback (no roughness source)")
    if spec.mode == 'NONE':
        spec = scalar_channel(PRM_FALLBACK_SPEC, "fallback (no specular source)")

    # With CustomBoolean1 off the shader ignores PRM.a and substitutes a flat
    # 0.16, so whatever gets baked there is dead data. Writing that constant
    # directly says so in the report and skips a bake call that could not have
    # affected the render either way. Anisotropy wins over this - a rotation
    # is still read from PRM.a regardless of CustomBoolean1.
    if not ctx.prm_alpha_is_used and not ctx.prm_alpha_is_rotation:
        spec = scalar_channel_raw(
            0.16,
            f"CustomBoolean1 is off on '{mat.name}', so the shader ignores "
            f"PRM.a and uses a flat 0.16")
        ch['spec_scale'] = 1.0
        ch.pop('ior', None)
        ior = NONE_CHANNEL

    if 'spec_scale' not in ch:
        ch['spec_scale'] = float(PRM_SPECULAR_SCALE)
        if PRM_SPEC_FROM_IOR and ior.mode == 'CONST':
            n = float(ior.value[0])
            if n <= 1.0 + 1e-6:
                ch['ior'] = Channel('CONST', value=ior.value,
                                    origin=f"{ior.origin} - IOR {n:g} gives no "
                                           f"reflection, so PRM.a keeps the "
                                           f"standard scale instead")
            elif abs(n - 1.5) > 1e-4:
                f0 = ((n - 1.0) / (n + 1.0)) ** 2
                ch['spec_scale'] = 10.0 * f0
                ch['ior'] = Channel('CONST', value=ior.value,
                                    origin=f"{ior.origin} -> F0 {f0:.4f}, "
                                           f"spec scale {ch['spec_scale']:.3f}")
        elif PRM_SPEC_FROM_IOR and ior.mode == 'SOCKET':
            ch['ior'] = Channel('NONE', origin="IOR is textured - cannot be baked "
                                               "into 8-bit, using IOR 1.5")

    ch.update(col=col, alpha=alpha, metal=metal, rough=rough, spec=spec)

    if 'ao' in preset:
        ch['ao'] = scalar_channel(preset['ao'], f"preset for '{mat.name}'")
    elif PRM_AO_MODE == "CONST":
        ch['ao'] = scalar_channel(PRM_AO_CONST, "PRM_AO_MODE=CONST")
    elif PRM_AO_MODE == "BAKE":
        ch['ao'] = Channel('PASS', origin="baked AO pass")
    elif ch.get('hb_master') is not None:
        # The master shader tints its own AO into COL, so an AO pass here too
        # would darken the same creases twice. Its "AO to PRM" switch decides.
        ao_to_prm, _ = scalar_from_instance(ch['hb_master'], path[:-1], "AO to PRM", 0.0)
        if ao_to_prm >= 0.5:
            ch['ao'] = Channel('PASS', origin="baked AO pass (master shader: AO to PRM on)")
        else:
            ch['ao'] = scalar_channel(
                PRM_AO_CONST,
                "master shader: AO is tinted into COL (AO to PRM off), so PRM.b stays flat")
    elif kind in ('PRINCIPLED', 'GLOSSY'):
        ch['ao'] = Channel('PASS', origin="baked AO pass (AUTO: PBR shader)")
    else:
        ch['ao'] = scalar_channel(
            PRM_AO_CONST,
            "AUTO: shading is already baked into COL, so PRM.b stays flat")
    return ch


# =============================================================================
# IMAGE ASSEMBLY
# =============================================================================
_NORMAL_DETAIL_NODES = {'BUMP', 'NORMAL_MAP', 'DISPLACEMENT', 'VECTOR_DISPLACEMENT'}
_NORMAL_DETAIL_MODIFIERS = {'MULTIRES', 'DISPLACE'}


def normal_bake_would_be_flat(materials, objects):
    """True when a tangent-space normal bake provably can't capture anything.

    Baking an object's tangent normals onto its own UVs yields flat
    (0.5, 0.5, 1.0) unless something perturbs the surface normal: a Bump or
    Normal Map node, something wired into a shader's Normal input, or a
    modifier that adds geometry detail the bake could pick up. If none of
    that exists there is nothing to find, so the bake is pure cost.

    Deliberately conservative - it only returns True when it has walked every
    material (groups included) and every modifier and found nothing at all.
    Any doubt falls through to a real bake.
    """
    for obj in objects:
        for modifier in obj.modifiers:
            if modifier.type in _NORMAL_DETAIL_MODIFIERS and modifier.show_render:
                return False

    def tree_has_detail(tree, depth=0, seen=None):
        if tree is None or depth > 12:
            return False
        seen = set() if seen is None else seen
        if tree.name in seen:
            return False
        seen.add(tree.name)
        for node in tree.nodes:
            if node.type in _NORMAL_DETAIL_NODES:
                return True
            # Only a *shader's* Normal input redirects the shading normal.
            # Plenty of other nodes have a socket of that name that has
            # nothing to do with surface detail - an Ambient Occlusion node's
            # Normal just aims its hemisphere, and these toon groups wire
            # Geometry.Normal straight into one, which made an earlier version
            # of this check fire on every single material and never skip a
            # thing.
            if node.type.startswith('BSDF_') or node.type == 'SUBSURFACE_SCATTERING':
                normal_input = node.inputs.get('Normal')
                if normal_input is not None and normal_input.is_linked:
                    return True
            if node.type == 'GROUP' and tree_has_detail(node.node_tree, depth + 1, seen):
                return True
        return False

    for material in materials:
        if material.use_nodes and tree_has_detail(material.node_tree):
            return False
    return True


def resolve_channel_image(scene, objects, members, name, size, colorspace,
                          scratch, default_rgba=(0.0, 0.0, 0.0, 1.0)):
    """Turn a channel into an image, baking only when it has to."""
    img = create_or_get_image(name, size)
    set_colorspace(img, colorspace)
    live = [(m, c) for m, c in members if c is not None and c.mode != 'NONE']

    if not live:
        fill_image_const(img, default_rgba)
        return img

    # Flood-fill instead of baking whenever every member resolves to the SAME
    # constant. Each bpy.ops.object.bake call costs ~0.5s of fixed setup on
    # this machine before it traces a single ray (measured: a 128px 1-sample
    # bake takes 0.53s, a 2048px one 1.69s), so skipping a whole call is worth
    # far more than making it cheaper. Used to only skip for a lone member, so
    # a bundle whose members happen to agree - which is most of them, since
    # Shiny variants usually differ only in metalness - still paid for a full
    # bake per channel to paint one flat value.
    if all(c.mode == 'CONST' for _, c in live):
        distinct = {tuple(round(float(v), 6) for v in c.value) for _, c in live}
        if len(distinct) == 1:
            fill_image_const(img, live[0][1].value)
            return img

    fill_image_const(img, default_rgba)
    bake_members(scene, objects, live, img, colorspace, scratch)
    return img

def pack_emi(emi_img, out_name, size, strength, alpha_img=None):
    """Emissive map, with the Principled's Emission Strength folded in.

    Returns (image, custom_vector_3). An 8-bit texture cannot store a value
    above 1.0, so when the emission exceeds that the map is divided down to fit
    and the divisor comes back as the CustomVector3 the material needs to
    restore the intended brightness. That is exactly what CustomVector3 is for
    - Material Parameters.md describes it as the emission multiplier, "often
    higher than 1 to increase bloom" - so this loses nothing and is how vanilla
    stores bright emission too.
    """
    out = create_or_get_image(out_name, size)
    set_colorspace(out, "sRGB")

    px = img_to_np(emi_img).copy()
    rgb = np.nan_to_num(px[:, 0:3]) * float(strength)

    custom_vector_3 = (1.0, 1.0, 1.0, 1.0)
    peak = float(rgb.max()) if rgb.size else 0.0
    if peak > 1.0:
        if EMI_NORMALIZE_TO_CV3:
            rgb = rgb / peak
            custom_vector_3 = (peak, peak, peak, 1.0)
            print(f"    EMI peaks at {peak:.3f}; map normalised and "
                  f"CustomVector3 = {peak:.3f} restores it.")
        else:
            print(f"    ! EMI clips: peak {peak:.3f} > 1.0. Enable "
                  f"'Normalize To CustomVector3' to keep the range.")

    if alpha_img is not None:
        a = gray_of(img_to_np(alpha_img))
    else:
        a = np.ones(rgb.shape[0], dtype=np.float32)

    np_to_img(out, np.stack([
        np.clip(rgb[:, 0], 0.0, 1.0),
        np.clip(rgb[:, 1], 0.0, 1.0),
        np.clip(rgb[:, 2], 0.0, 1.0),
        np.clip(a, 0.0, 1.0),
    ], axis=1))
    return out, custom_vector_3


def pack_prm(metal_img, rough_img, ao_img, spec_img, prm_rgb_img, out_name,
             size, spec_scale, alpha_is_rotation=False):
    out = create_or_get_image(out_name, size)
    set_colorspace(out, "Non-Color")

    if prm_rgb_img is not None:
        rgb = img_to_np(prm_rgb_img)
        metal = np.nan_to_num(rgb[:, 0])
        rough = np.nan_to_num(rgb[:, 1])
        ao = np.nan_to_num(rgb[:, 2])
    else:
        metal = gray_of(img_to_np(metal_img))
        rough = gray_of(img_to_np(rough_img))
        ao = gray_of(img_to_np(ao_img))

    rough = np.maximum(rough, clamp01(PRM_ROUGHNESS_MIN))
    if PRM_ROUGHNESS_EXPORT_SQRT:
        rough = np.sqrt(rough)

    spec = gray_of(img_to_np(spec_img))
    if alpha_is_rotation:
        # PRM.a is a rotation angle here, not a reflectance. Blender's
        # Anisotropic Rotation covers a full turn over 0-1 and Smash covers
        # half a turn, so the value doubles; the wrap is safe because an
        # anisotropic highlight is symmetric every 180 degrees. No specular
        # scale applies, and clipping would be wrong - 1.2 turns is 0.2 turns.
        spec = np.mod(spec * 2.0, 1.0)
    else:
        spec = spec * float(spec_scale)
        if spec.max() > 1.0:
            print(f"    ! PRM.a clips: peak {spec.max():.2f} > 1.0, so the specular "
                  f"channel saturates. Lower the IOR or Specular IOR Level - "
                  f"vanilla Smash sits around 0.16.")

    np_to_img(out, np.stack([
        np.clip(metal, 0.0, 1.0),
        np.clip(rough, 0.0, 1.0),
        np.clip(ao, 0.0, 1.0),
        np.clip(spec, 0.0, 1.0),
    ], axis=1))
    return out

def pack_nor(normal_img, out_name, size, cavity_img=None, passthrough_rgb=None,
             passthrough_alpha=None):
    out = create_or_get_image(out_name, size)
    set_colorspace(out, "Non-Color")

    src = img_to_np(passthrough_rgb if passthrough_rgb is not None else normal_img)
    x = np.clip(src[:, 0], 0.0, 1.0)
    y = np.clip(src[:, 1], 0.0, 1.0)
    z = np.clip(src[:, 2], 0.0, 1.0)
    if NOR_FLIP_GREEN:
        y = 1.0 - y

    if passthrough_rgb is not None or NOR_BLUE_MODE == "Z_FROM_BAKE":
        b = z
    else:
        b = np.full_like(x, clamp01(NOR_BLUE_CONST))

    if passthrough_alpha is not None:
        a = gray_of(img_to_np(passthrough_alpha))
    elif NOR_ALPHA_MODE in ("FROM_AO", "FROM_CAVITY") and cavity_img is not None:
        a = gray_of(img_to_np(cavity_img))
    else:
        a = np.full_like(x, clamp01(NOR_ALPHA_CONST))

    np_to_img(out, np.stack([x, y, b, a], axis=1))
    return out

def apply_alpha(rgb_img, alpha_img):
    if tuple(rgb_img.size) != tuple(alpha_img.size):
        raise RuntimeError("alpha size mismatch")
    px = img_to_np(rgb_img)
    px[:, 3] = gray_of(img_to_np(alpha_img))
    np_to_img(rgb_img, px)
    return rgb_img

def apply_coverage_default(img, mask_img, default_rgba):
    """Replace never-baked texels with a sane default instead of black."""
    if mask_img is None:
        return img
    px = img_to_np(img)
    covered = gray_of(img_to_np(mask_img)) > 0.5
    px[~covered] = np.array(default_rgba, dtype=np.float32)
    np_to_img(img, px)
    return img


# =============================================================================
# MAIN
# =============================================================================
def collect_targets():
    """Map every texture set to its materials and the meshes that use them."""
    pool = (bpy.context.selected_objects if USE_SELECTION_ONLY
            else bpy.context.view_layer.objects)
    meshes, hidden = [], []
    for obj in pool:
        if obj.type != 'MESH':
            continue
        (meshes if obj.visible_get() else hidden).append(obj)

    if hidden:
        print("  ! skipping hidden objects (they cannot be baked): "
              + ", ".join(o.name for o in hidden))
    if not meshes:
        raise RuntimeError("No visible MESH objects to bake. Select at least one.")

    usable = {}
    for obj in meshes:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes or mat.name in SKIP_MATERIALS:
                continue
            entry = usable.setdefault(mat.name, {"mat": mat, "objects": []})
            if obj not in entry["objects"]:
                entry["objects"].append(obj)
    if not usable:
        raise RuntimeError("No node-based materials found on the selected meshes.")

    groups = {}
    for name, entry in usable.items():
        key = group_key_for(name, set(usable))
        group = groups.setdefault(key, {"materials": [], "objects": []})
        group["materials"].append(entry["mat"])
        for obj in entry["objects"]:
            if obj not in group["objects"]:
                group["objects"].append(obj)

    for key, group in groups.items():
        group["materials"].sort(key=lambda m: (m.name != key, m.name))
    return groups, meshes


def bake_all():
    """Bake, then hand the scene back exactly as it was found."""
    scene = bpy.context.scene
    state = save_scene_state(scene)
    try:
        return _bake_all()
    finally:
        restore_scene_state(scene, state)
        if state.get('engine') != 'CYCLES':
            print(f"Render engine restored to {state.get('engine')}.")


# Per-texture-set results the bake wants to hand back to whoever applies the
# textures afterwards - currently just the CustomVector3 an EMI map needs to
# restore its brightness. Keyed by output stem. Rewritten on every bake.
LAST_BAKE_INFO = {}


def _bake_all():
    ensure_cycles()
    LAST_BAKE_INFO.clear()
    if not bpy.data.filepath:
        raise RuntimeError("Save the .blend first so the bake output folder resolves to a real path.")
    ensure_dir(BAKE_DIR)

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    groups, _all_meshes = collect_targets()

    original_active = view_layer.objects.active
    original_selected = list(bpy.context.selected_objects)

    scratch = create_or_get_image("__BAKE_SCRATCH__", 64)
    set_colorspace(scratch, "Non-Color")
    written = []

    summary = [
        "BakeTexs report",
        f"blend:      {bpy.data.filepath}",
        f"output:     {BAKE_DIR}",
        f"size:       {BAKE_SIZE}   margin: {BAKE_MARGIN} ({BAKE_MARGIN_TYPE})",
        f"char tag:   {CHAR_TAG}",
        f"dry run:    {DRY_RUN}",
        f"texture sets: {len(groups)}",
        "",
    ]
    print(f"\nBakeTexs: {len(groups)} texture set(s) -> {BAKE_DIR}")

    for group_name, group in groups.items():
        materials = group["materials"]
        objects = group["objects"]
        mat = materials[0]
        mat_key = safe_filename(group_name)
        stem = make_stem(objects[0], mat)
        out_col = out_nor = out_prm = out_emi = None

        bundled = len(materials) > 1
        lines = [
            f"Texture set: {group_name}" + (
                f"   (bundled: {', '.join(m.name for m in materials)})" if bundled else ""),
            f"Objects:  {', '.join(o.name for o in objects)}",
            f"Output:   {stem}_col / _nor / _prm / _emi",
        ]
        print(f"\n=== {group_name}" + (f"  + {len(materials)-1} bundled" if bundled else "")
              + f"  [{', '.join(o.name for o in objects)}]")
        print(f"    -> {stem}_col/_nor/_prm")

        resolved = []
        for member in materials:
            kind, payload, path = find_main_shader(member)
            if kind is None:
                msg = f"  {member.name}: nothing connected to Surface - skipped."
                print(msg)
                lines.append(msg)
                continue
            member_ctx = MatlContext(member)
            channels = resolve_channels(member, kind, payload, path, ctx=member_ctx)
            resolved.append((member, channels))
            label = f"  {member.name} [{kind}]" if bundled else f"  Surface: {kind}"
            print(label)
            lines.append(label.strip())

            # What the target shader expects, and every place that changed how
            # a channel was resolved. This is the part that used to be
            # invisible - the baker wrote the same PRM for every material.
            shader_line = f"    shader:  {member_ctx.describe()}"
            print(shader_line)
            lines.append(shader_line.strip())
            for note_key in ('sss_note', 'aniso_note', 'prm_r_note'):
                note = channels.get(note_key)
                if note:
                    print(f"    ! {note}")
                    lines.append(f"! {note}")
            for note in member_ctx.notes:
                print(f"    ! {note}")
                lines.append(f"! {note}")
            if DEBUG_PRINT_GRAPH:
                for key in ('col', 'alpha', 'metal', 'rough', 'spec', 'ior', 'ao',
                            'prm_rgb', 'nor_rgb', 'nor_a', 'emi'):
                    c = channels.get(key)
                    if c is None or not isinstance(c, Channel) or c.mode == 'NONE':
                        continue
                    line = f"    {key:8s} {c!r}"
                    print(line)
                    lines.append(line.strip())

        if not resolved:
            summary.extend(lines + [""])
            if DEBUG_WRITE_DEBUG_REPORT and not DRY_RUN:
                write_report(os.path.join(BAKE_DIR, f"{mat_key}_DEBUG.txt"), lines)
            continue

        materials = [m for m, _ in resolved]
        channels = resolved[0][1]
        spec_scale = float(channels.get('spec_scale', PRM_SPECULAR_SCALE))

        # An EMI map is only written when something can actually use it: the
        # material has emission to bake, AND (on AUTO) its shader reads
        # Texture5. Writing one for a plain PRM shader would produce a file
        # nothing references.
        has_emission = any(
            ch.get('emi') is not None and ch['emi'].mode != 'NONE' for _, ch in resolved)
        emi_ctx = channels.get('matl_context')
        if EMI_MODE == 'NEVER':
            emi_wanted = False
        elif EMI_MODE == 'ALWAYS':
            emi_wanted = has_emission
        else:  # AUTO
            emi_wanted = has_emission and bool(emi_ctx and emi_ctx.reads_emissive)
            if has_emission and not emi_wanted:
                msg = ("  EMI: material emits, but its shader does not read "
                       "Texture5 - no _emi written. Apply the Emissive preset "
                       "to give it one.")
                print(msg)
                lines.append(msg.strip())

        def members_for(key):
            return [(m, ch.get(key)) for m, ch in resolved]

        if DRY_RUN:
            summary.extend(lines + [""])
            continue

        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)
        view_layer.objects.active = objects[0]

        made = []
        try:
            mask = None
            if FILL_UNCOVERED_TEXELS:
                mask = bake_coverage_mask(scene, objects, materials, BAKE_SIZE,
                                          scratch, group_name)
                made.append(mask)
                if SKIP_EMPTY_MATERIALS and not bool(
                        (gray_of(img_to_np(mask)) > 0.5).any()):
                    msg = ("  SKIP: no faces use this texture set, so it covers "
                           "none of the UV map.")
                    print(msg)
                    lines.append(msg)
                    raise _EmptyMaterial()

            if WRITE_COL:
                img_col = resolve_channel_image(
                    scene, objects, members_for('col'), f"{mat_key}__col",
                    BAKE_SIZE, "sRGB", scratch)
                made.append(img_col)
                alpha_ch = channels['alpha']
                if alpha_ch.mode != 'NONE':
                    img_a = resolve_channel_image(
                        scene, objects, members_for('alpha'), f"{mat_key}__colA",
                        BAKE_SIZE, "Non-Color", scratch,
                        default_rgba=(1.0, 1.0, 1.0, 1.0))
                    made.append(img_a)
                    apply_alpha(img_col, img_a)
                else:
                    px = img_to_np(img_col)
                    px[:, 3] = 1.0
                    np_to_img(img_col, px)
                apply_coverage_default(img_col, mask, COL_DEFAULT)
                out_col = unique_path(os.path.join(BAKE_DIR, f"{stem}_col.png"))
                save_image(img_col, out_col)
                written.append(out_col)
                print(f"    COL -> {os.path.basename(out_col)}")

            if WRITE_NOR:
                passthrough_rgb = passthrough_a = None
                img_norm = None
                cavity = None

                if channels['nor_rgb'].mode == 'SOCKET':
                    passthrough_rgb = resolve_channel_image(
                        scene, objects, members_for('nor_rgb'), f"{mat_key}__norSrc",
                        BAKE_SIZE, "Non-Color", scratch)
                    made.append(passthrough_rgb)
                    if channels['nor_a'].mode != 'NONE':
                        passthrough_a = resolve_channel_image(
                            scene, objects, members_for('nor_a'), f"{mat_key}__norSrcA",
                            BAKE_SIZE, "Non-Color", scratch,
                            default_rgba=(1.0, 1.0, 1.0, 1.0))
                        made.append(passthrough_a)
                else:
                    img_norm = create_or_get_image(f"{mat_key}__normBake", BAKE_SIZE)
                    set_colorspace(img_norm, "Non-Color")
                    if SKIP_FLAT_NORMAL_BAKE and normal_bake_would_be_flat(materials, objects):
                        # Nothing drives a Normal input and no modifier adds
                        # surface detail, so a tangent-space bake of these
                        # objects onto themselves can only produce flat
                        # (0.5, 0.5, 1.0) everywhere. Filling it directly
                        # skips a bake call that could never have found
                        # anything - and every call costs ~0.5s before it
                        # traces a ray. Anything that CAN carry detail (a
                        # Bump/Normal Map node, a linked Normal socket, a
                        # multires/displace modifier) falls through to a real
                        # bake below.
                        # 128/255, not 0.5. That is the exact byte value a
                        # real tangent-space bake writes for a flat normal;
                        # plain 0.5 lands on 127 after quantisation and makes
                        # the skipped result differ from a baked one by 1/255.
                        fill_image_const(img_norm, (128.0 / 255.0, 128.0 / 255.0, 1.0, 1.0))
                    else:
                        scene.render.bake.normal_space = "TANGENT"
                        bake_pass(scene, objects, materials, 'NORMAL', img_norm,
                                  "Non-Color", scratch, samples=1)
                    made.append(img_norm)

                    if NOR_ALPHA_MODE == "FROM_CAVITY":
                        cavity = create_or_get_image(f"{mat_key}__cavity", BAKE_SIZE)
                        set_colorspace(cavity, "Non-Color")
                        world = scene.world
                        prev = None
                        if world is not None:
                            prev = world.light_settings.distance
                            world.light_settings.distance = NOR_CAVITY_DISTANCE
                        try:
                            bake_pass(scene, objects, materials, 'AO', cavity,
                                      "Non-Color", scratch, samples=AO_SAMPLES)
                        finally:
                            if world is not None and prev is not None:
                                world.light_settings.distance = prev
                        made.append(cavity)

                img_nor = pack_nor(img_norm, f"{mat_key}__nor", BAKE_SIZE,
                                   cavity_img=cavity,
                                   passthrough_rgb=passthrough_rgb,
                                   passthrough_alpha=passthrough_a)
                made.append(img_nor)
                apply_coverage_default(img_nor, mask, NOR_DEFAULT)
                out_nor = unique_path(os.path.join(BAKE_DIR, f"{stem}_nor.png"))
                save_image(img_nor, out_nor)
                written.append(out_nor)
                print(f"    NOR -> {os.path.basename(out_nor)}")

            if WRITE_PRM:
                prm_rgb = None
                img_metal = img_rough = img_ao = None

                if channels['prm_rgb'].mode == 'SOCKET':
                    prm_rgb = resolve_channel_image(
                        scene, objects, members_for('prm_rgb'), f"{mat_key}__prmSrc",
                        BAKE_SIZE, "Non-Color", scratch)
                    made.append(prm_rgb)
                else:
                    img_metal = resolve_channel_image(
                        scene, objects, members_for('metal'), f"{mat_key}__metal",
                        BAKE_SIZE, "Non-Color", scratch)
                    img_rough = resolve_channel_image(
                        scene, objects, members_for('rough'), f"{mat_key}__rough",
                        BAKE_SIZE, "Non-Color", scratch)
                    made.extend([img_metal, img_rough])

                    ao_ch = channels['ao']
                    img_ao = create_or_get_image(f"{mat_key}__ao", BAKE_SIZE)
                    set_colorspace(img_ao, "Non-Color")
                    if ao_ch.mode == 'PASS':
                        bake_pass(scene, objects, materials, 'AO', img_ao,
                                  "Non-Color", scratch, samples=AO_SAMPLES)
                        px = img_to_np(img_ao)
                        floor = clamp01(PRM_AO_FLOOR)
                        px[gray_of(px) < floor, 0:3] = floor
                        np_to_img(img_ao, px)
                    else:
                        fill_image_const(img_ao, ao_ch.value)
                    made.append(img_ao)

                img_spec = resolve_channel_image(
                    scene, objects, members_for('spec'), f"{mat_key}__spec",
                    BAKE_SIZE, "Non-Color", scratch)
                made.append(img_spec)

                img_prm = pack_prm(img_metal, img_rough, img_ao, img_spec,
                                   prm_rgb, f"{mat_key}__prm", BAKE_SIZE,
                                   spec_scale,
                                   alpha_is_rotation=channels.get(
                                       'prm_alpha_is_rotation', False))
                made.append(img_prm)
                apply_coverage_default(img_prm, mask, PRM_DEFAULT)
                out_prm = unique_path(os.path.join(BAKE_DIR, f"{stem}_prm.png"))
                save_image(img_prm, out_prm)
                written.append(out_prm)
                print(f"    PRM -> {os.path.basename(out_prm)}")

                if WRITE_COMPONENT_DEBUG_MAPS:
                    for tag, comp in (("metal", img_metal), ("rough", img_rough),
                                      ("ao", img_ao), ("spec", img_spec)):
                        if comp is not None:
                            save_image(comp, os.path.join(BAKE_DIR, f"{stem}_{tag}.png"))

            if WRITE_EMI and emi_wanted:
                img_emi_src = resolve_channel_image(
                    scene, objects, members_for('emi'), f"{mat_key}__emiSrc",
                    BAKE_SIZE, "sRGB", scratch, default_rgba=EMI_DEFAULT)
                made.append(img_emi_src)

                img_emi, cv3 = pack_emi(img_emi_src, f"{mat_key}__emi", BAKE_SIZE,
                                        channels.get('emi_scale', 1.0))
                made.append(img_emi)
                apply_coverage_default(img_emi, mask, EMI_DEFAULT)
                out_emi = unique_path(os.path.join(BAKE_DIR, f"{stem}_emi.png"))
                save_image(img_emi, out_emi)
                written.append(out_emi)
                LAST_BAKE_INFO[stem] = {'custom_vector_3': cv3}
                print(f"    EMI -> {os.path.basename(out_emi)}")
                lines.append(f"EMI: {stem}_emi.png   CustomVector3 = "
                             f"({cv3[0]:.3f}, {cv3[1]:.3f}, {cv3[2]:.3f}, {cv3[3]:.3f})")

            if DEBUG_SAMPLE_PIXELS:
                for label, filepath, cs in (("COL", out_col, "sRGB"),
                                            ("NOR", out_nor, "Non-Color"),
                                            ("PRM", out_prm, "Non-Color"),
                                            ("EMI", out_emi, "sRGB")):
                    if filepath is None:
                        continue
                    check = load_fresh_image(filepath, cs)
                    lines.append(sample_pixels(check, label, DEBUG_SAMPLE_COUNT))
                    bpy.data.images.remove(check)

        except _EmptyMaterial:
            pass
        except Exception as e:
            msg = f"  FAILED: {type(e).__name__}: {e}"
            print(msg)
            lines.append(msg)
            import traceback
            traceback.print_exc()
        finally:
            for img in made:
                free_image(img)

        if DEBUG_WRITE_DEBUG_REPORT:
            write_report(os.path.join(BAKE_DIR, f"{mat_key}_DEBUG.txt"), lines)
        summary.extend(lines + [""])

    free_image(scratch)

    bpy.ops.object.select_all(action='DESELECT')
    for obj in original_selected:
        if obj and obj.name in bpy.context.scene.objects:
            obj.select_set(True)
    if original_active and original_active.name in bpy.context.scene.objects:
        view_layer.objects.active = original_active

    if DEBUG_WRITE_DEBUG_REPORT:
        write_report(os.path.join(BAKE_DIR, "_BAKE_REPORT.txt"), summary)
    print("\nDone" + (" (dry run - nothing written)" if DRY_RUN else "")
          + f". Output folder: {BAKE_DIR}")
    return written


# =============================================================================
# NUTEXB COMPILE
# =============================================================================
import json
import subprocess
import struct
import glob

from ..model.material.texture.convert_nutexb_to_png import get_ultimate_tex_path


def settings_file():
    """Where the remembered nutexb output folder lives - per user, not per blend."""
    folder = bpy.utils.user_resource('CONFIG', path="bake_texs", create=True)
    return os.path.join(folder, "settings.json")

def load_settings():
    try:
        with open(settings_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(data):
    try:
        with open(settings_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"  ! could not save settings: {e}")

def get_output_dir():
    path = load_settings().get("nutexb_dir", "")
    return path if path and os.path.isdir(path) else ""

def set_output_dir(path):
    data = load_settings()
    data["nutexb_dir"] = os.path.normpath(bpy.path.abspath(path))
    save_settings(data)
    print(f"  Remembered nutexb output folder: {data['nutexb_dir']}")

def read_nutexb_format(path):
    """Pull the format id out of a nutexb footer, so we can verify the CLI."""
    try:
        with open(path, "rb") as f:
            f.seek(-48, os.SEEK_END)
            return struct.unpack("<12I", f.read(48))[4]
    except Exception:
        return None

def suffix_of(stem):
    for suffix in NUTEXB_FORMATS:
        if stem.lower().endswith(suffix):
            return suffix
    return None

def target_name(out_dir, stem):
    """Reuse an existing file's exact casing when one matches case-insensitively."""
    if NUTEXB_MATCH_EXISTING_CASE:
        wanted = (stem + ".nutexb").lower()
        try:
            for existing in os.listdir(out_dir):
                if existing.lower() == wanted:
                    return existing
        except Exception:
            pass
    return stem + ".nutexb"

def compile_nutexb(png_paths, out_dir=None):
    """Convert baked PNGs to .nutexb in the model folder. Returns (ok, failed)."""
    out_dir = out_dir or get_output_dir()
    if not out_dir or not os.path.isdir(out_dir):
        print(f"  ! nutexb output folder is not set or missing: {out_dir!r}")
        return (0, 0)

    cli = str(get_ultimate_tex_path())
    if not os.path.isfile(cli):
        print(f"  ! ultimate_tex_cli not found at {cli!r} - is dependencies/ultimate_tex/ present?")
        return (0, 0)

    ok = failed = 0
    print(f"\nCompiling nutexb -> {out_dir}")
    for png in sorted(png_paths):
        if not png or not os.path.isfile(png):
            continue
        stem = os.path.splitext(os.path.basename(png))[0]
        suffix = suffix_of(stem)
        if suffix is None:
            continue                            # not a col/nor/prm map
        fmt = NUTEXB_FORMATS[suffix]
        if fmt not in NUTEXB_VALID_FORMATS:
            print(f"  ! {stem}: {fmt!r} is not a valid format name - skipped.")
            failed += 1
            continue

        out_path = os.path.join(out_dir, target_name(out_dir, stem))
        cmd = [cli, png, out_path, "--format", fmt]
        if not NUTEXB_MIPMAPS:
            cmd.append("--no-mipmaps")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            print(f"  ! {stem}: could not run the CLI: {e}")
            failed += 1
            continue

        if result.returncode != 0 or not os.path.isfile(out_path):
            print(f"  ! {stem}: FAILED (exit {result.returncode}) "
                  f"{result.stderr.strip()[:120]}")
            failed += 1
            continue

        written = read_nutexb_format(out_path)
        written_name = NUTEXB_FORMAT_IDS.get(written, f"id {written}")
        if written in NUTEXB_FORMAT_IDS and written_name != fmt:
            print(f"  ! {os.path.basename(out_path)}: asked for {fmt} but the "
                  f"file says {written_name}")
            failed += 1
            continue

        ok += 1
        print(f"    {os.path.basename(out_path):48s} {fmt}")

    print(f"  nutexb: {ok} written" + (f", {failed} failed" if failed else ""))
    return (ok, failed)

def bakes_in_folder(folder=None):
    folder = folder or BAKE_DIR
    return [p for p in glob.glob(os.path.join(folder, "*.png"))
            if suffix_of(os.path.splitext(os.path.basename(p))[0])]
