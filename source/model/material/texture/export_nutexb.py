import bpy
import os
import struct

from pathlib import Path
from subprocess import run, CalledProcessError

from .convert_nutexb_to_png import get_ultimate_tex_path
from ..create_matl_from_blender_materials import has_sub_matl_data, get_linked_materials, resolve_material_data_source
from .default_textures import generated_default_texture_name_value
from .. import validate
from ....material_grouping import group_key_for
from ...export_model import would_trimmed_names_be_unique, get_problematic_names, trim_name

# Texture4 (NOR) and Texture6 (PRM) are the only slots create_blender_materials_
# from_matl.py's own setup_blender_material_node_tree() explicitly forces to
# 'Non-Color' on import - COL (Texture0) is never explicitly set to 'sRGB'
# anywhere in this addon, it only ever gets whatever colorspace Blender
# happened to default a freshly-loaded image to. That made this function's
# old colorspace-only format check silently wrong for COL whenever that
# default wasn't exactly 'sRGB' - a real, reproduced case (colorspace was
# something else, so it fell into the "unsupported colorspace" branch, which
# defaulted to BC7Unorm/linear: a color texture written as data). Determine
# the format primarily from which texture param the image actually is - a
# deterministic Smash Ultimate convention, not per-image Blender state -
# falling back to the image's own colorspace only for slots outside this set.
#
# Texture2/7 (cube maps) and Texture16 (ink normal) are data slots too, and
# used to be missing from this set - the same silent mis-format described above
# for COL was possible for them in reverse. The list now comes from validate.py
# so that the exporter and the material linter cannot disagree about which
# slots hold colour; it matches expects_srgb() in SSBH Editor's validation.rs.
LINEAR_TEXTURE_PARAM_NAMES = validate.NON_COLOR_TEXTURE_PARAMS

# ultimate_tex_cli's real format names.
#
# These used to be passed as "BC7Srgb" and "BC7Unorm", which are not format
# names the tool knows. It does not reject an unknown --format: it exits 0,
# writes nothing to stderr, and silently produces BC7RgbaUnorm. Verified
# directly - "--format TotalNonsense" behaves identically. So "BC7Unorm" was
# landing on the right format by pure coincidence, while every colour texture
# asked for as "BC7Srgb" was written linear. That is why exported _col
# textures came out unorm: there was never any error to notice.
NUTEXB_FORMAT_SRGB = "BC7RgbaUnormSrgb"
NUTEXB_FORMAT_LINEAR = "BC7RgbaUnorm"

# Format id stored in the nutexb footer, used to confirm what was written.
NUTEXB_FORMAT_IDS = {
    1024: "Rgba8Unorm",
    1029: "Rgba8UnormSrgb",
    1157: "BC1RgbaUnormSrgb",
    1248: "BC7RgbaUnorm",
    1253: "BC7RgbaUnormSrgb",
}


def read_nutexb_format(path: Path) -> str | None:
    """The format a .nutexb was actually written with, or None if unreadable.

    Worth the read-back: the converter cannot be trusted to report a format it
    did not honour, so this is the only way to know the request took effect.
    """
    try:
        with open(path, "rb") as f:
            f.seek(-48, os.SEEK_END)
            format_id = struct.unpack("<12I", f.read(48))[4]
        return NUTEXB_FORMAT_IDS.get(format_id, f"unknown ({format_id})")
    except Exception:
        return None

