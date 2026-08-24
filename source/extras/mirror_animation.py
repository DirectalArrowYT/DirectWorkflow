import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty
import logging
import mathutils

# Set up logging
logger = logging.getLogger(__name__)

NEGATE_DATA_PATH_XAXIS = (
    ('location', 0),
    ('rotation_quaternion', 2),
    ('rotation_quaternion', 3),
    ('rotation_euler', 2),
    ('rotation_euler', 1), # armature space z axis is y
)

NEGATE_DATA_PATH_YAXIS = (
    ('location', 2), # in armature space y axis is z
    ('rotation_quaternion', 1),  # Negate X component for Y-axis mirror (index 1 = x in [w,x,y,z])
    ('rotation_quaternion', 2),  # Negate Y component for Y-axis mirror (index 2 = y in [w,x,y,z])
    ('rotation_euler', 0),
    ('rotation_euler', 1), # armature space z axis is y
)

NEGATE_DATA_PATH_ZAXIS = (
    ('location', 1),
    ('rotation_quaternion', 1),
    ('rotation_quaternion', 3),
    ('rotation_euler', 0),
    ('rotation_euler', 2),
)


#########################################################################################
# Create Mirror Map
#########################################################################################


def difference(a, b):
    """Find difference in two strings to check if they are left and right"""
    import os
    common_prefix = os.path.commonprefix((a,b))
    common_suffix = os.path.commonprefix((a[::-1],b[::-1]))[::-1]
    
    # TODO: add checks incase of 'alc' and 'arc'
    #       check if difference is at start or end
    #       check if surrounds with punctuation or camelcase
    return a[len(common_prefix) : len(a)-len(common_suffix)], b[len(common_prefix) : len(b)-len(common_suffix)]

def lower_tuple(wl):
    return tuple(w.lower() for w in wl)

def create_mirror_map(names, patterns=None):
    
    mirror_map = {}

    if patterns == None:
        # Insert more default pattern if necessary - add Smash Ultimate specific patterns
        patterns = (
            ('l', 'r'), 
            ('left', 'right'),
            ('L', 'R'),  # Add capitalized L/R for Smash Ultimate
            ('Left', 'Right')  # Add capitalized words
        )
    
    # lower case and remove difference eg. remove 't' from 'Left', 'Right'
    patterns = tuple(lower_tuple(difference(*pattern)) for pattern in patterns)
    rpatterns = tuple(pattern[::-1] for pattern in patterns)

    # First pass: exact pattern matching for clear L/R pairs
    for lname in names:
        for name in names:
            if lname == name:
                continue  # Skip same name
            if lower_tuple(difference(lname, name)) in (*patterns, *rpatterns):
                rname = name
                mirror_map[lname] = rname
                break
    
    # Second pass: handle Smash Ultimate specific naming patterns
    # Look for bones ending in L/R that weren't caught by the first pass
    unmatched_names = [name for name in names if name not in mirror_map]
    for bone_name in unmatched_names:
        if bone_name.endswith('L'):
            # Look for corresponding R bone
            r_bone_name = bone_name[:-1] + 'R'
            if r_bone_name in names:
                mirror_map[bone_name] = r_bone_name
                mirror_map[r_bone_name] = bone_name
        elif bone_name.endswith('R'):
            # Look for corresponding L bone  
            l_bone_name = bone_name[:-1] + 'L'
            if l_bone_name in names:
                mirror_map[bone_name] = l_bone_name
                mirror_map[l_bone_name] = bone_name
    
    # Third pass: center bones (bones without L/R pairs) should map to themselves
    # This ensures they still get processed with negations applied (matching Idle Pose Library behavior)
    for name in names:
        if name not in mirror_map:
            mirror_map[name] = name
    
    return mirror_map


#########################################################################################
# Mirror Action
#########################################################################################


def negate_fcurve(fcurve, only_active_frame=False, current_frame=None):
    for k in fcurve.keyframe_points:
        if only_active_frame and current_frame is not None:
            # Only negate keyframes at the current frame
            if abs(k.co[0] - current_frame) < 0.001:  # Small tolerance for floating point comparison
                k.co[1] = -k.co[1]
                k.handle_left[1] = -k.handle_left[1]
                k.handle_right[1] = -k.handle_right[1]
        else:
            # Negate all keyframes
            k.co[1] = -k.co[1]
            k.handle_left[1] = -k.handle_left[1]
            k.handle_right[1] = -k.handle_right[1]

