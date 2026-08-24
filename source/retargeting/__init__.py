"""
Retargeting module - Integration of expy_kit into Smash Ultimate Blender Tools
This module provides 1:1 integration of expy_kit retargeting tools.
Panels are always visible and will guide the user to enter POSE mode when necessary.
All functionality is consolidated in the 'Retargeting' section of the 'Ultimate' tab.
"""
import bpy
import os
from bpy.types import Panel
from bpy.app.handlers import persistent

# Import expy_kit modules using relative imports
# expy_kit is at the plugin root level, so we go up two levels from source/retargeting/
from ...expy_kit import operators, properties, preferences, ui, preset_handler


# Auto-detection for Smash armatures
_last_detected_armature = None

def load_custom_bones_from_preset(preset_path):
    """Parse a preset file and extract custom bone definitions"""
    import os
    import re
    
    custom_bones = {}
    
    if not os.path.exists(preset_path):
        return custom_bones
    
    try:
        with open(preset_path, 'r') as f:
            content = f.read()
            
        # Look for lines like: skeleton.custom.identifier = 'BoneName'
        pattern = r"skeleton\.custom\.(\w+)\s*=\s*['\"]([^'\"]*)['\"]"
        matches = re.findall(pattern, content)
        
        for identifier, bone_name in matches:
            if identifier != 'name' and bone_name:  # Skip the legacy 'name' property
                custom_bones[identifier] = bone_name
    except Exception as e:
        print(f"Error parsing preset for custom bones: {e}")
    
    return custom_bones


def ensure_custom_bones_exist(skeleton, custom_bones_dict):
    """Ensure custom bone properties exist before setting them"""
    for identifier, bone_name in custom_bones_dict.items():
        if not hasattr(skeleton.custom, identifier):
            # Create the property using add_bone
            skeleton.custom.add_bone(identifier, bone_name)
        else:
            # Property exists, just set the value
            setattr(skeleton.custom, identifier, bone_name)


def load_preset_with_custom_bones(preset_name, armature_obj=None):
    """Load a preset and properly handle custom bones"""
    if armature_obj is None:
        armature_obj = bpy.context.object
    
    if not armature_obj or armature_obj.type != 'ARMATURE':
        return False
    
    try:
        # Get the preset path
        import os
        preset_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'expy_kit', 'rig_mapping', 'presets')
        preset_path = os.path.join(preset_dir, preset_name)
        
        # Parse custom bones from preset
        custom_bones = load_custom_bones_from_preset(preset_path)
        
        # Get skeleton
        skeleton = armature_obj.data.expykit_retarget
        
        # Pre-create custom bone properties
        if custom_bones:
            ensure_custom_bones_exist(skeleton, custom_bones)
        
        # Now load the preset normally
        preset_handler.set_preset_skel(preset_name, validate=True)
        
        # Force UI refresh
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        
        return True
    except Exception as e:
        print(f"Error loading preset with custom bones: {e}")
        return False


def check_and_load_smash_preset(armature_obj):
    """Check if an armature is a smash rig and load preset if needed"""
    if not armature_obj or armature_obj.type != 'ARMATURE':
        return False
    
    armature_name = armature_obj.name
    if "smush_blender_import" not in armature_name.lower():
        return False
    
    # Check if settings are already loaded
    if armature_obj.data.expykit_retarget.has_settings():
        return False  # Already has settings
    
    # Load Smash preset with proper custom bone handling
    try:
        # Temporarily set as active to load preset
        original_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = armature_obj
        
        if load_preset_with_custom_bones('Smash.py', armature_obj):
            print(f"Auto-loaded Smash preset for armature: {armature_name}")
            
            # Restore original active object
            if original_active:
                bpy.context.view_layer.objects.active = original_active
            return True
    except Exception as e:
        print(f"Failed to auto-load Smash preset for {armature_name}: {e}")
    
    return False


@persistent
def auto_detect_smash_armature(scene):
    """Auto-load Smash preset when a smush_blender_import armature is selected"""
    global _last_detected_armature
    
    try:
        # Check active object
        if bpy.context.object and bpy.context.object.type == 'ARMATURE':
            armature_name = bpy.context.object.name
            
            # Only process if we haven't already processed this armature
            if _last_detected_armature != armature_name:
                if check_and_load_smash_preset(bpy.context.object):
                    _last_detected_armature = armature_name
        
        # Also check the "Bind To" target if set
        if hasattr(bpy.context.scene, 'expykit_bind_to') and bpy.context.scene.expykit_bind_to:
            target_armature = bpy.context.scene.expykit_bind_to
            if target_armature and target_armature.type == 'ARMATURE':
                check_and_load_smash_preset(target_armature)
    
    except Exception as e:
        # Silently fail to avoid spamming console
        pass


# Override preset execution to handle custom bones properly
class ULTIMATE_OT_execute_preset_retarget(bpy.types.Operator):
    """Apply a Bone Retarget Preset with Custom Bone Support"""
    bl_idname = "object.ultimate_armature_preset_apply"
    bl_label = "Apply Bone Retarget Preset"

    filepath: bpy.props.StringProperty(
        subtype='FILE_PATH',
        options={'SKIP_SAVE'},
    )
    menu_idname: bpy.props.StringProperty(
        name="Menu ID Name",
        description="ID name of the menu this was called from",
        options={'SKIP_SAVE'},
    )

    def execute(self, context):
        from os.path import basename, splitext
        filepath = self.filepath

        # change the menu title to the most recently chosen option
        preset_class = ui.VIEW3D_MT_retarget_presets
        preset_class.bl_label = bpy.path.display_name(basename(filepath), title_case=False)

        ext = splitext(filepath)[1].lower()

        if ext not in {".py", ".xml"}:
            self.report({'ERROR'}, "Unknown file type: %r" % ext)
            return {'CANCELLED'}

        if hasattr(preset_class, "reset_cb"):
            preset_class.reset_cb(context)

        if ext == ".py":
            try:
                # PRE-PROCESS: Extract and create custom bone properties
                custom_bones = load_custom_bones_from_preset(filepath)
                if custom_bones and context.object and context.object.type == 'ARMATURE':
                    skeleton = context.object.data.expykit_retarget
                    ensure_custom_bones_exist(skeleton, custom_bones)
                
                # Execute the preset file
                bpy.utils.execfile(filepath)
            except Exception as ex:
                self.report({'ERROR'}, "Failed to execute the preset: " + repr(ex))

        elif ext == ".xml":
            import rna_xml
            rna_xml.xml_file_run(context,
                                 filepath,
                                 preset_class.preset_xml_map)

        if hasattr(preset_class, "post_cb"):
            preset_class.post_cb(context)

        preset_handler.validate_preset(context.object.data)

        settings = context.object.data.expykit_retarget
        preset_handler.reset_preset_names(settings)
        
        # Force UI refresh to show custom bones
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        return {'FINISHED'}


# Custom preset menu that uses our custom operator
class ULTIMATE_MT_retarget_presets(ui.VIEW3D_MT_retarget_presets):
    """Retarget presets menu with custom bone support"""
    bl_label = "Retarget Presets"  # Required for preset deletion to work
    preset_operator = "object.ultimate_armature_preset_apply"  # Use our custom operator


# Custom preset add/remove operator that properly references our custom menu
# Inherits from AddPresetBase to get proper preset saving behavior
from bl_operators.presets import AddPresetBase

