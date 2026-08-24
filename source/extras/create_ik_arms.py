import bpy
import mathutils
from mathutils import Vector
import math
from . import fk_to_ik
from ..blender_compat import assign_bone_to_collection, ensure_bone_collection

class SUB_OP_create_arm_ik_operator(bpy.types.Operator):
    """Generate Arm and Hand IK Bones with Constraints and Coloring"""
    bl_idname = "sub.create_arm_ik"
    bl_label = "Create Arm IK Bones"
    bl_options = {'REGISTER', 'UNDO'}
    
    match_position: bpy.props.BoolProperty(
        name="Match IK to FK Position",
        description="Match IK bones position to FK bones after creation",
        default=True
    )

    @classmethod
    def poll(cls, context):
        return True  # Always show the button

    def execute(self, context):
        armature_object = context.object

        if not armature_object or armature_object.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature selected")
            return {'CANCELLED'}

        armature = armature_object.data
        bpy.ops.object.mode_set(mode="EDIT")
        
        side = ("L", "R")
        # We'll use a larger size for IK bones for better visibility
        ik_scale_factor = 1.5  # IK bones will be 1.5x larger
        
        for i in side:
            shoulder_bone = armature.edit_bones.get("Shoulder"+i)
            arm_bone = armature.edit_bones.get("Arm"+i)
            hand_bone = armature.edit_bones.get("Hand"+i)
            
            if not arm_bone or not hand_bone:
                continue
                
            # NOTE: Removed base bone modifications to preserve armature integrity
            # The original code modified shoulder_bone.tail and arm_bone.head which caused armature deformation
            
            arm_ik_bone = armature.edit_bones.new("ArmIK" + i)
            # Position the pole target at a fixed distance behind
            arm_ik_bone.head = Vector((arm_bone.head.x, 4.0, arm_bone.head.z))
            arm_ik_bone.tail = Vector((arm_bone.head.x, 5.5, arm_bone.head.z))
            
            # Scale the pole target bone to be larger for better visibility
            arm_bone_length = (arm_bone.tail - arm_bone.head).length
            if arm_bone_length > 0.001:
                pole_length = arm_bone_length * 0.2 * ik_scale_factor
                pole_dir = (arm_ik_bone.tail - arm_ik_bone.head).normalized()
                arm_ik_bone.tail = arm_ik_bone.head + pole_dir * pole_length
            
            hand_ik_bone = armature.edit_bones.new("HandIK" + i)
            hand_ik_bone.head = arm_bone.tail
            
            # Make the hand IK bone larger
            hand_ik_length = (hand_bone.tail - hand_bone.head).length
            if hand_ik_length < 0.1:
                hand_ik_length = 0.5
            hand_ik_length *= ik_scale_factor
            
            hand_ik_bone.tail = Vector((arm_bone.tail.x, arm_bone.tail.y, arm_bone.tail.z + hand_ik_length))
            hand_ik_bone.roll = math.radians(0.0)  # Set explicit roll value
        
        bpy.ops.object.mode_set(mode="POSE")
        
        # Store the original position data for later precise matching
        fk_positions = {}
        for i in side:
            arm_bone = armature_object.pose.bones.get("Arm" + i)
            hand_bone = armature_object.pose.bones.get("Hand" + i)
            
            if hand_bone:
                # Store world space hand position and rotation
                fk_positions[f"hand_matrix_{i}"] = hand_bone.matrix.copy()
                fk_positions[f"hand_loc_{i}"] = hand_bone.location.copy()
                fk_positions[f"hand_rot_{i}"] = hand_bone.rotation_quaternion.copy() if hand_bone.rotation_mode == 'QUATERNION' else hand_bone.rotation_euler.copy()
            
            if arm_bone:
                # Store arm position for pole angle calculations
                fk_positions[f"arm_matrix_{i}"] = arm_bone.matrix.copy()
        
        for i in side:
            arm_pose = armature_object.pose.bones.get("Arm" + i)
            hand_pose = armature_object.pose.bones.get("Hand" + i)
            
            if not arm_pose or not hand_pose:
                continue
            
            arm_ik_constraint = arm_pose.constraints.new("IK")
            arm_ik_constraint.target = armature_object
            arm_ik_constraint.subtarget = "HandIK" + i
            arm_ik_constraint.pole_target = armature_object
            arm_ik_constraint.pole_subtarget = "ArmIK" + i
            arm_ik_constraint.chain_count = 2

            if i == "L":
                arm_ik_constraint.pole_angle = math.radians(-90)
            
            hand_rot_constraint = hand_pose.constraints.new("COPY_ROTATION")
            hand_rot_constraint.target = armature_object
            hand_rot_constraint.subtarget = "HandIK" + i
        
        bpy.ops.object.mode_set(mode="POSE")
        
        for bone in armature_object.pose.bones:
            if "IK" in bone.name:
                bone.color.palette = 'THEME01'
        
        ik_bone_collection = ensure_bone_collection(armature, "ArmsIK Bones")
        
        for bone in armature.bones:
            if "IK" in bone.name:
                assign_bone_to_collection(ik_bone_collection, bone)
        
        # Accurately position the IK bones to match FK
        for i in side:
            hand_ik_bone = armature_object.pose.bones.get("HandIK" + i)
            if hand_ik_bone and f"hand_matrix_{i}" in fk_positions:
                # Exact position matching
                hand_ik_bone.matrix = fk_positions[f"hand_matrix_{i}"]
                
                # Ensure the exact transform is applied
                if f"hand_loc_{i}" in fk_positions:
                    hand_ik_bone.location = fk_positions[f"hand_loc_{i}"]
                
                hand_rot = fk_positions.get(f"hand_rot_{i}")
                if hand_rot:
                    if isinstance(hand_rot, mathutils.Quaternion):
                        hand_ik_bone.rotation_quaternion = hand_rot
                    else:
                        hand_ik_bone.rotation_euler = hand_rot
        
        self.report({'INFO'}, "IK bones created, colored red, and assigned to 'IK Bones' collection.")
        
        # Prompt for position matching if requested
        if self.match_position:
            fk_to_ik.invoke_position_match_dialog(cleanup_mode='ARMS')
            
        return {'FINISHED'}
    
    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "match_position")

class SUB_PT_arm_ik_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport"""
    bl_label = "IK Bone Generator"
    bl_idname = "SUB_PT_arm_ik_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'IK Bones'

    def draw(self, context):
        layout = self.layout
        layout.operator("sub.create_arm_ik", text="Generate Arm IK Bones")


def register():
    bpy.utils.register_class(SUB_OP_create_arm_ik_operator)
    bpy.utils.register_class(SUB_PT_arm_ik_panel)

def unregister():
    bpy.utils.unregister_class(SUB_OP_create_arm_ik_operator)
    bpy.utils.unregister_class(SUB_PT_arm_ik_panel)

if __name__ == "__main__":
    register()