def apply_global_mirror_transform(value, axis, object_matrix):
    """Apply global mirroring transformation considering object's world matrix"""
    if object_matrix is None:
        return -value
    
    # Create a vector for the value
    if axis == 'X':
        vec = mathutils.Vector((value, 0, 0))
    elif axis == 'Y':
        vec = mathutils.Vector((0, value, 0))
    elif axis == 'Z':
        vec = mathutils.Vector((0, 0, value))
    else:
        return -value
    
    # Transform to world space
    world_vec = object_matrix @ vec
    
    # Mirror in world space
    if axis == 'X':
        world_vec.x = -world_vec.x
    elif axis == 'Y':
        world_vec.y = -world_vec.y
    elif axis == 'Z':
        world_vec.z = -world_vec.z
    
    # Transform back to local space
    local_vec = object_matrix.inverted() @ world_vec
    
    # Return the appropriate component
    if axis == 'X':
        return local_vec.x
    elif axis == 'Y':
        return local_vec.y
    elif axis == 'Z':
        return local_vec.z
    
    return -value

def should_exclude_bone_from_mirroring(bone_name, armature=None, include_fingers=True):
    """Check if a bone should be excluded from mirroring (like facial, hair bones, and optionally finger bones)"""
    # Exclude facial/hair bones (S_ prefix)
    if bone_name.startswith('S_'):
        return True
    
    # Exclude bones with facial keywords
    facial_keywords = ['brow', 'lip', 'eye', 'nose', 'cheek', 'jaw', 'mouth']
    bone_name_lower = bone_name.lower()
    if any(keyword in bone_name_lower for keyword in facial_keywords):
        return True
    
    # Exclude finger bones only if include_fingers is False
    if not include_fingers:
        # Match patterns like FingerL11, FingerR23, Finger10, etc.
        if bone_name.startswith('Finger'):
            return True
    
    # Exclude Face bone and its children
    if armature and armature.type == 'ARMATURE':
        if bone_name == 'Face':
            return True
        # Check if this bone is a child of Face
        if bone_name in armature.pose.bones:
            bone = armature.pose.bones[bone_name]
            if bone.parent and bone.parent.name == 'Face':
                return True
    
    return False