class ULTIMATE_OT_add_preset_retarget(AddPresetBase, bpy.types.Operator):
    """Add or Remove a Bone Retarget Preset"""
    bl_idname = "object.ultimate_armature_preset_add"
    bl_label = "Add Bone Retarget Preset"
    
    # Point to OUR custom menu class (this fixes the bl_label error on delete)
    preset_menu = "ULTIMATE_MT_retarget_presets"
    
    # Same preset_defines and preset_values as original
    preset_defines = [
        "skeleton = bpy.context.object.data.expykit_retarget"
    ]
    
    preset_values = [
        "skeleton.face",
        "skeleton.spine",
        "skeleton.right_arm",
        "skeleton.left_arm",
        "skeleton.right_leg",
        "skeleton.left_leg",
        "skeleton.left_fingers",
        "skeleton.right_fingers",
        "skeleton.right_arm_ik",
        "skeleton.left_arm_ik",
        "skeleton.right_leg_ik",
        "skeleton.left_leg_ik",
        "skeleton.custom",
        "skeleton.custom.name",
        "skeleton.root",
        "skeleton.deform_preset"
    ]
    
    # Same preset directory as original expy_kit (armature/retarget)
    preset_subdir = os.path.join("armature", "retarget")


# We'll create a REPLACEMENT operator instead of patching
_original_constrain_class = None


def draw_binded_settings_ui(layout, context, show_presets=True):
    """Shared drawing function for BOTH the Binded Settings panel AND the Bind to Active Armature dialog.
    This ensures they are literally the SAME UI editing the SAME properties."""
    scene = context.scene
    column = layout.column()
    
    _prop_indent = 0.15
    
    # Presets section
    if show_presets:
        row = column.row()
        row.prop(scene, 'expykit_src_preset', text="To Bind")
        
        row = column.row()
        row.prop(scene, 'expykit_trg_preset', text="Bind To")
        
        column.separator()

    # Conversion section
    row = column.row()
    row.label(text='Conversion')

    row = column.split(factor=_prop_indent, align=True)
    row.separator()
    col = row.column()
    col.prop(scene, 'expykit_match_transform', text='')
    col.prop(scene, 'expykit_match_object_transform')
    col.prop(scene, 'expykit_fit_target_scale')
    if scene.expykit_fit_target_scale != "--":
        col.prop(scene, 'expykit_adjust_location')

    if not scene.expykit_loc_constraints and scene.expykit_match_transform == 'Bone':
        col.label(text="'Copy Location' might be required", icon='ERROR')
    elif scene.expykit_fit_target_scale == '--' and scene.expykit_match_transform == 'Pose':
        col.label(text="'Fit height' might improve results", icon='ERROR')
    else:
        col.separator()

    # Constraints section
    column.separator()
    row = column.row()
    row.label(text='Constraints')

    row = column.split(factor=_prop_indent, align=True)
    row.separator()

    constr_col = row.column()
    
    copy_loc_row = constr_col.row()
    copy_loc_row.prop(scene, 'expykit_loc_constraints')
    if scene.expykit_loc_constraints:
        copy_loc_row.prop(scene, 'expykit_no_finger_loc', text="Except Fingers")
    else:
        copy_loc_row.prop(scene, 'expykit_bind_floating', text="Only Floating")
    
    copy_rot_row = constr_col.row()
    copy_rot_row.prop(scene, 'expykit_rot_constraints')
    copy_rot_row.prop(scene, 'expykit_math_look_at')
    
    copy_scale_row = constr_col.row()
    copy_scale_row.prop(scene, 'expykit_scale_constraints')

    ik_aim_row = constr_col.row()
    ik_aim_row.prop(scene, 'expykit_copy_IK_roll_hands')
    ik_aim_row.prop(scene, 'expykit_copy_IK_roll_feet')

    constr_col.prop(scene, 'expykit_constraint_policy', text='')
    
    # Affect Bones section
    column.separator()
    row = column.row()
    row.label(text="Affect Bones")
    
    row = column.split(factor=_prop_indent, align=True)
    row.separator()
    col = row.column()
    col.prop(scene, 'expykit_only_selected')
    row.prop(scene, 'expykit_bind_by_name', text="Also by Name")
    if scene.expykit_bind_by_name:
        row = column.row()
        col = row.column()
        col.label(text="Prefix")
        col.prop(scene, 'expykit_name_prefix', text="")

        col = row.column()
        col.label(text="Replace:")
        col.prop(scene, 'expykit_name_replace', text="")

        col = row.column()
        col.label(text="With:")
        col.prop(scene, 'expykit_name_replace_with', text="")

        col = row.column()
        col.label(text="Suffix:")
        col.prop(scene, 'expykit_name_suffix', text="")

    # Root Animation section
    column.separator()
    row = column.row()
    row.label(text="Root Animation")
    row = column.split(factor=_prop_indent, align=True)
    row.separator()
    row.prop(scene, 'expykit_constrain_root', text="")

    if scene.expykit_constrain_root != 'None':
        row = column.split(factor=_prop_indent, align=True)
        row.label(text="")
        if context.active_object and context.active_object.type == 'ARMATURE':
            row.prop_search(scene, 'expykit_root_motion_bone',
                            context.active_object.data,
                            "bones", text="")

    if scene.expykit_constrain_root != 'None':
        row = column.row(align=True)
        row.label(text="Location")
        row.prop(scene, "expykit_root_cp_loc_x", text="X", toggle=True)
        row.prop(scene, "expykit_root_cp_loc_y", text="Y", toggle=True)
        row.prop(scene, "expykit_root_cp_loc_z", text="Z", toggle=True)

        row = column.row(align=True)
        row.label(text="Rotation")
        row.prop(scene, "expykit_root_cp_rot_x", text="X", toggle=True)
        row.prop(scene, "expykit_root_cp_rot_y", text="Y", toggle=True)
        row.prop(scene, "expykit_root_cp_rot_z", text="Z", toggle=True)


