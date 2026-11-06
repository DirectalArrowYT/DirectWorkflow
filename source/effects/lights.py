"""
Lighting the shot with its effects: a light per effect, keyed from what EffectForge read off
each baked frame (`lights.json`), so effects light what is around them in EEVEE and Cycles,
with bounce, reflections and soft shadows in Cycles.

EffectForge reads each effect's light from what it draws, so everything the effect does
(colours and alpha over time, textures, size, blend, a script's tint) is in it: dark smoke
gives off little, bright fire much. Each light sits in the middle of its effect's glow and is
as big as the glow is spread. Its power and size are keyed as raw values on the light and
scaled by the scene's Light Strength and Light Size through drivers, so those can be tuned
without baking again.
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


def clear(scene):
    """Remove the lights a bake made."""
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        return
    for obj in list(coll.objects):
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data)


def set_shadows(scene):
    coll = bpy.data.collections.get(COLLECTION)
    for obj in coll.objects if coll else ():
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


def apply(scene, out, names):
    """Key a light per effect from `out/lights.json`; `names` are the effect objects in the
    order the scene file listed them. Returns how many lights were made."""
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
        keys = effect['frames']
        if not any(keys):
            continue
        light = bpy.data.lights.new(f'{name} Light', 'POINT')
        light.use_shadow = settings.light_shadows
        obj = bpy.data.objects.new(f'{name} Light', light)
        coll.objects.link(obj)
        obj['smash_fx_effect'] = name
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
        made += 1
    return made
