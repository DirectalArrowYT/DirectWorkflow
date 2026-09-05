import math
import bpy
import mathutils
import re
import collections
import time
import numpy as np
import cProfile
import pstats
import json
import os
from pathlib import Path

from ...dependencies import ssbh_data_py
from bpy_extras.io_utils import ImportHelper
from bpy.props import IntProperty, StringProperty, BoolProperty, FloatProperty, EnumProperty
from bpy.types import Operator, Panel
from mathutils import Matrix, Quaternion, Vector
from ..model.import_model import get_blender_transform
from ..blender_compat import assign_action, draw_progress, ensure_action_slot
from .fcurve_compat import find_fcurve, new_fcurve, style_material_fcurve, style_visibility_fcurve
from .raw_anim import (
    RAW_ANIM_EXTENSION,
    import_raw_animation,
    is_fighter_motion_body_path,
    refresh_raw_animation_import_list,
    schedule_raw_animation_list_refresh,
    get_raw_anim_import_directory,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .anim_data import SUB_PG_sub_anim_data, SUB_PG_mat_track, SUB_PG_mat_track_property
    from bpy.types import ShaderNodeGroup, Material
    from ..model.material.sub_matl_data import SUB_PG_sub_matl_data
    from ..blender_property_extensions import SubSceneProperties

ANIM_FOLDER_KEY = "sub_anim_import_folder"
_last_anim_sync_ptr = 0


def fill_animation_import_list(ssp, folder):
    ssp.animation_import_files.clear()
    if not folder or not os.path.isdir(folder):
        ssp.animation_import_folder_path = folder or ""
        return 0
    ssp.animation_import_folder_path = folder
    count = 0
    try:
        names = sorted(
            name for name in os.listdir(folder) if name.endswith(".nuanmb")
        )
    except OSError:
        return 0
    for anim_file in names:
        anim_item = ssp.animation_import_files.add()
        anim_item.name = os.path.splitext(anim_file)[0]
        anim_item.path = str(Path(folder) / anim_file)
        count += 1
    return count


def bind_anim_folder_to_armature(armature, folder):
    if armature is None or not folder:
        return
    try:
        armature[ANIM_FOLDER_KEY] = folder
        if armature.data is not None:
            armature.data[ANIM_FOLDER_KEY] = folder
    except Exception:
        pass


def anim_folder_for_armature(armature):
    if armature is None:
        return ""
    folder = armature.get(ANIM_FOLDER_KEY, "") or ""
    if folder:
        return folder
    data = getattr(armature, "data", None)
    if data is not None:
        folder = data.get(ANIM_FOLDER_KEY, "") or ""
        if folder:
            return folder
    smash = armature.get("sub_smash_model_folder", "") or ""
    if data is not None and not smash:
        smash = data.get("sub_smash_model_folder", "") or ""
    if smash:
        motion = smash.replace("model", "motion")
        if os.path.isdir(motion):
            nuanmb = [name for name in os.listdir(motion) if name.endswith(".nuanmb")]
            if nuanmb:
                return motion
            body = Path(motion) / "body" if os.path.basename(motion) != "body" else Path(motion)
            if not str(body).endswith("body"):
                fighter = Path(smash).parent.parent.parent
                body = fighter / "motion" / "body"
            if body.is_dir():
                subs = [name for name in os.listdir(body) if os.path.isdir(body / name)]
                if subs:
                    return str(body / subs[0])
    return ""


def sync_anim_importer_to_active(context=None):
    global _last_anim_sync_ptr
    context = context or bpy.context
    obj = getattr(context, "object", None)
    if obj is None or getattr(obj, "type", "") != "ARMATURE":
        return
    try:
        ptr = int(obj.as_pointer())
    except Exception:
        ptr = 0
    if ptr == _last_anim_sync_ptr:
        return
    folder = anim_folder_for_armature(obj)
    if not folder:
        _last_anim_sync_ptr = ptr
        return
    ssp = getattr(getattr(context, "scene", None), "sub_scene_properties", None)
    if ssp is None:
        return
    current = getattr(ssp, "animation_import_folder_path", "") or ""
    if os.path.normcase(os.path.normpath(current)) == os.path.normcase(os.path.normpath(folder)):
        _last_anim_sync_ptr = ptr
        return
    fill_animation_import_list(ssp, folder)
    try:
        from .raw_anim import refresh_raw_animation_import_list
        refresh_raw_animation_import_list(ssp)
    except Exception:
        pass
    _last_anim_sync_ptr = ptr


def import_animation_file(
    context: bpy.types.Context,
    operator: bpy.types.Operator,
    obj: bpy.types.Object,
    filepath: str,
    include_transform: bool,
    include_material: bool,
    include_visibility: bool,
    first_frame: int,
) -> bool:
    def refresh_smash_viewport():
        # Action assignment can update Blender at the current frame without a
        # frame-change event. Make the native Smash model consume that pose on
        # its very next draw instead of waiting for playback to start.
        try:
            from ..extras.smash_viewport import invalidate_animation_state
            invalidate_animation_state()
        except Exception:
            pass

    if filepath.lower().endswith(RAW_ANIM_EXTENSION):
        if obj.type != 'ARMATURE':
            operator.report({'ERROR'}, 'Raw animation import requires an armature.')
            return False
        old_mode = context.mode
        if old_mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE', toggle=False)
        success = import_raw_animation(context, obj, filepath, operator)
        if context.mode != old_mode:
            bpy.ops.object.mode_set(mode=old_mode, toggle=False)
        if success:
            refresh_smash_viewport()
        return success

    if obj.type == 'ARMATURE':
        old_mode = context.mode
        if old_mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE', toggle=False)
        import_model_anim(
            context,
            filepath,
            include_transform,
            include_material,
            include_visibility,
            first_frame,
            armature_object=obj,
        )
        if context.mode != old_mode:
            bpy.ops.object.mode_set(mode=old_mode, toggle=False)
    else:
        import_camera_anim(operator, context, filepath, first_frame)
    refresh_smash_viewport()
    return True


class SUB_UL_animation_import_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.label(text=item.name)
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text=item.name)


class SUB_UL_raw_animation_import_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.label(text=item.name)
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text=item.name)

