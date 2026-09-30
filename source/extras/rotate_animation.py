"""
Rotate Animation: turn a whole animation around the vertical axis (e.g. 35 degrees so a
ported idle reads like the 2D pose it came from), without rotation keys on the bones that
cannot have them.

Trans / Rot / Hip (by default) keep their keys. The rotation goes onto the first bones below
them (Waist, LegC, ...), so everything under those turns with them, and onto the animation-rig
controls that hang off the locked bones (HandIK, FootIK, ArmIK, KneeIK, ...), so IK limbs turn
with the body instead of staying behind. The axis is vertical and goes through the hips on
every frame, so the character turns in place, its path is kept, and the feet stay on the floor.
"""

import math

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from mathutils import Matrix, Quaternion, Vector

from ..anim.fcurve_compat import get_all_action_fcurves
from .create_animation_rig import _activate_armature, canonical_bone_name, find_target_armature

DEFAULT_LOCKED = "Trans, Rot, Hip"
DEFAULT_EXCLUDED = "Throw"


def _names(text):
    return {n.strip() for n in (text or "").replace(";", ",").split(",") if n.strip()}


def rotation_roots(armature_obj, locked, excluded):
    """The bones that receive the rotation: not locked themselves, with a locked parent (or no
    parent at all)."""
    roots = []
    for pose_bone in armature_obj.pose.bones:
        base = canonical_bone_name(pose_bone.name)
        if base in locked or base in excluded or pose_bone.name in excluded:
            continue
        parent = pose_bone.parent
        if parent is None or canonical_bone_name(parent.name) in locked:
            roots.append(pose_bone.name)
    return roots


def _pivot(armature_obj, pivot_mode, pivot_bone):
    if pivot_mode == 'ORIGIN':
        return Vector((0.0, 0.0, 0.0))
    bone = armature_obj.pose.bones.get(pivot_bone)
    return bone.head.copy() if bone is not None else Vector((0.0, 0.0, 0.0))


def _basis(pose_bone, matrix, parent_matrix):
    bone = pose_bone.bone
    if bone.parent is None or parent_matrix is None:
        return bone.matrix_local.inverted() @ matrix
    rest = bone.parent.matrix_local.inverted() @ bone.matrix_local
    return rest.inverted() @ parent_matrix.inverted() @ matrix


def rotate_animation(context, armature_obj, angle, frames, locked, excluded,
                     pivot_mode='HIP', pivot_bone='Hip'):
    """Rotate the animation by `angle` (radians, about +Z). Returns (bones keyed, frames)."""
    scene = context.scene
    roots = rotation_roots(armature_obj, locked, excluded)
    if not roots:
        return 0, 0
    pose = armature_obj.pose.bones
    action = armature_obj.animation_data.action if armature_obj.animation_data else None

    # Held (muted) curves - e.g. the IK controls while the rig is in FK - still have to be read.
    muted = []
    if action is not None:
        prefixes = tuple(f'pose.bones["{n}"]' for n in roots)
        for fcurve in get_all_action_fcurves(action):
            if fcurve.mute and (fcurve.data_path or "").startswith(prefixes):
                fcurve.mute = False
                muted.append(fcurve)

    saved_frame = scene.frame_current
    turn = Matrix.Rotation(angle, 4, 'Z')
    samples = []
    try:
        for frame in frames:
            scene.frame_set(frame)
            # Axis in armature space; the armature object itself is not rotated.
            p = _pivot(armature_obj, pivot_mode, pivot_bone)
            around = Matrix.Translation((p.x, p.y, 0.0)) @ turn @ Matrix.Translation((-p.x, -p.y, 0.0))
            frame_bases = {}
            for name in roots:
                pose_bone = pose[name]
                parent = pose_bone.parent.matrix.copy() if pose_bone.parent else None
                frame_bases[name] = _basis(pose_bone, around @ pose_bone.matrix, parent)
            samples.append((frame, frame_bases))

        previous = {}
        for frame, frame_bases in samples:
            for name, basis in frame_bases.items():
                pose_bone = pose[name]
                location, rotation, scale = basis.decompose()
                pose_bone.location = location
                group = pose_bone.name
                if pose_bone.rotation_mode == 'QUATERNION':
                    last = previous.get(name)
                    if last is not None and last.dot(rotation) < 0.0:
                        rotation = -rotation
                    previous[name] = rotation.copy()
                    pose_bone.rotation_quaternion = rotation
                    pose_bone.keyframe_insert('rotation_quaternion', frame=frame, group=group)
                elif pose_bone.rotation_mode == 'AXIS_ANGLE':
                    axis, value = rotation.to_axis_angle()
                    pose_bone.rotation_axis_angle = (value, *axis)
                    pose_bone.keyframe_insert('rotation_axis_angle', frame=frame, group=group)
                else:
                    last = previous.get(name)
                    euler = rotation.to_euler(pose_bone.rotation_mode, last) if last else \
                        rotation.to_euler(pose_bone.rotation_mode)
                    previous[name] = euler.copy()
                    pose_bone.rotation_euler = euler
                    pose_bone.keyframe_insert('rotation_euler', frame=frame, group=group)
                pose_bone.scale = scale
                pose_bone.keyframe_insert('location', frame=frame, group=group)
                pose_bone.keyframe_insert('scale', frame=frame, group=group)
    finally:
        for fcurve in muted:
            fcurve.mute = True
        scene.frame_set(saved_frame)
    return len(roots), len(samples)


