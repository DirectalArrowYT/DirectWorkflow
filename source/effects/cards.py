"""
Effects standing in the shot: a card per effect, showing what EffectForge cut out of its frames, so effects are seen
in reflections (windows, water, chrome) and through glass, which the effects laid over the render cannot be.

Each frame's card stands where its picture was cut from: square to the camera at the effect's distance, so from the
camera it covers just what the laid-over effect does. The camera does not see the cards (the laid-over effects are
sharper, and soft against what they meet); reflections and refraction do. They light nothing: the effect lights do that,
and in Cycles those stay out of reflections when there are cards, so a reflection shows the effect, not a bright ball.
In EEVEE the cards show in planar and sphere light probes (screen-space reflections only see what the camera does).
"""

import json
import os

import bpy
from mathutils import Matrix, Vector

from .lights import FROM_SMASH
from .layering import _place

COLLECTION = 'Smash FX Cards'
MESH = 'Smash FX Card'


def _collection(scene):
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def clear(scene):
    """Remove the cards a bake made."""
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        return
    for obj in list(coll.objects):
        material = obj.material_slots[0].material if obj.material_slots else None
        bpy.data.objects.remove(obj)
        if material is not None and material.users == 0:
            image = next((n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'), None)
            bpy.data.materials.remove(material)
            if image is not None and image.users == 0:
                bpy.data.images.remove(image)


def _mesh():
    """A plane two across, its picture the right way up."""
    mesh = bpy.data.meshes.get(MESH)
    if mesh is None:
        mesh = bpy.data.meshes.new(MESH)
        mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new(name='UVMap')
        for loop, at in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop.uv = at
        mesh.materials.append(None)
    return mesh


def _material(name, image, frames):
    """Premultiplied like the laid-over effects: their colour is given off, and what they cover is taken out of what
    lies behind."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    texture = tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    texture.interpolation = 'Linear'
    _place(texture.image_user, frames)
    texture.image_user.use_auto_refresh = True
    emission = tree.nodes.new('ShaderNodeEmission')
    emission.inputs['Strength'].default_value = 1.0
    behind = tree.nodes.new('ShaderNodeMath')
    behind.operation = 'SUBTRACT'
    behind.inputs[0].default_value = 1.0
    clear_bsdf = tree.nodes.new('ShaderNodeBsdfTransparent')
    add = tree.nodes.new('ShaderNodeAddShader')
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    for i, node in enumerate((texture, behind, emission, clear_bsdf, add, output)):
        node.location = (i * 200 - 600, 0)
    tree.links.new(texture.outputs['Color'], emission.inputs['Color'])
    tree.links.new(texture.outputs['Alpha'], behind.inputs[1])
    tree.links.new(behind.outputs[0], clear_bsdf.inputs['Color'])
    tree.links.new(emission.outputs[0], add.inputs[0])
    tree.links.new(clear_bsdf.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs['Surface'])
    if hasattr(material, 'surface_render_method'):
        material.surface_render_method = 'BLENDED'
    else:
        material.blend_method = 'BLEND'
    material.use_backface_culling = False
    # The effect lights light the shot; the cards would light it twice.
    if hasattr(material, 'cycles') and hasattr(material.cycles, 'emission_sampling'):
        material.cycles.emission_sampling = 'NONE'
    return material


def _hide_from_camera(obj):
    obj.visible_camera = False
    obj.visible_diffuse = False
    obj.visible_shadow = False
    obj.visible_volume_scatter = False
    obj.visible_glossy = True
    obj.visible_transmission = True


def _constant(obj):
    action = obj.animation_data.action if obj.animation_data else None
    if action is None:
        return
    curves = getattr(action, 'fcurves', None)
    if curves is None or not len(curves):
        curves = [fc for layer in getattr(action, 'layers', []) for strip in layer.strips
                  for bag in strip.channelbags for fc in bag.fcurves]
    for fc in curves:
        for key in fc.keyframe_points:
            key.interpolation = 'CONSTANT'


def apply(scene, out, names):
    """Key a card per effect from the cards in `out/lights.json`; `names` are the effect objects in the order the
    scene file listed them. Returns how many cards were made."""
    path = os.path.join(out, 'lights.json')
    if not os.path.isfile(path):
        return 0
    with open(path) as f:
        data = json.load(f)
    frames = data['frames']
    clear(scene)
    coll = _collection(scene)
    mesh = _mesh()
    made = 0
    for name, effect in zip(names, data['effects']):
        keys = effect.get('cards') or []
        first = next((k for k in keys if k), None)
        if first is None:
            continue
        image = bpy.data.images.load(os.path.join(out, first['image']))
        image.name = f'{name} Card'
        image.source = 'SEQUENCE'
        image.alpha_mode = 'CHANNEL_PACKED'
        obj = bpy.data.objects.new(f'{name} Card', mesh)
        coll.objects.link(obj)
        obj['smash_fx_effect'] = name
        # The cards share a plane; each has its own picture on the object.
        obj.material_slots[0].link = 'OBJECT'
        obj.material_slots[0].material = _material(f'{name} Card', image, frames)
        _hide_from_camera(obj)
        obj.rotation_mode = 'QUATERNION'
        for frame, key in zip(frames, keys):
            if key is None:
                # Nothing to show: no card.
                obj.scale = (0, 0, 0)
            else:
                right, up = Vector(key['right']), Vector(key['up'])
                normal = right.cross(up).normalized()
                m = Matrix.Identity(4)
                for row in range(3):
                    m[row][0], m[row][1], m[row][2], m[row][3] = right[row], up[row], normal[row], key['centre'][row]
                obj.matrix_world = FROM_SMASH @ m
            obj.keyframe_insert('location', frame=frame)
            obj.keyframe_insert('rotation_quaternion', frame=frame)
            obj.keyframe_insert('scale', frame=frame)
        _constant(obj)
        made += 1
    return made
