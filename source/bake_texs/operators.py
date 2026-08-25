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


def _find_texture_slot(material, param_id_name):
    """The existing sub_matl_data.textures entry for a param, or None."""
    sub_matl_data = getattr(material, 'sub_matl_data', None)
    if sub_matl_data is None:
        return None
    for texture in sub_matl_data.textures:
        if texture.param_id_name == param_id_name:
            return texture
    return None


def _load_or_refresh_image(filepath, name, colorspace):
    """Load a baked PNG as an Image named exactly `name` (no extension).

    Reuses the existing datablock of that name on a re-apply (pointing it at
    the file and reloading), rather than piling up Body_col.001, .002, ... on
    every re-bake.
    """
    existing = bpy.data.images.get(name)
    if existing is not None:
        existing.filepath = filepath
        existing.source = 'FILE'
        try:
            existing.reload()
        except Exception:
            pass
        core.set_colorspace(existing, colorspace)
        return existing
    img = bpy.data.images.load(filepath, check_existing=False)
    img.name = name
    core.set_colorspace(img, colorspace)
    return img


class SUB_OP_bake_texs_apply_to_materials(Operator):
    """Point each baked group's base material at its freshly baked textures"""
    bl_idname = 'sub.bake_texs_apply_to_materials'
    bl_label = 'Apply Baked Textures to Materials'
    bl_description = (
        'Load the baked COL/NOR/PRM PNGs and assign them onto each baked material\'s '
        'own texture slots (Texture0/4/6), so Export Model actually picks them up. '
        'Only touches the base material of a bundled group (e.g. Body, not BodyShiny) - '
        'export already points BodyShiny at Body\'s textures on its own. '
        'This never happens automatically when you bake - run it explicitly whenever '
        'you actually want the bake to become the live material'
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.selected_objects

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        try:
            groups, _ = core.collect_targets()
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        applied = 0
        notes = []
        for group_name, group in groups.items():
            base_material = group['materials'][0]
            stem = core.make_stem(group['objects'][0], base_material)

            for suffix, param_id_name, colorspace in (
                ('_col', 'Texture0', 'sRGB'),
                ('_nor', 'Texture4', 'Non-Color'),
                ('_prm', 'Texture6', 'Non-Color'),
                ('_emi', 'Texture5', 'sRGB'),
            ):
                png_path = os.path.join(core.BAKE_DIR, f'{stem}{suffix}.png')
                if not os.path.isfile(png_path):
                    continue
                slot = _find_texture_slot(base_material, param_id_name)
                if slot is None:
                    notes.append(f'{base_material.name}: no {param_id_name} slot for {stem}{suffix} - skipped')
                    continue
                slot.image = _load_or_refresh_image(png_path, f'{stem}{suffix}', colorspace)
                applied += 1

            # An EMI map baked from an Emission Strength above 1 was divided
            # down to fit in 8 bits, and CustomVector3 is what puts the
            # brightness back. Applying the texture without it would render the
            # glow dimmer than it was authored.
            bake_info = core.LAST_BAKE_INFO.get(stem)
            if bake_info and 'custom_vector_3' in bake_info:
                cv3_value = bake_info['custom_vector_3']
                sub_matl_data = getattr(base_material, 'sub_matl_data', None)
                cv3 = sub_matl_data.vectors.get('CustomVector3') if sub_matl_data else None
                if cv3 is not None:
                    cv3.value = cv3_value
                    if max(cv3_value[:3]) > 1.0:
                        notes.append(
                            f'{base_material.name}: CustomVector3 set to '
                            f'{cv3_value[0]:.3f} to restore EMI brightness')
                elif max(cv3_value[:3]) > 1.0:
                    notes.append(
                        f'{base_material.name}: needs CustomVector3 = '
                        f'{cv3_value[0]:.3f} for the EMI map, but has no such '
                        f'parameter - apply the Emissive preset')

        msg = f'Applied {applied} texture(s) to material slots.'
        if notes:
            msg += ' ' + '; '.join(notes)
        self.report({'INFO' if not notes else 'WARNING'}, msg)
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
