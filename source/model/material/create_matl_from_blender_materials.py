import bpy
import re

from ....dependencies import ssbh_data_py
from ...material_grouping import group_key_for, side_loaded_name
from .sub_matl_data import *

def get_blend_state(sub_matl_blend_state: SUB_PG_matl_blend_state) -> ssbh_data_py.matl_data.BlendStateParam:
    data=ssbh_data_py.matl_data.BlendStateData(
        source_color=ssbh_data_py.matl_data.BlendFactor.from_str(sub_matl_blend_state.source_color),
        destination_color=ssbh_data_py.matl_data.BlendFactor.from_str(sub_matl_blend_state.destination_color),
        alpha_sample_to_coverage=sub_matl_blend_state.alpha_sample_to_coverage
    )
    return ssbh_data_py.matl_data.BlendStateParam(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_blend_state.param_id_name),
        data=data,
    )
def get_blend_states(blend_states: list[SUB_PG_matl_blend_state]) -> list[ssbh_data_py.matl_data.BlendStateParam]:
    return [get_blend_state(sub_matl_blend_state) for sub_matl_blend_state in blend_states]

def get_float(sub_matl_float: SUB_PG_matl_float) -> ssbh_data_py.matl_data.FloatParam:
    return ssbh_data_py.matl_data.FloatParam(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_float.param_id_name),
        data=sub_matl_float.value
    )
def get_floats(floats: list[SUB_PG_matl_float]) -> list[ssbh_data_py.matl_data.FloatParam]:
    return [get_float(sub_matl_float) for sub_matl_float in floats]


def get_boolean(sub_matl_boolean: SUB_PG_matl_bool) -> ssbh_data_py.matl_data.BooleanParam:
    return ssbh_data_py.matl_data.BooleanParam(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_boolean.param_id_name),
        data=sub_matl_boolean.value
    )
def get_booleans(booleans: list[SUB_PG_matl_bool]) -> list[ssbh_data_py.matl_data.BooleanParam]:
    return [get_boolean(sub_matl_boolean) for sub_matl_boolean in booleans]

def get_vector(sub_matl_vector: SUB_PG_matl_vector) -> ssbh_data_py.matl_data.Vector4Param:
    return ssbh_data_py.matl_data.Vector4Param(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_vector.param_id_name),
        data=list(sub_matl_vector.value[0:4])
    )
def get_vectors(vectors: list[SUB_PG_matl_vector]) -> list[ssbh_data_py.matl_data.Vector4Param]:
    return [get_vector(sub_matl_vector) for sub_matl_vector in vectors]

def get_rasterizer_state(sub_matl_rasterizer_state: SUB_PG_matl_rasterizer_state) -> ssbh_data_py.matl_data.RasterizerStateParam:
    data = ssbh_data_py.matl_data.RasterizerStateData()
    data.cull_mode = ssbh_data_py.matl_data.CullMode.from_str(sub_matl_rasterizer_state.cull_mode)
    data.fill_mode = ssbh_data_py.matl_data.FillMode.from_str(sub_matl_rasterizer_state.fill_mode)
    data.depth_bias = sub_matl_rasterizer_state.depth_bias

    return ssbh_data_py.matl_data.RasterizerStateParam(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_rasterizer_state.param_id_name),
        data=data
    )
def get_rasterizer_states(rasterizer_states: list[SUB_PG_matl_rasterizer_state]) -> list[ssbh_data_py.matl_data.RasterizerStateParam]:
    return [get_rasterizer_state(rasterizer_state) for rasterizer_state in rasterizer_states]

def get_sampler(sub_matl_sampler: SUB_PG_matl_sampler) -> ssbh_data_py.matl_data.SamplerParam:
    data = ssbh_data_py.matl_data.SamplerData()
    data.wrapr = ssbh_data_py.matl_data.WrapMode.from_str(sub_matl_sampler.wrap_r)
    data.wraps = ssbh_data_py.matl_data.WrapMode.from_str(sub_matl_sampler.wrap_s)
    data.wrapt = ssbh_data_py.matl_data.WrapMode.from_str(sub_matl_sampler.wrap_t)
    data.min_filter = ssbh_data_py.matl_data.MinFilter.from_str(sub_matl_sampler.min_filter)
    data.mag_filter = ssbh_data_py.matl_data.MagFilter.from_str(sub_matl_sampler.mag_filter)
    if sub_matl_sampler.anisotropic_filtering is True:
        data.max_anisotropy = ssbh_data_py.matl_data.MaxAnisotropy.from_str(sub_matl_sampler.max_anisotropy)
    else:
        data.max_anisotropy = None
    data.border_color = list(sub_matl_sampler.border_color[0:4])
    data.lod_bias = sub_matl_sampler.lod_bias

    return ssbh_data_py.matl_data.SamplerParam(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_sampler.param_id_name),
        data=data,
    )

def get_samplers(samplers: list[SUB_PG_matl_sampler]) -> list[ssbh_data_py.matl_data.SamplerParam]:
    return [get_sampler(sub_matl_sampler) for sub_matl_sampler in samplers]