def mirror_action(act, axis='X', selected_bones_only=False, context=None, only_active_frame=False, mirror_space='LOCAL', include_fingers=True):
    
    if not (act and act.fcurves):
        print("No Keyframes")
        return
    
    # Get current frame if only_active_frame is enabled
    current_frame = None
    if only_active_frame and context:
        current_frame = context.scene.frame_current
    
    # Get selected bone names if filtering is enabled
    selected_bone_names = set()
    if selected_bones_only and context and context.active_object and context.active_object.type == 'ARMATURE':
        armature_obj = context.active_object
        # Try multiple methods to get selected bones for reliability
        # Method 1: context.selected_pose_bones (works in pose mode with proper context)
        if context.selected_pose_bones:
            selected_bone_names = {bone.name for bone in context.selected_pose_bones}
        # Method 2: Check pose bones directly for selection state
        elif armature_obj.mode == 'POSE':
            selected_bone_names = {pbone.name for pbone in armature_obj.pose.bones if pbone.bone.select}
        # Method 3: Check armature data bones for selection state
        else:
            selected_bone_names = {bone.name for bone in armature_obj.data.bones if bone.select}
        
        if not selected_bone_names:
            print("Warning: No bones selected for 'Selected Bones Only' mode")
    
    # Get armature for bone exclusion checks
    armature = context.active_object if context and context.active_object and context.active_object.type == 'ARMATURE' else None
    
    # Get object transformation matrix for global mirroring
    object_matrix = None
    if mirror_space == 'GLOBAL' and context and context.active_object:
        object_matrix = context.active_object.matrix_world.copy()
    
    # create name map
    # strip attribute suffix eg. 'pose.bones["root"].location' -> 'pose.bones["root"]'
    bone_names = {fc.data_path.rsplit('.', 1)[0] for fc in act.fcurves if '.' in fc.data_path}
    mirror_map = create_mirror_map(bone_names)

    if axis == 'X':
        negate_data_path_tuples = NEGATE_DATA_PATH_XAXIS
    elif axis == 'Y':
        negate_data_path_tuples = NEGATE_DATA_PATH_YAXIS
    elif axis == 'Z':
        negate_data_path_tuples = NEGATE_DATA_PATH_ZAXIS
    else:
        raise ValueError(f"Unsupported {axis=}")

    if only_active_frame and current_frame is not None:
        # Frame-specific mirroring: only affect keyframes at current frame
        # Step 1: Collect all source data first to prevent overwriting issues
        keyframe_data = []  # List of (source_path, target_path, array_index, value, left_handle, right_handle)
        
        for fc in act.fcurves:
            data_path = fc.data_path
            array_index = fc.array_index
            path, _dot, attribute = data_path.rpartition('.')
            
            # Extract bone name if this is a bone fcurve
            bone_name = ""
            if 'pose.bones[' in path:
                bone_name = path.split('"')[1] if '"' in path else path.split("'")[1] if "'" in path else ""
            
            # Check if this bone should be excluded from mirroring
            if bone_name and should_exclude_bone_from_mirroring(bone_name, armature, include_fingers):
                continue
            
            # Determine target data path (mirrored bone)
            target_data_path = data_path
            target_bone_name = bone_name
            if path and (path in mirror_map):
                target_data_path = "".join((mirror_map[path], _dot, attribute))
                # Extract target bone name
                if 'pose.bones[' in mirror_map[path]:
                    target_bone_name = mirror_map[path].split('"')[1] if '"' in mirror_map[path] else mirror_map[path].split("'")[1] if "'" in mirror_map[path] else bone_name
            
            # Check if TARGET bone should be affected (selected bones only filter)
            # This ensures only selected bones receive mirrored data
            if selected_bones_only and target_bone_name:
                if target_bone_name not in selected_bone_names:
                    continue
            
            # Find the keyframe at current frame
            current_kf = None
            for kf in fc.keyframe_points:
                if abs(kf.co[0] - current_frame) < 0.001:
                    current_kf = kf
                    break
            
            if current_kf is None:
                continue
                
            # Get the value and handles
            value = current_kf.co[1]
            left_handle = current_kf.handle_left[1]
            right_handle = current_kf.handle_right[1]
            
            # Check if negation should be applied
            should_negate = (attribute, array_index) in negate_data_path_tuples
            
            # Special case: Hip bone should NOT have its location negated (only rotation)
            # This matches the Idle Pose Library behavior
            if bone_name and bone_name.lower() == 'hip' and attribute == 'location':
                should_negate = False
            
            # Apply negation if needed
            if should_negate:
                if mirror_space == 'GLOBAL':
                    value = apply_global_mirror_transform(value, axis, object_matrix)
                    left_handle = apply_global_mirror_transform(left_handle, axis, object_matrix)
                    right_handle = apply_global_mirror_transform(right_handle, axis, object_matrix)
                else:
                    value = -value
                    left_handle = -left_handle
                    right_handle = -right_handle
            
            # Store the data for later application
            keyframe_data.append((data_path, target_data_path, array_index, value, left_handle, right_handle))
        
        # Step 2: Apply all mirrored keyframes at once
        for source_path, target_path, array_index, value, left_handle, right_handle in keyframe_data:
            # Find or create target fcurve
            target_fc = None
            for fc_check in act.fcurves:
                if fc_check.data_path == target_path and fc_check.array_index == array_index:
                    target_fc = fc_check
                    break
            
            if target_fc is None:
                target_fc = act.fcurves.new(target_path, index=array_index)
            
            # Set keyframe at current frame
            target_fc.keyframe_points.insert(current_frame, value)
            
            # Update the keyframe handles
            for kf in target_fc.keyframe_points:
                if abs(kf.co[0] - current_frame) < 0.001:
                    kf.handle_left = (kf.handle_left[0], left_handle)
                    kf.handle_right = (kf.handle_right[0], right_handle)
                    break
                    
    else:
        # Full animation mirroring: collect all data first, delete fcurves, then create new ones
        # Step 1: Collect all fcurve data with mirrored values
        fcurve_data = []  # List of (target_path, array_index, keyframe_values, action_group)
        fcurves_to_remove = []  # Track which fcurves to remove
        
        for fc in act.fcurves:
            data_path = fc.data_path
            array_index = fc.array_index

            # bone curves are 'pose.bones["root"].location'
            # objects curves are simply 'location'
            path, _dot, attribute = data_path.rpartition('.')
            
            # Extract bone name if this is a bone fcurve
            bone_name = ""
            if 'pose.bones[' in path:
                bone_name = path.split('"')[1] if '"' in path else path.split("'")[1] if "'" in path else ""
            
            # Check if this bone should be excluded from mirroring
            if bone_name and should_exclude_bone_from_mirroring(bone_name, armature, include_fingers):
                continue
            
            # Determine target path (mirrored bone)
            target_data_path = data_path
            target_bone_name = bone_name
            
            if path and (path in mirror_map):
                target_data_path = "".join((mirror_map[path], _dot, attribute))
                # Extract target bone name for action group
                if 'pose.bones[' in mirror_map[path]:
                    target_bone_name = mirror_map[path].split('"')[1] if '"' in mirror_map[path] else mirror_map[path].split("'")[1] if "'" in mirror_map[path] else bone_name
            
            # Check if TARGET bone should be affected (selected bones only filter)
            # This ensures only selected bones receive mirrored data
            if selected_bones_only and target_bone_name:
                if target_bone_name not in selected_bone_names:
                    continue
            
            # Check if values should be negated
            should_negate = (attribute, array_index) in negate_data_path_tuples
            
            # Special case: Hip bone should NOT have its location negated (only rotation)
            # This matches the Idle Pose Library behavior
            if bone_name and bone_name.lower() == 'hip' and attribute == 'location':
                should_negate = False
            
            # Collect all keyframe data from this fcurve
            keyframe_values = []
            for kf in fc.keyframe_points:
                frame = kf.co[0]
                value = kf.co[1]
                left_handle_x = kf.handle_left[0]
                left_handle_y = kf.handle_left[1]
                right_handle_x = kf.handle_right[0]
                right_handle_y = kf.handle_right[1]
                interpolation = kf.interpolation
                
                # Apply negation if needed
                if should_negate:
                    if mirror_space == 'GLOBAL':
                        value = apply_global_mirror_transform(value, axis, object_matrix)
                        left_handle_y = apply_global_mirror_transform(left_handle_y, axis, object_matrix)
                        right_handle_y = apply_global_mirror_transform(right_handle_y, axis, object_matrix)
                    else:
                        value = -value
                        left_handle_y = -left_handle_y
                        right_handle_y = -right_handle_y
                
                keyframe_values.append((frame, value, left_handle_x, left_handle_y, right_handle_x, right_handle_y, interpolation))
            
            # Store the mirrored data
            fcurve_data.append((target_data_path, array_index, keyframe_values, target_bone_name))
            fcurves_to_remove.append(fc)
        
        # Step 2: Remove all source fcurves
        for fc in fcurves_to_remove:
            act.fcurves.remove(fc)
        
        # Step 3: Create new fcurves with mirrored data
        for target_path, array_index, keyframe_values, action_group_name in fcurve_data:
            # Check if target fcurve already exists (can happen with selected_bones_only 
            # when target bone wasn't selected but source was)
            existing_fc = act.fcurves.find(target_path, index=array_index)
            if existing_fc:
                act.fcurves.remove(existing_fc)
            
            # Create new fcurve
            new_fc = act.fcurves.new(target_path, index=array_index, action_group=action_group_name)
            
            # Add all keyframes
            for frame, value, left_x, left_y, right_x, right_y, interpolation in keyframe_values:
                kf = new_fc.keyframe_points.insert(frame, value)
                kf.handle_left = (left_x, left_y)
                kf.handle_right = (right_x, right_y)
                kf.interpolation = interpolation