class ULTIMATE_OT_constrain_to_armature(bpy.types.Operator):
    """REPLACEMENT for ConstrainToArmature that uses scene properties.
    This operator has the SAME bl_idname so it replaces the original."""
    bl_idname = "armature.expykit_constrain_to_armature"
    bl_label = "Bind to Active Armature"
    bl_description = "Constrain bones of selected armatures to active armature"
    bl_options = {'REGISTER', 'UNDO'}

    # We still need the operator properties for the actual binding logic
    # But we'll copy from scene properties before executing
    src_preset: bpy.props.EnumProperty(items=preset_handler.iterate_presets_with_current, name="To Bind", options={'SKIP_SAVE'})
    trg_preset: bpy.props.EnumProperty(items=preset_handler.iterate_presets_with_current, name="Bind To", options={'SKIP_SAVE'})
    
    only_selected: bpy.props.BoolProperty(name="Only Selected", default=False)
    bind_by_name: bpy.props.BoolProperty(name="Bind bones by name", default=True)
    name_prefix: bpy.props.StringProperty(name="Add prefix to name", default="")
    name_replace: bpy.props.StringProperty(name="Replace in name", default="")
    name_replace_with: bpy.props.StringProperty(name="Replace in name with", default="")
    name_suffix: bpy.props.StringProperty(name="Add suffix to name", default="")
    
    ret_bones_collection: bpy.props.StringProperty(name="Layer", default="Retarget Bones")
    
    match_transform: bpy.props.EnumProperty(
        items=[
            ('None', "- None -", "Don't match any transform"),
            ('Bone', "Bones Offset", "Account for difference between control and deform rest pose"),
            ('Pose', "Current Pose is target Rest Pose", "Armature was posed manually to match rest pose of target"),
            ('World', "Follow target Pose in world space", "Just copy target world positions"),
        ],
        name="Match Transform", default='None'
    )
    match_object_transform: bpy.props.BoolProperty(name="Match Object Transform", default=True)
    math_look_at: bpy.props.BoolProperty(name="Fix direction", default=False)
    copy_IK_roll_hands: bpy.props.BoolProperty(name="Hands IK Roll", default=False)
    copy_IK_roll_feet: bpy.props.BoolProperty(name="Feet IK Roll", default=False)
    fit_target_scale: bpy.props.EnumProperty(
        name="Fit height",
        items=(('--', '- None -', 'None'), ('head', 'head', 'head'), ('neck', 'neck', 'neck'),
               ('spine2', 'chest', 'spine2'), ('spine1', 'spine1', 'spine1'),
               ('spine', 'spine', 'spine'), ('hips', 'hips', 'hips')),
        default='--'
    )
    adjust_location: bpy.props.BoolProperty(default=True, name="Adjust location to new scale")
    constrain_root: bpy.props.EnumProperty(
        items=[('None', "No Root", ""), ('Bone', "Bone", ""), ('Object', "Object", "")],
        name="Constrain Root", default='None'
    )
    loc_constraints: bpy.props.BoolProperty(name="Copy Location", default=False)
    rot_constraints: bpy.props.BoolProperty(name="Copy Rotation", default=True)
    scale_constraints: bpy.props.BoolProperty(name="Copy Scale", default=False)
    constraint_policy: bpy.props.EnumProperty(
        items=[('skip', "Skip Existing Constraints", ""), ('disable', "Disable Existing Constraints", ""), ('remove', "Delete Existing Constraints", "")],
        name="Policy", default='skip'
    )
    bind_floating: bpy.props.BoolProperty(name="Bind Floating", default=True)
    root_motion_bone: bpy.props.StringProperty(name="Root Motion", default="")
    root_cp_loc_x: bpy.props.BoolProperty(name="Root Copy Loc X", default=True)
    root_cp_loc_y: bpy.props.BoolProperty(name="Root Copy Loc Y", default=True)
    root_cp_loc_z: bpy.props.BoolProperty(name="Root Copy Loc Z", default=True)
    root_cp_rot_x: bpy.props.BoolProperty(name="Root Copy Rot X", default=True)
    root_cp_rot_y: bpy.props.BoolProperty(name="Root Copy Rot Y", default=True)
    root_cp_rot_z: bpy.props.BoolProperty(name="Root Copy Rot Z", default=False)
    no_finger_loc: bpy.props.BoolProperty(default=False, name="No Finger Location")
    force_dialog: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) != 2:
            return False
        if context.mode != 'POSE':
            return False
        for ob in context.selected_objects:
            if ob.type != 'ARMATURE':
                return False
        return True

    def invoke(self, context, event):
        scene = context.scene
        
        # Auto-detect and load presets
        try:
            to_bind = None
            for ob in context.selected_objects:
                if ob != context.active_object and ob.type == 'ARMATURE':
                    to_bind = ob
                    break
            if to_bind:
                check_and_load_smash_preset(to_bind)
            if context.active_object and context.active_object.type == 'ARMATURE':
                check_and_load_smash_preset(context.active_object)
        except Exception as e:
            print(f"Auto-detection failed: {e}")
        
        # Set presets in scene based on armature settings
        try:
            to_bind = next(ob for ob in context.selected_objects if ob != context.active_object)
            if to_bind.data.expykit_retarget.has_settings():
                scene.expykit_src_preset = '--Current--'
            if context.active_object.data.expykit_retarget.has_settings():
                scene.expykit_trg_preset = '--Current--'
        except:
            pass
        
        if self.force_dialog:
            return context.window_manager.invoke_props_dialog(self, width=400)
        return self.execute(context)

    def draw(self, context):
        """Draw using the SAME function as the Binded Settings panel"""
        draw_binded_settings_ui(self.layout, context, show_presets=True)

    def execute(self, context):
        scene = context.scene
        
        # Copy ALL settings from scene properties to operator properties
        self.src_preset = scene.expykit_src_preset
        self.trg_preset = scene.expykit_trg_preset
        self.match_transform = scene.expykit_match_transform
        self.match_object_transform = scene.expykit_match_object_transform
        self.fit_target_scale = scene.expykit_fit_target_scale
        self.adjust_location = scene.expykit_adjust_location
        self.loc_constraints = scene.expykit_loc_constraints
        self.rot_constraints = scene.expykit_rot_constraints
        self.scale_constraints = scene.expykit_scale_constraints
        self.bind_floating = scene.expykit_bind_floating
        self.math_look_at = scene.expykit_math_look_at
        self.copy_IK_roll_hands = scene.expykit_copy_IK_roll_hands
        self.copy_IK_roll_feet = scene.expykit_copy_IK_roll_feet
        self.constraint_policy = scene.expykit_constraint_policy
        self.only_selected = scene.expykit_only_selected
        self.bind_by_name = scene.expykit_bind_by_name
        self.name_prefix = scene.expykit_name_prefix
        self.name_replace = scene.expykit_name_replace
        self.name_replace_with = scene.expykit_name_replace_with
        self.name_suffix = scene.expykit_name_suffix
        self.constrain_root = scene.expykit_constrain_root
        self.root_motion_bone = scene.expykit_root_motion_bone
        self.no_finger_loc = scene.expykit_no_finger_loc
        self.root_cp_loc_x = scene.expykit_root_cp_loc_x
        self.root_cp_loc_y = scene.expykit_root_cp_loc_y
        self.root_cp_loc_z = scene.expykit_root_cp_loc_z
        self.root_cp_rot_x = scene.expykit_root_cp_rot_x
        self.root_cp_rot_y = scene.expykit_root_cp_rot_y
        self.root_cp_rot_z = scene.expykit_root_cp_rot_z
        
        # Call the original operator's execute logic
        return _original_constrain_class.execute(self, context)


# Custom bind operator with auto-detection and auto pose mode
class ULTIMATE_OT_bind_armatures(bpy.types.Operator):
    """Bind armatures with automatic Smash preset detection and pose mode switching"""
    bl_idname = "object.ultimate_bind_armatures"
    bl_label = "Bind Armatures"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Allow binding even if not in pose mode - we'll switch automatically
        return (context.object and context.object.type == 'ARMATURE' and 
                context.scene.expykit_bind_to and 
                context.object != context.scene.expykit_bind_to)
    
    def execute(self, context):
        source_armature = context.object
        target_armature = context.scene.expykit_bind_to
        
        # Auto-detect and load Smash preset for both armatures
        check_and_load_smash_preset(source_armature)
        check_and_load_smash_preset(target_armature)
        
        # Ensure we're in pose mode
        if context.mode != 'POSE':
            # Switch to object mode first, then to pose mode
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            # Select source armature and enter pose mode
            for ob in context.selected_objects:
                ob.select_set(False)
            source_armature.select_set(True)
            context.view_layer.objects.active = source_armature
            bpy.ops.object.mode_set(mode='POSE')
        
        # Deselect all objects except source
        for ob in context.selected_objects:
            ob.select_set(ob == source_armature)
        
        # Select target armature and make it active
        target_armature.select_set(True)
        context.view_layer.objects.active = target_armature
        
        # Ensure target is in pose mode
        if target_armature != source_armature:
            bpy.ops.object.mode_set(mode='POSE')
        
        # Set action range if target has animation
        if target_armature.animation_data and target_armature.animation_data.action:
            try:
                bpy.ops.object.expykit_action_to_range()
            except:
                pass  # Operator may not always work
        
        # Call the original constrain operator with dialog
        bpy.ops.armature.expykit_constrain_to_armature('INVOKE_DEFAULT', force_dialog=True)
        
        return {'FINISHED'}