def get_texture(sub_matl_texture: SUB_PG_matl_texture, texture_overrides: dict[str, str] = None) -> ssbh_data_py.matl_data.TextureParam:
    image_name = sub_matl_texture.image.name
    if texture_overrides is not None:
        image_name = texture_overrides.get(sub_matl_texture.param_id_name, image_name)
    return ssbh_data_py.matl_data.TextureParam(
        param_id=ssbh_data_py.matl_data.ParamId.from_str(sub_matl_texture.param_id_name),
        data=image_name
    )

def get_textures(textures: list[SUB_PG_matl_texture], texture_overrides: dict[str, str] = None) -> list[ssbh_data_py.matl_data.TextureParam]:
    return [get_texture(sub_matl_texture, texture_overrides) for sub_matl_texture in textures]

def get_texture_overrides_for_bundle(material: bpy.types.Material, materials_by_name: dict[str, bpy.types.Material], data_source_by_name: dict[str, bpy.types.Material] = None) -> dict[str, str] | None:
    """Point a Shiny-style bundled material's textures at its base material's.

    Body and BodyShiny share texture files - see source/material_grouping.py -
    but keep their own material parameters (BodyShiny's own metalness etc.).
    Returns {param_id_name: image_name} for the base material's textures, or
    None if this material isn't bundled into another one (or its base isn't
    available in this export), meaning the material's own images are used as-is.

    data_source_by_name lets the base material's own resolved data source
    (see resolve_material_data_source - itself, or its side-loaded twin) supply
    the textures instead of the base material's live data, so a bundled
    material follows whatever its base resolves to. Defaults to the base
    material itself when not given.
    """
    group_key = group_key_for(material.name, set(materials_by_name))
    if group_key == material.name:
        return None
    base_material = materials_by_name.get(group_key)
    if base_material is None:
        return None
    base_data_source = (data_source_by_name or {}).get(group_key, base_material)
    if not has_sub_matl_data(base_data_source):
        return None
    return {
        t.param_id_name: t.image.name
        for t in base_data_source.sub_matl_data.textures
        if t.image is not None
    }

def has_sub_matl_data(material: bpy.types.Material) -> bool:
    try:
        sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    except AttributeError:
        return False
    if sub_matl_data.shader_label == "":
        return False
    return True

def resolve_material_data_source(material: bpy.types.Material, prefer_side_loaded: bool) -> bpy.types.Material:
    """Which material's sub_matl_data should actually be read for `material`.

    Normally just `material` itself. When prefer_side_loaded is on and a
    side-loaded twin exists with real smash data (source/material_grouping.py),
    the twin is used instead - the exported material_label still comes from
    `material`, only the underlying data changes.
    """
    if not prefer_side_loaded:
        return material
    twin = bpy.data.materials.get(side_loaded_name(material.name))
    if twin is not None and has_sub_matl_data(twin):
        return twin
    return material

def get_linked_materials(materials: set[bpy.types.Material]) -> set[bpy.types.Material]:
    linked_materials: set[bpy.types.Material] = set()
    for material in materials:
        if not has_sub_matl_data(material):
            continue
        sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
        for linked_material in sub_matl_data.linked_materials:
            linked_materials.add(linked_material.blender_material)
    return linked_materials


