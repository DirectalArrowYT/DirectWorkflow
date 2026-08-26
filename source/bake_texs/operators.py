import bpy
import os
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty, EnumProperty

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
        'Load the baked COL/NOR/PRM/EMI PNGs and assign them onto each baked '
        'material\'s own texture slots, so Export Model actually picks them up. '
        'Only touches the base material of a bundled group (e.g. Body, not BodyShiny) - '
        'export already points BodyShiny at Body\'s textures on its own. '
        'This never happens automatically when you bake - run it explicitly whenever '
        'you actually want the bake to become the live material'
    )
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name='Apply To',
        description='Which copy of each material gets the baked textures',
        items=(
            ('BOTH', 'Material And Side-Loaded Copy',
             'Assign to the mesh material and to its "(Side-Loaded)" twin when '
             'one exists. The safe default - a twin describes the same surface, '
             'so it wants the same freshly baked maps'),
            ('LIVE', 'Material Only',
             'Only the material assigned to the mesh. A side-loaded twin keeps '
             'whatever textures it already had'),
            ('SIDE_LOADED', 'Side-Loaded Copy Only',
             'Only the "(Side-Loaded)" twin, leaving the mesh material untouched'),
        ),
        default='BOTH',
    )

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.selected_objects

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'target')

        scene_props = context.scene.sub_scene_properties
        if (self.target != 'LIVE'
                and scene_props.export_prefer_sideloaded_materials):
            box = layout.box()
            box.label(text='Export is reading from side-loaded copies,', icon='INFO')
            box.label(text='so those are the ones that need the bakes.')

    def invoke(self, context, event):
        # When export is already set to read from side-loaded data, applying
        # only to the mesh materials would put the new bakes somewhere export
        # never looks - which is exactly the case where this silently does
        # nothing useful.
        return context.window_manager.invoke_props_dialog(self)

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
        twins_written = 0
        missing_twins = []

        for group_name, group in groups.items():
            base_material = group['materials'][0]
            stem = core.make_stem(group['objects'][0], base_material)

            targets = self._target_materials(base_material)
            if not targets:
                missing_twins.append(base_material.name)
                continue
            twins_written += sum(1 for m in targets if m is not base_material)

            for material in targets:
                for suffix, param_id_name, colorspace in (
                    ('_col', 'Texture0', 'sRGB'),
                    ('_nor', 'Texture4', 'Non-Color'),
                    ('_prm', 'Texture6', 'Non-Color'),
                    ('_emi', 'Texture5', 'sRGB'),
                ):
                    png_path = os.path.join(core.BAKE_DIR, f'{stem}{suffix}.png')
                    if not os.path.isfile(png_path):
                        continue
                    slot = _find_texture_slot(material, param_id_name)
                    if slot is None:
                        notes.append(f'{material.name}: no {param_id_name} slot for {stem}{suffix} - skipped')
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
                    sub_matl_data = getattr(material, 'sub_matl_data', None)
                    cv3 = sub_matl_data.vectors.get('CustomVector3') if sub_matl_data else None
                    if cv3 is not None:
                        cv3.value = cv3_value
                        if max(cv3_value[:3]) > 1.0:
                            notes.append(
                                f'{material.name}: CustomVector3 set to '
                                f'{cv3_value[0]:.3f} to restore EMI brightness')
                    elif max(cv3_value[:3]) > 1.0:
                        notes.append(
                            f'{material.name}: needs CustomVector3 = '
                            f'{cv3_value[0]:.3f} for the EMI map, but has no such '
                            f'parameter - apply the Emissive preset')

        if missing_twins:
            notes.append(
                'no side-loaded twin for ' + ', '.join(sorted(missing_twins))
                + ' - nothing written for those')

        msg = f'Applied {applied} texture(s) to material slots.'
        if twins_written:
            msg += f' {twins_written} side-loaded copy/copies updated.'
        if notes:
            msg += ' ' + '; '.join(dict.fromkeys(notes))
        self.report({'INFO' if not notes else 'WARNING'}, msg)
        return {'FINISHED'}

    def _target_materials(self, base_material):
        """Which materials this run should write the baked textures onto.

        A side-loaded twin is never assigned to a mesh, so it never turns up in
        the bake's own target list - which is why baking and applying could
        leave the twin pointing at stale textures while export, reading from
        the twin, quietly ignored everything that was just baked.
        """
        from ..model.material.create_matl_from_blender_materials import get_side_loaded_twin
        from ..material_grouping import SIDE_LOAD_SUFFIX

        # Already a twin: write to it and stop, rather than hunting for a twin
        # of a twin.
        if base_material.name.endswith(SIDE_LOAD_SUFFIX):
            return [base_material]

        twin = get_side_loaded_twin(base_material)

        if self.target == 'LIVE':
            return [base_material]
        if self.target == 'SIDE_LOADED':
            return [twin] if twin is not None else []
        return [base_material] + ([twin] if twin is not None else [])


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