class SUB_OP_import_all_animations(bpy.types.Operator):
    bl_idname = 'sub.import_all_animations'
    bl_label = 'Import All Animations'
    bl_options = {'REGISTER', 'UNDO'}

    # Choice of range to import
    import_mode: EnumProperty(
        name="Import Range",
        description="Choose whether to import all animations or start from the selected one",
        items=(
            ('ALL', "All Animations", "Import every animation in the list"),
            ('FROM_SELECTED', "From Selected Onward", "Start importing at the selected animation and continue to the end"),
        ),
        default='ALL'
    )

    @classmethod
    def poll(cls, context):
        obj: bpy.types.Object = context.object
        if obj is None:
            return False
        elif obj.type != 'ARMATURE' and obj.type != 'CAMERA':
            return False
        
        ssp = context.scene.sub_scene_properties
        return len(ssp.animation_import_files) > 0
    
    # Progress tracking properties
    progress: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0)
    progress_text: bpy.props.StringProperty(default="")
    is_importing: bpy.props.BoolProperty(default=False)
    current_animation_index: bpy.props.IntProperty(default=0)
    imported_count: bpy.props.IntProperty(default=0)
    
    def invoke(self, context, event):
        ssp = context.scene.sub_scene_properties
        anim_count = len(ssp.animation_import_files)
        self.progress = 0.0
        self.progress_text = f"Ready to import {anim_count} animations"
        self.is_importing = False
        self.current_animation_index = 0
        self.imported_count = 0
        return context.window_manager.invoke_props_dialog(self, width=450)
    
    def draw(self, context):
        layout = self.layout
        ssp = context.scene.sub_scene_properties
        anim_count = len(ssp.animation_import_files)
        
        if not self.is_importing:
            # Confirmation phase
            layout.label(text=f"Choose what to import ({anim_count} found):")
            layout.prop(self, "import_mode", expand=True)
            if self.import_mode == 'FROM_SELECTED' and 0 <= ssp.animation_import_files_index < anim_count:
                sel_name = ssp.animation_import_files[ssp.animation_import_files_index].name
                layout.label(text=f"Starting from: {sel_name}")
        else:
            # Progress phase
            layout.label(text=self.progress_text)
            draw_progress(layout, self.progress)
            layout.label(text=f"Imported: {self.imported_count}/{anim_count}")
            
    def modal(self, context, event):
        if event.type == 'TIMER':
            # Check if we're still importing to prevent multiple calls
            if self.is_importing:
                self.import_next_animation(context)
                return {'RUNNING_MODAL'}
            else:
                # Import is finished, clean up and exit
                return {'FINISHED'}
        elif event.type == 'ESC':
            # Cancel the import process
            self.cancel_import(context)
            return {'FINISHED'}
        return {'PASS_THROUGH'}
            
    def execute(self, context):
        if not self.is_importing:
            # Start the import process
            self.is_importing = True
            self.imported_count = 0
            
            # Setup for modal operation
            ssp = context.scene.sub_scene_properties
            
            # Use scene properties instead of operator properties
            self.include_transform = ssp.anim_include_transform
            self.include_material = ssp.anim_include_material  
            self.include_visibility = ssp.anim_include_visibility
            self.first_frame = 1
            # Determine starting index
            total_animations = len(ssp.animation_import_files)
            if self.import_mode == 'FROM_SELECTED' and total_animations > 0:
                start_index = max(0, min(ssp.animation_import_files_index, total_animations - 1))
            else:
                start_index = 0
            self.current_animation_index = start_index
            
            # Save current auto-keyframe setting and disable it
            self.use_keyframe_insert_auto = context.scene.tool_settings.use_keyframe_insert_auto
            context.scene.tool_settings.use_keyframe_insert_auto = False
            
            # Set to pose mode if needed
            self.old_mode = context.mode
            obj = context.object
            if obj.type == 'ARMATURE' and self.old_mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE', toggle=False)
            
            # Start timer for processing animations
            self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        else:
            return {'FINISHED'}
    
    def import_next_animation(self, context):
        ssp = context.scene.sub_scene_properties
        total_animations = len(ssp.animation_import_files)
        
        if self.current_animation_index >= total_animations:
            # Import complete - only finish if we're still importing
            if self.is_importing:
                self.finish_import(context)
            return
        
        anim_item = ssp.animation_import_files[self.current_animation_index]
        self.progress_text = f"Importing: {anim_item.name}"
        self.progress = self.current_animation_index / total_animations
        
        # Force UI update
        for area in context.screen.areas:
            area.tag_redraw()
        
        if not Path(anim_item.path).exists():
            self.report({"WARNING"}, f"Animation file not found: {anim_item.path}")
        else:
            try:
                obj = context.object
                import_animation_file(
                    context,
                    self,
                    obj,
                    anim_item.path,
                    self.include_transform,
                    self.include_material,
                    self.include_visibility,
                    self.first_frame,
                )
                
                self.imported_count += 1
                
            except Exception as e:
                self.report({"ERROR"}, f"Failed to import animation '{anim_item.name}': {str(e)}")
        
        self.current_animation_index += 1
    
    def cancel_import(self, context):
        # Clean up
        if hasattr(self, '_timer'):
            context.window_manager.event_timer_remove(self._timer)
            delattr(self, '_timer')
        
        # Restore original mode
        obj = context.object
        if obj.type == 'ARMATURE' and self.old_mode != 'POSE':
            bpy.ops.object.mode_set(mode=self.old_mode, toggle=False)
        
        # Restore auto-keyframe setting
        context.scene.tool_settings.use_keyframe_insert_auto = self.use_keyframe_insert_auto
        
        # Mark as finished to prevent multiple reports
        self.is_importing = False
        
        # Report cancellation
        ssp = context.scene.sub_scene_properties
        total_animations = len(ssp.animation_import_files)
        self.report({"WARNING"}, f"Bulk import cancelled. Imported {self.imported_count}/{total_animations} animations")
        
        # Force UI update
        for area in context.screen.areas:
            area.tag_redraw()
    
    def finish_import(self, context):
        # Clean up
        if hasattr(self, '_timer'):
            context.window_manager.event_timer_remove(self._timer)
            delattr(self, '_timer')
        
        # Restore original mode
        obj = context.object
        if obj.type == 'ARMATURE' and self.old_mode != 'POSE':
            bpy.ops.object.mode_set(mode=self.old_mode, toggle=False)
        
        # Restore auto-keyframe setting
        context.scene.tool_settings.use_keyframe_insert_auto = self.use_keyframe_insert_auto
        
        # Final progress update
        ssp = context.scene.sub_scene_properties
        total_animations = len(ssp.animation_import_files)
        self.progress = 1.0
        self.progress_text = f"Complete! Imported {self.imported_count}/{total_animations} animations"
        
        # Force final UI update
        for area in context.screen.areas:
            area.tag_redraw()
        
        # Mark as finished to prevent multiple reports
        self.is_importing = False
        
        self.report({"INFO"}, f"Successfully imported {self.imported_count}/{total_animations} animations")
        return {'FINISHED'}