def create_default_matl_entry(material_label: str) -> ssbh_data_py.matl_data.MatlEntryData:
    """
    # TODO: What change stopped this error?
    TypeError: 'MatlEntryData' object cannot be converted to 'MatlEntryData'
    """
    # Mario's phong0_sfx_0x9a011063_____VTC___TANGENT___BINORMAL_101 material.
    # This is a good default for fighters since the user can just assign textures in another application.
    entry = ssbh_data_py.matl_data.MatlEntryData(material_label, 'SFX_PBS_0100000008008269_opaque')
    entry.blend_states = [ssbh_data_py.matl_data.BlendStateParam(
        ssbh_data_py.matl_data.ParamId.BlendState0,
        ssbh_data_py.matl_data.BlendStateData(
            source_color=ssbh_data_py.matl_data.BlendFactor.from_str("One"),
            destination_color=ssbh_data_py.matl_data.BlendFactor.from_str("Zero"),
            alpha_sample_to_coverage=False
        )
    )]
    entry.floats = [ssbh_data_py.matl_data.FloatParam(ssbh_data_py.matl_data.ParamId.CustomFloat0, 0.8)]
    entry.booleans = [
        ssbh_data_py.matl_data.BooleanParam(ssbh_data_py.matl_data.ParamId.CustomBoolean1, True),
        ssbh_data_py.matl_data.BooleanParam(ssbh_data_py.matl_data.ParamId.CustomBoolean3, True),
        ssbh_data_py.matl_data.BooleanParam(ssbh_data_py.matl_data.ParamId.CustomBoolean4, True),
    ]
    entry.vectors = [
        ssbh_data_py.matl_data.Vector4Param(ssbh_data_py.matl_data.ParamId.CustomVector0, [1.0, 0.0, 0.0, 0.0]),
        ssbh_data_py.matl_data.Vector4Param(ssbh_data_py.matl_data.ParamId.CustomVector13, [1.0, 1.0, 1.0, 1.0]),
        ssbh_data_py.matl_data.Vector4Param(ssbh_data_py.matl_data.ParamId.CustomVector14, [1.0, 1.0, 1.0, 1.0]),
        ssbh_data_py.matl_data.Vector4Param(ssbh_data_py.matl_data.ParamId.CustomVector8, [1.0, 1.0, 1.0, 1.0]),
    ]
    data = ssbh_data_py.matl_data.RasterizerStateData()
    data.cull_mode = ssbh_data_py.matl_data.CullMode.from_str('Back')
    data.fill_mode = ssbh_data_py.matl_data.FillMode.from_str('Solid')
    data.depth_bias = 0.0
    entry.rasterizer_states = [ssbh_data_py.matl_data.RasterizerStateParam(
        ssbh_data_py.matl_data.ParamId.RasterizerState0,
        data
    )]
    data = ssbh_data_py.matl_data.SamplerData()
    data.wrapr = ssbh_data_py.matl_data.WrapMode.from_str('Repeat')
    data.wraps = ssbh_data_py.matl_data.WrapMode.from_str('Repeat')
    data.wrapt = ssbh_data_py.matl_data.WrapMode.from_str('Repeat')
    data.min_filter = ssbh_data_py.matl_data.MinFilter.from_str('Nearest')
    data.mag_filter = ssbh_data_py.matl_data.MagFilter.from_str('Nearest')
    data.max_anisotropy = ssbh_data_py.matl_data.MaxAnisotropy.from_str('One')
    entry.samplers = [
        ssbh_data_py.matl_data.SamplerParam(ssbh_data_py.matl_data.ParamId.Sampler0, data),
        ssbh_data_py.matl_data.SamplerParam(ssbh_data_py.matl_data.ParamId.Sampler4, data),
        ssbh_data_py.matl_data.SamplerParam(ssbh_data_py.matl_data.ParamId.Sampler6, data),
        ssbh_data_py.matl_data.SamplerParam(ssbh_data_py.matl_data.ParamId.Sampler7, data),
    ]
    # Use magenta for the albedo/base color to avoid confusion with existing error colors like white, yellow, or red.
    # Magenta is commonly used to indicate missing/invalid textures in applications and game engines.
    entry.textures = [
        ssbh_data_py.matl_data.TextureParam(ssbh_data_py.matl_data.ParamId.Texture0, '/common/shader/sfxpbs/default_params_r100_g025_b100'),
        ssbh_data_py.matl_data.TextureParam(ssbh_data_py.matl_data.ParamId.Texture4, '/common/shader/sfxpbs/fighter/default_normal'),
        ssbh_data_py.matl_data.TextureParam(ssbh_data_py.matl_data.ParamId.Texture6, '/common/shader/sfxpbs/fighter/default_params'),
        ssbh_data_py.matl_data.TextureParam(ssbh_data_py.matl_data.ParamId.Texture7, '#replace_cubemap'),
    ]

    return entry

def create_matl_entry_from_sub_matl_data(material_label: str, sub_matl_data: SUB_PG_sub_matl_data, texture_overrides: dict[str, str] = None) -> ssbh_data_py.matl_data.MatlEntryData:
    return ssbh_data_py.matl_data.MatlEntryData(
        material_label=material_label,
        shader_label=sub_matl_data.shader_label,
        blend_states=get_blend_states(sub_matl_data.blend_states),
        floats=get_floats(sub_matl_data.floats),
        booleans=get_booleans(sub_matl_data.bools),
        vectors=get_vectors(sub_matl_data.vectors),
        rasterizer_states=get_rasterizer_states(sub_matl_data.rasterizer_states),
        samplers=get_samplers(sub_matl_data.samplers),
        textures=get_textures(sub_matl_data.textures, texture_overrides),
    )

def create_matl_from_blender_materials(operator: bpy.types.Operator, blender_materials: set[bpy.types.Material]) -> ssbh_data_py.matl_data.MatlData:
    matl = ssbh_data_py.matl_data.MatlData()

    linked_materials = get_linked_materials(blender_materials)
    all_materials = blender_materials | linked_materials
    materials_by_name = {m.name: m for m in all_materials}

    prefer_side_loaded = bpy.context.scene.sub_scene_properties.export_prefer_sideloaded_materials
    # Resolve each material's effective data source (itself, or its
    # side-loaded twin) up front, so a Shiny-style bundled material follows
    # whatever its base material resolves to, not just the base's live data.
    data_source_by_name = {
        name: resolve_material_data_source(material, prefer_side_loaded)
        for name, material in materials_by_name.items()
    }

    for material in all_materials:
        data_source = data_source_by_name[material.name]
        if has_sub_matl_data(data_source):
            texture_overrides = get_texture_overrides_for_bundle(material, materials_by_name, data_source_by_name)
            new_matl_entry = create_matl_entry_from_sub_matl_data(material.name, data_source.sub_matl_data, texture_overrides)
        else:
            new_matl_entry = create_default_matl_entry(material.name)
        matl.entries.append(new_matl_entry)

    return matl