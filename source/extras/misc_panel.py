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

# ---------------------------------------------------------------------------
# Sidebar panels. Which tab each one sits in, and in what order, is decided in
# source/ui_tabs.py - keep bl_category/bl_order out of here.
# ---------------------------------------------------------------------------

_ARMATURE_MODES = {'POSE', 'OBJECT', 'EDIT_ARMATURE'}


class _SidebarPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash'


def _mode_button(layout, context, operator, text, modes, reason):
    """An operator button that is greyed out, with the reason, outside `modes`."""
    row = layout.row(align=True)
    if context.mode in modes:
        row.operator(operator, text=text)
    else:
        row.enabled = False
        row.operator(operator, text=f"{text} ({reason})")
    return row


# --- Smash Anim ---------------------------------------------------------------

class SUB_PT_anim_rig(_SidebarPanel, Panel):
    bl_label = 'Animation Rig'

    @classmethod
    def poll(cls, context):
        return context.mode in _ARMATURE_MODES

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        layout.use_property_split = False

        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("sub.create_animation_rig", text="Create Animation Rig", icon="OUTLINER_OB_ARMATURE")
        row.operator("sub.remove_animation_rig", text="", icon="X")
        layout.prop(ssp, "clean_keyframes_after_rig", text="Clean keyframes after creation")

        arm = find_anim_rig_armature(context)
        if arm is None:
            layout.label(text="Select a Smash armature.", icon="INFO")
            return

        if armature_has_ik(arm):
            box = layout.box()
            box.label(text="IK / FK", icon="CON_KINEMATIC")
            grid = box.column(align=True)
            for limbs, label in (('ARMS', "Arms"), ('LEGS', "Legs")):
                if not armature_has_ik(arm, limbs):
                    continue
                row = grid.row(align=True)
                row.label(text=label)
                if armature_ik_is_enabled(arm, limbs):
                    op = row.operator("sub.anim_rig_toggle_ik_fk", text="Switch to FK", icon="BONE_DATA")
                else:
                    op = row.operator("sub.anim_rig_toggle_ik_fk", text="Switch to IK", icon="CON_KINEMATIC")
                op.limbs = limbs
            if armature_has_ik(arm, 'ARMS') and armature_has_ik(arm, 'LEGS'):
                row = grid.row(align=True)
                row.label(text="Both")
                op = row.operator("sub.anim_rig_toggle_ik_fk", text="IK", icon="CON_KINEMATIC")
                op.limbs, op.set_enabled, op.enable_ik = 'BOTH', True, True
                op = row.operator("sub.anim_rig_toggle_ik_fk", text="FK", icon="BONE_DATA")
                op.limbs, op.set_enabled, op.enable_ik = 'BOTH', True, False
            from .smash_ik import has_ik_v2
            if has_ik_v2(arm):
                row = box.row(align=True)
                row.label(text="Snap")
                row.operator("sub.anim_rig_snap_ik_fk", text="IK → FK", icon="SNAP_ON").direction = 'IK_TO_FK'
                row.operator("sub.anim_rig_snap_ik_fk", text="FK → IK", icon="SNAP_ON").direction = 'FK_TO_IK'
            else:
                box.operator("sub.anim_rig_upgrade_ik", text="Upgrade IK (pole-driven)", icon="CON_KINEMATIC")

        from .finger_sliders import has_finger_sliders, finger_sliders_are_enabled
        if has_finger_sliders(arm):
            row = layout.row(align=True)
            row.label(text="Fingers", icon="VIEW_PAN")
            if finger_sliders_are_enabled(arm):
                op = row.operator("sub.toggle_finger_sliders", text="Circles", icon="MESH_CIRCLE")
                op.set_enabled, op.enable_sliders = True, False
            else:
                op = row.operator("sub.toggle_finger_sliders", text="Sliders", icon="DRIVER")
                op.set_enabled, op.enable_sliders = True, True

        col = layout.column(align=True)
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator("sub.rotate_animation", text="Rotate Animation", icon="DRIVER_ROTATIONAL_DIFFERENCE")
        if armature_has_animation_rig(arm):
            col.operator("sub.bake_and_remove_rig", text="Bake and Remove Rig", icon="ACTION")