class SUB_OP_import_selected_anim(bpy.types.Operator):
    bl_idname = 'sub.import_selected_anim'
    bl_label = 'Import Selected Animation'
    bl_options = {'UNDO'}

    include_transform_track: BoolProperty(
        name='Include Transform',
        description='Include Transform Track',
        default=True,
    )
    include_material_track: BoolProperty(
        name='Include Material',
        description='Include Material Track',
        default=True,
    )
    include_visibility_track: BoolProperty(
        name='Include Visibility',
        description='Include Visibility Track',
        default=True,
    )
    first_blender_frame: IntProperty(
        name='Start Frame',
        description='What frame to start importing the track on',
        default=1,
    )
    use_debug_timer: BoolProperty(
        name='Debug timing stats',
        description='Print advance import timing info to the console',
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj: bpy.types.Object = context.object
        if obj is None:
            return False
        elif obj.type != 'ARMATURE' and obj.type != 'CAMERA':
            return False
        
        ssp = context.scene.sub_scene_properties
        return len(ssp.animation_import_files) > 0 and ssp.animation_import_files_index < len(ssp.animation_import_files)
    
    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        selected_anim = ssp.animation_import_files[ssp.animation_import_files_index]
        
        if not Path(selected_anim.path).exists():
            self.report({"ERROR"}, f"Animation file not found: {selected_anim.path}")
            return {'CANCELLED'}
            
        ssp.last_anim_import_dir = str(Path(selected_anim.path).parent)
        obj: bpy.types.Object = context.object
        
        # Use scene properties instead of operator properties
        include_transform = ssp.anim_include_transform
        include_material = ssp.anim_include_material  
        include_visibility = ssp.anim_include_visibility
        first_frame = 1
        
        use_keyframe_insert_auto = bpy.context.scene.tool_settings.use_keyframe_insert_auto
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        import_animation_file(
            context,
            self,
            obj,
            selected_anim.path,
            include_transform,
            include_material,
            include_visibility,
            first_frame,
        )
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = use_keyframe_insert_auto
        
        return {'FINISHED'}


class SUB_OP_browse_raw_animation_folder(Operator):
    bl_idname = 'sub.browse_raw_animation_folder'
    bl_label = 'Browse Raw Animation Folder'
    bl_options = {'UNDO'}

    directory: StringProperty(subtype="DIR_PATH")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and obj.select_get()

    def invoke(self, context, _event):
        ssp = context.scene.sub_scene_properties
        if ssp.raw_animation_import_folder_path:
            self.directory = ssp.raw_animation_import_folder_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        folder_path = refresh_raw_animation_import_list(ssp, self.directory)
        if folder_path and os.path.isdir(folder_path):
            count = len(ssp.raw_animation_import_files)
            self.report({'INFO'}, f'Found {count} raw animation(s) in: {folder_path}')
        elif folder_path:
            self.report({'INFO'}, f'Raw animation folder not found yet: {folder_path}')
        else:
            self.report({'INFO'}, 'No raw animation folder selected.')
        return {'FINISHED'}


class SUB_OP_import_raw_anim_file(Operator, ImportHelper):
    bl_idname = 'sub.import_raw_anim_file'
    bl_label = 'Import Raw Animation File'
    bl_description = 'Import a single .rawanim onto the selected armature'
    bl_options = {'UNDO'}

    filter_glob: StringProperty(default='*.rawanim', options={'HIDDEN'})
    filename_ext = '.rawanim'

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and obj.select_get()

    def invoke(self, context, event):
        ssp = context.scene.sub_scene_properties
        folder = get_raw_anim_import_directory(ssp)
        if folder and os.path.isdir(folder):
            self.filepath = os.path.join(folder, '')
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.active_object
        filepath = self.filepath
        if not filepath or not os.path.isfile(filepath):
            self.report({'ERROR'}, 'No raw animation file selected.')
            return {'CANCELLED'}
        if not filepath.lower().endswith(RAW_ANIM_EXTENSION):
            self.report({'ERROR'}, 'Selected file is not a .rawanim.')
            return {'CANCELLED'}
        use_keyframe_insert_auto = context.scene.tool_settings.use_keyframe_insert_auto
        context.scene.tool_settings.use_keyframe_insert_auto = False
        try:
            ok = import_animation_file(
                context, self, obj, filepath, True, False, False, 1
            )
        finally:
            context.scene.tool_settings.use_keyframe_insert_auto = use_keyframe_insert_auto
        if not ok:
            return {'CANCELLED'}
        folder = os.path.dirname(filepath)
        if folder:
            refresh_raw_animation_import_list(context.scene.sub_scene_properties, folder)
        self.report({'INFO'}, f"Imported raw animation: {os.path.basename(filepath)}")
        return {'FINISHED'}


class SUB_OP_refresh_raw_animation_list(Operator):
    bl_idname = 'sub.refresh_raw_animation_list'
    bl_label = 'Refresh Raw Animation List'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and obj.select_get()

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        folder_path = refresh_raw_animation_import_list(ssp)
        count = len(ssp.raw_animation_import_files)
        if folder_path:
            self.report({'INFO'}, f'Found {count} raw animation(s) in: {folder_path}')
        else:
            self.report({'INFO'}, 'No raw animation folder detected.')
        return {'FINISHED'}


class SUB_OP_import_selected_raw_anim(Operator):
    bl_idname = 'sub.import_selected_raw_anim'
    bl_label = 'Import Selected Raw Animation'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'ARMATURE':
            return False
        ssp = context.scene.sub_scene_properties
        return (
            len(ssp.raw_animation_import_files) > 0
            and ssp.raw_animation_import_files_index < len(ssp.raw_animation_import_files)
        )

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        selected_anim = ssp.raw_animation_import_files[ssp.raw_animation_import_files_index]
        if not Path(selected_anim.path).exists():
            self.report({'ERROR'}, f"Raw animation file not found: {selected_anim.path}")
            return {'CANCELLED'}

        obj = context.active_object
        use_keyframe_insert_auto = context.scene.tool_settings.use_keyframe_insert_auto
        context.scene.tool_settings.use_keyframe_insert_auto = False
        import_animation_file(context, self, obj, selected_anim.path, True, False, False, 1)
        context.scene.tool_settings.use_keyframe_insert_auto = use_keyframe_insert_auto
        self.report({'INFO'}, f"Imported raw animation: {selected_anim.name}")
        return {'FINISHED'}


class SUB_OP_import_all_raw_anims(Operator):
    bl_idname = 'sub.import_all_raw_anims'
    bl_label = 'Import All Raw Animations'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'ARMATURE':
            return False
        ssp = context.scene.sub_scene_properties
        return len(ssp.raw_animation_import_files) > 0

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        obj = context.active_object
        use_keyframe_insert_auto = context.scene.tool_settings.use_keyframe_insert_auto
        context.scene.tool_settings.use_keyframe_insert_auto = False

        imported_count = 0
        for anim_item in ssp.raw_animation_import_files:
            if not Path(anim_item.path).exists():
                self.report({'WARNING'}, f"Raw animation file not found: {anim_item.path}")
                continue
            try:
                import_animation_file(context, self, obj, anim_item.path, True, False, False, 1)
                imported_count += 1
            except Exception as exc:
                self.report({'ERROR'}, f"Failed to import raw animation '{anim_item.name}': {exc}")

        context.scene.tool_settings.use_keyframe_insert_auto = use_keyframe_insert_auto
        self.report({'INFO'}, f"Imported {imported_count}/{len(ssp.raw_animation_import_files)} raw animations")
        return {'FINISHED'}


class SUB_PT_import_anim(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Animation Importer'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if context.mode == "POSE" or context.mode == "OBJECT":
            return True
        return False
    
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        obj: bpy.types.Object = context.active_object
        ssp = context.scene.sub_scene_properties
        
        # Show browse button
        row = layout.row()
        if obj is None:
            row.label(text="Click on an Armature or Camera.")
        elif obj.select_get() is False:
            row.label(text="Click on an Armature or Camera.")
        elif obj.type == 'ARMATURE' or obj.type == 'CAMERA':
            row.operator(SUB_OP_import_anim.bl_idname, icon='IMPORT', text='Browse .NUANMB')
        else:
            row.label(text=f'The selected {obj.type.lower()} is not an armature or a camera.')
            
        # Show animations from imported model
        if obj and obj.select_get() and (obj.type == 'ARMATURE' or obj.type == 'CAMERA'):
            # Add button to browse for an animation folder
            row = layout.row()
            row.operator(SUB_OP_select_animation_folder.bl_idname, icon='ZOOM_ALL', text='Browse Animation Folder')
            
            if ssp.animation_import_folder_path and len(ssp.animation_import_files) > 0:
                # Collapsible Related Animations section
                box = layout.box()
                header_row = box.row()
                header_row.prop(ssp, "related_animations_expanded", 
                               icon="TRIA_DOWN" if ssp.related_animations_expanded else "TRIA_RIGHT",
                               icon_only=True, emboss=False)
                header_row.label(text="Related Animations:")
                
                # Only show content if expanded
                if ssp.related_animations_expanded:
                    # Collapsible Import Options section
                    header_row = box.row()
                    header_row.prop(ssp, "import_options_expanded", 
                                   icon="TRIA_DOWN" if ssp.import_options_expanded else "TRIA_RIGHT",
                                   icon_only=True, emboss=False)
                    header_row.label(text="Import Options:")
                    
                    # Only show import options if expanded
                    if ssp.import_options_expanded:
                        row = box.row()
                        col = row.column()
                        col.prop(ssp, "anim_include_transform", text="Include Transform")
                        col.prop(ssp, "anim_include_material", text="Include Material")  
                        col.prop(ssp, "anim_include_visibility", text="Include Visibility")
                    
                    if ssp.animation_import_folder_path:
                        row = box.row()
                        row.label(text=f"Folder: {ssp.animation_import_folder_path}")
                    
                    row = box.row()
                    row.template_list("SUB_UL_animation_import_list", "", ssp, "animation_import_files", ssp, "animation_import_files_index")
                    
                    row = box.row()
                    row.operator(SUB_OP_import_selected_anim.bl_idname, text="Import Selected Animation")
                    
                    # Add batch import button
                    row = box.row()
                    row.operator(SUB_OP_import_all_animations.bl_idname, text="Import All Animations")


class SUB_PT_raw_animations(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Raw Animations'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in {"POSE", "OBJECT"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        ssp = context.scene.sub_scene_properties
        obj = context.active_object

        help_box = layout.box()
        help_box.label(text="What are Raw Animations?", icon='INFO')
        col = help_box.column(align=True)
        col.scale_y = 0.9
        col.label(text="Sparse .rawanim files for animator round-trips.")
        col.label(text="They keep pose/IK keys without baking every frame,")
        col.label(text="unlike game .nuanmb exports.")

        if obj is None or not obj.select_get() or obj.type != 'ARMATURE':
            layout.label(text="Select an armature to import or export raw anims.")
            return

        if (
            not ssp.raw_animation_import_folder_path
            and is_fighter_motion_body_path(ssp.animation_import_folder_path)
        ):
            schedule_raw_animation_list_refresh(context)

        import_box = layout.box()
        import_box.label(text="Import", icon='IMPORT')

        row = import_box.row()
        row.operator(
            SUB_OP_browse_raw_animation_folder.bl_idname,
            icon='ZOOM_ALL',
            text='Browse Raw Animation Folder',
        )
        row.operator(SUB_OP_refresh_raw_animation_list.bl_idname, icon='FILE_REFRESH', text='')

        row = import_box.row()
        row.operator(
            SUB_OP_import_raw_anim_file.bl_idname,
            icon='IMPORT',
            text='Import Raw Animation File',
        )

        display_raw_folder = get_raw_anim_import_directory(ssp)
        if display_raw_folder:
            row = import_box.row()
            row.label(text=f"Folder: {display_raw_folder}")

        if len(ssp.raw_animation_import_files) > 0:
            row = import_box.row()
            row.template_list(
                "SUB_UL_raw_animation_import_list",
                "",
                ssp,
                "raw_animation_import_files",
                ssp,
                "raw_animation_import_files_index",
                rows=3,
            )
            row = import_box.row()
            row.operator(
                SUB_OP_import_selected_raw_anim.bl_idname,
                text="Import Selected Raw Animation",
            )
            row = import_box.row()
            row.operator(
                SUB_OP_import_all_raw_anims.bl_idname,
                text="Import All Raw Animations",
            )
        elif display_raw_folder:
            row = import_box.row()
            row.label(text="No .rawanim files found in this folder.", icon='INFO')

        export_box = layout.box()
        export_box.label(text="Export", icon='EXPORT')
        export_box.prop(ssp, "anim_include_raw_animation", text="Include Raw with .NUANMB Export")
        row = export_box.row()
        row.scale_y = 1.2
        row.operator('sub.raw_anim_export', icon='EXPORT', text='Export Raw Animation')


class SUB_OP_import_anim(Operator):
    bl_idname = 'sub.import_anim'
    bl_label = 'Import Anim'
    bl_options = {'UNDO'}

    filter_glob: StringProperty(
        default='*.nuanmb;*.rawanim',
        options={'HIDDEN'}
    )
    include_transform_track: BoolProperty(
        name='Include Transform',
        description='Include Transform Track',
        default=True,
    )
    include_material_track: BoolProperty(
        name='Include Material',
        description='Include Material Track',
        default=True,
    )
    include_visibility_track: BoolProperty(
        name='Include Visibility',
        description='Include Visibility Track',
        default=True,
    )
    first_blender_frame: IntProperty(
        name='Start Frame',
        description='What frame to start importing the track on',
        default=1,
    )
    use_debug_timer: BoolProperty(
        name='Debug timing stats',
        description='Print advance import timing info to the console',
        default=False,
    )

    filepath: StringProperty(subtype="FILE_PATH")

    @classmethod
    def poll(cls, context):
        obj: bpy.types.Object = context.object
        if obj is None:
            return False
        elif obj.type != 'ARMATURE' and obj.type != 'CAMERA':
            return False
        return True
    
    def invoke(self, context, event):
        self.first_blender_frame = context.scene.frame_start
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.filepath == '' or Path(self.filepath).is_dir():
            self.report({"ERROR"}, f"No file selected!")
            return {'CANCELLED'}
        ssp = context.scene.sub_scene_properties
        ssp.last_anim_import_dir = str(Path(self.filepath).parent)
        obj: bpy.types.Object = context.object
        
        include_transform = ssp.anim_include_transform
        include_material = ssp.anim_include_material  
        include_visibility = ssp.anim_include_visibility
        first_frame = 1
        
        use_keyframe_insert_auto = bpy.context.scene.tool_settings.use_keyframe_insert_auto
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        import_animation_file(
            context,
            self,
            obj,
            self.filepath,
            include_transform,
            include_material,
            include_visibility,
            first_frame,
        )
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = use_keyframe_insert_auto

        return {'FINISHED'}
  
def poll_cameras(self, obj):
    return obj.type == 'CAMERA'

def hierarchy_order(bone, reordered):
        if bone not in reordered:
            reordered.append(bone)
        for child in bone.children:
            hierarchy_order(child, reordered)

def get_hierarchy_order(bone_list: list[bpy.types.PoseBone]) -> list[bpy.types.PoseBone]:
    root_bones: list[bpy.types.PoseBone] = []
    for bone in bone_list:
        if bone.parent is None:
            root_bones.append(bone)
    return root_bones + [c for root_bone in root_bones for c in root_bone.children_recursive if c in bone_list]

class BoneTranslationFCurves():
    def __init__(self, action, bone_name, values_length):
        self.data_path = f'pose.bones["{bone_name}"].location'
        self.x: bpy.types.FCurve = create_fcurve(action, 'OBJECT', self.data_path, 0, f'{bone_name}')
        self.y: bpy.types.FCurve = create_fcurve(action, 'OBJECT', self.data_path, 1, f'{bone_name}')
        self.z: bpy.types.FCurve = create_fcurve(action, 'OBJECT', self.data_path, 2, f'{bone_name}')
        self.x_stashed_values = [[0.0, 0.0]] * values_length
        self.y_stashed_values = [[0.0, 0.0]] * values_length
        self.z_stashed_values = [[0.0, 0.0]] * values_length
    def get_translation_matrix(self, index: int):
        if index < len(self.x.keyframe_points):
            x = self.x_stashed_values[index][1]
            y = self.y_stashed_values[index][1]
            z = self.z_stashed_values[index][1]
        else:
            x = self.x_stashed_values[0][1]
            y = self.y_stashed_values[0][1]
            z = self.z_stashed_values[0][1]
        return Matrix.Translation([x,y,z])
    def stash_keyframe_set_from_vector(self, index, frame, translation_vector: Vector):
        x, y, z = translation_vector
        self.x_stashed_values[index] = [frame, x]
        self.y_stashed_values[index] = [frame, y]
        self.z_stashed_values[index] = [frame, z]
    def set_keyframe_values_from_stash(self):
        self.x.keyframe_points.add(count=len(self.x_stashed_values))
        self.y.keyframe_points.add(count=len(self.y_stashed_values))
        self.z.keyframe_points.add(count=len(self.z_stashed_values))
        self.x.keyframe_points.foreach_set('co', [x for tup in self.x_stashed_values for x in tup])
        self.y.keyframe_points.foreach_set('co', [x for tup in self.y_stashed_values for x in tup])
        self.z.keyframe_points.foreach_set('co', [x for tup in self.z_stashed_values for x in tup])

class BoneRotationFCurves():
    def __init__(self, action, base_data_path, bone_name, values_length):
        self.w: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.rotation_quaternion', 0, f'{bone_name}')
        self.x: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.rotation_quaternion', 1, f'{bone_name}')
        self.y: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.rotation_quaternion', 2, f'{bone_name}')
        self.z: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.rotation_quaternion', 3, f'{bone_name}')
        self.w_stashed_values = [[0.0, 0.0]] * values_length
        self.x_stashed_values = [[0.0, 0.0]] * values_length
        self.y_stashed_values = [[0.0, 0.0]] * values_length
        self.z_stashed_values = [[0.0, 0.0]] * values_length
    def get_rotation_matrix(self, index: int):
        if index < len(self.w.keyframe_points):
            w = self.w_stashed_values[index][1]
            x = self.x_stashed_values[index][1]
            y = self.y_stashed_values[index][1]
            z = self.z_stashed_values[index][1]
        else:
            w = self.w_stashed_values[0][1]
            x = self.x_stashed_values[0][1]
            y = self.y_stashed_values[0][1]
            z = self.z_stashed_values[0][1]           
        q = Quaternion([w,x,y,z])
        return Matrix.Rotation(q.angle, 4, q.axis)
    def stash_keyframe_values_from_quaternion(self, index, frame, quaternion: Quaternion):
        w,x,y,z = quaternion
        self.w_stashed_values[index] = [frame, w]
        self.x_stashed_values[index] = [frame, x]
        self.y_stashed_values[index] = [frame, y]
        self.z_stashed_values[index] = [frame, z]
    def set_keyframe_values_from_stash(self):
        self.w.keyframe_points.add(count=len(self.w_stashed_values))
        self.x.keyframe_points.add(count=len(self.x_stashed_values))
        self.y.keyframe_points.add(count=len(self.y_stashed_values))
        self.z.keyframe_points.add(count=len(self.z_stashed_values))
        self.w.keyframe_points.foreach_set('co', [x for tup in self.w_stashed_values for x in tup])
        self.x.keyframe_points.foreach_set('co', [x for tup in self.x_stashed_values for x in tup])
        self.y.keyframe_points.foreach_set('co', [x for tup in self.y_stashed_values for x in tup])
        self.z.keyframe_points.foreach_set('co', [x for tup in self.z_stashed_values for x in tup])

class BoneScaleFCurves():
    def __init__(self, action, base_data_path, bone_name, values_length):
        self.x: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.scale', 0, f'{bone_name}')
        self.y: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.scale', 1, f'{bone_name}')
        self.z: bpy.types.FCurve = create_fcurve(action, 'OBJECT', f'{base_data_path}.scale', 2, f'{bone_name}')
        self.x_stashed_values = [[0.0, 0.0]] * values_length
        self.y_stashed_values = [[0.0, 0.0]] * values_length
        self.z_stashed_values = [[0.0, 0.0]] * values_length
    def get_scale_matrix(self, index: int):
        if index < len(self.x.keyframe_points):
            x = self.x_stashed_values[index][1]
            y = self.y_stashed_values[index][1]
            z = self.z_stashed_values[index][1]
        else:
            x = self.x_stashed_values[0][1]
            y = self.y_stashed_values[0][1]
            z = self.z_stashed_values[0][1]
        return Matrix.Diagonal([x,y,z,1.0])
    def stash_keyframe_set_from_vector(self, index, frame, scale_vector: Vector):
        x, y, z = scale_vector
        self.x_stashed_values[index] = [frame, x]
        self.y_stashed_values[index] = [frame, y]
        self.z_stashed_values[index] = [frame, z]
    def set_keyframe_values_from_stash(self):
        self.x.keyframe_points.add(count=len(self.x_stashed_values))
        self.y.keyframe_points.add(count=len(self.y_stashed_values))
        self.z.keyframe_points.add(count=len(self.z_stashed_values))
        self.x.keyframe_points.foreach_set('co', [x for tup in self.x_stashed_values for x in tup])
        self.y.keyframe_points.foreach_set('co', [x for tup in self.y_stashed_values for x in tup])
        self.z.keyframe_points.foreach_set('co', [x for tup in self.z_stashed_values for x in tup])

class BoneFCurves():
    def __init__(self, bone_name, action, values_length):
        self.bone_name: str = bone_name
        self.base_data_path: str = f'pose.bones["{bone_name}"]'
        self.translation = BoneTranslationFCurves(action, bone_name, values_length)
        self.rotation = BoneRotationFCurves(action, self.base_data_path, bone_name, values_length)
        self.scale = BoneScaleFCurves(action, self.base_data_path, bone_name, values_length)
    def get_matrix_basis(self, index):
        tm = self.translation.get_translation_matrix(index)
        rm = self.rotation.get_rotation_matrix(index)
        sm = self.scale.get_scale_matrix(index)
        return Matrix(tm @ rm @ sm)
    def stash_keyframe_set_from_matrix(self, index, frame, matrix: Matrix):
        t, r, s = matrix.decompose()
        self.translation.stash_keyframe_set_from_vector(index, frame, t)
        self.rotation.stash_keyframe_values_from_quaternion(index, frame, r)
        self.scale.stash_keyframe_set_from_vector(index, frame, s)
    def set_keyframe_values_from_stash(self):
        self.translation.set_keyframe_values_from_stash()
        self.rotation.set_keyframe_values_from_stash()
        self.scale.set_keyframe_values_from_stash()


def reset_bones_to_rest_pose(armature):
    """Reset all bones in the armature to their rest pose."""
    if armature.type != 'ARMATURE':
        return
    
    # Store current mode
    old_mode = bpy.context.mode
    
    # Switch to pose mode if needed
    if old_mode != 'POSE':
        bpy.ops.object.mode_set(mode='POSE', toggle=False)
    
    # Reset all bones to rest pose
    for bone in armature.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    
    # Restore original mode
    if old_mode != 'POSE':
        bpy.ops.object.mode_set(mode=old_mode, toggle=False)


def remove_visibility_drivers(context):
    remove_visibility_drivers_for_armature(context.object)


def remove_visibility_drivers_for_armature(armature_object):
    if armature_object is None:
        return
    mesh_children = [child for child in armature_object.children if child.type == 'MESH']
    for mesh in mesh_children:
        if not mesh.animation_data:
            continue
        drivers = mesh.animation_data.drivers
        for driver in list(drivers):
            if driver.data_path in {'hide_viewport', 'hide_render'}:
                drivers.remove(driver)


def import_model_anim(context: bpy.types.Context, filepath: str,
                      include_transform_track, include_material_track,
                      include_visibility_track, first_blender_frame,
                      armature_object: bpy.types.Object | None = None):
    # Load the anim data first with ssbh_data_py since blender setup relies on data from it
    ssbh_anim_data = ssbh_data_py.anim_data.read_anim(filepath)
    # Blender Action setup
    arma: bpy.types.Object = armature_object or context.object
    if arma is None or arma.type != 'ARMATURE':
        raise ValueError("import_model_anim requires an armature object")
    if context.view_layer.objects.active != arma:
        for scene_obj in context.view_layer.objects:
            scene_obj.select_set(scene_obj == arma)
        context.view_layer.objects.active = arma
    if arma.animation_data is None: # For the bones
        arma.animation_data_create()
    if arma.data.animation_data is None: # For vis and mat tracks
        arma.data.animation_data_create()

    bone_action = bpy.data.actions.new(Path(filepath).name)
    sap_action = bpy.data.actions.new(f"{arma.name} {bone_action.name} SAP Data")
    try:
        bone_action["sub_anim_source_path"] = str(Path(filepath).resolve())
        sap_action["sub_anim_source_path"] = str(Path(filepath).resolve())
    except Exception:
        bone_action["sub_anim_source_path"] = filepath
        sap_action["sub_anim_source_path"] = filepath
    ensure_action_slot(bone_action, arma)
    ensure_action_slot(sap_action, arma.data)
    assign_action(arma.animation_data, bone_action)

    # Blender frame range setup
    scene = context.scene
    # Ensure we're using integers for frame calculation
    frame_count = int(ssbh_anim_data.final_frame_index + 1)
    scene.frame_start = first_blender_frame
    scene.frame_end = scene.frame_start + frame_count - 1
    # Convenience dict for group gathering
    name_to_group_dict = {group.group_type.name : group for group in ssbh_anim_data.groups}
    # Transform group import stuff
    transform_group = name_to_group_dict.get('Transform') if include_transform_track else None
    if transform_group:
        bones: list[bpy.types.PoseBone] = arma.pose.bones
        bone_to_node = {bones[n.name]:n for n in transform_group.nodes if n.name in bones}
        reordered: list[bpy.types.PoseBone] = get_hierarchy_order(list(bones)) # Do this to gaurantee we never process a child before its parent
        bone_to_fcurves = {b:BoneFCurves(b.name, bone_action, len(n.tracks[0].values)) for b,n in bone_to_node.items()} # only create fcurves for animated bones

        # Reset all bones to rest pose before importing this animation
        reset_bones_to_rest_pose(arma)
        smash_pose_cache = {}

        for index, frame in enumerate(range(scene.frame_start, scene.frame_end + 1)): # +1 because range() excludes the final value
            for bone in reordered:
                node = bone_to_node.get(bone)
                # Some bones may not be animated, but their children may be.
                if node is None: 
                    continue

                # Bones either have a value on the first frame or every frame.
                if index >= len(node.tracks[0].values): 
                    continue 

                smash_value = node.tracks[0].values[index]
                smash_pose_cache.setdefault(str(int(frame)), {})[bone.name] = {
                    "translation": list(smash_value.translation),
                    "rotation": list(smash_value.rotation),
                    "scale": list(smash_value.scale),
                }

                raw_matrix = get_raw_matrix(bone_to_node, bone, index, node)

                bone_fcurves = bone_to_fcurves[bone]
                if bone.parent is None:
                    # The root bone
                    y_up_to_z_up = Matrix.Rotation(math.radians(90), 4, 'X')
                    x_major_to_y_major = Matrix.Rotation(math.radians(-90), 4, 'Z')
                    bone.matrix = y_up_to_z_up @ raw_matrix @ x_major_to_y_major

                    bone_fcurves.stash_keyframe_set_from_matrix(index, frame, bone.matrix_basis)
                else:
                    # The anim transform is relative to the parent bone's animated world transform.
                    bone.matrix = bone.parent.matrix @ get_blender_transform(raw_matrix).transposed()

                    # Matrix basis is the transform set for the pose bone by the user.
                    # The fcurves work on these user configurable values.
                    matrix_basis = apply_transform_flags(bone.matrix_basis, node.tracks[0].transform_flags)

                    bone_fcurves.stash_keyframe_set_from_matrix(index, frame, matrix_basis)

        for bone, bone_fcurves in bone_to_fcurves.items():
            bone_fcurves.set_keyframe_values_from_stash()
        bone_action["sub_smash_pose_cache"] = json.dumps(smash_pose_cache)

    visibility_group = name_to_group_dict.get('Visibility') if include_visibility_track else None
    material_group = name_to_group_dict.get('Material') if include_material_track else None

    if visibility_group:
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        for node in visibility_group.nodes:
            sub_vis_track_entry = sap.vis_track_entries.get(node.name)
            if sub_vis_track_entry is None:
                sub_vis_track_entry = sap.vis_track_entries.add()
                sub_vis_track_entry.name = node.name

    # Material group import stuff
    if material_group:
        sap: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
        # Initial Setup
        for node in material_group.nodes:
            mat_track: SUB_PG_mat_track = sap.mat_tracks.get(node.name)
            if mat_track is None:
                mat_track = sap.mat_tracks.add()
                mat_track.name = node.name
            for track in node.tracks:
                prop: SUB_PG_mat_track_property = mat_track.properties.get(track.name)
                if prop is None:
                    prop = mat_track.properties.add()
                    prop.name = track.name
                prop.name = track.name
                if 'CustomBoolean' in track.name:
                    prop.sub_type = 'BOOL'
                elif 'CustomFloat' in track.name:
                    prop.sub_type = 'FLOAT'
                elif 'CustomVector' in track.name:
                    prop.sub_type = 'VECTOR'
                elif 'PatternIndex' in track.name:
                    prop.sub_type = 'PATTERN'
                elif 'Texture' in track.name:
                    prop.sub_type = 'TEXTURE'
                elif track.name == 'DiffuseUVTransform':
                    prop.sub_type = 'DIFFUSE_UV'
                else:
                    raise TypeError(f'Unsupported track name {track.name}')

    # Bind the SAP action before writing visibility/material keys so Blender 5
    # routes keyframe_insert/create_fcurve to the correct action slot.
    if visibility_group or material_group:
        assign_action(arma.data.animation_data, sap_action)

    if visibility_group:
        sap = arma.data.sub_anim_properties
        for node in visibility_group.nodes:
            if not node.tracks:
                continue
            entry_index = sap.vis_track_entries.find(node.name)
            if entry_index < 0:
                continue
            data_path = f'sub_anim_properties.vis_track_entries[{entry_index}].value'
            vis_entry = sap.vis_track_entries[entry_index]
            last_value = None
            for index, value in enumerate(node.tracks[0].values):
                bool_value = bool(value)
                if bool_value == last_value:
                    continue
                vis_entry.value = bool_value
                arma.data.keyframe_insert(
                    data_path=data_path,
                    frame=scene.frame_start + index,
                    group='Visibility',
                )
                last_value = bool_value
            fcurve = find_fcurve(sap_action, data_path, id_type='ARMATURE')
            if fcurve is not None:
                for keyframe in fcurve.keyframe_points:
                    keyframe.interpolation = 'CONSTANT'
                style_visibility_fcurve(fcurve)
                fcurve.update()

    if material_group:
        sap = arma.data.sub_anim_properties
        for node in material_group.nodes:
            mat_track: SUB_PG_mat_track = sap.mat_tracks.get(node.name)
            mat_track_index = sap.mat_tracks.find(mat_track.name)
            for track in node.tracks:
                prop = mat_track.properties.get(track.name)
                prop_index = mat_track.properties.find(prop.name)
                if prop.sub_type == 'VECTOR':
                    data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].custom_vector'
                    for index in (0,1,2,3):
                        vector_index_values = [vector[index] for vector in track.values]
                        fcurve = create_fcurve(sap_action, 'ARMATURE', data_path, index=index, action_group=f'Material ({mat_track.name})')
                        fcurve.keyframe_points.add(count=len(vector_index_values))
                        frame_and_value_flattened = []
                        for index, value in enumerate(vector_index_values):
                            frame_and_value_flattened.extend([scene.frame_start + index, value])
                        fcurve.keyframe_points.foreach_set('co', frame_and_value_flattened)
                        fcurve.update()
                        style_material_fcurve(fcurve)
                elif prop.sub_type == 'FLOAT':
                    data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].custom_float'
                    fcurve = create_fcurve(sap_action, 'ARMATURE', data_path, action_group=f'Material ({mat_track.name})')
                    fcurve.keyframe_points.add(count=len(track.values))
                    frame_and_value_flattened = []
                    for index, value in enumerate(track.values):
                        frame_and_value_flattened.extend([scene.frame_start + index, value])
                    fcurve.keyframe_points.foreach_set('co', frame_and_value_flattened)
                    fcurve.update()
                    style_material_fcurve(fcurve)
                elif prop.sub_type == 'BOOL':
                    data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].custom_bool'
                    fcurve = create_fcurve(sap_action, 'ARMATURE', data_path, action_group=f'Material ({mat_track.name})')
                    fcurve.keyframe_points.add(count=len(track.values))
                    frame_and_value_flattened = []
                    for index, value in enumerate(track.values):
                        frame_and_value_flattened.extend([scene.frame_start + index, value])
                    fcurve.keyframe_points.foreach_set('co', frame_and_value_flattened)
                    fcurve.update()
                    style_material_fcurve(fcurve)
                elif prop.sub_type == 'PATTERN':
                    data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].pattern_index'
                    fcurve = create_fcurve(sap_action, 'ARMATURE', data_path, action_group=f'Material ({mat_track.name})')
                    fcurve.keyframe_points.add(count=len(track.values))
                    frame_and_value_flattened = []
                    for index, value in enumerate(track.values):
                        frame_and_value_flattened.extend([scene.frame_start + index, value])
                    fcurve.keyframe_points.foreach_set('co', frame_and_value_flattened)
                    fcurve.update()
                    style_material_fcurve(fcurve)
                elif prop.sub_type == 'TEXTURE':
                    data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].texture_transform'
                    for index in (0,1,2,3,4):
                        if index == 0:
                            vector_index_values = [uv_transform.scale_u for uv_transform in track.values]
                        elif index == 1:
                            vector_index_values = [uv_transform.scale_v for uv_transform in track.values]
                        elif index == 2:
                            vector_index_values = [uv_transform.rotation for uv_transform in track.values]
                        elif index == 3:
                            vector_index_values = [uv_transform.translate_u for uv_transform in track.values]
                        elif index == 4:
                            vector_index_values = [uv_transform.translate_v for uv_transform in track.values]
                        fcurve = create_fcurve(sap_action, 'ARMATURE', data_path, index=index, action_group=f'Material ({mat_track.name})')
                        fcurve.keyframe_points.add(count=len(vector_index_values))
                        frame_and_value_flattened = []
                        for index, value in enumerate(vector_index_values):
                            frame_and_value_flattened.extend([scene.frame_start + index, value])
                        fcurve.keyframe_points.foreach_set('co', frame_and_value_flattened)
                        fcurve.update()
                        style_material_fcurve(fcurve)
                elif prop.sub_type == 'DIFFUSE_UV':
                    # TODO: implement support for diffuse UV transforms
                    pass

    if visibility_group:
        setup_visibility_drivers(arma)
    if material_group:
        setup_material_drivers(arma)

    # Assign actions (and slots on Blender 4.4+ / 5.x).
    assign_action(arma.animation_data, bone_action)
    assign_action(arma.data.animation_data, sap_action)

    from .anim_data import mark_sap_sync_known
    mark_sap_sync_known(arma)
    try:
        from ..extras.eye_rig import ensure_eye_live_preview
        ensure_eye_live_preview(scene)
    except Exception:
        pass


