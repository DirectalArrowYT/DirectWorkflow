"""
World of Light clone materials - light_model.numatb and dark_model.numatb.

Every fighter ships three material files in each model folder: model.numatb for
the normal fighter, plus light_model.numatb and dark_model.numatb for the red
and purple puppet clones used in World of Light. 8,840 of each in the shipped
game. A mod without them shows its clones with missing materials.

The research repo names the files (docs/src/materials/world_of_light/index.md)
but its Light and Dark sections are empty stubs, so the rules below were
derived from the vanilla material dump instead - 27,040 matched material
triples across every fighter.

WHAT THE CLONES ACTUALLY ARE
  Not a tint applied to the normal material. The clone shaders are a separate
  family that ignores colour entirely: SFX_PBS_3a...0029 for light,
  SFX_PBS_3b...0029 for dark. Between them they read seven parameters and, for
  the opaque variant, no textures at all - the puppet colour is generated in
  the shader. The alpha-tested variant additionally takes Texture0, purely for
  its alpha channel, so cutouts still cut out.

  So converting a material is not a matter of copying it and changing a value.
  The entry is rebuilt against the clone shader's own required parameter list,
  which is why the col and prm maps simply disappear from the output.

  Eye materials are the exception and pass through unchanged - they are already
  on shadeless emissive shaders that the clone treatment leaves alone.
"""

import gzip
import json
from pathlib import Path

from ....dependencies import ssbh_data_py
from . import shader_info
from .matl_params import (
    vector_param_id_values, vector_param_id_value_to_default_value,
    float_param_id_values, float_param_id_value_to_default_value,
    bool_param_id_values, bool_param_id_value_to_default_value,
    texture_param_id_values, sampler_param_id_values,
    blend_state_param_id_values, rasterizer_state_param_id_values,
)

LIGHT = 'light'
DARK = 'dark'
VARIANTS = (LIGHT, DARK)

VARIANT_FILE_NAMES = {
    LIGHT: 'light_model.numatb',
    DARK: 'dark_model.numatb',
}

# First two hex digits of the clone shader label. Used only as a fallback when
# a base shader is not in the derived table below.
VARIANT_PREFIX = {
    LIGHT: '3a',
    DARK: '3b',
}

# Values every clone body material carries, measured over the 17,432 vanilla
# light_model materials on the 3a family:
#   CustomVector8    (1,1,1,1)           17432 / 17432
#   CustomVector13   (1,1,1,1)           17432 / 17432
#   CustomFloat8     0.4                 17432 / 17432
#   CustomVector14   (0.75,0.75,0.75,1)  16049 / 17432
# CustomVector0 is deliberately absent - it is the alpha test cutoff and is
# copied from the base material, since the clone has to cut out in the same
# places the fighter does.
CLONE_VECTORS = {
    'CustomVector8': [1.0, 1.0, 1.0, 1.0],
    'CustomVector13': [1.0, 1.0, 1.0, 1.0],
    'CustomVector14': [0.75, 0.75, 0.75, 1.0],
}
CLONE_FLOATS = {
    'CustomFloat8': 0.4,
}
CLONE_BOOLS = {
    'CustomBoolean1': True,
    'CustomBoolean3': True,
    'CustomBoolean4': True,
}

_BASE_LEN = len('SFX_PBS_0100000008008269')
_PREFIX_LEN = len('SFX_PBS_')

_map_cache = None


def _load_map():
    """base shader program -> (light program, dark program).

    Derived by matching every fighter material in model.numatb against the
    entries of the same name in light_model.numatb and dark_model.numatb, then
    taking the majority mapping for each base shader. 163 base shaders, which
    between them account for 89.1% of vanilla fighter materials.
    """
    global _map_cache
    if _map_cache is None:
        path = Path(__file__).parent.joinpath('shader_file', 'wol_shader_map.json.gz').resolve()
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                _map_cache = json.load(f)['map']
        except Exception as e:
            print(f'[sub_matl] Could not read the World of Light shader map: {e}')
            _map_cache = {}
    return _map_cache


def wol_shader_label(shader_label, variant):
    """The clone shader for a base shader, keeping its render pass tag.

    Falls back to rebuilding the label from the clone prefix when the base is
    not in the table, which preserves the transparency bits - those sit in the
    same place in both families - and is correct for the ordinary
    SFX_PBS_01...  materials the table would have covered anyway.
    """
    base = shader_info.base_label(shader_label)
    render_pass = shader_info.render_pass_of(shader_label) or '_opaque'

    # The table is keyed by the bare 16 hex digits, without the SFX_PBS_
    # prefix, so the prefix has to come off before the lookup and back on
    # after it.
    hex_part = base[_PREFIX_LEN:]

    mapped = _load_map().get(hex_part)
    if mapped is not None:
        program = 'SFX_PBS_' + (mapped[0] if variant == LIGHT else mapped[1])
    else:
        program = 'SFX_PBS_' + VARIANT_PREFIX[variant] + hex_part[2:12] + '0029'
        if not shader_info.exists(program):
            # No clone equivalent could be found, so leave the material alone
            # rather than pointing it at a shader that does not exist.
            return shader_label

    return program + render_pass


def _params_by_id(params):
    return {p.param_id.value: p for p in params}