class SUB_PT_anim_poses(_SidebarPanel, Panel):
    bl_label = 'Poses'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in _ARMATURE_MODES

    def draw(self, context):
        self.layout.label(text="Store and reuse poses on any frame.", icon="INFO")


class SUB_PT_anim_idle_poses(_SidebarPanel, Panel):
    bl_label = 'Idle Pose Library'
    bl_parent_id = 'SUB_PT_anim_poses'

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        row = layout.row(align=True)
        row.prop(ssp, "idle_pose_include_trans", text="Include Trans")
        row.prop(ssp, "idle_pose_mirrored", text="Mirrored")
        row.prop(ssp, "idle_pose_180_rotate", text="180°")
        layout.operator("sub.store_idle_pose", text="Store Custom Pose", icon="ADD")
        layout.template_list("UI_UL_list", "idle_pose_list", ssp, "idle_pose_list", ssp, "idle_pose_list_index")
        row = layout.row(align=True)
        row.enabled = bool(ssp.idle_pose_list) and ssp.idle_pose_list_index < len(ssp.idle_pose_list)
        row.operator("sub.apply_idle_pose_from_list", text="Apply Selected Pose", icon="CHECKMARK")


class SUB_PT_anim_user_poses(_SidebarPanel, Panel):
    bl_label = 'User Poses'
    bl_parent_id = 'SUB_PT_anim_poses'

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        row = layout.row(align=True)
        row.operator("sub.user_pose_add", text="Add", icon='ADD')
        row.operator("sub.user_pose_remove", text="Remove", icon='REMOVE')
        layout.template_list("UI_UL_list", "user_pose_list", ssp, "user_pose_list", ssp, "user_pose_list_index")
        layout.prop(ssp, "user_pose_apply_only_selected", text="Only selected bones")
        row = layout.row(align=True)
        row.enabled = bool(ssp.user_pose_list) and ssp.user_pose_list_index < len(ssp.user_pose_list)
        row.operator("sub.user_pose_apply_selected", text="Apply to Current Frame", icon="CHECKMARK")


def _eye_cv31_ready(arma):
    sap = arma.data.sub_anim_properties
    return any((t := sap.mat_tracks.get(n)) is not None and t.properties.get('CustomVector31') is not None
               for n in ('EyeL', 'EyeR'))


class SUB_PT_anim_eyes(_SidebarPanel, Panel):
    bl_label = 'Eyes'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in _ARMATURE_MODES

    def draw_header(self, context):
        self.layout.label(icon="HIDE_OFF")

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        layout.use_property_split = False

        col = layout.column(align=True)
        col.operator("sub.setup_eye_cv31", icon="DRIVER")
        col.operator("sub.eye_material_custom_vector_31_modal", text="Aim Eyes With Mouse", icon="RESTRICT_SELECT_OFF")
        arma = context.object if (context.object and context.object.type == 'ARMATURE') else None
        if arma is not None and not _eye_cv31_ready(arma):
            warn = layout.box()
            warn.alert = True
            warn.label(text="No EyeL/EyeR CustomVector31 yet -", icon="ERROR")
            warn.label(text="aiming does nothing. Run Set Up first.")

        box = layout.box()
        box.label(text="Look Control", icon="BONE_DATA")
        col = box.column(align=True)
        col.operator("sub.add_eye_look_control", icon="BONE_DATA")
        col.operator("sub.match_eye_look_from_material", icon="KEYINGSET")
        col.operator("sub.bake_eye_look", icon="KEYFRAME")
        box.prop(ssp, "eye_look_live_preview")
        box.prop(ssp, "eye_look_mode")
        row = box.row(align=True)
        if ssp.eye_look_mode == 'LOOK_AT':
            row.prop(ssp, "eye_look_gain", text="Gain X")
            row.prop(ssp, "eye_look_gain_y", text="Gain Y")
        else:
            row.prop(ssp, "eye_look_sensitivity", text="Sens X")
            row.prop(ssp, "eye_look_sensitivity_y", text="Sens Y")
        box.prop(ssp, "eye_look_clamp")
        row = box.row(align=True)
        row.prop(ssp, "eye_look_invert_x", toggle=True)
        row.prop(ssp, "eye_look_invert_y", toggle=True)
        box.prop(ssp, "eye_look_pupil_from_scale")
        if ssp.eye_look_pupil_from_scale:
            box.prop(ssp, "eye_look_scale_about_pupil")
            if ssp.eye_look_scale_about_pupil:
                box.prop(ssp, "eye_pupil_centre_auto")
                if not ssp.eye_pupil_centre_auto:
                    box.prop(ssp, "eye_pupil_centre", text="Centre UV")
                box.operator("sub.measure_pupil_centre", icon="EYEDROPPER")
            box.label(text="Scale the control (S) to resize the pupil.", icon="INFO")
        hint = layout.column(align=True)
        hint.scale_y = 0.8
        hint.label(text="Bake Eyes so the look exports -", icon="INFO")
        hint.label(text="export reads keyframes, not the live preview.")


