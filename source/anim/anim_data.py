import re
import sys
import inspect

import bpy

from bpy.types import Panel, Operator, UIList, Menu, PropertyGroup, Armature
from bpy.props import (
    IntProperty,
    StringProperty, 
    EnumProperty, 
    BoolProperty, 
    FloatProperty, 
    CollectionProperty, 
    PointerProperty,
    FloatVectorProperty,)

from .fcurve_compat import get_id_action_fcurves, find_fcurve, new_fcurve

mat_sub_types = (
    ('VECTOR', 'Custom Vector', 'Custom Vector'),
    ('FLOAT', 'Custom Float', 'Custom Float'),
    ('BOOL', 'Custom Bool', 'Custom Bool'),
    ('PATTERN', 'Pattern Index', 'Pattern Index'),
    ('TEXTURE', 'Texture Transform', 'Texture Transform'),
    ('DIFFUSE_UV', 'Diffuse UV Transform', 'Diffuse UV Transform')
)

# Store the last known action for each armature to detect changes
_last_known_actions = {}

# Global owner object for msgbus subscriptions
_msgbus_owner = object()


def mark_sap_sync_known(armature_object: bpy.types.Object):
    """Record an armature's current action as already seen by the SAP sync.

    An importer assigns a new action itself, so the sync handler must not treat
    that as a user-driven action change and go looking for a matching SAP action
    to switch to - it would fight the import. Recording the action here makes the
    handler's `last_action == current_action` check pass on its next run.

    Upstream calls this from import_anim and raw_anim. Their SAP system differs
    from ours, but both key off _last_known_actions with the same meaning, so the
    helper carries over unchanged.
    """
    global _last_known_actions
    if armature_object is None:
        return
    anim = getattr(armature_object, 'animation_data', None)
    if anim is not None and anim.action is not None:
        _last_known_actions[armature_object.name] = anim.action

# Handler to sync SAP data action with bone animation action
@bpy.app.handlers.persistent
def sync_sap_action_handler(scene):
    """
    Handler that automatically switches the SAP data action when the main action changes.
    This ensures that visibility and material animation data stays in sync with bone animation.
    """
    global _last_known_actions
    
    for obj in bpy.data.objects:
        if obj.type != 'ARMATURE':
            continue
        
        # Skip if no animation data
        if not obj.animation_data or not obj.animation_data.action:
            # Clear the stored action if there's no current action
            if obj.name in _last_known_actions:
                print(f"Clearing stored action for {obj.name}")
                del _last_known_actions[obj.name]
            continue
            
        # Skip if no armature data animation data
        if not obj.data.animation_data:
            continue
            
        current_action = obj.animation_data.action
        current_sap_action = obj.data.animation_data.action
        
        # Check if the action has changed since last time
        last_action = _last_known_actions.get(obj.name)
        if last_action == current_action:
            continue  # No change, skip
            
        # Update the stored action
        _last_known_actions[obj.name] = current_action
        
        # Look for corresponding SAP action
        expected_sap_action_name = f"{obj.name} {current_action.name} SAP Data"
        expected_sap_action = bpy.data.actions.get(expected_sap_action_name)
        
        # If we found a matching SAP action and it's different from current, switch to it
        if expected_sap_action and expected_sap_action != current_sap_action:
            obj.data.animation_data.action = expected_sap_action

# Additional handler for depsgraph updates (more frequent)
@bpy.app.handlers.persistent  
def sync_sap_action_depsgraph_handler(scene, depsgraph):
    """
    Alternative handler that runs on depsgraph updates.
    This catches more events including action changes.
    """
    sync_sap_action_handler(scene)

# Timer function for periodic checking
def sync_sap_timer():
    """
    Timer function that runs periodically to check for action changes.
    This is a fallback method to ensure SAP actions stay synced.
    """
    try:
        # Check if we're in a valid context for modifying data
        if bpy.context.mode in {'OBJECT', 'POSE'}:
            scene = bpy.context.scene
            sync_sap_action_handler(scene)
    except Exception as e:
        # Silently handle context errors
        pass
    
    # Return the interval for the next call (0.1 seconds)
    return 0.1

# Message bus callback for action changes
def action_change_msgbus_callback(*args):
    """
    Callback function for msgbus that triggers when animation_data.action changes.
    This provides more direct detection of action changes in the UI.
    """
    try:
        if bpy.context.mode in {'OBJECT', 'POSE'}:
            scene = bpy.context.scene
            sync_sap_action_handler(scene)
    except Exception as e:
        # Silently handle context errors
        pass

# Subscribe to action changes via msgbus
def subscribe_to_action_changes():
    """
    Subscribe to animation_data.action changes using Blender's message bus system.
    This provides more direct detection of action switching in the UI.
    """
    try:
        # Subscribe to changes in animation_data.action for all objects
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.AnimData, "action"),
            owner=_msgbus_owner,
            args=(),
            notify=action_change_msgbus_callback,
        )
    except Exception as e:
        # Silently handle msgbus errors
        pass

def unsubscribe_from_action_changes():
    """
    Unsubscribe from action changes when cleaning up.
    """
    try:
        bpy.msgbus.clear_by_owner(_msgbus_owner)
    except:
        pass

# Modal operator for continuous monitoring
class SUB_OP_sap_sync_monitor(Operator):
    bl_idname = 'sub.sap_sync_monitor'
    bl_label = 'SAP Sync Monitor'
    bl_description = 'Start/stop continuous SAP action monitoring'
    
    action: EnumProperty(
        items=[
            ('TOGGLE', 'Toggle', 'Toggle monitoring on/off'),
            ('START', 'Start', 'Start monitoring'),
            ('STOP', 'Stop', 'Stop monitoring'),
        ],
        default='TOGGLE'
    )
    
    _timer = None
    _is_running = False
    
    @classmethod
    def poll(cls, context):
        return True
    
    def modal(self, context, event):
        if event.type == 'TIMER':
            # Check for action changes
            try:
                if context.mode in {'OBJECT', 'POSE'}:
                    sync_sap_action_handler(context.scene)
            except Exception as e:
                if "not allowed" not in str(e):
                    print(f"SAP sync monitor error: {e}")
        
        # Continue running
        return {'PASS_THROUGH'}
    
    def execute(self, context):
        should_start = False
        
        if self.action == 'START' or (self.action == 'TOGGLE' and not SUB_OP_sap_sync_monitor._is_running):
            should_start = True
        elif self.action == 'STOP' or (self.action == 'TOGGLE' and SUB_OP_sap_sync_monitor._is_running):
            should_start = False
        
        if should_start and not SUB_OP_sap_sync_monitor._is_running:
            # Start monitoring
            wm = context.window_manager
            SUB_OP_sap_sync_monitor._timer = wm.event_timer_add(0.1, window=context.window)
            wm.modal_handler_add(self)
            SUB_OP_sap_sync_monitor._is_running = True
            self.report({'INFO'}, "SAP sync monitoring started")
            return {'RUNNING_MODAL'}
        elif not should_start and SUB_OP_sap_sync_monitor._is_running:
            # Stop monitoring
            if SUB_OP_sap_sync_monitor._timer:
                wm = context.window_manager
                wm.event_timer_remove(SUB_OP_sap_sync_monitor._timer)
                SUB_OP_sap_sync_monitor._timer = None
            SUB_OP_sap_sync_monitor._is_running = False
            self.report({'INFO'}, "SAP sync monitoring stopped")
            return {'FINISHED'}
        
        return {'FINISHED'}



class SUB_OP_sync_sap_action(Operator):
    bl_idname = 'sub.sync_sap_action'
    bl_label = 'Sync SAP Action'
    bl_description = 'Manually sync the SAP data action with the current bone animation action'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.object and 
                context.object.type == 'ARMATURE' and 
                context.object.animation_data and 
                context.object.animation_data.action)

    def execute(self, context):
        obj = context.object
        
        if not obj.data.animation_data:
            self.report({'WARNING'}, "No SAP animation data found")
            return {'CANCELLED'}
            
        current_action = obj.animation_data.action
        expected_sap_action_name = f"{obj.name} {current_action.name} SAP Data"
        expected_sap_action = bpy.data.actions.get(expected_sap_action_name)
        
        if expected_sap_action:
            obj.data.animation_data.action = expected_sap_action
            self.report({'INFO'}, f"Synced SAP action to: {expected_sap_action_name}")
        else:
            self.report({'WARNING'}, f"No matching SAP action found: {expected_sap_action_name}")
            
        return {'FINISHED'}

class SUB_PT_sub_smush_anim_data_main(Panel):
    bl_label = "Ultimate Animation Data"
    bl_idname = __qualname__
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'

    def draw(self, context):
        layout = self.layout
        arma = context.object
        
        # Show auto-sync status and manual control
        box = layout.box()
        row = box.row()
        
        # Check if handlers are registered to show auto-sync status
        handlers_active = (sync_sap_action_handler in bpy.app.handlers.frame_change_post and
                          sync_sap_action_depsgraph_handler in bpy.app.handlers.depsgraph_update_post and
                          bpy.app.timers.is_registered(sync_sap_timer))
        
        if handlers_active:
            row.label(text="SAP Auto-Sync: Active", icon='CHECKMARK')
        else:
            row.label(text="SAP Auto-Sync: Inactive", icon='ERROR')
            
        # Manual sync button
        row.operator(SUB_OP_sync_sap_action.bl_idname, icon='FILE_REFRESH', text="Manual Sync")
        layout.operator("sub.face_picker_popup", text="Easy Facial Animation", icon="IMAGE_DATA")

class SUB_PT_sub_smush_anim_data_vis_tracks(Panel):
    bl_label = "Ultimate Visibility Track Entries"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = SUB_PT_sub_smush_anim_data_main.bl_idname

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'

    def draw(self, context):
        layout = self.layout
        obj = context.object
        arma = obj.data
        row = layout.row()
        row.template_list(
            "SUB_UL_vis_track_entries",
            "",
            arma.sub_anim_properties,
            "vis_track_entries",
            arma.sub_anim_properties,
            "active_vis_track_index",
            rows=5,
            maxrows=10,
            )
        col = row.column(align=True)
        col.operator(SUB_OP_vis_entry_add.bl_idname, icon='ADD', text="")
        col.operator(SUB_OP_vis_entry_remove.bl_idname, icon='REMOVE', text="")
        col.separator()
        col.menu("SUB_MT_vis_entry_context_menu", icon='DOWNARROW_HLT', text="")
        col.separator()
        col.operator(SUB_OP_vis_entry_shift.bl_idname, icon='TRIA_UP', text='').shift_direction = 'UP'
        col.operator(SUB_OP_vis_entry_shift.bl_idname, icon='TRIA_DOWN', text='').shift_direction = 'DOWN'
        row = layout.row()
        row.operator(SUB_OP_patch_vis_entries_in_anim_folder.bl_idname, icon='FILE_FOLDER', text='Patch Missing Entries in .nuanmb Folder')
        layout.operator(SUB_OP_reset_vis_to_defaults.bl_idname, icon='LOOP_BACK', text='Reset Vis to Defaults')
        layout.operator(SUB_OP_hide_extra_vis_tracks.bl_idname, icon='HIDE_ON', text='Hide Extra Vis Tracks')
        row = layout.row(align=True)
        row.prop(arma.sub_anim_properties, 'force_vis_track_name', text='')
        row.prop(arma.sub_anim_properties, 'force_vis_track_value', text='True' if arma.sub_anim_properties.force_vis_track_value else 'False', toggle=True)
        row.operator(SUB_OP_force_vis_track.bl_idname, icon='PINNED', text='Force Track')

        sap = arma.sub_anim_properties

        # ── Vis Animation Porting ──────────────────────────────────────────────
        port_box = layout.box()
        port_hdr = port_box.row()
        port_hdr.prop(sap, 'vis_port_expanded',
                      icon='TRIA_DOWN' if sap.vis_port_expanded else 'TRIA_RIGHT',
                      icon_only=True, emboss=False)
        port_hdr.label(text='Vis Animation Porting', icon='FORWARD')
        port_box.prop(sap, 'vis_port_source_folder', text='Source (A)')
        port_box.prop(sap, 'vis_port_target_folder', text='Target (B)')
        port_box.operator(SUB_OP_copy_vis_animation.bl_idname, icon='COPYDOWN', text='Copy Vis (No Rename)')
        if sap.vis_port_expanded:
            port_box.prop(sap, 'vis_port_pairing_file',  text='Pairing File')
            port_row = port_box.row(align=True)
            port_row.operator(SUB_OP_export_vis_names.bl_idname,  icon='EXPORT', text='1. Export Names')
            port_row.operator(SUB_OP_port_vis_animation.bl_idname, icon='IMPORT', text='2. Port Animation')
        box = layout.box()
        header = box.row()
        header.prop(sap, 'face_zones_expanded',
                    icon='TRIA_DOWN' if sap.face_zones_expanded else 'TRIA_RIGHT',
                    icon_only=True, emboss=False)
        header.label(text='Face Zone Fix Config', icon='FACE_MAPS')
        if sap.face_zones_expanded:
            for label, default_prop, kw_prop in (
                ('Eye Zone',        'face_zone_eye_default',   'face_zone_eye_keywords'),
                ('Upper Face Zone', 'face_zone_upper_default', 'face_zone_upper_keywords'),
                ('Lower Face Zone', 'face_zone_lower_default', 'face_zone_lower_keywords'),
            ):
                sub = box.column(align=True)
                sub.label(text=label + ':')
                sub.prop(sap, default_prop, text='Default Entry')
                sub.prop(sap, kw_prop,      text='Keywords')
                box.separator(factor=0.5)
            box.prop(sap, 'face_zone_pair_upper_to_eye')