def get_raw_matrix(bone_to_node, bone, index, node) -> Matrix:
    translation = node.tracks[0].values[index].translation
    rotation = node.tracks[0].values[index].rotation
    scale = node.tracks[0].values[index].scale

    tm = Matrix.Translation(translation)
    qr = Quaternion([rotation[3], rotation[0], rotation[1], rotation[2]])
    rm = Matrix.Rotation(qr.angle, 4, qr.axis)
    # Blender doesn't have this built in for some reason.
    scale_matrix = Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
    compensate_scale = node.tracks[0].compensate_scale
    scale_compensation = get_scale_compensation(bone_to_node, bone, index, compensate_scale)

    return tm @ scale_compensation @ rm @ scale_matrix


def get_scale_compensation(bone_to_node, bone, frame, compensate_scale):
    scale_compensation = Matrix.Diagonal((1.0, 1.0, 1.0, 1.0))
    if compensate_scale and bone.parent:
        # Scale compensation "compensates" the effect of the immediate parent's scale.
        parent_node = bone_to_node.get(bone.parent, None)
        if parent_node is not None:
            try:
                # The parent may not have the same frame count.
                # Handle the case where the parent has only one frame.
                if frame >= len(parent_node.tracks[0].values):
                    parent_scale = parent_node.tracks[0].values[0].scale
                else:
                    parent_scale = parent_node.tracks[0].values[frame].scale

                scale_compensation = Matrix.Diagonal((1.0 / parent_scale[0], 1.0 / parent_scale[1], 1.0 / parent_scale[2], 1.0))
            except IndexError:
                # TODO: Handle the case when the parent has no animation track?
                pass

    return scale_compensation