class SUB_PT_anim_utilities(_SidebarPanel, Panel):
    bl_label = 'Animation Utilities'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in _ARMATURE_MODES

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False

        # Hip <-> Trans root motion transfer, both directions. The reverse re-derives the Hip
        # curve from whatever the Trans curve holds now, so motion edited on Trans comes back.
        box = layout.box()
        box.label(text="Root Motion (Hip ↔ Trans)", icon="ORIENTATION_GLOBAL")
        grid = box.grid_flow(columns=2, align=True)
        grid.operator("sub.transfer_hip_animation", text="Hip X → Trans Z")
        grid.operator("sub.transfer_trans_animation_to_hip", text="Trans Z → Hip X")
        grid.operator("sub.transfer_hip_jump_animation", text="Hip Y → Trans X")
        grid.operator("sub.transfer_trans_jump_animation_to_hip", text="Trans X → Hip Y")
        box.label(text="Horizontal transfers mirror over the 3D cursor", icon='INFO')

        col = layout.column(align=True)
        col.operator("sub.reset_bone_locations", text="Reset Bone Locations", icon="LOOP_BACK")
        col.operator("sub.ground_character", text="Ground Character", icon="TRIA_DOWN_BAR")
        row = col.row(align=True)
        if context.mode == 'POSE' and context.selected_pose_bones:
            row.operator("sub.invert_rotation_values", text="Invert Rotation Values", icon="ARROW_LEFTRIGHT")
        else:
            row.enabled = False
            reason = "Pose Mode" if context.mode != 'POSE' else "Select Bones"
            row.operator("sub.invert_rotation_values", text=f"Invert Rotation Values ({reason})",
                         icon="ARROW_LEFTRIGHT")
        col.operator("sub.remove_swing_bone_animation", text="Remove Swing Bone Animation", icon="TRASH")
        layout.operator("sub.gif_or_photo", text="Gif or Photo", icon="RENDER_ANIMATION")


class SUB_PT_anim_mirror(_SidebarPanel, Panel):
    bl_label = 'Mirror Animation'
    bl_parent_id = 'SUB_PT_anim_utilities'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        col = layout.column(align=True)
        col.prop(ssp, "mirror_space", text="Space")
        col.prop(ssp, "mirror_smash_y_anim_flip", text="Smash Y Anim Flip")
        layout.operator("sub.find_custom_mirror_bones", text="Find Custom Bones", icon="VIEWZOOM")
        if ssp.mirror_custom_bones:
            included = sum(1 for item in ssp.mirror_custom_bones if item.include)
            layout.label(text=f"Custom bones: {included}/{len(ssp.mirror_custom_bones)} set to mirror")
            layout.template_list("SUB_UL_mirror_custom_bones", "", ssp, "mirror_custom_bones",
                                 ssp, "mirror_custom_bones_index", rows=6)
            row = layout.row(align=True)
            row.operator("sub.mirror_custom_bones_set_all", text="Check All").include = True
            row.operator("sub.mirror_custom_bones_set_all", text="Uncheck All").include = False
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("sub.mirror_action", text="Mirror Animation", icon="MOD_MIRROR")
        col.operator("sub.mirror_all_actions", text="Mirror All Loaded Animations", icon='RENDER_ANIMATION')