class SUB_PT_sub_smush_anim_data_mat_tracks(Panel):
    bl_label = "Ultimate Material Tracks"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = SUB_PT_sub_smush_anim_data_main.bl_idname

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'

    def draw(self, context):
        layout = self.layout
        obj = context.object
        arma = obj.data
        col = layout.column()
        row = col.row()
        split = row.split(factor=.4)
        c = split.column()
        c.label(text='Material Names')
        c.template_list(
            "SUB_UL_mat_tracks",
            "",
            arma.sub_anim_properties,
            "mat_tracks",
            arma.sub_anim_properties,
            "active_mat_track_index",
            rows=5,
            maxrows=5,
            )
        split = split.split(factor=.66)
        c = split.column()
        c.label(text='Property Names')
        amti = arma.sub_anim_properties.active_mat_track_index
        if len(arma.sub_anim_properties.mat_tracks) > 0:
            c.template_list(
                "SUB_UL_mat_properties",
                "",
                arma.sub_anim_properties.mat_tracks[amti],
                "properties",
                arma.sub_anim_properties.mat_tracks[amti],
                "active_property_index",
                rows=5,
                maxrows=5,
            )
        else:
            c.enabled = False
        split = split.split()
        c = split.column()
        c.enabled = False
        c.label(text='Property Values')
        if len(arma.sub_anim_properties.mat_tracks) > 0:
            if len(arma.sub_anim_properties.mat_tracks[amti].properties) > 0:
                '''
                After removing the last entry from the list, the 'active' index can remain its previous value
                which is now out of bounds
                '''
                amtpi = arma.sub_anim_properties.mat_tracks[amti].active_property_index
                if amtpi < len(arma.sub_anim_properties.mat_tracks[amti].properties):
                    ap = arma.sub_anim_properties.mat_tracks[amti].properties[amtpi]
                    if ap.sub_type == 'VECTOR':
                        c.prop(ap, "custom_vector", text="")
                        c.prop(ap, "custom_vector", text="", index=0)
                        c.prop(ap, "custom_vector", text="", index=1)
                        c.prop(ap, "custom_vector", text="", index=2)
                        c.prop(ap, "custom_vector", text="", index=3)
                    elif ap.sub_type == 'FLOAT':
                        c.prop(ap, "custom_float", text="", emboss=False)
                    elif ap.sub_type == 'BOOL':
                        icon = 'CHECKBOX_HLT' if ap.custom_bool == True else 'CHECKBOX_DEHLT'
                        c.prop(ap, "custom_bool", text="", icon=icon, emboss=False)
                    elif ap.sub_type == 'PATTERN':
                        c.prop(ap, "pattern_index", text="", emboss=False)
                    elif ap.sub_type == 'TEXTURE':
                        c.prop(ap, "texture_transform", text="", emboss=False)
                    c.enabled = True
        # Bottom Row, composed of 3 Sub Rows algined with the above columns
        row = layout.row()
        # Sub Row 1
        split = row.split(factor=.4)
        sr = split.row(align=True)
        sr.operator(SUB_OP_mat_track_add.bl_idname, text='+')
        sr.operator(SUB_OP_mat_track_remove.bl_idname, text='-')
        # Sub Row 2
        split = split.split(factor=.66)
        sr = split.row(align=True)
        sr.operator(SUB_OP_mat_property_add.bl_idname, text='+')
        sr.operator(SUB_OP_mat_property_remove.bl_idname, text='-')
        sr.operator(SUB_OP_mat_property_shift.bl_idname, icon='TRIA_UP', text='').shift_direction = 'UP'
        sr.operator(SUB_OP_mat_property_shift.bl_idname, icon='TRIA_DOWN', text='').shift_direction = 'DOWN'
        # Sub 3
        split = split.split()
        sr = split.row(align=True)
        sr.menu('SUB_MT_mat_entry_context_menu', text='Drivers...')      

class SUB_OP_mat_track_add(Operator):
    bl_idname = 'sub.mat_track_add'
    bl_label  = 'Add Mat Track'

    def execute(self, context):
        mat_tracks = context.object.data.sub_anim_properties.mat_tracks
        mat_track = mat_tracks.add()
        mat_track.name = 'NewMaterialTrack'
        sap = context.object.data.sub_anim_properties
        sap.active_mat_track_index = sap.mat_tracks.find(mat_track.name)
        return {'FINISHED'}

class SUB_OP_mat_track_remove(Operator):
    bl_idname = 'sub.mat_track_remove'
    bl_label = 'Remove Mat Track'

    @classmethod
    def poll(cls, context):
        sap = context.object.data.sub_anim_properties
        return len(sap.mat_tracks) > 0

    def execute(self, context):
        sap = context.object.data.sub_anim_properties
        amt = sap.mat_tracks[sap.active_mat_track_index]
        # Find matching Fcurve and Remove
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is None:
            sap.mat_tracks.remove(sap.active_mat_track_index)
            i = sap.active_mat_track_index
            sap.active_mat_track_index = min(max(0,i-1),len(sap.mat_tracks))
            return {'FINISHED'}
        # Remove fcurves of all properties of this material track
        for fc in fcurves:
            amti = sap.active_mat_track_index
            if fc.data_path.startswith(f"sub_anim_properties.mat_tracks[{amti}]"):
                fcurves.remove(fc)
        # The remaining materials with an index greater than this one must have all thier fcurves adjusted
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is None:
            sap.mat_tracks.remove(sap.active_mat_track_index)
            i = sap.active_mat_track_index
            sap.active_mat_track_index = min(max(0,i-1),len(sap.mat_tracks))
            return {'FINISHED'}
        for fc in fcurves:
            regex = r"sub_anim_properties\.mat_tracks\[(\d+)\](\.properties\[\d+\]\.\w+)"
            matches = re.match(regex, fc.data_path)
            if matches is None:
                continue
            if len(matches.groups()) < 2:
                continue
            cmti = int(matches.groups()[0])
            suffix = matches.groups()[1]
            amti = sap.active_mat_track_index
            if cmti < amti:
                continue
            new_data_path = f"sub_anim_properties.mat_tracks[{cmti-1}]{suffix}"
            fc.data_path = new_data_path
        # Now actually remove the material track
        sap.mat_tracks.remove(sap.active_mat_track_index)
        i = sap.active_mat_track_index
        sap.active_mat_track_index = min(max(0,i-1),len(sap.mat_tracks))
        # Refresh Material Drivers
        remove_anim_material_drivers(context.object)
        from .import_anim import setup_material_drivers
        setup_material_drivers(context.object)
        return {'FINISHED'}

class SUB_OP_mat_property_add(Operator):
    bl_idname = 'sub.mat_prop_add'
    bl_label = 'Add Material Property'
    bl_property = "sub_type"

    sub_type: bpy.props.EnumProperty(
        name='Mat Track Entry Subtype',
        description='',
        items=mat_sub_types, 
        default='VECTOR',)

    @classmethod
    def poll(cls, context):
        sap = context.object.data.sub_anim_properties
        return len(sap.mat_tracks) > 0

    def execute(self, context):
        sap = context.object.data.sub_anim_properties
        props = sap.mat_tracks[sap.active_mat_track_index].properties
        prop = props.add()
        prop.sub_type = self.sub_type
        if prop.sub_type == 'VECTOR':
            prop.name = f'CustomVectorX'
        elif prop.sub_type == 'FLOAT':
            prop.name = f'CustomFloatX'
        elif prop.sub_type == 'BOOL':
            prop.name = f'CustomBooleanX'
        else:
            prop.name = f'New{prop.sub_type}Property'
        sap.mat_tracks[sap.active_mat_track_index].active_property_index = props.find(prop.name)
        return {'FINISHED'}
    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

def refresh_material_drivers(context):
    from .import_anim import setup_material_drivers
    remove_anim_material_drivers(context.object)
    setup_material_drivers(context.object)

class SUB_OP_mat_property_remove(Operator):
    bl_idname = 'sub.mat_prop_remove'
    bl_label = 'Remove Material Property'

    @classmethod
    def poll(cls, context):
        sap = context.object.data.sub_anim_properties
        if len(sap.mat_tracks) > 0:
            active_track = sap.mat_tracks[sap.active_mat_track_index]
            if len(active_track.properties) > 0:
                return True
        return False

    def execute(self, context):
        sap = context.object.data.sub_anim_properties
        amt = sap.mat_tracks[sap.active_mat_track_index]  
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is None:
            amt.properties.remove(amt.active_property_index)
            i = amt.active_property_index
            amt.active_property_index = min(max(0,i-1), len(amt.properties)-1)
            return {'FINISHED'}
        # Remove matching fcurve
        for fc in fcurves:
            amti = sap.active_mat_track_index
            api = sap.mat_tracks[amti].active_property_index
            if fc.data_path.startswith(f"sub_anim_properties.mat_tracks[{amti}].properties[{api}]"):
                fcurves.remove(fc)
        # The material's remaining properties' fcurves with indexes greater to this one must be decremented
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is None:
            amt.properties.remove(amt.active_property_index)
            i = amt.active_property_index
            amt.active_property_index = min(max(0,i-1), len(amt.properties)-1)
            return {'FINISHED'}

        for fc in fcurves:    
            regex = r"sub_anim_properties\.mat_tracks\[(\d+)\]\.properties\[(\d+)\](\.\w+)"
            matches = re.match(regex, fc.data_path)
            if matches is None:
                continue
            if len(matches.groups()) < 3:
                continue
            cmti = int(matches.groups()[0])
            cpi = int(matches.groups()[1])
            suffix = matches.groups()[2]
            amti = sap.active_mat_track_index
            api = sap.mat_tracks[amti].active_property_index
            if cmti != amti or cpi <= api:
                continue
            new_data_path = f"sub_anim_properties.mat_tracks[{cmti}].properties[{cpi-1}]{suffix}"
            fc.data_path = new_data_path 
        # Now actually remove the property
        amt.properties.remove(amt.active_property_index)
        i = amt.active_property_index
        amt.active_property_index = min(max(0,i-1), len(amt.properties)-1)
        # Refresh Material Drivers
        refresh_material_drivers(context)
        return {'FINISHED'}

