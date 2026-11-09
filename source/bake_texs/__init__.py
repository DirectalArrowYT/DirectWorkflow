"""Bake Textures, TextureCraft and the HB Master Shader.

Inside DirectWorkflow this is source/bake_texs and the add-on registers the
classes from new_classes_to_register.py. Built by tools/build_hb_smash_pbr.py
it is the whole "HB Smash PBR" add-on and registers itself here.

Both can be installed at once: whichever is enabled first owns the panels and
the other stays out of the way (see owned_elsewhere()).
"""
bl_info = {
    "name": "HB Smash PBR",
    "author": "Hype Bros Studio",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Smash",
    "description": "Bake Smash COL/NOR/PRM/EMI from any material, HB Master Shader, TextureCraft round trip",
    "category": "Material",
}

import bpy

from . import smash_bridge
from . import core
from . import properties
from . import operators
from . import ui
from . import master_shader
from . import hair_strands
from . import texturecraft

CLASSES = (
    properties.SUB_PG_bake_texs_settings,
    operators.SUB_OP_bake_texs_run,
    operators.SUB_OP_bake_texs_compile_only,
    operators.SUB_OP_bake_texs_apply_to_materials,
    operators.SUB_OP_bake_texs_pick_output,
    operators.SUB_OP_bake_texs_stack_materials,
    operators.SUB_OP_bake_texs_remap_uvs,
    ui.SUB_PT_bake_texs,
    ui.SUB_PT_bake_texs_channels,
    ui.SUB_PT_bake_texs_output,
    texturecraft.SUB_OP_texturecraft_send,
    texturecraft.SUB_OP_texturecraft_get,
    texturecraft.SUB_PT_texturecraft,
    master_shader.SUB_OP_hb_master_shader_update,
    master_shader.SUB_OP_hb_master_shader_convert,
    master_shader.SUB_OP_hb_master_shader_preset,
    master_shader.SUB_PT_hb_master_shader,
    hair_strands.SUB_OP_hb_hair_strands_add,
)

_registered_here = []


def owned_elsewhere():
    """True when the other copy (add-on or DirectWorkflow) already registered the panels."""
    existing = getattr(bpy.types, 'SUB_PT_bake_texs', None)
    return existing is not None and existing is not ui.SUB_PT_bake_texs


def register():
    if smash_bridge.STANDALONE:
        if owned_elsewhere():
            print('HB Smash PBR: DirectWorkflow already provides these panels; staying off')
            return
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_here.append(cls)
    # In DirectWorkflow the classes come from new_classes_to_register.py,
    # which skips them the same way when the standalone add-on got there first.
    if properties.SUB_PG_bake_texs_settings.is_registered:
        properties.register()


def unregister():
    if not properties.SUB_PG_bake_texs_settings.is_registered:
        return
    properties.unregister()
    for cls in reversed(_registered_here):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    _registered_here.clear()
