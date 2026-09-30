"""
Mouth controls for Smash armatures that have mouth bones (Jaw, Uplip*/Downlip*,
as on the MHA fighters), in the style of the PSK face rig:

    BL_Mouth            under the chin. Down opens the jaw, sideways shifts it,
                        forward/back pushes it out/in.
    BL_MouthCornerL/R   at the lip corners. Moves the corner lips (smile, frown,
                        wide, narrow).
    BL_UpLip/BL_DownLip in front of the lips. Moves the middle of each lip.

The controls add on top of the keyed pose (the game's mouth animation still
plays), with Transformation constraints in local space. Everything starts with
BL_, so the exporter skips the controls, and "Bake Rig on Export" / "Bake and
Remove Rig" write the result onto the Smash bones.
"""

import math
import re

import bpy
from mathutils import Matrix, Vector

from ..blender_compat import assign_bone_to_collection, ensure_bone_collection
from .create_animation_rig import (
    _LEFT_COLOR,
    _RIGHT_COLOR,
    _assign_shape,
    _set_collection_visible,
    _widget_object,
    bone_name_suffix,
    canonical_bone_name,
)

COLLECTION = "Mouth Controls"
CONSTRAINT_PREFIX = "SUB_Mouth"
CENTER_COLOR = "THEME09"
JAW_OPEN_ANGLE = math.radians(30.0)
JAW_SIDE_ANGLE = math.radians(12.0)

FORWARD = Vector((0.0, -1.0, 0.0))
UP = Vector((0.0, 0.0, 1.0))
LEFT = Vector((1.0, 0.0, 0.0))

_LIP_RE = re.compile(r'^(Up|Down)lip([LRC])(\d*)$', re.IGNORECASE)


def _control_names(suffix=''):
    return {
        'jaw': f'BL_Mouth{suffix}',
        'corner_L': f'BL_MouthCornerL{suffix}',
        'corner_R': f'BL_MouthCornerR{suffix}',
        'up': f'BL_UpLip{suffix}',
        'down': f'BL_DownLip{suffix}',
    }


def find_mouth(armature_obj, suffix=''):
    """The mouth bones of one suffix group, or None when there is no mouth to rig."""
    bones = armature_obj.data.bones
    jaw = None
    lips = []
    for bone in bones:
        if bone_name_suffix(bone.name) != suffix:
            continue
        base = canonical_bone_name(bone.name)
        if base.lower() == 'jaw':
            jaw = bone.name
            continue
        match = _LIP_RE.match(base)
        if match:
            lips.append((bone.name, match.group(1).lower(), match.group(2).upper(), match.group(3)))
    if jaw is None and len(lips) < 2:
        return None
    head = None
    probe = bones.get(jaw) if jaw else bones.get(lips[0][0])
    while probe is not None:
        if canonical_bone_name(probe.name) in {'Head', 'HeadN'}:
            head = probe.name
            break
        probe = probe.parent
    if head is None:
        head = (bones.get(jaw) or bones.get(lips[0][0])).parent.name
    return {'jaw': jaw, 'lips': lips, 'head': head, 'suffix': suffix}


def iter_mouths(armature_obj):
    suffixes = {bone_name_suffix(b.name) for b in armature_obj.data.bones}
    for suffix in sorted(suffixes):
        mouth = find_mouth(armature_obj, suffix)
        if mouth is not None:
            yield mouth


def has_mouth_controls(armature_obj):
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return False
    return any(b.name.startswith('BL_Mouth') for b in armature_obj.data.bones)


