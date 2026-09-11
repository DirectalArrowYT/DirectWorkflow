import bpy

from bpy.types import Panel, Operator

from ..model.material.convert_smash_material import (
    find_target_armature as find_material_armature,
    armature_has_converted_smash_materials,
    armature_has_unconverted_smash_materials,
)
from .create_animation_rig import (
    armature_has_animation_rig,
    armature_has_ik,
    armature_ik_is_enabled,
    find_target_armature as find_anim_rig_armature,
)

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

        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("sub.create_animation_rig", text="Create Animation Rig", icon="OUTLINER_OB_ARMATURE")
        row.operator("sub.remove_animation_rig", text="", icon="X")
        layout.prop(ssp, "clean_keyframes_after_rig", text="Clean keyframes after creation")

        arm = find_anim_rig_armature(context)
        if arm is not None and armature_has_ik(arm):
            has_arms = armature_has_ik(arm, 'ARMS')
            has_legs = armature_has_ik(arm, 'LEGS')
            if has_arms:
                row = layout.row(align=True)
                row.label(text="Arms")
                if armature_ik_is_enabled(arm, 'ARMS'):
                    op = row.operator("sub.anim_rig_toggle_ik_fk", text="Switch to FK", icon="BONE_DATA")
                else:
                    op = row.operator("sub.anim_rig_toggle_ik_fk", text="Switch to IK", icon="CON_KINEMATIC")
                op.limbs = 'ARMS'
            if has_legs:
                row = layout.row(align=True)
                row.label(text="Legs")
                if armature_ik_is_enabled(arm, 'LEGS'):
                    op = row.operator("sub.anim_rig_toggle_ik_fk", text="Switch to FK", icon="BONE_DATA")
                else:
                    op = row.operator("sub.anim_rig_toggle_ik_fk", text="Switch to IK", icon="CON_KINEMATIC")
                op.limbs = 'LEGS'
            if has_arms and has_legs:
                row = layout.row(align=True)
                row.label(text="Both")
                op = row.operator("sub.anim_rig_toggle_ik_fk", text="IK", icon="CON_KINEMATIC")
                op.limbs = 'BOTH'
                op.set_enabled = True
                op.enable_ik = True
                op = row.operator("sub.anim_rig_toggle_ik_fk", text="FK", icon="BONE_DATA")
                op.limbs = 'BOTH'
                op.set_enabled = True
                op.enable_ik = False

        from .finger_sliders import has_finger_sliders, finger_sliders_are_enabled
        if arm is not None and has_finger_sliders(arm):
            row = layout.row(align=True)
            row.label(text="Fingers")
            if finger_sliders_are_enabled(arm):
                op = row.operator("sub.toggle_finger_sliders", text="Switch to Circles", icon="MESH_CIRCLE")
                op.set_enabled = True
                op.enable_sliders = False
            else:
                op = row.operator("sub.toggle_finger_sliders", text="Switch to Sliders", icon="DRIVER")
                op.set_enabled = True
                op.enable_sliders = True

        if arm is not None and armature_has_animation_rig(arm):
            layout.operator("sub.bake_and_remove_rig", text="Bake and Remove Rig", icon="ACTION")

        layout.separator()

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
                if hasattr(bpy.types, 'SUB_OP_quick_switch_ik_fk'):
                    col.operator("sub.quick_switch_ik_fk", text="Switch IK/FK")
                col.separator()
                
                # Animation Tools section
                col.label(text="Animation Tools:")
                col.operator("sub.apply_ik_animation", text="Bake & Remove IK/FK")
                col.separator()
                
                # IK/FK Control section (moved to bottom)
                col.label(text="IK/FK Control:")
                if hasattr(bpy.types, 'SUB_OP_advanced_ik_fk_control'):
                    col.operator("sub.advanced_ik_fk_control", text="Advanced IK/FK Control")
                col.operator("sub.toggle_ik_influence", text="Toggle IK Influence")

                # Bulk IK sub-section
                bulk_box = box.box()
                bulk_header = bulk_box.row()
                bulk_header.prop(ssp, "bulk_ik_expanded",
                                 icon="TRIA_DOWN" if ssp.bulk_ik_expanded else "TRIA_RIGHT",
                                 icon_only=True, emboss=False)
                bulk_header.label(text="Bulk IK")

                if ssp.bulk_ik_expanded:
                    arm_obj = context.object
                    if arm_obj and arm_obj.type == 'ARMATURE':
                        bulk_col = bulk_box.column(align=True)
                        bulk_col.label(text="Left Leg:")
                        row = bulk_col.row(align=True)
                        row.prop_search(ssp, "bulk_ik_leg_l", arm_obj.data, "bones", text="Leg")
                        op = row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER')
                        op.target_property = "bulk_ik_leg_l"
                        row = bulk_col.row(align=True)
                        row.prop_search(ssp, "bulk_ik_knee_l", arm_obj.data, "bones", text="Knee")
                        op = row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER')
                        op.target_property = "bulk_ik_knee_l"
                        row = bulk_col.row(align=True)
                        row.prop_search(ssp, "bulk_ik_foot_l", arm_obj.data, "bones", text="Foot")
                        op = row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER')
                        op.target_property = "bulk_ik_foot_l"

                        bulk_col.separator()
                        bulk_col.label(text="Right Leg:")
                        row = bulk_col.row(align=True)
                        row.prop_search(ssp, "bulk_ik_leg_r", arm_obj.data, "bones", text="Leg")
                        op = row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER')
                        op.target_property = "bulk_ik_leg_r"
                        row = bulk_col.row(align=True)
                        row.prop_search(ssp, "bulk_ik_knee_r", arm_obj.data, "bones", text="Knee")
                        op = row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER')
                        op.target_property = "bulk_ik_knee_r"
                        row = bulk_col.row(align=True)
                        row.prop_search(ssp, "bulk_ik_foot_r", arm_obj.data, "bones", text="Foot")
                        op = row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER')
                        op.target_property = "bulk_ik_foot_r"

                        bulk_col.separator()
                        bulk_col.operator("sub.bulk_ik_match_all", text="Run Bulk IK on All Animations", icon='RENDER_ANIMATION')
                        bulk_col.operator("sub.bulk_ik_bake_all", text="Bulk Bake & Remove IK on All Animations", icon='EXPORT')
                    else:
                        bulk_box.label(text="Select an armature to configure Bulk IK", icon='INFO')

        layout.separator()
        
        # Hip <-> Trans root motion transfer, both directions.
        #
        # The reverse is not just undo: it re-derives the Hip curve from whatever the Trans
        # curve holds now, so motion edited on Trans comes back down to Hip. The jump pair was
        # registered but had no button, which made it reachable only from search.
        box = layout.box()
        box.label(text="Root motion (Hip <-> Trans):")
        row = box.row(align=True)
        row.operator("sub.transfer_hip_animation", text="Hip X -> Trans Z")
        row.operator("sub.transfer_trans_animation_to_hip", text="Trans Z -> Hip X")
        row = box.row(align=True)
        row.operator("sub.transfer_hip_jump_animation", text="Hip Y -> Trans X")
        row.operator("sub.transfer_trans_jump_animation_to_hip", text="Trans X -> Hip Y")
        box.label(
            text="Horizontal transfers mirror over the 3D cursor",
            icon='INFO',
        )

        
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
            col.prop(ssp, "mirror_smash_y_anim_flip", text="Smash Y Anim Flip")
            
            col.separator()
            col.operator("sub.find_custom_mirror_bones", text="Find Custom Bones")
            if ssp.mirror_custom_bones:
                included = sum(1 for item in ssp.mirror_custom_bones if item.include)
                col.label(text=f"Custom bones: {included}/{len(ssp.mirror_custom_bones)} set to mirror")
                col.template_list(
                    "SUB_UL_mirror_custom_bones",
                    "",
                    ssp,
                    "mirror_custom_bones",
                    ssp,
                    "mirror_custom_bones_index",
                    rows=6,
                )
                row = col.row(align=True)
                op_all = row.operator("sub.mirror_custom_bones_set_all", text="Check All")
                op_all.include = True
                op_none = row.operator("sub.mirror_custom_bones_set_all", text="Uncheck All")
                op_none.include = False
            else:
                col.label(text="Scan the armature to list extra bones")
            
            # Add spacing between dropdown and button
            col.separator()
            
            # Mirror Animation button
            col.operator("sub.mirror_action", text="Mirror Animation")
            col.operator("sub.mirror_all_actions", text="Mirror All Loaded Animations", icon='RENDER_ANIMATION')
            
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

        row = layout.row(align=True)
        row.operator("sub.remove_swing_bone_animation", text="Remove Animation from Swing Bones")

        row = layout.row(align=True)
        row.operator("sub.gif_or_photo", text="Gif or Photo", icon="RENDER_ANIMATION")

