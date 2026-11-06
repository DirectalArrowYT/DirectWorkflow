"""
Lighting the shot with its effects: a light per layer of each effect, keyed from what
EffectForge read off each baked frame (`lights.json`), so effects light what is around them in
EEVEE and Cycles, with bounce, reflections and soft shadows in Cycles. An effect of many
colours (a blue core in orange flames) lights the shot in each of them, from where each is.

EffectForge reads each layer's light from what it draws, so everything the layer does
(colours and alpha over time, textures, size, blend, a script's tint) is in it: dark smoke
gives off little, bright fire much. Each light sits in the middle of its layer's glow and is
as big as the glow is spread. An effect's lights are grouped in a collection of its own. Its power and size are keyed as raw values on the light and
scaled by the scene's Light Strength and Light Size through drivers, so those can be tuned
without baking again. With cards (cards.py), the lights stay out of Cycles' reflections, where
the cards show the effects themselves.
"""

import json
import os

import bpy
from mathutils import Vector

from .forge import TO_SMASH

COLLECTION = 'Smash FX Lights'
FROM_SMASH = TO_SMASH.inverted()
# Raw values keyed on each light's data.
POWER = 'smash_fx_power'
RADIUS = 'smash_fx_radius'
# The smallest light, so a pinpoint glow still casts a soft shadow.
MIN_RADIUS = 0.05


def _collection(scene):
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def _group(coll, name):
    """A collection of its own inside `coll`, for one effect's objects."""
    group = bpy.data.collections.new(name)
    coll.children.link(group)
    return group


def clear(scene):
    """Remove the lights a bake made."""
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        return
    for obj in list(coll.all_objects):
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data)
    for group in list(coll.children_recursive):
        bpy.data.collections.remove(group)


def set_shadows(scene):
    coll = bpy.data.collections.get(COLLECTION)
    for obj in coll.all_objects if coll else ():
        if obj.type == 'LIGHT':
            obj.data.use_shadow = scene.sub_smash_fx.light_shadows


def _driver(data, path, prop, setting):
    """`path` on the light = its keyed `prop` times the scene's `setting`."""
    data.driver_remove(path)
    driver = data.driver_add(path).driver
    driver.type = 'SCRIPTED'
    raw = driver.variables.new()
    raw.name = 'raw'
    raw.targets[0].id_type = 'LIGHT'
    raw.targets[0].id = data
    raw.targets[0].data_path = f'["{prop}"]'
    scale = driver.variables.new()
    scale.name = 'scale'
    scale.targets[0].id_type = 'SCENE'
    scale.targets[0].id = bpy.context.scene
    scale.targets[0].data_path = f'sub_smash_fx.{setting}'
    driver.expression = f'max(raw * scale, {MIN_RADIUS})' if prop == RADIUS else 'raw * scale'


def _linear(id_data):
    action = id_data.animation_data.action if id_data.animation_data else None
    if action is None:
        return
    # Slotted actions (Blender 4.4+) keep their curves in channelbags; older ones on the action.
    curves = getattr(action, 'fcurves', None)
    if curves is None or not len(curves):
        curves = [fc for layer in getattr(action, 'layers', []) for strip in layer.strips
                  for bag in strip.channelbags for fc in bag.fcurves]
    for fc in curves:
        for key in fc.keyframe_points:
            key.interpolation = 'LINEAR'


def _layers(effect):
    """An effect's layers in `lights.json` as (name, keys); a bake from before lights per layer
    has one for the whole effect."""
    if 'layers' in effect:
        return [(layer['name'], layer['frames']) for layer in effect['layers']]
    return [(None, effect['frames'])]


def apply(scene, out, names, geometry=False):
    """Key a light per layer of each effect from `out/lights.json`; `names` are the effect
    objects in the order the scene file listed them. Returns how many lights were made."""
    path = os.path.join(out, 'lights.json')
    if not os.path.isfile(path):
        return 0
    with open(path) as f:
        data = json.load(f)
    frames = data['frames']
    settings = scene.sub_smash_fx
    clear(scene)
    coll = _collection(scene)
    made = 0
    for name, effect in zip(names, data['effects']):
        layers = [(layer, keys) for layer, keys in _layers(effect) if any(keys)]
        if not layers:
            continue
        group = _group(coll, f'{name} Lights')
        for layer, keys in layers:
            _light(group, name, layer, frames, keys, settings, geometry)
            made += 1
    return made


def _light(group, name, layer, frames, keys, settings, geometry):
    """Key one layer's light into `group`."""
    title = f'{name} {layer} Light' if layer else f'{name} Light'
    light = bpy.data.lights.new(title, 'POINT')
    light.use_shadow = settings.light_shadows
    obj = bpy.data.objects.new(title, light)
    # With cards or meshes, reflections show the effect itself (Cycles).
    obj.visible_glossy = not (settings.cards or geometry)
    group.objects.link(obj)
    obj['smash_fx_effect'] = name
    if layer:
        obj['smash_fx_layer'] = layer
    light[POWER] = 0.0
    light[RADIUS] = 0.0
    last = None
    for frame, key in zip(frames, keys):
        if key is None:
            # Dark, where it was last.
            light[POWER] = 0.0
            light.keyframe_insert(f'["{POWER}"]', frame=frame)
            if last is None:
                continue
            key = last
        else:
            light[POWER] = key['power']
            light.keyframe_insert(f'["{POWER}"]', frame=frame)
            last = key
        obj.location = FROM_SMASH @ Vector(key['position'])
        obj.keyframe_insert('location', frame=frame)
        light.color = key['colour']
        light.keyframe_insert('color', frame=frame)
        light[RADIUS] = key['radius']
        light.keyframe_insert(f'["{RADIUS}"]', frame=frame)
    _linear(obj)
    _linear(light)
    _driver(light, 'energy', POWER, 'light_strength')
    _driver(light, 'shadow_soft_size', RADIUS, 'light_size')
