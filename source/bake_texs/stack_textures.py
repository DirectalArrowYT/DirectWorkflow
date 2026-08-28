"""
Stack two materials' baked textures into one atlas, switched by CustomVector6.

A material can only reference one Col map, but a face often needs two looks -
Shigaraki clean, and Shigaraki with the burn - swapped at runtime. Rather than
swapping the texture (which a material cannot do) or swapping the mesh (which
costs a draw call and a vis track), both looks go into one texture stacked
vertically and the material scrolls between them with its UV transform.

THE MATH, read out of the vertex shader rather than assumed
  SFX_PBS_010000080800826b's vertex shader applies CustomVector6 to map1 as:

      U_out = CV6.x * (U - CV6.z)
      V_out = 1 - CV6.y * (1 - CV6.w - V)

  The identity CustomVector6 (1, 1, 0, 0) gives V_out = 1 - (1 - 0 - V) = V,
  which is the check that the reading is right.

  Setting CV6.y to 0.5 squeezes the mesh's 0-1 V range into half the atlas:

      CV6.w =  0   ->  V_out spans 0.5 .. 1.0   (the TOP half)
      CV6.w = -1   ->  V_out spans 0.0 .. 0.5   (the BOTTOM half)

  So CustomVector6 is (1, 0.5, 0, 0) at rest, and animating just .w from 0 to
  -1 cuts from the top texture to the bottom one. Nothing else changes.

  Note the halves are top and bottom as the IMAGE is seen. Blender's pixel
  buffer starts at the bottom row, so the top half is the second half of the
  buffer - which is why the top image is written to the upper slice below and
  not the lower one.

WHY THE OTHER MAPS HAVE TO BE STACKED TOO
  The UV transform is a property of the material, not of one texture: it moves
  the coordinates used to sample every map the shader reads. Stack the Col map
  alone and the Nor and PRM maps get sampled at the shifted coordinates as
  well, reading whatever happens to be in the other half. So Col, Nor, PRM and
  Emi all have to be stacked the same way, even when both halves are identical.
"""

import os

import bpy

from . import core

# The maps a stacked material needs, and the colour space each is read in.
STACKED_SUFFIXES = (
    ('_col', 'sRGB'),
    ('_nor', 'Non-Color'),
    ('_prm', 'Non-Color'),
    ('_emi', 'sRGB'),
)

# Two ways to aim the mesh at the top half of the atlas. Both put the same
# pixels on screen in game; they differ in what Blender shows you.
#
# SHADER  - the mesh keeps its 0-1 UVs and CustomVector6.y squeezes them into
#           half the atlas at runtime. Nothing about the mesh changes, but
#           Blender does not apply CustomVector6 (the master shader has no
#           wiring for it), so the viewport stretches the face over the whole
#           double-height texture.
# REMAP   - the mesh's V coordinates are rewritten into the top half up front,
#           so the UVs already point where the texture is. CustomVector6 stays
#           at identity scale and Blender previews it correctly.
STACK_UV_TRANSFORM = (1.0, 0.5, 0.0, 0.0)
STACK_W_TOP = 0.0
STACK_W_BOTTOM = -1.0

# With the UVs pre-remapped the scale is 1, so the offset needed to drop to the
# lower half is half a texture rather than a whole one.
REMAP_UV_TRANSFORM = (1.0, 1.0, 0.0, 0.0)
REMAP_W_TOP = 0.0
REMAP_W_BOTTOM = -0.5


def remap_uvs_to_top_half(mesh_objects, uv_layer_name=None):
    """Rewrite V so 0-1 becomes 0.5-1.0, the top half of a two-way atlas.

    V' = 0.5 + 0.5 * V, which is exactly the squeeze CustomVector6.y would have
    applied at runtime - just baked into the mesh so Blender can see it too.
    The aspect ratio is preserved: the texture got twice as tall and the UVs now
    cover half of it, so the face is neither stretched nor squashed.
    """
    import numpy as np

    remapped = []
    for obj in mesh_objects:
        mesh = obj.data
        if not mesh.uv_layers:
            continue
        layer = (mesh.uv_layers.get(uv_layer_name) if uv_layer_name
                 else mesh.uv_layers.active)
        if layer is None:
            continue

        count = len(layer.data)
        buffer = np.empty(count * 2, dtype=np.float32)
        layer.data.foreach_get('uv', buffer)
        coords = buffer.reshape(-1, 2)
        coords[:, 1] = 0.5 + 0.5 * coords[:, 1]
        layer.data.foreach_set('uv', coords.reshape(-1))
        mesh.update()
        remapped.append(obj.name)
    return remapped


