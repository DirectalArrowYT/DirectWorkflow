import bpy
import mathutils
from mathutils import Vector, Matrix
import math

# Function to invoke the position matching dialog that can be imported by other scripts
def invoke_position_match_dialog():
    bpy.ops.sub.fk_to_ik_transfer('INVOKE_DEFAULT')

class SUB_OP_fk_to_ik_transfer(bpy.types.Operator):
    """Perfectly positions IK controls to match the FK bone positions"""
    bl_idname = "sub.fk_to_ik_transfer"
    bl_label = "Position IK Controls"
    bl_options = {'REGISTER', 'UNDO'}
    
    entire_animation: bpy.props.BoolProperty(
        name="Entire Animation",
        description="Apply to the entire animation instead of just the current frame",
        default=True
    )
    
    auto_keyframe: bpy.props.BoolProperty(
        name="Auto Keyframe",
        description="Automatically insert keyframes when applying to the entire animation",
        default=True
    )

    remove_knee_frames: bpy.props.BoolProperty(
        name="Remove Knee Frames",
        description="Remove all keyframes from the original Knee bones except frame 1 (helps with mocap cleanup)",
        default=True
    )

    # When removing knee frames, allow the user to choose the reference frame
    reference_frame: bpy.props.EnumProperty(
        name="Reference Frame",
        description="Which frame to keep as the reference when cleaning knee/leg keyframes",
        items=(
            ('FIRST', "Keep First Frame", "Keep only the first frame's keys"),
            ('LAST', "Keep Last Frame", "Keep only the last frame's keys"),
        ),
        default='LAST'
    )

    reset_foot_bones: bpy.props.BoolProperty(
        name="Reset Foot FK Bones",
        description="Reset transforms and remove keyframes from Foot FK bones after IK transfer",
        default=False
    )

    @classmethod
    def poll(cls, context):
        return (context.object and 
                context.object.type == 'ARMATURE' and 
                context.mode == 'POSE')

    def process_frame(self, context):
        armature_object = context.object
        armature = armature_object.data
        transfer_count = 0
        
        # Store all constraints states and disable them
        constraint_states = {}
        for pose_bone in armature_object.pose.bones:
            for i, constraint in enumerate(pose_bone.constraints):
                constraint_key = (pose_bone.name, i)
                constraint_states[constraint_key] = constraint.mute
                constraint.mute = True
        
        # Update the view layer to see the pure FK pose
        context.view_layer.update()
        
        # NOW capture the original FK bone world matrices after constraints are disabled
        # This captures the true FK positions in world space without any IK influence
        original_fk_world_matrices = {}
        for side in ["L", "R"]:
            foot_bone = armature_object.pose.bones.get(f"Foot{side}")
            hand_bone = armature_object.pose.bones.get(f"Hand{side}")
            if foot_bone:
                # Convert to world space matrix
                world_matrix = armature_object.matrix_world @ foot_bone.matrix
                original_fk_world_matrices[f"Foot{side}"] = world_matrix
            if hand_bone:
                # Convert to world space matrix
                world_matrix = armature_object.matrix_world @ hand_bone.matrix
                original_fk_world_matrices[f"Hand{side}"] = world_matrix
        
        # Track bones that need keyframes
        bones_to_keyframe = []
        
        # Process arms and legs with IK
        for side in ["L", "R"]:
            # -- LEG CHAIN --
            leg_bone = armature_object.pose.bones.get(f"Leg{side}")
            knee_bone = armature_object.pose.bones.get(f"Knee{side}")
            foot_bone = armature_object.pose.bones.get(f"Foot{side}")
            foot_ik_bone = armature_object.pose.bones.get(f"FootIK{side}")
            knee_ik_bone = armature_object.pose.bones.get(f"KneeIK{side}")
            
            if all([leg_bone, knee_bone, foot_bone, foot_ik_bone, knee_ik_bone]):
                # Get world space positions
                leg_pos = leg_bone.matrix.to_translation()
                knee_pos = knee_bone.matrix.to_translation()
                foot_pos = foot_bone.matrix.to_translation()
                
                # Position foot IK at EXACT foot position with EXACT rotation
                # Copy the entire matrix to ensure perfect positioning
                foot_ik_bone.matrix = foot_bone.matrix.copy()
                
                # Calculate knee pole target position
                # Vector from leg to foot (IK chain direction)
                leg_to_foot = foot_pos - leg_pos
                leg_to_foot.normalize()
                
                # Projected knee position onto leg-foot line
                knee_proj = leg_pos + leg_to_foot * leg_to_foot.dot(knee_pos - leg_pos)
                
                # Vector from projected knee to actual knee (bend direction)
                pole_dir = knee_pos - knee_proj
                
                # If knee is almost perfectly straight, use armature object relative direction
                if pole_dir.length < 0.001:
                    arm_obj_forward_local = Vector((0.0, -1.0, 0.0)) 
                    arm_obj_forward_world = armature_object.matrix_world.to_3x3() @ arm_obj_forward_local
                    
                    chain_axis = (foot_pos - leg_pos).normalized()
                    
                    pole_dir = arm_obj_forward_world - arm_obj_forward_world.project(chain_axis)
                    if pole_dir.length < 0.01: # If char forward is aligned with leg, use char up
                        arm_obj_up_local = Vector((0.0, 0.0, 1.0))
                        arm_obj_up_world = armature_object.matrix_world.to_3x3() @ arm_obj_up_local
                        pole_dir = arm_obj_up_world - arm_obj_up_world.project(chain_axis)
                        if pole_dir.length < 0.01: # Fallback if char up is also aligned (e.g. leg pointing up)
                            arm_obj_right_local = Vector((1.0, 0.0, 0.0))
                            arm_obj_right_world = armature_object.matrix_world.to_3x3() @ arm_obj_right_local
                            pole_dir = arm_obj_right_world - arm_obj_right_world.project(chain_axis)
                    
                    if pole_dir.length > 0.0001: # Ensure pole_dir is not zero
                        pole_dir.normalize()
                    else: # Ultimate fallback to a world vector if all else fails
                        pole_dir = Vector((0,1,0)) # Default to world +Y (arbitrary)

                else:
                    pole_dir.normalize()
                    
                # Scale the vector and position the pole target
                pole_distance = 4.0
                knee_ik_bone.matrix = Matrix.Translation(knee_pos + pole_dir * pole_distance)
                
                # Add to keyframing list
                bones_to_keyframe.append(foot_ik_bone)
                bones_to_keyframe.append(knee_ik_bone)
                
                transfer_count += 1
                
            # -- ARM CHAIN --
            shoulder_bone = armature_object.pose.bones.get(f"Shoulder{side}")
            arm_bone = armature_object.pose.bones.get(f"Arm{side}")
            hand_bone = armature_object.pose.bones.get(f"Hand{side}")
            hand_ik_bone = armature_object.pose.bones.get(f"HandIK{side}")
            arm_ik_bone = armature_object.pose.bones.get(f"ArmIK{side}")
            
            if all([arm_bone, hand_bone, hand_ik_bone, arm_ik_bone]):
                # Get world space positions
                if shoulder_bone:
                    shoulder_pos = shoulder_bone.matrix.to_translation()
                else:
                    # Fake a shoulder position if none exists
                    shoulder_dir = arm_bone.matrix.to_translation() - hand_bone.matrix.to_translation()
                    shoulder_dir.normalize()
                    shoulder_pos = arm_bone.matrix.to_translation() + shoulder_dir * 1.0
                
                arm_pos = arm_bone.matrix.to_translation()
                hand_pos = hand_bone.matrix.to_translation()
                
                # Position hand IK at EXACT hand position with EXACT rotation
                # Copy the entire matrix to ensure perfect positioning
                hand_ik_bone.matrix = hand_bone.matrix.copy()
                
                # Calculate elbow pole target position
                # Vector from shoulder to hand (IK chain direction)
                shoulder_to_hand = hand_pos - shoulder_pos
                shoulder_to_hand.normalize()
                
                # Projected elbow position onto shoulder-hand line
                arm_proj = shoulder_pos + shoulder_to_hand * shoulder_to_hand.dot(arm_pos - shoulder_pos)
                
                # Vector from projected elbow to actual elbow (bend direction)
                pole_dir = arm_pos - arm_proj
                
                # If elbow is almost perfectly straight, use armature object relative direction
                if pole_dir.length < 0.001:
                    arm_obj_backward_local = Vector((0.0, 1.0, 0.0))
                    arm_obj_backward_world = armature_object.matrix_world.to_3x3() @ arm_obj_backward_local
                    
                    # Ensure shoulder_pos is valid (it's calculated earlier)
                    chain_axis = (hand_pos - shoulder_pos).normalized()

                    pole_dir = arm_obj_backward_world - arm_obj_backward_world.project(chain_axis)
                    if pole_dir.length < 0.01: # If char backward is aligned with arm, use char up
                        arm_obj_up_local = Vector((0.0, 0.0, 1.0))
                        arm_obj_up_world = armature_object.matrix_world.to_3x3() @ arm_obj_up_local
                        pole_dir = arm_obj_up_world - arm_obj_up_world.project(chain_axis)
                        if pole_dir.length < 0.01: # Fallback if char up is also aligned
                            arm_obj_right_local = Vector((1.0, 0.0, 0.0))
                            arm_obj_right_world = armature_object.matrix_world.to_3x3() @ arm_obj_right_local
                            pole_dir = arm_obj_right_world - arm_obj_right_world.project(chain_axis)

                    if pole_dir.length > 0.0001: # Ensure pole_dir is not zero
                        pole_dir.normalize()
                    else: # Ultimate fallback
                        pole_dir = Vector((0,-1,0)) # Default to world -Y for arms

                else:
                    pole_dir.normalize()
                    
                # Scale the vector and position the pole target
                pole_distance = 4.0
                arm_ik_bone.matrix = Matrix.Translation(arm_pos + pole_dir * pole_distance)
                
                # Add to keyframing list
                bones_to_keyframe.append(hand_ik_bone)
                bones_to_keyframe.append(arm_ik_bone)
                
                transfer_count += 1
        
        # Update view layer before restoring constraints
        context.view_layer.update()
        
        # Get FK positions again for pole angle calculation, as they are clean here
        # (constraints are off, IK bones are placed but not yet influencing)
        fk_positions = {}
        for side in ["L", "R"]:
            fk_positions[f'leg_pos{side}'] = armature_object.pose.bones.get(f"Leg{side}").matrix.to_translation() if armature_object.pose.bones.get(f"Leg{side}") else None
            fk_positions[f'knee_pos{side}'] = armature_object.pose.bones.get(f"Knee{side}").matrix.to_translation() if armature_object.pose.bones.get(f"Knee{side}") else None
            fk_positions[f'foot_pos{side}'] = armature_object.pose.bones.get(f"Foot{side}").matrix.to_translation() if armature_object.pose.bones.get(f"Foot{side}") else None
            
            # Shoulder pos might be faked if Shoulder bone doesn't exist, retrieve the one used for pole calc
            shoulder_bone = armature_object.pose.bones.get(f"Shoulder{side}")
            arm_bone = armature_object.pose.bones.get(f"Arm{side}")
            hand_bone = armature_object.pose.bones.get(f"Hand{side}")
            if shoulder_bone:
                fk_positions[f'shoulder_pos{side}'] = shoulder_bone.matrix.to_translation()
            elif arm_bone and hand_bone: # Reconstruct faked shoulder if necessary
                shoulder_dir_temp = arm_bone.matrix.to_translation() - hand_bone.matrix.to_translation()
                if shoulder_dir_temp.length > 0.001: shoulder_dir_temp.normalize()
                fk_positions[f'shoulder_pos{side}'] = arm_bone.matrix.to_translation() + shoulder_dir_temp * 1.0
            else:
                fk_positions[f'shoulder_pos{side}'] = None
            
            fk_positions[f'arm_pos{side}'] = arm_bone.matrix.to_translation() if arm_bone else None
            fk_positions[f'hand_pos{side}'] = hand_bone.matrix.to_translation() if hand_bone else None

        # Restore all constraints (without changing pole angles yet)
        for (bone_name, constraint_idx), original_state in constraint_states.items():
            bone = armature_object.pose.bones.get(bone_name)
            if bone and constraint_idx < len(bone.constraints):
                constraint = bone.constraints[constraint_idx]
                constraint.mute = original_state
        
        # Wait for the IK system to update before applying pole angles
        context.view_layer.update()
        
        # Now apply the calculated pole angles for each bone
        # Calculate pole angles dynamically based on KneeIK positions
        for side in ["L", "R"]:
            knee_bone = armature_object.pose.bones.get(f"Knee{side}")
            knee_ik_bone = armature_object.pose.bones.get(f"KneeIK{side}")
            leg_bone = armature_object.pose.bones.get(f"Leg{side}")
            foot_bone = armature_object.pose.bones.get(f"Foot{side}")
            
            if all([knee_bone, knee_ik_bone, leg_bone, foot_bone]):
                # Find the IK constraint on the knee bone
                ik_constraint = None
                for constraint in knee_bone.constraints:
                    if constraint.type == 'IK':
                        ik_constraint = constraint
                        break
                
                if ik_constraint:
                    # Calculate the required pole angle to point knee toward KneeIK
                    pole_angle_rad = self.calculate_pole_angle_to_target(
                        armature_object, knee_bone, knee_ik_bone, leg_bone, foot_bone
                    )
                    
                    # Apply the calculated pole angle
                    ik_constraint.pole_angle = pole_angle_rad
        
        # Final update to apply pole angles
        context.view_layer.update()
        
        # Final positioning: Force FootIK and HandIK to exact ORIGINAL FK positions
        # This ensures perfect positioning using the original FK world positions before any IK influence
        for side in ["L", "R"]:
            # Leg chain final positioning using original FK world matrix
            foot_ik_bone = armature_object.pose.bones.get(f"FootIK{side}")
            if foot_ik_bone and f"Foot{side}" in original_fk_world_matrices:
                # Convert world matrix back to bone space
                world_matrix = original_fk_world_matrices[f"Foot{side}"]
                bone_matrix = armature_object.matrix_world.inverted() @ world_matrix
                foot_ik_bone.matrix = bone_matrix
                if foot_ik_bone not in bones_to_keyframe:
                    bones_to_keyframe.append(foot_ik_bone)
            
            # Arm chain final positioning using original FK world matrix
            hand_ik_bone = armature_object.pose.bones.get(f"HandIK{side}")
            if hand_ik_bone and f"Hand{side}" in original_fk_world_matrices:
                # Convert world matrix back to bone space
                world_matrix = original_fk_world_matrices[f"Hand{side}"]
                bone_matrix = armature_object.matrix_world.inverted() @ world_matrix
                hand_ik_bone.matrix = bone_matrix
                if hand_ik_bone not in bones_to_keyframe:
                    bones_to_keyframe.append(hand_ik_bone)
        
        # Also handle arms if needed
        for side in ["L", "R"]:
            arm_bone = armature_object.pose.bones.get(f"Arm{side}")
            arm_ik_bone = armature_object.pose.bones.get(f"ArmIK{side}")
            shoulder_bone = armature_object.pose.bones.get(f"Shoulder{side}")
            hand_bone = armature_object.pose.bones.get(f"Hand{side}")
            
            if all([arm_bone, arm_ik_bone]) and hand_bone:
                # Find the IK constraint on the arm bone
                ik_constraint = None
                for constraint in arm_bone.constraints:
                    if constraint.type == 'IK':
                        ik_constraint = constraint
                        break
                
                if ik_constraint:
                    # For arms, use a simpler approach or set to 0 for now
                    ik_constraint.pole_angle = 0.0
                    print(f"Applied default pole angle to Arm{side}: 0.0°")
        
        # Auto keyframe if needed
        if self.entire_animation and self.auto_keyframe:
            current_frame = context.scene.frame_current
            for bone in bones_to_keyframe:
                bone.keyframe_insert(data_path="location", frame=current_frame, group=bone.name)
                bone.keyframe_insert(data_path="rotation_quaternion", frame=current_frame, group=bone.name)
                bone.keyframe_insert(data_path="rotation_euler", frame=current_frame, group=bone.name)
                bone.keyframe_insert(data_path="scale", frame=current_frame, group=bone.name)
        
        # Final update
        context.view_layer.update()
        
        return transfer_count

    def calculate_pole_angle_to_target(self, armature_object, knee_bone, knee_ik_bone, leg_bone, foot_bone):
        """Calculate the pole angle needed to make the knee bone head point toward the KneeIK bone head"""
        
        # Find the IK constraint on the knee bone
        ik_constraint = None
        for constraint in knee_bone.constraints:
            if constraint.type == 'IK':
                ik_constraint = constraint
                break
        
        if not ik_constraint:
            return 0.0
        
        # Store the original pole angle
        original_pole_angle = ik_constraint.pole_angle
        
        # Get the fixed target direction (from leg to KneeIK - this should NOT change)
        leg_pos = leg_bone.matrix.to_translation()
        foot_pos = foot_bone.matrix.to_translation()
        knee_ik_pos = knee_ik_bone.matrix.to_translation()
        
        # Calculate the chain axis (leg to foot)
        chain_axis = (foot_pos - leg_pos).normalized()
        
        # Target direction (where we want knee to point)
        target_direction = knee_ik_pos - leg_pos
        target_projected = target_direction - target_direction.dot(chain_axis) * chain_axis
        if target_projected.length < 0.001:
            return 0.0
        target_projected.normalize()
        
        # Use binary search to find the pole angle that makes knee point toward target
        min_angle = math.radians(-180.0)
        max_angle = math.radians(180.0)
        best_angle = 0.0
        best_alignment = -1.0  # Best dot product (closer to 1.0 is better)
        
        # Binary search with high precision
        for iteration in range(20):  # 20 iterations should give us very high precision
            # Test the middle angle
            test_angle = (min_angle + max_angle) / 2.0
            ik_constraint.pole_angle = test_angle
            bpy.context.view_layer.update()
            
            # Get the current knee position with this pole angle
            current_knee_pos = knee_bone.matrix.to_translation()
            
            # Calculate the current knee direction
            current_direction = current_knee_pos - leg_pos
            current_projected = current_direction - current_direction.dot(chain_axis) * chain_axis
            
            if current_projected.length > 0.001:
                current_projected.normalize()
                
                # Calculate alignment (dot product - closer to 1.0 means better alignment)
                alignment = current_projected.dot(target_projected)
                
                # If this is the best alignment so far, save it
                if alignment > best_alignment:
                    best_alignment = alignment
                    best_angle = test_angle
                
                # For binary search, test small steps to determine direction
                # Test positive direction
                ik_constraint.pole_angle = test_angle + math.radians(1.0)
                bpy.context.view_layer.update()
                pos_plus = knee_bone.matrix.to_translation()
                dir_plus = pos_plus - leg_pos
                proj_plus = dir_plus - dir_plus.dot(chain_axis) * chain_axis
                if proj_plus.length > 0.001:
                    proj_plus.normalize()
                    align_plus = proj_plus.dot(target_projected)
                else:
                    align_plus = -1.0
                
                # Test negative direction
                ik_constraint.pole_angle = test_angle - math.radians(1.0)
                bpy.context.view_layer.update()
                pos_minus = knee_bone.matrix.to_translation()
                dir_minus = pos_minus - leg_pos
                proj_minus = dir_minus - dir_minus.dot(chain_axis) * chain_axis
                if proj_minus.length > 0.001:
                    proj_minus.normalize()
                    align_minus = proj_minus.dot(target_projected)
                else:
                    align_minus = -1.0
                
                # Determine which direction to search
                if align_plus > align_minus:
                    # Positive direction gives better alignment
                    min_angle = test_angle
                else:
                    # Negative direction gives better alignment
                    max_angle = test_angle
                
                # If we're very well aligned, we can stop
                if best_alignment > 0.999:  # Very close to perfect alignment
                    break
        
        # Restore original angle temporarily for cleanup
        ik_constraint.pole_angle = original_pole_angle
        bpy.context.view_layer.update()
        
        return best_angle

    def execute(self, context):
        if self.entire_animation:
            # Process all frames in the animation
            original_frame = context.scene.frame_current
            start_frame = context.scene.frame_start
            end_frame = context.scene.frame_end
            
            total_frames = end_frame - start_frame + 1
            total_transfers = 0
            
            # Show a progress indicator in the status bar
            context.window_manager.progress_begin(0, 100)
            
            try:
                for frame_num in range(start_frame, end_frame + 1):
                    # Update progress
                    progress = (frame_num - start_frame) / total_frames * 100
                    context.window_manager.progress_update(progress)
                    
                    # Set the current frame
                    context.scene.frame_set(frame_num)
                    
                    # Process this frame
                    transfers = self.process_frame(context)
                    total_transfers += transfers
                    
                # End progress indicator
                context.window_manager.progress_end()
                
                # Return to the original frame
                context.scene.frame_set(original_frame)
                
                # Remove keyframes from knee (and leg) bones if requested
                if self.remove_knee_frames:
                    keep_frame = start_frame if self.reference_frame == 'FIRST' else end_frame
                    self.remove_knee_and_leg_keyframes(context, keep_frame)
                
                # Reset foot FK bones if requested
                if self.reset_foot_bones:
                    self.reset_foot_bone_transforms(context)
                
                # Report success
                self.report({'INFO'}, f"Successfully positioned IK controllers across {total_frames} frames")
                return {'FINISHED'}
                
            except Exception as e:
                # End progress indicator if there was an error
                context.window_manager.progress_end()
                context.scene.frame_set(original_frame)
                self.report({'ERROR'}, f"Error processing animation: {str(e)}")
                return {'CANCELLED'}
        else:
            # Process only the current frame
            transfer_count = self.process_frame(context)
            
            # Report success
            if transfer_count > 0:
                self.report({'INFO'}, f"Successfully positioned {transfer_count} IK controllers")
            else:
                self.report({'WARNING'}, "No IK controllers could be positioned")
                
            return {'FINISHED'}
    
    def remove_knee_and_leg_keyframes(self, context, frame_to_keep):
        """Remove all keyframes from Knee and Leg bones except the chosen reference frame"""
        armature_object = context.object
        
        # Bones to process: knees and legs
        knee_bones = [
            armature_object.pose.bones.get("KneeL"),
            armature_object.pose.bones.get("KneeR")
        ]
        leg_bones = [
            armature_object.pose.bones.get("LegL"),
            armature_object.pose.bones.get("LegR")
        ]
        bones_to_process = [b for b in knee_bones + leg_bones if b]
        
        if not bones_to_process:
            self.report({'WARNING'}, "No Knee/Leg bones found to remove keyframes from")
            return
        
        # Ensure action exists
        if not armature_object.animation_data or not armature_object.animation_data.action:
            self.report({'WARNING'}, "No animation data found")
            return
        
        action = armature_object.animation_data.action
        bone_names = [bone.name for bone in bones_to_process]
        
        # Track the number of keyframes removed
        removed_count = 0
        
        # Find all FCurves associated with the knee bones
        fcurves_to_process = []
        for fcurve in action.fcurves:
            # Parse the data path to check if it belongs to a knee bone
            if fcurve.data_path.startswith('pose.bones["') and any(bone_name in fcurve.data_path for bone_name in bone_names):
                fcurves_to_process.append(fcurve)
        
        # For each FCurve, remove all keyframes except for the chosen frame
        for fcurve in fcurves_to_process:
            # Sort keyframes by frame
            keyframes = sorted(fcurve.keyframe_points, key=lambda kf: kf.co.x)
            
            # Skip if there's only one keyframe or none
            if len(keyframes) <= 1:
                continue
            
            # Decide which keyframe to keep (first or last) and move it to frame_to_keep
            keep_index = 0 if self.reference_frame == 'FIRST' else len(keyframes) - 1
            keep_kf = keyframes[keep_index]
            # Remove all other keyframes, starting from the end to avoid reindex issues
            for i in range(len(keyframes) - 1, -1, -1):
                if i == keep_index:
                    continue
                fcurve.keyframe_points.remove(keyframes[i])
                removed_count += 1
            
            # Move the kept keyframe to the exact reference frame
            if keep_kf.co.x != frame_to_keep:
                keep_kf.co.x = frame_to_keep
                keep_kf.handle_left.x = frame_to_keep - 0.5
                keep_kf.handle_right.x = frame_to_keep + 0.5
            fcurve.update()
        
        # Report the number of keyframes removed
        if removed_count > 0:
            self.report({'INFO'}, f"Removed {removed_count} keyframes from knee/leg bones, keeping only frame {int(frame_to_keep)}")
        else:
            self.report({'INFO'}, "No knee/leg bone keyframes found to remove")
    
    def reset_foot_bone_transforms(self, context):
        """Reset transforms and remove keyframes from Foot FK bones after IK transfer"""
        armature_object = context.object
        
        # Foot bones to process
        foot_bones = [
            armature_object.pose.bones.get("FootL"),
            armature_object.pose.bones.get("FootR")
        ]
        
        # Filter out None values (in case a bone doesn't exist)
        foot_bones = [bone for bone in foot_bones if bone]
        
        if not foot_bones:
            self.report({'WARNING'}, "No Foot bones found to reset")
            return
        
        # Reset transforms for each foot bone
        for bone in foot_bones:
            # Reset location
            bone.location = (0.0, 0.0, 0.0)
            
            # Reset rotation based on rotation mode
            if bone.rotation_mode == 'QUATERNION':
                bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            else:
                bone.rotation_euler = (0.0, 0.0, 0.0)
            
            # Reset scale
            bone.scale = (1.0, 1.0, 1.0)
        
        # Update the view layer to apply the transform resets
        context.view_layer.update()
        
        # Remove keyframes if animation data exists
        if armature_object.animation_data and armature_object.animation_data.action:
            action = armature_object.animation_data.action
            bone_names = [bone.name for bone in foot_bones]
            
            # Track the number of keyframes removed
            removed_count = 0
            
            # Find all FCurves associated with the foot bones and remove them
            fcurves_to_remove = []
            for fcurve in action.fcurves:
                # Parse the data path to check if it belongs to a foot bone
                if fcurve.data_path.startswith('pose.bones["') and any(bone_name in fcurve.data_path for bone_name in bone_names):
                    fcurves_to_remove.append(fcurve)
                    removed_count += len(fcurve.keyframe_points)
            
            # Remove the FCurves entirely
            for fcurve in fcurves_to_remove:
                action.fcurves.remove(fcurve)
            
            # Report the number of keyframes removed
            if removed_count > 0:
                self.report({'INFO'}, f"Reset foot bone transforms and removed {removed_count} keyframes")
            else:
                self.report({'INFO'}, "Reset foot bone transforms (no keyframes found to remove)")
        else:
            self.report({'INFO'}, "Reset foot bone transforms (no animation data found)")
        
        # Final update
        context.view_layer.update()

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.label(text="Match IK positions to FK bones?")
        layout.prop(self, "entire_animation")
        
        # Only show auto keyframe option if entire animation is selected
        if self.entire_animation:
            layout.prop(self, "auto_keyframe")
            layout.prop(self, "remove_knee_frames")
            if self.remove_knee_frames:
                layout.prop(self, "reference_frame")
            layout.prop(self, "reset_foot_bones")

def register():
    bpy.utils.register_class(SUB_OP_fk_to_ik_transfer)

def unregister():
    bpy.utils.unregister_class(SUB_OP_fk_to_ik_transfer)

if __name__ == "__main__":
    register()