class SUB_PT_anim_legacy_ik(_SidebarPanel, Panel):
    bl_label = 'Legacy IK'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'POSE', 'OBJECT'}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Animation Rig covers these; kept for old files.", icon="INFO")
        col = layout.column(align=True)
        col.label(text="Setup")
        col.operator("sub.create_ik_bones", text="Create IK Bones (Arms + Legs)")
        col.operator("sub.create_arm_ik", text="Create Arm IK Bones")
        col.operator("sub.create_foot_ik", text="Create Foot IK Bones")
        col = layout.column(align=True)
        col.label(text="Control")
        if hasattr(bpy.types, 'SUB_OP_quick_switch_ik_fk'):
            col.operator("sub.quick_switch_ik_fk", text="Switch IK/FK")
        if hasattr(bpy.types, 'SUB_OP_advanced_ik_fk_control'):
            col.operator("sub.advanced_ik_fk_control", text="Advanced IK/FK Control")
        col.operator("sub.toggle_ik_influence", text="Toggle IK Influence")
        layout.operator("sub.apply_ik_animation", text="Bake & Remove IK/FK", icon="RENDER_ANIMATION")


class SUB_PT_anim_bulk_ik(_SidebarPanel, Panel):
    bl_label = 'Bulk IK'
    bl_parent_id = 'SUB_PT_anim_legacy_ik'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        arm_obj = context.object
        if not (arm_obj and arm_obj.type == 'ARMATURE'):
            layout.label(text="Select an armature to configure Bulk IK", icon='INFO')
            return
        col = layout.column(align=True)
        for side, label in (('l', "Left Leg"), ('r', "Right Leg")):
            col.label(text=label)
            for part, text in (('leg', "Leg"), ('knee', "Knee"), ('foot', "Foot")):
                prop = f"bulk_ik_{part}_{side}"
                row = col.row(align=True)
                row.prop_search(ssp, prop, arm_obj.data, "bones", text=text)
                row.operator("sub.bulk_ik_pick_bone", text="", icon='EYEDROPPER').target_property = prop
            col.separator()
        col = layout.column(align=True)
        col.operator("sub.bulk_ik_match_all", text="Run Bulk IK on All Animations", icon='RENDER_ANIMATION')
        col.operator("sub.bulk_ik_bake_all", text="Bulk Bake & Remove IK", icon='EXPORT')


# --- Smash Model ---------------------------------------------------------------

class SUB_PT_model_viewport(_SidebarPanel, Panel):
    bl_label = 'Viewport & Materials'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        row = layout.row()
        row.scale_y = 1.4
        row.operator("sub.smash_vp_shade_setup", text="Reload Smash Model", icon="FILE_REFRESH")

        box = layout.box()
        box.label(text="Armature Materials", icon="MATERIAL")
        armature = find_material_armature(context)
        if armature is None:
            row = box.row()
            row.enabled = False
            row.operator("sub.convert_armature_smash_materials", text="Convert All to Principled BSDF",
                         icon="MATERIAL")
            box.label(text="Select an armature.")
        elif armature_has_converted_smash_materials(armature):
            box.operator("sub.revert_armature_smash_materials", text="Revert to Smash Material", icon="LOOP_BACK")
        else:
            row = box.row()
            row.enabled = armature_has_unconverted_smash_materials(armature)
            row.operator("sub.convert_armature_smash_materials", text="Convert All to Principled BSDF",
                         icon="MATERIAL")

        from .smash_viewport import draw_smash_viewport_ui
        draw_smash_viewport_ui(layout, context)


