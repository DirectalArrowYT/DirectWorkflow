import bpy
from bpy.types import PropertyGroup
from bpy.props import (
    StringProperty, IntProperty, BoolProperty, EnumProperty, FloatProperty,
)


class SUB_PG_bake_texs_settings(PropertyGroup):
    char_tag: StringProperty(
        name='Character Tag',
        default='',
        description="Appended after the mesh/material name, e.g. alp_Hair_Shigaraki_col.png",
    )
    name_from: EnumProperty(
        name='Name From',
        items=(
            ('MATERIAL', 'Material', 'Name output files from the material name'),
            ('OBJECT', 'Object', 'Name output files from the mesh object name'),
        ),
        default='MATERIAL',
    )
    use_selection_only: BoolProperty(
        name='Selected Only',
        default=True,
        description="Only bake meshes you have selected. Off = every visible mesh in the scene",
    )
    dry_run: BoolProperty(
        name='Dry Run',
        default=False,
        description="Report what would be baked, and from where, without baking. Fast, bakes nothing",
    )

    bake_size: EnumProperty(
        name='Bake Size',
        items=(
            ('512', '512', ''),
            ('1024', '1024', ''),
            ('2048', '2048', ''),
            ('4096', '4096', ''),
        ),
        default='2048',
    )
    bake_margin: IntProperty(name='Margin', default=16, min=0, max=64)
    bake_margin_type: EnumProperty(
        name='Margin Type',
        items=(
            ('EXTEND', 'Extend', 'Best for texture atlases'),
            ('ADJACENT_FACES', 'Adjacent Faces', ''),
        ),
        default='EXTEND',
    )
    bake_subfolder: StringProperty(
        name='Output Subfolder',
        default='bakes',
        description="Folder next to the .blend that baked PNGs are written into",
    )
    overwrite_files: BoolProperty(name='Overwrite Existing', default=True)

    write_col: BoolProperty(name='COL', default=True)
    write_nor: BoolProperty(name='NOR', default=True)
    write_prm: BoolProperty(name='PRM', default=True)
    write_component_debug_maps: BoolProperty(
        name='Write Component Debug Maps',
        default=False,
        description="Also dump _metal/_rough/_ao/_spec PNGs alongside the packed PRM",
    )

    prm_ao_mode: EnumProperty(
        name='PRM AO Mode',
        items=(
            ('AUTO', 'Auto', 'Bake AO only when a real PBR shader drives the surface'),
            ('BAKE', 'Always Bake', 'Always bake an AO pass into PRM.b'),
            ('CONST', 'Flat', 'Always use a flat PRM.b value'),
        ),
        default='AUTO',
    )

    compile_nutexb: BoolProperty(
        name='Compile to .nutexb',
        default=True,
        description="After baking, convert each PNG straight into the model folder",
    )
    nutexb_always_ask: BoolProperty(
        name='Always Ask For Folder',
        default=False,
        description="Prompt for the destination model folder every run instead of reusing the remembered one",
    )


def register():
    # SUB_PG_bake_texs_settings itself is registered via new_classes_to_register.py,
    # like every other PropertyGroup in this addon - this only attaches it to Scene.
    if not hasattr(bpy.types.Scene, "sub_bake_texs_properties"):
        bpy.types.Scene.sub_bake_texs_properties = bpy.props.PointerProperty(type=SUB_PG_bake_texs_settings)


def unregister():
    if hasattr(bpy.types.Scene, "sub_bake_texs_properties"):
        del bpy.types.Scene.sub_bake_texs_properties