def restore_uvs_from_full_range(mesh_objects, uv_layer_name=None):
    """The inverse of remap_uvs_to_top_half: V' = (V - 0.5) * 2.

    Needed before re-baking. A bake writes into wherever the UVs point, so
    baking a mesh whose UVs already sit in the top half would put the result
    in the top half of a fresh full-size texture - half of it wasted, half of
    it empty.
    """
    import numpy as np

    restored = []
    for obj in mesh_objects:
        mesh = obj.data
        if not mesh.uv_layers:
            continue
        layer = (mesh.uv_layers.get(uv_layer_name) if uv_layer_name
                 else mesh.uv_layers.active)
        if layer is None:
            continue
        buffer = np.empty(len(layer.data) * 2, dtype=np.float32)
        layer.data.foreach_get('uv', buffer)
        coords = buffer.reshape(-1, 2)
        coords[:, 1] = (coords[:, 1] - 0.5) * 2.0
        layer.data.foreach_set('uv', coords.reshape(-1))
        mesh.update()
        restored.append(obj.name)
    return restored


def uv_v_range(obj, uv_layer_name=None):
    """(min V, max V) of a mesh's UVs, or None when it has none."""
    import numpy as np

    mesh = obj.data
    if not mesh.uv_layers:
        return None
    layer = (mesh.uv_layers.get(uv_layer_name) if uv_layer_name
             else mesh.uv_layers.active)
    if layer is None or len(layer.data) == 0:
        return None
    buffer = np.empty(len(layer.data) * 2, dtype=np.float32)
    layer.data.foreach_get('uv', buffer)
    v = buffer.reshape(-1, 2)[:, 1]
    return float(v.min()), float(v.max())


def looks_remapped(obj, uv_layer_name=None, tolerance=0.01):
    """True when a mesh's V already sits inside the top half.

    Guards against running the remap twice, which would squeeze 0-1 into
    0.5-1.0 and then into 0.75-1.0 - silently shrinking the face into a
    quarter of the atlas with no error anywhere.
    """
    span = uv_v_range(obj, uv_layer_name)
    if span is None:
        return False
    return span[0] >= 0.5 - tolerance and span[1] <= 1.0 + tolerance


def meshes_using_material(material):
    import bpy as _bpy
    users = []
    for obj in _bpy.data.objects:
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            if slot.material is material:
                users.append(obj)
                break
    return users


def stack_images(top_image, bottom_image, out_name, colorspace, half_height=False):
    """One image with `top_image` above `bottom_image`.

    half_height squeezes each input to half its rows so the atlas keeps the
    original texture size, trading vertical resolution for file size. Off, the
    atlas is twice as tall and neither input loses anything.
    """
    import numpy as np

    top_width, top_height = top_image.size
    bottom_width, bottom_height = bottom_image.size
    if top_width != bottom_width:
        raise RuntimeError(
            f'{top_image.name} is {top_width}px wide but {bottom_image.name} is '
            f'{bottom_width}px - both halves must be the same width.')
    if top_height != bottom_height:
        raise RuntimeError(
            f'{top_image.name} is {top_height}px tall but {bottom_image.name} is '
            f'{bottom_height}px - both halves must be the same height.')

    def pixels_of(image):
        buffer = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        return buffer.reshape(image.size[1], image.size[0], 4)

    top = pixels_of(top_image)
    bottom = pixels_of(bottom_image)

    if half_height:
        # Drop every other row. Crude next to a filtered resize, but these are
        # baked maps with no fine detail to alias, and it keeps normals and PRM
        # values exact rather than averaging neighbours into something that was
        # never in either source.
        top = top[::2]
        bottom = bottom[::2]

    # Blender's buffer runs bottom row first, so the bottom image occupies the
    # first rows and the top image the last.
    stacked = np.concatenate([bottom, top], axis=0)

    height, width = stacked.shape[0], stacked.shape[1]

    # Made directly rather than through core.create_or_get_image, which only
    # makes square images - an atlas is twice as tall as it is wide by
    # definition, and resizing a generated image afterwards does not reliably
    # update its pixel buffer.
    existing = bpy.data.images.get(out_name)
    if existing is not None and tuple(existing.size) != (width, height):
        bpy.data.images.remove(existing)
        existing = None
    out = existing or bpy.data.images.new(
        name=out_name, width=width, height=height, alpha=True, float_buffer=False)
    out.alpha_mode = 'STRAIGHT'
    core.set_colorspace(out, colorspace)
    out.pixels.foreach_set(stacked.reshape(-1))
    out.update()
    return out