def change_mat_property_fcurve_target_index(fcurve, new_property_index):
    regex = r"sub_anim_properties\.mat_tracks\[(\d+)\]\.properties\[(\d+)\](\.\w+)"
    matches = re.match(regex, fcurve.data_path)
    if matches is None:
        return
    if len(matches.groups()) < 3:
        return
    mat_track_index = int(matches.groups()[0])
    _property_index = int(matches.groups()[1])
    suffix = matches.groups()[2]
    new_data_path = f"sub_anim_properties.mat_tracks[{mat_track_index}].properties[{new_property_index}]{suffix}"
    fcurve.data_path = new_data_path

def swap_mat_property_fcurve_target_indices(fcurves, sap, index_a, index_b):
    amti = sap.active_mat_track_index

    a_data_path = f"sub_anim_properties.mat_tracks[{amti}].properties[{index_a}]"
    a_fcurves = [fc for fc in fcurves if fc.data_path.startswith(a_data_path)]
    
    b_data_path = f"sub_anim_properties.mat_tracks[{amti}].properties[{index_b}]"
    b_fcurves = [fc for fc in fcurves if fc.data_path.startswith(b_data_path)]
    
    for fc in a_fcurves:
        change_mat_property_fcurve_target_index(fc, index_b)
    for fc in b_fcurves:
        change_mat_property_fcurve_target_index(fc, index_a)
          
class SUB_OP_mat_property_shift(Operator):
    bl_idname = 'sub.mat_property_shift'
    bl_label = 'Shift Mat Propery'

    shift_direction: EnumProperty(
        name='Shift Direction',
        description='The direction to shift',
        items=[('UP', 'Up', 'Shift it up'),
                ('DOWN', 'Down', 'Shift it down')])
    
    @classmethod
    def poll(cls, context):
        sap = context.object.data.sub_anim_properties
        if len(sap.mat_tracks) >= 1:
            active_track = sap.mat_tracks[sap.active_mat_track_index]
            if len(active_track.properties) >= 2:
                return True
        return False
    
    def execute(self, context):
        sap = context.object.data.sub_anim_properties
        active_mat = sap.mat_tracks[sap.active_mat_track_index]
        active_property_index = active_mat.active_property_index
            
        if (self.shift_direction == 'UP' and active_property_index == 0) or \
           (self.shift_direction == 'DOWN' and active_property_index == len(active_mat.properties)-1):
                return {'CANCELLED'}
        
        other_index = active_property_index-1 if self.shift_direction == 'UP' else active_property_index+1
            
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is not None:
            swap_mat_property_fcurve_target_indices(fcurves, sap, active_property_index, other_index)

        active_mat.properties.move(active_property_index, other_index)
        active_mat.active_property_index = other_index
        # Refresh Material Drivers
        refresh_material_drivers(context)
        return {'FINISHED'}
    
class SUB_OP_vis_entry_add(Operator):
    bl_idname = 'sub.vis_entry_add'
    bl_label = 'Add Vis Track Entry'

    def execute(self, context):
        entries = context.object.data.sub_anim_properties.vis_track_entries
        entry = entries.add()
        entry.name = 'NewVisTrackEntry'
        entry.value = True
        sap = context.object.data.sub_anim_properties
        sap.active_vis_track_index = entries.find(entry.name)
        return {'FINISHED'} 

def refresh_visibility_drivers(context):
    from .import_anim import setup_visibility_drivers
    remove_visibility_drivers(context)
    setup_visibility_drivers(context.object)

class SUB_OP_vis_entry_remove(Operator):
    bl_idname = 'sub.vis_entry_remove'
    bl_label = 'Remove Vis Track Entry'

    @classmethod
    def poll(cls, context):
        sap = context.object.data.sub_anim_properties
        return len(sap.vis_track_entries) > 0

    def execute(self, context):
        sap = context.object.data.sub_anim_properties
        active_vis_track_index = sap.active_vis_track_index
        
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is not None:
            fcurve_to_remove = fcurves.find(f'sub_anim_properties.vis_track_entries[{active_vis_track_index}].value')
            if fcurve_to_remove is not None:
                fcurves.remove(fcurve_to_remove)
            for index in range(active_vis_track_index+1, len(sap.vis_track_entries)):
                fcurve_to_decrement = fcurves.find(f'sub_anim_properties.vis_track_entries[{index}].value')
                if fcurve_to_decrement is not None:
                    fcurve_to_decrement.data_path = f'sub_anim_properties.vis_track_entries[{index-1}].value'
        
        sap.vis_track_entries.remove(active_vis_track_index)
        i = active_vis_track_index
        sap.active_vis_track_index = min(max(0, i-1), len(sap.vis_track_entries))

        refresh_visibility_drivers(context)       
        return {'FINISHED'} 
    
class SUB_OP_vis_entry_shift(Operator):
    bl_idname = 'sub.vis_entry_shift'
    bl_label = 'Shift Vis Entry'

    shift_direction: EnumProperty(
        name='Shift Direction',
        description='The direction to shift',
        items=[('UP', 'Up', 'Shift it up'),
                ('DOWN', 'Down', 'Shift it down')])
    
    @classmethod
    def poll(cls, context):
        sap = context.object.data.sub_anim_properties
        return len(sap.vis_track_entries) > 1
    
    def execute(self, context):
        sap = context.object.data.sub_anim_properties
        vis_entries = sap.vis_track_entries
        active_vis_entry_index = sap.active_vis_track_index
            
        if (self.shift_direction == 'UP' and active_vis_entry_index == 0) or \
           (self.shift_direction == 'DOWN' and active_vis_entry_index == len(vis_entries)-1):
                return {'CANCELLED'}
        
        other_index = active_vis_entry_index-1 if self.shift_direction == 'UP' else active_vis_entry_index+1
            
        fcurves = get_id_action_fcurves(context.object.data)
        if fcurves is not None:
            active_fcurve = fcurves.find(f"sub_anim_properties.vis_track_entries[{active_vis_entry_index}].value")
            other_fcurve = fcurves.find(f"sub_anim_properties.vis_track_entries[{other_index}].value")
            if active_fcurve is not None:
                active_fcurve.data_path = f"sub_anim_properties.vis_track_entries[{other_index}].value"
            if other_fcurve is not None:
                other_fcurve.data_path = f"sub_anim_properties.vis_track_entries[{active_vis_entry_index}].value"

        vis_entries.move(active_vis_entry_index, other_index)
        sap.active_vis_track_index = other_index
        refresh_visibility_drivers(context)
        return {'FINISHED'}

class SUB_OP_vis_drivers_refresh(Operator):
    bl_idname = 'sub.vis_drivers_refresh'
    bl_label = 'Refresh Visibility Drivers'

    def execute(self, context):
        refresh_visibility_drivers(context)
        return {'FINISHED'} 

class SUB_OP_vis_drivers_remove(Operator):
    bl_idname = 'sub.vis_drivers_remove'
    bl_label = 'Remove Visibility Drivers'

    def execute(self, context):
        remove_visibility_drivers(context)
        return {'FINISHED'}

class SUB_OP_auto_fill_vis_entries(Operator):
    bl_idname = 'sub.auto_fill_vis_entries'
    bl_label = 'Auto Fill Vis Entries'

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'

    def execute(self, context):
        arma: bpy.types.Object = context.object
        mesh_names = {child.name for child in arma.children if child.type == 'MESH'}
        vis_names: set[str] = set()
        for name in mesh_names:
            regex = r"(.*)\_VIS\_.*"
            match = re.match(regex, name)
            if match:
                vis_names.add(match.groups()[0])
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        for vis_name in vis_names:
            if vis_name not in sap.vis_track_entries:
                new_entry: SUB_PG_vis_track_entry = sap.vis_track_entries.add()
                new_entry.name = vis_name
                new_entry.value = True
        return {'FINISHED'}

class SUB_OP_set_all_vis_entries_false(Operator):
    bl_idname = 'sub.set_all_vis_entries_false'
    bl_label = 'Set All Vis Entries False'

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'
    
    def execute(self, context):
        for vis_entry in context.object.data.sub_anim_properties.vis_track_entries:
            vis_entry.value = False
        return {'FINISHED'}

class SUB_OP_set_all_vis_entries_true(Operator):
    bl_idname = 'sub.set_all_vis_entries_true'
    bl_label = 'Set All Vis Entries True'

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'
    
    def execute(self, context):
        for vis_entry in context.object.data.sub_anim_properties.vis_track_entries:
            vis_entry.value = True
        return {'FINISHED'}

class SUB_OP_insert_all_vis_entry_keyframes(Operator):
    bl_idname = 'sub.insert_all_vis_entry_keyframes'
    bl_label = 'Insert All Vis Entry Keyframes'

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'
    
    def execute(self, context):
        arma: bpy.types.Object = context.object
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        for index, vis_entry in enumerate(sap.vis_track_entries):
            arma.data.keyframe_insert(data_path=f'sub_anim_properties.vis_track_entries[{index}].value', group='Visibility')
        return {'FINISHED'}

class SUB_OP_organize_vis_entries_alphabetically(Operator):
    bl_idname = 'sub.organize_vis_entries_alphabetically'
    bl_label = 'Organize Vis Entries Alphabetically'

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'
    
    def execute(self, context):
        arma: bpy.types.Object = context.object
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        
        # Get all entries and sort them alphabetically by full name
        entries = list(sap.vis_track_entries)
        entries.sort(key=lambda x: x.name.lower())
        
        # Use bubble sort approach to avoid conflicts
        for i in range(len(entries)):
            for j in range(len(entries) - 1):
                current_entry = sap.vis_track_entries[j]
                next_entry = sap.vis_track_entries[j + 1]
                
                # Compare names (case-insensitive)
                if current_entry.name.lower() > next_entry.name.lower():
                    # Swap positions
                    sap.vis_track_entries.move(j, j + 1)
        
        # Reset active index
        sap.active_vis_track_index = 0
        
        refresh_visibility_drivers(context)
        return {'FINISHED'}

class SUB_OP_organize_vis_entries_by_move(Operator):
    bl_idname = 'sub.organize_vis_entries_by_move'
    bl_label = 'Organize Vis Entries by Move'

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'
    
    def execute(self, context):
        arma: bpy.types.Object = context.object
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        
        # Get all entries
        entries = list(sap.vis_track_entries)
        
        # Group entries by their move type (last part after underscore)
        move_groups = {}
        for entry in entries:
            # Split by underscore and get the last part
            parts = entry.name.split('_')
            if len(parts) > 1:
                move_type = parts[-1]  # Last part after underscore
                if move_type not in move_groups:
                    move_groups[move_type] = []
                move_groups[move_type].append(entry)
            else:
                # If no underscore, put in a special group
                if 'no_move_type' not in move_groups:
                    move_groups['no_move_type'] = []
                move_groups['no_move_type'].append(entry)
        
        # Sort move types alphabetically
        sorted_move_types = sorted(move_groups.keys())
        
        # Create the desired order - group by move type, then sort alphabetically within each group
        desired_order = []
        for move_type in sorted_move_types:
            # Sort entries within each move type alphabetically
            move_entries = sorted(move_groups[move_type], key=lambda x: x.name.lower())
            desired_order.extend(move_entries)
        
        # Move entries to their correct positions using a more direct approach
        # Create a mapping of entry names to their desired positions
        name_to_desired = {entry.name: i for i, entry in enumerate(desired_order)}
        
        # Sort the current list based on the desired order
        for i in range(len(sap.vis_track_entries)):
            for j in range(len(sap.vis_track_entries) - 1):
                current_entry = sap.vis_track_entries[j]
                next_entry = sap.vis_track_entries[j + 1]
                
                # Get desired positions
                current_desired = name_to_desired.get(current_entry.name, len(desired_order))
                next_desired = name_to_desired.get(next_entry.name, len(desired_order))
                
                # If they're in wrong order, swap them
                if current_desired > next_desired:
                    sap.vis_track_entries.move(j, j + 1)
        
        # Reset active index
        sap.active_vis_track_index = 0
        
        refresh_visibility_drivers(context)
        return {'FINISHED'}