def _lip_weights(mouth, rest):
    """{control: {bone: weight}} from the lip positions (corners are the outermost lips)."""
    lips = mouth['lips']
    if not lips:
        return {}
    xs = [rest[name].translation.x for name, *_ in lips]
    half_width = max(max(abs(x) for x in xs), 1e-6)
    names = _control_names(mouth['suffix'])
    weights = {names['corner_L']: {}, names['corner_R']: {}, names['up']: {}, names['down']: {}}
    for name, part, side, _digits in lips:
        # 0 at the middle of the mouth, 1 at the corners.
        t = min(abs(rest[name].translation.x) / half_width, 1.0) if side != 'C' else 0.0
        if t > 0.2:
            corner = names['corner_L'] if rest[name].translation.x > 0.0 else names['corner_R']
            weights[corner][name] = t ** 1.5
        lip = names['up'] if part == 'up' else names['down']
        middle = 1.0 - t ** 2
        if middle > 0.05:
            weights[lip][name] = middle
    return weights


def _frame(head, y, z_hint):
    y = y.normalized()
    z = (z_hint - y * z_hint.dot(y)).normalized()
    x = y.cross(z)
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = head
    return m


def build(context, armature_obj):
    """Create (or rebuild) the mouth controls. Returns the number of mouths rigged."""
    mouths = list(iter_mouths(armature_obj))
    if not mouths:
        return 0
    remove(armature_obj)
    armature = armature_obj.data
    rest = {b.name: b.matrix_local.copy() for b in armature.bones}
    layout = {}
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = armature.edit_bones
        for mouth in mouths:
            names = _control_names(mouth['suffix'])
            points = [rest[n].translation for n, *_ in mouth['lips']]
            if mouth['jaw']:
                points.append(Vector(armature.bones[mouth['jaw']].tail_local))
            center = sum(points, Vector()) / len(points)
            width = max((max(p.x for p in points) - min(p.x for p in points)), 1e-3)
            if width < 1e-2 and mouth['jaw']:
                width = armature.bones[mouth['jaw']].length * 0.6
            front = max(p.dot(FORWARD) for p in points) + width * 0.5
            size = width * 0.12

            def add(name, position):
                bone = edit_bones.new(name)
                bone.head = position
                bone.tail = position + FORWARD * size
                bone.matrix = _frame(position, FORWARD, UP)
                bone.length = size
                bone.use_deform = False
                bone.parent = edit_bones[mouth['head']]

            def in_front(p):
                return p + FORWARD * (front - p.dot(FORWARD))

            up_points = [rest[n].translation for n, part, *_ in mouth['lips'] if part == 'up'] or points
            down_points = [rest[n].translation for n, part, *_ in mouth['lips'] if part == 'down'] or points
            up_z = max(p.z for p in up_points)
            down_z = min(p.z for p in down_points)
            if mouth['jaw']:
                add(names['jaw'], in_front(Vector((center.x, center.y, down_z - width * 0.45))))
            if mouth['lips']:
                add(names['up'], in_front(Vector((center.x, center.y, up_z + width * 0.12))))
                add(names['down'], in_front(Vector((center.x, center.y, down_z - width * 0.12))))
                for side, sign in (('L', 1.0), ('R', -1.0)):
                    x = center.x + sign * width * 0.62
                    add(names[f'corner_{side}'], in_front(Vector((x, center.y, (up_z + down_z) * 0.5))))
            layout[mouth['suffix']] = width
    finally:
        bpy.ops.object.mode_set(mode='POSE')

    collection = ensure_bone_collection(armature, COLLECTION)
    _set_collection_visible(armature, COLLECTION, True)
    widget = _widget_object(context, 'circle')
    jaw_widget = _widget_object(context, 'diamond')
    for mouth in mouths:
        width = layout[mouth['suffix']]
        names = _control_names(mouth['suffix'])
        controls = [n for n in names.values() if armature.bones.get(n) is not None]
        for name in controls:
            pose_bone = armature_obj.pose.bones[name]
            pose_bone.lock_rotation = (True, True, True)
            pose_bone.lock_rotation_w = True
            pose_bone.lock_scale = (True, True, True)
            color = _LEFT_COLOR if name.startswith('BL_MouthCornerL') else (
                _RIGHT_COLOR if name.startswith('BL_MouthCornerR') else CENTER_COLOR)
            if name == names['jaw']:
                _assign_shape(pose_bone, jaw_widget, width * 0.14, color, False, armature_obj)
            else:
                _assign_shape(pose_bone, widget, width * 0.08, color, False, armature_obj,
                              rotation_euler=(math.radians(90.0), 0.0, 0.0))
            if collection is not None:
                assign_bone_to_collection(collection, armature.bones[name])

        # Lips follow their controls: 1 unit of control movement moves the lip 1 unit (x weight).
        for control, bone_weights in _lip_weights(mouth, rest).items():
            if armature.bones.get(control) is None:
                continue
            for bone, weight in bone_weights.items():
                _add_translation_map(armature_obj, bone, control, weight, width)
        if mouth['jaw'] and armature.bones.get(names['jaw']) is not None:
            _add_jaw_map(armature_obj, mouth['jaw'], names['jaw'], width)
    return len(mouths)


