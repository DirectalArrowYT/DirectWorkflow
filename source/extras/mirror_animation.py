import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty
import logging
import mathutils

from ..anim.fcurve_compat import get_fcurves, new_fcurve, find_fcurve, remove_fcurve
from ..blender_compat import is_armature_bone_selected, is_pose_bone_selected
from .anim_flip import (
    collect_excluded_bone_names,
    collect_unchecked_custom_mirror_bones,
    create_mirror_map,
    extract_bone_name_from_path,
    find_custom_mirror_bones,
    keyframe_pose_bones,
    load_smash_pose_cache,
    mirror_evaluated_pose,
    should_exclude_bone_from_mirroring,
    smash_pose_data_from_cache,
)

# Set up logging
logger = logging.getLogger(__name__)

# Extra axes kept for the operator UI. Default Y uses Smash-space anim_flip instead.
NEGATE_DATA_PATH_XAXIS = (
    ('location', 0),
    ('rotation_quaternion', 2),
    ('rotation_quaternion', 3),
    ('rotation_euler', 2),
    ('rotation_euler', 1),
)

NEGATE_DATA_PATH_YAXIS = (
    ('location', 2),
    ('rotation_quaternion', 1),
    ('rotation_quaternion', 2),
    ('rotation_euler', 0),
    ('rotation_euler', 1),
)

NEGATE_DATA_PATH_ZAXIS = (
    ('location', 1),
    ('rotation_quaternion', 1),
    ('rotation_quaternion', 3),
    ('rotation_euler', 0),
    ('rotation_euler', 2),
)


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


def _apply_channel_negation(value, left_handle, right_handle, axis, mirror_space, object_matrix):
    if mirror_space != 'GLOBAL':
        return -value, -left_handle, -right_handle
    return (
        apply_global_mirror_transform(value, axis, object_matrix),
        apply_global_mirror_transform(left_handle, axis, object_matrix),
        apply_global_mirror_transform(right_handle, axis, object_matrix),
    )


def _selected_pose_bone_names(context):
    if not context or not context.active_object or context.active_object.type != 'ARMATURE':
        return set()
    armature_obj = context.active_object
    if context.selected_pose_bones:
        return {bone.name for bone in context.selected_pose_bones}
    if armature_obj.mode == 'POSE':
        return {pbone.name for pbone in armature_obj.pose.bones if is_pose_bone_selected(pbone)}
    return {bone.name for bone in armature_obj.data.bones if is_armature_bone_selected(armature_obj, bone)}


def _action_bone_names(act):
    names = set()
    for fcurve in get_fcurves(act):
        bone_name = extract_bone_name_from_path(fcurve.data_path)
        if bone_name:
            names.add(bone_name)
    return names


def _idle_library_pose_data(context, act):
    """Reuse stored Idle Pose nuanmb data when the action has no import cache."""
    import json
    action_name = act.name if act else ""
    candidates = []
    ssp = getattr(context.scene, "sub_scene_properties", None)
    if ssp is not None:
        for pose in getattr(ssp, "idle_pose_list", []):
            if pose.data and pose.name and pose.name in action_name:
                candidates.append(pose.data)
    if "idle_pose_data" in context.scene:
        candidates.append(context.scene["idle_pose_data"])
    for data in candidates:
        try:
            parsed = json.loads(data)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