def export_nutexb_from_blender_materials(operator: bpy.types.Operator, materials: set[bpy.types.Material], export_dir: Path):
    # image -> every param_id_name (Texture0, Texture4, ...) it's plugged into,
    # across every exported material - needed to pick the export format below,
    # since a single `images` set would lose which slot each one came from.
    image_param_names: dict[bpy.types.Image, set[str]] = {}

    linked_materials = get_linked_materials(materials)
    all_materials = materials | linked_materials
    materials_by_name = {m.name: m for m in all_materials}

    prefer_side_loaded = bpy.context.scene.sub_scene_properties.export_prefer_sideloaded_materials
    # Same resolution as create_matl_from_blender_materials.py, so the images
    # exported here always match what the matl entries actually reference.
    data_source_by_name = {
        name: resolve_material_data_source(material, prefer_side_loaded)
        for name, material in materials_by_name.items()
    }

    for material in all_materials:
        data_source = data_source_by_name[material.name]
        if not has_sub_matl_data(data_source):
            continue

        # A Shiny-style bundled material (see source/material_grouping.py) has
        # its matl texture params pointed at the base material's images
        # (create_matl_from_blender_materials.py), so its own images - if any
        # are even still assigned - are no longer referenced by anything and
        # would just be an orphaned file in the output folder. Skip them here;
        # the base material's own pass through this loop exports the shared
        # set. Falls back to exporting this material's own images when the
        # base isn't present in this export, matching the same fallback in
        # create_matl_from_blender_materials.get_texture_overrides_for_bundle().
        group_key = group_key_for(material.name, set(materials_by_name))
        base_material = materials_by_name.get(group_key)
        if group_key != material.name and base_material is not None:
            base_data_source = data_source_by_name.get(group_key, base_material)
            if has_sub_matl_data(base_data_source):
                continue

        for texture in data_source.sub_matl_data.textures:
            # A slot with no image is normal, not an error: the matl side
            # (create_matl_from_blender_materials.get_texture) resolves it to a
            # default texture path by name and reports that it did so. There is
            # no image to write a .nutexb from, so there is nothing to do here.
            # This used to dereference texture.image unconditionally and raise
            # AttributeError partway through, which aborted texture export for
            # the whole model - every material after the empty slot silently
            # lost its textures.
            if texture.image is None:
                continue
            if texture.image.name in generated_default_texture_name_value:
                operator.report({'INFO'}, f'Not exporting {texture.image.name}, as it is a default texture.')
                continue
            image_param_names.setdefault(texture.image, set()).add(texture.param_id_name)

    images = set(image_param_names)
    texture_names = {image.name for image in images}

    trim_names = would_trimmed_names_be_unique(texture_names)

    temp_image_path = export_dir.joinpath("temp.png")
    for image in images:
        # Incase a user attempts to export placeholder images.
        if not image.packed_file:
            if image.source == 'FILE':
                if image.filepath == '':
                    operator.report({'WARNING'}, f"The image `{image.name}` is just a placeholder in blender (likely due to a failed import), so it has no data and cannot be exported.")
                    continue
        # For some image types, such as DDS, blender fails to save using "save", but "save_render" still works.
        try:
            image.file_format = 'PNG' # This feels like a hack... but changing this before saving ensures blender exports as a PNG even if its on-disk as a JPG or BMP etc
            image.save(filepath=str(temp_image_path))
        except Exception as e:
            operator.report({'WARNING'}, f"Unable to save the blender image {image.name} to disk using `save`, but will attempt using `save_render`. Error = {e}")
            try:
                image.save_render(filepath=str(temp_image_path))
            except Exception as e:
                operator.report({'ERROR'}, f"Failed to save the blender image `{image.name}` to disk using either `save` or `save_render`. Error = {e}")
                continue

        nutexb_filepath: Path
        if trim_names:
            nutexb_filepath = export_dir.joinpath(trim_name(image.name) + ".nutexb")
        else:
            nutexb_filepath = export_dir.joinpath(image.name + ".nutexb")

        # The slot an image is plugged into decides the format, in BOTH
        # directions. Only the linear half of this used to be decided that way;
        # the sRGB half still fell back to the image's own colorspace, so a
        # _col image that happened to be marked 'Non-Color' - which is easy to
        # end up with, since nothing in this addon forces Texture0 to sRGB -
        # was written as BC7Unorm. That is a colour texture stored as data: it
        # renders visibly washed out in game, and nothing about the export
        # reports a problem. Blender colorspace is only consulted now for an
        # image in no recognised slot at all.
        format: str
        param_names = image_param_names.get(image, set())
        linear_params = param_names & LINEAR_TEXTURE_PARAM_NAMES
        color_params = param_names - LINEAR_TEXTURE_PARAM_NAMES

        if linear_params and color_params:
            # The same image feeds both a data slot and a colour slot, so one
            # of them is going to be wrong whichever format is chosen. Keep the
            # data reading intact, since a mis-decoded normal or PRM map breaks
            # shading outright rather than just shifting a colour.
            operator.report({'WARNING'}, f"Image `{image.name}` is used as both data "
                                         f"({sorted(linear_params)}) and colour ({sorted(color_params)}). "
                                         f"Exporting as linear BC7Unorm - split it into two images if the "
                                         f"colour slot looks wrong.")
            format = NUTEXB_FORMAT_LINEAR
        elif linear_params:
            format = NUTEXB_FORMAT_LINEAR
        elif color_params:
            format = NUTEXB_FORMAT_SRGB
        elif image.colorspace_settings.name == 'Non-Color':
            format = NUTEXB_FORMAT_LINEAR
        else:
            format = NUTEXB_FORMAT_SRGB

        try:
            run([get_ultimate_tex_path(), str(temp_image_path), str(nutexb_filepath), "--format", format], capture_output=True, check=True)
        except CalledProcessError as e:
            operator.report({'WARNING'}, f"failed to export `{image.name}` as .NUTEXB, error = {e.stderr}")
            continue

        # Confirm the converter honoured the request. It reports success for a
        # format name it does not recognise, so without this a silently wrong
        # format is invisible - which is exactly how every _col texture came to
        # be written linear for as long as the wrong names were being passed.
        written_format = read_nutexb_format(nutexb_filepath)
        if written_format is not None and written_format != format:
            operator.report({'WARNING'},
                            f"`{image.name}` was requested as {format} but the converter "
                            f"wrote {written_format}. The texture will render incorrectly.")

    try:
        if temp_image_path.exists():
            temp_image_path.unlink()
    except Exception as e:
        operator.report({'WARNING'}, f"Failed to remove temporary .png file used for exporting textures, error = {e}")