def _local_map(armature, owner, control):
    """3x3: control local translation -> owner local translation (rest orientations)."""
    owner_rot = armature.bones[owner].matrix_local.to_3x3()
    control_rot = armature.bones[control].matrix_local.to_3x3()
    return owner_rot.inverted() @ control_rot


def _new_transform(pose_bone, name, armature_obj, control):
    c = pose_bone.constraints.new('TRANSFORM')
    c.name = f'{CONSTRAINT_PREFIX} {name}'
    c.target, c.subtarget = armature_obj, control
    c.owner_space = c.target_space = 'LOCAL'
    c.use_motion_extrapolate = True
    return c


_AXES = 'xyz'


def _add_translation_map(armature_obj, bone, control, weight, width):
    m = _local_map(armature_obj.data, bone, control)
    pose_bone = armature_obj.pose.bones[bone]
    span = width * 0.5
    for j, source in enumerate('XYZ'):
        c = _new_transform(pose_bone, f'{control} {source}', armature_obj, control)
        c.map_from = c.map_to = 'LOCATION'
        setattr(c, f'from_min_{_AXES[j]}', -span)
        setattr(c, f'from_max_{_AXES[j]}', span)
        for i, out in enumerate(_AXES):
            setattr(c, f'map_to_{out}_from', source)
            setattr(c, f'to_min_{out}', -span * m[i][j] * weight)
            setattr(c, f'to_max_{out}', span * m[i][j] * weight)
        c.mix_mode = 'ADD'


def _add_jaw_map(armature_obj, jaw, control, width):
    """Control down -> jaw opens (about the character's left axis); sideways -> jaw yaw;
    forward/back -> jaw slides."""
    armature = armature_obj.data
    jaw_rot = armature.bones[jaw].matrix_local.to_3x3()
    control_rot = armature.bones[control].matrix_local.to_3x3()
    to_jaw = jaw_rot.inverted()
    open_axis = to_jaw @ LEFT      # +angle about +X (left) swings the chin down
    side_axis = to_jaw @ UP
    travel = width * 0.5
    pose_bone = armature_obj.pose.bones[jaw]
    for j, source in enumerate('XYZ'):
        direction = control_rot.col[j]
        down = -direction.dot(UP)
        side = direction.dot(LEFT)
        axis = open_axis * (down * JAW_OPEN_ANGLE) + side_axis * (side * JAW_SIDE_ANGLE)
        if axis.length > 1e-6:
            c = _new_transform(pose_bone, f'{control} {source} Rotate', armature_obj, control)
            c.map_from, c.map_to = 'LOCATION', 'ROTATION'
            setattr(c, f'from_min_{_AXES[j]}', -travel)
            setattr(c, f'from_max_{_AXES[j]}', travel)
            for i, out in enumerate(_AXES):
                setattr(c, f'map_to_{out}_from', source)
                setattr(c, f'to_min_{out}_rot', -axis[i])
                setattr(c, f'to_max_{out}_rot', axis[i])
            # BEFORE: the axis is in the rest frame, whatever the keyed jaw rotation is.
            c.mix_mode_rot = 'BEFORE'
        slide = direction.dot(FORWARD)
        if abs(slide) > 1e-6:
            c = _new_transform(pose_bone, f'{control} {source} Slide', armature_obj, control)
            c.map_from = c.map_to = 'LOCATION'
            setattr(c, f'from_min_{_AXES[j]}', -travel)
            setattr(c, f'from_max_{_AXES[j]}', travel)
            delta = to_jaw @ (FORWARD * slide * travel * 0.4)
            for i, out in enumerate(_AXES):
                setattr(c, f'map_to_{out}_from', source)
                setattr(c, f'to_min_{out}', -delta[i])
                setattr(c, f'to_max_{out}', delta[i])
            c.mix_mode = 'ADD'