def apply_transform_flags(matrix_basis: Matrix, transform_flags: ssbh_data_py.anim_data.TransformFlags):
    # Some tracks override parts of the anim transform.
    # This allows bones like swing bones to be animated in other ways.
    mbtv, mbrq, mbsv = matrix_basis.decompose()

    if transform_flags.override_translation:
        mbtv = [0.0, 0.0, 0.0]
    if transform_flags.override_rotation:
        mbrq = Quaternion([1,0,0,0])
    if transform_flags.override_scale:
        mbsv = [1.0, 1.0, 1.0]

    mbtm = Matrix.Translation(mbtv)
    mbrm = Matrix.Rotation(mbrq.angle, 4, mbrq.axis)
    mbsm = Matrix.Diagonal((mbsv[0], mbsv[1], mbsv[2], 1.0))

    return mbtm @ mbrm @ mbsm


def keyframe_insert_camera_locrotscale(camera, frame):
    for parameter in ['location', 'rotation_quaternion', 'scale']:
        camera.keyframe_insert(
            data_path=f'{parameter}',
            frame=frame,
            group='Transform',
            #options={'INSERTKEY_NEEDED'}, started causing errors errors in 4.1
        ) 

def uvtransform_to_list(uvtransform) -> list[float]:
    scale_u = uvtransform.scale_u
    scale_v = uvtransform.scale_v
    rotation = uvtransform.rotation
    translate_u = uvtransform.translate_u
    translate_v = uvtransform.translate_v
    return [scale_u, scale_v, rotation, translate_u, translate_v]