class SUB_PT_model_tools(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Model Tools'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        modes = ['POSE', 'OBJECT', 'EDIT_ARMATURE', 'EDIT_MESH']
        return context.mode in modes

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        ssp = context.scene.sub_scene_properties

        try:
            row = layout.row()
            row.scale_y = 1.4
            row.operator(
                "sub.smash_vp_shade_setup",
                text="Reload Smash Model",
                icon="FILE_REFRESH",
            )
        except Exception:
            pass

        row = layout.row(align=True)
        row.operator("sub.limit_weights", text="Limit Weights to 4")

        row = layout.row(align=True)
        if context.mode == 'OBJECT':
            row.operator("sub.mirror_vertex_groups", text="Mirror Vertex Groups")
        else:
            row.enabled = False
            row.operator("sub.mirror_vertex_groups", text="Mirror Vertex Groups (Object Mode Only)")

        row = layout.row(align=True)
        if context.mode == 'OBJECT':
            row.operator("sub.mirror_mesh_as_separate_object", text="Mirror Mesh as Separate Object")
        else:
            row.enabled = False
            row.operator("sub.mirror_mesh_as_separate_object", text="Mirror Mesh as Separate Object (Object Mode Only)")

        row = layout.row(align=True)
        selected_objects = context.selected_objects or []
        if context.mode not in {'POSE', 'EDIT_ARMATURE'} and (
            (context.active_object and context.active_object.type == 'MESH')
            or any(obj.type == 'MESH' for obj in selected_objects)
        ):
            row.operator("sub.unstack_uv_islands", text="Unstack UV Islands")
        else:
            row.enabled = False
            row.operator("sub.unstack_uv_islands", text="Unstack UV Islands (Select a Mesh)")
        row = layout.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("sub.smart_hair_seams", text="Smart Seams (Hair)", icon='MOD_UVPROJECT')
        row = layout.row(align=True)
        row.operator("sub.uv_align_upright", text="Align UVs Upright (Hair)", icon='SORT_DESC')

        row = layout.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("sub.merge_rigs", text="Merge Rigs", icon='GROUP_BONE')

        row = layout.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("sub.protect_datablocks", text="Protect Unused Data", icon='FAKE_USER_ON')


        row = layout.row(align=True)
        if context.mode == 'OBJECT':
            row.label(text="Shape Keys Prefix:")
            row.prop(ssp, "shape_keys_prefix", text="")
        else:
            row.enabled = False
            row.label(text="Shape Keys Prefix (Object Mode Only)")

        row = layout.row(align=True)
        if context.mode == 'OBJECT':
            row.operator("sub.convert_shape_keys_to_meshes", text="Convert Shape Keys to Meshes")
        else:
            row.enabled = False
            row.operator("sub.convert_shape_keys_to_meshes", text="Convert Shape Keys to Meshes (Object Mode Only)")

        row = layout.row(align=True)
        if context.mode == 'EDIT_ARMATURE':
            row.operator("sub.remove_selected_bones")
        else:
            row.enabled = False
            row.operator("sub.remove_selected_bones", text="Remove Bones (Edit Mode Only)")

        row = layout.row(align=True)
        selected_bones = context.selected_bones if context.mode == 'EDIT_ARMATURE' else None
        selected_pose_bones = context.selected_pose_bones if context.mode == 'POSE' else None
        if (context.mode == 'EDIT_ARMATURE' and selected_bones) or (
            context.mode == 'POSE' and selected_pose_bones
        ):
            row.operator("sub.connect_bone_chain", text="Connect Bone Chain")
        else:
            row.enabled = False
            row.operator("sub.connect_bone_chain", text="Connect Bone Chain (Select Bones)")

        row = layout.row(align=True)
        selected_objects = context.selected_objects or []
        active = context.active_object
        has_armature = bool(
            (active and active.type == 'ARMATURE')
            or any(obj.type == 'ARMATURE' for obj in selected_objects)
            or (active and active.type == 'MESH' and active.find_armature())
        )
        if has_armature:
            row.operator("sub.delete_unweighted_bones", text="Delete Unweighted Bones")
        else:
            row.enabled = False
            row.operator("sub.delete_unweighted_bones", text="Delete Unweighted Bones (Select Armature)")

        col = layout.column(align=True)
        col.separator()
        col.label(text="Roll Value Copier", icon="BONE_DATA")
        col.prop(ssp, "roll_copy_source")
        col.prop(ssp, "roll_copy_target")
        col.prop(ssp, "roll_copy_selected_only")
        button_row = layout.row()
        source = ssp.roll_copy_source
        target = ssp.roll_copy_target
        button_row.enabled = (
            source is not None
            and target is not None
            and source != target
            and source.type == "ARMATURE"
            and target.type == "ARMATURE"
        )
        button_row.operator("sub.copy_bone_rolls", icon="DUPLICATE")
        help_box = layout.box()
        help_box.label(text="Matches bone names exactly (case-sensitive).", icon="INFO")
        help_box.label(text="Only roll values are changed.")
        col = layout.column(align=True)
        col.separator()
        col.label(text="Bone Symmetry", icon="MOD_MIRROR")
        col.prop(ssp, "bone_sym_source")
        col.prop(ssp, "bone_sym_convention")
        sym_row = layout.row()
        active = context.active_object
        sym_row.enabled = active is not None and active.type == "ARMATURE"
        sym_row.operator("sub.bone_symmetrize", icon="MOD_MIRROR")
        layout.row().operator("sub.bone_symmetry_audit", icon="VIEWZOOM")
        sym_box = layout.box()
        if ssp.bone_sym_convention == "PURE":
            sym_box.alert = True
            sym_box.label(text="Pure mirror is NOT the Smash convention.", icon="ERROR")
            sym_box.label(text="Mirrored bones end up rolled 180 degrees")
            sym_box.label(text="from what the game expects.")
        else:
            sym_box.label(text="Mirrors head/tail and applies Smash's", icon="INFO")
            sym_box.label(text="roll rule (180 - roll), not a plain mirror.")
        sym_box.label(text="Geometry and parenting only - no constraints,")
        sym_box.label(text="custom shapes or vertex weights.")
        col = layout.column(align=True)
        col.separator()
        col.label(text="Vanilla Roll Preset", icon="PRESET")
        col.prop(ssp, "bone_sym_roll_preset")
        col.prop(ssp, "bone_sym_preset_selected_only")
        preset_row = layout.row(align=True)
        preset_row.enabled = active is not None and active.type == "ARMATURE"
        preset_row.operator("sub.apply_roll_preset", text="Preview", icon="VIEWZOOM").dry_run = True
        preset_row.operator("sub.apply_roll_preset", text="Apply", icon="CHECKMARK").dry_run = False
        preset_box = layout.box()
        preset_box.label(text="Sets shared bones to the vanilla rig's", icon="INFO")
        preset_box.label(text="rolls, so vanilla animations drive them")
        preset_box.label(text="correctly. Bones the vanilla rig doesn't")
        preset_box.label(text="have (scarves, coats, IK) are untouched.")

        adv = layout.box()
        adv.label(text="Advanced", icon="PREFERENCES")
        adv.prop(ssp, "bone_sym_extra_pairs")
        adv.prop(ssp, "bone_sym_center_eps")


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
        ssp = context.scene.sub_scene_properties

        eye_box = layout.box()
        eye_box.label(text="Eye Look (CustomVector31)", icon="HIDE_OFF")
        eye_box.operator("sub.setup_eye_cv31", icon="DRIVER")
        row = eye_box.row(align=True)
        row.operator("sub.eye_material_custom_vector_31_modal", text="Aim Eyes With Mouse")

        # Warn when the modal has nothing to edit. It cancels with a message
        # about loading an animation, which sends people looking in the wrong
        # place - the usual cause is a track that exists with no CV31 property.
        arma = context.object if (context.object and context.object.type == 'ARMATURE') else None
        if arma is not None:
            sap = arma.data.sub_anim_properties
            ready = any((t := sap.mat_tracks.get(n)) is not None
                        and t.properties.get('CustomVector31') is not None
                        for n in ('EyeL', 'EyeR'))
            if not ready:
                warn = eye_box.box()
                warn.alert = True
                warn.label(text="No EyeL/EyeR CustomVector31 yet -", icon="ERROR")
                warn.label(text="aiming will do nothing. Run Set Up first.")


        arma = context.object if (context.object and context.object.type == 'ARMATURE') else None
        if arma is not None:
            sap = arma.data.sub_anim_properties
            ready = any(
                (t := sap.mat_tracks.get(n)) is not None
                and t.properties.get('CustomVector31') is not None
                for n in ('EyeL', 'EyeR')
            )
            if not ready:
                warn = eye_box.box()
                warn.alert = True
                warn.label(text="No EyeL/EyeR CustomVector31 yet -", icon="ERROR")
                warn.label(text="aiming will do nothing. Run Set Up first.")

        eye_box.separator()
        eye_box.label(text="Look Control Rig", icon="BONE_DATA")
        eye_box.operator("sub.add_eye_look_control", icon="BONE_DATA")
        eye_box.operator("sub.match_eye_look_from_material", icon="KEYINGSET")
        eye_box.operator("sub.bake_eye_look", icon="KEYFRAME")
        eye_box.prop(ssp, "eye_look_live_preview")
        eye_box.prop(ssp, "eye_look_mode")
        rowlc = eye_box.row(align=True)
        if ssp.eye_look_mode == 'LOOK_AT':
            rowlc.prop(ssp, "eye_look_gain", text="Gain X")
            rowlc.prop(ssp, "eye_look_gain_y", text="Gain Y")
        else:
            rowlc.prop(ssp, "eye_look_sensitivity", text="Sens X")
            rowlc.prop(ssp, "eye_look_sensitivity_y", text="Sens Y")
        eye_box.prop(ssp, "eye_look_clamp")
        rowinv = eye_box.row(align=True)
        rowinv.prop(ssp, "eye_look_invert_x", toggle=True)
        rowinv.prop(ssp, "eye_look_invert_y", toggle=True)
        eye_box.prop(ssp, "eye_look_pupil_from_scale")
        if ssp.eye_look_pupil_from_scale:
            eye_box.prop(ssp, "eye_look_scale_about_pupil")
            if ssp.eye_look_scale_about_pupil:
                eye_box.prop(ssp, "eye_pupil_centre_auto")
                if not ssp.eye_pupil_centre_auto:
                    eye_box.prop(ssp, "eye_pupil_centre", text="Centre UV")
                eye_box.operator("sub.measure_pupil_centre", icon="EYEDROPPER")
            pupil_box = eye_box.box()
            pupil_box.label(text="Scale the control bone (S) to resize", icon="INFO")
            pupil_box.label(text="the pupil. Smaller bone = smaller pupil.")
        hint = eye_box.box()
        hint.label(text="Move the control in Pose Mode to preview,", icon="INFO")
        hint.label(text="then Bake Eyes so the look exports")
        hint.label(text="and the control bone is removed.")
        hint.label(text="Live preview alone won't export -")
        hint.label(text="export reads keyframes, not drivers.")
        hint.label(text="Turn on Live Preview for Solid Texture")
        hint.label(text="and Material look, including imported")
        hint.label(text="EyeL/EyeR material anims. Turn it off when done.")

        layout.separator()
        box = layout.box()
        box.label(text="Armature Materials", icon="MATERIAL")
        armature = find_material_armature(context)
        if armature is None:
            row = box.row()
            row.enabled = False
            row.operator("sub.convert_armature_smash_materials", text="Convert All to Principled BSDF", icon="MATERIAL")
            box.label(text="Select an armature.")
        elif armature_has_converted_smash_materials(armature):
            box.operator(
                "sub.revert_armature_smash_materials",
                text="Revert to Smash Material",
                icon="LOOP_BACK",
            )
        else:
            row = box.row()
            row.enabled = armature_has_unconverted_smash_materials(armature)
            row.operator(
                "sub.convert_armature_smash_materials",
                text="Convert All to Principled BSDF",
                icon="MATERIAL",
            )

        layout.separator()
        from .smash_viewport import draw_smash_viewport_ui
        draw_smash_viewport_ui(layout, context) 
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


def _mirror_mesh_geometry_x(mesh):
    from mathutils import Matrix

    matrix = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))
    try:
        mesh.transform(matrix, shape_keys=True)
    except TypeError:
        mesh.transform(matrix)
        if mesh.shape_keys:
            for key_block in mesh.shape_keys.key_blocks:
                for point in key_block.data:
                    point.co.x *= -1
    mesh.flip_normals()
    mesh.update()