def driven_bone_names(armature_obj):
    return [pb.name for pb in armature_obj.pose.bones
            if any(c.name.startswith(CONSTRAINT_PREFIX) for c in pb.constraints)]


def bake(context, armature_obj):
    """Key the mouth bones as the controls show them over the scene range. Returns keys set."""
    names = driven_bone_names(armature_obj)
    anim = armature_obj.animation_data
    if not names or anim is None or anim.action is None:
        return 0
    scene = context.scene
    saved = scene.frame_current
    pose = armature_obj.pose.bones
    frames = range(int(scene.frame_start), int(scene.frame_end) + 1)
    samples = {}
    for frame in frames:
        scene.frame_set(frame)
        for name in names:
            pb = pose[name]
            local = pb.matrix if pb.parent is None else pb.parent.matrix.inverted() @ pb.matrix
            bone = pb.bone
            rel = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
            samples[(name, frame)] = (rel.inverted() @ local).decompose()
    for name in names:
        for c in list(pose[name].constraints):
            if c.name.startswith(CONSTRAINT_PREFIX):
                c.mute = True
    count = 0
    for frame in frames:
        for name in names:
            pb = pose[name]
            loc, rot, scale = samples[(name, frame)]
            pb.location = loc
            if pb.rotation_mode == 'QUATERNION':
                pb.rotation_quaternion = rot
                pb.keyframe_insert('rotation_quaternion', frame=frame, group=name)
            else:
                pb.rotation_euler = rot.to_euler(pb.rotation_mode)
                pb.keyframe_insert('rotation_euler', frame=frame, group=name)
            pb.scale = scale
            pb.keyframe_insert('location', frame=frame, group=name)
            pb.keyframe_insert('scale', frame=frame, group=name)
            count += 1
    scene.frame_set(saved)
    return count


def remove(armature_obj):
    """Remove the mouth controls and their constraints (without baking)."""
    for pb in armature_obj.pose.bones:
        for c in list(pb.constraints):
            if c.name.startswith(CONSTRAINT_PREFIX):
                pb.constraints.remove(c)
    names = [b.name for b in armature_obj.data.bones
             if b.name.startswith(('BL_Mouth', 'BL_UpLip', 'BL_DownLip'))]
    if not names:
        return 0
    anim = armature_obj.animation_data
    action = getattr(anim, 'action', None) if anim else None
    if action is not None:
        from ..anim.fcurve_compat import get_all_action_fcurves, remove_fcurve
        prefixes = tuple(f'pose.bones["{n}"]' for n in names)
        for fcurve in list(get_all_action_fcurves(action)):
            if (fcurve.data_path or '').startswith(prefixes):
                remove_fcurve(action, fcurve)
    previous = armature_obj.mode
    bpy.ops.object.mode_set(mode='EDIT')
    for name in names:
        bone = armature_obj.data.edit_bones.get(name)
        if bone is not None:
            armature_obj.data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode=previous if previous in {'POSE', 'OBJECT'} else 'POSE')
    return len(names)