def setup_material_drivers(arma: bpy.types.Object):
    from ..model.export_model import trim_name
    sub_anim_data: SUB_PG_sub_anim_data = arma.data.sub_anim_properties
    mesh_children = [child for child in arma.children if child.type == 'MESH']
    materials: set[Material] = {material_slot.material for mesh in mesh_children for material_slot in mesh.material_slots}
    trimmed_material_name_to_material: dict[str, Material] = {trim_name(material.name) : material for material in materials}
    
    for track_index, mat_track in enumerate(sub_anim_data.mat_tracks):
        for property_index, mat_track_property in enumerate(mat_track.properties):
            if mat_track_property.sub_type == 'VECTOR':
                for axis_index, axis in enumerate(['X', 'Y', 'Z', 'W']):
                    material = trimmed_material_name_to_material.get(mat_track.name)
                    if material is None:
                        continue
                    value_node: bpy.types.ShaderNodeValue = material.node_tree.nodes.get(f"{mat_track_property.name}_{axis}")
                    if value_node is None:
                        continue
                    # Remove Existing Driver
                    value_node.outputs[0].driver_remove('default_value')
                    # Setup Driver
                    driver_fcurve: bpy.types.FCurve = value_node.outputs[0].driver_add('default_value')
                    var = driver_fcurve.driver.variables.new()
                    var.name = "var"
                    target = var.targets[0]
                    target.id_type = 'ARMATURE'
                    target.id = arma.data
                    target.data_path = f'sub_anim_properties.mat_tracks[{track_index}].properties[{property_index}].custom_vector[{axis_index}]'
                    driver_fcurve.driver.expression = f'{var.name}'

