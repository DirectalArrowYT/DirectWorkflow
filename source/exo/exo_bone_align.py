import bpy
from bpy.types import Operator, PropertyGroup
from bpy.props import BoolProperty, StringProperty, CollectionProperty
from mathutils import Vector

class FilteredBone(PropertyGroup):
    name: StringProperty()

def register_filtered_bones():
    bpy.utils.register_class(FilteredBone)
    if not hasattr(bpy.types.Scene, "filtered_exo_bones"):
        bpy.types.Scene.filtered_exo_bones = CollectionProperty(type=FilteredBone)

def unregister_filtered_bones():
    if hasattr(bpy.types.Scene, "filtered_exo_bones"):
        del bpy.types.Scene.filtered_exo_bones
    try:
        bpy.utils.unregister_class(FilteredBone)
    except RuntimeError:
        pass

class SUB_OP_align_exo_bones(Operator):
    bl_idname = "sub.align_exo_bones"
    bl_label = "Align Exo Bones"
    bl_description = "Align Smash bones to their corresponding Exo bones. IMPORTANT: You must be in EDIT MODE to use this function."
    bl_options = {'REGISTER', 'UNDO'}
    
    finger_chains_as_units: BoolProperty(
        name="Move Finger Chains as Units",
        description="Move entire finger chains together as units",
        default=True
    )
    
    adjust_children: BoolProperty(
        name="Adjust Children",
        description="Adjust child bone positions to maintain their relative positions",
        default=True
    )
    
    maintain_roll: BoolProperty(
        name="Maintain Roll",
        description="Maintain the original roll angle of the bones",
        default=True
    )
    
    @classmethod
    def poll(cls, context):
        # Ensure we're in edit mode with an armature that has helper bone data
        return (context.mode == 'EDIT_ARMATURE' and 
                context.active_object and 
                context.active_object.type == 'ARMATURE' and 
                hasattr(context.active_object.data, "sub_helper_bone_data"))
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "finger_chains_as_units")
        layout.prop(self, "adjust_children")
        layout.prop(self, "maintain_roll")
    
    def execute(self, context):
        armature = context.active_object
        helper_bone_data = armature.data.sub_helper_bone_data
        
        # Must be in edit mode
        if context.mode != 'EDIT_ARMATURE':
            bpy.ops.object.mode_set(mode='EDIT')
        
        # Get exo bone mapping from helper bone data
        exo_bone_mapping = {}
        
        # Process orient constraints - they connect Exo bones to controlling bones
        for constraint in helper_bone_data.orient_constraints:
            # Check if this is an Exo bone constraint
            if "H_Exo_" in constraint.target_bone_name:
                exo_bone_mapping[constraint.target_bone_name] = constraint.source_bone_name
        
        
        # Store original bone rolls if maintaining roll
        original_rolls = {}
        if self.maintain_roll:
            for smash_bone_name in exo_bone_mapping.values():
                if smash_bone_name in armature.data.edit_bones:
                    original_rolls[smash_bone_name] = armature.data.edit_bones[smash_bone_name].roll
        
        # Store original lengths for bones
        original_lengths = {}
        for smash_bone_name in exo_bone_mapping.values():
            if smash_bone_name in armature.data.edit_bones:
                original_lengths[smash_bone_name] = armature.data.edit_bones[smash_bone_name].length
        
        # Pre-processing: identify finger bones by patterns
        finger_bones = {}  # Format: {bone_name: (finger_side, finger_num, position)}
        finger_chains = {}  # Format: {(side, num): [bone_names_in_chain]}
        
        for bone in armature.data.edit_bones:
            # Check for finger naming patterns
            bone_name_lower = bone.name.lower()
            
            # Pattern 1: FingerR41, FingerL12, etc. (uppercase Finger)
            if "Finger" in bone.name and len(bone.name) >= 9:
                try:
                    # Extract the finger number and position from the name
                    # Format is "FingerXYZ" where X is side (R/L), Y is finger number (1-5), Z is position (1-3)
                    side = bone.name[6]  # R or L
                    if side in ["R", "L"]:
                        finger_num = int(bone.name[7])
                        position = int(bone.name[8])
                        
                        # Store bone in finger_bones dictionary
                        finger_bones[bone.name] = (side, finger_num, position)
                        
                        # Add to finger chains
                        chain_key = (side, finger_num)
                        if chain_key not in finger_chains:
                            finger_chains[chain_key] = []
                        finger_chains[chain_key].append(bone.name)
                except (IndexError, ValueError):
                    pass  # Not a standard finger bone name format
            
            # Pattern 2: fingerl51, fingerr51, etc. (lowercase finger)
            elif "finger" in bone_name_lower and len(bone.name) >= 8:
                try:
                    # Extract the finger number and position from the name
                    # Format is "fingerXYZ" where X is side (l/r), Y is finger number (1-5), Z is position (1-3)
                    side = bone.name[6].upper()  # Convert to uppercase
                    if side in ["R", "L"]:
                        finger_num = int(bone.name[7])
                        position = int(bone.name[8])
                        
                        # Store bone in finger_bones dictionary
                        finger_bones[bone.name] = (side, finger_num, position)
                        
                        # Add to finger chains
                        chain_key = (side, finger_num)
                        if chain_key not in finger_chains:
                            finger_chains[chain_key] = []
                        finger_chains[chain_key].append(bone.name)
                except (IndexError, ValueError):
                    pass  # Not a standard finger bone name format
        
        
        # Store original child positions relative to their parents
        child_offsets = {}
        if self.adjust_children:
            for bone in armature.data.edit_bones:
                if bone.parent:
                    # Store position relative to parent
                    parent_head = bone.parent.head
                    child_offsets[bone.name] = [
                        bone.head[0] - parent_head[0],
                        bone.head[1] - parent_head[1],
                        bone.head[2] - parent_head[2]
                    ]
        
        # Track which bones have been moved already (to avoid double-processing)
        processed_bones = set()
        
        # First phase: Process bones and move finger chains as units
        aligned_bones = 0
        for exo_bone_name, smash_bone_name in exo_bone_mapping.items():
            # Skip if either bone doesn't exist
            if (exo_bone_name not in armature.data.edit_bones or 
                smash_bone_name not in armature.data.edit_bones or
                smash_bone_name in processed_bones):
                continue
            
            exo_bone = armature.data.edit_bones[exo_bone_name]
            smash_bone = armature.data.edit_bones[smash_bone_name]
            
            # Check if this is a finger bone and move the whole chain if enabled
            is_finger_bone = smash_bone_name in finger_bones
            
            
            if is_finger_bone and self.finger_chains_as_units:
                # Get finger chain information
                side, finger_num, position = finger_bones[smash_bone_name]
                
                # Only process finger chains starting with the first bone (position 1)
                if position == 1:  # This is the first bone in the chain
                    # Get the offset needed to move this bone
                    offset = exo_bone.head - smash_bone.head
                    
                    # Move all bones in this finger chain by the same offset
                    chain_key = (side, finger_num)
                    for bone_name in finger_chains[chain_key]:
                        if bone_name in armature.data.edit_bones:
                            bone = armature.data.edit_bones[bone_name]
                            bone.head += offset
                            bone.tail += offset
                            processed_bones.add(bone_name)
                    
                    aligned_bones += 1
                else:
                    # Skip other finger bones - they'll be moved as part of their chain
                    continue
            
            elif is_finger_bone and not self.finger_chains_as_units:
                # Handle finger bones when finger_chains_as_units is disabled
                side, finger_num, position = finger_bones[smash_bone_name]
                is_main_finger = finger_num in [1, 2, 3, 4, 5]  # 1=10, 2=20, 3=30, 4=40, 5=50
                
                if is_main_finger and position == 0:
                    # Main finger bones (pos=0) should be handled with hand bones
                    # Skip them here - they'll be processed when hand bones are processed
                    continue
                else:
                    # For finger bones in exo mapping (pos=1,2,3), use the proper alignment logic
                    # Find the children of the Exo bone
                    exo_children = [b for b in armature.data.edit_bones if b.parent == exo_bone]
                    
                    if exo_children:
                        # Has children - Set the head to match the Exo bone's head
                        smash_bone.head = exo_bone.head.copy()
                        # Use the first child's head as the tail target
                        smash_bone.tail = exo_children[0].head.copy()
                    else:
                        # No children - move as a unit (like in TRANSLATE mode)
                        # Calculate the offset vector
                        offset = exo_bone.head - smash_bone.head
                        
                        # Move head and tail by the same offset
                        smash_bone.head += offset
                        smash_bone.tail += offset
                    
                    processed_bones.add(smash_bone_name)
                    aligned_bones += 1
            
            else:
                # Hand bones move as a unit
                if ("Hand" in exo_bone_name or "hand" in exo_bone_name.lower() or 
                    "Hand" in smash_bone_name or "hand" in smash_bone_name.lower()):
                    # Move the whole bone as a unit
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                    
                    # If there's a "Have" bone that should follow this Hand bone, move it too
                    # Handle both uppercase and lowercase variations
                    have_bone_name = smash_bone_name.replace("Hand", "Have").replace("hand", "have")
                    if have_bone_name in armature.data.edit_bones:
                        have_bone = armature.data.edit_bones[have_bone_name]
                        have_bone.head += offset
                        have_bone.tail += offset
                        processed_bones.add(have_bone_name)
                    
                    # Always move main finger bones (10, 20, 30, 40, 50) with hand bones
                    # This happens regardless of the finger_chains_as_units setting
                    hand_side = "R" if "r" in smash_bone_name.lower() else "L"
                    for finger_num in [1, 2, 3, 4, 5]:  # 1=10, 2=20, 3=30, 4=40, 5=50
                        chain_key = (hand_side, finger_num)
                        if chain_key in finger_chains:
                            for bone_name in finger_chains[chain_key]:
                                if bone_name in armature.data.edit_bones and bone_name not in processed_bones:
                                    # Only add main finger bones (pos=0) to processed_bones
                                    # Secondary finger bones (pos=1,2,3) should be processed individually
                                    if bone_name in finger_bones:
                                        side, finger_num_check, position = finger_bones[bone_name]
                                        if position == 0:  # Only main finger bones
                                            bone = armature.data.edit_bones[bone_name]
                                            bone.head += offset
                                            bone.tail += offset
                                            processed_bones.add(bone_name)
                                    else:
                                        # Fallback for bones not in finger_bones dictionary
                                        bone = armature.data.edit_bones[bone_name]
                                        bone.head += offset
                                        bone.tail += offset
                                        processed_bones.add(bone_name)
                
                # Wrist bones should move as a unit
                elif "Wrist" in exo_bone_name:
                    # Move the whole bone as a unit
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                
                # LegC bone should move as a unit
                elif "LegC" in exo_bone_name or "LegC" == smash_bone_name:
                    # Move the whole bone as a unit without connecting to any children
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                    
                    # Make sure we mark any children that might be from the Exo bone
                    # to avoid them being handled in the default case
                    exo_children = [b for b in armature.data.edit_bones if b.parent == exo_bone]
                    for child in exo_children:
                        processed_bones.add(child.name)
                
                # Foot bones should move as a unit
                elif "Foot" in exo_bone_name or "Foot" in smash_bone_name:
                    # Move the whole bone as a unit
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                
                # Neck bone should move as a unit
                elif "Neck" in exo_bone_name or "Neck" == smash_bone_name:
                    # Move the whole bone as a unit
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                
                # Head bone should move as a unit
                elif "Head" == exo_bone_name or "Head" == smash_bone_name:
                    # Move the whole bone as a unit
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                    
                    # Make all children of the Head bone follow it with the same offset
                    head_children = [bone for bone in armature.data.edit_bones if bone.parent and bone.parent.name == smash_bone_name]
                    for child_bone in head_children:
                        child_bone.head += offset
                        child_bone.tail += offset
                        processed_bones.add(child_bone.name)
                        
                        # Also move grandchildren (recursive approach for the entire head hierarchy)
                        def move_child_bones(parent_bone, offset_vector):
                            for bone in armature.data.edit_bones:
                                if bone.parent and bone.parent.name == parent_bone.name:
                                    bone.head += offset_vector
                                    bone.tail += offset_vector
                                    processed_bones.add(bone.name)
                                    # Recursive call to move this bone's children
                                    move_child_bones(bone, offset_vector)
                        
                        # Apply to all descendants
                        move_child_bones(child_bone, offset)
                
                # Hip bone should move as a unit
                elif ("Hip" in exo_bone_name or "hip" in exo_bone_name.lower() or 
                      "Hip" in smash_bone_name or "hip" in smash_bone_name.lower()):
                    # Move the whole bone as a unit
                    offset = exo_bone.head - smash_bone.head
                    smash_bone.head += offset
                    smash_bone.tail += offset
                    processed_bones.add(smash_bone_name)
                
                # Regular processing for all other bones
                else:
                    # Find the children of the Exo bone
                    exo_children = [b for b in armature.data.edit_bones if b.parent == exo_bone]
                    
                    if exo_children:
                        # Has children - Set the head to match the Exo bone's head
                        smash_bone.head = exo_bone.head.copy()
                        # Use the first child's head as the tail target
                        smash_bone.tail = exo_children[0].head.copy()
                    else:
                        # No children - move as a unit (like in TRANSLATE mode)
                        # Calculate the offset vector
                        offset = exo_bone.head - smash_bone.head
                        
                        # Move head and tail by the same offset
                        smash_bone.head += offset
                        smash_bone.tail += offset
                    
                    processed_bones.add(smash_bone_name)
            
            # Restore original roll if requested
            if self.maintain_roll and smash_bone_name in original_rolls:
                smash_bone.roll = original_rolls[smash_bone_name]
            
            aligned_bones += 1
        
        # Only adjust non-finger children if finger chains are moved as units
        if self.adjust_children:
            for bone in armature.data.edit_bones:
                # Skip finger bones and already processed bones
                if bone.name in processed_bones or bone.name in finger_bones:
                    continue
                    
                if bone.parent and bone.name in child_offsets:
                    # Only adjust children of bones we haven't aligned directly
                    parent_name = bone.parent.name
                    if parent_name in exo_bone_mapping.values():
                        # Skip children of aligned bones as they've already been handled
                        continue
                    
                    # Restore position relative to parent
                    offset = child_offsets[bone.name]
                    parent_head = bone.parent.head
                    bone.head = Vector([
                        parent_head[0] + offset[0],
                        parent_head[1] + offset[1],
                        parent_head[2] + offset[2]
                    ])
        
        # Report results
        if aligned_bones == 0:
            self.report({'WARNING'}, "No bones were aligned. Make sure you have Exo bones set up with constraints.")
            return {'CANCELLED'}
        else:
            self.report({'INFO'}, f"Successfully aligned {aligned_bones} bones")
            return {'FINISHED'}