# Help operator for how to use retargeting
class ULTIMATE_OT_retargeting_help(bpy.types.Operator):
    """Show instructions for using the retargeting system"""
    bl_idname = "object.ultimate_retargeting_help"
    bl_label = "How to Use Expy Kit Retargeting"
    
    def execute(self, context):
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=500)
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column(align=True)
        col.label(text="Expy Kit Retargeting - Quick Guide", icon='INFO')
        col.separator()
        
        # Step 1
        box = layout.box()
        box.label(text="1. Set Up Source Armature (the one you want to retarget FROM):", icon='ARMATURE_DATA')
        col = box.column(align=True)
        col.label(text="   • Select your source armature")
        col.label(text="   • Load the animations you want to retarget")
        col.label(text="   • Enter Pose Mode")
        col.label(text="   • Choose or create a preset in 'Expy Mapping'")
        col.label(text="   • Map bones to the preset's skeleton structure")
        
        # Step 2
        box = layout.box()
        box.label(text="2. Set Up Target Armature (the one you want to retarget TO):", icon='ARMATURE_DATA')
        col = box.column(align=True)
        col.label(text="   • Select your target armature")
        col.label(text="   • Enter Pose Mode")
        col.label(text="   • Choose or create a matching preset")
        
        # Step 3
        box = layout.box()
        box.label(text="3. Bind Armatures:", icon='LINKED')
        col = box.column(align=True)
        col.label(text="   • Select your target armature")
        col.label(text="   • In 'Bind To' panel, choose the target armature")
        col.label(text="   • A panel will appear with 'To Bind' and 'Bind To' options;")
        col.label(text="     choose presets for each model as needed and click OK")
        col.label(text="   • Click 'Bind Armatures'")
        col.label(text="   • A panel will appear in the bottom-left viewport corner")
        col.label(text="   • Use it to: adjust time stretching, toggle constraints,")
        col.label(text="     pick the Root Animation bone (e.g., 'Trans') for the armature,")
        col.label(text="     set offsets at the top (e.g., 'Bone Offset',")
        col.label(text="     'Current Pose is Target Pose'), and preview in real-time")
        
        # Step 4 - Tweaking
        box = layout.box()
        box.label(text="4. Tweaking:", icon='OUTLINER_OB_ARMATURE')
        col = box.column(align=True)
        col.label(text="   • A new set of helper bones is created for the source armature")
        col.label(text="   • Find them in the Bone Collections; use them to fix imperfections")
        col.label(text="   • Every target bone has constraints you can adjust")
        col.label(text="     if you don't want certain motions from the source")
        
        # Step 5
        box = layout.box()
        box.label(text="5. Bake Animation:", icon='RENDER_ANIMATION')
        col = box.column(align=True)
        col.label(text="   • Select your target armature in Pose Mode")
        col.label(text="   • Right-click in the viewport")
        col.label(text="   • Navigate to: Expy Kit > Animation > Bake Constrained Actions")
        col.label(text="   • Animation will be converted to keyframes")
        col.label(text="   • You can now unbind and use the baked animation")
        
        # Tips
        layout.separator()
        box = layout.box()
        box.label(text="Tips:", icon='LIGHTPROBE_CUBEMAP')
        col = box.column(align=True)
        col.label(text="   • Custom Bones: Add unique bones not in standard skeleton")
        col.label(text="   • Use the eyedropper to quickly assign active bone")
        col.label(text="   • Smash armatures auto-load the Smash preset")
        col.label(text="   • Both armatures must use the same preset structure")


# Main Retargeting panel in Ultimate tab
class SUB_PT_retargeting_main(Panel):
    """Main Retargeting panel in Ultimate tab"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Retargeting'
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 80

    @classmethod
    def poll(cls, context):
        # Always visible
        return True

    def draw(self, context):
        layout = self.layout
        
        # Check if we need to auto-switch to pose mode
        needs_pose_mode = False
        if context.object and context.object.type == 'ARMATURE':
            if context.mode != 'POSE':
                needs_pose_mode = True
        
        # Header
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Expy Kit Retargeting Tools", icon='ARMATURE_DATA')
        
        # How to use button
        row = col.row()
        row.operator("object.ultimate_retargeting_help", text="How to Use", icon='QUESTION')
        
        # Mode check - offer to auto-switch
        if needs_pose_mode:
            col.separator()
            row = col.row()
            row.alert = True
            row.label(text="Most features require Pose Mode", icon='INFO')
            col.operator("object.mode_set", text="Enter Pose Mode", icon='POSE_HLT').mode = 'POSE'


# Expy_kit panels moved to Ultimate tab, all as children of Retargeting
# These will replace the original panels

class ULTIMATE_PT_expy_retarget(ui.VIEW3D_PT_expy_retarget):
    """Expy Mapping panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "SUB_PT_retargeting_main"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True
    
    def draw(self, context):
        layout = self.layout

        # Add help button for active bone functionality
        row = layout.row()
        row.operator(ui.SetToActiveBoneHelpText.bl_idname, text="How to Set Active Bone", icon='HELP')
        layout.separator()

        # Use our custom preset menu that handles custom bones
        split = layout.split(factor=0.75)
        split.menu(ULTIMATE_MT_retarget_presets.__name__, text=ULTIMATE_MT_retarget_presets.bl_label)
        row = split.row(align=True)
        row.operator(ULTIMATE_OT_add_preset_retarget.bl_idname, text="+")
        row.operator(ULTIMATE_OT_add_preset_retarget.bl_idname, text="-").remove_active = True


class ULTIMATE_PT_BindPanel(ui.VIEW3D_PT_BindPanel):
    """Bind To panel in Ultimate tab with custom bind operator"""
    bl_category = 'Ultimate'
    bl_parent_id = "SUB_PT_retargeting_main"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True
    
    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, 'expykit_bind_to', text="")
        
        # Use our custom bind operator with auto-detection and auto pose mode
        layout.operator("object.ultimate_bind_armatures")


class ULTIMATE_PT_ActionsPanel(Panel):
    """Actions panel - contains Binding, Conversion, and Animation operators"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = "Actions"
    bl_parent_id = "SUB_PT_retargeting_main"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        return True
    
    def draw(self, context):
        layout = self.layout
        
        if context.mode != 'POSE':
            layout.label(text="Enter Pose Mode for actions", icon='INFO')


class ULTIMATE_PT_ActionsBinding(Panel):
    """Binding sub-panel under Actions"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = "Binding"
    bl_parent_id = "ULTIMATE_PT_ActionsPanel"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.operator(operators.ConstrainToArmature.bl_idname)
        col.operator(operators.ConstraintStatus.bl_idname)
        op = col.operator(operators.SelectConstrainedControls.bl_idname)
        op.select_type = 'constr'