def do_material_stuff(context, material_group, index, frame):
    arma = context.scene.sub_scene_properties.anim_import_arma
    sap = arma.data.sub_anim_properties
    for node in material_group.nodes:
        mat_track = sap.mat_tracks.get(node.name)
        mat_track_index = sap.mat_tracks.find(mat_track.name)
        for track in node.tracks:
            try:
                track.values[index]
            except IndexError:
                continue
            value = track.values[index]
            prop = mat_track.properties.get(track.name)
            prop_index = mat_track.properties.find(prop.name)
            if prop.sub_type == 'VECTOR':
                prop.custom_vector = value
                arma.data.keyframe_insert(data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].custom_vector', frame=frame, group=f'Material ({mat_track.name})', options={'INSERTKEY_NEEDED'})
            elif prop.sub_type == 'FLOAT':
                prop.custom_float = value
                arma.data.keyframe_insert(data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].custom_float', frame=frame,  group=f'Material ({mat_track.name})', options={'INSERTKEY_NEEDED'})
            elif prop.sub_type == 'BOOL':
                prop.custom_bool = value
                arma.data.keyframe_insert(data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].custom_bool', frame=frame,  group=f'Material ({mat_track.name})', options={'INSERTKEY_NEEDED'})
            elif prop.sub_type == 'PATTERN':
                prop.pattern_index = value
                arma.data.keyframe_insert(data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].pattern_index', frame=frame,  group=f'Material ({mat_track.name})', options={'INSERTKEY_NEEDED'})
            elif prop.sub_type == 'TEXTURE':
                prop.texture_transform = [value.scale_u, value.scale_v, value.rotation, value.translate_u, value.translate_v]
                arma.data.keyframe_insert(data_path=f'sub_anim_properties.mat_tracks[{mat_track_index}].properties[{prop_index}].texture_transform', frame=frame,  group=f'Material ({mat_track.name})', options={'INSERTKEY_NEEDED'})

def setup_sap_material_properties(context, material_group):
    arma = context.scene.sub_scene_properties.anim_import_arma
    sap = arma.data.sub_anim_properties
    # Setup
    for node in material_group.nodes:
        mat_track = sap.mat_tracks.get(node.name, None)
        if mat_track is None:
            mat_track = sap.mat_tracks.add()
            mat_track.name = node.name
        for track in node.tracks:
            prop = mat_track.properties.get(track.name, None)
            if prop is None:
                prop = mat_track.properties.add()
                prop.name = track.name
                if 'CustomBoolean' in track.name:
                    prop.sub_type = 'BOOL'
                elif 'CustomFloat' in track.name:
                    prop.sub_type = 'FLOAT'
                elif 'CustomVector' in track.name:
                    prop.sub_type = 'VECTOR'
                elif 'PatternIndex' in track.name:
                    prop.sub_type = 'PATTERN'
                elif 'Texture' in track.name:
                    prop.sub_type = 'TEXTURE'
                else:
                    raise TypeError(f'Unsupported track name {track.name}')         
            

def setup_visibility_drivers(arma:bpy.types.Object):
    # Setup Vis Drivers
    vis_track_entries = arma.data.sub_anim_properties.vis_track_entries
    mesh_children = [child for child in arma.children if child.type == 'MESH']
    for mesh in mesh_children:
        true_mesh_name = re.split('Shape|_VIS_|_O_', mesh.name)[0]
        if any(true_mesh_name == key for key in vis_track_entries.keys()):
            entries_index = vis_track_entries.find(true_mesh_name)
            for property in ['hide_viewport', 'hide_render']:
                driver_handle = mesh.driver_add(property)
                var = driver_handle.driver.variables.new()
                var.name = "var"
                target = var.targets[0]
                target.id_type = 'ARMATURE'
                target.id = arma.data
                target.data_path = f'sub_anim_properties.vis_track_entries[{entries_index}].value'
                driver_handle.driver.expression = f'1 - {var.name}'

def do_visibility_stuff(context, visibility_group, index, frame):
    for node in visibility_group.nodes:
        try:
            node.tracks[0].values[index]
        except IndexError: # Not every vis track entry will have values on every frame. Many only have the first frame.
            continue
        value = node.tracks[0].values[index]

        arma = context.scene.sub_scene_properties.anim_import_arma
        entries = arma.data.sub_anim_properties.vis_track_entries
        sub_vis_track_entry = entries.get(node.name, None)
        if sub_vis_track_entry is None:
            sub_vis_track_entry = entries.add()
            sub_vis_track_entry.name = node.name
        sub_vis_track_entry.value = value
        entry_index = entries.find(sub_vis_track_entry.name)
        arma.data.keyframe_insert(data_path=f'sub_anim_properties.vis_track_entries[{entry_index}].value', frame=frame, group='Visibility', options={'INSERTKEY_NEEDED'})

