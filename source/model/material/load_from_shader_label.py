import bpy
import sqlite3

from pathlib import Path
from typing import Any

from ....dependencies import ssbh_data_py
from .sub_matl_data import *
from .matl_params import vector_param_id_values, param_id_to_ui_name, vector_param_id_value_to_default_value
from ..export_model import default_texture
from .matl_params import *
from .create_blender_materials_from_matl import setup_blender_material_settings, setup_blender_material_node_tree, get_shader_db_file_path, get_vertex_attributes, create_default_textures

"""
def get_shader_db_file_path():
    # This file was generated with duplicates removed to optimize space.
    # https://github.com/ScanMountGoat/Smush-Material-Research#shader-database
    this_file_path = Path(__file__)
    return this_file_path.parent.parent.parent.joinpath('shader_file').joinpath('Nufx.db').resolve()
"""

def is_valid_shader_label(operator: bpy.types.Operator, shader_label: str) -> bool:
    if len(shader_label) > len("SFX_PBS_0100000008008269_opaque"):
        operator.report({'ERROR'}, f'Shader Label "{shader_label}" was too long!')
        return False
    if len(shader_label) < len("SFX_PBS_0100000008008269"):
        operator.report({'ERROR'}, f'Shader Label "{shader_label}" was too short!')
        return False
    if len(shader_label) != len("SFX_PBS_0100000008008269"):
        suffixes = ["_opaque", "_far", "_sort", "_near"]
        if not any(shader_label.endswith(suffix) for suffix in suffixes):
            operator.report({'ERROR'}, f'Shader Label "{shader_label}" has an invalid suffix!')
            return False
    with sqlite3.connect(get_shader_db_file_path()) as con:
        sql = """
            SELECT *
            FROM ShaderProgram s 
            WHERE s.Name = ?
            """
        ret = [row[0] for row in con.execute(sql, (shader_label[:len('SFX_PBS_0000000000000080')],)).fetchall()]
        if len(ret) == 0:
            operator.report({'ERROR'}, f'Shader Label "{shader_label}" was not in the database!')
            return False
    return True


def get_material_parameter_ids(shader_label: str) -> set[int]:
    # Query the shader database for attribute information.
    # Using SQLite is much faster than iterating through the JSON dump.
    with sqlite3.connect(get_shader_db_file_path()) as con:
        # Construct a query to find all the vertex attributes for this shader.
        # Invalid shaders will return an empty list.
        sql = """
            SELECT m.ParamId
            FROM MaterialParameter m 
            INNER JOIN ShaderProgram s ON m.ShaderProgramID = s.ID 
            WHERE s.Name = ?
            """
        # The database has a single entry for each program, so don't include the render pass tag.
        return {row[0] for row in con.execute(sql, (shader_label[:len('SFX_PBS_0000000000000080')],)).fetchall()}


