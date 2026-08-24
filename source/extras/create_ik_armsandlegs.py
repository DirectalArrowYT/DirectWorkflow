import bpy
import mathutils
from mathutils import Vector
import math  # Import the math module
from . import fk_to_ik

class SUB_OP_create_ik_bones_operator(bpy.types.Operator):
    """Generate IK Bones for Arms and Legs with Automatic Setup"""
    bl_idname = "sub.create_ik_bones"
    bl_label = "Create IK Bones Arms + Legs"
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
        side = ("L", "R")
        
        # We'll use a larger size for IK bones for better visibility
        ik_scale_factor = 1.5  # IK bones will be 1.5x larger
        
        bpy.ops.object.mode_set(mode="EDIT")
        
        for i in side:
            # Get edit bones
            leg_bone = armature.edit_bones.get("Leg"+i)
            knee_bone = armature.edit_bones.get("Knee"+i)
            foot_bone = armature.edit_bones.get("Foot"+i)
            shoulder_bone = armature.edit_bones.get("Shoulder"+i)
            arm_bone = armature.edit_bones.get("Arm"+i)
            hand_bone = armature.edit_bones.get("Hand"+i)
            
            # Skip if bones don't exist
            if not all([bone for bone in [leg_bone, knee_bone, foot_bone, arm_bone, hand_bone]]):
                continue
            
            # NOTE: Removed base bone modifications to preserve armature integrity
            # The original code modified base bone positions which caused armature deformation
            
            # Create knee IK pole target
            knee_ik_bone = armature.edit_bones.new("KneeIK"+i)
            knee_ik_bone.head = Vector((knee_bone.head.x, -4.0, knee_bone.head.z))
            knee_ik_bone.tail = Vector((knee_bone.head.x, -5.5, knee_bone.head.z))
            
            # Scale the knee pole target bone for better visibility
            leg_bone_length = (leg_bone.tail - leg_bone.head).length
            if leg_bone_length > 0.001:
                pole_length = leg_bone_length * 0.2 * ik_scale_factor
                pole_dir = (knee_ik_bone.tail - knee_ik_bone.head).normalized()
                knee_ik_bone.tail = knee_ik_bone.head + pole_dir * pole_length
            
            # Create arm IK pole target
            arm_ik_bone = armature.edit_bones.new("ArmIK"+i)
            arm_ik_bone.head = Vector((arm_bone.head.x, 4.0, arm_bone.head.z))
            arm_ik_bone.tail = Vector((arm_bone.head.x, 5.5, arm_bone.head.z))
            
            # Scale the arm pole target bone for better visibility
            arm_bone_length = (arm_bone.tail - arm_bone.head).length
            if arm_bone_length > 0.001:
                pole_length = arm_bone_length * 0.2 * ik_scale_factor
                pole_dir = (arm_ik_bone.tail - arm_ik_bone.head).normalized()
                arm_ik_bone.tail = arm_ik_bone.head + pole_dir * pole_length
            
            # Create foot IK target
            foot_ik_bone = armature.edit_bones.new("FootIK"+i)
            foot_ik_bone.head = knee_bone.tail
            
            # Make the foot IK bone larger
            foot_ik_length = foot_bone.length if foot_bone.length > 0.01 else leg_bone.length * 0.3
            foot_ik_length *= ik_scale_factor
            
            foot_ik_bone.tail = Vector((knee_bone.tail.x, knee_bone.tail.y, knee_bone.tail.z - foot_ik_length))
            foot_ik_bone.roll = math.radians(90.0)
            
            # Create hand IK target
            hand_ik_bone = armature.edit_bones.new("HandIK"+i)
            hand_ik_bone.head = arm_bone.tail
            
            # Make the hand IK bone larger
            hand_ik_length = (hand_bone.tail - hand_bone.head).length
            if hand_ik_length < 0.1:
                hand_ik_length = 0.5
            hand_ik_length *= ik_scale_factor
            
            hand_ik_bone.tail = Vector((arm_bone.tail.x, arm_bone.tail.y, arm_bone.tail.z + hand_ik_length))
            hand_ik_bone.roll = math.radians(0.0)
        
        bpy.ops.object.mode_set(mode="POSE")
        
        # Store the original position data for later precise matching
        fk_positions = {}
        for i in side:
            # Store foot position data
            foot_bone = armature_object.pose.bones.get("Foot"+i)
            if foot_bone:
                fk_positions[f"foot_matrix_{i}"] = foot_bone.matrix.copy()
                fk_positions[f"foot_loc_{i}"] = foot_bone.location.copy()
                fk_positions[f"foot_rot_{i}"] = foot_bone.rotation_quaternion.copy() if foot_bone.rotation_mode == 'QUATERNION' else foot_bone.rotation_euler.copy()
            
            # Store knee position for pole angle calculations
            knee_bone = armature_object.pose.bones.get("Knee"+i)
            if knee_bone:
                fk_positions[f"knee_matrix_{i}"] = knee_bone.matrix.copy()
            
            # Store hand position data
            hand_bone = armature_object.pose.bones.get("Hand"+i)
            if hand_bone:
                fk_positions[f"hand_matrix_{i}"] = hand_bone.matrix.copy()
                fk_positions[f"hand_loc_{i}"] = hand_bone.location.copy()
                fk_positions[f"hand_rot_{i}"] = hand_bone.rotation_quaternion.copy() if hand_bone.rotation_mode == 'QUATERNION' else hand_bone.rotation_euler.copy()
            
            # Store arm position for pole angle calculations
            arm_bone = armature_object.pose.bones.get("Arm"+i)
            if arm_bone:
                fk_positions[f"arm_matrix_{i}"] = arm_bone.matrix.copy()
        
        for i in side:
            # Get pose bones
            knee_pose = armature_object.pose.bones.get("Knee"+i)
            arm_pose = armature_object.pose.bones.get("Arm"+i)
            foot_pose = armature_object.pose.bones.get("Foot"+i)
            hand_pose = armature_object.pose.bones.get("Hand"+i)
            
            # Setup knee IK constraint
            if knee_pose:
                knee_ik_constraint = knee_pose.constraints.new("IK")
                knee_ik_constraint.target = armature_object
                knee_ik_constraint.subtarget = "FootIK"+i
                knee_ik_constraint.pole_target = armature_object
                knee_ik_constraint.pole_subtarget = "KneeIK"+i
                knee_ik_constraint.chain_count = 2
                knee_ik_constraint.pole_angle = 0.0  # Will be calculated properly later
            
            # Setup arm IK constraint
            if arm_pose:
                arm_ik_constraint = arm_pose.constraints.new("IK")
                arm_ik_constraint.target = armature_object
                arm_ik_constraint.subtarget = "HandIK"+i
                arm_ik_constraint.pole_target = armature_object
                arm_ik_constraint.pole_subtarget = "ArmIK"+i
                arm_ik_constraint.chain_count = 2
                if i == "L":
                    arm_ik_constraint.pole_angle = math.radians(-90)
            
            # Setup foot copy rotation constraint
            if foot_pose:
                foot_rot_constraint = foot_pose.constraints.new("COPY_ROTATION")
                foot_rot_constraint.target = armature_object
                foot_rot_constraint.subtarget = "FootIK"+i
            
            # Setup hand copy rotation constraint
            if hand_pose:
                hand_rot_constraint = hand_pose.constraints.new("COPY_ROTATION")
                hand_rot_constraint.target = armature_object
                hand_rot_constraint.subtarget = "HandIK"+i
        
        # Color IK bones red
        bpy.ops.object.mode_set(mode="POSE")
        for bone in armature.bones:
            if "IK" in bone.name:
                bone.color.palette = 'THEME01'
        
        # Create and assign bones to IK collection
        ik_bone_collection_name = "IK Bones"
        if ik_bone_collection_name not in armature.collections:
            ik_bone_collection = armature.collections.new(name=ik_bone_collection_name)
        else:
            ik_bone_collection = armature.collections[ik_bone_collection_name]

        for bone in armature.bones:
            if "IK" in bone.name:
                ik_bone_collection.assign(bone)
        
        # Accurately position the IK bones to match FK
        for i in side:
            # Position foot IK bones
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
            
            # Position hand IK bones
            hand_ik_bone = armature_object.pose.bones.get("HandIK"+i)
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
        
        self.report({'INFO'}, "IK bones successfully created, aligned, colored red, and added to the 'IK Bones' collection.")
        
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

class SUB_PT_ik_bones_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport"""
    bl_label = "IK Bone Generator"
    bl_idname = "SUB_PT_ik_bones_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'IK Bones'

    def draw(self, context):
        layout = self.layout
        layout.operator("sub.create_ik_bones", text="Generate IK Bones")


def register():
    bpy.utils.register_class(SUB_OP_create_ik_bones_operator)
    bpy.utils.register_class(SUB_PT_ik_bones_panel)

def unregister():
    bpy.utils.unregister_class(SUB_OP_create_ik_bones_operator)
    bpy.utils.unregister_class(SUB_PT_ik_bones_panel)

if __name__ == "__main__":
    register()