def mirror_action_smash_y(act, selected_bones_only=False, context=None, only_active_frame=False, include_fingers=False):
    """
    Y-axis Smash Ultimate mirror: same Studio SB flip + importer as Idle Pose
    Library Mirrored. Prefers the Smash TRS cache written on nuanmb import.
    """
    if not context or not context.active_object or context.active_object.type != 'ARMATURE':
        print("Smash Y mirror requires an active armature")
        return

    armature = context.active_object
    excluded_bones = collect_excluded_bone_names(armature, include_fingers=include_fingers)
    ssp = getattr(context.scene, 'sub_scene_properties', None)
    if ssp is not None:
        excluded_bones |= collect_unchecked_custom_mirror_bones(
            armature, getattr(ssp, 'mirror_custom_bones', [])
        )
    target_bones = None
    if selected_bones_only:
        target_bones = _selected_pose_bone_names(context)
        if not target_bones:
            print("Warning: No bones selected for 'Selected Bones Only' mode")
            return

    scene = context.scene
    smash_cache = load_smash_pose_cache(act)
    animated_bones = _action_bone_names(act)
    if only_active_frame:
        frames = [scene.frame_current]
    else:
        frames = sorted({
            int(round(keyframe.co[0]))
            for fcurve in get_fcurves(act)
            for keyframe in fcurve.keyframe_points
        })
        if not frames:
            frames = [scene.frame_current]

    original_frame = scene.frame_current
    for frame in frames:
        if scene.frame_current != frame:
            scene.frame_set(frame)
        context.view_layer.update()
        pose_data = smash_pose_data_from_cache(smash_cache, frame) if smash_cache else None
        if pose_data is None:
            pose_data = _idle_library_pose_data(context, act)
        applied = mirror_evaluated_pose(
            armature,
            excluded_bones=excluded_bones,
            target_bones=target_bones,
            pose_data=pose_data,
            bone_filter=animated_bones or None,
        )
        keyframe_pose_bones(applied, frame)

    if scene.frame_current != original_frame:
        scene.frame_set(original_frame)


