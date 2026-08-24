import bpy
import mathutils
from mathutils import Vector
import math
from . import fk_to_ik

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
                
            # NOTE: Removed base bone modifications to preserve armature integrity
            # The original code modified leg_bone.tail and knee_bone.head which caused armature deformation

            # --- New Pole Target Placement Logic ---
            fk_knee_pos = knee_bone.head # Position of the FK shin bone's head (the knee joint)
            
            # Armature's local -Y axis (character forward in armature space)
            char_forward_local = Vector((0.0, -1.0, 0.0))
            
            # Thigh bone vector and normalized direction (in armature space)
            thigh_vec = leg_bone.tail - leg_bone.head 
            thigh_dir = thigh_vec.normalized() if thigh_vec.length > 0.001 else Vector((0,0,1)) # Fallback to Z-up

            # Calculate pole direction: char_forward_local projected to be orthogonal to thigh_dir
            pole_dir_initial = char_forward_local - char_forward_local.project(thigh_dir)
            
            if pole_dir_initial.length < 0.01: # If char forward is (anti-)aligned with thigh
                # Try armature's local Z-axis (character up)
                char_up_local = Vector((0.0, 0.0, 1.0))
                pole_dir_initial = char_up_local - char_up_local.project(thigh_dir)
                if pole_dir_initial.length < 0.01: # Fallback to armature's local X-axis
                    char_right_local = Vector((1.0, 0.0, 0.0))
                    pole_dir_initial = char_right_local - char_right_local.project(thigh_dir)

            if pole_dir_initial.length > 0.001:
                pole_dir_initial.normalize()
            else: 
                # Ultimate fallback if all are aligned (very unlikely unless thigh is zero length)
                pole_dir_initial = Vector((0.0, -1.0, 0.0)) 

            pole_distance_factor = 0.75 # Distance from knee, as a factor of thigh length
            actual_pole_distance = leg_bone.length * pole_distance_factor
            if actual_pole_distance < 0.1: actual_pole_distance = 0.5 # Min distance

            knee_ik_bone = armature.edit_bones.new("KneeIK"+i)
            knee_ik_bone.head = fk_knee_pos + pole_dir_initial * actual_pole_distance
            # Make the pole bone a reasonable length, e.g., 20% of thigh length or a fixed small amount
            pole_bone_length = max(leg_bone.length * 0.2, 0.2) * ik_scale_factor  # Now using scale factor 
            knee_ik_bone.tail = knee_ik_bone.head + pole_dir_initial * pole_bone_length
            
            # Align roll of the pole target bone
            if pole_dir_initial.length > 0.001:
                knee_ik_bone.align_roll(pole_dir_initial)
            else:
                knee_ik_bone.roll = 0.0

            foot_ik_bone = armature.edit_bones.new("FootIK"+i)
            foot_ik_bone.head = knee_bone.tail # FK Shin bone's tail (ankle position)
            
            # Make the foot IK bone larger
            foot_ik_length = foot_bone.length if foot_bone.length > 0.01 else leg_bone.length * 0.3
            foot_ik_length *= ik_scale_factor  # Apply scale factor
            
            if foot_ik_length < 0.1: foot_ik_length = 0.3
            
            # Align FootIK with the Foot FK bone
            foot_fk_dir = (foot_bone.tail - foot_bone.head).normalized() if foot_bone.length > 0.001 else Vector((0,0,-1))
            foot_ik_bone.tail = foot_ik_bone.head + foot_fk_dir * foot_ik_length
            
            foot_ik_bone.roll = math.radians(90.0)

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
        ik_bone_collection_name = "FootIK Bones"
        if ik_bone_collection_name not in armature.collections:
            ik_bone_collection = armature.collections.new(name=ik_bone_collection_name)
        else:
            ik_bone_collection = armature.collections[ik_bone_collection_name]

        for bone in armature.bones:
            if "IK" in bone.name:
                ik_bone_collection.assign(bone)

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
            fk_to_ik.invoke_position_match_dialog()
            
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