class SUB_OP_batch_add_hidden_keyframes_for_new_vis_entries(Operator):
    bl_idname = 'sub.batch_add_hidden_keyframes_for_new_vis_entries'
    bl_label = 'Set New Vis Entries Hidden in All Actions'
    bl_description = (
        'For every SAP Data action belonging to this armature, inserts a constant False keyframe '
        'for any vis track entry that currently has no keyframes in that action. '
        'Use this after adding new facial expression entries to make them hidden across all 160+ existing animations.'
    )

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == 'ARMATURE'

    def execute(self, context):
        arma: bpy.types.Object = context.object
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        arma_name = arma.name

        sap_prefix = f'{arma_name} '
        sap_suffix = ' SAP Data'
        sap_actions = [
            action for action in bpy.data.actions
            if action.name.startswith(sap_prefix) and action.name.endswith(sap_suffix)
        ]

        if not sap_actions:
            self.report({'WARNING'}, 'No SAP Data actions found for this armature.')
            return {'CANCELLED'}

        added = 0
        for action in sap_actions:
            for index, entry in enumerate(sap.vis_track_entries):
                data_path = f'sub_anim_properties.vis_track_entries[{index}].value'
                if find_fcurve(action, data_path) is not None:
                    continue
                fcurve = new_fcurve(action, data_path, index=0, action_group='Visibility')
                fcurve.extrapolation = 'CONSTANT'
                kp = fcurve.keyframe_points.insert(frame=1, value=0.0)
                kp.interpolation = 'CONSTANT'
                added += 1

        self.report({'INFO'}, f'Added {added} hidden keyframes across {len(sap_actions)} SAP actions.')
        return {'FINISHED'}

