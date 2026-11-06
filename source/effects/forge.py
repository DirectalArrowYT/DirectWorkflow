"""
Talking to EffectForge, which draws the effects: listing an .eff file's effects, and
turning the scene into the scene file its `render-scene` command draws.

EffectForge works in Smash's own space (Y up). The add-on brings Smash in with a quarter
turn about X (Z up), so everything going out is turned back by the opposite quarter turn.
"""

import json
import math
import os
import subprocess

import bpy
from mathutils import Matrix

DEFAULT_FORGE = os.path.expanduser('~/Documents/GitHub/EffectForge/target/release/effect_forge')
DEFAULT_GAME_ROOT = '/mnt/AllForMe/Archives/SmashArc'

# Blender (Z up) to Smash (Y up).
TO_SMASH = Matrix.Rotation(math.radians(-90), 4, 'X')
# The game counts effect frames at 60 a second.
GAME_FPS = 60.0


def forge_path(settings):
    return bpy.path.abspath(settings.forge) if settings.forge else DEFAULT_FORGE


def out_dir(settings):
    return os.path.normpath(bpy.path.abspath(settings.out))


def vanilla_eff(game_root, source):
    """The .eff of a vanilla source: 'common', 'item' or a fighter's folder name."""
    # Kept as given, so a blend-relative root (//game) stays relative in what is stored.
    root = game_root
    if source == 'common':
        return os.path.join(root, 'effect', 'system', 'common', 'ef_common.eff')
    if source == 'item':
        return os.path.join(root, 'effect', 'system', 'item', 'ef_item.eff')
    return os.path.join(root, 'effect', 'fighter', source, f'ef_{source}.eff')


def fighters(game_root):
    folder = os.path.join(bpy.path.abspath(game_root), 'effect', 'fighter')
    try:
        return sorted(name for name in os.listdir(folder)
                      if os.path.isfile(os.path.join(folder, name, f'ef_{name}.eff')))
    except OSError:
        return []


def list_effects(forge, eff):
    """[{name, layers, length, loops}] of an .eff file, from `effect_forge effects`."""
    done = subprocess.run([forge, 'effects', eff], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip().splitlines()[-1:] or ['EffectForge failed'])
    return json.loads(done.stdout)


def _columns(m):
    """A matrix as 16 floats, column-major."""
    return [m[r][c] for c in range(4) for r in range(4)]


def effect_objects(scene):
    return [o for o in scene.objects if o.smash_fx.enabled and o.smash_fx.play and o.smash_fx.eff and o.smash_fx.effect]


def _step(scene, fx):
    """Effect frames per scene frame."""
    fps = scene.render.fps / scene.render.fps_base
    return GAME_FPS / fps * fx.rate


def clock(scene, fx, frame):
    """The effect's age at a scene frame, or None where it is not playing."""
    if frame < fx.start or (fx.use_end and frame >= fx.end):
        return None
    return (frame - fx.start) * _step(scene, fx)


def resolution(scene):
    r = scene.render
    return (max(1, r.resolution_x * r.resolution_percentage // 100),
            max(1, r.resolution_y * r.resolution_percentage // 100))


def build_scene(context, frames, out, shots=None, preview=True, lights=False, cards=False, geometry=False):
    """The scene file for `render-scene`: a camera per frame and each effect's track.

    Every effect is sampled once per effect frame from its start, so an effect at 60 a
    second in a 30 fps shot gets a sample between frames; each sample is a frame_set,
    shared by every effect and the camera wanting that moment. With `lights`, EffectForge
    also reads the light each effect gives off (see lights.py), and with `cards` cuts out
    each effect's picture to stand in the shot (see cards.py), and with `geometry` writes
    each effect's triangles and atlas (see geometry.py); each effect names its
    object for those, which EffectForge passes over."""
    scene = context.scene
    width, height = resolution(scene)
    objects = effect_objects(scene)

    # Every moment to sample, and who wants it.
    wants = {}

    def want(time, what):
        wants.setdefault(round(time, 4), []).append(what)

    for frame in frames:
        want(frame, ('camera', frame))
    tracks = {}
    for obj in objects:
        fx = obj.smash_fx
        ages = [clock(scene, fx, f) for f in frames]
        oldest = max((a for a in ages if a is not None), default=None)
        if oldest is None:
            continue
        step = _step(scene, fx)
        samples = int(math.ceil(oldest)) + 2
        tracks[obj.name] = [None] * samples
        for k in range(samples):
            want(fx.start + k / step, ('track', obj.name, k))

    cameras = {}
    keep = (scene.frame_current, scene.frame_subframe)
    try:
        for time in sorted(wants):
            whole = math.floor(time)
            scene.frame_set(whole, subframe=time - whole)
            depsgraph = context.evaluated_depsgraph_get()
            for what in wants[time]:
                if what[0] == 'camera':
                    cameras[what[1]] = _camera(scene, depsgraph, width, height)
                else:
                    obj = scene.objects[what[1]]
                    tracks[what[1]][what[2]] = _columns(TO_SMASH @ obj.matrix_world)
    finally:
        scene.frame_set(keep[0], subframe=keep[1])

    effects = []
    for obj in objects:
        if obj.name not in tracks:
            continue
        fx = obj.smash_fx
        stop = (fx.stop - fx.start) * _step(scene, fx) if fx.use_stop else None
        effects.append({
            'object': obj.name,
            'eff': bpy.path.abspath(fx.eff),
            'effect': fx.effect,
            'seed': fx.seed,
            'colour': list(fx.colour),
            'track': tracks[obj.name],
            'clock': [clock(scene, fx, f) for f in frames],
            'stop': stop,
        })

    return {
        'width': width,
        'height': height,
        'frames': list(frames),
        'cameras': [cameras[f] for f in frames],
        'shots': shots,
        'out': out,
        'preview': preview,
        'lights': lights,
        'cards': cards,
        'geometry': geometry,
        'effects': effects,
    }


def _camera(scene, depsgraph, width, height):
    cam = scene.camera
    if cam is None:
        raise RuntimeError('The scene has no camera')
    if cam.data.type != 'PERSP':
        raise RuntimeError(f'{cam.name} is not a perspective camera; effects need one')
    r = scene.render
    proj = cam.calc_matrix_camera(depsgraph, x=width, y=height, scale_x=r.pixel_aspect_x, scale_y=r.pixel_aspect_y)
    view = (TO_SMASH @ cam.matrix_world.normalized()).inverted()
    return {
        'view': _columns(view),
        'lens': [proj[0][0], proj[1][1], proj[0][2], proj[1][2]],
        'near': cam.data.clip_start,
    }
