import os

import bpy
from bpy.types import PropertyGroup
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
    IntProperty, PointerProperty, StringProperty,
)

from . import forge


class SUB_PG_smash_fx_listed(PropertyGroup):
    """One effect of the listed .eff file."""
    layers: IntProperty()
    length: IntProperty()
    loops: BoolProperty()


def refresh_list(settings, eff):
    """List the effects of an .eff file into the scene's list, unless it holds them already."""
    path = os.path.normpath(bpy.path.abspath(eff)) if eff else ''
    if path == settings.listed_eff:
        return
    settings.listed.clear()
    settings.listed_eff = path
    settings.list_error = ''
    if not path:
        return
    try:
        for e in forge.list_effects(forge.forge_path(settings), path):
            item = settings.listed.add()
            item.name = e['name']
            item.layers = e['layers']
            item.length = int(round(e['length']))
            item.loops = e['loops']
    except Exception as e:
        settings.list_error = str(e)


def _eff_changed(self, context):
    refresh_list(context.scene.sub_smash_fx, self.eff)


class SUB_PG_smash_fx(PropertyGroup):
    """An empty playing a Smash effect: where the empty is, the effect is, its size included."""
    enabled: BoolProperty(default=False)
    play: BoolProperty(name='Play', default=True, description='Draw this effect when baking')
    eff: StringProperty(name='File', subtype='FILE_PATH', update=_eff_changed,
                        description='The .eff file the effect is in')
    effect: StringProperty(name='Effect', description="The effect's name in its file")
    start: IntProperty(name='Start', default=1, description='The frame the effect starts on')
    use_stop: BoolProperty(name='Stop', default=False,
                           description="Stop making particles on a frame; the ones already out live on (the game's EFFECT_OFF)")
    stop: IntProperty(name='Stop Frame', default=30)
    use_end: BoolProperty(name='End', default=False,
                          description='Remove the effect, particles and all, on a frame')
    end: IntProperty(name='End Frame', default=60)
    rate: FloatProperty(name='Rate', default=1.0, min=0.01, soft_max=4.0,
                        description="How fast the effect plays: 1 is the game's speed, whatever the scene's frame rate")
    colour: FloatVectorProperty(name='Colour', subtype='COLOR', size=4, default=(1, 1, 1, 1), min=0.0, soft_max=1.0,
                                description="Tint and alpha, as a script's LAST_EFFECT_SET_COLOR / _ALPHA")
    seed: IntProperty(name='Seed', min=0, description='Another seed gives the random parts another roll')


def _bloom_changed(context):
    from . import layering
    layering.tune_bloom(context.scene)


def _lights_changed(self, context):
    """The effect lights' drivers read Light Strength and Size; the scene is tagged so they
    run again now, not at the next frame change."""
    from . import lights
    lights.set_shadows(context.scene)
    context.scene.update_tag()


_SOURCE_ITEMS = []


def _sources(self, context):
    # Blender needs the list kept alive on the Python side.
    _SOURCE_ITEMS[:] = [
        ('common', 'Common', 'effect/system/common: hits, flashes, smoke, fire...'),
        ('item', 'Item', 'effect/system/item'),
        ('CUSTOM', 'Custom File', 'Any .eff file, a mod\'s included'),
    ] + [(name, name, f'effect/fighter/{name}') for name in forge.fighters(self.game_root)]
    return _SOURCE_ITEMS


_SCRIPT_ITEMS = []


def _acmd_scripts(self, context):
    from . import acmd
    try:
        text = open(bpy.path.abspath(self.acmd_file), encoding='utf-8', errors='replace').read()
        names = acmd.scripts(text)
    except OSError:
        names = []
    _SCRIPT_ITEMS[:] = [(n, n, '') for n in names] or [('NONE', 'No effect scripts', '')]
    return _SCRIPT_ITEMS


class SUB_PG_smash_fx_settings(PropertyGroup):
    forge: StringProperty(name='EffectForge', subtype='FILE_PATH', default=forge.DEFAULT_FORGE,
                          description='The effect_forge program that draws the effects')
    game_root: StringProperty(name='Game Files', subtype='DIR_PATH', default=forge.DEFAULT_GAME_ROOT,
                              description='The extracted data.arc (the folder holding effect/)')
    source: EnumProperty(name='Source', items=_sources,
                         description='Where new effects come from')
    custom_eff: StringProperty(name='File', subtype='FILE_PATH', description='A custom .eff file')
    out: StringProperty(name='Output', subtype='DIR_PATH', default='//smash_fx/',
                        description='Where the baked effect frames go')
    use_shot: BoolProperty(name='Against the Shot', default=False,
                           description="Draw the effects against a render of the shot (Render Shot Depth): hidden "
                                       "behind what is in front of them, soft where they meet it, heat haze bending it")
    show_in_camera: BoolProperty(name='Show in Camera', default=True,
                                 description="Lay the baked effects over the camera view in the viewport")
    composite: BoolProperty(name='Composite', default=True,
                            description="Lay the baked effects over the render in the compositor")
    lights: BoolProperty(name='Light the Shot', default=True,
                         description="Key a light per effect from what it draws when baking, so effects light "
                                     "what is around them in EEVEE and Cycles (bounce and reflections in Cycles)")
    light_strength: FloatProperty(name='Light Strength', default=1.0, min=0.0, soft_max=10.0,
                                  description='How bright the effect lights are; 1 is what the effects give off',
                                  update=_lights_changed)
    light_size: FloatProperty(name='Light Size', default=1.0, min=0.0, soft_max=4.0,
                              description='How big the effect lights are against how far their glow spreads: '
                                          'bigger is softer light and shadows',
                              update=_lights_changed)
    cards: BoolProperty(name='In Reflections', default=True,
                        description="Stand a card per effect in the shot when baking, so effects are seen in "
                                    "reflections and through glass (Cycles; EEVEE through light probes)")
    light_shadows: BoolProperty(name='Shadows', default=True,
                                description='Effect lights cast shadows',
                                update=_lights_changed)
    bloom: BoolProperty(name='Bloom', default=True,
                        description='Let bright effects glow out into the frame in the compositor',
                        update=lambda self, context: _bloom_changed(context))
    bloom_strength: FloatProperty(name='Bloom Strength', default=0.6, min=0.0, soft_max=2.0,
                                  update=lambda self, context: _bloom_changed(context))
    bloom_size: FloatProperty(name='Bloom Size', default=0.6, min=0.0, max=1.0,
                              update=lambda self, context: _bloom_changed(context))

    acmd_file: StringProperty(name='Script', subtype='FILE_PATH',
                              description="A move's effect script: smashline Rust or a dumped ACMD script")
    acmd_script: EnumProperty(name='Move', items=_acmd_scripts, description='The effect script to place')

    listed: CollectionProperty(type=SUB_PG_smash_fx_listed)
    listed_eff: StringProperty()
    list_error: StringProperty()


classes = (
    SUB_PG_smash_fx_listed,
    SUB_PG_smash_fx,
    SUB_PG_smash_fx_settings,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.smash_fx = PointerProperty(type=SUB_PG_smash_fx)
    bpy.types.Scene.sub_smash_fx = PointerProperty(type=SUB_PG_smash_fx_settings)


def unregister():
    del bpy.types.Scene.sub_smash_fx
    del bpy.types.Object.smash_fx
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
