import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty, FloatProperty

class SUB_OP_cleanup_unused_exo_bones(Operator):
    bl_idname = "sub.cleanup_unused_exo_bones"
    bl_label = "Cleanup Unused Exo Bones"
    bl_description = "Remove unused exo bones by transferring their weights to the nearest paired exo bone in the parent chain"
    bl_options = {'REGISTER', 'UNDO'}
    
    weight_transfer_method: EnumProperty(
        name="Weight Transfer Method",
        description="How to transfer weights from unused bones to their parents",
        items=[
            ('PARENT', "Recursive Parent Chain", "Transfer weights up the parent chain until reaching a paired exo bone"),
            ('CONSTRAINT', "Direct Constraint Target", "Transfer weights directly to the constraint target bone (if available)")
        ],
        default='PARENT'
    )
    
    delete_bones: BoolProperty(
        name="Delete Unused Bones",
        description="Delete unused exo bones after transferring weights",
        default=True
    )
    
    show_report: BoolProperty(
        name="Show Detailed Report",
        description="Show a detailed report of the changes made",
        default=True
    )
    
    
    @classmethod
    def poll(cls, context):
        # Ensure we have an armature with helper bone data
        return (context.active_object and 
                context.active_object.type == 'ARMATURE' and 
                hasattr(context.active_object.data, "sub_helper_bone_data"))
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "weight_transfer_method")
        layout.prop(self, "delete_bones")
        layout.prop(self, "show_report")
        
        # Add explanation about the weight transfer method
        box = layout.box()
        box.label(text="Weight Transfer Method:")
        box.label(text="• Finds exo bones not paired with standard bones")
        box.label(text="• Transfers weights up parent chain recursively")
        box.label(text="• Stops at first exo bone paired with standard bone")
    
    def execute(self, context):
        armature = context.active_object
        helper_bone_data = armature.data.sub_helper_bone_data
        
        # Get used exo bones from helper bone data - these are exo bones paired with standard bones
        used_exo_bones = set()
        
        # Process orient constraints to find which exo bones are paired with standard bones
        for constraint in helper_bone_data.orient_constraints:
            # Check if this is an Exo bone constraint where the target is an exo bone
            if "H_Exo_" in constraint.target_bone_name:
                # The source bone should be a standard bone (not starting with H_Exo_)
                if not constraint.source_bone_name.startswith("H_Exo_"):
                    used_exo_bones.add(constraint.target_bone_name)
        
        # Process aim constraints as well
        for constraint in helper_bone_data.aim_constraints:
            # Check for exo bones that are paired with standard bones
            if "H_Exo_" in constraint.aim_bone_name1 and not constraint.target_bone_name1.startswith("H_Exo_"):
                used_exo_bones.add(constraint.aim_bone_name1)
            if "H_Exo_" in constraint.aim_bone_name2 and not constraint.target_bone_name2.startswith("H_Exo_"):
                used_exo_bones.add(constraint.aim_bone_name2)
        
        # Find all exo bones in the armature
        all_exo_bones = []
        exo_bone_parents = {}
        
        # Switch to object mode for the initial scan
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        for bone in armature.pose.bones:
            if "H_Exo_" in bone.name:
                all_exo_bones.append(bone.name)
                # Store parent information for each exo bone
                if bone.parent:
                    exo_bone_parents[bone.name] = bone.parent.name
        
        # Find unused exo bones
        unused_exo_bones = [bone for bone in all_exo_bones if bone not in used_exo_bones]
        
        # If there are no unused bones, report and exit
        if not unused_exo_bones:
            self.report({'INFO'}, "No unused exo bones found to clean up")
            return {'CANCELLED'}
        
        # Track which bones to delete and process weights
        bones_to_delete = []
        weights_transferred = 0
        
        # Helper function to find the target bone for weight transfer
        def find_target_bone_for_transfer(bone_name, processed_bones=None):
            if processed_bones is None:
                processed_bones = set()
            
            # Prevent infinite recursion
            if bone_name in processed_bones:
                return None
            processed_bones.add(bone_name)
            
            # If this bone is already paired with a standard bone, use it as target
            if bone_name in used_exo_bones:
                return bone_name
            
            if self.weight_transfer_method == 'CONSTRAINT':
                # For constraint method, look for direct constraint targets
                for constraint in helper_bone_data.orient_constraints:
                    if constraint.target_bone_name == bone_name and constraint.source_bone_name not in processed_bones:
                        return find_target_bone_for_transfer(constraint.source_bone_name, processed_bones)
                # If no constraint found, fall back to parent
                if bone_name in exo_bone_parents:
                    parent_name = exo_bone_parents[bone_name]
                    return find_target_bone_for_transfer(parent_name, processed_bones)
            else:
                # For PARENT method (recursive parent chain)
                if bone_name in exo_bone_parents:
                    parent_name = exo_bone_parents[bone_name]
                    return find_target_bone_for_transfer(parent_name, processed_bones)
            
            return None
        
        # Process each mesh object parented to the armature to transfer weights
        if armature.children:
            for obj in armature.children:
                if obj.type == 'MESH' and obj.vertex_groups:
                    weights_modified = False
                    
                    # Process each unused exo bone
                    for bone_name in unused_exo_bones:
                        # Skip if the mesh doesn't have a vertex group for this bone
                        if bone_name not in obj.vertex_groups:
                            continue
                        
                        # Get the source vertex group
                        source_group = obj.vertex_groups[bone_name]
                        source_group_index = source_group.index
                        
                        # Find the target bone using recursive parent search
                        target_bone_name = find_target_bone_for_transfer(bone_name)
                        
                        # If we don't have a target bone, skip this bone
                        if not target_bone_name:
                            self.report({'WARNING'}, f"Could not find suitable target bone for {bone_name}")
                            continue
                        
                        # Create target vertex group if it doesn't exist
                        if target_bone_name not in obj.vertex_groups:
                            target_group = obj.vertex_groups.new(name=target_bone_name)
                        else:
                            target_group = obj.vertex_groups[target_bone_name]
                        
                        # Transfer weights using the same method as Remove Selected Bones
                        for v in obj.data.vertices:
                            # Find weight in source group
                            source_weight = 0
                            for g in v.groups:
                                if g.group == source_group_index:
                                    source_weight = g.weight
                                    break
                            
                            if source_weight > 0:
                                # Use ADD mode like the Remove Selected Bones method
                                target_group.add([v.index], source_weight, 'ADD')
                        
                        # Remove the source vertex group after transfer
                        obj.vertex_groups.remove(source_group)
                        weights_transferred += 1
                        weights_modified = True
                        
                        # Add to the list of bones to delete
                        if self.delete_bones and bone_name not in bones_to_delete:
                            bones_to_delete.append(bone_name)
                    
                    # Update the mesh if weights were modified
                    if weights_modified:
                        obj.data.update()
        
        # Delete the unused bones if requested
        deleted_bones = 0
        if self.delete_bones and bones_to_delete:
            # Switch to edit mode to delete bones
            bpy.ops.object.mode_set(mode='EDIT')
            
            # Delete the bones
            for bone_name in bones_to_delete:
                if bone_name in armature.data.edit_bones:
                    armature.data.edit_bones.remove(armature.data.edit_bones[bone_name])
                    deleted_bones += 1
            
            # Switch back to object mode
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # Generate report
        if self.show_report:
            report_msg = f"Found {len(unused_exo_bones)} unused exo bones.\n"
            report_msg += f"Transferred weights for {weights_transferred} bones.\n"
            if self.delete_bones:
                report_msg += f"Deleted {deleted_bones} bones."
            self.report({'INFO'}, report_msg)
        else:
            self.report({'INFO'}, f"Processed {len(unused_exo_bones)} unused exo bones")
        
        return {'FINISHED'}

def register():
    bpy.utils.register_class(SUB_OP_cleanup_unused_exo_bones)

def unregister():
    bpy.utils.unregister_class(SUB_OP_cleanup_unused_exo_bones)

if __name__ == "__main__":
    register() 