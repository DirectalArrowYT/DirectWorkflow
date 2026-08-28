"""
Ready-made Smash material setups you can drop onto a character.

Picking a shader label by hand means typing something like
SFX_PBS_010000000800826b_opaque into a text field and knowing, from nothing,
that the `6b` on the end is what makes it a skin shader. A preset is that
choice already made, together with the parameter values vanilla ships with it.

Applying a preset deliberately does NOT clear the material's textures. It
routes through create_sub_matl_data_from_shader_label(), which keeps every
param the new shader still wants and only drops the ones it doesn't - so
switching Body from Opaque to Alpha Blend keeps your col/nor/prm images and
just changes how they're rendered.

WHERE THESE VALUES COME FROM
  The eleven presets below are ported from SSBH Editor's src/presets.rs, so a
  material set up here matches one set up there. Each was then checked against
  the vanilla material dump (Smush-Material-Research materials_v13.0.1.db) to
  confirm the shader label and values are what the shipped game actually uses
  - e.g. Skin's CustomVector11 (0.25, 0.0333, 0.0, 1.0) and CustomVector30
  (0.5, 1.5, 1.0, 1.0) on SFX_PBS_010000000800826b_opaque are byte-for-byte
  what fighter/mario/model/body/c00 skin_mario_001 carries.

  The `fighter_usage` note on each preset is how many vanilla fighter
  materials use that shader program, counted from the same dump.
"""

from . import shader_info


class MaterialPreset:
    """One named material setup: a shader label plus the values to write."""

    def __init__(self, name, shader_label, description='', category='Character',
                 vectors=None, floats=None, bools=None,
                 blend=None, cull_mode='Back', notes=''):
        self.name = name
        self.shader_label = shader_label
        self.description = description
        self.category = category
        self.vectors = vectors or {}
        self.floats = floats or {}
        self.bools = bools or {}
        # (source_color, destination_color, alpha_sample_to_coverage) or None
        # to leave the blend state at the shader's default.
        self.blend = blend
        self.cull_mode = cull_mode
        self.notes = notes

    @property
    def fighter_usage(self):
        return shader_info.fighter_usage_count(self.shader_label)

    @property
    def textures(self):
        """Texture slots this preset's shader actually reads."""
        return shader_info.used_textures(self.shader_label)

    def summary(self):
        return shader_info.describe(self.shader_label)


# Blend state shorthands.
_OPAQUE = ('One', 'Zero', False)
_ALPHA_BLEND = ('One', 'OneMinusSourceAlpha', False)
_ALPHA_TO_COVERAGE = ('One', 'Zero', True)

# Values every lit fighter material carries. CustomVector8 is the final colour
# multiplier and CustomVector13 the diffuse multiplier - both identity at
# (1,1,1,1) - and CustomVector0.x is the alpha test cutoff.
_COMMON_LIT = {
    'CustomVector0': (1.0, 0.0, 0.0, 0.0),
    'CustomVector8': (1.0, 1.0, 1.0, 1.0),
    'CustomVector13': (1.0, 1.0, 1.0, 1.0),
}

_SPECULAR_BOOLS = {
    'CustomBoolean1': True,   # PRM alpha is the specular value, not a flat 0.16
    'CustomBoolean3': True,   # specular light contribution on
    'CustomBoolean4': True,   # specular cube map contribution on
}