class SUB_OP_rotate_animation(Operator):
    """Turn the whole animation around the vertical axis, IK controls included"""
    bl_idname = 'sub.rotate_animation'
    bl_label = 'Rotate Animation'
    bl_description = (
        'Turn the character around the vertical axis through its hips on every frame (e.g. 35 '
        'degrees for a 2D-looking idle). Trans/Rot/Hip keep their keys; the rotation goes on the '
        'bones below them and on the IK controls, so IK limbs turn with the body'
    )
    bl_options = {'REGISTER', 'UNDO'}

    angle: FloatProperty(name='Angle', default=math.radians(35.0), subtype='ANGLE',
                         description='Turn around +Z (positive is counter-clockwise seen from above)')
    frame_range: EnumProperty(
        name='Frames',
        items=(
            ('ACTION', "Whole Action", "Every frame of the current action"),
            ('SCENE', "Scene Range", "Every frame from the scene start to end"),
            ('CURRENT', "Current Frame", "Only this frame (to pose, not animate)"),
        ),
        default='ACTION',
    )
    pivot: EnumProperty(
        name='Pivot',
        items=(
            ('HIP', "Hips (follows)", "A vertical axis through the pivot bone on each frame, so the "
                                      "character turns in place wherever it is"),
            ('ORIGIN', "Origin", "The vertical axis through the armature origin"),
        ),
        default='HIP',
    )
    pivot_bone: StringProperty(name='Pivot Bone', default='Hip')
    locked_bones: StringProperty(
        name='Keep Keys On', default=DEFAULT_LOCKED,
        description='Bones that must not get rotation keys (comma separated). The rotation goes on '
                    'the bones directly below them',
    )
    excluded_bones: StringProperty(
        name='Leave Alone', default=DEFAULT_EXCLUDED,
        description='Bones under the locked ones that must not turn (e.g. Throw, which is gameplay)',
    )

    @classmethod
    def poll(cls, context):
        armature = find_target_armature(context)
        return armature is not None and armature.type == 'ARMATURE'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        armature_obj = find_target_armature(context)
        _activate_armature(context, armature_obj)
        if context.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        scene = context.scene
        action = armature_obj.animation_data.action if armature_obj.animation_data else None
        if self.frame_range == 'CURRENT' or (self.frame_range == 'ACTION' and action is None):
            frames = [scene.frame_current]
        elif self.frame_range == 'ACTION':
            start, end = (int(round(v)) for v in action.frame_range)
            frames = range(start, end + 1)
        else:
            frames = range(scene.frame_start, scene.frame_end + 1)
        bones, count = rotate_animation(
            context, armature_obj, self.angle, frames,
            _names(self.locked_bones), _names(self.excluded_bones), self.pivot, self.pivot_bone,
        )
        if not bones:
            self.report({'ERROR'}, "No bones below the locked ones to rotate.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Rotated {math.degrees(self.angle):.1f} degrees on {count} frame(s) "
                              f"({bones} bones keyed)")
        return {'FINISHED'}