class SUB_PT_model_mesh(_SidebarPanel, Panel):
    bl_label = 'Mesh'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        layout.use_property_split = False
        col = layout.column(align=True)
        col.operator("sub.limit_weights", text="Limit Weights to 4", icon="MOD_VERTEX_WEIGHT")
        _mode_button(col, context, "sub.mirror_vertex_groups", "Mirror Vertex Groups", {'OBJECT'}, "Object Mode")
        _mode_button(col, context, "sub.mirror_mesh_as_separate_object", "Mirror Mesh as Separate Object",
                     {'OBJECT'}, "Object Mode")
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator("sub.protect_datablocks", text="Protect Unused Data", icon='FAKE_USER_ON')

        box = layout.box()
        box.label(text="Shape Keys → VIS Meshes", icon="SHAPEKEY_DATA")
        row = box.row(align=True)
        row.enabled = context.mode == 'OBJECT'
        row.prop(ssp, "shape_keys_prefix", text="Prefix")
        _mode_button(box, context, "sub.convert_shape_keys_to_meshes", "Convert Shape Keys to Meshes",
                     {'OBJECT'}, "Object Mode")


class SUB_PT_model_uvs(_SidebarPanel, Panel):
    bl_label = 'UVs & Normals'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        has_mesh = any(obj.type == 'MESH' for obj in (context.selected_objects or [])) or (
            context.active_object is not None and context.active_object.type == 'MESH')

        col = layout.column(align=True)
        col.enabled = has_mesh
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator("sub.unstack_uv_islands", text="Unstack UV Islands", icon="UV_ISLANDSEL")
        col.operator("sub.smart_hair_seams", text="Smart Seams (Hair)", icon='MOD_UVPROJECT')
        col.operator("sub.uv_align_upright", text="Align UVs Upright (Hair)", icon='SORT_DESC')

        box = layout.box()
        box.enabled = has_mesh
        box.label(text="Hair Bake UVs (keeps the cel UVs)", icon='TEXTURE')
        row = box.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("sub.hair_bake_uv", text="Make Bake UVs", icon='UV')
        row = box.row(align=True)
        row.operator("sub.hair_bake_uv_finalize", text="Use Bake UVs", icon='CHECKMARK')
        row.operator("sub.hair_bake_uv_restore", text="Restore Cel UVs", icon='LOOP_BACK')

        row = layout.row(align=True)
        row.enabled = has_mesh
        row.scale_y = 1.2
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("sub.smart_normals", text="Smart Normals", icon='NORMALS_VERTEX_FACE')
        if not has_mesh:
            layout.label(text="Select a mesh.", icon="INFO")


class SUB_PT_model_bones(_SidebarPanel, Panel):
    bl_label = 'Bones'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'POSE', 'OBJECT', 'EDIT_ARMATURE', 'EDIT_MESH'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        col = layout.column(align=True)
        _mode_button(col, context, "sub.remove_selected_bones", "Remove Selected Bones", {'EDIT_ARMATURE'},
                     "Edit Mode")
        has_selection = (context.mode == 'EDIT_ARMATURE' and context.selected_bones) or (
            context.mode == 'POSE' and context.selected_pose_bones)
        row = col.row(align=True)
        row.enabled = bool(has_selection)
        row.operator("sub.connect_bone_chain", text="Connect Bone Chain" if has_selection
                     else "Connect Bone Chain (Select Bones)")
        active = context.active_object
        has_armature = bool((active and active.type == 'ARMATURE')
                            or any(obj.type == 'ARMATURE' for obj in (context.selected_objects or []))
                            or (active and active.type == 'MESH' and active.find_armature()))
        row = col.row(align=True)
        row.enabled = has_armature
        row.operator("sub.delete_unweighted_bones", text="Delete Unweighted Bones")
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator("sub.merge_rigs", text="Merge Rigs", icon='GROUP_BONE')


class SUB_PT_model_bone_rolls(_SidebarPanel, Panel):
    bl_label = 'Roll Copier'
    bl_parent_id = 'SUB_PT_model_bones'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        col = layout.column(align=True)
        col.prop(ssp, "roll_copy_source")
        col.prop(ssp, "roll_copy_target")
        col.prop(ssp, "roll_copy_selected_only")
        source, target = ssp.roll_copy_source, ssp.roll_copy_target
        row = layout.row()
        row.enabled = (source is not None and target is not None and source != target
                       and source.type == "ARMATURE" and target.type == "ARMATURE")
        row.operator("sub.copy_bone_rolls", icon="DUPLICATE")
        layout.label(text="Matches bone names exactly; only rolls change.", icon="INFO")