def mirror_action(act, axis='X', selected_bones_only=False, context=None, only_active_frame=False, mirror_space='LOCAL', include_fingers=True):
    
    if not (act and get_fcurves(act)):
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
            selected_bone_names = {pbone.name for pbone in armature_obj.pose.bones if is_pose_bone_selected(pbone)}
        # Method 3: Check armature data bones for selection state
        else:
            selected_bone_names = {bone.name for bone in armature_obj.data.bones if is_armature_bone_selected(armature_obj, bone)}
        
        if not selected_bone_names:
            print("Warning: No bones selected for 'Selected Bones Only' mode")
    
    # Get armature for bone exclusion checks
    armature = context.active_object if context and context.active_object and context.active_object.type == 'ARMATURE' else None
    custom_skip = set()
    if context and armature is not None:
        ssp = getattr(context.scene, 'sub_scene_properties', None)
        if ssp is not None:
            custom_skip = collect_unchecked_custom_mirror_bones(
                armature, getattr(ssp, 'mirror_custom_bones', [])
            )
    
    # Get object transformation matrix for global mirroring
    object_matrix = None
    if mirror_space == 'GLOBAL' and context and context.active_object:
        object_matrix = context.active_object.matrix_world.copy()
    
    # create name map
    # strip attribute suffix eg. 'pose.bones["root"].location' -> 'pose.bones["root"]'
    bone_names = {fc.data_path.rsplit('.', 1)[0] for fc in get_fcurves(act) if '.' in fc.data_path}
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
        
        for fc in get_fcurves(act):
            data_path = fc.data_path
            array_index = fc.array_index
            path, _dot, attribute = data_path.rpartition('.')
            
            bone_name = extract_bone_name_from_path(path)
            
            # Check if this bone should be excluded from mirroring
            if bone_name and (
                should_exclude_bone_from_mirroring(bone_name, armature, include_fingers)
                or bone_name in custom_skip
            ):
                continue
            
            # Determine target data path (mirrored bone)
            target_data_path = data_path
            target_bone_name = bone_name
            if path and (path in mirror_map):
                target_data_path = "".join((mirror_map[path], _dot, attribute))
                target_bone_name = extract_bone_name_from_path(mirror_map[path]) or bone_name
            
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
            
            # Studio SB anim_flip applies to Hip / Trans location as well.
            should_negate = (attribute, array_index) in negate_data_path_tuples
            
            if should_negate:
                value, left_handle, right_handle = _apply_channel_negation(
                    value, left_handle, right_handle, axis, mirror_space, object_matrix
                )
            
            # Store the data for later application
            keyframe_data.append((data_path, target_data_path, array_index, value, left_handle, right_handle))
        
        # Step 2: Apply all mirrored keyframes at once
        for source_path, target_path, array_index, value, left_handle, right_handle in keyframe_data:
            # Find or create target fcurve
            target_fc = None
            for fc_check in get_fcurves(act):
                if fc_check.data_path == target_path and fc_check.array_index == array_index:
                    target_fc = fc_check
                    break
            
            if target_fc is None:
                target_fc = new_fcurve(act, target_path, index=array_index)
            
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
        
        for fc in get_fcurves(act):
            data_path = fc.data_path
            array_index = fc.array_index

            # bone curves are 'pose.bones["root"].location'
            # objects curves are simply 'location'
            path, _dot, attribute = data_path.rpartition('.')
            
            bone_name = extract_bone_name_from_path(path)
            
            # Check if this bone should be excluded from mirroring
            if bone_name and (
                should_exclude_bone_from_mirroring(bone_name, armature, include_fingers)
                or bone_name in custom_skip
            ):
                continue
            
            # Determine target path (mirrored bone)
            target_data_path = data_path
            target_bone_name = bone_name
            
            if path and (path in mirror_map):
                target_data_path = "".join((mirror_map[path], _dot, attribute))
                target_bone_name = extract_bone_name_from_path(mirror_map[path]) or bone_name
            
            # Check if TARGET bone should be affected (selected bones only filter)
            # This ensures only selected bones receive mirrored data
            if selected_bones_only and target_bone_name:
                if target_bone_name not in selected_bone_names:
                    continue
            
            # Studio SB anim_flip applies to Hip / Trans location as well.
            should_negate = (attribute, array_index) in negate_data_path_tuples
            
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
                
                if should_negate:
                    value, left_handle_y, right_handle_y = _apply_channel_negation(
                        value, left_handle_y, right_handle_y, axis, mirror_space, object_matrix
                    )
                
                keyframe_values.append((frame, value, left_handle_x, left_handle_y, right_handle_x, right_handle_y, interpolation))
            
            # Store the mirrored data
            fcurve_data.append((target_data_path, array_index, keyframe_values, target_bone_name))
            fcurves_to_remove.append(fc)
        
        # Step 2: Remove all source fcurves
        for fc in fcurves_to_remove:
            remove_fcurve(act, fc)
        
        # Step 3: Create new fcurves with mirrored data
        for target_path, array_index, keyframe_values, action_group_name in fcurve_data:
            # Check if target fcurve already exists (can happen with selected_bones_only 
            # when target bone wasn't selected but source was)
            existing_fc = find_fcurve(act, target_path, index=array_index)
            if existing_fc:
                remove_fcurve(act, existing_fc)
            
            # Create new fcurve
            new_fc = new_fcurve(act, target_path, index=array_index, action_group=action_group_name)
            
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
        for fcurve in get_fcurves(action):
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
        description="Rotate hip bone 180 degrees on selected axis after mirroring. Leave off to match Idle Pose Library Mirrored.",
        default=False
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
        description="Include finger bones (FingerL11, FingerR23, etc.) in the mirroring process. Off matches Idle Pose Library Mirrored.",
        default=False
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
        if not get_fcurves(context.active_object.animation_data.action):
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
                has_selected = any(is_pose_bone_selected(pbone) for pbone in armature_obj.pose.bones)
            else:
                has_selected = any(is_armature_bone_selected(armature_obj, bone) for bone in armature_obj.data.bones)
            
            if not has_selected:
                self.report({"WARNING"}, "No bones selected. Select bones in Pose mode first.")
                return {'CANCELLED'}
        
        # Get mirror space from scene properties, include_fingers from operator property
        ssp = context.scene.sub_scene_properties
        mirror_space = ssp.mirror_space
        include_fingers = self.include_fingers
        
        action = context.active_object.animation_data.action
        smash_y_kwargs = dict(
            selected_bones_only=self.selected_bones_only,
            context=context,
            only_active_frame=self.only_active_frame,
            include_fingers=include_fingers,
        )
        fcurve_kwargs = dict(
            selected_bones_only=self.selected_bones_only,
            context=context,
            only_active_frame=self.only_active_frame,
            mirror_space=mirror_space,
            include_fingers=include_fingers,
        )

        # Apply mirroring
        if self.axis == 'Y':
            mirror_action_smash_y(action, **smash_y_kwargs)
            if self.rotate_180:
                rotate_hip_180(context.active_object, self.axis, only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis in ('X', 'Z'):
            mirror_action(action, axis=self.axis, **fcurve_kwargs)
            if self.rotate_180:
                rotate_hip_180(context.active_object, self.axis, only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'XY':
            mirror_action(action, axis='X', **fcurve_kwargs)
            mirror_action_smash_y(action, **smash_y_kwargs)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'X', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Y', only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'XZ':
            mirror_action(action, axis='X', **fcurve_kwargs)
            mirror_action(action, axis='Z', **fcurve_kwargs)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'X', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Z', only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'YZ':
            mirror_action_smash_y(action, **smash_y_kwargs)
            mirror_action(action, axis='Z', **fcurve_kwargs)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'Y', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Z', only_active_frame=self.only_active_frame, current_frame=current_frame)
        elif self.axis == 'XYZ':
            mirror_action(action, axis='X', **fcurve_kwargs)
            mirror_action_smash_y(action, **smash_y_kwargs)
            mirror_action(action, axis='Z', **fcurve_kwargs)
            if self.rotate_180:
                rotate_hip_180(context.active_object, 'X', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Y', only_active_frame=self.only_active_frame, current_frame=current_frame)
                rotate_hip_180(context.active_object, 'Z', only_active_frame=self.only_active_frame, current_frame=current_frame)
        # Skip 'O'; helps back and forth between poses
        
        if self.axis in ('Y', 'XY', 'YZ', 'XYZ'):
            message = f"Action mirrored on {self.axis}-axis using Smash anim_flip."
        else:
            message = f"Action mirrored on {self.axis}-axis using {mirror_space.lower()} space!"
        if self.rotate_180:
            message += f" Hip rotated 180° on {self.axis}-axis."
        self.report({"INFO"}, message)
        return {'FINISHED'}


class SUB_UL_mirror_custom_bones(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "include", text="")
        row.label(text=item.name, translate=False)


class SUB_OT_find_custom_mirror_bones(Operator):
    """List bones that are not part of a normal Smash Ultimate armature"""
    bl_idname = "sub.find_custom_mirror_bones"
    bl_label = "Find Custom Bones"
    bl_options = {'REGISTER'}

    def execute(self, context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            self.report({'WARNING'}, "Select an armature first")
            return {'CANCELLED'}

        ssp = context.scene.sub_scene_properties
        previous = {item.name: item.include for item in ssp.mirror_custom_bones}
        custom_names = find_custom_mirror_bones(armature)
        ssp.mirror_custom_bones.clear()
        for name in custom_names:
            item = ssp.mirror_custom_bones.add()
            item.name = name
            item.include = previous.get(name, False)
        ssp.mirror_custom_bones_index = 0
        ssp.mirror_custom_armature_name = armature.name
        if custom_names:
            self.report({'INFO'}, f"Found {len(custom_names)} custom bone(s). Check the ones to mirror.")
        else:
            self.report({'INFO'}, "No custom bones found on this armature")
        return {'FINISHED'}


class SUB_OT_mirror_custom_bones_set_all(Operator):
    """Check or uncheck every custom bone in the list"""
    bl_idname = "sub.mirror_custom_bones_set_all"
    bl_label = "Set All Custom Bones"
    bl_options = {'REGISTER'}

    include: BoolProperty(default=True)

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        if not ssp.mirror_custom_bones:
            self.report({'WARNING'}, "Find custom bones first")
            return {'CANCELLED'}
        for item in ssp.mirror_custom_bones:
            item.include = self.include
        return {'FINISHED'}


#########################################################################################
# REGISTER/UNREGISTER
#########################################################################################


classes = (
    SUB_OT_mirror_action,
    SUB_UL_mirror_custom_bones,
    SUB_OT_find_custom_mirror_bones,
    SUB_OT_mirror_custom_bones_set_all,
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