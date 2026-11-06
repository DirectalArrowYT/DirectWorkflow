"""
Effects as meshes standing in the shot, for Cycles to treat like anything else: they light what is around them by what
they give off, show in reflections and through glass, and take depth of field.

EffectForge writes, per effect and frame, every triangle the effect's layers drew (`geo/geo_<effect>_<frame>.bin`, in
Smash space) and an atlas of what each paints (`geo/atlas_<effect>_<frame>.exr`): on the left what it adds to what lies
behind it, on the right at the same place how much of that shows through. Each effect becomes one mesh holding every
frame's triangles, each face knowing its frame; geometry nodes keep the scene frame's faces only. Its material gives off
the left half and lets the right half through, so additive fire glows, smoke covers and tints, and both stack as deep
as Cycles' transparent bounces let them.
"""

import glob
import os
import struct

import bpy
import numpy as np

from .layering import _place
from .lights import FROM_SMASH

COLLECTION = 'Smash FX Geometry'
NODES = 'Smash FX Frame'
FRAME = 'fx_frame'
# Overlapping particles each take a transparent bounce in Cycles.
TRANSPARENT_BOUNCES = 128


def _collection(scene):
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def clear(scene):
    """Remove the meshes a bake made."""
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        return
    for obj in list(coll.objects):
        mesh = obj.data
        material = mesh.materials[0] if mesh.materials else None
        bpy.data.objects.remove(obj)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        if material is not None and material.users == 0:
            image = next((n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'), None)
            bpy.data.materials.remove(material)
            if image is not None and image.users == 0:
                bpy.data.images.remove(image)


def _read(path):
    """A frame's triangles: positions (n*3, 3) in Smash space and atlas coordinates (n*3, 2)."""
    with open(path, 'rb') as f:
        data = f.read()
    (count,) = struct.unpack_from('<I', data)
    corners = count * 3
    positions = np.frombuffer(data, dtype='<f4', count=corners * 3, offset=4).reshape(-1, 3)
    uvs = np.frombuffer(data, dtype='<f4', count=corners * 2, offset=4 + corners * 12).reshape(-1, 2)
    return positions, uvs


def _mesh(name, frames_of):
    """One mesh of every frame's triangles, unwelded, each face's frame in `fx_frame`."""
    positions, uvs, faces_frame = [], [], []
    for frame, (p, uv) in frames_of:
        positions.append(p)
        uvs.append(uv)
        faces_frame.append(np.full(len(p) // 3, frame, dtype=np.int32))
    positions = np.concatenate(positions)
    uvs = np.concatenate(uvs)
    faces_frame = np.concatenate(faces_frame)
    # Smash's Y up to Blender's Z up.
    m = np.array(FROM_SMASH.to_3x3(), dtype=np.float32)
    positions = positions @ m.T
    corners = len(positions)
    faces = corners // 3

    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(corners)
    mesh.vertices.foreach_set('co', positions.astype(np.float32).ravel())
    mesh.loops.add(corners)
    mesh.loops.foreach_set('vertex_index', np.arange(corners, dtype=np.int32))
    mesh.polygons.add(faces)
    mesh.polygons.foreach_set('loop_start', np.arange(0, corners, 3, dtype=np.int32))
    mesh.polygons.foreach_set('loop_total', np.full(faces, 3, dtype=np.int32))
    uv = mesh.uv_layers.new(name='UVMap')
    uv.data.foreach_set('uv', uvs.astype(np.float32).ravel())
    attribute = mesh.attributes.new(FRAME, 'INT', 'FACE')
    attribute.data.foreach_set('value', faces_frame)
    mesh.update()
    return mesh


def _frame_nodes():
    """Geometry nodes keeping the faces of the scene's frame."""
    group = bpy.data.node_groups.get(NODES)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(NODES, 'GeometryNodeTree')
    group.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    given = nodes.new('NodeGroupInput')
    given.location = (-600, 0)
    out = nodes.new('NodeGroupOutput')
    out.location = (300, 0)
    time = nodes.new('GeometryNodeInputSceneTime')
    time.location = (-600, -200)
    nearest = nodes.new('ShaderNodeMath')
    nearest.operation = 'ROUND'
    nearest.location = (-400, -200)
    frame = nodes.new('GeometryNodeInputNamedAttribute')
    frame.data_type = 'INT'
    frame.inputs['Name'].default_value = FRAME
    frame.location = (-400, -350)
    other = nodes.new('FunctionNodeCompare')
    other.data_type = 'INT'
    other.operation = 'NOT_EQUAL'
    other.location = (-200, -250)
    delete = nodes.new('GeometryNodeDeleteGeometry')
    delete.domain = 'FACE'
    delete.location = (0, 0)
    links.new(time.outputs['Frame'], nearest.inputs[0])
    ints = [s for s in other.inputs if s.type == 'INT']
    links.new(nearest.outputs[0], ints[0])
    links.new(frame.outputs['Attribute'], ints[1])
    links.new(given.outputs[0], delete.inputs['Geometry'])
    links.new(other.outputs['Result'], delete.inputs['Selection'])
    links.new(delete.outputs['Geometry'], out.inputs[0])
    return group


def _material(name, image, frames, light_by_emission):
    """Gives off the atlas' left half and lets its right half through."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    nodes, links = tree.nodes, tree.links
    coords = nodes.new('ShaderNodeUVMap')
    coords.uv_map = 'UVMap'
    across = nodes.new('ShaderNodeVectorMath')
    across.operation = 'ADD'
    across.inputs[1].default_value = (0.5, 0.0, 0.0)
    textures = []
    for k in range(2):
        texture = nodes.new('ShaderNodeTexImage')
        texture.image = image
        texture.interpolation = 'Linear'
        texture.extension = 'EXTEND'
        _place(texture.image_user, frames)
        texture.image_user.use_auto_refresh = True
        textures.append(texture)
    gives, through = textures
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Strength'].default_value = 1.0
    clear_bsdf = nodes.new('ShaderNodeBsdfTransparent')
    add = nodes.new('ShaderNodeAddShader')
    output = nodes.new('ShaderNodeOutputMaterial')
    for node, at in ((coords, (-800, 0)), (across, (-600, -250)), (gives, (-400, 100)), (through, (-400, -250)),
                     (emission, (-100, 100)), (clear_bsdf, (-100, -150)), (add, (100, 0)), (output, (300, 0))):
        node.location = at
    links.new(coords.outputs['UV'], gives.inputs['Vector'])
    links.new(coords.outputs['UV'], across.inputs[0])
    links.new(across.outputs[0], through.inputs['Vector'])
    links.new(gives.outputs['Color'], emission.inputs['Color'])
    links.new(through.outputs['Color'], clear_bsdf.inputs['Color'])
    links.new(emission.outputs[0], add.inputs[0])
    links.new(clear_bsdf.outputs[0], add.inputs[1])
    links.new(add.outputs[0], output.inputs['Surface'])
    if hasattr(material, 'surface_render_method'):
        material.surface_render_method = 'BLENDED'
    else:
        material.blend_method = 'BLEND'
    material.use_backface_culling = False
    # With effect lights on, they light the shot directly; sampling the meshes too would light it twice.
    if hasattr(material, 'cycles') and hasattr(material.cycles, 'emission_sampling'):
        material.cycles.emission_sampling = 'NONE' if not light_by_emission else 'AUTO'
    return material


def apply(scene, out, names, light_by_emission=True):
    """Build a mesh per effect from `out/geo`; `names` are the effect objects in the order the scene file listed
    them. Returns how many were made."""
    folder = os.path.join(out, 'geo')
    clear(scene)
    if not os.path.isdir(folder):
        return 0
    coll = _collection(scene)
    nodes = _frame_nodes()
    made = 0
    for e, name in enumerate(names):
        files = sorted(glob.glob(os.path.join(folder, f'geo_{e:02}_*.bin')))
        frames_of = []
        for path in files:
            frame = int(os.path.basename(path)[len(f'geo_{e:02}_'):-len('.bin')])
            p, uv = _read(path)
            if len(p):
                frames_of.append((frame, (p, uv)))
        if not frames_of:
            continue
        atlases = sorted(glob.glob(os.path.join(folder, f'atlas_{e:02}_*.exr')))
        frames = [int(os.path.basename(a)[len(f'atlas_{e:02}_'):-len('.exr')]) for a in atlases]
        image = bpy.data.images.load(atlases[0])
        image.name = f'{name} Atlas'
        image.source = 'SEQUENCE'
        image.alpha_mode = 'CHANNEL_PACKED'
        mesh = _mesh(f'{name} Geometry', frames_of)
        mesh.materials.append(_material(f'{name} Geometry', image, frames, light_by_emission))
        obj = bpy.data.objects.new(f'{name} Geometry', mesh)
        coll.objects.link(obj)
        obj['smash_fx_effect'] = name
        modifier = obj.modifiers.new('Frame', 'NODES')
        modifier.node_group = nodes
        # Effects give light and tint it; they cast no hard shadow of their own.
        obj.visible_shadow = False
        made += 1
    if made and hasattr(scene, 'cycles'):
        scene.cycles.transparent_max_bounces = max(scene.cycles.transparent_max_bounces, TRANSPARENT_BOUNCES)
    return made