class SUB_OP_patch_vis_entries_in_anim_folder(Operator):
    bl_idname = 'sub.patch_vis_entries_in_anim_folder'
    bl_label = 'Patch Vis Entries in Animation Folder'
    bl_description = (
        'Scans a folder of .nuanmb files, injects missing vis entries as hidden, '
        'and optionally ensures each face zone has at least one active track. '
        'Edits files directly — no Blender actions are created.'
    )

    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_glob: bpy.props.StringProperty(default='*.nuanmb', options={'HIDDEN'})
    apply_face_zone_fix: BoolProperty(
        name='Apply Face Zone Fix',
        description=(
            'After injecting missing entries, ensure each configured face zone has an '
            'active track. If a zone has no enabled track, the zone\'s default entry is '
            'set to True for all frames. Configure zones in the Face Zone Fix Config box.'
        ),
        default=True,
    )
    zero_inactive_zone_tracks: BoolProperty(
        name='Zero Inactive Zone Tracks',
        description=(
            'Before running the zone fix, set all zone expression tracks to False. '
            'This clears stale True values left by the original animation or previous '
            'patch runs, so only what the zone fix explicitly enables will be active.'
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        return len(context.object.data.sub_anim_properties.vis_track_entries) > 0

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.prop(self, 'apply_face_zone_fix')
        sub = col.column()
        sub.enabled = self.apply_face_zone_fix
        sub.prop(self, 'zero_inactive_zone_tracks')

    @staticmethod
    def _extract_variant(node_name, keywords):
        """Return (matched_keyword, variant_str) for the first matching zone keyword.

        Naming convention: {Char}_{Zone}_{Variant}_VIS_O_OBJShape
          default  →  Pit_Eye_VIS_O_OBJShape          variant = ''
          custom   →  Pit_Eye_Charge_VIS_O_OBJShape   variant = 'Charge'

        Returns (None, None) when no keyword matches.
        """
        for kw in keywords:
            marker = f'_{kw}_'
            idx = node_name.find(marker)
            if idx != -1:
                after = node_name[idx + len(marker):]
                if after.startswith('VIS_'):
                    return kw, ''           # default — nothing between zone and VIS suffix
                vis_idx = after.find('_VIS_')
                variant = after[:vis_idx] if vis_idx != -1 else after
                return kw, variant
            if node_name.endswith(f'_{kw}'):
                return kw, ''
        return None, None

    def _run_frame_zone_fixes(self, sap, vis_group, frame_count, zero_inactive=False, log=None):
        """Pass 2 (variant sync) + Pass 3 (conflict resolution), frame by frame.

        If *zero_inactive* is True, all zone expression entries are zeroed before
        passes run so stale True values from previous edits can't persist.
        If *log* is a list it is filled with diagnostic lines for patch_log.txt.
        """
        zone_defs = [
            (sap.face_zone_eye_default.strip(),
             [k.strip() for k in sap.face_zone_eye_keywords.split(',')   if k.strip()]),
            (sap.face_zone_upper_default.strip(),
             [k.strip() for k in sap.face_zone_upper_keywords.split(',') if k.strip()]),
            (sap.face_zone_lower_default.strip(),
             [k.strip() for k in sap.face_zone_lower_keywords.split(',') if k.strip()]),
        ]
        zone_defs = [(d, kws) for d, kws in zone_defs if kws]
        if not zone_defs:
            if log is not None:
                log.append('  [SKIP] No face zones have keywords configured — zone fix skipped.')
            return False

        # The configured zone defaults are mesh-visibility tracks (_VIS_O_OBJShape).
        # They must NOT enter zone_nodes because they serve a different purpose than
        # the expression tracks and would cause Pass 3 to conflict-resolve them.
        # They are handled exclusively by Pass 4 below.
        vis_default_names = {d for d, _ in zone_defs if d}

        # Assign each node (that has track data) to the first matching zone.
        # zone_nodes[z] = [(node, variant_str), ...]
        zone_nodes = [[] for _ in zone_defs]
        for node in vis_group.nodes:
            if not node.tracks:
                continue
            if node.name in vis_default_names:
                continue  # VIS default handled by Pass 4
            for z_idx, (_, keywords) in enumerate(zone_defs):
                _, variant = self._extract_variant(node.name, keywords)
                if variant is not None:
                    zone_nodes[z_idx].append((node, variant))
                    break

        # Log zone layout once per file.
        if log is not None:
            log.append(f'  frame_count={frame_count}')
            for z_idx, (default_name, keywords) in enumerate(zone_defs):
                log.append(f'  Zone {z_idx} keywords={keywords} default={default_name!r} — {len(zone_nodes[z_idx])} entries:')
                for n, v in zone_nodes[z_idx]:
                    log.append(f'    variant={v!r:20s}  {n.name}')

        # Mutable Python value lists keyed by node name.
        # Constant tracks are stored as a single element in ssbh_data_py; expand
        # them to full frame_count so per-frame indexing is always safe.
        vals = {}
        for n in vis_group.nodes:
            if not n.tracks:
                continue
            v = list(n.tracks[0].values)
            if len(v) < frame_count:
                last = v[-1] if v else False
                v = v + [last] * (frame_count - len(v))
            vals[n.name] = v

        changed = False

        # Determine which zones had any pre-existing True values BEFORE the zero pass.
        # The quiet / not-matched branches only enable defaults for zones that originally
        # had expression activity, preventing spurious defaults from being added to
        # animations (like wait01) whose entries were injected as all-False by Pass 1.
        zone_was_active = [
            any(vals[n.name][f] for n, _ in entries for f in range(frame_count))
            for entries in zone_nodes
        ]

        # ── Smart auto-zero: uniform-True non-default entries are stale artifacts ──
        # Non-default expression entries that are True for EVERY frame are artifacts from
        # a previous bad patch run — legitimate animation data has per-frame variation.
        # Zero them so they don't corrupt cross-zone signals or leave spurious actives.
        # Default entries (variant == '') are intentionally excluded.
        auto_zeroed = []
        for entries in zone_nodes:
            for n, v in entries:
                if v == '':
                    continue  # Never touch default entries
                if frame_count > 0 and all(vals[n.name][f] for f in range(frame_count)):
                    for f in range(frame_count):
                        vals[n.name][f] = False
                    changed = True
                    auto_zeroed.append(n.name)
        if auto_zeroed and log is not None:
            short = (auto_zeroed if len(auto_zeroed) <= 6
                     else auto_zeroed[:6] + [f'...+{len(auto_zeroed)-6} more'])
            log.append(
                f'  AutoZero: cleared {len(auto_zeroed)} uniform-True non-default'
                f' entries (stale): {short}'
            )

        # ── Zero pass (optional): clear all zone expression entries to False ──
        # Eliminates stale True values from original keyframe data or previous runs
        # before the zone sync passes re-enable only what's needed.
        if zero_inactive:
            for entries in zone_nodes:
                for n, _ in entries:
                    for f in range(frame_count):
                        if vals[n.name][f]:
                            vals[n.name][f] = False
                            changed = True

        for f in range(frame_count):
            # ── Pass 2a: snapshot the first active non-default variant per zone ──
            # Using per-zone snapshots (not a global list) so that each zone's cross-zone
            # signal excludes its OWN active variant.  This is the key that lets us
            # detect and correct mismatches like Eye_Charge + Mouth_Pissed.
            zone_cur_variant = []   # zone_cur_variant[z] = variant str or None
            for entries in zone_nodes:
                v_active = next(
                    (v for n, v in entries if v and vals[n.name][f]),
                    None,
                )
                zone_cur_variant.append(v_active)

            any_cross = any(zone_cur_variant)
            if any_cross and log is not None:
                summary = [f'Z{z}={v!r}' for z, v in enumerate(zone_cur_variant) if v]
                log.append(f'  Frame {f:4d}: zone_variants={summary}')

            # ── Pass 2b: sync each zone to the cross-zone signal ──
            # cross_variants[z] = variants active in zones *other than* z.
            # If a zone's own variant already matches the cross-zone signal → leave it.
            # If it has a mismatched non-default → disable the mismatch, enable the signal.
            # If it has nothing → enable the signal (or fall back to zone default).
            for z_idx, (default_name, _) in enumerate(zone_defs):
                entries = zone_nodes[z_idx]

                # Build cross-zone variant list (deduplicated, excluding this zone).
                cross_variants = []
                for z2, v2 in enumerate(zone_cur_variant):
                    if z2 != z_idx and v2 is not None and v2 not in cross_variants:
                        cross_variants.append(v2)

                this_v = zone_cur_variant[z_idx]

                if cross_variants:
                    # Zone already shows one of the cross-zone variants → correct, skip.
                    if this_v in cross_variants:
                        if log is not None:
                            log.append(
                                f'    Zone {z_idx}: variant={this_v!r} matches cross-zone {cross_variants} — skip'
                            )
                        continue

                    # Mismatch or empty: disable the wrong non-default (if any), then
                    # enable the first matching cross-zone variant that exists here.
                    if this_v is not None:
                        for n, v in entries:
                            if v == this_v and vals[n.name][f]:
                                vals[n.name][f] = False
                                changed = True
                                if log is not None:
                                    log.append(
                                        f'    Zone {z_idx}: DISABLED mismatch {n.name} '
                                        f'(had {this_v!r}, cross-zone wants {cross_variants})'
                                    )
                        zone_cur_variant[z_idx] = None   # update live snapshot

                    matched = False
                    for target in cross_variants:
                        hit = next((n for n, v in entries if v == target), None)
                        if hit is not None:
                            if not vals[hit.name][f]:
                                vals[hit.name][f] = True
                                changed = True
                                if log is not None:
                                    log.append(
                                        f'    Zone {z_idx}: ENABLED {hit.name} (cross-zone variant={target!r})'
                                    )
                            else:
                                if log is not None:
                                    log.append(
                                        f'    Zone {z_idx}: {hit.name} already True (cross-zone variant={target!r})'
                                    )
                            zone_cur_variant[z_idx] = target   # update live snapshot
                            matched = True
                            break

                    if not matched:
                        if log is not None:
                            zone_variants = [v for _, v in entries if v]
                            log.append(
                                f'    Zone {z_idx}: cross-zone {cross_variants} not in zone '
                                f'(zone has {zone_variants}) — falling to default'
                            )
                        # Variant doesn't exist here → enable zone default if zone is empty
                        # AND the zone originally had activity (to avoid enabling defaults in
                        # zones that are empty only because Pass 1 injected them as False).
                        if zone_was_active[z_idx] and not any(vals[n.name][f] for n, _ in entries):
                            default_node = next((n for n, v in entries if v == ''), None)
                            if default_node and not vals[default_node.name][f]:
                                vals[default_node.name][f] = True
                                changed = True
                                if log is not None:
                                    log.append(
                                        f'    Zone {z_idx} frame {f}: no variant match — enabled default {default_node.name}'
                                    )

                else:
                    # No cross-zone signal: quiet frame or zone is completely independent
                    # (e.g. an eye-blink with no matching mouth variant).
                    # Only enable the zone default if the zone is entirely empty AND the
                    # zone originally had activity (to avoid adding defaults to animations
                    # that never had any expression data in this zone to begin with).
                    if zone_was_active[z_idx] and not any(vals[n.name][f] for n, _ in entries):
                        default_node = next((n for n, v in entries if v == ''), None)
                        if default_node and not vals[default_node.name][f]:
                            vals[default_node.name][f] = True
                            changed = True
                            if log is not None:
                                log.append(
                                    f'    Zone {z_idx} frame {f}: quiet — enabled default {default_node.name}'
                                )

            # ── Pass 3: at most one active entry per zone per frame ──
            for z_idx, entries in enumerate(zone_nodes):
                active = [(n, v) for n, v in entries if vals[n.name][f]]
                if len(active) <= 1:
                    continue

                # Drop defaults (empty variant) first.
                non_def = [(n, v) for n, v in active if v]
                if non_def:
                    for n, v in active:
                        if not v:
                            vals[n.name][f] = False
                            changed = True
                            if log is not None:
                                log.append(f'    Pass3 Zone {z_idx} frame {f}: dropped default {n.name}')
                    active = non_def

                # Still multiple non-defaults? Keep the first, disable the rest.
                if len(active) > 1:
                    for n, v in active[1:]:
                        vals[n.name][f] = False
                        changed = True
                        if log is not None:
                            log.append(
                                f'    Pass3 Zone {z_idx} frame {f}: dropped extra non-default {n.name} '
                                f'(kept {active[0][0].name})'
                            )

        # ── Pass 4: mesh-visibility (VIS) default tracks ──
        # The configured zone defaults (face_zone_*_default) are _VIS_O_OBJShape tracks
        # that control whether each face region's geometry is visible at all.
        # They are always-on; expressions are overlaid on top of them.
        #   • upper (Zone 1) and lower (Zone 2) defaults → True for every frame
        #   • eye (Zone 0) default → coupled to upper (True iff upper is True)
        # Additionally, when a zone's VIS track is True but no expression is active
        # (e.g. animations injected as all-False by Pass 1 with no original data),
        # the zone's default expression is enabled so the game has an explicit cue.
        if len(zone_defs) >= 2:
            upper_vis = zone_defs[1][0]   # e.g. Pit_Openblink_VIS_O_OBJShape
            eye_vis   = zone_defs[0][0]   # e.g. Pit_Eye_VIS_O_OBJShape
            lower_vis = zone_defs[2][0] if len(zone_defs) >= 3 else ''

            if log is not None:
                log.append(
                    f'  Pass4: upper_vis={upper_vis!r} in_vals={upper_vis in vals}  '
                    f'eye_vis={eye_vis!r} in_vals={eye_vis in vals}  '
                    f'lower_vis={lower_vis!r} in_vals={bool(lower_vis and lower_vis in vals)}'
                )

            # Set upper and lower to True for all frames.
            for vis_name in (upper_vis, lower_vis):
                if vis_name and vis_name in vals:
                    for f in range(frame_count):
                        if not vals[vis_name][f]:
                            vals[vis_name][f] = True
                            changed = True

            # Couple eye VIS to upper VIS.
            if sap.face_zone_pair_upper_to_eye and upper_vis in vals and eye_vis in vals:
                for f in range(frame_count):
                    target = vals[upper_vis][f]
                    if vals[eye_vis][f] != target:
                        vals[eye_vis][f] = target
                        changed = True

            # When a zone's VIS track is on but no expression is active, enable the zone
            # default expression.  This handles animations that had no original face data
            # (zone_was_active=False, so the quiet branch correctly stayed silent) but still
            # need an explicit expression track for the game to display the correct face.
            for z_idx, (vis_name, _) in enumerate(zone_defs):
                if not vis_name or vis_name not in vals:
                    continue
                entries = zone_nodes[z_idx]
                for f in range(frame_count):
                    if not vals[vis_name][f]:
                        continue  # VIS off — no expression needed for this frame
                    if any(vals[n.name][f] for n, _ in entries):
                        continue  # An expression is already active — leave it alone
                    def_node = next((n for n, v in entries if v == ''), None)
                    if def_node and not vals[def_node.name][f]:
                        vals[def_node.name][f] = True
                        changed = True
                        if log is not None:
                            log.append(
                                f'    Pass4 Zone {z_idx} frame {f}: VIS on, zone empty'
                                f' — enabled default {def_node.name}'
                            )

        # ── Final state dump: confirm exactly what will be written to disk ──
        if log is not None:
            # VIS defaults summary (same every frame after Pass 4, so just show frame 0)
            vis_summary = []
            for z_idx, (default_name, _) in enumerate(zone_defs):
                if default_name and default_name in vals:
                    state = 'True' if vals[default_name][0] else 'False'
                    vis_summary.append(f'{default_name}={state}')
            if vis_summary:
                log.append(f'  VIS defaults (all frames): {", ".join(vis_summary)}')

            log.append('  Final state (frames with active expression variants):')
            prev_line = None
            repeat_start = None
            repeat_count = 0
            for f in range(frame_count):
                has_variant = any(
                    v and vals[n.name][f]
                    for entries in zone_nodes
                    for n, v in entries
                )
                if not has_variant:
                    continue
                parts = []
                for z_idx, entries in enumerate(zone_nodes):
                    active_names = [n.name for n, _ in entries if vals[n.name][f]]
                    if active_names:
                        parts.append(f'Z{z_idx}=[{",".join(active_names)}]')
                line = ' | '.join(parts)
                if line == prev_line:
                    repeat_count += 1
                else:
                    if repeat_count > 0:
                        log.append(f'    ... (same through F{f - 1:4d})')
                    log.append(f'    F{f:4d}: {line}')
                    prev_line = line
                    repeat_start = f
                    repeat_count = 0
            if repeat_count > 0:
                log.append(f'    ... (same through F{frame_count - 1:4d})')

        if changed:
            for node in vis_group.nodes:
                if node.name in vals and node.tracks:
                    node.tracks[0].values = vals[node.name]

        return changed

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        arma: bpy.types.Object = context.object
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties

        entry_names = [entry.name for entry in sap.vis_track_entries]
        if not entry_names:
            self.report({'WARNING'}, 'No vis track entries defined on this armature.')
            return {'CANCELLED'}

        folder = Path(self.directory)
        nuanmb_files = sorted(folder.glob('*.nuanmb'))
        if not nuanmb_files:
            self.report({'WARNING'}, f'No .nuanmb files found in: {self.directory}')
            return {'CANCELLED'}

        patched = 0
        skipped = 0
        added_total = 0

        import datetime
        log_lines = [
            f'patch_log.txt — {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'Folder : {folder}',
            f'Files  : {len(nuanmb_files)}',
            f'Face zone fix: {self.apply_face_zone_fix}',
            '',
        ]

        for filepath in nuanmb_files:
            try:
                ssbh_anim_data = ssbh_data_py.anim_data.read_anim(str(filepath))
            except Exception as e:
                self.report({'WARNING'}, f'Could not read {filepath.name}: {e}')
                log_lines.append(f'ERROR reading {filepath.name}: {e}')
                skipped += 1
                continue

            frame_count = int(ssbh_anim_data.final_frame_index) + 1

            vis_group = None
            for group in ssbh_anim_data.groups:
                if group.group_type == ssbh_data_py.anim_data.GroupType.Visibility:
                    vis_group = group
                    break

            if vis_group is None:
                log_lines.append(f'SKIP (no vis group): {filepath.name}')
                skipped += 1
                continue

            file_changed = False
            file_log = [f'FILE: {filepath.name}  ({frame_count} frames)']

            # Pass 1: inject missing entries as False
            existing = {node.name for node in vis_group.nodes}
            added_here = []
            for name in entry_names:
                if name in existing:
                    continue
                node = ssbh_data_py.anim_data.NodeData(name)
                track = ssbh_data_py.anim_data.TrackData('Visibility')
                track.values = [False] * frame_count
                node.tracks.append(track)
                vis_group.nodes.append(node)
                added_here.append(name)
                added_total += 1
                file_changed = True
            if added_here:
                file_log.append(f'  Pass1 injected {len(added_here)} entries: {added_here}')
            else:
                file_log.append('  Pass1: no missing entries')

            # Inject configured zone VIS defaults if missing.
            # These are the _VIS_O_OBJShape tracks that control whether each face
            # region is geometrically visible.  They must exist in the file so that
            # Pass 4 can set them to True; the game won't show the face without them
            # if the track is present (it overrides the character's neutral default).
            if self.apply_face_zone_fix:
                vis_def_names = [
                    sap.face_zone_eye_default.strip(),
                    sap.face_zone_upper_default.strip(),
                    sap.face_zone_lower_default.strip(),
                ]
                existing_now = {node.name for node in vis_group.nodes}
                vis_injected = []
                for name in vis_def_names:
                    if name and name not in existing_now:
                        node = ssbh_data_py.anim_data.NodeData(name)
                        track = ssbh_data_py.anim_data.TrackData('Visibility')
                        track.values = [False] * frame_count
                        node.tracks.append(track)
                        vis_group.nodes.append(node)
                        vis_injected.append(name)
                        file_changed = True
                        existing_now.add(name)
                if vis_injected:
                    file_log.append(f'  VIS defaults injected: {vis_injected}')

            # Pass 2 + 3 + 4: per-frame face zone fix
            if self.apply_face_zone_fix:
                zone_log = []
                if self._run_frame_zone_fixes(
                    sap, vis_group, frame_count,
                    zero_inactive=self.zero_inactive_zone_tracks,
                    log=zone_log,
                ):
                    file_changed = True
                file_log.extend(zone_log)

            log_lines.extend(file_log)
            log_lines.append('')

            if not file_changed:
                continue

            try:
                ssbh_anim_data.save(str(filepath))
                patched += 1
            except Exception as e:
                self.report({'WARNING'}, f'Could not save {filepath.name}: {e}')
                log_lines.append(f'  ERROR saving: {e}')
                skipped += 1

        # Write the log file
        log_path = folder / 'patch_log.txt'
        try:
            log_path.write_text('\n'.join(log_lines), encoding='utf-8')
        except Exception as e:
            self.report({'WARNING'}, f'Could not write patch_log.txt: {e}')

        self.report(
            {'INFO'},
            f'Done: {patched} files patched ({added_total} entries injected), {skipped} skipped. '
            f'Log: {log_path}',
        )
        return {'FINISHED'}

class SUB_OP_reset_vis_to_defaults(Operator):
    """Set all vis tracks in a folder to the 'default face' state:
    VIS defaults (Openblink, FaceN, Eye coupling) True, all expression tracks False."""

    bl_idname = 'sub.reset_vis_to_defaults'
    bl_label = 'Reset Vis Tracks to Defaults'
    bl_description = (
        'For each .nuanmb in the selected folder, set all face expression vis tracks to '
        'False and set the configured VIS default tracks to True. Use to restore a clean '
        'neutral-face state before re-patching.'
    )

    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_glob: bpy.props.StringProperty(default='*.nuanmb', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        sap = context.object.data.sub_anim_properties
        return bool(
            sap.face_zone_eye_default.strip() or
            sap.face_zone_upper_default.strip() or
            sap.face_zone_lower_default.strip()
        )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        arma = context.object
        sap = arma.data.sub_anim_properties

        vis_def_names = {
            sap.face_zone_eye_default.strip(),
            sap.face_zone_upper_default.strip(),
            sap.face_zone_lower_default.strip(),
        }
        vis_def_names.discard('')

        upper_vis = sap.face_zone_upper_default.strip()
        eye_vis   = sap.face_zone_eye_default.strip()
        lower_vis = sap.face_zone_lower_default.strip()

        folder = Path(self.directory)
        if self.files:
            nuanmb_files = [
                folder / f.name for f in self.files
                if (folder / f.name).suffix.lower() == '.nuanmb'
            ]
        else:
            nuanmb_files = sorted(folder.glob('*.nuanmb'))
        if not nuanmb_files:
            self.report({'WARNING'}, f'No .nuanmb files selected in: {self.directory}')
            return {'CANCELLED'}

        patched = 0
        skipped = 0

        for filepath in nuanmb_files:
            try:
                ssbh_anim_data = ssbh_data_py.anim_data.read_anim(str(filepath))
            except Exception as e:
                self.report({'WARNING'}, f'Could not read {filepath.name}: {e}')
                skipped += 1
                continue

            frame_count = int(ssbh_anim_data.final_frame_index) + 1

            vis_group = None
            for group in ssbh_anim_data.groups:
                if group.group_type == ssbh_data_py.anim_data.GroupType.Visibility:
                    vis_group = group
                    break
            if vis_group is None:
                skipped += 1
                continue

            existing = {node.name for node in vis_group.nodes}

            # Inject any missing VIS defaults as all-False (Pass 4 sets them True).
            for name in (upper_vis, lower_vis, eye_vis):
                if name and name not in existing:
                    node = ssbh_data_py.anim_data.NodeData(name)
                    track = ssbh_data_py.anim_data.TrackData('Visibility')
                    track.values = [False] * frame_count
                    node.tracks.append(track)
                    vis_group.nodes.append(node)
                    existing.add(name)

            # Zero all tracks, then enable VIS defaults.
            for node in vis_group.nodes:
                if not node.tracks:
                    continue
                if node.name in vis_def_names:
                    node.tracks[0].values = [True] * frame_count
                else:
                    node.tracks[0].values = [False] * frame_count

            # Couple eye VIS to upper VIS (both True at this point → eye=True too).
            if sap.face_zone_pair_upper_to_eye and upper_vis and eye_vis:
                upper_node = next((n for n in vis_group.nodes if n.name == upper_vis), None)
                eye_node   = next((n for n in vis_group.nodes if n.name == eye_vis),   None)
                if upper_node and eye_node:
                    eye_node.tracks[0].values = list(upper_node.tracks[0].values)

            try:
                ssbh_anim_data.save(str(filepath))
                patched += 1
            except Exception as e:
                self.report({'WARNING'}, f'Could not save {filepath.name}: {e}')
                skipped += 1

        self.report(
            {'INFO'},
            f'Reset {patched} files to default vis state, {skipped} skipped.',
        )
        return {'FINISHED'}


class SUB_OP_hide_extra_vis_tracks(Operator):
    """Zero any vis track in the .nuanmb files that is NOT in the Blender vis entry list.
    Tracks missing from the list are extras left over from the original file and should
    be hidden (False for all frames) so they don't cause unexpected mesh visibility."""

    bl_idname = 'sub.hide_extra_vis_tracks'
    bl_label = 'Hide Extra Vis Tracks'
    bl_description = (
        'For each .nuanmb in the selected folder, find any vis track that is NOT '
        'in the Blender vis entry list and set it to False for all frames. '
        'Tracks already in the list are left untouched.'
    )

    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_glob: bpy.props.StringProperty(default='*.nuanmb', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        return bool(context.object.data.sub_anim_properties.vis_track_entries)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        arma = context.object
        sap = arma.data.sub_anim_properties

        # Build the set of track names that are in the Blender list — these are kept.
        known_names = {entry.name for entry in sap.vis_track_entries}

        if not known_names:
            self.report({'WARNING'}, 'No vis track entries in the Blender list.')
            return {'CANCELLED'}

        folder = Path(self.directory)
        if self.files:
            nuanmb_files = [
                folder / f.name for f in self.files
                if (folder / f.name).suffix.lower() == '.nuanmb'
            ]
        else:
            nuanmb_files = sorted(folder.glob('*.nuanmb'))
        if not nuanmb_files:
            self.report({'WARNING'}, f'No .nuanmb files selected in: {self.directory}')
            return {'CANCELLED'}

        patched = 0
        skipped = 0
        total_zeroed = 0

        for filepath in nuanmb_files:
            try:
                ssbh_anim_data = ssbh_data_py.anim_data.read_anim(str(filepath))
            except Exception as e:
                self.report({'WARNING'}, f'Could not read {filepath.name}: {e}')
                skipped += 1
                continue

            frame_count = int(ssbh_anim_data.final_frame_index) + 1

            vis_group = None
            for group in ssbh_anim_data.groups:
                if group.group_type == ssbh_data_py.anim_data.GroupType.Visibility:
                    vis_group = group
                    break
            if vis_group is None:
                skipped += 1
                continue

            file_zeroed = 0
            extra_names = []
            for node in vis_group.nodes:
                if node.name in known_names:
                    continue  # This track is in the Blender list — leave it alone
                if not node.tracks:
                    continue
                # Extra track not in the list — zero it unconditionally
                node.tracks[0].values = [False] * frame_count
                file_zeroed += 1
                extra_names.append(node.name)

            if extra_names:
                self.report(
                    {'INFO'},
                    f'{filepath.name}: zeroed {file_zeroed} extra tracks: {extra_names[:6]}'
                    + (f' ...+{len(extra_names)-6} more' if len(extra_names) > 6 else ''),
                )

            total_zeroed += file_zeroed

            try:
                ssbh_anim_data.save(str(filepath))
                patched += 1
            except Exception as e:
                self.report({'WARNING'}, f'Could not save {filepath.name}: {e}')
                skipped += 1

        self.report(
            {'INFO'},
            f'Done: {patched} files processed, {total_zeroed} extra tracks zeroed total, {skipped} skipped.',
        )
        return {'FINISHED'}


class SUB_OP_force_vis_track(Operator):
    """Force a single vis track to a fixed value (True or False) for all frames
    in each selected .nuanmb file.  If the track is missing from a file it is
    injected first.  Whatever was there before is completely replaced."""

    bl_idname = 'sub.force_vis_track'
    bl_label = 'Force Vis Track'
    bl_description = (
        'For each selected .nuanmb file, set the named vis track to True or False '
        'for every frame, overwriting whatever was there.  The track is injected if '
        'it does not already exist in the file.'
    )

    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_glob: bpy.props.StringProperty(default='*.nuanmb', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        return bool(context.object.data.sub_anim_properties.force_vis_track_name.strip())

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        arma = context.object
        sap = arma.data.sub_anim_properties

        track_name = sap.force_vis_track_name.strip()
        track_value = sap.force_vis_track_value

        if not track_name:
            self.report({'WARNING'}, 'No track name specified.')
            return {'CANCELLED'}

        folder = Path(self.directory)
        if self.files:
            nuanmb_files = [
                folder / f.name for f in self.files
                if (folder / f.name).suffix.lower() == '.nuanmb'
            ]
        else:
            nuanmb_files = sorted(folder.glob('*.nuanmb'))
        if not nuanmb_files:
            self.report({'WARNING'}, f'No .nuanmb files selected in: {self.directory}')
            return {'CANCELLED'}

        patched = 0
        injected = 0
        skipped = 0

        for filepath in nuanmb_files:
            try:
                ssbh_anim_data = ssbh_data_py.anim_data.read_anim(str(filepath))
            except Exception as e:
                self.report({'WARNING'}, f'Could not read {filepath.name}: {e}')
                skipped += 1
                continue

            frame_count = int(ssbh_anim_data.final_frame_index) + 1

            vis_group = None
            for group in ssbh_anim_data.groups:
                if group.group_type == ssbh_data_py.anim_data.GroupType.Visibility:
                    vis_group = group
                    break

            if vis_group is None:
                self.report({'WARNING'}, f'{filepath.name}: no Visibility group found, skipping.')
                skipped += 1
                continue

            # Find or inject the target node.
            target_node = next((n for n in vis_group.nodes if n.name == track_name), None)
            if target_node is None:
                target_node = ssbh_data_py.anim_data.NodeData(track_name)
                track = ssbh_data_py.anim_data.TrackData('Visibility')
                track.values = [track_value] * frame_count
                target_node.tracks.append(track)
                vis_group.nodes.append(target_node)
                injected += 1
            else:
                if not target_node.tracks:
                    track = ssbh_data_py.anim_data.TrackData('Visibility')
                    target_node.tracks.append(track)
                target_node.tracks[0].values = [track_value] * frame_count

            try:
                ssbh_anim_data.save(str(filepath))
                patched += 1
            except Exception as e:
                self.report({'WARNING'}, f'Could not save {filepath.name}: {e}')
                skipped += 1

        self.report(
            {'INFO'},
            f'Forced {track_name!r} = {track_value} in {patched} files '
            f'({injected} injected), {skipped} skipped.',
        )
        return {'FINISHED'}


class SUB_OP_copy_vis_animation(Operator):
    """Copy vis tracks from Source (A) into Target (B) by matching track names directly.
    No pairing list needed — use this when both folders already share the same names."""

    bl_idname = 'sub.copy_vis_animation'
    bl_label = 'Copy Vis Animation'
    bl_description = (
        'For each .nuanmb present in both Source Folder (A) and Target Folder (B), '
        'copy all vis tracks from A into B by name.  Existing tracks in B are '
        'overwritten; tracks missing from B are injected.  No name translation.'
    )

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        sap = context.object.data.sub_anim_properties
        return bool(
            sap.vis_port_source_folder.strip() and
            sap.vis_port_target_folder.strip()
        )

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        sap = context.object.data.sub_anim_properties
        source = Path(sap.vis_port_source_folder.strip())
        target = Path(sap.vis_port_target_folder.strip())

        if not source.is_dir():
            self.report({'WARNING'}, f'Source folder not found: {source}')
            return {'CANCELLED'}
        if not target.is_dir():
            self.report({'WARNING'}, f'Target folder not found: {target}')
            return {'CANCELLED'}

        src_files = sorted(source.glob('*.nuanmb'))
        if not src_files:
            self.report({'WARNING'}, f'No .nuanmb files found in source: {source}')
            return {'CANCELLED'}

        no_match = [f.name for f in src_files if not (target / f.name).exists()]
        self.report(
            {'INFO'},
            f'Source: {len(src_files)} files, {len(src_files) - len(no_match)} matched in target, '
            f'{len(no_match)} unmatched'
            + (': ' + ', '.join(no_match[:5]) + (f' ...+{len(no_match)-5} more' if len(no_match) > 5 else '') if no_match else '.'),
        )

        patched = 0
        skipped = 0
        total_tracks = 0

        for src_file in src_files:
            dst_file = target / src_file.name
            if not dst_file.exists():
                continue

            try:
                src_anim = ssbh_data_py.anim_data.read_anim(str(src_file))
                dst_anim = ssbh_data_py.anim_data.read_anim(str(dst_file))
            except Exception as e:
                self.report({'WARNING'}, f'Could not read {src_file.name}: {e}')
                skipped += 1
                continue

            src_vis = next(
                (g for g in src_anim.groups
                 if g.group_type == ssbh_data_py.anim_data.GroupType.Visibility),
                None,
            )
            if src_vis is None:
                continue

            dst_vis = next(
                (g for g in dst_anim.groups
                 if g.group_type == ssbh_data_py.anim_data.GroupType.Visibility),
                None,
            )
            if dst_vis is None:
                dst_vis = ssbh_data_py.anim_data.GroupData(
                    ssbh_data_py.anim_data.GroupType.Visibility
                )
                dst_anim.groups.append(dst_vis)

            src_frames = int(src_anim.final_frame_index) + 1
            dst_frames = int(dst_anim.final_frame_index) + 1
            dst_node_map = {n.name: n for n in dst_vis.nodes}

            file_tracks = 0
            active_names = []
            for src_node in src_vis.nodes:
                if not src_node.tracks:
                    continue

                src_vals = list(src_node.tracks[0].values)
                if len(src_vals) < src_frames:
                    last = src_vals[-1] if src_vals else False
                    src_vals += [last] * (src_frames - len(src_vals))
                if len(src_vals) < dst_frames:
                    src_vals += [src_vals[-1]] * (dst_frames - len(src_vals))
                elif len(src_vals) > dst_frames:
                    src_vals = src_vals[:dst_frames]

                if any(src_vals):
                    active_names.append(src_node.name)

                if src_node.name in dst_node_map:
                    dst_node = dst_node_map[src_node.name]
                    if not dst_node.tracks:
                        dst_node.tracks.append(ssbh_data_py.anim_data.TrackData('Visibility'))
                    dst_node.tracks[0].values = src_vals
                else:
                    new_node = ssbh_data_py.anim_data.NodeData(src_node.name)
                    track = ssbh_data_py.anim_data.TrackData('Visibility')
                    track.values = src_vals
                    new_node.tracks.append(track)
                    dst_vis.nodes.append(new_node)
                    dst_node_map[src_node.name] = new_node

                file_tracks += 1

            if file_tracks == 0:
                continue

            self.report(
                {'INFO'},
                f'{src_file.name}: copied {file_tracks} tracks. '
                f'Active (any True): {active_names if active_names else "NONE — source is all-False"}',
            )

            try:
                dst_anim.save(str(dst_file))
                patched += 1
                total_tracks += file_tracks
            except Exception as e:
                self.report({'WARNING'}, f'Could not save {dst_file.name}: {e}')
                skipped += 1

        self.report(
            {'INFO'},
            f'Copied vis animation into {patched} files '
            f'({total_tracks} tracks total), {skipped} skipped.',
        )
        return {'FINISHED'}


class SUB_OP_export_vis_names(Operator):
    """Scan every .nuanmb in Source Folder (A) and write all unique vis track names
    to vis_pairing_list.txt in that folder.  Edit the file to add the matching new
    names, then use Port Vis Animation to copy the data across."""

    bl_idname = 'sub.export_vis_names'
    bl_label = 'Export Vis Names'
    bl_description = (
        'Scan all .nuanmb files in Source Folder (A) and write a vis_pairing_list.txt '
        'with every unique vis track name.  Fill in the new names on the right side of '
        'each line, then click Port Vis Animation.'
    )

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        return bool(context.object.data.sub_anim_properties.vis_port_source_folder.strip())

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        sap = context.object.data.sub_anim_properties
        source = Path(sap.vis_port_source_folder.strip())

        if not source.is_dir():
            self.report({'WARNING'}, f'Source folder not found: {source}')
            return {'CANCELLED'}

        nuanmb_files = sorted(source.glob('*.nuanmb'))
        if not nuanmb_files:
            self.report({'WARNING'}, f'No .nuanmb files in: {source}')
            return {'CANCELLED'}

        all_names = set()
        for filepath in nuanmb_files:
            try:
                ssbh_anim_data = ssbh_data_py.anim_data.read_anim(str(filepath))
            except Exception:
                continue
            for group in ssbh_anim_data.groups:
                if group.group_type == ssbh_data_py.anim_data.GroupType.Visibility:
                    for node in group.nodes:
                        all_names.add(node.name)
                    break

        if not all_names:
            self.report({'WARNING'}, 'No vis tracks found in any .nuanmb file.')
            return {'CANCELLED'}

        out_path = source / 'vis_pairing_list.txt'
        lines = [
            '# Vis Animation Pairing List',
            f'# Source folder: {source}',
            '#',
            '# Format:  OldName | NewName',
            '# Fill in NewName for every track you want to port.',
            '# Leave NewName blank (or omit the | entirely) to skip that track.',
            '# Lines starting with # are comments and are ignored.',
            '',
        ]
        for name in sorted(all_names):
            lines.append(f'{name} | ')

        out_path.write_text('\n'.join(lines), encoding='utf-8')
        sap.vis_port_pairing_file = str(out_path)

        self.report(
            {'INFO'},
            f'Exported {len(all_names)} vis track names to {out_path.name}. '
            f'Pairing File field updated automatically.',
        )
        return {'FINISHED'}


class SUB_OP_port_vis_animation(Operator):
    """Read the pairing list, then for each .nuanmb that exists in both Folder A and
    Folder B, copy the vis keyframe data from A into B using the name translation."""

    bl_idname = 'sub.port_vis_animation'
    bl_label = 'Port Vis Animation'
    bl_description = (
        'For each .nuanmb present in both Source Folder (A) and Target Folder (B), '
        'copy vis track animation data from A into B, renaming tracks according to '
        'the configured Pairing File.  Existing tracks in B are overwritten; '
        'missing ones are injected.'
    )

    @classmethod
    def poll(cls, context):
        if not context.object or context.object.type != 'ARMATURE':
            return False
        sap = context.object.data.sub_anim_properties
        return bool(
            sap.vis_port_source_folder.strip() and
            sap.vis_port_target_folder.strip() and
            sap.vis_port_pairing_file.strip()
        )

    def execute(self, context):
        from pathlib import Path
        from ...dependencies import ssbh_data_py

        sap = context.object.data.sub_anim_properties
        source  = Path(sap.vis_port_source_folder.strip())
        target  = Path(sap.vis_port_target_folder.strip())
        pair_fp = Path(sap.vis_port_pairing_file.strip())

        if not source.is_dir():
            self.report({'WARNING'}, f'Source folder not found: {source}')
            return {'CANCELLED'}
        if not target.is_dir():
            self.report({'WARNING'}, f'Target folder not found: {target}')
            return {'CANCELLED'}
        if not pair_fp.is_file():
            self.report({'WARNING'}, f'Pairing file not found: {pair_fp}')
            return {'CANCELLED'}

        # Parse pairing list → {old_name: new_name}
        pairing = {}
        for line in pair_fp.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '|' not in line:
                continue
            old, _, new = line.partition('|')
            old = old.strip()
            new = new.strip()
            if old and new:
                pairing[old] = new

        if not pairing:
            self.report(
                {'WARNING'},
                'No complete pairings found in the file. '
                'Make sure each line has the format: OldName | NewName',
            )
            return {'CANCELLED'}

        patched = 0
        skipped = 0
        total_tracks = 0

        for src_file in sorted(source.glob('*.nuanmb')):
            dst_file = target / src_file.name
            if not dst_file.exists():
                continue

            try:
                src_anim = ssbh_data_py.anim_data.read_anim(str(src_file))
                dst_anim = ssbh_data_py.anim_data.read_anim(str(dst_file))
            except Exception as e:
                self.report({'WARNING'}, f'Could not read {src_file.name}: {e}')
                skipped += 1
                continue

            src_vis = next(
                (g for g in src_anim.groups
                 if g.group_type == ssbh_data_py.anim_data.GroupType.Visibility),
                None,
            )
            if src_vis is None:
                continue

            dst_vis = next(
                (g for g in dst_anim.groups
                 if g.group_type == ssbh_data_py.anim_data.GroupType.Visibility),
                None,
            )
            if dst_vis is None:
                dst_vis = ssbh_data_py.anim_data.GroupData(
                    ssbh_data_py.anim_data.GroupType.Visibility
                )
                dst_anim.groups.append(dst_vis)

            src_frames = int(src_anim.final_frame_index) + 1
            dst_frames = int(dst_anim.final_frame_index) + 1

            dst_node_map = {n.name: n for n in dst_vis.nodes}

            file_tracks = 0
            for src_node in src_vis.nodes:
                new_name = pairing.get(src_node.name)
                if not new_name or not src_node.tracks:
                    continue

                # Expand constant / short tracks to full source frame count.
                src_vals = list(src_node.tracks[0].values)
                if len(src_vals) < src_frames:
                    last = src_vals[-1] if src_vals else False
                    src_vals += [last] * (src_frames - len(src_vals))

                # Adapt length to destination frame count.
                if len(src_vals) < dst_frames:
                    src_vals += [src_vals[-1]] * (dst_frames - len(src_vals))
                elif len(src_vals) > dst_frames:
                    src_vals = src_vals[:dst_frames]

                if new_name in dst_node_map:
                    dst_node = dst_node_map[new_name]
                    if not dst_node.tracks:
                        dst_node.tracks.append(ssbh_data_py.anim_data.TrackData('Visibility'))
                    dst_node.tracks[0].values = src_vals
                else:
                    new_node = ssbh_data_py.anim_data.NodeData(new_name)
                    track = ssbh_data_py.anim_data.TrackData('Visibility')
                    track.values = src_vals
                    new_node.tracks.append(track)
                    dst_vis.nodes.append(new_node)
                    dst_node_map[new_name] = new_node

                file_tracks += 1

            if file_tracks == 0:
                continue

            try:
                dst_anim.save(str(dst_file))
                patched += 1
                total_tracks += file_tracks
            except Exception as e:
                self.report({'WARNING'}, f'Could not save {dst_file.name}: {e}')
                skipped += 1

        self.report(
            {'INFO'},
            f'Ported vis animation into {patched} files '
            f'({total_tracks} track writes total), {skipped} skipped.',
        )
        return {'FINISHED'}


def remove_visibility_drivers(context):
    arma = context.object
    mesh_children = [child for child in arma.children if child.type == 'MESH']
    for m in mesh_children:
        if not m.animation_data:
            continue
        drivers = m.animation_data.drivers
        for d in drivers:
            if any(d.data_path == s for s in ['hide_viewport', 'hide_render']):
                drivers.remove(d)

def remove_anim_material_drivers(arma:bpy.types.Object):
    from ..model.material.sub_matl_data import SUB_PG_sub_matl_data
    from ..model.material.create_blender_materials_from_matl import setup_sub_matl_data_node_drivers
    mesh_children = [child for child in arma.children if child.type == 'MESH']
    materials = {material_slot.material for mesh in mesh_children for material_slot in mesh.material_slots}
    for material in materials:
        for node in material.node_tree.nodes:
            for output in node.outputs:
                if hasattr(output, 'default_value'):
                    output.driver_remove('default_value')
        
        sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
        if sub_matl_data is not None:
            setup_sub_matl_data_node_drivers(sub_matl_data)    

class SUB_OP_mat_drivers_refresh(Operator):
    bl_idname = 'sub.mat_drivers_refresh'
    bl_label = 'Refresh Material Drivers'   

    def execute(self, context):
        refresh_material_drivers(context)
        return {'FINISHED'}  

class SUB_OP_mat_drivers_remove(Operator):
    bl_idname = 'sub.mat_drivers_remove'
    bl_label = 'Remove Material Drivers'

    def execute(self, context):
        remove_anim_material_drivers(context.object)
        return {'FINISHED'}  

class SUB_MT_vis_entry_context_menu(Menu):
    bl_label = "Vis Entry Specials"

    def draw(self, context):
        layout = self.layout
        layout.operator('sub.vis_drivers_refresh', icon='FILE_REFRESH', text='Refresh Visibility Drivers')
        layout.operator('sub.vis_drivers_remove', icon='X', text='Remove Visibility Drivers')
        layout.separator()
        layout.operator('sub.auto_fill_vis_entries', icon='SHADERFX', text='Autofill Visibility Entries')
        layout.operator('sub.insert_all_vis_entry_keyframes', icon='KEY_HLT', text='Insert Keyframes for All Entries')
        layout.separator()
        layout.operator('sub.organize_vis_entries_alphabetically', icon='SORTALPHA', text='Organize Alphabetically')
        layout.operator('sub.organize_vis_entries_by_move', icon='SORTSIZE', text='Organize by Move')
        layout.separator()
        layout.operator('sub.set_all_vis_entries_false', icon='HIDE_ON', text='Set All Entries Off')
        layout.operator('sub.set_all_vis_entries_true', icon='HIDE_OFF', text='Set All Entries On')
        
class SUB_MT_mat_entry_context_menu(Menu):
    bl_label = "Mat Entry Specials"

    def draw(self, context):
        layout = self.layout
        layout.operator(SUB_OP_mat_drivers_refresh.bl_idname, icon='FILE_REFRESH', text='Refresh Material Drivers')
        layout.operator(SUB_OP_mat_drivers_remove.bl_idname, icon='X', text='Remove Material Drivers')

class SUB_UL_vis_track_entries(UIList):
    def draw_item(self, _context, layout, _data, item, icon, active_data, _active_propname, index):
        # assert(isinstance(item, bpy.types.ShapeKey))
        obj = active_data
        # key = data
        entry = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            split = layout.split(factor=0.66, align=False)
            split.prop(entry, "name", text="", emboss=False, icon='HIDE_OFF')
            row = split.row(align=True)
            row.emboss = 'NONE_OR_STATUS'
            row.label(text="")
            icon = 'CHECKBOX_HLT' if entry.value == True else 'CHECKBOX_DEHLT'
            row.prop(entry, "value", text="", icon=icon, emboss=False)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)

class SUB_UL_mat_tracks(UIList):
    def draw_item(self, _context, layout, _data, item, icon, active_data, _active_propname, index):
        obj = active_data
        entry = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row()
            row.prop(entry, "name", text="", emboss=False, icon='MATERIAL')
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)

class SUB_UL_mat_properties(UIList):
    def draw_item(self, _context, layout, _data, item, icon, active_data, _active_propname, index):
        obj = active_data
        entry = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row()
            row.prop(entry, "name", text="", emboss=False)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)

class SUB_UL_mat_property_values(UIList):
    def draw_item(self, _context, layout, _data, item, icon, active_data, _active_propname, index):
        obj = active_data
        entry = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row()
            if entry.sub_type == 'VECTOR':
                row.prop(entry, "custom_vector", text="", emboss=False)
            elif entry.sub_type == 'FLOAT':
                row.prop(entry, "custom_float", text="", emboss=False)
            elif entry.sub_type == 'BOOL':
                row.prop(entry, "custom_bool", text="", emboss=False)
            elif entry.sub_type == 'PATTERN':
                row.prop(entry, "pattern_index", text="", emboss=False)
            elif entry.sub_type == 'TEXTURE':
                row.prop(entry, "texture_transform", text="", emboss=False)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)        




def vis_track_name_update(self, context):
    sap = context.object.data.sub_anim_properties
    dupe = None
    for vt in sap.vis_track_entries:
        if vt.as_pointer() == self.as_pointer():
            continue
        if vt.name == self.name:
            dupe = vt
            break  
    if dupe is None:
        return
    regex = r"(\w+\.)(\d+)"
    matches = re.match(regex, self.name)
    if matches is None:
        self.name = self.name + '.001'
    else:
        base_name = matches.groups()[0]
        number = int(matches.groups()[1])
        self.name = f'{base_name}{number+1:003d}' 



def mat_track_prop_name_update(self, context):
    sap = context.object.data.sub_anim_properties
    found = False
    current_mat_track_index = None
    for mat_track_index, mat_track in enumerate(sap.mat_tracks):
        for property in mat_track.properties:
            if property.as_pointer() == self.as_pointer():
                current_mat_track_index = mat_track_index
                found = True
                break
        if found:
            break
    current_mat_track = sap.mat_tracks[current_mat_track_index]
    # There should be at most only one duplicate
    dupe = None
    for p in current_mat_track.properties:
        if p.as_pointer() == self.as_pointer():
            continue
        if p.name == self.name:
            dupe = p
            break
    # No duplicate found, name can remain as is
    if dupe is None:
        return
    # Regex match the name, see if it already has like '.001'
    # if it doesnt then add the '.001', otherwise increment the number
    regex = r"(\w+\.)(\d+)"
    matches = re.match(regex, self.name)
    if matches is None:
        self.name = self.name + '.001'
    else:
        base_name = matches.groups()[0]
        number = int(matches.groups()[1])
        self.name = f'{base_name}{number+1:003d}'

def mat_track_name_update(self, context):
    sap = context.object.data.sub_anim_properties
    dupe = None
    for mt in sap.mat_tracks:
        if mt.as_pointer() == self.as_pointer():
            continue
        if mt.name == self.name:
            dupe = mt
            break  
    if dupe is None:
        return
    regex = r"(\w+\.)(\d+)"
    matches = re.match(regex, self.name)
    if matches is None:
        self.name = self.name + '.001'
    else:
        base_name = matches.groups()[0]
        number = int(matches.groups()[1])
        self.name = f'{base_name}{number+1:003d}' 

def dummy_update(self, context):
    '''
    This is needed to force blender to update the driver values when updating via a modal.
    '''
    pass

class SUB_PG_vis_track_entry(PropertyGroup):
    name: StringProperty(
        name="Vis Name",
        default="Unknown",
        update=vis_track_name_update,)
    value: BoolProperty(name="Visible", default=False, update=dummy_update)

class SUB_PG_mat_track_property(PropertyGroup):
    name: StringProperty(
        name="Property Name",
        default="Unknown",
        update=mat_track_prop_name_update,)
    sub_type: EnumProperty(
        name='Mat Track Entry Subtype',
        description='CustomVector or CustomFloat or CustomBool',
        items=mat_sub_types, 
        default='VECTOR',)
    custom_vector: FloatVectorProperty(name='Custom Vector', size=4, update=dummy_update, subtype='COLOR_GAMMA', soft_min=0.0, soft_max=1.0)
    custom_bool: BoolProperty(name='Custom Bool')
    custom_float: FloatProperty(name='Custom Float')
    pattern_index: IntProperty(name='Pattern Index', subtype='UNSIGNED')
    texture_transform: FloatVectorProperty(name='Texture Transform', size=5)

class SUB_PG_mat_track(PropertyGroup):
    name: StringProperty(
        name="Material Name",
        default="Unknown",
        update=mat_track_name_update,)
    properties: CollectionProperty(type=SUB_PG_mat_track_property)
    active_property_index: IntProperty(name='Active Mat Property Index', default=0, options={'HIDDEN'})

class SUB_PG_sub_anim_data(PropertyGroup):
    vis_track_entries: CollectionProperty(type=SUB_PG_vis_track_entry)
    active_vis_track_index: IntProperty(name='Active Vis Track Index', default=0, options={'HIDDEN'})
    mat_tracks: CollectionProperty(type=SUB_PG_mat_track)
    active_mat_track_index: IntProperty(name='Active Mat Track Index', default=0, options={'HIDDEN'})

    # Face zone fix — three zones (eye / upper face / lower face), each with a default
    # entry name and comma-separated keywords used to recognise which vis tracks belong
    # to that zone. Matching uses _keyword_ underscore-bounded substrings.
    face_zones_expanded: BoolProperty(default=False, options={'HIDDEN'})
    face_zone_eye_default: StringProperty(
        name='Eye Default',
        default='',
        description='Vis entry to enable when no eye-zone track is active in a file',
    )
    face_zone_eye_keywords: StringProperty(
        name='Eye Keywords',
        default='Eye',
        description='Comma-separated substrings that identify eye-zone tracks (matched as _keyword_ or _keyword at end)',
    )
    face_zone_upper_default: StringProperty(
        name='Upper Default',
        default='',
        description='Vis entry to enable when no upper-face-zone track is active',
    )
    face_zone_upper_keywords: StringProperty(
        name='Upper Keywords',
        default='Openblink',
        description='Comma-separated substrings that identify upper-face-zone tracks',
    )
    face_zone_lower_default: StringProperty(
        name='Lower Default',
        default='',
        description='Vis entry to enable when no lower-face-zone track is active',
    )
    face_zone_lower_keywords: StringProperty(
        name='Lower Keywords',
        default='FaceN,Mouth',
        description='Comma-separated substrings that identify lower-face-zone tracks',
    )
    face_zone_pair_upper_to_eye: BoolProperty(
        name='Pair Upper Default → Eye Default',
        default=True,
        description=(
            'After all other passes, whenever the upper-face zone default entry is True '
            'at a frame, also force the eye zone default entry True. '
            'Use this when the eye mesh (e.g. Pit_Eye_VIS_O_OBJShape) must always be '
            'visible alongside the open-eye upper-face mesh (e.g. Pit_Openblink_VIS_O_OBJShape).'
        ),
    )

    # Vis animation porting — copy animation data from one character's folder to another
    # with name translation via a user-edited pairing list.
    vis_port_expanded: BoolProperty(default=False, options={'HIDDEN'})
    vis_port_source_folder: StringProperty(
        name='Source Folder (A)',
        subtype='DIR_PATH',
        default='',
        description='Folder A: the original character\'s animation folder to export vis names from / port vis data from',
    )
    vis_port_target_folder: StringProperty(
        name='Target Folder (B)',
        subtype='DIR_PATH',
        default='',
        description='Folder B: the new character\'s animation folder to port vis data into',
    )
    vis_port_pairing_file: StringProperty(
        name='Pairing File',
        subtype='FILE_PATH',
        default='',
        description='Path to the vis_pairing_list.txt file that maps old track names to new ones',
    )

    # Force-set a single vis track to a fixed value across all frames in a folder.
    force_vis_track_name: StringProperty(
        name='Track Name',
        default='',
        description='Vis track name to force-set in selected .nuanmb files',
    )
    force_vis_track_value: BoolProperty(
        name='Value',
        default=True,
        description='Value to write for every frame of the specified track (True = visible, False = hidden)',
    )

# Auto-start system functions
def init_sap_auto_sync():
    """Initialize the SAP auto-sync system - called from main addon register"""
    print("SAP Auto-Sync system activated")
    print("✅ SAP handlers and timer registered for automatic synchronization")
    
    # Subscribe to action changes for more direct detection
    subscribe_to_action_changes()

def cleanup_sap_auto_sync():
    """Cleanup the SAP auto-sync system - called from main addon unregister"""
    # Stop the SAP monitor if running
    if SUB_OP_sap_sync_monitor._is_running and SUB_OP_sap_sync_monitor._timer:
        try:
            wm = bpy.context.window_manager
            if wm:
                wm.event_timer_remove(SUB_OP_sap_sync_monitor._timer)
            SUB_OP_sap_sync_monitor._timer = None
            SUB_OP_sap_sync_monitor._is_running = False
            print("SAP Monitor stopped")
        except:
            pass
    
    # Unsubscribe from action changes
    unsubscribe_from_action_changes()

def register():
    """Register only handlers and timers - classes are registered separately"""
    # Register the SAP action sync handlers
    if sync_sap_action_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(sync_sap_action_handler)
    
    if sync_sap_action_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(sync_sap_action_depsgraph_handler)
    
    # Also register a timer for more frequent checking
    if not bpy.app.timers.is_registered(sync_sap_timer):
        bpy.app.timers.register(sync_sap_timer, first_interval=0.1, persistent=True)
    
    # Subscribe to action changes for direct detection
    subscribe_to_action_changes()
    

    """
    for name, obj in inspect.getmembers(
        sys.modules[__name__], 
        lambda member: inspect.isclass(member) and member.__module__ == __name__ and issubclass(member, bpy.types.bpy_struct)):
        print(f"{name}, {obj}")
        bpy.utils.register_class()
    """

def unregister():
    """Unregister only handlers and timers - classes are unregistered separately"""
    # Unregister all SAP action sync handlers
    if sync_sap_action_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(sync_sap_action_handler)
    
    if sync_sap_action_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(sync_sap_action_depsgraph_handler)
    
    # Unregister the timer
    if bpy.app.timers.is_registered(sync_sap_timer):
        bpy.app.timers.unregister(sync_sap_timer)
    
    # Clear the stored actions
    global _last_known_actions
    _last_known_actions.clear()
    
    # Unsubscribe from action changes
    unsubscribe_from_action_changes()
    
if __name__ == '__main__':
    register()