class SUB_OP_add_single_exo_constraint(Operator):
    bl_idname = "sub.add_single_exo_constraint"
    bl_label = "Add Single Exo Constraint"
    bl_description = "Add a helper bone constraint between a selected exo bone and a smash bone"
    bl_options = {'REGISTER', 'UNDO'}
    
    exo_bone: StringProperty(
        name="Exo Bone",
        description="The exo bone to constrain",
        default=""
    )
    
    smash_bone: StringProperty(
        name="Smash Bone",
        description="The smash bone to constrain to",
        default=""
    )
    
    @classmethod
    def poll(cls, context):
        # Ensure we have an armature with helper bone data
        return (context.active_object and 
                context.active_object.type == 'ARMATURE' and 
                hasattr(context.active_object.data, "sub_helper_bone_data"))
    
    def invoke(self, context, event):
        # Ensure the property exists
        if not hasattr(bpy.types.Scene, "filtered_exo_bones"):
            register_filtered_bones()
            
        # Clear and repopulate the filtered collection
        context.scene.filtered_exo_bones.clear()
        for bone in context.active_object.pose.bones:
            if bone.name.startswith('H_Exo_'):
                item = context.scene.filtered_exo_bones.add()
                item.name = bone.name
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        armature = context.active_object
        
        # Show filtered exo bones for selection
        layout.prop_search(self, "exo_bone", context.scene, "filtered_exo_bones", text="Exo Bone")
        
        # Show all bones for the smash bone selection
        layout.prop_search(self, "smash_bone", armature.pose, "bones", text="Smash Bone")
    
    def execute(self, context):
        armature = context.active_object
        
        # Validate bone selections
        if not self.exo_bone or not self.smash_bone:
            self.report({'ERROR'}, "Please select both an exo bone and a smash bone")
            return {'CANCELLED'}
        
        exo_bone = armature.pose.bones.get(self.exo_bone)
        smash_bone = armature.pose.bones.get(self.smash_bone)
        
        if not exo_bone or not smash_bone:
            self.report({'ERROR'}, "Selected bones not found in armature")
            return {'CANCELLED'}
        
        if not smash_bone.parent:
            self.report({'ERROR'}, f'Cannot constrain to {smash_bone.name}. It has no parent.')
            return {'CANCELLED'}
        
        # Switch to edit mode to adjust bone positions
        bpy.ops.object.mode_set(mode='EDIT')
        
        # Get edit bones
        edit_exo_bone = armature.data.edit_bones[self.exo_bone]
        edit_smash_bone = armature.data.edit_bones[self.smash_bone]
        
        # Store original exo bone data
        original_head = edit_exo_bone.head.copy()
        original_tail = edit_exo_bone.tail.copy()
        original_roll = edit_exo_bone.roll
        original_matrix = edit_exo_bone.matrix.copy()
        
        # Store original vectors
        original_y = (original_tail - original_head).normalized()
        original_x = original_matrix.to_3x3().col[0].normalized()
        original_z = original_matrix.to_3x3().col[2].normalized()
        
        # Calculate relative scale
        original_length = (original_tail - original_head).length
        target_length = (edit_smash_bone.tail - edit_smash_bone.head).length
        scale_factor = original_length / target_length if target_length > 0 else 1.0
        
        # Calculate the offset to maintain position
        offset = original_head - edit_smash_bone.head
        
        # Set position while maintaining orientation
        edit_exo_bone.head = edit_smash_bone.head + offset
        edit_exo_bone.tail = edit_smash_bone.tail + offset
        
        # Restore original roll
        edit_exo_bone.roll = original_roll
        
        # Move all child bones with the same offset
        for bone in armature.data.edit_bones:
            if bone.parent == edit_exo_bone:
                child_offset = bone.head - bone.parent.tail
                bone.head = edit_exo_bone.tail + child_offset
                bone.tail = bone.head + (bone.tail - bone.head)
        
        # Switch back to pose mode
        bpy.ops.object.mode_set(mode='POSE')
        
        # Add the constraint to helper bone data
        shbd = armature.data.sub_helper_bone_data
        new_constraint = shbd.orient_constraints.add()
        
        # Generate a unique index for the constraint name
        existing_indices = []
        for constraint in shbd.orient_constraints:
            if constraint.name.startswith('nuHelperBoneRotateInterp'):
                try:
                    index = int(constraint.name[21:])  # Extract number after 'nuHelperBoneRotateInterp'
                    existing_indices.append(index)
                except ValueError:
                    continue
        
        new_index = 3000
        while new_index in existing_indices:
            new_index += 1
        
        # Set up the constraint
        new_constraint.name = f'nuHelperBoneRotateInterp{new_index}'
        new_constraint.parent_bone_name1 = smash_bone.parent.name
        new_constraint.parent_bone_name2 = smash_bone.parent.name
        new_constraint.source_bone_name = smash_bone.name
        new_constraint.target_bone_name = exo_bone.name
        new_constraint.unk_type = 1
        new_constraint.constraint_axes = [1.0, 1.0, 1.0]
        new_constraint.quat1 = [0.0, 0.0, 0.0, 1.0]
        new_constraint.quat2 = [0.0, 0.0, 0.0, 1.0]
        new_constraint.range_min = [-180.0, -180.0, -180.0]
        new_constraint.range_max = [180.0, 180.0, 180.0]
        
        # Refresh the constraints
        from ..model.import_model import refresh_helper_bone_constraints
        refresh_helper_bone_constraints(armature)
        
        self.report({'INFO'}, f'Successfully added constraint between {exo_bone.name} and {smash_bone.name}')
        return {'FINISHED'}

def register():
    register_filtered_bones()
    bpy.utils.register_class(SUB_OP_align_exo_bones)
    bpy.utils.register_class(SUB_OP_add_single_exo_constraint)

def unregister():
    bpy.utils.unregister_class(SUB_OP_add_single_exo_constraint)
    bpy.utils.unregister_class(SUB_OP_align_exo_bones)
    unregister_filtered_bones()

if __name__ == "__main__":
    register() 