'''
Typical SSBH Camera Layout.
Group: 'Transform'
    Node: 'gya_camera'
        Track: 'Transform'
Group: 'Camera'
    Node: 'gya_cameraShape'
        Track: 'FarClip'
        Track: 'FieldOfView'
        Track: 'NearClip'
'''
# TODO: Stages use additional anim layouts.
def import_camera_anim(operator, context:bpy.types.Context, filepath, first_blender_frame):
    camera: bpy.types.Object = context.object
    ssbh_anim_data = ssbh_data_py.anim_data.read_anim(filepath)
    name_group_dict = {group.group_type.name : group for group in ssbh_anim_data.groups}
    transform_group = name_group_dict.get('Transform')
    camera_group = name_group_dict.get('Camera')

    # Ensure we're using integers for frame calculation
    frame_count = int(ssbh_anim_data.final_frame_index + 1)
    scene = context.scene
    scene.frame_start = first_blender_frame
    scene.frame_end = scene.frame_start + frame_count - 1
    scene.frame_set(scene.frame_start)

    #try:
    #    bpy.ops.object.mode_set(mode='OBJECT', toggle=False) # whatever object is currently selected, exit whatever mode its in
    #except RuntimeError: # There may not have been any active or selected object
    #    pass
    context.view_layer.objects.active = camera

    from pathlib import Path
    action_name = camera.name + ' ' + Path(filepath).stem
    if camera.animation_data is None:
        camera.animation_data_create()
    action = bpy.data.actions.new(action_name)
    ensure_action_slot(action, camera)
    assign_action(camera.animation_data, action)
    camera.matrix_local.identity()
    camera.rotation_mode = 'QUATERNION'

    for index, frame in enumerate(range(scene.frame_start, scene.frame_end+1)):
        scene.frame_set(frame)
        if camera_group is not None:
            update_camera_properties(operator, camera, camera_group, index, frame)
        if transform_group is not None:
            update_camera_transforms(camera, transform_group, index, frame)

def update_camera_properties(operator: bpy.types.Operator, camera:bpy.types.Object, camera_group, index, frame):
    node: ssbh_data_py.anim_data.NodeData = None
    # Imported anim should always have at least one node under the camera group
    if len(camera_group.nodes) == 0:
        message = f'The camera anim has no Nodes in the Camera group! Skipping setting camera properties'
        operator.report({'WARNING'}, message)
        return
    # The standard behavior
    if len(camera_group.nodes) == 1:
        node = camera_group.nodes[0]
    # If the camera group has multiple nodes instead of just 'gya_cameraShape', just use the 'gya_cameraShape' one
    if len(camera_group.nodes) > 1:
        message = f'The camera anim has multiple Camera Property Nodes! Will use the one called "gya_camera_Shape", but will not be able to export the other Node!'
        operator.report({'WARNING'}, message)
        for n in camera_group.nodes:
            if n.name == 'gya_cameraShape':
                node = n
        if node is None:
            node = camera_group.nodes[0]
    for track in node.tracks:
        if track.name == 'FieldOfView':
            if index < len(track.values):
                #scp.field_of_view = track.values[index]
                #cam_keyframe_insert(camera, 'field_of_view', frame)
                camera.data.angle_y = track.values[index]
                camera.data.keyframe_insert(data_path = 'lens', frame=frame)
        elif track.name == 'FarClip':
            if index < len(track.values):
                #scp.far_clip = track.values[index]
                #cam_keyframe_insert(camera, 'far_clip', frame)
                camera.data.clip_end= track.values[index]
                camera.data.keyframe_insert(data_path = 'clip_end', frame=frame)
        elif track.name == 'NearClip':
            if index < len(track.values):
                #scp.near_clip = track.values[index]
                #cam_keyframe_insert(camera, 'near_clip', frame)
                camera.data.clip_start = track.values[index]
                camera.data.keyframe_insert(data_path = 'clip_start', frame=frame)
        else:
            operator.report({'WARNING'}, f'Unsupported track {track.name} in camera group, skipping!')

def update_camera_transforms(camera: bpy.types.Object, transform_group, index, frame):
    value = transform_group.nodes[0].tracks[0].values[index]
    translation =  Matrix.Translation(value.translation)
    quaternion = Quaternion([value.rotation[3], value.rotation[0], value.rotation[1], value.rotation[2]])
    rotation = Matrix.Rotation(quaternion.angle, 4, quaternion.axis)
    # Blender doesn't have this built in for some reason.
    scale = Matrix.Diagonal((value.scale[0], value.scale[1], value.scale[2], 1.0))
    axis_correction = Matrix.Rotation(math.radians(90), 4, 'X')   
    camera.matrix_local = axis_correction @ translation @ rotation @ scale
    keyframe_insert_camera_locrotscale(camera, frame)

class SUB_OP_select_animation_folder(Operator):
    bl_idname = 'sub.ssbh_animation_folder_selector'
    bl_label = 'Animation Folder Selector'
    bl_options = {'UNDO'}

    filter_glob: StringProperty(
        default='*.nuanmb',
        options={'HIDDEN'}
    )
    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        anim_path = Path(self.directory)
        
        # Store animation path
        ssp.animation_import_folder_path = str(anim_path)
        
        # Clear previous animation files
        ssp.animation_import_files.clear()
        
        # First, try the direct selected path
        if anim_path.exists():
            nuanmb_files = sorted(
                file_name
                for file_name in os.listdir(anim_path)
                if file_name.endswith('.nuanmb')
            )
            
            for anim_file in nuanmb_files:
                anim_item = ssp.animation_import_files.add()
                anim_item.name = os.path.splitext(anim_file)[0]
                anim_item.path = str(anim_path / anim_file)
            
            if nuanmb_files:
                self.report({'INFO'}, f'Found {len(nuanmb_files)} animations in: {anim_path}')
            # If no animations were found, check if we're in a fighter folder
            elif "fighter" in str(anim_path):
                # Try to find the structure motion/body/[first subfolder]
                try:
                    # First check if this is already a fighter folder
                    if "motion" in os.listdir(anim_path):
                        fighter_folder = anim_path
                    else:
                        # Try to find the fighter folder (this might be a subfolder)
                        parts = str(anim_path).split("fighter")
                        if len(parts) > 1:
                            fighter_folder = Path(parts[0] + "fighter" + parts[1].split(os.sep)[0])
                    
                    motion_folder = fighter_folder / "motion"
                    
                    if motion_folder.exists():
                        body_folder = motion_folder / "body"
                        
                        if body_folder.exists():
                            # Get the first subfolder in body
                            try:
                                subfolders = [f for f in os.listdir(body_folder) if os.path.isdir(body_folder / f)]
                                if subfolders:
                                    deep_anim_path = body_folder / subfolders[0]
                                    
                                    if deep_anim_path.exists():
                                        # Update the stored animation path
                                        ssp.animation_import_folder_path = str(deep_anim_path)
                                        
                                        deep_nuanmb_files = sorted(
                                            file_name
                                            for file_name in os.listdir(deep_anim_path)
                                            if file_name.endswith('.nuanmb')
                                        )
                                        
                                        for anim_file in deep_nuanmb_files:
                                            anim_item = ssp.animation_import_files.add()
                                            anim_item.name = os.path.splitext(anim_file)[0]
                                            anim_item.path = str(deep_anim_path / anim_file)
                                        
                                        if deep_nuanmb_files:
                                            self.report({'INFO'}, f'Found {len(deep_nuanmb_files)} animations in deep path: {deep_anim_path}')
                                        else:
                                            self.report({'INFO'}, f'No animations found in deep path: {deep_anim_path}')
                            except Exception as e:
                                self.report({'INFO'}, f'Failed to search in deep animation path: {str(e)}')
                except Exception as e:
                    self.report({'INFO'}, f'Failed to find deep animation structure: {str(e)}')
                
                if len(ssp.animation_import_files) == 0:
                    self.report({'INFO'}, f'No animations found in: {anim_path} or deeper structure')
            else:
                self.report({'INFO'}, f'No animations found in: {anim_path}')
        else:
            self.report({'ERROR'}, f'Animation directory not found: {anim_path}')

        refresh_raw_animation_import_list(ssp)
        obj = getattr(context, "object", None)
        if obj is not None and getattr(obj, "type", "") == "ARMATURE":
            bind_anim_folder_to_armature(obj, ssp.animation_import_folder_path)
            
        return {'FINISHED'}


def create_fcurve(action, id_type: str, data_path: str, index: int = 0, action_group: str = '') -> bpy.types.FCurve:
    return new_fcurve(action, data_path, index=index, action_group=action_group, id_type=id_type)