#########################################################################################
# Hip Bone 180 Rotation
#########################################################################################

def find_hip_bone(armature):
    """Find the hip bone in the armature"""
    if not armature or armature.type != 'ARMATURE':
        return None
    
    # Common hip bone names
    hip_keywords = ['hip', 'pelvis', 'root']
    
    for bone in armature.pose.bones:
        bone_name_lower = bone.name.lower()
        for keyword in hip_keywords:
            if keyword in bone_name_lower:
                return bone
    
    return None

def rotate_hip_180(armature, axis, only_active_frame=False, current_frame=None):
    """Rotate hip bone 180 degrees on specified axis"""
    hip_bone = find_hip_bone(armature)
    
    if not hip_bone:
        print("No hip bone found for 180 degree rotation")
        return False
    
    import math
    rotation_180 = math.radians(180)
    
    # Store original mode and ensure we're in pose mode
    original_mode = bpy.context.mode
    if original_mode != 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
    
    if only_active_frame and current_frame is not None:
        # Only rotate at current frame
        if hip_bone.rotation_mode == 'QUATERNION':
            # Create rotation quaternion and apply it
            if axis == 'X':
                rot_quat = mathutils.Quaternion((1, 0, 0), rotation_180)
            elif axis == 'Y':
                rot_quat = mathutils.Quaternion((0, 1, 0), rotation_180)
            elif axis == 'Z':
                rot_quat = mathutils.Quaternion((0, 0, 1), rotation_180)
            hip_bone.rotation_quaternion = rot_quat @ hip_bone.rotation_quaternion
            hip_bone.keyframe_insert(data_path="rotation_quaternion", frame=current_frame, group=hip_bone.name)
        else:
            # Apply 180 degree rotation to euler
            if axis == 'X':
                hip_bone.rotation_euler[0] += rotation_180
            elif axis == 'Y':
                hip_bone.rotation_euler[1] += rotation_180
            elif axis == 'Z':
                hip_bone.rotation_euler[2] += rotation_180
            hip_bone.keyframe_insert(data_path="rotation_euler", frame=current_frame, group=hip_bone.name)
    else:
        # Rotate for entire animation
        action = armature.animation_data.action if armature.animation_data else None
        if not action:
            print("No action found for hip bone rotation")
            return False
        
        # Find all frames with keyframes for the hip bone
        hip_frames = set()
        for fcurve in action.fcurves:
            if f'pose.bones["{hip_bone.name}"]' in fcurve.data_path and 'rotation' in fcurve.data_path:
                for keyframe in fcurve.keyframe_points:
                    hip_frames.add(int(keyframe.co[0]))
        
        # If no keyframes found, apply to current frame only
        if not hip_frames:
            hip_frames = {current_frame or 1}
        
        # Store original frame
        original_frame = bpy.context.scene.frame_current
        
        # Apply rotation to each frame
        for frame in hip_frames:
            bpy.context.scene.frame_set(frame)
            
            if hip_bone.rotation_mode == 'QUATERNION':
                # Create rotation quaternion and apply it
                if axis == 'X':
                    rot_quat = mathutils.Quaternion((1, 0, 0), rotation_180)
                elif axis == 'Y':
                    rot_quat = mathutils.Quaternion((0, 1, 0), rotation_180)
                elif axis == 'Z':
                    rot_quat = mathutils.Quaternion((0, 0, 1), rotation_180)
                hip_bone.rotation_quaternion = rot_quat @ hip_bone.rotation_quaternion
                hip_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame, group=hip_bone.name)
            else:
                if axis == 'X':
                    hip_bone.rotation_euler[0] += rotation_180
                elif axis == 'Y':
                    hip_bone.rotation_euler[1] += rotation_180
                elif axis == 'Z':
                    hip_bone.rotation_euler[2] += rotation_180
                hip_bone.keyframe_insert(data_path="rotation_euler", frame=frame, group=hip_bone.name)
        
        # Restore original frame
        bpy.context.scene.frame_set(original_frame)
    
    # Restore original mode
    if original_mode != 'POSE':
        bpy.ops.object.mode_set(mode=original_mode)
    
    print(f"Applied 180 degree rotation to hip bone '{hip_bone.name}' on {axis}-axis")
    return True


