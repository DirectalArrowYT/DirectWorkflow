import bpy
import mathutils
from mathutils import Vector
import math
from . import fk_to_ik
from .ik_leg_placement import place_leg_ik_edit_bones
from ..blender_compat import assign_bone_to_collection, ensure_bone_collection

class SUB_OP_create_foot_ik_operator(bpy.types.Operator):
    """Generate Foot and Knee IK Bones with Constraints"""
    bl_idname = "sub.create_foot_ik"
    bl_label = "Create Foot IK Bones"
    bl_options = {'REGISTER', 'UNDO'}
    
    match_position: bpy.props.BoolProperty(
        name="Match IK to FK Position",
        description="Match IK bones position to FK bones after creation",
        default=True
    )

    def execute(self, context):
        armature_object = context.object
        
        if not armature_object or armature_object.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected. Please select an armature in Object Mode.")
            return {'CANCELLED'}

        armature = armature_object.data
        
        bpy.ops.object.mode_set(mode="EDIT")
        side = ("L", "R")
        # We'll use a larger size for IK bones for better visibility
        ik_scale_factor = 1.5  # IK bones will be 1.5x larger

        # Add small offsets to help with IK alignment
        for i in side:
            leg_bone = armature.edit_bones.get("Leg"+i)
            knee_bone = armature.edit_bones.get("Knee"+i) # This is the FK shin bone
            foot_bone = armature.edit_bones.get("Foot"+i)
            
            if not knee_bone or not foot_bone or not leg_bone:
                self.report({'WARNING'}, f"Skipping {i} leg due to missing FK bones (Leg, Knee, or Foot).")
                continue

            place_leg_ik_edit_bones(armature, i, leg_bone, knee_bone, foot_bone, ik_scale_factor)

        bpy.ops.object.mode_set(mode="POSE")

        # Store the original position data for later precise matching
        fk_positions = {}
        for i in side:
            foot_bone = armature_object.pose.bones.get("Foot"+i)
            knee_bone = armature_object.pose.bones.get("Knee"+i)
            
            if foot_bone:
                # Store world space foot position and rotation
                fk_positions[f"foot_matrix_{i}"] = foot_bone.matrix.copy()
                fk_positions[f"foot_loc_{i}"] = foot_bone.location.copy()
                fk_positions[f"foot_rot_{i}"] = foot_bone.rotation_quaternion.copy() if foot_bone.rotation_mode == 'QUATERNION' else foot_bone.rotation_euler.copy()
            
            if knee_bone:
                # Store knee position for pole angle calculations
                fk_positions[f"knee_matrix_{i}"] = knee_bone.matrix.copy()

        # Create constraints
        for i in side:
            knee_pose = armature_object.pose.bones.get("Knee"+i)
            foot_pose = armature_object.pose.bones.get("Foot"+i)
            
            # Check if bones and target bones exist before constraining
            knee_ik_target_bone = armature_object.pose.bones.get("FootIK"+i)
            knee_pole_target_bone = armature_object.pose.bones.get("KneeIK"+i)

            if not knee_pose or not foot_pose or not knee_ik_target_bone or not knee_pole_target_bone:
                self.report({'WARNING'}, f"Skipping constraints for {i} leg due to missing pose bones or IK target bones.")
                continue

            knee_ik_constraint = knee_pose.constraints.new("IK")
            knee_ik_constraint.target = armature_object
            knee_ik_constraint.subtarget = knee_ik_target_bone.name
            knee_ik_constraint.pole_target = armature_object
            knee_ik_constraint.pole_subtarget = knee_pole_target_bone.name
            knee_ik_constraint.chain_count = 2
            knee_ik_constraint.pole_angle = 0.0  # Will be calculated properly later

            foot_rot_constraint = foot_pose.constraints.new("COPY_ROTATION")
            foot_rot_constraint.target = armature_object
            foot_rot_constraint.subtarget = knee_ik_target_bone.name

        # Apply red color to all IK bones
        bpy.ops.object.mode_set(mode="POSE")
        for bone in armature.bones:
            if "IK" in bone.name:
                bone.color.palette = 'THEME01'

        # Create and assign bones to the IK Bones collection
        ik_bone_collection = ensure_bone_collection(armature, "FootIK Bones")

        for bone in armature.bones:
            if "IK" in bone.name:
                assign_bone_to_collection(ik_bone_collection, bone)

        # Accurately position the IK bones to match FK
        for i in side:
            foot_ik_bone = armature_object.pose.bones.get("FootIK"+i)
            if foot_ik_bone and f"foot_matrix_{i}" in fk_positions:
                # Exact position matching
                foot_ik_bone.matrix = fk_positions[f"foot_matrix_{i}"]
                
                # Ensure the exact transform is applied
                if f"foot_loc_{i}" in fk_positions:
                    foot_ik_bone.location = fk_positions[f"foot_loc_{i}"]
                
                foot_rot = fk_positions.get(f"foot_rot_{i}")
                if foot_rot:
                    if isinstance(foot_rot, mathutils.Quaternion):
                        foot_ik_bone.rotation_quaternion = foot_rot
                    else:
                        foot_ik_bone.rotation_euler = foot_rot
        
        self.report({'INFO'}, "Foot and knee IK bones successfully created and assigned.")
        
        # Prompt for position matching if requested
        if self.match_position:
            fk_to_ik.invoke_position_match_dialog(cleanup_mode='LEGS')
            
        return {'FINISHED'}
    
    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "match_position")


class SUB_PT_foot_ik_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport"""
    bl_label = "Foot IK Bone Generator"
    bl_idname = "SUB_PT_foot_ik_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'IK Bones'

    def draw(self, context):
        layout = self.layout
        layout.operator("sub.create_foot_ik", text="Generate Foot IK Bones")


def register():
    bpy.utils.register_class(SUB_OP_create_foot_ik_operator)
    bpy.utils.register_class(SUB_PT_foot_ik_panel)


def unregister():
    bpy.utils.unregister_class(SUB_OP_create_foot_ik_operator)
    bpy.utils.unregister_class(SUB_PT_foot_ik_panel)


if __name__ == "__main__":
    register()