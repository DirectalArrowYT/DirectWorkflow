import bpy

from ..anim.fcurve_compat import get_all_action_fcurves, remove_fcurve
from ..blender_compat import set_pose_bone_select


def get_ik_bone_names(armature_data):
    return [bone.name for bone in armature_data.bones if "IK" in bone.name]


def collect_fk_bone_names(armature_object, leg_bone_map=None):
    """Return FK bones whose IK constraints should be cleared after baking."""
    names = set()
    if leg_bone_map:
        for side_roles in leg_bone_map.values():
            names.update(value for value in side_roles.values() if value)
    else:
        for side in ("L", "R"):
            for part in ("Leg", "Knee", "Foot", "Arm", "Hand"):
                names.add(f"{part}{side}")

    for pose_bone in armature_object.pose.bones:
        for constraint in pose_bone.constraints:
            if constraint.type not in {"IK", "COPY_ROTATION"}:
                continue
            subtarget = constraint.subtarget or ""
            if "IK" in subtarget:
                names.add(pose_bone.name)

    return [name for name in names if name in armature_object.data.bones]


def skip_ik_visual_bake(name):
    """Finger / slider / extra control bones must not be visual-baked with IK."""
    if not name:
        return False
    if name.startswith("Finger") or name.startswith("BL_"):
        return True
    return False


def bake_action_visual(context, armature_object, frame_start, frame_end):
    bpy.ops.object.mode_set(mode="POSE")
    for bone in armature_object.pose.bones:
        set_pose_bone_select(bone, not skip_ik_visual_bake(bone.name))

    bpy.ops.nla.bake(
        frame_start=frame_start,
        frame_end=frame_end,
        visual_keying=True,
        clear_constraints=False,
        use_current_action=True,
        bake_types={"POSE"},
    )


def remove_constraints_from_bones(armature_object, bone_names):
    for bone_name in bone_names:
        bone = armature_object.pose.bones.get(bone_name)
        if not bone:
            continue
        while bone.constraints:
            bone.constraints.remove(bone.constraints[0])


def remove_ik_fcurves_from_action(action, ik_bone_names):
    if not action or not ik_bone_names:
        return 0

    fcurves = get_all_action_fcurves(action)
    to_remove = []
    for fcurve in fcurves:
        for ik_name in ik_bone_names:
            if f'pose.bones["{ik_name}"]' in fcurve.data_path:
                to_remove.append(fcurve)
                break

    for fcurve in to_remove:
        remove_fcurve(action, fcurve)
    return len(to_remove)


def delete_ik_bones_from_armature(armature_object):
    ik_bone_names = get_ik_bone_names(armature_object.data)
    if not ik_bone_names:
        return []

    bpy.ops.object.mode_set(mode="EDIT")
    for bone_name in ik_bone_names:
        edit_bone = armature_object.data.edit_bones.get(bone_name)
        if edit_bone:
            armature_object.data.edit_bones.remove(edit_bone)
    bpy.ops.object.mode_set(mode="OBJECT")
    return ik_bone_names


def bake_and_clean_current_action(context, armature_object, leg_bone_map=None, remove_ik_rig=True):
    """Bake the current action, optionally strip the IK rig from the armature."""
    ik_bone_names = get_ik_bone_names(armature_object.data)
    fk_bone_names = collect_fk_bone_names(armature_object, leg_bone_map)

    bake_action_visual(
        context,
        armature_object,
        context.scene.frame_start,
        context.scene.frame_end,
    )

    removed_fcurves = 0
    if armature_object.animation_data and armature_object.animation_data.action:
        removed_fcurves = remove_ik_fcurves_from_action(
            armature_object.animation_data.action,
            ik_bone_names,
        )
    from .create_animation_rig import _remove_ik_fk_switch_keys
    _remove_ik_fk_switch_keys(armature_object)

    if remove_ik_rig:
        remove_constraints_from_bones(armature_object, fk_bone_names)
        delete_ik_bones_from_armature(armature_object)

    return removed_fcurves, ik_bone_names


class SUB_OP_apply_ik_animation_operator(bpy.types.Operator):
    """Bake IK Animation to Original Bones and Remove IK Bones"""
    bl_idname = "sub.apply_ik_animation"
    bl_label = "Apply IK Animation"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_object = context.object

        if not armature_object or armature_object.type != "ARMATURE":
            self.report({"ERROR"}, "No armature selected. Please select an armature in Object Mode.")
            return {"CANCELLED"}

        removed_fcurves, _ik_bone_names = bake_and_clean_current_action(
            context,
            armature_object,
            remove_ik_rig=True,
        )

        if removed_fcurves:
            self.report({"INFO"}, f"Removed {removed_fcurves} IK keyframe channels.")
        self.report({"INFO"}, "Animation baked to original bones and IK bones removed.")
        return {"FINISHED"}


class SUB_PT_apply_ik_animation_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport"""
    bl_label = "Apply IK Animation"
    bl_idname = "SUB_PT_apply_ik_animation_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IK Bones"

    def draw(self, context):
        layout = self.layout
        layout.operator("sub.apply_ik_animation", text="Bake & Remove IK")


def register():
    bpy.utils.register_class(SUB_OP_apply_ik_animation_operator)
    bpy.utils.register_class(SUB_PT_apply_ik_animation_panel)


def unregister():
    bpy.utils.unregister_class(SUB_OP_apply_ik_animation_operator)
    bpy.utils.unregister_class(SUB_PT_apply_ik_animation_panel)


if __name__ == "__main__":
    register()