class ULTIMATE_PT_ActionsConversion(Panel):
    """Conversion sub-panel under Actions"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = "Conversion"
    bl_parent_id = "ULTIMATE_PT_ActionsPanel"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.operator(operators.ConvertGameFriendly.bl_idname)
        col.operator(operators.RevertDotBoneNames.bl_idname)
        col.operator(operators.ConvertBoneNaming.bl_idname)
        col.operator(operators.ExtractMetarig.bl_idname)
        col.operator(operators.CreateTransformOffset.bl_idname)


class ULTIMATE_PT_ActionsAnimation(Panel):
    """Animation sub-panel under Actions"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = "Animation"
    bl_parent_id = "ULTIMATE_PT_ActionsPanel"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.operator(operators.ActionRangeToScene.bl_idname)
        col.operator("armature.ultimate_bake_actions", text="Bake Constrained Actions")
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator(operators.RenameActionsFromFbxFiles.bl_idname)
        col.operator(operators.AddRootMotion.bl_idname)
        op = col.operator(operators.SelectConstrainedControls.bl_idname, text="Select Animated Controls")
        op.select_type = 'anim'


# Custom bake operator with "Bake Visible" option
class ULTIMATE_OT_bake_actions(bpy.types.Operator):
    """Bake Constrained Actions with multiple baking modes"""
    bl_idname = "armature.ultimate_bake_actions"
    bl_label = "Bake Constrained Actions"
    bl_description = "Bake Actions constrained from another Armature with selectable baking mode"
    bl_options = {'REGISTER', 'UNDO'}
    
    bake_mode: bpy.props.EnumProperty(
        name="Bake Mode",
        items=[
            ('CONSTRAINED', "Bake Constrained", "Original behavior - bake constrained actions"),
            ('VISIBLE', "Bake Visible", "Bake with visual keying and clear constraints"),
        ],
        default='CONSTRAINED'
    )
    
    clear_users_old: bpy.props.BoolProperty(
        name="Clear original Action Users",
        default=True
    )
    
    fake_user_new: bpy.props.BoolProperty(
        name="Save New Action User",
        default=True
    )
    
    exclude_deform: bpy.props.BoolProperty(
        name="Exclude deform bones",
        default=False
    )
    
    do_bake: bpy.props.BoolProperty(
        name="Bake and Exit",
        description="Bake driven motion and exit",
        default=True,
        options={'SKIP_SAVE'}
    )
    
    copy_visibility_fcurves: bpy.props.BoolProperty(
        name="Copy Vis Layers",
        description="Link SAP Data animations to the new retargeted animation",
        default=False
    )
    
    # Bake Visible specific options
    clear_constraints_after: bpy.props.BoolProperty(
        name="Clear Constraints After Bake",
        description="Remove constraints after baking",
        default=True
    )
    
    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        column = layout.column()
        
        # Bake mode selector
        column.prop(self, "bake_mode")
        column.separator()
        
        if self.bake_mode == 'CONSTRAINED':
            # Original bake constrained mode UI
            from ...expy_kit import bone_utils
            
            for to_bake in context.selected_objects:
                trg_ob = self._get_trg_ob(to_bake)
                if not trg_ob:
                    continue
                column.label(text=f"Baking from {trg_ob.name} to {to_bake.name}")
            
            if len(context.selected_objects) > 1:
                column.label(text="No need to select two Armatures anymore", icon='ERROR')
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "clear_users_old")
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "fake_user_new")
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "exclude_deform")
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "copy_visibility_fcurves")
        
        else:  # VISIBLE mode
            column.label(text="Bake Visible Mode:", icon='INFO')
            column.label(text="Bulk bakes all actions with visual keying")
            column.label(text="Renames originals to _old, baked get original names")
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "clear_users_old")
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "fake_user_new")
            
            row = column.split(factor=0.30, align=True)
            row.label(text="")
            row.prop(self, "clear_constraints_after")
        
        column.separator()
        row = column.split(factor=0.30, align=True)
        row.label(text="")
        row.prop(self, "do_bake", toggle=True)
    
    def _get_trg_ob(self, ob):
        """Get target object from constrained bones"""
        from ...expy_kit import bone_utils
        
        for pb in bone_utils.get_constrained_controls(armature_object=ob, use_deform=not self.exclude_deform):
            for constr in pb.constraints:
                try:
                    subtarget = constr.subtarget
                except AttributeError:
                    continue
                
                if subtarget.endswith("_RET"):
                    return constr.target
        return None
    
    def execute(self, context):
        if not self.do_bake:
            return {'FINISHED'}
        
        if self.bake_mode == 'VISIBLE':
            return self._execute_bake_visible(context)
        else:
            return self._execute_bake_constrained(context)
    
    def _execute_bake_visible(self, context):
        """Bake using visual keying - bulk bakes all actions like the constrained mode"""
        from ...expy_kit.operators import validate_actions
        from ...expy_kit import bone_utils
        
        ob = context.object
        
        if not ob or ob.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        
        # Find the target armature (the one this armature is constrained TO)
        trg_ob = self._get_trg_ob(ob)
        
        if trg_ob:
            # We have constraints pointing to another armature - bake from that
            source_armature = trg_ob
            dest_armature = ob
            print(f"Bake Visible: Found constrained setup - baking from {trg_ob.name} to {ob.name}")
        else:
            # No constraints - just bake the current armature's own actions
            source_armature = ob
            dest_armature = ob
            print(f"Bake Visible: No constraints found - baking {ob.name}'s own actions")
        
        if not source_armature.animation_data:
            self.report({'ERROR'}, f"No animation data on source armature ({source_armature.name})")
            return {'CANCELLED'}
        
        # Select pose bones for baking
        for pb in dest_armature.pose.bones:
            pb.select = True
        
        # Get list of actions to bake - those that belong to the SOURCE armature
        actions_to_bake = []
        for action in bpy.data.actions:
            if "SAP Data" in action.name:
                continue
            if "_old" in action.name:
                continue
            # Check if action belongs to the source armature
            if validate_actions(action, source_armature.path_resolve):
                actions_to_bake.append(action)
        
        if not actions_to_bake:
            self.report({'WARNING'}, "No actions found to bake")
            return {'CANCELLED'}
        
        total_actions = len(actions_to_bake)
        current_action = 0
        
        context.window.cursor_modal_set('WAIT')
        
        # Store which bones have constraints (to clear after all baking)
        bones_with_constraints = []
        if self.clear_constraints_after:
            for pb in dest_armature.pose.bones:
                if pb.constraints:
                    bones_with_constraints.append(pb.name)
        
        try:
            for action in list(actions_to_bake):  # Use list() to allow modification during iteration
                current_action += 1
                progress = current_action / total_actions if total_actions > 0 else 0
                context.window_manager.progress_update(progress)
                bpy.context.view_layer.update()
                
                # Set the action on the SOURCE armature (the one constraints point to)
                source_armature.animation_data.action = action
                fr_start, fr_end = action.frame_range
                
                # Bake with visual keying on the DESTINATION armature (don't clear constraints yet)
                bpy.ops.nla.bake(
                    frame_start=int(fr_start),
                    frame_end=int(fr_end),
                    bake_types={'POSE'},
                    only_selected=True,
                    visual_keying=True,
                    clear_constraints=False
                )
                
                if not dest_armature.animation_data or not dest_armature.animation_data.action:
                    self.report({'WARNING'}, f"Failed to bake {action.name}")
                    continue
                
                # Get the baked action
                baked_action = dest_armature.animation_data.action
                baked_action.use_fake_user = self.fake_user_new
                
                # Store original action name
                original_name = action.name
                
                # Clean the name - remove armature prefixes
                if "|" in original_name:
                    clean_action_name = original_name.split("|")[-1]
                else:
                    clean_action_name = original_name
                
                # Rename original to _old
                action.name = f"{original_name}_old"
                
                # Name the baked action like the original
                baked_action.name = clean_action_name
                
                if self.clear_users_old and action.users > 0:
                    action.user_clear()
                
                print(f"Baked '{original_name}' -> '{clean_action_name}' (original renamed to '{action.name}')")
            
            # Clear constraints after all baking is done
            if self.clear_constraints_after:
                for bone_name in bones_with_constraints:
                    try:
                        pbone = dest_armature.pose.bones[bone_name]
                        for constr in reversed(pbone.constraints):
                            pbone.constraints.remove(constr)
                    except KeyError:
                        continue
            
            self.report({'INFO'}, f"Bake Visible completed - {total_actions} actions baked")
            
        except Exception as e:
            self.report({'ERROR'}, f"Bake failed: {str(e)}")
            return {'CANCELLED'}
        
        finally:
            context.window.cursor_modal_restore()
        
        return {'FINISHED'}
    
    def _execute_bake_constrained(self, context):
        """Original constrained baking behavior"""
        import re
        from ...expy_kit import bone_utils
        from ...expy_kit.operators import validate_actions, clean_baked_action, sync_vis_and_mat_tracks
        
        sel_obs = list(context.selected_objects)
        
        # Calculate total actions to bake for progress tracking
        total_actions = 0
        for ob in sel_obs:
            trg_ob = self._get_trg_ob(ob)
            if trg_ob:
                for action in bpy.data.actions:
                    if validate_actions(action, trg_ob.path_resolve) and "SAP Data" not in action.name:
                        total_actions += 1
        
        current_action = 0
        context.window.cursor_modal_set('WAIT')
        
        for ob in sel_obs:
            ob.select_set(False)
            
            trg_ob = self._get_trg_ob(ob)
            if not trg_ob:
                continue
            
            constr_bone_names = []
            for pb in bone_utils.get_constrained_controls(ob, unselect=True, use_deform=not self.exclude_deform):
                if pb.name + "_RET" in trg_ob.data.bones:
                    pb.select = True
                    constr_bone_names.append(pb.name)
            
            for action in list(bpy.data.actions):
                if not validate_actions(action, trg_ob.path_resolve):
                    continue
                
                if "SAP Data" in action.name:
                    continue
                
                current_action += 1
                progress = current_action / total_actions if total_actions > 0 else 0
                context.window_manager.progress_update(progress)
                bpy.context.view_layer.update()
                
                trg_ob.animation_data.action = action
                fr_start, fr_end = action.frame_range
                bpy.ops.nla.bake(frame_start=int(fr_start), frame_end=int(fr_end),
                                 bake_types={'POSE'}, only_selected=True,
                                 visual_keying=True, clear_constraints=False)
                
                if not ob.animation_data:
                    self.report({'WARNING'}, f"failed to bake {action.name}")
                    continue
                
                baked_action = ob.animation_data.action
                baked_action.use_fake_user = self.fake_user_new
                
                clean_baked_action(action, baked_action, ob)
                
                original_name = action.name
                
                if "|" in original_name:
                    clean_action_name = original_name.split("|")[-1]
                else:
                    clean_action_name = original_name
                
                action.name = f"{original_name}_old"
                baked_action.name = clean_action_name
                
                if self.clear_users_old and action.users > 0:
                    action.user_clear()
                
                if self.copy_visibility_fcurves:
                    if hasattr(trg_ob.data, 'sub_anim_properties') and hasattr(ob.data, 'sub_anim_properties'):
                        sync_vis_and_mat_tracks(trg_ob.data, ob.data)
                    
                    target_armature_name = ob.name
                    source_armature_name = trg_ob.name
                    
                    for sap_action in bpy.data.actions:
                        if "SAP Data" not in sap_action.name:
                            continue
                        source_prefix_with_space = f"{source_armature_name} "
                        if not sap_action.name.startswith(source_prefix_with_space):
                            continue
                        
                        suffix = sap_action.name[len(source_prefix_with_space):]
                        new_sap_data_name = f"{target_armature_name} {suffix}"
                        sap_action.name = new_sap_data_name
                    
                    try:
                        dup_pattern = re.compile(rf"^{re.escape(target_armature_name)}(?:\s+\.\d+)+\s+(?P<rest>.+)$")
                    except Exception:
                        dup_pattern = None
                    
                    if dup_pattern is not None:
                        for sap_action in bpy.data.actions:
                            if "SAP Data" not in sap_action.name:
                                continue
                            match = dup_pattern.match(sap_action.name)
                            if not match:
                                continue
                            cleaned = f"{target_armature_name} {match.group('rest')}"
                            sap_action.name = cleaned
                    
                    if hasattr(ob.data, 'animation_data'):
                        sap_data_action_name = f"{target_armature_name} {clean_action_name} SAP Data"
                        sap_data_action = bpy.data.actions.get(sap_data_action_name)
                        if sap_data_action:
                            if not ob.data.animation_data:
                                ob.data.animation_data_create()
                            ob.data.animation_data.action = sap_data_action
            
            # Delete constraints
            for bone_name in constr_bone_names:
                try:
                    pbone = ob.pose.bones[bone_name]
                except KeyError:
                    continue
                for constr in reversed(pbone.constraints):
                    pbone.constraints.remove(constr)
        
        context.window.cursor_modal_restore()
        
        return {'FINISHED'}