def _clone_entry(operator, entry, variant):
    """One material converted to its clone form."""
    new_shader_label = wol_shader_label(entry.shader_label, variant)

    # Eye materials and anything with no clone equivalent keep their entry
    # exactly as it is - vanilla does the same.
    if new_shader_label == entry.shader_label:
        return ssbh_data_py.matl_data.MatlEntryData(
            material_label=entry.material_label,
            shader_label=entry.shader_label,
            blend_states=list(entry.blend_states),
            floats=list(entry.floats),
            booleans=list(entry.booleans),
            vectors=list(entry.vectors),
            rasterizer_states=list(entry.rasterizer_states),
            samplers=list(entry.samplers),
            textures=list(entry.textures),
        )

    from .load_from_shader_label import get_material_parameter_ids
    required = get_material_parameter_ids(new_shader_label)

    old_vectors = _params_by_id(entry.vectors)
    old_floats = _params_by_id(entry.floats)
    old_bools = _params_by_id(entry.booleans)
    old_textures = _params_by_id(entry.textures)
    old_samplers = _params_by_id(entry.samplers)
    old_blend_states = _params_by_id(entry.blend_states)
    old_rasterizer_states = _params_by_id(entry.rasterizer_states)

    vectors, floats, booleans = [], [], []
    textures, samplers = [], []
    blend_states, rasterizer_states = [], []

    ParamId = ssbh_data_py.matl_data.ParamId
    for param_id_value in sorted(required):
        param_id = ParamId.from_value(param_id_value)
        name = param_id.name

        if param_id_value in vector_param_id_values:
            if name in CLONE_VECTORS:
                data = list(CLONE_VECTORS[name])
            elif param_id_value in old_vectors:
                data = list(old_vectors[param_id_value].data)
            else:
                data = list(vector_param_id_value_to_default_value[param_id_value])
            vectors.append(ssbh_data_py.matl_data.Vector4Param(param_id=param_id, data=data))

        elif param_id_value in float_param_id_values:
            if name in CLONE_FLOATS:
                data = CLONE_FLOATS[name]
            elif param_id_value in old_floats:
                data = old_floats[param_id_value].data
            else:
                data = float_param_id_value_to_default_value[param_id_value]
            floats.append(ssbh_data_py.matl_data.FloatParam(param_id=param_id, data=data))

        elif param_id_value in bool_param_id_values:
            if name in CLONE_BOOLS:
                data = CLONE_BOOLS[name]
            elif param_id_value in old_bools:
                data = old_bools[param_id_value].data
            else:
                data = bool_param_id_value_to_default_value[param_id_value]
            booleans.append(ssbh_data_py.matl_data.BooleanParam(param_id=param_id, data=data))

        elif param_id_value in texture_param_id_values:
            # The alpha-tested clone shader wants Texture0 for its alpha, so
            # the base material's own col map carries straight over. Anything
            # the base did not have falls back to the usual default texture.
            if param_id_value in old_textures:
                data = old_textures[param_id_value].data
            else:
                from ..export_model import default_texture
                data = default_texture(name)
            textures.append(ssbh_data_py.matl_data.TextureParam(param_id=param_id, data=data))

        elif param_id_value in sampler_param_id_values:
            if param_id_value in old_samplers:
                samplers.append(old_samplers[param_id_value])
            else:
                samplers.append(ssbh_data_py.matl_data.SamplerParam(
                    param_id=param_id, data=ssbh_data_py.matl_data.SamplerData()))

        elif param_id_value in blend_state_param_id_values:
            if param_id_value in old_blend_states:
                blend_states.append(old_blend_states[param_id_value])
            else:
                blend_states.append(ssbh_data_py.matl_data.BlendStateParam(
                    param_id=param_id, data=ssbh_data_py.matl_data.BlendStateData()))

        elif param_id_value in rasterizer_state_param_id_values:
            if param_id_value in old_rasterizer_states:
                rasterizer_states.append(old_rasterizer_states[param_id_value])
            else:
                rasterizer_states.append(ssbh_data_py.matl_data.RasterizerStateParam(
                    param_id=param_id, data=ssbh_data_py.matl_data.RasterizerStateData()))

    return ssbh_data_py.matl_data.MatlEntryData(
        material_label=entry.material_label,
        shader_label=new_shader_label,
        blend_states=blend_states,
        floats=floats,
        booleans=booleans,
        vectors=vectors,
        rasterizer_states=rasterizer_states,
        samplers=samplers,
        textures=textures,
    )


def create_wol_matl(operator, matl_data, variant):
    """A whole light_model / dark_model matl built from the normal one.

    Material labels are preserved exactly, because the clone reuses the same
    model.numdlb and so has to match it name for name.
    """
    if variant not in VARIANTS:
        raise ValueError(f'Unknown World of Light variant {variant!r}')

    clone = ssbh_data_py.matl_data.MatlData()
    converted = 0
    unchanged = 0
    for entry in matl_data.entries:
        new_entry = _clone_entry(operator, entry, variant)
        if new_entry.shader_label != entry.shader_label:
            converted += 1
        else:
            unchanged += 1
        clone.entries.append(new_entry)

    return clone, converted, unchanged