PRESETS = [
    MaterialPreset(
        name='Standard (Opaque)',
        shader_label='SFX_PBS_0100000008008269_opaque',
        category='Character',
        description='The default fighter material. Col, Nor and PRM, fully lit and opaque.',
        vectors=dict(_COMMON_LIT, CustomVector14=(1.0, 1.0, 1.0, 1.0)),
        floats={'CustomFloat8': 0.4},
        bools=dict(_SPECULAR_BOOLS),
        blend=_OPAQUE,
        notes='Use for body, clothing, armour and metal - anything solid.',
    ),
    MaterialPreset(
        name='Skin (Subsurface)',
        shader_label='SFX_PBS_010000000800826b_opaque',
        category='Character',
        description='Skin, with the fake subsurface scattering vanilla uses on faces and limbs.',
        vectors=dict(
            _COMMON_LIT,
            CustomVector11=(0.25, 0.03333333, 0.0, 1.0),
            CustomVector14=(1.0, 1.0, 1.0, 1.0),
            CustomVector30=(0.5, 1.5, 1.0, 1.0),
        ),
        floats={'CustomFloat8': 0.7},
        bools=dict(_SPECULAR_BOOLS),
        blend=_OPAQUE,
        notes=(
            'CustomVector11 RGB is the subsurface colour and CustomVector30.x its '
            'blend factor, .y the shading sharpness. On this shader the PRM red '
            'channel is the SSS mask, NOT metalness - so bake it from Subsurface '
            'Weight rather than Metallic.'
        ),
    ),
    MaterialPreset(
        name='Skin (UV Switchable)',
        shader_label='SFX_PBS_010000080800826b_opaque',
        category='Character',
        description='Subsurface skin whose UVs can be scrolled, for switching between stacked textures.',
        vectors=dict(
            _COMMON_LIT,
            CustomVector6=(1.0, 1.0, 0.0, 0.0),
            CustomVector11=(0.17, 0.0578, 0.018, 1.0),
            CustomVector14=(0.75, 0.75, 0.75, 1.0),
            CustomVector30=(0.6, 1.0, 1.0, 1.0),
        ),
        floats={'CustomFloat8': 0.4},
        # Vanilla has CustomBoolean1 off on all 8 of these, so PRM alpha is
        # ignored and specular is a flat 0.16.
        bools={'CustomBoolean1': False, 'CustomBoolean3': True, 'CustomBoolean4': True},
        blend=_OPAQUE,
        notes=(
            'The plain Skin preset uses SFX_PBS_010000000800826b, which does NOT '
            'read CustomVector6 - so its UVs cannot be scrolled and stacked '
            'textures cannot be switched. This one does. Vanilla uses it for '
            "Kazuya's skin_demon_001. Bake two materials, run Bake Textures > "
            'Stack Two Materials, then keyframe CustomVector6.w: 0 for the top '
            'texture, -1 for the bottom.'
        ),
    ),
    MaterialPreset(
        name='Skin + Second Col Layer',
        shader_label='SFX_PBS_010000000800824f_opaque',
        category='Character',
        description='Subsurface skin with a second Col map layered over it, for decals like scars or burns.',
        vectors=dict(
            _COMMON_LIT,
            CustomVector11=(0.25, 0.02, 0.01, 1.0),
            CustomVector30=(0.5, 1.5, 1.0, 1.0),
            CustomVector31=(1.0, 1.0, 0.0, 0.0),
        ),
        bools={
            'CustomBoolean1': True,
            'CustomBoolean3': True,
            'CustomBoolean4': True,
            # False = alpha blend the second layer over the first. All 866
            # vanilla materials on this shader use alpha blending; nothing
            # ships with additive.
            'CustomBoolean11': False,
        },
        blend=_OPAQUE,
        notes=(
            'Texture1 is the second Col layer and samples the uvSet UV map, NOT '
            'map1 - the mesh needs both. The blend is driven per-texel by '
            "Texture1's own ALPHA: result = Col0 + Tex1.a * (Tex1.rgb - Col0.rgb). "
            'So the decal shape lives in the second texture\'s alpha channel. '
            'There is no scalar that fades the whole layer in and out; '
            'CustomVector13 multiplies both layers together. To move the layer '
            'at runtime, animate CustomVector31 (.xy scale, .zw offset), which '
            'the vertex shader applies to the uvSet coordinates. Vanilla uses '
            'this shader for eyes - layer 1 the eye white, layer 2 the iris.'
        ),
    ),
    MaterialPreset(
        name='Alpha Blend',
        shader_label='SFX_PBS_0100000008018269_sort',
        category='Character',
        description='Smoothly blended transparency. Renders in the sort pass, after opaque geometry.',
        vectors=dict(_COMMON_LIT, CustomVector0=(0.0, 0.0, 0.0, 0.0),
                     CustomVector14=(1.0, 1.0, 1.0, 1.0)),
        floats={'CustomFloat8': 0.4},
        bools=dict(_SPECULAR_BOOLS, CustomBoolean2=False),
        blend=_ALPHA_BLEND,
        notes=(
            'Blended surfaces sort per mesh, not per pixel, so overlapping '
            'transparent parts can still draw in the wrong order. Alpha Test is '
            'the safer choice for dense hair cards.'
        ),
    ),
    MaterialPreset(
        name='Alpha Test',
        # SSBH Editor's equivalent preset uses SFX_PBS_01000000080c8269, which
        # also reads colorSet1 - so a mesh without a vertex colour layer gets a
        # validation warning for no reason. This one needs only map1 and is the
        # variant vanilla actually reaches for (1562 fighter materials vs 192).
        shader_label='SFX_PBS_0100000008048269_opaque',
        category='Character',
        description='Hard cutout transparency. Every pixel is fully opaque or fully gone.',
        vectors=dict(_COMMON_LIT, CustomVector0=(0.0, 0.0, 0.0, 0.0),
                     CustomVector14=(1.0, 1.0, 1.0, 1.0)),
        floats={'CustomFloat8': 0.4},
        bools=dict(_SPECULAR_BOOLS),
        blend=_OPAQUE,
        notes=(
            'CustomVector0.x is the alpha cutoff. No sorting problems, which is '
            'why vanilla uses this for hair and foliage.'
        ),
    ),
    MaterialPreset(
        name='Hair (Anisotropic)',
        shader_label='SFX_PBS_0100080008048269_opaque',
        category='Character',
        description='Alpha-tested hair with the stretched, directional specular highlight.',
        vectors=dict(_COMMON_LIT, CustomVector0=(0.0, 0.0, 0.0, 0.0),
                     CustomVector14=(0.75, 0.75, 0.75, 1.0)),
        floats={'CustomFloat10': 0.6, 'CustomFloat8': 0.7},
        bools=dict(_SPECULAR_BOOLS),
        blend=_ALPHA_TO_COVERAGE,
        notes=(
            'CustomFloat10 is the anisotropy strength. While it is non-zero the '
            'PRM alpha channel stops being specular and becomes the highlight '
            'ROTATION, mapped 0.0-1.0 onto 0-180 degrees.'
        ),
    ),
    MaterialPreset(
        name='Emissive',
        # SSBH Editor's equivalent uses SFX_PBS_010000080a008269 (56 fighter
        # materials). This is the one vanilla overwhelmingly picks - 1726
        # materials, including link's def_link_001 - and it reads a smaller
        # parameter set, with no CustomVector6/29 or CustomBoolean5 to set.
        shader_label='SFX_PBS_010000000a008269_opaque',
        category='Character',
        description='Lit material that also glows. Col, Nor, PRM and an Emi map.',
        vectors=dict(
            _COMMON_LIT,
            CustomVector3=(1.0, 1.0, 1.0, 1.0),
            CustomVector14=(0.75, 0.75, 0.75, 1.0),
        ),
        floats={'CustomFloat8': 0.4},
        bools=dict(_SPECULAR_BOOLS),
        blend=_OPAQUE,
        notes=(
            'CustomVector3 multiplies the Emi map. Push it above 1.0 to drive '
            'bloom - that is how vanilla makes lights read as bright.'
        ),
    ),
    MaterialPreset(
        name='Emissive (Shadeless)',
        # The colorSet1-free variant of SSBH Editor's SFX_PBS_0000000000080100,
        # so this applies cleanly to a mesh with no vertex colour layer.
        shader_label='SFX_PBS_0000000000000100_opaque',
        category='Character',
        description='Pure glow with no lighting at all. The Emi map is the whole result.',
        vectors={
            'CustomVector3': (1.0, 1.0, 1.0, 1.0),
            'CustomVector8': (1.0, 1.0, 1.0, 1.0),
        },
        blend=_OPAQUE,
        notes='For screens, energy and UI-like surfaces that should ignore stage lighting.',
    ),
    MaterialPreset(
        name='Emissive + Nor',
        # SSBH Editor calls this one "Emi Nor Shadeless", but the shader info
        # dump reports lighting=True for this program, so it is not shadeless.
        shader_label='SFX_PBS_2f00000002014248_opaque',
        category='Character',
        description='Glow driven by an Emi map, with a normal map and no Col map.',
        vectors={
            'CustomVector0': (0.0, 0.0, 0.0, 0.0),
            'CustomVector3': (1.0, 1.0, 1.0, 1.0),
            'CustomVector8': (1.0, 1.0, 1.0, 1.0),
            'CustomVector13': (1.0, 1.0, 1.0, 1.0),
        },
        bools={
            'CustomBoolean1': True, 'CustomBoolean2': True,
            'CustomBoolean3': True, 'CustomBoolean4': True,
        },
        blend=_OPAQUE,
    ),
    MaterialPreset(
        name='Glass (Angle Fade)',
        shader_label='SFX_PBS_0100000008018279_sort',
        category='Character',
        description='Transparent surface that gets more reflective at glancing angles.',
        vectors=dict(_COMMON_LIT, CustomVector0=(0.0, 0.0, 0.0, 0.0),
                     CustomVector14=(4.618421, 4.618421, 4.618421, 1.0)),
        floats={'CustomFloat19': 1.2, 'CustomFloat8': 1.5},
        bools=dict(_SPECULAR_BOOLS, CustomBoolean2=True),
        blend=_ALPHA_BLEND,
        notes='CustomFloat19 controls how hard the edge-on reflection ramps up.',
    ),
    MaterialPreset(
        name='Mesh-wide PRM (No PRM Map)',
        shader_label='SFX_PBS_010000000808ba68_opaque',
        category='Character',
        description='One set of PRM values for the whole mesh, from CustomVector47 instead of a texture.',
        vectors=dict(
            _COMMON_LIT,
            CustomVector0=(0.0, 0.0, 0.0, 0.0),
            CustomVector14=(1.0, 1.0, 1.0, 1.0),
            CustomVector47=(0.0, 0.5, 1.0, 0.16),
        ),
        bools=dict(_SPECULAR_BOOLS),
        blend=_OPAQUE,
        notes=(
            'CustomVector47 is RGBA = metalness, roughness, AO, specular. Saves '
            'authoring a PRM map for a surface that is uniform anyway.'
        ),
    ),
    MaterialPreset(
        name='Diffuse Cube Map',
        shader_label='SFX_PBS_0d00000000000000_opaque',
        category='Character',
        description='Shaded purely from a diffuse cube map. Used for a few special-case surfaces.',
        vectors={'CustomVector8': (1.0, 1.0, 1.0, 1.0)},
        blend=_OPAQUE,
    ),
]


PRESETS_BY_NAME = {preset.name: preset for preset in PRESETS}


def get(name):
    return PRESETS_BY_NAME.get(name)


def enum_items():
    """Preset list formatted for a Blender EnumProperty."""
    items = []
    for preset in PRESETS:
        uses = preset.fighter_usage
        tooltip = preset.description
        if uses:
            tooltip += f'  ({uses} vanilla fighter materials use this shader)'
        items.append((preset.name, preset.name, tooltip))
    return items