class ULTIMATE_PT_retarget_spine(ui.VIEW3D_PT_expy_retarget_spine):
    """Spine panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_arms(ui.VIEW3D_PT_expy_retarget_arms):
    """Arms panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_arms_IK(ui.VIEW3D_PT_expy_retarget_arms_IK):
    """Arms IK panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_legs(ui.VIEW3D_PT_expy_retarget_leg):
    """Legs panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_legs_IK(ui.VIEW3D_PT_expy_retarget_leg_IK):
    """Legs IK panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_fingers(ui.VIEW3D_PT_expy_retarget_fingers):
    """Fingers panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_face(ui.VIEW3D_PT_expy_retarget_face):
    """Face panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_root(ui.VIEW3D_PT_expy_retarget_root):
    """Root panel in Ultimate tab"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True


class ULTIMATE_PT_retarget_custom(ui.VIEW3D_PT_expy_retarget_custom):
    """Custom Bones panel in Ultimate tab - first item under Expy Mapping"""
    bl_category = 'Ultimate'
    bl_parent_id = "ULTIMATE_PT_expy_retarget"  # Under Expy Mapping, but first
    
    @classmethod
    def poll(cls, context):
        # Always show the panel
        return True
    
    def draw(self, context):
        # Call parent draw method
        super().draw(context)
        # Force UI refresh to update custom bones list immediately
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# List of custom panels to register (all in Ultimate tab under Retargeting)
custom_panels = [
    SUB_PT_retargeting_main,
    ULTIMATE_PT_BindPanel,  # Bind To panel at the top
    ULTIMATE_PT_expy_retarget,  # Expy Mapping panel
    ULTIMATE_PT_retarget_custom,  # Custom Bones - first under Expy Mapping
    ULTIMATE_PT_retarget_spine,
    ULTIMATE_PT_retarget_arms,
    ULTIMATE_PT_retarget_arms_IK,
    ULTIMATE_PT_retarget_legs,
    ULTIMATE_PT_retarget_legs_IK,
    ULTIMATE_PT_retarget_fingers,
    ULTIMATE_PT_retarget_face,
    ULTIMATE_PT_retarget_root,
    ULTIMATE_PT_ActionsPanel,  # Actions panel
    ULTIMATE_PT_ActionsBinding,  # Binding sub-panel
    ULTIMATE_PT_ActionsConversion,  # Conversion sub-panel (actions)
    ULTIMATE_PT_ActionsAnimation,  # Animation sub-panel
]