def create_sub_matl_data_from_shader_label(material: bpy.types.Material, shader_label: str):

    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data

    collections = (
        sub_matl_data.bools,
        sub_matl_data.floats,
        sub_matl_data.vectors,
        sub_matl_data.textures,
        sub_matl_data.samplers,
        sub_matl_data.blend_states,
        sub_matl_data.rasterizer_states,
    )
    needed_param_ids: set[int] = get_material_parameter_ids(shader_label)

    # The placeholder images the new texture slots point at only exist in a
    # .blend that has imported a model, because that is the only thing that
    # used to call this. Setting a material up from a shader label or a preset
    # can happen in a file that never imported anything, and then every
    # bpy.data.images.get() below returns None and the slots come out empty -
    # which export then has to paper over with default texture paths, and which
    # crashed .nutexb export outright before it learned to skip them. Creating
    # them up front costs nothing when they already exist.
    create_default_textures()
    # Remove Un-Needed Attributes
    for collection in collections:
        prop_names_to_remove: set[str] = set(name for name,prop in collection.items() if prop.param_id_value not in needed_param_ids)
        for prop_name in prop_names_to_remove:
            sub_matl_prop_index = collection.find(prop_name)
            collection.remove(sub_matl_prop_index)

    # Add Missing ones
    current_param_ids: set[int] = {sub_matl_prop.param_id_value for c in collections for sub_matl_prop in c}
    missing_param_ids = needed_param_ids - current_param_ids
    for missing_param_id in missing_param_ids:
        if missing_param_id in bool_param_id_values:
            sub_matl_data.add_bool(
                ssbh_data_py.matl_data.BooleanParam(
                    param_id=ssbh_data_py.matl_data.ParamId.from_value(missing_param_id),
                    data=bool_param_id_value_to_default_value[missing_param_id]
                )
            )
        elif missing_param_id in float_param_id_values:
            sub_matl_data.add_float(
                ssbh_data_py.matl_data.FloatParam(
                    param_id=ssbh_data_py.matl_data.ParamId.from_value(missing_param_id),
                    data=float_param_id_value_to_default_value[missing_param_id]
                )
            )
        elif missing_param_id in vector_param_id_values:
            sub_matl_data.add_vector(
                ssbh_data_py.matl_data.Vector4Param(
                    param_id=ssbh_data_py.matl_data.ParamId.from_value(missing_param_id),
                    # The defaults table stores tuples, but ssbh_data_py's
                    # Vector4Param only accepts a list and raises TypeError on
                    # anything else. Without this conversion, adding any missing
                    # vector param fails - which is every vector param when the
                    # material is new, so setting a material up from a shader
                    # label could never work at all.
                    data=list(vector_param_id_value_to_default_value[missing_param_id]),
                )
            )
        elif missing_param_id in texture_param_id_values:
            tex_param_id = ssbh_data_py.matl_data.ParamId.from_value(missing_param_id)
            default_texture_name = default_texture(tex_param_id.name)
            default_image = bpy.data.images.get(default_texture_name)
            sub_matl_data.add_texture(
                texture_param=ssbh_data_py.matl_data.TextureParam(
                    param_id=tex_param_id,
                    data=default_texture_name,
                ),
                texture_name_to_image_dict={default_texture_name:default_image},
            )

        elif missing_param_id in sampler_param_id_values:
            param_id = ssbh_data_py.matl_data.ParamId.from_value(missing_param_id)
            new_sampler: SUB_PG_matl_sampler = sub_matl_data.samplers.add()
            new_sampler.name = param_id.name
            new_sampler.param_id_name = param_id.name
            new_sampler.param_id_value = param_id.value
            new_sampler.ui_name = param_id_to_ui_name[param_id.value]
            new_sampler.node_name = param_id.name
            new_sampler.sampler_number = int(param_id.name.split('Sampler')[1])
            
        elif missing_param_id in blend_state_param_id_values:
            param_id = ssbh_data_py.matl_data.ParamId.from_value(missing_param_id)
            new_blend_state: SUB_PG_matl_blend_state = sub_matl_data.blend_states.add()
            new_blend_state.name = param_id.name
            new_blend_state.ui_name = param_id_to_ui_name[param_id.value]
            new_blend_state.param_id_name = param_id.name
            new_blend_state.param_id_value = param_id.value

        elif missing_param_id in rasterizer_state_param_id_values:
            param_id = ssbh_data_py.matl_data.ParamId.from_value(missing_param_id)
            new_rasterizer_state: SUB_PG_matl_rasterizer_state = sub_matl_data.rasterizer_states.add()
            new_rasterizer_state.name = param_id.name
            new_rasterizer_state.ui_name = param_id_to_ui_name[param_id.value]
            new_rasterizer_state.param_id_name = param_id.name
            new_rasterizer_state.param_id_value = param_id.value

    # Repair texture slots that already existed but have no image. The loop
    # above only touches params the material was MISSING, so a slot left empty
    # by an earlier run - before create_default_textures() was called up front -
    # would stay empty no matter how many times a preset was re-applied. Since
    # re-applying the preset is the obvious thing to try when a material looks
    # wrong, it should actually fix it.
    for sub_matl_texture in sub_matl_data.textures:
        if sub_matl_texture.image is not None:
            continue
        placeholder_name = default_texture(sub_matl_texture.param_id_name)
        placeholder = bpy.data.images.get(placeholder_name)
        if placeholder is not None:
            sub_matl_texture.image = placeholder

    # Refresh needed vertex attributes
    sub_matl_data.vertex_attributes.clear()
    attrs = get_vertex_attributes(shader_label)
    sub_matl_data.add_vertex_attributes(attrs)
    
    # Relabel
    if len(shader_label) == len("SFX_PBS_0100000008008269"):
        shader_label = f'{shader_label}_opaque'
    sub_matl_data.set_shader_label(shader_label)

    # Reload Material
    setup_blender_material_settings(material)
    setup_blender_material_node_tree(material)
    return


def apply_material_preset(material: bpy.types.Material, preset) -> list[str]:
    """Switch a material onto a preset's shader and write the preset's values.

    Textures survive this. create_sub_matl_data_from_shader_label() keeps every
    param the incoming shader still wants and only drops the ones it doesn't,
    and the node tree rebuild re-reads its image pointers out of sub_matl_data
    - so moving Body from Standard to Alpha Blend keeps the col/nor/prm you
    already assigned and only changes how they are rendered.

    Returns a list of notes about anything the preset could not set, for the
    operator to report. An empty list means everything applied.
    """
    notes = []

    create_sub_matl_data_from_shader_label(material, preset.shader_label)
    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data

    # Only params the new shader kept are present, so a miss here means the
    # preset named something this shader has no slot for - worth surfacing
    # rather than silently dropping.
    for collection, values, kind in (
        (sub_matl_data.vectors, preset.vectors, 'vector'),
        (sub_matl_data.floats, preset.floats, 'float'),
        (sub_matl_data.bools, preset.bools, 'bool'),
    ):
        for param_name, value in values.items():
            prop = collection.get(param_name)
            if prop is None:
                notes.append(f'{param_name} ({kind}) is not used by {preset.shader_label}')
                continue
            prop.value = value

    if preset.blend is not None:
        source_color, destination_color, alpha_to_coverage = preset.blend
        for blend_state in sub_matl_data.blend_states:
            blend_state.source_color = source_color
            blend_state.destination_color = destination_color
            blend_state.alpha_sample_to_coverage = alpha_to_coverage

    for rasterizer_state in sub_matl_data.rasterizer_states:
        rasterizer_state.cull_mode = preset.cull_mode

    # Values were written after create_sub_matl_data_from_shader_label() did
    # its own rebuild, so the node tree has to be rebuilt again to pick them up.
    setup_blender_material_settings(material)
    setup_blender_material_node_tree(material)
    return notes