class SUB_PT_model_bone_symmetry(_SidebarPanel, Panel):
    bl_label = 'Bone Symmetry'
    bl_parent_id = 'SUB_PT_model_bones'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        col = layout.column(align=True)
        col.prop(ssp, "bone_sym_source")
        col.prop(ssp, "bone_sym_convention")
        active = context.active_object
        row = layout.row(align=True)
        row.enabled = active is not None and active.type == "ARMATURE"
        row.operator("sub.bone_symmetrize", icon="MOD_MIRROR")
        row.operator("sub.bone_symmetry_audit", icon="VIEWZOOM", text="Audit")
        box = layout.box()
        box.scale_y = 0.8
        if ssp.bone_sym_convention == "PURE":
            box.alert = True
            box.label(text="Pure mirror is NOT the Smash convention:", icon="ERROR")
            box.label(text="mirrored bones end up rolled 180°.")
        else:
            box.label(text="Mirrors head/tail with Smash's roll rule", icon="INFO")
            box.label(text="(180 - roll). Geometry and parenting only.")
        adv = layout.column(align=True)
        adv.prop(ssp, "bone_sym_extra_pairs")
        adv.prop(ssp, "bone_sym_center_eps")


class SUB_PT_model_roll_preset(_SidebarPanel, Panel):
    bl_label = 'Vanilla Roll Preset'
    bl_parent_id = 'SUB_PT_model_bones'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp = context.scene.sub_scene_properties
        layout = self.layout
        col = layout.column(align=True)
        col.prop(ssp, "bone_sym_roll_preset")
        col.prop(ssp, "bone_sym_preset_selected_only")
        active = context.active_object
        row = layout.row(align=True)
        row.enabled = active is not None and active.type == "ARMATURE"
        row.operator("sub.apply_roll_preset", text="Preview", icon="VIEWZOOM").dry_run = True
        row.operator("sub.apply_roll_preset", text="Apply", icon="CHECKMARK").dry_run = False
        box = layout.box()
        box.scale_y = 0.8
        box.label(text="Sets shared bones to the vanilla rig's rolls,", icon="INFO")
        box.label(text="so vanilla animations drive them correctly.")
        box.label(text="Extra bones (scarves, coats, IK) are untouched.")


class SUB_PT_model_eyes(_SidebarPanel, Panel):
    bl_label = 'Eye Converter (MHA)'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="Turns an MHA eye + iris mesh into Smash", icon="INFO")
        col.label(text="scrolling eyes (iris decal on uvSet,")
        col.label(text="EyeL/EyeR materials, CustomVector31).")
        obj = context.active_object
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = obj is not None and obj.type == 'MESH'
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("sub.convert_mha_eyes", icon="HIDE_OFF")
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select the mesh with the eyes.")


# Parents before children.
PANELS = (
    SUB_PT_anim_rig,
    SUB_PT_anim_poses, SUB_PT_anim_idle_poses, SUB_PT_anim_user_poses,
    SUB_PT_anim_eyes,
    SUB_PT_anim_utilities, SUB_PT_anim_mirror,
    SUB_PT_anim_legacy_ik, SUB_PT_anim_bulk_ik,
    SUB_PT_model_viewport, SUB_PT_model_mesh, SUB_PT_model_uvs, SUB_PT_model_eyes,
    SUB_PT_model_bones, SUB_PT_model_bone_rolls, SUB_PT_model_bone_symmetry, SUB_PT_model_roll_preset,
)


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
    for cls in PANELS:
        if not getattr(cls, 'is_registered', False):
            bpy.utils.register_class(cls)
    bpy.utils.register_class(SUB_OP_mirror_vertex_groups)
    bpy.utils.register_class(SUB_OP_mirror_mesh_as_separate_object)
    bpy.utils.register_class(SUB_OP_convert_shape_keys_to_meshes)


def unregister():
    bpy.utils.unregister_class(SUB_OP_convert_shape_keys_to_meshes)
    bpy.utils.unregister_class(SUB_OP_mirror_mesh_as_separate_object)
    bpy.utils.unregister_class(SUB_OP_mirror_vertex_groups)
    for cls in reversed(PANELS):
        if getattr(cls, 'is_registered', False):
            bpy.utils.unregister_class(cls)
