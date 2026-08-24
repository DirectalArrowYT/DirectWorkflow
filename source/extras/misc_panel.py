import bpy

from bpy.types import Panel, Operator

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..anim.anim_data import SUB_PG_sub_anim_data

class SUB_PT_animation_tools(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Animation Tools'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        modes = ['POSE', 'OBJECT', 'EDIT_ARMATURE']  # Allow panel in all these modes
        return context.mode in modes

    def draw(self, context):
        ssp: SUB_PG_sub_anim_data = context.scene.sub_scene_properties

        layout = self.layout
        layout.use_property_split = False

        # Add idle pose library (moved to top)
        box = layout.box()
        
        # Collapsible header with toggle
        header_row = box.row()
        header_row.prop(ssp, "idle_pose_library_expanded", 
                       icon="TRIA_DOWN" if ssp.idle_pose_library_expanded else "TRIA_RIGHT",
                       icon_only=True, emboss=False)
        header_row.label(text="Idle Pose Library")
        
        # Only show content if expanded
        if ssp.idle_pose_library_expanded:
            # Checkboxes
            row = box.row(align=True)
            row.prop(ssp, "idle_pose_include_trans", text="Include Trans Bone")
            
            row = box.row(align=True)
            row.prop(ssp, "idle_pose_mirrored", text="Mirrored")
            row.prop(ssp, "idle_pose_180_rotate", text="180 Rotate")
            
            # Store custom pose button (moved above dropdown)
            row = box.row(align=True)
            row.operator("sub.store_idle_pose", text="Store Custom Pose")
            
            # Dropdown for pose selection using template_list
            row = box.row()
            row.template_list("UI_UL_list", "idle_pose_list", ssp, "idle_pose_list", ssp, "idle_pose_list_index")
            
            # Apply button for selected pose
            row = box.row(align=True)
            if ssp.idle_pose_list and ssp.idle_pose_list_index < len(ssp.idle_pose_list):
                row.operator("sub.apply_idle_pose_from_list", text="Apply Selected Pose")
            else:
                row.enabled = False
                row.operator("sub.apply_idle_pose_from_list", text="Apply Selected Pose (None Selected)")

        layout.separator()

        # User Poses section (below Idle Pose Library)
        box = layout.box()

        header_row = box.row()
        header_row.prop(ssp, "user_poses_expanded",
                        icon="TRIA_DOWN" if ssp.user_poses_expanded else "TRIA_RIGHT",
                        icon_only=True, emboss=False)
        header_row.label(text="User Poses")

        if ssp.user_poses_expanded:
            # Controls above list
            controls = box.row(align=True)
            controls.operator("sub.user_pose_add", text="Add (+)", icon='ADD')
            controls.operator("sub.user_pose_remove", text="Remove (-)", icon='REMOVE')

            # List
            row = box.row()
            row.template_list("UI_UL_list", "user_pose_list", ssp, "user_pose_list", ssp, "user_pose_list_index")

            # Options and Apply below list
            box.prop(ssp, "user_pose_apply_only_selected", text="Apply to only selected bones")

            apply_row = box.row(align=True)
            if ssp.user_pose_list and ssp.user_pose_list_index < len(ssp.user_pose_list):
                apply_row.operator("sub.user_pose_apply_selected", text="Apply to Current Frame")
            else:
                apply_row.enabled = False
                apply_row.operator("sub.user_pose_apply_selected", text="Apply to Current Frame (None Selected)")

        # Add IK Tools collapsible section (similar to Idle Pose Library)
        box = layout.box()
        
        # Collapsible header with toggle
        header_row = box.row()
        header_row.prop(ssp, "ik_tools_expanded", 
                       icon="TRIA_DOWN" if ssp.ik_tools_expanded else "TRIA_RIGHT",
                       icon_only=True, emboss=False)
        header_row.label(text="IK Tools")
        
        # Only show content if expanded
        if ssp.ik_tools_expanded:
            if context.mode != 'EDIT_ARMATURE':
                # IK Setup section (using OG operators)
                col = box.column(align=True)
                col.label(text="IK Setup:")
                col.operator("sub.create_ik_bones", text="Create IK Bones (Arms + Legs)")
                col.operator("sub.create_arm_ik", text="Create Arm IK Bones")
                col.operator("sub.create_foot_ik", text="Create Foot IK Bones")
                # Only show buttons for operators that are registered
                if hasattr(bpy.types, 'SUB_OP_quick_switch_ik_fk') or hasattr(bpy.ops, 'sub.quick_switch_ik_fk'):
                    col.operator("sub.quick_switch_ik_fk", text="Switch IK/FK")
                col.separator()
                
                # Animation Tools section
                col.label(text="Animation Tools:")
                col.operator("sub.apply_ik_animation", text="Bake & Remove IK/FK")
                col.separator()
                
                # IK/FK Control section (moved to bottom)
                col.label(text="IK/FK Control:")
                if hasattr(bpy.types, 'SUB_OP_advanced_ik_fk_control') or hasattr(bpy.ops, 'sub.advanced_ik_fk_control'):
                    col.operator("sub.advanced_ik_fk_control", text="Advanced IK/FK Control")
                col.operator("sub.toggle_ik_influence", text="Toggle IK Influence")

        layout.separator()
        
        # Add button for hip animation transfer
        row = layout.row(align=True)
        row.operator("sub.transfer_hip_animation", text="Transfer Hip to Trans (Forward)")
        row2 = layout.row(align=True)
        row2.operator("sub.transfer_hip_jump_animation", text="Transfer Hip to Trans (Jump)")
        
        # Add Mirror Animation section
        layout.separator()
        box = layout.box()
        
        # Collapsible header with toggle
        ssp = context.scene.sub_scene_properties
        header_row = box.row()
        header_row.prop(ssp, "mirror_animation_expanded", 
                       icon="TRIA_DOWN" if ssp.mirror_animation_expanded else "TRIA_RIGHT",
                       icon_only=True, emboss=False)
        header_row.label(text="Mirror Animation")
        
        # Only show content if expanded
        if ssp.mirror_animation_expanded:
            col = box.column(align=True)
            
            # Add some spacing
            col.separator()
            
            # Mirror space option
            col.prop(ssp, "mirror_space", text="Space")
            
            # Add spacing between dropdown and button
            col.separator()
            
            # Mirror Animation button
            col.operator("sub.mirror_action", text="Mirror Animation")
            
            # Add bottom spacing
            col.separator()
        else:
            # Show simple button when collapsed
            row = box.row(align=True)
            row.operator("sub.mirror_action", text="Mirror Animation")
        
        # Add Reset Bone Locations button - just a button, not a section
        row = layout.row(align=True)
        row.operator("sub.reset_bone_locations", text="Reset Bone Locations")
        
        # Add Ground Character button
        row = layout.row(align=True)
        row.operator("sub.ground_character", text="Ground Character")
        
        # Add Invert Rotation button (only available in pose mode with selected bones)
        row = layout.row(align=True)
        if context.mode == 'POSE' and context.selected_pose_bones:
            row.operator("sub.invert_rotation_values", text="Invert Positive and Negative")
        else:
            row.enabled = False
            if context.mode != 'POSE':
                row.operator("sub.invert_rotation_values", text="Invert Positive and Negative (Pose Mode Only)")
            else:
                row.operator("sub.invert_rotation_values", text="Invert Positive and Negative (Select Bones)")

class SUB_PT_misc_utilities(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Misc.'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        modes = ['POSE', 'OBJECT', 'EDIT_ARMATURE']  # Allow panel in all these modes
        return context.mode in modes

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False

        # Eye Material Custom Vector 31 Modal Operator
        row = layout.row(align=True)
        row.operator("sub.eye_material_custom_vector_31_modal")
        
        # Show bone removal button only in edit mode or grayed out
        row = layout.row(align=True)
        if context.mode == 'EDIT_ARMATURE':
            row.operator("sub.remove_selected_bones")
        else:
            row.enabled = False
            row.operator("sub.remove_selected_bones", text="Remove Bones (Edit Mode Only)")
        
        # Add collapsible model tools section
        layout.separator()
        box = layout.box()
        
        # Get scene properties
        ssp = context.scene.sub_scene_properties
        
        # Collapsible header with toggle
        header_row = box.row()
        header_row.prop(ssp, "model_tools_expanded", 
                       icon="TRIA_DOWN" if ssp.model_tools_expanded else "TRIA_RIGHT",
                       icon_only=True, emboss=False)
        header_row.label(text="Model Tools")
        
        # Only show content if expanded
        if ssp.model_tools_expanded:
            row = box.row(align=True)
            row.operator("sub.limit_weights", text="Limit Weights to 4")
            
            # Add Mirror Vertex Groups button
            row = box.row(align=True)
            if context.mode == 'OBJECT':
                row.operator("sub.mirror_vertex_groups", text="Mirror Vertex Groups")
            else:
                row.enabled = False
                row.operator("sub.mirror_vertex_groups", text="Mirror Vertex Groups (Object Mode Only)")
            
            # Add Shape Keys to Meshes conversion
            row = box.row(align=True)
            if context.mode == 'OBJECT':
                row.label(text="Shape Keys Prefix:")
                row.prop(ssp, "shape_keys_prefix", text="")
            else:
                row.enabled = False
                row.label(text="Shape Keys Prefix (Object Mode Only)")
            
            row = box.row(align=True)
            if context.mode == 'OBJECT':
                row.operator("sub.convert_shape_keys_to_meshes", text="Convert Shape Keys to Meshes")
            else:
                row.enabled = False
                row.operator("sub.convert_shape_keys_to_meshes", text="Convert Shape Keys to Meshes (Object Mode Only)")
        
    
        
class SUB_OP_mirror_vertex_groups(bpy.types.Operator):
    bl_idname = "sub.mirror_vertex_groups"
    bl_label = "Mirror Vertex Groups"
    bl_description = "Swap L and R in vertex group names (e.g., ClavicleR to ClavicleL), avoiding certain words/numbers."
    bl_options = {'REGISTER', 'UNDO'}

    keywords = [
        'Clavicle', 'Shoulder', 'Arm', 'Hand', 'Finger', 'Leg', 'Knee', 'Foot', 'Toe',
        '10', '11', '12', '13', '20', '21', '22', '23', '30', '31', '32', '33',
        '40', '41', '42', '43', '51', '52', '53'
    ]

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and context.mode == 'OBJECT'

    def execute(self, context):
        obj = context.active_object
        vgs = obj.vertex_groups
        keywords = tuple(self.keywords)
        rename_map = {}

        # Find all groups to rename
        for vg in vgs:
            name = vg.name
            if not any(k in name for k in keywords):
                continue
            # Swap L <-> R before digits at the end or at the very end
            import re
            m = re.match(r'^(.*?)(L|R)(\d*)$', name)
            if m:
                base, side, digits = m.group(1), m.group(2), m.group(3)
                new_side = 'R' if side == 'L' else 'L'
                new_name = f"{base}{new_side}{digits}"
                rename_map[name] = new_name
            else:
                # Also handle ...L or ...R at the end
                if name.endswith('L'):
                    new_name = name[:-1] + 'R'
                    rename_map[name] = new_name
                elif name.endswith('R'):
                    new_name = name[:-1] + 'L'
                    rename_map[name] = new_name

        # To avoid collisions, use temp names
        temp_map = {old: f"__temp__{i}__" for i, old in enumerate(rename_map)}
        for old, temp in temp_map.items():
            vgs[old].name = temp
        for old, temp in temp_map.items():
            vgs[temp].name = rename_map[old]

        self.report({'INFO'}, f"Renamed {len(rename_map)} vertex groups.")
        return {'FINISHED'}


class SUB_OP_convert_shape_keys_to_meshes(bpy.types.Operator):
    bl_idname = "sub.convert_shape_keys_to_meshes"
    bl_label = "Convert Shape Keys to Meshes"
    bl_description = "Convert all shape keys to separate meshes with the specified prefix and VIS_O_OBJShape suffix"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and context.mode == 'OBJECT' and obj.data.shape_keys

    def execute(self, context):
        obj = context.active_object
        ssp = context.scene.sub_scene_properties
        prefix = ssp.shape_keys_prefix
        
        if not prefix:
            self.report({'ERROR'}, "Please enter a prefix for the shape keys.")
            return {'CANCELLED'}
        
        if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
            self.report({'ERROR'}, "No shape keys found on this mesh.")
            return {'CANCELLED'}
        
        # Create new meshes for each shape key
        created_meshes = 0
        for shape_key in obj.data.shape_keys.key_blocks:
            # Skip Basis
            if shape_key.name == "Basis":
                continue
            
            # Create a copy of the mesh
            new_mesh_obj = obj.copy()
            new_mesh_obj.data = obj.data.copy()
            
            # Create the new name: Prefix_ShapeKeyName_VIS_O_OBJShape
            new_name = f"{prefix}_{shape_key.name}_VIS_O_OBJShape"
            new_mesh_obj.name = new_name
            new_mesh_obj.data.name = new_name
            
            # Set the shape key as active and "show only shape key"
            new_mesh_obj.show_only_shape_key = True
            new_mesh_obj.active_shape_key_index = new_mesh_obj.data.shape_keys.key_blocks.find(shape_key.name)
            
            # Add a combined key from the mix
            new_mesh_obj.shape_key_add(name="_temp_combined_key", from_mix=True)
            
            # Remove all shape keys
            for sk in list(new_mesh_obj.data.shape_keys.key_blocks):
                new_mesh_obj.shape_key_remove(sk)
            
            # Add to the scene
            context.collection.objects.link(new_mesh_obj)
            created_meshes += 1
        
        self.report({'INFO'}, f"Created {created_meshes} meshes from shape keys.")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUB_PT_animation_tools)
    bpy.utils.register_class(SUB_PT_misc_utilities)
    bpy.utils.register_class(SUB_OP_mirror_vertex_groups)
    bpy.utils.register_class(SUB_OP_convert_shape_keys_to_meshes)


def unregister():
    bpy.utils.unregister_class(SUB_OP_convert_shape_keys_to_meshes)
    bpy.utils.unregister_class(SUB_OP_mirror_vertex_groups)
    bpy.utils.unregister_class(SUB_PT_misc_utilities)
    bpy.utils.unregister_class(SUB_PT_animation_tools)
        
    
        