#########################################################################################
# OPERATORS
#########################################################################################


class SUB_OT_mirror_action(Operator):
    """Mirror/flip animation on selected axis"""
    bl_idname = "sub.mirror_action"
    bl_label = "Mirror Action"
    bl_options = {"REGISTER","UNDO"}

    axis : EnumProperty(
        name="Axis",
        description="Select mirror axis",
        default='Y',
        items = (
            ('X', 'X', "X axis"),
            ('Y', 'Y', "Y axis"),
            ('Z', 'Z', "Z axis"),
            ('XY', 'XY', "Both XY axes"),
            ('XZ', 'XZ', "Both XZ axes"),
            ('YZ', 'YZ', "Both YZ axes"),
            ('XYZ', 'XYZ', "All XYZ axes"),
            ('O', 'Original', "Original"),
        )
    )

    rotate_180 : BoolProperty(
        name="180 Rotate",
        description="Rotate hip bone 180 degrees on selected axis after mirroring",
        default=True
    )

    selected_bones_only : BoolProperty(
        name="Selected Bones Only",
        description="Mirror only selected bones (armatures only)",
        default=False
    )

    only_active_frame : BoolProperty(
        name="Only Active Frame",
        description="Mirror only keyframes at the current frame",
        default=False
    )

    include_fingers : BoolProperty(
        name="Include Fingers",
        description="Include finger bones (FingerL11, FingerR23, etc.) in the mirroring process",
        default=True
    )


    @classmethod
    def poll(cls, context):
        return context.active_object

    def execute(self, context):

        if not context.active_object.animation_data:
            self.report({"ERROR"}, "No Animation Data")
            return {'CANCELLED'}
        if not context.active_object.animation_data.action:
            self.report({"ERROR"}, "No Action assigned")
            return {'CANCELLED'}
        if not context.active_object.animation_data.action.fcurves:
            self.report({"ERROR"}, "No Keyframes")
            return {'CANCELLED'}
        
        # Get current frame for hip rotation
        current_frame = context.scene.frame_current if self.only_active_frame else None
        
        # Check if selected bones only is enabled but no bones are selected
        if self.selected_bones_only and context.active_object.type == 'ARMATURE':
            armature_obj = context.active_object
            has_selected = False
            if context.selected_pose_bones:
                has_selected = True
            elif armature_obj.mode == 'POSE':
                has_selected = any(pbone.bone.select for pbone in armature_obj.pose.bones)
            else:
                has_selected = any(bone.select for bone in armature_obj.data.bones)
            
            if not has_selected:
                self.report({"WARNING"}, "No bones selected. Select bones in Pose mode first.")
                return {'CANCELLED'}
        
        # Get mirror space from scene properties, include_fingers from operator property
        ssp = context.scene.sub_scene_properties
        mirror_space = ssp.mirror_space
        include_fingers = self.include_fingers
        
        # Apply mirroring
        if self.axis in ('X', 'Y', 'Z'): 
            mirror_action(context.active_object.animation_data.action, axis=self.axis, selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            # Apply 180 rotation to hip if enabled
            if self.rotate_180:
                rotate_hip_180(context.active_object, self.axis, only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'XY':
            mirror_action(context.active_object.animation_data.action, axis='X', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            mirror_action(context.active_object.animation_data.action, axis='Y', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            # Apply 180 rotation to hip if enabled (for each axis)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'X', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Y', only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'XZ':
            mirror_action(context.active_object.animation_data.action, axis='X', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            mirror_action(context.active_object.animation_data.action, axis='Z', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            # Apply 180 rotation to hip if enabled (for each axis)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'X', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Z', only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'YZ':
            mirror_action(context.active_object.animation_data.action, axis='Y', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            mirror_action(context.active_object.animation_data.action, axis='Z', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            # Apply 180 rotation to hip if enabled (for each axis)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'Y', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Z', only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'XYZ':
            mirror_action(context.active_object.animation_data.action, axis='X', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            mirror_action(context.active_object.animation_data.action, axis='Y', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            mirror_action(context.active_object.animation_data.action, axis='Z', selected_bones_only=self.selected_bones_only, context=context, only_active_frame=self.only_active_frame, mirror_space=mirror_space, include_fingers=include_fingers)
            # Apply 180 rotation to hip if enabled (for each axis)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'X', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Y', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Z', only_active_frame=self.only_active_frame, current_frame=current_frame)
        # Skip 'O'; helps back and forth between poses
        
        # Update success message to include hip rotation info
        message = f"Action mirrored on {self.axis}-axis using {mirror_space.lower()} space!"
        if self.rotate_180:
            message += f" Hip rotated 180° on {self.axis}-axis."
        self.report({"INFO"}, message)
        return {'FINISHED'}


#########################################################################################
# REGISTER/UNREGISTER
#########################################################################################


classes = (
    SUB_OT_mirror_action,
)

def register():
    logger.info("Registering mirror_animation.py classes")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
            logger.info(f"Successfully registered {cls.__name__}")
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {str(e)}")

def unregister():
    logger.info("Unregistering mirror_animation.py classes")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
            logger.info(f"Successfully unregistered {cls.__name__}")
        except Exception as e:
            logger.error(f"Failed to unregister {cls.__name__}: {str(e)}")


if __name__ == "__main__":
    register() 