import bpy
import os
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty

from . import core


class SUB_OP_bake_texs_run(Operator):
    """Bake COL/NOR/PRM for the selected meshes, then compile to .nutexb"""
    bl_idname = 'sub.bake_texs_run'
    bl_label = 'Bake Textures'
    bl_description = (
        'Bake COL/NOR/PRM texture sets for every material on the selected meshes. '
        'If "Compile to .nutexb" is on, also converts them into the model folder afterward'
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.selected_objects

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        out_dir = core.get_output_dir()
        if core.COMPILE_NUTEXB and not core.DRY_RUN and (not out_dir or props.nutexb_always_ask):
            bpy.ops.baketexs.pick_output('INVOKE_DEFAULT', do_bake=True)
            return {'FINISHED'}

        try:
            written = core.bake_all()
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        if core.DRY_RUN:
            self.report({'INFO'}, 'Dry run complete - see the console / _BAKE_REPORT.txt for what would bake.')
            return {'FINISHED'}

        if core.COMPILE_NUTEXB:
            ok, failed = core.compile_nutexb(written, out_dir)
            self.report({'INFO' if not failed else 'WARNING'},
                       f'Baked {len(written)} texture(s). nutexb: {ok} written'
                       + (f', {failed} failed' if failed else ''))
        else:
            self.report({'INFO'}, f'Baked {len(written)} texture(s) -> {core.BAKE_DIR}')
        return {'FINISHED'}


class SUB_OP_bake_texs_compile_only(Operator):
    """Compile already-baked PNGs to .nutexb, without re-baking"""
    bl_idname = 'sub.bake_texs_compile_only'
    bl_label = 'Compile Existing Bakes'
    bl_description = (
        'Convert the PNGs already sitting in the bake output folder into .nutexb, '
        'without baking again. Use this when you hand-edited a PNG, or a previous '
        'compile step failed'
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        pngs = core.bakes_in_folder(core.BAKE_DIR)
        if not pngs:
            self.report({'WARNING'}, f'No _col/_nor/_prm PNGs found in {core.BAKE_DIR}')
            return {'CANCELLED'}

        out_dir = core.get_output_dir()
        if not out_dir or props.nutexb_always_ask:
            bpy.ops.baketexs.pick_output('INVOKE_DEFAULT', do_bake=False)
            return {'FINISHED'}

        ok, failed = core.compile_nutexb(pngs, out_dir)
        self.report({'INFO' if not failed else 'WARNING'},
                   f'{ok} nutexb written' + (f', {failed} failed' if failed else ''))
        return {'FINISHED'}


class SUB_OP_bake_texs_pick_output(Operator):
    """Choose the model folder that .nutexb files are written to"""
    bl_idname = 'baketexs.pick_output'
    bl_label = 'Select Model Folder for .nutexb Output'

    directory: StringProperty(subtype='DIR_PATH')
    filter_folder: BoolProperty(default=True, options={'HIDDEN'})
    do_bake: BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        self.directory = core.get_output_dir() or bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not os.path.isdir(self.directory):
            self.report({'ERROR'}, "That is not a folder")
            return {'CANCELLED'}
        core.set_output_dir(self.directory)

        try:
            written = core.bake_all() if self.do_bake else core.bakes_in_folder(core.BAKE_DIR)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        ok, failed = core.compile_nutexb(written, self.directory)
        self.report({'INFO' if not failed else 'WARNING'},
                   f'{ok} nutexb written' + (f', {failed} failed' if failed else ''))
        return {'FINISHED'}