def stack_material_bakes(operator, top_material, bottom_material, top_stem,
                         bottom_stem, bake_dir, out_stem, half_height=False):
    """Stack every baked map the two materials have. Returns written paths."""
    written = []
    skipped = []

    for suffix, colorspace in STACKED_SUFFIXES:
        top_path = os.path.join(bake_dir, f'{top_stem}{suffix}.png')
        bottom_path = os.path.join(bake_dir, f'{bottom_stem}{suffix}.png')

        top_exists = os.path.isfile(top_path)
        bottom_exists = os.path.isfile(bottom_path)
        if not top_exists and not bottom_exists:
            continue
        if not (top_exists and bottom_exists):
            # Stacking only one half would put that map in the wrong place for
            # one of the two states, which is worse than not stacking it.
            skipped.append(
                f'{suffix} (only {"top" if top_exists else "bottom"} was baked)')
            continue

        top_image = core.load_fresh_image(top_path, colorspace)
        bottom_image = core.load_fresh_image(bottom_path, colorspace)
        try:
            stacked = stack_images(top_image, bottom_image,
                                   f'{out_stem}{suffix}', colorspace, half_height)
            out_path = os.path.join(bake_dir, f'{out_stem}{suffix}.png')
            core.save_image(stacked, out_path)
            written.append(out_path)
        finally:
            core.free_image(top_image)
            core.free_image(bottom_image)

    if skipped and operator is not None:
        operator.report({'WARNING'}, 'Not stacked: ' + '; '.join(skipped))
    return written


def apply_stacked_textures(material, out_stem, bake_dir, uv_mode='REMAP'):
    """Point a material at the stacked maps and set up its UV transform."""
    from .operators import _find_texture_slot, _load_or_refresh_image

    applied = 0
    notes = []
    for suffix, colorspace in STACKED_SUFFIXES:
        path = os.path.join(bake_dir, f'{out_stem}{suffix}.png')
        if not os.path.isfile(path):
            continue
        param = {'_col': 'Texture0', '_nor': 'Texture4',
                 '_prm': 'Texture6', '_emi': 'Texture5'}[suffix]
        slot = _find_texture_slot(material, param)
        if slot is None:
            notes.append(f'no {param} slot for {suffix}')
            continue
        slot.image = _load_or_refresh_image(path, f'{out_stem}{suffix}', colorspace)
        applied += 1

    sub_matl_data = getattr(material, 'sub_matl_data', None)
    uv_transform = sub_matl_data.vectors.get('CustomVector6') if sub_matl_data else None
    if uv_transform is not None:
        uv_transform.value = (REMAP_UV_TRANSFORM if uv_mode == 'REMAP'
                              else STACK_UV_TRANSFORM)
    else:
        notes.append(
            'this material has no CustomVector6 - its shader cannot scroll the '
            'UVs, so the stack cannot be switched. Use a shader that reads it, '
            'e.g. SFX_PBS_010000080800826b_opaque')

    return applied, notes