def _flipped_mesh_name(name):
    vis_suffix = "_VIS_O_OBJShape"
    vis_index = name.find(vis_suffix)
    if vis_index != -1:
        return f"{name[:vis_index]}FLIP{name[vis_index:]}"
    return f"{name}FLIP"


class SUB_OP_mirror_mesh_as_separate_object(bpy.types.Operator):
    bl_idname = "sub.mirror_mesh_as_separate_object"
    bl_label = "Mirror Mesh as Separate Object"
    bl_description = "Duplicate the selected mesh as a new object that contains only the mirrored geometry"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        sources = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not sources:
            self.report({'ERROR'}, "Select a mesh object.")
            return {'CANCELLED'}

        created = []

        for obj in sources:
            new_mesh = obj.data.copy()
            new_obj = obj.copy()
            new_obj.data = new_mesh

            collections = list(obj.users_collection)
            if collections:
                for col in collections:
                    col.objects.link(new_obj)
            else:
                context.collection.objects.link(new_obj)

            flipped_name = _flipped_mesh_name(obj.name)
            new_obj.name = flipped_name
            new_obj.data.name = flipped_name

            _mirror_mesh_geometry_x(new_mesh)
            created.append(new_obj)

        for selected in list(context.selected_objects):
            selected.select_set(False)
        for new_obj in created:
            new_obj.select_set(True)
        context.view_layer.objects.active = created[-1]

        self.report({'INFO'}, f"Created {len(created)} mirrored mesh object(s).")
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
    bpy.utils.register_class(SUB_PT_model_tools)
    bpy.utils.register_class(SUB_PT_misc_utilities)
    bpy.utils.register_class(SUB_OP_mirror_vertex_groups)
    bpy.utils.register_class(SUB_OP_mirror_mesh_as_separate_object)
    bpy.utils.register_class(SUB_OP_convert_shape_keys_to_meshes)


def unregister():
    bpy.utils.unregister_class(SUB_OP_convert_shape_keys_to_meshes)
    bpy.utils.unregister_class(SUB_OP_mirror_mesh_as_separate_object)
    bpy.utils.unregister_class(SUB_OP_mirror_vertex_groups)
    bpy.utils.unregister_class(SUB_PT_misc_utilities)
    bpy.utils.unregister_class(SUB_PT_model_tools)
    bpy.utils.unregister_class(SUB_PT_animation_tools)
        
    
        