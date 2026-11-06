"""
Putting baked effect frames where they are seen: over the render in the compositor, and
over the camera view in the viewport.

`fx_<frame>.exr` is the effects alone in linear light, premultiplied: Alpha Over lays it
on the render the way the game blends (additive layers carry colour with no alpha).
`preview_<frame>.png` is the same in sRGB with straight alpha, for camera backgrounds.
"""

import os

import bpy

FX_IMAGE = 'Smash FX'
PREVIEW_IMAGE = 'Smash FX Preview'
NODE = 'Smash FX'
OVER = 'Smash FX Over'
GLARE = 'Smash FX Bloom'
ADD = 'Smash FX Bloom Add'


def _sequence(name, path, alpha_mode, colourspace=None):
    """The image named `name`, reading the sequence `path` belongs to, loaded fresh."""
    image = bpy.data.images.get(name)
    if image is None:
        image = bpy.data.images.load(path)
        image.name = name
    else:
        image.filepath = path
    image.source = 'SEQUENCE'
    image.alpha_mode = alpha_mode
    if colourspace:
        try:
            image.colorspace_settings.name = colourspace
        except TypeError:
            pass
    image.reload()
    return image


def _place(user, frames):
    """Frame n of the scene shows file n."""
    user.frame_start = frames[0]
    user.frame_offset = frames[0] - 1
    user.frame_duration = len(frames)


def _tree(scene):
    """The compositor's node tree, made if there is none, and its output's colour input."""
    if hasattr(scene, 'compositing_node_group'):
        tree = scene.compositing_node_group
        if tree is None:
            tree = bpy.data.node_groups.new('Compositing', 'CompositorNodeTree')
            tree.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
            scene.compositing_node_group = tree
        out = next((n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
        if out is None:
            out = tree.nodes.new('NodeGroupOutput')
            out.location = (600, 0)
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        out = next((n for n in tree.nodes if n.bl_idname == 'CompositorNodeComposite'), None)
        if out is None:
            out = tree.nodes.new('CompositorNodeComposite')
            out.location = (600, 0)
    return tree, out.inputs[0]


def composite(scene, out, frames):
    tree, final = _tree(scene)
    image = _sequence(FX_IMAGE, os.path.join(out, f'fx_{frames[0]:04}.exr'), 'PREMUL')

    over = tree.nodes.get(OVER)
    if over is None:
        # Between whatever reached the output and the output.
        before = final.links[0].from_socket if final.links else None
        if before is None:
            layers = tree.nodes.new('CompositorNodeRLayers')
            layers.location = (0, 0)
            before = layers.outputs['Image']
        over = tree.nodes.new('CompositorNodeAlphaOver')
        over.name = over.label = OVER
        over.location = (final.node.location.x - 220, final.node.location.y)
        tree.links.new(before, over.inputs[0])
        tree.links.new(over.outputs[0], final)
    if 'Straight Alpha' in over.inputs:
        over.inputs['Straight Alpha'].default_value = False
    elif hasattr(over, 'use_premultiply'):
        over.use_premultiply = False

    node = tree.nodes.get(NODE)
    if node is None:
        node = tree.nodes.new('CompositorNodeImage')
        node.name = node.label = NODE
        node.location = (over.location.x - 260, over.location.y - 200)
    node.image = image
    _place(node, frames)
    tree.links.new(node.outputs['Image'], over.inputs[1])
    _bloom(scene, tree, node, over)


def _add_node(tree):
    """A node adding two colours: the Mix node in Blender 5, MixRGB before."""
    try:
        add = tree.nodes.new('ShaderNodeMix')
        add.data_type = 'RGBA'
        add.blend_type = 'ADD'
        return add, add.inputs[6], add.inputs[7], add.outputs[2]
    except RuntimeError:
        add = tree.nodes.new('CompositorNodeMixRGB')
        add.blend_type = 'ADD'
        return add, add.inputs[1], add.inputs[2], add.outputs[0]


def _bloom(scene, tree, image, over):
    """Glare off the effects alone, added after they are laid over the render, so only the
    effects glow out into the frame. Off, the nodes are muted."""
    settings = scene.sub_smash_fx
    glare = tree.nodes.get(GLARE)
    add = tree.nodes.get(ADD)
    if glare is None or add is None:
        if not settings.bloom:
            return
        glare = tree.nodes.new('CompositorNodeGlare')
        glare.name = glare.label = GLARE
        glare.location = (image.location.x + 220, image.location.y - 220)
        add, a, b, result = _add_node(tree)
        add.name = add.label = ADD
        add.location = (over.location.x + 200, over.location.y)
        if 'Factor' in add.inputs:
            add.inputs[0].default_value = 1.0
        # What the Alpha Over fed now goes through the add.
        for link in list(over.outputs[0].links):
            tree.links.new(result, link.to_socket)
        tree.links.new(over.outputs[0], a)
        tree.links.new(image.outputs['Image'], glare.inputs['Image'])
        glare_out = glare.outputs['Glare'] if 'Glare' in glare.outputs else glare.outputs[0]
        tree.links.new(glare_out, b)
    tune_bloom(scene)


def tune_bloom(scene):
    tree = scene.compositing_node_group if hasattr(scene, 'compositing_node_group') else scene.node_tree
    glare = tree.nodes.get(GLARE) if tree else None
    add = tree.nodes.get(ADD) if tree else None
    if glare is None or add is None:
        return
    settings = scene.sub_smash_fx
    glare.mute = add.mute = not settings.bloom
    if 'Type' in glare.inputs:
        glare.inputs['Type'].default_value = 'Bloom'
        glare.inputs['Quality'].default_value = 'High'
        glare.inputs['Threshold'].default_value = 0.2
        glare.inputs['Strength'].default_value = settings.bloom_strength
        glare.inputs['Size'].default_value = settings.bloom_size
    else:
        glare.glare_type = 'FOG_GLOW'
        glare.quality = 'HIGH'
        glare.threshold = 0.2
        glare.size = max(6, min(9, int(6 + settings.bloom_size * 3)))
        glare.mix = settings.bloom_strength - 1.0


def show_in_cameras(cameras, out, frames):
    image = _sequence(PREVIEW_IMAGE, os.path.join(out, f'preview_{frames[0]:04}.png'), 'STRAIGHT', 'sRGB')
    for cam in cameras:
        data = cam.data
        data.show_background_images = True
        bg = next((b for b in data.background_images if b.image and b.image.name == PREVIEW_IMAGE), None)
        if bg is None:
            bg = data.background_images.new()
            bg.image = image
        bg.display_depth = 'FRONT'
        bg.frame_method = 'STRETCH'
        bg.alpha = 1.0
        bg.image_user.use_auto_refresh = True
        _place(bg.image_user, frames)