def register():
    """Register the retargeting module and all expy_kit components"""
    print("  Registering Retargeting module (expy_kit integration)...")
    
    # Install expy_kit presets if not already installed
    try:
        preset_handler.install_presets()
    except Exception as e:
        print(f"  Warning: Could not install expy_kit presets: {e}")
    
    # Register expy_kit properties
    properties.register_classes()
    
    # Register expy_kit operators FIRST
    operators.register_classes()
    
    # NOW unregister the original ConstrainToArmature and register our replacement
    global _original_constrain_class
    try:
        from ..expy_kit.operators import ConstrainToArmature
        _original_constrain_class = ConstrainToArmature
        # Unregister the original
        bpy.utils.unregister_class(ConstrainToArmature)
        # Register our replacement (same bl_idname)
        bpy.utils.register_class(ULTIMATE_OT_constrain_to_armature)
        print("  Replaced ConstrainToArmature with our custom version that uses scene properties")
    except Exception as e:
        print(f"  Warning: Could not replace constrain operator: {e}")
    
    # Register expy_kit preferences
    preferences.register_classes()
    
    # Register original expy_kit UI classes (menus, operators UI, etc)
    # But we'll unregister the panels and replace them with our versions
    ui.register_classes()
    
    # Register our custom operators and menu
    try:
        bpy.utils.register_class(ULTIMATE_OT_execute_preset_retarget)
        bpy.utils.register_class(ULTIMATE_OT_add_preset_retarget)
        bpy.utils.register_class(ULTIMATE_MT_retarget_presets)
        bpy.utils.register_class(ULTIMATE_OT_bind_armatures)
        bpy.utils.register_class(ULTIMATE_OT_retargeting_help)
        bpy.utils.register_class(ULTIMATE_OT_bake_actions)
    except Exception as e:
        print(f"  Warning: Could not register custom operators/menu: {e}")
    
    # Register scene properties for Binded Settings panel
    # Preset properties (use the same enum callback as the operator)
    if not hasattr(bpy.types.Scene, 'expykit_src_preset'):
        bpy.types.Scene.expykit_src_preset = bpy.props.EnumProperty(
            items=preset_handler.iterate_presets_with_current,
            name="To Bind",
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_trg_preset'):
        bpy.types.Scene.expykit_trg_preset = bpy.props.EnumProperty(
            items=preset_handler.iterate_presets_with_current,
            name="Bind To",
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_match_transform'):
        bpy.types.Scene.expykit_match_transform = bpy.props.EnumProperty(
            items=[
                ('None', "- None -", "Don't match any transform"),
                ('Bone', "Bones Offset", "Account for difference between control and deform rest pose"),
                ('Pose', "Current Pose is target Rest Pose", "Armature was posed manually to match rest pose of target"),
                ('World', "Follow target Pose in world space", "Just copy target world positions"),
            ],
            name="Match Transform",
            default='None'
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_match_object_transform'):
        bpy.types.Scene.expykit_match_object_transform = bpy.props.BoolProperty(
            name="Match Object Transform",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_fit_target_scale'):
        bpy.types.Scene.expykit_fit_target_scale = bpy.props.EnumProperty(
            name="Fit height",
            items=(
                ('--', '- None -', 'None'),
                ('head', 'head', 'head'),
                ('neck', 'neck', 'neck'),
                ('spine2', 'chest', 'spine2'),
                ('spine1', 'spine1', 'spine1'),
                ('spine', 'spine', 'spine'),
                ('hips', 'hips', 'hips'),
            ),
            default='--'
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_loc_constraints'):
        bpy.types.Scene.expykit_loc_constraints = bpy.props.BoolProperty(
            name="Copy Location",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_rot_constraints'):
        bpy.types.Scene.expykit_rot_constraints = bpy.props.BoolProperty(
            name="Copy Rotation",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_scale_constraints'):
        bpy.types.Scene.expykit_scale_constraints = bpy.props.BoolProperty(
            name="Copy Scale",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_bind_floating'):
        bpy.types.Scene.expykit_bind_floating = bpy.props.BoolProperty(
            name="Only Floating",
            description="Always bind unparented bones Location and Rotation",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_math_look_at'):
        bpy.types.Scene.expykit_math_look_at = bpy.props.BoolProperty(
            name="Fix direction",
            description="Correct chain direction based on mid limb",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_copy_IK_roll_hands'):
        bpy.types.Scene.expykit_copy_IK_roll_hands = bpy.props.BoolProperty(
            name="Hands IK Roll",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_copy_IK_roll_feet'):
        bpy.types.Scene.expykit_copy_IK_roll_feet = bpy.props.BoolProperty(
            name="Feet IK Roll",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_constraint_policy'):
        bpy.types.Scene.expykit_constraint_policy = bpy.props.EnumProperty(
            items=[
                ('skip', "Skip Existing Constraints", "Skip Bones that are constrained already"),
                ('disable', "Disable Existing Constraints", "Disable existing binding constraints and add new ones"),
                ('remove', "Delete Existing Constraints", "Delete existing binding constraints")
            ],
            name="Policy",
            default='skip'
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_only_selected'):
        bpy.types.Scene.expykit_only_selected = bpy.props.BoolProperty(
            name="Only Selected",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_bind_by_name'):
        bpy.types.Scene.expykit_bind_by_name = bpy.props.BoolProperty(
            name="Also by Name",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_name_prefix'):
        bpy.types.Scene.expykit_name_prefix = bpy.props.StringProperty(
            name="Prefix",
            default=""
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_name_replace'):
        bpy.types.Scene.expykit_name_replace = bpy.props.StringProperty(
            name="Replace",
            default=""
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_name_replace_with'):
        bpy.types.Scene.expykit_name_replace_with = bpy.props.StringProperty(
            name="With",
            default=""
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_name_suffix'):
        bpy.types.Scene.expykit_name_suffix = bpy.props.StringProperty(
            name="Suffix",
            default=""
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_constrain_root'):
        bpy.types.Scene.expykit_constrain_root = bpy.props.EnumProperty(
            items=[
                ('None', "No Root", "Don't constrain root bone"),
                ('Bone', "Bone", "Constrain root to bone"),
                ('Object', "Object", "Constrain root to object")
            ],
            name="Root Animation",
            default='None'
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_motion_bone'):
        bpy.types.Scene.expykit_root_motion_bone = bpy.props.StringProperty(
            name="Root Motion Bone",
            default=""
        )
    
    # Additional properties for full Conversion panel support
    if not hasattr(bpy.types.Scene, 'expykit_adjust_location'):
        bpy.types.Scene.expykit_adjust_location = bpy.props.BoolProperty(
            name="Adjust location to new scale",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_no_finger_loc'):
        bpy.types.Scene.expykit_no_finger_loc = bpy.props.BoolProperty(
            name="Except Fingers",
            description="Don't copy location for finger bones",
            default=False
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_cp_loc_x'):
        bpy.types.Scene.expykit_root_cp_loc_x = bpy.props.BoolProperty(
            name="Root Copy Loc X",
            description="Copy Root X Location",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_cp_loc_y'):
        bpy.types.Scene.expykit_root_cp_loc_y = bpy.props.BoolProperty(
            name="Root Copy Loc Y",
            description="Copy Root Y Location",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_cp_loc_z'):
        bpy.types.Scene.expykit_root_cp_loc_z = bpy.props.BoolProperty(
            name="Root Copy Loc Z",
            description="Copy Root Z Location",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_cp_rot_x'):
        bpy.types.Scene.expykit_root_cp_rot_x = bpy.props.BoolProperty(
            name="Root Copy Rot X",
            description="Copy Root X Rotation",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_cp_rot_y'):
        bpy.types.Scene.expykit_root_cp_rot_y = bpy.props.BoolProperty(
            name="Root Copy Rot Y",
            description="Copy Root Y Rotation",
            default=True
        )
    
    if not hasattr(bpy.types.Scene, 'expykit_root_cp_rot_z'):
        bpy.types.Scene.expykit_root_cp_rot_z = bpy.props.BoolProperty(
            name="Root Copy Rot Z",
            description="Copy Root Z Rotation",
            default=False
        )
    
    # Note: ConstrainToArmature was replaced with our custom version earlier
    
    # Unregister original panels
    original_panels = [
        ui.VIEW3D_PT_expy_retarget,
        ui.VIEW3D_PT_BindPanel,
        ui.VIEW3D_PT_expy_retarget_spine,
        ui.VIEW3D_PT_expy_retarget_arms,
        ui.VIEW3D_PT_expy_retarget_arms_IK,
        ui.VIEW3D_PT_expy_retarget_leg,
        ui.VIEW3D_PT_expy_retarget_leg_IK,
        ui.VIEW3D_PT_expy_retarget_fingers,
        ui.VIEW3D_PT_expy_retarget_face,
        ui.VIEW3D_PT_expy_retarget_root,
        ui.VIEW3D_PT_expy_retarget_custom,
        ui.VIEW3D_PT_expy_rename_candidates,
        ui.VIEW3D_PT_expy_rename_advanced,
    ]
    
    for panel in original_panels:
        try:
            bpy.utils.unregister_class(panel)
        except:
            pass
    
    # Register our custom panels
    for panel in custom_panels:
        try:
            bpy.utils.register_class(panel)
        except Exception as e:
            print(f"  Warning: Could not register panel {panel.__name__}: {e}")
    
    # Callback for when bind_to property changes
    def on_bind_to_update(self, context):
        """Check if the selected bind_to armature is a smash rig"""
        if self.expykit_bind_to and self.expykit_bind_to.type == 'ARMATURE':
            check_and_load_smash_preset(self.expykit_bind_to)
    
    # Register scene properties for binding
    if not hasattr(bpy.types.Scene, 'expykit_bind_to'):
        bpy.types.Scene.expykit_bind_to = bpy.props.PointerProperty(
            type=bpy.types.Object,
            name="Bind To",
            description="Target armature to bind to",
            poll=ui.poll_armature_bind_to,
            update=on_bind_to_update
        )
    
    # Register auto-detection handler for Smash armatures
    if auto_detect_smash_armature not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(auto_detect_smash_armature)
    
    print("  Retargeting module registered successfully!")


def unregister():
    """Unregister the retargeting module and all expy_kit components"""
    print("  Unregistering Retargeting module...")
    
    # Unregister auto-detection handler
    if auto_detect_smash_armature in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(auto_detect_smash_armature)
    
    # Unregister our custom panels
    for panel in reversed(custom_panels):
        try:
            bpy.utils.unregister_class(panel)
        except:
            pass
    
    # Restore original ConstrainToArmature operator
    global _original_constrain_class
    if _original_constrain_class:
        try:
            # Unregister our replacement
            bpy.utils.unregister_class(ULTIMATE_OT_constrain_to_armature)
            # Re-register the original
            bpy.utils.register_class(_original_constrain_class)
            print("  Restored original ConstrainToArmature operator")
        except:
            pass
    
    # Unregister our custom operators and menu
    try:
        bpy.utils.unregister_class(ULTIMATE_OT_bake_actions)
        bpy.utils.unregister_class(ULTIMATE_OT_retargeting_help)
        bpy.utils.unregister_class(ULTIMATE_OT_bind_armatures)
        bpy.utils.unregister_class(ULTIMATE_MT_retarget_presets)
        bpy.utils.unregister_class(ULTIMATE_OT_add_preset_retarget)
        bpy.utils.unregister_class(ULTIMATE_OT_execute_preset_retarget)
    except:
        pass
    
    # Unregister scene properties for Binded Settings panel
    scene_props = [
        'expykit_src_preset',
        'expykit_trg_preset',
        'expykit_match_transform',
        'expykit_match_object_transform',
        'expykit_fit_target_scale',
        'expykit_adjust_location',
        'expykit_loc_constraints',
        'expykit_rot_constraints',
        'expykit_scale_constraints',
        'expykit_bind_floating',
        'expykit_math_look_at',
        'expykit_copy_IK_roll_hands',
        'expykit_copy_IK_roll_feet',
        'expykit_constraint_policy',
        'expykit_only_selected',
        'expykit_bind_by_name',
        'expykit_name_prefix',
        'expykit_name_replace',
        'expykit_name_replace_with',
        'expykit_name_suffix',
        'expykit_constrain_root',
        'expykit_root_motion_bone',
        'expykit_no_finger_loc',
        'expykit_root_cp_loc_x',
        'expykit_root_cp_loc_y',
        'expykit_root_cp_loc_z',
        'expykit_root_cp_rot_x',
        'expykit_root_cp_rot_y',
        'expykit_root_cp_rot_z',
    ]
    
    for prop in scene_props:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    
    # Unregister original expy_kit UI classes
    try:
        ui.unregister_classes()
    except:
        pass
    
    # Unregister expy_kit preferences
    try:
        preferences.unregister_classes()
    except:
        pass
    
    # Unregister expy_kit operators
    try:
        operators.unregister_classes()
    except:
        pass
    
    # Unregister expy_kit properties
    try:
        properties.unregister_classes()
    except:
        pass
    
    # Remove scene properties
    if hasattr(bpy.types.Scene, 'expykit_bind_to'):
        del bpy.types.Scene.expykit_bind_to
    
    # Reset global state
    global _last_detected_armature
    _last_detected_armature = None
    
    print("  Retargeting module unregistered successfully!")

