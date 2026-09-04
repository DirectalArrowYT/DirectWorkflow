import math

from mathutils import Vector


def place_leg_ik_edit_bones(armature, side, leg_bone, knee_bone, foot_bone, ik_scale_factor=1.5):
    """Create KneeIK and FootIK edit bones for one leg from FK bone geometry."""
    fk_knee_pos = knee_bone.head

    char_forward_local = Vector((0.0, -1.0, 0.0))
    thigh_vec = leg_bone.tail - leg_bone.head
    thigh_dir = thigh_vec.normalized() if thigh_vec.length > 0.001 else Vector((0, 0, 1))

    pole_dir_initial = char_forward_local - char_forward_local.project(thigh_dir)
    if pole_dir_initial.length < 0.01:
        char_up_local = Vector((0.0, 0.0, 1.0))
        pole_dir_initial = char_up_local - char_up_local.project(thigh_dir)
        if pole_dir_initial.length < 0.01:
            char_right_local = Vector((1.0, 0.0, 0.0))
            pole_dir_initial = char_right_local - char_right_local.project(thigh_dir)

    if pole_dir_initial.length > 0.001:
        pole_dir_initial.normalize()
    else:
        pole_dir_initial = Vector((0.0, -1.0, 0.0))

    pole_distance_factor = 0.75
    actual_pole_distance = leg_bone.length * pole_distance_factor
    if actual_pole_distance < 0.1:
        actual_pole_distance = 0.5

    knee_ik_bone = armature.edit_bones.new(f"KneeIK{side}")
    knee_ik_bone.head = fk_knee_pos + pole_dir_initial * actual_pole_distance
    pole_bone_length = max(leg_bone.length * 0.2, 0.2) * ik_scale_factor
    knee_ik_bone.tail = knee_ik_bone.head + pole_dir_initial * pole_bone_length
    if pole_dir_initial.length > 0.001:
        knee_ik_bone.align_roll(pole_dir_initial)
    else:
        knee_ik_bone.roll = 0.0

    foot_ik_bone = armature.edit_bones.new(f"FootIK{side}")
    foot_ik_bone.head = knee_bone.tail

    foot_ik_length = foot_bone.length if foot_bone.length > 0.01 else leg_bone.length * 0.3
    foot_ik_length *= ik_scale_factor
    if foot_ik_length < 0.1:
        foot_ik_length = 0.3

    if foot_bone.length > 0.001:
        foot_fk_dir = (foot_bone.tail - foot_bone.head).normalized()
    else:
        foot_fk_dir = Vector((0, 0, -1))
    foot_ik_bone.tail = foot_ik_bone.head + foot_fk_dir * foot_ik_length
    foot_ik_bone.roll = math.radians(90.0)

    return knee_ik_bone, foot_ik_bone
